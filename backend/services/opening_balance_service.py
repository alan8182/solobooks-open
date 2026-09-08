"""Opening balances represented by immutable system-generated journals."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, ENTRY_POSTED_IMMUTABLE
from core.money import ZERO, money, money_text
from db.models import ChartAccount, JournalEntry, JournalLine
from db.session import session_scope
from services.accounting_core_service import (
    consume_step_up_token,
    create_entry,
    require_role,
    serialize_journal,
)


def _entry_id(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}"


async def _find_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    return (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.id == _entry_id(company_id, fiscal_year),
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type == "opening",
            )
        )
    ).scalar_one_or_none()


async def get_opening_balances(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    if session is None:
        async with session_scope() as owned_session:
            return await get_opening_balances(
                company_id,
                fiscal_year,
                session=owned_session,
            )
    accounts = (
        await session.execute(
            select(ChartAccount)
            .where(
                ChartAccount.company_id == company_id,
                ChartAccount.is_active.is_(True),
                ChartAccount.is_leaf.is_(True),
            )
            .order_by(ChartAccount.code)
        )
    ).scalars().all()
    entry = await _find_entry(session, company_id, fiscal_year)
    line_map: dict[str, tuple[Decimal, Decimal]] = {}
    if entry is not None:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry.id)
            )
        ).scalars().all()
        for line in lines:
            debit, credit = line_map.get(line.account_code, (ZERO, ZERO))
            if line.direction == "debit":
                debit += money(line.amount)
            else:
                credit += money(line.amount)
            line_map[line.account_code] = (debit, credit)

    return [
        {
            "id": entry.id if entry else "",
            "company_id": company_id,
            "account_code": account.code,
            "account_name": account.name,
            "debit_balance": money_text(line_map.get(account.code, (ZERO, ZERO))[0]),
            "credit_balance": money_text(line_map.get(account.code, (ZERO, ZERO))[1]),
            "fiscal_year": fiscal_year,
            "voucherStatus": entry.status if entry else None,
            "voucherRef": entry.voucher_no if entry else None,
        }
        for account in accounts
    ]


async def save_opening_balances(
    company_id: str,
    fiscal_year: int,
    items: list[dict[str, Any]],
    *,
    user_id: str,
    step_up_token: str | None,
    request_id: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    if session is None:
        async with session_scope() as owned_session:
            return await check_balanced(company_id, fiscal_year, session=owned_session)
    await require_role(session, company_id, user_id, {"owner"})
    await consume_step_up_token(
        session,
        step_up_token,
        company_id=company_id,
        user_id=user_id,
        action="adjust_opening_balance",
    )
    existing = await _find_entry(session, company_id, fiscal_year)
    if existing is not None:
        raise AccountingError(
            ENTRY_POSTED_IMMUTABLE,
            "期初凭证已存在；如需调整，请冲销后创建新的期初调整凭证",
            409,
            {"entryId": existing.id, "status": existing.status},
        )

    debit_lines: list[dict[str, Any]] = []
    credit_lines: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    for item in items:
        debit = money(item.get("debit_balance") or ZERO, allow_negative=False)
        credit = money(item.get("credit_balance") or ZERO, allow_negative=False)
        if debit > ZERO and credit > ZERO:
            raise AccountingError("OPENING_SIDE_CONFLICT", "同一科目不能同时填写借方和贷方期初余额")
        base = {
            "accountCode": str(item.get("account_code") or "").strip(),
            "accountName": str(item.get("account_name") or "").strip(),
        }
        if debit > ZERO:
            debit_lines.append({**base, "amount": debit})
            total_debit += debit
        if credit > ZERO:
            credit_lines.append({**base, "amount": credit})
            total_credit += credit
    if total_debit == ZERO or total_debit != total_credit:
        raise AccountingError(
            "UNBALANCED_ENTRY",
            "期初余额借贷必须相等且大于零",
            422,
            {"debit": money_text(total_debit), "credit": money_text(total_credit)},
        )

    entry = await create_entry(
        session,
        {
            "id": _entry_id(company_id, fiscal_year),
            "direction": "owner_transfer",
            "amount": total_debit,
            "title": f"{fiscal_year} 年期初余额",
            "category": "期初余额",
            "occurDate": date(fiscal_year, 1, 1).isoformat(),
            "sourceType": "opening_balance",
            "dedupFingerprint": f"opening:{company_id}:{fiscal_year}",
            "entryType": "opening",
            "voucherAuthority": "system",
            "voucherLines": {"debit": debit_lines, "credit": credit_lines},
        },
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )
    return await serialize_journal(session, entry)


async def check_balanced(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession,
) -> dict[str, Any]:
    balances = await get_opening_balances(company_id, fiscal_year, session=session)
    total_debit = sum((money(item["debit_balance"]) for item in balances), ZERO)
    total_credit = sum((money(item["credit_balance"]) for item in balances), ZERO)
    return {
        "balanced": total_debit == total_credit,
        "total_debit": money_text(total_debit),
        "total_credit": money_text(total_credit),
        "count": sum(1 for item in balances if money(item["debit_balance"]) or money(item["credit_balance"])),
    }
