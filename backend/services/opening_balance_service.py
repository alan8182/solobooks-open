# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Opening balances represented by immutable system-generated journals."""
from __future__ import annotations

from datetime import date, timedelta
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
    reverse_journal,
    serialize_journal,
)

# ponytail: 损益类 + 成本类科目代码集合，用于年中建账与资产负债期初拆分
EXPENSE_REVENUE_CODES = frozenset([
    "5001", "5051", "5111", "5301", "5401", "5401001", "5401002", "5402", "5403",
    "5601", "5601001", "5601006", "5601011", "5601015", "5601016",
    "5602", "5602001", "5602002", "5602004", "5602005", "5602006", "5602007",
    "5602011", "5602013", "5602014", "5602015", "5602999",
    "5603", "5603001", "5603002", "5603003", "5603004",
    "5711", "5801", "4301",
])


def _entry_id(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}"


def _ytd_entry_id(company_id: str, fiscal_year: int) -> str:
    return f"opening-{company_id}-{fiscal_year}-ytd"


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


async def _find_ytd_entry(session: AsyncSession, company_id: str, fiscal_year: int) -> JournalEntry | None:
    return (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.id == _ytd_entry_id(company_id, fiscal_year),
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
    line_map: dict[str, tuple[Decimal, Decimal, str]] = {}
    if entry is not None:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry.id)
            )
        ).scalars().all()
        for line in lines:
            debit, credit, partner = line_map.get(line.account_code, (ZERO, ZERO, ""))
            if line.direction == "debit":
                debit += money(line.amount)
            else:
                credit += money(line.amount)
            partner_name = (line.auxiliary or {}).get("partner_name") or partner
            line_map[line.account_code] = (debit, credit, partner_name)

    return [
        {
            "id": entry.id if entry else "",
            "company_id": company_id,
            "account_code": account.code,
            "account_name": account.name,
            "debit_balance": money_text(line_map.get(account.code, (ZERO, ZERO, ""))[0]),
            "credit_balance": money_text(line_map.get(account.code, (ZERO, ZERO, ""))[1]),
            "partner_name": line_map.get(account.code, (ZERO, ZERO, ""))[2],
            "fiscal_year": fiscal_year,
            "voucherStatus": entry.status if entry else None,
            "voucherRef": entry.voucher_no if entry else None,
        }
        for account in accounts
    ]


async def get_opening_details(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """获取完整的期初配置，包括资产负债期初、损益本年累计与税务持久化备忘。"""
    if session is None:
        async with session_scope() as owned_session:
            return await get_opening_details(company_id, fiscal_year, session=owned_session)

    all_balances = await get_opening_balances(company_id, fiscal_year, session=session)

    # 读取可能存在的 YTD 凭证
    ytd_entry = await _find_ytd_entry(session, company_id, fiscal_year)
    ytd_map: dict[str, tuple[Decimal, Decimal]] = {}
    if ytd_entry is not None:
        ytd_lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == ytd_entry.id)
            )
        ).scalars().all()
        for line in ytd_lines:
            if line.account_code in ("3103", "3104006"):
                continue
            debit, credit = ytd_map.get(line.account_code, (ZERO, ZERO))
            if line.direction == "debit":
                debit += money(line.amount)
            else:
                credit += money(line.amount)
            ytd_map[line.account_code] = (debit, credit)

    leaf_accounts = (
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

    # ponytail: 5 开头或属于损益类的末级科目归入年中累计，其余归入资产负债期初
    expense_revenue_set = {
        acc.code
        for acc in leaf_accounts
        if acc.code.startswith("5")
        or (acc.category and "损益" in acc.category)
        or acc.code in EXPENSE_REVENUE_CODES
    }

    balance_items = [b for b in all_balances if b["account_code"] not in expense_revenue_set]

    ytd_items = [
        {
            "account_code": acc.code,
            "account_name": acc.name,
            "debit_balance": money_text(ytd_map.get(acc.code, (ZERO, ZERO))[0]),
            "credit_balance": money_text(ytd_map.get(acc.code, (ZERO, ZERO))[1]),
        }
        for acc in leaf_accounts
        if acc.code in expense_revenue_set
    ]

    from db.models import CompanyProfile
    tax_memory = {
        "priorYearLoss": 0,
        "prepaidCitTax": 0,
        "migrationNote": "",
        "startPeriod": f"{fiscal_year}-01",
    }
    try:
        prof_row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        if prof_row and prof_row.profile and "taxOpening" in prof_row.profile:
            tax_memory.update(prof_row.profile["taxOpening"])
    except Exception:
        pass

    entry = await _find_entry(session, company_id, fiscal_year)
    start_period = tax_memory.get("startPeriod") or (entry.occurred_on.strftime("%Y-%m") if entry else f"{fiscal_year}-01")

    # ponytail: 尝试读取关联的期初固定资产卡片
    fixed_assets: list[dict[str, Any]] = []
    try:
        from services.database import get_conn
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT payload FROM fixed_assets WHERE company_id = ? AND payload LIKE ?",
                (company_id, f'%"{_entry_id(company_id, fiscal_year)}"%' ),
            )
            rows = await cursor.fetchall()
            import json
            for r in rows:
                try:
                    fixed_assets.append(json.loads(r["payload"]))
                except Exception:
                    pass
        finally:
            await conn.close()
    except Exception:
        pass

    return {
        "items": balance_items,
        "ytd_items": ytd_items,
        "start_period": start_period,
        "tax_memory": tax_memory,
        "fixed_assets": fixed_assets,
        "is_locked": bool(entry and entry.status in ("posted", "reversed")),
        "voucher_no": entry.voucher_no if entry else None,
    }


async def save_opening_balances(
    company_id: str,
    fiscal_year: int,
    items: list[dict[str, Any]],
    *,
    user_id: str,
    step_up_token: str | None,
    request_id: str,
    start_period: str | None = None,
    ytd_items: list[dict[str, Any]] | None = None,
    tax_memory: dict[str, Any] | None = None,
    fixed_assets: list[dict[str, Any]] | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    if session is None:
        async with session_scope() as owned_session:
            return await save_opening_balances(
                company_id,
                fiscal_year,
                items,
                user_id=user_id,
                step_up_token=step_up_token,
                request_id=request_id,
                start_period=start_period,
                ytd_items=ytd_items,
                tax_memory=tax_memory,
                fixed_assets=fixed_assets,
                session=owned_session,
            )
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
            "期初凭证已存在；如需调整，请先冲销重置后重新导入",
            409,
            {"entryId": existing.id, "status": existing.status},
        )

    # 1. 解析启用期间与生效日期
    period_str = (start_period or f"{fiscal_year}-01").strip()
    try:
        pyear, pmonth = [int(p) for p in period_str.split("-")[:2]]
    except Exception:
        pyear, pmonth = fiscal_year, 1
    occur_date = date(pyear, pmonth, 1)

    # 2. 组装资产负债期初分录（可挂接客商辅助信息）
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
        partner = str(item.get("partner_name") or "").strip()
        if partner:
            base["auxiliary"] = {"partner_name": partner, "type": "partner"}

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

    title_desc = f"{pyear} 年 {pmonth} 月期初余额" if pmonth > 1 else f"{fiscal_year} 年期初余额"
    entry = await create_entry(
        session,
        {
            "id": _entry_id(company_id, fiscal_year),
            "direction": "owner_transfer",
            "amount": total_debit,
            "title": title_desc,
            "category": "期初余额",
            "occurDate": occur_date.isoformat(),
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

    # 3. 年中建账：若 pmonth > 1 且录入了损益累计发生额，生成 YTD 过渡凭证
    # ponytail: 差额直接计入 3103 本年利润，使 YTD 凭证借贷严格平衡且无缝衔接后续利润表本年累计
    ytd_entry = None
    if pmonth > 1 and ytd_items:
        ytd_debits: list[dict[str, Any]] = []
        ytd_credits: list[dict[str, Any]] = []
        sum_ytd_debit = ZERO
        sum_ytd_credit = ZERO

        for yi in ytd_items:
            d = money(yi.get("debit_balance") or ZERO, allow_negative=False)
            c = money(yi.get("credit_balance") or ZERO, allow_negative=False)
            acode = str(yi.get("account_code") or "").strip()
            aname = str(yi.get("account_name") or "").strip()
            if d > ZERO:
                ytd_debits.append({"accountCode": acode, "accountName": aname, "amount": d})
                sum_ytd_debit += d
            if c > ZERO:
                ytd_credits.append({"accountCode": acode, "accountName": aname, "amount": c})
                sum_ytd_credit += c

        if sum_ytd_debit > ZERO or sum_ytd_credit > ZERO:
            # 动态查找当前账套有效的本年利润/未分配利润末级科目（《小企业会计准则》为 3103）
            profit_acc = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.is_active.is_(True),
                        ChartAccount.is_leaf.is_(True),
                        ChartAccount.code.in_(("3103", "3104006")),
                    )
                )
            ).scalars().first()
            p_code = profit_acc.code if profit_acc else "3103"
            p_name = profit_acc.name if profit_acc else "本年利润"

            if sum_ytd_credit > sum_ytd_debit:
                # 贷方(收入)大于借方(费用)，借方补入本年利润差额配平
                diff = sum_ytd_credit - sum_ytd_debit
                ytd_debits.append({"accountCode": p_code, "accountName": p_name, "amount": diff})
            elif sum_ytd_debit > sum_ytd_credit:
                # 借方(费用)大于贷方(收入)，贷方补入本年利润差额配平
                diff = sum_ytd_debit - sum_ytd_credit
                ytd_credits.append({"accountCode": p_code, "accountName": p_name, "amount": diff})

            ytd_occur_date = occur_date - timedelta(days=1)
            balanced_ytd_amount = max(sum_ytd_debit, sum_ytd_credit)
            ytd_entry = await create_entry(
                session,
                {
                    "id": _ytd_entry_id(company_id, fiscal_year),
                    "direction": "owner_transfer",
                    "amount": balanced_ytd_amount,
                    "title": f"{pyear} 年 1-{pmonth - 1} 月损益累计发生额",
                    "category": "期初余额",
                    "occurDate": ytd_occur_date.isoformat(),
                    "sourceType": "opening_balance_ytd",
                    "dedupFingerprint": f"opening_ytd:{company_id}:{fiscal_year}",
                    "entryType": "opening",
                    "voucherAuthority": "system",
                    "voucherLines": {"debit": ytd_debits, "credit": ytd_credits},
                },
                company_id=company_id,
                user_id=user_id,
                request_id=f"{request_id}:ytd",
                confirm=True,
            )

    # 4. 持久化税务备忘与建账起始期
    if tax_memory or period_str:
        from db.models import CompanyProfile
        try:
            prof_row = (
                await session.execute(
                    select(CompanyProfile).where(CompanyProfile.company_id == company_id)
                )
            ).scalar_one_or_none()
            tax_dict = {
                "priorYearLoss": float((tax_memory or {}).get("prior_year_loss") or (tax_memory or {}).get("priorYearLoss") or 0),
                "prepaidCitTax": float((tax_memory or {}).get("prepaid_cit_tax") or (tax_memory or {}).get("prepaidCitTax") or 0),
                "migrationNote": str((tax_memory or {}).get("migration_note") or (tax_memory or {}).get("migrationNote") or ""),
                "startPeriod": period_str,
            }
            if prof_row is None:
                session.add(CompanyProfile(company_id=company_id, profile={"taxOpening": tax_dict}))
            else:
                p = dict(prof_row.profile or {})
                p["taxOpening"] = tax_dict
                prof_row.profile = p
        except Exception:
            pass

    # 5. 关联同步固定资产卡片
    if fixed_assets:
        from services.fixed_asset_service import save_fixed_asset
        acct_1601_debit = sum((money(item.get("debit_balance") or ZERO) for item in items if str(item.get("account_code") or "").strip() == "1601"), ZERO)
        acct_1602_credit = sum((money(item.get("credit_balance") or ZERO) for item in items if str(item.get("account_code") or "").strip() == "1602"), ZERO)

        total_card_cost = sum((money(fa.get("cost") or fa.get("originalValue") or ZERO) for fa in fixed_assets), ZERO)
        total_card_depr = sum((money(fa.get("accumulated_depreciation") or fa.get("accumulatedDepreciation") or ZERO) for fa in fixed_assets), ZERO)

        if acct_1601_debit > ZERO and abs(total_card_cost - acct_1601_debit) > Decimal("0.05"):
            raise AccountingError(
                "FIXED_ASSET_MISMATCH",
                f"固定资产卡片原值合计(¥{money_text(total_card_cost)})与1601科目借方(¥{money_text(acct_1601_debit)})不一致",
                422,
            )
        if acct_1602_credit > ZERO and abs(total_card_depr - acct_1602_credit) > Decimal("0.05"):
            raise AccountingError(
                "FIXED_ASSET_MISMATCH",
                f"固定资产卡片已提折旧合计(¥{money_text(total_card_depr)})与1602科目贷方(¥{money_text(acct_1602_credit)})不一致",
                422,
            )

        for fa in fixed_assets:
            card_payload = {
                "name": str(fa.get("name") or "期初固定资产"),
                "category": str(fa.get("category") or "电子设备"),
                "purchaseDate": str(fa.get("purchase_date") or fa.get("purchaseDate") or f"{pyear}-01-01"),
                "cost": float(fa.get("cost") or fa.get("originalValue") or 0),
                "accumulatedDepreciation": float(fa.get("accumulated_depreciation") or fa.get("accumulatedDepreciation") or 0),
                "usefulMonths": int(fa.get("useful_months") or fa.get("usefulMonths") or 36),
                "salvageRate": float(fa.get("salvage_rate") or fa.get("salvageRate") or 0.05),
                "sourceEntryId": _entry_id(company_id, fiscal_year),
                "isOpening": True,
            }
            try:
                await save_fixed_asset(card_payload, user_id=user_id, company_id=company_id)
            except Exception:
                pass

    res = await serialize_journal(session, entry)
    if ytd_entry:
        res["ytdEntry"] = await serialize_journal(session, ytd_entry)
    return res


async def reset_opening_balances(
    company_id: str,
    fiscal_year: int,
    *,
    user_id: str,
    step_up_token: str | None,
    request_id: str,
    reason: str = "期初数据冲销重置",
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """冲销已有期初凭证（含 YTD 凭证），允许用户重新导入建账。"""
    if session is None:
        async with session_scope() as owned_session:
            return await reset_opening_balances(
                company_id,
                fiscal_year,
                user_id=user_id,
                step_up_token=step_up_token,
                request_id=request_id,
                reason=reason,
                session=owned_session,
            )
    await require_role(session, company_id, user_id, {"owner"})
    await consume_step_up_token(
        session,
        step_up_token,
        company_id=company_id,
        user_id=user_id,
        action="adjust_opening_balance",
    )
    entry = await _find_entry(session, company_id, fiscal_year)
    if entry is None:
        raise AccountingError("OPENING_NOT_FOUND", "未找到可冲销的期初凭证", 404)

    # 冲销主期初凭证
    reversal_entry = await reverse_journal(
        session,
        entry.id,
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:reset_main",
        reason=reason,
    )

    # 若有 YTD 凭证，同步冲销
    ytd_entry = await _find_ytd_entry(session, company_id, fiscal_year)
    if ytd_entry is not None and ytd_entry.status == "posted":
        await reverse_journal(
            session,
            ytd_entry.id,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:reset_ytd",
            reason=reason,
        )

    # 清理该期初关联的固定资产卡片
    try:
        from services.database import get_conn
        conn = await get_conn()
        try:
            await conn.execute(
                "DELETE FROM fixed_assets WHERE company_id = ? AND payload LIKE ?",
                (company_id, f'%"{_entry_id(company_id, fiscal_year)}"%' ),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        pass

    return {"ok": True, "reversal": await serialize_journal(session, reversal_entry)}


async def check_balanced(
    company_id: str,
    fiscal_year: int,
    *,
    session: AsyncSession,
) -> dict[str, Any]:
    balances = await get_opening_balances(company_id, fiscal_year, session=session)
    # 仅资产负债科目参与试算平衡
    balance_only = [item for item in balances if item["account_code"] not in EXPENSE_REVENUE_CODES]
    total_debit = sum((money(item["debit_balance"]) for item in balance_only), ZERO)
    total_credit = sum((money(item["credit_balance"]) for item in balance_only), ZERO)
    return {
        "balanced": total_debit == total_credit and total_debit > ZERO,
        "total_debit": money_text(total_debit),
        "total_credit": money_text(total_credit),
        "count": sum(1 for item in balance_only if money(item["debit_balance"]) or money(item["credit_balance"])),
    }
