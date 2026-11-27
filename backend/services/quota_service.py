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
from datetime import date, datetime, timezone
from typing import Any

import config
from core.errors import CompanyLimitExceededError, QuotaExceededError
from core.plans import (
    DEFAULT_PLAN_CODE,
    FIRST_MONTH_PROMO_LOW_QUOTA_RATIO,
    PLAN_CATALOG,
    PlanDef,
    any_limit_configured,
    get_plan,
)

logger = logging.getLogger(__name__)


def is_quota_enforced() -> bool:
    """仅当开启商业化额度开关且套餐目录存在限额配置时，才执行卡口拦截。"""
    return bool(config.QUOTA_ENFORCEMENT_ENABLED and any_limit_configured())


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


def _effective_plan(user_row: Any) -> PlanDef:
    """当前生效套餐：付费套餐 plan_expires_at 已过期时惰性回落 trial。

    无 cron 的到期收口：读取侧（卡口/用量展示/计数）统一走本函数，
    到期次日起按 DEFAULT_PLAN_CODE 口径计费与展示。trial/历史编码无到期概念。
    """
    plan = get_plan(getattr(user_row, "plan_code", None))
    if plan.code == DEFAULT_PLAN_CODE:
        return plan
    expires = parse_iso_date(getattr(user_row, "plan_expires_at", None))
    if expires is not None and datetime.now(timezone.utc).date() > expires:
        return PLAN_CATALOG[DEFAULT_PLAN_CODE]
    return plan


def effective_period_limit(plan: PlanDef, user_row: Any, today: date | None = None) -> int | None:
    """当期基础配额上限（不含 quota_bonus_tokens、新人特惠与永久充值余额）。

    trial 新用户注册后首月（created_at 起 1 个月内）按 first_month_token_quota（20 万）放行，
    次月起自动恢复 token_quota（10 万）；已订阅过并到期回落的付费用户不重复享受首月额度。
    """
    if plan.token_quota is None:
        return None
    if plan.first_month_token_quota is not None and not getattr(user_row, "plan_expires_at", None):
        ref_today = today or datetime.now(timezone.utc).date()
        created = parse_iso_date(getattr(user_row, "created_at", None))
        if created is not None and created <= ref_today < add_months(created, 1):
            return plan.first_month_token_quota
    return plan.token_quota


def active_promo_tokens(user_row: Any, today: date | None = None) -> int:
    """当前仍处于有效期内的「新人首月体验包」剩余点数（惰性到期，过期返回 0）。"""
    balance = int(getattr(user_row, "promo_token_balance", 0) or 0)
    if balance <= 0:
        return 0
    expires = parse_iso_date(getattr(user_row, "promo_expires_at", None))
    if expires is None:
        return 0
    ref_today = today or datetime.now(timezone.utc).date()
    if ref_today > expires:
        return 0
    return balance


def is_first_month_promo_eligible(plan: PlanDef, user_row: Any) -> bool:
    """是否有资格购买「新人首月 AI 体验包」（¥1 买 80 万 Token，30 天有效）。

    条件：已启用额度卡口、当前为免费体验版、从未开通过年付套餐（plan_expires_at 为空）、
    且从未购买过该体验包（promo_purchased_at 为空，含退款不恢复）。
    """
    return bool(
        config.QUOTA_ENFORCEMENT_ENABLED
        and plan.code == DEFAULT_PLAN_CODE
        and not getattr(user_row, "plan_expires_at", None)
        and not getattr(user_row, "promo_purchased_at", None)
    )


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
    plan = _effective_plan(user_row)
    return plan, _anchor_date(user_row, datetime.now(timezone.utc).date())


def _overage_tokens(new_used: int, tokens: int, limit: int) -> int:
    """本次调用中超出套餐额度的 token 数（充值余额应扣减的部分）。

    计数行按「总用量」累加；超出 limit 的部分由充值余额承担。用累计口径
    做差可保证多笔并发/连续调用只对新增的超额部分扣一次余额。
    """
    before = max(0, new_used - tokens - limit)
    after = max(0, new_used - limit)
    return max(0, after - before)


def _overage_note(scene: str, model: str) -> str:
    return f"AI 消耗超出套餐额度（{scene or 'other'} / {model or '-'}）"[:255]


def _split_promo_overage(user: Any, overage: int) -> tuple[int, int]:
    """超额消耗分配：优先扣有效期内新人特惠余额，剩余部分再扣永久充值余额。"""
    if overage <= 0:
        return 0, 0
    active_promo = active_promo_tokens(user)
    promo_deduct = min(active_promo, overage) if active_promo > 0 else 0
    return promo_deduct, overage - promo_deduct


async def _charge_balance_overage_async(
    session: Any,
    user: Any,
    plan: PlanDef,
    period_start: str,
    tokens: int,
    *,
    scene: str = "",
    model: str = "",
) -> None:
    """套餐额度用尽后，先扣有效期内新人特惠余额，再从永久充值余额扣减并写只增流水。"""
    if not config.QUOTA_ENFORCEMENT_ENABLED or plan.token_quota is None:
        return
    from sqlalchemy import case, select, update

    from db.models import AiTokenLedger, AuthUser, UserQuotaCounter

    counter = (
        await session.execute(
            select(UserQuotaCounter.used_tokens).where(
                UserQuotaCounter.user_id == user.user_id,
                UserQuotaCounter.period_type == plan.period_type,
                UserQuotaCounter.period_start == period_start,
            )
        )
    ).scalar()
    new_used = int(counter or 0)
    base_limit = effective_period_limit(plan, user)
    if base_limit is None:
        return
    limit = base_limit + int(getattr(user, "quota_bonus_tokens", 0) or 0)
    overage = _overage_tokens(new_used, tokens, limit)
    promo_deduct, overage = _split_promo_overage(user, overage)
    if promo_deduct > 0:
        await session.execute(
            update(AuthUser)
            .where(AuthUser.user_id == user.user_id)
            .values(
                promo_token_balance=case(
                    (
                        AuthUser.promo_token_balance >= promo_deduct,
                        AuthUser.promo_token_balance - promo_deduct,
                    ),
                    else_=0,
                )
            )
        )
    if overage <= 0:
        return
    # 原子扣减：并发充值与消耗都落在同一行，避免读改写丢失更新。允许为负，
    # 下一次充值会先抵扣欠费；卡口看到余额 <= 0 即停止放行新调用。
    await session.execute(
        update(AuthUser)
        .where(AuthUser.user_id == user.user_id)
        .values(token_balance=AuthUser.token_balance - overage)
    )
    balance_after = int(
        (
            await session.execute(
                select(AuthUser.token_balance).where(AuthUser.user_id == user.user_id)
            )
        ).scalar_one()
        or 0
    )
    session.add(
        AiTokenLedger(
            user_id=user.user_id,
            kind="consume",
            delta_tokens=-overage,
            balance_after=balance_after,
            note=_overage_note(scene, model),
            created_by=user.user_id,
        )
    )
    if balance_after < 0:
        logger.warning(
            "AI 充值余额透支 user=%s overage=%s balance=%s（等待充值抵扣）",
            user.user_id, overage, balance_after,
        )


def _charge_balance_overage_sync(
    session: Any,
    user: Any,
    plan: PlanDef,
    period_start: str,
    tokens: int,
    *,
    scene: str = "",
    model: str = "",
) -> None:
    if not config.QUOTA_ENFORCEMENT_ENABLED or plan.token_quota is None:
        return
    from sqlalchemy import case, select, update

    from db.models import AiTokenLedger, AuthUser, UserQuotaCounter

    counter = session.execute(
        select(UserQuotaCounter.used_tokens).where(
            UserQuotaCounter.user_id == user.user_id,
            UserQuotaCounter.period_type == plan.period_type,
            UserQuotaCounter.period_start == period_start,
        )
    ).scalar()
    new_used = int(counter or 0)
    base_limit = effective_period_limit(plan, user)
    if base_limit is None:
        return
    limit = base_limit + int(getattr(user, "quota_bonus_tokens", 0) or 0)
    overage = _overage_tokens(new_used, tokens, limit)
    promo_deduct, overage = _split_promo_overage(user, overage)
    if promo_deduct > 0:
        session.execute(
            update(AuthUser)
            .where(AuthUser.user_id == user.user_id)
            .values(
                promo_token_balance=case(
                    (
                        AuthUser.promo_token_balance >= promo_deduct,
                        AuthUser.promo_token_balance - promo_deduct,
                    ),
                    else_=0,
                )
            )
        )
    if overage <= 0:
        return
    session.execute(
        update(AuthUser)
        .where(AuthUser.user_id == user.user_id)
        .values(token_balance=AuthUser.token_balance - overage)
    )
    balance_after = int(
        session.execute(
            select(AuthUser.token_balance).where(AuthUser.user_id == user.user_id)
        ).scalar_one()
        or 0
    )
    session.add(
        AiTokenLedger(
            user_id=user.user_id,
            kind="consume",
            delta_tokens=-overage,
            balance_after=balance_after,
            note=_overage_note(scene, model),
            created_by=user.user_id,
        )
    )
    if balance_after < 0:
        logger.warning(
            "AI 充值余额透支 user=%s overage=%s balance=%s（等待充值抵扣）",
            user.user_id, overage, balance_after,
        )


async def record_consumption_async(
    session: Any, *, user_id: str, tokens: int, scene: str = "", model: str = ""
) -> None:
    """在既有 async 埋点事务里累计周期计数，并扣减充值余额超额部分。

    尽力而为，绝不抛出影响业务；余额扣减失败会打 warning 留痕。
    """
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
        period_start = start.isoformat()
        await session.execute(
            _counter_upsert_stmt(
                session.get_bind().dialect.name,
                user_id, plan.period_type, period_start, tokens,
            )
        )
        await _charge_balance_overage_async(
            session, user, plan, period_start, tokens, scene=scene, model=model
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota counter / 充值余额累计失败（忽略）: %s", exc)


def record_consumption_sync(
    session: Any, *, user_id: str, tokens: int, scene: str = "", model: str = ""
) -> None:
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
        period_start = start.isoformat()
        session.execute(
            _counter_upsert_stmt(
                session.get_bind().dialect.name,
                user_id, plan.period_type, period_start, tokens,
            )
        )
        _charge_balance_overage_sync(
            session, user, plan, period_start, tokens, scene=scene, model=model
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota counter / 充值余额累计失败（忽略）: %s", exc)


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


def _assemble_state(
    plan: PlanDef,
    user_row: Any,
    start: date,
    end: date,
    used: int,
    company_count: int | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    ref_today = today or datetime.now(timezone.utc).date()
    # 开源自部署关闭额度卡口时，对外统一返回不限量，避免前端误展示「已用完」
    base_limit = effective_period_limit(plan, user_row, today=ref_today)
    limit = base_limit if config.QUOTA_ENFORCEMENT_ENABLED else None
    max_companies = plan.max_companies if config.QUOTA_ENFORCEMENT_ENABLED else None
    bonus = int(getattr(user_row, "quota_bonus_tokens", 0) or 0)
    effective = None if limit is None else limit + bonus
    # 充值余额永久有效，不随周期重置；负数表示额度边界上的超额欠费，对外按 0 展示。
    token_balance = int(getattr(user_row, "token_balance", 0) or 0)
    promo_balance = active_promo_tokens(user_row, today=ref_today)
    promo_expires_at = getattr(user_row, "promo_expires_at", None) if promo_balance > 0 else None
    promo_eligible = is_first_month_promo_eligible(plan, user_row)
    plan_remaining = max(0, effective - used) if effective is not None else None
    promo_low_quota = bool(
        promo_eligible
        and effective is not None
        and effective > 0
        and (
            used >= int(effective * FIRST_MONTH_PROMO_LOW_QUOTA_RATIO)
            or ((plan_remaining or 0) + max(0, token_balance)) <= 0
        )
    )
    state: dict[str, Any] = {
        "planCode": plan.code,
        "planName": plan.name,
        "periodType": plan.period_type,
        "periodStart": start.isoformat(),
        "periodEnd": end.isoformat(),
        "limitTokens": limit,
        "bonusTokens": bonus,
        "usedTokens": used,
        # 充值余额（原值，可能为负）；前端展示时取 max(0, tokenBalance)
        "tokenBalance": token_balance,
        # 新人首月体验包（30 天有效、先于永久余额扣减）
        "promoTokenBalance": promo_balance,
        "promoExpiresAt": promo_expires_at,
        "promoEligible": promo_eligible,
        "promoLowQuota": promo_low_quota,
        # 付费套餐到期日（ISO 日期串；None = 不适用）。到期后 _effective_plan 已按 trial 口径生效
        "planExpiresAt": getattr(user_row, "plan_expires_at", None),
        # 可用合计 = 套餐本期剩余 + 有效期内特惠余额 + 充值余额；limit 为 None（未设限）时恒为 None
        "remainingTokens": (
            plan_remaining + promo_balance + max(0, token_balance)
            if plan_remaining is not None
            else None
        ),
        # 账套上限（None = 不限；未来面向代账机构开放更多账套）
        "maxCompanies": max_companies,
    }
    if company_count is not None:
        state["companyCount"] = company_count
    return state


async def quota_state(user_id: str) -> dict[str, Any] | None:
    """用户当前配额状态（给 /api/usage/me；用户行缺失返回 None）。"""
    from sqlalchemy import func, select

    from db.models import AuthUser, Company, UserQuotaCounter
    from db.session import session_scope

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        if user is None:
            return None
        today = datetime.now(timezone.utc).date()
        plan = _effective_plan(user)
        anchor = _anchor_date(user, today)
        start, end = current_period(plan, anchor, today)
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
        company_count = int(
            (
                await session.execute(
                    select(func.count()).select_from(Company).where(Company.owner_user_id == user_id)
                )
            ).scalar_one()
            or 0
        )
        return _assemble_state(plan, user, start, end, used, company_count=company_count, today=today)


async def is_paid_plan_active(user_id: str | None) -> bool:
    """用户当前是否处于有效付费套餐（非 trial 且未过期）。"""
    if not user_id:
        return False
    from sqlalchemy import select

    from db.models import AuthUser
    from db.session import session_scope

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
    if user is None:
        return False
    return _effective_plan(user).code != DEFAULT_PLAN_CODE


async def ensure_within_company_limit(user_id: str | None) -> None:
    """新建账套卡口：开启商业化限额且当前套餐设了 max_companies 时校验证照数量。"""
    if not user_id or not config.QUOTA_ENFORCEMENT_ENABLED:
        return
    from sqlalchemy import func, select

    from db.models import AuthUser, Company
    from db.session import session_scope

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        plan = _effective_plan(user) if user is not None else PLAN_CATALOG[DEFAULT_PLAN_CODE]
        limit = plan.max_companies
        if limit is None:
            return
        company_count = int(
            (
                await session.execute(
                    select(func.count()).select_from(Company).where(Company.owner_user_id == user_id)
                )
            ).scalar_one()
            or 0
        )
        if company_count >= limit:
            raise CompanyLimitExceededError(
                message=f"当前{plan.name}最多支持 {limit} 个账套；未来将面向代账机构开放更多账套方案",
                context={
                    "planCode": plan.code,
                    "maxCompanies": limit,
                    "companyCount": company_count,
                },
            )


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
    try:
        with Session(get_sync_engine()) as session:
            user = session.execute(
                select(AuthUser).where(AuthUser.user_id == user_id)
            ).scalars().first()
            if user is None:
                return None
            plan = _effective_plan(user)
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
            return _assemble_state(plan, user, start, end, used, today=today)
    except Exception as exc:  # noqa: BLE001
        logger.debug("quota state 读取失败，按 fail-open 放行: %s", exc)
        return None


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
    """同步卡口：未开启商业化限额开关或套餐未设限时直通。

    用户行缺失（含开发兜底身份）按无套餐处理，放行——与鉴权层的 fail-open 口径一致。
    """
    if not user_id or not is_quota_enforced():
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
            "tokenBalance": state.get("tokenBalance", 0),
            "promoTokenBalance": state.get("promoTokenBalance", 0),
            "promoEligible": bool(state.get("promoEligible", False)),
        },
    )


async def ensure_within_quota_async(user_id: str | None) -> None:
    """异步入口的卡口：未启用限额时零开销直通；启用后经线程池读库（带 TTL 缓存）。"""
    if not user_id or not is_quota_enforced():
        return
    await asyncio.to_thread(ensure_within_quota, user_id)


def clear_state_cache() -> None:
    """测试与管理端改套餐后的缓存失效口。"""
    with _STATE_CACHE_LOCK:
        _STATE_CACHE.clear()
