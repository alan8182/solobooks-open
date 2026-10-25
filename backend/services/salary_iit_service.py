# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""工资薪金个人所得税（累计预扣法）试算。

工资表里的「个税代扣」原本纯手填：小微企业没有 HR，老板要自己算一个人该扣多少
税，很容易漏掉累计口径导致某个月多扣/少扣。这里按国家税务总局公告 2018 年第 61 号
的累计预扣法给出试算，结果只是建议值，最终以个税扣缴端申报数为准。

口径：
    累计应纳税所得额 = 累计收入 − 累计减除费用 − 累计专项扣除（个人社保公积金）
                       − 累计专项附加扣除 − 累计其他扣除
    本期应预扣 = 累计应纳税额 − 累计已预扣（本年前几个月实际扣的个税）
累计收入/专项扣除/已预扣取自已计提（accrued/paid）的历史工资表，草稿月份不算。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from core.money import ZERO, money
from services.database import get_conn

# 每月基本减除费用（起征点）
BASIC_DEDUCTION = Decimal("5000.00")

# 综合所得预扣率表（累计应纳税所得额，税率，速算扣除数）
PREPAY_BRACKETS: tuple[tuple[Optional[Decimal], Decimal, str], ...] = (
    (Decimal("36000"), Decimal("0.03"), "0"),
    (Decimal("144000"), Decimal("0.10"), "2520"),
    (Decimal("300000"), Decimal("0.20"), "16920"),
    (Decimal("420000"), Decimal("0.25"), "31920"),
    (Decimal("660000"), Decimal("0.30"), "52920"),
    (Decimal("960000"), Decimal("0.35"), "85920"),
    (None, Decimal("0.45"), "181920"),
)

# 已计入成本的工资表状态：只有这些月份的收入/代扣算进累计口径
COUNTED_STATUSES = ("accrued", "paid")


def cumulative_tax(taxable: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """按预扣率表算累计应纳税额，返回 (税额, 税率, 速算扣除数)。"""
    taxable = money(taxable, allow_negative=False)
    if taxable <= ZERO:
        return ZERO, ZERO, ZERO
    for upper, rate, quick in PREPAY_BRACKETS:
        if upper is None or taxable <= upper:
            quick_amount = money(quick)
            return money(taxable * rate - quick_amount), rate, quick_amount
    # 上面的 None 分支必然命中，兜底保持返回形态一致
    return ZERO, ZERO, ZERO


def _month_index(month: str) -> int:
    """YYYY-MM → 顺序月序号，便于算跨年跨度。"""
    year, mon = (int(part) for part in month.split("-"))
    return year * 12 + mon


def _months_inclusive(start_month: str, end_month: str) -> int:
    return max(0, _month_index(end_month) - _month_index(start_month) + 1)


def employment_start_month(month: str, hire_date: Optional[str]) -> str:
    """本年内的起算月：入职晚于 1 月就从入职当月起算减除费用。"""
    year = month.split("-")[0]
    january = f"{year}-01"
    text = str(hire_date or "").strip()
    if len(text) >= 7 and text[:4] == year:
        candidate = text[:7]
        # 入职月晚于目标月份（补发历史工资）时仍按 1 月起算，避免负数月份
        if _month_index(candidate) <= _month_index(month):
            return candidate
    return january


def resolve_start_month(month: str, hire_date: Optional[str], first_sheet_month: Optional[str]) -> str:
    """实际起算月 = max(入职月, 本账套首个已计提工资月)。

    只按 1 月起算会让年中启用的账套凭空多出几个月减除费用（5000×月数），
    把个税算成 0；而 hire_date 经常没人填。所以用「账套里该员工最早一笔已计提
    工资」兜底，没有历史时从当月（1 个月）起算。
    """
    start = employment_start_month(month, hire_date)
    floor = first_sheet_month or month
    return floor if _month_index(floor) > _month_index(start) else start


async def preview_individual_income_tax(
    company_id: str,
    employee_id: str,
    month: str,
    *,
    gross: Any,
    withheld: Any,
    special_additional: Any = None,
    other_deduction: Any = ZERO,
) -> Dict[str, Any]:
    """试算某员工在指定月份应预扣的个税。

    gross            本期应发工资（基本+奖金+津贴−考勤扣款）
    withheld         本期个人社保公积金（专项扣除）
    special_additional 月度专项附加扣除合计；None 时取员工档案
    other_deduction  其他扣除（年金、商业健康险等，月度）
    """
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, name, hire_date, special_additional_deduction FROM employees "
            "WHERE company_id = ? AND id = ?",
            (company_id, employee_id),
        )
        employee = await cursor.fetchone()
        if not employee:
            raise ValueError("员工不存在")

        stored_special = money(employee["special_additional_deduction"] or ZERO, allow_negative=False)
        monthly_special = (
            stored_special if special_additional is None
            else money(special_additional, allow_negative=False)
        )
        monthly_other = money(other_deduction or ZERO, allow_negative=False)

        year = month.split("-")[0]
        january = f"{year}-01"
        statuses = ", ".join("?" for _ in COUNTED_STATUSES)
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(sr.base_salary + sr.bonus + sr.allowance - sr.deduction), 0) AS gross, "
            "       COALESCE(SUM(sr.personal_social_security), 0) AS withheld, "
            "       COALESCE(SUM(sr.personal_tax), 0) AS tax, "
            "       MIN(ss.month) AS first_month "
            "FROM salary_records sr JOIN salary_sheets ss ON ss.id = sr.sheet_id "
            "WHERE ss.company_id = ? AND sr.employee_id = ? "
            f"      AND ss.month >= ? AND ss.month < ? AND ss.status IN ({statuses})",
            (company_id, employee_id, january, month, *COUNTED_STATUSES),
        )
        history = await cursor.fetchone()
    finally:
        await conn.close()

    prior_gross = money(history["gross"] if history else ZERO, allow_negative=False)
    prior_withheld = money(history["withheld"] if history else ZERO, allow_negative=False)
    prior_tax = money(history["tax"] if history else ZERO, allow_negative=False)

    current_gross = money(gross, allow_negative=False)
    current_withheld = money(withheld, allow_negative=False)

    start_month = resolve_start_month(
        month,
        employee["hire_date"],
        str(history["first_month"]) if history and history["first_month"] else None,
    )
    months = _months_inclusive(start_month, month)

    cumulative_income = money(prior_gross + current_gross)
    cumulative_basic = money(BASIC_DEDUCTION * months)
    cumulative_withheld = money(prior_withheld + current_withheld)
    cumulative_special = money(monthly_special * months)
    cumulative_other = money(monthly_other * months)
    taxable = money(
        cumulative_income
        - cumulative_basic
        - cumulative_withheld
        - cumulative_special
        - cumulative_other
    )
    if taxable < ZERO:
        taxable = ZERO

    cumulative_taxable_amount, tax_rate, quick_deduction = cumulative_tax(taxable)
    due = money(cumulative_taxable_amount - prior_tax)
    if due < ZERO:
        # 本期算出来是负数时不退税，结转下期抵减（与扣缴端一致）
        due = ZERO

    return {
        "employeeId": str(employee["id"]),
        "employeeName": str(employee["name"]),
        "month": month,
        "months": months,
        "startMonth": start_month,
        # 没填入职日期时起算月靠工资数据兜底，累计口径可能偏低，前端要提示
        "hireDateMissing": not str(employee["hire_date"] or "").strip(),
        "currentGross": str(current_gross),
        "currentWithheld": str(current_withheld),
        "priorGross": str(prior_gross),
        "priorWithheld": str(prior_withheld),
        "priorTax": str(prior_tax),
        "cumulativeIncome": str(cumulative_income),
        "cumulativeBasicDeduction": str(cumulative_basic),
        "cumulativeSpecialDeduction": str(cumulative_withheld),
        "cumulativeAdditionalDeduction": str(cumulative_special),
        "cumulativeOtherDeduction": str(cumulative_other),
        "taxableIncome": str(taxable),
        "taxRate": str(tax_rate),
        "quickDeduction": str(quick_deduction),
        "cumulativeTax": str(cumulative_taxable_amount),
        "prepaidTax": str(prior_tax),
        "taxDue": str(due),
        "monthlySpecialAdditional": str(monthly_special),
    }
