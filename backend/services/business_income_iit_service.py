# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""经营所得个人所得税（个人独资企业 / 个体工商户）测算引擎。

适用口径（与计划文档 plans/business-income-iit-plan.md 一致）：

- **只服务查账征收**；核定征收（定期定额/核定应税所得率）不属于服务范围。
- 计税依据 = 收入总额 − 成本费用 − 损失（P1 直接取利润表累计利润总额，
  纳税调整——业主工资调增、无票支出等——属 P2 汇算范围）。
- 税率：5%–35% 五级超额累进 + 速算扣除数（经营所得税率表，
  **不是**综合所得税率表）。
- 减半：个体工商户年应纳税所得额不超过 200 万元的部分减半征收
  （财政部 税务总局公告 2023 年第 12 号，政策执行至 2027-12-31）；
  **个人独资企业不享受减半**。
- 预缴按**累计**应纳税所得额取税率表（国家税务总局公告 2019 年第 46 号
  预缴口径），不是单期独立计算。
- 预缴 A 表期限：月报次月 15 日内 / 季报季终后 15 日内；
  汇算 B 表：次年 3 月 31 日前（与企税 5-31 无关）。

计算结果是申报底稿的"系统测算默认值"，正式申报以电子税务局为准。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from core.money import ZERO, money
from services.company_entity_service import (
    ENTITY_INDIVIDUAL_BUSINESS,
    ENTITY_LTD,
    normalize_entity_type,
)

# 经营所得个人所得税税率表（年应纳税所得额，元）
# (级距上限 None=无上限, 税率, 速算扣除数)
PROGRESSIVE_BRACKETS: tuple[tuple[Decimal | None, Decimal, Decimal], ...] = (
    (Decimal("30000"), Decimal("0.05"), ZERO),
    (Decimal("90000"), Decimal("0.10"), Decimal("1500")),
    (Decimal("300000"), Decimal("0.20"), Decimal("10500")),
    (Decimal("500000"), Decimal("0.30"), Decimal("40500")),
    (None, Decimal("0.35"), Decimal("65500")),
)

# 个体工商户经营所得减半：年应纳税所得额不超过 200 万的部分（2023-12 号公告）
HALVING_CAP = Decimal("2000000")
HALVING_POLICY_END = date(2027, 12, 31)

INCOME_ACCOUNT_CODES = ("5001", "5051")


@dataclass
class BusinessIncomeTaxResult:
    taxable_income: Decimal
    tax: Decimal
    halving_reduction: Decimal
    rate: Decimal
    bracket_label: str
    halving_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "taxableIncome": self.taxable_income,
            "tax": self.tax,
            "halvingReduction": self.halving_reduction,
            "rate": self.rate,
            "bracketLabel": self.bracket_label,
            "halvingApplied": self.halving_applied,
        }


def progressive_tax(taxable_income: Decimal) -> Decimal:
    """按年应纳税所得额计算经营所得税额（五级超额累进 + 速算扣除数）。"""
    taxable = money(max(Decimal("0"), taxable_income))
    for cap, rate, quick_deduction in PROGRESSIVE_BRACKETS:
        if cap is None or taxable <= cap:
            return money(taxable * rate - quick_deduction)
    return ZERO


def halving_eligible(entity_type: str, as_of: date | None = None) -> bool:
    """个体工商户且在减半政策执行期内（至 2027-12-31）。个人独资企业不享受。"""
    normalized = normalize_entity_type(entity_type)
    if normalized != ENTITY_INDIVIDUAL_BUSINESS:
        return False
    if as_of is None:
        as_of = date.today()
    return as_of <= HALVING_POLICY_END


def _bracket_label(taxable: Decimal) -> tuple[Decimal, str]:
    for cap, rate, quick in PROGRESSIVE_BRACKETS:
        if cap is None or taxable <= cap:
            upper = "以上" if cap is None else f"{cap:,.0f}"
            return rate, f"不超过 {upper} 元部分" if cap is not None else f"超过 500,000 元部分（{rate:.0%}）"
    return PROGRESSIVE_BRACKETS[-1][1], ""


def tax_payable(
    taxable_income: Decimal,
    entity_type: str,
    as_of: date | None = None,
) -> BusinessIncomeTaxResult:
    """应纳税额：全额计税；个体户对 ≤200 万部分的对应税额减半。

    减半的精确实现：应纳税所得额超过 200 万时，超过部分对应的税额
    （全额税 − 200 万级距税）不减半，200 万以内部分对应的税额减半。
    """
    taxable = money(max(Decimal("0"), taxable_income))
    full_tax = progressive_tax(taxable)
    if full_tax <= ZERO:
        rate, label = PROGRESSIVE_BRACKETS[0][1], "不超过 30,000 元部分"
        return BusinessIncomeTaxResult(taxable, ZERO, ZERO, rate, label, False)

    if not halving_eligible(entity_type, as_of):
        rate, label = _bracket_label(taxable)
        return BusinessIncomeTaxResult(taxable, full_tax, ZERO, rate, label, False)

    if taxable <= HALVING_CAP:
        tax = money(full_tax * Decimal("0.5"))
    else:
        tax_at_cap = progressive_tax(HALVING_CAP)
        tax = money(tax_at_cap * Decimal("0.5") + (full_tax - tax_at_cap))
    rate, label = _bracket_label(taxable)
    return BusinessIncomeTaxResult(taxable, tax, money(full_tax - tax), rate, label, True)


async def build_prepayment_assist(user_id: str, company_id: str, period: str) -> dict[str, Any]:
    """经营所得 A 表预缴作业包（累计口径）。

    period 支持 ``YYYY-MM``（月报）与 ``YYYY-Qn``（季报）——两种口径的
    ``profit_ytd`` 都是「年初至本期末」的累计数，符合 46 号公告的
    累计预缴法。已预缴税额 P1 无数据源，由用户按上期申报回执确认。
    """
    from services.tax_filing_export_service import build_statement_data, parse_period

    entity_type = await _entity_type(user_id, company_id)
    if normalize_entity_type(entity_type) == ENTITY_LTD:
        return {"applicable": False, "reason": "entity_type_not_applicable"}

    period_type = "quarter" if "Q" in (period or "").upper() else "month"
    statement = await build_statement_data(user_id, company_id, period, period_type)
    period_range = parse_period(period, period_type)

    # 收入取不含税口径（与利润表一致），成本费用为损益支出合计
    revenue = money(statement.profit_ytd.get("revenue") or ZERO)
    profit_total = money(statement.profit_ytd.get("total_profit") or ZERO)
    net_profit = money(statement.profit_ytd.get("net_profit") or ZERO)
    expenses = money(revenue - profit_total)

    # 计税依据 = 收入总额 − 成本费用 − 损失 ≈ 利润总额（P1 无纳税调整）
    # 不为负（亏损时可结转弥补，预缴不退税）
    taxable = max(Decimal("0"), profit_total)
    result = tax_payable(taxable, entity_type, period_range.end)

    cycle = "quarter" if period_type == "quarter" else "month"
    return {
        "applicable": True,
        "period": period,
        "periodType": period_type,
        "filingCycle": cycle,
        "entityType": normalize_entity_type(entity_type),
        "revenue": revenue,
        "expenses": expenses,
        "profitTotal": profit_total,
        "netProfit": net_profit,
        **result.as_dict(),
        "previouslyPaid": ZERO,
        "suggestedPayable": money(max(Decimal("0"), result.tax)),
        "notes": _notes(entity_type, taxable),
    }


def _notes(entity_type: str, taxable: Decimal) -> list[str]:
    label = "个体工商户" if normalize_entity_type(entity_type) == ENTITY_INDIVIDUAL_BUSINESS else "个人独资企业"
    notes = [
        "本卡按查账征收口径测算；核定征收（定期定额/核定应税所得率）不适用本系统。",
        "累计应纳税所得额为系统按账面利润测算，业主本人工资不得税前扣除，"
        "汇算时需调增（B 表汇算作业包见本卡下方）。",
        "已预缴税额请按上期申报回执填写核对。",
        MULTI_SOURCE_C_TABLE_NOTE,
    ]
    if halving_eligible(entity_type):
        notes.append(
            f"{label}年应纳税所得额不超过 200 万元的部分减半征收（政策至 2027-12-31），"
            "已按减半口径测算。"
        )
    elif normalize_entity_type(entity_type) == ENTITY_INDIVIDUAL_BUSINESS:
        notes.append("累计应纳税所得额已超 200 万元，超出部分不减半。")
    else:
        notes.append("个人独资企业不适用「200 万以下减半」优惠，已按全额测算。")
    return notes


async def _entity_type(user_id: str, company_id: str) -> str:
    from services.account_service import get_company

    payload = await get_company(company_id) or {}
    return normalize_entity_type(payload.get("type"))


def annual_due_date(fiscal_year: int) -> date:
    """B 表汇算清缴截止：次年 3 月 31 日。与企税 5-31 无关，不可复用。"""
    return date(fiscal_year + 1, 3, 31)


# ── B 表：年度汇算清缴 ──


async def _annual_expense_scan(
    company_id: str, fiscal_year: int
) -> tuple[Decimal, list[dict[str, Any]], Decimal]:
    """年度费用扫描：返回（无票支出合计, 无票明细, 人力成本合计）。

    无票支出判定复用 CIT 底稿同一套缺凭证逻辑，口径同源不二次发明；
    人力成本按 sourceType 精确筛选：工资模块四张凭证（salary_accrual /
    salary_pay / salary_social / salary_iit）的 category 都是"人力成本"，
    只有 sourceType="salary_pay" 才是真正的工资发放额。若按 category 累加，
    计提与缴纳凭证会被重复计入，基数成倍虚高。
    手工录入的"人力成本"凭证（无 sourceType 或 sourceType=manual）
    仍按原口径计入。
    """
    from db.session import session_scope
    from services.accounting_core_service import posted_entries_for_reports
    from services.cit_adjustment_service import _entry_amount, _entry_text, _in_period, _missing_document

    async with session_scope() as session:
        entries = await posted_entries_for_reports(session, company_id)
    undocumented = ZERO
    detail: list[dict[str, Any]] = []
    salary_paid = ZERO
    for entry in entries:
        if entry.get("direction") != "expense":
            continue
        if not _in_period(entry, f"{fiscal_year}-01-01", f"{fiscal_year}-12-31"):
            continue
        amount = _entry_amount(entry)
        if amount <= ZERO:
            continue
        source_type = _entry_text(entry, "sourceType")
        if source_type == "salary_pay":
            salary_paid += amount
        elif source_type in ("", "manual") and _entry_text(entry, "category") == "人力成本":
            salary_paid += amount
        if _missing_document(entry):
            undocumented += amount
            detail.append({
                "entryId": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "occurDate": str(entry.get("occurDate") or "")[:10],
                "amount": amount,
            })
    return money(undocumented), detail, money(salary_paid)


# C 表：两处以上取得经营所得需合并申报（系统无外部数据源，仅提示）
MULTI_SOURCE_C_TABLE_NOTE = (
    "如果您在两处以上取得经营所得（如多家个体户/个独），年度需合并各处所得"
    "办理汇总年度申报（C 表），本表仅覆盖单一主体的所得。"
)


async def build_annual_settlement_assist(
    user_id: str,
    company_id: str,
    fiscal_year: int,
    *,
    owner_salary: Decimal | None = None,
    statutory_deduction: Decimal | None = None,
    special_deductions: Decimal = ZERO,
    special_additional_deductions: Decimal = ZERO,
    other_deductions: Decimal = ZERO,
    prepaid_total: Decimal = ZERO,
) -> dict[str, Any]:
    """经营所得 B 表汇算清缴作业包（年度口径）。

    应纳税所得额 = 利润总额 + 纳税调增（业主工资不得扣除、无票支出）− 扣除
    （减除费用 6 万/年、专项扣除、专项附加扣除、其他）。

    业主工资来源（自动优先、手工兜底）：
    - ``owner_salary`` 显式传入 → 手工确认口径（ownerSalarySource=manual）；
    - 未传入 → 自动取名册 is_owner 员工的年度应发工资合计
      （ownerSalarySource=auto）；名册未标记业主时为 0，卡片提示去标记。
    """
    from services.account_service import get_company
    from services.tax_filing_export_service import build_statement_data

    entity_type = normalize_entity_type((await get_company(company_id) or {}).get("type"))
    if entity_type == ENTITY_LTD:
        return {"applicable": False, "reason": "entity_type_not_applicable"}

    # 年度累计 = Q4 季度视图的 profit_ytd（fiscal_year_start..12-31）
    statement = await build_statement_data(user_id, company_id, f"{fiscal_year}-Q4", "quarter")
    revenue = money(statement.profit_ytd.get("revenue") or ZERO)
    profit_total = money(statement.profit_ytd.get("total_profit") or ZERO)

    undocumented, undocumented_detail, salary_paid = await _annual_expense_scan(company_id, fiscal_year)

    owner_salary_source = "manual"
    if owner_salary is None:
        owner_salary_source = "auto"
        owner_salary = await _auto_owner_salary(company_id, fiscal_year)
    owner_salary = money(max(Decimal("0"), owner_salary or ZERO))

    addbacks = owner_salary + undocumented
    statutory = money(Decimal("60000") if statutory_deduction is None else max(Decimal("0"), statutory_deduction))
    deductions = money(
        statutory
        + max(Decimal("0"), special_deductions or ZERO)
        + max(Decimal("0"), special_additional_deductions or ZERO)
        + max(Decimal("0"), other_deductions or ZERO)
    )

    taxable = max(Decimal("0"), money(profit_total + addbacks - deductions))
    result = tax_payable(taxable, entity_type, date(fiscal_year, 12, 31))
    prepaid = money(max(Decimal("0"), prepaid_total or ZERO))
    balance = money(result.tax - prepaid)

    notes = [
        "应纳税所得额 = 账面利润 + 业主工资等纳税调增 − 减除费用与专项（附加）扣除，"
        "最终以电子税务局 B 表申报为准。",
        "业主/投资者本人的工资不得税前扣除，须全额调增；下方工资总额为账面全年发放数，"
        "请确认其中业主本人的部分后填入调增额。",
        MULTI_SOURCE_C_TABLE_NOTE,
    ]
    if statutory_deduction is None:
        notes.append(
            "已默认扣除减除费用 6 万/年；若业主已在工资薪金综合所得中扣除过 6 万，"
            "本表不得重复扣除，请把减除费用改为 0。"
        )
    if undocumented > ZERO:
        notes.append(
            f"年度无票支出 {undocumented} 元已全额调增，取得合规凭证后可调减。"
        )
    if profit_total < ZERO:
        notes.append(
            "账面亏损：经营所得可按规定结转以后年度弥补（最长 5 年），本年度预缴部分可申请退税。"
        )
    if owner_salary_source == "auto":
        if owner_salary > ZERO:
            notes.append(
                f"业主工资调增 {owner_salary} 元来自名册「业主/投资者」标记的年度应发工资"
                "（基本工资+奖金+津贴），如与实际不符请手工修改。"
            )
        else:
            notes.append(
                "名册中没有标记「业主/投资者」的员工，业主工资调增暂按 0；"
                "请在员工名册中标记业主后重新生成，或直接手工填入调增额。"
            )

    return {
        "applicable": True,
        "fiscalYear": fiscal_year,
        "entityType": entity_type,
        "revenue": revenue,
        "profitTotal": profit_total,
        "salaryPaidTotal": salary_paid,
        "adjustments": {
            "ownerSalary": owner_salary,
            "ownerSalarySource": owner_salary_source,
            "undocumentedExpenses": undocumented,
            "undocumentedDetail": undocumented_detail,
            "total": addbacks,
        },
        "deductions": {
            "statutory": statutory,
            "specialDeductions": money(max(Decimal("0"), special_deductions or ZERO)),
            "specialAdditionalDeductions": money(max(Decimal("0"), special_additional_deductions or ZERO)),
            "otherDeductions": money(max(Decimal("0"), other_deductions or ZERO)),
            "total": deductions,
        },
        "taxableIncome": taxable,
        **result.as_dict(),
        "prepaidTotal": prepaid,
        "finalPayable": max(Decimal("0"), balance),
        "finalRefund": max(Decimal("0"), -balance),
        "notes": notes,
    }


async def _auto_owner_salary(company_id: str, fiscal_year: int) -> Decimal:
    """名册业主标记的年度应发工资合计（salary_service 异常时降级为 0）。"""
    try:
        from services.salary_service import sum_owner_salary_cost

        return await sum_owner_salary_cost(company_id, fiscal_year)
    except Exception:  # noqa: BLE001 —— 名册缺失/表结构旧不拖垮 B 表测算
        return ZERO
