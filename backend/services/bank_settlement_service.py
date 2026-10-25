# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Bank matching and WeChat settlement in the authoritative journal transaction."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine, LegacyEntryProjection, ReconciliationLink
from services.accounting_core_service import (
    WRITE_ROLES, _advisory_lock, assert_bank_reconciliable, create_entry, delete_draft, get_journal,
    record_audit, require_role, serialize_journal, sync_bank_reconciliation,
)
from services.wechat_bill_service import WECHAT_CLEARING_CODE
from services.bank_service import (
    _account_effect_from_voucher, _entry_bank_effect_from_payload,
    build_group_reconciliation_check,
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
) -> str | None:
    """配对确认=付款事实确认：draft 直接改凭证；posted 生成付款凭证并返回其 ID。"""
    from services.accounting_core_service import update_draft

    kind = _pending_payment_kind(payload)
    if not kind:
        return ""
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
    payment_id = f"invoice-payment-{statement['id']}-{entry.id[:8]}"
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


async def confirm_statement_match(
    session: AsyncSession, *, company_id: str, user_id: str,
    statement_id: str, entry_ids: list[str] | None, request_id: str,
    auto_post: bool = False,
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
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
    settlement_id = f"bank-settlement-{statement_id}"
    settlement = await session.get(JournalEntry, settlement_id)
    if settlement and (settlement.company_id != company_id or
                       (settlement.metadata_json or {}).get("linkedStatementId") != statement_id):
        raise AccountingError("SETTLEMENT_CONFLICT", "结算凭证来源不一致，请人工核对", 409)
    requested = list(dict.fromkeys(str(item).strip() for item in
        (existing if entry_ids is None else entry_ids) if str(item).strip()))
    requested = [item for item in requested if item != settlement_id]
    if len(requested) > MAX_MATCH_ENTRIES:
        raise AccountingError("MATCH_LIMIT", f"一次最多合并 {MAX_MATCH_ENTRIES} 笔账目记录")

    async def sync(ids: list[str]) -> None:
        await sync_bank_reconciliation(session, company_id=company_id, user_id=user_id,
            statement_id=statement_id, entry_ids=ids,
            transaction_date=statement["transaction_date"], description=statement["description"],
            signed_amount=statement["amount"], request_id=request_id)

    if entry_ids == []:
        if settlement and settlement.status not in {"draft", "pending_review"}:
            raise AccountingError("SETTLEMENT_POSTED", "结算凭证已过账，请先冲销并核对银行记录，不能直接取消关联", 409)
        await sync([])
        if settlement:
            await session.execute(delete(ReconciliationLink).where(
                ReconciliationLink.company_id == company_id,
                ReconciliationLink.journal_entry_id == settlement_id))
            await delete_draft(session, settlement_id, company_id=company_id,
                user_id=user_id, request_id=request_id)
            # PostgreSQL cascades these; explicit cleanup also supports SQLite
            # compatibility databases whose foreign-key enforcement was disabled.
            await session.execute(delete(JournalLine).where(JournalLine.journal_entry_id == settlement_id))
        for row in rows:
            projection = await session.get(LegacyEntryProjection, row["entry_id"])
            if not projection or projection.company_id != company_id:
                continue
            payload = json.loads(projection.payload)
            # 配对转销的发票回滚：draft 发票改回待付款（2241/1122）；
            # posted 发票去掉转销标记并删除对应付款凭证（draft 可删）
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
                    payment_id = f"invoice-payment-{statement['id']}-{row['entry_id'][:8]}"
                    payment_entry = await session.get(JournalEntry, payment_id)
                    if payment_entry is not None and payment_entry.company_id == company_id                             and payment_entry.status == "draft":
                        await delete_draft(session, payment_id, company_id=company_id,
                            user_id=user_id, request_id=request_id)
                        await session.execute(delete(JournalLine).where(
                            JournalLine.journal_entry_id == payment_id))
            for field, column in (("billingStatus", "previous_billing_status"),
                                  ("paymentDate", "previous_payment_date")):
                if row[column] is None:
                    payload.pop(field, None)
                else:
                    payload[field] = row[column]
            projection.payload = json.dumps(payload, ensure_ascii=False)
        await session.execute(text("DELETE FROM bank_statement_matches WHERE company_id=:company AND statement_id=:statement"), params)
        await session.execute(text("UPDATE bank_statements SET matched_entry_id=NULL WHERE company_id=:company AND id=:statement"), params)
        await session.flush()
        return {"status": "unmatched", "entryId": None, "entryIds": existing}

    if not requested:
        raise AccountingError("EMPTY_MATCH", "没有可关联的原收款或手续费")
    if existing and set(existing) - {settlement_id} != set(requested):
        raise AccountingError("MATCH_CONFLICT", "该流水已匹配其他账目记录，请先取消关联", 409)
    payloads = []
    reconcile_ids: list[str] = []                      # 进入核销校验的代表（posted 发票由付款凭证代表）
    draft_invoice_ids: list[str] = []                  # 已就地转销的 draft 发票（回滚用）
    posted_invoice_ids: list[str] = []                 # 生成付款凭证的 posted 发票（回滚用）
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
        payer = str(payload.get("payer") or "")
        if (payer in {"boss_personal", "employee_personal"} or payer.startswith("employee_personal:")) and \
                _entry_bank_effect_from_payload(payload) == ZERO:
            raise AccountingError("PERSONAL_REIMBURSEMENT", "垫付报销请在银行流水页手动确认")
        # 发票待付款/待收款：配对确认即付款事实确认。
        # draft 发票就地改凭证（2241→1002）留在核销列表；posted 发票不可变，
        # 生成付款凭证并由其代表核销（原发票仅打标记，journal 不动）。
        payment_id = await _settle_pending_payment_invoice(
            session, entry, payload, statement,
            company_id=company_id, user_id=user_id, request_id=request_id,
            backfill_fields=backfill_fields,
        )
        if payment_id:
            payment_entry = await get_journal(session, payment_id, company_id=company_id, lock=True)
            payloads.append(await serialize_journal(session, payment_entry))
            reconcile_ids.append(payment_id)
            posted_invoice_ids.append(entry_id)
        elif payment_id == "":
            payloads.append(await serialize_journal(session, entry))
            reconcile_ids.append(entry_id)
            draft_invoice_ids.append(entry_id)
        else:
            payloads.append(payload)
            reconcile_ids.append(entry_id)
    await assert_bank_reconciliable(session, company_id=company_id, entry_ids=reconcile_ids)
    amount = money(statement["amount"])
    check = build_group_reconciliation_check(amount, payloads)
    if not check["amountMatches"] or not check["directionMatches"]:
        raise AccountingError("AMOUNT_MISMATCH", "所选账目净额与银行流水金额或方向不一致")
    bank_net = money(sum((_entry_bank_effect_from_payload(p) for p in payloads), ZERO))
    clearing_net = money(sum((_account_effect_from_voucher(p.get("voucherLines") or {}, WECHAT_CLEARING_CODE) for p in payloads), ZERO))
    created = False
    if bank_net == ZERO and clearing_net != ZERO:
        if amount == ZERO:
            raise AccountingError("ZERO_SETTLEMENT", "零金额流水不能生成结算凭证")
        if not settlement:
            value = money_text(abs(amount))
            settlement = await create_entry(session, {
                "id": settlement_id, "source": "wechat_settlement", "sourceType": "wechat_settlement",
                "direction": "owner_transfer", "amount": value, "category": "微信支付结算",
                "title": f"微信支付净额结算-{str(statement['transaction_date'])[:10]}",
                "occurDate": str(statement["transaction_date"])[:10],
                "paymentDate": str(statement["transaction_date"])[:10],
                "reconciliationType": "wechat_settlement", "linkedStatementId": statement_id,
                "originalEntryIds": requested, "settlementNetAmount": money_text(amount),
                "voucherAuthority": "system", "billingStatus": "reconciled",
                **backfill_fields,
                "voucherLines": {
                    "debit": [{"accountCode": "1002" if amount > ZERO else WECHAT_CLEARING_CODE, "amount": value}],
                    "credit": [{"accountCode": WECHAT_CLEARING_CODE if amount > ZERO else "1002", "amount": value}],
                },
            }, company_id=company_id, user_id=user_id, request_id=request_id, confirm=auto_post)
            created = True
        settlement_payload = await serialize_journal(session, settlement)
        if settlement.status not in {"draft", "pending_review", "posted"} or \
                _entry_bank_effect_from_payload(settlement_payload) != amount or \
                _account_effect_from_voucher(settlement_payload.get("voucherLines") or {}, WECHAT_CLEARING_CODE) != -amount:
            raise AccountingError("SETTLEMENT_CONFLICT", "已有结算凭证与银行净额不一致，请人工核对", 409)
    elif settlement:
        raise AccountingError("SETTLEMENT_CONFLICT", "原凭证已有银行影响，不能重复生成结算凭证", 409)

    linked = reconcile_ids + ([settlement_id] if settlement else [])
    now = datetime.now(timezone.utc).isoformat()
    for entry_id in dict.fromkeys(linked + draft_invoice_ids + posted_invoice_ids):
        projection = await session.get(LegacyEntryProjection, entry_id)
        payload = json.loads(projection.payload)
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
