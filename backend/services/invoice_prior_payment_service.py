# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票「前期已付款入账」：把收票月的发票挂到前期已入账的付款凭证上。

「先付款、后收票」的第三条路（前两条：往来暂付 122199 对冲、预存扣款 1123）。
DeepSeek 等服务商充值即开票，付款流水在付款当月已被系统规则入账（常为费用），
收票月的挂账草稿（借 费用 / 贷 2241）在当月公户永远找不到对应扣款，现有四个
确认选项全部导致重复入账或错误挂账。

本服务提供：
- ``find_prior_payment_candidates``：跨期搜索「金额一致 + 日期 ≤ 发票日 + 公户已付」
  的已入账凭证，按对手方文本相似度分级（强=流水对方含销方名 / 中=摘要相似 / 弱=仅金额）。
- ``link_prior_payment``：确认挂接——发票主数据 ``entry_id`` 重定向到前期凭证
  （复用 ``rebind_invoice_entry`` 审计语义），随后删除收票月挂账草稿，
  收票月不再产生重复费用。已过账凭证不在此列（走冲销/重新选择）。

与匹配基建的分工：流水→凭证方向仍禁止跨期（``CROSS_PERIOD_MATCH`` 语义不变）；
本服务是发票→凭证方向，只动主数据与草稿，不触碰已过账凭证（A1）。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import Invoice, JournalEntry, JournalLine, LegacyEntryProjection

# 银行存款科目：前期付款凭证的标志是「贷 1002 且金额与票面一致」
BANK_ACCOUNT_CODE = "1002"
# 与 bank_service.RECONCILIATION_AMOUNT_TOLERANCE 同口径（金额比较容差 1 分）
AMOUNT_TOLERANCE = Decimal("0.01")
# 可挂接的前期凭证状态：必须是真实入账的付款；reversed 已冲销不可挂
PAYMENT_LINKABLE_STATUSES = ("draft", "pending_review", "posted")
# 这些来源的凭证是发票本体或发票付款，不是「待挂接的付款」
NON_PAYMENT_SOURCES = {"invoice_import", "invoice_payment"}


def _tolerant_equal(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= AMOUNT_TOLERANCE


def _text_overlap_strong(a: str, b: str) -> bool:
    """比 bank_service._has_text_similarity 更严的文本相关性。

    那边的「2 个汉字交集即相关」在报销场景太松：发票摘要与付款摘要都带
    「报销」就能命中，会把不相关的付款推成中置信建议。这里要求
    ≥4 个汉字交集，或清洗后 ≥6 字的整段包含。
    """
    import re

    han_a = set(re.findall(r"[\u4e00-\u9fa5]", a or ""))
    han_b = set(re.findall(r"[\u4e00-\u9fa5]", b or ""))
    if len(han_a & han_b) >= 4:
        return True
    clean_a = re.sub(r"[^\w\u4e00-\u9fa5]", "", a or "")
    clean_b = re.sub(r"[^\w\u4e00-\u9fa5]", "", b or "")
    return len(clean_a) >= 6 and (clean_a in clean_b or clean_b in clean_a)


_COMPANY_LEGAL_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "有限公司",
    "集团",
)
_COMPANY_INDUSTRY_SUFFIXES = (
    "人工智能基础技术研究",
    "基础技术研究",
    "人工智能",
    "信息技术",
    "网络科技",
    "信息科技",
    "科技",
    "信息",
    "技术",
    "网络",
    "电子",
    "商贸",
    "咨询",
    "服务",
    "研究",
)
_CITY_PREFIXES = (
    "北京", "上海", "杭州", "深圳", "广州", "浙江", "江苏", "广东",
    "成都", "苏州", "南京", "武汉", "西安", "重庆", "天津",
)


def _statement_counterparty(statement_desc: str) -> str:
    match = re.search(r"[(（]对方[:：]\s*([^)）]+)[)）]", statement_desc or "")
    return (match.group(1) if match else "").strip()


def _strip_legal_suffix(name: str) -> str:
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", "", cleaned)
    for suffix in _COMPANY_LEGAL_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def _company_core_name(name: str) -> str:
    cleaned = _strip_legal_suffix(name)
    changed = True
    while changed and cleaned:
        changed = False
        for suffix in _COMPANY_INDUSTRY_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 4:
                cleaned = cleaned[: -len(suffix)]
                changed = True
                break
    for prefix in _CITY_PREFIXES:
        if cleaned.startswith(prefix) and len(cleaned) - len(prefix) >= 4:
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned


def _confidence_for(seller_names: list[str], statement_desc: str, entry_summary: str, meta: dict[str, Any]) -> str:
    """强=流水对方/商户含销方名（含银行截断户名及去后缀核心字号）；中=严格文本相关；弱=仅金额一致。"""
    haystacks = " ".join(
        part
        for part in (
            statement_desc,
            entry_summary,
            str(meta.get("merchant") or ""),
            str(meta.get("counterparty") or ""),
        )
        if part
    )
    cp_base = _strip_legal_suffix(_statement_counterparty(statement_desc))
    for seller in seller_names:
        seller = seller.strip()
        if len(seller) >= 4 and seller in haystacks:
            return "counterparty"
        seller_base = _strip_legal_suffix(seller)
        if len(cp_base) >= 4 and len(seller_base) >= 4 and (cp_base in seller_base or seller_base in cp_base):
            return "counterparty"
        core = _company_core_name(seller)
        if len(core) >= 4 and core in haystacks:
            return "counterparty"
    similarity_target = entry_summary or (seller_names[0] if seller_names else "")
    if statement_desc and similarity_target and _text_overlap_strong(statement_desc, similarity_target):
        return "text"
    return "amount_only"


async def _statement_descriptions_by_entry(session, company_id: str, entry_ids: list[str]) -> dict[str, str]:
    """经 bank_statement_matches 取候选凭证关联的流水描述（含「对方: xxx」原文）。"""
    if not entry_ids:
        return {}
    placeholders = ",".join(f":e{i}" for i in range(len(entry_ids)))
    params: dict[str, Any] = {"cid": company_id}
    for index, entry_id in enumerate(entry_ids):
        params[f"e{index}"] = entry_id
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        "SELECT bsm.entry_id AS entry_id, bs.description AS description "
                        "FROM bank_statement_matches bsm "
                        "JOIN bank_statements bs ON bs.id = bsm.statement_id "
                        f"WHERE bsm.company_id = :cid AND bsm.entry_id IN ({placeholders})"
                    ),
                    params,
                )
            ).mappings().all()
    except (OperationalError, ProgrammingError):
        return {}
    return {str(row["entry_id"]): str(row["description"] or "") for row in rows}


async def find_prior_payment_candidates(
    session,
    *,
    company_id: str,
    invoice_entry_id: str,
    limit: int = 5,
    lookback_days: int | None = None,
) -> dict[str, Any]:
    """跨期搜索可挂接的前期付款凭证。

    返回 ``{"candidates": [...]}``；发票凭证不存在/不是发票挂账凭证时返回空列表
    （批量接口对普通凭证静默跳过，不让一张非发票单据拖垮整批）。
    """
    invoice_entry = await session.get(JournalEntry, invoice_entry_id)
    if invoice_entry is None or invoice_entry.company_id != company_id:
        return {"candidates": []}
    meta = dict(invoice_entry.metadata_json or {})
    if str(meta.get("source") or "") != "invoice_import":
        return {"candidates": []}
    invoices = (
        await session.execute(
            select(Invoice).where(
                Invoice.company_id == company_id,
                Invoice.entry_id == invoice_entry_id,
            )
        )
    ).scalars().all()
    if not invoices:
        return {"candidates": []}

    amount = money(meta.get("amount") or ZERO)
    if amount <= ZERO:
        amount = sum((inv.amount_incl_tax for inv in invoices), ZERO)
    if amount <= ZERO:
        return {"candidates": []}
    invoice_date = min((inv.invoice_date for inv in invoices if inv.invoice_date), default=invoice_entry.occurred_on)
    seller_names = [str(inv.seller_name or "") for inv in invoices if str(inv.seller_name or "").strip()]

    conditions = [
        JournalEntry.company_id == company_id,
        JournalEntry.status.in_(PAYMENT_LINKABLE_STATUSES),
        JournalEntry.entry_type == "standard",
        JournalEntry.reversal_of_id.is_(None),
        JournalEntry.id != invoice_entry_id,
        JournalEntry.occurred_on <= invoice_date,
        JournalLine.company_id == company_id,
        JournalLine.account_code == BANK_ACCOUNT_CODE,
        JournalLine.direction == "credit",
        JournalLine.amount >= amount - AMOUNT_TOLERANCE,
        JournalLine.amount <= amount + AMOUNT_TOLERANCE,
    ]
    if lookback_days is not None:
        conditions.append(JournalEntry.occurred_on >= invoice_date - timedelta(days=lookback_days))

    rows = (
        await session.execute(
            select(
                JournalEntry.id,
                JournalEntry.summary,
                JournalEntry.occurred_on,
                JournalEntry.status,
                JournalEntry.metadata_json,
            )
            .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .where(*conditions)
            .order_by(JournalEntry.occurred_on.desc())
            .limit(max(limit * 4, 20))
        )
    ).all()

    statement_desc_by_entry = await _statement_descriptions_by_entry(
        session, company_id, [str(row.id) for row in rows]
    )

    candidates: list[dict[str, Any]] = []
    for row in rows:
        row_meta = dict(row.metadata_json or {})
        if row_meta.get("is_archive_only") or row_meta.get("isArchiveOnly"):
            continue  # 仅归档底稿不参与任何配对
        if str(row_meta.get("source") or "") in NON_PAYMENT_SOURCES:
            continue  # 别的发票凭证/发票付款凭证不是「待挂接的付款」
        projection = await session.get(LegacyEntryProjection, row.id)
        projection_payload: dict[str, Any] = {}
        if projection is not None:
            try:
                projection_payload = (
                    json.loads(projection.payload)
                    if isinstance(projection.payload, str)
                    else dict(projection.payload or {})
                )
            except (json.JSONDecodeError, TypeError):
                projection_payload = {}
        if projection_payload.get("invoiceNumber"):
            continue  # 已是其他发票的证据凭证，防止二次占用
        statement_desc = statement_desc_by_entry.get(str(row.id), "")
        confidence = _confidence_for(seller_names, statement_desc, str(row.summary or ""), row_meta)
        candidates.append(
            {
                "entryId": str(row.id),
                "summary": str(row.summary or ""),
                "occurredOn": str(row.occurred_on),
                "status": str(row.status),
                "amount": money_text(amount),
                "statementDescription": statement_desc,
                "confidence": confidence,
            }
        )

    # 置信度升序为主序；同级时离发票日更近的付款排前面
    rank = {"counterparty": 0, "text": 1, "amount_only": 2}
    candidates.sort(key=lambda item: str(item["occurredOn"]), reverse=True)
    candidates.sort(key=lambda item: rank.get(str(item["confidence"]), 3))
    return {"candidates": candidates[:limit]}


async def link_prior_payment(
    session,
    *,
    company_id: str,
    invoice_entry_id: str,
    prior_entry_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """确认挂接：发票主数据迁到前期凭证 → 删除收票月挂账草稿。

    先重绑再删草稿：``delete_draft`` 会清掉 ``entry_id`` 指向本凭证的发票行
    （防孤儿票），重绑后发票已指向前期凭证，得以存活（A3：主数据指向活凭证）。
    """
    from services.accounting_core_service import (
        _iso_now,
        _json_safe,
        _utcnow,
        delete_draft,
        get_journal,
        record_audit,
    )

    draft = await get_journal(session, invoice_entry_id, company_id=company_id, lock=True)
    if draft.status not in {"draft", "pending_review"}:
        raise AccountingError(
            "ENTRY_POSTED_IMMUTABLE",
            "该发票凭证已过账，不能改为前期已付款；请走冲销/重新选择流程",
            409,
            {"entryId": draft.id, "status": draft.status},
        )
    draft_meta = dict(draft.metadata_json or {})
    if str(draft_meta.get("source") or "") != "invoice_import":
        raise AccountingError(
            "NOT_INVOICE_ENTRY",
            "该凭证不是发票挂账凭证，不能挂接前期付款",
            422,
            {"entryId": draft.id, "source": str(draft_meta.get("source") or "")},
        )

    invoices = (
        await session.execute(
            select(Invoice).where(
                Invoice.company_id == company_id,
                Invoice.entry_id == invoice_entry_id,
            )
        )
    ).scalars().all()
    if not invoices:
        raise AccountingError(
            "NOT_INVOICE_ENTRY",
            "该凭证没有关联的发票主数据，不能挂接前期付款",
            422,
            {"entryId": draft.id},
        )

    prior = await get_journal(session, prior_entry_id, company_id=company_id, lock=True)
    if prior.status not in PAYMENT_LINKABLE_STATUSES:
        raise AccountingError(
            "PRIOR_ENTRY_NOT_LINKABLE",
            "前期凭证已冲销或不在可挂接状态",
            422,
            {"priorEntryId": prior.id, "status": prior.status},
        )
    prior_meta = dict(prior.metadata_json or {})
    if str(prior_meta.get("source") or "") in NON_PAYMENT_SOURCES:
        raise AccountingError(
            "PRIOR_ENTRY_NOT_PAYMENT",
            "所选凭证是发票凭证或发票付款凭证，不是可挂接的付款记录",
            422,
            {"priorEntryId": prior.id},
        )
    invoice_date = min((inv.invoice_date for inv in invoices if inv.invoice_date), default=draft.occurred_on)
    if prior.occurred_on > invoice_date:
        raise AccountingError(
            "PRIOR_ENTRY_AFTER_INVOICE",
            f"前期凭证日期 {prior.occurred_on.isoformat()} 晚于发票日期 {invoice_date.isoformat()}，"
            "请走当月付款确认流程",
            422,
            {"priorEntryId": prior.id, "occurredOn": prior.occurred_on.isoformat()},
        )
    amount = money(draft_meta.get("amount") or ZERO)
    if amount <= ZERO:
        amount = sum((inv.amount_incl_tax for inv in invoices), ZERO)
    bank_lines = (
        await session.execute(
            select(JournalLine).where(
                JournalLine.journal_entry_id == prior.id,
                JournalLine.company_id == company_id,
                JournalLine.account_code == BANK_ACCOUNT_CODE,
                JournalLine.direction == "credit",
            )
        )
    ).scalars().all()
    if not any(_tolerant_equal(line.amount, amount) for line in bank_lines):
        raise AccountingError(
            "PRIOR_PAYMENT_AMOUNT_MISMATCH",
            "前期凭证的公户付款金额与发票金额不一致，不能挂接",
            422,
            {"priorEntryId": prior.id, "invoiceAmount": money_text(amount)},
        )

    # 1. 发票主数据迁移 + 发票号写进前期凭证投影（台账/向导据此识别发票证据）
    invoice_numbers: list[str] = []
    for invoice in invoices:
        invoice.entry_id = prior.id
        invoice.updated_at = _utcnow()
        if str(invoice.invoice_number or "").strip():
            invoice_numbers.append(str(invoice.invoice_number).strip())
    projection = await session.get(LegacyEntryProjection, prior.id)
    if projection is not None and invoice_numbers:
        try:
            payload = (
                json.loads(projection.payload)
                if isinstance(projection.payload, str)
                else dict(projection.payload or {})
            )
        except (json.JSONDecodeError, TypeError):
            payload = {}
        merged = [n for n in str(payload.get("invoiceNumber") or "").split(",") if n]
        for number in invoice_numbers:
            if number not in merged:
                merged.append(number)
        payload["invoiceNumber"] = ",".join(merged)
        projection.payload = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
        projection.updated_at = _iso_now()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="rebind_invoice_entry",
        entity_type="invoice",
        entity_id=invoices[0].id,
        request_id=request_id,
        before={"entryId": draft.id},
        after={
            "entryId": prior.id,
            "reason": "prior_paid_link",
            "invoiceCount": len(invoices),
        },
        entity_version=prior.version,
    )
    await session.flush()

    # 2. 删除收票月挂账草稿（重绑后 delete_draft 的防孤儿票清理不会再动发票主数据）
    await delete_draft(session, draft.id, company_id=company_id, user_id=user_id, request_id=request_id)

    return {
        "invoiceIds": [str(inv.id) for inv in invoices],
        "priorEntry": {
            "id": prior.id,
            "summary": prior.summary,
            "occurredOn": prior.occurred_on.isoformat(),
            "status": prior.status,
        },
    }


async def find_duplicate_prior_payment_invoices(
    session,
    *,
    company_id: str,
    month_start: date,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """扫描当月非公户进项发票（垫付/挂账），核对近 90 天内是否存在未挂票的同销方同金额公户付款。"""
    entries = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.period_start == month_start,
                JournalEntry.status.in_(PAYMENT_LINKABLE_STATUSES),
                JournalEntry.entry_type == "standard",
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).scalars().all()

    records: list[dict[str, Any]] = []
    total_amount = ZERO
    for entry in entries:
        meta = dict(entry.metadata_json or {})
        if str(meta.get("source") or "") != "invoice_import":
            continue
        if str(meta.get("direction") or "") != "expense":
            continue
        if meta.get("is_archive_only") or meta.get("isArchiveOnly"):
            continue
        if str(meta.get("billingStatus") or "") == "reconciled":
            continue
        payer = str(meta.get("payer") or "")
        pay_status = str(meta.get("paymentStatus") or "")
        is_non_bank = (
            pay_status == "unpaid"
            or payer in ("boss_personal", "personal")
            or payer.startswith("employee_personal")
        )
        if not is_non_bank:
            continue

        projection = await session.get(LegacyEntryProjection, entry.id)
        if projection is not None:
            try:
                proj_payload = (
                    json.loads(projection.payload)
                    if isinstance(projection.payload, str)
                    else dict(projection.payload or {})
                )
            except (json.JSONDecodeError, TypeError):
                proj_payload = {}
            if str(proj_payload.get("billingStatus") or "") == "reconciled":
                continue

        prior = await find_prior_payment_candidates(
            session,
            company_id=company_id,
            invoice_entry_id=str(entry.id),
            limit=3,
            lookback_days=lookback_days,
        )
        match = next(
            (
                c
                for c in prior.get("candidates", [])
                if c.get("confidence") == "counterparty" and not str(c.get("entryId") or "").startswith("reim-")
            ),
            None,
        )
        if not match:
            continue
        amt = money(meta.get("amount") or match.get("amount") or ZERO)
        total_amount += amt
        records.append(
            {
                "entryId": str(entry.id),
                "voucherRef": str(entry.voucher_no or ""),
                "businessDate": entry.occurred_on.isoformat(),
                "summary": str(entry.summary or ""),
                "amount": money_text(amt),
                "priorEntryId": str(match["entryId"]),
                "priorOccurredOn": str(match["occurredOn"]),
                "priorSummary": str(match["summary"]),
                "reason": f"疑似与 {match['occurredOn']} 公户付款「{match['summary']}」重复",
            }
        )

    return {
        "count": len(records),
        "amount": money_text(total_amount),
        "records": records,
    }
