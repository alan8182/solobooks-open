# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""通用归集（管理口径）：把分散的凭证行按维度收拢，并支持分摊。

与研发费用归集的关系
--------------------
研发走的是**重写凭证行**模型（`rd_service.correct_unallocated_entry` 删掉 4301
待归集行、重建为 N 行研发明细科目行），因为加计扣除申报要求科目级证据。本模块
走**分配表**模型（`journal_line_allocations`），凭证行一字不动，分配可重算、可撤回、
可跨维度并存。两套模型物理隔离，服务层 union，不在同一张表里混写。

三条不可动摇的约定
------------------
1. **只收金额，不收比例。** ``RATE = Numeric(12,6)``，按比例分摊必然尾差
   （100 × 0.333333 × 3 = 99.99990），精确相等校验永远不成立。研发之所以敢写
   「分配合计必须等于待归集金额」，正是因为它收的是金额（``rd_service.py:381``）。
   ``allocation_rate`` 由后端按 ``amount / 行金额`` 回填，仅作留痕。
2. **默认取记账期间 ``period``。** 补记凭证的业务日期在上月，按 ``occurred_on``
   过滤会让它在自己的记账期间里消失。切 ``occurDate`` 只在管理口径报表开放。
3. **金额恒正、方向在字段。** ``journal_lines.amount`` 有 ``CHECK (amount > 0)``，
   所有聚合都必须自行带符号，范例 ``rd_service.py:334``。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    ALLOCATION_BASIS_REQUIRED,
    ALLOCATION_ON_REVERSED,
    ALLOCATION_RATE_OVERFLOW,
    ALLOCATION_TOTAL_MISMATCH,
    AccountingError,
    DUPLICATE_DIMENSION,
    DUPLICATE_DIMENSION_MEMBER,
    INVALID_ALLOCATION,
    INVALID_ALLOCATION_MEMBER,
    INVALID_DIMENSION,
    INVALID_DIMENSION_MEMBER,
    NOT_FOUND,
)
from core.money import ZERO, money, money_text, positive_money
from db.models import (
    AccountingDimension,
    AccountingDimensionMember,
    AggregationRule,
    JournalEntry,
    JournalLine,
    JournalLineAllocation,
)
from services.accounting_core_service import (
    WRITE_ROLES,
    ensure_tenant,
    get_journal,
    period_start_for,
    record_audit,
    require_role,
)


AGGREGATION_KINDS = [
    {"value": "project", "label": "项目"},
    {"value": "department", "label": "部门"},
    {"value": "customer", "label": "客户"},
    {"value": "supplier", "label": "供应商"},
    {"value": "custom", "label": "自定义"},
]

ALLOCATION_METHODS = [
    {"value": "direct", "label": "直接归属"},
    {"value": "timesheet", "label": "工时"},
    {"value": "usage", "label": "使用量"},
    {"value": "area", "label": "面积"},
    {"value": "quantity", "label": "数量"},
    {"value": "revenue", "label": "收入占比"},
    {"value": "headcount", "label": "人数"},
    {"value": "other", "label": "其他"},
]

MEMBER_STATUSES = [
    {"value": "draft", "label": "草稿"},
    {"value": "active", "label": "启用"},
    {"value": "completed", "label": "已完成"},
    {"value": "archived", "label": "已归档"},
]

# 归集范围的默认口径：损益类科目。不设"必须归集"的全局开关——范围由调用方
# 按查询参数给定，默认损益类，避免把银行流水、往来科目全拖进待归集清单。
DEFAULT_SCOPE_PREFIXES = ("5", "6")

PENDING_STATUSES = ("draft", "pending_review", "posted")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _signed(amount: Decimal, direction: str) -> Decimal:
    """金额恒正、方向在字段，所有聚合自行带符号。"""
    return amount if direction == "debit" else -amount


# ── 序列化 ──────────────────────────────────────────────────────────────────


def serialize_dimension(row: AccountingDimension) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "kind": row.kind,
        "status": row.status,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def serialize_member(row: AccountingDimensionMember) -> dict[str, Any]:
    return {
        "id": row.id,
        "dimensionId": row.dimension_id,
        "code": row.code,
        "name": row.name,
        "startDate": row.start_date,
        "endDate": row.end_date,
        "status": row.status,
        "metadata": row.metadata_json or {},
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def serialize_allocation(row: JournalLineAllocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "journalLineId": row.journal_line_id,
        "dimensionId": row.dimension_id,
        "memberId": row.member_id,
        "amount": money_text(row.amount),
        "allocationRate": str(row.allocation_rate),
        "allocationMethod": row.allocation_method,
        "allocationBasis": row.allocation_basis,
        "source": row.source,
        "ruleId": row.rule_id,
    }


def serialize_rule(row: AggregationRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "dimensionId": row.dimension_id,
        "memberId": row.member_id,
        "priority": row.priority,
        "accountPrefix": row.account_prefix,
        "direction": row.direction,
        "keyword": row.keyword,
        "counterparty": row.counterparty,
        "rate": str(row.rate),
        "method": row.method,
        "basis": row.basis,
        "status": row.status,
    }


# ── 维度 / 归集对象 ──────────────────────────────────────────────────────────


async def _require_dimension(
    session: AsyncSession, company_id: str, dimension_id: str, *, allow_archived: bool = False
) -> AccountingDimension:
    row = await session.get(AccountingDimension, dimension_id)
    if row is None or row.company_id != company_id:
        raise AccountingError(INVALID_DIMENSION, "归集维度不存在", 404)
    if not allow_archived and row.status != "active":
        raise AccountingError(INVALID_DIMENSION, "归集维度已归档", 409)
    return row


async def list_dimensions(
    session: AsyncSession, company_id: str, user_id: str, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    query = select(AccountingDimension).where(AccountingDimension.company_id == company_id)
    if not include_archived:
        query = query.where(AccountingDimension.status == "active")
    rows = (await session.execute(query.order_by(AccountingDimension.created_at))).scalars().all()
    return [serialize_dimension(row) for row in rows]


async def create_dimension(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    kind = str(payload.get("kind") or "custom").strip()
    if not code or not name:
        raise AccountingError(INVALID_DIMENSION, "维度编码和名称不能为空", 422)
    if kind not in {item["value"] for item in AGGREGATION_KINDS}:
        raise AccountingError(INVALID_DIMENSION, "维度类型无效", 422)
    exists = (await session.execute(
        select(AccountingDimension.id).where(
            AccountingDimension.company_id == company_id, AccountingDimension.code == code,
        )
    )).scalar_one_or_none()
    if exists:
        raise AccountingError(DUPLICATE_DIMENSION, "该维度编码已存在", 409)

    now = _utcnow()
    row = AccountingDimension(
        id=str(uuid4()),
        company_id=company_id,
        code=code,
        name=name,
        kind=kind,
        status="active",
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="create_aggregation_dimension",
        entity_type="accounting_dimension", entity_id=row.id, request_id=request_id,
        after=serialize_dimension(row),
    )
    return serialize_dimension(row)


async def update_dimension(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    dimension_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    row = await _require_dimension(session, company_id, dimension_id, allow_archived=True)
    before = serialize_dimension(row)
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or "").strip()
    if name:
        row.name = name
    if status:
        if status not in {"active", "archived"}:
            raise AccountingError(INVALID_DIMENSION, "维度状态无效", 422)
        row.status = status
    row.updated_at = _utcnow()
    await session.flush()
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="update_aggregation_dimension",
        entity_type="accounting_dimension", entity_id=row.id, request_id=request_id,
        before=before, after=serialize_dimension(row),
    )
    return serialize_dimension(row)


async def list_members(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    dimension_id: str,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    await _require_dimension(session, company_id, dimension_id, allow_archived=True)
    query = select(AccountingDimensionMember).where(
        AccountingDimensionMember.company_id == company_id,
        AccountingDimensionMember.dimension_id == dimension_id,
    )
    if not include_archived:
        query = query.where(AccountingDimensionMember.status != "archived")
    rows = (await session.execute(
        query.order_by(AccountingDimensionMember.code)
    )).scalars().all()
    return [serialize_member(row) for row in rows]


async def create_member(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    dimension_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    await _require_dimension(session, company_id, dimension_id)
    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not code or not name:
        raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象编码和名称不能为空", 422)
    try:
        start_date = period_start_for(payload.get("startDate"))
    except Exception as exc:
        raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象启用日期无效", 422) from exc
    end_date_raw = str(payload.get("endDate") or "").strip()
    end_date = None
    if end_date_raw:
        try:
            end_date = date.fromisoformat(end_date_raw[:10])
        except ValueError as exc:
            raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象失效日期无效", 422) from exc
        if end_date < start_date:
            raise AccountingError(INVALID_DIMENSION_MEMBER, "失效日期不能早于启用日期", 422)
    status = str(payload.get("status") or "active").strip()
    if status not in {"draft", "active", "completed", "archived"}:
        raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象状态无效", 422)

    exists = (await session.execute(
        select(AccountingDimensionMember.id).where(
            AccountingDimensionMember.company_id == company_id,
            AccountingDimensionMember.dimension_id == dimension_id,
            AccountingDimensionMember.code == code,
        )
    )).scalar_one_or_none()
    if exists:
        raise AccountingError(DUPLICATE_DIMENSION_MEMBER, "该维度下的归集对象编码已存在", 409)

    now = _utcnow()
    row = AccountingDimensionMember(
        id=str(uuid4()),
        company_id=company_id,
        dimension_id=dimension_id,
        code=code,
        name=name,
        start_date=start_date,
        end_date=end_date,
        status=status,
        metadata_json=dict(payload.get("metadata") or {}),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="create_aggregation_member",
        entity_type="accounting_dimension_member", entity_id=row.id, request_id=request_id,
        after=serialize_member(row),
    )
    return serialize_member(row)


async def update_member(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    member_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, WRITE_ROLES)
    row = await session.get(AccountingDimensionMember, member_id)
    if row is None or row.company_id != company_id:
        raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象不存在", 404)
    before = serialize_member(row)
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or "").strip()
    end_date_raw = str(payload.get("endDate") or "").strip()
    if name:
        row.name = name
    if status:
        if status not in {"draft", "active", "completed", "archived"}:
            raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象状态无效", 422)
        row.status = status
    if end_date_raw:
        try:
            end_date = date.fromisoformat(end_date_raw[:10])
        except ValueError as exc:
            raise AccountingError(INVALID_DIMENSION_MEMBER, "归集对象失效日期无效", 422) from exc
        if end_date < row.start_date:
            raise AccountingError(INVALID_DIMENSION_MEMBER, "失效日期不能早于启用日期", 422)
        row.end_date = end_date
    row.updated_at = _utcnow()
    await session.flush()
    # 归档只禁新增分配，不动历史分配记录：报表永远按记录取数，
    # 归档等于毁数据。这里只改成员自身状态。
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="update_aggregation_member",
        entity_type="accounting_dimension_member", entity_id=row.id, request_id=request_id,
        before=before, after=serialize_member(row),
    )
    return serialize_member(row)


# ── 自动归集规则 ────────────────────────────────────────────────────────────


async def list_rules(
    session: AsyncSession, company_id: str, user_id: str, dimension_id: str
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    await _require_dimension(session, company_id, dimension_id, allow_archived=True)
    rows = (await session.execute(
        select(AggregationRule).where(
            AggregationRule.company_id == company_id,
            AggregationRule.dimension_id == dimension_id,
            AggregationRule.status == "active",
        ).order_by(AggregationRule.priority, AggregationRule.created_at)
    )).scalars().all()
    return [serialize_rule(row) for row in rows]


async def replace_rules(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    dimension_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> list[dict[str, Any]]:
    """整批替换某维度的规则。对齐既有 replace_salary_allocations 的风格。

    规则只影响后续 apply-rules，不会自动改动已经存在的分配记录。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    await _require_dimension(session, company_id, dimension_id)
    rules = list(payload.get("rules") or [])
    member_ids = {str(item.get("memberId") or "").strip() for item in rules}
    members = (await session.execute(
        select(AccountingDimensionMember).where(
            AccountingDimensionMember.company_id == company_id,
            AccountingDimensionMember.id.in_(member_ids),
        )
    )).scalars().all() if member_ids else []
    member_map = {row.id: row for row in members}
    invalid = sorted(member_ids - set(member_map))
    if invalid:
        raise AccountingError(INVALID_ALLOCATION_MEMBER, "规则引用了不存在的归集对象", 422, {"memberIds": invalid})

    before = [serialize_rule(row) for row in (await session.execute(
        select(AggregationRule).where(
            AggregationRule.company_id == company_id,
            AggregationRule.dimension_id == dimension_id,
        )
    )).scalars().all()]

    await session.execute(delete(AggregationRule).where(
        AggregationRule.company_id == company_id,
        AggregationRule.dimension_id == dimension_id,
    ))

    now = _utcnow()
    created: list[AggregationRule] = []
    for item in rules:
        member_id = str(item.get("memberId") or "").strip()
        direction = str(item.get("direction") or "").strip() or None
        if direction and direction not in {"debit", "credit"}:
            raise AccountingError(INVALID_ALLOCATION, "规则方向只能是借方或贷方", 422)
        method = str(item.get("method") or "direct").strip()
        if method not in {m["value"] for m in ALLOCATION_METHODS}:
            raise AccountingError(INVALID_ALLOCATION, "分摊方法无效", 422)
        raw_rate = item.get("rate")
        rule_rate = ZERO + Decimal("1") if raw_rate in (None, "") else money(raw_rate)
        if rule_rate <= ZERO or rule_rate > 1:
            raise AccountingError(INVALID_ALLOCATION, "规则比例必须大于 0 且不超过 100%", 422)
        row = AggregationRule(
            id=str(uuid4()),
            company_id=company_id,
            dimension_id=dimension_id,
            member_id=member_id,
            priority=int(item.get("priority") or 100),
            account_prefix=str(item.get("accountPrefix") or "").strip() or None,
            direction=direction,
            keyword=str(item.get("keyword") or "").strip() or None,
            counterparty=str(item.get("counterparty") or "").strip() or None,
            rate=rule_rate,
            method=method,
            basis=str(item.get("basis") or "").strip() or None,
            status="active",
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        created.append(row)
    await session.flush()
    after = [serialize_rule(row) for row in created]
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="replace_aggregation_rules",
        entity_type="accounting_dimension", entity_id=dimension_id, request_id=request_id,
        before={"rules": before}, after={"rules": after},
    )
    return after


def _rule_matches(rule: AggregationRule, line: JournalLine, summary: str) -> bool:
    if rule.account_prefix and not line.account_code.startswith(rule.account_prefix):
        return False
    if rule.direction and line.direction != rule.direction:
        return False
    if rule.keyword and rule.keyword not in summary:
        return False
    if rule.counterparty and rule.counterparty not in summary:
        return False
    return True


# ── 分配 ────────────────────────────────────────────────────────────────────


async def _load_allocatable_members(
    session: AsyncSession, company_id: str, dimension_id: str
) -> dict[str, AccountingDimensionMember]:
    rows = (await session.execute(
        select(AccountingDimensionMember).where(
            AccountingDimensionMember.company_id == company_id,
            AccountingDimensionMember.dimension_id == dimension_id,
        )
    )).scalars().all()
    return {row.id: row for row in rows}


def _validate_member(
    member_id: str,
    member_map: dict[str, AccountingDimensionMember],
    period: date,
) -> AccountingDimensionMember:
    if member_id not in member_map:
        raise AccountingError(
            INVALID_ALLOCATION_MEMBER, "归集对象不存在或不属于该维度", 422, {"memberIds": [member_id]},
        )
    member = member_map[member_id]
    if member.status != "active":
        raise AccountingError(
            INVALID_ALLOCATION_MEMBER, "归集对象未启用或已归档，不能新增分配", 422, {"memberIds": [member_id]},
        )
    if member.start_date > period or (member.end_date is not None and member.end_date < period):
        raise AccountingError(
            INVALID_ALLOCATION_MEMBER, "归集对象在该期间无效", 422, {"memberIds": [member_id]},
        )
    return member


async def replace_line_allocations(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    journal_line_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    """整行替换某凭证行在某一维度下的全部分配（PUT 语义）。

    - 只收金额；allocation_rate 由后端按 amount / 行金额 回填，仅作留痕。
    - complete=true 时合计必须精确等于行金额；false（默认）允许停在「部分归集」。
    - 分配前锁 journal_lines 行，避免两个并发请求各自通过合计校验、合计翻倍。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    dimension_id = str(payload.get("dimensionId") or "").strip()
    raw_allocations: list[dict[str, Any]] = list(payload.get("allocations") or [])
    complete = bool(payload.get("complete"))

    line = (await session.execute(
        select(JournalLine).where(
            JournalLine.id == journal_line_id, JournalLine.company_id == company_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if line is None:
        raise AccountingError(NOT_FOUND, "凭证行不存在", 404)

    entry = await get_journal(session, line.journal_entry_id, company_id=company_id, lock=True)
    if entry.entry_type == "reversal" or entry.status == "reversed":
        raise AccountingError(ALLOCATION_ON_REVERSED, "冲销凭证或已冲销凭证不能归集", 409)
    if entry.status not in PENDING_STATUSES:
        raise AccountingError(ALLOCATION_ON_REVERSED, "该凭证状态不允许归集", 409)

    await _require_dimension(session, company_id, dimension_id)
    period = entry.period_start
    member_map = await _load_allocatable_members(session, company_id, dimension_id)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = ZERO
    rate_total = ZERO
    for item in raw_allocations:
        member_id = str(item.get("memberId") or "").strip()
        _validate_member(member_id, member_map, period)
        if member_id in seen:
            raise AccountingError(
                INVALID_ALLOCATION, "同一归集对象不能重复分配", 422, {"memberIds": [member_id]},
            )
        seen.add(member_id)
        try:
            amount = positive_money(item.get("amount"))
        except Exception as exc:
            raise AccountingError(INVALID_ALLOCATION, "分配金额必须大于零", 422) from exc
        if amount > line.amount:
            raise AccountingError(
                INVALID_ALLOCATION,
                "分配金额不能超过凭证行金额",
                422,
                {"lineAmount": money_text(line.amount), "amount": money_text(amount)},
            )
        method = str(item.get("allocationMethod") or "direct").strip()
        if method not in {m["value"] for m in ALLOCATION_METHODS}:
            raise AccountingError(INVALID_ALLOCATION, "分摊方法无效", 422)
        basis = str(item.get("allocationBasis") or "").strip() or None
        if len(raw_allocations) > 1 and not basis:
            raise AccountingError(
                ALLOCATION_BASIS_REQUIRED, "拆给多个归集对象时必须填写每条分摊依据", 422,
            )
        total += amount
        rate_total += Decimal(money_text(money(amount / line.amount)))
        normalized.append({
            "memberId": member_id,
            "amount": amount,
            "allocationMethod": method,
            "allocationBasis": basis,
        })

    if rate_total > 1:
        raise AccountingError(
            ALLOCATION_RATE_OVERFLOW,
            "同一维度内该凭证行的分配比例合计不能超过 100%",
            422,
            {"rateTotal": str(rate_total)},
        )
    if complete and total != line.amount:
        raise AccountingError(
            ALLOCATION_TOTAL_MISMATCH,
            "标记为完成归集时，分配金额合计必须等于凭证行金额",
            422,
            {"expected": money_text(line.amount), "actual": money_text(total)},
        )

    before = [serialize_allocation(row) for row in (await session.execute(
        select(JournalLineAllocation).where(
            JournalLineAllocation.company_id == company_id,
            JournalLineAllocation.journal_line_id == journal_line_id,
            JournalLineAllocation.dimension_id == dimension_id,
        )
    )).scalars().all()]

    await session.execute(delete(JournalLineAllocation).where(
        JournalLineAllocation.company_id == company_id,
        JournalLineAllocation.journal_line_id == journal_line_id,
        JournalLineAllocation.dimension_id == dimension_id,
    ))

    now = _utcnow()
    created: list[JournalLineAllocation] = []
    for item in normalized:
        row = JournalLineAllocation(
            id=str(uuid4()),
            company_id=company_id,
            journal_line_id=journal_line_id,
            dimension_id=dimension_id,
            member_id=item["memberId"],
            amount=item["amount"],
            allocation_rate=Decimal(money_text(money(item["amount"] / line.amount))),
            allocation_method=item["allocationMethod"],
            allocation_basis=item["allocationBasis"],
            source="manual",
            rule_id=None,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        created.append(row)
    await session.flush()

    after = [serialize_allocation(row) for row in created]
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="replace_line_allocations",
        entity_type="journal_line_allocation", entity_id=journal_line_id, request_id=request_id,
        before={"dimensionId": dimension_id, "allocations": before},
        after={"dimensionId": dimension_id, "allocations": after},
    )
    return {
        "journalLineId": journal_line_id,
        "dimensionId": dimension_id,
        "lineAmount": money_text(line.amount),
        "allocatedAmount": money_text(total),
        "remainingAmount": money_text(line.amount - total),
        "state": _line_state(line.amount, total),
        "allocations": after,
    }


def _line_state(line_amount: Decimal, allocated: Decimal) -> str:
    if allocated <= ZERO:
        return "unallocated"
    if allocated < line_amount:
        return "partial"
    return "allocated"


async def clear_line_allocations(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    journal_line_id: str,
    dimension_id: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    """撤回该凭证行在某维度下的全部分配，使其回到待归集清单。"""
    await require_role(session, company_id, user_id, WRITE_ROLES)
    line = (await session.execute(
        select(JournalLine).where(
            JournalLine.id == journal_line_id, JournalLine.company_id == company_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if line is None:
        raise AccountingError(NOT_FOUND, "凭证行不存在", 404)

    before = [serialize_allocation(row) for row in (await session.execute(
        select(JournalLineAllocation).where(
            JournalLineAllocation.company_id == company_id,
            JournalLineAllocation.journal_line_id == journal_line_id,
            JournalLineAllocation.dimension_id == dimension_id,
        )
    )).scalars().all()]
    if not before:
        raise AccountingError(NOT_FOUND, "该凭证行在此维度下没有归集记录", 404)

    await session.execute(delete(JournalLineAllocation).where(
        JournalLineAllocation.company_id == company_id,
        JournalLineAllocation.journal_line_id == journal_line_id,
        JournalLineAllocation.dimension_id == dimension_id,
    ))
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="clear_line_allocations",
        entity_type="journal_line_allocation", entity_id=journal_line_id, request_id=request_id,
        before={"dimensionId": dimension_id, "allocations": before},
        after={"dimensionId": dimension_id, "allocations": []},
    )
    return {
        "journalLineId": journal_line_id,
        "dimensionId": dimension_id,
        "lineAmount": money_text(line.amount),
        "allocatedAmount": money_text(ZERO),
        "remainingAmount": money_text(line.amount),
        "state": "unallocated",
        "allocations": [],
    }


# ── 待归集清单 ──────────────────────────────────────────────────────────────


def _scope_filter(query, model, prefixes: Sequence[str]):
    """按科目前缀圈定归集范围。多个前缀是「或」关系——写成 AND 会让
    ``startswith("5") AND startswith("6")`` 永远匹配不到任何一行。"""
    conditions = [model.account_code.startswith(prefix) for prefix in prefixes]
    return query.where(or_(*conditions)) if conditions else query


async def list_pending_lines(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
    dimension_id: str | None = None,
    prefixes: Sequence[str] | None = None,
    basis: str = "period",
    state_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """待归集清单：范围内没有归集记录、或只归了一部分的凭证行。

    清单口径含草稿（draft/pending_review/posted），让用户能提前归集；
    报表口径只取 posted，两者刻意不同，否则归集金额会和总账余额对不上。
    """
    await ensure_tenant(session, company_id, user_id)
    scope = tuple(prefixes) if prefixes else DEFAULT_SCOPE_PREFIXES
    query = select(JournalLine, JournalEntry).join(
        JournalEntry, JournalEntry.id == JournalLine.journal_entry_id
    ).where(
        JournalLine.company_id == company_id,
        JournalEntry.company_id == company_id,
        JournalEntry.status.in_(PENDING_STATUSES),
        JournalEntry.entry_type != "reversal",
    )
    if basis == "occurDate":
        query = query.where(JournalEntry.occurred_on >= start, JournalEntry.occurred_on <= end) if start and end \
            else query.where(JournalEntry.occurred_on >= (start or date.min), JournalEntry.occurred_on <= (end or date.max))
    else:
        query = query.where(JournalEntry.period_start >= start, JournalEntry.period_start <= end) if start and end \
            else query.where(JournalEntry.period_start >= (start or date.min), JournalEntry.period_start <= (end or date.max))
    query = _scope_filter(query, JournalLine, scope)
    query = query.order_by(JournalEntry.period_start.desc(), JournalEntry.voucher_no.desc(), JournalLine.line_no)

    rows = (await session.execute(query)).all()
    alloc_query = select(JournalLineAllocation).where(
        JournalLineAllocation.company_id == company_id
    )
    if dimension_id:
        alloc_query = alloc_query.where(JournalLineAllocation.dimension_id == dimension_id)
    allocations = (await session.execute(alloc_query)).scalars().all()
    alloc_by_line: dict[str, list[JournalLineAllocation]] = defaultdict(list)
    for alloc in allocations:
        alloc_by_line[alloc.journal_line_id].append(alloc)

    items: list[dict[str, Any]] = []
    totals = {"lineAmount": ZERO, "allocatedAmount": ZERO, "remainingAmount": ZERO}
    for line, entry in rows:
        signed = _signed(line.amount, line.direction)
        line_allocs = alloc_by_line.get(line.id, [])
        allocated = sum((a.amount for a in line_allocs), ZERO)
        state = _line_state(line.amount, allocated)
        if state == "allocated" and state_filter != "partial" and state_filter != "unallocated":
            if state_filter is None and dimension_id:
                continue
        if state_filter and state != state_filter:
            continue
        totals["lineAmount"] += signed
        totals["allocatedAmount"] += _signed(allocated, line.direction)
        totals["remainingAmount"] += _signed(line.amount - allocated, line.direction)
        items.append({
            "entryId": entry.id,
            "lineId": line.id,
            "voucherNo": entry.voucher_no,
            "summary": entry.summary,
            "status": entry.status,
            "occurredOn": entry.occurred_on,
            "periodStart": entry.period_start,
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "direction": line.direction,
            "amount": money_text(signed),
            "allocatedAmount": money_text(_signed(allocated, line.direction)),
            "remainingAmount": money_text(_signed(line.amount - allocated, line.direction)),
            "state": state,
            "allocations": [serialize_allocation(a) for a in line_allocs],
        })

    total_rows = len(items)
    page = items[offset: offset + limit]
    return {
        "items": page,
        "total": total_rows,
        "limit": limit,
        "offset": offset,
        "basis": basis,
        "state": state_filter or "all",
        "totals": {
            "lineAmount": money_text(totals["lineAmount"]),
            "allocatedAmount": money_text(totals["allocatedAmount"]),
            "remainingAmount": money_text(totals["remainingAmount"]),
        },
    }


# ── 归集报表 ────────────────────────────────────────────────────────────────


async def aggregation_report(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    dimension_id: str,
    *,
    start: date,
    end: date,
    basis: str = "period",
    prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """按维度出归集报表，三态（已归集 / 部分归集 / 未归集）必须全部可见。

    口径：只取 status='posted' 的凭证行，才能与总账余额勾稽；默认按记账期间
    period_start 聚合，切 occurDate 仅在管理口径报表开放。
    """
    await ensure_tenant(session, company_id, user_id)
    dimension = await _require_dimension(session, company_id, dimension_id, allow_archived=True)
    scope = tuple(prefixes) if prefixes else DEFAULT_SCOPE_PREFIXES

    date_column = JournalEntry.occurred_on if basis == "occurDate" else JournalEntry.period_start
    query = select(JournalLine, JournalEntry).join(
        JournalEntry, JournalEntry.id == JournalLine.journal_entry_id
    ).where(
        JournalLine.company_id == company_id,
        JournalEntry.company_id == company_id,
        JournalEntry.status == "posted",
        JournalEntry.entry_type != "reversal",
        date_column >= start,
        date_column <= end,
    )
    query = _scope_filter(query, JournalLine, scope)
    rows = (await session.execute(query)).all()

    allocations = (await session.execute(
        select(JournalLineAllocation).where(
            JournalLineAllocation.company_id == company_id,
            JournalLineAllocation.dimension_id == dimension_id,
        )
    )).scalars().all()
    alloc_by_line: dict[str, list[JournalLineAllocation]] = defaultdict(list)
    for alloc in allocations:
        alloc_by_line[alloc.journal_line_id].append(alloc)

    member_ids = {alloc.member_id for alloc in allocations}
    members = (await session.execute(
        select(AccountingDimensionMember).where(
            AccountingDimensionMember.company_id == company_id,
            AccountingDimensionMember.id.in_(member_ids),
        )
    )).scalars().all() if member_ids else []
    member_map = {row.id: row for row in members}

    buckets: dict[str, dict[str, Any]] = {}
    line_total = ZERO
    allocated_total = ZERO
    unassigned_total = ZERO
    allocated_lines = 0
    partial_lines = 0
    unallocated_lines = 0

    for line, _entry in rows:
        signed = _signed(line.amount, line.direction)
        line_total += signed
        line_allocs = alloc_by_line.get(line.id, [])
        allocated = sum((a.amount for a in line_allocs), ZERO)
        state = _line_state(line.amount, allocated)
        if state == "allocated":
            allocated_lines += 1
        elif state == "partial":
            partial_lines += 1
        else:
            unallocated_lines += 1
        if state != "allocated":
            unassigned_total += _signed(line.amount - allocated, line.direction)
        for alloc in line_allocs:
            allocated_total += _signed(alloc.amount, line.direction)
            bucket = buckets.setdefault(alloc.member_id, {
                "memberId": alloc.member_id,
                "code": member_map[alloc.member_id].code if alloc.member_id in member_map else "",
                "name": member_map[alloc.member_id].name if alloc.member_id in member_map else "",
                "status": member_map[alloc.member_id].status if alloc.member_id in member_map else "",
                "allocatedAmount": ZERO,
                "allocatedLineCount": 0,
            })
            bucket["allocatedAmount"] += _signed(alloc.amount, line.direction)
            bucket["allocatedLineCount"] += 1

    items = []
    for bucket in buckets.values():
        items.append({
            **bucket,
            "allocatedAmount": money_text(bucket["allocatedAmount"]),
        })
    items.sort(key=lambda x: abs(float(x["allocatedAmount"])), reverse=True)

    return {
        "dimensionId": dimension.id,
        "dimensionCode": dimension.code,
        "dimensionName": dimension.name,
        "basis": basis,
        "start": start,
        "end": end,
        "scopePrefixes": list(scope),
        "items": items,
        "totals": {
            "lineAmount": money_text(line_total),
            "allocatedAmount": money_text(allocated_total),
            "unassignedAmount": money_text(unassigned_total),
            "balanced": line_total == allocated_total + unassigned_total,
            "allocatedLineCount": allocated_lines,
            "partialLineCount": partial_lines,
            "unallocatedLineCount": unallocated_lines,
            "lineCount": len(rows),
        },
    }


# ── 规则批量归集 ────────────────────────────────────────────────────────────


async def apply_rules(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    """按规则给范围内凭证行自动打维度标签。

    冲突约定（设计文档 §4.4）：
    - 绝不覆盖 source='manual' 的记录，命中已有人工分配的行**整行跳过**；
    - 同一行只由 priority 最小的第一条命中规则分配；
    - 规则 rate<1 产生的尾差不自动补齐，该行停在「部分归集」由人工处理。
    """
    await require_role(session, company_id, user_id, WRITE_ROLES)
    dimension_id = str(payload.get("dimensionId") or "").strip()
    start = period_start_for(payload.get("start"))
    end = period_start_for(payload.get("end"))
    basis = str(payload.get("basis") or "period").strip()
    scope = tuple(payload.get("scopePrefixes") or DEFAULT_SCOPE_PREFIXES)

    await _require_dimension(session, company_id, dimension_id)
    rules = (await session.execute(
        select(AggregationRule).where(
            AggregationRule.company_id == company_id,
            AggregationRule.dimension_id == dimension_id,
            AggregationRule.status == "active",
        ).order_by(AggregationRule.priority, AggregationRule.created_at)
    )).scalars().all()
    if not rules:
        return {"allocated": 0, "skipped": 0, "unmatched": 0, "rules": 0}

    date_column = JournalEntry.occurred_on if basis == "occurDate" else JournalEntry.period_start
    query = select(JournalLine, JournalEntry).join(
        JournalEntry, JournalEntry.id == JournalLine.journal_entry_id
    ).where(
        JournalLine.company_id == company_id,
        JournalEntry.company_id == company_id,
        JournalEntry.status == "posted",
        JournalEntry.entry_type != "reversal",
        date_column >= start,
        date_column <= end,
    )
    query = _scope_filter(query, JournalLine, scope)
    rows = (await session.execute(query)).all()

    existing = (await session.execute(
        select(JournalLineAllocation).where(
            JournalLineAllocation.company_id == company_id,
            JournalLineAllocation.dimension_id == dimension_id,
        )
    )).scalars().all()
    taken_lines = {alloc.journal_line_id for alloc in existing}

    member_map = await _load_allocatable_members(session, company_id, dimension_id)
    now = _utcnow()
    allocated = 0
    skipped = 0
    unmatched = 0
    created: list[JournalLineAllocation] = []

    for line, entry in rows:
        if line.id in taken_lines:
            skipped += 1
            continue
        matched: AggregationRule | None = None
        for rule in rules:
            if _rule_matches(rule, line, entry.summary or ""):
                matched = rule
                break
        if matched is None:
            unmatched += 1
            continue
        member = member_map.get(matched.member_id)
        if member is None or member.status != "active":
            skipped += 1
            continue
        if member.start_date > entry.period_start or (
            member.end_date is not None and member.end_date < entry.period_start
        ):
            skipped += 1
            continue
        amount = money(line.amount * matched.rate)
        if amount <= ZERO:
            skipped += 1
            continue
        row = JournalLineAllocation(
            id=str(uuid4()),
            company_id=company_id,
            journal_line_id=line.id,
            dimension_id=dimension_id,
            member_id=matched.member_id,
            amount=amount,
            allocation_rate=matched.rate,
            allocation_method=matched.method,
            allocation_basis=matched.basis,
            source="rule",
            rule_id=matched.id,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        created.append(row)
        taken_lines.add(line.id)
        allocated += 1

    await session.flush()
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="apply_aggregation_rules",
        entity_type="accounting_dimension", entity_id=dimension_id, request_id=request_id,
        after={
            "start": start, "end": end, "basis": basis,
            "allocated": allocated, "skipped": skipped, "unmatched": unmatched,
            "allocations": [serialize_allocation(r) for r in created],
        },
    )
    return {"allocated": allocated, "skipped": skipped, "unmatched": unmatched, "rules": len(rules)}
