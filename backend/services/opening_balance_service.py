# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Opening balances represented by immutable system-generated journals."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, ENTRY_POSTED_IMMUTABLE
from core.money import ZERO, money, money_text
from db.models import ChartAccount, JournalEntry, JournalLine
from db.session import session_scope
from services.account_classification import is_profit_loss_code
from services.accounting_core_service import (
    consume_step_up_token,
    create_entry,
    require_role,
    reverse_journal,
    serialize_journal,
)
# 错误 context 里最多回传多少行期初分录/候选明细：救援弹窗要的是「定位到错处」。
_RESCUE_LINE_LIMIT = 10

# ponytail: 损益类 + 成本类科目代码集合，用于年中建账与资产负债期初拆分。
# 只作兜底白名单：5xxx 全段由 account_classification.is_profit_loss_code 统一判定，
# 枚举永远追不上 COA 新增明细（5602016 单位社保就曾漏掉，导致试算平衡把
# 损益科目当资产科目合计）。
EXPENSE_REVENUE_CODES = frozenset([
    "5001", "5051", "5111", "5301", "5401", "5401001", "5401002", "5402", "5403",
    "5601", "5601001", "5601006", "5601011", "5601015", "5601016",
    "5602", "5602001", "5602002", "5602004", "5602005", "5602006", "5602007",
    "5602011", "5602013", "5602014", "5602015", "5602016", "5602017", "5602999",
    "5603", "5603001", "5603002", "5603003", "5603004",
    "5711", "5801", "4301",
])


def _is_expense_revenue(code: Any) -> bool:
    """损益/成本类判定：is_profit_loss_code 为主，白名单兜底。"""
    return is_profit_loss_code(code) or code in EXPENSE_REVENUE_CODES


# 期初凭证 id 的代数。冲销只把原凭证置 reversed、不删行（A1 要求留审计历史），
# 而 create_entry 按 id 查重，同一 id 无法二次创建 —— 所以「冲销后重导」必须换
# 一代 id。第 1 代沿用裸 id 以兼容已有库；第 2 代起带 -g{n} 后缀。
_OPENING_GEN_MARK = "-g"


def _entry_id(company_id: str, fiscal_year: int, generation: int = 1) -> str:
    base = f"opening-{company_id}-{fiscal_year}"
    return base if generation <= 1 else f"{base}{_OPENING_GEN_MARK}{generation}"


def _ytd_entry_id(company_id: str, fiscal_year: int, generation: int = 1) -> str:
    return f"{_entry_id(company_id, fiscal_year, generation)}-ytd"


def _entry_generation(entry_id: str, company_id: str, fiscal_year: int) -> int:
    """从凭证 id 反解代数；裸 id（第 1 代）与无法解析的都算第 1 代。"""
    suffix = entry_id[len(f"opening-{company_id}-{fiscal_year}"):]
    if suffix.startswith(_OPENING_GEN_MARK):
        head = suffix[len(_OPENING_GEN_MARK):].split("-")[0]
        if head.isdigit():
            return int(head)
    return 1


async def _load_accounts_allowing_referenced(
    session: AsyncSession,
    company_id: str,
    referenced_codes: Iterable[str],
) -> list[ChartAccount]:
    """读侧兜底：凭证里引用到、但已被过滤（非末级/停用）的科目也要原样返回。

    历史凭证不可变：1221/2211/2221 容器化或用户停用科目后，读侧若只查
    is_leaf=True 且 is_active=True，这些分录行会从期初余额里静默消失，
    期初与总账对不上。此时必须把被引用的科目补回来（只读呈现，不重启用）。
    """
    accounts = (
        await session.execute(
            select(ChartAccount)
            .where(
                ChartAccount.company_id == company_id,
                ChartAccount.is_active.is_(True),
                ChartAccount.is_leaf.is_(True),
            )
            .order_by(ChartAccount.code)
        )
    ).scalars().all()
    known = {account.code for account in accounts}
    missing = sorted({str(code) for code in referenced_codes if code} - known)
    if missing:
        extra = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(missing),
                )
            )
        ).scalars().all()
        accounts = sorted([*accounts, *extra], key=lambda account: account.code)
    return accounts


def _validate_opening_account(account_map: dict[str, ChartAccount], code: str) -> None:
    """写侧前置校验：期初凭证不可变，容器/停用/不存在科目必须当场拦下。

    否则错误要等到 create_entry 才以通用的 INVALID_ACCOUNT 抛出，
    用户看不到是哪个科目、该换成什么。

    context 里带上 accountCode / reason / suggestedChildren：救援弹窗的出路是
    「去改用明细科目」，没有候选明细码这个出路就落不了地。
    """
    row = account_map.get(code)
    if row is None:
        raise AccountingError(
            "OPENING_ACCOUNT_INVALID",
            f"科目 {code or '(空)'} 不存在于科目表，请刷新页面后重试",
            422,
            {"accountCode": code, "accountName": "", "reason": "not_found"},
        )
    if not row.is_active:
        raise AccountingError(
            "OPENING_ACCOUNT_INVALID",
            f"科目 {code}（{row.name}）已停用，不能录入期初余额",
            422,
            {"accountCode": code, "accountName": str(row.name), "reason": "inactive"},
        )
    if not row.is_leaf:
        children = sorted(
            child_code
            for child_code, child in account_map.items()
            if child.is_active and child.is_leaf and child_code != code and child_code.startswith(code)
        )
        hint = f"；请改用末级明细：{'、'.join(children[:6])}" if children else "；请改用其末级明细科目"
        raise AccountingError(
            "OPENING_ACCOUNT_INVALID",
            f"科目 {code}（{row.name}）是容器科目，不能直接记账{hint}",
            422,
            {
                "accountCode": code,
                "accountName": str(row.name),
                "reason": "not_leaf",
                "suggestedChildren": [
                    {
                        "code": child_code,
                        "name": str(account_map[child_code].name),
                    }
                    for child_code in children[:_RESCUE_LINE_LIMIT]
                ],
            },
        )


def _opening_id_prefix(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}"


async def _live_opening_entries(
    session: AsyncSession, company_id: str, fiscal_year: int
) -> list[JournalEntry]:
    """该年度所有「活」期初凭证（未被冲销）。

    冲销只把原凭证置 reversed 并留作审计历史，所以按确定性 id 单点查会命中
    墓碑。用 id 前缀 + 状态过滤拿到活的那一代（含 YTD 过渡凭证）。
    """
    prefix = _opening_id_prefix(company_id, fiscal_year)
    rows = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type == "opening",
                JournalEntry.status != "reversed",
                JournalEntry.id.startswith(prefix),
            )
        )
    ).scalars().all()
    return list(rows)


async def _find_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    """当前有效的期初主凭证（未冲销）。已冲销的留作审计历史，不算数。"""
    entries = await _live_opening_entries(session, company_id, fiscal_year)
    mains = [e for e in entries if not e.id.endswith("-ytd")]
    return max(mains, key=lambda e: _entry_generation(e.id, company_id, fiscal_year)) if mains else None


async def _find_ytd_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    entries = await _live_opening_entries(session, company_id, fiscal_year)
    ytds = [e for e in entries if e.id.endswith("-ytd")]
    return max(ytds, key=lambda e: _entry_generation(e.id, company_id, fiscal_year)) if ytds else None


async def _next_opening_generation(session: AsyncSession, company_id: str, fiscal_year: int) -> int:
    """下一代期初凭证代数：已有凭证的最大代数 + 1（含已冲销的，避免撞 id）。"""
    prefix = _opening_id_prefix(company_id, fiscal_year)
    ids = (
        await session.execute(
            select(JournalEntry.id).where(
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type == "opening",
                JournalEntry.id.startswith(prefix),
            )
        )
    ).scalars().all()
    gens = [_entry_generation(i, company_id, fiscal_year) for i in ids]
    return (max(gens) + 1) if gens else 1


async def _non_opening_activity(
    session: AsyncSession,
    company_id: str,
    *,
    before: date,
    on_or_after: date | None = None,
    effective_only: bool = False,
) -> tuple[int, date | None]:
    """统计 [on_or_after, before) 内非期初分录的条数与最早日期。

    effective_only 排除已冲销的原分录和冲销分录：两者净额为零，不应阻止
    期初重导（「冲销重置后重导」是正常流程）。
    """
    conditions = [
        JournalEntry.company_id == company_id,
        JournalEntry.occurred_on < before,
        JournalEntry.entry_type != "opening",
        JournalEntry.status.in_(("posted", "reversed", "draft", "pending_review")),
    ]
    if on_or_after is not None:
        conditions.append(JournalEntry.occurred_on >= on_or_after)
    if effective_only:
        conditions.append(JournalEntry.entry_type != "reversal")
        conditions.append(JournalEntry.status != "reversed")
    count, earliest = (
        await session.execute(
            select(func.count(), func.min(JournalEntry.occurred_on))
            .select_from(JournalEntry)
            .where(*conditions)
        )
    ).one()
    return int(count or 0), earliest


async def _has_activity_before(session: AsyncSession, company_id: str, fiscal_year: int) -> bool:
    """该年 1 月 1 日之前是否存在业务分录（opening 除外）。"""
    count, _ = await _non_opening_activity(
        session, company_id, before=date(fiscal_year, 1, 1)
    )
    return count > 0


async def derive_opening_items(
    session: AsyncSession, company_id: str, fiscal_year: int
) -> list[dict[str, Any]]:
    """无显式期初凭证时，从历史分录派生跨年期初余额。

    返回值复用 ``get_opening_balances`` 的条目结构，并带 ``derived=True`` 标记，
    UI 应将其渲染为只读；用户确需覆盖时先反结账年度。
    """
    from services.fiscal_year_service import derive_year_opening_balances

    balances = await derive_year_opening_balances(session, company_id, fiscal_year)
    if not balances:
        return []
    # 读侧兜底：历史分录里的容器/停用科目也要呈现（详见 _load_accounts_allowing_referenced）
    accounts = await _load_accounts_allowing_referenced(session, company_id, balances.keys())
    items: list[dict[str, Any]] = []
    for account in accounts:
        debit, credit = balances.get(account.code, (ZERO, ZERO))
        if debit == ZERO and credit == ZERO:
            continue
        items.append({
            "id": "",
            "company_id": company_id,
            "account_code": account.code,
            "account_name": account.name,
            "debit_balance": money_text(debit),
            "credit_balance": money_text(credit),
            "partner_name": "",
            "fiscal_year": fiscal_year,
            "voucherStatus": None,
            "voucherRef": None,
            "derived": True,
        })
    return items


async def get_year_start_balances(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """年初（1 月 1 日）余额快照，返回 (items, known)。

    报表「年初余额」列不能拿建账时点的期初顶替：年中建账时两者差着建账前
    几个月的全部变动（例如 2026-10 启用的账套，期初是 9 月 30 日余额）。
    快照在导入期初时随凭证一起落库（期初凭证 metadata 的 yearStartBalances）。

    known 的判定：
    - 没有期初凭证 → 年初由历史分录派生，口径本来就是年初，known=True；
    - 有凭证且存了快照 → known=True，用快照；
    - 有凭证但没快照：建账月为 1 月时期初 == 年初，known=True；
      年中建账则 1 月 1 日余额无从得知，known=False（报表不得填数）。
    """
    if session is None:
        async with session_scope() as owned_session:
            return await get_year_start_balances(
                company_id, fiscal_year, session=owned_session,
            )
    entry = await _find_entry(session, company_id, fiscal_year)
    if entry is None:
        return [], True
    raw = (entry.metadata_json or {}).get("yearStartBalances") or []
    items = [
        {
            "account_code": str(row.get("accountCode") or ""),
            "account_name": str(row.get("accountName") or ""),
            # 客商维度保留到报表：往来科目要按明细方向重分类（应付借余 → 预付）
            "partner_name": str(row.get("partnerName") or ""),
            "debit_balance": money_text(money(row.get("debit") or ZERO)),
            "credit_balance": money_text(money(row.get("credit") or ZERO)),
        }
        for row in raw
        if isinstance(row, dict) and row.get("accountCode")
    ]
    if items:
        return items, True
    return [], entry.occurred_on.month == 1


async def get_opening_balances(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    if session is None:
        async with session_scope() as owned_session:
            return await get_opening_balances(
                company_id,
                fiscal_year,
                session=owned_session,
            )
    entry = await _find_entry(session, company_id, fiscal_year)
    line_map: dict[str, tuple[Decimal, Decimal, str]] = {}
    derived_items: list[dict[str, Any]] = []
    accounts: list[ChartAccount] = []
    if entry is not None:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry.id)
            )
        ).scalars().all()
        for line in lines:
            debit, credit, partner = line_map.get(line.account_code, (ZERO, ZERO, ""))
            if line.direction == "debit":
                debit += money(line.amount)
            else:
                credit += money(line.amount)
            partner_name = (line.auxiliary or {}).get("partner_name") or partner
            line_map[line.account_code] = (debit, credit, partner_name)
        # 读侧兜底：凭证引用了非末级/停用科目（如容器化前的 2211）时，
        # 不能把这些行从期初余额里静默丢掉。
        accounts = await _load_accounts_allowing_referenced(session, company_id, line_map.keys())
    else:
        accounts = await _load_accounts_allowing_referenced(session, company_id, ())
        # 跨年连续性：第二年及以后没有显式期初凭证时，从全部历史分录派生，
        # 否则资产负债表期初归零、账套断年（总账连续而报表断年的口径撕裂）。
        derived_items = await derive_opening_items(session, company_id, fiscal_year)

    if derived_items and not entry:
        return derived_items

    return [
        {
            "id": entry.id if entry else "",
            "company_id": company_id,
            "account_code": account.code,
            "account_name": account.name,
            "debit_balance": money_text(line_map.get(account.code, (ZERO, ZERO, ""))[0]),
            "credit_balance": money_text(line_map.get(account.code, (ZERO, ZERO, ""))[1]),
            "partner_name": line_map.get(account.code, (ZERO, ZERO, ""))[2],
            "fiscal_year": fiscal_year,
            "voucherStatus": entry.status if entry else None,
            "voucherRef": entry.voucher_no if entry else None,
            "derived": False,
        }
        for account in accounts
    ]


async def get_opening_partner_balances(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """期初凭证按「科目 + 客商」拆分的余额，报表按明细方向重分类用。

    《小企业会计准则》资产负债表不得按科目净额相互抵销：应付账款某客商是借方
    余额时要列进预付账款，其他应付款某人是借方余额时要列进其他应收款。
    `get_opening_balances` 是科目级汇总，客商维度在这里单独取一次。
    """
    if session is None:
        async with session_scope() as owned_session:
            return await get_opening_partner_balances(
                company_id, fiscal_year, session=owned_session,
            )
    entry = await _find_entry(session, company_id, fiscal_year)
    if entry is None:
        return []
    lines = (
        await session.execute(
            select(JournalLine).where(JournalLine.journal_entry_id == entry.id)
        )
    ).scalars().all()
    buckets: dict[tuple[str, str], list[Decimal]] = {}
    for line in lines:
        partner = str((line.auxiliary or {}).get("partner_name") or "").strip()
        bucket = buckets.setdefault((line.account_code, partner), [ZERO, ZERO])
        bucket[0 if line.direction == "debit" else 1] += money(line.amount)
    return [
        {
            "account_code": code,
            "partner_name": partner,
            "debit_balance": money_text(debit),
            "credit_balance": money_text(credit),
        }
        for (code, partner), (debit, credit) in buckets.items()
    ]


async def get_opening_details(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """获取完整的期初配置，包括资产负债期初、损益本年累计与税务持久化备忘。"""
    if session is None:
        async with session_scope() as owned_session:
            return await get_opening_details(company_id, fiscal_year, session=owned_session)

    all_balances = await get_opening_balances(company_id, fiscal_year, session=session)

    # 读取可能存在的 YTD 凭证
    ytd_entry = await _find_ytd_entry(session, company_id, fiscal_year)
    ytd_map: dict[str, tuple[Decimal, Decimal]] = {}
    if ytd_entry is not None:
        ytd_lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == ytd_entry.id)
            )
        ).scalars().all()
        for line in ytd_lines:
            if line.account_code in ("3103", "3104006"):
                continue
            debit, credit = ytd_map.get(line.account_code, (ZERO, ZERO))
            if line.direction == "debit":
                debit += money(line.amount)
            else:
                credit += money(line.amount)
            ytd_map[line.account_code] = (debit, credit)

    # 读侧兜底：YTD 凭证里的容器/停用科目同样不能丢
    leaf_accounts = await _load_accounts_allowing_referenced(session, company_id, ytd_map.keys())

    # ponytail: 损益类末级科目归入年中累计，其余归入资产负债期初。
    # 判定走 account_classification.is_profit_loss_code 单一口径
    # （5xxx 全段 + 4301*），白名单仅兜底。
    expense_revenue_set = {
        acc.code
        for acc in leaf_accounts
        if _is_expense_revenue(acc.code)
        or (acc.category and "损益" in acc.category)
    }

    balance_items = [b for b in all_balances if b["account_code"] not in expense_revenue_set]

    ytd_items = [
        {
            "account_code": acc.code,
            "account_name": acc.name,
            "debit_balance": money_text(ytd_map.get(acc.code, (ZERO, ZERO))[0]),
            "credit_balance": money_text(ytd_map.get(acc.code, (ZERO, ZERO))[1]),
        }
        for acc in leaf_accounts
        if acc.code in expense_revenue_set
    ]

    from db.models import CompanyProfile
    tax_memory = {
        "priorYearLoss": 0,
        "prepaidCitTax": 0,
        "migrationNote": "",
        "startPeriod": f"{fiscal_year}-01",
    }
    # 只有真正保存过（税务备忘里写过 startPeriod）或已有期初凭证时，start_period
    # 才算用户的选择；否则上面那个 {year}-01 只是兜底显示值，不能当成建账时间——
    # 前端要按公司信息里的启用日期重新给默认值。
    saved_start_period: str | None = None
    try:
        prof_row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        if prof_row and prof_row.profile and "taxOpening" in prof_row.profile:
            tax_memory.update(prof_row.profile["taxOpening"])
            saved_start_period = (prof_row.profile.get("taxOpening") or {}).get("startPeriod") or None
    except Exception:
        pass

    entry = await _find_entry(session, company_id, fiscal_year)
    entry_period = entry.occurred_on.strftime("%Y-%m") if entry else None
    saved_start_period = saved_start_period or entry_period
    # 税务备忘里的 startPeriod 每个账套只存一个值，换年度查时不能拿它冒充该年的
    # 建账时间（否则查 2027 会返回 2026-01）
    if saved_start_period and not saved_start_period.startswith(f"{fiscal_year}-"):
        saved_start_period = None
    start_period = saved_start_period or f"{fiscal_year}-01"

    # ponytail: 尝试读取关联的期初固定资产卡片（P1 WP-B 起写核心 ORM，
    # 返回字段保持 legacy payload 形状，前端 opening 页无需感知）
    fixed_assets: list[dict[str, Any]] = []
    try:
        from core.fixed_asset_categories import CATEGORY_LABELS
        from db.models import FixedAsset

        # 卡片挂的是「活的那一代」期初凭证；用 entry.id 而非确定性裸 id，
        # 否则冲销重导后卡片会指向已冲销的旧凭证。
        live_entry_id = entry.id if entry else _entry_id(company_id, fiscal_year)
        cards = (
            await session.execute(
                select(FixedAsset).where(
                    FixedAsset.company_id == company_id,
                    FixedAsset.source_entry_id == live_entry_id,
                    FixedAsset.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        label_by_code = {code: label for label, code in CATEGORY_LABELS.items()}
        for card in cards:
            fixed_assets.append(
                {
                    "name": card.name,
                    "category": label_by_code.get(card.category, "其他"),
                    "purchaseDate": card.acquisition_date.isoformat(),
                    "cost": float(money(card.original_value)),
                    "accumulatedDepreciation": float(money(card.accumulated_depreciation or ZERO)),
                    "usefulMonths": int(card.useful_life_months),
                    "salvageRate": float(card.residual_rate or ZERO),
                    "oneTimeDepreciation": card.depreciation_method == "one_time",
                    "isOpening": True,
                }
            )
    except Exception:
        pass

    year_start_items = [
        {
            "account_code": str(row.get("accountCode") or ""),
            "account_name": str(row.get("accountName") or ""),
            "partner_name": str(row.get("partnerName") or ""),
            "debit_balance": money_text(money(row.get("debit") or ZERO)),
            "credit_balance": money_text(money(row.get("credit") or ZERO)),
        }
        for row in ((entry.metadata_json or {}).get("yearStartBalances") or [])
        if isinstance(row, dict) and row.get("accountCode")
    ] if entry else []

    return {
        "items": balance_items,
        "ytd_items": ytd_items,
        # 年初快照：重导时前端原样带回，不用重新解析 Excel
        "year_start_items": year_start_items,
        "start_period": start_period,
        "start_period_saved": bool(saved_start_period),
        "tax_memory": tax_memory,
        "fixed_assets": fixed_assets,
        # 只认 posted：冲销后原凭证是墓碑，页面必须解锁让用户重新导入
        "is_locked": bool(entry and entry.status == "posted"),
        "voucher_no": entry.voucher_no if entry else None,
    }


async def _resolve_opening_partners(
    session: AsyncSession,
    company_id: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给只带 partner_name 的期初行补 partner_id（按名字复用或建档）。

    复用 `auxiliary_service` 的归一化名 + 编码分配，但在**调用方 session** 内
    建档：期初保存与建档案同一事务，保存失败不会留下孤儿客商档案
    （`ensure_partner_by_name` 走独立 session_scope，脱离本次事务，不适合这里）。
    名字为空或解析失败时原样放行——不强制建档，与「匹配不上就留纯文本」的克制一致。
    """
    from db.models import Partner
    from services.auxiliary_service import CODE_PREFIX, _mnemonic, _normalize_name, _next_code

    names = {
        str(item.get("partner_name") or "").strip()
        for item in items
        if str(item.get("partner_name") or "").strip()
        and not str(item.get("partner_id") or "").strip()
    }
    if not names:
        return items

    existing = (
        await session.execute(select(Partner).where(Partner.company_id == company_id))
    ).scalars().all()
    by_name = {_normalize_name(row.name): row for row in existing}

    resolved: list[dict[str, Any]] = []
    for item in items:
        payload = dict(item)
        name = str(payload.get("partner_name") or "").strip()
        if not name or str(payload.get("partner_id") or "").strip():
            resolved.append(payload)
            continue
        key = _normalize_name(name)
        partner = by_name.get(key)
        if partner is None:
            code = await _next_code(session, company_id, Partner, CODE_PREFIX["both"])
            partner = Partner(
                company_id=company_id,
                code=code,
                name=name,
                type="both",
                mnemonic=_mnemonic(name),
            )
            session.add(partner)
            await session.flush()
            by_name[key] = partner
        payload["partner_name"] = partner.name
        payload["partner_id"] = partner.id
        resolved.append(payload)
    return resolved


async def save_opening_balances(
    company_id: str,
    fiscal_year: int,
    items: list[dict[str, Any]],
    *,
    user_id: str,
    step_up_token: str | None,
    request_id: str,
    start_period: str | None = None,
    ytd_items: list[dict[str, Any]] | None = None,
    year_start_items: list[dict[str, Any]] | None = None,
    tax_memory: dict[str, Any] | None = None,
    fixed_assets: list[dict[str, Any]] | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    if session is None:
        async with session_scope() as owned_session:
            return await save_opening_balances(
                company_id,
                fiscal_year,
                items,
                user_id=user_id,
                step_up_token=step_up_token,
                request_id=request_id,
                start_period=start_period,
                ytd_items=ytd_items,
                year_start_items=year_start_items,
                tax_memory=tax_memory,
                fixed_assets=fixed_assets,
                session=owned_session,
            )
    await require_role(session, company_id, user_id, {"owner"})
    await consume_step_up_token(
        session,
        step_up_token,
        company_id=company_id,
        user_id=user_id,
        action="adjust_opening_balance",
    )
    existing = await _find_entry(session, company_id, fiscal_year)
    if existing is not None:
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE,
            "期初凭证已存在；如需调整，请先冲销重置后重新导入",
            409,
            {"entryId": existing.id, "status": existing.status,
             "occurredOn": existing.occurred_on.isoformat(),
             "entryType": existing.entry_type},
        )
    # 1. 解析启用期间与生效日期（双计校验要用，提到活动检查之前）
    period_str = (start_period or f"{fiscal_year}-01").strip()
    try:
        pyear, pmonth = [int(p) for p in period_str.split("-")[:2]]
    except Exception:
        pyear, pmonth = fiscal_year, 1
    occur_date = date(pyear, pmonth, 1)

    # 跨年防双计：已有历史分录的年份，期初余额由历史派生；再手工录期初会重复计入
    prior_activity = await _has_activity_before(session, company_id, fiscal_year)
    if prior_activity:
        raise AccountingError(
            "OPENING_BALANCE_DERIVED",
            f"{fiscal_year} 年已有历史账务，期初余额自动从历史分录派生，不能手工录入；"
            "如需调整请先反结账年度或冲销相关凭证",
            409,
            {"fiscalYear": fiscal_year},
        )

    # 同年防双计：建账月之前本年已有业务分录，这些业务的结果已经包含在
    # 导入的期初余额表里，再生成期初凭证会把同一段期间算两遍。
    if pmonth > 1 and pyear == fiscal_year:
        prior_count, earliest = await _non_opening_activity(
            session,
            company_id,
            on_or_after=date(fiscal_year, 1, 1),
            before=occur_date,
            effective_only=True,
        )
        if prior_count:
            earliest_text = f"（最早 {earliest.isoformat()}）" if earliest else ""
            raise AccountingError(
                "OPENING_PRIOR_ACTIVITY",
                f"{fiscal_year} 年 1-{pmonth - 1} 月已有 {prior_count} 条业务分录"
                f"{earliest_text}。期初余额应包含这些业务的结果，再生成期初凭证会重复计入；"
                "请先冲销或删除这些分录，或把建账月份调到最早一笔业务之前",
                409,
                {
                    "before": occur_date.isoformat(),
                    "count": prior_count,
                    "earliestOccurredOn": earliest.isoformat() if earliest else None,
                },
            )

    # 写侧前置校验：期初凭证不可变，容器/停用/不存在科目必须当场拦下，
    # 并给出可操作的换科目提示（详见 _validate_opening_account）
    account_map = {
        row.code: row
        for row in (
            await session.execute(
                select(ChartAccount).where(ChartAccount.company_id == company_id)
            )
        ).scalars().all()
    }

    # 2. 组装资产负债期初分录（可挂接客商辅助信息）
    # 期初分录不做辅助核算归一化（accounting_core_service 里 opening 豁免
    # normalize_line_auxiliary），所以 partner_id 必须在这里解析好：只给了名字的
    # 行，按名字取或建往来单位档案，再落 id。否则期初的往来余额在按 partner_id
    # 分组的账龄里会全部塌进「未指定往来单位」，老板看不出欠谁钱。
    items = await _resolve_opening_partners(session, company_id, items)

    debit_lines: list[dict[str, Any]] = []
    credit_lines: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    for item in items:
        debit = money(item.get("debit_balance") or ZERO, allow_negative=False)
        credit = money(item.get("credit_balance") or ZERO, allow_negative=False)
        code = str(item.get("account_code") or "").strip()
        if debit > ZERO and credit > ZERO:
            raise AccountingError(
                "OPENING_SIDE_CONFLICT",
                "同一科目不能同时填写借方和贷方期初余额",
                422,
                {
                    "accountCode": code,
                    "accountName": str(item.get("account_name") or "").strip(),
                    "debit": money_text(debit),
                    "credit": money_text(credit),
                },
            )
        if debit > ZERO or credit > ZERO:
            _validate_opening_account(account_map, code)
        base = {
            "accountCode": code,
            "accountName": str(item.get("account_name") or "").strip(),
        }
        partner = str(item.get("partner_name") or "").strip()
        if partner:
            # 旧格式只有文本 partner_name；新导入（旧账导账）会带 partner_id，
            # 带上就一起落库，让期初的往来余额也能进按往来单位的账龄/余额表。
            partner_id = str(item.get("partner_id") or "").strip()
            base["auxiliary"] = {
                "partner_name": partner,
                "type": "partner",
                **({"partner_id": partner_id} if partner_id else {}),
            }

        if debit > ZERO:
            debit_lines.append({**base, "amount": debit})
            total_debit += debit
        if credit > ZERO:
            credit_lines.append({**base, "amount": credit})
            total_credit += credit

    if total_debit == ZERO or total_debit != total_credit:
        rescue_lines = [
            {
                "lineNo": index + 1,
                "accountCode": line["accountCode"],
                "accountName": line["accountName"],
                "direction": direction,
                "amount": money_text(line["amount"]),
            }
            for index, (direction, line) in enumerate(
                [("debit", item) for item in debit_lines]
                + [("credit", item) for item in credit_lines],
                start=1,
            )
        ][:_RESCUE_LINE_LIMIT]
        raise AccountingError(
            "UNBALANCED_ENTRY",
            "期初余额借贷必须相等且大于零",
            422,
            {
                "debit": money_text(total_debit),
                "credit": money_text(total_credit),
                "difference": money_text(abs(total_debit - total_credit)),
                "lines": rescue_lines,
            },
        )

    # 年初（1 月 1 日）快照：只用于报表「年初余额」列，不进分录、不参与试算平衡。
    # 随期初凭证一起落库，冲销重设换新一代凭证时由重导覆盖，不会留下两份数据。
    year_start_payload = [
        {
            "accountCode": str(item.get("account_code") or "").strip(),
            "accountName": str(item.get("account_name") or "").strip(),
            "partnerName": str(item.get("partner_name") or "").strip(),
            "debit": money_text(money(item.get("debit_balance") or ZERO, allow_negative=False)),
            "credit": money_text(money(item.get("credit_balance") or ZERO, allow_negative=False)),
        }
        for item in (year_start_items or [])
        if str(item.get("account_code") or "").strip()
        and (
            money(item.get("debit_balance") or ZERO) != ZERO
            or money(item.get("credit_balance") or ZERO) != ZERO
        )
    ]

    title_desc = f"{pyear} 年 {pmonth} 月期初余额" if pmonth > 1 else f"{fiscal_year} 年期初余额"
    # 换代：冲销过的旧代留作审计历史，重导必须用新 id（同 id 无法二次创建）
    generation = await _next_opening_generation(session, company_id, fiscal_year)
    entry = await create_entry(
        session,
        {
            "id": _entry_id(company_id, fiscal_year, generation),
            "direction": "owner_transfer",
            "amount": total_debit,
            "title": title_desc,
            "category": "期初余额",
            "occurDate": occur_date.isoformat(),
            "sourceType": "opening_balance",
            # 来源键带代数：同一账套年间可重导多次，每次是独立来源
            "dedupFingerprint": f"opening:{company_id}:{fiscal_year}:{generation}",
            "entryType": "opening",
            "voucherAuthority": "system",
            "voucherLines": {"debit": debit_lines, "credit": credit_lines},
            **({"yearStartBalances": year_start_payload} if year_start_payload else {}),
        },
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )

    # 3. 年中建账：若 pmonth > 1 且录入了损益累计发生额，生成 YTD 过渡凭证
    # ponytail: 差额直接计入 3103 本年利润，使 YTD 凭证借贷严格平衡且无缝衔接后续利润表本年累计
    ytd_entry = None
    if pmonth > 1 and ytd_items:
        ytd_debits: list[dict[str, Any]] = []
        ytd_credits: list[dict[str, Any]] = []
        sum_ytd_debit = ZERO
        sum_ytd_credit = ZERO

        for yi in ytd_items:
            d = money(yi.get("debit_balance") or ZERO, allow_negative=False)
            c = money(yi.get("credit_balance") or ZERO, allow_negative=False)
            acode = str(yi.get("account_code") or "").strip()
            aname = str(yi.get("account_name") or "").strip()
            if d > ZERO or c > ZERO:
                _validate_opening_account(account_map, acode)
            if d > ZERO:
                ytd_debits.append({"accountCode": acode, "accountName": aname, "amount": d})
                sum_ytd_debit += d
            if c > ZERO:
                ytd_credits.append({"accountCode": acode, "accountName": aname, "amount": c})
                sum_ytd_credit += c

        if sum_ytd_debit > ZERO or sum_ytd_credit > ZERO:
            # 动态查找当前账套有效的本年利润/未分配利润末级科目（《小企业会计准则》为 3103）
            profit_acc = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.is_active.is_(True),
                        ChartAccount.is_leaf.is_(True),
                        ChartAccount.code.in_(("3103", "3104006")),
                    )
                )
            ).scalars().first()
            p_code = profit_acc.code if profit_acc else "3103"
            p_name = profit_acc.name if profit_acc else "本年利润"

            if sum_ytd_credit > sum_ytd_debit:
                # 贷方(收入)大于借方(费用)，借方补入本年利润差额配平
                diff = sum_ytd_credit - sum_ytd_debit
                ytd_debits.append({"accountCode": p_code, "accountName": p_name, "amount": diff})
            elif sum_ytd_debit > sum_ytd_credit:
                # 借方(费用)大于贷方(收入)，贷方补入本年利润差额配平
                diff = sum_ytd_debit - sum_ytd_credit
                ytd_credits.append({"accountCode": p_code, "accountName": p_name, "amount": diff})

            ytd_occur_date = occur_date - timedelta(days=1)
            balanced_ytd_amount = max(sum_ytd_debit, sum_ytd_credit)
            ytd_entry = await create_entry(
                session,
                {
                    "id": _ytd_entry_id(company_id, fiscal_year, generation),
                    "direction": "owner_transfer",
                    "amount": balanced_ytd_amount,
                    "title": f"{pyear} 年 1-{pmonth - 1} 月损益累计发生额",
                    "category": "期初余额",
                    "occurDate": ytd_occur_date.isoformat(),
                    "sourceType": "opening_balance_ytd",
                    "dedupFingerprint": f"opening_ytd:{company_id}:{fiscal_year}:{generation}",
                    "entryType": "opening",
                    "voucherAuthority": "system",
                    "voucherLines": {"debit": ytd_debits, "credit": ytd_credits},
                },
                company_id=company_id,
                user_id=user_id,
                request_id=f"{request_id}:ytd",
                confirm=True,
            )

    # 4. 持久化税务备忘与建账起始期
    if tax_memory or period_str:
        from db.models import CompanyProfile
        try:
            prof_row = (
                await session.execute(
                    select(CompanyProfile).where(CompanyProfile.company_id == company_id)
                )
            ).scalar_one_or_none()
            tax_dict = {
                "priorYearLoss": float((tax_memory or {}).get("prior_year_loss") or (tax_memory or {}).get("priorYearLoss") or 0),
                "prepaidCitTax": float((tax_memory or {}).get("prepaid_cit_tax") or (tax_memory or {}).get("prepaidCitTax") or 0),
                "migrationNote": str((tax_memory or {}).get("migration_note") or (tax_memory or {}).get("migrationNote") or ""),
                "startPeriod": period_str,
            }
            if prof_row is None:
                session.add(CompanyProfile(company_id=company_id, profile={"taxOpening": tax_dict}))
            else:
                p = dict(prof_row.profile or {})
                p["taxOpening"] = tax_dict
                prof_row.profile = p
        except Exception:
            pass

    # 5. 关联同步固定资产卡片（P1 WP-B：写核心 ORM，卡片挂期初凭证幂等）
    if fixed_assets:
        from core.fixed_asset_categories import CATEGORY_LABELS
        from services.fixed_asset_service import ingest_card

        acct_1601_debit = sum((money(item.get("debit_balance") or ZERO) for item in items if str(item.get("account_code") or "").strip() == "1601"), ZERO)
        acct_1602_credit = sum((money(item.get("credit_balance") or ZERO) for item in items if str(item.get("account_code") or "").strip() == "1602"), ZERO)

        total_card_cost = sum((money(fa.get("cost") or fa.get("originalValue") or ZERO) for fa in fixed_assets), ZERO)
        total_card_depr = sum((money(fa.get("accumulated_depreciation") or fa.get("accumulatedDepreciation") or ZERO) for fa in fixed_assets), ZERO)

        if acct_1601_debit > ZERO and abs(total_card_cost - acct_1601_debit) > Decimal("0.05"):
            raise AccountingError(
                "FIXED_ASSET_MISMATCH",
                f"固定资产卡片原值合计(¥{money_text(total_card_cost)})与1601科目借方(¥{money_text(acct_1601_debit)})不一致",
                422,
            )
        if acct_1602_credit > ZERO and abs(total_card_depr - acct_1602_credit) > Decimal("0.05"):
            raise AccountingError(
                "FIXED_ASSET_MISMATCH",
                f"固定资产卡片已提折旧合计(¥{money_text(total_card_depr)})与1602科目贷方(¥{money_text(acct_1602_credit)})不一致",
                422,
            )

        # 期初已提折旧锚定在期初月（起提前一个月），剩余计划自次月起继续
        anchor_period = (date(pyear, pmonth, 1) - timedelta(days=1)).strftime("%Y-%m")
        card_failures: list[str] = []
        for fa in fixed_assets:
            category_label = str(fa.get("category") or "其他").strip()
            original = money(fa.get("cost") or fa.get("originalValue") or ZERO)
            if original <= ZERO:
                card_failures.append(f"{fa.get('name') or '固定资产'}：原值必须大于零")
                continue
            salvage_rate = Decimal(str(fa.get("salvage_rate") or fa.get("salvageRate") or 0))
            residual = money(original * salvage_rate)
            if residual >= original:
                salvage_rate = Decimal("0")
            life = int(fa.get("useful_months") or fa.get("usefulMonths") or 36)
            life = max(12, min(600, life))
            one_time = bool(fa.get("oneTimeDepreciation") or fa.get("one_time_depreciation"))
            try:
                await ingest_card(
                    session,
                    {
                        "name": str(fa.get("name") or "期初固定资产"),
                        "category": CATEGORY_LABELS.get(category_label, "other"),
                        "acquisitionDate": str(fa.get("purchase_date") or fa.get("purchaseDate") or f"{pyear}-{pmonth:02d}-01"),
                        "originalValue": original,
                        "residualRate": Decimal("0") if one_time else salvage_rate,
                        "usefulLifeMonths": 12 if one_time else life,
                        "depreciationMethod": "one_time" if one_time else "straight_line",
                    },
                    company_id=company_id,
                    user_id=user_id,
                    source_entry_id=_entry_id(company_id, fiscal_year, generation),
                    accumulated_depreciation=money(fa.get("accumulated_depreciation") or fa.get("accumulatedDepreciation") or ZERO),
                    last_depreciated_period=anchor_period,
                    status="active",
                    acquisition_type="purchase",
                )
            except AccountingError as exc:
                card_failures.append(f"{fa.get('name') or '固定资产'}：{exc.message}")
        if card_failures:
            raise AccountingError(
                "FIXED_ASSET_CARD_INVALID",
                "期初固定资产卡片录入失败：" + "；".join(card_failures[:5]),
                422,
                {"failures": card_failures},
            )

    res = await serialize_journal(session, entry)
    if ytd_entry:
        res["ytdEntry"] = await serialize_journal(session, ytd_entry)
    return res


async def reset_opening_balances(
    company_id: str,
    fiscal_year: int,
    *,
    user_id: str,
    step_up_token: str | None,
    request_id: str,
    reason: str = "期初数据冲销重置",
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """冲销已有期初凭证（含 YTD 凭证），允许用户重新导入建账。"""
    if session is None:
        async with session_scope() as owned_session:
            return await reset_opening_balances(
                company_id,
                fiscal_year,
                user_id=user_id,
                step_up_token=step_up_token,
                request_id=request_id,
                reason=reason,
                session=owned_session,
            )
    await require_role(session, company_id, user_id, {"owner"})
    await consume_step_up_token(
        session,
        step_up_token,
        company_id=company_id,
        user_id=user_id,
        action="adjust_opening_balance",
    )
    entry = await _find_entry(session, company_id, fiscal_year)
    if entry is None:
        raise AccountingError("OPENING_NOT_FOUND", "未找到可冲销的期初凭证", 404)

    # 冲销主期初凭证
    reversal_entry = await reverse_journal(
        session,
        entry.id,
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:reset_main",
        reason=reason,
    )

    # 若有 YTD 凭证，同步冲销
    ytd_entry = await _find_ytd_entry(session, company_id, fiscal_year)
    if ytd_entry is not None and ytd_entry.status == "posted":
        await reverse_journal(
            session,
            ytd_entry.id,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:reset_ytd",
            reason=reason,
        )

    # 清理该期初关联的固定资产卡片（P1 WP-B：核心 ORM，计划行随卡片硬删）
    try:
        from db.models import FixedAsset, FixedAssetDepreciationSchedule

        opening_cards = (
            await session.execute(
                select(FixedAsset).where(
                    FixedAsset.company_id == company_id,
                    FixedAsset.source_entry_id == entry.id,
                )
            )
        ).scalars().all()
        if opening_cards:
            card_ids = [card.id for card in opening_cards]
            await session.execute(
                FixedAssetDepreciationSchedule.__table__.delete().where(
                    FixedAssetDepreciationSchedule.asset_id.in_(card_ids)
                )
            )
            for card in opening_cards:
                await session.delete(card)
            await session.flush()
    except Exception:
        pass

    # 来源行置 void（墓碑），否则重导时 create_entry 按 external_key 查重会抛
    # DUPLICATE_SOURCE —— delete_draft 就是这么做的，reset 此前漏了这一步。
    try:
        from db.models import SourceTransaction

        source_rows = (
            await session.execute(
                select(SourceTransaction).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.source_type.in_(("opening_balance", "opening_balance_ytd")),
                    SourceTransaction.status != "void",
                )
            )
        ).scalars().all()
        for row in source_rows:
            row.status = "void"
        if source_rows:
            await session.flush()
    except Exception:
        pass

    # 重设期初意味着建账时间也要重新选：清掉上次保存的 startPeriod（其余税务备忘
    # 保留），下次打开页面按公司信息里的启用日期给默认值，而不是钉在旧月份。
    try:
        from db.models import CompanyProfile

        prof_row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        tax_opening = dict((prof_row.profile or {}).get("taxOpening") or {}) if prof_row else {}
        if tax_opening.pop("startPeriod", None) is not None:
            profile = dict(prof_row.profile or {})
            profile["taxOpening"] = tax_opening
            prof_row.profile = profile
    except Exception:
        pass

    return {"ok": True, "reversal": await serialize_journal(session, reversal_entry)}


async def check_balanced(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession,
) -> dict[str, Any]:
    balances = await get_opening_balances(company_id, fiscal_year, session=session)
    # 仅资产负债科目参与试算平衡；损益/成本类判定走 is_profit_loss_code 单一口径
    balance_only = [item for item in balances if not _is_expense_revenue(item["account_code"])]
    total_debit = sum((money(item["debit_balance"]) for item in balance_only), ZERO)
    total_credit = sum((money(item["credit_balance"]) for item in balance_only), ZERO)
    return {
        "balanced": total_debit == total_credit and total_debit > ZERO,
        "total_debit": money_text(total_debit),
        "total_credit": money_text(total_credit),
        "count": sum(1 for item in balance_only if money(item["debit_balance"]) or money(item["credit_balance"])),
    }
