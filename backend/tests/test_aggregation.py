# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""通用归集（管理口径）测试。

锁死的契约（对应设计文档 §9 验收断言）：
- **只收金额**：complete=true 时合计必须精确等于行金额，尾差不放过；
- **渐进归集**：complete=false 允许停在「部分归集」，三态可逆；
- **期间口径**：默认按记账期间 period_start 聚合，补记凭证（业务日期在上月）
  必须出现在自己的记账期间，切 occurDate 才移到上月；
- **方向自带符号**：amount 有 CHECK(amount > 0)，聚合自行带符号，勾稽恒成立；
- **归档不毁数据**：成员归档只禁新增分配，历史报表金额原样保留；
- **规则不覆盖人工**：命中已有手工分配的行整行跳过。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from core.errors import AccountingError
from db.models import AccountingDimension, AccountingDimensionMember, JournalEntry, JournalLine, JournalLineAllocation
from services.accounting_core_service import ensure_tenant
from services.aggregation_service import (
    aggregation_report,
    apply_rules,
    clear_line_allocations,
    create_dimension,
    create_member,
    list_pending_lines,
    replace_line_allocations,
    replace_rules,
    update_member,
)


COMPANY = "agg-co-1"
OTHER = "agg-co-2"
PERIOD = date(2026, 10, 1)


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _reset() -> None:
    await _ensure_tables()
    from db.session import session_scope

    async with session_scope() as session:
        for model in (JournalLineAllocation, JournalLine, JournalEntry):
            await session.execute(delete(model).where(model.company_id.in_([COMPANY, OTHER])))
        await session.execute(
            delete(AccountingDimensionMember).where(AccountingDimensionMember.company_id.in_([COMPANY, OTHER]))
        )
        await session.execute(delete(AccountingDimension).where(AccountingDimension.company_id.in_([COMPANY, OTHER])))
        await ensure_tenant(session, COMPANY, "owner-1")
        await ensure_tenant(session, OTHER, "owner-1")


async def _make_voucher(
    entry_id: str,
    *,
    amount: str = "100.00",
    occurred_on: date = date(2026, 10, 15),
    period_start: date = PERIOD,
    status: str = "posted",
    entry_type: str = "standard",
    summary: str = "10月办公费",
    company_id: str = COMPANY,
    account_code: str = "5602001",
    account_name: str = "管理人员职工薪酬",
) -> str:
    """造一张借贷各一行的凭证，返回借方行 id（归集对象）。"""
    from db.models import ChartAccount
    from db.session import session_scope

    async with session_scope() as session:
        accounts = {}
        for code in (account_code, "1002"):
            row = (await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id, ChartAccount.code == code,
                )
            )).scalar_one_or_none()
            assert row is not None, f"科目 {code} 未在科目表中，测试无法造凭证"
            accounts[code] = row.id

        entry = JournalEntry(
            id=entry_id,
            company_id=company_id,
            occurred_on=occurred_on,
            period_start=period_start,
            summary=summary,
            status=status,
            entry_type=entry_type,
            currency="CNY",
            created_by="owner-1",
        )
        session.add(entry)
        debit = JournalLine(
            id=f"{entry_id}-d",
            journal_entry_id=entry_id,
            company_id=company_id,
            line_no=1,
            account_id=accounts[account_code],
            account_code=account_code,
            account_name=account_name,
            direction="debit",
            amount=Decimal(amount),
        )
        credit = JournalLine(
            id=f"{entry_id}-c",
            journal_entry_id=entry_id,
            company_id=company_id,
            line_no=2,
            account_id=accounts["1002"],
            account_code="1002",
            account_name="银行存款",
            direction="credit",
            amount=Decimal(amount),
        )
        session.add_all([debit, credit])
        await session.flush()
    return debit.id


async def _dimension(company_id: str = COMPANY, code: str = "project") -> str:
    from db.session import session_scope

    async with session_scope() as session:
        row = await create_dimension(
            session, company_id, "owner-1", {"code": code, "name": "项目", "kind": "project"},
            request_id="req-dim",
        )
    return row["id"]


async def _member(dimension_id: str, code: str = "P1", company_id: str = COMPANY, **kwargs) -> str:
    from db.session import session_scope

    payload = {"code": code, "name": code, "startDate": "2026-01-01", "status": "active", **kwargs}
    async with session_scope() as session:
        row = await create_member(session, company_id, "owner-1", dimension_id, payload, request_id="req-mem")
    return row["id"]


async def _allocate(line_id: str, dimension_id: str, allocations: list[dict], *, complete: bool = False) -> dict:
    from db.session import session_scope

    async with session_scope() as session:
        return await replace_line_allocations(
            session, COMPANY, "owner-1", line_id,
            {"dimensionId": dimension_id, "allocations": allocations, "complete": complete},
            request_id="req-alloc",
        )


# ── 分配校验 ────────────────────────────────────────────────────────────────


async def test_complete_requires_exact_total():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")

    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": member, "amount": "99.99"}], complete=True)
    assert exc.value.code == "ALLOCATION_TOTAL_MISMATCH"
    assert exc.value.context["expected"] == "100.00"


async def test_multi_member_requires_basis():
    await _reset()
    dim = await _dimension()
    a = await _member(dim, "P1")
    b = await _member(dim, "P2")
    line = await _make_voucher("e1")

    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": a, "amount": "50.00"}, {"memberId": b, "amount": "50.00"}])
    assert exc.value.code == "ALLOCATION_BASIS_REQUIRED"

    ok = await _allocate(line, dim, [
        {"memberId": a, "amount": "50.00", "allocationBasis": "按门店面积"},
        {"memberId": b, "amount": "50.00", "allocationBasis": "按门店面积"},
    ], complete=True)
    assert ok["state"] == "allocated"
    assert ok["allocatedAmount"] == "100.00"


async def test_amount_above_line_rejected():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")
    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": member, "amount": "100.01"}])
    assert exc.value.code == "INVALID_ALLOCATION"


async def test_reversal_entry_cannot_allocate():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1", entry_type="reversal")
    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": member, "amount": "100.00"}], complete=True)
    assert exc.value.code == "ALLOCATION_ON_REVERSED"


async def test_archived_member_rejected():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")

    from db.session import session_scope
    async with session_scope() as session:
        await update_member(session, COMPANY, "owner-1", member, {"status": "archived"}, request_id="req-arch")

    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": member, "amount": "100.00"}])
    assert exc.value.code == "INVALID_ALLOCATION_MEMBER"


async def test_cross_company_member_rejected():
    await _reset()
    dim = await _dimension()
    other_dim = await _dimension(OTHER, "dept")
    other_member = await _member(other_dim, "X1", company_id=OTHER)
    line = await _make_voucher("e1")
    with pytest.raises(AccountingError) as exc:
        await _allocate(line, dim, [{"memberId": other_member, "amount": "100.00"}])
    assert exc.value.code == "INVALID_ALLOCATION_MEMBER"


# ── 渐进归集与三态 ──────────────────────────────────────────────────────────


async def test_partial_allocation_then_clear():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")

    partial = await _allocate(line, dim, [{"memberId": member, "amount": "60.00"}])
    assert partial["state"] == "partial"
    assert partial["remainingAmount"] == "40.00"

    from db.session import session_scope
    async with session_scope() as session:
        pending = await list_pending_lines(session, COMPANY, "owner-1", dimension_id=dim)
    states = {item["lineId"]: item["state"] for item in pending["items"]}
    assert states[line] == "partial"

    async with session_scope() as session:
        cleared = await clear_line_allocations(session, COMPANY, "owner-1", line, dim, request_id="req-clear")
    assert cleared["state"] == "unallocated"
    assert cleared["allocatedAmount"] == "0.00"

    async with session_scope() as session:
        pending = await list_pending_lines(session, COMPANY, "owner-1", dimension_id=dim)
    assert {item["lineId"]: item["state"] for item in pending["items"]}[line] == "unallocated"


async def test_report_three_states_balance():
    await _reset()
    dim = await _dimension()
    p1 = await _member(dim, "P1")
    p2 = await _member(dim, "P2")
    full = await _make_voucher("e-full", amount="300.00")
    part = await _make_voucher("e-part", amount="200.00")
    none = await _make_voucher("e-none", amount="500.00")

    await _allocate(full, dim, [{"memberId": p1, "amount": "300.00"}], complete=True)
    await _allocate(part, dim, [{"memberId": p2, "amount": "120.00"}])

    from db.session import session_scope
    async with session_scope() as session:
        report = await aggregation_report(
            session, COMPANY, "owner-1", dim, start=PERIOD, end=PERIOD, prefixes=("5602",),
        )
    totals = report["totals"]
    assert totals["lineAmount"] == "1000.00"
    assert totals["allocatedAmount"] == "420.00"
    assert totals["unassignedAmount"] == "580.00"
    assert totals["balanced"] is True
    assert totals["allocatedLineCount"] == 1
    assert totals["partialLineCount"] == 1
    assert totals["unallocatedLineCount"] == 1
    amounts = {item["code"]: item["allocatedAmount"] for item in report["items"]}
    assert amounts["P1"] == "300.00"
    assert amounts["P2"] == "120.00"


async def test_archived_member_keeps_history():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")
    await _allocate(line, dim, [{"memberId": member, "amount": "100.00"}], complete=True)

    from db.session import session_scope
    async with session_scope() as session:
        await update_member(session, COMPANY, "owner-1", member, {"status": "archived"}, request_id="req-arch")
        report = await aggregation_report(
            session, COMPANY, "owner-1", dim, start=PERIOD, end=PERIOD, prefixes=("5602",),
        )
    assert report["items"][0]["allocatedAmount"] == "100.00"
    assert report["items"][0]["status"] == "archived"


# ── 期间口径 ────────────────────────────────────────────────────────────────


async def test_backfilled_entry_follows_posting_period():
    """补记凭证：业务日期 9 月，记账期间 10 月。默认口径必须落在 10 月。"""
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e-backfill", occurred_on=date(2026, 9, 20), period_start=date(2026, 10, 1))
    await _allocate(line, dim, [{"memberId": member, "amount": "100.00"}], complete=True)

    from db.session import session_scope
    async with session_scope() as session:
        oct_report = await aggregation_report(
            session, COMPANY, "owner-1", dim, start=date(2026, 10, 1), end=date(2026, 10, 1), prefixes=("5602",),
        )
        sep_report = await aggregation_report(
            session, COMPANY, "owner-1", dim, start=date(2026, 9, 1), end=date(2026, 9, 30),
            prefixes=("5602",), basis="occurDate",
        )
    assert oct_report["totals"]["allocatedAmount"] == "100.00"
    assert sep_report["totals"]["allocatedAmount"] == "100.00"
    assert oct_report["basis"] == "period"
    assert sep_report["basis"] == "occurDate"


def test_allocation_declares_database_cascade():
    """凭证行删除后分配记录必须由数据库级联清掉，不留孤儿。

    级联是 DB 行为（生产 PG 生效）；测试环境是 SQLite 且未开 foreign_keys
    pragma，级联不会真的发生，所以这里锁的是**契约本身**：FK 上必须挂
    ondelete=CASCADE。改掉它 = 生产上会留下指向空凭证行的分配记录。
    """
    from db.models import AggregationRule, JournalLineAllocation as Alloc

    fk = [f for f in Alloc.__table__.foreign_keys if f.target_fullname == "journal_lines.id"]
    assert len(fk) == 1
    assert fk[0].ondelete == "CASCADE"

    rule_fk = [f for f in AggregationRule.__table__.foreign_keys if f.target_fullname == "accounting_dimensions.id"]
    assert any(f.ondelete == "CASCADE" for f in rule_fk)


async def test_allocation_rows_survive_member_lookup_by_record_only():
    """报表取数只按分配记录，不按成员当前状态过滤——归档不等于毁数据。"""
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")
    await _allocate(line, dim, [{"memberId": member, "amount": "100.00"}], complete=True)

    from db.session import session_scope
    async with session_scope() as session:
        member_row = await session.get(AccountingDimensionMember, member)
        member_row.status = "archived"
        await session.flush()
        rows = (await session.execute(
            select(JournalLineAllocation).where(JournalLineAllocation.company_id == COMPANY)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].journal_line_id == line


# ── 规则引擎 ────────────────────────────────────────────────────────────────


async def _rule(dim: str, member: str, **kwargs) -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await replace_rules(session, COMPANY, "owner-1", dim, {"rules": [{"memberId": member, **kwargs}]}, request_id="req-rule")


async def test_apply_rules_allocates_by_prefix():
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    await _make_voucher("e1")
    await _rule(dim, member, accountPrefix="5602", direction="debit")

    from db.session import session_scope
    async with session_scope() as session:
        result = await apply_rules(
            session, COMPANY, "owner-1",
            {"dimensionId": dim, "start": "2026-10-01", "end": "2026-10-31", "scopePrefixes": ["5602"]},
            request_id="req-apply",
        )
    assert result["allocated"] == 1
    assert result["skipped"] == 0
    assert result["unmatched"] == 0


async def test_apply_rules_never_overwrites_manual():
    await _reset()
    dim = await _dimension()
    manual_member = await _member(dim, "P1")
    rule_member = await _member(dim, "P2")
    line = await _make_voucher("e1")
    await _allocate(line, dim, [{"memberId": manual_member, "amount": "100.00"}], complete=True)
    await _rule(dim, rule_member, accountPrefix="5602")

    from db.session import session_scope
    async with session_scope() as session:
        result = await apply_rules(
            session, COMPANY, "owner-1",
            {"dimensionId": dim, "start": "2026-10-01", "end": "2026-10-31", "scopePrefixes": ["5602"]},
            request_id="req-apply",
        )
    assert result["allocated"] == 0
    assert result["skipped"] == 1

    async with session_scope() as session:
        rows = (await session.execute(
            select(JournalLineAllocation).where(JournalLineAllocation.company_id == COMPANY)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].member_id == manual_member
    assert rows[0].source == "manual"


async def test_apply_rules_partial_rate_leaves_partial_state():
    """规则 rate=0.5 只分一半，尾差不自动补齐，该行停在「部分归集」。"""
    await _reset()
    dim = await _dimension()
    member = await _member(dim)
    line = await _make_voucher("e1")
    await _rule(dim, member, accountPrefix="5602", rate="0.5")

    from db.session import session_scope
    async with session_scope() as session:
        await apply_rules(
            session, COMPANY, "owner-1",
            {"dimensionId": dim, "start": "2026-10-01", "end": "2026-10-31", "scopePrefixes": ["5602"]},
            request_id="req-apply",
        )
        pending = await list_pending_lines(session, COMPANY, "owner-1", dimension_id=dim)
    item = next(i for i in pending["items"] if i["lineId"] == line)
    assert item["state"] == "partial"
    assert item["allocatedAmount"] == "50.00"
    assert item["remainingAmount"] == "50.00"


# ── 零回归 ──────────────────────────────────────────────────────────────────


async def test_company_without_dimension_still_lists_pending():
    """未配置维度的公司行为与改造前一致：清单照常可列，不报错。"""
    await _reset()
    line = await _make_voucher("e1")
    from db.session import session_scope

    async with session_scope() as session:
        pending = await list_pending_lines(session, COMPANY, "owner-1", prefixes=("5602",))
    assert pending["total"] == 1
    assert pending["items"][0]["lineId"] == line
    assert pending["items"][0]["state"] == "unallocated"
