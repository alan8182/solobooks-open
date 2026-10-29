# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""报表会计专业视图（standard 块）测试。

1. 资产负债表会小企01表全量行次：年初/期末两侧恒等（资产 = 负债 + 权益），
   年初余额由历史分录派生（跨年连续性口径）。
2. 权益变动表会企04表节次结构：年初 + 净利润 + 投入 + 分配 + 内部结转 = 年末，
   逐列成立，且与简化口径的年末数一致。

月份选用 2197/2198 避开其他测试文件的固定期间（test_api 用 2031-06 / 2099-12），
凭证 id 统一 std-view- 前缀保证幂等。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from services.equity_service import get_equity_changes


# ── 资产负债表 standard 块（走 API，test-token 直连 c1 公司）──

_PNL_CREDIT_PREFIXES = ("5001", "5051", "5111", "5301")
_PNL_DEBIT_PREFIXES = ("5401", "5402", "5403", "5601", "5602", "5603", "5711", "5801")


def _post_year_close_voucher(fiscal_year: int) -> None:
    """补 2197 年的期末损益结转凭证（产品由月结自动生成，测试内手动补齐）。

    金额从派生期初（历史分录累计）动态计算：5xxx 科目正常余额轧差后结转 3103。
    """
    import asyncio

    from services.fiscal_year_service import derive_year_opening_balances

    def _is_pnl(code: str) -> str | None:
        if code.startswith(_PNL_CREDIT_PREFIXES):
            return "credit"
        if code.startswith(_PNL_DEBIT_PREFIXES):
            return "debit"
        return None

    async def _run() -> None:
        from db.models import ChartAccount, JournalEntry, JournalLine
        from db.session import get_engine, session_scope
        from db.base import Base
        import db.models  # noqa: F401

        engine = get_engine()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_scope() as session:
            # 派生 fiscal_year+1 的期初 = fiscal_year 年末状态（boundary 是次年 1 月 1 日）
            balances = await derive_year_opening_balances(session, "c1", fiscal_year + 1)

        # 正常余额：贷方性质科目余额为正，借方性质为正表示借方余额
        signed: dict[str, Decimal] = {}
        for code, (debit, credit) in balances.items():
            nature = _is_pnl(code)
            if nature == "credit":
                signed[code] = credit - debit
            elif nature == "debit":
                signed[code] = debit - credit
        # 利润 = 贷方性质余额合计 − 借方性质余额合计
        profit = sum(
            (balance if _is_pnl(code) == "credit" else -balance)
            for code, balance in signed.items()
        )

        debit_lines: list[tuple[str, str, Decimal]] = []
        credit_lines: list[tuple[str, str, Decimal]] = []
        for code, balance in sorted(signed.items()):
            if balance == 0:
                continue
            nature = _is_pnl(code)
            name = f"结转-{code}"
            if nature == "credit":
                # 贷方余额 → 借方结转平掉
                (debit_lines if balance > 0 else credit_lines).append((code, name, abs(balance)))
            else:
                # 借方余额 → 贷方结转平掉
                (credit_lines if balance > 0 else debit_lines).append((code, name, abs(balance)))
        if profit > 0:
            credit_lines.append(("3103", "本年利润", profit))
        elif profit < 0:
            debit_lines.append(("3103", "本年利润", abs(profit)))
        if not debit_lines and not credit_lines:
            return

        async with session_scope() as session:
            codes = {code for code, _, _ in debit_lines + credit_lines}
            chart = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == "c1",
                        ChartAccount.code.in_(codes),
                    )
                )
            ).scalars().all()
            by_code = {row.code: row for row in chart}
            entry = JournalEntry(
                company_id="c1",
                voucher_no=f"STD-CLOSE-{fiscal_year}",
                occurred_on=date(fiscal_year, 12, 31),
                period_start=date(fiscal_year, 12, 1),
                summary=f"{fiscal_year}年12月期末损益结转（测试）",
                status="posted",
                entry_type="closing",
                created_by="owner-1",
                metadata_json={"isClosingTransfer": True},
            )
            session.add(entry)
            await session.flush()
            for idx, (code, name, amount) in enumerate(debit_lines + credit_lines, start=1):
                direction = "debit" if idx <= len(debit_lines) else "credit"
                session.add(
                    JournalLine(
                        journal_entry_id=entry.id,
                        company_id="c1",
                        line_no=idx,
                        account_id=by_code[code].id,
                        account_code=code,
                        account_name=name,
                        direction=direction,
                        amount=amount,
                    )
                )

    asyncio.run(_run())


def _confirm(client, auth_headers, entry_id, direction, amount, category, occurred_on):
    resp = client.post(
        "/api/record/confirm",
        json={
            "id": entry_id,
            "direction": direction,
            "amount": amount,
            "category": category,
            "title": f"standard视图测试-{entry_id}",
            "occurDate": occurred_on,
            "sourceType": "integration_test",
            "dedupFingerprint": entry_id,
        },
        headers={**auth_headers, "Idempotency-Key": f"confirm:{entry_id}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestBalanceSheetStandardView:
    def test_full_rows_with_opening_from_prior_year(self, client, auth_headers):
        """上年分录 → 年初列派生；年初/期末两侧恒等；boss 视图年初口径。

        c1 里还有其他测试文件写入的历史（2031-06 等），所以绝对数不可断言，
        改用「基线 + 增量」：先取贴数前的基线，再验本组业务带来的增量与恒等式。
        """
        def snapshot(month):
            return client.post(
                "/api/report/balance-sheet", json={"month": month}, headers=auth_headers
            ).json()

        baseline = snapshot("2198-05")

        _confirm(client, auth_headers, "std-view-py-income", "income", "10000.00", "主营业务收入", "2197-12-15")
        _confirm(client, auth_headers, "std-view-py-expense", "expense", "2000.00", "办公费", "2197-12-20")
        # 产品在月结时自动生成年末损益结转（5xxx → 3103）；测试补齐该凭证，
        # 否则上年损益悬在 5xxx 科目，年初权益列天然不平（见 standard 残差口径注释）。
        _post_year_close_voucher(2197)
        _confirm(client, auth_headers, "std-view-cy-income", "income", "5000.00", "主营业务收入", "2198-05-10")
        _confirm(client, auth_headers, "std-view-cy-expense", "expense", "800.00", "办公费", "2198-05-20")

        data = snapshot("2198-05")
        std = data["standard"]
        assert std is not None
        base_std = baseline["standard"]
        base_left = {row["name"]: row for row in base_std["assets"]}
        base_right = {row["name"]: row for row in base_std["rights"]}
        left = {row["name"]: row for row in std["assets"]}
        right = {row["name"]: row for row in std["rights"]}

        # 年初列增量：上年收入 10000 按 1% 价税分离 → 收入 9900.99、税 99.01、净利 7900.99
        assert Decimal(left["资产总计"]["openingAmount"]) - Decimal(base_left["资产总计"]["openingAmount"]) == Decimal("8000.00")
        assert Decimal(right["负债和所有者权益（或股东权益）总计"]["openingAmount"]) - Decimal(base_right["负债和所有者权益（或股东权益）总计"]["openingAmount"]) == Decimal("8000.00")
        assert Decimal(right["应交税费"]["openingAmount"]) - Decimal(base_right["应交税费"]["openingAmount"]) == Decimal("99.01")
        assert Decimal(right["未分配利润"]["openingAmount"]) - Decimal(base_right["未分配利润"]["openingAmount"]) == Decimal("7900.99")
        # 期末列增量：+5000 收入（4950.50 + 税 49.50）− 800 费用；
        # 应交税费期末 = 年初(99.01) + 本年(49.50)，增量 148.51
        assert Decimal(left["资产总计"]["currentAmount"]) - Decimal(base_left["资产总计"]["currentAmount"]) == Decimal("12200.00")
        assert Decimal(right["应交税费"]["currentAmount"]) - Decimal(base_right["应交税费"]["currentAmount"]) == Decimal("148.51")
        assert Decimal(right["未分配利润"]["currentAmount"]) - Decimal(base_right["未分配利润"]["currentAmount"]) == Decimal("12051.49")

        # 与 boss 视图总计一致（同一份 balance map 的两种呈现）——
        # 仅在科目口径天然平衡（无未月结月份）时两侧才一致
        if std["balanced"]:
            assert Decimal(left["资产总计"]["currentAmount"]) == Decimal(data["totalAssets"])
            assert (
                Decimal(right["负债和所有者权益（或股东权益）总计"]["currentAmount"])
                == Decimal(data["totalLiabilities"]) + Decimal(data["totalEquity"])
            )
        # 残差口径下两侧恒等恒成立（无论账套是否有未月结月份）
        assert left["资产总计"]["currentAmount"] == right["负债和所有者权益（或股东权益）总计"]["currentAmount"]
        assert left["资产总计"]["openingAmount"] == right["负债和所有者权益（或股东权益）总计"]["openingAmount"]

        # 会小企01表全量行次：零余额行也要在（存货/工程物资本例无发生）
        assert Decimal(left["存货"]["currentAmount"]) == 0
        assert Decimal(left["工程物资"]["currentAmount"]) == 0
        assert left["流动资产："]["kind"] == "section"
        assert left["流动资产合计"]["kind"] == "subtotal"
        assert left["资产总计"]["kind"] == "total"
        # 流动资产合计增量 = 货币资金增量（本组业务全部走银行存款）
        assert (
            Decimal(left["流动资产合计"]["currentAmount"]) - Decimal(base_left["流动资产合计"]["currentAmount"])
            == Decimal("12200.00")
        )

    def test_boss_equity_opening_split(self, client, auth_headers):
        """本年利润年初恒为 0，全部年初权益落在未分配利润行。"""
        data = client.post(
            "/api/report/balance-sheet", json={"month": "2198-05"}, headers=auth_headers
        ).json()
        equity = {item["accountCode"]: item for item in data["equity"]}
        assert equity["3103"]["previousAmount"] == "0.00"
        std = data["standard"]
        right = {row["name"]: row for row in std["rights"]}
        # boss 视图权益年初合计 = standard 未分配利润年初（残差口径）
        assert (
            Decimal(equity["3103"]["previousAmount"]) + Decimal(equity["3104"]["previousAmount"])
            == Decimal(right["未分配利润"]["openingAmount"])
        )

    def test_standard_balanced_on_empty_company(self, client, auth_headers):
        data = client.post(
            "/api/report/balance-sheet", json={"month": "2198-01"}, headers=auth_headers
        ).json()
        std = data["standard"]
        assert std["assets"][0]["name"] == "流动资产："
        assert std["assets"][-1]["name"] == "资产总计"
        assert std["rights"][-1]["name"] == "负债和所有者权益（或股东权益）总计"
        assert std["assets"][-1]["currentAmount"] == std["rights"][-1]["currentAmount"]


# ── 权益变动表 standard 块（直连 equity_service，独立公司避免串扰）──

async def _setup_equity_company(company_id: str) -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine, session_scope
    from services.accounting_core_service import ensure_tenant

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


async def _post_voucher(company_id: str, voucher_no: str, occurred: date, lines: list[tuple[str, str, str, str]]) -> None:
    """lines: (account_code, account_name, direction, amount)"""
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_([line[0] for line in lines]),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no=voucher_no,
            occurred_on=occurred,
            period_start=occurred.replace(day=1),
            summary=voucher_no,
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        for idx, (code, name, direction, amount) in enumerate(lines, start=1):
            session.add(
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=idx,
                    account_id=by_code[code].id,
                    account_code=code,
                    account_name=name,
                    direction=direction,
                    amount=Decimal(amount),
                )
            )


async def _post_closing_voucher(company_id: str, voucher_no: str, occurred: date, lines: list[tuple[str, str, str, str]]) -> None:
    """结转形态凭证：entry_type=closing + isClosingTransfer 标记，与产品月结产物一致。"""
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_([line[0] for line in lines]),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no=voucher_no,
            occurred_on=occurred,
            period_start=occurred.replace(day=1),
            summary=voucher_no,
            status="posted",
            entry_type="closing",
            created_by="owner-1",
            metadata_json={"isClosingTransfer": True},
        )
        session.add(entry)
        await session.flush()
        for idx, (code, name, direction, amount) in enumerate(lines, start=1):
            session.add(
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=idx,
                    account_id=by_code[code].id,
                    account_code=code,
                    account_name=name,
                    direction=direction,
                    amount=Decimal(amount),
                )
            )


@pytest.mark.asyncio
async def test_equity_standard_decomposition():
    """出资入上年 → 本年净利 30000、分配 10000、提取盈余公积 5000。

    standard 各列恒等成立；盈余公积提取列在「利润分配」节（会企04表口径），
    未分配利润借方含提取与分配，内部结转余项为 0。
    """
    company_id = "c-equity-std"
    await _setup_equity_company(company_id)
    await _post_voucher(company_id, "STD-CAP-1", date(2027, 6, 1), [
        ("1002", "银行存款", "debit", "100000.00"),
        ("3001", "实收资本", "credit", "100000.00"),
    ])
    await _post_voucher(company_id, "STD-PNL-1", date(2028, 5, 10), [
        ("1002", "银行存款", "debit", "50000.00"),
        ("5001", "主营业务收入", "credit", "50000.00"),
    ])
    await _post_voucher(company_id, "STD-PNL-2", date(2028, 6, 10), [
        ("5602", "管理费用", "debit", "20000.00"),
        ("1002", "银行存款", "credit", "20000.00"),
    ])
    await _post_voucher(company_id, "STD-DIST-1", date(2028, 7, 10), [
        ("3104", "利润分配-应付利润", "debit", "10000.00"),
        ("2232", "应付利润", "credit", "10000.00"),
    ])
    await _post_voucher(company_id, "STD-DIST-2", date(2028, 8, 10), [
        ("3104", "利润分配-提取盈余公积", "debit", "5000.00"),
        ("3101", "盈余公积", "credit", "5000.00"),
    ])

    data = await get_equity_changes(company_id, 2028)
    std = data["standard"]
    col_keys = [col["key"] for col in std["columns"]]
    assert col_keys == ["paid_in_capital", "capital_reserve", "surplus_reserve", "retained_earnings", "total"]

    rows = {row["key"]: row for row in std["rows"]}
    total = rows["year_end"]["values"]["total"]

    # 与简化口径互相印证：年末合计一致（10 万出资 + 净利 3 万 − 分配 1.5 万）
    assert Decimal(total) == Decimal(data["totals"]["yearEnd"]) == Decimal("120000.00")
    assert Decimal(rows["prior_year_end"]["values"]["total"]) == Decimal("100000.00")
    assert Decimal(rows["net_profit"]["values"]["retained_earnings"]) == Decimal("30000.00")
    assert Decimal(rows["net_profit"]["values"]["total"]) == Decimal("30000.00")
    # 利润分配：盈余公积 +5000（提取）、未分配利润 −15000（提取 5000 + 应付利润 10000）
    assert Decimal(rows["profit_distribution"]["values"]["surplus_reserve"]) == Decimal("5000.00")
    assert Decimal(rows["profit_distribution"]["values"]["retained_earnings"]) == Decimal("-15000.00")
    # 投入列：本年无新增出资
    assert Decimal(rows["owner_investment"]["values"]["paid_in_capital"]) == Decimal("0.00")
    # 每列恒等：年初 + 净利润 + 投入 + 分配 + 结转 = 年末
    for key in col_keys:
        values = rows["year_end"]["values"]
        expected = (
            Decimal(rows["prior_year_end"]["values"][key])
            + Decimal(rows["net_profit"]["values"][key])
            + Decimal(rows["owner_investment"]["values"][key])
            + Decimal(rows["profit_distribution"]["values"][key])
            + Decimal(rows["internal_transfer"]["values"][key])
        )
        assert Decimal(values[key]) == expected, f"列 {key} 节次分解不守恒"
    # 各分项行之和 = 增减变动行
    for key in col_keys:
        movements = sum(
            Decimal(rows[k]["values"][key])
            for k in ("net_profit", "owner_investment", "profit_distribution", "internal_transfer")
        )
        assert Decimal(rows["changes"]["values"][key]) == movements


@pytest.mark.asyncio
async def test_equity_with_monthly_closing_vouchers():
    """做了月结的账套：损益经结转凭证滚入 3103，净利润从结转净额取。

    本年：5 月结转净利 800、6 月结转亏损 300、9 月结转净利 500 → 净利润 1000；
    3103 结转借贷不进未分配利润增减列；年末 3103→3104006 结转是内部结转，
    不改变年末数。分配 200 走 3104 借方。
    """
    company_id = "c-equity-closing"
    await _setup_equity_company(company_id)
    await _post_voucher(company_id, "EQC-CAP-1", date(2027, 3, 1), [
        ("1002", "银行存款", "debit", "50000.00"),
        ("3001", "实收资本", "credit", "50000.00"),
    ])

    async def _post_closing(voucher_no: str, occurred: date, signed_pnl: str, carryforward: bool = False) -> None:
        if carryforward:
            lines = [("3103", "本年利润", "debit", signed_pnl), ("3104006", "利润分配-未分配利润", "credit", signed_pnl)]
        elif Decimal(signed_pnl) > 0:
            lines = [("3103", "本年利润", "credit", signed_pnl)]
        else:
            lines = [("3103", "本年利润", "debit", signed_pnl.lstrip("-"))]
        await _post_closing_voucher(company_id, voucher_no, occurred, lines)

    await _post_closing("EQC-CLOSE-5", date(2028, 5, 31), "800.00")
    await _post_closing("EQC-CLOSE-6", date(2028, 6, 30), "-300.00")
    await _post_closing("EQC-CLOSE-9", date(2028, 9, 30), "500.00")
    # 月内再分配（3104 借方），随后年末 3103→3104006 结转
    await _post_voucher(company_id, "EQC-DIST-1", date(2028, 11, 1), [
        ("3104", "利润分配-应付利润", "debit", "200.00"),
        ("2232", "应付利润", "credit", "200.00"),
    ])
    await _post_closing("EQC-CLOSE-12", date(2028, 12, 31), "1000.00", carryforward=True)

    data = await get_equity_changes(company_id, 2028)
    std = data["standard"]
    rows = {row["key"]: row for row in std["rows"]}

    assert data["netProfit"] == "1000.00"
    assert Decimal(rows["net_profit"]["values"]["retained_earnings"]) == Decimal("1000.00")
    assert Decimal(rows["profit_distribution"]["values"]["retained_earnings"]) == Decimal("-200.00")
    # 年末 = 0 + 1000 − 200 = 800（结转 3103→3104006 不改变权益总额）
    assert Decimal(rows["year_end"]["values"]["retained_earnings"]) == Decimal("800.00")
    # 简化口径与节次分解一致
    retained_row = next(row for row in data["rows"] if row["key"] == "retained_earnings")
    assert Decimal(retained_row["yearEnd"]) == Decimal("800.00")
    assert Decimal(data["totals"]["yearEnd"]) == Decimal("50800.00")
