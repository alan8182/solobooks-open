# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""垫付报销流水 → 冲减本人 2241 其他应付款余额。

回归 2026-11：杨云飞建账前已入账的其他应付款 ¥35,096.72，建账后支付报销
¥104.30 时 AI 建议「员工报销」落 122103 其他应收款，把应付款付成了应收款。
现在只要有该人 2241 贷方余额且足以覆盖付款，就把建议纠正为
「员工垫付报销」→ 借 2241 / 贷 1002，并在卡片上展示可冲减余额。
"""
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))


async def _setup_company(company_id: str) -> None:
    from sqlalchemy import select

    from db.base import Base
    import db.models  # noqa: F401
    from db.models import Company
    from db.session import get_engine, session_scope
    from services.accounting_core_service import ensure_tenant

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")
        company = (
            await session.execute(select(Company).where(Company.company_id == company_id))
        ).scalar_one()
        company.payload = {**(company.payload or {}), "taxId": "91330100MA2TEST001"}


async def _post_personal_payable(company_id: str, person: str, amount: str) -> None:
    """直接落一条已过账的「借 1002 / 贷 2241-某人」期初垫付凭证。"""
    from sqlalchemy import select

    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        async def _account(code: str) -> ChartAccount:
            return (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.code == code,
                    )
                )
            ).scalar_one()

        bank = await _account("1002")
        payable = await _account("2241")
        entry = JournalEntry(
            id=f"opening-payable-{company_id}",
            company_id=company_id,
            voucher_no="VOU-OPEN-0001",
            occurred_on=date(2026, 9, 15),
            period_start=date(2026, 9, 1),
            summary=f"{person}垫付（建账前）",
            status="posted",
            entry_type="opening",
            created_by="owner-1",
            metadata_json={},
        )
        session.add(entry)
        await session.flush()
        session.add(JournalLine(
            journal_entry_id=entry.id,
            company_id=company_id,
            line_no=1,
            account_id=bank.id,
            account_code="1002",
            account_name="银行存款",
            direction="debit",
            amount=Decimal(amount),
            auxiliary={},
        ))
        session.add(JournalLine(
            journal_entry_id=entry.id,
            company_id=company_id,
            line_no=2,
            account_id=payable.id,
            account_code="2241",
            account_name="其他应付款",
            direction="credit",
            amount=Decimal(amount),
            auxiliary={"partner_id": "p-yang", "partner_name": person},
        ))


async def _raise_payable_suggestion(company_id: str, person: str, amount: str, category: str = "员工报销"):
    from services.bank_service import (
        _enrich_advance_payable_suggestions,
        _person_payable_balances,
    )

    balances = await _person_payable_balances(company_id)
    suggestions = [{
        "statementId": "stmt-reimburse-1",
        "statement": {
            "date": "2026-11-20",
            "description": f"移动支付 | 报销 (对方: {person})",
            "amount": Decimal(amount),
            "balance": "18303.83",
        },
        "type": "new",
        "suggestedEntry": {
            "direction": "expense",
            "category": category,
            "accountCode": "122103",
            "accountName": "其他应收款-员工往来",
            "title": "报销顺丰快递费",
        },
        "confidence": 0.9,
        "reason": "摘要含报销",
    }]
    await _enrich_advance_payable_suggestions(company_id, suggestions)
    return balances, suggestions[0]


@pytest.mark.asyncio
async def test_advance_payable_offsets_existing_payable():
    """有本人 2241 余额且金额可覆盖：改为冲减应付，并带出可冲减证据。"""
    company_id = "c-adv-payable-hit"
    await _setup_company(company_id)
    await _post_personal_payable(company_id, "杨云飞", "35096.72")

    balances, sug = await _raise_payable_suggestion(company_id, "杨云飞", "-104.30")

    assert balances["杨云飞"]["available"] == Decimal("35096.72")
    entry = sug["suggestedEntry"]
    assert entry["direction"] == "owner_transfer"
    assert entry["category"] == "员工垫付报销"
    assert entry["accountCode"] == "2241"
    assert sug["advancePayable"] == {
        "counterparty": "杨云飞",
        "accountCode": "2241",
        "accountName": "其他应付款",
        "available": "35096.72",
        "kind": "opening",
        "sources": [{
            "entryId": "opening-payable-c-adv-payable-hit",
            "voucherRef": "VOU-OPEN-0001",
            "date": "2026-09-15",
            "entryType": "opening",
            "amount": Decimal("35096.72"),
            "label": "建账时带入",
        }],
        "original": {
            "direction": "expense",
            "category": "员工报销",
            "accountCode": "122103",
            "accountName": "其他应收款-员工往来",
            "title": "报销顺丰快递费",
        },
    }
    assert "35096.72" in sug["reason"]
    assert sug["rule"] == "advance_payable"
    # 改的是资产→负债科目，必须逐笔确认，不能混进批量或沉淀为自动规则。
    assert sug["requiresReview"] is True

    # 已经是股东垫付分类时保留标签（同一 2241），不要改叫员工垫付
    _balances2, sug2 = await _raise_payable_suggestion(
        company_id, "杨云飞", "-104.30", category="股东垫付报销"
    )
    assert sug2["suggestedEntry"]["category"] == "股东垫付报销"
    assert sug2["suggestedEntry"]["direction"] == "owner_transfer"


@pytest.mark.asyncio
async def test_advance_payable_requires_enough_balance():
    """付款金额超过可冲余额：不自动改分类，仍交给用户确认。"""
    company_id = "c-adv-payable-short"
    await _setup_company(company_id)
    await _post_personal_payable(company_id, "杨云飞", "50.00")

    _balances, sug = await _raise_payable_suggestion(company_id, "杨云飞", "-104.30")

    assert sug["suggestedEntry"]["category"] == "员工报销"
    assert sug["suggestedEntry"].get("direction") == "expense"
    assert "advancePayable" not in sug


@pytest.mark.asyncio
async def test_advance_payable_requires_existing_person_balance():
    """账上没有这个人的应付：不能凭「报销」二字把费用直接冲成负债。"""
    company_id = "c-adv-payable-miss"
    await _setup_company(company_id)
    await _post_personal_payable(company_id, "杨云飞", "1000.00")

    _balances, sug = await _raise_payable_suggestion(company_id, "王源源", "-104.30")

    assert sug["suggestedEntry"]["category"] == "员工报销"
    assert "advancePayable" not in sug


@pytest.mark.asyncio
async def test_advance_payable_consumes_balance_once_per_payload():
    """同一批里同一人两笔：合计不得超过余额，第二笔按剩余额度判断。"""
    from services.bank_service import _enrich_advance_payable_suggestions

    company_id = "c-adv-payable-multi"
    await _setup_company(company_id)
    await _post_personal_payable(company_id, "杨云飞", "100.00")

    def _sug(stmt_id: str, amount: str) -> dict:
        return {
            "statementId": stmt_id,
            "statement": {
                "date": "2026-11-20",
                "description": "报销 (对方: 杨云飞)",
                "amount": Decimal(amount),
            },
            "type": "new",
            "suggestedEntry": {"direction": "expense", "category": "员工报销"},
            "confidence": 0.9,
        }

    suggestions = [_sug("s1", "-60.00"), _sug("s2", "-60.00")]
    await _enrich_advance_payable_suggestions(company_id, suggestions)

    assert suggestions[0]["suggestedEntry"]["category"] == "员工垫付报销"
    # 第一笔冲 60 后只剩 40，第二笔 60 超了，不能继续改分类
    assert suggestions[1]["suggestedEntry"]["category"] == "员工报销"
    assert "advancePayable" not in suggestions[1]


def test_match_payable_person_handles_embedded_name():
    """摘要没有「对方:」前缀时，按姓名包含匹配；同为子串且等长则放弃。"""
    from services.bank_service import _match_payable_person

    balances = {
        "杨云飞": {"name": "杨云飞", "available": Decimal("100.00")},
        "王源源": {"name": "王源源", "available": Decimal("100.00")},
    }
    assert _match_payable_person("快捷支付 杨云飞 报销顺丰快递费", balances) == "杨云飞"
    assert _match_payable_person("向李四付款", balances) == ""
    # 摘要明确写了对方是公司，即使包含个人姓名也不能冲减个人应付
    assert _match_payable_person("货款 (对方: 杭州杨云飞科技有限公司)", balances) == ""


async def _prepare_invoice_and_reimbursement_statement(
    company_id: str,
    *,
    entry_id: str,
    stmt_desc: str = "汇兑 | 附言: 报销微信开放平台认证费用 (对方: 杨云飞)",
    amount: str = "300.00",
    post_entry: bool = False,
) -> str:
    """准备当月进项发票（默认 company_account）与同金额公户付款流水，返回流水 id。"""
    from sqlalchemy import select

    from db.models import SourceTransaction
    from db.session import session_scope
    from services.accounting_core_service import create_entry, post_journal
    from services.bank_service import _ensure_schema as ensure_bank_schema
    from services.bank_service import import_statements_core

    await _setup_company(company_id)
    await ensure_bank_schema()

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id,
                "source": "invoice_import",
                "sourceType": "invoice_import",
                "direction": "expense",
                "amount": amount,
                "title": "发票-深圳市腾讯计算机系统有限公司",
                "merchant": "深圳市腾讯计算机系统有限公司",
                "category": "信息技术服务费",
                "payer": "company_account",
                "occurDate": "2026-06-02",
                "billingStatus": "has_invoice",
                "voucherLines": {
                    "debit": [{"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": amount}],
                    "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": amount}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"req-{entry_id}",
            confirm=False,
        )
        if post_entry:
            await post_journal(
                session, entry_id, company_id=company_id, user_id="owner-1", request_id=f"post-{entry_id}"
            )
        await import_statements_core(
            session,
            company_id=company_id,
            user_id="owner-1",
            rows=[{
                "transaction_date": "2026-06-26",
                "description": stmt_desc,
                "amount": f"-{amount}",
                "balance": "99281.90",
            }],
            statement_id=f"upload-{company_id}",
            request_id=f"req-stmt-{company_id}",
        )
        bank_tx = (
            await session.execute(
                select(SourceTransaction).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.source_type == "bank",
                )
            )
        ).scalar_one()
        return str(bank_tx.external_key)


@pytest.mark.asyncio
async def test_ai_suggest_flags_invoice_reimbursement_candidate_when_counterparty_mismatch_and_reimburse_desc():
    """当月发票草稿遇上付给个人的报销流水：标记 counterpartyMismatch 与两步垫付报销建议。"""
    from services.bank_service import ai_suggest_matches

    company_id = "c-inv-reim-detect"
    entry_id = "inv-tencent-300"
    stmt_id = await _prepare_invoice_and_reimbursement_statement(company_id, entry_id=entry_id)

    suggestions = await ai_suggest_matches(company_id, month="2026-06", user_id="owner-1")
    hit = next(s for s in suggestions if s["statementId"] == stmt_id)

    assert hit["type"] == "match"
    assert hit["matchedEntryId"] == entry_id
    assert hit.get("counterpartyMismatch") is True
    assert hit.get("requiresReview") is True
    assert hit.get("invoiceReimbursement") == {
        "counterparty": "杨云飞",
        "sellerName": "深圳市腾讯计算机系统有限公司",
        "suggestedPayer": "boss_personal",
        "payerLabel": "老板垫付",
        "entryId": entry_id,
    }
    assert "杨云飞" in hit["reason"]
    assert "两步" in hit["reason"]


@pytest.mark.asyncio
async def test_confirm_match_with_reimbursement_payer_creates_two_step_entries():
    """带 reimbursementPayer 确认配对：原发票草稿转个人垫付，并生成 reim-* 报销凭证；取消配对可完整回滚。"""
    from fastapi import Request

    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.accounting_core_service import get_journal, serialize_journal
    from services.bank_service import get_statements

    company_id = "c-inv-reim-confirm"
    entry_id = "inv-tencent-two-step"
    stmt_id = await _prepare_invoice_and_reimbursement_statement(company_id, entry_id=entry_id)
    ctx = AuthContext(user_id="owner-1", company_id=company_id)
    req_scope = {"type": "http", "headers": []}

    async with session_scope() as session:
        res = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt_id,
                type="match",
                matched_entry_id=entry_id,
                matched_entry_ids=[entry_id],
                reimbursementPayer="boss_personal",
            ),
            ctx=ctx,
            request=Request(req_scope),
            session=session,
            idempotency_key=None,
        )

    assert res["status"] == "matched"
    reim_id = res["entryId"]
    assert reim_id == f"reim-{stmt_id}-{entry_id}"

    # 1) 原发票草稿保留费用，贷方保持 2241 个人垫付且状态转为 reconciled
    async with session_scope() as session:
        orig_entry = await get_journal(session, entry_id, company_id=company_id)
        orig_payload = await serialize_journal(session, orig_entry)
        assert orig_payload["payer"] == "boss_personal"
        assert orig_payload["billingStatus"] == "reconciled"
        assert orig_payload["paymentDate"] == "2026-06-26"
        assert any(line["accountCode"] == "2241" for line in orig_payload["voucherLines"]["credit"])
        assert not any(line["accountCode"] == "1002" for line in orig_payload["voucherLines"]["credit"])

        # 2) 生成的报销凭证是 2026-06-26 的 借 2241 / 贷 1002
        reim_entry = await get_journal(session, reim_id, company_id=company_id)
        reim_payload = await serialize_journal(session, reim_entry)
        assert reim_payload["direction"] == "owner_transfer"
        assert reim_payload["category"] == "股东垫付报销"
        assert reim_payload["occurDate"] == "2026-06-26"
        assert any(line["accountCode"] == "2241" for line in reim_payload["voucherLines"]["debit"])
        assert any(line["accountCode"] == "1002" for line in reim_payload["voucherLines"]["credit"])

    # 3) 银行流水指向 reim-* 而不是原发票
    stmts = await get_statements(company_id)
    matched_stmt = next(item for item in stmts if item["id"] == stmt_id)
    assert matched_stmt["matched_entry_id"] == reim_id

    # 4) 取消配对完整回滚：删除 reim-* 草稿，原发票恢复未核销
    async with session_scope() as session:
        unmatched = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt_id,
                type="match",
                matched_entry_id="",
                matched_entry_ids=[],
            ),
            ctx=ctx,
            request=Request(req_scope),
            session=session,
            idempotency_key=None,
        )
    assert unmatched["status"] == "unmatched"
    async with session_scope() as session:
        orig_after = await serialize_journal(session, await get_journal(session, entry_id, company_id=company_id))
        assert orig_after.get("billingStatus") != "reconciled"


@pytest.mark.asyncio
async def test_confirm_match_with_reimbursement_payer_rejects_posted_entry():
    """已过账原发票不允许通过 reimbursementPayer 原地转为个人垫付（严守 A1 不可变）。"""
    from fastapi import HTTPException, Request

    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion

    company_id = "c-inv-reim-posted"
    entry_id = "inv-tencent-posted"
    stmt_id = await _prepare_invoice_and_reimbursement_statement(
        company_id, entry_id=entry_id, post_entry=True
    )
    ctx = AuthContext(user_id="owner-1", company_id=company_id)

    with pytest.raises(HTTPException) as exc:
        async with session_scope() as session:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id=stmt_id,
                    type="match",
                    matched_entry_id=entry_id,
                    matched_entry_ids=[entry_id],
                    reimbursementPayer="boss_personal",
                ),
                ctx=ctx,
                request=Request({"type": "http", "headers": []}),
                session=session,
                idempotency_key=None,
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_ai_suggest_flags_fallback_category_invoice_for_review():
    """公户直付发票仍落兜底科目时标记 fallbackCategory 与 requiresReview；具体科目直付不拦截。"""
    from sqlalchemy import select

    from db.models import SourceTransaction
    from db.session import session_scope
    from services.accounting_core_service import create_entry
    from services.bank_service import _ensure_schema as ensure_bank_schema
    from services.bank_service import ai_suggest_matches, import_statements_core

    company_id = "c-inv-fallback-cat"
    await _setup_company(company_id)
    await ensure_bank_schema()

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": "inv-fallback-200",
                "source": "invoice_import",
                "sourceType": "invoice_import",
                "direction": "expense",
                "amount": "200.00",
                "title": "发票-北京测试软件有限公司",
                "merchant": "北京测试软件有限公司",
                "category": "其他支出",
                "categorySource": "fallback",
                "payer": "company_account",
                "occurDate": "2026-06-05",
                "billingStatus": "has_invoice",
                "voucherLines": {
                    "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "200.00"}],
                    "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": "200.00"}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id="req-inv-fallback-200",
            confirm=False,
        )
        await create_entry(
            session,
            {
                "id": "inv-rule-350",
                "source": "invoice_import",
                "sourceType": "invoice_import",
                "direction": "expense",
                "amount": "350.00",
                "title": "发票-杭州深度求索人工智能基础技术研究有限公司",
                "merchant": "杭州深度求索人工智能基础技术研究有限公司",
                "category": "信息技术服务费",
                "categorySource": "rule",
                "categoryReason": "品名关键词「技术服务」命中信息技术服务费规则",
                "item_name": "*信息技术服务*软件测试服务",
                "payer": "company_account",
                "occurDate": "2026-06-06",
                "billingStatus": "has_invoice",
                "voucherLines": {
                    "debit": [{"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": "350.00"}],
                    "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": "350.00"}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id="req-inv-rule-350",
            confirm=False,
        )
        await import_statements_core(
            session,
            company_id=company_id,
            user_id="owner-1",
            rows=[
                {
                    "transaction_date": "2026-06-05",
                    "description": "汇兑 | 附言: 服务费 (对方: 北京测试软件有限公司)",
                    "amount": "-200.00",
                    "balance": "99000.00",
                },
                {
                    "transaction_date": "2026-06-06",
                    "description": "汇兑 | 附言: deepseek调用服务费 (对方: 杭州深度求索人工智能基础技术研究有限公司)",
                    "amount": "-350.00",
                    "balance": "98650.00",
                },
            ],
            statement_id=f"upload-{company_id}",
            request_id=f"req-stmt-{company_id}",
        )
        bank_rows = (
            await session.execute(
                select(SourceTransaction).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.source_type == "bank",
                )
            )
        ).scalars().all()
        stmt_by_amt = {abs(Decimal(str(row.amount))): str(row.external_key) for row in bank_rows}

    suggestions = await ai_suggest_matches(company_id, month="2026-06", user_id="owner-1")
    fallback_hit = next(s for s in suggestions if s["statementId"] == stmt_by_amt[Decimal("200.00")])
    concrete_hit = next(s for s in suggestions if s["statementId"] == stmt_by_amt[Decimal("350.00")])

    assert fallback_hit["type"] == "match"
    assert fallback_hit["matchedEntryId"] == "inv-fallback-200"
    assert fallback_hit.get("fallbackCategory") is True
    assert fallback_hit.get("requiresReview") is True

    assert concrete_hit["type"] == "match"
    assert concrete_hit["matchedEntryId"] == "inv-rule-350"
    assert concrete_hit.get("fallbackCategory") is not True
    assert concrete_hit.get("requiresReview") is not True
    assert concrete_hit["matchedEntry"].get("item_name") == "*信息技术服务*软件测试服务"
    assert concrete_hit["matchedEntry"].get("categoryReason") == "品名关键词「技术服务」命中信息技术服务费规则"
