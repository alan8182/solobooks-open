from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    AccountingError,
    DUPLICATE_SOURCE,
    ENTRY_POSTED_IMMUTABLE,
    FORBIDDEN,
    NOT_FOUND,
    PERIOD_CLOSED,
    REVIEW_REQUIRED,
    STEP_UP_REQUIRED,
    UNBALANCED_ENTRY,
)
from core.money import ZERO, money, money_text, positive_money
from db.models import (
    AccountingPeriod,
    AuditEvent,
    AuthUser,
    ChartAccount,
    Company,
    CompanyMember,
    IdempotencyRecord,
    JournalEntry,
    JournalLine,
    LegacyEntryProjection,
    PeriodCloseRun,
    ReconciliationLink,
    RdJournalLineDetail,
    RdProject,
    SourceTransaction,
    StepUpToken,
    VoucherSequence,
)
from services.account_mapper import CATEGORY_TO_ACCOUNT, COUNTERPARTY_ACCOUNTS, get_voucher_hint


SHANGHAI = ZoneInfo("Asia/Shanghai")
POSTING_ROLES = {"owner", "accountant"}
WRITE_ROLES = {"owner", "accountant", "preparer"}
ACTIVE_ENTRY_STATUSES = {"draft", "pending_review", "posted", "reversed"}
RD_ACCOUNT_BY_TYPE = {
    "personnel": "530101",
    "direct_input": "530102",
    "depreciation_amortization": "530103",
    "design": "530104",
    "testing": "530105",
    "outsourced": "530106",
    "other": "530199",
}
RD_ACCOUNT_NAMES = {
    "personnel": "研发支出-人员人工",
    "direct_input": "研发支出-直接投入",
    "depreciation_amortization": "研发支出-折旧与摊销",
    "design": "研发支出-设计费",
    "testing": "研发支出-试验费",
    "outsourced": "研发支出-委外研发费",
    "other": "研发支出-其他研发费用",
}
RD_EXPENSE_TYPE_BY_ACCOUNT = {code: expense_type for expense_type, code in RD_ACCOUNT_BY_TYPE.items()}
RD_ALLOCATION_METHODS = {"direct", "timesheet", "usage", "area", "quantity", "other"}


def apply_rd_detail_to_voucher(payload: dict[str, Any], voucher: dict[str, Any]) -> dict[str, Any]:
    """Attach authoritative R&D project metadata to a generated voucher."""
    project_id = str(payload.get("rdProjectId") or "").strip()
    expense_type = str(payload.get("rdExpenseType") or "").strip()
    if not (project_id or expense_type):
        return voucher
    if not project_id or expense_type not in RD_ACCOUNT_BY_TYPE:
        raise AccountingError("RD_DETAIL_REQUIRED", "研发费用必须选择研发项目和费用类别", 422)
    debit_lines = voucher.get("debit") or []
    if not debit_lines:
        raise AccountingError("RD_DEBIT_REQUIRED", "研发费用凭证缺少借方分录", 422)
    amount = payload.get("amount") or ZERO
    debit_lines[0] = {
        **debit_lines[0],
        "accountCode": RD_ACCOUNT_BY_TYPE[expense_type],
        "accountName": RD_ACCOUNT_NAMES[expense_type],
        "rdDetail": {
            "projectId": project_id,
            "expenseType": expense_type,
            "originalAmount": payload.get("rdOriginalAmount") or amount,
            "allocationMethod": payload.get("rdAllocationMethod") or "direct",
            "allocationBasis": payload.get("rdAllocationBasis"),
            "notes": payload.get("rdNotes"),
        },
    }
    return voucher


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return money_text(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(SHANGHAI).date()
    if len(raw) == 7:
        raw = f"{raw}-01"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise AccountingError("INVALID_ACCOUNTING_DATE", "会计日期格式必须为 YYYY-MM-DD") from exc


def period_start_for(value: Any) -> date:
    occurred_on = _parse_date(value)
    return occurred_on.replace(day=1)


MONEY_ACCOUNT_CODES = ("1002", "1012")


async def _entry_money_link_amount(session: AsyncSession, company_id: str, entry_id: str) -> Decimal:
    """核销关联金额：优先 1002 银行存款净额，为 0 时回落 1012 其他货币资金（微信待结算）净额。

    微信商户资金先归集到 1012，提现结算才进 1002，与匹配层
    _entry_group_reconciliation_effect 的口径保持一致。
    """
    for code in MONEY_ACCOUNT_CODES:
        amount = (
            await session.execute(
                select(func.coalesce(func.sum(JournalLine.amount), 0)).where(
                    JournalLine.journal_entry_id == entry_id,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == code,
                )
            )
        ).scalar_one()
        link_amount = abs(money(amount))
        if link_amount != ZERO:
            return link_amount
    return ZERO


async def assert_bank_reconciliable(session: AsyncSession, *, company_id: str, entry_ids: list[str]) -> None:
    """在提交匹配状态前预检 sync_bank_reconciliation 的资金科目规则。

    匹配状态（bank_statement_matches）与核销关联分属两个事务，
    若等 sync_bank_reconciliation 才校验，失败会把流水卡在已匹配。
    """
    for entry_id in entry_ids:
        entry = await session.get(JournalEntry, entry_id)
        if entry is None or entry.company_id != company_id:
            raise AccountingError(NOT_FOUND, "银行核销对应的权威凭证不存在", 404)
        if await _entry_money_link_amount(session, company_id, entry_id) == ZERO:
            raise AccountingError(
                "BANK_EFFECT_REQUIRED",
                "所选账目的凭证未包含银行存款或其他货币资金科目影响，请先补正凭证再对账",
            )


async def sync_bank_reconciliation(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    statement_id: str,
    entry_ids: list[str],
    transaction_date: Any,
    description: str,
    signed_amount: Any,
    request_id: str,
) -> None:
    """Reflect a confirmed bank match in the authoritative source/link model."""
    await ensure_tenant(session, company_id, user_id)
    source = (
        await session.execute(
            select(SourceTransaction).where(
                SourceTransaction.company_id == company_id,
                SourceTransaction.source_type == "bank",
                SourceTransaction.external_key == statement_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = SourceTransaction(
            id=f"source-{statement_id}",
            company_id=company_id,
            source_type="bank",
            external_key=statement_id,
            occurred_on=_parse_date(transaction_date),
            description=description or "银行流水",
            amount=abs(money(signed_amount)),
            currency="CNY",
            original_amount=abs(money(signed_amount)),
            raw_payload={
                "bankStatementId": statement_id,
                "signedAmount": money_text(signed_amount),
            },
            status="candidate",
            created_by=user_id,
        )
        session.add(source)
        await session.flush()

    existing_links = (
        await session.execute(
            select(ReconciliationLink).where(
                ReconciliationLink.company_id == company_id,
                ReconciliationLink.source_transaction_id == source.id,
            )
        )
    ).scalars().all()
    requested = set(entry_ids)
    for link in existing_links:
        link.status = "confirmed" if link.journal_entry_id in requested else "void"

    existing_by_entry = {link.journal_entry_id: link for link in existing_links}
    for entry_id in entry_ids:
        entry = await session.get(JournalEntry, entry_id)
        if entry is None or entry.company_id != company_id:
            raise AccountingError(NOT_FOUND, "银行核销对应的权威凭证不存在", 404)
        bank_amount = (
            await session.execute(
                select(func.coalesce(func.sum(JournalLine.amount), 0)).where(
                    JournalLine.journal_entry_id == entry_id,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == "1002",
                )
            )
        ).scalar_one()
        link_amount = abs(money(bank_amount))
        if link_amount == ZERO:
            link_amount = await _entry_money_link_amount(session, company_id, entry_id)
        if link_amount == ZERO:
            raise AccountingError("BANK_EFFECT_REQUIRED", "核销凭证必须包含银行存款或其他货币资金科目影响")
        current = existing_by_entry.get(entry_id)
        if current is None:
            session.add(
                ReconciliationLink(
                    company_id=company_id,
                    source_transaction_id=source.id,
                    journal_entry_id=entry_id,
                    reconciliation_type="bank_statement",
                    amount=link_amount,
                    status="confirmed",
                    created_by=user_id,
                )
            )
        else:
            current.amount = link_amount
            current.status = "confirmed"

    source.status = "reconciled" if entry_ids else "candidate"
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="reconcile_bank_statement" if entry_ids else "unreconcile_bank_statement",
        entity_type="source_transaction",
        entity_id=source.id,
        request_id=request_id,
        after={"status": source.status, "entryIds": entry_ids},
    )


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


async def ensure_tenant(session: AsyncSession, company_id: str, user_id: str) -> tuple[Company, CompanyMember]:
    """Ensure development/test identities and production owner membership exist."""
    user = await session.get(AuthUser, user_id)
    if user is None:
        now = _iso_now()
        user = AuthUser(
            user_id=user_id,
            openid="",
            phone="",
            phone_verified_at="",
            wechat_openid_bound_at="",
            current_company_id=company_id,
            nickname="",
            avatar="",
            created_at=now,
            last_login_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()

    company = await session.get(Company, company_id)
    if company is None:
        now = _iso_now()
        company = Company(
            company_id=company_id,
            owner_user_id=user_id,
            payload={},
            base_currency="CNY",
            accounting_standard="small_enterprise_cn",
            posting_policy="owner_self_post",
            timezone="Asia/Shanghai",
            created_at=now,
            updated_at=now,
        )
        session.add(company)
        await session.flush()

    member = await session.get(CompanyMember, {"company_id": company_id, "user_id": user_id})
    if member is None and company.owner_user_id == user_id:
        member = CompanyMember(company_id=company_id, user_id=user_id, role="owner", status="active")
        session.add(member)
        await session.flush()
    if member is None or member.status != "active":
        raise AccountingError(FORBIDDEN, "无权访问该企业账套", 403)

    await seed_accounts(session, company_id)
    return company, member


def _seed_account_definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    seed_path = Path(__file__).resolve().parent.parent / "data" / "chart_of_accounts.json"
    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        for group_name, accounts in data.get("accounts", {}).items():
            for code, info in accounts.items():
                definitions[str(code)] = {
                    "name": str(info.get("name") or code),
                    "category": str(group_name),
                    "type": str(info.get("type") or ""),
                    "parent_code": str(info.get("parentCode") or "") or None,
                    "level": 2 if info.get("parentCode") else 1,
                    "is_leaf": bool(info.get("isLeaf", True)),
                }
    except (OSError, ValueError, TypeError):
        pass
    for info in list(CATEGORY_TO_ACCOUNT.values()) + list(COUNTERPARTY_ACCOUNTS.values()):
        code = str(info.get("accountCode") or "")
        if not code:
            continue
        definitions.setdefault(
            code,
            {
                "name": str(info.get("accountName") or code),
                "category": str(info.get("accountType") or ""),
                "type": str(info.get("accountType") or ""),
            },
        )
    definitions.setdefault("1002", {"name": "银行存款", "category": "资产", "type": "流动资产"})
    definitions.setdefault("5699", {"name": "管理费用-其他", "category": "损益", "type": "费用"})
    return definitions


async def seed_accounts(session: AsyncSession, company_id: str) -> None:
    existing_accounts = list(
        (await session.execute(select(ChartAccount).where(ChartAccount.company_id == company_id))).scalars().all()
    )
    existing = {account.code: account for account in existing_accounts}
    now = _iso_now()
    for code, info in _seed_account_definitions().items():
        if code in existing:
            if code == "5301" and existing[code].name == "研发支出-费用化支出":
                existing[code].name = info["name"]
            continue
        normal_side = "credit" if code.startswith(("2", "3", "4", "6")) else "debit"
        session.add(
            ChartAccount(
                id=f"acct-{company_id}-{code}",
                company_id=company_id,
                code=code,
                name=info["name"],
                parent_code=info.get("parent_code"),
                level=int(info.get("level") or 1),
                category=info["category"],
                type=info["type"],
                normal_side=normal_side,
                is_leaf=bool(info.get("is_leaf", True)),
                is_system=True,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()


async def member_role(session: AsyncSession, company_id: str, user_id: str) -> str:
    _, member = await ensure_tenant(session, company_id, user_id)
    return member.role


async def require_role(session: AsyncSession, company_id: str, user_id: str, roles: set[str]) -> str:
    role = await member_role(session, company_id, user_id)
    if role not in roles:
        raise AccountingError(FORBIDDEN, "当前角色无权执行此操作", 403, {"required": sorted(roles)})
    return role


async def get_or_create_period(
    session: AsyncSession,
    company_id: str,
    value: Any,
    *,
    lock: bool = False,
) -> AccountingPeriod:
    month = period_start_for(value)
    await _advisory_lock(session, f"period:{company_id}:{month.isoformat()}")
    query = select(AccountingPeriod).where(
        AccountingPeriod.company_id == company_id,
        AccountingPeriod.period_start == month,
    )
    if lock:
        query = query.with_for_update()
    period = (await session.execute(query)).scalar_one_or_none()
    if period is None:
        period = AccountingPeriod(company_id=company_id, period_start=month, status="open", version=1)
        session.add(period)
        await session.flush()
    return period


def assert_period_writable(period: AccountingPeriod) -> None:
    if period.status in {"closing", "closed"}:
        raise AccountingError(PERIOD_CLOSED, "该会计期间已锁定，不能修改账务", 409, {"period": period.period_start.isoformat()[:7]})


async def record_audit(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    request_id: str,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    entity_version: int | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=str(uuid4()),
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_version=entity_version,
        before=_json_safe(before) if before is not None else None,
        after=_json_safe(after) if after is not None else None,
        reason=reason,
        request_id=request_id,
    )
    if session.bind and session.bind.dialect.name == "sqlite":
        maximum = (await session.execute(select(func.max(AuditEvent.sequence_no)))).scalar_one_or_none() or 0
        event.sequence_no = int(maximum) + 1
    session.add(event)
    await session.flush()
    return event


async def _load_accounts(session: AsyncSession, company_id: str, codes: Iterable[str]) -> dict[str, ChartAccount]:
    unique_codes = {str(code) for code in codes if code}
    accounts = (
        await session.execute(
            select(ChartAccount).where(
                ChartAccount.company_id == company_id,
                ChartAccount.code.in_(unique_codes),
            )
        )
    ).scalars().all()
    result = {account.code: account for account in accounts}
    invalid = sorted(code for code in unique_codes if code not in result or not result[code].is_active or not result[code].is_leaf)
    if invalid:
        raise AccountingError("INVALID_ACCOUNT", "凭证包含无效或非末级科目", 422, {"accountCodes": invalid})
    return result


def _normalize_voucher_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    voucher = payload.get("voucherLines")
    if not voucher:
        voucher = apply_rd_detail_to_voucher(payload, get_voucher_hint(
            str(payload.get("direction") or "expense"),
            str(payload.get("category") or "其他支出"),
            money(payload.get("amount") or ZERO),
            payer=str(payload.get("payer") or "company_account"),
        ))
    normalized: list[dict[str, Any]] = []
    for direction in ("debit", "credit"):
        for raw in voucher.get(direction, []) or []:
            normalized.append(
                {
                    "direction": direction,
                    "accountCode": str(raw.get("accountCode") or raw.get("code") or "").strip(),
                    "accountName": str(raw.get("accountName") or raw.get("name") or "").strip(),
                    "amount": positive_money(raw.get("amount")),
                    "auxiliary": _json_safe(raw.get("auxiliary") or {}),
                    "rdDetail": _json_safe(raw.get("rdDetail") or {}),
                }
            )
    debit = sum((line["amount"] for line in normalized if line["direction"] == "debit"), ZERO)
    credit = sum((line["amount"] for line in normalized if line["direction"] == "credit"), ZERO)
    if len(normalized) < 2 or debit <= ZERO or debit != credit:
        raise AccountingError(
            UNBALANCED_ENTRY,
            "凭证借贷不平衡",
            422,
            {"debit": money_text(debit), "credit": money_text(credit), "lineCount": len(normalized)},
        )
    return normalized


async def _add_lines(
    session: AsyncSession,
    entry: JournalEntry,
    raw_lines: list[dict[str, Any]],
) -> None:
    accounts = await _load_accounts(session, entry.company_id, [line["accountCode"] for line in raw_lines])
    for line_no, line in enumerate(raw_lines, start=1):
        account = accounts[line["accountCode"]]
        journal_line = JournalLine(
                journal_entry_id=entry.id,
                company_id=entry.company_id,
                line_no=line_no,
                account_id=account.id,
                account_code=account.code,
                account_name=account.name,
                direction=line["direction"],
                amount=line["amount"],
                auxiliary=line.get("auxiliary") or {},
            )
        session.add(journal_line)
        await session.flush()
        rd_detail = line.get("rdDetail") or {}
        expected_type = RD_EXPENSE_TYPE_BY_ACCOUNT.get(account.code)
        if expected_type:
            if not rd_detail:
                raise AccountingError("RD_DETAIL_REQUIRED", "研发费用分录必须选择研发项目和费用类别", 422)
            expense_type = str(rd_detail.get("expenseType") or "")
            if expense_type != expected_type:
                raise AccountingError(
                    "RD_EXPENSE_TYPE_MISMATCH",
                    "研发费用类别与会计科目不一致",
                    422,
                    {"accountCode": account.code, "expectedExpenseType": expected_type},
                )
            project_id = str(rd_detail.get("projectId") or "").strip()
            project = await session.get(RdProject, project_id)
            if project is None or project.company_id != entry.company_id or project.status not in {"active", "completed"}:
                raise AccountingError("INVALID_RD_PROJECT", "请选择当前企业有效的研发项目", 422)
            if entry.occurred_on < project.start_date or (project.end_date and entry.occurred_on > project.end_date):
                raise AccountingError("RD_PROJECT_DATE_MISMATCH", "记账日期不在研发项目周期内", 422)
            original_amount = money(rd_detail.get("originalAmount") or line["amount"])
            rd_amount = money(line["amount"])
            if original_amount < rd_amount:
                raise AccountingError("INVALID_RD_ALLOCATION", "研发金额不能超过原始费用金额", 422)
            rate = (rd_amount / original_amount).quantize(Decimal("0.000001"))
            method = str(rd_detail.get("allocationMethod") or "direct")
            basis = str(rd_detail.get("allocationBasis") or "").strip() or None
            if method not in RD_ALLOCATION_METHODS:
                raise AccountingError("INVALID_RD_ALLOCATION_METHOD", "研发费用分摊方法无效", 422)
            if (rate < Decimal("1") or method != "direct") and not basis:
                raise AccountingError("RD_ALLOCATION_BASIS_REQUIRED", "共用研发费用必须填写分摊依据", 422)
            session.add(
                RdJournalLineDetail(
                    journal_line_id=journal_line.id,
                    company_id=entry.company_id,
                    project_id=project.id,
                    expense_type=expense_type,
                    original_amount=original_amount,
                    rd_amount=rd_amount,
                    allocation_rate=rate,
                    allocation_method=method,
                    allocation_basis=basis,
                    notes=str(rd_detail.get("notes") or "").strip() or None,
                )
            )
        elif rd_detail:
            raise AccountingError("RD_ACCOUNT_REQUIRED", "研发明细只能关联研发费用明细科目", 422)
    await session.flush()


async def _entry_lines(session: AsyncSession, entry_id: str) -> list[JournalLine]:
    return list(
        (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry_id).order_by(JournalLine.line_no)
            )
        ).scalars().all()
    )


async def _sync_projection(session: AsyncSession, entry: JournalEntry) -> None:
    payload = await serialize_journal(session, entry)
    payload["user_id"] = entry.created_by
    payload["userId"] = entry.created_by
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    projection = await session.get(LegacyEntryProjection, entry.id)
    created_at = entry.created_at.isoformat() if entry.created_at else _iso_now()
    updated_at = entry.updated_at.isoformat() if entry.updated_at else _iso_now()
    if projection is None:
        session.add(
            LegacyEntryProjection(
                id=entry.id,
                user_id=entry.created_by,
                company_id=entry.company_id,
                payload=encoded,
                created_at=created_at,
                updated_at=updated_at,
            )
        )
    else:
        projection.user_id = entry.created_by
        projection.company_id = entry.company_id
        projection.payload = encoded
        projection.updated_at = updated_at
    await session.flush()


async def _validate_postable(session: AsyncSession, entry: JournalEntry) -> None:
    lines = await _entry_lines(session, entry.id)
    debit = sum((line.amount for line in lines if line.direction == "debit"), ZERO)
    credit = sum((line.amount for line in lines if line.direction == "credit"), ZERO)
    if len(lines) < 2 or debit <= ZERO or debit != credit:
        raise AccountingError(
            UNBALANCED_ENTRY,
            "凭证借贷不平衡，不能过账",
            422,
            {"debit": money_text(debit), "credit": money_text(credit), "lineCount": len(lines)},
        )
    await _load_accounts(session, entry.company_id, [line.account_code for line in lines])


async def _allocate_voucher_no(session: AsyncSession, company_id: str, month: date) -> str:
    await _advisory_lock(session, f"voucher:{company_id}:{month.isoformat()}")
    sequence = await session.get(
        VoucherSequence,
        {"company_id": company_id, "period_start": month},
        with_for_update=True,
    )
    if sequence is None:
        current = 1
        session.add(VoucherSequence(company_id=company_id, period_start=month, next_number=2))
    else:
        current = sequence.next_number
        sequence.next_number += 1
    await session.flush()
    return f"VOU-{month:%Y%m}-{current:06d}"


async def _post_existing(
    session: AsyncSession,
    entry: JournalEntry,
    *,
    user_id: str,
    request_id: str,
    reviewer: bool = False,
) -> JournalEntry:
    company, member = await ensure_tenant(session, entry.company_id, user_id)
    if member.role not in POSTING_ROLES:
        entry.status = "pending_review"
        raise AccountingError(REVIEW_REQUIRED, "该凭证需要老板或会计复核", 409)
    if company.posting_policy == "maker_checker" and entry.created_by == user_id and not reviewer:
        entry.status = "pending_review"
        raise AccountingError(REVIEW_REQUIRED, "制单人与复核人必须不同", 409)
    if company.posting_policy == "maker_checker" and entry.created_by == user_id:
        raise AccountingError(REVIEW_REQUIRED, "制单人不能复核自己的凭证", 409)

    period = await get_or_create_period(session, entry.company_id, entry.period_start, lock=True)
    assert_period_writable(period)
    await _validate_postable(session, entry)
    if entry.entry_type == "standard":
        source = await session.get(SourceTransaction, entry.source_transaction_id) if entry.source_transaction_id else None
        if source is None or source.company_id != entry.company_id or source.status == "void":
            raise AccountingError("SOURCE_REQUIRED", "缺少有效原始业务来源，不能过账", 409)
    from services.closing_validation_service import possible_duplicate_wechat_income
    duplicate_candidates = await possible_duplicate_wechat_income(session, entry)
    if duplicate_candidates:
        raise AccountingError("POSSIBLE_DUPLICATE_WECHAT_INCOME", "银行到账可能重复记为微信收入，请先核对原收入和结算关系", 409, {"candidateEntryIds": duplicate_candidates})
    before = await serialize_journal(session, entry)
    entry.voucher_no = entry.voucher_no or await _allocate_voucher_no(session, entry.company_id, entry.period_start)
    entry.status = "posted"
    entry.posted_by = user_id
    entry.posted_at = _utcnow()
    if reviewer:
        entry.reviewed_by = user_id
    entry.version += 1
    entry.updated_at = _utcnow()
    if entry.source_transaction_id:
        source = await session.get(SourceTransaction, entry.source_transaction_id)
        if source and source.status != "reconciled":
            source.status = "confirmed"
    await session.flush()
    after = await serialize_journal(session, entry)
    await record_audit(
        session,
        company_id=entry.company_id,
        user_id=user_id,
        action="post" if not reviewer else "approve_and_post",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        before=before,
        after=after,
        entity_version=entry.version,
    )
    await _sync_projection(session, entry)
    return entry


async def create_entry(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    confirm: bool,
) -> JournalEntry:
    company, member = await ensure_tenant(session, company_id, user_id)
    if member.role not in WRITE_ROLES:
        raise AccountingError(FORBIDDEN, "当前角色不能制单", 403)
    occurred_on = _parse_date(payload.get("occurDate") or payload.get("date"))
    period = await get_or_create_period(session, company_id, occurred_on, lock=True)
    assert_period_writable(period)
    amount_value = money(payload.get("amount") or ZERO)
    if amount_value == ZERO:
        raise AccountingError("ZERO_AMOUNT", "记账金额不能为零")
    raw_lines = _normalize_voucher_lines(payload)
    entry_id = str(payload.get("id") or uuid4())
    await _advisory_lock(session, f"journal-entry:{company_id}:{entry_id}")
    existing = await session.get(JournalEntry, entry_id)
    if existing is not None:
        raise AccountingError(DUPLICATE_SOURCE, "该记账记录已经存在", 409, {"entryId": entry_id})

    source_type = str(payload.get("sourceType") or payload.get("source") or "manual")
    external_key = str(
        payload.get("dedupFingerprint")
        or payload.get("wechat_transaction_key")
        or payload.get("invoiceNumber")
        or ""
    ).strip() or None
    if external_key:
        await _advisory_lock(session, f"source:{company_id}:{source_type}:{external_key}")
        duplicate = (
            await session.execute(
                select(SourceTransaction.id).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.source_type == source_type,
                    SourceTransaction.external_key == external_key,
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            raise AccountingError(DUPLICATE_SOURCE, "该来源业务已入账", 409, {"sourceTransactionId": duplicate})

    safe_payload = _json_safe(payload)
    safe_payload["amount"] = money_text(amount_value)
    source = SourceTransaction(
        company_id=company_id,
        source_type=source_type,
        external_key=external_key,
        occurred_on=occurred_on,
        description=str(payload.get("title") or payload.get("description") or "记账记录").strip(),
        amount=abs(amount_value),
        currency="CNY",
        raw_payload=safe_payload,
        status="candidate",
        created_by=user_id,
    )
    session.add(source)
    await session.flush()

    metadata = {key: value for key, value in safe_payload.items() if key != "voucherLines"}
    metadata["amount"] = money_text(amount_value)
    entry = JournalEntry(
        id=entry_id,
        company_id=company_id,
        source_transaction_id=source.id,
        occurred_on=occurred_on,
        period_start=period.period_start,
        summary=source.description,
        status="draft",
        entry_type=str(payload.get("entryType") or "standard"),
        currency="CNY",
        created_by=user_id,
        rule_version=str(payload.get("ruleVersion") or "account-mapping-v1"),
        metadata_json=metadata,
        version=1,
    )
    session.add(entry)
    await session.flush()
    await _add_lines(session, entry, raw_lines)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="create_draft",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        after=await serialize_journal(session, entry),
        entity_version=entry.version,
    )

    if confirm:
        if member.role in POSTING_ROLES and company.posting_policy == "owner_self_post":
            return await _post_existing(session, entry, user_id=user_id, request_id=request_id)
        entry.status = "pending_review"
        await session.flush()
    await _sync_projection(session, entry)
    return entry


async def get_journal(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    lock: bool = False,
) -> JournalEntry:
    query = select(JournalEntry).where(JournalEntry.id == entry_id, JournalEntry.company_id == company_id)
    if lock:
        query = query.with_for_update()
    entry = (await session.execute(query)).scalar_one_or_none()
    if entry is None:
        raise AccountingError(NOT_FOUND, "凭证不存在", 404)
    return entry


def _mask_phone(phone: str) -> str:
    digits = str(phone or "").strip()
    if len(digits) == 11 and digits.isdigit():
        return f"{digits[:3]}****{digits[-4:]}"
    return digits


async def _user_display_names(session: AsyncSession, user_ids: Iterable[str]) -> dict[str, str]:
    """凭证署名显示名：昵称优先，其次掩码手机号，最后回落 user_id。"""
    ids = {str(uid) for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(AuthUser.user_id, AuthUser.nickname, AuthUser.phone).where(AuthUser.user_id.in_(ids))
        )
    ).all()
    labels: dict[str, str] = {}
    for uid, nickname, phone in rows:
        labels[str(uid)] = str(nickname or "").strip() or _mask_phone(phone) or str(uid)
    return labels


async def serialize_journal(
    session: AsyncSession,
    entry: JournalEntry,
    *,
    api_money: bool = True,
) -> dict[str, Any]:
    lines = await _entry_lines(session, entry.id)
    payload = dict(entry.metadata_json or {})
    if entry.source_transaction_id:
        source = await session.get(SourceTransaction, entry.source_transaction_id)
        if source:
            for key in ("billingStatus", "reconciliationType", "paymentDate", "bankStatementId"):
                if key in (source.raw_payload or {}):
                    payload[key] = source.raw_payload[key]
    # 对账状态由银行确认/解绑路径直接维护在 legacy 投影 payload 上
    # （json_set/json_remove），权威层 metadata 感知不到，这里以投影为准回填。
    projection = await session.get(LegacyEntryProjection, entry.id)
    if projection is not None:
        try:
            legacy_payload = json.loads(projection.payload) if isinstance(projection.payload, str) else (projection.payload or {})
        except (json.JSONDecodeError, TypeError):
            legacy_payload = {}
        if isinstance(legacy_payload, dict):
            for key in ("billingStatus", "reconciliationType", "paymentDate", "bankStatementId"):
                if legacy_payload.get(key) is not None:
                    payload[key] = legacy_payload[key]
            # 归档标志由导入/迁移路径直接维护在投影 payload 上，同样以投影为准回填
            if legacy_payload.get("is_archive_only") is not None:
                payload["is_archive_only"] = legacy_payload.get("is_archive_only")
            if legacy_payload.get("isArchiveOnly") is not None:
                payload["isArchiveOnly"] = legacy_payload.get("isArchiveOnly")
    rd_details = {
        detail.journal_line_id: detail
        for detail in (
            await session.execute(
                select(RdJournalLineDetail).where(
                    RdJournalLineDetail.journal_line_id.in_([line.id for line in lines])
                )
            )
        ).scalars().all()
    } if lines else {}

    def serialize_line(line: JournalLine) -> dict[str, Any]:
        result = {
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": money_text(line.amount) if api_money else line.amount,
        }
        if line.auxiliary:
            result["auxiliary"] = line.auxiliary
        detail = rd_details.get(line.id)
        if detail:
            result["rdDetail"] = {
                "projectId": detail.project_id,
                "expenseType": detail.expense_type,
                "originalAmount": money_text(detail.original_amount) if api_money else detail.original_amount,
                "rdAmount": money_text(detail.rd_amount) if api_money else detail.rd_amount,
                "allocationRate": str(detail.allocation_rate),
                "allocationMethod": detail.allocation_method,
                "allocationBasis": detail.allocation_basis,
                "notes": detail.notes,
            }
        return result

    name_map = await _user_display_names(
        session, {entry.created_by, entry.reviewed_by, entry.posted_by}
    )
    debit = [serialize_line(line) for line in lines if line.direction == "debit"]
    credit = [serialize_line(line) for line in lines if line.direction == "credit"]
    payload.update(
        {
            "id": entry.id,
            "company_id": entry.company_id,
            "companyId": entry.company_id,
            "title": entry.summary,
            "occurDate": entry.occurred_on.isoformat(),
            "voucherStatus": entry.status,
            "voucherRef": entry.voucher_no,
            "entryType": entry.entry_type,
            # 凭证署名：来自真实操作留痕（创建/复核/过账人），不提供手工配置
            "createdByName": name_map.get(entry.created_by, entry.created_by),
            "reviewedByName": (name_map.get(entry.reviewed_by, "") if entry.reviewed_by else ""),
            "postedByName": (name_map.get(entry.posted_by, "") if entry.posted_by else ""),
            "voucherLines": {"debit": debit, "credit": credit},
            "currency": entry.currency,
            "version": entry.version,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        }
    )
    if "amount" in payload:
        payload["amount"] = money_text(payload["amount"]) if api_money else money(payload["amount"])
    return payload


async def list_journals(
    session: AsyncSession,
    company_id: str,
    *,
    statuses: Iterable[str] = ACTIVE_ENTRY_STATUSES,
    api_money: bool = True,
) -> list[dict[str, Any]]:
    entries = (
        await session.execute(
            select(JournalEntry)
            .where(JournalEntry.company_id == company_id, JournalEntry.status.in_(tuple(statuses)))
            .order_by(JournalEntry.occurred_on.desc(), JournalEntry.created_at.desc())
        )
    ).scalars().all()
    return [await serialize_journal(session, entry, api_money=api_money) for entry in entries]


async def posted_entries_for_reports(session: AsyncSession, company_id: str) -> list[dict[str, Any]]:
    """Return authoritative posted/reversed entries with Decimal amounts."""
    return await list_journals(
        session,
        company_id,
        statuses=("posted", "reversed"),
        api_money=False,
    )


async def _update_posted_billing_status(
    session: AsyncSession,
    entry: JournalEntry,
    billing_status: Any,
    *,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    """已过账凭证的开票状态只在 legacy 投影 payload 上维护（与银行对账路径 json_set billingStatus 同一模式），
    权威凭证行保持不可变，不触碰借贷。"""
    projection = await session.get(LegacyEntryProjection, entry.id)
    if projection is None:
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "已过账凭证不能修改，请使用冲销", 409)
    try:
        payload = json.loads(projection.payload) if isinstance(projection.payload, str) else dict(projection.payload or {})
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if billing_status:
        payload["billingStatus"] = billing_status
    else:
        payload.pop("billingStatus", None)
    projection.payload = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    projection.updated_at = _iso_now()
    await record_audit(
        session,
        company_id=entry.company_id,
        user_id=user_id,
        action="update_billing_status",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        after={"billingStatus": billing_status, "entryStatus": entry.status},
        entity_version=entry.version,
    )
    await session.flush()
    return entry


async def update_draft(
    session: AsyncSession,
    entry_id: str,
    updates: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status not in {"draft", "pending_review"}:
        # 开票状态是申报跟踪标志，不影响借贷；已过账凭证允许仅更新它（投影层），
        # 其余任何字段修改仍然必须走冲销。
        if set(updates) == {"billingStatus"}:
            return await _update_posted_billing_status(
                session, entry, updates["billingStatus"], user_id=user_id, request_id=request_id
            )
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "已过账凭证不能修改，请使用冲销", 409)
    if (entry.metadata_json or {}).get("source") == "wechat_settlement":
        raise AccountingError("SYSTEM_SETTLEMENT", "结算凭证按银行净额生成，请取消对账后重新确认，不能直接修改", 409)
    old_period = await get_or_create_period(session, company_id, entry.period_start, lock=True)
    assert_period_writable(old_period)
    before = await serialize_journal(session, entry)
    metadata = dict(entry.metadata_json or {})
    metadata.update(_json_safe(updates))
    if "amount" in updates:
        metadata["amount"] = money_text(updates["amount"])
    if "title" in updates and updates["title"]:
        entry.summary = str(updates["title"]).strip()
    if "occurDate" in updates and updates["occurDate"]:
        new_date = _parse_date(updates["occurDate"])
        new_period = await get_or_create_period(session, company_id, new_date, lock=True)
        assert_period_writable(new_period)
        entry.occurred_on = new_date
        entry.period_start = new_period.period_start
    financial_fields = {"amount", "direction", "category", "payer", "voucherLines"}
    if financial_fields.intersection(updates):
        if updates.get("category") == "研发支出-费用化支出" and not (
            updates.get("rdProjectId") and updates.get("rdExpenseType")
        ):
            raise AccountingError("RD_DETAIL_REQUIRED", "研发费用必须选择研发项目和费用类别", 422)
        rebuilt_payload = {**metadata, **_json_safe(updates)}
        if "voucherLines" not in updates:
            metadata.pop("voucherLines", None)
            rebuilt_payload.pop("voucherLines", None)
        raw_lines = _normalize_voucher_lines(rebuilt_payload)
        await session.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry.id))
        await session.flush()
        await _add_lines(session, entry, raw_lines)
        if entry.source_transaction_id:
            source = await session.get(SourceTransaction, entry.source_transaction_id)
            if source:
                source.amount = abs(money(rebuilt_payload.get("amount") or ZERO))
                source.occurred_on = entry.occurred_on
                source.description = entry.summary
                source.raw_payload = rebuilt_payload
    entry.metadata_json = metadata
    entry.status = "draft"
    entry.reviewed_by = None
    entry.version += 1
    entry.updated_at = _utcnow()
    await session.flush()
    after = await serialize_journal(session, entry)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="update_draft",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        before=before,
        after=after,
        entity_version=entry.version,
    )
    await _sync_projection(session, entry)
    return entry


async def delete_draft(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> None:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status not in {"draft", "pending_review"}:
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "已过账凭证不能删除，请使用冲销", 409)
    linked = (await session.execute(select(ReconciliationLink.id).where(
        ReconciliationLink.company_id == company_id,
        ReconciliationLink.journal_entry_id == entry_id,
        ReconciliationLink.status == "confirmed",
    ).limit(1))).first()
    if linked:
        raise AccountingError("ENTRY_RECONCILED", "凭证已关联银行流水，请先取消对账，不能直接删除", 409)
    period = await get_or_create_period(session, company_id, entry.period_start, lock=True)
    assert_period_writable(period)
    before = await serialize_journal(session, entry)
    if entry.source_transaction_id:
        source = await session.get(SourceTransaction, entry.source_transaction_id)
        if source:
            source.status = "void"
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="delete_draft",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        before=before,
        entity_version=entry.version,
    )
    projection = await session.get(LegacyEntryProjection, entry.id)
    if projection is not None:
        await session.delete(projection)
    await session.delete(entry)
    await session.flush()


async def post_journal(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status == "posted":
        return entry
    if entry.status not in {"draft", "pending_review"}:
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "该凭证不能再次过账", 409)
    return await _post_existing(session, entry, user_id=user_id, request_id=request_id)


async def approve_journal(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status != "pending_review":
        raise AccountingError(REVIEW_REQUIRED, "只有待复核凭证可以审批", 409)
    return await _post_existing(session, entry, user_id=user_id, request_id=request_id, reviewer=True)


async def submit_journal_for_review(session: AsyncSession, entry_id: str, *, company_id: str, user_id: str, request_id: str) -> JournalEntry:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status == "pending_review":
        return entry
    if entry.status != "draft":
        raise AccountingError(REVIEW_REQUIRED, "只有草稿凭证可以提交复核", 409)
    period = await get_or_create_period(session, company_id, entry.period_start, lock=True)
    assert_period_writable(period)
    await _validate_postable(session, entry)
    before = await serialize_journal(session, entry)
    entry.status = "pending_review"
    entry.version += 1
    await session.flush()
    await record_audit(session, company_id=company_id, user_id=user_id, action="submit_for_review", entity_type="journal_entry", entity_id=entry.id, request_id=request_id, before=before, after=await serialize_journal(session, entry), entity_version=entry.version)
    await _sync_projection(session, entry)
    return entry


async def reverse_journal(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    reason: str,
    reversal_date: Any = None,
) -> JournalEntry:
    await require_role(session, company_id, user_id, POSTING_ROLES)
    original = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if original.status != "posted":
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "只有已过账凭证可以冲销", 409)
    if not reason or not reason.strip():
        raise AccountingError("REVERSAL_REASON_REQUIRED", "冲销必须填写原因")
    # 冲销默认落在原凭证期间（红字与原凭证同期相消，月度报表才不失真）；
    # 原期间已关账时回落到指定日期/当天，走"当期调整"口径。
    if reversal_date in (None, ""):
        occurred_on = original.occurred_on
    else:
        occurred_on = _parse_date(reversal_date)
    try:
        period = await get_or_create_period(session, company_id, occurred_on, lock=True)
        assert_period_writable(period)
    except AccountingError as exc:
        if exc.code != PERIOD_CLOSED or reversal_date not in (None, ""):
            raise
        occurred_on = _parse_date(None)
        period = await get_or_create_period(session, company_id, occurred_on, lock=True)
        assert_period_writable(period)
    original_lines = await _entry_lines(session, original.id)
    reversal_total = money(sum((line.amount for line in original_lines if line.direction == "debit"), ZERO))
    original_rd_details = {
        detail.journal_line_id: detail
        for detail in (
            await session.execute(
                select(RdJournalLineDetail).where(
                    RdJournalLineDetail.journal_line_id.in_([line.id for line in original_lines])
                )
            )
        ).scalars().all()
    } if original_lines else {}
    reversal = JournalEntry(
        company_id=company_id,
        occurred_on=occurred_on,
        period_start=period.period_start,
        summary=f"冲销：{original.summary}",
        status="draft",
        entry_type="reversal",
        currency="CNY",
        created_by=user_id,
        reversal_of_id=original.id,
        reason=reason.strip(),
        rule_version=original.rule_version,
        # 携带原凭证的展示字段（方向/分类），金额记为负数（红字口径）：
        # 账本列表与月度汇总按金额求和即可自然抵消，前端对负数金额已做显示适配。
        metadata_json={
            "reversalOf": original.id,
            "reason": reason.strip(),
            "direction": (original.metadata_json or {}).get("direction") or "expense",
            "category": (original.metadata_json or {}).get("category") or "",
            "payer": (original.metadata_json or {}).get("payer") or "company_account",
            "amount": money_text(-reversal_total),
            "isReversal": True,
        },
        version=1,
    )
    session.add(reversal)
    await session.flush()
    raw_lines = []
    for line in original_lines:
        detail = original_rd_details.get(line.id)
        raw_line = {
            "direction": "credit" if line.direction == "debit" else "debit",
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": line.amount,
            "auxiliary": line.auxiliary,
        }
        if detail:
            raw_line["rdDetail"] = {
                "projectId": detail.project_id,
                "expenseType": detail.expense_type,
                "originalAmount": detail.original_amount,
                "allocationMethod": detail.allocation_method,
                "allocationBasis": detail.allocation_basis,
                "notes": detail.notes,
            }
        raw_lines.append(raw_line)
    await _add_lines(session, reversal, raw_lines)
    await _post_existing(session, reversal, user_id=user_id, request_id=request_id)
    original.status = "reversed"
    original.version += 1
    original.updated_at = _utcnow()
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="mark_reversed",
        entity_type="journal_entry",
        entity_id=original.id,
        request_id=request_id,
        after={"status": "reversed", "reversalEntryId": reversal.id},
        reason=reason.strip(),
        entity_version=original.version,
    )
    await _sync_projection(session, original)
    await _sync_projection(session, reversal)
    return reversal


async def period_close_checks(session: AsyncSession, company_id: str, month: Any) -> dict[str, Any]:
    month_start = period_start_for(month)
    period = (
        await session.execute(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == month_start,
            )
        )
    ).scalar_one_or_none()
    pending_count = int(
        (
            await session.execute(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.period_start == month_start,
                    JournalEntry.status.in_(("draft", "pending_review")),
                )
            )
        ).scalar_one()
    )
    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.period_start == month_start,
                JournalEntry.status.in_(("posted", "reversed")),
            )
        )
    ).one()
    debit = money(totals[0])
    credit = money(totals[1])
    unreconciled_bank = int(
        (
            await session.execute(
                select(func.count(SourceTransaction.id)).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.occurred_on >= month_start,
                    SourceTransaction.occurred_on < (month_start.replace(day=28) + timedelta(days=4)).replace(day=1),
                    SourceTransaction.source_type.in_(("bank", "bank_statement")),
                    SourceTransaction.status.notin_(("reconciled", "void")),
                )
            )
        ).scalar_one()
    )
    account_rows = (
        await session.execute(
            select(
                JournalLine.account_code,
                JournalLine.account_name,
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.period_start <= month_start,
                JournalEntry.status.in_(("posted", "reversed")),
            )
            .group_by(JournalLine.account_code, JournalLine.account_name)
            .order_by(JournalLine.account_code)
        )
    ).all()
    missing_rule_versions = int(
        (
            await session.execute(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.period_start == month_start,
                    JournalEntry.status.in_(("posted", "reversed")),
                    (JournalEntry.rule_version.is_(None) | (JournalEntry.rule_version == "")),
                )
            )
        ).scalar_one()
    )
    account_debit = sum((money(row[2]) for row in account_rows), ZERO)
    account_credit = sum((money(row[3]) for row in account_rows), ZERO)
    voucher_issues = []
    month_entries = (await session.execute(
        select(JournalEntry, SourceTransaction)
        .outerjoin(SourceTransaction, SourceTransaction.id == JournalEntry.source_transaction_id)
        .where(JournalEntry.company_id == company_id, JournalEntry.period_start == month_start)
        .order_by(JournalEntry.occurred_on, JournalEntry.id)
    )).all()
    for entry, source in month_entries:
        metadata = entry.metadata_json or {}
        # ponytail: 仅归档不记账单据不参与财务凭证核验与待过账阻断
        if metadata.get("is_archive_only") or metadata.get("isArchiveOnly"):
            continue
        reasons = []
        if entry.status == "draft":
            reasons.append("pending_post")
        elif entry.status == "pending_review":
            reasons.append("pending_review")
        if entry.entry_type == "standard" and entry.status in ACTIVE_ENTRY_STATUSES and (
            source is None or source.company_id != company_id or source.status == "void"
        ):
            reasons.append("missing_source")
        if reasons:
            voucher_issues.append({
                "entryId": entry.id, "title": entry.summary,
                "date": entry.occurred_on.isoformat(), "status": entry.status,
                "voucherRef": entry.voucher_no, "reasons": reasons,
            })
    orphan_vouchers = sum("missing_source" in item["reasons"] for item in voucher_issues)
    draft_count = sum("pending_post" in item["reasons"] for item in voucher_issues)
    review_count = sum("pending_review" in item["reasons"] for item in voucher_issues)
    pending_count = draft_count + review_count
    from services.closing_validation_service import bank_close_validation
    bank_validation = _json_safe(await bank_close_validation(session, company_id, month_start))
    from services.opening_balance_service import get_opening_balances
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period
    report_entries = await posted_entries_for_reports(session, company_id)
    opening_items = await get_opening_balances(company_id, month_start.year, session=session)
    company = await session.get(Company, company_id)
    statement = build_statement_data_from_entries(report_entries, opening_items, parse_period(month_start.strftime("%Y-%m"), "month"), (company.payload or {}) if company else {})
    report_issues = [item for item in statement.checks[2:] if item["level"] == "error"]
    # Compare the actual exported statement's cash to authoritative account
    # balances, rather than comparing debit and credit totals a second time.
    ledger_cash = sum((money(row[2]) - money(row[3]) for row in account_rows if row[0] in {"1001", "1002", "1012"}), ZERO)
    report_cash = money(statement.balance_current.get("cash", ZERO))
    if ledger_cash != report_cash:
        report_issues.append({"level": "error", "message": "账簿货币资金与申报财务报表不一致"})
    snapshot = {
        "period": month_start.isoformat()[:7],
        "debit": money_text(debit),
        "credit": money_text(credit),
        "accounts": [
            {
                "accountCode": row[0],
                "accountName": row[1],
                "debit": money_text(row[2]),
                "credit": money_text(row[3]),
                "balance": money_text(money(row[2]) - money(row[3])),
            }
            for row in account_rows
        ],
    }
    checks = [
        {
            "code": "VOUCHER_TO_SOURCE",
            "name": "账证核对",
            "status": "blocking" if pending_count or orphan_vouchers else "passed",
            "uiStatus": "blocked" if pending_count or orphan_vouchers else "complete",
            "pendingCount": pending_count,
            "draftCount": draft_count,
            "autoPostAtCloseCount": draft_count,
            "pendingReviewCount": review_count,
            "orphanVoucherCount": orphan_vouchers,
            "records": voucher_issues,
        },
        {
            "code": "LEDGER_TO_LEDGER",
            "name": "账账核对",
            "status": "passed" if debit == credit else "blocking",
            "uiStatus": "complete" if debit == credit else "blocked",
            "debit": money_text(debit),
            "credit": money_text(credit),
        },
        {
            "code": "BOOK_TO_ACTUAL",
            "name": "账实核对",
            "status": "blocking" if unreconciled_bank or bank_validation["issueCount"] else "passed",
            "uiStatus": "blocked" if unreconciled_bank or bank_validation["issueCount"] else "complete",
            "unreconciledBankCount": unreconciled_bank,
            **bank_validation,
        },
        {
            "code": "LEDGER_TO_REPORT",
            "name": "账表核对",
            "status": "blocking" if report_issues else "passed",
            "uiStatus": "blocked" if report_issues else "complete",
            "reportDebit": money_text(account_debit),
            "reportCredit": money_text(account_credit),
            "ledgerCash": money_text(ledger_cash),
            "reportCash": money_text(report_cash),
            "issues": report_issues,
        },
        {
            "code": "REPORT_TO_TAX",
            "name": "申报财务报表校验",
            "status": "blocking" if missing_rule_versions or report_issues else "passed",
            "uiStatus": "blocked" if missing_rule_versions or report_issues else "complete",
            "missingRuleVersionCount": missing_rule_versions,
            "records": [{"entryId": entry.id, "title": entry.summary, "date": entry.occurred_on.isoformat(), "status": entry.status, "reasons": ["missing_rule"]}
                        for entry, _ in month_entries if entry.status in {"posted", "reversed"} and not entry.rule_version],
            "issues": report_issues,
            "scope": "记账规则与申报财务报表内部勾稽，不含增值税/所得税申报表核验",
        },
    ]
    # Other checks describe posted books only. Drafts must not produce a green
    # "all verified" result for business which has not entered those books yet.
    if pending_count:
        for check in checks[1:]:
            check["blockedByPendingCount"] = pending_count
            if check["status"] == "passed":
                check["status"] = "needs_review"
                check["uiStatus"] = "incomplete"
    return {
        "period": month_start.isoformat()[:7],
        "periodStatus": period.status if period else "open",
        "canClose": all(check["status"] == "passed" for check in checks),
        "blockingCount": sum(check["status"] == "blocking" for check in checks),
        "checks": checks,
        "snapshot": snapshot,
    }


async def _close_period_in_transaction(
    session: AsyncSession,
    company_id: str,
    month: Any,
    *,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, POSTING_ROLES)
    period = await get_or_create_period(session, company_id, month, lock=True)
    if period.status == "closed":
        return await period_close_checks(session, company_id, month)

    # The user's single final close action also posts this period's drafts.
    # Keep posting and close validation in one savepoint so a failed posting or
    # any later check leaves every voucher and the period unchanged.
    draft_entries = (await session.execute(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.period_start == period.period_start,
            JournalEntry.status == "draft",
        ).order_by(JournalEntry.occurred_on, JournalEntry.id).with_for_update()
    )).scalars().all()
    posted_at_close: list[JournalEntry] = []
    for entry in draft_entries:
        metadata = entry.metadata_json or {}
        # ponytail: 仅归档不记账的草稿凭证不自动过账进总账，避免产生未核对银行存款分录
        if metadata.get("is_archive_only") or metadata.get("isArchiveOnly"):
            continue
        posted_at_close.append(await post_journal(
            session, entry.id, company_id=company_id, user_id=user_id,
            request_id=f"{request_id}:close-post:{entry.id}",
        ))

    period.status = "closing"
    await session.flush()
    result = await period_close_checks(session, company_id, month)
    snapshot_json = json.dumps(result["snapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    session.add(
        PeriodCloseRun(
            company_id=company_id,
            period_start=period.period_start,
            status="closed" if result["canClose"] else "blocked",
            checks=result["checks"],
            snapshot=result["snapshot"],
            snapshot_hash=digest,
            rule_versions={"accountMapping": "account-mapping-v1"},
            ledger_watermark=int((await session.execute(select(func.max(AuditEvent.sequence_no)))).scalar_one_or_none() or 0),
            requested_by=user_id,
        )
    )
    if not result["canClose"]:
        period.status = "open"
        await session.flush()
        raise AccountingError("CLOSE_CHECK_FAILED", "存在阻断项，不能关账", 409, {"checks": result["checks"]})
    period.status = "closed"
    period.closed_by = user_id
    period.closed_at = _utcnow()
    period.version += 1
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="close_period",
        entity_type="accounting_period",
        entity_id=period.id,
        request_id=request_id,
        after={"period": result["period"], "status": "closed", "snapshotHash": digest,
               "autoPostedEntryIds": [entry.id for entry in posted_at_close],
               "autoPostedVoucherRefs": [entry.voucher_no for entry in posted_at_close]},
        entity_version=period.version,
    )
    result["status"] = "closed"
    result["snapshotHash"] = digest
    result["autoPostedCount"] = len(posted_at_close)
    result["autoPostedVoucherRefs"] = [entry.voucher_no for entry in posted_at_close]
    return result


async def close_period(
    session: AsyncSession,
    company_id: str,
    month: Any,
    *,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """Atomically post eligible drafts and close after all checks pass."""
    async with session.begin_nested():
        return await _close_period_in_transaction(
            session, company_id, month, user_id=user_id, request_id=request_id,
        )


async def issue_step_up_token(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    action: str,
) -> str:
    await ensure_tenant(session, company_id, user_id)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session.add(
        StepUpToken(
            token_hash=token_hash,
            company_id=company_id,
            user_id=user_id,
            action=action,
            expires_at=_utcnow() + timedelta(minutes=5),
        )
    )
    await session.flush()
    return raw_token


async def consume_step_up_token(
    session: AsyncSession,
    raw_token: str | None,
    *,
    company_id: str,
    user_id: str,
    action: str,
) -> None:
    if not raw_token:
        raise AccountingError(STEP_UP_REQUIRED, "该操作需要短信重新认证", 403)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token = (
        await session.execute(select(StepUpToken).where(StepUpToken.token_hash == token_hash).with_for_update())
    ).scalar_one_or_none()
    expires_at = token.expires_at if token is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        # SQLite test/rehearsal databases do not retain timezone metadata.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        token is None
        or token.company_id != company_id
        or token.user_id != user_id
        or token.action != action
        or token.used_at is not None
        or expires_at is None
        or expires_at <= _utcnow()
    ):
        raise AccountingError(STEP_UP_REQUIRED, "重新认证凭证无效或已过期", 403)
    token.used_at = _utcnow()
    await session.flush()


async def reopen_period(
    session: AsyncSession,
    company_id: str,
    month: Any,
    *,
    user_id: str,
    request_id: str,
    reason: str,
    step_up_token: str | None,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, {"owner"})
    if not reason or not reason.strip():
        raise AccountingError("REOPEN_REASON_REQUIRED", "反结账必须填写原因")
    await consume_step_up_token(
        session,
        step_up_token,
        company_id=company_id,
        user_id=user_id,
        action="reopen_period",
    )
    period = await get_or_create_period(session, company_id, month, lock=True)
    if period.status != "closed":
        raise AccountingError("PERIOD_NOT_CLOSED", "只有已关账期间可以反结账", 409)
    before = {"period": period.period_start.isoformat()[:7], "status": period.status}
    period.status = "reopened"
    period.reopened_by = user_id
    period.reopened_at = _utcnow()
    period.version += 1
    session.add(
        PeriodCloseRun(
            company_id=company_id,
            period_start=period.period_start,
            status="reopened",
            checks=[],
            snapshot={},
            snapshot_hash=hashlib.sha256(b"{}").hexdigest(),
            rule_versions={},
            ledger_watermark=int((await session.execute(select(func.max(AuditEvent.sequence_no)))).scalar_one_or_none() or 0),
            requested_by=user_id,
        )
    )
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="reopen_period",
        entity_type="accounting_period",
        entity_id=period.id,
        request_id=request_id,
        before=before,
        after={"period": period.period_start.isoformat()[:7], "status": "reopened"},
        reason=reason.strip(),
        entity_version=period.version,
    )
    return {"period": period.period_start.isoformat()[:7], "status": "reopened"}


def request_digest(payload: Any) -> str:
    canonical = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_idempotent_response(
    session: AsyncSession,
    *,
    company_id: str,
    key: str | None,
    operation: str,
    payload: Any,
) -> dict[str, Any] | None:
    if not key:
        return None
    await _advisory_lock(session, f"idempotency:{company_id}:{operation}:{key}")
    record = (
        await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.company_id == company_id,
                IdempotencyRecord.idempotency_key == key,
                IdempotencyRecord.operation == operation,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    if record.request_hash != request_digest(payload):
        raise AccountingError("IDEMPOTENCY_KEY_REUSED", "同一幂等键不能用于不同请求", 409)
    return dict(record.response_body or {}) if record.response_status else None


async def save_idempotent_response(
    session: AsyncSession,
    *,
    company_id: str,
    key: str | None,
    operation: str,
    payload: Any,
    status: int,
    response: dict[str, Any],
) -> None:
    if not key:
        return
    session.add(
        IdempotencyRecord(
            company_id=company_id,
            idempotency_key=key,
            operation=operation,
            request_hash=request_digest(payload),
            response_status=status,
            response_body=_json_safe(response),
            expires_at=_utcnow() + timedelta(days=7),
        )
    )
    await session.flush()
