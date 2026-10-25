# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""所有者权益变动表（小企业会计准则简化年度版）。

四行简化口径：实收资本 / 资本公积 / 盈余公积 / 未分配利润（含本年利润）。
- 显式权益科目（3001/3002/3101）：上年末 = 建账以来贷方累计 − 借方累计；
  本年增加 = 本年贷方发生，本年减少 = 本年借方发生。
- 未分配利润 = 3103/3104 明细累计 + 全部损益类科目累计净额（与资产负债表
  "未分配利润 = 权益合计 − 实收资本 − 资本公积 − 盈余公积 − 本年利润" 同一
  恒等式的行视角）；本年净利润进增加列（亏损进减少列），利润分配（3104 借方）
  进减少列。
- 恒等式：上年末 + 本年增加 − 本年减少 = 年末，对每一行成立，合计行同理。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from db.session import session_scope
from services.ledger_service import POSTED_STATUSES

CAPITAL_FAMILIES = [
    ("paid_in_capital", "实收资本（或股本）", ("3001",)),
    ("capital_reserve", "资本公积", ("3002",)),
    ("surplus_reserve", "盈余公积", ("3101",)),
]
RETAINED_CODES = ("3103", "3104")
PNL_CREDIT_PREFIXES = ("5001", "5051", "5301")
PNL_DEBIT_PREFIXES = ("5401", "5402", "5403", "5601", "5602", "5603", "5711", "4301")


def _match(code: str, prefixes: tuple[str, ...]) -> bool:
    return any(code == p or code.startswith(p) for p in prefixes)


async def get_equity_changes(company_id: str, year: int) -> dict[str, Any]:
    prior_end = date(year - 1, 12, 31)
    year_end = date(year, 12, 31)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start <= year_end,
                    JournalLine.company_id == company_id,
                )
            )
        ).all()

    family_buckets = {
        key: {"prior": ZERO, "credit": ZERO, "debit": ZERO}
        for key, _, _ in CAPITAL_FAMILIES
    }
    retained = {"prior": ZERO, "credit": ZERO, "debit": ZERO}
    pnl = {"prior": ZERO, "year": ZERO}
    for entry, line in rows:
        code = line.account_code
        amount = money(line.amount)
        credit_part = amount if line.direction == "credit" else -amount
        in_prior = entry.period_start <= prior_end
        for key, _, prefixes in CAPITAL_FAMILIES:
            if _match(code, prefixes):
                bucket = family_buckets[key]
                if in_prior:
                    bucket["prior"] += credit_part
                elif line.direction == "credit":
                    bucket["credit"] += amount
                else:
                    bucket["debit"] += amount
                break
        else:
            if _match(code, RETAINED_CODES):
                if in_prior:
                    retained["prior"] += credit_part
                elif line.direction == "credit":
                    retained["credit"] += amount
                else:
                    retained["debit"] += amount
                continue
            pnl_nature = False
            if _match(code, PNL_CREDIT_PREFIXES) or _match(code, PNL_DEBIT_PREFIXES):
                pnl_nature = True
            if not pnl_nature:
                continue
            # 损益统一用贷正符号累计：收入贷记为正、费用借记为负，累计即净利润
            signed = credit_part
            if in_prior:
                pnl["prior"] += signed
            else:
                pnl["year"] += signed

    rows_out: list[dict[str, Any]] = []
    total_prior = ZERO
    total_increase = ZERO
    total_decrease = ZERO
    total_close = ZERO
    for key, label, _ in CAPITAL_FAMILIES:
        bucket = family_buckets[key]
        prior = money(bucket["prior"])
        increase = money(bucket["credit"])
        decrease = money(bucket["debit"])
        close = money(prior + increase - decrease)
        rows_out.append(
            {
                "key": key,
                "label": label,
                "priorYearEnd": money_text(prior),
                "increase": money_text(increase),
                "decrease": money_text(decrease),
                "yearEnd": money_text(close),
            }
        )
        total_prior += prior
        total_increase += increase
        total_decrease += decrease
        total_close += close

    net_profit = money(pnl["year"])
    retained_prior = money(retained["prior"] + pnl["prior"])
    retained_close = money(
        retained["prior"] + pnl["prior"] + pnl["year"] + retained["credit"] - retained["debit"]
    )
    retained_increase = money((net_profit if net_profit > ZERO else ZERO) + retained["credit"])
    retained_decrease = money(((-net_profit) if net_profit < ZERO else ZERO) + retained["debit"])

    rows_out.append(
        {
            "key": "retained_earnings",
            "label": "未分配利润（含本年利润）",
            "priorYearEnd": money_text(retained_prior),
            "increase": money_text(retained_increase),
            "decrease": money_text(retained_decrease),
            "yearEnd": money_text(retained_close),
            "isRetained": True,
        }
    )
    total_prior += retained_prior
    total_increase += retained_increase
    total_decrease += retained_decrease
    total_close += retained_close

    return {
        "year": year,
        "rows": rows_out,
        "netProfit": money_text(net_profit),
        "totals": {
            "priorYearEnd": money_text(total_prior),
            "increase": money_text(total_increase),
            "decrease": money_text(total_decrease),
            "yearEnd": money_text(total_close),
        },
        "simplified": True,
    }
