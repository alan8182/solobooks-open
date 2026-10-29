# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""AI 用量配额服务（预留，额度数值待商业决策）。

机制全量就位、惰性生效：
- core/plans.py 所有套餐额度为 None（当前状态）时，卡口 ensure_within_quota
  只做一次内存目录扫描即直通，零 DB 开销、零行为变化；
- 周期计数随 services/llm.py 的用量埋点同事务持续积累，为启用限制预热历史数据；
- 启用限制 = 在 core/plans.py 填上数值并部署，其余代码零改动。

周期口径（惰性推算，无 cron）：monthly = 锚点日（plan_anchor_at，缺省注册时间）
的月周年；annual = 12 个月年周年。新周期自然落新计数行，旧行即历史。
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.errors import QuotaExceededError
from core.plans import PlanDef, any_limit_configured, get_plan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 周期推算（纯函数，无 IO）
# ---------------------------------------------------------------------------

def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def add_months(day: date, months: int) -> date:
    """月份加法；锚点日超过目标月天数时钳制到月末（31 号锚 → 2 月按 28/29）。"""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def current_period(plan: PlanDef, anchor: date, today: date) -> tuple[date, date]:
    """返回当前周期 [start, end)。

    monthly：锚点日的月周年（锚 3-15 → 每月 15 重置）；annual：按 12 个月。
    today 尚未到达本周期锚点日时回退一个周期；today 早于锚点（异常数据）时
    以锚点为起点兜底。
    """
    step = 1 if plan.period_type == "monthly" else 12
    months_elapsed = (today.year - anchor.year) * 12 + (today.month - anchor.month)
    offset = (months_elapsed // step) * step
    start = add_months(anchor, offset)
    if start > today:
        start = add_months(anchor, offset - step)
    if start < anchor:
        start = anchor
    return start, add_months(start, step)


def _anchor_date(user_row: Any, today: date) -> date:
    """周期锚点：plan_anchor_at 优先，缺省回退注册时间；异常数据兜底为今天。"""
    anchor = parse_iso_date(getattr(user_row, "plan_anchor_at", None))
    if anchor is None:
        anchor = parse_iso_date(getattr(user_row, "created_at", None))
    if anchor is None or anchor > today:
        return today
    return anchor


# ---------------------------------------------------------------------------
# 周期计数（随 llm_usage 埋点同事务 upsert；dialect 感知 ON CONFLICT）
# ---------------------------------------------------------------------------

def _counter_upsert_stmt(dialect_name: str, user_id: str, period_type: str, period_start: str, tokens: int):
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from db.models import UserQuotaCounter

    table = UserQuotaCounter.__table__
    insert_cls = pg_insert if dialect_name == "postgresql" else sqlite_insert
    stmt = insert_cls(table).values(
        user_id=user_id,
        period_type=period_type,
        period_start=period_start,
        used_tokens=int(tokens),
    )
    return stmt.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.period_type, table.c.period_start],
        set_={
            "used_tokens": table.c.used_tokens + stmt.excluded.used_tokens,
            "updated_at": func.now(),
        },
    )


def _plan_and_anchor_for(user_row: Any) -> tuple[PlanDef, date]:
    plan = get_plan(getattr(user_row, "plan_code", None))
    return plan, _anchor_date(user_row, datetime.now(timezone.utc).date())


async def record_consumption_async(session: Any, *, user_id: str, tokens: int) -> None:
    """在既有 async 埋点事务里累计周期计数。尽力而为，绝不抛出影响业务。"""
    try:
        from sqlalchemy import select

        from db.models import AuthUser

        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        if user is None:
            return
        plan, anchor = _plan_and_anchor_for(user)
        start, _end = current_period(plan, anchor, datetime.now(timezone.utc).date())
        await session.execute(
            _counter_upsert_stmt(
                session.get_bind().dialect.name,
                user_id, plan.period_type, start.isoformat(), tokens,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("quota counter 累计失败（忽略）: %s", exc)


def record_consumption_sync(session: Any, *, user_id: str, tokens: int) -> None:
    """同步版本：线程池上下文的埋点写路径使用。尽力而为，绝不抛出。"""
    try:
        from sqlalchemy import select

        from db.models import AuthUser

        user = session.execute(
            select(AuthUser).where(AuthUser.user_id == user_id)
        ).scalars().first()
        if user is None:
            return
        plan, anchor = _plan_and_anchor_for(user)
        start, _end = current_period(plan, anchor, datetime.now(timezone.utc).date())
        session.execute(
            _counter_upsert_stmt(
                session.get_bind().dialect.name,
                user_id, plan.period_type, start.isoformat(), tokens,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("quota counter 累计失败（忽略）: %s", exc)


# ---------------------------------------------------------------------------
# 状态查询（/api/usage/me 用；used 以计数行为准，行缺失时回退聚合 llm_usage）
# ---------------------------------------------------------------------------

def _period_usage_fallback(session: Any, user_id: str, start: date, end: date) -> int:
    """计数行缺失时从 llm_usage 聚合当期用量（同步 Session 版；覆盖预留前的历史）。"""
    from sqlalchemy import func, select

    from db.models import LlmUsage

    begin = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    finish = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    total = session.execute(
        select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(
            LlmUsage.user_id == user_id,
            LlmUsage.created_at >= begin,
            LlmUsage.created_at < finish,
        )
    ).scalar()
    return int(total or 0)


async def _period_usage_fallback_async(session: Any, user_id: str, start: date, end: date) -> int:
    """异步 Session 版本（AsyncSession.execute 须 await，不能与同步版混用）。"""
    from sqlalchemy import func, select

    from db.models import LlmUsage

    begin = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    finish = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    total = await session.execute(
        select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(
            LlmUsage.user_id == user_id,
            LlmUsage.created_at >= begin,
            LlmUsage.created_at < finish,
        )
    )
    return int(total.scalar() or 0)


def _assemble_state(plan: PlanDef, user_row: Any, start: date, end: date, used: int) -> dict[str, Any]:
    limit = plan.token_quota
    bonus = int(getattr(user_row, "quota_bonus_tokens", 0) or 0)
    effective = None if limit is None else limit + bonus
    return {
        "planCode": plan.code,
        "planName": plan.name,
        "periodType": plan.period_type,
        "periodStart": start.isoformat(),
        "periodEnd": end.isoformat(),
        "limitTokens": limit,
        "bonusTokens": bonus,
        "usedTokens": used,
        # remaining 基于计数行；limit 为 None（未设限）时恒为 None
        "remainingTokens": max(0, effective - used) if effective is not None else None,
    }


async def quota_state(user_id: str) -> dict[str, Any] | None:
    """用户当前配额状态（给 /api/usage/me；用户行缺失返回 None）。"""
    from sqlalchemy import select

    from db.models import AuthUser, UserQuotaCounter
    from db.session import session_scope

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        if user is None:
            return None
        plan = get_plan(user.plan_code)
        anchor = _anchor_date(user, datetime.now(timezone.utc).date())
        start, end = current_period(plan, anchor, datetime.now(timezone.utc).date())
        counter = (
            await session.execute(
                select(UserQuotaCounter).where(
                    UserQuotaCounter.user_id == user_id,
                    UserQuotaCounter.period_type == plan.period_type,
                    UserQuotaCounter.period_start == start.isoformat(),
                )
            )
        ).scalars().first()
        used = int(counter.used_tokens) if counter else await _period_usage_fallback_async(session, user_id, start, end)
        return _assemble_state(plan, user, start, end, used)


# ---------------------------------------------------------------------------
# 卡口（启用限制后的唯一检查点；未设限时零开销直通）
# ---------------------------------------------------------------------------

_STATE_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_STATE_CACHE_TTL_SECONDS = 30.0
_STATE_CACHE_LOCK = threading.Lock()


def _load_state_sync(user_id: str) -> dict[str, Any] | None:
    """同步读配额状态（独立同步引擎），供线程上下文的卡口使用。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from db.models import AuthUser, UserQuotaCounter
    from db.session import get_sync_engine

    today = datetime.now(timezone.utc).date()
    with Session(get_sync_engine()) as session:
        user = session.execute(
            select(AuthUser).where(AuthUser.user_id == user_id)
        ).scalars().first()
        if user is None:
            return None
        plan = get_plan(user.plan_code)
        anchor = _anchor_date(user, today)
        start, end = current_period(plan, anchor, today)
        counter = session.execute(
            select(UserQuotaCounter).where(
                UserQuotaCounter.user_id == user_id,
                UserQuotaCounter.period_type == plan.period_type,
                UserQuotaCounter.period_start == start.isoformat(),
            )
        ).scalars().first()
        # 卡口读计数行（PK 单行查询，快）；行缺失时回退聚合一次（含历史回填）
        used = int(counter.used_tokens) if counter else _period_usage_fallback(session, user_id, start, end)
        return _assemble_state(plan, user, start, end, used)


def _cached_state(user_id: str) -> dict[str, Any] | None:
    now = time.monotonic()
    with _STATE_CACHE_LOCK:
        hit = _STATE_CACHE.get(user_id)
        if hit and hit[0] > now:
            return hit[1]
    state = _load_state_sync(user_id)
    with _STATE_CACHE_LOCK:
        _STATE_CACHE[user_id] = (time.monotonic() + _STATE_CACHE_TTL_SECONDS, state)
    return state


def ensure_within_quota(user_id: str | None) -> None:
    """同步卡口：套餐未设限（当前默认状态）时只扫内存目录即直通。

    用户行缺失（含开发兜底身份）按无套餐处理，放行——与鉴权层的 fail-open 口径一致。
    """
    if not user_id or not any_limit_configured():
        return
    state = _cached_state(user_id)
    if state is None or state["limitTokens"] is None:
        return
    if state["remainingTokens"] is not None and state["remainingTokens"] > 0:
        return
    raise QuotaExceededError(
        context={
            "planCode": state["planCode"],
            "periodStart": state["periodStart"],
            "periodEnd": state["periodEnd"],
            "limitTokens": state["limitTokens"],
            "usedTokens": state["usedTokens"],
        },
    )


async def ensure_within_quota_async(user_id: str | None) -> None:
    """异步入口的卡口：未设限时零开销直通；设限后经线程池读库（带 TTL 缓存）。"""
    if not user_id or not any_limit_configured():
        return
    await asyncio.to_thread(ensure_within_quota, user_id)


def clear_state_cache() -> None:
    """测试与管理端改套餐后的缓存失效口。"""
    with _STATE_CACHE_LOCK:
        _STATE_CACHE.clear()
