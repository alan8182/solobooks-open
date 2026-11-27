# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""企业所得税 / 经营所得纳税调整的确定性计算（A105000 简化口径）。

会计利润 ≠ 应纳税所得额。税会差异里最常见、且完全可以从账上算出来的几项：
- 业务招待费：按发生额 60% 扣除，且不超过当年销售（营业）收入的 5‰（实施条例第 43 条）
- 广告费和业务宣传费：不超过当年销售（营业）收入 15%，超出部分结转以后年度（第 44 条）
- 职工福利费：不超过工资薪金总额 14%（实施条例第 40 条 / 35 号令第 27 条）
- 工会经费：不超过工资薪金总额 2%（实施条例第 41 条 / 35 号令第 27 条）
- 职工教育经费：企业不超过工资薪金总额 8%（财税〔2018〕51 号），个体户/个独不超过 2.5%（35 号令第 27 条），超出部分结转以后年度
- 公益性捐赠支出：企业不超过年度利润总额 12%（企税法第 9 条，超额可结转 3 年），个体户/个独不超过应纳税所得额 30%（财税〔2019〕99 号）
- 罚款、滞纳金：不得扣除（企业所得税法第 10 条）
- 无票支出：账上已计费用但没有合规扣除凭证，汇算时必须调增

这些数字必须由规则算出，不能交给 LLM 猜——税率与扣除限额是法定值。
计算结果作为 A200000 第 19-24 行的"系统测算默认值"（以及 B 表汇算默认调增），用户仍可修改并确认。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.money import ZERO, money

ENTERTAINMENT_RATE = Decimal("0.6")
ENTERTAINMENT_REVENUE_CAP = Decimal("0.005")  # 5‰
ADVERTISING_REVENUE_CAP = Decimal("0.15")
WELFARE_SALARY_CAP = Decimal("0.14")
UNION_SALARY_CAP = Decimal("0.02")
EDUCATION_SALARY_CAP_CIT = Decimal("0.08")
EDUCATION_SALARY_CAP_IIT = Decimal("0.025")
DONATION_PROFIT_CAP_CIT = Decimal("0.12")
DONATION_TAXABLE_CAP_IIT = Decimal("0.30")
SMALL_MICRO_CIT_RATE = Decimal("0.05")
STANDARD_CIT_RATE = Decimal("0.25")

SALARY_GROSS_CREDIT_CODES = frozenset({"221101", "221104", "2221012"})
WELFARE_ACCOUNT_CODES = frozenset({"5602014", "221103"})

FINE_KEYWORDS = ("罚款", "罚金", "滞纳金")
WELFARE_KEYWORDS = ("福利费", "员工福利", "职工福利")
UNION_KEYWORDS = ("工会经费", "工会费")
EDUCATION_KEYWORDS = ("教育经费", "职工教育", "员工培训")
DONATION_KEYWORDS = ("捐赠",)
ENTERTAINMENT_KEYWORDS = ("招待", "餐饮")
ADVERTISING_KEYWORDS = ("广告", "宣传", "推广")


@dataclass
class CitAdjustmentItem:
    code: str
    label: str
    amount: Decimal
    basis: str
    detail: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "amount": money(self.amount),
            "basis": self.basis,
            "detail": self.detail,
        }


def _entry_amount(entry: dict[str, Any]) -> Decimal:
    return money(entry.get("amount") or ZERO)


def _entry_text(entry: dict[str, Any], *keys: str) -> str:
    return " ".join(str(entry.get(key) or "") for key in keys)


def _is_fine(entry: dict[str, Any]) -> bool:
    text = _entry_text(entry, "category", "title")
    return any(keyword in text for keyword in FINE_KEYWORDS)


def _missing_document(entry: dict[str, Any]) -> bool:
    if entry.get("direction") != "expense":
        return False
    billing = str(entry.get("billingStatus") or "")
    if billing in {"has_invoice", "has_receipt", "reconciled", "issued"}:
        return False
    if entry.get("invoiceType") and entry.get("invoiceNumber"):
        return False
    return True


def _entry_kind(entry: dict[str, Any]) -> str:
    """识别限额费用类别：welfare / union / education / donation / entertainment / advertising / other。"""
    debit_lines = (entry.get("voucherLines") or {}).get("debit") or []
    for line in debit_lines:
        if str(line.get("accountCode") or "") in WELFARE_ACCOUNT_CODES:
            return "welfare"
    text = _entry_text(entry, "category", "title")
    if any(keyword in text for keyword in WELFARE_KEYWORDS):
        return "welfare"
    if any(keyword in text for keyword in UNION_KEYWORDS):
        return "union"
    if any(keyword in text for keyword in EDUCATION_KEYWORDS):
        return "education"
    if any(keyword in text for keyword in DONATION_KEYWORDS):
        return "donation"
    if any(keyword in text for keyword in ENTERTAINMENT_KEYWORDS):
        return "entertainment"
    if any(keyword in text for keyword in ADVERTISING_KEYWORDS):
        return "advertising"
    return "other"


def _compute_salary_base(
    entries: list[dict[str, Any]],
    period_start: Any = None,
    period_end: Any = None,
    exclude_salary: Decimal = ZERO,
) -> Decimal:
    """提取合理应发工资薪金总额，作为福利费/工会经费/教育经费的税前扣除基数。

    - 工资计提凭证（sourceType="salary_accrual"）：从贷方 221101（应付工资）+
      221104（代扣社保公积金）+ 2221012（代扣个税）汇总应发工资，自动剔除
      221102（单位社保）；无分录明细行时回退取 entry.amount。
    - 手工人力成本凭证（无 sourceType 或 manual 且 category="人力成本"）：直接计入。
    - 跳过 salary_pay / salary_social / salary_iit，防止重复统计。
    - 个体户/个独场景通过 ``exclude_salary`` 扣减业主本人工资（不得作为扣除基数）。
    """
    gross = ZERO
    for entry in entries:
        if entry.get("direction") != "expense" or not _in_period(entry, period_start, period_end):
            continue
        source_type = str(entry.get("sourceType") or "")
        if source_type == "salary_accrual":
            credit_lines = (entry.get("voucherLines") or {}).get("credit") or []
            matched = sum(
                (money(line.get("amount") or ZERO)
                 for line in credit_lines
                 if str(line.get("accountCode") or "") in SALARY_GROSS_CREDIT_CODES),
                ZERO,
            )
            gross += matched if matched > ZERO else _entry_amount(entry)
        elif source_type in ("", "manual") and str(entry.get("category") or "") == "人力成本":
            gross += _entry_amount(entry)
    return max(ZERO, money(gross - max(ZERO, money(exclude_salary))))


def _in_period(entry: dict[str, Any], period_start: Any, period_end: Any) -> bool:
    occurred = str(entry.get("occurDate") or "")[:10]
    if period_start and occurred and occurred < str(period_start)[:10]:
        return False
    if period_end and occurred and occurred > str(period_end)[:10]:
        return False
    return True


def build_cit_adjustments(
    entries: list[dict[str, Any]],
    profit: dict[str, Any],
    period_start: Any = None,
    period_end: Any = None,
    *,
    entity_type: str = "ltd",
    salary_base: Decimal | None = None,
    exclude_salary: Decimal = ZERO,
    donation_base: Decimal | None = None,
    pre_deductions: Decimal = ZERO,
    carryforward_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据已过账分录与利润表科目余额计算纳税调增明细。

    ``entries`` 需要是已过账的权威分录（posted_entries_for_reports 的输出）。
    无分录上下文（entries 为空）时退化为仅按利润表科目余额测算限额类调增。
    """
    is_iit = entity_type != "ltd"
    revenue = money(profit.get("revenue") or ZERO)
    profit_entertainment = money(profit.get("entertainment") or ZERO)
    profit_advertising = money(profit.get("advertising") or ZERO)
    effective_salary_base = (
        max(ZERO, money(salary_base))
        if salary_base is not None
        else _compute_salary_base(entries, period_start, period_end, exclude_salary=exclude_salary)
    )

    # 逐笔区分"有票"与"无票"：
    # - 有票的限额类费用走限额规则；无票的全额不得扣除，不再叠加限额（避免重复调增）
    documented: dict[str, Decimal] = {
        "entertainment": ZERO,
        "advertising": ZERO,
        "welfare": ZERO,
        "union": ZERO,
        "education": ZERO,
        "donation": ZERO,
    }
    undocumented = ZERO
    fine_amount = ZERO
    fine_detail: list[dict[str, Any]] = []
    no_document_detail: list[dict[str, Any]] = []

    for entry in entries:
        if entry.get("direction") != "expense" or not _in_period(entry, period_start, period_end):
            continue
        amount = _entry_amount(entry)
        if amount == ZERO:
            continue
        kind = _entry_kind(entry)
        is_fine = _is_fine(entry)
        if is_fine:
            fine_amount += amount
            fine_detail.append({
                "entryId": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "occurDate": str(entry.get("occurDate") or "")[:10],
                "amount": amount,
            })
            continue
        if _missing_document(entry):
            undocumented += amount
            no_document_detail.append({
                "entryId": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "occurDate": str(entry.get("occurDate") or "")[:10],
                "amount": amount,
            })
            continue
        if kind in documented:
            documented[kind] += amount

    items: list[CitAdjustmentItem] = []

    # 1. 业务招待费：按发生额 60% 扣除，且不超过当年销售（营业）收入 5‰
    entertainment = documented["entertainment"] if entries else profit_entertainment
    if entertainment > ZERO:
        allowed = min(
            money(entertainment * ENTERTAINMENT_RATE),
            money(revenue * ENTERTAINMENT_REVENUE_CAP),
        )
        increase = money(entertainment - allowed)
        if increase > ZERO:
            items.append(CitAdjustmentItem(
                code="A105000-14",
                label="业务招待费超限额调增",
                amount=increase,
                basis=(
                    f"有票发生额 {entertainment}，可扣除 "
                    f"min(60%={money(entertainment * ENTERTAINMENT_RATE)}, 收入5‰={money(revenue * ENTERTAINMENT_REVENUE_CAP)})={allowed}"
                ),
            ))

    # 2. 广告费和业务宣传费：收入 15% 限额，超出部分结转以后年度
    advertising = documented["advertising"] if entries else profit_advertising
    if advertising > ZERO:
        allowed = money(revenue * ADVERTISING_REVENUE_CAP)
        increase = money(advertising - allowed)
        if increase > ZERO:
            items.append(CitAdjustmentItem(
                code="A105000-16",
                label="广告费和业务宣传费超限额调增（可结转）",
                amount=increase,
                basis=f"有票发生额 {advertising}，限额 收入×15%={allowed}，超出部分可结转以后年度",
            ))

    # 3. 职工福利费：工资薪金总额 14% 限额
    welfare = documented["welfare"] if entries else money(profit.get("welfare") or ZERO)
    if welfare > ZERO:
        allowed = money(effective_salary_base * WELFARE_SALARY_CAP)
        increase = money(welfare - allowed)
        if increase > ZERO:
            salary_note = (
                "当年工资薪金基数为 0，不得扣除"
                if effective_salary_base == ZERO
                else f"限额 工资基数 {effective_salary_base}×14%={allowed}"
            )
            items.append(CitAdjustmentItem(
                code="A105000-02",
                label="职工福利费超限额调增",
                amount=increase,
                basis=f"有票发生额 {welfare}，{salary_note}",
            ))

    # 4. 工会经费：工资薪金总额 2% 限额
    union_fee = documented["union"] if entries else money(profit.get("union_fee") or ZERO)
    if union_fee > ZERO:
        allowed = money(effective_salary_base * UNION_SALARY_CAP)
        increase = money(union_fee - allowed)
        if increase > ZERO:
            salary_note = (
                "当年工资薪金基数为 0，不得扣除"
                if effective_salary_base == ZERO
                else f"限额 工资基数 {effective_salary_base}×2%={allowed}"
            )
            items.append(CitAdjustmentItem(
                code="A105000-03",
                label="工会经费超限额调增",
                amount=increase,
                basis=f"有票发生额 {union_fee}，{salary_note}",
            ))

    # 5. 职工教育经费：企税 8% / 个体户及个独 2.5%，超出部分可结转以后年度
    education = documented["education"] if entries else money(profit.get("education_fee") or ZERO)
    if education > ZERO:
        edu_rate = EDUCATION_SALARY_CAP_IIT if is_iit else EDUCATION_SALARY_CAP_CIT
        edu_pct = "2.5%" if is_iit else "8%"
        allowed = money(effective_salary_base * edu_rate)
        increase = money(education - allowed)
        if increase > ZERO:
            salary_note = (
                "当年工资薪金基数为 0，不得扣除"
                if effective_salary_base == ZERO
                else f"限额 工资基数 {effective_salary_base}×{edu_pct}={allowed}"
            )
            items.append(CitAdjustmentItem(
                code="A105000-04",
                label="职工教育经费超限额调增（可结转）",
                amount=increase,
                basis=f"有票发生额 {education}，{salary_note}，超出部分可结转以后年度",
            ))

    # 6. 罚款、滞纳金：不得扣除，全额调增
    if fine_amount > ZERO:
        items.append(CitAdjustmentItem(
            code="A105000-19",
            label="罚款、滞纳金不得扣除调增",
            amount=money(fine_amount),
            basis="《企业所得税法》第十条：罚款、罚金和滞纳金不得税前扣除",
            detail=fine_detail,
        ))

    # 7. 无票支出：账上已计费用但缺少合规扣除凭证（限额费用无票时在此全额调增）
    if undocumented > ZERO:
        items.append(CitAdjustmentItem(
            code="A105000-30",
            label="无合规扣除凭证支出调增",
            amount=money(undocumented),
            basis="取得发票等合规凭证前不得税前扣除，补票后可转回",
            detail=no_document_detail,
        ))

    # 8. 公益性捐赠支出：企税利润总额 12%（可结转 3 年）/ 个体户及个独扣除前应纳税所得额 30%
    donation = documented["donation"] if entries else money(profit.get("donation") or ZERO)
    ledger_by_kind = (carryforward_ledger or {}).get("byKind") or {}
    if donation > ZERO:
        total_profit = money(profit.get("total_profit") or ZERO)
        if is_iit:
            if donation_base is not None:
                base = max(ZERO, money(donation_base))
            else:
                non_donation_increase = money(sum((item.amount for item in items), ZERO))
                base = max(
                    ZERO,
                    money(
                        total_profit
                        + donation
                        + non_donation_increase
                        + max(ZERO, money(exclude_salary))
                        - max(ZERO, money(pre_deductions))
                    ),
                )
            allowed = money(base * DONATION_TAXABLE_CAP_IIT)
            increase = money(donation - allowed)
            if increase > ZERO:
                items.append(CitAdjustmentItem(
                    code="A105000-17",
                    label="公益性捐赠超限额调增",
                    amount=increase,
                    basis=(
                        f"有票发生额 {donation}，限额 扣除前应纳税所得额 {base}×30%={allowed}；"
                        "仅限通过公益性社会组织取得公益捐赠票据，直接捐赠须全额调增"
                    ),
                ))
        else:
            don_ledger = ledger_by_kind.get("donation")
            if don_ledger is not None:
                increase = money(don_ledger.get("currentExcess") or ZERO)
                cap_amount = money(don_ledger.get("currentCap") or ZERO)
                deducted_prior = money(don_ledger.get("deductedFromPrior") or ZERO)
                base_note = (
                    f"有票发生额 {donation}，本年利润 12% 限额 {cap_amount}"
                    + (f"（已先按 FIFO 抵扣以前年度结转 {deducted_prior}）" if deducted_prior > ZERO else "")
                    + "，超限额部分可结转以后 3 年；仅限通过公益性社会组织取得公益捐赠票据，直接捐赠须全额调增"
                )
            else:
                base = max(
                    ZERO,
                    money(donation_base if donation_base is not None else total_profit),
                )
                allowed = money(base * DONATION_PROFIT_CAP_CIT)
                increase = money(donation - allowed)
                base_note = (
                    f"有票发生额 {donation}，限额 年度利润总额 {base}×12%={allowed}，"
                    "超限额部分可结转以后 3 年；仅限通过公益性社会组织取得公益捐赠票据，直接捐赠须全额调增"
                )
            if increase > ZERO:
                items.append(CitAdjustmentItem(
                    code="A105000-17",
                    label="公益性捐赠超限额调增（可结转3年）",
                    amount=increase,
                    basis=base_note,
                ))

    decrease_items: list[CitAdjustmentItem] = []
    for row in (carryforward_ledger or {}).get("items") or []:
        deducted = money(row.get("deductedFromPrior") or ZERO)
        if deducted <= ZERO:
            continue
        if row.get("kind") == "donation":
            basis = (
                f"年初有效结转 {row['openingBalance']}，本年利润 12% 限额 {row['currentCap']}，"
                f"按最早年度 FIFO 优先抵减 {deducted}"
            )
        else:
            basis = (
                f"年初结转 {row['openingBalance']}，本年限额 {row['currentCap']}，"
                f"本年发生 {row['currentAmount']}，使用剩余限额抵减以前年度结转 {deducted}"
            )
        decrease_items.append(CitAdjustmentItem(
            code=str(row["code"]),
            label=f"{row['label']}结转抵减调减",
            amount=deducted,
            basis=basis,
        ))

    total_increase = money(sum((item.amount for item in items), ZERO))
    total_decrease = money(sum((item.amount for item in decrease_items), ZERO))
    net_adjustment = money(total_increase - total_decrease)
    return {
        "items": [item.as_dict() for item in items],
        "decreaseItems": [item.as_dict() for item in decrease_items],
        "totalIncrease": total_increase,
        "totalDecrease": total_decrease,
        "netAdjustment": net_adjustment,
        "carryforwardLedger": list((carryforward_ledger or {}).get("items") or []),
        "revenue": revenue,
        "salaryBase": effective_salary_base,
        "entertainmentExpense": profit_entertainment,
        "advertisingExpense": profit_advertising,
        "pendingDocumentAmount": ZERO,
    }


def evaluate_deductibility(
    required_billing: str | None,
    billing_status: str | None,
    has_invoice_number: bool,
) -> bool | None:
    """按扣除凭证规则判断单笔支出当前是否可税前扣除。

    返回三态：True 可扣除 / False 不得扣除 / None 待确认（缺单据信息）。
    绝不默认 True——把无票支出当成可扣除是所得税底稿最典型的错报；
    也不武断返回 False——用户只是还没录入票据状态时，标成"待确认"。
    无票支出在汇算口径仍会被 build_cit_adjustments 全额调增（_missing_document），
    这里三态只决定单据层标记，两套口径互不掩盖。
    """
    billing = str(billing_status or "")
    if required_billing == "invoice":
        # 规则要求发票：收据/对账不构成合规扣除凭证，只能算待确认
        if billing in {"has_invoice", "issued"} or has_invoice_number:
            return True
        if billing in {"no_document", "no_invoice"}:
            return False
        return None
    if billing in {"has_invoice", "has_receipt", "issued", "reconciled"}:
        return True
    return None
