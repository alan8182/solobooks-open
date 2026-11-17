# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""平台自营收款订单服务（支付宝电脑网站支付，售卖 SoloBooks 套餐）。

A 档纪律在本服务的落点：
- **金额**：下单金额唯一来源 core/plans.py 目录；notify 的 total_amount 与订单
  金额用 core.money 两位小数语义严格比对，不符不激活、留档人工处理；
- **幂等**：状态迁移全部 CAS（UPDATE ... WHERE status=旧值），支付宝重复推送
  同一通知只生效一次；重复推送返回 success 停止重试；
- **审计只增**：每次通知（含验签失败）原样落 billing_notify_logs，永不更新；
- **真相唯一**：套餐权益只写在 auth_users（plan_code/plan_anchor_at/
  plan_expires_at），与账套账务层完全隔离，不触碰 journal_entries。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update

from core.errors import AccountingError
from core.money import money
from core.plans import PlanDef, get_plan, sellable_plans

logger = logging.getLogger(__name__)


@dataclass
class BillingError(AccountingError):
    """支付/订单域错误基类；子类须全字段带默认值（dataclass 继承约束）。"""

    code: str = "BILLING_ERROR"
    message: str = "支付请求无效"
    status_code: int = 422


@dataclass
class PlanNotSellable(BillingError):
    code: str = "PLAN_NOT_SELLABLE"
    message: str = "该套餐不在售卖目录中"
    status_code: int = 422


@dataclass
class AlipayNotConfigured(BillingError):
    code: str = "ALIPAY_NOT_CONFIGURED"
    message: str = "支付通道未开通，请稍后再试或联系客服"
    status_code: int = 503


@dataclass
class OrderNotFound(BillingError):
    code: str = "ORDER_NOT_FOUND"
    message: str = "订单不存在"
    status_code: int = 404


# 视为「支付成功」的支付宝 trade_status（TRADE_FINISHED 为不可退款终态，同样放行）
_SUCCESS_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")
_CLOSE_STATUS = "TRADE_CLOSED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> Any:
    """套餐到期日按 UTC 日期口径，与 quota_service 的周期推算保持一致。"""
    return _now().date()


def _parse_timeout(raw: str) -> timedelta:
    """解析 '2h'/'30m'/'15s'/'1d' 形式的订单有效期。"""
    text = (raw or "").strip().lower()
    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in unit_seconds and text[:-1].isdigit():
        return timedelta(seconds=int(text[:-1]) * unit_seconds[text[-1]])
    return timedelta(hours=2)


def _generate_out_trade_no() -> str:
    """SB + 北京日期 + 6 位随机（20 位，满足支付宝 6-32 位字母数字约束）。"""
    from datetime import timedelta as _td

    tz_cn = timezone(_td(hours=8))
    stamp = _now().astimezone(tz_cn).strftime("%y%m%d%H%M%S")
    return f"SB{stamp}{uuid.uuid4().hex[:6].upper()}"


def get_pricing() -> dict[str, Any]:
    """售卖目录（/api/billing/pricing）：前端不得自写价格，唯一真相在 core/plans.py。"""
    from config import alipay_enabled

    return {
        "alipayEnabled": alipay_enabled(),
        "items": [
            {
                "planCode": plan.code,
                "planName": plan.name,
                "period": "annual",
                "priceAnnual": plan.price_annual,
            }
            for plan in sellable_plans()
        ],
    }


async def _close_expired_orders(session: Any) -> None:
    """惰性关单：支付有效期已过的 created 订单置 closed（无 cron，读路径顺带收口）。"""
    from db.models import BillingOrder

    await session.execute(
        update(BillingOrder)
        .where(BillingOrder.status == "created", BillingOrder.expires_at < _now())
        .values(status="closed", updated_at=_now())
    )


async def create_order(user_id: str, plan_code: str) -> dict[str, Any]:
    """创建待支付订单并返回收银台跳转 URL。金额取目录，绝不接受前端传价。"""
    from config import ALIPAY_ORDER_TIMEOUT, alipay_enabled
    from core.money import money_text
    from db.models import BillingOrder
    from db.session import session_scope
    from services import alipay_client

    if not alipay_enabled():
        raise AlipayNotConfigured()
    plan = get_plan(plan_code)
    if plan.price_annual is None:
        raise PlanNotSellable(context={"planCode": plan_code})

    async with session_scope() as session:
        await _close_expired_orders(session)
        order = BillingOrder(
            out_trade_no=_generate_out_trade_no(),
            user_id=user_id,
            plan_code=plan.code,
            period="annual",
            amount=money(plan.price_annual, allow_negative=False),
            status="created",
            subject=f"{plan.name}（年付）",
            expires_at=_now() + _parse_timeout(ALIPAY_ORDER_TIMEOUT),
        )
        session.add(order)
        await session.flush()
        pay_url = alipay_client.build_page_pay_url(
            out_trade_no=order.out_trade_no,
            total_amount=money_text(order.amount),
            subject=order.subject,
        )
        return {
            "orderNo": order.out_trade_no,
            "planCode": plan.code,
            "planName": plan.name,
            "amount": money_text(order.amount),
            "payUrl": pay_url,
            "expiresAt": order.expires_at.isoformat(),
        }


def _order_summary(order: Any) -> dict[str, Any]:
    from core.money import money_text

    return {
        "orderNo": order.out_trade_no,
        "planCode": order.plan_code,
        "subject": order.subject,
        "amount": money_text(order.amount),
        "status": order.status,
        "tradeNo": order.trade_no or None,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "activatedAt": order.activated_at.isoformat() if order.activated_at else None,
        "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
    }


async def get_order_for_user(user_id: str, out_trade_no: str) -> dict[str, Any]:
    """本人订单详情（收银台回跳后的轮询端点）；顺带惰性关单。"""
    from db.models import BillingOrder
    from db.session import session_scope

    async with session_scope() as session:
        await _close_expired_orders(session)
        order = (
            await session.execute(
                select(BillingOrder).where(
                    BillingOrder.out_trade_no == out_trade_no,
                    BillingOrder.user_id == user_id,
                )
            )
        ).scalars().first()
        if order is None:
            raise OrderNotFound()
        return _order_summary(order)


async def list_orders_for_user(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from db.models import BillingOrder
    from db.session import session_scope

    async with session_scope() as session:
        await _close_expired_orders(session)
        rows = (
            (
                await session.execute(
                    select(BillingOrder)
                    .where(BillingOrder.user_id == user_id)
                    .order_by(BillingOrder.created_at.desc())
                    .limit(min(max(limit, 1), 100))
                )
            )
            .scalars()
            .all()
        )
        return [_order_summary(row) for row in rows]


# ---------------------------------------------------------------------------
# 支付宝异步通知（幂等核心）
# ---------------------------------------------------------------------------

def _expected_amount(order: Any) -> Decimal:
    return money(order.amount, allow_negative=False)


async def _activate_plan(session: Any, user_id: str, plan: PlanDef) -> None:
    """把付费权益写入 auth_users（唯一真相），随后失效配额缓存。

    升/续费语义：
    - 同套餐且未到期（续费）：到期日顺延一年，周期锚点不动（不重置已用额度周期）；
    - 跨套餐或已到期（升级/过期重购）：锚点=今天，到期日=今天+1 年（自然年，钳制月末）。
    """
    from core.plans import DEFAULT_PLAN_CODE
    from db.models import AuthUser
    from services.quota_service import add_months, clear_state_cache, parse_iso_date

    user = (
        await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
    ).scalars().first()
    if user is None:
        logger.error("订单激活找不到用户行 user_id=%s（异常数据，人工排查）", user_id)
        return
    today = _today()
    current_plan_code = user.plan_code
    current_expires = parse_iso_date(user.plan_expires_at)
    if plan.code == current_plan_code and current_plan_code != DEFAULT_PLAN_CODE and current_expires and current_expires >= today:
        new_expires = add_months(current_expires, 12)
        # anchor 不动
    else:
        new_expires = add_months(today, 12)
        user.plan_anchor_at = today.isoformat()
    user.plan_code = plan.code
    user.plan_expires_at = new_expires.isoformat()
    clear_state_cache()
    logger.info(
        "套餐激活 user=%s plan=%s expires=%s（原 %s/%s）",
        user_id, plan.code, new_expires.isoformat(), current_plan_code,
        current_expires.isoformat() if current_expires else None,
    )


async def _record_notify(
    session: Any,
    *,
    form: dict[str, str],
    sign_verified: bool,
    result: str,
) -> None:
    from db.models import BillingNotifyLog

    session.add(
        BillingNotifyLog(
            out_trade_no=str(form.get("out_trade_no") or ""),
            trade_no=str(form.get("trade_no") or ""),
            trade_status=str(form.get("trade_status") or ""),
            sign_verified=sign_verified,
            result=result,
            payload=dict(form),
        )
    )


async def apply_notify(form: dict[str, str]) -> str:
    """处理支付宝异步通知，返回 "success"/"failure"（支付宝按此决定是否重推）。

    幂等保证：created→paid 与 paid→activated 都是 CAS；并发重复通知只有一条
    CAS 生效，其余按 duplicate 落档并同样返回 success。
    """
    from config import ALIPAY_APP_ID
    from db.models import BillingOrder
    from db.session import session_scope
    from services import alipay_client

    sign_verified = alipay_client.verify_notify_signature(form)
    out_trade_no = str(form.get("out_trade_no") or "")
    trade_status = str(form.get("trade_status") or "")

    if not sign_verified:
        # 验签失败：留档但绝不碰订单/用户；回 failure 让支付宝重推（可能是我们配置错了公钥）
        async with session_scope() as session:
            await _record_notify(session, form=form, sign_verified=False, result="rejected_bad_sign")
        logger.warning("支付宝通知验签失败 out_trade_no=%s", out_trade_no)
        return "failure"

    async with session_scope() as session:
        if str(form.get("app_id") or "") != ALIPAY_APP_ID:
            await _record_notify(session, form=form, sign_verified=True, result="rejected_app_id")
            return "failure"

        order = (
            await session.execute(
                select(BillingOrder)
                .where(BillingOrder.out_trade_no == out_trade_no)
                .with_for_update()
            )
        ).scalars().first()
        if order is None:
            await _record_notify(session, form=form, sign_verified=True, result="order_not_found")
            logger.warning("支付宝通知找不到订单 out_trade_no=%s", out_trade_no)
            return "failure"

        if trade_status in _SUCCESS_STATUSES:
            try:
                notified_amount = money(form.get("total_amount"), allow_negative=False)
            except Exception:  # noqa: BLE001 — money() 抛 MoneyError(ValueError)
                await _record_notify(session, form=form, sign_verified=True, result="rejected_amount")
                return "failure"
            if notified_amount != _expected_amount(order):
                # 金额不符绝不激活（部分支付/篡改场景），留档人工处理
                await _record_notify(session, form=form, sign_verified=True, result="rejected_amount")
                logger.error(
                    "通知金额与订单不符 order=%s 订单额=%s 通知额=%s",
                    out_trade_no, _expected_amount(order), notified_amount,
                )
                return "failure"

            if order.status in ("paid", "activated"):
                # 幂等：重复推送，已处理过
                await _record_notify(session, form=form, sign_verified=True, result="duplicate")
                return "success"
            if order.status != "created":
                # closed/failed 后收到成功通知：钱货状态冲突，人工介入
                await _record_notify(session, form=form, sign_verified=True, result="rejected_status_conflict")
                logger.error("订单 %s 状态 %s 收到成功通知，需人工核对", out_trade_no, order.status)
                return "failure"

            # CAS created→paid：并发重复通知只有一条生效
            now = _now()
            paid = await session.execute(
                update(BillingOrder)
                .where(BillingOrder.id == order.id, BillingOrder.status == "created")
                .values(
                    status="paid",
                    trade_no=str(form.get("trade_no") or ""),
                    buyer_logon_id=str(form.get("buyer_logon_id") or ""),
                    notify_payload=dict(form),
                    paid_at=now,
                    updated_at=now,
                )
            )
            if paid.rowcount != 1:
                await _record_notify(session, form=form, sign_verified=True, result="duplicate")
                return "success"
            await _activate_plan(session, order.user_id, get_plan(order.plan_code))
            await session.execute(
                update(BillingOrder)
                .where(BillingOrder.id == order.id, BillingOrder.status == "paid")
                .values(status="activated", activated_at=now, updated_at=_now())
            )
            await _record_notify(session, form=form, sign_verified=True, result="processed")
            return "success"

        if trade_status == _CLOSE_STATUS:
            closed = await session.execute(
                update(BillingOrder)
                .where(BillingOrder.id == order.id, BillingOrder.status == "created")
                .values(status="closed", notify_payload=dict(form), updated_at=_now())
            )
            await _record_notify(
                session,
                form=form,
                sign_verified=True,
                result="closed_by_notify" if closed.rowcount == 1 else "duplicate",
            )
            return "success"

        # WAIT_BUYER_PAY 等中间态：只落档
        await _record_notify(session, form=form, sign_verified=True, result="ignored_state")
        return "success"
