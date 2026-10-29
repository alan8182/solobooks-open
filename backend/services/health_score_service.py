# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""确定性经营健康评分规则引擎 (Deterministic Health Score Engine).

不依赖任何 LLM，基于真实凭证、结账检查、发票附着率、税务风险标签进行百分制确定性审计计算。
计算耗时 < 5ms，数值可解释、可复现、可测试。
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional


def _to_decimal(val: Any) -> Decimal:
    if val is None:
        return Decimal("0")
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0")


def calculate_health_score(
    entries: List[Dict[str, Any]],
    all_entries: Optional[List[Dict[str, Any]]] = None,
    target_month: str = "",
    checks: Optional[Dict[str, Any]] = None,
    todos: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """计算百分制经营健康度评分。

    维度权重（总计 100 分）：
    1. 账务完整性 (integrity): 20 分
    2. 发票合规度 (invoice):   25 分
    3. 资金安全度 (cashflow):  20 分
    4. 税务合规度 (tax):       20 分
    5. 盈利与成本 (profit):    15 分

    Returns:
        包含 totalScore, level, levelCode, summary, dimensions,
        dimensionDetails, penalties, suggestions, keyMetrics 的字典。
    """
    if all_entries is None:
        all_entries = entries

    penalties: List[Dict[str, Any]] = []
    penalty_counter = 0

    def add_penalty(dim_code: str, title: str, desc: str, deduction: int, severity: str, route: str = "", action: str = ""):
        nonlocal penalty_counter
        penalty_counter += 1
        penalties.append({
            "id": f"p-{penalty_counter}",
            "dimensionCode": dim_code,
            "title": title,
            "description": desc,
            "deduction": deduction,
            "severity": severity,
            "targetRoute": route,
            "actionLabel": action,
        })

    # 1. 基础收支统计
    income = Decimal("0")
    expense = Decimal("0")
    expense_count = 0
    invoice_count = 0
    large_unbilled_expenses = []

    for e in entries:
        direction = e.get("direction")
        amount = _to_decimal(e.get("amount", 0))
        if direction == "income":
            income += amount
        elif direction == "expense":
            expense += amount
            expense_count += 1
            billing_status = str(e.get("billingStatus") or "").lower()
            if billing_status in ("has_invoice", "has_receipt"):
                invoice_count += 1
            else:
                if amount >= Decimal("1000"):
                    large_unbilled_expenses.append(e)

    # ----------------------------------------------------
    # 维度 1: 账务完整性 (integrity) - 满分 20
    # ----------------------------------------------------
    integrity_score = 20
    integrity_highlight = "记账规范完整"

    if checks:
        if not checks.get("canClose", True):
            # 结账有阻断性检查
            check_details = checks.get("checks") or {}
            if check_details.get("trial_balance") is False:
                integrity_score -= 8
                add_penalty("integrity", "期末试算平衡不平", "借贷总额存在偏差，账套数据不平衡", 8, "high", "/closing-wizard", "去核对结账")
            if check_details.get("numbering_gap") is True:
                integrity_score -= 4
                add_penalty("integrity", "记账凭证存在断号", "凭证编号不连续，需重新整理凭证号", 4, "medium", "/closing-wizard", "去整理凭证")
            if check_details.get("profit_transfer") is False:
                integrity_score -= 4
                add_penalty("integrity", "期末损益未完成结转", "当期存在未结转的收入或费用科目余额", 4, "medium", "/closing-wizard", "去结转损益")
    else:
        # 无 checks 数据时，检查草稿状态条目
        draft_count = sum(1 for e in entries if e.get("status") in ("draft", "pending", "pending_review"))
        if draft_count > 0:
            ded = min(draft_count * 2, 8)
            integrity_score -= ded
            add_penalty("integrity", f"存在 {draft_count} 笔待确认记账草稿", "草稿未过账前不计入正式报表", ded, "medium", f"/ledger?status=draft&month={target_month}", "去审核入账")

    if not entries:
        integrity_score = 14
        integrity_highlight = "当期暂无经营流水"
    elif integrity_score == 20:
        integrity_highlight = "试算平衡且分录完备"
    else:
        integrity_highlight = f"扣除 {20 - max(0, integrity_score)} 分，待整理凭证"

    integrity_score = max(0, min(20, integrity_score))

    # ----------------------------------------------------
    # 维度 2: 发票合规度 (invoice) - 满分 25
    # ----------------------------------------------------
    invoice_score = 25
    if expense_count == 0:
        compliance_rate = 100
        invoice_highlight = "当期无支出发生"
    else:
        compliance_rate = round(invoice_count / expense_count * 100)
        invoice_highlight = f"发票附着率 {compliance_rate}%"

    if compliance_rate < 85:
        # 附着率偏低按比例扣分
        rate_deduction = min(round((85 - compliance_rate) * 0.2), 12)
        invoice_score -= rate_deduction
        add_penalty("invoice", f"发票附着率偏低 ({compliance_rate}%)", "低于 85% 行业平均合规线，易被税局关注无票列支", rate_deduction, "high", f"/ledger?billingStatus=missing&month={target_month}", "去补充发票")

    if large_unbilled_expenses:
        large_cnt = len(large_unbilled_expenses)
        large_ded = min(large_cnt * 3, 10)
        invoice_score -= large_ded
        large_total = sum((_to_decimal(e.get("amount", 0)) for e in large_unbilled_expenses), Decimal("0"))
        add_penalty(
            "invoice",
            f"{large_cnt} 笔单笔超千元无票支出 (共 ¥{large_total:,.2f})",
            "大额支出无合规凭据，企业所得税汇算清缴时需调增补税",
            large_ded,
            "high",
            f"/ledger?billingStatus=missing&minAmount=1000&month={target_month}",
            "排查大额无票",
        )

    invoice_score = max(0, min(25, invoice_score))

    # ----------------------------------------------------
    # 维度 3: 资金安全度 (cashflow) - 满分 20
    # ----------------------------------------------------
    cashflow_score = 20
    net_flow = income - expense

    if net_flow > Decimal("0"):
        cashflow_highlight = f"月净流入 +¥{net_flow:,.2f}"
    elif net_flow == Decimal("0"):
        cashflow_highlight = "月度收支平衡"
    else:
        cashflow_highlight = f"月净流出 -¥{abs(net_flow):,.2f}"

    if income == Decimal("0") and expense > Decimal("5000"):
        cashflow_score -= 6
        add_penalty("cashflow", "当期产生净流出且无营业收入", "需关注公司现金储备跑道与客户回款节点", 6, "medium", "/report/cash-flow", "查看现金流量")
    elif net_flow < Decimal("0") and abs(net_flow) > (income * Decimal("1.5")):
        cashflow_score -= 4
        add_penalty("cashflow", "支出明显超出当期营收", "经营活动现金流承压，建议排查应收账款到账情况", 4, "medium", "/report/aging-list", "查看应收账龄")

    cashflow_score = max(0, min(20, cashflow_score))

    # ----------------------------------------------------
    # 维度 4: 税务合规度 (tax) - 满分 20
    # ----------------------------------------------------
    tax_score = 20
    tax_highlight = "暂无明显税务风险"

    # 检查风险标签
    high_risk_entries = []
    for e in entries:
        tags = [str(t) for t in (e.get("riskTags") or []) if t]
        if any(kw in "".join(tags) for kw in ("股东借款", "个人往来", "私户", "大额提现", "跨期", "滞纳金", "罚款")):
            high_risk_entries.append(e)

    if high_risk_entries:
        risk_cnt = len(high_risk_entries)
        risk_ded = min(risk_cnt * 4, 10)
        tax_score -= risk_ded
        tax_highlight = f"排查到 {risk_cnt} 项敏感涉税业务"
        add_penalty(
            "tax",
            f"存在 {risk_cnt} 笔敏感涉税交易（股东往来/跨期等）",
            "股东长期借款或私户关联交易易引发个税与增值税穿透稽查风险",
            risk_ded,
            "high",
            f"/ledger?hasRisk=1&month={target_month}",
            "核验风险标签",
        )

    # 检查待办中的报税逾期
    if todos:
        tax_todos = [t for t in todos if "税" in str(t.get("title", "")) or t.get("type") == "tax"]
        overdue_tax = [t for t in tax_todos if t.get("level") in ("urgent", "danger") or t.get("status") == "overdue"]
        if overdue_tax:
            tax_score -= 8
            add_penalty("tax", "存在逾期或即将截止的报税待办", "未按期申报将产生税务罚款及纳税信用降级", 8, "high", "/tax-filing", "去处理申报")

    tax_score = max(0, min(20, tax_score))

    # ----------------------------------------------------
    # 维度 5: 盈利与成本 (profit) - 满分 15
    # ----------------------------------------------------
    profit_score = 15
    if income > Decimal("0"):
        profit_rate = round((income - expense) / income * Decimal("100"), 1)
        if profit_rate >= Decimal("20"):
            profit_highlight = f"净利率健康 ({profit_rate}%)"
        elif profit_rate >= Decimal("0"):
            profit_highlight = f"微利状态 (净利率 {profit_rate}%)"
            profit_score -= 2
        else:
            profit_highlight = f"当期亏损 (净利率 {profit_rate}%)"
            profit_score -= 5
            add_penalty("profit", "当期经营出现亏损", "营收无法覆盖当期营业成本及各项费用，需优化毛利与开支", 5, "medium", "/report/income-statement", "查看利润表")
    else:
        profit_highlight = "暂无营业收入"
        if expense > Decimal("0"):
            profit_score -= 4

    profit_score = max(0, min(15, profit_score))

    # ----------------------------------------------------
    # 总分与等级综合计算
    # ----------------------------------------------------
    total_score = integrity_score + invoice_score + cashflow_score + tax_score + profit_score
    total_score = max(0, min(100, total_score))

    if total_score >= 85:
        level_code = "excellent"
        level_str = "优秀"
        level_label = "稳健优秀"
        summary = "各项财税指标均处于良性区间，账务与发票合规度高，暂无重大风控隐患。"
    elif total_score >= 70:
        level_code = "good"
        level_str = "良好"
        level_label = "运营良好"
        summary = "整体经营状况平稳，核心账目清晰，可重点跟进缺票与局部成本开支。"
    elif total_score >= 55:
        level_code = "fair"
        level_str = "一般"
        level_label = "存在隐患"
        summary = "发现若干发票缺漏或未结账待办事项，建议尽快排查整改，防范财税合规风险。"
    else:
        level_code = "poor"
        level_str = "需改善"
        level_label = "急需整改"
        summary = "账务完整性或发票合规率严重不足，需立即梳理原始凭证与报税状态。"

    # 构造前端五维指标详情
    dimension_details = [
        {
            "code": "integrity",
            "name": "账务完整性",
            "score": round(integrity_score / 20 * 100),
            "weight": 20,
            "rawScore": integrity_score,
            "status": "pass" if integrity_score >= 16 else ("warning" if integrity_score >= 12 else "danger"),
            "highlight": integrity_highlight,
            "tips": "衡量结账五项检查、试算平衡及凭证断号情况",
        },
        {
            "code": "invoice",
            "name": "发票合规度",
            "score": round(invoice_score / 25 * 100),
            "weight": 25,
            "rawScore": invoice_score,
            "status": "pass" if invoice_score >= 20 else ("warning" if invoice_score >= 15 else "danger"),
            "highlight": invoice_highlight,
            "tips": "考核支出发票附着率及单笔超千元大额无票排查",
        },
        {
            "code": "cashflow",
            "name": "资金安全度",
            "score": round(cashflow_score / 20 * 100),
            "weight": 20,
            "rawScore": cashflow_score,
            "status": "pass" if cashflow_score >= 16 else ("warning" if cashflow_score >= 12 else "danger"),
            "highlight": cashflow_highlight,
            "tips": "评估经营活动净现金流及收支覆盖倍数",
        },
        {
            "code": "tax",
            "name": "税务合规度",
            "score": round(tax_score / 20 * 100),
            "weight": 20,
            "rawScore": tax_score,
            "status": "pass" if tax_score >= 16 else ("warning" if tax_score >= 12 else "danger"),
            "highlight": tax_highlight,
            "tips": "监测高危涉税标签、股东借款及申报待办",
        },
        {
            "code": "profit",
            "name": "盈利与成本",
            "score": round(profit_score / 15 * 100),
            "weight": 15,
            "rawScore": profit_score,
            "status": "pass" if profit_score >= 12 else ("warning" if profit_score >= 9 else "danger"),
            "highlight": profit_highlight,
            "tips": "考察净利率水平与费用结构合理性",
        },
    ]

    # 兼容旧版的维度字典（统一转为 100 分制）
    dimensions_dict = {d["name"]: d["score"] for d in dimension_details}

    # 提取建议 suggestions（根据真实扣分项生成，确保有据可依）
    suggestions: List[str] = []
    if penalties:
        for p in penalties[:3]:
            suggestions.append(f"{p['title']}：{p['description']}")
    else:
        suggestions.append("本月各项财务动作规范，建议保持并及时完成期末关账")

    # 核心指标卡片数据
    key_metrics = [
        {"label": "发票合规率", "value": f"{compliance_rate}%", "status": "pass" if compliance_rate >= 85 else "warning"},
        {"label": "当月净收支", "value": f"{'+' if net_flow > 0 else ''}¥{net_flow:,.2f}", "status": "pass" if net_flow >= 0 else "danger"},
        {"label": "待排查扣分项", "value": f"{len(penalties)} 项", "status": "pass" if not penalties else ("warning" if len(penalties) <= 2 else "danger")},
    ]

    return {
        "month": target_month,
        "totalScore": total_score,
        "level": level_str,
        "levelCode": level_code,
        "levelLabel": level_label,
        "summary": summary,
        "dimensions": dimensions_dict,
        "dimensionDetails": dimension_details,
        "penalties": penalties,
        "suggestions": suggestions,
        "keyMetrics": key_metrics,
        "taxRiskScore": 100 - round(tax_score / 20 * 100),
        "taxRiskLevel": "低" if tax_score >= 18 else ("中" if tax_score >= 12 else "高"),
        "riskItems": [p["title"] for p in penalties if p["dimensionCode"] == "tax"],
    }
