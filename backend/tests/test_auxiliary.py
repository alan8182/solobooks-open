# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""辅助核算（P0 WP2）：主数据 CRUD、auxiliary 服务端收口、核算项目余额表、账龄分组。

锁定四条口径：
1. name 由服务端按 id 回填，客户端传的 name 被丢弃；
2. 假 id / 停用档案在落库时被拒（不信客户端行数据）；
3. 被凭证引用的档案只能停用不能删除；
4. 按往来单位的余额表与账龄分组金额，与手工汇总一致。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from core.errors import AccountingError
from services.accounting_core_service import create_entry, ensure_tenant
from services.aging_service import query_ar_aging
from services.auxiliary_service import (
    create_master,
    deactivate_master,
    delete_master,
    list_master,
    update_master,
)
from services.ledger_service import query_partner_balances


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str) -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


async def _post_receivable(company_id: str, entry_id: str, date: str, amount: str, partner: dict | None):
    from db.session import session_scope

    debit_line = {"accountCode": "1122", "accountName": "应收账款", "amount": amount}
    if partner:
        debit_line["auxiliary"] = {
            "partner_id": partner["id"],
            # 故意传一个假名字：服务端必须按 id 回填真实名称
            "partner_name": "客户端伪造的名称",
        }
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id,
                "direction": "income",
                "amount": amount,
                "title": "客户应收",
                "category": "主营业务收入",
                "occurDate": date,
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [debit_line],
                    "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": amount}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{entry_id}-req",
            confirm=True,
        )


@pytest.mark.asyncio
async def test_partner_crud():
    company_id = "c-aux-crud"
    await _setup_company(company_id)

    partner = await create_master(
        company_id, "partner", {"name": "杭州甲客户", "type": "customer"}, user_id="owner-1"
    )
    assert partner["code"].startswith("C")
    assert partner["type"] == "customer"

    with pytest.raises(AccountingError) as duplicate:
        await create_master(company_id, "partner", {"name": "杭州甲客户"}, user_id="owner-1")
    assert duplicate.value.code == "DUPLICATE_AUXILIARY"

    renamed = await update_master(company_id, "partner", partner["id"], {"name": "甲客户（更名）"})
    assert renamed["name"] == "甲客户（更名）"

    await deactivate_master(company_id, "partner", partner["id"])
    active = await list_master(company_id, "partner")
    assert active["total"] == 0
    everything = await list_master(company_id, "partner", include_inactive=True)
    assert everything["total"] == 1

    await delete_master(company_id, "partner", partner["id"])
    assert (await list_master(company_id, "partner", include_inactive=True))["total"] == 0


@pytest.mark.asyncio
async def test_auxiliary_name_backfilled_and_partner_dropped_on_non_partner_account():
    company_id = "c-aux-backfill"
    await _setup_company(company_id)
    partner = await create_master(company_id, "partner", {"name": "乙客户"}, user_id="owner-1")
    await _post_receivable(company_id, "aux-entry-1", "2026-10-10", "1000.00", partner)

    from db.models import JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == "aux-entry-1")
            )
        ).scalars().all()
    by_code = {line.account_code: line for line in lines}
    # 服务端按 id 回填真实名称，客户端传的"伪造名称"被丢弃
    assert by_code["1122"].auxiliary["partner_name"] == "乙客户"
    assert by_code["1122"].auxiliary["partner_id"] == partner["id"]
    # 收入科目不允许挂往来单位，维度被白名单裁掉
    assert "partner_id" not in (by_code["5001"].auxiliary or {})


@pytest.mark.asyncio
async def test_invalid_and_inactive_partner_rejected():
    company_id = "c-aux-invalid"
    await _setup_company(company_id)

    with pytest.raises(AccountingError) as fake:
        await _post_receivable(
            company_id, "aux-entry-fake", "2026-10-11", "100.00", {"id": "pt-does-not-exist"}
        )
    assert fake.value.code == "INVALID_AUXILIARY"

    partner = await create_master(company_id, "partner", {"name": "停用客户"}, user_id="owner-1")
    await deactivate_master(company_id, "partner", partner["id"])
    with pytest.raises(AccountingError) as inactive:
        await _post_receivable(company_id, "aux-entry-inactive", "2026-10-12", "100.00", partner)
    assert inactive.value.code == "INVALID_AUXILIARY"


@pytest.mark.asyncio
async def test_delete_blocked_when_referenced():
    company_id = "c-aux-inuse"
    await _setup_company(company_id)
    partner = await create_master(company_id, "partner", {"name": "被引用客户"}, user_id="owner-1")
    await _post_receivable(company_id, "aux-entry-used", "2026-10-13", "500.00", partner)

    with pytest.raises(AccountingError) as in_use:
        await delete_master(company_id, "partner", partner["id"], user_id="owner-1")
    assert in_use.value.code == "AUXILIARY_IN_USE"
    assert in_use.value.context["references"] == 1


@pytest.mark.asyncio
async def test_partner_balances_and_aging_grouping():
    company_id = "c-aux-report"
    await _setup_company(company_id)
    first = await create_master(company_id, "partner", {"name": "甲客户"}, user_id="owner-1")
    second = await create_master(company_id, "partner", {"name": "乙客户"}, user_id="owner-1")

    await _post_receivable(company_id, "aux-r-1", "2026-09-20", "800.00", first)
    await _post_receivable(company_id, "aux-r-2", "2026-10-05", "1200.00", first)
    await _post_receivable(company_id, "aux-r-3", "2026-10-06", "300.00", second)
    await _post_receivable(company_id, "aux-r-4", "2026-10-07", "50.00", None)

    balances = await query_partner_balances(company_id, "1122", "2026-10")
    by_name = {item["partnerName"]: item for item in balances["items"]}
    assert float(by_name["甲客户"]["opening"]) == pytest.approx(800.0)
    assert float(by_name["甲客户"]["debit"]) == pytest.approx(1200.0)
    assert float(by_name["甲客户"]["closing"]) == pytest.approx(2000.0)
    assert float(by_name["乙客户"]["closing"]) == pytest.approx(300.0)
    # 未挂往来单位的行归入 unassigned，不丢数据
    assert balances["unassigned"] is not None
    assert float(balances["unassigned"]["closing"]) == pytest.approx(50.0)
    assert float(balances["totalDebit"]) == pytest.approx(1550.0)

    aging = await query_ar_aging(company_id, "2026-10-31", group_by="partner")
    assert aging["group_by"] == "partner"
    groups = {group["partnerName"]: group for group in aging["groups"]}
    assert float(groups["甲客户"]["total"]) == pytest.approx(2000.0)
    assert float(groups["乙客户"]["total"]) == pytest.approx(300.0)
    assert aging["unassigned"] is not None
    assert float(aging["unassigned"]["total"]) == pytest.approx(50.0)
    assert float(aging["total"]) == pytest.approx(2350.0)


# ── 银行对手方 → 往来单位（3001 实收资本纳入辅助核算）──────────────────────


async def _post_transfer_entry(
    company_id: str,
    entry_id: str,
    date: str,
    amount: str,
    counterparty: str,
    credit_code: str,
    credit_name: str,
    *,
    credit_auxiliary: dict | None = None,
):
    from db.session import session_scope

    credit_line = {"accountCode": credit_code, "accountName": credit_name, "amount": amount}
    if credit_auxiliary:
        credit_line["auxiliary"] = credit_auxiliary
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id,
                "direction": "owner_transfer",
                "amount": amount,
                "title": f"银行流水-{counterparty}",
                "category": "股东投入",
                "occurDate": date,
                "sourceType": "bank_import",
                "counterparty": counterparty,
                "voucherLines": {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                    "credit": [credit_line],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{entry_id}-req",
            confirm=True,
        )


async def _entry_lines(entry_id: str):
    from db.models import JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == entry_id)
            )
        ).scalars().all()
    return {line.account_code: line for line in lines}


async def _partner_by_name(company_id: str, name: str):
    from db.models import Partner
    from db.session import session_scope

    async with session_scope() as session:
        return (
            await session.execute(
                select(Partner).where(Partner.company_id == company_id, Partner.name == name)
            )
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_bank_counterparty_builds_person_partner_on_paid_in_capital():
    """3001 实收资本要按股东挂辅助，否则只看到一个总数、看不出谁实缴了多少。"""
    company_id = "c-aux-counterparty-3001"
    await _setup_company(company_id)

    await _post_transfer_entry(
        company_id, "aux-owner-1", "2026-05-20", "20000.00", "何静谷", "3001", "实收资本"
    )

    lines = await _entry_lines("aux-owner-1")
    assert lines["3001"].auxiliary["partner_name"] == "何静谷"
    partner = await _partner_by_name(company_id, "何静谷")
    assert partner is not None and partner.type == "person"
    assert lines["3001"].auxiliary["partner_id"] == partner.id
    # 银行存款不是往来科目，不能被挂上
    assert "partner_id" not in (lines["1002"].auxiliary or {})


@pytest.mark.asyncio
async def test_bank_counterparty_builds_company_partner_on_other_payable():
    company_id = "c-aux-counterparty-2241"
    await _setup_company(company_id)

    await _post_transfer_entry(
        company_id, "aux-company-1", "2026-05-21", "5000.00",
        "杭州深度求索人工智能基础技术研究有限公司", "2241", "其他应付款",
    )

    lines = await _entry_lines("aux-company-1")
    assert lines["2241"].auxiliary["partner_name"] == "杭州深度求索人工智能基础技术研究有限公司"
    partner = await _partner_by_name(company_id, "杭州深度求索人工智能基础技术研究有限公司")
    assert partner is not None and partner.type == "both"


@pytest.mark.asyncio
async def test_counterparty_does_not_override_explicit_partner_or_build_placeholders():
    company_id = "c-aux-counterparty-respect"
    await _setup_company(company_id)
    explicit = await create_master(company_id, "partner", {"name": "票面供应商"}, user_id="owner-1")

    await _post_transfer_entry(
        company_id, "aux-respect-1", "2026-05-22", "800.00", "杨云飞", "2241", "其他应付款",
        credit_auxiliary={"partner_id": explicit["id"], "partner_name": "客户端伪造名称"},
    )
    lines = await _entry_lines("aux-respect-1")
    assert lines["2241"].auxiliary["partner_id"] == explicit["id"]
    assert lines["2241"].auxiliary["partner_name"] == "票面供应商"
    assert await _partner_by_name(company_id, "杨云飞") is None

    await _post_transfer_entry(
        company_id, "aux-respect-2", "2026-05-23", "90.00", "应付利息", "2241", "其他应付款"
    )
    lines = await _entry_lines("aux-respect-2")
    assert not (lines["2241"].auxiliary or {}).get("partner_id")
    assert await _partner_by_name(company_id, "应付利息") is None


def test_counterparty_classification_and_statement_extraction():
    from services.auxiliary_service import counterparty_partner_type
    from services.bank_service import extract_statement_counterparty

    assert counterparty_partner_type("何静谷") == "person"
    assert counterparty_partner_type("常雪雪") == "person"
    assert counterparty_partner_type("深圳市腾讯计算机系统有限公司") == "both"
    assert counterparty_partner_type("国家金库西安市高新支库") == "both"
    # 泛称/科目名不是交易对手，建档案只会污染账龄表
    assert counterparty_partner_type("应付利息") is None
    assert counterparty_partner_type("个人客户") is None

    assert extract_statement_counterparty("汇兑 | 附言: 何静谷实缴出资款 (对方: 何静谷)") == "何静谷"
    assert extract_statement_counterparty("汇兑 | 附言: 稿费（对方：常雪雪）") == "常雪雪"
    assert extract_statement_counterparty(
        "对方户名：杭州白鹭文化传媒有限公司 | 转入-项目尾款"
    ) == "杭州白鹭文化传媒有限公司"
    # 招行格式：户名在业务摘要末尾括号里
    assert extract_statement_counterparty(
        "招行对公 | 转入-项目尾款(杭州白鹭文化传媒有限公司)"
    ) == "杭州白鹭文化传媒有限公司"
    # 括号里是说明而不是对手方时不取，避免建出「个人客户」「5元」这种假档案
    assert extract_statement_counterparty("招行对公 | 转入-技术服务预付款(个人客户)") == ""
    assert extract_statement_counterparty("收费 | 跨行转账(5元)") == ""
