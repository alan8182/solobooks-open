# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""申报作业包服务（M3，小规模纳税人）。

把已有的测算资产编排成"应报尽报 + 数字一致 + 应享尽享"的作业包：
- 增值税：build_small_scale_vat_assist（逐字段申报表草稿 + 免征判断）
- 企业所得税：build_statement_data + 小型微利预缴测算（A200000 口径）
- 核对报告：增值税销售额 vs 发票池 vs 银行流水入账，三方 diff
- 优惠建议：从两项测算的资格/阈值结果提炼勾选指引
- 指引清单：财务报表/印花税/个税扣缴/工商年报等，逐项可标记完成（闭环）

一期不触碰电子税务局页面；提交申报永远由用户本人在税局完成。
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core.money import ZERO, money
from services import business_income_iit_service, company_entity_service, filing_deadline_service, headcount_service
from services.tax_filing_export_service import build_statement_data, parse_period
from services.vat_filing_service import VAT_REVENUE_ACCOUNTS, build_small_scale_vat_assist
from services.vat_service import REDUCED_RATE, UnsupportedTaxpayerType

logger = logging.getLogger(__name__)

D2 = Decimal("0.01")


def _d2(value: Any) -> Decimal:
    return money(value or ZERO)


def _f(value: Any) -> float:
    try:
        return float(_d2(value))
    except Exception:
        return 0.0


def _quarter_of_period(period: str) -> Optional[str]:
    """2026-08 -> 2026-Q3；2026-Q3 -> 原样。"""
    text = (period or "").strip()
    if "Q" in text.upper():
        return text.upper().replace("季度", "Q").replace("第", "")
    try:
        year, month = int(text[:4]), int(text[5:7])
        return f"{year}-Q{(month - 1) // 3 + 1}"
    except (TypeError, ValueError):
        return None


async def _authoritative_entries(company_id: str) -> list[Dict[str, Any]]:
    """申报底稿只认已过账/已冲销的复式分录，草稿不得混入申报口径。"""
    from db.session import session_scope
    from services.accounting_core_service import posted_entries_for_reports

    async with session_scope() as session:
        return await posted_entries_for_reports(session, company_id)


async def _deduction_materials(
    company_id: str, period: str, period_type: str,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """企业所得税税前扣除资料状态（原报表中心税务汇总页口径，随作业包收口）。"""
    if entries is None:
        entries = await _authoritative_entries(company_id)
    period_range = parse_period(period, period_type)
    book = deductible = non_deductible = Decimal("0")
    for entry in entries:
        occurred = str(entry.get("occurDate") or "")[:10]
        if not occurred or not period_range.start.isoformat() <= occurred <= period_range.end.isoformat():
            continue
        if entry.get("direction") != "expense":
            continue
        amount = _d2(entry.get("amount", 0))
        book += amount
        flag = entry.get("taxDeductible")
        if flag is True:
            deductible += amount
        elif flag is False:
            non_deductible += amount
    pending = max(Decimal("0"), book - deductible - non_deductible)
    return {
        "bookExpenses": _f(book),
        "totalDeductible": _f(deductible),
        "totalNonDeductible": _f(non_deductible),
        "pendingDeductibility": _f(pending),
    }


async def _vat_assist(
    user_id: str, company_id: str, period: str, period_type: str,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if entries is None:
        entries = await _authoritative_entries(company_id)
    rate_value = REDUCED_RATE
    try:
        from services.vat_service import get_vat_config_by_id

        # 3% 征收率纳税人的历史含税凭证须按自身征收率还原；配置不支持时保持 1% 旧行为
        rate_value = (await get_vat_config_by_id(company_id)).rate
    except UnsupportedTaxpayerType:
        pass
    return build_small_scale_vat_assist(
        entries, period, period_type, rate_value=rate_value
    )  # type: ignore[arg-type]


async def _cit_estimate(
    user_id: str, company_id: str, period: str,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """小型微利企业所得税预缴估算（A200000 口径的精简版）。

    与 /report/cit-assist 同源（build_statement_data 的利润表本年累计），
    并执行同一套确定性纳税调整（招待费/广宣费限额、罚款滞纳金、无票支出）。
    """
    statement = await build_statement_data(user_id, company_id, period, "quarter")
    from services.cit_adjustment_service import build_cit_adjustments

    profit = statement.profit_ytd or {}
    total_profit = _d2(profit.get("total_profit", 0))
    revenue = _d2(profit.get("revenue", 0))
    cost = _d2(profit.get("cost", 0))

    if entries is None:
        entries = await _authoritative_entries(company_id)
    period_range = parse_period(period, "quarter")
    year_entries = [
        entry for entry in entries
        if (occurred := str(entry.get("occurDate") or "")[:10])
        and statement.period.fiscal_year_start.isoformat() <= occurred <= period_range.end.isoformat()
    ]
    adjustments = build_cit_adjustments(
        year_entries, profit, statement.period.fiscal_year_start, period_range.end,
    )
    auto_increase = _d2(adjustments.get("totalIncrease") or 0)

    taxable_base = max(Decimal("0"), total_profit + auto_increase)
    standard_tax = (taxable_base * Decimal("0.25")).quantize(D2, rounding="ROUND_HALF_UP")
    preferential_tax = (taxable_base * Decimal("0.05")).quantize(D2, rounding="ROUND_HALF_UP")

    note = "按实际利润额×5% 估算（小型微利优惠）。如有以前年度亏损或已预缴，以税局申报表为准。"
    if auto_increase > 0:
        note = (
            f"已按规则自动纳税调增 {auto_increase}（招待费/广宣费限额、罚款滞纳金、无票支出），"
            + note
        )

    return {
        "period": statement.period.label if statement.period else period,
        "revenue": _f(revenue),
        "cost": _f(cost),
        "totalProfit": _f(total_profit),
        "autoAdjustmentTotal": _f(auto_increase),
        "autoAdjustments": adjustments.get("items", []),
        "taxableBase": _f(taxable_base),
        "standardTax": _f(standard_tax),
        "preferentialTax": _f(preferential_tax),
        # A200000 第 28 栏减免所得税额（老板小抄直接抄表用）
        "preferentialRelief": _f(standard_tax - preferential_tax),
        "note": note,
        "fields": [
            {"line": "1", "label": "营业收入（本年累计）", "amount": _f(revenue)},
            {"line": "2", "label": "营业成本（本年累计）", "amount": _f(cost)},
            {"line": "18", "label": "利润总额（本年累计）", "amount": _f(total_profit)},
            {"line": "18A", "label": "纳税调增（系统测算）", "amount": _f(auto_increase)},
            {"line": "25", "label": "实际利润额", "amount": _f(taxable_base)},
            {"line": "27", "label": "应纳所得税额（25%）", "amount": _f(standard_tax)},
            {"line": "28", "label": "减免所得税额（小型微利）", "amount": _f(standard_tax - preferential_tax)},
            {"line": "32", "label": "本期应补（退）所得税额", "amount": _f(preferential_tax)},
        ],
    }


async def _cross_check(
    user_id: str, company_id: str, period: str, vat: Dict[str, Any],
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """三方核对：增值税销售额（账载） vs 发票池 vs 银行流水入账。"""
    from services.bank_service import get_statements

    period_range = parse_period(period, "quarter" if "Q" in period.upper() else "month")
    start = period_range.start
    end = period_range.end

    if entries is None:
        entries = await _authoritative_entries(company_id)
    invoice_total = ZERO
    invoice_count = 0
    for entry in entries:
        try:
            occurred = date.fromisoformat(str(entry.get("occurDate") or "")[:10])
        except (TypeError, ValueError):
            continue
        if not (start <= occurred <= end):
            continue
        if str(entry.get("direction") or "") != "income":
            continue
        if entry.get("invoiceNumber") and entry.get("invoiceType"):
            invoice_total = _d2(invoice_total + _d2(entry.get("amount")))
            invoice_count += 1

    statements = await get_statements(company_id)
    # 回款口径：只统计销售性质的到账。微信/平台销售回款凭证的贷方是收入
    # 或待结算科目；利息、认证退款、股东注资、往来等非销售进账若一并计入，
    # 会与账载销售额产生无法解释的差额。未匹配的流水保留（可能是漏记的销售回款）。
    entries_by_id = {str(e.get("id") or ""): e for e in entries}

    def _is_sales_inflow(entry: dict[str, Any] | None) -> bool:
        if not entry:
            return True
        voucher = entry.get("voucherLines") or {}
        for line in (voucher.get("credit") or []):
            if str(line.get("accountCode") or "") in {"5001", "5051", "1012001", "1012"}:
                return True
        return False

    bank_inflow = ZERO
    bank_inflow_count = 0
    for stmt in statements:
        try:
            occurred = date.fromisoformat(str(stmt.get("date") or stmt.get("transaction_date") or "")[:10])
        except (TypeError, ValueError):
            continue
        if not (start <= occurred <= end):
            continue
        amount = _d2(stmt.get("amount") or stmt.get("signed_amount") or 0)
        if amount <= 0:
            continue
        matched_id = str(stmt.get("matched_entry_id") or "").strip()
        if not _is_sales_inflow(entries_by_id.get(matched_id)):
            continue
        bank_inflow = _d2(bank_inflow + amount)
        bank_inflow_count += 1

    # 账载销售额取含税口径（与回款的现金口径对齐）：收入科目 5001/5051 净额，
    # 排除期末结转及其红字。不含税 totalSales 仍是申报表口径，不在此处混用。
    closing_ids = {
        str(entry.get("id") or "")
        for entry in entries
        if str(entry.get("entryType") or "") == "closing"
    }
    book_sales = ZERO
    book_count = 0
    for entry in entries:
        if str(entry.get("entryType") or "") == "closing" or entry.get("isClosingTransfer"):
            continue
        if str(entry.get("reversalOf") or "") in closing_ids:
            continue
        try:
            occurred = date.fromisoformat(str(entry.get("occurDate") or "")[:10])
        except (TypeError, ValueError):
            continue
        if not (start <= occurred <= end):
            continue
        voucher = entry.get("voucherLines") or {}
        revenue = _d2(sum(
            (_d2(line.get("amount")) for line in voucher.get("credit", [])
             if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNTS),
            ZERO,
        )) - _d2(sum(
            (_d2(line.get("amount")) for line in voucher.get("debit", [])
             if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNTS),
            ZERO,
        ))
        if revenue == ZERO:
            continue
        book_sales = _d2(book_sales + revenue)
        book_count += 1
    diffs = []
    if invoice_total != book_sales:
        diffs.append(
            {
                "code": "invoice_vs_book",
                "label": "发票开具合计 vs 账载销售额",
                "left": _f(invoice_total),
                "right": _f(book_sales),
                "difference": _f(book_sales - invoice_total),
                "hint": "差异可能来自未开票收入、跨期发票或发票未导入。差额为正说明有收入已入账但无票。",
            }
        )
    if bank_inflow != book_sales:
        diffs.append(
            {
                "code": "bank_vs_book",
                "label": "银行/微信入账合计 vs 账载销售额",
                "left": _f(bank_inflow),
                "right": _f(book_sales),
                "difference": _f(book_sales - bank_inflow),
                "hint": "账载销售额与销售回款均为含税口径，剩余差异主要来自微信结算手续费、跨期结算或未结算在途。",
            }
        )
    return {
        "bookSales": _f(book_sales),
        "bookCount": book_count,
        "invoiceTotal": _f(invoice_total),
        "invoiceCount": invoice_count,
        "bankInflow": _f(bank_inflow),
        "bankInflowCount": bank_inflow_count,
        "differences": diffs,
        "consistent": not diffs,
    }


def _preferences(vat: Dict[str, Any], cit: Optional[Dict[str, Any]], cit_applicable: bool = True) -> List[Dict[str, Any]]:
    """优惠勾选建议：应享尽享。"""
    out: List[Dict[str, Any]] = []
    if vat.get("isBelowThreshold"):
        out.append(
            {
                "code": "vat_exemption",
                "title": "增值税免征（季度销售额未超 30 万元）",
                "detail": (
                    f"本期销售额 {vat.get('totalSales')} 元，未超过小规模季度 30 万元免征标准，"
                    "申报表选择'免税销售额'列填报，增值税及附加均为 0。"
                ),
                "action": "在申报表勾选/填写免税销售额列",
            }
        )
    else:
        out.append(
            {
                "code": "vat_reduced_rate",
                "title": "增值税减按 1% 征收",
                "detail": "销售额超过免征标准，适用 1% 征收率（政策延续至 2027 年），申报表按 1% 列填报，系统已换算不含税销售额。",
                "action": "在申报表 1% 征收率列填报不含税销售额",
            }
        )
    if vat.get("surchargeTotal") is not None and _d2(vat.get("surchargeTotal")) == ZERO:
        out.append(
            {
                "code": "surcharge_zero",
                "title": "附加税费随增值税免征为 0",
                "detail": "增值税免征时城建税/教育费附加等同步为 0，无需额外操作。",
                "action": "无需操作",
            }
        )
    if cit_applicable and cit:
        out.append(
            {
                "code": "cit_small_micro",
                "title": "企业所得税小型微利优惠（实际税负 5%）",
                "detail": (
                    f"本年累计实际利润额 {cit.get('totalProfit')} 元；按小型微利企业政策"
                    "（应纳税所得额≤300 万部分实际税负 5%，政策至 2027 年）估算本期预缴 "
                    f"{cit.get('preferentialTax')} 元。"
                ),
                "action": "预缴申报表第 28 行'减免所得税额'自动体现；从业人数自动取自员工名册、资产总额取自报表，补充所属行业即可",
            }
        )
    out.append(
        {
            "code": "six_taxes_half",
            "title": "六税两费减半（已计入附加税费测算）",
            "detail": "城建税、教育费附加、地方教育附加按 50% 减征计入测算（政策至 2027 年）。",
            "action": "申报时确认减征比例勾选",
        }
    )
    return out


def _iit_hint(
    headcount: Optional[Any], entity_type: str
) -> tuple[str, bool]:
    """个税扣缴提示：按主体类型分叉，返回 (hint, 是否需要先录入员工)。"""
    et = company_entity_service.normalize_entity_type(entity_type)
    label = company_entity_service.ENTITY_LABELS.get(et, "当前主体")
    if headcount is None:
        return "暂时无法读取员工名册；如本月有工资发放，需在扣缴端按月申报。", False
    if headcount.is_corporate:
        if headcount.raw == 0:
            return (
                "公司制企业至少应有 1 名从业人员（含法定代表人）。请先在「薪酬社保」"
                "录入员工名册，再按月申报工资薪金个税。",
                True,
            )
        return "有员工，必须在自然人电子税务局扣缴端按月申报。", False
    if headcount.raw == 0:
        return f"{label}无雇工的可不办理工资薪金扣缴申报；如有雇工，请先录入员工名册。", False
    return "有雇工，需在扣缴端按月申报工资薪金个税。", False


def _checklist(
    period: str,
    quarter: str,
    headcount: Optional[Any] = None,
    entity_type: str = "ltd",
    cit_applicable: bool = True,
    active_groups: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """申报事项清单（不含 vat/cit 两个主表，它们单独成节）。

    `active_groups` 为税费种认定表里出现过的申报表组；为 None 表示公司还没同步
    认定表，按内置清单全量展示。同步过之后，认定表里没有的税种不再出现在清单里
    ——否则用户刚把不需要的税种去掉，页面上又冒出来一条待办。
    """
    items: List[Dict[str, Any]] = []
    year = int(period[:4]) if period[:4].isdigit() else date.today().year

    def _enabled(tax_kind: str) -> bool:
        return active_groups is None or tax_kind in active_groups
    items.append(
        {
            "taxKind": "fin_statements",
            "name": filing_deadline_service.TAX_KINDS["fin_statements"]["name"],
            "guide": filing_deadline_service.TAX_KINDS["fin_statements"]["guide"],
            "hint": "可直接在本页导出官方 XLS 模板（资产负债表/利润表/现金流量表，小企业会计准则）。",
            "canExport": True,
        }
    )
    if _enabled("stamp_duty"):
        items.append(
            {
                "taxKind": "stamp_duty",
                "name": filing_deadline_service.TAX_KINDS["stamp_duty"]["name"],
                "guide": filing_deadline_service.TAX_KINDS["stamp_duty"]["guide"],
                "hint": "本期无应税合同/增资可零申报（确认后本项即完成）。",
            }
        )
    if _enabled("iit_withholding"):
        iit_hint, needs_employee_setup = _iit_hint(headcount, entity_type)
        # 个税扣缴按月申报：季度作业包展开为该季度逐月，各自截止与状态，
        # 避免用季度末月代表式折叠，把前两月的逾期/待办藏起来
        q_num = int(quarter[-1])
        first_month = (q_num - 1) * 3 + 1
        for m in range(3):
            month_no = first_month + m
            items.append(
                {
                    "taxKind": "iit_withholding",
                    "itemPeriod": f"{year}-{month_no:02d}",
                    "name": f"{filing_deadline_service.TAX_KINDS['iit_withholding']['name']}（{month_no} 月）",
                    "guide": filing_deadline_service.TAX_KINDS["iit_withholding"]["guide"],
                    # 提示只在首月放一次，避免逐月重复
                    "hint": iit_hint if m == 0 else "",
                    "needsEmployeeSetup": needs_employee_setup,
                    "actionPath": "/mine/salary" if needs_employee_setup else None,
                    "actionLabel": "去录入员工" if needs_employee_setup else None,
                }
            )
    if entity_type != "ltd" and _enabled("business_income_iit_prepay"):
        items.append(
            {
                "taxKind": "business_income_iit_prepay",
                "name": filing_deadline_service.TAX_KINDS["business_income_iit_prepay"]["name"],
                "guide": filing_deadline_service.TAX_KINDS["business_income_iit_prepay"]["guide"],
                "hint": "查账征收适用；累计收入、应纳税所得额与应纳税额见上方经营所得卡片，核定征收不适用。",
            }
        )
    if quarter and quarter.endswith("-Q2"):
        items.append(
            {
                "taxKind": "annual_report",
                "name": filing_deadline_service.TAX_KINDS["annual_report"]["name"],
                "guide": filing_deadline_service.TAX_KINDS["annual_report"]["guide"],
                "hint": f"{year} 年度年报，6 月 30 日截止，现在做正合适。",
            }
        )
    if active_groups:
        for tax_kind in ("culture_fee", "disability_fund", "other"):
            if tax_kind not in active_groups:
                continue
            # 残保金按年申报（次年 9-30）：只在 Q3 申报季显示，其余季度不出现
            if tax_kind == "disability_fund" and not (quarter and quarter.endswith("-Q3")):
                continue
            meta = filing_deadline_service.TAX_KINDS[tax_kind]
            hint = "按税费种认定表的纳税期限申报，可在「税费种认定」页查看认定明细。"
            name = meta["name"]
            if tax_kind == "disability_fund":
                name = f"{name}（下一年度申报）"
                hint = "按上年度在职职工工资计征，次年 9 月 30 日前申报，30 人以下小微企业免征。"
            items.append(
                {
                    "taxKind": tax_kind,
                    "name": name,
                    "guide": meta["guide"],
                    "hint": hint,
                }
            )
    if cit_applicable and quarter and quarter.endswith("-Q4"):
        items.append(
            {
                "taxKind": "cit_annual",
                "name": filing_deadline_service.TAX_KINDS["cit_annual"]["name"],
                "guide": filing_deadline_service.TAX_KINDS["cit_annual"]["guide"],
                "hint": f"{year} 年度汇算清缴，次年 5 月 31 日截止；提前整理费用票据。",
            }
        )
    return items


async def build_filing_package(
    user_id: str,
    company_id: str,
    period: str,
) -> Dict[str, Any]:
    """生成某期间的申报作业包（实时计算，不落库）。"""
    period = (period or "").strip()
    is_quarter = "Q" in period.upper()
    quarter = _quarter_of_period(period) or period
    package_period = quarter if is_quarter else period
    period_type = "quarter" if is_quarter else "month"

    # 权威分录整包只取一次：vat/cit/cross_check/deduction 四段共享
    # （原实现每段各自全量加载+序列化一遍，申报包一次请求四倍开销）
    authoritative_entries = await _authoritative_entries(company_id)
    vat = await _vat_assist(user_id, company_id, package_period, period_type,
                            entries=authoritative_entries)
    # 所得税按主体类型分支：个人独资/个体工商户不缴企业所得税，CIT 整体不适用，
    # 改出经营所得个税 A 表预缴卡（5%-35% 五级累进，个体户 200 万内减半）
    entity_type = await company_entity_service.get_company_entity_type(company_id)
    cit_applicable = company_entity_service.pays_corporate_income_tax(entity_type)
    cit = (
        await _cit_estimate(user_id, company_id, quarter, entries=authoritative_entries)
        if cit_applicable else None
    )
    iit = (
        await business_income_iit_service.build_prepayment_assist(user_id, company_id, package_period)
        if not cit_applicable else None
    )
    iit_applicable = bool(iit and iit.get("applicable"))
    cross = await _cross_check(user_id, company_id, package_period, vat,
                               entries=authoritative_entries)
    deduction = await _deduction_materials(company_id, package_period, period_type,
                                           entries=authoritative_entries)

    # 税费种认定表：有认定就按认定口径裁剪申报清单，没认定走内置全量清单
    from services import tax_registration_service

    registrations = await tax_registration_service.list_registrations(company_id)
    active_groups = {reg["returnGroup"] for reg in registrations} or None

    # 征期状态（物化后读取）
    try:
        await filing_deadline_service.refresh_deadlines(company_id)
    except Exception:
        logger.exception("refresh deadlines failed")
    deadlines = await filing_deadline_service.list_deadlines(company_id)
    by_key = {(d["taxKind"], d["period"]): d for d in deadlines}
    quarter_deadlines = [d for d in deadlines if d["period"] == quarter]

    def _item_state(tax_kind: str, item_period: Optional[str] = None) -> Dict[str, Any]:
        row = by_key.get((tax_kind, item_period or quarter))
        if row:
            return {
                "deadlineId": row["id"],
                "status": row["status"],
                "dueDate": row["dueDate"],
                "daysLeft": row["daysLeft"],
            }
        return {"deadlineId": None, "status": "untracked", "dueDate": None, "daysLeft": None}

    # 增值税征期随公司申报周期：月报公司取该季度末月，季报公司取季度期间
    # 周期以税费种认定表为准（公司档案只是未同步前的兜底）
    vat_cycle = await filing_deadline_service.effective_vat_cycle(company_id)
    vat_deadline_period = quarter
    if vat_cycle == "month":
        parsed_quarter = filing_deadline_service._quarter_label(quarter)
        if parsed_quarter:
            vat_deadline_period = f"{parsed_quarter[0]}-{parsed_quarter[1] * 3:02d}"
    vat_deadline = _item_state("vat", vat_deadline_period)
    # 申报口径不一致时要显式告诉用户：季报公司看季度包、月报公司看月度包
    vat_cycle_mismatch = (vat_cycle == "month") != (period_type == "month")
    cit_deadline = _item_state("cit", quarter) if cit_applicable else None
    iit_deadline = _item_state("business_income_iit_prepay", package_period) if iit_applicable else None

    headcount = None
    try:
        headcount = await headcount_service.get_headcount(company_id, entity_type)
    except Exception:  # noqa: BLE001 —— 名册读取失败降级为兜底文案
        logger.exception("headcount lookup failed company=%s", company_id)

    def _item_period_for(tax_kind: str) -> str:
        # 个税扣缴按月申报：用该季度最后一个月的征期作为代表
        if tax_kind == "iit_withholding":
            parsed = filing_deadline_service._quarter_label(quarter)
            if parsed:
                year, q = parsed
                return f"{year}-{q * 3:02d}"
            return quarter
        # 经营所得 A 表预缴周期随公司配置（月/季）：直接用作业包自身期间匹配
        if tax_kind == "business_income_iit_prepay":
            return package_period
        # 残保金按年属期（次年 9-30），期间就是年份本身
        if tax_kind == "disability_fund":
            return quarter[:4] if quarter[:4].isdigit() else quarter
        return quarter

    package = {
        "period": package_period,
        "taxRegistrations": {
            "synced": bool(registrations),
            "count": len(registrations),
            "groups": sorted(active_groups) if active_groups else [],
        },
        "periodType": period_type,
        "generatedAt": date.today().isoformat(),
        # 各税种申报周期（增值税与经营所得可能不同）
        "filingCycles": {"vat": vat_cycle},
        "vatCycleMismatch": vat_cycle_mismatch,
        "vat": {
            **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in vat.items() if k not in ("items", "filingFields", "threshold")},
            "filingFields": [
                {**f, "amount": float(f["amount"])} for f in vat.get("filingFields", [])
            ],
            "items": [
                {**i, "salesAmount": float(i["salesAmount"])} for i in vat.get("items", [])
            ],
            "deadline": vat_deadline,
        },
        "cit": {**cit, "deadline": cit_deadline} if cit else None,
        "citNotice": company_entity_service.income_tax_notice(entity_type),
        "businessIncomeIit": (
            {
                **{
                    k: (float(v) if isinstance(v, Decimal) else v)
                    for k, v in iit.items() if k not in ("notes",)
                },
                "notes": iit.get("notes", []),
                "deadline": iit_deadline,
            }
            if iit_applicable else None
        ),
        "crossCheck": cross,
        "deductionMaterials": deduction,
        "preferences": _preferences(vat, cit, cit_applicable),
        "checklist": [
            {**item, **_item_state(item["taxKind"], item.get("itemPeriod") or _item_period_for(item["taxKind"]))}
            for item in _checklist(
                package_period,
                quarter,
                headcount=headcount,
                entity_type=entity_type,
                cit_applicable=cit_applicable,
                active_groups=active_groups,
            )
        ],
        "summary": {
            "readyToFile": bool(vat.get("readyToFile")),
            "blockingIssues": vat.get("blockingIssues", []),
            "warnings": vat.get("warnings", []),
            "vatPayable": float(_d2(vat.get("totalVatPayable"))),
            "citPayable": cit.get("preferentialTax") if cit else 0,
            "iitPayable": float(iit.get("suggestedPayable")) if iit_applicable else 0,
            "overdue": [d["taxName"] for d in quarter_deadlines if d["status"] == "pending" and d["daysLeft"] < 0],
        },
    }
    return package


async def mark_item_filed(
    user_id: str,
    company_id: str,
    deadline_id: str,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """清单项/税种完成（含回执归档信息）。"""
    return await filing_deadline_service.mark_filed(
        company_id, user_id, deadline_id, evidence=evidence
    )
