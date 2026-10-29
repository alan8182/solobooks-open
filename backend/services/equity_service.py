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
- 净利润 = 未月结损益（5xxx 余额净额）+ 月结滚入 3103 的净额。账套做了月结后
  5xxx 余额恒为 0，只看余额会把全年利润算成 0；结转凭证的 3103 借贷双方是
  月度盈亏，不是权益增减，也不进未分配利润行的增减列。年末 3103→3104006
  结转是内部结转，两头都不进增减行（其价值已含在各月损益里）。
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

    # 第一遍：按凭证归集行（join 结果不保证同凭证相邻，用 dict 分组）。
    # 损益结转/年末结转是凭证级口径，必须整张凭证一起看。
    entries_by_id: dict[str, dict[str, Any]] = {}
    for entry, line in rows:
        code = line.account_code
        amount = money(line.amount)
        signed = amount if line.direction == "credit" else -amount
        info = entries_by_id.get(entry.id)
        if info is None:
            info = {"entry": entry, "in_prior": entry.period_start <= prior_end, "lines": []}
            entries_by_id[entry.id] = info
        info["lines"].append((code, line.direction, amount, signed))

    family_buckets = {
        key: {"prior": ZERO, "credit": ZERO, "debit": ZERO}
        for key, _, _ in CAPITAL_FAMILIES
    }
    retained = {"prior": ZERO, "credit": ZERO, "debit": ZERO}
    pnl = {"prior": ZERO, "year": ZERO}
    # 月结损益滚入 3103 的净额（收入贷/亏损借）：账套做了月结后 5xxx 余额恒为 0，
    # 净利润必须从结转凭证取，否则全年利润被算成 0；年末 3103→3104006 结转是
    # 内部结转，两头都不进增减行（其价值已含在各月损益里）。
    pnl_closed = ZERO
    # 3104 借方 = 利润分配（提取盈余公积 / 应付利润）；3103 的结转借方是亏损，
    # 不是分配，不能混入。
    distribution_debit = ZERO

    for info in entries_by_id.values():
        entry = info["entry"]
        in_prior = info["in_prior"]
        is_closing = entry.entry_type in ("closing", "reversal") or bool(
            (entry.metadata_json or {}).get("isClosingTransfer")
        )
        entry_codes = {code for code, _, _, _ in info["lines"]}
        is_carryforward = is_closing and "3104006" in entry_codes

        for code, direction, amount, signed in info["lines"]:
            for key, _, prefixes in CAPITAL_FAMILIES:
                if _match(code, prefixes):
                    bucket = family_buckets[key]
                    if in_prior:
                        bucket["prior"] += signed
                    elif direction == "credit":
                        bucket["credit"] += amount
                    else:
                        bucket["debit"] += amount
                    break
            else:
                if _match(code, RETAINED_CODES):
                    if in_prior:
                        retained["prior"] += signed
                    elif is_closing:
                        if code == "3103" and not is_carryforward:
                            pnl_closed += signed
                        # 3104006 年末结转行与 3103 结转行的价值已含在各月损益中
                    else:
                        if code == "3104" and direction == "debit":
                            distribution_debit += amount
                        if direction == "credit":
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

    # 净利润 = 未月结损益（5xxx 余额净额）+ 月结滚入 3103 的净额。
    # 账套做了月结后 5xxx 恒为 0，只看余额会把全年利润算成 0。
    net_profit = money(pnl["year"] + pnl_closed)
    retained_prior = money(retained["prior"] + pnl["prior"])
    retained_close = money(
        retained["prior"] + pnl["prior"] + net_profit + retained["credit"] - retained["debit"]
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

    # ── 会计专业视图：会企04表节次结构 × 小企业四组件 ──
    # 每列恒等式：年初 + 净利润 + 所有者投入 + 利润分配 + 内部结转 = 年末，
    # 内部结转做轧差余项，吸收未单列的权益调整（如盈余公积转增、资本公积转增）。
    # 利润分配列：盈余公积取本年贷方（提取盈余公积），未分配利润取 3104 借方的负数
    # （提取与对所有者分配都走 3104 借方；3103 的结转借方是亏损，已含在净利润里）；
    # 未分配利润非结转贷方罕见调整归入投入列，代数恒等。
    comp_keys = [key for key, _, _ in CAPITAL_FAMILIES] + ["retained_earnings"]

    def _cells(prior_v: Decimal, np_v: Decimal, inv_v: Decimal, dist_v: Decimal, close_v: Decimal) -> dict[str, str]:
        transfer = money(close_v - prior_v - np_v - inv_v - dist_v)
        return {
            "priorYearEnd": money_text(prior_v),
            "yearStart": money_text(prior_v),
            "netProfit": money_text(np_v),
            "ownerInvestment": money_text(inv_v),
            "profitDistribution": money_text(dist_v),
            "internalTransfer": money_text(transfer),
            "yearEnd": money_text(close_v),
        }

    cols: dict[str, dict[str, str]] = {}
    for key, _, _ in CAPITAL_FAMILIES:
        bucket = family_buckets[key]
        prior_v = money(bucket["prior"])
        close_v = money(prior_v + bucket["credit"] - bucket["debit"])
        if key == "surplus_reserve":
            cols[key] = _cells(prior_v, ZERO, ZERO, bucket["credit"], close_v)
        else:
            cols[key] = _cells(prior_v, ZERO, money(bucket["credit"] - bucket["debit"]), ZERO, close_v)
    cols["retained_earnings"] = _cells(
        retained_prior, net_profit, retained["credit"], -distribution_debit, retained_close,
    )

    total_cells = {
        field: money_text(sum((money(cols[k][field]) for k in comp_keys), ZERO))
        for field in ("priorYearEnd", "yearStart", "netProfit", "ownerInvestment",
                      "profitDistribution", "internalTransfer", "yearEnd")
    }

    row_specs = [
        ("prior_year_end", "一、上年年末余额", "total", "priorYearEnd"),
        ("year_start", "二、本年年初余额", "total", "yearStart"),
        ("changes", "三、本年增减变动金额", "section", None),
        ("net_profit", "（一）净利润（净亏损以“－”号填列）", "item", "netProfit"),
        ("owner_investment", "（二）所有者投入和减少资本", "item", "ownerInvestment"),
        ("profit_distribution", "（三）利润分配", "item", "profitDistribution"),
        ("internal_transfer", "（四）所有者权益内部结转", "item", "internalTransfer"),
        ("year_end", "四、本年年末余额", "total", "yearEnd"),
    ]
    std_rows: list[dict[str, Any]] = []
    for row_key, label, kind, field in row_specs:
        if row_key == "changes":
            values = {
                k: money_text(money(cols[k]["yearEnd"]) - money(cols[k]["priorYearEnd"]))
                for k in comp_keys
            }
            values["total"] = money_text(
                money(total_cells["yearEnd"]) - money(total_cells["priorYearEnd"])
            )
        else:
            values = {k: cols[k][field] for k in comp_keys}
            values["total"] = total_cells[field]
        row: dict[str, Any] = {"key": row_key, "label": label, "values": values}
        if kind == "item":
            row["level"] = 1
        else:
            row["kind"] = kind
        std_rows.append(row)

    standard = {
        "columns": [
            {"key": "paid_in_capital", "label": "实收资本（或股本）"},
            {"key": "capital_reserve", "label": "资本公积"},
            {"key": "surplus_reserve", "label": "盈余公积"},
            {"key": "retained_earnings", "label": "未分配利润"},
            {"key": "total", "label": "所有者权益合计"},
        ],
        "rows": std_rows,
    }

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
        "standard": standard,
    }
