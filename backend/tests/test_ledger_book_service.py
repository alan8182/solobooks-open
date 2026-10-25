# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿整册取数测试。

三条硬口径：
1. 整册与旧接口（余额表/明细账）逐字段一致——新接口不许顺手改口径；
2. 账页内部必须自洽：期初 + 本期借 - 本期贷 == 期末，且期末与余额表同数；
3. 容器科目（1221/2211/2221）只出子级合计行，不重复计入合计。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_book_service import InvalidPeriod, build_ledger_book
from services.ledger_service import get_balance_summary


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


async def _post_container_entry(company_id: str) -> None:
    """工资计提形态凭证：借 5602001/5602016，贷 221101/221102/122101/2221012。"""
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-payroll",
                "direction": "expense",
                "amount": "26600.00",
                "title": "10月计提工资及社保",
                "category": "人力成本",
                "occurDate": "2026-10-31",
                "sourceType": "salary_accrual",
                "voucherLines": {
                    "debit": [
                        {"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": "20000.00"},
                        {"accountCode": "5602016", "accountName": "管理费用-单位社保", "amount": "6600.00"},
                    ],
                    "credit": [
                        {"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": "14800.00"},
                        {"accountCode": "221102", "accountName": "应付职工薪酬-单位社保", "amount": "6600.00"},
                        {"accountCode": "122101", "accountName": "其他应收款-代扣代缴社保", "amount": "3700.00"},
                        {"accountCode": "2221012", "accountName": "应交税费-应交个人所得税", "amount": "1500.00"},
                    ],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{company_id}-req",
            confirm=True,
        )


async def _insert_backfill_entry(company_id: str) -> None:
    """补记形态：业务日期 2026-09-20、记账期间 2026-10。"""
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1001", "5001")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no="TEST-BF-001",
            occurred_on=date(2026, 9, 20),
            period_start=date(2026, 10, 1),
            summary="补记9月收入（10月记账）",
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=1,
                    account_id=by_code["1001"].id,
                    account_code="1001",
                    account_name="库存现金",
                    direction="debit",
                    amount=Decimal("500.00"),
                ),
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=2,
                    account_id=by_code["5001"].id,
                    account_code="5001",
                    account_name="主营业务收入",
                    direction="credit",
                    amount=Decimal("500.00"),
                ),
            ]
        )


def _by_code(items: list[dict]) -> dict[str, dict]:
    return {item["accountCode"]: item for item in items}


def _warning_codes(book: dict) -> set[str]:
    return {warning["code"] for warning in book["warnings"]}


@pytest.mark.asyncio
async def test_book_balance_matches_legacy_summary():
    """整册余额表与旧 /balance-summary 逐科目逐字段一致（口径不许漂移）。"""
    company_id = "c-book-parity"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10", include_zero_rows=True)
    legacy = await get_balance_summary(company_id, "2026-10")

    assert book["balance"]["totalDebit"] == legacy["totalDebit"]
    assert book["balance"]["totalCredit"] == legacy["totalCredit"]
    assert book["balance"]["isBalanced"] == legacy["isBalanced"] is True

    new_rows = _by_code(book["balance"]["items"])
    old_rows = _by_code(legacy["items"])
    assert set(new_rows) == set(old_rows), "行集合必须完全一致（含 0 行科目）"
    for code, old in old_rows.items():
        new = new_rows[code]
        for field in ("openingBalance", "debitTotal", "creditTotal", "closingBalance"):
            assert new[field] == old[field], f"{code}.{field} 与旧接口不一致"
        assert new["isParent"] == old["isParent"]


@pytest.mark.asyncio
async def test_book_details_roll_forward_matches_balance():
    """账页自洽：期初 + 借 - 贷 == 期末，且期末与余额表同数。"""
    company_id = "c-book-roll"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    balance = _by_code(book["balance"]["items"])

    assert book["details"], "有发生额必须出明细账页"
    for page in book["details"]:
        opening = Decimal(page["opening"])
        debit = Decimal(page["periodTotals"]["debit"])
        credit = Decimal(page["periodTotals"]["credit"])
        assert opening + debit - credit == Decimal(page["closing"])
        assert page["closing"] == balance[page["code"]]["closingBalance"]
        assert Decimal(page["lines"][-1]["balance"]) == Decimal(page["closing"]), "末行余额应等于账页期末"
        assert page["lineCount"] == len(page["lines"])


@pytest.mark.asyncio
async def test_book_container_rows_not_double_counted():
    """2211 出子级合计行但不计入合计；details 不出 2211 自身页。"""
    company_id = "c-book-container"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    rows = _by_code(book["balance"]["items"])

    parent = rows["2211"]
    assert parent["isParent"] is True
    assert Decimal(parent["creditTotal"]) == Decimal("21400.00")
    assert Decimal(parent["closingBalance"]) == Decimal("-21400.00")

    # 合计只统计末级行：26600，不是 26600 + 21400
    assert book["balance"]["totalDebit"] == "26600.00"
    assert book["balance"]["totalCredit"] == "26600.00"

    detail_codes = {page["code"] for page in book["details"]}
    assert "2211" not in detail_codes
    assert detail_codes == {"5602001", "5602016", "221101", "221102", "122101", "2221012"}


@pytest.mark.asyncio
async def test_book_direction_and_abs_for_credit_balance():
    """贷余科目：方向为 credit、金额为正数（页面据此显示「贷」，不出负号）。"""
    company_id = "c-book-direction"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    page = next(item for item in book["details"] if item["code"] == "221101")

    assert page["closingDirection"] == "credit"
    assert page["closingAbs"] == "14800.00"
    assert page["closing"] == "-14800.00"
    assert page["lines"][-1]["balanceDirection"] == "credit"

    debit_page = next(item for item in book["details"] if item["code"] == "5602001")
    assert debit_page["closingDirection"] == "debit"
    assert debit_page["closingAbs"] == "20000.00"


@pytest.mark.asyncio
async def test_book_backfill_counts_in_posting_period():
    """补记凭证计入记账期间（10月），9月册既无行也不含余额。"""
    company_id = "c-book-backfill"
    await _setup_company(company_id)
    await _insert_backfill_entry(company_id)

    sept = await build_ledger_book(company_id, month="2026-09")
    assert sept["details"] == []
    # 默认口径下 9 月既无发生额也无余额，1001 不该出现（科目表仍在，可按开关展开）
    assert "1001" not in _by_code(sept["balance"]["items"])
    assert sept["balance"]["totalRows"] > 0
    sept_full = await build_ledger_book(company_id, month="2026-09", include_zero_rows=True)
    assert Decimal(_by_code(sept_full["balance"]["items"])["1001"]["debitTotal"]) == Decimal("0.00")

    october = await build_ledger_book(company_id, month="2026-10")
    cash_page = next(item for item in october["details"] if item["code"] == "1001")
    assert Decimal(cash_page["periodTotals"]["debit"]) == Decimal("500.00")
    assert cash_page["lines"][0]["date"] == "2026-09-20", "业务日期仍显示实际发生日"
    assert cash_page["lines"][0]["backfill"] is True
    assert "BACKFILL_IN_PERIOD" in _warning_codes(october)


@pytest.mark.asyncio
async def test_book_counterparties_exclude_self():
    """明细账的对方科目不含本科目自身，且覆盖同凭证其余行。"""
    company_id = "c-book-counterparty"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    page = next(item for item in book["details"] if item["code"] == "221101")

    codes = {item["code"] for item in page["lines"][0]["counterparties"]}
    assert "221101" not in codes
    assert codes == {"5602001", "5602016", "221102", "122101", "2221012"}


@pytest.mark.asyncio
async def test_book_empty_month_reports_warnings():
    """空月份：200 正常返回，details 为空但要能解释为什么没有账页。"""
    company_id = "c-book-empty"
    await _setup_company(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    assert book["details"] == []
    assert book["chronological"]["items"] == []
    codes = _warning_codes(book)
    assert "NO_ACTIVITY" in codes
    assert "PERIOD_NOT_CLOSED" in codes, "无期间行等同未结账，必须提示不能归档"
    assert book["meta"]["periodStatusLabel"] == "未结账"


@pytest.mark.asyncio
async def test_book_chronological_and_journals_scope():
    """序时账逐行平铺；日记账按业务日期分组并日结存。"""
    company_id = "c-book-journals"
    await _setup_company(company_id)
    await _insert_backfill_entry(company_id)

    book = await build_ledger_book(company_id, month="2026-10")
    assert book["chronological"]["voucherCount"] == 1
    assert book["chronological"]["isBalanced"] is True

    cash = next(item for item in book["journals"] if item["kind"] == "cash")
    assert len(cash["rows"]) == 1
    assert cash["rows"][0]["date"] == "2026-09-20"
    assert Decimal(cash["rows"][0]["debit"]) == Decimal("500.00")
    assert Decimal(cash["closing"]) == Decimal("500.00")
    bank = next(item for item in book["journals"] if item["kind"] == "bank")
    assert bank["rows"] == []


@pytest.mark.asyncio
async def test_book_year_scope_general_has_twelve_months():
    """年度册：总账每科目 12 行，且合计数与余额表全年发生额一致。"""
    company_id = "c-book-year"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    book = await build_ledger_book(company_id, year=2026, scope="year")
    general = book["general"]
    assert general["year"] == 2026
    assert general["items"]

    page = next(item for item in general["items"] if item["accountCode"] == "5602001")
    assert len(page["months"]) == 12
    assert page["months"][9]["month"] == "2026-10"
    assert Decimal(page["months"][9]["debit"]) == Decimal("20000.00")
    assert Decimal(page["totalDebit"]) == Decimal("20000.00")
    assert Decimal(page["closing"]) == Decimal("20000.00")

    # 年度册余额表是"全年发生额 + 截至年末余额"，期末必须与 12 月册同数
    december = await build_ledger_book(company_id, month="2026-12")
    year_rows = _by_code(book["balance"]["items"])
    dec_rows = _by_code(december["balance"]["items"])
    for code, row in dec_rows.items():
        assert year_rows[code]["closingBalance"] == row["closingBalance"], f"{code} 年末余额不一致"


@pytest.mark.asyncio
async def test_book_rejects_invalid_period():
    company_id = "c-book-invalid"
    await _setup_company(company_id)

    with pytest.raises(InvalidPeriod):
        await build_ledger_book(company_id, month="2026-13")
    with pytest.raises(InvalidPeriod):
        await build_ledger_book(company_id, month=None, year=None, scope="year")
    with pytest.raises(InvalidPeriod):
        await build_ledger_book(company_id, month="2026-10", scope="quarter")


@pytest.mark.asyncio
async def test_book_pagination_follows_balance_filter():
    """余额表过滤后，分页页数必须按过滤后的行数算（2026-10-20 真机打印抓到的 bug）。

    曾经前端自己过滤零发生额科目，后端却按全量 185 行算页数：目录说余额表占 8 页、
    实际只渲染 1 页，页码从第 3 页直接跳到第 11 页。现在过滤下沉到后端。
    """
    from services.ledger_book_service import PAGE_SIZE

    company_id = "c-book-pagination"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    filtered = await build_ledger_book(company_id, month="2026-10")
    full = await build_ledger_book(company_id, month="2026-10", include_zero_rows=True)

    assert filtered["balance"]["totalRows"] == len(full["balance"]["items"])
    assert len(filtered["balance"]["items"]) < filtered["balance"]["totalRows"], "样本应存在零发生额科目"
    assert all(row["hasActivity"] or row["hasBalance"] for row in filtered["balance"]["items"])

    section = next(s for s in filtered["pagination"]["sections"] if s["key"] == "balance")
    expected = max(1, (len(filtered["balance"]["items"]) + PAGE_SIZE["balance"] - 1) // PAGE_SIZE["balance"])
    assert section["pageCount"] == expected

    # 页码必须首尾相接，中间不留空洞
    pages = [s for s in filtered["pagination"]["sections"]]
    cursor = 1
    for item in pages:
        assert item["pageNo"] == cursor, f"分段 {item['key']} 页码不连续"
        cursor += item["pageCount"]
    assert filtered["pagination"]["totalPages"] == cursor - 1


@pytest.mark.asyncio
async def test_book_raises_when_lines_exceed_protection(monkeypatch):
    """超过单册保护上限时抛 BookTooLarge，并提示按季/年度分册（而不是默默截断）。"""
    from services.ledger_book_service import BookTooLarge

    company_id = "c-book-limit"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    monkeypatch.setattr("services.ledger_book_service.MAX_LINES", 1)
    with pytest.raises(BookTooLarge) as exc:
        await build_ledger_book(company_id, month="2026-10")
    assert "分册" in str(exc.value)


@pytest.mark.asyncio
async def test_book_build_stays_within_time_budget():
    """性能护栏：300 行分录的一册要在 1.5 秒内构建完（防未来引入 N+1 时的回归）。"""
    import time

    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    company_id = "c-book-perf"
    await _setup_company(company_id)

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1002", "5602013")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        for index in range(150):
            entry = JournalEntry(
                company_id=company_id,
                voucher_no=f"PERF-{index:04d}",
                occurred_on=date(2026, 10, 1),
                period_start=date(2026, 10, 1),
                summary="性能样本",
                status="posted",
                created_by="owner-1",
            )
            session.add(entry)
            await session.flush()
            session.add_all(
                [
                    JournalLine(
                        journal_entry_id=entry.id,
                        company_id=company_id,
                        line_no=1,
                        account_id=by_code["5602013"].id,
                        account_code="5602013",
                        account_name="管理费用-租赁费",
                        direction="debit",
                        amount=Decimal("10.00"),
                    ),
                    JournalLine(
                        journal_entry_id=entry.id,
                        company_id=company_id,
                        line_no=2,
                        account_id=by_code["1002"].id,
                        account_code="1002",
                        account_name="银行存款",
                        direction="credit",
                        amount=Decimal("10.00"),
                    ),
                ]
            )

    started = time.perf_counter()
    book = await build_ledger_book(company_id, month="2026-10")
    elapsed = time.perf_counter() - started

    assert book["chronological"]["voucherCount"] == 150
    assert elapsed < 1.5, f"整册构建耗时 {elapsed:.2f}s，超过 1.5s 预算"


@pytest.mark.asyncio
async def test_book_account_code_returns_single_leaf_page():
    """单科目查询模式：精确命中时 details 只含该科目一页，且与旧 sub-ledger 逐行一致。"""
    from services.ledger_service import query_sub_ledger

    company_id = "c-book-one-leaf"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    full = await build_ledger_book(company_id, month="2026-10")
    single = await build_ledger_book(company_id, month="2026-10", account_code="221101")

    assert [d["code"] for d in single["details"]] == ["221101"]
    expected = next(d for d in full["details"] if d["code"] == "221101")
    assert single["details"][0] == expected, "精确命中必须原样复用册内明细页"

    # 与旧接口逐行对账（金额、余额序列完全一致）
    legacy = await query_sub_ledger(company_id, "221101", "2026-10")
    new_lines = single["details"][0]["lines"]
    assert [Decimal(r["balance"]) for r in legacy] == [Decimal(r["balance"]) for r in new_lines]
    assert [Decimal(r["debit"]) + Decimal(r["credit"]) for r in legacy] == [
        Decimal(r["debit"]) + Decimal(r["credit"]) for r in new_lines
    ]


@pytest.mark.asyncio
async def test_book_account_code_aggregates_parent_prefix():
    """父科目/容器科目查询：前缀合成聚合页，期末与余额表该科目行勾稽。"""
    company_id = "c-book-one-parent"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    single = await build_ledger_book(company_id, month="2026-10", account_code="2211")
    assert [d["code"] for d in single["details"]] == ["2211"]

    page = single["details"][0]
    balance_row = next(r for r in single["balance"]["items"] if r["accountCode"] == "2211")
    assert Decimal(page["closing"]) == Decimal(balance_row["closingBalance"])
    assert Decimal(page["opening"]) == Decimal(balance_row["openingBalance"])
    # 容器 2211 本身不直接记账：行来自子级 221101/221102，按凭证顺序合并
    assert {line["voucherRef"] for line in page["lines"]} == {"VOU-202610-000001"}
    assert Decimal(page["periodTotals"]["credit"]) == Decimal("21400.00")
    # 对方科目排除整个科目族：221101 的对方是 5602001/122101/2221012，不含 221102
    first_line = next(line for line in page["lines"] if line["debit"] == "0.00" and line["credit"] == "14800.00")
    assert all(not cp["code"].startswith("2211") for cp in first_line["counterparties"])


@pytest.mark.asyncio
async def test_book_account_code_without_activity_returns_empty():
    """无任何数据的科目：details 为空，由前端显示空态（不报错）。"""
    company_id = "c-book-one-empty"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    single = await build_ledger_book(company_id, month="2026-10", account_code="6001")
    assert single["details"] == []


def test_book_route_exposes_account_code_param():
    """路由层防呆：account_code 参数曾因替换锚点错位而静默丢失（真机冒烟才发现）。

    服务层测试直调 ``build_ledger_book``，路由签名丢参数不会被发现；这里把
    路由签名本身锁住。
    """
    import inspect

    from routers.ledger import ledger_book

    sig = inspect.signature(ledger_book)
    assert "account_code" in sig.parameters
    assert "show_zero_rows" in sig.parameters


@pytest.mark.asyncio
async def test_journal_book_splits_by_cash_account():
    """F2：其他货币资金的平台待结算科目是事实账户，各出独立日记账页。

    微信/支付宝待结算（1012001/1012002）有发生额时各出一页，期末与余额表
    该科目行勾稽；无发生的平台账户不出空白页；现金/银行页固定保留。
    """
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    company_id = "c-book-journal-split"
    await _setup_company(company_id)

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1002", "1012001", "1012002")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no="WX-001",
            occurred_on=date(2026, 10, 5),
            period_start=date(2026, 10, 1),
            summary="微信收款待结算",
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=1,
                    account_id=by_code["1012001"].id,
                    account_code="1012001",
                    account_name="微信支付待结算",
                    direction="debit",
                    amount=Decimal("88.00"),
                ),
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=2,
                    account_id=by_code["1002"].id,
                    account_code="1002",
                    account_name="银行存款",
                    direction="credit",
                    amount=Decimal("88.00"),
                ),
            ]
        )

    book = await build_ledger_book(company_id, month="2026-10")
    journals = {item["kind"]: item for item in book["journals"]}

    assert set(journals) == {"cash", "bank", "1012001"}, "支付宝无发生不出页"
    assert journals["1012001"]["name"] == "微信支付待结算日记账"
    assert Decimal(journals["1012001"]["totalDebit"]) == Decimal("88.00")
    assert Decimal(journals["1012001"]["closing"]) == Decimal("88.00")

    balance_row = next(r for r in book["balance"]["items"] if r["accountCode"] == "1012001")
    assert journals["1012001"]["closing"] == balance_row["closingBalance"]

    # 目录分段按新增页自动延伸且页码连续；空页（现金本期无发生）不占页码
    sections = book["pagination"]["sections"]
    journal_sections = [s for s in sections if s["kind"] == "journal"]
    assert [s["key"] for s in journal_sections] == ["journal:bank", "journal:1012001"]
    cursor = 1
    for item in sections:
        assert item["pageNo"] == cursor
        cursor += item["pageCount"]
    assert book["pagination"]["totalPages"] == cursor - 1
