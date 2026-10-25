# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""存货成本结转（配比原则）。

商品/原材料采购计入 1403/1405 后，如果销售时不结转主营业务成本，
利润表会"有收入、无成本"，毛利与所得税双高，存货永久挂账。

系统无法自动知道"卖了多少"（没有出入库单），所以结转金额由用户确认：
按月提供一键结转，金额不得超过该科目当前借方余额；未结转时在结账检查中提醒。
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from services.accounting_core_service import POSTING_ROLES, create_entry, require_role

# P1 WP-D：1408 委托加工物资并入存货结转口径（此前漏计，余额会滞留账上）
INVENTORY_ACCOUNTS = ("1403", "1405", "1408")
COST_ACCOUNT_BY_INVENTORY = {"1403": "5401002", "1405": "5401002"}
COST_ACCOUNT_NAME = "主营业务成本-生产"


async def get_inventory_balance(session: AsyncSession, company_id: str, month: str | None = None) -> dict[str, Any]:
    """各存货科目的借方余额（截至月末）。"""
    from datetime import date
    import calendar

    boundary = None
    if month:
        year, mon = int(month[:4]), int(month[5:7])
        boundary = date(year, mon, calendar.monthrange(year, mon)[1])
    query = (
        select(
            JournalLine.account_code,
            JournalLine.account_name,
            func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
            func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.status.in_(("posted", "reversed")),
            JournalEntry.entry_type != "closing",
            JournalLine.company_id == company_id,
            JournalLine.account_code.in_(INVENTORY_ACCOUNTS),
        )
        .group_by(JournalLine.account_code, JournalLine.account_name)
    )
    if boundary is not None:
        query = query.where(JournalEntry.occurred_on <= boundary)
    rows = (
        await session.execute(query.order_by(JournalLine.account_code))
    ).all()
    accounts = {}
    total = ZERO
    for code, name, debit, credit in rows:
        balance = money(money(debit) - money(credit))
        accounts[code] = {"accountName": name, "balance": balance}
        total += balance
    return {"accounts": accounts, "total": money(total)}


async def transfer_inventory_to_cost(
    session: AsyncSession,
    company_id: str,
    *,
    month: str,
    amount: Decimal | str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """月末一键结转：借 主营业务成本 / 贷 库存商品。

    金额由用户确认（系统没有出入库单，无法自动判定销售数量），
    但不得超过存货科目当前余额，防止把没买的成本结出来。
    """
    await require_role(session, company_id, user_id, POSTING_ROLES)
    transfer_amount = money(amount)
    if transfer_amount <= ZERO:
        raise AccountingError("INVALID_AMOUNT", "结转金额必须大于零", 422)

    inventory = await get_inventory_balance(session, company_id, month=month)
    available = inventory["total"]
    if transfer_amount > available:
        raise AccountingError(
            "INVENTORY_INSUFFICIENT",
            f"结转金额 {money_text(transfer_amount)} 超过存货余额 {money_text(available)}",
            422,
            {"available": money_text(available), "requested": money_text(transfer_amount)},
        )

    # 按科目余额比例分摊，尾差落在余额最大的科目上，保证借贷必平
    allocations: list[tuple[str, str, Decimal]] = []
    allocated = ZERO
    entries_sorted = sorted(
        inventory["accounts"].items(), key=lambda kv: kv[1]["balance"], reverse=True,
    )
    for index, (code, info) in enumerate(entries_sorted):
        if index == len(entries_sorted) - 1:
            share = money(transfer_amount - allocated)
        else:
            share = money(transfer_amount * info["balance"] / available) if available > ZERO else ZERO
            allocated += share
        if share > ZERO:
            allocations.append((code, info["accountName"], share))

    credit_lines = [
        {"accountCode": code, "accountName": name, "amount": share}
        for code, name, share in allocations
    ]
    debit_total = money(sum((line["amount"] for line in credit_lines), ZERO))
    debit_lines = [{"accountCode": "5401002", "accountName": COST_ACCOUNT_NAME, "amount": debit_total}]
    year, mon = int(month[:4]), int(month[5:7])
    occur_date = date(year, mon, calendar.monthrange(year, mon)[1]).isoformat()
    entry = await create_entry(
        session,
        {
            "id": f"inventory-transfer-{company_id}-{month}",
            "direction": "expense",
            "amount": debit_total,
            "title": f"{month} 月末存货成本结转",
            "category": "商品采购",
            "occurDate": occur_date,
            "sourceType": "inventory_transfer",
            "dedupFingerprint": f"inventory_transfer:{company_id}:{month}",
            "entryType": "standard",
            "ruleVersion": "inventory-transfer-v1",
            "voucherLines": {
                "debit": [{"accountCode": "5401002", "accountName": COST_ACCOUNT_NAME, "amount": debit_total}],
                "credit": credit_lines,
            },
        },
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )
    return {
        "entryId": entry.id,
        "voucherRef": entry.voucher_no,
        "amount": money_text(debit_total),
        "allocations": [
            {"accountCode": code, "accountName": name, "amount": money_text(share)}
            for code, name, share in allocations
        ],
    }
