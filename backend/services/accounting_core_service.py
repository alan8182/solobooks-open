# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.chart_policy import DEFAULT_QUANTITY_ACCOUNT_CODES, chart_is_leaf
from core.errors import (
    AccountingError,
    BACKFILL_REASON_REQUIRED,
    DUPLICATE_SOURCE,
    ENTRY_BEFORE_START_DATE,
    ENTRY_POSTED_IMMUTABLE,
    FORBIDDEN,
    NOT_FOUND,
    PERIOD_BEFORE_BOOK_START,
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
    PeriodBackfill,
    PeriodCloseRun,
    ReconciliationLink,
    RdJournalLineDetail,
    RdProject,
    SourceTransaction,
    StepUpToken,
    VoucherSequence,
)
from services.account_mapper import CATEGORY_TO_ACCOUNT, COUNTERPARTY_ACCOUNTS, get_voucher_hint
from services.account_classification import is_profit_loss_code
from services.period_resolution_service import (
    SYSTEM_ENTRY_TYPES,
    PeriodResolution,
    resolve_posting_period,
)

logger = logging.getLogger(__name__)


SHANGHAI = ZoneInfo("Asia/Shanghai")
POSTING_ROLES = {"owner", "accountant"}
WRITE_ROLES = {"owner", "accountant", "preparer"}
ACTIVE_ENTRY_STATUSES = {"draft", "pending_review", "posted", "reversed"}
# 错误 context 里最多回传多少行分录/明细：救援弹窗要的是「定位到错处」，
# 不是把整张凭证搬过去。截断也能挡住异常大的 payload。
_RESCUE_LINE_LIMIT = 10


def _locked_entry_context(entry: JournalEntry) -> dict[str, Any]:
    """凭证被锁死时的定位信息。

    救援出路是「去冲销 / 看这张凭证」，指不到具体凭证的按钮是死的；
    ``voucherRef`` 用凭证号，没有就退回摘要，都空则只给 id。
    """
    return {
        "entryId": entry.id,
        "occurredOn": entry.occurred_on.isoformat(),
        "voucherRef": str(entry.voucher_no or entry.summary or "")[:60],
        "status": entry.status,
        "entryType": entry.entry_type,
    }
RD_ACCOUNT_BY_TYPE = {
    "personnel": "430100101",
    "direct_input": "430100102",
    "depreciation_amortization": "430100103",
    "design": "430100104",
    "testing": "430100105",
    "outsourced": "430100106",
    "other": "430100199",
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
# 计征增值税的销售收入科目（与 vat_filing_service 保持一致）
VAT_REVENUE_ACCOUNT_CODES = {"5001", "5051"}


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


def book_start_date(company: Company) -> date | None:
    """账套启用日期（主体档案 startDate）；缺省或格式非法时视为未启用约束。"""
    raw = str((company.payload or {}).get("startDate") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def book_start_month(session: AsyncSession, company_id: str) -> date | None:
    """账套启用月（月初），即「期间下限」；未填启用日期时返回 None。

    启用日期是记账起点：启用前的月份既不该出现在期次选择器里，也不该被算成
    「更早的未关账月份」——否则启用前导进来的历史流水会让本期永远结不了账。
    """
    company = await session.get(Company, company_id)
    if company is None:
        return None
    start = book_start_date(company)
    return start.replace(day=1) if start else None


def assert_book_started(company: Company, occurred_on: date, entry_type: str) -> None:
    """启用日期是账套的记账起点；期初凭证（opening）承载启用日之前的历史，不受此限。"""
    if entry_type == "opening":
        return
    start = book_start_date(company)
    if start is None or occurred_on >= start:
        return
    raise AccountingError(
        ENTRY_BEFORE_START_DATE,
        f"记账日期 {occurred_on.isoformat()} 早于账套启用日期 {start.isoformat()}："
        "启用日期前的业务请在「我的 → 期初设置」录入期初余额，或到「我的 → 主体管理」调整启用日期",
        422,
        {"startDate": start.isoformat(), "occurDate": occurred_on.isoformat()},
    )


# 1012 为历史编码（现 1012001 其他货币资金-微信待结算），保留以兼容存量行
MONEY_ACCOUNT_CODES = ("1002", "1012001", "1012")


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
                SourceTransaction.source_type.in_(("bank", "bank_statement")),
                SourceTransaction.external_key == statement_id,
            )
        )
    ).scalars().first()
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
    elif (source.raw_payload or {}).get("signedAmount") is None and signed_amount is not None:
        source.raw_payload = {**(source.raw_payload or {}), "signedAmount": money_text(signed_amount)}

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
                    "direction": str(info.get("direction") or ""),
                    "parent_code": str(info.get("parentCode") or "") or None,
                    "level": 1 + (len(str(code)) > 4) + (len(str(code)) > 7),
                    "is_leaf": chart_is_leaf(code, info),
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
    definitions.setdefault("5602999", {"name": "管理费用-其他", "category": "损益", "type": "费用"})
    return definitions


def _resolve_direction(code: str, info: dict[str, Any]) -> str:
    direction = info.get("direction")
    if direction not in ("debit", "credit"):
        # 未声明方向的科目按编码段兜底：负债/权益为贷方，其余为借方
        return "credit" if code.startswith(("2", "3")) else "debit"
    return direction


async def seed_accounts(session: AsyncSession, company_id: str) -> None:
    existing_accounts = list(
        (await session.execute(select(ChartAccount).where(ChartAccount.company_id == company_id))).scalars().all()
    )
    existing = {account.code: account for account in existing_accounts}
    now = _iso_now()
    for code, info in _seed_account_definitions().items():
        direction = _resolve_direction(code, info)
        row = existing.get(code)
        if row is None:
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
                    normal_side=direction,
                    is_leaf=chart_is_leaf(code, info),
                    is_system=True,
                    is_active=True,
                    # 存货三科目默认开数量核算；用户事后可关，稳态更新不会回填（见下）
                    quantity_enabled=code in DEFAULT_QUANTITY_ACCOUNT_CODES,
                    created_at=now,
                    updated_at=now,
                )
            )
            continue
        # 系统科目跟随 COA 定义演进（新增子级时父级要翻转 is_leaf），
        # 但保留用户的 is_active —— 用户手工停用的科目不能被重新启用。
        # is_leaf 一律走 chart_policy 单一口径：只有 1221/2211/2221 转容器，
        # 4 位总账（5602 等）保持末级，否则存量记账路径会被 422 打断。
        if not row.is_system:
            continue
        target_parent = info.get("parent_code")
        target_level = int(info.get("level") or 1)
        target_leaf = chart_is_leaf(code, info)
        # 稳态下必须零写入：ensure_tenant 在每个请求都会走到这里，
        # 无条件 UPDATE 会让并发请求在 chart_accounts 上互等行锁（实测能把整个
        # 账套的接口全部拖住 60s 以上）。只有口径真的演进时才落库。
        if (
            row.name == info["name"]
            and row.parent_code == target_parent
            and row.level == target_level
            and row.category == info["category"]
            and row.type == info["type"]
            and row.normal_side == direction
            and row.is_leaf == target_leaf
        ):
            continue
        row.name = info["name"]
        row.parent_code = target_parent
        row.level = target_level
        row.category = info["category"]
        row.type = info["type"]
        row.normal_side = direction
        row.is_leaf = target_leaf
        row.updated_at = now
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


async def _load_accounts(
    session: AsyncSession,
    company_id: str,
    codes: Iterable[str],
    *,
    enforce_leaf: bool = True,
) -> dict[str, ChartAccount]:
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
    # enforce_leaf=False 用于改单、冲销与期末结转：这些路径忠实重放历史凭证行，
    # 不能因为科目表后续新增了子级而把历史凭证锁死。
    invalid = sorted(
        code
        for code in unique_codes
        if code not in result or not result[code].is_active or (enforce_leaf and not result[code].is_leaf)
    )
    if invalid:
        raise AccountingError(
            "INVALID_ACCOUNT",
            "凭证包含无效或非末级科目",
            422,
            {
                "accountCodes": invalid,
                **await _account_issue_context(session, company_id, invalid, result),
            },
        )
    return result


async def _voucher_nos_of_month(
    session: AsyncSession, company_id: str, month_hint: str
) -> list[str]:
    """某个月已占用的凭证号，供「凭证号重复」时给用户挑一个没被用的。

    只回传编号本身，不猜「下一个可用号」——号规则跨期回填与前缀都参与生成，
    猜错比不给更糟。挑号这件事交给用户。
    """
    if len(month_hint) != 7:
        return []
    try:
        month_start = date(int(month_hint[:4]), int(month_hint[5:7]), 1)
    except ValueError:
        return []
    month_end = date(
        month_start.year + (1 if month_start.month == 12 else 0),
        1 if month_start.month == 12 else month_start.month + 1,
        1,
    )
    rows = (
        await session.execute(
            select(JournalEntry.voucher_no).where(
                JournalEntry.company_id == company_id,
                JournalEntry.voucher_no.isnot(None),
                JournalEntry.occurred_on >= month_start,
                JournalEntry.occurred_on < month_end,
            )
        )
    ).scalars().all()
    return sorted({str(item) for item in rows})[:_RESCUE_LINE_LIMIT]


async def _account_issue_context(
    session: AsyncSession,
    company_id: str,
    codes: list[str],
    loaded: dict[str, ChartAccount],
) -> dict[str, Any]:
    """科目为什么不能用、以及能换成哪个明细。

    救援弹窗的出路是「去改用明细科目」，所以必须带上候选明细码；
    只有错误码没有明细码的话，这个出路就落不了地。
    """
    children = (
        await session.execute(
            select(ChartAccount.code, ChartAccount.name, ChartAccount.parent_code).where(
                ChartAccount.company_id == company_id,
                ChartAccount.parent_code.in_(codes),
                ChartAccount.is_active.is_(True),
            )
        )
    ).all()
    by_parent: dict[str, list[dict[str, str]]] = {}
    for child_code, child_name, parent_code in children:
        if not parent_code:
            continue
        by_parent.setdefault(parent_code, []).append(
            {"code": str(child_code), "name": str(child_name)}
        )

    issues: list[dict[str, Any]] = []
    for code in codes:
        account = loaded.get(code)
        if account is None:
            reason = "not_found"
        elif not account.is_active:
            reason = "inactive"
        else:
            reason = "not_leaf"
        issues.append(
            {
                "accountCode": code,
                "accountName": str(account.name) if account is not None else "",
                "reason": reason,
                "suggestedChildren": sorted(
                    by_parent.get(code, []), key=lambda item: item["code"]
                )[:_RESCUE_LINE_LIMIT],
            }
        )
    return {"lines": issues}


def _optional_quantity(value: Any) -> Decimal | None:
    """数量字段宽松解析：空/非法一律视为未填，4 位小数量化，0 视为未填。"""
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.0001"))
    except (ValueError, TypeError, ArithmeticError):
        return None
    return parsed if parsed != ZERO else None


def _optional_unit_price(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.000001"))
    except (ValueError, TypeError, ArithmeticError):
        return None
    return parsed if parsed != ZERO else None


def _normalize_voucher_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    voucher = payload.get("voucherLines")
    if not voucher:
        voucher = apply_rd_detail_to_voucher(payload, get_voucher_hint(
            str(payload.get("direction") or "expense"),
            str(payload.get("category") or "其他支出"),
            money(payload.get("amount") or ZERO),
            payer=str(payload.get("payer") or "company_account"),
            payment_status=payload.get("paymentStatus"),
            vat_rate=payload.get("vatRate"),
            vat_amount=payload.get("vatAmount"),
        ))
    normalized: list[dict[str, Any]] = []
    for direction in ("debit", "credit"):
        for raw in voucher.get(direction, []) or []:
            amount = positive_money(raw.get("amount"))
            quantity = _optional_quantity(raw.get("quantity"))
            unit_price = _optional_unit_price(raw.get("unitPrice") or raw.get("unit_price"))
            # 数量×单价校验只在两者都给时做（容差 1 分）：估价入账允许只填数量；
            # 金额仍是唯一权威值，数量不参与借贷平衡。
            if quantity is not None and unit_price is not None:
                expected = (quantity * unit_price).quantize(Decimal("0.01"))
                if abs(expected - amount) > Decimal("0.01"):
                    raise AccountingError(
                        "QUANTITY_AMOUNT_MISMATCH",
                        "数量 × 单价与金额不一致",
                        422,
                        {
                            "accountCode": str(raw.get("accountCode") or ""),
                            "quantity": str(quantity),
                            "unitPrice": str(unit_price),
                            "amount": money_text(amount),
                            "expected": money_text(expected),
                        },
                    )
            normalized.append(
                {
                    "direction": direction,
                    "accountCode": str(raw.get("accountCode") or raw.get("code") or "").strip(),
                    "accountName": str(raw.get("accountName") or raw.get("name") or "").strip(),
                    "amount": amount,
                    "quantity": quantity,
                    "unitPrice": unit_price,
                    "unit": str(raw.get("unit") or "").strip() or None,
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
            {
                "debit": money_text(debit),
                "credit": money_text(credit),
                "difference": money_text(abs(debit - credit)),
                "lineCount": len(normalized),
                # 救援弹窗要能指出「错在哪一行」。只带定位需要的字段，
                # 绝不带 auxiliary（内含 partner_name 客户名）。
                "lines": [
                    {
                        "lineNo": index + 1,
                        "accountCode": line["accountCode"],
                        "accountName": line["accountName"],
                        "direction": line["direction"],
                        "amount": money_text(line["amount"]),
                    }
                    for index, line in enumerate(normalized[:_RESCUE_LINE_LIMIT])
                ],
            },
        )
    return normalized


async def _add_lines(
    session: AsyncSession,
    entry: JournalEntry,
    raw_lines: list[dict[str, Any]],
    *,
    enforce_leaf: bool = True,
) -> None:
    accounts = await _load_accounts(
        session, entry.company_id, [line["accountCode"] for line in raw_lines], enforce_leaf=enforce_leaf
    )
    # 科目完整路径名：二级科目拼父级（如「管理费用-办公费」），底账与报表口径统一
    parent_codes = {a.parent_code for a in accounts.values() if a.parent_code}
    parent_names: dict[str, str] = {}
    if parent_codes:
        parent_rows = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == entry.company_id,
                    ChartAccount.code.in_(parent_codes),
                )
            )
        ).scalars().all()
        parent_names = {p.code: p.name for p in parent_rows}
    # 期末结转/期初凭证是"搬运"不是"发生"：对冲研发科目时不可能带项目明细，
    # 因此系统凭证允许缺省 rdDetail，不参与研发辅助核算校验。
    require_rd_detail = entry.entry_type not in {"closing", "opening"}
    # 辅助核算同理由服务端收口：标准凭证校验 id 有效性并按 id 回填 name；
    # 冲销/结转/期初忠实重放历史行，不再校验（历史档案可能已停用）。
    from services.auxiliary_service import normalize_line_auxiliary

    strict_auxiliary = entry.entry_type not in {"reversal", "closing", "opening"}
    for line_no, line in enumerate(raw_lines, start=1):
        account = accounts[line["accountCode"]]
        auxiliary = line.get("auxiliary") or {}
        if strict_auxiliary:
            auxiliary = await normalize_line_auxiliary(
                session, entry.company_id, account.code, auxiliary
            )
        journal_line = JournalLine(
                journal_entry_id=entry.id,
                company_id=entry.company_id,
                line_no=line_no,
                account_id=account.id,
                account_code=account.code,
                account_name=(
                    f"{parent_names[account.parent_code]}-{account.name}"
                    if account.parent_code and account.parent_code in parent_names
                    else account.name
                ),
                direction=line["direction"],
                amount=line["amount"],
                # 数量金额式（P1 WP-D）：只在科目勾选数量核算时落数量三列；
                # 非数量科目传了数量一律忽略（不报错，客户端无需预判科目配置）。
                quantity=line["quantity"] if account.quantity_enabled else None,
                unit_price=line["unitPrice"] if account.quantity_enabled else None,
                unit=line["unit"] if account.quantity_enabled else None,
                auxiliary=auxiliary,
            )
        session.add(journal_line)
        await session.flush()
        rd_detail = line.get("rdDetail") or {}
        expected_type = RD_EXPENSE_TYPE_BY_ACCOUNT.get(account.code)
        if expected_type:
            if not rd_detail:
                if not require_rd_detail:
                    continue
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
            {
                "debit": money_text(debit),
                "credit": money_text(credit),
                "difference": money_text(abs(debit - credit)),
                "lineCount": len(lines),
                "lines": [
                    {
                        "lineNo": line.line_no,
                        "accountCode": line.account_code,
                        "accountName": line.account_name,
                        "direction": line.direction,
                        "amount": money_text(line.amount),
                    }
                    for line in lines[:_RESCUE_LINE_LIMIT]
                ],
            },
        )
    # 冲销凭证必须能过账：它忠实重放历史凭证行，而这些行可能含后来才被拆出
    # 子级的父级科目（如 2211 → 221101/221102）。期末结转账证同理：它按
    # 实际发生额逐行清零损益科目，历史行里有什么码就得带什么码。
    await _load_accounts(
        session, entry.company_id, [line.account_code for line in lines],
        enforce_leaf=entry.entry_type not in ("reversal", "closing"),
    )


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
        raise AccountingError(
            REVIEW_REQUIRED, "该凭证需要老板或会计复核", 409, _locked_entry_context(entry)
        )
    is_system_entry = entry.entry_type in {"opening", "closing", "reversal"}
    if company.posting_policy == "maker_checker" and not is_system_entry and entry.created_by == user_id and not reviewer:
        entry.status = "pending_review"
        raise AccountingError(
            REVIEW_REQUIRED, "制单人与复核人必须不同", 409,
            {**_locked_entry_context(entry), "createdBy": entry.created_by},
        )
    if company.posting_policy == "maker_checker" and not is_system_entry and entry.created_by == user_id:
        raise AccountingError(
            REVIEW_REQUIRED, "制单人不能复核自己的凭证", 409,
            {**_locked_entry_context(entry), "createdBy": entry.created_by},
        )

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
    entry_type = str(payload.get("entryType") or "standard")
    assert_book_started(company, occurred_on, entry_type)

    # 业务日期 → 记账期间。系统凭证（期初/结转/冲销）由调用方给定期间，它们的
    # occurred_on 未必是业务发生日，不参与补记判定。
    resolution: PeriodResolution | None = None
    backfill_reason = str(payload.get("backfillReason") or "").strip()
    if entry_type in SYSTEM_ENTRY_TYPES:
        period = await get_or_create_period(
            session, company_id, period_start_for(payload.get("period") or occurred_on), lock=True
        )
        assert_period_writable(period)
    else:
        resolution = await resolve_posting_period(
            session,
            company_id,
            occurred_on,
            requested_period=payload.get("period"),
            # 补记会把凭证写进用户没指定的月份，必须由调用方显式同意，不能静默发生
            allow_backfill=bool(payload.get("allowBackfill")),
        )
        if resolution.is_backfill and not backfill_reason:
            raise AccountingError(
                BACKFILL_REASON_REQUIRED,
                f"该业务将补记到 {resolution.posting_period.strftime('%Y-%m')}，请说明补记原因",
                422,
                resolution.as_dict(),
            )
        period = await get_or_create_period(session, company_id, resolution.posting_period, lock=True)
        assert_period_writable(period)
    # 年度锁按「记账期间」判定：补记时业务日期可能落在已结账年度，但本期仍在开放年度
    if entry_type not in ("opening",):
        from services.fiscal_year_service import assert_fiscal_year_open

        await assert_fiscal_year_open(session, company_id, period.period_start.year)
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
    # 旧账导入保留原凭证号（审计连续性）。只对 legacy_import 开放：
    # 通用入口允许指定凭证号等于把编号权交给客户端，断号/重号风险不可控。
    requested_voucher_no = ""
    if source_type == "legacy_import":
        candidate = str(payload.get("voucherNo") or "").strip()
        if candidate:
            taken = (
                await session.execute(
                    select(JournalEntry.id).where(
                        JournalEntry.company_id == company_id,
                        JournalEntry.voucher_no == candidate,
                    )
                )
            ).scalar_one_or_none()
            if taken:
                occurred = str(payload.get("occurDate") or payload.get("occur_date") or "")
                used_numbers = await _voucher_nos_of_month(
                    session, company_id, occurred[:7]
                )
                raise AccountingError(
                    "DUPLICATE_VOUCHER_NO",
                    f"凭证号 {candidate} 已存在，请改号或跳过该张凭证",
                    409,
                    {
                        "voucherNo": candidate,
                        "occurredOn": occurred,
                        "usedVoucherNos": used_numbers,
                    },
                )
            requested_voucher_no = candidate
    summary = source.description
    if resolution is not None and resolution.is_backfill:
        # 摘要前缀是给账簿阅读者的第一提示：这笔不是本月的业务。前缀由系统写死，
        # 后续改单不覆盖；真实业务日期仍保留在 occurred_on / 台账里。
        summary = f"补记{resolution.business_period.strftime('%Y-%m')}｜{summary}"
        metadata.update(
            {
                "isBackfill": True,
                "backfillFromPeriod": resolution.business_period.strftime("%Y-%m"),
                "backfillReason": backfill_reason,
                "backfillSource": source_type,
            }
        )
    entry = JournalEntry(
        id=entry_id,
        company_id=company_id,
        source_transaction_id=source.id,
        voucher_no=requested_voucher_no or None,
        occurred_on=occurred_on,
        period_start=period.period_start,
        summary=summary,
        status="draft",
        entry_type=entry_type,
        currency="CNY",
        created_by=user_id,
        rule_version=str(payload.get("ruleVersion") or "account-mapping-v2"),
        metadata_json=metadata,
        version=1,
    )
    session.add(entry)
    await session.flush()
    # 冲销与期末结转凭证忠实重放历史分录行，这些行可能含后来才被拆出子级的
    # 父级科目（如 2211 → 221101/221102），创建与过账都不能再卡末级校验。
    await _add_lines(
        session, entry, raw_lines,
        enforce_leaf=entry_type not in ("reversal", "closing"),
    )
    if resolution is not None and resolution.is_backfill:
        session.add(
            PeriodBackfill(
                company_id=company_id,
                journal_entry_id=entry.id,
                business_date=occurred_on,
                original_period=resolution.business_period,
                posting_period=period.period_start,
                amount=abs(amount_value),
                reason=backfill_reason,
                source=source_type,
                status="active",
                created_by=user_id,
            )
        )
        await session.flush()
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
        if member.role in POSTING_ROLES and (
            company.posting_policy == "owner_self_post"
            or entry.entry_type in {"opening", "closing", "reversal"}
        ):
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


# 投影回填键：这些字段由银行确认/解绑、已过账投影更新等路径直接维护在
# legacy 投影 payload 上（权威层 metadata 感知不到），序列化时以投影为准。
# 但 update_draft 显式修改其中字段时必须先摘除旧投影值（见 update_draft），
# 否则 stale 投影经 serialize_journal 反灌、再被 _sync_projection 固化，
# 草稿改 payer/paymentStatus 永远不生效（自反馈，2026-10-25）。
PROJECTION_BACKFILL_KEYS = (
    "billingStatus",
    "reconciliationType",
    "paymentDate",
    "bankStatementId",
    "paymentStatus",
    "payer",
    "invoiceNumber",
    "invoiceType",
    "linkedInvoiceEntryId",
    "linkedExpenseEntryId",
)


class _JournalPrefetch:
    """serialize_journal 的批量预取上下文。

    列表场景（list_journals → 报表/待办/申报包）原实现每条凭证发 5-7 条子查询，
    N 条凭证线性放大。这里把行/来源/投影/研发明细/科目全名/用户显示名各收敛成
    一次 IN 查询，渲染阶段零 await。单条路径与批量路径共用 _render_journal，
    保证两条路径输出逐字节一致。
    """

    __slots__ = ("lines_by_entry", "sources", "projections", "rd_by_line", "full_names", "user_names")

    def __init__(
        self,
        lines_by_entry: dict[str, list[JournalLine]],
        sources: dict[str, SourceTransaction],
        projections: dict[str, "LegacyEntryProjection"],
        rd_by_line: dict[str, "RdJournalLineDetail"],
        full_names: dict[str, str],
        user_names: dict[str, str],
    ) -> None:
        self.lines_by_entry = lines_by_entry
        self.sources = sources
        self.projections = projections
        self.rd_by_line = rd_by_line
        self.full_names = full_names
        self.user_names = user_names


async def _prefetch_journals(
    session: AsyncSession, entries: list[JournalEntry], *, lite: bool = False
) -> _JournalPrefetch:
    """lite=True 时跳过分录行/研发明细/科目名/用户名查询（约省 4 次）。

    仅供不读 voucherLines 的轻量消费方（如待办派生）使用：metadata + 投影
    回填语义保持完整，分录行序列化跳过（debit/credit 为空列表）。
    """
    entry_ids = [e.id for e in entries]
    if not entry_ids:
        return _JournalPrefetch({}, {}, {}, {}, {}, {})

    lines_by_entry: dict[str, list[JournalLine]] = {eid: [] for eid in entry_ids}
    all_line_ids: list[str] = []
    rd_by_line: dict[str, RdJournalLineDetail] = {}
    if not lite:
        for line in (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id.in_(entry_ids))
                .order_by(JournalLine.journal_entry_id, JournalLine.line_no)
            )
        ).scalars().all():
            lines_by_entry.setdefault(line.journal_entry_id, []).append(line)

        all_line_ids = [line.id for lines in lines_by_entry.values() for line in lines]
    if all_line_ids:
        for detail in (
            await session.execute(
                select(RdJournalLineDetail).where(RdJournalLineDetail.journal_line_id.in_(all_line_ids))
            )
        ).scalars().all():
            rd_by_line[detail.journal_line_id] = detail

    source_ids = {e.source_transaction_id for e in entries if e.source_transaction_id}
    sources: dict[str, SourceTransaction] = {}
    if source_ids:
        for source in (
            await session.execute(select(SourceTransaction).where(SourceTransaction.id.in_(source_ids)))
        ).scalars().all():
            sources[source.id] = source

    projections = {
        p.id: p
        for p in (
            await session.execute(select(LegacyEntryProjection).where(LegacyEntryProjection.id.in_(entry_ids)))
        ).scalars().all()
    }

    company_id = entries[0].company_id
    all_codes = {line.account_code for lines in lines_by_entry.values() for line in lines}
    full_names: dict[str, str] = {}
    if not lite and all_codes and company_id:
        acct_rows = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(all_codes),
                )
            )
        ).scalars().all()
        acct_by_code = {a.code: a for a in acct_rows}
        parent_codes = {a.parent_code for a in acct_rows if a.parent_code}
        parent_names: dict[str, str] = {}
        if parent_codes:
            parent_rows = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.code.in_(parent_codes),
                    )
                )
            ).scalars().all()
            parent_names = {p.code: p.name for p in parent_rows}
        for a in acct_rows:
            pn = parent_names.get(a.parent_code) if a.parent_code else None
            if not pn:
                continue
            full_names[a.code] = a.name if a.name.startswith(pn + "-") else f"{pn}-{a.name}"

    user_names: dict[str, str] = {}
    if not lite:
        user_ids: set[str] = set()
        for e in entries:
            user_ids.update({e.created_by, e.reviewed_by or "", e.posted_by or ""} - {""})
        user_names = await _user_display_names(session, user_ids)

    return _JournalPrefetch(lines_by_entry, sources, projections, rd_by_line, full_names, user_names)


def _render_journal(entry: JournalEntry, *, api_money: bool, pf: _JournalPrefetch) -> dict[str, Any]:
    lines = pf.lines_by_entry.get(entry.id) or []
    payload = dict(entry.metadata_json or {})
    if entry.source_transaction_id:
        source = pf.sources.get(entry.source_transaction_id)
        if source:
            for key in ("billingStatus", "reconciliationType", "paymentDate", "bankStatementId"):
                if key in (source.raw_payload or {}):
                    payload[key] = source.raw_payload[key]
    # 对账状态由银行确认/解绑路径直接维护在 legacy 投影 payload 上
    # （json_set/json_remove），权威层 metadata 感知不到，这里以投影为准回填。
    projection = pf.projections.get(entry.id)
    if projection is not None:
        try:
            legacy_payload = json.loads(projection.payload) if isinstance(projection.payload, str) else (projection.payload or {})
        except (json.JSONDecodeError, TypeError):
            legacy_payload = {}
        if isinstance(legacy_payload, dict):
            for key in PROJECTION_BACKFILL_KEYS:
                if legacy_payload.get(key) is not None:
                    payload[key] = legacy_payload[key]
            # 归档标志由导入/迁移路径直接维护在投影 payload 上，同样以投影为准回填
            if legacy_payload.get("is_archive_only") is not None:
                payload["is_archive_only"] = legacy_payload.get("is_archive_only")
            if legacy_payload.get("isArchiveOnly") is not None:
                payload["isArchiveOnly"] = legacy_payload.get("isArchiveOnly")
    rd_details = pf.rd_by_line

    # 科目完整路径名：预取阶段已按公司全量代码装配（含父级名）
    full_names: dict[str, str] = pf.full_names

    def serialize_line(line: JournalLine) -> dict[str, Any]:
        result = {
            "accountCode": line.account_code,
            "accountName": full_names.get(line.account_code) or line.account_name,
            "amount": money_text(line.amount) if api_money else line.amount,
        }
        if line.quantity is not None:
            # 数量去掉末尾 0（2500.0000 → 2500），单价保持金额惯例
            result["quantity"] = f"{line.quantity:.4f}".rstrip("0").rstrip(".") or "0"
            if line.unit_price is not None:
                result["unitPrice"] = money_text(line.unit_price) if api_money else line.unit_price
            if line.unit:
                result["unit"] = line.unit
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

    name_map = pf.user_names
    debit = [serialize_line(line) for line in lines if line.direction == "debit"]
    credit = [serialize_line(line) for line in lines if line.direction == "credit"]
    payload.update(
        {
            "id": entry.id,
            "company_id": entry.company_id,
            "companyId": entry.company_id,
            "title": entry.summary,
            "occurDate": entry.occurred_on.isoformat(),
            # 记账期间（法定口径）。补记时可能与业务日期所属月不同，
            # 报表按法定口径取 period，管理口径才用 occurDate。
            "period": entry.period_start.strftime("%Y-%m"),
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


async def serialize_journal(
    session: AsyncSession,
    entry: JournalEntry,
    *,
    api_money: bool = True,
    lite: bool = False,
) -> dict[str, Any]:
    pf = await _prefetch_journals(session, [entry], lite=lite)
    return _render_journal(entry, api_money=api_money, pf=pf)


async def serialize_journals(
    session: AsyncSession,
    entries: list[JournalEntry],
    *,
    api_money: bool = True,
    lite: bool = False,
) -> list[dict[str, Any]]:
    """批量序列化：行/投影/研发明细/科目/用户名各一次 IN 查询（消除列表场景 N+1）。

    lite=True 供不读 voucherLines 的消费方（待办派生）跳过分录行与科目装配。
    """
    pf = await _prefetch_journals(session, entries, lite=lite)
    return [_render_journal(entry, api_money=api_money, pf=pf) for entry in entries]


async def list_journals(
    session: AsyncSession,
    company_id: str,
    *,
    statuses: Iterable[str] = ACTIVE_ENTRY_STATUSES,
    api_money: bool = True,
    lite: bool = False,
) -> list[dict[str, Any]]:
    entries = (
        await session.execute(
            select(JournalEntry)
            .where(JournalEntry.company_id == company_id, JournalEntry.status.in_(tuple(statuses)))
            .order_by(JournalEntry.occurred_on.desc(), JournalEntry.created_at.desc())
        )
    ).scalars().all()
    return await serialize_journals(session, list(entries), api_money=api_money, lite=lite)


async def journal_entries_for_reports(
    session: AsyncSession,
    company_id: str,
    *,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """Return report entries with Decimal amounts.

    Legal reports use posted/reversed entries only. Management dashboards can
    include draft and pending-review entries so the boss view matches the
    entries visible on the workbench before month-end posting.
    """
    statuses = ACTIVE_ENTRY_STATUSES if include_drafts else {"posted", "reversed"}
    return await list_journals(
        session,
        company_id,
        statuses=tuple(sorted(statuses)),
        api_money=False,
    )


async def posted_entries_for_reports(session: AsyncSession, company_id: str) -> list[dict[str, Any]]:
    """Return authoritative posted/reversed entries with Decimal amounts."""
    return await journal_entries_for_reports(session, company_id)


# 已过账凭证仍可更新的"申报/对账跟踪"字段：只写投影 payload，不碰借贷
POSTED_PROJECTION_FIELDS = {
    "billingStatus",
    "paymentStatus",
    "payer",
    "invoiceNumber",
    "invoiceType",
    "linkedInvoiceEntryId",
    "linkedExpenseEntryId",
    "is_archive_only",
    "isArchiveOnly",
}
NON_BANK_PAYERS = {"boss_personal", "employee_personal", "prepaid_deduct", "platform_balance", "advance_offset"}


async def _update_posted_projection_fields(
    session: AsyncSession,
    entry: JournalEntry,
    updates: dict[str, Any],
    *,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    """已过账凭证的跟踪字段（开票状态/付款状态/付款方归属）只在 legacy 投影 payload 上维护，
    权威凭证与 metadata 保持不可变，不触碰借贷。"""
    projection = await session.get(LegacyEntryProjection, entry.id)
    if projection is None:
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE, "已过账凭证不能修改，请使用冲销", 409, _locked_entry_context(entry)
        )
    try:
        payload = json.loads(projection.payload) if isinstance(projection.payload, str) else dict(projection.payload or {})
    except (json.JSONDecodeError, TypeError):
        payload = {}
    for key, value in updates.items():
        if value:
            payload[key] = value
        else:
            payload.pop(key, None)
    projection.payload = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    projection.updated_at = _iso_now()
    await record_audit(
        session,
        company_id=entry.company_id,
        user_id=user_id,
        action="update_entry_tracking_fields",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        after={**updates, "entryStatus": entry.status},
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
    auto_reverse_repost = bool(updates.pop("autoReverseRepost", False))
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status not in {"draft", "pending_review"}:
        # 开票/付款状态是申报与对账跟踪标志，不影响借贷；已过账凭证允许只更新它们（投影层），
        # 其余任何字段修改仍然必须走冲销。
        if updates and set(updates).issubset(POSTED_PROJECTION_FIELDS):
            # 付款去向决定借贷对手科目：已过账凭证的 1002 行不可改，去向与凭证不一致时必须冲销重记，
            # 否则账面银行存款与实际不符却被对账跳过。
            if {"payer", "paymentStatus"} & set(updates):
                merged = {**(entry.metadata_json or {}), **updates}
                payer = str(merged.get("payer") or "")
                wants_bank = not (
                    str(merged.get("paymentStatus") or "") == "unpaid"
                    or payer in NON_BANK_PAYERS
                    or payer.startswith("employee_personal:")
                )
                has_bank = any(line.account_code == "1002" for line in await _entry_lines(session, entry.id))
                if wants_bank != has_bank:
                    if auto_reverse_repost and entry.status == "posted":
                        # ponytail: 老板结账页一键改已过账凭证去向 → 后台自动红冲原凭证 + 按新去向重记过账
                        await reverse_journal(
                            session,
                            entry.id,
                            company_id=company_id,
                            user_id=user_id,
                            request_id=f"{request_id}-rev",
                            reason="结账确认收支去向自动冲销重记",
                        )
                        new_payload: dict[str, Any] = {**(entry.metadata_json or {})}
                        for drop_key in ("id", "voucherLines", "voucherNo", "voucher_no", "dedupFingerprint", "wechat_transaction_key", "invoiceNumber"):
                            new_payload.pop(drop_key, None)
                        for k, v in updates.items():
                            if v:
                                new_payload[k] = v
                            else:
                                new_payload.pop(k, None)
                        new_payload["id"] = str(uuid4())
                        new_payload["title"] = entry.summary
                        new_payload["occurDate"] = entry.occurred_on.isoformat()
                        return await create_entry(
                            session,
                            new_payload,
                            company_id=company_id,
                            user_id=user_id,
                            request_id=f"{request_id}-repost",
                            confirm=True,
                        )
                    raise AccountingError(
                        ENTRY_POSTED_IMMUTABLE,
                        "该凭证已过账，付款去向与凭证科目不一致，请先冲销后按新去向重新记账",
                        409,
                        _locked_entry_context(entry),
                    )
            return await _update_posted_projection_fields(
                session, entry, updates, user_id=user_id, request_id=request_id
            )
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE, "已过账凭证不能修改，请使用冲销", 409, _locked_entry_context(entry)
        )
    if (entry.metadata_json or {}).get("source") == "wechat_settlement":
        raise AccountingError("SYSTEM_SETTLEMENT", "结算凭证按银行净额生成，请取消对账后重新确认，不能直接修改", 409)
    old_period = await get_or_create_period(session, company_id, entry.period_start, lock=True)
    assert_period_writable(old_period)
    # confirmRegenerateVoucher 是控制位，不是凭证字段，绝不能写进 metadata
    confirm_regenerate = bool(updates.pop("confirmRegenerateVoucher", False))
    regenerate_keys = {"amount", "direction", "category", "payer", "paymentStatus"} & set(updates)
    before = await serialize_journal(session, entry)
    metadata = dict(entry.metadata_json or {})
    metadata.update(_json_safe(updates))
    if "amount" in updates:
        metadata["amount"] = money_text(updates["amount"])
    if "title" in updates and updates["title"]:
        entry.summary = str(updates["title"]).strip()
    ledger: PeriodBackfill | None = None
    if "occurDate" in updates and updates["occurDate"]:
        new_date = _parse_date(updates["occurDate"])
        company_row = await session.get(Company, company_id)
        if company_row is not None:
            assert_book_started(company_row, new_date, entry.entry_type)
        new_resolution: PeriodResolution | None = None
        if entry.entry_type not in SYSTEM_ENTRY_TYPES:
            new_resolution = await resolve_posting_period(
                session,
                company_id,
                new_date,
                requested_period=updates.get("period"),
                allow_backfill=bool(updates.get("allowBackfill")),
            )
            if new_resolution.is_backfill and not str(
                updates.get("backfillReason") or metadata.get("backfillReason") or ""
            ).strip():
                raise AccountingError(
                    BACKFILL_REASON_REQUIRED,
                    f"该业务将补记到 {new_resolution.posting_period.strftime('%Y-%m')}，请说明补记原因",
                    422,
                    new_resolution.as_dict(),
                )
        new_period = await get_or_create_period(
            session,
            company_id,
            new_resolution.posting_period if new_resolution else new_date,
            lock=True,
        )
        assert_period_writable(new_period)
        entry.occurred_on = new_date
        entry.period_start = new_period.period_start
        ledger = (
            await session.execute(
                select(PeriodBackfill).where(
                    PeriodBackfill.company_id == company_id,
                    PeriodBackfill.journal_entry_id == entry.id,
                )
            )
        ).scalar_one_or_none()
        if new_resolution is not None and new_resolution.is_backfill:
            reason_text = str(updates.get("backfillReason") or metadata.get("backfillReason") or "").strip()
            metadata.update(
                {
                    "isBackfill": True,
                    "backfillFromPeriod": new_resolution.business_period.strftime("%Y-%m"),
                    "backfillReason": reason_text,
                }
            )
            if ledger is None:
                ledger = PeriodBackfill(
                    company_id=company_id,
                    journal_entry_id=entry.id,
                    business_date=new_date,
                    original_period=new_resolution.business_period,
                    posting_period=new_period.period_start,
                    amount=abs(money(metadata.get("amount") or ZERO)),
                    reason=reason_text,
                    source=str(metadata.get("sourceType") or metadata.get("source") or "manual"),
                    status="active",
                    created_by=user_id,
                )
                session.add(ledger)
                await session.flush()
            else:
                ledger.business_date = new_date
                ledger.original_period = new_resolution.business_period
                ledger.posting_period = new_period.period_start
                ledger.reason = reason_text
                ledger.amount = abs(money(metadata.get("amount") or ZERO))
                ledger.status = "active"
        else:
            # 改回到业务所属期间本身：这笔业务不再跨期，台账留痕但标记失效
            for key in ("isBackfill", "backfillFromPeriod", "backfillReason", "backfillSource"):
                metadata.pop(key, None)
            if ledger is not None and ledger.status == "active":
                ledger.status = "reversed"
    financial_fields = {"amount", "direction", "category", "payer", "paymentStatus", "voucherLines"}
    # 人工调整过的分录不能因为「顺手改个分类」被规则静默重算冲掉：
    # 必须由前端弹窗确认后带 confirmRegenerateVoucher 才允许覆盖。
    if (
        metadata.get("voucherManuallyAdjusted")
        and regenerate_keys
        and "voucherLines" not in updates
        and not confirm_regenerate
    ):
        raise AccountingError(
            "VOUCHER_ADJUSTMENT_WILL_BE_LOST",
            "这笔凭证的分录已被人工调整，修改方向/分类/付款方式/金额会按系统规则重新生成并覆盖手改结果",
            409,
            {
                "entryId": entry.id,
                "adjustedFields": sorted(regenerate_keys),
                "voucherAdjustment": metadata.get("voucherAdjustment") or {},
            },
        )
    if "voucherLines" in updates or (confirm_regenerate and regenerate_keys):
        # 分录已被重写（系统路径显式换行，或用户确认覆盖）：手工调整标记随之失效，
        # 否则标记与实际分录不一致，下一次改分类会被无谓地拦住。
        metadata.pop("voucherManuallyAdjusted", None)
        metadata.pop("voucherAdjustment", None)
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
        await _add_lines(session, entry, raw_lines, enforce_leaf=False)
        if entry.source_transaction_id:
            source = await session.get(SourceTransaction, entry.source_transaction_id)
            if source:
                source.amount = abs(money(rebuilt_payload.get("amount") or ZERO))
                source.occurred_on = entry.occurred_on
                source.description = entry.summary
                source.raw_payload = rebuilt_payload
        if ledger is not None and ledger.status == "active":
            ledger.amount = abs(money(metadata.get("amount") or ZERO))
    # 补记前缀由系统维护：改标题不会把前缀冲掉，也不会重复叠加；
    # 改回到业务所属期间后前缀自动摘除。
    base_summary = entry.summary
    if base_summary.startswith("补记") and "｜" in base_summary[:12]:
        base_summary = base_summary.split("｜", 1)[1]
    if metadata.get("isBackfill"):
        entry.summary = f"补记{metadata.get('backfillFromPeriod')}｜{base_summary}"
    else:
        entry.summary = base_summary
    metadata.pop("allowBackfill", None)
    metadata.pop("period", None)
    entry.metadata_json = metadata
    entry.status = "draft"
    entry.reviewed_by = None
    entry.version += 1
    entry.updated_at = _utcnow()
    await session.flush()
    # 本次更新命中的投影回填键，先从旧投影摘除：serialize_journal 以投影为准回填，
    # 不摘除的话 stale 投影值会反灌进 after 与 _sync_projection 的新投影（自反馈）。
    stale_keys = set(PROJECTION_BACKFILL_KEYS) & set(updates)
    if stale_keys:
        projection = await session.get(LegacyEntryProjection, entry.id)
        if projection is not None:
            legacy_payload = (
                json.loads(projection.payload) if isinstance(projection.payload, str) else dict(projection.payload or {})
            )
            changed = False
            for key in stale_keys:
                if legacy_payload.get(key) is not None:
                    legacy_payload.pop(key, None)
                    changed = True
            if changed:
                projection.payload = json.dumps(_json_safe(legacy_payload), ensure_ascii=False, separators=(",", ":"))
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


# ── 人工调整分录（会计纠错）────────────────────────────────────────────────────
# 系统按「方向 + 分类 + 付款方式」生成分录，AI 分类或规则偶有记错科目。允许会计
# 在过账前直接改分录，但三条底线不能破：
#   1. 借贷必须平衡，且借方合计仍等于这笔交易的金额（改错金额属于改交易，不是改科目）；
#   2. 科目必须是本企业启用中的末级科目（口径见 core.chart_policy）；
#   3. 每次调整都留痕（谁、何时、改前改后），且已过账凭证仍只能冲销。
MANUAL_VOUCHER_LINE_LIMIT = 30


def _normalize_manual_voucher_lines(raw_lines: Any) -> list[dict[str, Any]]:
    """人工调整用：只接受显式传入的分录行，绝不回落到系统建议。

    ``_normalize_voucher_lines`` 在没有 ``voucherLines`` 时会用 hint 现生成一套，
    这对「保存交易」是对的，对「人工调整」是灾难——前端状态坏了会静默把用户
    刚改的科目换成系统建议，看上去像保存成功。这里宁可报错。
    """
    if not isinstance(raw_lines, list) or len(raw_lines) < 2:
        raise AccountingError("VOUCHER_LINES_REQUIRED", "分录至少需要一借一贷两行", 422)
    if len(raw_lines) > MANUAL_VOUCHER_LINE_LIMIT:
        raise AccountingError(
            "VOUCHER_LINES_TOO_MANY", f"一张凭证最多 {MANUAL_VOUCHER_LINE_LIMIT} 行", 422
        )
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            raise AccountingError("INVALID_VOUCHER_LINE", f"第 {index} 行格式不正确", 422, {"lineNo": index})
        side = str(raw.get("direction") or raw.get("side") or "").strip().lower()
        if side in {"借", "借方", "debit", "dr"}:
            direction = "debit"
        elif side in {"贷", "贷方", "credit", "cr"}:
            direction = "credit"
        else:
            raise AccountingError(
                "INVALID_VOUCHER_LINE", f"第 {index} 行未选择借贷方向", 422, {"lineNo": index}
            )
        code = str(raw.get("accountCode") or raw.get("code") or "").strip()
        if not code:
            raise AccountingError(
                "INVALID_VOUCHER_LINE", f"第 {index} 行未选择科目", 422, {"lineNo": index}
            )
        try:
            amount = positive_money(raw.get("amount"))
        except Exception as exc:  # noqa: BLE001 - MoneyError 统一转成可展示的 422
            raise AccountingError(
                "INVALID_VOUCHER_LINE",
                f"第 {index} 行金额必须大于零",
                422,
                {"lineNo": index},
            ) from exc
        normalized.append(
            {
                "direction": direction,
                "accountCode": code,
                "accountName": str(raw.get("accountName") or raw.get("name") or "").strip(),
                "amount": amount,
                "quantity": None,
                "unitPrice": None,
                "unit": None,
                "auxiliary": _json_safe(raw.get("auxiliary") or {}),
                "rdDetail": _json_safe(raw.get("rdDetail") or {}),
            }
        )
    debit = sum((line["amount"] for line in normalized if line["direction"] == "debit"), ZERO)
    credit = sum((line["amount"] for line in normalized if line["direction"] == "credit"), ZERO)
    if debit <= ZERO or debit != credit:
        raise AccountingError(
            UNBALANCED_ENTRY,
            "凭证借贷不平衡",
            422,
            {
                "debit": money_text(debit),
                "credit": money_text(credit),
                "difference": money_text(abs(debit - credit)),
                "lineCount": len(normalized),
                "lines": [
                    {
                        "lineNo": index + 1,
                        "accountCode": line["accountCode"],
                        "accountName": line["accountName"],
                        "direction": line["direction"],
                        "amount": money_text(line["amount"]),
                    }
                    for index, line in enumerate(normalized[:_RESCUE_LINE_LIMIT])
                ],
            },
        )
    return normalized


def _carry_over_line_meta(
    new_lines: list[dict[str, Any]],
    previous_lines: list[Any],
    rd_snapshots: dict[str, dict[str, Any]],
) -> None:
    """同科目同方向的行沿用原辅助核算/研发明细。

    会计改一个科目，不该顺带把客户、研发项目清空——那些是业务属性，不是科目
    属性。只有科目被换掉时才丢弃（辅助核算维度是挂在科目上的）。
    """
    pool: dict[tuple[str, str], list[Any]] = {}
    for line in previous_lines:
        pool.setdefault((line.direction, line.account_code), []).append(line)
    for line in new_lines:
        candidates = pool.get((line["direction"], line["accountCode"])) or []
        if not candidates:
            continue
        source = candidates.pop(0)
        if source.auxiliary:
            line["auxiliary"] = _json_safe(source.auxiliary)
        if source.quantity is not None:
            line["quantity"] = source.quantity
            line["unitPrice"] = source.unit_price
            line["unit"] = source.unit
        if line["rdDetail"]:
            continue
        rd_detail = rd_snapshots.get(source.id)
        if rd_detail:
            detail = dict(rd_detail)
            # 金额改过的话按新行金额收口，避免 rdAmount 与行金额打架
            detail["rdAmount"] = money_text(line["amount"])
            if money(detail.get("originalAmount") or ZERO) < line["amount"]:
                detail["originalAmount"] = money_text(line["amount"])
            line["rdDetail"] = detail


async def _rd_detail_snapshots(session: AsyncSession, lines: list[JournalLine]) -> dict[str, dict[str, Any]]:
    if not lines:
        return {}
    rows = (
        await session.execute(
            select(RdJournalLineDetail).where(
                RdJournalLineDetail.journal_line_id.in_([line.id for line in lines])
            )
        )
    ).scalars().all()
    return {
        row.journal_line_id: {
            "projectId": row.project_id,
            "expenseType": row.expense_type,
            "originalAmount": money_text(row.original_amount),
            "rdAmount": money_text(row.rd_amount),
            "allocationMethod": row.allocation_method,
            "allocationBasis": row.allocation_basis,
            "notes": row.notes,
        }
        for row in rows
    }


async def adjust_voucher_lines(
    session: AsyncSession,
    entry_id: str,
    *,
    lines: Any = None,
    reason: str = "",
    reset_to_system: bool = False,
    company_id: str,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    """会计手工改写分录（未过账凭证）。

    ``reset_to_system=True`` 时丢弃手工调整，按系统的科目映射规则重新生成。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status not in {"draft", "pending_review"}:
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE,
            "已过账凭证不能直接调整科目，如需更正请使用冲销",
            409,
            _locked_entry_context(entry),
        )
    metadata = dict(entry.metadata_json or {})
    if metadata.get("source") == "wechat_settlement":
        raise AccountingError(
            "SYSTEM_SETTLEMENT", "结算凭证按银行净额生成，请取消对账后重新确认，不能直接调整", 409
        )
    if entry.entry_type in SYSTEM_ENTRY_TYPES:
        raise AccountingError(
            "SYSTEM_VOUCHER_IMMUTABLE", "期初、结转、冲销凭证的分录由系统生成，不能手工调整", 409
        )
    period = await get_or_create_period(session, company_id, entry.period_start, lock=True)
    assert_period_writable(period)

    before = await serialize_journal(session, entry)
    previous_lines = await _entry_lines(session, entry.id)
    snapshots = await _rd_detail_snapshots(session, previous_lines)
    previous_payload = [
        {
            "lineNo": index + 1,
            "direction": line.direction,
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": money_text(line.amount),
        }
        for index, line in enumerate(previous_lines[:_RESCUE_LINE_LIMIT])
    ]

    if reset_to_system:
        # 与 create/save 的生成路径同源（含含税拆分），否则「恢复系统建议」
        # 会得到一套和系统原本建议不一样的分录。
        system_hint = apply_rd_detail_to_voucher(
            metadata,
            get_voucher_hint(
                str(metadata.get("direction") or "expense"),
                str(metadata.get("category") or "其他支出"),
                money(metadata.get("amount") or ZERO),
                payer=str(metadata.get("payer") or "company_account"),
                vat_rate=metadata.get("vatRate") if metadata.get("vatSeparated") else None,
            ),
        )
        target = _normalize_voucher_lines({**metadata, "voucherLines": system_hint})
    else:
        target = _normalize_manual_voucher_lines(lines)
        _carry_over_line_meta(target, previous_lines, snapshots)

    debit_total = sum((line["amount"] for line in target if line["direction"] == "debit"), ZERO)
    raw_amount = metadata.get("amount")
    trade_amount = money(raw_amount) if raw_amount not in (None, "") else ZERO
    if trade_amount > ZERO and debit_total != trade_amount:
        raise AccountingError(
            "VOUCHER_AMOUNT_MISMATCH",
            f"借方合计需等于交易金额 {money_text(trade_amount)}（当前 {money_text(debit_total)}）；"
            "如交易金额本身记错，请用「编辑」修改金额",
            422,
            {"expected": money_text(trade_amount), "actual": money_text(debit_total)},
        )

    await session.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry.id))
    await session.flush()
    # enforce_leaf=True：手工调整是「新记账」，必须落到启用中的末级科目；
    # 历史凭证据实重放才用 False（改单/冲销/结转路径）。
    await _add_lines(session, entry, target, enforce_leaf=True)

    if reset_to_system:
        metadata.pop("voucherManuallyAdjusted", None)
        metadata.pop("voucherAdjustment", None)
        action = "reset_voucher_lines"
    else:
        metadata["voucherManuallyAdjusted"] = True
        metadata["voucherAdjustment"] = {
            "adjustedAt": _utcnow().isoformat(),
            "adjustedBy": user_id,
            "reason": str(reason or "").strip(),
            "previousLines": previous_payload,
        }
        action = "adjust_voucher_lines"
    entry.metadata_json = metadata
    # 待复核的凭证被改过分录，原复核人看到的内容已变，必须重新走复核。
    if entry.status == "pending_review":
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
        action=action,
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
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE, "已过账凭证不能删除，请使用冲销", 409, _locked_entry_context(entry)
        )
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
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE, "该凭证不能再次过账", 409, _locked_entry_context(entry)
        )
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
        raise AccountingError(
            REVIEW_REQUIRED, "只有待复核凭证可以审批", 409,
            {**_locked_entry_context(entry), "createdBy": entry.created_by},
        )
    return await _post_existing(session, entry, user_id=user_id, request_id=request_id, reviewer=True)


async def submit_journal_for_review(session: AsyncSession, entry_id: str, *, company_id: str, user_id: str, request_id: str) -> JournalEntry:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.status == "pending_review":
        return entry
    if entry.status != "draft":
        raise AccountingError(
            REVIEW_REQUIRED, "只有草稿凭证可以提交复核", 409,
            {**_locked_entry_context(entry), "createdBy": entry.created_by},
        )
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
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE, "只有已过账凭证可以冲销", 409, _locked_entry_context(original)
        )
    if not reason or not reason.strip():
        raise AccountingError("REVERSAL_REASON_REQUIRED", "冲销必须填写原因")
    # 冲销默认落在原凭证期间（红字与原凭证同期相消，月度报表才不失真）；
    # 原期间已关账时回落到指定日期/当天，走"当期调整"口径。
    if reversal_date in (None, ""):
        occurred_on = original.occurred_on
    else:
        occurred_on = _parse_date(reversal_date)
        company_row = await session.get(Company, company_id)
        if company_row is not None:
            assert_book_started(company_row, occurred_on, original.entry_type)
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
            **(
                {
                    "isClosingTransfer": True,
                    "sourceType": (original.metadata_json or {}).get("sourceType") or "closing_transfer",
                }
                if original.entry_type == "closing" or (original.metadata_json or {}).get("isClosingTransfer")
                else {}
            ),
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
    await _add_lines(session, reversal, raw_lines, enforce_leaf=False)
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


async def correct_entry_category(
    session: AsyncSession,
    entry_id: str,
    new_category: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    reason: str,
) -> dict[str, str]:
    """更正已过账凭证的分类/科目：红字冲销原凭证 + 按新分类重建并双双过账。

    合规留痕：原凭证生成红字冲销（reversal 凭证），新凭证独立成单；两者过账后
    账面净额即新分类口径，报表/对账/税务口径一致。只适用于普通业务凭证，发票
    挂账（贷 2241/借 1122）等特殊凭证应走冲销后手动重做。
    """
    from services.account_mapper import get_voucher_hint

    original = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if original.status != "posted":
        raise AccountingError(ENTRY_POSTED_IMMUTABLE, "只有已过账凭证可以更正科目", 409, _locked_entry_context(original))
    meta = dict(original.metadata_json or {})
    direction = str(meta.get("direction") or "expense")
    amount = money(meta.get("amount") or ZERO)
    payer = str(meta.get("payer") or "company_account")
    if not new_category or not new_category.strip():
        raise AccountingError("INVALID_CATEGORY", "请选择更正后的分类", 422, {"entryId": entry_id})

    reversal = await reverse_journal(
        session,
        entry_id,
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:rev",
        reason=reason.strip() or f"更正科目为 {new_category}",
    )

    hint = get_voucher_hint(
        direction,
        new_category,
        amount,
        payer,
        vat_rate=meta.get("vatRate"),
        vat_amount=meta.get("vatAmount"),
        payment_status=meta.get("paymentStatus"),
    )
    corrected = await create_entry(
        session,
        {
            "id": f"correct-{original.id[:12]}-{uuid4().hex[:6]}",
            "title": meta.get("title") or original.summary,
            "amount": money_text(amount),
            "direction": direction,
            "category": new_category,
            "occurDate": original.occurred_on.isoformat(),
            "payer": payer,
            "source": str(meta.get("source") or "manual"),
            "paymentStatus": meta.get("paymentStatus"),
            "invoiceNumber": meta.get("invoiceNumber"),
            "invoiceType": meta.get("invoiceType"),
            "voucherLines": hint,
            "voucherAuthority": "system",
            "reason": f"更正自 {original.id}",
        },
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:new",
        confirm=False,
    )
    await post_journal(session, reversal.id, company_id=company_id, user_id=user_id, request_id=f"{request_id}:post-rev")
    await post_journal(session, corrected.id, company_id=company_id, user_id=user_id, request_id=f"{request_id}:post-new")
    await _sync_projection(session, corrected)
    return {"reversalId": reversal.id, "correctedId": corrected.id}


async def available_periods(
    session: AsyncSession, company_id: str, *, start_from: date | None = None
) -> dict[str, Any]:
    """Distinct months holding any activity, for the closing-wizard period picker.

    Sources: journal entries (invoices, salary accruals and manual bookings all
    land here), accounting periods (status per month), imported bank statements
    and salary sheets. ``status`` mirrors the accounting period status and is
    None when the month was never touched by closing.

    ``start_from`` 是期间下限（账套启用月）。启用日期之前导进来的历史流水、
    工资表不在本账套的记账范围：列进选择器会让用户以为「1 月要结账」，
    更糟的是被 ``preceding_open_months`` 当成未关账月份而挡住本期结账。
    传 None 时自动取账套启用日期；账套没填启用日期则不加限制。
    """
    if start_from is None:
        start_from = await book_start_month(session, company_id)
    floor_key = start_from.strftime("%Y-%m") if start_from else None

    def below_floor(key: str) -> bool:
        return bool(floor_key) and key < floor_key

    entry_months = (
        await session.execute(
            select(JournalEntry.period_start).where(JournalEntry.company_id == company_id).distinct()
        )
    ).scalars().all()
    period_rows = (
        await session.execute(
            select(AccountingPeriod.period_start, AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id
            )
        )
    ).all()

    months: dict[str, str | None] = {}
    for period_start in entry_months:
        key = period_start.strftime("%Y-%m")
        if not below_floor(key):
            months[key] = None
    for period_start, status in period_rows:
        key = period_start.strftime("%Y-%m")
        if below_floor(key):
            continue
        months[key] = status or months.get(key)

    from services.database import get_conn

    conn = await get_conn()
    try:
        try:
            bank_cursor = await conn.execute(
                "SELECT DISTINCT substr(transaction_date, 1, 7) AS month FROM bank_statements WHERE company_id = ?",
                (company_id,),
            )
            for row in await bank_cursor.fetchall():
                month = str(row["month"] or "")[:7]
                if len(month) == 7 and not below_floor(month):
                    months.setdefault(month, None)
        except Exception as exc:
            # 表可能尚未建（迁移未跑齐）：降级为跳过该来源，但留痕便于排查
            logger.warning("关账顺序检查：辅助表月份查询失败，跳过该来源 company=%s: %s", company_id, exc)
        try:
            salary_cursor = await conn.execute(
                "SELECT DISTINCT month FROM salary_sheets WHERE company_id = ?",
                (company_id,),
            )
            for row in await salary_cursor.fetchall():
                month = str(row["month"] or "")[:7]
                if len(month) == 7 and not below_floor(month):
                    months.setdefault(month, None)
        except Exception as exc:
            logger.warning("关账顺序检查：辅助表月份查询失败，跳过该来源 company=%s: %s", company_id, exc)
    finally:
        await conn.close()

    return {
        "months": [
            {"month": month, "status": months[month]}
            for month in sorted(months, reverse=True)
        ]
    }


async def pre_book_start_activity(
    session: AsyncSession, company_id: str, *, start: date | None = None
) -> dict[str, Any]:
    """启用日期之前、仍留在库里的原始业务数据（银行流水 / 工资表）。

    这些月份被 ``available_periods`` 挡在选择器之外了，但不能让数据凭空消失：
    结账体检要把它们作为**提示项**报出来，用户才知道「账套建晚了」或「这批
    历史数据该走期初导账」，而不是继续在启用前的月份里找凭证。
    """
    if start is None:
        start = await book_start_month(session, company_id)
    if start is None:
        return {"count": 0}
    floor_key = start.strftime("%Y-%m")

    from services.database import get_conn

    bank_count = salary_count = 0
    months: set[str] = set()
    conn = await get_conn()
    try:
        try:
            cursor = await conn.execute(
                "SELECT substr(transaction_date, 1, 7) AS month FROM bank_statements WHERE company_id = ?",
                (company_id,),
            )
            for row in await cursor.fetchall():
                month = str(row["month"] or "")[:7]
                if len(month) == 7 and month < floor_key:
                    bank_count += 1
                    months.add(month)
        except Exception as exc:
            # 表可能尚未建（迁移未跑齐）：降级为跳过该来源，但留痕便于排查
            logger.warning("关账顺序检查：辅助表月份查询失败，跳过该来源 company=%s: %s", company_id, exc)
        try:
            cursor = await conn.execute(
                "SELECT month FROM salary_sheets WHERE company_id = ?",
                (company_id,),
            )
            for row in await cursor.fetchall():
                month = str(row["month"] or "")[:7]
                if len(month) == 7 and month < floor_key:
                    salary_count += 1
                    months.add(month)
        except Exception as exc:
            logger.warning("结账前活动检查：辅助表月份查询失败，跳过该来源 company=%s: %s", company_id, exc)
    finally:
        await conn.close()

    return {
        "count": bank_count + salary_count,
        "startDate": start.isoformat(),
        "bankCount": bank_count,
        "salaryCount": salary_count,
        "months": sorted(months),
    }


async def preceding_open_months(
    session: AsyncSession, company_id: str, month_start: date
) -> tuple[int, str | None]:
    """早于 ``month_start``、有业务发生但尚未关账的月份。

    口径与 ``available_periods`` 一致（凭证 / 期间行 / 银行流水 / 工资表），
    所以「有流水却一张凭证都没有」的月份同样算未关账——否则它的业务会被
    静默跳过，后一个月照常结转。关账守卫与体检清单共用本函数，避免两处漂移。

    下限是账套启用月：启用日期之前的流水/工资表不在本账套记账范围，不能拿它
    拦着本期结账（否则启用前的历史数据会让整本账永远结不掉）。
    """
    target = month_start.strftime("%Y-%m")
    pending = [
        item["month"]
        for item in (await available_periods(session, company_id))["months"]
        if item["status"] != "closed" and item["month"] < target
    ]
    if not pending:
        return 0, None
    return len(pending), min(pending)


async def _intangible_close_checks(session: AsyncSession, company_id: str, month_start: Any) -> list[dict[str, Any]]:
    """无形资产月结检查项（设计方案第 8 章）。

    - INTANGIBLE_AMORTIZATION：本期及以前存在未摊销 planned 行 → blocking；
    - IMPAIRMENT_TEST_DUE：存在未复核的减值迹象记录 → warning（P2 启用数据，
      当前仅当有记录时出现）。
    """
    from db.models import IntangibleAmortizationSchedule, IntangibleAsset, IntangibleImpairmentTest

    pending_rows = (
        await session.execute(
            select(
                IntangibleAmortizationSchedule.asset_id,
                IntangibleAmortizationSchedule.period_start,
                IntangibleAmortizationSchedule.amount,
                IntangibleAsset.code,
                IntangibleAsset.name,
            )
            .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
            .where(
                IntangibleAmortizationSchedule.company_id == company_id,
                IntangibleAmortizationSchedule.period_start <= month_start.strftime("%Y-%m"),
                IntangibleAmortizationSchedule.status == "planned",
                IntangibleAsset.deleted_at.is_(None),
                IntangibleAsset.status.in_(("active", "idle")),
            )
            .order_by(IntangibleAmortizationSchedule.period_start.asc())
            .limit(50)
        )
    ).all()

    unreviewed_impairment = int(
        (
            await session.execute(
                select(func.count(IntangibleImpairmentTest.id)).where(
                    IntangibleImpairmentTest.company_id == company_id,
                    IntangibleImpairmentTest.conclusion == "impairment_noted",
                    IntangibleImpairmentTest.reviewed_by.is_(None),
                )
            )
        ).scalar_one()
    )

    checks: list[dict[str, Any]] = []
    if pending_rows:
        total_amount = money(sum((money(r.amount) for r in pending_rows), ZERO))
        checks.append(
            {
                "code": "INTANGIBLE_AMORTIZATION",
                "name": "无形资产摊销",
                "status": "blocking",
                "uiStatus": "blocked",
                "pendingCount": len(pending_rows),
                "totalAmount": money_text(total_amount),
                "suggestedAction": "run_amortization",
                "records": [
                    {
                        "assetId": r.asset_id,
                        "assetCode": r.code,
                        "assetName": r.name,
                        "periodStart": r.period_start,
                        "amount": money_text(money(r.amount)),
                    }
                    for r in pending_rows
                ],
            }
        )
    else:
        checks.append(
            {
                "code": "INTANGIBLE_AMORTIZATION",
                "name": "无形资产摊销",
                "status": "passed",
                "uiStatus": "complete",
                "pendingCount": 0,
            }
        )
    if unreviewed_impairment:
        checks.append(
            {
                "code": "IMPAIRMENT_TEST_DUE",
                "name": "无形资产减值迹象",
                "status": "needs_review",
                "uiStatus": "incomplete",
                "count": unreviewed_impairment,
                "suggestedAction": "review_impairment",
            }
        )
    return checks


async def _fixed_asset_close_checks(session: AsyncSession, company_id: str, month_start: Any) -> list[dict[str, Any]]:
    """固定资产月结检查项（P1 WP-B）。

    - FIXED_ASSET_DEPRECIATION：本期及以前存在未计提 planned 行 → blocking，
      suggestedAction=run_depreciation（与无形资产 INTANGIBLE_AMORTIZATION 同款）；
    - FIXED_ASSET_RECONCILIATION：卡片 vs 1601/1602 vs 折旧计划三方勾稽不平
      → blocking，差异明细挂在 records（仅公司有已入账卡片时适用）。
    """
    from db.models import FixedAsset, FixedAssetDepreciationSchedule
    from services.fixed_asset_reconciliation_service import (
        asset_reconciliation,
        reconciliation_summary,
    )

    pending_rows = (
        await session.execute(
            select(
                FixedAssetDepreciationSchedule.asset_id,
                FixedAssetDepreciationSchedule.period_start,
                FixedAssetDepreciationSchedule.amount,
                FixedAsset.code,
                FixedAsset.name,
            )
            .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
            .where(
                FixedAssetDepreciationSchedule.company_id == company_id,
                FixedAssetDepreciationSchedule.period_start <= month_start.strftime("%Y-%m"),
                FixedAssetDepreciationSchedule.status == "planned",
                FixedAsset.deleted_at.is_(None),
                FixedAsset.status.in_(("active", "idle")),
            )
            .order_by(FixedAssetDepreciationSchedule.period_start.asc())
            .limit(50)
        )
    ).all()

    checks: list[dict[str, Any]] = []
    if pending_rows:
        total_amount = money(sum((money(r.amount) for r in pending_rows), ZERO))
        checks.append(
            {
                "code": "FIXED_ASSET_DEPRECIATION",
                "name": "固定资产折旧",
                "status": "blocking",
                "uiStatus": "blocked",
                "pendingCount": len(pending_rows),
                "totalAmount": money_text(total_amount),
                "suggestedAction": "run_depreciation",
                "records": [
                    {
                        "assetId": r.asset_id,
                        "assetCode": r.code,
                        "assetName": r.name,
                        "periodStart": r.period_start,
                        "amount": money_text(money(r.amount)),
                    }
                    for r in pending_rows
                ],
            }
        )
    else:
        checks.append(
            {
                "code": "FIXED_ASSET_DEPRECIATION",
                "name": "固定资产折旧",
                "status": "passed",
                "uiStatus": "complete",
                "pendingCount": 0,
            }
        )

    reconciliation = reconciliation_summary(await asset_reconciliation(session, company_id))
    if reconciliation.get("applicable"):
        balanced = bool(reconciliation.get("balanced"))
        checks.append(
            {
                "code": "FIXED_ASSET_RECONCILIATION",
                "name": "固定资产核对总账",
                "status": "passed" if balanced else "blocking",
                "uiStatus": "complete" if balanced else "blocked",
                **{k: v for k, v in reconciliation.items() if k not in ("applicable", "balanced")},
                "suggestedAction": None if balanced else "review_fixed_assets",
            }
        )
    return checks


async def _salary_accrual_close_check(
    session: AsyncSession, company_id: str, month_start: date
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """工资按权责发生制必须有计提；发放延到次月不阻断结账。

    只在有在职员工的公司生效。工资表/员工表不存在时静默跳过，避免把
    未启用工资模块的租户挡在关账之外。
    """
    month = month_start.strftime("%Y-%m")
    try:
        active_count = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM employees "
                        "WHERE company_id = :company_id AND is_active = 1"
                    ),
                    {"company_id": company_id},
                )
            ).scalar_one()
        )
    except (OperationalError, ProgrammingError):
        return None, None
    if active_count <= 0:
        return None, None

    try:
        sheet = (
            await session.execute(
                text(
                    "SELECT ss.status, ss.accrual_voucher_id, "
                    "  (SELECT je.status FROM journal_entries je "
                    "   WHERE je.id = ss.accrual_voucher_id AND je.company_id = ss.company_id) AS accrual_status, "
                    "  (SELECT je.status FROM journal_entries je "
                    "   WHERE je.id = ss.payment_voucher_id AND je.company_id = ss.company_id) AS payment_status "
                    "FROM salary_sheets ss "
                    "WHERE ss.company_id = :company_id AND ss.month = :month"
                ),
                {"company_id": company_id, "month": month},
            )
        ).mappings().first()
    except (OperationalError, ProgrammingError):
        return None, None

    accrual_status = str(sheet["accrual_status"] or "") if sheet is not None else ""
    if sheet is None:
        status, ui_status = "blocking", "blocked"
        message = f"本月有 {active_count} 名在职员工，工资尚未计提"
    elif accrual_status == "pending_review":
        status, ui_status = "blocking", "blocked"
        message = "工资计提凭证待复核，过账后才能结账"
    elif accrual_status == "posted":
        status, ui_status = "passed", "complete"
        message = "工资已计提并发放" if str(sheet["payment_status"] or "") == "posted" else "工资已计提"
    else:
        status, ui_status = "blocking", "blocked"
        message = "工资表尚未完成计提"

    check = {
        "code": "PAYROLL_ACCRUAL",
        "name": "工资计提",
        "status": status,
        "uiStatus": ui_status,
        "employeeCount": active_count,
        "message": message,
        "suggestedAction": None if status == "passed" else "accrue_payroll",
    }

    notice = None
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT ss.month, COALESCE(SUM(sr.net_salary), 0) AS net_salary "
                    "FROM salary_sheets ss "
                    "LEFT JOIN salary_records sr ON sr.sheet_id = ss.id "
                    "LEFT JOIN journal_entries acc ON acc.id = ss.accrual_voucher_id "
                    "  AND acc.company_id = ss.company_id "
                    "LEFT JOIN journal_entries pay ON pay.id = ss.payment_voucher_id "
                    "  AND pay.company_id = ss.company_id "
                    "WHERE ss.company_id = :company_id "
                    "AND acc.status = 'posted' "
                    "AND COALESCE(pay.status, '') != 'posted' "
                    "AND ss.month <= :month "
                    "GROUP BY ss.month ORDER BY ss.month"
                ),
                {"company_id": company_id, "month": month},
            )
        ).mappings().all()
    except (OperationalError, ProgrammingError):
        rows = []
    if rows:
        total = sum((money(row["net_salary"]) for row in rows), ZERO)
        notice = {
            "code": "SALARY_PAYABLE_OUTSTANDING",
            "name": "已计提未发放工资",
            "severity": "warning",
            "count": len(rows),
            "amount": money_text(total),
            "records": [
                {
                    "businessPeriod": row["month"],
                    "amount": money_text(row["net_salary"]),
                    "description": f"{row['month']} 工资已计提，尚未确认发放",
                }
                for row in rows
            ],
            "suggestedAction": "salary",
            "message": (
                f"有 {len(rows)} 个月工资已计提但尚未发放，合计 {money_text(total)} 元。"
                "发放时可延至次月，凭银行流水核销应付职工薪酬，不影响本月结账。"
            ),
        }
    return check, notice


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
    # 账实核对子项：固定资产卡片 vs 1601/1602 三方勾稽（P1 WP-B；无卡片不适用）
    from services.fixed_asset_reconciliation_service import (
        asset_reconciliation,
        reconciliation_summary,
    )

    fixed_asset_recon = reconciliation_summary(await asset_reconciliation(session, company_id))
    fixed_asset_unbalanced = bool(fixed_asset_recon.get("applicable")) and not fixed_asset_recon.get("balanced", True)
    from services.opening_balance_service import get_opening_balances
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period
    report_entries = await posted_entries_for_reports(session, company_id)
    opening_items = await get_opening_balances(company_id, month_start.year, session=session)
    company = await session.get(Company, company_id)
    statement = build_statement_data_from_entries(report_entries, opening_items, parse_period(month_start.strftime("%Y-%m"), "month"), (company.payload or {}) if company else {})
    report_issues = [item for item in statement.checks[2:] if item["level"] == "error"]
    # Compare the actual exported statement's cash to authoritative account
    # balances, rather than comparing debit and credit totals a second time.
    ledger_cash = sum((money(row[2]) - money(row[3]) for row in account_rows if row[0] in {"1001", "1002", "1012001", "1012"}), ZERO)
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
    salary_check, salary_notice = await _salary_accrual_close_check(
        session, company_id, month_start
    )
    # 关账顺序与 _close_period_in_transaction 的守卫同口径：更早的月份还有业务
    # 或仍未关账时本期不能结。这里让体检清单提前暴露它，否则界面显示“可以结账”，
    # 用户点下去才吃到 PERIOD_OUT_OF_ORDER。
    earlier_open, earliest_open_month = await preceding_open_months(
        session, company_id, month_start
    )
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
            "status": "blocking" if unreconciled_bank or bank_validation["issueCount"] or fixed_asset_unbalanced else "passed",
            "uiStatus": "blocked" if unreconciled_bank or bank_validation["issueCount"] or fixed_asset_unbalanced else "complete",
            "unreconciledBankCount": unreconciled_bank,
            **bank_validation,
            "fixedAsset": fixed_asset_recon,
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
        *await _intangible_close_checks(session, company_id, month_start),
        *await _fixed_asset_close_checks(session, company_id, month_start),
        *([salary_check] if salary_check else []),
        {
            "code": "PERIOD_ORDER",
            "name": "关账顺序",
            "status": "blocking" if earlier_open else "passed",
            "uiStatus": "blocked" if earlier_open else "complete",
            "earlierOpenCount": earlier_open,
            "earliestOpenMonth": earliest_open_month,
            "message": (
                f"存在 {earlier_open} 个更早的未关账月份，请从最早的月份开始按顺序关账"
                if earlier_open else "更早的月份均已关账"
            ),
        },
    ]
    # Other checks describe posted books only. Drafts must not produce a green
    # "all verified" result for business which has not entered those books yet.
    if pending_count:
        for check in checks[1:]:
            if check["code"] == "PERIOD_ORDER":
                continue
            check["blockedByPendingCount"] = pending_count
            if check["status"] == "passed":
                check["status"] = "needs_review"
                check["uiStatus"] = "incomplete"

    # 提示项（不阻断）：五项核对回答"账做平了没有"，提示项回答"有没有业务被漏在
    # 别的月份"。两件事分开，避免把「跨期未处理」混进核对类别的语义里。
    from services.backfill_service import backfills_in_period, cross_period_pending

    # 启用日期是记账起点：下限之前的流水/工资表既不算「跨期未处理」（补记不进去，
    # 业务日期会被 assert_book_started 拦），也不该出现在期间列表里。单独作为一条
    # 提示报出来，用户才知道该去改启用日期还是走期初导账。
    book_start = await book_start_month(session, company_id)
    pre_book = await pre_book_start_activity(session, company_id, start=book_start)

    pending_sources = await cross_period_pending(
        session, company_id, month_start, start_from=book_start
    )
    backfilled = await backfills_in_period(session, company_id, month_start)
    notices: list[dict[str, Any]] = []
    if salary_notice:
        notices.append(salary_notice)
    if pre_book["count"]:
        first_month, last_month = pre_book["months"][0], pre_book["months"][-1]
        span = first_month if first_month == last_month else f"{first_month}~{last_month}"
        parts = [
            f"{pre_book['bankCount']} 条银行流水" if pre_book["bankCount"] else "",
            f"{pre_book['salaryCount']} 份工资表" if pre_book["salaryCount"] else "",
        ]
        notices.append(
            {
                "code": "PRE_BOOK_START_ACTIVITY",
                "name": "启用日期之前的数据",
                "severity": "warning",
                "count": pre_book["count"],
                "bookStartDate": pre_book["startDate"],
                "months": pre_book["months"],
                "bankCount": pre_book["bankCount"],
                "salaryCount": pre_book["salaryCount"],
                "message": (
                    f"启用日期 {pre_book['startDate']} 之前还有 {span} 的 "
                    f"{'、'.join(p for p in parts if p)}。这些业务不在本账套的记账范围，"
                    "不参与结账，也不占关账顺序。若它们本该入账，请到「我的 → 主体管理」"
                    "把启用日期前移；若属于建账前历史，请走「期初设置」录入期初余额。"
                ),
            }
        )
    if pending_sources["count"]:
        notices.append(
            {
                "code": "CROSS_PERIOD_PENDING",
                "name": "跨期未处理单据",
                "severity": "warning",
                "count": pending_sources["count"],
                "amount": pending_sources["amount"],
                "records": pending_sources["records"],
                "suggestedAction": "backfill",
                "message": (
                    f"有 {pending_sources['count']} 笔早于本期、仍未入账的单据。"
                    "可以在录入时补记到本期，也可以确认这些单据不再入账。"
                ),
            }
        )
    if backfilled["count"]:
        notices.append(
            {
                "code": "BACKFILL_IN_PERIOD",
                "name": "本期补记",
                "severity": "warning",
                "count": backfilled["count"],
                "amount": backfilled["amount"],
                "records": backfilled["records"],
                "message": (
                    f"本期有 {backfilled['count']} 笔凭证补记自以前月份，"
                    "期末结转会把它们计入本期损益。"
                ),
            }
        )

    # 补记不只是损益错位，还会把增值税带到后一期申报。小规模没有进项抵扣，
    # 所以要盯的是销项的纳税义务所属期（跨季时才真的改变申报结果）。
    from services.backfill_service import vat_period_mismatch

    vat_mismatch = await vat_period_mismatch(session, company_id, posting_period=month_start)
    if vat_mismatch["count"]:
        cycle_label = "月报" if vat_mismatch["filingCycle"] == "month" else "季报"
        notices.append(
            {
                "code": "VAT_PERIOD_MISMATCH",
                "name": "增值税申报所属期差异",
                "severity": "warning",
                "count": vat_mismatch["count"],
                "amount": vat_mismatch["amount"],
                "records": vat_mismatch["records"],
                "filingCycle": vat_mismatch["filingCycle"],
                "suggestedAction": "vat_period_export",
                "message": (
                    f"本期有 {vat_mismatch['count']} 笔补记凭证含增值税，"
                    f"纳税义务属于业务原期间。按{cycle_label}申报时，"
                    "这些销售额应计入原所属期，否则账表会不一致。"
                ),
            }
        )

    from services.accrual_service import list_pending_accruals

    stale_accruals = await list_pending_accruals(
        session, company_id, before_period=month_start, limit=50
    )
    if stale_accruals["count"]:
        notices.append(
            {
                "code": "ACCRUAL_PENDING_REVERSAL",
                "name": "未冲回暂估",
                "severity": "warning",
                "count": stale_accruals["count"],
                "amount": stale_accruals["amount"],
                "records": stale_accruals["records"],
                "suggestedAction": "reverse_accrual",
                "message": (
                    f"上期及以前有 {stale_accruals['count']} 笔暂估尚未冲回。"
                    "暂估应次月初红冲、票到后按发票入账，长期挂账会让资产与负债同时虚增。"
                ),
            }
        )
    period_accruals = await list_pending_accruals(
        session, company_id, period=month_start, limit=50
    )
    if period_accruals["count"]:
        notices.append(
            {
                "code": "ACCRUAL_IN_PERIOD",
                "name": "本期暂估",
                "severity": "warning",
                "count": period_accruals["count"],
                "amount": period_accruals["amount"],
                "records": period_accruals["records"],
                "message": (
                    f"本期有 {period_accruals['count']} 笔暂估入账（票未到）。"
                    "下月初需要红冲，取得发票后在「票到冲抵」里处理。"
                ),
            }
        )
    vat_base = await _calculate_vat_exemption_base(session, company_id, month_start)
    if vat_base and vat_base["exemptable_vat"] > ZERO:
        cycle_name = "本季度" if vat_base["config"].filing_cycle == "quarter" else "本月"
        if vat_base["is_eligible"]:
            notices.append(
                {
                    "code": "VAT_EXEMPTION_PREVIEW",
                    "name": "增值税小微免税结转预告",
                    "severity": "info",
                    "count": 1,
                    "amount": str(vat_base["exemptable_vat"]),
                    "message": (
                        f"{cycle_name}累计销售额 ¥{vat_base['revenue_net']}（未超 ¥{vat_base['threshold']} 免税限额），"
                        f"确认结账时系统将自动生成免税凭证，减免增值税 ¥{vat_base['exemptable_vat']} 并转入营业外收入。"
                    ),
                }
            )
    return {
        "period": month_start.isoformat()[:7],
        "periodStatus": period.status if period else "open",
        "canClose": all(check["status"] == "passed" for check in checks),
        "blockingCount": sum(check["status"] == "blocking" for check in checks),
        "checks": checks,
        "notices": notices,
        "noticeCount": len(notices),
        "snapshot": snapshot,
    }


async def _generate_vat_exemption_transfer(
    session: AsyncSession,
    company_id: str,
    period_start: date,
    *,
    period_version: int,
    user_id: str,
    request_id: str,
) -> list[JournalEntry]:
    """小规模纳税人免征增值税转出：借 应交增值税 / 贷 营业外收入。

    销售收入入账时已按征收率计提应交增值税；按期销售额未达起征点时该税额免征，
    必须转入损益，否则负债永久挂账、利润被低估。
    开票为专用发票的部分不适用免税（受票方已抵扣），单独剔除。

    entry_type 用 closing 以便过账时绕过 maker-checker 并随反结账自动冲销，
    但不打 isClosingTransfer 标记——它不是损益结转，必须进利润表。
    """
    from services.vat_service import (
        VAT_EXEMPT_INCOME_ACCOUNT,
        VAT_EXEMPT_INCOME_ACCOUNT_NAME,
        VAT_PAYABLE_ACCOUNT,
    )

    base = await _calculate_vat_exemption_base(session, company_id, period_start)
    if not base or not base["is_eligible"]:
        return []

    config = base["config"]
    range_start = base["range_start"]
    month_end = base["month_end"]
    exemptable_vat = base["exemptable_vat"]

    if config.filing_cycle == "quarter":
        label = f"{range_start.year}Q{(range_start.month - 1) // 3 + 1}"
        title = f"{range_start.year} 年第 {(range_start.month - 1) // 3 + 1} 季度小规模纳税人免征增值税转出"
    else:
        label = range_start.strftime("%Y-%m")
        title = f"{range_start.year} 年 {range_start.month} 月小规模纳税人免征增值税转出"

    entry = await create_entry(
        session,
        {
            "id": f"closing-vat-exempt-{company_id}-{label}-v{period_version}",
            "direction": "owner_transfer",
            "amount": exemptable_vat,
            "title": title,
            "category": "免征增值税转入",
            "occurDate": month_end.isoformat(),
            "sourceType": "closing_vat_exemption",
            "dedupFingerprint": f"closing_vat_exempt:{company_id}:{label}:v{period_version}",
            "entryType": "closing",
            "ruleVersion": "vat-exemption-v1",
            "voucherAuthority": "system",
            "isClosingTransfer": False,
            "isVatExemptionTransfer": True,
            "voucherLines": {
                "debit": [{
                    "accountCode": VAT_PAYABLE_ACCOUNT,
                    "accountName": "应交增值税（小规模纳税人专用）",
                    "amount": exemptable_vat,
                }],
                "credit": [{
                    "accountCode": VAT_EXEMPT_INCOME_ACCOUNT,
                    "accountName": VAT_EXEMPT_INCOME_ACCOUNT_NAME,
                    "amount": exemptable_vat,
                }],
            },
        },
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:closing-vat-exempt",
        confirm=True,
    )
    return [entry]


async def _calculate_vat_exemption_base(
    session: AsyncSession,
    company_id: str,
    period_start: date,
) -> dict[str, Any] | None:
    """计算当期（季度末或月末）小规模免征增值税测算基数。"""
    from services.vat_service import (
        VAT_PAYABLE_ACCOUNT,
        get_vat_config,
    )

    company = (
        await session.execute(select(Company).where(Company.company_id == company_id))
    ).scalar_one_or_none()
    if company is None:
        return None
    try:
        config = get_vat_config(company.payload or {})
    except ValueError:
        return None

    if config.filing_cycle == "quarter":
        if period_start.month % 3 != 0:
            return None
        range_start = date(period_start.year, period_start.month - 2, 1)
    else:
        range_start = period_start
    month_end = date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )

    entries = await posted_entries_for_reports(session, company_id)
    revenue_net = ZERO
    exemptable_vat = ZERO
    for entry in entries:
        if entry.get("entryType") == "opening" or entry.get("isClosingTransfer"):
            continue
        # 成对跳过红字冲销：已冲销原凭证（voucherStatus=reversed）与其冲销凭证
        # （isReversal）净额为零。
        if entry.get("voucherStatus") == "reversed" or entry.get("isReversal"):
            continue
        try:
            occurred = _parse_date(entry.get("occurDate"))
        except AccountingError:
            continue
        if occurred < range_start or occurred > month_end:
            continue
        voucher = entry.get("voucherLines") or {}
        revenue_credit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("credit") or []
             if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNT_CODES),
            ZERO,
        )
        revenue_debit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("debit") or []
             if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNT_CODES),
            ZERO,
        )
        revenue_net += revenue_credit - revenue_debit
        vat_credit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("credit") or []
             if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
            ZERO,
        )
        vat_debit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("debit") or []
             if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
            ZERO,
        )
        entry_vat = money(vat_credit - vat_debit)
        if entry_vat == ZERO:
            continue
        invoice_type = str(entry.get("invoiceType") or "")
        if "专用发票" in invoice_type or "专票" in invoice_type:
            # 专票不得免税，其税额留在应交税费等待申报缴纳
            continue
        exemptable_vat += entry_vat

    revenue_net = money(revenue_net)
    exemptable_vat = money(exemptable_vat)
    threshold = config.threshold_for(config.filing_cycle)
    is_eligible = (revenue_net > ZERO and revenue_net < threshold and exemptable_vat > ZERO)

    return {
        "config": config,
        "range_start": range_start,
        "month_end": month_end,
        "revenue_net": revenue_net,
        "exemptable_vat": exemptable_vat,
        "threshold": threshold,
        "is_eligible": is_eligible,
    }


async def _generate_closing_entries(
    session: AsyncSession,
    company_id: str,
    period_start: date,
    *,
    period_version: int,
    user_id: str,
    request_id: str,
) -> list[JournalEntry]:
    """自动生成并过账期末损益结转凭证（月末结转 5xxx 至 3103 本年利润；12 月末结转 3103 至 3104006 未分配利润）。"""
    month_end = date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )
    period_label = period_start.strftime("%Y-%m")
    created_entries: list[JournalEntry] = []

    # 1. 汇总当期已过账/冲销的损益类科目净发生额
    # 口径由 account_classification 统一：5xxx + 研发费用化 4301001*，
    # 排除研发资本化 4301002（资产性质，不结转本年利润）。
    pnl_rows = (
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
                JournalEntry.period_start == period_start,
                JournalEntry.status.in_(("posted", "reversed")),
                JournalLine.company_id == company_id,
            )
            .group_by(JournalLine.account_code, JournalLine.account_name)
            .order_by(JournalLine.account_code)
        )
    ).all()

    closing_debits: list[dict[str, Any]] = []
    closing_credits: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO

    for code, name, raw_debit, raw_credit in pnl_rows:
        if not is_profit_loss_code(code):
            continue
        net_debit = money(raw_debit) - money(raw_credit)
        if net_debit < ZERO:
            # 贷方净额（如收入类 5001、利息收入 5603005）：借记该科目予以清零
            amt = abs(net_debit)
            closing_debits.append({"accountCode": code, "accountName": name, "amount": amt})
            total_debit += amt
        elif net_debit > ZERO:
            # 借方净额（如成本费用类 5401、5601、5602、5603、5711、5801）：贷记该科目予以清零
            closing_credits.append({"accountCode": code, "accountName": name, "amount": net_debit})
            total_credit += net_debit

    if closing_debits or closing_credits:
        profit_diff = total_debit - total_credit
        if profit_diff > ZERO:
            # 本期实现净利润：贷记 3103 本年利润
            closing_credits.append({"accountCode": "3103", "accountName": "本年利润", "amount": profit_diff})
        elif profit_diff < ZERO:
            # 本期发生净亏损：借记 3103 本年利润
            closing_debits.append({"accountCode": "3103", "accountName": "本年利润", "amount": abs(profit_diff)})

        voucher_total = max(total_debit, total_credit)
        pnl_entry = await create_entry(
            session,
            {
                "id": f"closing-pnl-{company_id}-{period_label}-v{period_version}",
                "direction": "owner_transfer",
                "amount": voucher_total,
                "title": f"{period_start.year} 年 {period_start.month} 月期末损益结转",
                "category": "期末损益结转",
                "occurDate": month_end.isoformat(),
                "sourceType": "closing_transfer",
                "dedupFingerprint": f"closing_pnl:{company_id}:{period_label}:v{period_version}",
                "entryType": "closing",
                "ruleVersion": "closing-transfer-v1",
                "voucherAuthority": "system",
                "isClosingTransfer": True,
                "voucherLines": {"debit": closing_debits, "credit": closing_credits},
            },
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:closing-pnl",
            confirm=True,
        )
        created_entries.append(pnl_entry)

    # 2. 年末（12月）将 3103 本年利润全年累计余额结转至 3104006 未分配利润
    if period_start.month == 12:
        profit_totals = (
            await session.execute(
                select(
                    func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                    func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
                )
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.occurred_on <= month_end,
                    JournalEntry.status.in_(("posted", "reversed")),
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == "3103",
                )
            )
        ).one()
        annual_net_credit = money(profit_totals[1]) - money(profit_totals[0])
        if annual_net_credit != ZERO:
            retained_acc = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.is_active.is_(True),
                        ChartAccount.is_leaf.is_(True),
                        ChartAccount.code.in_(("3104006", "3104")),
                    ).order_by(ChartAccount.code.desc())
                )
            ).scalars().first()
            ret_code = retained_acc.code if retained_acc else "3104006"
            ret_name = retained_acc.name if retained_acc else "未分配利润"
            amt = abs(annual_net_credit)
            if annual_net_credit > ZERO:
                ye_debits = [{"accountCode": "3103", "accountName": "本年利润", "amount": amt}]
                ye_credits = [{"accountCode": ret_code, "accountName": ret_name, "amount": amt}]
            else:
                ye_debits = [{"accountCode": ret_code, "accountName": ret_name, "amount": amt}]
                ye_credits = [{"accountCode": "3103", "accountName": "本年利润", "amount": amt}]

            ye_entry = await create_entry(
                session,
                {
                    "id": f"closing-ye-{company_id}-{period_start.year}-v{period_version}",
                    "direction": "owner_transfer",
                    "amount": amt,
                    "title": f"{period_start.year} 年末本年利润结转未分配利润",
                    "category": "年末利润分配结转",
                    "occurDate": month_end.isoformat(),
                    "sourceType": "closing_year_end",
                    "dedupFingerprint": f"closing_ye:{company_id}:{period_start.year}:v{period_version}",
                    "entryType": "closing",
                    "ruleVersion": "closing-transfer-v1",
                    "voucherAuthority": "system",
                    "isClosingTransfer": True,
                    "voucherLines": {"debit": ye_debits, "credit": ye_credits},
                },
                company_id=company_id,
                user_id=user_id,
                request_id=f"{request_id}:closing-ye",
                confirm=True,
            )
            created_entries.append(ye_entry)

    return created_entries


async def _close_period_in_transaction(
    session: AsyncSession,
    company_id: str,
    month: Any,
    *,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, POSTING_ROLES)
    # 启用日期是记账起点：启用前的月份根本不属于本账套的期间。拦住它既避免
    # 结出一笔空账，也避免 get_or_create_period 先落一条脏账期行（前端选择器
    # 已不再给出这些月份，这里挡的是 URL 直达与老客户端）。
    book_start = await book_start_month(session, company_id)
    requested_month = period_start_for(month)
    if book_start is not None and requested_month < book_start:
        raise AccountingError(
            PERIOD_BEFORE_BOOK_START,
            f"{requested_month.strftime('%Y-%m')} 早于账套启用月份 {book_start.strftime('%Y-%m')}，"
            "不在本账套的记账范围。启用前的业务请通过「我的 → 期初设置」录入期初余额",
            422,
            {
                "period": requested_month.strftime("%Y-%m"),
                "bookStartMonth": book_start.strftime("%Y-%m"),
            },
        )
    period = await get_or_create_period(session, company_id, month, lock=True)
    if period.status == "closed":
        return await period_close_checks(session, company_id, month)

    # 顺序关账：存在更早的未关账月份时禁止关本期。
    # 否则"先结 3 月再结 1 月"会绕过 1 月的核对，且损益结转的口径会错序。
    # 口径与体检清单一致：有流水/工资表但一张凭证都没有的月份也算未关账。
    earlier_open, _earliest_open_month = await preceding_open_months(
        session, company_id, period.period_start
    )
    if earlier_open:
        raise AccountingError(
            "PERIOD_OUT_OF_ORDER",
            f"存在 {earlier_open} 个更早的未关账月份，请从最早的月份开始按顺序关账",
            409,
            {"period": period.period_start.isoformat()[:7], "earlierOpenCount": int(earlier_open)},
        )

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

    pre_check = await period_close_checks(session, company_id, month)
    if not pre_check["canClose"]:
        snapshot_json = json.dumps(pre_check["snapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        session.add(
            PeriodCloseRun(
                company_id=company_id,
                period_start=period.period_start,
                status="blocked",
                checks=pre_check["checks"],
                snapshot=pre_check["snapshot"],
                snapshot_hash=digest,
                rule_versions={"accountMapping": "account-mapping-v2"},
                ledger_watermark=int((await session.execute(select(func.max(AuditEvent.sequence_no)))).scalar_one_or_none() or 0),
                requested_by=user_id,
            )
        )
        period.status = "open"
        await session.flush()
        raise AccountingError("CLOSE_CHECK_FAILED", "存在阻断项，不能关账", 409, {"checks": pre_check["checks"]})

    # 五项核验通过后，先处理免征增值税转出（必须早于损益结转），
    # 再全自动生成并过账期末损益结转凭证（含 12 月末未分配利润结转）
    vat_exempt_entries = await _generate_vat_exemption_transfer(
        session,
        company_id,
        period.period_start,
        period_version=period.version,
        user_id=user_id,
        request_id=request_id,
    )
    closing_entries = vat_exempt_entries + await _generate_closing_entries(
        session,
        company_id,
        period.period_start,
        period_version=period.version,
        user_id=user_id,
        request_id=request_id,
    )

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
            rule_versions={"accountMapping": "account-mapping-v2", "closingTransfer": "closing-transfer-v1"},
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
               "autoPostedVoucherRefs": [entry.voucher_no for entry in posted_at_close],
               "closingEntryIds": [entry.id for entry in closing_entries],
               "closingVoucherRefs": [entry.voucher_no for entry in closing_entries]},
        entity_version=period.version,
    )
    result["status"] = "closed"
    result["snapshotHash"] = digest
    result["autoPostedCount"] = len(posted_at_close)
    result["autoPostedVoucherRefs"] = [entry.voucher_no for entry in posted_at_close]
    result["closingCount"] = len(closing_entries)
    result["closingEntryIds"] = [entry.id for entry in closing_entries]
    result["closingVoucherRefs"] = [entry.voucher_no for entry in closing_entries]
    result["vatExemptCount"] = len(vat_exempt_entries)
    result["vatExemptAmount"] = str(sum((money(entry.metadata_json.get("amount") or ZERO) for entry in vat_exempt_entries), ZERO))
    result["vatExemptVoucherRefs"] = [entry.voucher_no for entry in vat_exempt_entries]
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
    # 反结账级联保护：后续月份已关账时，冲销本期结转会破坏其期末余额口径
    later_closed = (
        await session.execute(
            select(func.count()).select_from(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start > period.period_start,
                AccountingPeriod.status.in_(("closed", "closing")),
            )
        )
    ).scalar_one()
    if later_closed:
        raise AccountingError(
            "LATER_PERIOD_CLOSED",
            f"后续 {later_closed} 个月份已关账，请先反结账最新的月份",
            409,
            {"period": period.period_start.isoformat()[:7], "laterClosedCount": int(later_closed)},
        )
    before = {"period": period.period_start.isoformat()[:7], "status": period.status}
    period.status = "reopened"
    period.reopened_by = user_id
    period.reopened_at = _utcnow()
    period.version += 1
    await session.flush()

    # 反结账时自动红字冲销当期已过账的期末损益结转凭证
    closing_entries = (
        await session.execute(
            select(JournalEntry)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.period_start == period.period_start,
                JournalEntry.entry_type == "closing",
                JournalEntry.status == "posted",
            )
            .order_by(JournalEntry.occurred_on.desc(), JournalEntry.id.desc())
            .with_for_update()
        )
    ).scalars().all()
    reversed_closing: list[JournalEntry] = []
    for c_entry in closing_entries:
        rev = await reverse_journal(
            session,
            c_entry.id,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:reopen-rev:{c_entry.id}",
            reason=f"反结账自动冲销期末结转：{reason.strip()}",
        )
        reversed_closing.append(rev)

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
        after={
            "period": period.period_start.isoformat()[:7],
            "status": "reopened",
            "reversedClosingEntryIds": [r.id for r in reversed_closing],
            "reversedClosingVoucherRefs": [r.voucher_no for r in reversed_closing],
        },
        reason=reason.strip(),
        entity_version=period.version,
    )
    return {
        "period": period.period_start.isoformat()[:7],
        "status": "reopened",
        "reversedClosingEntryIds": [r.id for r in reversed_closing],
        "reversedClosingVoucherRefs": [r.voucher_no for r in reversed_closing],
    }


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
