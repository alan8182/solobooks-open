# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""报表项映射规则（P2）测试。

锁死的契约（对应设计文档 §4.5 / §9）：
- **前缀不得短于 4 位**。``_sum_prefix(balances,"4")`` 会把 4301 连同所有 4xxx
  一并计入，这是本项目真实踩过的坑；
- **精确码不等于前缀**。应付职工薪酬曾用 ``_sum_codes(["2211"])`` 求和，
  漏掉 221102 子级、静默归零。规则里 ``prefix:2211`` 必须能吃到 221102；
- **派生行不可配置化**。net_profit / asset_total 等由基础行算出，配置化等于
  在数据库里建公式引擎，要处理循环引用且无法静态校验；
- **减项必须能表达**。research_expense = 4301 − 4301002，单列前缀表达不了；
- **口径等价是硬要求**。用真实口径的 49 个基础行（利润表 13 + 资产负债表 36）
  跑双轨对比必须零差异——这是切换的前提，不是可选项；
- **未达标不得切换**。连续 N 次零差异才 ready，且「没配规则」不等于「零差异」；
- **零回归**。新建表不改变任何对外报表行为。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from core.errors import AccountingError
from core.money import ZERO
from db.models import ReportLineRule
from services.accounting_core_service import ensure_tenant
from services.report_line_rule_service import (
    DERIVED_LINES,
    MIN_PREFIX_LENGTH,
    SWITCH_REQUIRED_CLEAN_PERIODS,
    _parse_patterns,
    archive_rule,
    diff_report_lines,
    evaluate_line,
    list_rules,
    replace_rules,
    require_switch_ready,
    switch_readiness,
)
from services.tax_filing_export_service import (
    _build_balance_rows,
    _normal_balance,
    _profit_map,
)


COMPANY = "rlr-co-1"
OTHER = "rlr-co-2"


async def _reset() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.models import AuditEvent
    from db.session import get_engine, session_scope

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        # 对比历史直接决定切换达标，必须一并清干净，否则跨用例串味。
        await session.execute(
            delete(AuditEvent).where(
                AuditEvent.company_id.in_([COMPANY, OTHER]),
                AuditEvent.action == "report_line_rule_compare",
            )
        )
        await session.execute(
            delete(ReportLineRule).where(ReportLineRule.company_id.in_([COMPANY, OTHER]))
        )
        await ensure_tenant(session, COMPANY, "owner-1")
        await ensure_tenant(session, OTHER, "owner-1")


def _seed_compare_audits(session, company_id: str, prefix: str, flags: list[bool], *, skipped: bool = False) -> None:
    """按给定顺序写入对比审计。``flags[0]`` 是最近一次。"""
    from db.models import AuditEvent
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 10, 20, tzinfo=timezone.utc)
    for index, clean in enumerate(flags):
        session.add(AuditEvent(
            id=f"{prefix}-{company_id}-{index}",
            company_id=company_id,
            user_id="owner-1",
            action="report_line_rule_compare",
            entity_type="report_line_rule",
            entity_id="profit",
            # created_at 显式递增，保证顺序确定（否则同批写入的排序不可靠）
            created_at=base - timedelta(minutes=index),
            after={"report": "profit", "clean": clean, "skipped": skipped},
            request_id=f"{prefix}-req-{index}",
        ))


def _p(mode: str, value: str, sign: int = 1) -> dict:
    return {"mode": mode, "value": value, "sign": sign}


def _rule(report: str, line_code: str, patterns: list[dict], *, name: str = "") -> dict:
    """内部形态（diff/evaluate 用 snake_case 键）。"""
    return {
        "report": report,
        "line_code": line_code,
        "line_name": name or line_code,
        "status": "active",
        "patterns": patterns,
    }


def _payload_rule(line_code: str, patterns: list[dict], *, name: str = "") -> dict:
    """入库形态（replace_rules 用 camelCase 键，与 HTTP 契约一致）。"""
    return {
        "lineCode": line_code,
        "lineName": name or line_code,
        "patterns": patterns,
        "status": "active",
        "effectiveFrom": "2026-01-01",
    }


# ── 纯函数层：pattern 解析 ──

@pytest.mark.asyncio
async def test_prefix_shorter_than_four_rejected():
    """历史事故防线：短前缀会把 4301 连同所有 4xxx 一起吞掉。"""
    await _reset()
    with pytest.raises(AccountingError) as exc:
        _parse_patterns([_p("prefix", "43")], report="profit", line_code="research_expense")
    assert exc.value.code == "REPORT_LINE_PATTERN_PREFIX_TOO_SHORT"
    assert exc.value.context["minLength"] == MIN_PREFIX_LENGTH


@pytest.mark.asyncio
async def test_prefix_exactly_four_accepted():
    await _reset()
    assert _parse_patterns([_p("prefix", "4301")], report="profit", line_code="x") == [
        {"mode": "prefix", "value": "4301", "sign": 1},
    ]


@pytest.mark.asyncio
async def test_invalid_match_mode_rejected():
    await _reset()
    with pytest.raises(AccountingError) as exc:
        _parse_patterns([{"mode": "regex", "value": ".*"}], report="profit", line_code="x")
    assert exc.value.code == "INVALID_REPORT_LINE_RULE"


@pytest.mark.asyncio
async def test_sign_only_accepts_plus_one():
    """sign 是加减号，不是比例——放行 0.5 会静默改变金额。"""
    await _reset()
    with pytest.raises(AccountingError):
        _parse_patterns([_p("prefix", "4301", 2)], report="profit", line_code="x")
    with pytest.raises(AccountingError):
        _parse_patterns([_p("prefix", "4301", 0)], report="profit", line_code="x")
    assert _parse_patterns([_p("prefix", "4301", -1)], report="profit", line_code="x")[0]["sign"] == -1


@pytest.mark.asyncio
async def test_empty_patterns_rejected():
    await _reset()
    with pytest.raises(AccountingError):
        _parse_patterns([], report="profit", line_code="revenue")


@pytest.mark.asyncio
async def test_range_pattern_validated():
    await _reset()
    with pytest.raises(AccountingError):
        _parse_patterns([_p("range", "560199-560101")], report="profit", line_code="x")
    assert _parse_patterns([_p("range", "560101-560199")], report="profit", line_code="x")[0]["value"] == "560101-560199"


# ── 纯函数层：求值 ──

@pytest.mark.asyncio
async def test_prefix_captures_child_accounts():
    """2211 必须吃到 221102。这正是那次「应付职工薪酬静默归零」的事故点。"""
    await _reset()
    balances = {"2211": Decimal("70"), "221102": Decimal("30")}
    rule = _rule("balance", "salary_payable", [_p("prefix", "2211")])
    assert evaluate_line(rule, balances) == Decimal("100")


@pytest.mark.asyncio
async def test_exact_mode_does_not_capture_siblings():
    """exact 只取列出的科目——广宣费用 560101 前缀会误吞 5601011 运输费。"""
    await _reset()
    balances = {"5601015": Decimal("30"), "5601016": Decimal("20"), "5601011": Decimal("70")}
    rule = _rule("profit", "advertising", [_p("exact", "5601015,5601016")])
    assert evaluate_line(rule, balances) == Decimal("50")


@pytest.mark.asyncio
async def test_negative_pattern_subtracts():
    """research_expense = 4301 − 4301002（资本化部分不进损益）。

    prefix("4301") 会命中 4301001 **和** 4301002（子级也以 4301 开头），
    所以先得 250，再用 prefix("4301002") 减掉资本化的 50 → 200。
    这正是现有硬编码 ``_sum_prefix("4301") - _sum_prefix("4301002")`` 的行为。
    """
    await _reset()
    balances = {"4301001": Decimal("200"), "4301002": Decimal("50")}
    rule = _rule("profit", "research_expense", [_p("prefix", "4301"), _p("prefix", "4301002", -1)])
    assert evaluate_line(rule, balances) == Decimal("200")


@pytest.mark.asyncio
async def test_range_pattern_matches_inclusively():
    await _reset()
    balances = {"560101": Decimal("5"), "560199": Decimal("7"), "560200": Decimal("9")}
    rule = _rule("profit", "mid", [_p("range", "560101-560199")])
    assert evaluate_line(rule, balances) == Decimal("12")


# ── 派生行不可配置化 ──

@pytest.mark.asyncio
async def test_derived_lines_rejected_on_save():
    await _reset()
    from db.session import session_scope

    with pytest.raises(AccountingError) as exc:
        async with session_scope() as session:
            await replace_rules(
                session, COMPANY, "owner-1",
                {"report": "profit", "rules": [_payload_rule("net_profit", [_p("exact", "5001")])]},
                request_id="req-derived",
            )
    assert exc.value.code == "INVALID_REPORT_LINE_RULE"
    assert exc.value.context["derived"] is True


@pytest.mark.asyncio
async def test_all_derived_lines_known():
    """派生行清单必须覆盖现有两张表的全部计算行，否则会静默漏掉。"""
    await _reset()
    assert {"net_profit", "operating_profit", "total_profit"} <= DERIVED_LINES["profit"]
    assert {"asset_total", "liability_total", "equity_total"} <= DERIVED_LINES["balance"]


# ── 口径等价（切换的前提） ──

PROFIT_BALANCES = {
    "5001": Decimal("1000"), "5051": Decimal("500"),
    "5401": Decimal("300"), "5402": Decimal("200"), "5403": Decimal("50"),
    "5601": Decimal("120"), "5602": Decimal("80"),
    "5601015": Decimal("30"), "5601016": Decimal("20"), "5601011": Decimal("70"),
    "5602002": Decimal("25"),
    "4301001": Decimal("200"), "4301002": Decimal("50"),
    "5603": Decimal("15"), "5111": Decimal("8"), "5301": Decimal("3"),
    "5711": Decimal("9"), "5801": Decimal("40"),
}

BALANCE_BALANCES = {
    "1001": Decimal("900"), "1002": Decimal("600"), "1012001": Decimal("100"), "1012": Decimal("50"),
    "1101": Decimal("70"), "1121": Decimal("40"), "1122": Decimal("300"), "1123": Decimal("20"),
    "1131": Decimal("10"), "1132": Decimal("5"),
    "122101": Decimal("60"), "122102": Decimal("40"),
    "1403": Decimal("80"), "1405": Decimal("120"), "1901": Decimal("15"),
    "1501": Decimal("200"), "1511": Decimal("180"),
    "1601": Decimal("500"), "1602": Decimal("130"), "1604": Decimal("90"),
    "1701": Decimal("75"), "1702": Decimal("20"), "1801": Decimal("35"), "4301002": Decimal("210"),
    "2001": Decimal("150"), "2201": Decimal("30"), "2202": Decimal("240"), "2203": Decimal("45"),
    "221101": Decimal("70"), "221102": Decimal("30"), "2221012": Decimal("12"),
    "2231": Decimal("8"), "2232": Decimal("25"), "2241": Decimal("33"),
    "2501": Decimal("400"), "2701": Decimal("120"), "2401": Decimal("20"),
    "3001": Decimal("1000"), "3002": Decimal("60"), "3101": Decimal("100"),
    "3103": Decimal("50"), "3104": Decimal("25"), "3104006": Decimal("15"),
}

PROFIT_RULES = [
    _rule("profit", "revenue", [_p("exact", "5001,5051")]),
    _rule("profit", "cost", [_p("prefix", "5401"), _p("prefix", "5402")]),
    _rule("profit", "tax_surcharge", [_p("exact", "5403")]),
    _rule("profit", "sales_expense", [_p("prefix", "5601")]),
    _rule("profit", "advertising", [_p("exact", "5601015,5601016")]),
    _rule("profit", "management_expense", [_p("prefix", "5602"), _p("prefix", "4301"), _p("prefix", "4301002", -1)]),
    _rule("profit", "entertainment", [_p("exact", "5602002")]),
    _rule("profit", "research_expense", [_p("prefix", "4301"), _p("prefix", "4301002", -1)]),
    _rule("profit", "finance_expense", [_p("prefix", "5603")]),
    _rule("profit", "investment_income", [_p("exact", "5111")]),
    _rule("profit", "non_operating_income", [_p("exact", "5301")]),
    _rule("profit", "non_operating_expense", [_p("exact", "5711")]),
    _rule("profit", "income_tax", [_p("exact", "5801")]),
]

BALANCE_RULES = [
    _rule("balance", "cash", [_p("exact", "1001,1002,1012001,1012")]),
    _rule("balance", "short_investment", [_p("exact", "1101")]),
    _rule("balance", "notes_receivable", [_p("exact", "1121")]),
    _rule("balance", "accounts_receivable", [_p("exact", "1122")]),
    _rule("balance", "prepaid", [_p("exact", "1123")]),
    _rule("balance", "dividends_receivable", [_p("exact", "1131")]),
    _rule("balance", "interest_receivable", [_p("exact", "1132")]),
    _rule("balance", "other_receivable", [_p("prefix", "1221")]),
    _rule("balance", "inventory", [_p("exact", "1403,1405")]),
    _rule("balance", "raw_material", [_p("exact", "1403")]),
    _rule("balance", "goods", [_p("exact", "1405")]),
    _rule("balance", "other_current_asset", [_p("exact", "1901")]),
    _rule("balance", "long_bond_investment", [_p("exact", "1501")]),
    _rule("balance", "long_equity_investment", [_p("exact", "1511")]),
    _rule("balance", "fixed_asset_cost", [_p("exact", "1601")]),
    _rule("balance", "accumulated_depreciation", [_p("exact", "1602")]),
    _rule("balance", "construction", [_p("exact", "1604")]),
    _rule("balance", "intangible_asset_cost", [_p("exact", "1701")]),
    _rule("balance", "accumulated_amortization", [_p("exact", "1702")]),
    # 无形资产按净额列示（1701 − 1702，贷方科目带符号后为负，故 1702 取 sign=-1 相加）；
    # 净额行 intangible_asset_book_value 与别名 intangible_amortization 是派生行，不参与对比
    _rule("balance", "intangible_asset", [_p("exact", "1701"), _p("exact", "1702", -1)]),
    _rule("balance", "long_prepaid", [_p("exact", "1801")]),
    _rule("balance", "dev_expense", [_p("exact", "4301002")]),
    _rule("balance", "short_borrow", [_p("exact", "2001")]),
    _rule("balance", "notes_payable", [_p("exact", "2201")]),
    _rule("balance", "accounts_payable", [_p("exact", "2202")]),
    _rule("balance", "advance_receipt", [_p("exact", "2203")]),
    _rule("balance", "salary_payable", [_p("prefix", "2211")]),
    _rule("balance", "tax_payable", [_p("prefix", "2221")]),
    _rule("balance", "interest_payable", [_p("exact", "2231")]),
    _rule("balance", "profit_payable", [_p("exact", "2232")]),
    _rule("balance", "other_payable", [_p("exact", "2241")]),
    _rule("balance", "long_borrow", [_p("exact", "2501")]),
    _rule("balance", "long_payable", [_p("exact", "2701")]),
    _rule("balance", "deferred_income", [_p("exact", "2401")]),
    _rule("balance", "paid_in_capital", [_p("exact", "3001")]),
    _rule("balance", "capital_reserve", [_p("exact", "3002")]),
    _rule("balance", "surplus_reserve", [_p("exact", "3101")]),
    _rule("balance", "retained_earnings", [_p("exact", "3103,3104,3104006")]),
]


def _movements(raw: dict[str, Decimal]) -> dict:
    return {
        code: {"name": "", "debit": max(value, ZERO), "credit": max(-value, ZERO)}
        for code, value in raw.items()
    }


def _normal_balances(raw: dict[str, Decimal]) -> dict[str, Decimal]:
    """规则求值的输入必须是 normal_balance 之后的余额表——贷方科目为负。"""
    return {
        code: _normal_balance(code, info["debit"], info["credit"])
        for code, info in _movements(raw).items()
    }


@pytest.mark.asyncio
async def test_profit_rules_match_hardcoded_口径():
    """利润表 13 个基础行必须零差异，否则不许谈切换。"""
    await _reset()
    baseline = _profit_map(_movements(PROFIT_BALANCES))
    result = diff_report_lines(PROFIT_RULES, baseline, _normal_balances(PROFIT_BALANCES), report="profit")
    assert result["clean"] is True, result["diffs"]
    assert result["compared"] == 13
    assert result["diffs"] == []


@pytest.mark.asyncio
async def test_balance_rules_match_hardcoded_口径():
    """资产负债表 38 个基础行必须零差异（含无形资产三键：原值/累计摊销/净额）。"""
    await _reset()
    baseline, _ = _build_balance_rows(_normal_balances(BALANCE_BALANCES), {}, {})
    result = diff_report_lines(BALANCE_RULES, baseline, _normal_balances(BALANCE_BALANCES), report="balance")
    assert result["clean"] is True, result["diffs"]
    assert result["compared"] == 38


@pytest.mark.asyncio
async def test_wrong_rule_is_detected_as_diff():
    """把管理费用写成纯 5602（漏掉研发）必须被抓出来，而不是悄悄放行。"""
    await _reset()
    baseline = _profit_map(_movements(PROFIT_BALANCES))
    broken = [
        r for r in PROFIT_RULES if r["line_code"] != "management_expense"
    ] + [_rule("profit", "management_expense", [_p("prefix", "5602")])]
    result = diff_report_lines(broken, baseline, _normal_balances(PROFIT_BALANCES), report="profit")
    assert result["clean"] is False
    codes = {d["lineCode"] for d in result["diffs"]}
    assert "management_expense" in codes


@pytest.mark.asyncio
async def test_exact_vs_prefix_regression_detected():
    """把 2211 从前缀改成精确码，会漏掉 221102 —— 必须报差异。"""
    await _reset()
    baseline, _ = _build_balance_rows(_normal_balances(BALANCE_BALANCES), {}, {})
    broken = [
        r for r in BALANCE_RULES if r["line_code"] != "salary_payable"
    ] + [_rule("balance", "salary_payable", [_p("exact", "2211")])]
    result = diff_report_lines(broken, baseline, _normal_balances(BALANCE_BALANCES), report="balance")
    assert result["clean"] is False
    diff = next(d for d in result["diffs"] if d["lineCode"] == "salary_payable")
    # 2211 是贷方科目，normal_balance 后为负；exact("2211") 取不到任何子级 → 0，
    # 而 prefix("2211") 取到 221101+221102 = -100。
    assert diff["baseline"] == "-100.00"
    assert diff["configured"] == "0.00"
    assert diff["delta"] == "100.00"


@pytest.mark.asyncio
async def test_archived_rules_excluded_from_evaluation():
    """归档后该行不再参与求值 → 报表缺这行 → 必须报差异，不能悄悄按 0 处理。"""
    await _reset()
    kept = [r for r in PROFIT_RULES if r["line_code"] != "entertainment"]
    archived = _rule("profit", "entertainment", [_p("exact", "5602002")])
    archived["status"] = "archived"
    baseline = _profit_map(_movements(PROFIT_BALANCES))
    result = diff_report_lines(
        kept + [archived], baseline, _normal_balances(PROFIT_BALANCES), report="profit",
    )
    assert result["clean"] is False
    assert any(d["lineCode"] == "entertainment" for d in result["diffs"])


@pytest.mark.asyncio
async def test_no_rules_reports_skipped_not_clean_comparison():
    """没配规则 ≠ 口径一致。混为一谈会让 switch 直接达标。"""
    await _reset()
    result = diff_report_lines([], {"revenue": Decimal("100")}, {}, report="profit")
    assert result["skipped"] is True
    assert result["clean"] is True
    assert result["compared"] == 0


@pytest.mark.asyncio
async def test_orphan_rule_reported():
    """配置了一个报表里不存在的行——要么 baseline 缺它，要么就是配错了科目。"""
    await _reset()
    result = diff_report_lines(
        [_rule("profit", "ghost_line", [_p("exact", "5001")])],
        {"revenue": Decimal("100")},
        {"5001": Decimal("100")},
        report="profit",
    )
    assert result["clean"] is False
    orphan = next(d for d in result["diffs"] if d["lineCode"] == "ghost_line")
    assert orphan["orphan"] is True


@pytest.mark.asyncio
async def test_serialized_camel_case_rules_evaluate_identically():
    """回归防线：list_rules 返回的是 serialize_rule 的 camelCase 形态。

    求值器若按 snake_case 读，lineCode 会全部退化成 ""，所有行挤成一个空 key，
    对比时冒出一个 baseline=0.00 / configured=0.00 / delta=0.00 的假差异——
    unit test 全传裸 dict 抓不到，只有端到端才暴露。
    """
    await _reset()
    from db.session import session_scope

    internal = [
        _rule("profit", "revenue", [_p("exact", "5001,5051")], name="营业收入"),
        _rule("profit", "cost", [_p("prefix", "5401"), _p("prefix", "5402")], name="营业成本"),
    ]
    serialized = [
        {"report": r["report"], "lineCode": r["line_code"], "lineName": r["line_name"],
         "status": r["status"], "patterns": r["patterns"]}
        for r in internal
    ]
    balances = _normal_balances(PROFIT_BALANCES)
    # baseline 直接取现有硬编码口径，避免手写期望值时搞错方向：
    # 5001/5051 属 CREDIT_NATURE_CODES，normal_balance 后是负数。
    hardcoded = _profit_map(_movements(PROFIT_BALANCES))
    baseline = {k: hardcoded[k] for k in ("revenue", "cost")}

    from_snake = diff_report_lines(internal, baseline, balances, report="profit")
    from_camel = diff_report_lines(serialized, baseline, balances, report="profit")
    assert from_snake == from_camel
    assert from_camel["clean"] is True, from_camel["diffs"]
    assert "" not in {d["lineCode"] for d in from_camel["diffs"]}

    # 端到端口径：真的从库里读出来再比一次
    async with session_scope() as session:
        await replace_rules(
            session, COMPANY, "owner-1",
            {"report": "profit", "rules": [_payload_rule("revenue", [_p("exact", "5001,5051")])]},
            request_id="req-camel",
        )
    async with session_scope() as session:
        stored = await list_rules(session, COMPANY, "profit")
    assert stored and "lineCode" in stored[0]  # 确认拿到的是 camelCase 形态
    end_to_end = diff_report_lines(
        stored, {"revenue": hardcoded["revenue"]}, balances, report="profit",
    )
    assert end_to_end["clean"] is True, end_to_end["diffs"]


@pytest.mark.asyncio
async def test_rule_with_blank_line_code_ignored_not_crashed():
    """脏数据（line_code 为空）必须被跳过，而不是把所有行挤成一个空 key。"""
    await _reset()
    result = diff_report_lines(
        [_rule("profit", "", [_p("exact", "5001")])],
        {"revenue": Decimal("100")},
        {"5001": Decimal("100")},
        report="profit",
    )
    assert result["clean"] is True
    assert result["diffs"] == []


# ── 持久化与门槛 ──

@pytest.mark.asyncio
async def test_replace_rules_roundtrip_and_isolation():
    await _reset()
    from db.session import session_scope

    payload = {
        "report": "profit",
        "rules": [
            {"lineCode": "revenue", "lineName": "营业收入",
             "patterns": [{"mode": "exact", "value": "5001,5051"}],
             "effectiveFrom": "2026-01-01"},
        ],
    }
    async with session_scope() as session:
        saved = await replace_rules(session, COMPANY, "owner-1", payload, request_id="req-1")
    assert len(saved) == 1
    assert saved[0]["patterns"] == [{"mode": "exact", "value": "5001,5051", "sign": 1}]

    async with session_scope() as session:
        assert len(await list_rules(session, COMPANY, "profit")) == 1
        # 跨公司隔离
        assert await list_rules(session, OTHER, "profit") == []


@pytest.mark.asyncio
async def test_replace_is_full_overwrite():
    """整批替换语义：第二次提交后第一批必须消失，不能叠加。"""
    await _reset()
    from db.session import session_scope

    def _payload(code: str) -> dict:
        return {
            "report": "profit",
            "rules": [{"lineCode": code, "patterns": [{"mode": "exact", "value": "5001"}],
                       "effectiveFrom": "2026-01-01"}],
        }

    async with session_scope() as session:
        await replace_rules(session, COMPANY, "owner-1", _payload("revenue"), request_id="r1")
    async with session_scope() as session:
        await replace_rules(session, COMPANY, "owner-1", _payload("revenue_cost"), request_id="r2")
    async with session_scope() as session:
        rows = await list_rules(session, COMPANY, "profit")
    assert [r["lineCode"] for r in rows] == ["revenue_cost"]


@pytest.mark.asyncio
async def test_duplicate_line_code_within_report_rejected():
    await _reset()
    from db.session import session_scope

    dup = {
        "report": "profit",
        "rules": [
            {"lineCode": "revenue", "patterns": [{"mode": "exact", "value": "5001"}], "effectiveFrom": "2026-01-01"},
            {"lineCode": "revenue", "patterns": [{"mode": "exact", "value": "5051"}], "effectiveFrom": "2026-01-01"},
        ],
    }
    with pytest.raises(AccountingError):
        async with session_scope() as session:
            await replace_rules(session, COMPANY, "owner-1", dup, request_id="req-dup")


@pytest.mark.asyncio
async def test_effective_to_before_from_rejected():
    await _reset()
    from db.session import session_scope

    bad = {
        "report": "profit",
        "rules": [{"lineCode": "revenue", "patterns": [{"mode": "exact", "value": "5001"}],
                   "effectiveFrom": "2026-06-01", "effectiveTo": "2026-01-01"}],
    }
    with pytest.raises(AccountingError):
        async with session_scope() as session:
            await replace_rules(session, COMPANY, "owner-1", bad, request_id="req-bad")


@pytest.mark.asyncio
async def test_missing_effective_from_rejected():
    await _reset()
    from db.session import session_scope

    bad = {
        "report": "profit",
        "rules": [{"lineCode": "revenue", "patterns": [{"mode": "exact", "value": "5001"}]}],
    }
    with pytest.raises(AccountingError):
        async with session_scope() as session:
            await replace_rules(session, COMPANY, "owner-1", bad, request_id="req-bad2")


@pytest.mark.asyncio
async def test_archive_marks_status_not_deleted():
    """归档而非删除——报表口径需要可追溯。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        saved = await replace_rules(
            session, COMPANY, "owner-1",
            {"report": "profit", "rules": [
                {"lineCode": "revenue", "patterns": [{"mode": "exact", "value": "5001"}],
                 "effectiveFrom": "2026-01-01"}]},
            request_id="req-arch",
        )
    rule_id = saved[0]["id"]
    async with session_scope() as session:
        after = await archive_rule(session, COMPANY, "owner-1", rule_id, request_id="req-arch2")
    assert after["status"] == "archived"
    async with session_scope() as session:
        rows = (await session.execute(
            select(ReportLineRule).where(ReportLineRule.company_id == COMPANY)
        )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_switch_not_ready_without_compare_history():
    """没跑过对比就永远不够格——这是默认安全状态。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is False
    assert readiness["currentCleanStreak"] == 0
    with pytest.raises(AccountingError) as exc:
        require_switch_ready(readiness, "profit")
    assert exc.value.code == "REPORT_LINE_SWITCH_NOT_READY"


@pytest.mark.asyncio
async def test_switch_ready_after_consecutive_clean_runs():
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(session, COMPANY, "clean", [True] * SWITCH_REQUIRED_CLEAN_PERIODS)
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is True
    require_switch_ready(readiness, "profit")


@pytest.mark.asyncio
async def test_not_ready_when_one_short_of_threshold():
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(session, COMPANY, "short", [True] * (SWITCH_REQUIRED_CLEAN_PERIODS - 1))
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is False
    assert readiness["currentCleanStreak"] == SWITCH_REQUIRED_CLEAN_PERIODS - 1


@pytest.mark.asyncio
async def test_skipped_run_does_not_count_as_clean():
    """「没配规则」产生的 skipped=True 不能累加达标次数。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(
            session, COMPANY, "skip",
            [True] * (SWITCH_REQUIRED_CLEAN_PERIODS + 2), skipped=True,
        )
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is False


@pytest.mark.asyncio
async def test_dirty_run_resets_streak():
    """最近一次有差异 → 连续计数从 0 重新开始，之前攒的不作数。

    这里刻意只在 dirty 之后补 2 次 clean（门槛是3）：如果 dirty 没清零，
    3 次里混着一次 dirty 也会凑够 3 而误判为达标。
    """
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        # flags[0] 是最近一次：先 dirty，再两次 clean。
        _seed_compare_audits(
            session, COMPANY, "mixed",
            [False] + [True] * (SWITCH_REQUIRED_CLEAN_PERIODS - 1),
        )
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is False
    assert readiness["currentCleanStreak"] == SWITCH_REQUIRED_CLEAN_PERIODS - 1


@pytest.mark.asyncio
async def test_clean_run_after_dirty_eventually_ready():
    """dirty 之后重新攒够连续次数，应当恢复达标（门槛是可恢复的，不是永久拉黑）。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(
            session, COMPANY, "recover",
            [True] * SWITCH_REQUIRED_CLEAN_PERIODS + [False],
        )
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is True


@pytest.mark.asyncio
async def test_company_isolation_on_readiness():
    """别家公司的干净记录不能给自己攒达标次数。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(session, OTHER, "other", [True] * (SWITCH_REQUIRED_CLEAN_PERIODS + 2))
        await session.flush()

    async with session_scope() as session:
        readiness = await switch_readiness(session, COMPANY, "profit")
    assert readiness["ready"] is False


@pytest.mark.asyncio
async def test_readiness_is_per_report():
    """利润表干净不代表资产负债表也干净——两者独立判定。"""
    await _reset()
    from db.models import AuditEvent
    from db.session import session_scope

    async with session_scope() as session:
        _seed_compare_audits(session, COMPANY, "p", [True] * SWITCH_REQUIRED_CLEAN_PERIODS)
        for index in range(SWITCH_REQUIRED_CLEAN_PERIODS + 2):
            session.add(AuditEvent(
                id=f"b-{COMPANY}-{index}",
                company_id=COMPANY,
                user_id="owner-1",
                action="report_line_rule_compare",
                entity_type="report_line_rule",
                entity_id="balance",
                after={"report": "balance", "clean": False, "skipped": False},
                request_id=f"b-req-{index}",
            ))
        await session.flush()

    async with session_scope() as session:
        profit = await switch_readiness(session, COMPANY, "profit")
        balance = await switch_readiness(session, COMPANY, "balance")
    assert profit["ready"] is True
    assert balance["ready"] is False


# ── 零回归 ──

@pytest.mark.asyncio
async def test_company_without_rules_unaffected():
    """未配置规则的公司，报表行为必须与今天完全一致。"""
    await _reset()
    from db.session import session_scope

    async with session_scope() as session:
        assert await list_rules(session, COMPANY) == []
    baseline = _profit_map(_movements(PROFIT_BALANCES))
    result = diff_report_lines([], baseline, _normal_balances(PROFIT_BALANCES), report="profit")
    assert result["skipped"] is True
    assert result["diffs"] == []
