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
    "5602011", "5602013", "5602014", "5602015", "5602016", "5602999",
    "5603", "5603001", "5603002", "5603003", "5603004",
    "5711", "5801", "4301",
])


def _is_expense_revenue(code: Any) -> bool:
    """损益/成本类判定：is_profit_loss_code 为主，白名单兜底。"""
    return is_profit_loss_code(code) or code in EXPENSE_REVENUE_CODES


def _entry_id(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}"


def _ytd_entry_id(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}-ytd"


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


async def _find_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    return (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.id == _entry_id(company_id, fiscal_year),
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type == "opening",
            )
        )
    ).scalar_one_or_none()


async def _find_ytd_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    return (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.id == _ytd_entry_id(company_id, fiscal_year),
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type == "opening",
            )
        )
    ).scalar_one_or_none()


async def _has_activity_before(session: AsyncSession, company_id: str, fiscal_year: int) -> bool:
    """该年 1 月 1 日之前是否存在业务分录（opening 除外）。"""
    boundary = date(fiscal_year, 1, 1)
    count = (
        await session.execute(
            select(func.count()).select_from(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.occurred_on < boundary,
                JournalEntry.entry_type != "opening",
                JournalEntry.status.in_(("posted", "reversed", "draft", "pending_review")),
            )
        )
    ).scalar_one()
    return bool(count)


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
    try:
        prof_row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        if prof_row and prof_row.profile and "taxOpening" in prof_row.profile:
            tax_memory.update(prof_row.profile["taxOpening"])
    except Exception:
        pass

    entry = await _find_entry(session, company_id, fiscal_year)
    start_period = tax_memory.get("startPeriod") or (entry.occurred_on.strftime("%Y-%m") if entry else f"{fiscal_year}-01")

    # ponytail: 尝试读取关联的期初固定资产卡片（P1 WP-B 起写核心 ORM，
    # 返回字段保持 legacy payload 形状，前端 opening 页无需感知）
    fixed_assets: list[dict[str, Any]] = []
    try:
        from core.fixed_asset_categories import CATEGORY_LABELS
        from db.models import FixedAsset

        cards = (
            await session.execute(
                select(FixedAsset).where(
                    FixedAsset.company_id == company_id,
                    FixedAsset.source_entry_id == _entry_id(company_id, fiscal_year),
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

    return {
        "items": balance_items,
        "ytd_items": ytd_items,
        "start_period": start_period,
        "tax_memory": tax_memory,
        "fixed_assets": fixed_assets,
        "is_locked": bool(entry and entry.status in ("posted", "reversed")),
        "voucher_no": entry.voucher_no if entry else None,
    }


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

    # 1. 解析启用期间与生效日期
    period_str = (start_period or f"{fiscal_year}-01").strip()
    try:
        pyear, pmonth = [int(p) for p in period_str.split("-")[:2]]
    except Exception:
        pyear, pmonth = fiscal_year, 1
    occur_date = date(pyear, pmonth, 1)

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

    title_desc = f"{pyear} 年 {pmonth} 月期初余额" if pmonth > 1 else f"{fiscal_year} 年期初余额"
    entry = await create_entry(
        session,
        {
            "id": _entry_id(company_id, fiscal_year),
            "direction": "owner_transfer",
            "amount": total_debit,
            "title": title_desc,
            "category": "期初余额",
            "occurDate": occur_date.isoformat(),
            "sourceType": "opening_balance",
            "dedupFingerprint": f"opening:{company_id}:{fiscal_year}",
            "entryType": "opening",
            "voucherAuthority": "system",
            "voucherLines": {"debit": debit_lines, "credit": credit_lines},
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
                    "id": _ytd_entry_id(company_id, fiscal_year),
                    "direction": "owner_transfer",
                    "amount": balanced_ytd_amount,
                    "title": f"{pyear} 年 1-{pmonth - 1} 月损益累计发生额",
                    "category": "期初余额",
                    "occurDate": ytd_occur_date.isoformat(),
                    "sourceType": "opening_balance_ytd",
                    "dedupFingerprint": f"opening_ytd:{company_id}:{fiscal_year}",
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
                    source_entry_id=_entry_id(company_id, fiscal_year),
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
                    FixedAsset.source_entry_id == _entry_id(company_id, fiscal_year),
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
