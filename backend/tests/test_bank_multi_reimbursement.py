# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""1:N 合并发票结算、同人多笔垫付报销与唯一子集和匹配回归测试。"""
from __future__ import annotations

import json
import uuid

import aiosqlite
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError
from db.base import Base
import db.models  # noqa: F401
from db.models import JournalEntry, LegacyEntryProjection, SourceTransaction
from deps.auth import AuthContext
from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
from services.accounting_core_service import create_entry, ensure_tenant, get_journal, serialize_journal
from services.bank_service import ai_suggest_matches, import_statements_core, import_statements_json
from services.bank_settlement_service import confirm_statement_match


@pytest.fixture
async def multi_core_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statements ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, transaction_date TEXT NOT NULL, "
            "description TEXT NOT NULL, amount NUMERIC(20,2) NOT NULL, "
            "balance NUMERIC(20,2) NOT NULL, statement_id TEXT NOT NULL, "
            "matched_entry_id TEXT, description_normalized INTEGER NOT NULL, created_at TEXT NOT NULL, "
            "transaction_reference TEXT, source_row_index INTEGER)"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statement_matches (statement_id TEXT, company_id TEXT, entry_id TEXT, "
            "previous_billing_status TEXT, previous_payment_date TEXT, created_at TEXT, "
            "PRIMARY KEY(statement_id,entry_id))"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def multi_bank_db(monkeypatch):
    from services import bank_service, database, entry_service

    await entry_service._ensure_schema()
    bank_service._schema_ready = False
    uri = f"file:multi-reim-{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = await aiosqlite.connect(uri, uri=True)
    keeper.row_factory = aiosqlite.Row

    async def get_test_conn():
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        return conn

    monkeypatch.setattr(database, "get_conn", get_test_conn)
    monkeypatch.setattr(bank_service, "get_conn", get_test_conn)
    await keeper.execute(
        "CREATE TABLE entries ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, company_id TEXT NOT NULL, "
        "payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    await keeper.commit()
    try:
        yield uri
    finally:
        bank_service._schema_ready = False
        await keeper.close()


async def _insert_legacy_entry(company_id: str, payload: dict) -> None:
    from services import database

    conn = await database.get_conn()
    try:
        await conn.execute(
            "INSERT INTO entries (id, user_id, company_id, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload["id"],
                "u-multi-test",
                company_id,
                json.dumps(payload, ensure_ascii=False),
                "2026-10-01T00:00:00+00:00",
                "2026-10-01T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _get_legacy_payload(entry_id: str) -> dict | None:
    from db.session import session_scope
    from services import database

    conn = await database.get_conn()
    try:
        cursor = await conn.execute("SELECT payload FROM entries WHERE id = ?", (entry_id,))
        row = await cursor.fetchone()
        if row is not None:
            return json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    finally:
        await conn.close()

    async with session_scope() as session:
        journal = await session.get(JournalEntry, entry_id)
        if journal is None:
            return None
        return await serialize_journal(session, journal)


async def _get_stmt(batch_id: str) -> dict:
    from services import database

    conn = await database.get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM bank_statements WHERE statement_id = ? LIMIT 1", (batch_id,))
        row = await cursor.fetchone()
        assert row is not None
        return dict(row)
    finally:
        await conn.close()


def _pending_invoice_payload(
    entry_id: str, amount: str, title: str, *, occur_date: str = "2026-10-05",
) -> dict:
    return {
        "id": entry_id,
        "source": "invoice_import",
        "sourceType": "invoice_import",
        "direction": "expense",
        "amount": amount,
        "title": title,
        "category": "差旅费",
        "occurDate": occur_date,
        "billingStatus": "has_invoice",
        "voucherLines": {
            "debit": [{"accountCode": "5602006", "accountName": "管理费用-差旅费", "amount": amount}],
            "credit": [{"accountCode": "2241", "accountName": "其他应付款-发票待付款", "amount": amount}],
        },
    }


@pytest.mark.asyncio
async def test_multi_draft_pending_invoices_settle_and_unmatch_atomically(multi_core_sessions):
    async with multi_core_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-inv-draft-multi", "owner-1")
            await create_entry(
                session,
                _pending_invoice_payload("inv-draft-001", "600.00", "火车票(大连)"),
                company_id="c-inv-draft-multi", user_id="owner-1", request_id="r1", confirm=False,
            )
            await create_entry(
                session,
                _pending_invoice_payload("inv-draft-002", "856.50", "酒店住宿(丹东)"),
                company_id="c-inv-draft-multi", user_id="owner-1", request_id="r2", confirm=False,
            )
            await import_statements_core(
                session, company_id="c-inv-draft-multi", user_id="owner-1",
                rows=[{
                    "transaction_date": "2026-10-12",
                    "description": "杨云飞 报销差旅交通费大连/丹东",
                    "amount": "-1456.50",
                    "balance": "10000.00",
                }],
                statement_id="stmt-draft-multi", request_id="b1",
            )
            bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()

            res = await confirm_statement_match(
                session, company_id="c-inv-draft-multi", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=["inv-draft-001", "inv-draft-002"],
                request_id="match-multi",
            )
            assert res["status"] == "matched"
            assert set(res["entryIds"]) == {"inv-draft-001", "inv-draft-002"}

            for eid in ("inv-draft-001", "inv-draft-002"):
                p = await serialize_journal(session, await get_journal(session, eid, company_id="c-inv-draft-multi"))
                assert p["billingStatus"] == "reconciled"
                assert any(str(line["accountCode"]) == "1002" for line in p["voucherLines"]["credit"])

            await confirm_statement_match(
                session, company_id="c-inv-draft-multi", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=[],
                request_id="unmatch-multi",
            )
            for eid in ("inv-draft-001", "inv-draft-002"):
                p = await serialize_journal(session, await get_journal(session, eid, company_id="c-inv-draft-multi"))
                assert p["billingStatus"] == "has_invoice"
                assert any(str(line["accountCode"]) == "2241" for line in p["voucherLines"]["credit"])


@pytest.mark.asyncio
async def test_multi_posted_pending_invoices_distinct_payment_ids_and_unmatch(multi_core_sessions):
    async with multi_core_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-inv-post-multi", "owner-1")
            await create_entry(
                session,
                _pending_invoice_payload("invsame1-aaa", "500.00", "高铁票1"),
                company_id="c-inv-post-multi", user_id="owner-1", request_id="r1", confirm=True,
            )
            await create_entry(
                session,
                _pending_invoice_payload("invsame1-bbb", "956.50", "住宿票2"),
                company_id="c-inv-post-multi", user_id="owner-1", request_id="r2", confirm=True,
            )
            await import_statements_core(
                session, company_id="c-inv-post-multi", user_id="owner-1",
                rows=[{
                    "transaction_date": "2026-10-12",
                    "description": "报销差旅合并付款",
                    "amount": "-1456.50",
                    "balance": "8000.00",
                }],
                statement_id="stmt-post-multi", request_id="b1",
            )
            bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()

            res = await confirm_statement_match(
                session, company_id="c-inv-post-multi", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=["invsame1-aaa", "invsame1-bbb"],
                request_id="match-posted-multi",
            )
            expected_pids = {
                f"invoice-payment-{bank.external_key}-invsame1-aaa",
                f"invoice-payment-{bank.external_key}-invsame1-bbb",
            }
            assert set(res["entryIds"]) == expected_pids
            for pid in expected_pids:
                assert await session.get(JournalEntry, pid) is not None

            await confirm_statement_match(
                session, company_id="c-inv-post-multi", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=[],
                request_id="unmatch-posted-multi",
            )
            for pid in expected_pids:
                assert await session.get(JournalEntry, pid) is None
            for orig_id in ("invsame1-aaa", "invsame1-bbb"):
                proj = await session.get(LegacyEntryProjection, orig_id)
                payload = json.loads(proj.payload)
                assert payload["billingStatus"] == "has_invoice"
                assert "invoicePaymentSettled" not in payload


@pytest.mark.asyncio
async def test_multi_pending_invoice_mismatch_aborts_before_any_mutation(multi_core_sessions):
    async with multi_core_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-inv-partial", "owner-1")
            await create_entry(
                session,
                _pending_invoice_payload("inv-draft-keep", "600.00", "草稿差旅票"),
                company_id="c-inv-partial", user_id="owner-1", request_id="r1", confirm=False,
            )
            await create_entry(
                session,
                _pending_invoice_payload("inv-posted-keep", "300.00", "已过账差旅票"),
                company_id="c-inv-partial", user_id="owner-1", request_id="r2", confirm=True,
            )
            await import_statements_core(
                session, company_id="c-inv-partial", user_id="owner-1",
                rows=[{
                    "transaction_date": "2026-10-12",
                    "description": "差旅报销短少发票",
                    "amount": "-1000.00",
                    "balance": "7000.00",
                }],
                statement_id="stmt-partial", request_id="b1",
            )
            bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()

            with pytest.raises(AccountingError) as exc_info:
                await confirm_statement_match(
                    session, company_id="c-inv-partial", user_id="owner-1",
                    statement_id=bank.external_key, entry_ids=["inv-draft-keep", "inv-posted-keep"],
                    request_id="match-fail",
                )
            assert exc_info.value.code == "INVOICE_PARTIAL_SETTLEMENT"
            draft_p = await serialize_journal(session, await get_journal(session, "inv-draft-keep", company_id="c-inv-partial"))
            assert any(str(line["accountCode"]) == "2241" for line in draft_p["voucherLines"]["credit"])
            assert "invoicePaymentSettled" not in draft_p
            assert await session.get(JournalEntry, f"invoice-payment-{bank.external_key}-inv-posted-keep") is None


@pytest.mark.asyncio
async def test_multi_advancement_same_payer_reimburse_idempotent_double_guard_and_unmatch(multi_bank_db):
    company_id = "c-adv-batch"
    adv_ids = ["adv-trip-1", "adv-trip-2", "adv-trip-3"]
    for eid, amt, title in (
        ("adv-trip-1", 456.50, "高铁票大连"),
        ("adv-trip-2", 600.00, "丹东酒店"),
        ("adv-trip-3", 400.00, "市内交通"),
    ):
        await _insert_legacy_entry(company_id, {
            "id": eid,
            "direction": "expense",
            "amount": amt,
            "category": "差旅费",
            "title": title,
            "occurDate": "2026-10-08",
            "payer": "employee_personal:杨云飞",
            "billingStatus": "has_invoice",
        })
    await import_statements_json(company_id, [{
        "transaction_date": "2026-10-12",
        "description": "杨云飞 报销差旅交通费大连/丹东",
        "amount": -1456.50,
        "balance": 12000.00,
    }], "batch-adv-1")
    await import_statements_json(company_id, [{
        "transaction_date": "2026-10-13",
        "description": "疑似重复打款",
        "amount": -456.50,
        "balance": 11543.50,
    }], "batch-adv-2")
    stmt1 = await _get_stmt("batch-adv-1")
    stmt2 = await _get_stmt("batch-adv-2")
    ctx = AuthContext(user_id="u-multi-test", company_id=company_id)

    res = await confirm_suggestion(
        ConfirmSuggestionRequest(statement_id=stmt1["id"], type="match", matched_entry_ids=adv_ids),
        ctx,
    )
    assert res["status"] == "matched"
    reim_id = res["entryId"]
    assert reim_id == f"reim-{stmt1['id']}-batch"
    assert set(res["reimbursedEntryIds"]) == set(adv_ids)

    reim_payload = await _get_legacy_payload(reim_id)
    assert reim_payload is not None
    assert reim_payload["amount"] == "1456.50"
    assert reim_payload["direction"] == "owner_transfer"
    assert reim_payload["category"] == "员工垫付报销"
    assert reim_payload["payer"] == "employee_personal:杨云飞"
    assert set(reim_payload["reimbursedEntryIds"]) == set(adv_ids)

    for eid in adv_ids:
        orig = await _get_legacy_payload(eid)
        assert orig["billingStatus"] == "reconciled"
        assert orig["paymentDate"] == "2026-10-12"

    # Idempotent retry succeeds instead of 409
    retry = await confirm_suggestion(
        ConfirmSuggestionRequest(statement_id=stmt1["id"], type="match", matched_entry_ids=adv_ids),
        ctx,
    )
    assert retry["status"] == "already_matched"
    assert retry["entryId"] == reim_id

    # Same advancement cannot be reimbursed twice on a second statement
    with pytest.raises(HTTPException) as exc_dup:
        await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt2["id"], type="match", matched_entry_id="adv-trip-1"),
            ctx,
        )
    assert exc_dup.value.status_code == 409

    # Unmatch restores every original advancement entry and deletes batch reim entry
    unmatched = await confirm_suggestion(
        ConfirmSuggestionRequest(statement_id=stmt1["id"], type="match", matched_entry_ids=[]),
        ctx,
    )
    assert unmatched["status"] == "unmatched"
    assert await _get_legacy_payload(reim_id) is None
    for eid in adv_ids:
        orig = await _get_legacy_payload(eid)
        assert orig["billingStatus"] == "has_invoice"
        assert "paymentDate" not in orig


@pytest.mark.asyncio
async def test_multi_advancement_unmatch_via_async_session_restores_all_origins(multi_core_sessions):
    async with multi_core_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-adv-async-unmatch", "owner-1")
            for eid, amt in (("adv-a1", "500.00"), ("adv-a2", "300.00")):
                await create_entry(
                    session,
                    {
                        "id": eid,
                        "source": "invoice_import",
                        "sourceType": "invoice_import",
                        "direction": "expense",
                        "amount": amt,
                        "title": f"垫付-{eid}",
                        "category": "差旅费",
                        "payer": "boss_personal",
                        "occurDate": "2026-10-08",
                        "billingStatus": "reconciled",
                        "paymentDate": "2026-10-12",
                        "voucherLines": {
                            "debit": [{"accountCode": "5602006", "accountName": "管理费用-差旅费", "amount": amt}],
                            "credit": [{"accountCode": "2241", "accountName": "其他应付款-股东垫付", "amount": amt}],
                        },
                    },
                    company_id="c-adv-async-unmatch", user_id="owner-1", request_id=f"r-{eid}", confirm=False,
                )
            await import_statements_core(
                session, company_id="c-adv-async-unmatch", user_id="owner-1",
                rows=[{
                    "transaction_date": "2026-10-12",
                    "description": "报销老板垫付款",
                    "amount": "-800.00",
                    "balance": "5000.00",
                }],
                statement_id="stmt-async-reim", request_id="b1",
            )
            bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
            reim_id = f"reim-{bank.external_key}-batch"
            await create_entry(
                session,
                {
                    "id": reim_id,
                    "source": "bank_import",
                    "sourceType": "bank_import",
                    "direction": "owner_transfer",
                    "amount": "800.00",
                    "title": "合并报销垫付款",
                    "category": "股东垫付报销",
                    "payer": "boss_personal",
                    "occurDate": "2026-10-12",
                    "billingStatus": "reconciled",
                    "reimbursedEntryIds": ["adv-a1", "adv-a2"],
                    "reimbursedEntryStates": {
                        "adv-a1": {"billingStatus": "has_invoice", "paymentDate": None},
                        "adv-a2": {"billingStatus": "has_invoice", "paymentDate": None},
                    },
                    "voucherLines": {
                        "debit": [{"accountCode": "2241", "accountName": "其他应付款-股东垫付", "amount": "800.00"}],
                        "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "800.00"}],
                    },
                },
                company_id="c-adv-async-unmatch", user_id="owner-1", request_id="r-reim", confirm=False,
            )
            await confirm_statement_match(
                session, company_id="c-adv-async-unmatch", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=[reim_id], request_id="link-reim",
            )

            await confirm_statement_match(
                session, company_id="c-adv-async-unmatch", user_id="owner-1",
                statement_id=bank.external_key, entry_ids=[], request_id="unmatch-reim",
            )
            assert await session.get(JournalEntry, reim_id) is None
            for eid in ("adv-a1", "adv-a2"):
                proj = await session.get(LegacyEntryProjection, eid)
                payload = json.loads(proj.payload)
                assert payload["billingStatus"] == "has_invoice"
                assert "paymentDate" not in payload


@pytest.mark.asyncio
async def test_multi_advancement_rejects_mixed_payers_and_mixed_types(multi_bank_db):
    company_id = "c-adv-reject"
    await _insert_legacy_entry(company_id, {
        "id": "adv-boss",
        "direction": "expense",
        "amount": 500,
        "category": "差旅费",
        "title": "老板垫付",
        "occurDate": "2026-10-08",
        "payer": "boss_personal",
        "billingStatus": "has_invoice",
    })
    await _insert_legacy_entry(company_id, {
        "id": "adv-emp",
        "direction": "expense",
        "amount": 500,
        "category": "差旅费",
        "title": "员工垫付",
        "occurDate": "2026-10-08",
        "payer": "employee_personal:杨云飞",
        "billingStatus": "has_invoice",
    })
    await _insert_legacy_entry(company_id, {
        "id": "comp-inv",
        "direction": "expense",
        "amount": 500,
        "category": "差旅费",
        "title": "公户待付款发票",
        "occurDate": "2026-10-08",
        "payer": "company_account",
        "source": "invoice_import",
        "billingStatus": "has_invoice",
    })
    await import_statements_json(company_id, [{
        "transaction_date": "2026-10-12",
        "description": "混合报销测试",
        "amount": -1000,
        "balance": 9000,
    }], "batch-adv-reject")
    stmt = await _get_stmt("batch-adv-reject")
    ctx = AuthContext(user_id="u-multi-test", company_id=company_id)

    with pytest.raises(HTTPException) as exc_mixed_payer:
        await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match", matched_entry_ids=["adv-boss", "adv-emp"]),
            ctx,
        )
    assert exc_mixed_payer.value.status_code == 400

    with pytest.raises(HTTPException) as exc_mixed_type:
        await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match", matched_entry_ids=["adv-emp", "comp-inv"]),
            ctx,
        )
    assert exc_mixed_type.value.status_code == 400


@pytest.mark.asyncio
async def test_ai_suggest_multi_reimbursement_unique_vs_ambiguous_vs_cross_pool(multi_bank_db):
    # Case 1: Unique 3-entry subset sum inside same personal payer pool
    c_unique = "c-ai-multi-unique"
    for eid, amt, title, payer in (
        ("u1", 456.50, "高铁票1", "employee_personal:杨云飞"),
        ("u2", 600.00, "丹东酒店", "employee_personal:杨云飞"),
        ("u3", 400.00, "大连车票", "employee_personal:杨云飞"),
        ("u-extra", 210.00, "无关快递费", "employee_personal:杨云飞"),
    ):
        await _insert_legacy_entry(c_unique, {
            "id": eid,
            "direction": "expense",
            "amount": amt,
            "category": "差旅费",
            "title": title,
            "occurDate": "2026-10-08",
            "payer": payer,
            "billingStatus": "has_invoice",
        })
    await import_statements_json(c_unique, [{
        "transaction_date": "2026-10-12",
        "description": "杨云飞·移动支付 · 报销差旅交通费大连/丹东",
        "amount": -1456.50,
        "balance": 15000.00,
    }], "batch-unique-sug")
    res_unique = await ai_suggest_matches(c_unique)
    sug_unique = res_unique[0]
    assert sug_unique["type"] == "match"
    assert sug_unique["rule"] == "reimbursement_multi_match"
    assert set(sug_unique["matchedEntryIds"]) == {"u1", "u2", "u3"}
    assert len(sug_unique["matchedEntries"]) == 3

    # Case 2: Ambiguous (2 combinations both equal 1000) -> refuses auto multi-match
    c_ambig = "c-ai-multi-ambig"
    for eid, amt in (("a1", 400), ("a2", 600), ("a3", 300), ("a4", 700)):
        await _insert_legacy_entry(c_ambig, {
            "id": eid,
            "direction": "expense",
            "amount": amt,
            "category": "差旅费",
            "title": f"发票-{eid}",
            "occurDate": "2026-10-08",
            "source": "invoice_import",
            "billingStatus": "has_invoice",
            "voucherLines": {
                "debit": [{"accountCode": "5602006", "amount": amt}],
                "credit": [{"accountCode": "2241", "amount": amt}],
            },
        })
    await import_statements_json(c_ambig, [{
        "transaction_date": "2026-10-12",
        "description": "报销款",
        "amount": -1000,
        "balance": 12000,
    }], "batch-ambig-sug")
    res_ambig = await ai_suggest_matches(c_ambig)
    sug_ambig = res_ambig[0]
    assert sug_ambig.get("rule") != "reimbursement_multi_match"

    # Case 3: Cross-pool (boss_personal 400 + employee_personal 600 = 1000) -> never mixed
    c_cross = "c-ai-multi-cross"
    await _insert_legacy_entry(c_cross, {
        "id": "x-boss",
        "direction": "expense",
        "amount": 400,
        "category": "差旅费",
        "title": "老板垫付",
        "occurDate": "2026-10-08",
        "payer": "boss_personal",
        "billingStatus": "has_invoice",
    })
    await _insert_legacy_entry(c_cross, {
        "id": "x-emp",
        "direction": "expense",
        "amount": 600,
        "category": "差旅费",
        "title": "员工垫付",
        "occurDate": "2026-10-08",
        "payer": "employee_personal:杨云飞",
        "billingStatus": "has_invoice",
    })
    await import_statements_json(c_cross, [{
        "transaction_date": "2026-10-12",
        "description": "未知报销打款",
        "amount": -1000,
        "balance": 12000,
    }], "batch-cross-sug")
    res_cross = await ai_suggest_matches(c_cross)
    sug_cross = res_cross[0]
    assert sug_cross.get("rule") != "reimbursement_multi_match"
