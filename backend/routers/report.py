# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""V3 报表路由：月报 + 增值税/CIT/归档/税负/健康评分"""
import asyncio
import logging
from typing import Literal, Optional
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from core.money import ZERO, money, money_text
from deps.auth import AuthContext, require_auth
from models.schemas import (
    d2,
    MonthlyReportRequest, MonthlyReportResponse,
    ReportDiagnosisRequest, ReportDiagnosisResponse,
    HealthScoreRequest, HealthScoreResponse,
    VatAssistRequest, VatAssistResponse, VatAssistItem,
    CitAssistRequest, CitAssistResponse,
    ArchiveChecklistRequest, ArchiveChecklistResponse, ArchiveItem,
    BalanceSheetRequest, BalanceSheetResponse, BalanceSheetItem,
    StandardStatementRow, BalanceSheetStandard,
    CashFlowRequest, CashFlowResponse, CashFlowItem,
    EquityChangeRequest,
    IncomeStatementRequest, IncomeStatementResponse, IncomeStatementRow,
    ComparisonRequest, ComparisonResponse, ComparisonItem,
)
from core.errors import AccountingError
from services.llm import chat_text_json, chat_text
from services.ocr import HEALTH_SCORE_V3_PROMPT
from services.diagnosis_prompts import CFO_DIAGNOSIS_PROMPT
from services.tax_rules import compute_vat, lookup_tax, sanitize_risk_tags
from db.session import get_db_session
from services.accounting_core_service import (
    VAT_REVENUE_ACCOUNT_CODES,
    ensure_tenant,
    journal_entries_for_reports,
    posted_entries_for_reports,
)
from services.tax_filing_export_service import build_statement_data, default_completed_period
from services.equity_service import get_equity_changes
from services.vat_filing_service import build_small_scale_vat_assist
from services.vat_service import VAT_PAYABLE_ACCOUNT
from services.health_score_service import calculate_health_score
from services.cit_adjustment_service import build_cit_adjustments
from services.account_service import get_user_profile
from services import company_entity_service, headcount_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["report"], dependencies=[Depends(require_auth)])


def _as_bool(value) -> Optional[bool]:
    """把历史 payload 里的布尔值归一（兼容 "yes"/"no"/"是"/"否"/0/1/""）。

    返回 None 表示"未确认"，而不是 False——避免老数据把未填写误判为"非限制行业"。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("yes", "true", "1", "是"):
            return True
        if text in ("no", "false", "0", "否"):
            return False
    return None


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


async def _management_entries(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    include_drafts: bool,
) -> list[dict]:
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    return await journal_entries_for_reports(
        session, ctx.company_id, include_drafts=include_drafts,
    )


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


def _entry_month(entry: dict, basis: str = "posting") -> Optional[str]:
    """凭证归属月份。

    basis="posting"：按**记账期间**（法定口径，与账簿/申报一致）；
    basis="business"：按**业务日期**（管理口径，能看出业务实际发生在哪个月）。
    两者只在跨期补记时不同——补记凭证的 period 是本期，occurDate 是上个月。
    """
    if basis == "business":
        candidates = (entry.get("occurDate"), entry.get("date"), entry.get("time"))
    else:
        candidates = (entry.get("period"), entry.get("occurDate"), entry.get("date"), entry.get("time"))
    date_value = next((value for value in candidates if value), "") or entry.get("created_at") or ""
    if isinstance(date_value, str) and len(date_value) >= 7:
        return date_value[:7]
    return None


async def _backfill_impact(session: AsyncSession, company_id: str, months: list[str]) -> dict:
    """本期（或本报告期）内的补记笔数与金额，按原期间分组。

    只读 period_backfills 台账，不改任何金额——它回答「这张表里有多少东西
    其实不是这个月的业务」。
    """
    from db.models import PeriodBackfill

    if not months:
        return {"count": 0, "amount": "0.00", "periods": []}
    period_starts = [date(int(m[:4]), int(m[5:7]), 1) for m in months]
    rows = (
        await session.execute(
            select(
                PeriodBackfill.original_period,
                func.count(PeriodBackfill.id),
                func.coalesce(func.sum(PeriodBackfill.amount), 0),
            )
            .where(
                PeriodBackfill.company_id == company_id,
                PeriodBackfill.posting_period.in_(period_starts),
                PeriodBackfill.status == "active",
            )
            .group_by(PeriodBackfill.original_period)
            .order_by(PeriodBackfill.original_period)
        )
    ).all()
    periods = [
        {
            "originalPeriod": row[0].strftime("%Y-%m"),
            "count": int(row[1] or 0),
            "amount": money_text(money(row[2] or 0)),
        }
        for row in rows
    ]
    return {
        "count": sum(item["count"] for item in periods),
        "amount": money_text(sum((money(item["amount"]) for item in periods), ZERO)),
        "periods": periods,
    }


def _report_months(report_month: str, view_mode: str) -> list[str]:
    year = int(report_month[:4])
    last = int(report_month[5:7])
    if view_mode == "quarterly":
        return [f"{year}-{month:02d}" for month in range(1, last + 1)]
    return [report_month]


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


def _is_pnl_expense(entry: dict) -> bool:
    """月度报表的"支出"必须是费用口径，而不是资金流出口径。

    存货/设备/预付类支出在复式账里是资产，不进利润表；若按 direction 聚合，
    买一台 Mac 会让当月"支出"暴增、利润虚减，与利润表净利润对不上。
    以凭证借方科目为准判断。
    """
    if entry.get("direction") != "expense":
        return False
    category = str(entry.get("category") or "")
    if category in _NON_PNL_EXPENSE_CATEGORIES:
        return False
    voucher = entry.get("voucherLines") or {}
    debit_codes = [str(line.get("accountCode") or "") for line in voucher.get("debit", []) or []]
    if debit_codes and all(code.startswith(_ASSET_DEBIT_PREFIXES) for code in debit_codes):
        return False
    return True


_NON_PNL_EXPENSE_CATEGORIES = {"原材料采购", "商品采购", "固定资产", "设备采购", "无形资产", "预付账款"}
_ASSET_DEBIT_PREFIXES = ("14", "16", "17", "1123", "160")


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
    if code == "4301002" or code.startswith("4301002"):
        return None  # 研发支出-资本化是资产（开发支出），不进利润表
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
        asset_categories = ("原材料采购", "商品采购", "固定资产", "设备采购", "无形资产", "预付账款")
        if category in asset_categories or code in ("1403", "1405", "1601", "1701", "1123", "2221"):
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
    if entry.get("source") == "wechat_settlement" or entry.get("isClosingTransfer"):
        return values
    # 期初凭证不进利润表；但年中建账的 YTD 过渡凭证承载启用前损益，必须计入
    if entry.get("entryType") == "opening" and not str(entry.get("id") or "").endswith("-ytd"):
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
    now = datetime.now()
    if not period:
        return f"{now.year}-{now.month:02d}"
    if period_type == "year":
        year = int(period[:4])
        return f"{year}-{now.month:02d}" if year == now.year else f"{year}-12"
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
    include_drafts = bool(req.includeDrafts)
    all_entries = await _management_entries(
        session, ctx, include_drafts=include_drafts,
    )
    entries = _filter_period(all_entries, resolved_period, period_type)
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    # 支出按费用口径聚合（资产采购不进当期支出），与复式利润表一致
    pnl_entries = [e for e in entries if _is_pnl_expense(e)]
    expense = sum(e.get("amount", 0) for e in pnl_entries)
    asset_purchase = d2(sum(
        (d2(e.get("amount", 0)) for e in entries
         if e.get("direction") == "expense" and not _is_pnl_expense(e)),
        Decimal("0"),
    ))

    breakdown = {}
    for e in pnl_entries:
        cat = e.get("category", "其他")
        breakdown[cat] = breakdown.get(cat, 0) + e.get("amount", 0)

    # 增值税取账面已计提额（2221026 凭证行），不再累加 payload 里按 6% 估算的历史 vatAmount
    total_vat = sum(
        (d2(line.get("amount") or 0) for e in entries
         for line in (e.get("voucherLines") or {}).get("credit", [])
         if str(line.get("accountCode") or "") == "2221026"),
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
            result = await asyncio.to_thread(
                chat_text_json,
                system_prompt="你是经营助手，生成经营总结。只输出JSON: {\"summary\": \"...\"}",
                user_message=f"统计周期:{period_type}, 期间:{resolved_period}, 收入:{income}, 支出:{expense}, 净利润:{net_profit}, 分类:{breakdown}",
                max_tokens=512,
                scene="report",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
            )
            summary = result.get("summary", "经营报表已生成")
        except AccountingError:
            raise  # 配额超限保持 402
        except Exception:
            summary = "经营报表已生成"

    available_cash = None
    pending_receivables = None
    pending_payables = None
    try:
        end_month = _period_end_month(resolved_period, period_type)
        statement = await build_statement_data(
            ctx.user_id,
            ctx.company_id,
            end_month,
            "month",
            include_drafts=include_drafts,
        )
        available_cash = d2(statement.balance_current.get("cash", Decimal("0")))
        pending_receivables = d2(
            statement.balance_current.get("accounts_receivable", Decimal("0"))
            + statement.balance_current.get("other_receivable", Decimal("0"))
        )
        pending_payables = d2(
            statement.balance_current.get("accounts_payable", Decimal("0"))
            + statement.balance_current.get("other_payable", Decimal("0"))
        )
    except Exception:
        pass

    return MonthlyReportResponse(
        summary=summary, monthIncome=income, monthExpense=expense,
        netProfit=net_profit, categoryBreakdown=breakdown,
        taxSummary=tax_summary,
        availableCash=available_cash,
        pendingReceivables=pending_receivables,
        pendingPayables=pending_payables,
        assetPurchase=asset_purchase,
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
    include_drafts = bool(req.includeDrafts)
    all_entries = await _management_entries(
        session, ctx, include_drafts=include_drafts,
    )
    entries = _filter_period(all_entries, resolved_period, period_type)
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    # 支出按费用口径聚合（资产采购不进当期支出），与复式利润表一致
    pnl_entries = [e for e in entries if _is_pnl_expense(e)]
    expense = sum(e.get("amount", 0) for e in pnl_entries)
    asset_purchase = d2(sum(
        (d2(e.get("amount", 0)) for e in entries
         if e.get("direction") == "expense" and not _is_pnl_expense(e)),
        Decimal("0"),
    ))

    breakdown = {}
    for e in pnl_entries:
        cat = e.get("category", "其他")
        breakdown[cat] = breakdown.get(cat, 0) + e.get("amount", 0)

    net_profit = income - expense

    try:
        result = await asyncio.to_thread(
            chat_text_json,
            system_prompt="你是经营助手，生成经营总结。只输出JSON: {\"summary\": \"...\"}",
            user_message=f"统计周期:{period_type}, 期间:{resolved_period}, 收入:{income}, 支出:{expense}, 净利润:{net_profit}, 分类:{breakdown}",
            max_tokens=512,
            scene="report",
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
        summary = result.get("summary", "经营报表已生成")
    except AccountingError:
        raise  # 配额超限保持 402
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

    checks = None
    try:
        from services.accounting_core_service import period_close_checks
        checks = await period_close_checks(session, ctx.company_id, target_month)
    except Exception:
        checks = None

    todos = None
    try:
        from services.todo_service import list_todos
        todos = await list_todos(ctx.user_id, ctx.company_id)
    except Exception:
        todos = None

    score_result = calculate_health_score(
        entries=entries,
        all_entries=all_entries,
        target_month=target_month,
        checks=checks,
        todos=todos,
    )

    if req.includeDiagnosis:
        try:
            prompt = f"""基于以下确定性财务体检结果生成一段简明通俗的 CFO 点评（2-3句话）与改进建议：
- 月份：{target_month}
- 健康总分：{score_result['totalScore']} ({score_result['level']})
- 扣分项与问题：{[p['title'] for p in score_result['penalties']]}
- 核心指标：{score_result['keyMetrics']}"""
            llm_res = await asyncio.to_thread(
                chat_text_json,
                "你是企业 CFO 经营专家。请输出简明 JSON: {\"overview\": \"...\", \"actionPriorities\": [\"...\"]}",
                prompt,
                max_tokens=512,
                scene="report",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
            )
            score_result["aiDiagnosis"] = llm_res
        except AccountingError:
            raise
        except Exception:
            score_result["aiDiagnosis"] = {
                "overview": score_result["summary"],
                "actionPriorities": score_result["suggestions"],
            }

    return HealthScoreResponse(**score_result)


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
    from services.vat_service import UnsupportedTaxpayerType, get_vat_config_from_session

    try:
        config = await get_vat_config_from_session(session, ctx.company_id)
    except UnsupportedTaxpayerType as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VatAssistResponse(
        **build_small_scale_vat_assist(entries, period, period_type, rate_value=config.rate)
    )


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

    # 纳税调增的确定性计算：招待费 60%/5‰、广宣费 15%、罚款滞纳金、无票支出。
    # 这是 A105000 的简化口径，结果作为第 19-24 行的系统测算默认值，用户可修改。
    if session is not None:
        all_entries = await posted_entries_for_reports(session, ctx.company_id)
        year_entries = [
            entry for entry in all_entries
            if (occurred := str(entry.get("occurDate") or "")[:10])
            and statement.period.fiscal_year_start.isoformat() <= occurred <= statement.period.end.isoformat()
        ]
    else:
        year_entries = []
    adjustments = build_cit_adjustments(
        year_entries, profit, statement.period.fiscal_year_start, statement.period.end,
    )
    auto_adjustment_total = d2(adjustments["totalIncrease"])

    actual_profit = d2(
        total_profit + auto_adjustment_total + req.specificBusinessIncome - req.nonTaxableIncome
        - req.acceleratedDepreciationReduction - req.exemptDeduction
        - req.incomeExemption - req.priorYearLoss
    )
    taxable_base = max(Decimal("0"), actual_profit)
    standard_tax = d2(taxable_base * Decimal("0.25"))

    asset_total_start = d2(statement.balance_period_opening.get("asset_total", 0) / Decimal("10000"))
    asset_total_end = d2(statement.balance_current.get("asset_total", 0) / Decimal("10000"))
    # 小型微利资格由客观条件组合判定（非限制行业/从业人数/资产总额/应纳税所得额）。
    # 从业人数从员工名册派生（公司制下限 1），payload 里的历史 employeeCount 不再读取。
    asset_average = d2((asset_total_start + asset_total_end) / 2)
    restricted_industry = _as_bool(company.get("restrictedIndustry"))
    company_tax_profile_complete = restricted_industry is not None

    employee_average = None
    headcount_failed = False
    company_id = company.get("company_id") or ctx.company_id
    try:
        headcount = await headcount_service.get_headcount(
            company_id, company_entity_service.normalize_entity_type(company.get("type"))
        )
        employee_average = d2(headcount.count)
    except Exception:  # noqa: BLE001 —— 名册读取失败不拖垮整个测算
        headcount_failed = True
        logger.warning("cit-assist 从业人数读取失败 company=%s", company_id, exc_info=True)
        # 仅此场景回退到请求体（季初/季末平均），并把失败暴露为阻断项
        if req.employeeCountStart is not None and req.employeeCountEnd is not None:
            employee_average = d2((Decimal(req.employeeCountStart) + Decimal(req.employeeCountEnd)) / 2)

    eligibility_known = employee_average is not None and restricted_industry is not None
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
        ("18A", "加：纳税调增（系统测算）", auto_adjustment_total, False, "招待费/广宣费限额、罚款滞纳金、无票支出"),
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
    if restricted_industry is None:
        blocking_issues.append("请在「我的 → 主体管理」补充所属行业（系统按行业自动判定是否属于限制/禁止行业）")
    if employee_average is None:
        blocking_issues.append("暂时无法读取员工名册，请先在「薪酬社保」录入在职员工")
    if not req.manualAdjustmentsConfirmed:
        blocking_issues.append("请确认第19—24行纳税调整、以前年度亏损及第29—31行已预缴税额")
    warnings = []
    if headcount_failed and employee_average is not None:
        warnings.append("员工名册读取失败，本次从业人数使用申报表填报值")
    if eligibility == "ineligible":
        warnings.append("当前信息不满足小型微利企业条件，未计算优惠减免")
    if total_profit != statement.profit_ytd.get("net_profit", total_profit) + profit.get("income_tax", 0):
        warnings.append("利润总额与净利润勾稽存在差异，请检查所得税费用")
    if auto_adjustment_total > 0:
        warnings.append(f"系统已按规则自动纳税调增 {auto_adjustment_total}（明细见 autoAdjustments），补票后可转回")

    beginner_ready = (
        company_tax_profile_complete
        and employee_average is not None
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
        autoAdjustmentTotal=auto_adjustment_total,
        autoAdjustments=adjustments["items"],
    )


@router.post("/report/archive-checklist", response_model=ArchiveChecklistResponse)
async def archive_checklist(
    req: ArchiveChecklistRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """单据归档清单：检查凭证单据的完备度与合规度，辅助档案装订与汇算清缴排查。"""
    items = []
    compliant = 0
    missing = 0
    compliant_amount = Decimal("0")
    missing_amount = Decimal("0")
    total_amount = Decimal("0")

    entries = _filter_month(await _authoritative_entries(session, ctx), req.month)
    for e in entries:
        billing = e.get("billingStatus", "no_document")
        amt = Decimal(str(e.get("amount") or 0))
        total_amount += abs(amt)

        # unknown 和 no_document 都属于待补
        if billing in ("has_invoice", "has_receipt"):
            compliance = "合规"
            compliance_status = "compliant"
            compliant += 1
            compliant_amount += abs(amt)
        else:
            compliance = "待补"
            compliance_status = "missing"
            missing += 1
            missing_amount += abs(amt)

        risk = None
        tags = sanitize_risk_tags(e.get("riskTags", []))
        if tags:
            risk = tags[0] if isinstance(tags, list) else str(tags)
            if compliance_status == "compliant" and risk:
                compliance_status = "questionable"

        voucher_no = e.get("voucher_no") or e.get("voucherNo") or e.get("voucherRef")
        attachment_count = 1 if billing in ("has_invoice", "has_receipt") else 0

        items.append(ArchiveItem(
            id=str(e.get("id")) if e.get("id") else None,
            voucherNo=str(voucher_no) if voucher_no else None,
            date=e.get("date") or e.get("occurDate") or e.get("time", ""),
            summary=e.get("title", ""),
            amount=amt,
            direction=e.get("direction"),
            category=e.get("category"),
            docType=billing,
            invoiceNumber=e.get("invoiceNumber"),
            invoiceType=e.get("invoiceType"),
            compliance=compliance,
            complianceStatus=compliance_status,
            riskTag=risk,
            attachmentCount=attachment_count,
            note=e.get("explanation", ""),
        ))

    return ArchiveChecklistResponse(
        month=req.month,
        items=items,
        compliantCount=compliant,
        missingCount=missing,
        totalCount=len(items),
        compliantAmount=compliant_amount,
        missingAmount=missing_amount,
        totalAmount=total_amount,
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
        month = _entry_month(entry, req.basis)
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

    # §10 三项常驻字段：按凭证行净额直接取数（与免征转出同一口径），
    # 不依赖文本分类。让用户看见系统知道自己不知道什么。
    revenue_codes = VAT_REVENUE_ACCOUNT_CODES
    net_sales = Decimal("0")
    vat_accrued = Decimal("0")
    for entry in entries:
        lines = entry.get("voucherLines") or {}
        for side, sign in (("credit", 1), ("debit", -1)):
            for line in lines.get(side) or []:
                code = str(line.get("accountCode") or "")
                if code in revenue_codes:
                    net_sales += sign * d2(line.get("amount") or 0)
                elif code == VAT_PAYABLE_ACCOUNT:
                    vat_accrued += sign * d2(line.get("amount") or 0)
    adjustments = build_cit_adjustments(entries, {"revenue": max(net_sales, Decimal("0"))})
    vat_summary = {
        "netSales": _money(net_sales),
        "vatAccrued": _money(vat_accrued),
        "pendingCitAdjustment": _money(adjustments["totalIncrease"]),
        "pendingCitItems": len(adjustments["items"]),
    }

    backfill_impact = await _backfill_impact(
        session, ctx.company_id, _report_months(req.month, req.viewMode)
    )
    return IncomeStatementResponse(
        month=req.month,
        viewMode=req.viewMode,
        columns=columns,
        rows=rows,
        totals=totals,
        vatSummary=vat_summary,
        basis=req.basis,
        backfillImpact=backfill_impact,
    )


def _period_label(month: str, period_type: str) -> str:
    """month 钉住期间 + periodType → 期间标签（month 原样；quarter 取所在季度）。"""
    if period_type != "quarter":
        return month
    year = int(month[:4])
    month_num = int(month[5:7])
    return f"{year}-Q{(month_num - 1) // 3 + 1}"


@router.post("/report/balance-sheet", response_model=BalanceSheetResponse)
async def balance_sheet(
    req: BalanceSheetRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """资产负债表：与税务报表共用已过账复式分录及科目余额口径；支持月报/季报。

    同一份数据返回两种视图：boss（非零科目明细）与 standard（会小企01表全量行次，
    含年初/期末两列）。年初余额取自 balance_opening——有显式期初凭证用期初，
    否则从历史分录派生（见 opening_balance_service 跨年连续性）。
    """
    period = _period_label(req.month, req.periodType)
    statement = await build_statement_data(ctx.user_id, ctx.company_id, period, req.periodType)
    balance = statement.balance_current
    opening = statement.balance_opening
    current_profit = statement.profit_ytd.get("net_profit", Decimal("0"))
    prior_retained = balance.get("retained_earnings", Decimal("0")) - current_profit

    def item(code: str, name: str, key: str, sign: int = 1) -> BalanceSheetItem:
        return BalanceSheetItem(
            accountCode=code,
            accountName=name,
            currentAmount=d2(balance.get(key, Decimal("0")) * sign),
            previousAmount=d2(opening.get(key, Decimal("0")) * sign),
        )

    def nonzero_items(rows: list[tuple[str, str, str, int]]) -> list[BalanceSheetItem]:
        result: list[BalanceSheetItem] = []
        for code, name, key, sign in rows:
            amount = balance.get(key, Decimal("0")) * sign
            if amount:
                result.append(item(code, name, key, sign))
        return result

    assets = [item("1002", "货币资金", "cash")]
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
        ("1701", "无形资产", "intangible_asset_book_value", 1),
        ("1801", "长期待摊费用", "long_prepaid", 1),
        ("4301002", "开发支出", "dev_expense", 1),
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
    total_assets = d2(balance.get("asset_total", Decimal("0")))
    total_liabilities = d2(balance.get("liability_total", Decimal("0")))
    total_equity = d2(balance.get("equity_total", Decimal("0")))

    # 会计视图恒等口径：未分配利润 = 资产 − 负债 − 显式权益组件（残差），
    # 期末/年初同式。账套存在未月结/年结月份时 5xxx 余额尚未滚入 3103/3104，
    # 直接取科目会让报表两侧不平；boss 视图保留科目口径并用 isBalanced 提示，
    # standard.balanced 记录「科目口径是否天然平衡」供前端展示提示条。
    current_equity_total = d2(total_assets - total_liabilities)
    current_retained_std = d2(
        current_equity_total
        - balance.get("paid_in_capital", ZERO)
        - balance.get("capital_reserve", ZERO)
        - balance.get("surplus_reserve", ZERO)
    )
    opening_equity_total = d2(
        opening.get("asset_total", ZERO) - opening.get("liability_total", ZERO)
    )
    opening_retained = d2(
        opening_equity_total
        - opening.get("paid_in_capital", ZERO)
        - opening.get("capital_reserve", ZERO)
        - opening.get("surplus_reserve", ZERO)
    )
    equity.append(BalanceSheetItem(
        accountCode="3103", accountName="本年利润",
        currentAmount=d2(current_profit), previousAmount=ZERO,
    ))
    equity.append(BalanceSheetItem(
        accountCode="3104", accountName="利润分配-未分配利润",
        currentAmount=d2(prior_retained), previousAmount=opening_retained,
    ))

    def std_row(
        row_no: int, name: str, key: str | None, *,
        code: str | None = None, sign: int = 1, level: int = 0,
        kind: Literal["item", "section", "subtotal", "total"] = "item",
    ) -> StandardStatementRow:
        src, src_open = balance, opening
        if key:
            return StandardStatementRow(
                rowNo=row_no, name=name, level=level, kind=kind, accountCode=code,
                openingAmount=d2(src_open.get(key, Decimal("0")) * sign),
                currentAmount=d2(src.get(key, Decimal("0")) * sign),
            )
        return StandardStatementRow(rowNo=row_no, name=name, level=level, kind=kind)

    std_assets = [
        StandardStatementRow(name="流动资产：", kind="section"),
        std_row(1, "货币资金", "cash", code="1002"),
        std_row(2, "短期投资", "short_investment", code="1101"),
        std_row(3, "应收票据", "notes_receivable", code="1121"),
        std_row(4, "应收账款", "accounts_receivable", code="1122"),
        std_row(5, "预付账款", "prepaid", code="1123"),
        std_row(6, "应收股利", "dividends_receivable", code="1131"),
        std_row(7, "应收利息", "interest_receivable", code="1132"),
        std_row(8, "其他应收款", "other_receivable", code="1221"),
        std_row(9, "存货", "inventory", code="1405"),
        std_row(10, "其中：原材料", "raw_material", code="1403", level=1),
        std_row(11, "在产品", None, level=1),
        std_row(12, "库存商品", "goods", code="1405", level=1),
        std_row(13, "周转材料", None, level=1),
        std_row(14, "其他流动资产", "other_current_asset", code="1901"),
        std_row(15, "流动资产合计", "current_asset_total", kind="subtotal"),
        StandardStatementRow(name="非流动资产：", kind="section"),
        std_row(16, "长期债券投资", "long_bond_investment", code="1501"),
        std_row(17, "长期股权投资", "long_equity_investment", code="1511"),
        std_row(18, "固定资产原价", "fixed_asset_cost", code="1601"),
        std_row(19, "减：累计折旧", "accumulated_depreciation", code="1602", level=1),
        std_row(20, "固定资产账面价值", "fixed_asset_book_value"),
        std_row(21, "在建工程", "construction", code="1604"),
        std_row(22, "工程物资", None),
        std_row(23, "固定资产清理", None),
        std_row(24, "生产性生物资产", None),
        std_row(25, "无形资产", "intangible_asset_book_value", code="1701"),
        std_row(26, "开发支出", "dev_expense", code="4301002"),
        std_row(27, "长期待摊费用", "long_prepaid", code="1801"),
        std_row(28, "其他非流动资产", None),
        std_row(29, "非流动资产合计", "non_current_asset_total", kind="subtotal"),
        std_row(30, "资产总计", "asset_total", kind="total"),
    ]

    std_rights = [
        StandardStatementRow(name="流动负债：", kind="section"),
        std_row(31, "短期借款", "short_borrow", code="2001"),
        std_row(32, "应付票据", "notes_payable", code="2201"),
        std_row(33, "应付账款", "accounts_payable", code="2202"),
        std_row(34, "预收账款", "advance_receipt", code="2203"),
        std_row(35, "应付职工薪酬", "salary_payable", code="2211"),
        std_row(36, "应交税费", "tax_payable", code="2221"),
        std_row(37, "应付利息", "interest_payable", code="2231"),
        std_row(38, "应付利润", "profit_payable", code="2232"),
        std_row(39, "其他应付款", "other_payable", code="2241"),
        std_row(40, "其他流动负债", None),
        std_row(41, "流动负债合计", "current_liability_total", kind="subtotal"),
        StandardStatementRow(name="非流动负债：", kind="section"),
        std_row(42, "长期借款", "long_borrow", code="2501"),
        std_row(43, "长期应付款", "long_payable", code="2701"),
        std_row(44, "递延收益", "deferred_income", code="2401"),
        std_row(45, "其他非流动负债", None),
        std_row(46, "非流动负债合计", "non_current_liability_total", kind="subtotal"),
        std_row(47, "负债合计", "liability_total", kind="subtotal"),
        StandardStatementRow(name="所有者权益（或股东权益）：", kind="section"),
        std_row(48, "实收资本（或股本）", "paid_in_capital", code="3001"),
        std_row(49, "资本公积", "capital_reserve", code="3002"),
        std_row(50, "盈余公积", "surplus_reserve", code="3101"),
        std_row(51, "未分配利润", "retained_earnings"),
        std_row(52, "所有者权益（或股东权益）合计", "equity_total", kind="subtotal"),
        std_row(53, "负债和所有者权益（或股东权益）总计", "liability_equity_total", kind="total"),
    ]

    # 权益三行（未分配利润 / 权益合计 / 右侧总计）双列改用残差口径
    opening_assets = d2(opening.get("asset_total", Decimal("0")))
    for row in std_rights:
        if row.name == "未分配利润":
            row.openingAmount = opening_retained
            row.currentAmount = current_retained_std
        elif row.name == "所有者权益（或股东权益）合计":
            row.openingAmount = opening_equity_total
            row.currentAmount = current_equity_total
        elif row.name == "负债和所有者权益（或股东权益）总计":
            row.openingAmount = opening_assets
            row.currentAmount = total_assets

    standard = BalanceSheetStandard(
        assets=std_assets,
        rights=std_rights,
        # 科目口径（3103/3104 直接取数）是否天然平衡：存在未月结/年结月份时为 False，
        # 此时残差列与 boss 视图的权益合计会有差异，前端据此展示提示条。
        balanced=(
            total_assets == d2(balance.get("liability_equity_total", Decimal("0")))
            and opening_assets == d2(opening.get("liability_equity_total", Decimal("0")))
        ),
    )

    return BalanceSheetResponse(
        month=req.month,
        periodType=req.periodType,
        period=period,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        totalAssets=total_assets,
        totalLiabilities=total_liabilities,
        totalEquity=total_equity,
        isBalanced=total_assets == total_liabilities + total_equity,
        standard=standard,
    )


@router.post("/report/cash-flow", response_model=CashFlowResponse)
async def cash_flow(
    req: CashFlowRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """现金流量表：仅从已过账凭证的现金科目及对方科目归集；支持月报/季报。"""
    period = _period_label(req.month, req.periodType)
    statement = await build_statement_data(ctx.user_id, ctx.company_id, period, req.periodType)
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
        periodType=req.periodType,
        period=period,
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


@router.post("/report/equity-changes")
async def equity_changes(
    req: EquityChangeRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """所有者权益变动表（小企业准则简化年度版，口径见 equity_service 模块注释）。"""
    return await get_equity_changes(ctx.company_id, req.year)


def _filter_month(entries: list, month: str) -> list:
    """Filter entries by occurDate prefix (YYYY-MM)."""
    return [e for e in entries if (e.get("occurDate") or "").startswith(month)]


def _compute_totals(entries: list) -> tuple:
    income = sum(e.get("amount", 0) for e in entries if e.get("direction") == "income")
    expense = sum(e.get("amount", 0) for e in entries if _is_pnl_expense(e))
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

    # 所得税测算（按主体类型）：非公司主体带经营所得累计口径，公司主体带小微企税
    tax_context = ""
    try:
        entity_type = await company_entity_service.get_company_entity_type(ctx.company_id)
        if company_entity_service.pays_corporate_income_tax(entity_type):
            tax_context = "企业所得税按小型微利企业优惠测算（实际税负 5%，年度汇算为准）。"
        else:
            from services import business_income_iit_service
            iit = await business_income_iit_service.build_prepayment_assist(
                ctx.user_id, ctx.company_id, req.month
            )
            if iit.get("applicable"):
                tax_context = (
                    f"经营所得个人所得税（{iit.get('filingCycle')}报）：年初至今累计收入 ¥{iit.get('revenue'):,.2f}，"
                    f"累计应纳税所得额 ¥{iit.get('taxableIncome'):,.2f}，累计应纳税额 ¥{iit.get('tax'):,.2f}"
                    + ("（已按个体户减半优惠测算）。" if iit.get("halvingApplied") else "。")
                )
    except Exception:  # noqa: BLE001 —— 税负上下文失败不拖垮诊断
        tax_context = ""

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
{f'共 {boss_pay_count} 笔，合计 ¥{boss_pay_amount:,.2f}' if boss_pay_count > 0 else '无'}

【所得税测算】
{tax_context or '暂无'}"""

    try:
        # retry_on_parse=True：LLM 偶发带 Markdown 前缀导致 JSON 解析失败时，
        # 追加约束重试一次，避免用户直接看到"暂时无法生成"兜底文案
        result = await asyncio.to_thread(
            chat_text_json, CFO_DIAGNOSIS_PROMPT, context, max_tokens=2048, retry_on_parse=True,
            scene="report", user_id=ctx.user_id, company_id=ctx.company_id,
        )
        return {
            "diagnosis": result.get("diagnosis", "数据不足，暂无法生成诊断报告。"),
            "keyMetrics": result.get("keyMetrics", []),
            "urgentActions": result.get("urgentActions", []),
            "disclaimer": result.get("disclaimer", "以上分析仅供经营参考，具体决策以专业财税人员意见为准"),
            "generatedAt": _now_iso(),
        }
    except AccountingError:
        raise  # 配额超限保持 402
    except Exception:
        # 静默兜底可以，但不能无日志——否则"暂时无法生成"永远查不出原因
        logger.exception("AI CFO 诊断生成失败 month=%s", req.month)
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
