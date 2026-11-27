# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Bank matching and WeChat settlement in the authoritative journal transaction."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine, LegacyEntryProjection, ReconciliationLink
from services.accounting_core_service import (
    WRITE_ROLES, _advisory_lock, assert_bank_reconciliable, create_entry, delete_draft, get_journal,
    record_audit, require_role, reverse_journal, serialize_journal, sync_bank_reconciliation,
)
from services.platform_bill_common import WECHAT_CLEARING_CODE

logger = logging.getLogger(__name__)
from services.bank_service import (
    PLATFORM_CLEARING_INFO,
    _account_effect_from_voucher, _entry_bank_effect_from_payload,
    build_group_reconciliation_check,
    platform_clearing_codes_for_payloads,
)

MAX_MATCH_ENTRIES = 500

_INVOICE_PENDING_MARK = "invoicePaymentSettled"


def _pending_payment_kind(payload: dict) -> str:
    """发票待付款(expense, 贷2241)/待收款(income, 借1122)识别；非发票或已转资金科目返回空。"""
    if str(payload.get("source") or "") != "invoice_import":
        return ""
    voucher = payload.get("voucherLines") or {}
    if any(str(line.get("accountCode")) == "2241" for line in (voucher.get("credit") or [])):
        return "expense"
    if any(str(line.get("accountCode")) == "1122" for line in (voucher.get("debit") or [])):
        return "income"
    return ""


def _assert_full_settlement(payload: dict, statement: dict, kind: str) -> None:
    """发票转销必须是全额结算。

    发票凭证的 2241/1122 是整张票的金额；流水金额与票面不一致时（部分付款、
    分期付款、折扣）把整张票换成银行存款会虚增费用/收入，必须显式拒绝，
    让用户拆分或用差额调整凭证处理。
    """
    invoice_amount = abs(money(payload.get("amount") or ZERO))
    statement_amount = abs(money(statement.get("amount") or ZERO))
    if invoice_amount != statement_amount:
        raise AccountingError(
            "INVOICE_PARTIAL_SETTLEMENT",
            f"流水金额 {statement_amount} 与发票金额 {invoice_amount} 不一致，"
            "部分付款/收款需先拆分发票或补差额调整凭证",
            422,
            {
                "invoiceAmount": money_text(invoice_amount),
                "statementAmount": money_text(statement_amount),
                "invoiceEntryId": str(payload.get("id") or ""),
            },
        )


def _swap_pending_payment_lines(payload: dict, kind: str, statement_id: str) -> dict:
    """发票凭证的待付款/待收科目替换为银行存款，并打上转销标记（供取消关联回滚）。"""
    voucher = json.loads(json.dumps(payload.get("voucherLines") or {}))
    if kind == "expense":
        for line in voucher.get("credit") or []:
            if str(line.get("accountCode")) == "2241":
                line["accountCode"] = "1002"
                line["accountName"] = "银行存款"
    else:
        for line in voucher.get("debit") or []:
            if str(line.get("accountCode")) == "1122":
                line["accountCode"] = "1002"
                line["accountName"] = "银行存款"
    rebuilt = {**payload, "voucherLines": voucher, _INVOICE_PENDING_MARK: statement_id}
    rebuilt.pop("billingStatus", None)
    return rebuilt


async def _settle_pending_payment_invoice(
    session: AsyncSession, entry: JournalEntry, payload: dict, statement: dict,
    *, company_id: str, user_id: str, request_id: str,
    backfill_fields: dict | None = None,
    skip_single_amount_check: bool = False,
    is_group: bool = False,
) -> str | None:
    """配对确认=付款事实确认：draft 直接改凭证；posted 生成付款凭证并返回其 ID。"""
    from services.accounting_core_service import update_draft

    kind = _pending_payment_kind(payload)
    if not kind:
        return None
    if not skip_single_amount_check:
        _assert_full_settlement(payload, statement, kind)
    amount_text = str(payload.get("amount") or "0")
    if entry.status in {"draft", "pending_review"}:
        rebuilt = _swap_pending_payment_lines(payload, kind, str(statement["id"]))
        await update_draft(
            session, entry.id,
            {"voucherLines": rebuilt["voucherLines"], _INVOICE_PENDING_MARK: str(statement["id"])},
            company_id=company_id, user_id=user_id, request_id=request_id,
        )
        return ""
    amount_abs = money(amount_text)
    payment_id = f"invoice-payment-{statement['id']}-{entry.id if is_group else entry.id[:8]}"
    if await session.get(JournalEntry, payment_id) is not None:
        payment_id = f"{payment_id}-{uuid4().hex[:6]}"
    direction_out = kind == "expense"
    await create_entry(session, {
        "id": payment_id, "source": "invoice_payment", "sourceType": "invoice_payment",
        "direction": "expense" if direction_out else "income",
        "amount": money_text(amount_abs), "category": "发票付款" if direction_out else "发票收款",
        "title": f"{'付款' if direction_out else '收款'}-{payload.get('title') or '发票'}",
        "occurDate": str(statement["transaction_date"])[:10],
        "paymentDate": str(statement["transaction_date"])[:10],
        "linkedStatementId": str(statement["id"]), "linkedInvoiceId": entry.id,
        "voucherAuthority": "system", "billingStatus": "reconciled",
        **(backfill_fields or {}),
        "voucherLines": {
            "debit": [{"accountCode": "2241" if direction_out else "1002", "amount": money_text(amount_abs)}],
            "credit": [{"accountCode": "1002" if direction_out else "1122", "amount": money_text(amount_abs)}],
        },
    }, company_id=company_id, user_id=user_id, request_id=request_id, confirm=False)
    return payment_id


async def _delete_draft_entry(
    session: AsyncSession, entry_id: str, *, company_id: str, user_id: str, request_id: str,
) -> None:
    """删除未过账的自动衍生凭证及其连接/分录行。"""
    await session.execute(delete(ReconciliationLink).where(
        ReconciliationLink.company_id == company_id,
        ReconciliationLink.journal_entry_id == entry_id,
    ))
    await delete_draft(
        session, entry_id, company_id=company_id, user_id=user_id, request_id=request_id,
    )
    # PostgreSQL cascades these; explicit cleanup also supports SQLite
    # compatibility databases whose foreign-key enforcement was disabled.
    await session.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry_id))


async def _rollback_reimbursement_voucher(
    session: AsyncSession, row_id: str, payload: dict, statement_id: str,
    *, company_id: str, user_id: str, request_id: str,
    reverse_posted: bool = False,
) -> bool:
    """回滚个人垫付报销凭证（reim-*）并恢复被合并冲销的原始垫付条目状态（A3 不变量）。"""
    is_reim = row_id.startswith(f"reim-{statement_id}-") or bool(
        isinstance(payload, dict) and payload.get("reimbursedEntryIds")
    )
    if not is_reim:
        return False

    reim_entry = await session.get(JournalEntry, row_id)
    if reim_entry and reim_entry.company_id == company_id:
        if reim_entry.status in {"draft", "pending_review"}:
            await _delete_draft_entry(
                session, row_id, company_id=company_id, user_id=user_id, request_id=request_id,
            )
        elif reim_entry.status == "posted" and reverse_posted:
            await reverse_journal(
                session, row_id, company_id=company_id, user_id=user_id,
                request_id=f"{request_id}:rev-reim:{row_id}",
                reason="对账撤销个人垫付报销凭证",
            )

    raw_orig_ids = payload.get("reimbursedEntryIds") if isinstance(payload, dict) else None
    if isinstance(raw_orig_ids, list) and raw_orig_ids:
        orig_ids = [str(item).strip() for item in raw_orig_ids if str(item).strip()]
    elif row_id.startswith(f"reim-{statement_id}-") and not row_id.endswith("-batch"):
        orig_ids = [row_id.removeprefix(f"reim-{statement_id}-")]
    else:
        orig_ids = []

    saved_states = payload.get("reimbursedEntryStates") if isinstance(payload, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    for orig_id in orig_ids:
        still_matched = (await session.execute(text(
            "SELECT 1 FROM bank_statement_matches WHERE company_id=:company AND entry_id=:entry AND statement_id!=:statement "
            "UNION ALL "
            "SELECT 1 FROM bank_statements WHERE company_id=:company AND matched_entry_id=:entry AND id!=:statement"
        ), {"company": company_id, "entry": orig_id, "statement": statement_id})).first()
        if still_matched:
            continue
        orig_proj = await session.get(LegacyEntryProjection, orig_id)
        if not orig_proj or orig_proj.company_id != company_id:
            continue
        try:
            orig_payload = json.loads(orig_proj.payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(orig_payload, dict):
            continue
        prev_state = saved_states.get(orig_id) if isinstance(saved_states, dict) else None
        if isinstance(prev_state, dict):
            prev_status = prev_state.get("billingStatus")
            prev_payment_date = prev_state.get("paymentDate")
        else:
            prev_status = (
                "has_invoice"
                if (orig_payload.get("source") == "invoice_import" or orig_payload.get("invoiceNumber"))
                else None
            )
            prev_payment_date = None
        for field, prev_val in (("billingStatus", prev_status), ("paymentDate", prev_payment_date)):
            if prev_val is None:
                orig_payload.pop(field, None)
            else:
                orig_payload[field] = prev_val
        orig_proj.payload = json.dumps(orig_payload, ensure_ascii=False)
        orig_proj.updated_at = now
    return True


async def confirm_statement_match(
    session: AsyncSession, *, company_id: str, user_id: str,
    statement_id: str, entry_ids: list[str] | None, request_id: str,
    auto_post: bool = False,
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
    reverse_posted_bank_entries: bool = False,
) -> dict:
    """None repairs existing matches; [] cancels; other values confirm a group.

    Caller owns the transaction. Original journals (including closed periods)
    remain untouched; the settlement is dated on the actual bank arrival date.

    allow_backfill：流水所属期间已关账时，由用户在导入/对账界面确认后置 true，
    结算凭证补记到最早的未关账期间并写台账。默认 false —— 不静默换期。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    await _advisory_lock(session, f"bank-match:{company_id}:{statement_id}")
    params = {"company": company_id, "statement": statement_id}
    lock = " FOR UPDATE" if session.bind.dialect.name == "postgresql" else ""
    statement = (await session.execute(text(
        "SELECT * FROM bank_statements WHERE company_id=:company AND id=:statement" + lock
    ), params)).mappings().first()
    if not statement:
        raise AccountingError("NOT_FOUND", "银行流水不存在", 404)
    backfill_fields: dict = {}
    if allow_backfill:
        backfill_fields = {
            "allowBackfill": True,
            "backfillReason": backfill_reason or (
                f"对账确认：银行流水 {str(statement['transaction_date'])[:10]} "
                "所属期间已关账，按发现当期补记"
            ),
        }
    rows = (await session.execute(text(
        "SELECT * FROM bank_statement_matches WHERE company_id=:company AND statement_id=:statement"
    ), params)).mappings().all()
    existing = list(dict.fromkeys([str(row["entry_id"]) for row in rows]
        + ([str(statement["matched_entry_id"])] if statement["matched_entry_id"] else [])))
    settlement_prefix = f"bank-settlement-{statement_id}"
    settlement_id = next(
        (eid for eid in existing if eid == settlement_prefix or eid.startswith(f"{settlement_prefix}-")),
        settlement_prefix,
    )
    settlement = await session.get(JournalEntry, settlement_id)
    if settlement and settlement.status == "reversed":
        settlement = None
    if settlement and (settlement.company_id != company_id or
                       (settlement.metadata_json or {}).get("linkedStatementId") != statement_id):
        raise AccountingError("SETTLEMENT_CONFLICT", "结算凭证来源不一致，请人工核对", 409)
    requested = list(dict.fromkeys(str(item).strip() for item in
        (existing if entry_ids is None else entry_ids) if str(item).strip()))
    requested = [
        item for item in requested
        if item != settlement_prefix and not item.startswith(f"{settlement_prefix}-")
    ]
    if len(requested) > MAX_MATCH_ENTRIES:
        raise AccountingError("MATCH_LIMIT", f"一次最多合并 {MAX_MATCH_ENTRIES} 笔账目记录")

    async def sync(ids: list[str]) -> None:
        await sync_bank_reconciliation(session, company_id=company_id, user_id=user_id,
            statement_id=statement_id, entry_ids=ids,
            transaction_date=statement["transaction_date"], description=statement["description"],
            signed_amount=statement["amount"], request_id=request_id)

    if entry_ids == []:
        if not reverse_posted_bank_entries:
            if settlement and settlement.status not in {"draft", "pending_review"}:
                raise AccountingError("SETTLEMENT_POSTED", "结算凭证已过账，请先冲销并核对银行记录，不能直接取消关联", 409)
            for row in rows:
                row_id = str(row["entry_id"])
                if row_id.startswith(f"reim-{statement_id}-"):
                    reim_entry = await session.get(JournalEntry, row_id)
                    if reim_entry and reim_entry.company_id == company_id and reim_entry.status not in {"draft", "pending_review"}:
                        raise AccountingError("SETTLEMENT_POSTED", "报销付款凭证已过账，请先冲销并核对银行记录，不能直接取消关联", 409)
        await sync([])
        handled_ids: list[str] = []
        if settlement:
            if settlement.status in {"draft", "pending_review"}:
                await _delete_draft_entry(
                    session, settlement.id, company_id=company_id, user_id=user_id, request_id=request_id,
                )
            elif settlement.status == "posted" and reverse_posted_bank_entries:
                await reverse_journal(
                    session, settlement.id, company_id=company_id, user_id=user_id,
                    request_id=f"{request_id}:rev-settlement:{settlement.id}",
                    reason="对账撤销平台净额结算凭证",
                )
            handled_ids.append(settlement.id)
        for row in rows:
            row_id = str(row["entry_id"])
            if row_id.startswith(f"invoice-payment-{statement['id']}-"):
                pay_entry = await session.get(JournalEntry, row_id)
                if pay_entry and pay_entry.company_id == company_id:
                    if pay_entry.status in {"draft", "pending_review"}:
                        await _delete_draft_entry(
                            session, row_id, company_id=company_id, user_id=user_id, request_id=request_id,
                        )
                    elif pay_entry.status == "posted" and reverse_posted_bank_entries:
                        await reverse_journal(
                            session, row_id, company_id=company_id, user_id=user_id,
                            request_id=f"{request_id}:rev-inv-pay:{row_id}",
                            reason="对账撤销发票付款凭证",
                        )
                handled_ids.append(row_id)
                continue
            projection = await session.get(LegacyEntryProjection, row["entry_id"])
            if not projection or projection.company_id != company_id:
                continue
            try:
                payload = json.loads(projection.payload)
            except (TypeError, ValueError):
                logger.warning(
                    "结算回滚跳过损坏的投影 payload entry=%s company=%s",
                    row["entry_id"], company_id,
                )
                continue
            if await _rollback_reimbursement_voucher(
                session, row_id, payload, str(statement["id"]),
                company_id=company_id, user_id=user_id, request_id=request_id,
                reverse_posted=reverse_posted_bank_entries,
            ):
                handled_ids.append(row_id)
                continue
            # 配对转销的发票回滚：draft 发票改回待付款（2241/1122）；
            # posted 发票去掉转销标记并删除/冲销对应付款凭证
            pending_mark_statement = str(payload.get(_INVOICE_PENDING_MARK) or "")
            if pending_mark_statement == str(statement["id"]):
                invoice_entry = await session.get(JournalEntry, row["entry_id"])
                if invoice_entry and invoice_entry.status == "draft":
                    from services.accounting_core_service import update_draft as _update_draft

                    voucher = json.loads(json.dumps(payload.get("voucherLines") or {}))
                    kind = "income" if str(payload.get("direction") or "") == "income" else "expense"
                    pending_code, pending_name = ("1122", "应收账款-发票待收") if kind == "income" else ("2241", "其他应付款-发票待付款")
                    side = "debit" if kind == "income" else "credit"
                    for line in voucher.get(side) or []:
                        if str(line.get("accountCode")) == "1002":
                            line["accountCode"] = pending_code
                            line["accountName"] = pending_name
                    await _update_draft(
                        session, row["entry_id"],
                        {"voucherLines": voucher, _INVOICE_PENDING_MARK: None},
                        company_id=company_id, user_id=user_id, request_id=request_id,
                    )
                elif invoice_entry and invoice_entry.status == "posted":
                    payload.pop(_INVOICE_PENDING_MARK, None)
                    projection.payload = json.dumps(payload, ensure_ascii=False)
                    for payment_id in (
                        f"invoice-payment-{statement['id']}-{row['entry_id']}",
                        f"invoice-payment-{statement['id']}-{row['entry_id'][:8]}",
                    ):
                        payment_entry = await session.get(JournalEntry, payment_id)
                        if payment_entry is not None and payment_entry.company_id == company_id:
                            if payment_entry.status == "draft":
                                await _delete_draft_entry(
                                    session, payment_id, company_id=company_id, user_id=user_id, request_id=request_id,
                                )
                                handled_ids.append(payment_id)
                                break
                            if payment_entry.status == "posted" and reverse_posted_bank_entries:
                                await reverse_journal(
                                    session, payment_id, company_id=company_id, user_id=user_id,
                                    request_id=f"{request_id}:rev-inv-pay:{payment_id}",
                                    reason="对账撤销发票付款凭证",
                                )
                                handled_ids.append(payment_id)
                                break
            for field, column in (("billingStatus", "previous_billing_status"),
                                  ("paymentDate", "previous_payment_date")):
                if row[column] is None:
                    payload.pop(field, None)
                else:
                    payload[field] = row[column]
            projection.payload = json.dumps(payload, ensure_ascii=False)
        await session.execute(text("DELETE FROM bank_statement_matches WHERE company_id=:company AND statement_id=:statement"), params)
        await session.execute(text("UPDATE bank_statements SET matched_entry_id=NULL WHERE company_id=:company AND id=:statement"), params)
        if reverse_posted_bank_entries:
            for eid in existing:
                if eid in handled_ids:
                    continue
                still_other = (await session.execute(text(
                    "SELECT 1 FROM bank_statement_matches WHERE company_id=:company AND entry_id=:entry AND statement_id!=:statement "
                    "UNION ALL "
                    "SELECT 1 FROM bank_statements WHERE company_id=:company AND matched_entry_id=:entry AND id!=:statement"
                ), {"company": company_id, "entry": eid, "statement": statement_id})).first()
                if still_other:
                    continue
                bank_entry = await session.get(JournalEntry, eid)
                if not bank_entry or bank_entry.company_id != company_id:
                    continue
                meta = bank_entry.metadata_json or {}
                src = str(meta.get("source") or meta.get("sourceType") or "")
                if src != "bank_import":
                    continue
                if bank_entry.status in {"draft", "pending_review"}:
                    await _delete_draft_entry(
                        session, eid, company_id=company_id, user_id=user_id, request_id=request_id,
                    )
                    handled_ids.append(eid)
                elif bank_entry.status == "posted":
                    await reverse_journal(
                        session, eid, company_id=company_id, user_id=user_id,
                        request_id=f"{request_id}:rev-bank:{eid}",
                        reason="对账撤销原入账凭证",
                    )
                    handled_ids.append(eid)
        await session.flush()
        return {"status": "unmatched", "entryId": None, "entryIds": existing, "handledEntryIds": handled_ids}

    if not requested:
        raise AccountingError("EMPTY_MATCH", "没有可关联的原收款或手续费")
    if existing and set(existing) - {settlement_id} != set(requested):
        raise AccountingError("MATCH_CONFLICT", "该流水已匹配其他账目记录，请先取消关联", 409)
    prepared: list[tuple[JournalEntry, dict, str]] = []
    for entry_id in sorted(requested):
        # Entry locks also serialize attempts to match it to two different banks.
        entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
        if entry.status not in {"draft", "pending_review", "posted"}:
            raise AccountingError("INVALID_MATCH", "已作废或冲销的凭证不能用于新对账", 409)
        conflict = (await session.execute(text(
            "SELECT statement_id FROM bank_statement_matches WHERE company_id=:company "
            "AND entry_id=:entry AND statement_id!=:statement UNION ALL "
            "SELECT id FROM bank_statements WHERE company_id=:company "
            "AND matched_entry_id=:entry AND id!=:statement"
        ), {**params, "entry": entry_id})).first()
        if conflict:
            raise AccountingError("MATCH_CONFLICT", "其中一笔账目已匹配其他银行流水", 409)
        payload = await serialize_journal(session, entry)
        if entry_ids is not None:
            # 新确认（entry_ids=None 是修复模式，豁免）硬校验：底稿与跨期凭证不能当
            # 核销凭证。建议层护栏（_entry_not_after_statement / isArchiveOnly 过滤）
            # 只是"不再推荐"，权威层必须拒绝——否则旧建议/旧前端仍能造出
            # 5 月流水配 6 月底稿这种关不了账的配对（2026-10-31 实测踩坑）。
            from services.bank_service import _entry_not_after_statement

            if payload.get("isArchiveOnly"):
                raise AccountingError(
                    "ARCHIVE_ONLY_MATCH",
                    "仅归档底稿凭证不参与银行核销，请在付款当期确认收支入账",
                    409,
                )
            if not _entry_not_after_statement(payload.get("occurDate"), str(statement["transaction_date"])):
                raise AccountingError(
                    "CROSS_PERIOD_MATCH",
                    "凭证日期晚于流水日期（先付款后收票），请在付款当期确认收支入账，不能跨期配对",
                    409,
                )
        payer = str(payload.get("payer") or "")
        if (payer in {"boss_personal", "employee_personal"} or payer.startswith("employee_personal:")) and \
                _entry_bank_effect_from_payload(payload) == ZERO:
            raise AccountingError("PERSONAL_REIMBURSEMENT", "垫付报销请在银行流水页手动确认")
        prepared.append((entry, payload, _pending_payment_kind(payload)))

    group_mode = len(prepared) > 1
    if group_mode and any(kind for _, _, kind in prepared):
        # 两阶段校验：先按模拟转销（2241/1122 -> 1002）核对整组银行净额与方向，
        # 避免先转销前几张 draft/posted 发票后才发现整组差额不平。
        simulated_payloads = [
            _swap_pending_payment_lines(payload, kind, str(statement["id"])) if kind else payload
            for _, payload, kind in prepared
        ]
        precheck = build_group_reconciliation_check(statement["amount"], simulated_payloads)
        if not precheck["amountMatches"] or not precheck["directionMatches"]:
            group_amount = abs(money(precheck["entryBankEffect"]))
            stmt_abs = abs(money(precheck["statementAmount"]))
            raise AccountingError(
                "INVOICE_PARTIAL_SETTLEMENT",
                f"流水金额 {stmt_abs} 与合并发票净额 {group_amount} 不一致，"
                "部分付款/收款或差额需先拆分发票或补差额调整凭证",
                422,
                {
                    "invoiceAmount": money_text(group_amount),
                    "statementAmount": money_text(stmt_abs),
                    "invoiceEntryIds": [entry.id for entry, _, kind in prepared if kind],
                },
            )

    payloads = []
    reconcile_ids: list[str] = []                      # 进入核销校验的代表（posted 发票由付款凭证代表）
    draft_invoice_ids: list[str] = []                  # 已就地转销的 draft 发票（回滚用）
    posted_invoice_ids: list[str] = []                 # 生成付款凭证的 posted 发票（回滚用）
    for entry, payload, _kind in prepared:
        # 发票待付款/待收款：配对确认即付款事实确认。
        # draft 发票就地改凭证（2241→1002）留在核销列表；posted 发票不可变，
        # 生成付款凭证并由其代表核销（原发票仅打标记，journal 不动）。
        payment_id = await _settle_pending_payment_invoice(
            session, entry, payload, statement,
            company_id=company_id, user_id=user_id, request_id=request_id,
            backfill_fields=backfill_fields,
            skip_single_amount_check=group_mode,
            is_group=group_mode,
        )
        if payment_id:
            payment_entry = await get_journal(session, payment_id, company_id=company_id, lock=True)
            payloads.append(await serialize_journal(session, payment_entry))
            reconcile_ids.append(payment_id)
            posted_invoice_ids.append(entry.id)
        elif payment_id == "":
            payloads.append(await serialize_journal(session, entry))
            reconcile_ids.append(entry.id)
            draft_invoice_ids.append(entry.id)
        else:
            payloads.append(payload)
            reconcile_ids.append(entry.id)
    await assert_bank_reconciliable(session, company_id=company_id, entry_ids=reconcile_ids)
    amount = money(statement["amount"])
    check = build_group_reconciliation_check(amount, payloads)
    if not check["amountMatches"] or not check["directionMatches"]:
        raise AccountingError("AMOUNT_MISMATCH", "所选账目净额与银行流水金额或方向不一致")
    bank_net = money(sum((_entry_bank_effect_from_payload(p) for p in payloads), ZERO))
    clearing_codes = platform_clearing_codes_for_payloads(payloads)
    platform_sources = {
        str(p.get("source") or "") for p in payloads
    } & {"wechat_bill", "alipay_bill"}
    if len(clearing_codes) > 1 or len(platform_sources) > 1:
        raise AccountingError(
            "SETTLEMENT_PLATFORM_MIXED",
            "所选账目混用了多个平台待结算科目，请按平台分开对账",
            409,
        )
    clearing_info = PLATFORM_CLEARING_INFO.get(next(iter(clearing_codes), ""))
    if clearing_info is None:
        platform = next(iter(platform_sources), "wechat_bill").removesuffix("_bill")
        clearing_info = next(
            (info for info in PLATFORM_CLEARING_INFO.values() if info["platform"] == platform),
            PLATFORM_CLEARING_INFO[WECHAT_CLEARING_CODE],
        )
    clearing_code = clearing_info["clearingCode"]
    clearing_net = money(sum((_account_effect_from_voucher(p.get("voucherLines") or {}, clearing_code) for p in payloads), ZERO))
    created = False
    if bank_net == ZERO and clearing_net != ZERO:
        if amount == ZERO:
            raise AccountingError("ZERO_SETTLEMENT", "零金额流水不能生成结算凭证")
        if not settlement:
            if await session.get(JournalEntry, settlement_id) is not None:
                settlement_id = f"{settlement_prefix}-{uuid4().hex[:6]}"
            value = money_text(abs(amount))
            settlement = await create_entry(session, {
                "id": settlement_id,
                "source": clearing_info["reconciliationType"],
                "sourceType": clearing_info["reconciliationType"],
                "direction": "owner_transfer", "amount": value, "category": clearing_info["category"],
                "title": f"{clearing_info['titlePrefix']}-{str(statement['transaction_date'])[:10]}",
                "occurDate": str(statement["transaction_date"])[:10],
                "paymentDate": str(statement["transaction_date"])[:10],
                "reconciliationType": clearing_info["reconciliationType"], "linkedStatementId": statement_id,
                "originalEntryIds": requested, "settlementNetAmount": money_text(amount),
                "voucherAuthority": "system", "billingStatus": "reconciled",
                **backfill_fields,
                "voucherLines": {
                    "debit": [{"accountCode": "1002" if amount > ZERO else clearing_code, "amount": value}],
                    "credit": [{"accountCode": clearing_code if amount > ZERO else "1002", "amount": value}],
                },
            }, company_id=company_id, user_id=user_id, request_id=request_id, confirm=auto_post)
            created = True
        elif str((settlement.metadata_json or {}).get("reconciliationType") or "") not in {
            "", clearing_info["reconciliationType"],
        }:
            raise AccountingError("SETTLEMENT_CONFLICT", "已有结算凭证的平台类型与本次对账不一致，请人工核对", 409)
        settlement_payload = await serialize_journal(session, settlement)
        if settlement.status not in {"draft", "pending_review", "posted"} or \
                _entry_bank_effect_from_payload(settlement_payload) != amount or \
                _account_effect_from_voucher(settlement_payload.get("voucherLines") or {}, clearing_code) != -amount:
            raise AccountingError("SETTLEMENT_CONFLICT", "已有结算凭证与银行净额不一致，请人工核对", 409)
    elif settlement:
        raise AccountingError("SETTLEMENT_CONFLICT", "原凭证已有银行影响，不能重复生成结算凭证", 409)

    linked = reconcile_ids + ([settlement_id] if settlement else [])
    now = datetime.now(timezone.utc).isoformat()
    for entry_id in dict.fromkeys(linked + draft_invoice_ids + posted_invoice_ids):
        projection = await session.get(LegacyEntryProjection, entry_id)
        try:
            payload = json.loads(projection.payload)
        except (TypeError, ValueError) as exc:
            # payload 随后会被改写回投影：坏数据不能静默覆盖（丢失原始现场），
            # 也绝不能让未知状态的 payload 混入财务写路径——整体回滚并提示人工修复。
            logger.error("结算确认遇到损坏的投影 payload entry=%s company=%s", entry_id, company_id)
            raise AccountingError(
                "PROJECTION_CORRUPTED",
                f"凭证 {entry_id} 的底层数据损坏，已中止结算，请人工核对该凭证",
                500,
            ) from exc
        await session.execute(text(
            "INSERT INTO bank_statement_matches (company_id,statement_id,entry_id,previous_billing_status,previous_payment_date,created_at) "
            "VALUES (:company,:statement,:entry,:billing,:payment,:now) ON CONFLICT DO NOTHING"
        ), {**params, "entry": entry_id, "billing": payload.get("billingStatus"),
            "payment": payload.get("paymentDate"), "now": now})
        payload["billingStatus"] = "reconciled"
        payload.setdefault("paymentDate", str(statement["transaction_date"])[:10])
        if entry_id in posted_invoice_ids:
            payload[_INVOICE_PENDING_MARK] = str(statement["id"])
        projection.payload = json.dumps(payload, ensure_ascii=False)
        projection.updated_at = now
    await session.execute(text("UPDATE bank_statements SET matched_entry_id=:entry WHERE company_id=:company AND id=:statement"), {**params, "entry": reconcile_ids[0]})
    await sync(linked)
    response = {"status": "matched", "statementId": statement_id, "entryId": linked[0],
        "entryIds": linked, "originalEntryIds": requested,
        "settlementEntryId": settlement.id if settlement else None,
        "settlementStatus": settlement.status if settlement else None,
        "settlementCreated": created}
    await record_audit(session, company_id=company_id, user_id=user_id,
        action="confirm_bank_settlement", entity_type="bank_statement", entity_id=statement_id,
        request_id=request_id, after=response)
    await session.flush()
    return response
