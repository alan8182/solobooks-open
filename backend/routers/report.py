# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""V3 报表路由：月报 + 增值税/CIT/归档/税负/健康评分"""
from typing import Optional
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from deps.auth import AuthContext, require_auth
from models.schemas import (
    d2,
    MonthlyReportRequest, MonthlyReportResponse,
    ReportDiagnosisRequest, ReportDiagnosisResponse,
    HealthScoreRequest, HealthScoreResponse,
    VatAssistRequest, VatAssistResponse, VatAssistItem,
    CitAssistRequest, CitAssistResponse,
    ArchiveChecklistRequest, ArchiveChecklistResponse, ArchiveItem,
    TaxSummaryRequest, TaxSummaryResponse,
    BalanceSheetRequest, BalanceSheetResponse, BalanceSheetItem,
    CashFlowRequest, CashFlowResponse, CashFlowItem,
    IncomeStatementRequest, IncomeStatementResponse, IncomeStatementRow,
    ComparisonRequest, ComparisonResponse, ComparisonItem,
)
from services.llm import chat_text_json, chat_text
from services.ocr import HEALTH_SCORE_V3_PROMPT
from services.diagnosis_prompts import CFO_DIAGNOSIS_PROMPT
from services.tax_rules import compute_vat, lookup_tax, sanitize_risk_tags
from db.session import get_db_session
from services.accounting_core_service import ensure_tenant, posted_entries_for_reports
from services.tax_filing_export_service import build_statement_data, default_completed_period
from services.vat_filing_service import build_small_scale_vat_assist
from services.account_service import get_user_profile

router = APIRouter(prefix="/api", tags=["report"], dependencies=[Depends(require_auth)])


def _scope_entries(entries: list[dict], company_id: str) -> list[dict]:
    """Filter incoming entries by company_id to enforce isolation."""
    return [
        e
        for e in entries
        if (e.get("company_id") or e.get("companyId") or "c1") == company_id
    ]


async def _authoritative_entries(session: AsyncSession, ctx: AuthContext) -> list[dict]:
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    return await posted_entries_for_reports(session, ctx.company_id)


def _voucher_account_effect(entry: dict, account_code: str) -> Decimal:
    """Return debit-credit balance effect for an account from explicit voucherLines."""
    voucher = entry.get("voucherLines") or {}
    effect = Decimal("0")
    for line in voucher.get("debit", []) or []:
        if line.get("accountCode") == account_code:
            effect += d2(line.get("amount", 0) or 0)
    for line in voucher.get("credit", []) or []:
        if line.get("accountCode") == account_code:
            effect -= d2(line.get("amount", 0) or 0)
    return effect


INCOME_STATEMENT_ROW_DEFS = [
    ("operating_revenue", "一、营业收入", 0, False),
    ("operating_cost", "    减：营业成本", 1, False),
    ("tax_surcharges", "    税金及附加", 1, False),
    ("consumption_tax", "       其中：消费税", 2, False),
    ("business_tax", "       营业税", 2, False),
    ("city_maintenance_tax", "       城市维护建设税", 2, False),
    ("resource_tax", "       资源税", 2, False),
    ("land_vat", "       土地增值税", 2, False),
    ("property_related_taxes", "       城镇土地使用税、房产税、车船税、印花税", 2, False),
    ("education_surcharges", "       教育费附加、矿产资源补偿费、排污费", 2, False),
    ("selling_expenses", "    销售费用", 1, False),
    ("repair_cost", "       其中：商品维修费", 2, False),
    ("advertising_expense", "       广告费和业务宣传费", 2, False),
    ("admin_expenses", "    管理费用", 1, False),
    ("startup_expense", "       其中：开办费", 2, False),
    ("business_entertainment", "       业务招待费", 2, False),
    ("research_expense", "       研究费用", 2, False),
    ("finance_expenses", "    财务费用", 1, False),
    ("interest_expense", "       其中：利息费用（收入以“-”号填列）", 2, False),
    ("investment_income", "    加：投资收益", 1, False),
    ("operating_profit", '二、营业利润（亏损以"－"号填列）', 0, True),
    ("nonoperating_income", "    加：营业外收入", 1, False),
    ("government_grant", "       其中：政府补助", 2, False),
    ("nonoperating_expense", "    减：营业外支出", 1, False),
    ("bad_debt_loss", "       其中：坏账损失", 2, False),
    ("long_bond_loss", "       无法收回的长期债券投资损失", 2, False),
    ("long_equity_loss", "       无法收回的长期股权投资损失", 2, False),
    ("disaster_loss", "       自然灾害等不可抗力因素造成的损失", 2, False),
    ("tax_late_fee", "       税收滞纳金", 2, False),
    ("profit_total", '三、利润总额（亏损总额以"－"号填列）', 0, True),
    ("income_tax_expense", "    减：所得税费用", 1, False),
    ("net_profit", '四、净利润（净亏损以"－"号填列）', 0, True),
]

INCOME_STATEMENT_KEYS = [row[0] for row in INCOME_STATEMENT_ROW_DEFS]


def _empty_income_statement_values() -> dict[str, Decimal]:
    return {key: Decimal("0") for key in INCOME_STATEMENT_KEYS}


def _entry_month(entry: dict) -> Optional[str]:
    date_value = (
        entry.get("occurDate")
        or entry.get("date")
        or entry.get("time")
        or entry.get("created_at")
        or ""
    )
    if isinstance(date_value, str) and len(date_value) >= 7:
        return date_value[:7]
    return None


def _income_statement_columns(view_mode: str) -> list[dict]:
    if view_mode == "quarterly":
        return [
            {"key": "ytd", "label": "本年累计"},
            {"key": "q1", "label": "第一季度"},
            {"key": "q2", "label": "第二季度"},
            {"key": "q3", "label": "第三季度"},
            {"key": "q4", "label": "第四季度"},
        ]
    return [
        {"key": "current", "label": "本月数"},
        {"key": "ytd", "label": "本年累计"},
    ]


def _quarter_key(month_num: int) -> str:
    return f"q{((month_num - 1) // 3) + 1}"


def _classify_statement_account(code: str, name: str, category: str) -> Optional[tuple[str, str]]:
    text = f"{code} {name} {category}"
    if code in ("5001", "5051") or "主营业务收入" in text or "其他业务收入" in text:
        return "operating_revenue", "income"
    if code == "5301" or "营业外收入" in text:
        return "nonoperating_income", "income"
    if "投资收益" in text:
        return "investment_income", "income"
    if code.startswith(("5401", "5402")) or "主营业务成本" in text or "生产成本" in text or "人力成本" in text:
        return "operating_cost", "expense"
    if code == "5403" or "税金及附加" in text:
        return "tax_surcharges", "expense"
    if code.startswith("5601") or "销售费用" in text or "推广费" in text:
        return "selling_expenses", "expense"
    if code.startswith(("5602", "4301")) or "管理费用" in text or "研发支出" in text:
        return "admin_expenses", "expense"
    if code.startswith("5603") or "财务费用" in text or "银行及金融手续费" in text or "利息" in text:
        return "finance_expenses", "expense"
    if code == "5711" or "营业外支出" in text:
        return "nonoperating_expense", "expense"
    if code == "5801" or "所得税费用" in text:
        return "income_tax_expense", "expense"
    return None


def _fallback_statement_bucket(entry: dict) -> Optional[tuple[str, str, Decimal]]:
    direction = entry.get("direction", "")
    category = str(entry.get("category") or "")
    code = str(entry.get("accountCode") or "")
    name = str(entry.get("accountName") or "")
    amount = d2(entry.get("amount", 0) or 0)

    classified = _classify_statement_account(code, name, category)
    if classified:
        bucket, nature = classified
        if direction == "income" and bucket == "finance_expenses":
            return bucket, nature, -amount
        return bucket, nature, amount

    if direction == "income":
        if "营业外收入" in category:
            return "nonoperating_income", "income", amount
        if "投资收益" in category:
            return "investment_income", "income", amount
        if "利息收入" in category:
            return "finance_expenses", "expense", -amount
        return "operating_revenue", "income", amount

    if direction == "expense":
        asset_categories = ("原材料采购", "商品采购", "固定资产", "设备采购", "预付账款")
        if category in asset_categories or code in ("1403", "1405", "1601", "1123", "2221"):
            return None
        if category in ("生产成本", "人力成本"):
            return "operating_cost", "expense", amount
        if category in ("推广费", "广告费", "业务宣传费"):
            return "selling_expenses", "expense", amount
        if category in ("银行及金融手续费", "利息费用"):
            return "finance_expenses", "expense", amount
        if category in ("营业外支出", "坏账损失", "税收滞纳金"):
            return "nonoperating_expense", "expense", amount
        if category == "所得税费用":
            return "income_tax_expense", "expense", amount
        if category == "税费缴纳":
            return "tax_surcharges", "expense", amount
        return "admin_expenses", "expense", amount

    return None


def _add_statement_details(values: dict[str, Decimal], bucket: str, amount: Decimal, text: str) -> None:
    if bucket == "tax_surcharges":
        if "消费税" in text:
            values["consumption_tax"] += amount
        elif "营业税" in text:
            values["business_tax"] += amount
        elif "城市维护建设税" in text or "城建税" in text:
            values["city_maintenance_tax"] += amount
        elif "资源税" in text:
            values["resource_tax"] += amount
        elif "土地增值税" in text:
            values["land_vat"] += amount
        elif any(token in text for token in ("城镇土地使用税", "房产税", "车船税", "印花税")):
            values["property_related_taxes"] += amount
        elif any(token in text for token in ("教育费附加", "矿产资源补偿费", "排污费")):
            values["education_surcharges"] += amount
    elif bucket == "selling_expenses":
        if "维修" in text:
            values["repair_cost"] += amount
        if "广告" in text or "宣传" in text or "推广" in text:
            values["advertising_expense"] += amount
    elif bucket == "admin_expenses":
        if "开办费" in text:
            values["startup_expense"] += amount
        if "业务招待" in text or "餐饮" in text:
            values["business_entertainment"] += amount
        if "研究" in text or "研发" in text:
            values["research_expense"] += amount
    elif bucket == "finance_expenses":
        if "利息" in text:
            values["interest_expense"] += amount
    elif bucket == "nonoperating_income":
        if "政府补助" in text or "补助" in text:
            values["government_grant"] += amount
    elif bucket == "nonoperating_expense":
        if "坏账" in text:
            values["bad_debt_loss"] += amount
        elif "长期债券" in text:
            values["long_bond_loss"] += amount
        elif "长期股权" in text:
            values["long_equity_loss"] += amount
        elif "自然灾害" in text or "不可抗力" in text:
            values["disaster_loss"] += amount
        elif "滞纳金" in text:
            values["tax_late_fee"] += amount


def _income_statement_entry_values(entry: dict) -> dict[str, Decimal]:
    values = _empty_income_statement_values()
    if (
        entry.get("source") == "wechat_settlement"
        or entry.get("entryType") in ("opening", "closing")
        or entry.get("isClosingTransfer")
    ):
        return values

    voucher = entry.get("voucherLines") or {}
    has_voucher = bool(voucher.get("debit") or voucher.get("credit"))
    if has_voucher:
        for side in ("debit", "credit"):
            for line in voucher.get(side, []) or []:
                amount = d2(line.get("amount", 0) or 0)
                code = str(line.get("accountCode") or "")
                name = str(line.get("accountName") or "")
                category = str(entry.get("category") or "")
                # Explicit journal lines are authoritative. Entry-level category must
                # not turn the balancing bank/cash line into a second P&L line.
                classified = _classify_statement_account(
                    code,
                    name,
                    "" if code or name else category,
                )
                if not classified:
                    continue
                bucket, nature = classified
                signed = amount if side == ("credit" if nature == "income" else "debit") else -amount
                values[bucket] += signed
                _add_statement_details(values, bucket, signed, f"{name} {category}")
        return values

    fallback = _fallback_statement_bucket(entry)
    if fallback:
        bucket, _nature, amount = fallback
        values[bucket] += amount
        _add_statement_details(
            values,
            bucket,
            amount,
            f"{entry.get('accountName') or ''} {entry.get('category') or ''} {entry.get('title') or ''}",
        )
    return values


def _finalize_income_statement_values(values: dict[str, Decimal]) -> dict[str, Decimal]:
    values["operating_profit"] = (
        values["operating_revenue"]
        - values["operating_cost"]
        - values["tax_surcharges"]
        - values["selling_expenses"]
        - values["admin_expenses"]
        - values["finance_expenses"]
        + values["investment_income"]
    )
    values["profit_total"] = (
        values["operating_profit"]
        + values["nonoperating_income"]
        - values["nonoperating_expense"]
    )
    values["net_profit"] = values["profit_total"] - values["income_tax_expense"]
    return values


def _income_statement_period_keys(entry_month: str, report_month: str, view_mode: str) -> list[str]:
    report_year = int(report_month[:4])
    report_month_num = int(report_month[5:7])
    entry_year = int(entry_month[:4])
    entry_month_num = int(entry_month[5:7])

    if entry_year != report_year or entry_month_num > report_month_num:
        return []
    if view_mode == "quarterly":
        return ["ytd", _quarter_key(entry_month_num)]
    if entry_month == report_month:
        return ["current", "ytd"]
    return ["ytd"]


def _money(value) -> Decimal:
    return d2(value)


def _filter_period(entries: list, period: str, period_type: str = "month") -> list:
    """Filter entries by period based on periodType ('month', 'quarter', 'year')."""
    if not period:
        return entries
    if period_type == "year":
        year_str = period[:4]
        return [e for e in entries if (e.get("occurDate") or "").startswith(year_str)]
    if period_type == "quarter":
        year_str = period[:4]
        try:
            q = int(period[-1])
        except (ValueError, IndexError):
            q = 1
        months = [f"{year_str}-{(q - 1) * 3 + i:02d}" for i in (1, 2, 3)]
        return [e for e in entries if any((e.get("occurDate") or "").startswith(m) for m in months)]
    return [e for e in entries if (e.get("occurDate") or "").startswith(period[:7])]


def _period_end_month(period: str, period_type: str) -> str:
    if not period:
        now = datetime.now()
        return f"{now.year}-{now.month:02d}"
    if period_type == "year":
        return f"{period[:4]}-12"
    if period_type == "quarter":
        try:
            q = int(period[-1])
        except (ValueError, IndexError):
            q = 1
        return f"{period[:4]}-{q * 3:02d}"
    return period[:7]


@router.post("/report/monthly", response_model=MonthlyReportResponse)
async def monthly_report(
    req: MonthlyReportRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    period_type = req.periodType or "month"
    resolved_period = req.period or req.month or ""
    all_entries = await _authoritative_entries(session, ctx)
    entries = _filter_period(all_entries, resolved_period, period_type)
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    expense = sum(e.get("amount", 0) for e in entries if e.get("direction") == "expense")

    breakdown = {}
    for e in entries:
        cat = e.get("category", "其他")
        breakdown[cat] = breakdown.get(cat, 0) + e.get("amount", 0)

    # 税务汇总（只用已有 vatAmount 字段，避免重复计算）
    total_vat = sum(
        (d2(e.get("vatAmount", 0) or 0) for e in entries if e.get("direction") == "income"),
        Decimal("0"),
    )
    # 小微企业所得税实际税负为5%（20% × 25%计入应纳税所得额），实际以年度汇算为准
    net_profit = income - expense
    estimated_cit = d2(max(Decimal("0"), net_profit) * Decimal("0.05"))

    if period_type == "year":
        cit_note = "按小微企业所得税实际税负5%测算（年应纳税所得额300万以下，实际以年度汇算清缴结果为准）"
    elif period_type == "quarter":
        cit_note = "按小微企业所得税实际税负5%季度预缴估算（实际以年度汇算清缴结果为准）"
    else:
        cit_note = "按小微企业所得税实际税负5%粗略估算（实际以年度汇算清缴结果为准）"

    tax_summary = {
        "estimatedVat": round(total_vat, 2),
        "estimatedCit": estimated_cit,
        "citNote": cit_note,
    }

    summary = None
    if req.includeSummary:
        # ponytail: LLM summary only generated when explicitly requested; default returns instantly
        try:
            result = chat_text_json(
                system_prompt="你是经营助手，生成经营总结。只输出JSON: {\"summary\": \"...\"}",
                user_message=f"统计周期:{period_type}, 期间:{resolved_period}, 收入:{income}, 支出:{expense}, 净利润:{net_profit}, 分类:{breakdown}",
                max_tokens=512,
                scene="report",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
            )
            summary = result.get("summary", "经营报表已生成")
        except Exception:
            summary = "经营报表已生成"

    available_cash = None
    pending_receivables = None
    try:
        end_month = _period_end_month(resolved_period, period_type)
        statement = await build_statement_data(ctx.user_id, ctx.company_id, end_month, "month")
        available_cash = d2(statement.balance_current.get("cash", Decimal("0")))
        pending_receivables = d2(
            statement.balance_current.get("accounts_receivable", Decimal("0"))
            + statement.balance_current.get("other_receivable", Decimal("0"))
        )
    except Exception:
        pass

    return MonthlyReportResponse(
        summary=summary, monthIncome=income, monthExpense=expense,
        netProfit=net_profit, categoryBreakdown=breakdown,
        taxSummary=tax_summary,
        availableCash=available_cash,
        pendingReceivables=pending_receivables,
        period=resolved_period,
        periodType=period_type,
    )


@router.post("/report/diagnosis", response_model=ReportDiagnosisResponse)
async def report_diagnosis(
    req: ReportDiagnosisRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    # ponytail: manual on-demand AI diagnosis trigger, avoiding blocking normal report loading
    period_type = req.periodType or "month"
    resolved_period = req.period or req.month or ""
    all_entries = await _authoritative_entries(session, ctx)
    entries = _filter_period(all_entries, resolved_period, period_type)
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    expense = sum(e.get("amount", 0) for e in entries if e.get("direction") == "expense")

    breakdown = {}
    for e in entries:
        cat = e.get("category", "其他")
        breakdown[cat] = breakdown.get(cat, 0) + e.get("amount", 0)

    net_profit = income - expense

    try:
        result = chat_text_json(
            system_prompt="你是经营助手，生成经营总结。只输出JSON: {\"summary\": \"...\"}",
            user_message=f"统计周期:{period_type}, 期间:{resolved_period}, 收入:{income}, 支出:{expense}, 净利润:{net_profit}, 分类:{breakdown}",
            max_tokens=512,
            scene="report",
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
        summary = result.get("summary", "经营报表已生成")
    except Exception:
        summary = "经营报表已生成"

    return ReportDiagnosisResponse(summary=summary)


@router.post("/health-score", response_model=HealthScoreResponse)
async def health_score(
    req: HealthScoreRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    all_entries = await _authoritative_entries(session, ctx)
    target_month = req.month or max((_entry_month(entry) for entry in all_entries if _entry_month(entry)), default="")
    entries = _filter_month(all_entries, target_month) if target_month else all_entries
    income = sum((entry.get("amount", 0) for entry in entries if entry.get("direction") == "income"), Decimal("0"))
    expense = sum((entry.get("amount", 0) for entry in entries if entry.get("direction") == "expense"), Decimal("0"))

    # 计算发票合规率
    total_expense_cnt = sum(1 for e in entries if e.get("direction") == "expense")
    with_invoice = sum(1 for e in entries
                       if e.get("direction") == "expense"
                       and e.get("billingStatus") in ("has_invoice", "has_receipt"))
    compliance_rate = round(with_invoice / total_expense_cnt * 100) if total_expense_cnt > 0 else 0

    # #10 改用分类汇总代替截断原始记录，节省 token 且覆盖全量数据
    category_summary = {}
    risk_categories = []
    for e in entries:
        cat = e.get("category", "其他")
        direction = e.get("direction", "expense")
        key = f"{direction}:{cat}"
        category_summary[key] = category_summary.get(key, 0) + e.get("amount", 0)
        if sanitize_risk_tags(e.get("riskTags", [])):
            risk_categories.append(cat)

    prompt = f"""请评估：
- 月收入:{income}, 支出:{expense}, 净利润:{income - expense}
- 记录总数:{len(entries)}, 支出笔数:{total_expense_cnt}
- 发票合规率:{compliance_rate}%
- 分类金额汇总:{category_summary}
- 含风险标签的分类:{list(set(risk_categories))}"""

    try:
        result = chat_text_json(
        HEALTH_SCORE_V3_PROMPT,
        prompt,
        max_tokens=1024,
        scene="report",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
        return HealthScoreResponse(
            totalScore=result.get("totalScore", 70),
            level=result.get("level", "一般"),
            dimensions=result.get("dimensions", {}),
            suggestions=result.get("suggestions", []),
            taxRiskScore=result.get("taxRiskScore"),
            taxRiskLevel=result.get("taxRiskLevel"),
            riskItems=result.get("riskItems"),
        )
    except Exception:
        return HealthScoreResponse(
            totalScore=70, level="一般",
            dimensions={"账务完整性": 70, "现金流健康": 70, "科目准确性": 70, "发票合规": 70, "税务风险": 70, "成本合理性": 70, "申报及时性": 70},
            suggestions=["记录更多经营数据后评分会更准确"],
        )


@router.post("/report/vat-assist", response_model=VatAssistResponse)
async def vat_assist(
    req: VatAssistRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """按已过账营业收入分录生成小规模纳税人简易申报辅助字段。"""
    period_type = "month" if req.month and not req.period else req.periodType
    period = req.period or req.month or default_completed_period(period_type)
    entries = await _authoritative_entries(session, ctx)
    return VatAssistResponse(**build_small_scale_vat_assist(entries, period, period_type))


@router.post("/report/cit-assist", response_model=CitAssistResponse)
async def cit_assist(
    req: CitAssistRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """按2025版A200000结构生成季度企业所得税预缴辅助字段。"""
    period = req.period or req.month or default_completed_period("quarter")
    statement = await build_statement_data(ctx.user_id, ctx.company_id, period, "quarter")
    profit = statement.profit_ytd
    profile = await get_user_profile(ctx.user_id)
    company = (profile or {}).get("current_company") or {}

    revenue = d2(profit.get("revenue", 0))
    operating_cost = d2(profit.get("cost", 0))
    tax_surcharge = d2(profit.get("tax_surcharge", 0))
    selling_expense = d2(profit.get("sales_expense", 0))
    research_expense = d2(profit.get("research_expense", 0))
    management_expense = d2(profit.get("management_expense", 0) - research_expense)
    finance_expense = d2(profit.get("finance_expense", 0))
    investment_income = d2(profit.get("investment_income", 0))
    operating_profit = d2(profit.get("operating_profit", 0))
    non_operating_income = d2(profit.get("non_operating_income", 0))
    non_operating_expense = d2(profit.get("non_operating_expense", 0))
    total_profit = d2(profit.get("total_profit", 0))

    actual_profit = d2(
        total_profit + req.specificBusinessIncome - req.nonTaxableIncome
        - req.acceleratedDepreciationReduction - req.exemptDeduction
        - req.incomeExemption - req.priorYearLoss
    )
    taxable_base = max(Decimal("0"), actual_profit)
    standard_tax = d2(taxable_base * Decimal("0.25"))

    asset_total_start = d2(statement.balance_period_opening.get("asset_total", 0) / Decimal("10000"))
    asset_total_end = d2(statement.balance_current.get("asset_total", 0) / Decimal("10000"))
    # 小型微利资格由客观条件组合判定（非限制行业/从业人数/资产总额/应纳税所得额），
    # 不再单独要求用户"确认属于小型微利企业"。
    company_tax_profile_complete = all(
        company.get(key) is not None
        for key in ("restrictedIndustry", "employeeCount")
    )

    if company_tax_profile_complete:
        employee_average = d2(company.get("employeeCount", 0))
        asset_average = d2((asset_total_start + asset_total_end) / 2)
        restricted_industry = bool(company.get("restrictedIndustry"))
    else:
        employee_average = None
        if req.employeeCountStart is not None and req.employeeCountEnd is not None:
            employee_average = d2((Decimal(req.employeeCountStart) + Decimal(req.employeeCountEnd)) / 2)
        asset_average = None
        if req.assetTotalStart is not None and req.assetTotalEnd is not None:
            asset_average = d2((Decimal(req.assetTotalStart) + Decimal(req.assetTotalEnd)) / 2)
        restricted_industry = req.restrictedIndustry

    eligibility_known = employee_average is not None and asset_average is not None and restricted_industry is not None
    if not eligibility_known:
        eligibility = "undetermined"
    elif (
        restricted_industry is False
        and employee_average <= Decimal("300")
        and asset_average <= Decimal("5000")
        and taxable_base <= Decimal("3000000")
    ):
        eligibility = "eligible"
    else:
        eligibility = "ineligible"

    preferential_tax = d2(taxable_base * Decimal("0.05"))
    tax_reduction = d2(standard_tax - preferential_tax) if eligibility == "eligible" else Decimal("0.00")
    estimated_tax = d2(max(
        Decimal("0"),
        standard_tax - tax_reduction - req.taxCredit
        - req.prepaidTax - req.specialBusinessPrepaidTax,
    ))

    fields = [
        ("1", "营业收入", revenue, False, "已过账利润表本年累计"),
        ("2", "减：营业成本", operating_cost, False, "已过账利润表本年累计"),
        ("3", "减：税金及附加", tax_surcharge, False, "已过账利润表本年累计"),
        ("4", "减：销售费用", selling_expense, False, "已过账利润表本年累计"),
        ("5", "减：管理费用", management_expense, False, "已过账利润表本年累计"),
        ("6", "减：研发费用", research_expense, False, "已过账利润表本年累计"),
        ("7", "减：财务费用", finance_expense, False, "已过账利润表本年累计"),
        ("9", "加：投资收益", investment_income, False, "已过账利润表本年累计"),
        ("15", "营业利润", operating_profit, False, "确定性计算"),
        ("16", "加：营业外收入", non_operating_income, False, "已过账利润表本年累计"),
        ("17", "减：营业外支出", non_operating_expense, False, "已过账利润表本年累计"),
        ("18", "利润总额", total_profit, False, "与利润表勾稽"),
        ("19", "加：特定业务计算的应纳税所得额", req.specificBusinessIncome, True, "人工确认"),
        ("20", "减：不征税收入", req.nonTaxableIncome, True, "人工确认"),
        ("21", "减：资产加速折旧、摊销调减额", req.acceleratedDepreciationReduction, True, "人工确认"),
        ("22", "减：免税收入、减计收入、加计扣除", req.exemptDeduction, True, "人工确认"),
        ("23", "减：所得减免", req.incomeExemption, True, "人工确认"),
        ("24", "减：弥补以前年度亏损", req.priorYearLoss, True, "人工确认"),
        ("25", "实际利润额", actual_profit, False, "确定性计算"),
        ("26", "税率（25%）", Decimal("25.00"), False, "法定税率"),
        ("27", "应纳所得税额", standard_tax, False, "实际利润额×25%"),
        ("28", "减：减免所得税额", tax_reduction, False, "小型微利企业资格判断"),
        ("29", "减：抵免所得税额", req.taxCredit, True, "人工确认"),
        ("30", "减：本年累计已预缴所得税额", req.prepaidTax, True, "人工确认"),
        ("31", "减：特定业务预缴所得税额", req.specialBusinessPrepaidTax, True, "人工确认"),
        ("32", "本期应补（退）所得税额", estimated_tax, False, "确定性计算"),
    ]

    blocking_issues = []
    if eligibility == "undetermined":
        blocking_issues.append("请补充季初/季末从业人数、资产总额及是否属于限制或禁止行业")
    if not req.manualAdjustmentsConfirmed:
        blocking_issues.append("请确认第19—24行纳税调整、以前年度亏损及第29—31行已预缴税额")
    warnings = []
    if eligibility == "ineligible":
        warnings.append("当前信息不满足小型微利企业条件，未计算优惠减免")
    if total_profit != statement.profit_ytd.get("net_profit", total_profit) + profit.get("income_tax", 0):
        warnings.append("利润总额与净利润勾稽存在差异，请检查所得税费用")

    beginner_ready = (
        company_tax_profile_complete
        and req.operatingCostConfirmed
        and req.totalProfitConfirmed
        and req.assetLoss is False
    )

    return CitAssistResponse(
        period=statement.period.label,
        periodStart=statement.period.fiscal_year_start.isoformat(),
        periodEnd=statement.period.end.isoformat(),
        cumulativeRevenue=revenue,
        cumulativeOperatingCost=operating_cost,
        cumulativeProfit=total_profit,
        actualProfit=actual_profit,
        standardTax=standard_tax,
        taxReduction=tax_reduction,
        estimatedTax=estimated_tax,
        smallMicroEligibility=eligibility,
        employeeAverage=employee_average,
        assetAverage=asset_average,
        filingFields=[{"line": line, "label": label, "amount": amount, "editable": editable, "source": source} for line, label, amount, editable, source in fields],
        blockingIssues=blocking_issues,
        warnings=warnings,
        readyToFile=not blocking_issues,
        policyVersion="A200000-2025-17 / CIT-SME-2023-6",
        assetTotalStart=asset_total_start,
        assetTotalEnd=asset_total_end,
        operatingCostConfirmed=req.operatingCostConfirmed,
        totalProfitConfirmed=req.totalProfitConfirmed,
        assetLoss=req.assetLoss,
        companyTaxProfileComplete=company_tax_profile_complete,
        beginnerReady=beginner_ready,
    )


@router.post("/report/archive-checklist", response_model=ArchiveChecklistResponse)
async def archive_checklist(
    req: ArchiveChecklistRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """单据归档清单"""
    items = []
    compliant = 0
    missing = 0

    entries = _filter_month(await _authoritative_entries(session, ctx), req.month)
    for e in entries:
        billing = e.get("billingStatus", "no_document")
        # unknown 和 no_document 都属于待补
        compliance = "合规" if billing in ("has_invoice", "has_receipt") else "待补"
        if compliance == "合规":
            compliant += 1
        else:
            missing += 1

        risk = None
        tags = sanitize_risk_tags(e.get("riskTags", []))
        if tags:
            risk = tags[0] if isinstance(tags, list) else str(tags)

        items.append(ArchiveItem(
            date=e.get("date") or e.get("time", ""),
            summary=e.get("title", ""),
            amount=e.get("amount", 0),
            docType=billing,
            compliance=compliance,
            riskTag=risk,
            note=e.get("explanation", ""),
        ))

    return ArchiveChecklistResponse(
        month=req.month,
        items=items,
        compliantCount=compliant,
        missingCount=missing,
        totalCount=len(items),
    )


@router.post("/report/tax-summary", response_model=TaxSummaryResponse)
async def tax_summary(
    req: TaxSummaryRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """按申报所属期汇总增值税、企业所得税预估和真实征期。"""
    period_type = "month" if req.month and not req.period else req.periodType
    period = req.period or req.month or default_completed_period(period_type)
    statement = await build_statement_data(ctx.user_id, ctx.company_id, period, period_type)
    all_entries = await _authoritative_entries(session, ctx)
    period_range = statement.period
    entries = [
        entry for entry in all_entries
        if (occurred := str(entry.get("occurDate") or entry.get("date") or ""))
        and period_range.start.isoformat() <= occurred[:10] <= period_range.end.isoformat()
    ]
    vat = build_small_scale_vat_assist(all_entries, period, period_type)
    profit = statement.profit_current
    accounting_profit = d2(profit.get("total_profit", Decimal("0")))
    taxable_income = max(Decimal("0"), accounting_profit)
    # 税务汇总页没有人数、资产及行业确认信息，不能擅自套用小型微利
    # 企业5%实际税负；先按法定25%保守展示，详细页确认后再计算减免。
    estimated_cit = d2(taxable_income * Decimal("0.25"))

    book_expenses = d2(sum(
        (d2(entry.get("amount", 0)) for entry in entries if entry.get("direction") == "expense"),
        Decimal("0"),
    ))
    deductible = d2(sum(
        (d2(entry.get("amount", 0)) for entry in entries
         if entry.get("direction") == "expense" and entry.get("taxDeductible") is True),
        Decimal("0"),
    ))
    non_deductible = d2(sum(
        (d2(entry.get("amount", 0)) for entry in entries
         if entry.get("direction") == "expense" and entry.get("taxDeductible") is False),
        Decimal("0"),
    ))
    pending_deductibility = d2(max(Decimal("0"), book_expenses - deductible - non_deductible))

    # 2026年度具体征期来自税总办征科函〔2025〕64号；其他年度的
    # 常规15日仅作待核验占位，避免把推算日期冒充官方日期。
    official_2026_due_days = {1: 20, 2: 24, 3: 16, 4: 20, 5: 22, 6: 15, 7: 15, 8: 17, 9: 15, 10: 26, 11: 16, 12: 15}
    if period_type == "quarter":
        due_month = period_range.end.month + 1
        due_year = period_range.end.year
    else:
        due_month = 1 if period_range.end.month == 12 else period_range.end.month + 1
        due_year = period_range.end.year + (1 if period_range.end.month == 12 else 0)
    verified = due_year == 2026
    due_day = official_2026_due_days[due_month] if verified else 15
    due_date = date(due_year, due_month, due_day)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    days_remaining = (due_date - today).days
    status = "overdue" if days_remaining < 0 else "due_today" if days_remaining == 0 else "due_soon" if days_remaining <= 7 else "upcoming"
    deadlines = [
        {"taxType": "增值税", "filingPeriod": period_range.label, "dueDate": due_date.isoformat(), "status": status, "daysRemaining": days_remaining, "verified": verified},
        {"taxType": "企业所得税预缴", "filingPeriod": period_range.label, "dueDate": due_date.isoformat(), "status": status, "daysRemaining": days_remaining, "verified": verified},
    ]

    blocking_issues = list(vat["blockingIssues"])
    warnings = list(vat["warnings"])
    if taxable_income > 0:
        warnings.append("企业所得税按法定25%保守暂估；确认小型微利企业资格后可在详细页计算减免")
    if pending_deductibility > 0:
        warnings.append(f"{pending_deductibility}元支出的税前扣除状态尚未确认")
    if not verified:
        warnings.append("该年度官方申报期限尚未录入，当前日期按常规15日展示并标记为待核验")
    tagged_risks = sum(1 for entry in entries if sanitize_risk_tags(entry.get("riskTags", [])))

    return TaxSummaryResponse(
        period=period_range.label,
        periodType=period_type,
        periodStart=period_range.start.isoformat(),
        periodEnd=period_range.end.isoformat(),
        totalSales=vat["totalSales"],
        accountingProfit=accounting_profit,
        taxableIncomeEstimate=d2(taxable_income),
        estimatedVat=vat["totalVatPayable"],
        estimatedCit=estimated_cit,
        vatExemption=vat["vatExemption"],
        bookExpenses=book_expenses,
        totalDeductible=deductible,
        totalNonDeductible=non_deductible,
        pendingDeductibility=pending_deductibility,
        riskCount=tagged_risks + len(blocking_issues),
        filingDeadlines=deadlines,
        blockingIssues=blocking_issues,
        warnings=warnings,
        readyToFile=not blocking_issues,
        policyVersion=f"{vat['policyVersion']} / CIT-SME-2023-6",
        reminders=[
            "小规模纳税人进项税额不抵扣；支出卡片表示企业所得税税前扣除资料状态",
            "业务招待费、广告宣传费等限额扣除项目需在企业所得税申报时调整",
            "本页为申报辅助，提交前应核对电子税务局预填数据和主管税务机关要求",
        ],
    )


@router.post("/report/income-statement", response_model=IncomeStatementResponse)
async def income_statement(
    req: IncomeStatementRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """利润表：支持总表（本月数/本年累计）与季报（本年累计/四季度）。"""
    entries = await _authoritative_entries(session, ctx)
    columns = _income_statement_columns(req.viewMode)
    period_values = {col["key"]: _empty_income_statement_values() for col in columns}

    for entry in entries:
        month = _entry_month(entry)
        if not month:
            continue
        period_keys = _income_statement_period_keys(month, req.month, req.viewMode)
        if not period_keys:
            continue

        entry_values = _income_statement_entry_values(entry)
        for period_key in period_keys:
            for key, amount in entry_values.items():
                period_values[period_key][key] += amount

    for key in period_values:
        period_values[key] = _finalize_income_statement_values(period_values[key])

    rows = []
    for key, label, level, is_subtotal in INCOME_STATEMENT_ROW_DEFS:
        rows.append(IncomeStatementRow(
            key=key,
            label=label,
            level=level,
            values={col["key"]: _money(period_values[col["key"]][key]) for col in columns},
            isSubtotal=is_subtotal,
        ))

    totals = {
        "operatingProfit": {col["key"]: _money(period_values[col["key"]]["operating_profit"]) for col in columns},
        "profitTotal": {col["key"]: _money(period_values[col["key"]]["profit_total"]) for col in columns},
        "netProfit": {col["key"]: _money(period_values[col["key"]]["net_profit"]) for col in columns},
    }

    return IncomeStatementResponse(
        month=req.month,
        viewMode=req.viewMode,
        columns=columns,
        rows=rows,
        totals=totals,
    )


@router.post("/report/balance-sheet", response_model=BalanceSheetResponse)
async def balance_sheet(
    req: BalanceSheetRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """资产负债表：与税务报表共用已过账复式分录及科目余额口径。"""
    statement = await build_statement_data(ctx.user_id, ctx.company_id, req.month, "month")
    balance = statement.balance_current
    current_profit = statement.profit_ytd.get("net_profit", Decimal("0"))
    prior_retained = balance.get("retained_earnings", Decimal("0")) - current_profit

    def item(code: str, name: str, amount: Decimal) -> BalanceSheetItem:
        return BalanceSheetItem(accountCode=code, accountName=name, currentAmount=d2(amount))

    def nonzero_items(rows: list[tuple[str, str, str, int]]) -> list[BalanceSheetItem]:
        result: list[BalanceSheetItem] = []
        for code, name, key, sign in rows:
            amount = balance.get(key, Decimal("0")) * sign
            if amount:
                result.append(item(code, name, amount))
        return result

    assets = [item("1002", "货币资金", balance.get("cash", Decimal("0")))]
    assets.extend(nonzero_items([
        ("1101", "短期投资", "short_investment", 1),
        ("1121", "应收票据", "notes_receivable", 1),
        ("1122", "应收账款", "accounts_receivable", 1),
        ("1123", "预付账款", "prepaid", 1),
        ("1131", "应收股利", "dividends_receivable", 1),
        ("1132", "应收利息", "interest_receivable", 1),
        ("1221", "其他应收款", "other_receivable", 1),
        ("1405", "存货", "inventory", 1),
        ("1501", "长期债券投资", "long_bond_investment", 1),
        ("1511", "长期股权投资", "long_equity_investment", 1),
        ("1601", "固定资产原价", "fixed_asset_cost", 1),
        ("1602", "累计折旧", "accumulated_depreciation", -1),
        ("1604", "在建工程", "construction", 1),
        ("1701", "无形资产", "intangible_asset", 1),
        ("1801", "长期待摊费用", "long_prepaid", 1),
    ]))

    liabilities = nonzero_items([
        ("2001", "短期借款", "short_borrow", 1),
        ("2201", "应付票据", "notes_payable", 1),
        ("2202", "应付账款", "accounts_payable", 1),
        ("2203", "预收账款", "advance_receipt", 1),
        ("2211", "应付职工薪酬", "salary_payable", 1),
        ("2221", "应交税费", "tax_payable", 1),
        ("2231", "应付利息", "interest_payable", 1),
        ("2232", "应付利润", "profit_payable", 1),
        ("2241", "其他应付款", "other_payable", 1),
        ("2501", "长期借款", "long_borrow", 1),
        ("2701", "长期应付款", "long_payable", 1),
        ("2401", "递延收益", "deferred_income", 1),
    ])

    equity = nonzero_items([
        ("3001", "实收资本", "paid_in_capital", 1),
        ("3002", "资本公积", "capital_reserve", 1),
        ("3101", "盈余公积", "surplus_reserve", 1),
    ])
    equity.append(item("3103", "本年利润", current_profit))
    equity.append(item("3104", "利润分配-未分配利润", prior_retained))

    total_assets = d2(balance.get("asset_total", Decimal("0")))
    total_liabilities = d2(balance.get("liability_total", Decimal("0")))
    total_equity = d2(balance.get("equity_total", Decimal("0")))

    return BalanceSheetResponse(
        month=req.month,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        totalAssets=total_assets,
        totalLiabilities=total_liabilities,
        totalEquity=total_equity,
        isBalanced=total_assets == total_liabilities + total_equity,
    )


@router.post("/report/cash-flow", response_model=CashFlowResponse)
async def cash_flow(
    req: CashFlowRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """现金流量表：仅从已过账凭证的现金科目及对方科目归集。"""
    statement = await build_statement_data(ctx.user_id, ctx.company_id, req.month, "month")
    cash = statement.cash_current
    balance_sheet_cash = d2(statement.balance_current.get("cash", Decimal("0")))

    def cfi(cat: str, desc: str, inflow: Decimal = Decimal("0"), outflow: Decimal = Decimal("0")):
        return CashFlowItem(
            category=cat, description=desc,
            inflow=d2(inflow), outflow=d2(outflow),
            netAmount=d2(inflow - outflow),
        )

    operating = [
        cfi("经营活动", "销售产成品、商品、提供劳务收到的现金", cash["sales_cash"]),
        cfi("经营活动", "收到其他与经营活动有关的现金", cash["other_operating_in"]),
        cfi("经营活动", "购买原材料、商品、接受劳务支付的现金", outflow=cash["purchase_cash"]),
        cfi("经营活动", "支付的职工薪酬", outflow=cash["salary_cash"]),
        cfi("经营活动", "支付的税费", outflow=cash["tax_cash"]),
        cfi("经营活动", "支付其他与经营活动有关的现金", outflow=cash["other_operating_out"]),
    ]
    investing = [
        cfi("投资活动", "收回投资收到的现金", cash["investment_in"]),
        cfi("投资活动", "取得投资收益收到的现金", cash["investment_income"]),
        cfi("投资活动", "处置固定资产、无形资产和其他非流动资产收回的现金净额", cash["asset_disposal_cash"]),
        cfi("投资活动", "投资支付的现金", outflow=cash["investment_out"]),
        cfi("投资活动", "购建固定资产、无形资产和其他非流动资产支付的现金", outflow=cash["fixed_asset_cash"]),
    ]
    financing = [
        cfi("筹资活动", "取得借款收到的现金", cash["borrow_cash"]),
        cfi("筹资活动", "吸收投资者投资收到的现金", cash["capital_cash"]),
        cfi("筹资活动", "偿还借款本金支付的现金", outflow=cash["repay_borrow_cash"]),
        cfi("筹资活动", "偿还借款利息支付的现金", outflow=cash["interest_cash"]),
        cfi("筹资活动", "分配利润支付的现金", outflow=cash["profit_distribution_cash"]),
    ]

    op_net = d2(cash["operating_net"])
    inv_net = d2(cash["investing_net"])
    fin_net = d2(cash["financing_net"])
    net_increase = d2(cash["net_increase"])
    opening_cash = d2(cash["opening_cash"])
    ending_cash = d2(cash["ending_cash"])
    classification_difference = d2(op_net + inv_net + fin_net - net_increase)
    roll_forward_difference = d2(opening_cash + net_increase - ending_cash)
    balance_sheet_difference = d2(ending_cash - balance_sheet_cash)

    return CashFlowResponse(
        month=req.month,
        operating=operating,
        investing=investing,
        financing=financing,
        operatingNet=d2(op_net),
        investingNet=d2(inv_net),
        financingNet=d2(fin_net),
        netIncrease=net_increase,
        openingCash=opening_cash,
        endingCash=ending_cash,
        balanceSheetCash=balance_sheet_cash,
        classificationDifference=classification_difference,
        cashRollForwardDifference=roll_forward_difference,
        balanceSheetCashDifference=balance_sheet_difference,
        classificationBalanced=classification_difference == 0,
        cashRollForwardBalanced=roll_forward_difference == 0,
        balanceSheetCashBalanced=balance_sheet_difference == 0,
    )


def _filter_month(entries: list, month: str) -> list:
    """Filter entries by occurDate prefix (YYYY-MM)."""
    return [e for e in entries if (e.get("occurDate") or "").startswith(month)]


def _compute_totals(entries: list) -> tuple:
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    expense = sum(e.get("amount", 0) for e in entries if e.get("direction") == "expense")
    profit = income - expense
    return d2(income), d2(expense), d2(profit)


def _category_breakdown(entries: list) -> dict:
    breakdown = {}
    for e in entries:
        cat = e.get("category", "其他")
        direction = e.get("direction", "expense")
        key = f"{direction}:{cat}"
        breakdown[key] = breakdown.get(key, 0) + e.get("amount", 0)
    return breakdown


def _compute_change(base: Decimal, compare: Decimal) -> Optional[Decimal]:
    if base and base != 0:
        return d2((compare - base) / base * Decimal("100"))
    return None


@router.post("/report/comparison", response_model=ComparisonResponse)
async def comparison(
    req: ComparisonRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """同比/环比分析"""
    entries = await _authoritative_entries(session, ctx)

    base_entries = _filter_month(entries, req.base_month)

    # Determine compare month
    base_year = int(req.base_month[:4])
    base_month = int(req.base_month[5:7])
    if req.compare_mode == "mom":
        if base_month == 1:
            compare_month = f"{base_year - 1}-12"
        else:
            compare_month = f"{base_year}-{base_month - 1:02d}"
    else:  # yoy
        compare_month = f"{base_year - 1}-{base_month:02d}"

    compare_entries = _filter_month(entries, compare_month)

    base_inc, base_exp, base_prof = _compute_totals(base_entries)
    comp_inc, comp_exp, comp_prof = _compute_totals(compare_entries)

    # Category breakdown
    base_cats = _category_breakdown(base_entries)
    comp_cats = _category_breakdown(compare_entries)

    all_cats = set(list(base_cats.keys()) + list(comp_cats.keys()))
    breakdown = []
    for cat in sorted(all_cats):
        b_amt = d2(base_cats.get(cat, 0))
        c_amt = d2(comp_cats.get(cat, 0))
        change = _compute_change(b_amt, c_amt)
        breakdown.append(ComparisonItem(
            category=cat,
            base_amount=b_amt,
            compare_amount=c_amt,
            change_percent=change,
        ))

    return ComparisonResponse(
        base_month=req.base_month,
        compare_month=compare_month,
        base_income=base_inc,
        base_expense=base_exp,
        base_profit=base_prof,
        compare_income=comp_inc,
        compare_expense=comp_exp,
        compare_profit=comp_prof,
        income_change_percent=_compute_change(base_inc, comp_inc),
        expense_change_percent=_compute_change(base_exp, comp_exp),
        profit_change_percent=_compute_change(base_prof, comp_prof),
        category_breakdown=breakdown,
    )


class AIDiagnosisRequest(BaseModel):
    month: str


@router.post("/report/ai-diagnosis")
async def ai_diagnosis(
    req: AIDiagnosisRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """AI 财务经理经营诊断"""
    from pydantic import BaseModel as BM
    entries = await _authoritative_entries(session, ctx)
    period_entries = [e for e in entries if (e.get("occurDate") or "").startswith(req.month)]

    income = sum(e.get("amount", 0) for e in period_entries if e.get("direction") == "income")
    expense = sum(e.get("amount", 0) for e in period_entries if e.get("direction") == "expense")
    net_profit = income - expense

    # Category breakdown
    category_breakdown = {}
    for e in period_entries:
        cat = e.get("category", "其他")
        direction = e.get("direction", "expense")
        key = f"{direction}:{cat}"
        category_breakdown[key] = round(category_breakdown.get(key, 0) + e.get("amount", 0), 2)

    # Invoice compliance
    expense_entries = [e for e in period_entries if e.get("direction") == "expense"]
    with_invoice = sum(1 for e in expense_entries if e.get("billingStatus") in ("has_invoice", "has_receipt"))
    compliance_rate = round(with_invoice / len(expense_entries) * 100) if expense_entries else 0

    # Risk tags
    risk_entries = [
        {"title": e.get("title", ""), "category": e.get("category", ""), "amount": e.get("amount", 0)}
        for e in period_entries
        if sanitize_risk_tags(e.get("riskTags", []))
    ]

    # Boss personal payments
    boss_pay_count = sum(1 for e in period_entries if e.get("payer") == "boss_personal")
    boss_pay_amount = sum(e.get("amount", 0) for e in period_entries if e.get("payer") == "boss_personal")

    context = f"""请为这家小微企业生成本月经营诊断：

【{req.month} 月度经营数据】
- 总收入：¥{income:,.2f}
- 总支出：¥{expense:,.2f}
- 净利润：¥{net_profit:,.2f}（利润率 {round(net_profit / income * 100, 1) if income > 0 else 0}%）
- 记录笔数：{len(period_entries)} 笔
- 发票合规率：{compliance_rate}%（{with_invoice}/{len(expense_entries)} 笔支出有票据）

【分类金额明细】
{category_breakdown}

【风险项目】
{risk_entries if risk_entries else '无'}

【股东个人垫付】
{f'共 {boss_pay_count} 笔，合计 ¥{boss_pay_amount:,.2f}' if boss_pay_count > 0 else '无'}"""

    try:
        result = chat_text_json(CFO_DIAGNOSIS_PROMPT, context, max_tokens=1024)
        return {
            "diagnosis": result.get("diagnosis", "数据不足，暂无法生成诊断报告。"),
            "keyMetrics": result.get("keyMetrics", []),
            "urgentActions": result.get("urgentActions", []),
            "disclaimer": result.get("disclaimer", "以上分析仅供经营参考，具体决策以专业财税人员意见为准"),
            "generatedAt": _now_iso(),
        }
    except Exception:
        return {
            "diagnosis": "暂时无法生成 AI 诊断，请稍后再试。",
            "keyMetrics": [],
            "urgentActions": [],
            "disclaimer": "以上分析仅供经营参考，具体决策以专业财税人员意见为准",
            "generatedAt": _now_iso(),
        }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
