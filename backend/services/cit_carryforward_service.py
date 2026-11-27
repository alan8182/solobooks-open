# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""企业所得税 / 经营所得超限额费用跨年结转台账（确定性滚动引擎）。

支持 3 项可跨年结转税前扣除限额费用：
1. 广告费和业务宣传费（A105000-16）：无限期结转（企业所得税 15% / 个体户及个独 15%）
2. 职工教育经费（A105000-04）：无限期结转（企业所得税 8% / 个体户及个独 2.5%）
3. 公益性捐赠支出（A105000-17）：仅企业所得税，3 年结转期限，按来源年度 FIFO 优先抵扣
   最早年度未超期结转额（国家税务总局公告 2018 年第 15 号）；超 3 年未抵完自动作废。

设计约束：
- 零新增表，起始余额取自期初税务备忘（CompanyProfile.profile["taxOpening"]），
  之后按历年已过账有票分录逐年滚动派生，历史年度反结账调整后自动重算。
- 无票支出绝不进入结转池。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from core.money import ZERO, money
from services.cit_adjustment_service import (
    ADVERTISING_REVENUE_CAP,
    DONATION_PROFIT_CAP_CIT,
    EDUCATION_SALARY_CAP_CIT,
    EDUCATION_SALARY_CAP_IIT,
    _compute_salary_base,
    _entry_amount,
    _entry_kind,
    _is_fine,
    _missing_document,
)
from services.cit_tax_opening import tax_opening_fiscal_year

DONATION_CARRYFORWARD_YEARS = 3
_REVENUE_CODES = frozenset({"5001", "5051"})


def normalize_opening_carryforward(memo: dict[str, Any] | None) -> dict[str, Any]:
    """标准化期初税务备忘里的跨年结转余额（负数/脏数据按 0 或忽略处理）。"""
    data = memo or {}
    advertising = max(ZERO, money(data.get("carryforwardAdvertising") or ZERO))
    education = max(ZERO, money(data.get("carryforwardEducation") or ZERO))

    merged_buckets: dict[int, Decimal] = {}
    for raw_item in data.get("carryforwardDonations") or []:
        if not isinstance(raw_item, dict):
            continue
        raw_year = raw_item.get("year")
        try:
            origin_year = int(raw_year)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        amt = max(ZERO, money(raw_item.get("amount") or ZERO))
        if amt <= ZERO:
            continue
        merged_buckets[origin_year] = money(merged_buckets.get(origin_year, ZERO) + amt)

    donations = [
        {"year": yr, "amount": amt}
        for yr, amt in sorted(merged_buckets.items())
    ]
    return {
        "advertising": advertising,
        "education": education,
        "donations": donations,
    }


def _is_closing_entry(entry: dict[str, Any]) -> bool:
    if str(entry.get("sourceType") or "") == "closing":
        return True
    lines = entry.get("voucherLines") or {}
    for side in ("debit", "credit"):
        for line in lines.get(side) or []:
            if str(line.get("accountCode") or "") == "3103":
                return True
    return False


def _derive_year_pnl(entries: list[dict[str, Any]]) -> dict[str, Decimal]:
    """从某年已过账分录推算营业收入与利润总额（排除期末损益结转凭证）。"""
    revenue = ZERO
    total_profit = ZERO
    for entry in entries:
        if _is_closing_entry(entry):
            continue
        lines = entry.get("voucherLines") or {}
        debit_lines = lines.get("debit") or []
        credit_lines = lines.get("credit") or []
        if debit_lines or credit_lines:
            for side, sign in (("credit", 1), ("debit", -1)):
                for line in lines.get(side) or []:
                    code = str(line.get("accountCode") or "")
                    amt = money(line.get("amount") or ZERO)
                    if code in _REVENUE_CODES:
                        revenue = money(revenue + sign * amt)
                    if code.startswith("5") and not code.startswith("5801"):
                        total_profit = money(total_profit + sign * amt)
        else:
            amt = _entry_amount(entry)
            direction = entry.get("direction")
            if direction == "income":
                revenue = money(revenue + amt)
                total_profit = money(total_profit + amt)
            elif direction == "expense":
                total_profit = money(total_profit - amt)
    return {
        "revenue": max(ZERO, revenue),
        "total_profit": total_profit,
    }


def _documented_totals(entries: list[dict[str, Any]]) -> dict[str, Decimal]:
    totals = {"advertising": ZERO, "education": ZERO, "donation": ZERO}
    for entry in entries:
        if entry.get("direction") != "expense":
            continue
        amount = _entry_amount(entry)
        if amount <= ZERO or _is_fine(entry) or _missing_document(entry):
            continue
        kind = _entry_kind(entry)
        if kind in totals:
            totals[kind] = money(totals[kind] + amount)
    return totals


def _roll_unlimited(
    *,
    code: str,
    kind: str,
    label: str,
    opening: Decimal,
    current: Decimal,
    cap: Decimal,
) -> dict[str, Any]:
    if current >= cap:
        excess = money(current - cap)
        deducted = ZERO
        closing = money(opening + excess)
    else:
        room = money(cap - current)
        deducted = min(opening, room)
        excess = ZERO
        closing = money(opening - deducted)
    return {
        "code": code,
        "kind": kind,
        "label": label,
        "openingBalance": money(opening),
        "expiredAmount": ZERO,
        "currentAmount": money(current),
        "currentCap": money(cap),
        "deductedFromPrior": money(deducted),
        "currentExcess": money(excess),
        "closingBalance": money(closing),
        "buckets": [],
    }


def _roll_donation_fifo(
    *,
    year: int,
    buckets: list[dict[str, Any]],
    current: Decimal,
    cap: Decimal,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    valid_buckets: list[dict[str, Any]] = []
    expired = ZERO
    for bucket in sorted(buckets, key=lambda b: int(b["year"])):
        origin_year = int(bucket["year"])
        amt = money(bucket["amount"])
        if amt <= ZERO:
            continue
        if year - origin_year > DONATION_CARRYFORWARD_YEARS:
            expired = money(expired + amt)
        else:
            valid_buckets.append({"year": origin_year, "amount": amt})

    opening = money(sum((b["amount"] for b in valid_buckets), ZERO))
    rem_cap = max(ZERO, money(cap))
    deducted = ZERO
    next_buckets: list[dict[str, Any]] = []

    for bucket in valid_buckets:
        used = min(bucket["amount"], rem_cap)
        deducted = money(deducted + used)
        rem_cap = money(rem_cap - used)
        left = money(bucket["amount"] - used)
        if left > ZERO:
            next_buckets.append({"year": bucket["year"], "amount": left})

    allowed_current = min(current, rem_cap)
    excess = money(current - allowed_current)
    if excess > ZERO:
        next_buckets.append({"year": year, "amount": excess})

    closing = money(sum((b["amount"] for b in next_buckets), ZERO))
    row = {
        "code": "A105000-17",
        "kind": "donation",
        "label": "公益性捐赠支出",
        "openingBalance": opening,
        "expiredAmount": expired,
        "currentAmount": money(current),
        "currentCap": money(cap),
        "deductedFromPrior": deducted,
        "currentExcess": excess,
        "closingBalance": closing,
        "buckets": next_buckets,
    }
    return row, next_buckets


def compute_carryforward_ledger(
    all_entries: list[dict[str, Any]],
    target_year: int,
    target_profit: dict[str, Any],
    *,
    entity_type: str = "ltd",
    opening_memo: dict[str, Any] | None = None,
    target_salary_base: Decimal | None = None,
    target_exclude_salary: Decimal = ZERO,
    target_period_end: Any = None,
) -> dict[str, Any]:
    """从期初备忘或最早记账年度逐年滚算至 ``target_year``，生成跨年结转台账。"""
    is_iit = entity_type != "ltd"
    memo_year = tax_opening_fiscal_year(opening_memo)
    opening_cf = normalize_opening_carryforward(opening_memo)
    has_opening_cf = (
        opening_cf["advertising"] > ZERO
        or opening_cf["education"] > ZERO
        or bool(opening_cf["donations"])
    )

    entries_by_year: dict[int, list[dict[str, Any]]] = {}
    for entry in all_entries:
        occurred = str(entry.get("occurDate") or "")[:10]
        if len(occurred) < 4 or not occurred[:4].isdigit():
            continue
        yr = int(occurred[:4])
        if yr > target_year:
            continue
        if yr == target_year and target_period_end and occurred > str(target_period_end)[:10]:
            continue
        entries_by_year.setdefault(yr, []).append(entry)

    if has_opening_cf and memo_year is not None and memo_year <= target_year:
        start_year = memo_year
        adv_bal = opening_cf["advertising"]
        edu_bal = opening_cf["education"]
        don_buckets = list(opening_cf["donations"])
    else:
        start_year = min(entries_by_year.keys(), default=target_year)
        adv_bal = ZERO
        edu_bal = ZERO
        don_buckets = []

    target_items: list[dict[str, Any]] = []
    for year in range(start_year, target_year + 1):
        year_entries = entries_by_year.get(year, [])
        documented = _documented_totals(year_entries)

        if year == target_year:
            revenue = max(ZERO, money(target_profit.get("revenue") or ZERO))
            total_profit = money(target_profit.get("total_profit") or ZERO)
            salary_base = (
                max(ZERO, money(target_salary_base))
                if target_salary_base is not None
                else _compute_salary_base(year_entries, exclude_salary=target_exclude_salary)
            )
            cur_adv = documented["advertising"] if year_entries else max(ZERO, money(target_profit.get("advertising") or ZERO))
            cur_edu = documented["education"] if year_entries else max(ZERO, money(target_profit.get("education_fee") or ZERO))
            cur_don = documented["donation"] if year_entries else max(ZERO, money(target_profit.get("donation") or ZERO))
        else:
            pnl = _derive_year_pnl(year_entries)
            revenue = pnl["revenue"]
            total_profit = pnl["total_profit"]
            salary_base = _compute_salary_base(year_entries)
            cur_adv = documented["advertising"]
            cur_edu = documented["education"]
            cur_don = documented["donation"]

        adv_row = _roll_unlimited(
            code="A105000-16",
            kind="advertising",
            label="广告费和业务宣传费",
            opening=adv_bal,
            current=cur_adv,
            cap=money(revenue * ADVERTISING_REVENUE_CAP),
        )
        adv_bal = adv_row["closingBalance"]

        edu_rate = EDUCATION_SALARY_CAP_IIT if is_iit else EDUCATION_SALARY_CAP_CIT
        edu_row = _roll_unlimited(
            code="A105000-04",
            kind="education",
            label="职工教育经费",
            opening=edu_bal,
            current=cur_edu,
            cap=money(salary_base * edu_rate),
        )
        edu_bal = edu_row["closingBalance"]

        year_items = [adv_row, edu_row]
        if not is_iit:
            don_row, don_buckets = _roll_donation_fifo(
                year=year,
                buckets=don_buckets,
                current=cur_don,
                cap=money(max(ZERO, total_profit) * DONATION_PROFIT_CAP_CIT),
            )
            year_items.append(don_row)

        if year == target_year:
            target_items = year_items

    by_kind = {item["kind"]: item for item in target_items}
    total_decrease = money(sum((item["deductedFromPrior"] for item in target_items), ZERO))
    has_activity = any(
        item["openingBalance"] > ZERO
        or item["deductedFromPrior"] > ZERO
        or item["currentExcess"] > ZERO
        or item["closingBalance"] > ZERO
        or item["expiredAmount"] > ZERO
        for item in target_items
    )
    return {
        "fiscalYear": target_year,
        "items": target_items,
        "byKind": by_kind,
        "totalDecrease": total_decrease,
        "hasActivity": has_activity,
    }
