# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""业务归因分析与利现差额桥引擎（Variance Attribution & Profit-to-Cash Bridge）：

1. 同比/环比归因分析：因子分解识别拉动/拖累收入的客户，及导致成本激增的分类与异常单项；
2. 利润 vs 现金差额桥（Profit-to-Cash Bridge）：精确拆解"为什么账面有利润却没现钱"的会计原因。
所有数据严格来自已过账凭证，不破坏已锁账期间，遵循只读派生原则。
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from db.session import session_scope


REVENUE_PREFIXES = ("5001", "5051", "5301", "5111")
EXPENSE_PREFIXES = ("5401", "5402", "5403", "5601", "5602", "5603", "5711", "5801")


def _month_bounds(month_str: str) -> Tuple[date, date]:
    """返回 YYYY-MM 的首日与末日。"""
    y = int(month_str[:4])
    m = int(month_str[5:7])
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def _prev_month(month_str: str) -> str:
    y = int(month_str[:4])
    m = int(month_str[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def _prev_year_month(month_str: str) -> str:
    y = int(month_str[:4])
    m = int(month_str[5:7])
    return f"{y - 1}-{m:02d}"


async def get_business_attribution(
    company_id: str,
    base_month: str,
    compare_mode: str = "mom",
) -> dict[str, Any]:
    """计算业务波动归因与利现差额桥。"""
    if compare_mode == "yoy":
        compare_month = _prev_year_month(base_month)
    else:
        compare_mode = "mom"
        compare_month = _prev_month(base_month)

    base_start, base_end = _month_bounds(base_month)
    comp_start, comp_end = _month_bounds(compare_month)

    # 为了计算异常值（过去 3 个月均值），找出过去 3 个月的起点
    h_m1 = _prev_month(base_month)
    h_m2 = _prev_month(h_m1)
    h_m3 = _prev_month(h_m2)
    hist_start, _ = _month_bounds(h_m3)
    earliest_date = min(comp_start, hist_start, base_start)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(("posted", "reversed")),
                    JournalLine.company_id == company_id,
                )
                .order_by(JournalEntry.occurred_on.asc(), JournalEntry.voucher_no.asc())
            )
        ).all()

    # 冲销过滤
    reversal_of_ids = {
        str(entry.reversal_of_id)
        for entry, _ in rows
        if entry.entry_type == "reversal" and entry.reversal_of_id
    }
    active_rows = [
        (entry, line)
        for entry, line in rows
        if entry.entry_type != "reversal" and str(entry.id) not in reversal_of_ids
    ]

    # 按凭证 ID 聚合凭证行
    entries_map: dict[str, dict[str, Any]] = {}
    for entry, line in active_rows:
        e_data = entries_map.setdefault(
            entry.id,
            {
                "entry": entry,
                "lines": [],
            },
        )
        e_data["lines"].append(line)

    # 1. 提取 base_month 与 compare_month 的收入与费用凭证
    def get_month_pnl(m_start: date, m_end: date) -> Tuple[Decimal, Decimal, dict[str, Decimal], dict[str, Decimal], list[dict[str, Any]]]:
        income_total = ZERO
        expense_total = ZERO
        income_by_partner: dict[str, Decimal] = {}
        expense_by_cat: dict[str, Decimal] = {}
        entries_list: list[dict[str, Any]] = []

        for e_id, e_info in entries_map.items():
            entry: JournalEntry = e_info["entry"]
            lines: list[JournalLine] = e_info["lines"]
            if not (m_start <= entry.occurred_on <= m_end):
                continue
            entry_amt = sum((money(l.amount) for l in lines if l.direction == "debit"), ZERO)
            entries_list.append({"entry": entry, "lines": lines, "amount": entry_amt})

            pname = "未指定客户"
            for l in lines:
                aux = l.auxiliary if isinstance(l.auxiliary, dict) else {}
                if aux.get("partner_name"):
                    pname = str(aux["partner_name"])
                    break

            for l in lines:
                amt = money(l.amount)
                if any(l.account_code.startswith(p) for p in REVENUE_PREFIXES):
                    line_rev = amt if l.direction == "credit" else -amt
                    income_total += line_rev
                    income_by_partner[pname] = income_by_partner.get(pname, ZERO) + line_rev
                elif any(l.account_code.startswith(p) for p in EXPENSE_PREFIXES):
                    line_exp = amt if l.direction == "debit" else -amt
                    expense_total += line_exp
                    cat = str(l.account_name or "日常费用")
                    expense_by_cat[cat] = expense_by_cat.get(cat, ZERO) + line_exp

        return income_total, expense_total, income_by_partner, expense_by_cat, entries_list

    base_inc, base_exp, base_inc_p, base_exp_cat, base_entries = get_month_pnl(base_start, base_end)
    comp_inc, comp_exp, comp_inc_p, comp_exp_cat, _ = get_month_pnl(comp_start, comp_end)

    base_profit = base_inc - base_exp
    comp_profit = comp_inc - comp_exp
    profit_delta = base_profit - comp_profit

    # 2. 收入因子分解（按客户）
    all_partners = set(base_inc_p.keys()) | set(comp_inc_p.keys())
    income_drivers: list[dict[str, Any]] = []
    for p in all_partners:
        b_amt = base_inc_p.get(p, ZERO)
        c_amt = comp_inc_p.get(p, ZERO)
        delta = b_amt - c_amt
        if delta == ZERO:
            continue
        pct = ((delta / c_amt) * Decimal("100")).quantize(Decimal("0.1")) if c_amt > ZERO else None
        tag = ""
        if c_amt == ZERO and b_amt > ZERO:
            tag = "新客户"
        elif c_amt > ZERO and b_amt == ZERO:
            tag = "本期流失/未复购"
        elif delta > ZERO:
            tag = "业务增量"
        else:
            tag = "业务缩减"

        income_drivers.append({
            "key": p,
            "name": p,
            "baseAmount": b_amt,
            "compareAmount": c_amt,
            "deltaAmount": delta,
            "changePercent": pct,
            "tag": tag,
        })
    # 按变动绝对值排序，取影响最大的 Top 6
    income_drivers.sort(key=lambda x: abs(x["deltaAmount"]), reverse=True)
    top_income_drivers = income_drivers[:6]

    # 3. 支出因子分解（按费用分类）
    all_cats = set(base_exp_cat.keys()) | set(comp_exp_cat.keys())
    expense_drivers: list[dict[str, Any]] = []
    for c in all_cats:
        b_amt = base_exp_cat.get(c, ZERO)
        c_amt = comp_exp_cat.get(c, ZERO)
        delta = b_amt - c_amt
        if delta == ZERO:
            continue
        pct = ((delta / c_amt) * Decimal("100")).quantize(Decimal("0.1")) if c_amt > ZERO else None
        tag = "支出激增" if delta > ZERO else "支出缩减"
        expense_drivers.append({
            "key": c,
            "name": c,
            "baseAmount": b_amt,
            "compareAmount": c_amt,
            "deltaAmount": delta,
            "changePercent": pct,
            "tag": tag,
        })
    expense_drivers.sort(key=lambda x: abs(x["deltaAmount"]), reverse=True)
    top_expense_drivers = expense_drivers[:6]

    # 4. 异常支出识别（对比前 3 个月月均支出）
    hist_months = [h_m1, h_m2, h_m3]
    cat_history_amounts: dict[str, list[Decimal]] = {}
    for hm in hist_months:
        ms, me = _month_bounds(hm)
        _, _, _, m_cat, _ = get_month_pnl(ms, me)
        for cat, amt in m_cat.items():
            cat_history_amounts.setdefault(cat, []).append(amt)

    anomalies: list[dict[str, Any]] = []
    for cat, b_amt in base_exp_cat.items():
        hist_list = cat_history_amounts.get(cat, [])
        if hist_list:
            avg_amt = (sum(hist_list, ZERO) / len(hist_list)).quantize(Decimal("0.01"))
        else:
            avg_amt = ZERO

        # 如果当期比过去均值超支 50% 以上，且绝对增加额 >= 1000 元
        if avg_amt > ZERO and b_amt >= (avg_amt * Decimal("1.50")) and (b_amt - avg_amt) >= Decimal("1000.00"):
            growth_rate = (((b_amt - avg_amt) / avg_amt) * Decimal("100")).quantize(Decimal("0.1"))
            # 找出该类别当期金额最大的一笔
            max_entry_title = ""
            for item in base_entries:
                for l in item["lines"]:
                    if str(l.account_name or "") == cat:
                        max_entry_title = item["entry"].summary
                        break
                if max_entry_title:
                    break
            anomalies.append({
                "category": cat,
                "title": max_entry_title or cat,
                "amount": b_amt,
                "historyAvgAmount": avg_amt,
                "growthRate": growth_rate,
                "reason": f"当期金额超前3月均值 {growth_rate}%，增加 ¥{money_text(b_amt - avg_amt)}",
            })
    anomalies.sort(key=lambda x: x["amount"], reverse=True)

    # 5. 利润 vs 现金差额桥（Profit-to-Cash Bridge）
    # 当期现金净变动 = 当月最后一天的货币资金余额 - 当月第一天开始前的货币资金余额
    cash_beg = ZERO
    cash_end = ZERO
    depr_amort = ZERO
    ar_beg = ZERO
    ar_end = ZERO
    ap_beg = ZERO
    ap_end = ZERO
    capex = ZERO

    for entry, line in active_rows:
        amt = money(line.amount)
        # 货币资金 1001/1002
        if line.account_code.startswith(("1001", "1002")):
            eff = amt if line.direction == "debit" else -amt
            if entry.occurred_on < base_start:
                cash_beg += eff
            if entry.occurred_on <= base_end:
                cash_end += eff

        # 累计折旧 1602 / 累计摊销 1702（非付现费用，当期贷方计提）
        if base_start <= entry.occurred_on <= base_end:
            if line.account_code.startswith(("1602", "1702")) and line.direction == "credit":
                depr_amort += amt
            # 资本性支出：固定资产 1601 / 无形资产 1701 借方增加
            if line.account_code.startswith(("1601", "1701")) and line.direction == "debit":
                capex += amt

        # 应收账款 1122
        if line.account_code.startswith("1122"):
            eff = amt if line.direction == "debit" else -amt
            if entry.occurred_on < base_start:
                ar_beg += eff
            if entry.occurred_on <= base_end:
                ar_end += eff

        # 应付账款 2202
        if line.account_code.startswith("2202"):
            eff = amt if line.direction == "credit" else -amt
            if entry.occurred_on < base_start:
                ap_beg += eff
            if entry.occurred_on <= base_end:
                ap_end += eff

    net_cash_flow = cash_end - cash_beg
    ar_change = ar_end - ar_beg      # >0 表示客户欠款增加，占用现金
    ap_change = ap_end - ap_beg      # >0 表示供应商暂缓付款，留存现金

    # 桥梁拆解各项
    bridge_steps: list[dict[str, Any]] = [
        {"name": "当期账面净利润", "amount": base_profit, "type": "profit", "note": "营业收入扣减日常费用后净额"},
    ]
    if depr_amort > ZERO:
        bridge_steps.append({"name": "加：非付现折旧与摊销", "amount": depr_amort, "type": "add", "note": "折旧虽计入费用但未实际流出现金"})

    if ar_change > ZERO:
        bridge_steps.append({"name": "减：客户应收账款占用", "amount": -ar_change, "type": "sub", "note": "已确认收入但货款尚未收回"})
    elif ar_change < ZERO:
        bridge_steps.append({"name": "加：前期客户欠款回款", "amount": abs(ar_change), "type": "add", "note": "收回历史应收账款"})

    if ap_change > ZERO:
        bridge_steps.append({"name": "加：应付账款暂缓支付", "amount": ap_change, "type": "add", "note": "应付未付款项，暂时保留现金"})
    elif ap_change < ZERO:
        bridge_steps.append({"name": "减：偿付前期供应商欠款", "amount": ap_change, "type": "sub", "note": "实际支付前期挂账供应商款项"})

    if capex > ZERO:
        bridge_steps.append({"name": "减：购置设备/资产支出(CapEx)", "amount": -capex, "type": "sub", "note": "资本性资产采购现金流出"})

    # 其他营运资金/往来垫资等综合调节项
    sum_explained = base_profit + depr_amort - ar_change + ap_change - capex
    other_adj = net_cash_flow - sum_explained
    if other_adj != ZERO:
        bridge_steps.append({
            "name": "增值税/所得税/股东垫资及其他营运变动",
            "amount": other_adj,
            "type": "add" if other_adj > ZERO else "sub",
            "note": "税费缴纳、员工与股东往来收付等其他资金进出",
        })

    bridge_steps.append({"name": "当期实际现金净变动", "amount": net_cash_flow, "type": "cash", "note": "公户与手头可用资金期末对比期初真实增减"})

    # 大白话总结
    discrepancy = net_cash_flow - base_profit
    summary_parts = [f"【利现差额】本月账面净利润 ¥{money_text(base_profit)}，实际现金净变动 ¥{money_text(net_cash_flow)}。"]
    if ar_change > ZERO and ar_change >= abs(base_profit) * Decimal("0.3"):
        summary_parts.append(f"主要原因：有 ¥{money_text(ar_change)} 利润被客户应收账款占用（未回款）。")
    if capex > ZERO:
        summary_parts.append(f"当期采购设备资产消耗现金 ¥{money_text(capex)}。")
    if ap_change > ZERO:
        summary_parts.append(f"供应商欠款增加 ¥{money_text(ap_change)} 暂时缓解了现金压力。")
    if len(summary_parts) == 1:
        summary_parts.append("账面利润与现金变动基本对齐，无显著资金占用。")
    plain_summary = " ".join(summary_parts)

    return {
        "baseMonth": base_month,
        "compareMonth": compare_month,
        "baseProfit": base_profit,
        "compareProfit": comp_profit,
        "profitDelta": profit_delta,
        "topIncomeDrivers": top_income_drivers,
        "topExpenseDrivers": top_expense_drivers,
        "anomalies": anomalies,
        "netProfit": base_profit,
        "netCashFlow": net_cash_flow,
        "profitToCashDiscrepancy": discrepancy,
        "bridgeSteps": bridge_steps,
        "plainSummary": plain_summary,
    }
