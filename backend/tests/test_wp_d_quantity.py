# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P1 WP-D 数量金额式：数量行落库 / 容差校验 / 非数量科目忽略 / 余额表数量结存 /
confirm 数量挂接 / seed 默认开启 / 旧账凭证数量透传。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from db.models import ChartAccount, JournalEntry, JournalLine
from services.accounting_core_service import create_entry, ensure_tenant
from services.legacy_import_service import confirm_voucher_import, parse_voucher_rows
from services.ledger_service import get_balance_summary, query_general_ledger, query_sub_ledger


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str, user_id: str = "owner-1") -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)


async def _post(company_id: str, entry_id: str, occur_date: str, debit: list[dict], credit: list[dict]) -> None:
    from db.session import session_scope

    total = sum(float(line["amount"]) for line in debit)
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-{entry_id}",
                "direction": "expense",
                "amount": f"{total:.2f}",
                "title": "数量测试",
                "category": "商品采购",
                "occurDate": occur_date,
                "voucherLines": {"debit": debit, "credit": credit},
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{company_id}-{entry_id}-req",
            confirm=True,
        )


def _line(code: str, name: str, amount: str, **extra) -> dict:
    line = {"accountCode": code, "accountName": name, "amount": amount}
    line.update(extra)
    return line


async def _line_quantity(company_id: str, entry_id: str, code: str) -> tuple[object, object, object]:
    from db.session import session_scope

    async with session_scope() as session:
        line = (
            await session.execute(
                select(JournalLine)
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(JournalLine.company_id == company_id, JournalEntry.id == f"{company_id}-{entry_id}", JournalLine.account_code == code)
            )
        ).scalars().first()
        assert line is not None
        return line.quantity, line.unit_price, line.unit


@pytest.mark.asyncio
async def test_seed_defaults_quantity_accounts():
    company_id = "c-wpd-seed"
    await _setup_company(company_id)
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ChartAccount).where(ChartAccount.company_id == company_id, ChartAccount.code.in_(("1403", "1405", "1408", "5602")))
            )
        ).scalars().all()
    flags = {row.code: row.quantity_enabled for row in rows}
    assert flags == {"1403": True, "1405": True, "1408": True, "5602": False}


@pytest.mark.asyncio
async def test_quantity_line_persisted_and_mismatch_rejected():
    company_id = "c-wpd-line"
    await _setup_company(company_id)
    await _post(
        company_id, "buy", "2026-10-08",
        [_line("1405", "库存商品", "1000.00", quantity="50", unitPrice="20", unit="件")],
        [_line("1002", "银行存款", "1000.00")],
    )
    quantity, unit_price, unit = await _line_quantity(company_id, "buy", "1405")
    assert str(quantity) == "50.0000"
    assert str(unit_price) == "20.000000"
    assert unit == "件"

    # 数量×单价与金额差超过 1 分 → 拒绝
    from core.errors import AccountingError
    from db.session import session_scope

    with pytest.raises(AccountingError) as exc_info:
        async with session_scope() as session:
            await create_entry(
                session,
                {
                    "id": f"{company_id}-bad",
                    "direction": "expense",
                    "amount": "1000.00",
                    "title": "单价不符",
                    "category": "商品采购",
                    "occurDate": "2026-10-09",
                    "voucherLines": {
                        "debit": [_line("1405", "库存商品", "1000.00", quantity="50", unitPrice="30")],
                        "credit": [_line("1002", "银行存款", "1000.00")],
                    },
                },
                company_id=company_id,
                user_id="owner-1",
                request_id=f"{company_id}-bad-req",
                confirm=True,
            )
    assert exc_info.value.code == "QUANTITY_AMOUNT_MISMATCH"

    # 容差内（1 分）允许：999.99 vs 50×20
    await _post(
        company_id, "tolerance", "2026-10-10",
        [_line("1405", "库存商品", "999.99", quantity="50", unitPrice="20")],
        [_line("1002", "银行存款", "999.99")],
    )


@pytest.mark.asyncio
async def test_non_quantity_account_ignores_quantity():
    company_id = "c-wpd-ign"
    await _setup_company(company_id)
    await _post(
        company_id, "expense", "2026-10-08",
        [_line("5602004", "管理费用-办公费", "80.00", quantity="3", unit="箱")],
        [_line("1002", "银行存款", "80.00")],
    )
    quantity, unit_price, unit = await _line_quantity(company_id, "expense", "5602004")
    assert quantity is None and unit_price is None and unit is None


@pytest.mark.asyncio
async def test_balance_summary_quantity_columns():
    company_id = "c-wpd-bal"
    await _setup_company(company_id)
    await _post(
        company_id, "in-a", "2026-09-10",
        [_line("1405", "库存商品", "1000.00", quantity="50")],
        [_line("1002", "银行存款", "1000.00")],
    )
    await _post(
        company_id, "in-b", "2026-10-05",
        [_line("1405", "库存商品", "600.00", quantity="30")],
        [_line("1002", "银行存款", "600.00")],
    )
    await _post(
        company_id, "out-a", "2026-10-20",
        [_line("5401002", "主营业务成本-生产", "200.00")],
        [_line("1405", "库存商品", "200.00", quantity="10")],
    )

    summary = await get_balance_summary(company_id, "2026-10")
    item = next(row for row in summary["items"] if row["accountCode"] == "1405")
    assert item["hasQuantity"] is True
    assert item["quantityOpening"] == "50"
    assert item["quantityIn"] == "30"
    assert item["quantityOut"] == "10"
    assert item["quantityClosing"] == "70"
    # 非数量科目不带数量字段
    other = next(row for row in summary["items"] if row["accountCode"] == "1002")
    assert "hasQuantity" not in other


@pytest.mark.asyncio
async def test_ledgers_carry_quantity_fields():
    company_id = "c-wpd-led"
    await _setup_company(company_id)
    await _post(
        company_id, "buy", "2026-10-08",
        [_line("1405", "库存商品", "1000.00", quantity="50", unitPrice="20", unit="件")],
        [_line("1002", "银行存款", "1000.00")],
    )

    general = await query_general_ledger(company_id, "1405", "2026-10")
    assert general[0]["quantity"] == "50"
    assert general[0]["unitPrice"] == "20.00"
    assert general[0]["unit"] == "件"

    sub = await query_sub_ledger(company_id, "1405", "2026-10")
    assert sub[0]["quantityIn"] == "50"
    assert sub[0]["unit"] == "件"


@pytest.mark.asyncio
async def test_confirm_attaches_quantity_to_stock_line(client, auth_headers):
    """confirm 的数量由服务端挂到第一行数量科目；非数量科目请求不挂。"""
    company_id = "c1"
    await _setup_company(company_id, user_id="test_user_001")
    response = client.post(
        "/api/record/confirm",
        json={
            "direction": "expense",
            "amount": 600,
            "title": "进货一批",
            "category": "商品采购",
            "occurDate": "2026-10-12",
            "quantity": 30,
            "unitPrice": 20,
            "unit": "件",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    lines = response.json()["entry"]["voucherLines"]["debit"]
    stock_lines = [line for line in lines if line["accountCode"] == "1405"]
    assert stock_lines, lines
    assert stock_lines[0]["quantity"] == "30"
    assert stock_lines[0]["unit"] == "件"


@pytest.mark.asyncio
async def test_legacy_voucher_quantity_passthrough():
    company_id = "c-wpd-legacy"
    await _setup_company(company_id)
    rows = [
        {"凭证号": "1", "日期": "2026-05-06", "摘要": "旧账进货", "科目编码": "1405", "借方金额": "1200", "数量": "60", "单价": "20", "单位": "件"},
        {"凭证号": "1", "日期": "2026-05-06", "摘要": "旧账进货", "科目编码": "1002", "贷方金额": "1200"},
    ]
    parsed = await parse_voucher_rows(company_id, rows)
    assert parsed["errors"] == []
    line = next(line for line in parsed["valid"][0]["lines"] if line["accountCode"] == "1405")
    assert line["quantity"] == "60"
    assert line["unitPrice"] == "20"

    result = await confirm_voucher_import(company_id, "owner-1", parsed["valid"], batch_id="qty01")
    assert result["imported"] == 1
    from db.session import session_scope

    async with session_scope() as session:
        # 凭证 id 是确定性的：legacy-{company}-{batch}-{凭证号}
        entry_id = f"legacy-{company_id}-qty01-1"
        line = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry_id, JournalLine.account_code == "1405")
            )
        ).scalars().first()
    assert line is not None
    assert str(line.quantity) == "60.0000"
    assert line.unit == "件"
