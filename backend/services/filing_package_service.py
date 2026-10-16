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
from services import filing_deadline_service
from services.tax_filing_export_service import build_statement_data, parse_period
from services.vat_filing_service import build_small_scale_vat_assist

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


async def _vat_assist(user_id: str, company_id: str, period: str, period_type: str) -> Dict[str, Any]:
    from services.entry_service import list_entries

    entries = await list_entries(user_id, company_id)
    return build_small_scale_vat_assist(entries, period, period_type)  # type: ignore[arg-type]


async def _cit_estimate(user_id: str, company_id: str, period: str) -> Dict[str, Any]:
    """小型微利企业所得税预缴估算（A200000 口径的精简版）。

    与 /report/cit-assist 同源（build_statement_data 的利润表本年累计），
    作业包场景无人工调整项输入，按实际利润额直接测算并标注前提。
    """
    statement = await build_statement_data(user_id, company_id, period, "quarter")

    profit = statement.profit_ytd or {}
    total_profit = _d2(profit.get("total_profit", 0))
    revenue = _d2(profit.get("revenue", 0))
    taxable_base = max(Decimal("0"), total_profit)
    standard_tax = (taxable_base * Decimal("0.25")).quantize(D2, rounding="ROUND_HALF_UP")
    preferential_tax = (taxable_base * Decimal("0.05")).quantize(D2, rounding="ROUND_HALF_UP")

    return {
        "period": statement.period.label if statement.period else period,
        "revenue": _f(revenue),
        "totalProfit": _f(total_profit),
        "taxableBase": _f(taxable_base),
        "standardTax": _f(standard_tax),
        "preferentialTax": _f(preferential_tax),
        "note": "按实际利润额×5% 估算（小型微利优惠）。如有纳税调整、以前年度亏损或已预缴，以税局申报表为准。",
        "fields": [
            {"line": "1", "label": "营业收入（本年累计）", "amount": _f(revenue)},
            {"line": "18", "label": "利润总额（本年累计）", "amount": _f(total_profit)},
            {"line": "25", "label": "实际利润额", "amount": _f(taxable_base)},
            {"line": "27", "label": "应纳所得税额（25%）", "amount": _f(standard_tax)},
            {"line": "28", "label": "减免所得税额（小型微利）", "amount": _f(standard_tax - preferential_tax)},
            {"line": "32", "label": "本期应补（退）所得税额", "amount": _f(preferential_tax)},
        ],
    }


async def _cross_check(user_id: str, company_id: str, period: str, vat: Dict[str, Any]) -> Dict[str, Any]:
    """三方核对：增值税销售额（账载） vs 发票池 vs 银行流水入账。"""
    from services.bank_service import get_statements
    from services.entry_service import list_entries

    period_range = parse_period(period, "quarter" if "Q" in period.upper() else "month")
    start = period_range.start
    end = period_range.end

    entries = await list_entries(user_id, company_id)
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
        if amount > 0:
            bank_inflow = _d2(bank_inflow + amount)
            bank_inflow_count += 1

    book_sales = _d2(vat.get("totalSales") or 0)
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
                "hint": "差异可能来自老板私账收款、往来款或非收入入账（如验证款）。请区分后补记。",
            }
        )
    return {
        "bookSales": _f(book_sales),
        "bookCount": int(vat.get("sourceEntryCount") or 0),
        "invoiceTotal": _f(invoice_total),
        "invoiceCount": invoice_count,
        "bankInflow": _f(bank_inflow),
        "bankInflowCount": bank_inflow_count,
        "differences": diffs,
        "consistent": not diffs,
    }


def _preferences(vat: Dict[str, Any], cit: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    out.append(
        {
            "code": "cit_small_micro",
            "title": "企业所得税小型微利优惠（实际税负 5%）",
            "detail": (
                f"本年累计实际利润额 {cit.get('totalProfit')} 元；按小型微利企业政策"
                "（应纳税所得额≤300 万部分实际税负 5%，政策至 2027 年）估算本期预缴 "
                f"{cit.get('preferentialTax')} 元。"
            ),
            "action": "预缴申报表第 28 行'减免所得税额'自动体现，确认从业人数/资产总额条件即可",
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


def _checklist(period: str, quarter: str, employees_hint: Optional[int] = None) -> List[Dict[str, Any]]:
    """申报事项清单（不含 vat/cit 两个主表，它们单独成节）。"""
    items: List[Dict[str, Any]] = []
    year = int(period[:4]) if period[:4].isdigit() else date.today().year
    items.append(
        {
            "taxKind": "fin_statements",
            "name": filing_deadline_service.TAX_KINDS["fin_statements"]["name"],
            "guide": filing_deadline_service.TAX_KINDS["fin_statements"]["guide"],
            "hint": "可直接在本页导出官方 XLS 模板（资产负债表/利润表/现金流量表，小企业会计准则）。",
            "canExport": True,
        }
    )
    items.append(
        {
            "taxKind": "stamp_duty",
            "name": filing_deadline_service.TAX_KINDS["stamp_duty"]["name"],
            "guide": filing_deadline_service.TAX_KINDS["stamp_duty"]["guide"],
            "hint": "本期无应税合同/增资可零申报（确认后本项即完成）。",
        }
    )
    items.append(
        {
            "taxKind": "iit_withholding",
            "name": filing_deadline_service.TAX_KINDS["iit_withholding"]["name"],
            "guide": filing_deadline_service.TAX_KINDS["iit_withholding"]["guide"],
            "hint": (
                "插件覆盖不到扣缴端；无员工无工资发放时本月可不做（确认后完成）。"
                if (employees_hint or 0) == 0
                else "有员工，必须在自然人电子税务局扣缴端按月申报。"
            ),
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
    if quarter and quarter.endswith("-Q4"):
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

    vat = await _vat_assist(user_id, company_id, package_period, period_type)
    cit = await _cit_estimate(user_id, company_id, quarter)
    cross = await _cross_check(user_id, company_id, package_period, vat)

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

    vat_deadline = _item_state("vat", quarter)
    cit_deadline = _item_state("cit", quarter)

    employees_hint = None
    try:
        from services.salary_service import list_employees

        employees_hint = len(await list_employees(company_id))
    except Exception:
        pass

    def _item_period_for(tax_kind: str) -> str:
        # 个税扣缴按月申报：用该季度最后一个月的征期作为代表
        if tax_kind == "iit_withholding":
            parsed = filing_deadline_service._quarter_label(quarter)
            if parsed:
                year, q = parsed
                return f"{year}-{q * 3:02d}"
            return quarter
        return quarter

    package = {
        "period": package_period,
        "periodType": period_type,
        "generatedAt": date.today().isoformat(),
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
        "cit": {**cit, "deadline": cit_deadline},
        "crossCheck": cross,
        "preferences": _preferences(vat, cit),
        "checklist": [
            {**item, **_item_state(item["taxKind"], _item_period_for(item["taxKind"]))}
            for item in _checklist(package_period, quarter, employees_hint)
        ],
        "summary": {
            "readyToFile": bool(vat.get("readyToFile")),
            "blockingIssues": vat.get("blockingIssues", []),
            "warnings": vat.get("warnings", []),
            "vatPayable": float(_d2(vat.get("totalVatPayable"))),
            "citPayable": cit.get("preferentialTax"),
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
