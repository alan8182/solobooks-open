# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authoritative general ledger, sub-ledger, and account summary queries."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from core.chart_policy import CONTAINER_CODES
from core.money import ZERO, money, money_text
from db.models import ChartAccount, JournalEntry, JournalLine
from db.session import session_scope
from services.account_mapper import PAYER_BOSS, PAYER_COMPANY, normalize_payer

POSTED_STATUSES = ("posted", "reversed")


def _account_matches(requested_code: str, actual_code: str) -> bool:
    """查某科目账页 = 该科目及其全部下级的合计。

    原先靠 ``AGGREGATE_PARENT_CODES`` 白名单（只列了 8 个码）决定要不要归集子级，
    结果 5001/5051/5402 一旦有明细子科目，查它们的账页会**静默返回空**——白名单
    是会漂移的第二真值。科目编码本身是层级编码，更长的编码必然是下级，前缀判断
    天然正确，不需要清单。

    注意不能拿 ``chart_is_leaf`` 来判定：4 位总账恒为末级（存量记账依赖），
    5602 既有直接记账也有 5602001 子科目，两种都要算进来。
    """
    return actual_code == requested_code or actual_code.startswith(requested_code)


def _fmt_quantity(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _month_range(month: str) -> tuple[date, date]:
    try:
        year, month_number = (int(part) for part in month.split("-", 1))
        return date(year, month_number, 1), date(
            year,
            month_number,
            calendar.monthrange(year, month_number)[1],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("账期格式应为 YYYY-MM") from exc


async def query_general_ledger(
    company_id: str,
    account_code: str,
    month: str,
    *,
    partner_id: str | None = None,
) -> list[dict[str, Any]]:
    """单科目逐笔流水（总账下钻用）。

    .. deprecated:: 2026-10
        归集真值已移至 ``ledger_book_service``：请改用
        ``build_ledger_book(..., account_code=...)`` 的 ``details[0].lines``。
        本函数不再被前端调用，仅作历史兜底，勿在此基础上新增功能。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    # 法定账表口径：按记账期间归集。补记凭证（业务日期在前、记账期间
                    # 在后）必须计入记账期间，按业务日期过滤会让它在自己的期间里消失。
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(account_code),
                )
                .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no, JournalLine.line_no)
            )
        ).all()

    if partner_id:
        # auxiliary 无索引，量级小走内存过滤（与辅助核算余额表同一取舍）
        rows = [
            (entry, line)
            for entry, line in rows
            if isinstance(line.auxiliary, dict) and line.auxiliary.get("partner_id") == partner_id
        ]

    opening = ZERO
    transactions: list[dict[str, Any]] = []
    for entry, line in rows:
        signed = money(line.amount) if line.direction == "debit" else -money(line.amount)
        if entry.period_start < start:
            opening += signed
            continue
        transaction = {
            "entryId": entry.id,
            "voucherRef": entry.voucher_no,
            "date": entry.occurred_on.isoformat(),
            "summary": entry.summary,
            "debit": money(line.amount) if line.direction == "debit" else ZERO,
            "credit": money(line.amount) if line.direction == "credit" else ZERO,
        }
        quantity = _fmt_quantity(line.quantity)
        if quantity is not None:
            transaction["quantity"] = quantity
            transaction["unitPrice"] = money_text(line.unit_price) if line.unit_price is not None else None
            transaction["unit"] = line.unit
        transactions.append(transaction)

    running = money(opening)
    output: list[dict[str, Any]] = []
    for transaction in transactions:
        running = money(running + transaction["debit"] - transaction["credit"])
        row = {
            **transaction,
            "debit": money_text(transaction["debit"]),
            "credit": money_text(transaction["credit"]),
            "balance": money_text(running),
        }
        if transaction.get("quantity") is not None:
            row["quantity"] = transaction["quantity"]
            row["unitPrice"] = transaction.get("unitPrice")
            row["unit"] = transaction.get("unit")
        output.append(row)
    return output


async def query_sub_ledger(
    company_id: str,
    account_code: str,
    month: str,
    *,
    partner_id: str | None = None,
) -> list[dict[str, Any]]:
    """单科目明细账（按凭证聚合行）。

    .. deprecated:: 2026-10
        归集真值已移至 ``ledger_book_service``：请改用
        ``build_ledger_book(..., account_code=...)`` 的 ``details[0]``（粒度为
        凭证行，父科目前缀合成聚合页）。本函数不再被前端调用，仅作历史兜底
        与测试对账基准，勿在此基础上新增功能。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        entries = (
            await session.execute(
                select(JournalEntry)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    # 与总账同口径：按记账期间归集（见 query_general_ledger 注释）
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(account_code),
                )
                .distinct()
                .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no)
            )
        ).scalars().all()
        entry_ids = [entry.id for entry in entries]
        lines = (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id.in_(entry_ids))
                .order_by(JournalLine.journal_entry_id, JournalLine.line_no)
            )
        ).scalars().all() if entry_ids else []

    by_entry: dict[str, list[JournalLine]] = defaultdict(list)
    for line in lines:
        by_entry[line.journal_entry_id].append(line)

    def _partner_of(line: JournalLine) -> str:
        auxiliary = line.auxiliary if isinstance(line.auxiliary, dict) else {}
        return str(auxiliary.get("partner_id") or "")

    opening = ZERO
    output: list[dict[str, Any]] = []
    for entry in entries:
        own_lines = [line for line in by_entry[entry.id] if _account_matches(account_code, line.account_code)]
        if partner_id:
            # 与总账同口径：只保留挂了该往来单位的自身行（无匹配行的凭证整行跳过，
            # 不产生空明细也不污染期初）；对方科目行照常全额展示——往来挂账是行级
            # 信息，不改变凭证的借贷结构。
            own_lines = [line for line in own_lines if _partner_of(line) == partner_id]
            if not own_lines:
                continue
        debit = sum((money(line.amount) for line in own_lines if line.direction == "debit"), ZERO)
        credit = sum((money(line.amount) for line in own_lines if line.direction == "credit"), ZERO)
        quantity_in = sum((line.quantity for line in own_lines if line.direction == "debit" and line.quantity is not None), Decimal(0))
        quantity_out = sum((line.quantity for line in own_lines if line.direction == "credit" and line.quantity is not None), Decimal(0))
        if entry.period_start < start:
            opening += debit - credit
            continue
        counterparties = [
            {
                "code": line.account_code,
                "name": line.account_name,
                "amount": money_text(line.amount),
            }
            for line in by_entry[entry.id]
            if not _account_matches(account_code, line.account_code)
        ]
        row = {
            "entryId": entry.id,
            "voucherRef": entry.voucher_no,
            "date": entry.occurred_on.isoformat(),
            "summary": entry.summary,
            "debit": debit,
            "credit": credit,
            "counterparties": counterparties,
        }
        if quantity_in or quantity_out:
            row["quantityIn"] = _fmt_quantity(quantity_in)
            row["quantityOut"] = _fmt_quantity(quantity_out)
            row["unit"] = next((line.unit for line in own_lines if line.unit), None)
        output.append(row)

    running = money(opening)
    for transaction in output:
        running = money(running + transaction["debit"] - transaction["credit"])
        transaction["debit"] = money_text(transaction["debit"])
        transaction["credit"] = money_text(transaction["credit"])
        transaction["balance"] = money_text(running)
    return output


async def query_partner_balances(company_id: str, account_code: str, month: str) -> dict[str, Any]:
    """核算项目余额表：按往来单位汇总某科目的期初/本期借贷/期末。

    与科目余额表同口径（法定账表按记账期间 period_start 归集）。
    没有 partner_id 的行归入"未指定往来单位"组，而不是被丢掉——
    数据不丢是这张表能被会计信任的前提。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(account_code),
                )
            )
        ).all()

    buckets: dict[str, dict[str, Any]] = {}
    for entry, line in rows:
        auxiliary = line.auxiliary if isinstance(line.auxiliary, dict) else {}
        partner_id = str(auxiliary.get("partner_id") or "")
        partner_name = str(auxiliary.get("partner_name") or "") or "未指定往来单位"
        bucket = buckets.setdefault(
            partner_id or "__unassigned__",
            {
                "partnerId": partner_id,
                "partnerName": partner_name,
                "opening": ZERO,
                "debit": ZERO,
                "credit": ZERO,
            },
        )
        amount = money(line.amount)
        if entry.period_start < start:
            bucket["opening"] += amount if line.direction == "debit" else -amount
        elif line.direction == "debit":
            bucket["debit"] += amount
        else:
            bucket["credit"] += amount

    items: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    unassigned: dict[str, Any] | None = None
    for bucket in buckets.values():
        closing = bucket["opening"] + bucket["debit"] - bucket["credit"]
        item = {
            "partnerId": bucket["partnerId"],
            "partnerName": bucket["partnerName"],
            "opening": money_text(bucket["opening"]),
            "debit": money_text(bucket["debit"]),
            "credit": money_text(bucket["credit"]),
            "closing": money_text(closing),
            "unassigned": bucket["partnerId"] == "",
        }
        if item["unassigned"]:
            unassigned = item
        else:
            items.append(item)
        # 合计含"未指定往来单位"组：这张表的合计必须与科目余额表同数，
        # 否则会计一眼就能看出少了钱，整张表失去可信度。
        total_debit += bucket["debit"]
        total_credit += bucket["credit"]

    items.sort(key=lambda row: row["partnerName"])
    return {
        "accountCode": account_code,
        "month": month,
        "items": items,
        "unassigned": unassigned,
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
    }


ADVANCE_PAYABLE_PREFIX = "2241"
BOSS_ADVANCE_LABEL = "老板（股东）"


async def query_advance_balances(company_id: str, month: str) -> dict[str, Any]:
    """股东/员工垫付台账：按垫付人汇总其他应付款的期初、本期发生与期末未报。

    为什么不给 2241 加子科目：资产负债表用**精确码**求和（见 accrual_service
    里 2202 的注释——加 2202001 会让暂估应付款从报表上静默消失），而
    ``chart_is_leaf`` 又规定 4 位总账恒为末级。垫付人是"人"不是科目，
    身份本来就存在 ``entry.payer`` 上，这里按它归集，不动科目表、不动凭证。

    为什么不走往来单位辅助核算：``query_partner_balances`` 只按
    ``partner_id`` 分组，只有姓名没有档案的行会全部折叠进「未指定往来单位」，
    按人拆不开；而且把员工塞进客户/供应商档案会污染往来单位主数据。

    口径与法定账表一致：只认 posted/reversed，按 period_start 归集。
    本表是 2241 的**子集**（只含垫付业务），非垫付业务（发票待付款、往来款）
    单独统计供勾稽——不能让会计误以为这张表等于其他应付款期末余额。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(ADVANCE_PAYABLE_PREFIX),
                )
            )
        ).all()

    buckets: dict[str, dict[str, Any]] = {}
    other = {"advance": ZERO, "reimbursed": ZERO, "count": 0}
    total = {"opening": ZERO, "advance": ZERO, "reimbursed": ZERO}
    for entry, line in rows:
        metadata = entry.metadata_json if isinstance(entry.metadata_json, dict) else {}
        payer = normalize_payer(metadata.get("payer"))
        amount = money(line.amount)
        if payer == PAYER_COMPANY:
            # 非垫付业务：不计入任何人的余额，只统计发生额做勾稽
            other["count"] += 1
            if line.direction == "credit":
                other["advance"] += amount
            else:
                other["reimbursed"] += amount
            continue

        payee, _, employee_name = payer.partition(":")
        is_boss = payee == PAYER_BOSS
        bucket = buckets.setdefault(
            payer,
            {
                "payer": payer,
                "kind": "boss" if is_boss else "employee",
                "name": BOSS_ADVANCE_LABEL if is_boss else (employee_name or "员工"),
                "opening": ZERO,
                "advance": ZERO,
                "reimbursed": ZERO,
                "count": 0,
                "lastDate": "",
            },
        )
        # 2241 是负债：贷方=垫付增加，借方=报销冲销
        if entry.period_start < start:
            bucket["opening"] += amount if line.direction == "credit" else -amount
        elif line.direction == "credit":
            bucket["advance"] += amount
        else:
            bucket["reimbursed"] += amount
        bucket["count"] += 1
        occurred = entry.occurred_on.isoformat() if entry.occurred_on else ""
        if occurred > bucket["lastDate"]:
            bucket["lastDate"] = occurred

    items: list[dict[str, Any]] = []
    for bucket in buckets.values():
        closing = bucket["opening"] + bucket["advance"] - bucket["reimbursed"]
        items.append(
            {
                "payer": bucket["payer"],
                "kind": bucket["kind"],
                "name": bucket["name"],
                "opening": money_text(bucket["opening"]),
                "advance": money_text(bucket["advance"]),
                "reimbursed": money_text(bucket["reimbursed"]),
                "closing": money_text(closing),
                "count": bucket["count"],
                "lastDate": bucket["lastDate"],
            }
        )
        total["opening"] += bucket["opening"]
        total["advance"] += bucket["advance"]
        total["reimbursed"] += bucket["reimbursed"]

    # 老板排最前（老板最关心自己垫了多少），其余按未报余额从大到小
    items.sort(key=lambda row: (row["kind"] != "boss", -Decimal(row["closing"]), row["name"]))
    return {
        "accountCode": ADVANCE_PAYABLE_PREFIX,
        "month": month,
        "asOf": end.isoformat(),
        "items": items,
        "totals": {
            "opening": money_text(total["opening"]),
            "advance": money_text(total["advance"]),
            "reimbursed": money_text(total["reimbursed"]),
            "closing": money_text(total["opening"] + total["advance"] - total["reimbursed"]),
        },
        "other": {
            "advance": money_text(other["advance"]),
            "reimbursed": money_text(other["reimbursed"]),
            "count": other["count"],
        },
    }


async def get_balance_summary(company_id: str, month: str) -> dict[str, Any]:
    """科目余额表（全科目，含零行）。

    .. deprecated:: 2026-10
        归集真值已移至 ``ledger_book_service``：请改用
        ``build_ledger_book(..., include_zero_rows=True)`` 的 ``balance`` 段
        （字段兼容，另含方向与零行标记）。前端与内部调用方已全部迁移，本函数
        仅作测试对账基准与历史兜底，勿在此基础上新增功能。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    # 法定账表口径：按记账期间（period_start）归集，不用业务日期。
                    # 补记凭证计入记账期间所在月，与凭证列表/结账/申报口径一致。
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                )
            )
        ).all()
        chart_rows = (
            await session.execute(
                select(
                    ChartAccount.code,
                    ChartAccount.name,
                    ChartAccount.is_leaf,
                    ChartAccount.is_active,
                    ChartAccount.quantity_enabled,
                    ChartAccount.unit,
                )
                .where(ChartAccount.company_id == company_id)
                .order_by(ChartAccount.code)
            )
        ).all()

    chart_by_code = {
        code: {
            "name": name,
            "is_leaf": is_leaf,
            "is_active": is_active,
            "quantity_enabled": quantity_enabled,
            "unit": unit,
        }
        for code, name, is_leaf, is_active, quantity_enabled, unit in chart_rows
    }

    # 期初取记账期间早于本月所有过账分录的累计净额，期末 = 期初 + 本期借贷（借余为正的符号约定）
    accounts: dict[str, dict[str, Any]] = {}
    # 数量结存（P1 WP-D）：借方=收、贷方=发，跨期累计，方向恒按借正
    quantities: dict[str, dict[str, Decimal]] = {}
    for entry, line in rows:
        bucket = accounts.setdefault(
            line.account_code,
            {"name": line.account_name, "opening": ZERO, "debit": ZERO, "credit": ZERO},
        )
        if line.quantity is not None:
            qty_bucket = quantities.setdefault(
                line.account_code, {"opening": Decimal(0), "in": Decimal(0), "out": Decimal(0)}
            )
            if entry.period_start < start:
                qty_bucket["opening"] += line.quantity if line.direction == "debit" else -line.quantity
            elif line.direction == "debit":
                qty_bucket["in"] += line.quantity
            else:
                qty_bucket["out"] += line.quantity
        if entry.period_start < start:
            bucket["opening"] += money(line.amount) if line.direction == "debit" else -money(line.amount)
        elif line.direction == "debit":
            bucket["debit"] += money(line.amount)
        else:
            bucket["credit"] += money(line.amount)

    # 补全无发生额的启用末级科目（0 行），保证科目完整性可核对；容器科目行单独补，
    # 作为子级合计行（isParent）。中层级科目（is_leaf=False 且非容器）不出 0 行。
    for code, info in chart_by_code.items():
        if not info["is_active"]:
            continue
        if code in CONTAINER_CODES or info["is_leaf"]:
            accounts.setdefault(
                code,
                {"name": info["name"], "opening": ZERO, "debit": ZERO, "credit": ZERO},
            )

    items: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    for code, info in sorted(accounts.items()):
        is_parent = code in CONTAINER_CODES
        if is_parent:
            # 容器科目行 = 自身（历史直接记账，如有）+ 全部子级合计；仅展示，
            # 不计入合计与试算平衡，避免与子级行重复计数。
            opening = sum((b["opening"] for c, b in accounts.items() if c.startswith(code)), ZERO)
            debit = sum((b["debit"] for c, b in accounts.items() if c.startswith(code)), ZERO)
            credit = sum((b["credit"] for c, b in accounts.items() if c.startswith(code)), ZERO)
        else:
            opening, debit, credit = info["opening"], info["debit"], info["credit"]
        chart_info = chart_by_code.get(code)
        item = {
            "accountCode": code,
            "accountName": chart_info["name"] if chart_info else info["name"],
            "openingBalance": money_text(opening),
            "debitTotal": money_text(debit),
            "creditTotal": money_text(credit),
            "balance": money_text(debit - credit),
            "closingBalance": money_text(opening + debit - credit),
            "isParent": is_parent,
        }
        if not is_parent and chart_info and chart_info["quantity_enabled"]:
            qty = quantities.get(code, {"opening": Decimal(0), "in": Decimal(0), "out": Decimal(0)})
            item.update(
                {
                    "hasQuantity": True,
                    "unit": chart_info["unit"],
                    "quantityOpening": _fmt_quantity(qty["opening"]),
                    "quantityIn": _fmt_quantity(qty["in"]),
                    "quantityOut": _fmt_quantity(qty["out"]),
                    "quantityClosing": _fmt_quantity(qty["opening"] + qty["in"] - qty["out"]),
                }
            )
        items.append(item)
        if not is_parent:
            total_debit += money(item["debitTotal"])
            total_credit += money(item["creditTotal"])

    return {
        "month": month,
        "items": items,
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
        "isBalanced": total_debit == total_credit,
    }


async def query_chronological(company_id: str, month: str) -> dict[str, Any]:
    """序时账：本月全部已过账凭证分录按凭证顺序逐行平铺。

    与总账/科目余额表同口径（按记账期间 period_start 归集）；序时账只回答
    "这个月按顺序发生了什么"，不滚余额。
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start >= start,
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                )
                .order_by(JournalEntry.period_start, JournalEntry.voucher_no, JournalLine.line_no)
            )
        ).all()

    items: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    for entry, line in rows:
        amount = money(line.amount)
        items.append(
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "period": entry.period_start.isoformat(),
                "date": entry.occurred_on.isoformat(),
                "summary": entry.summary,
                "accountCode": line.account_code,
                "accountName": line.account_name,
                "direction": line.direction,
                "debit": money_text(amount) if line.direction == "debit" else money_text(ZERO),
                "credit": money_text(amount) if line.direction == "credit" else money_text(ZERO),
            }
        )
        if line.direction == "debit":
            total_debit += amount
        else:
            total_credit += amount

    return {
        "month": month,
        "items": items,
        "voucherCount": len({row["entryId"] for row in items}),
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
        "isBalanced": total_debit == total_credit,
    }


async def query_multi_column(company_id: str, account_code: str, month: str) -> dict[str, Any]:
    """多栏账：总账科目按明细子科目展开多栏（借方多栏或贷方多栏）。

    - 栏 = 启用的末级子科目（startswith 归集口径，与总账/明细账一致）
    - 收入类科目（损益-收入类）贷方多栏，其余借方多栏——经典费用多栏账的镜像
    - 行 = 本月凭证；每行在子科目栏记净发生额（借贷相抵，红冲自然为负）
    """
    start, end = _month_range(month)
    async with session_scope() as session:
        chart_rows = (
            await session.execute(
                select(ChartAccount.code, ChartAccount.name, ChartAccount.category, ChartAccount.is_active, ChartAccount.is_leaf)
                .where(ChartAccount.company_id == company_id)
                .order_by(ChartAccount.code)
            )
        ).all()
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start >= start,
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                )
                .order_by(JournalEntry.period_start, JournalEntry.voucher_no, JournalLine.line_no)
            )
        ).all()

    parent = next((row for row in chart_rows if row.code == account_code), None)
    if parent is None:
        raise ValueError(f"科目 {account_code} 不存在")
    children = [
        {"code": row.code, "name": row.name}
        for row in chart_rows
        if row.is_active and row.is_leaf and row.code != account_code and row.code.startswith(account_code)
    ]
    if not children:
        return {
            "accountCode": account_code,
            "accountName": parent.name,
            "month": month,
            "direction": "debit",
            "columns": [],
            "items": [],
            "columnTotals": {},
            "debitTotal": money_text(ZERO),
            "creditTotal": money_text(ZERO),
            "hint": "该科目没有启用的明细子科目，无法展开多栏",
        }

    child_codes = {child["code"] for child in children}

    def _column_of(code: str) -> str | None:
        # 子科目可能还有下级（9 位二级），按最长前缀落到末级栏
        matches = [c for c in child_codes if code == c or code.startswith(c)]
        return max(matches, key=len) if matches else None

    # 收入类：贷方多栏（贷记明细为正）；其余：借方多栏
    credit_mode = (parent.category or "") == "损益-收入类"
    main_side = "credit" if credit_mode else "debit"

    entries: dict[str, dict[str, Any]] = {}
    column_totals: dict[str, Decimal] = {child["code"]: ZERO for child in children}
    other_debit = ZERO
    other_credit = ZERO

    def _is_own(code: str) -> bool:
        # 多栏账的语义就是归集子级，与总账/明细账同一前缀口径
        # （如 5301 不在容器清单，但其子科目 5301001-4 必须落栏）
        return code == account_code or code.startswith(account_code)

    # 两遍处理：对方科目行可能排在自身行之前（按行号序），先建桶再补对方发生额
    for entry, line in rows:
        if not _is_own(line.account_code):
            continue
        column = _column_of(line.account_code)
        amount = money(line.amount)
        signed = amount if line.direction == main_side else -amount
        bucket = entries.setdefault(
            entry.id,
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "date": entry.occurred_on.isoformat(),
                "summary": entry.summary,
                "columns": {child["code"]: ZERO for child in children},
                "debit": ZERO,
                "credit": ZERO,
            },
        )
        if column:
            bucket["columns"][column] += signed
            column_totals[column] += signed
        if line.direction == "debit":
            bucket["debit"] += amount
        else:
            bucket["credit"] += amount

    for entry, line in rows:
        if _is_own(line.account_code):
            continue
        if entry.id not in entries:
            continue
        if line.direction == "debit":
            other_debit += money(line.amount)
        else:
            other_credit += money(line.amount)

    items: list[dict[str, Any]] = []
    total_main = ZERO
    for bucket in entries.values():
        item = {
            "entryId": bucket["entryId"],
            "voucherRef": bucket["voucherRef"],
            "date": bucket["date"],
            "summary": bucket["summary"],
            "columns": {code: money_text(value) for code, value in bucket["columns"].items()},
            "debit": money_text(bucket["debit"]),
            "credit": money_text(bucket["credit"]),
            "net": money_text(bucket["debit"] - bucket["credit"]),
        }
        items.append(item)
        total_main += bucket["debit"] - bucket["credit"] if not credit_mode else bucket["credit"] - bucket["debit"]

    return {
        "accountCode": account_code,
        "accountName": parent.name,
        "month": month,
        "direction": main_side,
        "columns": children,
        "items": items,
        "columnTotals": {code: money_text(value) for code, value in column_totals.items()},
        "mainTotal": money_text(total_main),
        "debitTotal": money_text(other_debit + sum((b["debit"] for b in entries.values()), ZERO)),
        "creditTotal": money_text(other_credit + sum((b["credit"] for b in entries.values()), ZERO)),
    }
