# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""上手引导 · 账套就绪度（只读聚合）。

与 ``period_close_checks`` 同族但不同粒度：那个回答「这个月能不能关账」，
这个回答「这个账套能不能开始用」。真值必须全部从既有业务数据推导——
引导的完成状态不能靠用户勾选，否则进度是假的。

两条硬约束：

1. **不要在这里调用 ``ensure_tenant``**。它内部会 ``seed_accounts``，既让 GET
   产生写副作用，又会让「科目表」永远自证完成（用户什么都没做就显示已完成）。
2. 就绪度只服务于「新手期」判断，不承担结账校验。需要账证/账账/账实勾稽时
   去调 ``period_close_checks``，那套要跑银行核对与整表构建，这里用不起。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import FORBIDDEN, NOT_FOUND, AccountingError
from core.money import ZERO, money
from db.models import (
    ChartAccount,
    Company,
    CompanyMember,
    JournalEntry,
    JournalLine,
    LegacyImportRecord,
    TaxRegistration,
)
from services.accounting_core_service import book_start_date
from services.opening_balance_service import check_balanced

ReadinessStatus = Literal["todo", "doing", "done", "waived", "locked"]
ReadinessView = Literal["boss", "accounting"]

POSTED_STATUSES = ("posted", "reversed")
# 期初凭证与结转凭证不算「记账」——否则刚录完期初就被判定为已开始记账
NON_BUSINESS_ENTRY_TYPES = ("opening", "closing")

# 每个视图的步骤顺序。同一份真值，两套表达（老板看「能不能报税」，会计看「能不能出报表」）。
VIEW_STEPS: dict[str, tuple[str, ...]] = {
    "boss": (
        "company_profile",
        "tax_registration",
        "opening_balance",
        "first_voucher",
        "report_view",
    ),
    "accounting": (
        "company_profile",
        "chart_accounts",
        "opening_balance",
        "legacy_import",
        "tax_registration",
        "first_voucher",
    ),
}

_STEP_TEXT: dict[str, dict[str, tuple[str, str]]] = {
    "company_profile": {
        "boss": ("建好账套", "告诉系统你是什么主体、从哪一天开始记账"),
        "accounting": ("账套与纳税人身份", "主体类型决定税种物化，纳税人身份决定申报表"),
    },
    "tax_registration": {
        "boss": ("确认要交哪些税", "照着税局的认定表抄一遍，征期提醒才准"),
        "accounting": ("税费种认定", "决定申报表归属与征期，未认定时按默认季报推算"),
    },
    "opening_balance": {
        "boss": ("录入期初余额", "开始记账之前，公司手里已有的钱和欠的款"),
        "accounting": ("期初试算平衡", "启用期间的科目期初，借贷相等后才能出报表"),
    },
    "legacy_import": {
        "boss": ("旧账导入", "把手工账搬进系统"),
        "accounting": ("旧账导入（可选）", "年中建账时导入历史凭证，与期初二选一"),
    },
    "first_voucher": {
        "boss": ("记第一笔账", "一句话、一张发票或一份流水都可以"),
        "accounting": ("首张凭证过账", "走完 draft → posted，账簿和报表才有数"),
    },
    "report_view": {
        "boss": ("看一眼经营情况", "收入、支出、这个月赚了多少"),
        "accounting": ("查看经营报表", "确认报表口径与账簿一致"),
    },
    "chart_accounts": {
        "boss": ("科目表", "系统已按小企业会计准则预置"),
        "accounting": ("科目表", "系统已按小企业会计准则预置，可按需增补明细"),
    },
}

DEEP_LINKS: dict[str, dict[str, str]] = {
    "company_profile": {"boss": "/mine/companies", "accounting": "/mine/companies"},
    "tax_registration": {"boss": "/tax-registration", "accounting": "/tax-registration"},
    "opening_balance": {"boss": "/mine/opening", "accounting": "/mine/opening"},
    "legacy_import": {"boss": "/mine/legacy-import", "accounting": "/mine/legacy-import"},
    "first_voucher": {"boss": "/copilot", "accounting": "/vouchers"},
    "report_view": {"boss": "/reports", "accounting": "/reports"},
    "chart_accounts": {"boss": "/mine/accounts", "accounting": "/mine/accounts"},
}

_PROFILE_FIELDS: dict[str, str] = {
    "name": "主体名称",
    "startDate": "启用日期",
    "taxId": "统一社会信用代码",
    "type": "主体类型",
    "taxpayerType": "纳税人身份",
}


def _step(
    key: str,
    view: str,
    status: ReadinessStatus,
    *,
    detail: str | None = None,
    blocked_by: str | None = None,
    progress: dict[str, Any] | None = None,
    informational: bool = False,
) -> dict[str, Any]:
    title, hint = _STEP_TEXT[key][view]
    return {
        "key": key,
        "title": title,
        "hint": hint,
        "status": status,
        "blockedBy": blocked_by,
        "detail": detail,
        "deepLink": DEEP_LINKS[key][view],
        "informational": informational,
        "progress": progress,
    }


def _opening_entry_id(company_id: str, fiscal_year: int) -> str:
    """与 ``opening_balance_service._entry_id`` 保持一致。

    必须按 id 查而不是按 ``period_start``：年中建账时期初凭证的 period_start
    是启用月份（如 2026-06），不是 1-1，按期间查会漏。
    """
    return f"opening-{company_id}-{fiscal_year}"


def resolve_fiscal_year(company: Company, today: date | None = None) -> int:
    """账套当前会计年度。

    跨年老账套看今年；启用日期在未来的账套看启用年（那时才有期初与业务）。
    与期初凭证 id 的年度约定一致，否则期初录完了却判不到。
    """
    today = today or date.today()
    start = book_start_date(company)
    return max(today.year, start.year) if start else today.year


def _profile_result(company: Company, view: str) -> tuple[ReadinessStatus, str | None]:
    payload = company.payload or {}
    required = ["name", "startDate", "taxId"]
    if view == "accounting":
        required += ["type", "taxpayerType"]
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if not missing:
        return "done", None
    if len(missing) == len(required):
        return "todo", None
    names = "、".join(_PROFILE_FIELDS[key] for key in missing)
    return "doing", f"还差 {len(missing)} 项：{names}"


async def _tax_result(
    session: AsyncSession, company_id: str, view: str
) -> tuple[ReadinessStatus, str | None]:
    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(TaxRegistration)
                .where(
                    TaxRegistration.company_id == company_id,
                    TaxRegistration.status == "active",
                )
            )
        ).scalar_one()
        or 0
    )
    if count:
        return "done", f"已认定 {count} 个税费种"
    if view == "boss":
        return "todo", "未认定时按默认季报推算征期，可能与你实际核定不一致"
    return "todo", None


async def _chart_result(
    session: AsyncSession, company_id: str
) -> tuple[ReadinessStatus, str | None]:
    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ChartAccount)
                .where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.is_active.is_(True),
                )
            )
        ).scalar_one()
        or 0
    )
    if count:
        return "done", f"系统已预置 {count} 个科目"
    return "todo", "首次进入业务页时自动预置"


async def _opening_result(
    session: AsyncSession, company: Company, fiscal_year: int
) -> tuple[ReadinessStatus, str | None, bool]:
    """期初余额六分支。返回 (status, detail, derived)。

    顺序不能调：显式凭证优先，其次旧账导入，再次上年结转，最后才是「从零起账」。
    """
    company_id = company.company_id
    entry = await session.get(JournalEntry, _opening_entry_id(company_id, fiscal_year))
    if entry is not None and entry.company_id == company_id:
        if entry.status == "posted":
            # 必须复用 check_balanced：它排除损益/成本科目，自己 sum 会误报不平
            balance = await check_balanced(company_id, fiscal_year, session=session)
            if balance["balanced"] and balance["total_debit"] != "0.00":
                return "done", f"已录入 {balance['count']} 个科目的期初", False
            return "doing", "期初已录入，但借贷不平", False
        if entry.status == "reversed":
            return "todo", "期初已冲销，请重新录入", False
        return "doing", "期初草稿还没确认", False

    imported = await _legacy_imported_count(session, company_id)
    if imported:
        return "done", f"已通过旧账导入带入（{imported} 条历史凭证）", False

    first_day = date(fiscal_year, 1, 1)
    has_prior = bool(
        await session.scalar(
            select(
                select(JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.occurred_on < first_day,
                    JournalEntry.status.in_(POSTED_STATUSES),
                )
                .exists()
            )
        )
    )
    if has_prior:
        return "done", "已按上年余额自动结转", True

    start = book_start_date(company)
    if start is not None and start >= first_day:
        return "waived", "从零起账，没有历史余额", False

    return "todo", None, False


async def _legacy_imported_count(session: AsyncSession, company_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(LegacyImportRecord)
                .where(
                    LegacyImportRecord.company_id == company_id,
                    LegacyImportRecord.status == "imported",
                )
            )
        ).scalar_one()
        or 0
    )


async def _legacy_result(
    session: AsyncSession, company_id: str, opening_status: ReadinessStatus
) -> tuple[ReadinessStatus, str | None]:
    imported = await _legacy_imported_count(session, company_id)
    if imported:
        return "done", f"已导入 {imported} 条历史凭证"
    quarantined = int(
        (
            await session.execute(
                select(func.count())
                .select_from(LegacyImportRecord)
                .where(
                    LegacyImportRecord.company_id == company_id,
                    LegacyImportRecord.status == "quarantined",
                )
            )
        ).scalar_one()
        or 0
    )
    if quarantined:
        return "doing", f"有 {quarantined} 条待处理"
    if opening_status in ("done", "waived"):
        # 期初要么已录完、要么本就不适用（从零起账），旧账导入都没有意义
        return "waived", "期初已处理，无需导入旧账"
    return "todo", None


async def _voucher_result(
    session: AsyncSession, company_id: str
) -> tuple[ReadinessStatus, str | None, dict[str, Any] | None]:
    posted = int(
        (
            await session.execute(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.entry_type.not_in(NON_BUSINESS_ENTRY_TYPES),
                )
            )
        ).scalar_one()
        or 0
    )
    if posted:
        return "done", f"已过账 {posted} 张凭证", {"current": posted, "target": 1, "unit": "张"}
    drafts = int(
        (
            await session.execute(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(("draft", "pending_review")),
                    JournalEntry.entry_type.not_in(NON_BUSINESS_ENTRY_TYPES),
                )
            )
        ).scalar_one()
        or 0
    )
    if drafts:
        return "doing", f"有 {drafts} 张凭证待过账", {"current": 0, "target": 1, "unit": "张"}
    return "todo", None, {"current": 0, "target": 1, "unit": "张"}


async def _report_result(
    session: AsyncSession, company_id: str, fiscal_year: int
) -> tuple[ReadinessStatus, str | None]:
    total = await session.scalar(
        select(func.coalesce(func.sum(JournalLine.amount), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.company_id == company_id,
            JournalEntry.company_id == company_id,
            JournalEntry.status.in_(POSTED_STATUSES),
            JournalEntry.entry_type == "standard",
            JournalEntry.occurred_on >= date(fiscal_year, 1, 1),
            JournalEntry.occurred_on < date(fiscal_year + 1, 1, 1),
        )
    )
    if money(total or 0) > ZERO:
        return "done", f"{fiscal_year} 年已有发生额"
    return "todo", "报表数据来自已过账的凭证"


async def _load_company(session: AsyncSession, company_id: str, user_id: str) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise AccountingError(NOT_FOUND, "账套不存在", 404)
    if company.owner_user_id != user_id:
        member = await session.get(
            CompanyMember, {"company_id": company_id, "user_id": user_id}
        )
        if member is None or member.status != "active":
            raise AccountingError(FORBIDDEN, "无权访问该账套", 403)
    return company


async def compute_readiness(
    session: AsyncSession,
    company_id: str,
    *,
    user_id: str,
    view: ReadinessView = "boss",
    waived: Sequence[str] = (),
    today: date | None = None,
) -> dict[str, Any]:
    if view not in VIEW_STEPS:
        view = "boss"
    company = await _load_company(session, company_id, user_id)
    fiscal_year = resolve_fiscal_year(company, today)
    payload = company.payload or {}
    has_start = bool(str(payload.get("startDate") or "").strip())
    # 只算当前视图需要的步骤：老板视图不查旧账导入，会计视图不查报表，
    # 单次请求的查询数因此从 9 条降到 5~7 条。
    needed = set(VIEW_STEPS[view])

    results: dict[str, dict[str, Any]] = {}

    profile_status, profile_detail = _profile_result(company, view)
    results["company_profile"] = _step(
        "company_profile", view, profile_status, detail=profile_detail
    )

    if "chart_accounts" in needed:
        chart_status, chart_detail = await _chart_result(session, company_id)
        results["chart_accounts"] = _step(
            "chart_accounts",
            view,
            chart_status,
            detail=chart_detail,
            informational=True,
        )

    if has_start:
        opening_status, opening_detail, derived = await _opening_result(
            session, company, fiscal_year
        )
    else:
        opening_status, opening_detail, derived = ("locked", "填好启用日期才能判断期初", False)
    results["opening_balance"] = _step(
        "opening_balance",
        view,
        opening_status,
        detail=opening_detail,
        blocked_by="company_profile" if opening_status == "locked" else None,
        progress={"current": 0, "target": 1, "unit": "个科目"} if derived else None,
    )

    if "legacy_import" in needed:
        legacy_status, legacy_detail = await _legacy_result(session, company_id, opening_status)
        if "legacy_import" in waived and legacy_status != "done":
            legacy_status, legacy_detail = "waived", "已标记为不适用"
        results["legacy_import"] = _step(
            "legacy_import", view, legacy_status, detail=legacy_detail
        )

    tax_status, tax_detail = await _tax_result(session, company_id, view)
    results["tax_registration"] = _step(
        "tax_registration", view, tax_status, detail=tax_detail
    )

    if has_start:
        voucher_status, voucher_detail, voucher_progress = await _voucher_result(
            session, company_id
        )
    else:
        voucher_status, voucher_detail, voucher_progress = (
            "locked",
            "填好启用日期才能开始记账",
            None,
        )
    results["first_voucher"] = _step(
        "first_voucher",
        view,
        voucher_status,
        detail=voucher_detail,
        blocked_by="company_profile" if voucher_status == "locked" else None,
        progress=voucher_progress,
    )

    if "report_view" in needed:
        if voucher_status in ("done", "waived"):
            report_status, report_detail = await _report_result(session, company_id, fiscal_year)
            report_blocked = None
        else:
            report_status, report_detail, report_blocked = (
                "locked",
                "先记第一笔账，报表才有数",
                "first_voucher",
            )
        results["report_view"] = _step(
            "report_view",
            view,
            report_status,
            detail=report_detail,
            blocked_by=report_blocked,
        )

    # 每个视图取各自顺序的子集；waived 只有显式标记的那些，从零起账不算跳过
    items = [results[key] for key in VIEW_STEPS[view]]
    for item in items:
        if item["status"] == "waived" and item["key"] not in waived:
            item["waivedReason"] = "zero_start" if item["key"] == "opening_balance" else "auto"

    actionable = [
        item
        for item in items
        if not item["informational"] and item["status"] != "waived"
    ]
    completed = sum(1 for item in actionable if item["status"] == "done")

    return {
        "companyId": company_id,
        "view": view,
        "fiscalYear": fiscal_year,
        "completed": completed,
        "total": len(actionable),
        "ready": bool(actionable) and completed == len(actionable),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }
