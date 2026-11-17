# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""滚动现金流预测引擎（30 / 60 / 90 天）：

基于已过账分录的货币资金（1001/1002）、未结清应收（1122）、未结清应付（2202）
以及周期性收支承诺规则（工资/房租等），提供确定性的未来滚动现金流预测与压力测试。
不修改任何历史数据，遵循只读派生原则。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
import calendar
import uuid

from sqlalchemy import select

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from db.session import session_scope
from agent.memory import get_profile, save_profile


DEFAULT_AR_DAYS = 30
DEFAULT_AP_DAYS = 30


def _normalize_manual_event(ev: dict[str, Any]) -> dict[str, Any]:
    raw_amount = ev.get("amount") or 0
    raw_id = str(ev.get("id") or "").strip()
    return {
        "id": raw_id or f"evt_{uuid.uuid4().hex[:8]}",
        "name": str(ev.get("name") or "未命名收支").strip()[:100],
        "type": "inflow" if str(ev.get("type")).lower() == "inflow" else "outflow",
        "amount": str(money(raw_amount)),
        "date": str(ev.get("date") or "")[:10],
        "repeat": ev.get("repeat") if ev.get("repeat") in ("monthly", "quarterly", "yearly") else "none",
        "repeatDay": max(1, min(int(ev["repeatDay"]), 31)) if ev.get("repeatDay") not in (None, "") else None,
        "confidence": ev.get("confidence") if ev.get("confidence") in ("high", "medium", "low") else "high",
        "enabled": bool(ev.get("enabled", True)),
    }


def _parse_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return datetime.now().date()


async def get_cash_forecast_config(company_id: str) -> dict[str, Any]:
    """读取公司的现金流预测配置。"""
    profile = await get_profile(company_id)
    raw = profile.get("cashForecastConfig") or {}
    raw_events = raw.get("manualCashEvents") or []
    events = [_normalize_manual_event(e) for e in raw_events if isinstance(e, dict)]
    return {
        "defaultArDays": int(raw.get("defaultArDays") or DEFAULT_AR_DAYS),
        "defaultApDays": int(raw.get("defaultApDays") or DEFAULT_AP_DAYS),
        "partnerCreditDays": dict(raw.get("partnerCreditDays") or {}),
        "safetyCashThreshold": money_text(money(raw["safetyCashThreshold"])) if raw.get("safetyCashThreshold") is not None else None,
        "manualCashEvents": events,
    }


async def save_cash_forecast_config(company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """保存公司的现金流预测配置。"""
    profile = await get_profile(company_id)
    raw_events = payload.get("manualCashEvents") or []
    events = [_normalize_manual_event(e) for e in raw_events if isinstance(e, dict)]
    config = {
        "defaultArDays": max(1, min(int(payload.get("defaultArDays") or DEFAULT_AR_DAYS), 365)),
        "defaultApDays": max(1, min(int(payload.get("defaultApDays") or DEFAULT_AP_DAYS), 365)),
        "partnerCreditDays": {str(k): max(1, min(int(v), 365)) for k, v in (payload.get("partnerCreditDays") or {}).items()},
        "safetyCashThreshold": str(money(payload["safetyCashThreshold"])) if payload.get("safetyCashThreshold") is not None else None,
        "manualCashEvents": events,
    }
    profile["cashForecastConfig"] = config
    await save_profile(company_id, profile)
    return config


async def calculate_cash_forecast(
    company_id: str,
    as_of: str = "",
    horizon_days: int = 60,
    scenario: str = "neutral",
) -> dict[str, Any]:
    """计算滚动现金流预测。"""
    as_of_date = _parse_date(as_of)
    horizon_days = max(7, min(int(horizon_days or 60), 180))
    if scenario not in ("neutral", "optimistic", "pessimistic"):
        scenario = "neutral"

    # 1. 获取配置
    config = await get_cash_forecast_config(company_id)
    default_ar_days = config["defaultArDays"]
    default_ap_days = config["defaultApDays"]
    partner_credit_days = config["partnerCreditDays"]

    # 2. 读取有效凭证（过滤冲销）
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

    # 3. 统计当前真金白银（货币资金：1001 库存现金 + 1002 银行存款）
    current_cash = ZERO
    for entry, line in active_rows:
        if entry.occurred_on <= as_of_date and (line.account_code.startswith("1001") or line.account_code.startswith("1002")):
            amt = money(line.amount)
            if line.direction == "debit":
                current_cash += amt
            else:
                current_cash -= amt

    # 4. 统计未结清应收账款（1122）- FIFO 分配法
    ar_lines_by_partner: dict[str, list[tuple[JournalEntry, JournalLine]]] = {}
    for entry, line in active_rows:
        if entry.occurred_on <= as_of_date and line.account_code.startswith("1122"):
            auxiliary = line.auxiliary if isinstance(line.auxiliary, dict) else {}
            pid = str(auxiliary.get("partner_id") or "")
            pname = str(auxiliary.get("partner_name") or "") or "未指定客户"
            ar_lines_by_partner.setdefault(f"{pid}@@{pname}", []).append((entry, line))

    overdue_receivables_map: dict[str, dict[str, Any]] = {}
    daily_ar_inflows: dict[date, Decimal] = {}

    for partner_key, partner_lines in ar_lines_by_partner.items():
        pid, pname = partner_key.split("@@", 1)
        # 计算该客户净应收
        net_ar = sum((money(l.amount) if l.direction == "debit" else -money(l.amount) for _, l in partner_lines), ZERO)
        if net_ar <= ZERO:
            continue

        # 按借方发生额升序做 FIFO 扣减
        debit_lines = [(e, l) for e, l in partner_lines if l.direction == "debit"]
        credit_total = sum((money(l.amount) for _, l in partner_lines if l.direction == "credit"), ZERO)

        credit_pool = credit_total
        unpaid_slices: list[tuple[JournalEntry, Decimal]] = []
        for e, l in debit_lines:
            amt = money(l.amount)
            if credit_pool >= amt:
                credit_pool -= amt
                continue
            amt_unpaid = amt - credit_pool
            credit_pool = ZERO
            unpaid_slices.append((e, amt_unpaid))

        # 根据约定账期计算到期日
        credit_days = int(partner_credit_days.get(pid) or default_ar_days)
        for e, amt_unpaid in unpaid_slices:
            expected_date = e.occurred_on + timedelta(days=credit_days)
            if expected_date < as_of_date:
                # 逾期
                days_overdue = (as_of_date - expected_date).days
                ov = overdue_receivables_map.setdefault(
                    pid or "__unassigned__",
                    {
                        "partnerId": pid,
                        "partnerName": pname,
                        "amount": ZERO,
                        "daysOverdue": 0,
                        "oldestDate": e.occurred_on.isoformat(),
                    },
                )
                ov["amount"] += amt_unpaid
                ov["daysOverdue"] = max(ov["daysOverdue"], days_overdue)
                if e.occurred_on.isoformat() < ov["oldestDate"]:
                    ov["oldestDate"] = e.occurred_on.isoformat()

                # 情景排期
                if scenario == "neutral":
                    # 基准：逾期后预计在 15 天内按 70% 催回收回
                    sched_date = as_of_date + timedelta(days=15)
                    daily_ar_inflows[sched_date] = daily_ar_inflows.get(sched_date, ZERO) + (amt_unpaid * Decimal("0.70"))
                elif scenario == "optimistic":
                    # 乐观：7 天内 100% 回款
                    sched_date = as_of_date + timedelta(days=7)
                    daily_ar_inflows[sched_date] = daily_ar_inflows.get(sched_date, ZERO) + amt_unpaid
                elif scenario == "pessimistic":
                    # 悲观：逾期账款在预测期内视为 0 回款（极限压力测试）
                    pass
            else:
                # 未逾期正常回款
                if scenario == "pessimistic":
                    # 悲观：整体推迟 30 天
                    sched_date = expected_date + timedelta(days=30)
                else:
                    sched_date = expected_date
                daily_ar_inflows[sched_date] = daily_ar_inflows.get(sched_date, ZERO) + amt_unpaid

    # 5. 统计未结清应付账款（2202）
    ap_lines_by_partner: dict[str, list[tuple[JournalEntry, JournalLine]]] = {}
    for entry, line in active_rows:
        if entry.occurred_on <= as_of_date and line.account_code.startswith("2202"):
            auxiliary = line.auxiliary if isinstance(line.auxiliary, dict) else {}
            pid = str(auxiliary.get("partner_id") or "")
            pname = str(auxiliary.get("partner_name") or "") or "未指定供应商"
            ap_lines_by_partner.setdefault(f"{pid}@@{pname}", []).append((entry, line))

    daily_ap_outflows: dict[date, Decimal] = {}
    for partner_key, partner_lines in ap_lines_by_partner.items():
        pid, _ = partner_key.split("@@", 1)
        net_ap = sum((money(l.amount) if l.direction == "credit" else -money(l.amount) for _, l in partner_lines), ZERO)
        if net_ap <= ZERO:
            continue

        credit_lines = [(e, l) for e, l in partner_lines if l.direction == "credit"]
        debit_total = sum((money(l.amount) for _, l in partner_lines if l.direction == "debit"), ZERO)

        debit_pool = debit_total
        unpaid_slices = []
        for e, l in credit_lines:
            amt = money(l.amount)
            if debit_pool >= amt:
                debit_pool -= amt
                continue
            amt_unpaid = amt - debit_pool
            debit_pool = ZERO
            unpaid_slices.append((e, amt_unpaid))

        pay_days = int(partner_credit_days.get(pid) or default_ap_days)
        for e, amt_unpaid in unpaid_slices:
            expected_pay_date = max(as_of_date, e.occurred_on + timedelta(days=pay_days))
            # 应付刚性兑付
            daily_ap_outflows[expected_pay_date] = daily_ap_outflows.get(expected_pay_date, ZERO) + amt_unpaid

    # 6. 计算月均刚性开销（基于过去 90 天实际过账的 5/6 字头成本费用，客观真实的 Burn Rate）
    # 排除非付现费用（折旧 1602、摊销 1702）以及走 2202 应付账款通道排程兑付的款项（避免双重扣减）
    past_90_days = as_of_date - timedelta(days=90)
    entry_lines_map: dict[str, list[JournalLine]] = {}
    for entry, line in active_rows:
        entry_lines_map.setdefault(str(entry.id), []).append(line)

    past_expenses: list[Decimal] = []
    for entry, line in active_rows:
        if not (past_90_days <= entry.occurred_on <= as_of_date):
            continue
        if line.direction == "debit" and line.account_code.startswith(("5", "6")):
            other_lines = entry_lines_map.get(str(entry.id), [])
            credit_codes = [l.account_code for l in other_lines if l.direction == "credit"]
            # 排除非付现（折旧 1602、摊销 1702）
            if any(c.startswith(("1602", "1702")) for c in credit_codes):
                continue
            # 排除走 2202 应付账款通道的供应商采购（已在第 5 步精确排期兑付，避免双重扣减）
            if any(c.startswith("2202") for c in credit_codes):
                continue
            past_expenses.append(money(line.amount))

    monthly_burn_rate = (sum(past_expenses, ZERO) / Decimal("3")).quantize(Decimal("0.01")) if past_expenses else ZERO
    daily_base_burn = (monthly_burn_rate / Decimal("30")).quantize(Decimal("0.01"))

    # 6.5 展开自定义与预期收支日程事件 (manualCashEvents) 及待办征期
    manual_events = config.get("manualCashEvents") or []
    daily_manual_inflows: dict[date, Decimal] = {}
    daily_manual_outflows: dict[date, Decimal] = {}
    timeline_events: list[dict[str, Any]] = []
    end_date = as_of_date + timedelta(days=horizon_days)

    for ev in manual_events:
        if not ev.get("enabled", True):
            continue

        ev_amt = money(ev.get("amount") or 0)
        if ev_amt <= ZERO:
            continue

        ev_type = ev.get("type", "outflow")
        ev_confidence = ev.get("confidence", "high")
        repeat = ev.get("repeat", "none")

        # 压力测试场景过滤：悲观情景下，口头/非高置信度的预期流入不予计提（极限防守）
        if scenario == "pessimistic" and ev_type == "inflow" and ev_confidence != "high":
            continue

        if repeat == "none":
            ev_date = _parse_date(ev.get("date"))
            if as_of_date < ev_date <= end_date:
                if ev_type == "inflow":
                    daily_manual_inflows[ev_date] = daily_manual_inflows.get(ev_date, ZERO) + ev_amt
                else:
                    daily_manual_outflows[ev_date] = daily_manual_outflows.get(ev_date, ZERO) + ev_amt
                timeline_events.append({
                    "id": ev["id"],
                    "date": ev_date.isoformat(),
                    "name": ev["name"],
                    "type": ev_type,
                    "amount": ev_amt,
                    "confidence": ev_confidence,
                    "repeat": "none",
                    "source": "manual",
                    "enabled": True,
                })
        elif repeat == "monthly":
            rep_day = ev.get("repeatDay") or 1
            cur = as_of_date
            while cur <= end_date:
                _, max_d = calendar.monthrange(cur.year, cur.month)
                target_d = min(rep_day, max_d)
                target_date = date(cur.year, cur.month, target_d)
                if as_of_date < target_date <= end_date:
                    if ev_type == "inflow":
                        daily_manual_inflows[target_date] = daily_manual_inflows.get(target_date, ZERO) + ev_amt
                    else:
                        daily_manual_outflows[target_date] = daily_manual_outflows.get(target_date, ZERO) + ev_amt
                    timeline_events.append({
                        "id": f"{ev['id']}_{target_date.isoformat()}",
                        "date": target_date.isoformat(),
                        "name": ev["name"],
                        "type": ev_type,
                        "amount": ev_amt,
                        "confidence": ev_confidence,
                        "repeat": "monthly",
                        "source": "manual",
                        "enabled": True,
                    })
                if cur.month == 12:
                    cur = date(cur.year + 1, 1, 1)
                else:
                    cur = date(cur.year, cur.month + 1, 1)
        elif repeat == "quarterly":
            base_date = _parse_date(ev.get("date")) if ev.get("date") else as_of_date
            rep_day = ev.get("repeatDay") or base_date.day
            cur = date(base_date.year, base_date.month, 1)
            while cur <= end_date:
                _, max_d = calendar.monthrange(cur.year, cur.month)
                target_date = date(cur.year, cur.month, min(rep_day, max_d))
                if as_of_date < target_date <= end_date:
                    if ev_type == "inflow":
                        daily_manual_inflows[target_date] = daily_manual_inflows.get(target_date, ZERO) + ev_amt
                    else:
                        daily_manual_outflows[target_date] = daily_manual_outflows.get(target_date, ZERO) + ev_amt
                    timeline_events.append({
                        "id": f"{ev['id']}_{target_date.isoformat()}",
                        "date": target_date.isoformat(),
                        "name": ev["name"],
                        "type": ev_type,
                        "amount": ev_amt,
                        "confidence": ev_confidence,
                        "repeat": "quarterly",
                        "source": "manual",
                        "enabled": True,
                    })
                new_month = cur.month + 3
                new_year = cur.year + (new_month - 1) // 12
                new_month = (new_month - 1) % 12 + 1
                cur = date(new_year, new_month, 1)
        elif repeat == "yearly":
            base_date = _parse_date(ev.get("date")) if ev.get("date") else as_of_date
            for yr in (as_of_date.year, as_of_date.year + 1):
                try:
                    target_date = date(yr, base_date.month, base_date.day)
                    if as_of_date < target_date <= end_date:
                        if ev_type == "inflow":
                            daily_manual_inflows[target_date] = daily_manual_inflows.get(target_date, ZERO) + ev_amt
                        else:
                            daily_manual_outflows[target_date] = daily_manual_outflows.get(target_date, ZERO) + ev_amt
                        timeline_events.append({
                            "id": f"{ev['id']}_{target_date.isoformat()}",
                            "date": target_date.isoformat(),
                            "name": ev["name"],
                            "type": ev_type,
                            "amount": ev_amt,
                            "confidence": ev_confidence,
                            "repeat": "yearly",
                            "source": "manual",
                            "enabled": True,
                        })
                except ValueError:
                    pass

    # 联动待办税费征期事件节点
    try:
        from services.filing_deadline_service import list_deadlines
        tax_rows = await list_deadlines(company_id, upcoming_days=horizon_days, status="pending")
        for tr in tax_rows:
            d_str = str(tr.get("deadlineDate") or tr.get("deadline_date") or "")
            if d_str and len(d_str) >= 10:
                t_date = _parse_date(d_str)
                if as_of_date < t_date <= end_date:
                    timeline_events.append({
                        "id": f"tax_{tr.get('id')}",
                        "date": t_date.isoformat(),
                        "name": f"【税费征期】{tr.get('taxName') or tr.get('tax_name') or '申报截止'}",
                        "type": "outflow",
                        "amount": Decimal("0.00"),
                        "confidence": "high",
                        "repeat": "none",
                        "source": "system_tax",
                        "enabled": True,
                    })
    except Exception:
        pass

    timeline_events.sort(key=lambda x: (x["date"], 0 if x["type"] == "outflow" else 1))

    # 7. 计算安全水位线
    if config.get("safetyCashThreshold") is not None:
        safety_threshold = money(config["safetyCashThreshold"])
    else:
        # 默认以 1 个月月均刚性支出作为安全储备红线（最低 10,000 元）
        safety_threshold = max(monthly_burn_rate, Decimal("10000.00"))

    # 8. 每日滚动平衡推演
    daily_points: list[dict[str, Any]] = []
    rolling_balance = current_cash
    min_balance = current_cash
    min_balance_date = as_of_date.isoformat()
    deficit_date: Optional[str] = None
    is_deficit_projected = False

    for d in range(1, horizon_days + 1):
        cur_date = as_of_date + timedelta(days=d)
        manual_in = daily_manual_inflows.get(cur_date, ZERO)
        manual_out = daily_manual_outflows.get(cur_date, ZERO)
        inflow = daily_ar_inflows.get(cur_date, ZERO) + manual_in
        outflow = daily_ap_outflows.get(cur_date, ZERO) + daily_base_burn + manual_out
        net = inflow - outflow
        rolling_balance += net

        if rolling_balance < min_balance:
            min_balance = rolling_balance
            min_balance_date = cur_date.isoformat()

        if rolling_balance < safety_threshold and deficit_date is None:
            deficit_date = cur_date.isoformat()
            is_deficit_projected = True

        daily_points.append({
            "date": cur_date.isoformat(),
            "inflow": money(inflow),
            "outflow": money(outflow),
            "net": money(net),
            "balance": money(rolling_balance),
        })

    # 9. 资金跑道（Runway 计算）
    if monthly_burn_rate > ZERO:
        runway_calc = (current_cash / monthly_burn_rate).quantize(Decimal("0.1"))
        runway_months = max(Decimal("0.0"), runway_calc)
    else:
        runway_months = None

    # 整理逾期客户列表
    overdue_list = sorted(
        [
            {
                "partnerId": v["partnerId"],
                "partnerName": v["partnerName"],
                "amount": money(v["amount"]),
                "daysOverdue": v["daysOverdue"],
                "oldestDate": v["oldestDate"],
            }
            for v in overdue_receivables_map.values()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )

    # 简析总结
    if scenario == "pessimistic":
        scen_text = "压力测试情景（客户回款推迟30天且逾期不回）："
    elif scenario == "optimistic":
        scen_text = "乐观情景（客户欠款全额如期收回）："
    else:
        scen_text = "基准预测情景："

    if is_deficit_projected:
        scen_text += f"预计将在 {deficit_date} 触及安全警戒线（¥{money_text(safety_threshold)}），最低现金额 ¥{money_text(min_balance)}（{min_balance_date}）。建议优先催收逾期款项。"
    else:
        scen_text += f"未来 {horizon_days} 天资金流充裕，最低现金额 ¥{money_text(min_balance)}，均高于安全储备水位。"

    return {
        "asOf": as_of_date.isoformat(),
        "horizonDays": horizon_days,
        "scenario": scenario,
        "currentCash": current_cash,
        "endingCash": rolling_balance,
        "minBalance": min_balance,
        "minBalanceDate": min_balance_date,
        "safetyThreshold": safety_threshold,
        "runwayMonths": runway_months,
        "isDeficitProjected": is_deficit_projected,
        "deficitDate": deficit_date,
        "dailyPoints": daily_points,
        "overdueReceivables": overdue_list,
        "timelineEvents": timeline_events,
        "monthlyBurnRate": monthly_burn_rate,
        "scenarioSummary": scen_text,
    }
