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
