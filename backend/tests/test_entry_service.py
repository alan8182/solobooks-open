# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import pytest


@pytest.fixture
async def temp_entry_db():
    from services import entry_service

    await entry_service._ensure_schema()
    yield


@pytest.mark.asyncio
async def test_save_entry_generates_owner_transfer_voucher_lines(temp_entry_db):
    from services.entry_service import save_entry

    saved = await save_entry(
        {
            "id": "owner-invest-1",
            "direction": "owner_transfer",
            "amount": 50000,
            "category": "股东投入",
            "title": "股东实缴出资款",
            "payer": "company_account",
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert saved["voucherLines"]["debit"][0]["accountCode"] == "1002"
    assert saved["voucherLines"]["credit"][0]["accountCode"] == "3001"


@pytest.mark.asyncio
async def test_save_entry_maps_interest_income_to_financial_expense(temp_entry_db):
    from services.entry_service import save_entry

    saved = await save_entry(
        {
            "id": "interest-income-1",
            "direction": "income",
            "amount": 4.15,
            "category": "利息收入",
            "title": "银行结息",
            "payer": "company_account",
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert saved["voucherLines"]["debit"][0]["accountCode"] == "1002"
    assert saved["voucherLines"]["credit"][0]["accountCode"] == "5603005"
    assert saved["voucherLines"]["credit"][0]["accountName"] == "财务费用-利息收入"


@pytest.mark.asyncio
async def test_save_entry_repairs_stale_interest_income_voucher_lines(temp_entry_db):
    from services.entry_service import save_entry

    saved = await save_entry(
        {
            "id": "interest-income-stale",
            "direction": "income",
            "amount": 4.15,
            "category": "利息收入",
            "title": "银行结息",
            "payer": "company_account",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 4.15}],
                "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 4.15}],
            },
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert saved["voucherLines"]["credit"][0]["accountCode"] == "5603005"
    assert saved["voucherLines"]["credit"][0]["accountName"] == "财务费用-利息收入"


@pytest.mark.asyncio
async def test_save_entry_repairs_stale_known_category_voucher_lines(temp_entry_db):
    from services.entry_service import save_entry

    saved = await save_entry(
        {
            "id": "office-stale-5699",
            "direction": "expense",
            "amount": 88,
            "category": "办公",
            "title": "办公用品",
            "payer": "company_account",
            "voucherLines": {
                "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": 88}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 88}],
            },
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert saved["voucherLines"]["debit"][0]["accountCode"] == "5602004"
    assert saved["voucherLines"]["debit"][0]["accountName"] == "办公费"
    assert saved["voucherLines"]["credit"][0]["accountCode"] == "1002"
@pytest.mark.asyncio
async def test_update_entry_persists_direction_and_recomputes_voucher_lines(temp_entry_db):
    from services.entry_service import save_entry, update_entry

    await save_entry(
        {
            "id": "entry-to-fix",
            "direction": "expense",
            "amount": 50000,
            "category": "其他支出",
            "title": "旧摘要",
            "payer": "boss_personal",
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    updated = await update_entry(
        "entry-to-fix",
        {
            "title": "股东实缴出资款",
            "direction": "owner_transfer",
            "category": "股东投入",
            "payer": "company_account",
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert updated is not None
    assert updated["title"] == "股东实缴出资款"
    assert updated["direction"] == "owner_transfer"
    assert updated["category"] == "股东投入"
    assert updated["payer"] == "company_account"
    assert updated["voucherLines"]["debit"][0]["accountCode"] == "1002"
    assert updated["voucherLines"]["credit"][0]["accountCode"] == "3001"

@pytest.mark.asyncio
async def test_generate_entry_vouchers_persists_status_ref_and_is_idempotent(temp_entry_db):
    from services.entry_service import generate_entry_vouchers, list_entries, save_entry

    await save_entry(
        {
            "id": "voucher-target",
            "direction": "expense",
            "amount": 88,
            "category": "办公",
            "title": "办公用品",
            "payer": "company_account",
        },
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    generated = await generate_entry_vouchers(
        ["voucher-target", "voucher-target"],
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert len(generated) == 1
    assert generated[0]["voucherStatus"] == "draft"
    assert generated[0]["voucherRef"] is None
    assert generated[0]["voucherLines"]["debit"][0]["accountCode"] == "5602004"

    stored = next(e for e in await list_entries("u-entry-test", "c-entry-test") if e["id"] == "voucher-target")
    first_ref = stored["voucherRef"]
    assert stored["voucherStatus"] == "draft"

    generated_again = await generate_entry_vouchers(
        ["voucher-target"],
        user_id="u-entry-test",
        company_id="c-entry-test",
    )

    assert len(generated_again) == 1
    assert generated_again[0]["voucherRef"] == first_ref
    stored_again = next(e for e in await list_entries("u-entry-test", "c-entry-test") if e["id"] == "voucher-target")
    assert stored_again["voucherRef"] == first_ref
