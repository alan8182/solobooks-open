# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""报表项映射规则（P2）：把科目余额按声明口径归集到报表行，**双轨校验后**才允许切换。

为什么是声明式 pattern 数组，而不是表达式
-------------------------------------------
设计文档 §4.5 原稿是「一行一个 ``match_mode`` + ``account_prefix``」。对照现有硬编码
口径（``tax_filing_export_service._profit_map`` / ``_build_balance_rows``）发现单列
表达不了真实情况：

===========================  =========================================
报表行                        真实口径
===========================  =========================================
``cost``                      prefix(5401) + prefix(5402)
``research_expense``          prefix(4301) **−** prefix(4301002)
``management_expense``        prefix(5602) + research_expense
``cash``                      exact(1001,1002,1012001,1012)
===========================  =========================================

所以改为 ``patterns`` 数组：每个 pattern 自带 ``mode`` / ``value`` / ``sign``(+1/−1)，
一个报表行 = 一组 pattern 的带符号和。这样既能表达减项与多科目组合，**又没有任何
表达式求值器**，不存在注入面。

配置化的边界：只覆盖**基础行**，不覆盖派生行
---------------------------------------------
利润表的 ``operating_profit`` / ``total_profit`` / ``net_profit``，
资产负债表的 ``fixed_asset_book_value`` / ``*_total`` 全是从基础行算出来的，
不是科目归集。若把它们也配置化，就等于在数据库里建了一套公式引擎——
既要处理循环引用，又没法静态校验。**派生行一律留在代码里**，
``DERIVED_LINES`` 显式列出，配置化层碰到它们直接拒绝。

前缀粒度：本项目吃过两次亏
---------------------------
- ``_sum_prefix(balances, "4")`` 会把 4301 连同所有 4xxx 一并计入。
- ``_sum_codes(["2211"])`` 精确码求和又漏掉 221102 子级，应付职工薪酬静默归零。

两者方向相反，说明粒度必须由配置**显式承担**且服务端**强校验**，不能靠约定。
``match_mode='prefix'`` 时 ``value`` 不得短于 4 位，否则 422。

双轨校验：为什么不直接切
-----------------------
对外报表被 400+ 测试锁定，硬切是自杀。本模块**不接管**任何对外报表，只做三件事：

1. 用规则算一遍（``evaluate_report_lines``）；
2. 与现有硬编码口径逐行对比（``compare_report_lines``）；
3. 差异落审计，并给出「是否够格切换」的判定（连续 N 期零差异）。

**未达切换门槛时任何导出路径都不会走规则**——这是本模块唯一的安全保证。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    AccountingError,
    INVALID_REPORT_LINE_RULE,
    NOT_FOUND,
    REPORT_LINE_PATTERN_PREFIX_TOO_SHORT,
    REPORT_LINE_SWITCH_NOT_READY,
)
from core.money import ZERO, money
from db.models import AuditEvent, ReportLineRule
from services.accounting_core_service import WRITE_ROLES, record_audit, require_role


REPORT_KINDS = [
    {"value": "profit", "label": "利润表"},
    {"value": "balance", "label": "资产负债表"},
]

MATCH_MODES = [
    {"value": "prefix", "label": "科目前缀"},
    {"value": "exact", "label": "精确科目"},
    {"value": "range", "label": "科目区间"},
]

# 前缀最小粒度。4 位是「一级科目(4) + 子级(3)」体系的最小安全粒度；
# 短于此会把 4301 和所有 4xxx 一起吞掉。
MIN_PREFIX_LENGTH = 4

# 派生行：不能被配置化，配置化层遇到直接拒绝。
DERIVED_LINES = {
    "profit": {
        "operating_profit",
        "total_profit",
        "net_profit",
    },
    "balance": {
        "fixed_asset_book_value",
        # 无形资产与固定资产同构：净额行（1701−1702）和 1702 的消费别名
        # 都是代码算出来的，不作为可配置的报表行参与双轨对比。
        "intangible_asset_book_value",
        "intangible_amortization",
        "current_asset_total",
        "non_current_asset_total",
        "asset_total",
        "current_liability_total",
        "non_current_liability_total",
        "liability_total",
        "equity_total",
        "liability_equity_total",
    },
}

# 切换门槛：连续多少个期间零差异才允许切。
SWITCH_REQUIRED_CLEAN_PERIODS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _err(code: str, message: str, **context: Any) -> AccountingError:
    return AccountingError(code, message, 422, context or None)


def money_text(value: Decimal) -> str:
    return f"{money(value):.2f}"


# --------------------------------------------------------------------------
# pattern 解析与校验
# --------------------------------------------------------------------------

def _parse_patterns(raw: Any, *, report: str, line_code: str) -> list[dict[str, Any]]:
    """把外部传入的 patterns 规整成内部形态，任何不可表达的口径在此拒绝。"""
    items = raw if isinstance(raw, list) else []
    if not items:
        raise _err(
            INVALID_REPORT_LINE_RULE,
            "报表行必须至少声明一个 pattern",
            report=report,
            lineCode=line_code,
        )
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise _err(INVALID_REPORT_LINE_RULE, "pattern 必须是对象", index=index)
        mode = str(item.get("mode") or "").strip()
        if mode not in {m["value"] for m in MATCH_MODES}:
            raise _err(
                INVALID_REPORT_LINE_RULE,
                f"match mode 只能是 {'/'.join(m['value'] for m in MATCH_MODES)}",
                index=index,
                mode=mode,
            )
        value = str(item.get("value") or "").strip()
        if mode == "exact":
            codes = [part.strip() for part in value.split(",") if part.strip()]
            if not codes:
                raise _err(INVALID_REPORT_LINE_RULE, "exact pattern 必须指定至少一个科目", index=index)
            value = ",".join(codes)
        elif mode == "range":
            if "-" not in value:
                raise _err(INVALID_REPORT_LINE_RULE, "range pattern 形如 560101-560199", index=index)
            low, _, high = value.partition("-")
            low, high = low.strip(), high.strip()
            if not low.isdigit() or not high.isdigit():
                raise _err(INVALID_REPORT_LINE_RULE, "range pattern 的起止必须是数字科目码", index=index)
            if low > high:
                raise _err(INVALID_REPORT_LINE_RULE, "range pattern 起止顺序颠倒", index=index)
            value = f"{low}-{high}"
        else:  # prefix
            if not value.isdigit():
                raise _err(INVALID_REPORT_LINE_RULE, "prefix pattern 必须是数字科目码", index=index)
            if len(value) < MIN_PREFIX_LENGTH:
                # 这条校验是本模块存在的核心理由：短前缀会把 4301 连同所有 4xxx 吞掉。
                raise AccountingError(
                    REPORT_LINE_PATTERN_PREFIX_TOO_SHORT,
                    f"科目前缀不得短于 {MIN_PREFIX_LENGTH} 位",
                    422,
                    {"index": index, "value": value, "minLength": MIN_PREFIX_LENGTH},
                )
        sign = item.get("sign", 1)
        try:
            sign_value = int(sign)
        except (TypeError, ValueError):
            raise _err(INVALID_REPORT_LINE_RULE, "pattern sign 只能是 1 或 -1", index=index) from None
        if sign_value not in (1, -1):
            raise _err(INVALID_REPORT_LINE_RULE, "pattern sign 只能是 1 或 -1", index=index)
        out.append({"mode": mode, "value": value, "sign": sign_value})
    return out


def _validate_line(report: str, line_code: str) -> None:
    if line_code in DERIVED_LINES.get(report, set()):
        raise _err(
            INVALID_REPORT_LINE_RULE,
            f"{line_code} 是派生行，由基础行计算得出，不允许配置化",
            report=report,
            lineCode=line_code,
            derived=True,
        )


# --------------------------------------------------------------------------
# 规则求值
# --------------------------------------------------------------------------

def _match(pattern: Mapping[str, Any], code: str) -> bool:
    mode = pattern["mode"]
    value = pattern["value"]
    if mode == "prefix":
        return code.startswith(value)
    if mode == "exact":
        return code in value.split(",")
    low, _, high = value.partition("-")
    return low <= code <= high


def _rule_view(rule: Mapping[str, Any]) -> dict[str, Any]:
    """把规则规整成内部 snake_case 视图，**同时接受** ORM 行、序列化输出和裸 dict。

    这个函数存在的原因是一个真实踩过的坑：``list_rules`` 返回的是
    ``serialize_rule`` 的 camelCase 形态（``lineCode``），而求值器内部按
    snake_case 读（``line_code``）。直接对接会静默退化成空字符串 key——
    所有行挤成一个 ``""``，双轨对比报出一个 delta 为 0 的假差异。
    单元测试传裸 dict 所以没抓到，是端到端冒烟抓到的。

    两边都接受，就不依赖调用方传哪种形态。
    """
    return {
        "report": rule.get("report"),
        "line_code": rule.get("line_code") or rule.get("lineCode") or "",
        "line_name": rule.get("line_name") or rule.get("lineName") or "",
        "status": rule.get("status") or "active",
        "patterns": rule.get("patterns") or [],
    }


def evaluate_line(
    rule: Mapping[str, Any],
    balances: Mapping[str, Decimal],
) -> Decimal:
    """按规则把科目余额归集成一个报表行金额。

    ``balances`` 是**已经带符号**的科目余额表（贷方科目为负），口径与
    ``_current_balance_map`` 的输出一致——调用方必须先做正常的方向处理，
    本函数只负责「哪些科目、带什么符号、加到一起」。
    """
    total = ZERO
    for pattern in _rule_view(rule)["patterns"]:
        matched = ZERO
        for code, value in balances.items():
            if _match(pattern, code):
                matched += value
        total += Decimal(pattern.get("sign", 1)) * matched
    return money(total)


def evaluate_report_lines(
    rules: Iterable[Mapping[str, Any]],
    balances: Mapping[str, Decimal],
    *,
    report: str,
) -> dict[str, Decimal]:
    """逐行求值。派生行不在结果里——它们由调用方从基础行算出。"""
    out: dict[str, Decimal] = {}
    for raw in rules:
        rule = _rule_view(raw)
        if rule["status"] != "active":
            continue
        if rule["report"] != report:
            continue
        line_code = rule["line_code"]
        if not line_code:
            continue
        if line_code in DERIVED_LINES.get(report, set()):
            # 双保险：入库时已拒，但存量数据或手工插入仍可能绕过。
            continue
        out[line_code] = evaluate_line(rule, balances)
    return out


# --------------------------------------------------------------------------
# 双轨对比
# --------------------------------------------------------------------------

def diff_report_lines(
    rules: Iterable[Mapping[str, Any]],
    baseline: Mapping[str, Decimal],
    balances: Mapping[str, Decimal],
    *,
    report: str,
) -> dict[str, Any]:
    """规则口径 vs 现有硬编码口径，逐行对比。

    ``baseline`` 是现有报表算出的 ``{line_code: Decimal}``。只对比**基础行**：
    派生行两边算法不同（一个是配置 pattern，一个是代码加减），不可直接比。

    返回差异清单 + ``clean`` 标记。未配置任何规则时 ``skipped=True``——
    「没配规则」不等于「口径有差异」，不能混为一谈。
    """
    active = [
        view for view in (_rule_view(rule) for rule in rules)
        if view["status"] == "active" and view["report"] == report and view["line_code"]
    ]
    if not active:
        return {"report": report, "skipped": True, "clean": True, "diffs": [], "compared": 0}

    computed = evaluate_report_lines(active, balances, report=report)
    derived = DERIVED_LINES.get(report, set())
    names = {rule["line_code"]: rule["line_name"] for rule in active}
    diffs: list[dict[str, Any]] = []
    compared = 0
    for line_code, base_value in sorted(baseline.items()):
        if line_code in derived:
            continue
        compared += 1
        rule_value = computed.get(line_code, ZERO)
        base_money = money(base_value)
        rule_money = money(rule_value)
        if base_money != rule_money:
            diffs.append({
                "lineCode": line_code,
                "lineName": names.get(line_code, ""),
                "baseline": money_text(base_money),
                "configured": money_text(rule_money),
                "delta": money_text(money(rule_money - base_money)),
                "hasRule": line_code in computed,
            })
    missing = sorted(code for code in computed if code not in baseline)
    for line_code in missing:
        diffs.append({
            "lineCode": line_code,
            "lineName": names.get(line_code, ""),
            "baseline": money_text(ZERO),
            "configured": money_text(money(computed[line_code])),
            "delta": money_text(money(computed[line_code])),
            "hasRule": True,
            "orphan": True,
        })
    return {
        "report": report,
        "skipped": False,
        "clean": not diffs,
        "diffs": diffs,
        "compared": compared,
        "ruleCount": len(active),
    }


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def serialize_rule(row: ReportLineRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "report": row.report,
        "lineCode": row.line_code,
        "lineName": row.line_name,
        "patterns": row.patterns,
        "sign": row.sign,
        "status": row.status,
        "effectiveFrom": row.effective_from.isoformat() if row.effective_from else None,
        "effectiveTo": row.effective_to.isoformat() if row.effective_to else None,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_rules(
    session: AsyncSession,
    company_id: str,
    report: str | None = None,
) -> list[dict[str, Any]]:
    query = select(ReportLineRule).where(ReportLineRule.company_id == company_id)
    if report:
        query = query.where(ReportLineRule.report == report)
    rows = (await session.execute(query.order_by(
        ReportLineRule.report, ReportLineRule.line_code,
    ))).scalars().all()
    return [serialize_rule(row) for row in rows]


async def replace_rules(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> list[dict[str, Any]]:
    """整批替换报表项规则。语义与 aggregation 的 replace_rules 一致。

    **替换后不自动切换对外报表**：必须先跑 compare 确认零差异。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    items = list(payload.get("rules") or [])
    report = str(payload.get("report") or "").strip()
    if report not in {k["value"] for k in REPORT_KINDS}:
        raise _err(INVALID_REPORT_LINE_RULE, "report 只能是 profit 或 balance", report=report)

    before = await list_rules(session, company_id, report)
    await session.execute(delete(ReportLineRule).where(
        ReportLineRule.company_id == company_id,
        ReportLineRule.report == report,
    ))

    now = _utcnow()
    created: list[ReportLineRule] = []
    seen: set[str] = set()
    for item in items:
        line_code = str(item.get("lineCode") or "").strip()
        if not line_code:
            raise _err(INVALID_REPORT_LINE_RULE, "lineCode 不能为空")
        if line_code in seen:
            raise _err(INVALID_REPORT_LINE_RULE, "同一报表内 lineCode 重复", lineCode=line_code)
        seen.add(line_code)
        _validate_line(report, line_code)
        patterns = _parse_patterns(item.get("patterns"), report=report, line_code=line_code)
        sign = str(item.get("sign") or "net").strip()
        if sign not in {"debit", "credit", "net"}:
            raise _err(INVALID_REPORT_LINE_RULE, "sign 只能是 debit / credit / net", sign=sign)
        status = str(item.get("status") or "active").strip()
        if status not in {"active", "archived"}:
            raise _err(INVALID_REPORT_LINE_RULE, "status 只能是 active 或 archived", status=status)
        effective_from = _parse_date(item.get("effectiveFrom"), required=True)
        effective_to = _parse_date(item.get("effectiveTo"), required=False)
        if effective_to and effective_to < effective_from:
            raise _err(
                INVALID_REPORT_LINE_RULE,
                "生效截止日不得早于生效起始日",
                lineCode=line_code,
            )
        row = ReportLineRule(
            id=str(uuid4()),
            company_id=company_id,
            report=report,
            line_code=line_code,
            line_name=str(item.get("lineName") or "").strip() or line_code,
            patterns=patterns,
            sign=sign,
            status=status,
            effective_from=effective_from,
            effective_to=effective_to,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        created.append(row)
    await session.flush()
    after = [serialize_rule(row) for row in created]
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="report_line_rule_replace",
        entity_type="report_line_rule",
        entity_id=report,
        request_id=request_id,
        before={"rules": before},
        after={"rules": after},
    )
    return after


def _parse_date(value: Any, *, required: bool) -> date | None:
    if value in (None, ""):
        if required:
            raise _err(INVALID_REPORT_LINE_RULE, "effectiveFrom 必填")
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise _err(INVALID_REPORT_LINE_RULE, "日期格式必须为 YYYY-MM-DD", value=str(value)) from None


async def archive_rule(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    rule_id: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    """归档单条规则。不物理删除——报表口径需要可追溯。"""
    await require_role(session, company_id, user_id, WRITE_ROLES)
    row = (await session.execute(
        select(ReportLineRule).where(
            ReportLineRule.company_id == company_id,
            ReportLineRule.id == rule_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise AccountingError(NOT_FOUND, "报表项规则不存在", 404, {"id": rule_id})
    before = serialize_rule(row)
    row.status = "archived"
    row.updated_at = _utcnow()
    await session.flush()
    after = serialize_rule(row)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="report_line_rule_archive",
        entity_type="report_line_rule",
        entity_id=rule_id,
        request_id=request_id,
        before=before,
        after=after,
    )
    return after


async def switch_readiness(
    session: AsyncSession,
    company_id: str,
    report: str,
) -> dict[str, Any]:
    """判定是否够格把对外报表切到规则口径。

    **判定依据刻意保守**：读审计日志里最近 N 次 compare 的结果，要求连续
    ``SWITCH_REQUIRED_CLEAN_PERIODS`` 次且每次都非 skipped（配置了规则、
    真的比过），才允许切换。任何一次有差异或没配规则，计数清零。

    本函数**只回答「够不够格」，不执行切换**——切换是另一个显式动作，
    避免"判定通过"和"已经切换"混成一步。
    """
    events = (await session.execute(
        select(AuditEvent.company_id, AuditEvent.created_at, AuditEvent.after)
        .where(
            AuditEvent.company_id == company_id,
            AuditEvent.action == "report_line_rule_compare",
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(SWITCH_REQUIRED_CLEAN_PERIODS * 3)
    )).all()
    clean_streak = 0
    checked = 0
    latest: dict[str, Any] | None = None
    for _, _, payload in events:
        # after 是单个 dict（一次 compare 一条审计），不是列表。
        if not isinstance(payload, dict) or payload.get("report") != report:
            continue
        checked += 1
        if latest is None:
            latest = payload
        if payload.get("clean") and not payload.get("skipped"):
            clean_streak += 1
        else:
            clean_streak = 0
        if clean_streak >= SWITCH_REQUIRED_CLEAN_PERIODS:
            break
    ready = clean_streak >= SWITCH_REQUIRED_CLEAN_PERIODS
    return {
        "report": report,
        "ready": ready,
        "requiredCleanPeriods": SWITCH_REQUIRED_CLEAN_PERIODS,
        "currentCleanStreak": clean_streak,
        "checkedRuns": checked,
        "latest": latest,
    }


def require_switch_ready(readiness: Mapping[str, Any], report: str) -> None:
    if not readiness.get("ready"):
        raise AccountingError(
            REPORT_LINE_SWITCH_NOT_READY,
            f"口径未达切换门槛：需要连续 {readiness['requiredCleanPeriods']} 次双轨对比零差异",
            409,
            {
                "report": report,
                "currentCleanStreak": readiness.get("currentCleanStreak"),
                "requiredCleanPeriods": readiness.get("requiredCleanPeriods"),
            },
        )


# --------------------------------------------------------------------------
# 双轨对比编排：接真实报表数据跑一遍
# --------------------------------------------------------------------------

async def compare_with_baseline(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    report: str,
    period: str,
    *,
    period_type: str = "month",
    request_id: str,
) -> dict[str, Any]:
    """拉真实账，用现有硬编码口径和配置口径各算一遍，逐行对比并落审计。

    这里**刻意复用** ``build_statement_data_from_entries`` 同一套数据与算法，
    而不是自己再实现一遍取数。理由：对比的意义全在于「同一份数据、两种算法」，
    一旦取数路径不同，差异就分不清是配置错了还是取数错了，这个功能就废了。
    """
    from services.tax_filing_export_service import (  # 局部导入避免循环依赖
        build_statement_data_from_entries,
        parse_period,
    )
    from services.accounting_core_service import (
        ensure_tenant,
        posted_entries_for_reports,
    )
    from services.account_service import get_user_profile
    from services.opening_balance_service import get_opening_balances

    if report not in {k["value"] for k in REPORT_KINDS}:
        raise _err(INVALID_REPORT_LINE_RULE, "report 只能是 profit 或 balance", report=report)

    await ensure_tenant(session, company_id, user_id)
    period_range = parse_period(period, period_type)  # type: ignore[arg-type]
    profile = await get_user_profile(user_id)
    company = (profile or {}).get("current_company") or {}
    entries = await posted_entries_for_reports(session, company_id)
    opening_items = await get_opening_balances(
        company_id, period_range.start.year, session=session,
    )
    data = build_statement_data_from_entries(entries, opening_items, period_range, company)

    if report == "profit":
        baseline = dict(data.profit_current)
    else:
        baseline = dict(data.balance_current)

    # 规则求值需要「已带符号的科目余额」，与 _current_balance_map 口径一致：
    # 贷方科目为负。这里直接复用报表内部的 current/opening 计算结果。
    from services.tax_filing_export_service import (
        _account_movements,
        _current_balance_map,
        _opening_balance_map,
    )

    opening = _opening_balance_map(opening_items)
    movements_ytd = _account_movements(
        entries, period_range.fiscal_year_start, period_range.end, include_ytd_opening=True,
    )
    current = _current_balance_map(opening, movements_ytd)

    rules = await list_rules(session, company_id, report)
    result = diff_report_lines(rules, baseline, current, report=report)
    result["period"] = period
    result["periodType"] = period_type
    result["comparedAt"] = _utcnow().isoformat()

    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="report_line_rule_compare",
        entity_type="report_line_rule",
        entity_id=report,
        request_id=request_id,
        after={
            "report": report,
            "period": period,
            "clean": result["clean"],
            "skipped": result["skipped"],
            "compared": result["compared"],
            "diffCount": len(result["diffs"]),
        },
    )
    readiness = await switch_readiness(session, company_id, report)
    result["switchReadiness"] = readiness
    return result
