# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""平台自营收款订单服务（支付宝电脑网站支付 + 微信支付 Native 扫码，售卖 SoloBooks 套餐）。

渠道模型：billing_orders.payment_channel 标识订单渠道（alipay/wechat），
下单/退款/通知分发按渠道走各自 client（alipay_client / wechat_pay_client），
无 provider 抽象——两渠道协议差异大，if/else 分发贴合仓库惯例。

A 档纪律在本服务的落点：
- **金额**：下单金额唯一来源 core/plans.py 目录；notify 的金额（支付宝
  total_amount 元 / 微信 amount.total 分）与订单金额严格比对，不符不激活、
  留档人工处理；
- **幂等**：状态迁移全部 CAS（UPDATE ... WHERE status=旧值），渠道重复推送
  同一通知只生效一次；重复推送按 duplicate 落档；
- **审计只增**：每次通知（含验签失败）原样落 billing_notify_logs，永不更新；
- **真相唯一**：套餐权益只写在 auth_users（plan_code/plan_anchor_at/
  plan_expires_at），与账套账务层完全隔离，不触碰 journal_entries。
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update

from core.errors import AccountingError
from core.money import money
from core.plans import (
    FIRST_MONTH_PROMO_CODE,
    FIRST_MONTH_PROMO_NAME,
    FIRST_MONTH_PROMO_ORIGINAL_YUAN,
    FIRST_MONTH_PROMO_PRICE_YUAN,
    FIRST_MONTH_PROMO_TOKENS,
    FIRST_MONTH_PROMO_VALID_DAYS,
    TOKEN_TOPUP_MAX_YUAN,
    TOKEN_TOPUP_MIN_YUAN,
    TOKEN_TOPUP_TOKENS_PER_YUAN,
    PlanDef,
    get_plan,
    sellable_plans,
    token_topup_tokens,
)

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
class TopupAmountInvalid(BillingError):
    code: str = "TOPUP_AMOUNT_INVALID"
    message: str = "充值金额必须为整数元，且不低于最低充值额"
    status_code: int = 422


@dataclass
class TopupNotRefundable(BillingError):
    code: str = "TOPUP_NOT_REFUNDABLE"
    message: str = "该充值已消耗，没有可退余额"
    status_code: int = 422


@dataclass
class PromoNotEligible(BillingError):
    code: str = "PROMO_NOT_ELIGIBLE"
    message: str = "当前账号不符合新人首月特惠购买条件"
    status_code: int = 422


@dataclass
class AlipayNotConfigured(BillingError):
    code: str = "ALIPAY_NOT_CONFIGURED"
    message: str = "支付通道未开通，请稍后再试或联系客服"
    status_code: int = 503


@dataclass
class WechatPayNotConfigured(BillingError):
    code: str = "WECHAT_PAY_NOT_CONFIGURED"
    message: str = "支付通道未开通，请稍后再试或联系客服"
    status_code: int = 503


@dataclass
class ChannelNotSupported(BillingError):
    code: str = "CHANNEL_NOT_SUPPORTED"
    message: str = "不支持的支付渠道"
    status_code: int = 422


@dataclass
class OrderNotFound(BillingError):
    code: str = "ORDER_NOT_FOUND"
    message: str = "订单不存在"
    status_code: int = 404


@dataclass
class OrderNotRefundable(BillingError):
    code: str = "ORDER_NOT_REFUNDABLE"
    message: str = "该订单当前状态不可退款（仅已支付订单可退）"
    status_code: int = 422


@dataclass
class RefundReasonRequired(BillingError):
    code: str = "REFUND_REASON_REQUIRED"
    message: str = "退款必须填写原因"
    status_code: int = 422


@dataclass
class OtherActiveOrders(BillingError):
    code: str = "OTHER_ACTIVE_ORDERS"
    message: str = "该用户存在其他有效订单，请先在用户管理手工调整套餐后再退款"
    status_code: int = 422


@dataclass
class RefundInProgress(BillingError):
    code: str = "REFUND_IN_PROGRESS"
    message: str = "退款正在处理中，请稍后刷新查看结果"
    status_code: int = 409


@dataclass
class RefundFailed(BillingError):
    code: str = "REFUND_FAILED"
    message: str = "支付宝退款失败"
    status_code: int = 502


# 视为「支付成功」的支付宝 trade_status（TRADE_FINISHED 为不可退款终态，同样放行）
_SUCCESS_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")
_CLOSE_STATUS = "TRADE_CLOSED"

# 支付渠道白名单：create_order 入口校验；退款/通知分发按 billing_orders.payment_channel
_PAYMENT_CHANNELS = ("alipay", "wechat")


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


def _first_month_promo_catalog(
    *, enabled: bool, eligible: bool, purchased: bool
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "eligible": eligible,
        "purchased": purchased,
        "code": FIRST_MONTH_PROMO_CODE,
        "name": FIRST_MONTH_PROMO_NAME,
        "priceYuan": FIRST_MONTH_PROMO_PRICE_YUAN,
        "originalPriceYuan": FIRST_MONTH_PROMO_ORIGINAL_YUAN,
        "tokens": FIRST_MONTH_PROMO_TOKENS,
        "validDays": FIRST_MONTH_PROMO_VALID_DAYS,
    }


def get_pricing() -> dict[str, Any]:
    """售卖目录（/api/billing/pricing）：前端不得自写价格，唯一真相在 core/plans.py。"""
    from config import QUOTA_ENFORCEMENT_ENABLED, alipay_enabled, wechat_pay_enabled

    quota_on = bool(QUOTA_ENFORCEMENT_ENABLED)
    return {
        "alipayEnabled": alipay_enabled(),
        "wechatEnabled": wechat_pay_enabled(),
        "items": [
            {
                "planCode": plan.code,
                "planName": plan.name,
                "period": "annual",
                "priceAnnual": plan.price_annual,
            }
            for plan in sellable_plans()
        ],
        # AI 点数充值：仅官方云端开启额度卡口时售卖；自部署/自带 Key 不显示入口。
        "tokenTopup": {
            "enabled": quota_on,
            "tokensPerYuan": TOKEN_TOPUP_TOKENS_PER_YUAN,
            "minYuan": TOKEN_TOPUP_MIN_YUAN,
            "maxYuan": TOKEN_TOPUP_MAX_YUAN,
        },
        "firstMonthPromo": _first_month_promo_catalog(
            enabled=quota_on, eligible=quota_on, purchased=False
        ),
    }


async def get_pricing_for_user(user_id: str | None) -> dict[str, Any]:
    """按用户身份返回售卖目录（叠加新人首月体验包购买资格）。"""
    from db.models import AuthUser, BillingOrder
    from db.session import session_scope
    from services.quota_service import _effective_plan, is_first_month_promo_eligible

    base = get_pricing()
    if not user_id or not base["firstMonthPromo"]["enabled"]:
        return base

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        if user is None:
            eligible = False
            purchased = False
        else:
            purchased = bool(getattr(user, "promo_purchased_at", None))
            if not purchased:
                existing_order = (
                    await session.execute(
                        select(BillingOrder.id).where(
                            BillingOrder.user_id == user_id,
                            BillingOrder.order_type == FIRST_MONTH_PROMO_CODE,
                            BillingOrder.status.in_(_REFUNDABLE_STATUSES),
                        )
                    )
                ).scalar()
                purchased = existing_order is not None
            plan = _effective_plan(user)
            eligible = bool(not purchased and is_first_month_promo_eligible(plan, user))

    base["firstMonthPromo"] = _first_month_promo_catalog(
        enabled=True, eligible=eligible, purchased=purchased
    )
    return base


async def _close_expired_orders(session: Any) -> None:
    """惰性关单：支付有效期已过的 created 订单置 closed（无 cron，读路径顺带收口）。"""
    from db.models import BillingOrder

    await session.execute(
        update(BillingOrder)
        .where(BillingOrder.status == "created", BillingOrder.expires_at < _now())
        .values(status="closed", updated_at=_now())
    )


def _amount_cents(amount: Any) -> int:
    """元 → 分（微信 APIv3 金额单位）：两位小数语义下精确换算，禁止 float。"""
    return int((money(amount, allow_negative=False) * 100).to_integral_value())


def _resolve_payment_channel(channel: str) -> tuple[str, str]:
    resolved = (channel or "alipay").strip().lower()
    if resolved not in _PAYMENT_CHANNELS:
        raise ChannelNotSupported(context={"channel": resolved})
    if resolved == "alipay":
        from config import ALIPAY_ORDER_TIMEOUT, alipay_enabled

        if not alipay_enabled():
            raise AlipayNotConfigured()
        return resolved, ALIPAY_ORDER_TIMEOUT
    from config import WECHAT_PAY_ORDER_TIMEOUT, wechat_pay_enabled

    if not wechat_pay_enabled():
        raise WechatPayNotConfigured()
    return resolved, WECHAT_PAY_ORDER_TIMEOUT


def _dispatch_gateway_order(
    channel: str,
    *,
    snapshot: dict[str, Any],
    subject: str,
    amount: Any,
    expires_at_dt: datetime,
) -> dict[str, Any]:
    if channel == "wechat":
        from services import wechat_pay_client

        node = wechat_pay_client.create_native_order(
            out_trade_no=snapshot["orderNo"],
            total_cents=_amount_cents(amount),
            description=subject,
            expires_at=expires_at_dt,
        )
        code_url = str(node.get("code_url") or "")
        if not code_url:
            logger.error("微信下单响应缺少 code_url order=%s node=%s", snapshot["orderNo"], node)
            raise BillingError(
                code="WECHAT_ORDER_FAILED",
                message="微信下单失败，请稍后再试",
                status_code=502,
            )
        return {**snapshot, "channel": "wechat", "codeUrl": code_url}

    from services import alipay_client

    pay_url = alipay_client.build_page_pay_url(
        out_trade_no=snapshot["orderNo"],
        total_amount=snapshot["amount"],
        subject=subject,
    )
    return {**snapshot, "channel": "alipay", "payUrl": pay_url}


async def create_order(user_id: str, plan_code: str, channel: str = "alipay") -> dict[str, Any]:
    """创建待支付订单并返回支付凭据（支付宝 payUrl / 微信 codeUrl）。

    金额取目录，绝不接受前端传价。微信分支的网关 IO 在**事务外**执行
    （对齐 admin_refund_order 三段式纪律：网关慢 IO 不占事务）——下单失败时
    本地遗留 created 单，由惰性关单收口。
    """
    from core.money import money_text
    from db.models import BillingOrder
    from db.session import session_scope

    channel, order_timeout = _resolve_payment_channel(channel)

    plan = get_plan(plan_code)
    if plan.price_annual is None:
        raise PlanNotSellable(context={"planCode": plan_code})

    subject = f"{plan.name}（年付）"
    async with session_scope() as session:
        await _close_expired_orders(session)
        order = BillingOrder(
            out_trade_no=_generate_out_trade_no(),
            user_id=user_id,
            plan_code=plan.code,
            order_type="plan",
            period="annual",
            payment_channel=channel,
            amount=money(plan.price_annual, allow_negative=False),
            status="created",
            subject=subject,
            expires_at=_now() + _parse_timeout(order_timeout),
        )
        session.add(order)
        await session.flush()
        expires_at_dt = order.expires_at
        snapshot = {
            "orderNo": order.out_trade_no,
            "planCode": plan.code,
            "planName": plan.name,
            "amount": money_text(order.amount),
            "expiresAt": expires_at_dt.isoformat(),
        }

    return _dispatch_gateway_order(
        channel,
        snapshot=snapshot,
        subject=subject,
        amount=plan.price_annual,
        expires_at_dt=expires_at_dt,
    )


async def create_topup_order(
    user_id: str, amount_yuan: int, channel: str = "alipay"
) -> dict[str, Any]:
    """创建 AI 点数充值订单（1 元 = 10,000 点，整数元，余额永久有效）。

    与套餐订单共用支付渠道、通知、退款与审计链路；金额与到账点数由后端按
    ``core/plans.py`` 唯一真相换算，前端只提交整数元，不传价、不传 token 数。
    """
    from config import QUOTA_ENFORCEMENT_ENABLED
    from core.money import money_text
    from db.models import BillingOrder
    from db.session import session_scope

    if not QUOTA_ENFORCEMENT_ENABLED:
        # 自部署/自带 Key 环境不计费也不限量，卖点数没有意义（目录同样不展示入口）
        raise BillingError(
            code="TOPUP_DISABLED",
            message="当前环境未开启 AI 用量计费，无需充值点数",
            status_code=403,
        )
    if isinstance(amount_yuan, bool) or not isinstance(amount_yuan, int):
        raise TopupAmountInvalid(context={"amountYuan": amount_yuan})
    if not (TOKEN_TOPUP_MIN_YUAN <= amount_yuan <= TOKEN_TOPUP_MAX_YUAN):
        raise TopupAmountInvalid(
            message=f"充值金额需为 {TOKEN_TOPUP_MIN_YUAN}–{TOKEN_TOPUP_MAX_YUAN} 元的整数",
            context={
                "amountYuan": amount_yuan,
                "minYuan": TOKEN_TOPUP_MIN_YUAN,
                "maxYuan": TOKEN_TOPUP_MAX_YUAN,
            },
        )

    channel, order_timeout = _resolve_payment_channel(channel)

    tokens = token_topup_tokens(amount_yuan)
    subject = f"AI 点数充值 {tokens} 点"
    async with session_scope() as session:
        await _close_expired_orders(session)
        order = BillingOrder(
            out_trade_no=_generate_out_trade_no(),
            user_id=user_id,
            plan_code="token_topup",
            order_type="token_topup",
            period="one_time",
            payment_channel=channel,
            token_amount=tokens,
            amount=money(amount_yuan, allow_negative=False),
            status="created",
            subject=subject,
            expires_at=_now() + _parse_timeout(order_timeout),
        )
        session.add(order)
        await session.flush()
        expires_at_dt = order.expires_at
        snapshot = {
            "orderNo": order.out_trade_no,
            "orderType": "token_topup",
            "tokenAmount": tokens,
            "planCode": "token_topup",
            "planName": subject,
            "amount": money_text(order.amount),
            "expiresAt": expires_at_dt.isoformat(),
        }

    return _dispatch_gateway_order(
        channel,
        snapshot=snapshot,
        subject=subject,
        amount=amount_yuan,
        expires_at_dt=expires_at_dt,
    )


async def create_first_month_promo_order(
    user_id: str, channel: str = "alipay"
) -> dict[str, Any]:
    """创建「新人首月 AI 体验包」订单（¥1 买 80 万 Token，30 天有效，限购 1 次）。"""
    from config import QUOTA_ENFORCEMENT_ENABLED
    from core.money import money_text
    from db.models import AuthUser, BillingOrder
    from db.session import session_scope
    from services.quota_service import _effective_plan, is_first_month_promo_eligible

    if not QUOTA_ENFORCEMENT_ENABLED:
        raise BillingError(
            code="TOPUP_DISABLED",
            message="当前环境未开启 AI 用量计费，无需购买体验包",
            status_code=403,
        )

    channel, order_timeout = _resolve_payment_channel(channel)
    subject = "新人首月 AI 体验包（80万Token·30天）"

    async with session_scope() as session:
        await _close_expired_orders(session)
        user = (
            await session.execute(
                select(AuthUser).where(AuthUser.user_id == user_id).with_for_update()
            )
        ).scalars().first()
        if user is None or not is_first_month_promo_eligible(_effective_plan(user), user):
            raise PromoNotEligible()
        existing_paid = (
            await session.execute(
                select(BillingOrder.id).where(
                    BillingOrder.user_id == user_id,
                    BillingOrder.order_type == FIRST_MONTH_PROMO_CODE,
                    BillingOrder.status.in_(_REFUNDABLE_STATUSES),
                )
            )
        ).scalar()
        if existing_paid is not None:
            raise PromoNotEligible(message="每位用户限购一次新人首月体验包")

        order = BillingOrder(
            out_trade_no=_generate_out_trade_no(),
            user_id=user_id,
            plan_code=FIRST_MONTH_PROMO_CODE,
            order_type=FIRST_MONTH_PROMO_CODE,
            period="one_time",
            payment_channel=channel,
            token_amount=FIRST_MONTH_PROMO_TOKENS,
            amount=money(FIRST_MONTH_PROMO_PRICE_YUAN, allow_negative=False),
            status="created",
            subject=subject,
            expires_at=_now() + _parse_timeout(order_timeout),
        )
        session.add(order)
        await session.flush()
        expires_at_dt = order.expires_at
        snapshot = {
            "orderNo": order.out_trade_no,
            "orderType": FIRST_MONTH_PROMO_CODE,
            "tokenAmount": FIRST_MONTH_PROMO_TOKENS,
            "planCode": FIRST_MONTH_PROMO_CODE,
            "planName": FIRST_MONTH_PROMO_NAME,
            "amount": money_text(order.amount),
            "expiresAt": expires_at_dt.isoformat(),
        }

    return _dispatch_gateway_order(
        channel,
        snapshot=snapshot,
        subject=subject,
        amount=FIRST_MONTH_PROMO_PRICE_YUAN,
        expires_at_dt=expires_at_dt,
    )


def _order_summary(order: Any) -> dict[str, Any]:
    from core.money import money_text

    return {
        "orderNo": order.out_trade_no,
        "planCode": order.plan_code,
        "orderType": getattr(order, "order_type", None) or "plan",
        "tokenAmount": order.token_amount,
        "subject": order.subject,
        "amount": money_text(order.amount),
        "status": order.status,
        "channel": getattr(order, "payment_channel", "alipay"),
        "tradeNo": order.trade_no or None,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "activatedAt": order.activated_at.isoformat() if order.activated_at else None,
        "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
    }


async def get_order_for_user(user_id: str, out_trade_no: str) -> dict[str, Any]:
    """本人订单详情（收银台回跳/微信扫码弹窗的轮询端点）；顺带惰性关单。

    微信渠道 created 单先向微信**查单兜底**（网关 IO 在事务外）：回调丢失时
    防止「钱已付但本地一直 created」——这也是前端「我已支付」按钮的实现。
    """
    from db.models import BillingOrder
    from db.session import session_scope

    resource = await _wechat_query_created_resource(user_id, out_trade_no)

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
        if resource is not None:
            result = await _settle_wechat_success(
                session,
                order,
                resource,
                payload={"source": "query_fallback", "resource": resource},
            )
            if result == "processed":
                # CAS 是 Core UPDATE，ORM 对象属性已过期；重读拿最新状态出摘要
                await session.refresh(order)
                logger.info("微信订单查单兜底结算成功 order=%s", out_trade_no)
        return _order_summary(order)


async def _wechat_query_created_resource(user_id: str, out_trade_no: str) -> dict[str, Any] | None:
    """向微信查单兜底，仅微信渠道且本地 created 时调用；失败/未配置一律返回 None（不阻塞轮询）。

    返回值是微信交易资源（与回调解密后的 resource 同 schema），结算前仍走金额比对。
    """
    from config import WECHAT_PAY_MCH_ID
    from db.models import BillingOrder
    from db.session import session_scope
    from services import wechat_pay_client

    async with session_scope() as session:
        order = (
            await session.execute(
                select(BillingOrder).where(
                    BillingOrder.out_trade_no == out_trade_no,
                    BillingOrder.user_id == user_id,
                    BillingOrder.payment_channel == "wechat",
                    BillingOrder.status == "created",
                )
            )
        ).scalars().first()
        if order is None:
            return None

    try:
        resource = wechat_pay_client.query_order(out_trade_no)
    except wechat_pay_client.WechatPayProtocolError as exc:
        logger.warning("微信查单兜底失败 order=%s error=%s", out_trade_no, exc)
        return None
    # 商户号不符的响应不可信（理论上不会发生），丢弃
    if str(resource.get("mchid") or "") != WECHAT_PAY_MCH_ID:
        logger.error("微信查单响应商户号不符 order=%s mchid=%s", out_trade_no, resource.get("mchid"))
        return None
    if wechat_pay_client.map_trade_state(str(resource.get("trade_state") or "")) != "PAID":
        return None
    return resource


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
# 微信支付回调与查单兜底（幂等核心）
#
# 应答语义与支付宝相反：支付宝永远 200 + success/failure 文本；微信 v3 只看
# HTTP 状态码——200 即停止重试，所以失败（验签/解密/金额不符/订单冲突）必须
# 返回非 200，否则通知被静默丢弃（visanote 早期事故）。路由层负责映射。
# ---------------------------------------------------------------------------

# 允许补结算的本地状态：closed 单若网关证据显示已支付（回调丢失且过了本地
# 有效期被惰性关单），以网关为准补结算（补单），大声留痕
_WECHAT_SETTLE_STATUSES = ("created", "closed")


def _parse_wechat_time(value: str) -> datetime | None:
    """微信 RFC3339 时间戳（如 2026-11-19T12:00:00+08:00）→ datetime。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _settle_wechat_success(
    session: Any,
    order: Any,
    resource: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> str:
    """微信「支付成功」结算内核（通知与查单兜底共用），返回处理结论。

    调用方保证 resource 已是可信证据（验签后的解密资源 / 商户号匹配的查单
    响应）。金额仍在此严格比对（分 vs 订单两位小数金额），不符绝不激活。
    FOR UPDATE 重读后基于最新状态判定，与通知/轮询并发只有一条 CAS 生效。
    """
    from db.models import BillingOrder

    fresh = (
        await session.execute(
            select(BillingOrder).where(BillingOrder.id == order.id).with_for_update()
        )
    ).scalars().first()
    if fresh is None:
        return "order_not_found"

    try:
        notified_cents = int(str((resource.get("amount") or {}).get("total") or ""))
    except (ValueError, TypeError):
        return "rejected_amount"
    if notified_cents != _amount_cents(fresh.amount):
        # 金额不符绝不激活（部分支付/篡改场景），留档人工处理
        logger.error(
            "微信通知金额与订单不符 order=%s 订单额=%s分 通知额=%s分",
            fresh.out_trade_no, _amount_cents(fresh.amount), notified_cents,
        )
        return "rejected_amount"

    if fresh.status in ("paid", "activated"):
        return "duplicate"
    if fresh.status not in _WECHAT_SETTLE_STATUSES:
        logger.error("微信订单 %s 状态 %s 收到成功证据，需人工核对", fresh.out_trade_no, fresh.status)
        return "rejected_status_conflict"
    if fresh.status == "closed":
        logger.warning(
            "微信订单 %s 本地已关单但网关证据为已支付，按网关证据补结算（补单）",
            fresh.out_trade_no,
        )

    now = _now()
    paid = await session.execute(
        update(BillingOrder)
        .where(BillingOrder.id == fresh.id, BillingOrder.status.in_(_WECHAT_SETTLE_STATUSES))
        .values(
            status="paid",
            trade_no=str(resource.get("transaction_id") or ""),
            buyer_logon_id="",
            notify_payload=payload,
            paid_at=now,
            updated_at=now,
        )
    )
    if paid.rowcount != 1:
        return "duplicate"
    await _fulfill_paid_order(session, fresh)
    await session.execute(
        update(BillingOrder)
        .where(BillingOrder.id == fresh.id, BillingOrder.status == "paid")
        .values(status="activated", activated_at=now, updated_at=_now())
    )
    return "processed"


def _wechat_parse_notify(
    headers: dict[str, str], raw_body: str
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """验签 + 解密回调信封，返回 (sign_verified, envelope, resource)。

    验签失败只返回原始信封（不可信，不解密）；验签通过但解密失败时
    resource 为空 dict，调用方按 rejected_decrypt 拒绝。
    """
    from services import wechat_pay_client

    sign_verified = wechat_pay_client.verify_notify_signature(
        str(headers.get("wechatpay-timestamp") or ""),
        str(headers.get("wechatpay-nonce") or ""),
        raw_body,
        str(headers.get("wechatpay-signature") or ""),
        str(headers.get("wechatpay-serial") or ""),
    )
    envelope: dict[str, Any] = {}
    resource: dict[str, Any] = {}
    try:
        parsed = json.loads(raw_body)
        if isinstance(parsed, dict):
            envelope = parsed
            maybe = envelope.get("resource")
            if isinstance(maybe, dict):
                resource = maybe
    except ValueError:
        envelope = {}
    if not sign_verified:
        return False, envelope, {}

    try:
        plain = wechat_pay_client.decrypt_notify_resource(
            ciphertext=str(resource.get("ciphertext") or ""),
            associated_data=str(resource.get("associated_data") or ""),
            nonce=str(resource.get("nonce") or ""),
        )
        decrypted = json.loads(plain)
        if not isinstance(decrypted, dict):
            raise ValueError("解密结果不是 JSON 对象")
        return True, envelope, decrypted
    except Exception as exc:  # noqa: BLE001 — 解密/解析失败统一按空资源拒绝
        logger.error("微信通知解密失败 error=%s", exc)
        return True, envelope, {}


def _wechat_notify_log_form(
    envelope: dict[str, Any],
    resource: dict[str, Any],
    headers: dict[str, str],
    *,
    out_trade_no: str = "",
) -> dict[str, Any]:
    """通知落档的规范化载荷（billing_notify_logs.payload）：信封 + 资源 + 验签头。"""
    return {
        "out_trade_no": out_trade_no,
        "trade_no": str(resource.get("transaction_id") or ""),
        "trade_status": str(resource.get("trade_state") or ""),
        "event_type": str(envelope.get("event_type") or ""),
        "summary": str(envelope.get("summary") or ""),
        "resource": resource,
        "headers": {
            "timestamp": str(headers.get("wechatpay-timestamp") or ""),
            "nonce": str(headers.get("wechatpay-nonce") or ""),
            "serial": str(headers.get("wechatpay-serial") or ""),
        },
    }


async def apply_wechat_notify(*, headers: dict[str, str], raw_body: str) -> str:
    """处理微信支付回调通知（TRANSACTION.SUCCESS），返回处理结论字符串。

    返回 processed/duplicate/closed_by_notify/ignored_state 之外的一律由
    路由层回非 200（微信才会重试）。幂等保证同支付宝：CAS + duplicate 落档。
    """
    from config import WECHAT_PAY_APP_ID, WECHAT_PAY_MCH_ID
    from db.models import BillingOrder
    from db.session import session_scope

    sign_verified, envelope, resource = _wechat_parse_notify(headers, raw_body)
    log_form = _wechat_notify_log_form(envelope, resource, headers)
    out_trade_no = str(resource.get("out_trade_no") or "")

    if not sign_verified:
        async with session_scope() as session:
            await _record_notify(session, form=log_form, sign_verified=False, result="rejected_bad_sign")
        logger.warning("微信通知验签失败 out_trade_no=%s", out_trade_no)
        return "rejected_bad_sign"

    if not resource:
        async with session_scope() as session:
            await _record_notify(session, form=log_form, sign_verified=True, result="rejected_decrypt")
        return "rejected_decrypt"

    async with session_scope() as session:
        # 解密成功后重建落档载荷：补上 out_trade_no（验签/解密前的信封不可信，留空）
        log_form = _wechat_notify_log_form(envelope, resource, headers, out_trade_no=out_trade_no)
        if (
            str(resource.get("mchid") or "") != WECHAT_PAY_MCH_ID
            or str(resource.get("appid") or "") != WECHAT_PAY_APP_ID
        ):
            await _record_notify(session, form=log_form, sign_verified=True, result="rejected_app_id")
            logger.error("微信通知商户号/appid 不符 out_trade_no=%s", out_trade_no)
            return "rejected_app_id"

        order = (
            await session.execute(
                select(BillingOrder)
                .where(BillingOrder.out_trade_no == out_trade_no)
                .with_for_update()
            )
        ).scalars().first()
        if order is None:
            await _record_notify(session, form=log_form, sign_verified=True, result="order_not_found")
            logger.warning("微信通知找不到订单 out_trade_no=%s", out_trade_no)
            return "order_not_found"

        trade_state = str(resource.get("trade_state") or "").upper()

        if trade_state == "SUCCESS":
            result = await _settle_wechat_success(session, order, resource, payload=log_form)
            await _record_notify(session, form=log_form, sign_verified=True, result=result)
            return result

        if trade_state == "CLOSED":
            closed = await session.execute(
                update(BillingOrder)
                .where(BillingOrder.id == order.id, BillingOrder.status == "created")
                .values(status="closed", notify_payload=log_form, updated_at=_now())
            )
            result = "closed_by_notify" if closed.rowcount == 1 else "duplicate"
            await _record_notify(session, form=log_form, sign_verified=True, result=result)
            return result

        # NOTPAY/USERPAYING/REFUND 等中间态：只落档（REFUND 终态走退款通知）
        await _record_notify(session, form=log_form, sign_verified=True, result="ignored_state")
        return "ignored_state"


async def apply_wechat_refund_notify(*, headers: dict[str, str], raw_body: str) -> str:
    """处理微信退款结果通知（REFUND.SUCCESS 等），返回处理结论（路由层映射 HTTP 状态）。

    退款终态权威入口：create_refund 同步响应只决定受理（submitted），SUCCESS
    由本通知落档并收回权益（若该订单是用户唯一有效订单）；已成功行重复通知
    幂等跳过。补上了 visanote 缺失的退款回调。
    """
    from db.models import BillingOrder, BillingRefund
    from db.session import session_scope

    sign_verified, envelope, resource = _wechat_parse_notify(headers, raw_body)
    log_form = _wechat_notify_log_form(envelope, resource, headers)
    out_trade_no = str(resource.get("out_trade_no") or "")
    out_request_no = str(resource.get("out_refund_no") or "")
    refund_status = str(resource.get("refund_status") or "").upper()

    if not sign_verified:
        async with session_scope() as session:
            await _record_notify(session, form=log_form, sign_verified=False, result="rejected_bad_sign")
        logger.warning("微信退款通知验签失败 out_refund_no=%s", out_request_no)
        return "rejected_bad_sign"

    if not resource:
        async with session_scope() as session:
            await _record_notify(session, form=log_form, sign_verified=True, result="rejected_decrypt")
        return "rejected_decrypt"

    async with session_scope() as session:
        form = {**_wechat_notify_log_form(envelope, resource, headers, out_trade_no=out_trade_no),
                "trade_status": f"REFUND_{refund_status or 'UNKNOWN'}"}

        refund_row = (
            await session.execute(
                select(BillingRefund).where(BillingRefund.out_request_no == out_request_no).with_for_update()
            )
        ).scalars().first()
        order = (
            await session.execute(
                select(BillingOrder).where(BillingOrder.out_trade_no == out_trade_no).with_for_update()
            )
        ).scalars().first()
        if refund_row is None or order is None:
            await _record_notify(session, form=form, sign_verified=True, result="refund_not_found")
            logger.error(
                "微信退款通知找不到订单/退款行 out_trade_no=%s out_refund_no=%s",
                out_trade_no, out_request_no,
            )
            return "refund_not_found"

        if refund_status == "SUCCESS":
            transitioned = await _settle_wechat_refund_row(
                session, refund_row, refund_status="SUCCESS", node=resource
            )
            result = "refund_processed" if transitioned else "refund_duplicate"
            await _record_notify(session, form=form, sign_verified=True, result=result)
            if transitioned:
                order_type = getattr(order, "order_type", "plan")
                if order_type == "token_topup":
                    await _deduct_topup_refund_tokens_in_session(
                        session,
                        order,
                        refund_row.refund_amount,
                        operator=refund_row.operator_user_id or "billing_refund",
                    )
                elif order_type == FIRST_MONTH_PROMO_CODE:
                    await _revoke_first_month_promo_in_session(
                        session, order.user_id, out_trade_no
                    )
                else:
                    await _revoke_plan_if_sole(order.user_id, out_trade_no)
            return result

        if refund_status in ("ABNORMAL", "CLOSED"):
            await _settle_wechat_refund_row(session, refund_row, refund_status=refund_status, node=resource)
            await _record_notify(session, form=form, sign_verified=True, result="refund_failed")
            return "refund_failed"

        # PROCESSING：受理中，只落档（待后续 SUCCESS 通知）
        await _record_notify(session, form=form, sign_verified=True, result="refund_pending")
        return "refund_pending"


async def _settle_wechat_refund_row(
    session: Any,
    row: Any,
    *,
    refund_status: str,
    node: dict[str, Any],
) -> bool:
    """微信退款行终态落档（session 内）。SUCCESS 且首次转成功返回 True。

    ABNORMAL/CLOSED → failed；PROCESSING → 保持 submitted（raw_response 留快照）。
    """
    if refund_status == "SUCCESS":
        if row.status == "success":
            return False
        row.status = "success"
        row.gateway_code = "SUCCESS"
        row.gateway_msg = ""
        row.raw_response = node
        row.refunded_at = _parse_wechat_time(str((node or {}).get("success_time") or "")) or _now()
        row.updated_at = _now()
        logger.info("微信退款成功 out_request_no=%s", row.out_request_no)
        return True
    if refund_status in ("ABNORMAL", "CLOSED"):
        row.status = "failed"
        row.gateway_code = refund_status[:16]
        row.gateway_msg = f"微信退款{refund_status}"[:255]
        row.raw_response = node
        row.updated_at = _now()
        logger.error("微信退款异常终止 out_request_no=%s status=%s", row.out_request_no, refund_status)
        return False
    # PROCESSING 等受理中
    row.gateway_code = refund_status[:16]
    row.raw_response = node
    row.updated_at = _now()
    return False


# ---------------------------------------------------------------------------
# 支付宝异步通知（幂等核心）
# ---------------------------------------------------------------------------

def _expected_amount(order: Any) -> Decimal:
    return money(order.amount, allow_negative=False)


async def _credit_topup_order(session: Any, order: Any) -> None:
    """充值订单支付成功：把点数记入永久余额并写只增流水（同订单只入账一次）。"""
    from db.models import AiTokenLedger, AuthUser
    from services.quota_service import clear_state_cache

    tokens = int(getattr(order, "token_amount", 0) or 0)
    if tokens <= 0:
        logger.error("充值订单缺少 token_amount order=%s（异常数据，人工排查）", order.out_trade_no)
        return
    existing = (
        await session.execute(
            select(AiTokenLedger.id).where(
                AiTokenLedger.order_no == order.out_trade_no,
                AiTokenLedger.kind == "topup",
            )
        )
    ).scalar()
    if existing is not None:
        return
    await session.execute(
        update(AuthUser)
        .where(AuthUser.user_id == order.user_id)
        .values(token_balance=AuthUser.token_balance + tokens)
    )
    balance_after = int(
        (
            await session.execute(
                select(AuthUser.token_balance).where(AuthUser.user_id == order.user_id)
            )
        ).scalar_one()
        or 0
    )
    session.add(
        AiTokenLedger(
            user_id=order.user_id,
            kind="topup",
            delta_tokens=tokens,
            balance_after=balance_after,
            order_no=order.out_trade_no,
            note=f"AI 点数充值 {tokens} 点",
            created_by=order.user_id,
        )
    )
    clear_state_cache()
    logger.info(
        "AI 点数到账 user=%s order=%s tokens=%s balance=%s",
        order.user_id, order.out_trade_no, tokens, balance_after,
    )


async def _deduct_topup_refund_tokens_in_session(
    session: Any, order: Any, refund_amount: Any, *, operator: str = "billing_refund"
) -> None:
    """退款成功后扣回对应点数（同一订单只扣一次）。

    充值余额按「未消耗部分」退款；扣回后允许短暂为负（退款与消耗并发），
    下一次充值先抵扣，卡口在余额 <= 0 时停止放行新调用。
    """
    from db.models import AiTokenLedger, AuthUser
    from services.quota_service import clear_state_cache

    tokens = int((money(refund_amount) * TOKEN_TOPUP_TOKENS_PER_YUAN).to_integral_value())
    if tokens <= 0:
        return
    existing = (
        await session.execute(
            select(AiTokenLedger.id).where(
                AiTokenLedger.order_no == order.out_trade_no,
                AiTokenLedger.kind == "refund",
            )
        )
    ).scalar()
    if existing is not None:
        return
    await session.execute(
        update(AuthUser)
        .where(AuthUser.user_id == order.user_id)
        .values(token_balance=AuthUser.token_balance - tokens)
    )
    balance_after = int(
        (
            await session.execute(
                select(AuthUser.token_balance).where(AuthUser.user_id == order.user_id)
            )
        ).scalar_one()
        or 0
    )
    session.add(
        AiTokenLedger(
            user_id=order.user_id,
            kind="refund",
            delta_tokens=-tokens,
            balance_after=balance_after,
            order_no=order.out_trade_no,
            note=f"充值退款扣回 {tokens} 点",
            created_by=operator,
        )
    )
    clear_state_cache()
    if balance_after < 0:
        logger.error(
            "充值退款后余额为负 user=%s order=%s balance=%s，需人工核对",
            order.user_id, order.out_trade_no, balance_after,
        )


async def _deduct_topup_refund_tokens(
    out_trade_no: str, refund_amount: Any, *, operator: str = "billing_refund"
) -> None:
    """按订单号扣回充值退款点数（独立事务入口，供同步退款路径使用）。"""
    from db.models import BillingOrder
    from db.session import session_scope

    async with session_scope() as session:
        order = (
            await session.execute(
                select(BillingOrder)
                .where(BillingOrder.out_trade_no == out_trade_no)
                .with_for_update()
            )
        ).scalars().first()
        if order is None or getattr(order, "order_type", "plan") != "token_topup":
            return
        await _deduct_topup_refund_tokens_in_session(
            session, order, refund_amount, operator=operator
        )


async def _activate_first_month_promo(session: Any, order: Any) -> None:
    """新人首月体验包支付成功：发放 80 万 30 天有效期额度并标记限购（同账号只发一次）。"""
    from db.models import AuthUser
    from services.quota_service import clear_state_cache

    user = (
        await session.execute(
            select(AuthUser).where(AuthUser.user_id == order.user_id).with_for_update()
        )
    ).scalars().first()
    if user is None:
        logger.error("新人体验包激活找不到用户行 user_id=%s order=%s", order.user_id, order.out_trade_no)
        return
    if getattr(user, "promo_purchased_at", None):
        logger.error(
            "用户已购买过新人体验包，跳过重复发放 user=%s order=%s",
            order.user_id, order.out_trade_no,
        )
        return
    tokens = int(getattr(order, "token_amount", 0) or FIRST_MONTH_PROMO_TOKENS)
    expires_at = (_today() + timedelta(days=FIRST_MONTH_PROMO_VALID_DAYS)).isoformat()
    user.promo_token_balance = tokens
    user.promo_expires_at = expires_at
    user.promo_purchased_at = _now().isoformat()
    clear_state_cache()
    logger.info(
        "新人体验包到账 user=%s order=%s tokens=%s expires=%s",
        order.user_id, order.out_trade_no, tokens, expires_at,
    )


async def _fulfill_paid_order(session: Any, order: Any) -> None:
    """按 order_type 分发履约动作（支付宝/微信回调与查单兜底共用）。"""
    order_type = getattr(order, "order_type", "plan")
    if order_type == "token_topup":
        await _credit_topup_order(session, order)
    elif order_type == FIRST_MONTH_PROMO_CODE:
        await _activate_first_month_promo(session, order)
    else:
        await _activate_plan(session, order.user_id, get_plan(order.plan_code))


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
            await _fulfill_paid_order(session, order)
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


# ---------------------------------------------------------------------------
# 管理端：订单列表 / 详情 / 全额退款 / 网关只读校准（/console 订单页）
#
# 纪律（对齐本模块头部 A 档落点）：
# - 退款金额唯一来源 = 订单 amount，绝不接受前端传退款金额；
# - 退款原因必填（沿用 *_REASON_REQUIRED 惯例），管理动作另在路由层写 admin_audit_logs；
# - 幂等：out_request_no 唯一约束 + 网关按同号幂等，双击/重试只退一次钱；
# - 权益收回只在该订单是用户唯一有效订单时执行（多单先拒，人工走用户管理 PATCH）。
# ---------------------------------------------------------------------------

_REFUNDABLE_STATUSES = ("paid", "activated")
# submitted 行的保鲜窗口：窗口内视为在途拒绝并发重入；超窗视为上次调用中断，可安全重试
# （支付宝对同一 out_request_no 幂等，重试不会二次扣款）
_SUBMITTED_FRESH_SECONDS = 600.0


def _refund_out_request_no(out_trade_no: str) -> str:
    return f"{out_trade_no}R1"


def _refund_summary(row: Any) -> dict[str, Any]:
    from core.money import money_text

    return {
        "outRequestNo": row.out_request_no,
        "status": row.status,
        "refundAmount": money_text(row.refund_amount),
        "reason": row.reason,
        "operatorUserId": row.operator_user_id,
        "gatewayCode": row.gateway_code or None,
        "gatewaySubCode": row.gateway_sub_code or None,
        "gatewayMsg": row.gateway_msg or None,
        "refundedAt": row.refunded_at.isoformat() if row.refunded_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def admin_list_orders(
    query: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """管理端订单列表：订单号/用户ID/支付宝流水号模糊 + 状态筛选 + 分页。

    退款信息由 billing_refunds 派生（最新一条），orders.status 不存 refunded。
    """
    from core.money import money_text
    from db.models import BillingOrder, BillingRefund
    from db.session import session_scope

    async with session_scope() as session:
        await _close_expired_orders(session)
        stmt = select(BillingOrder)
        q = (query or "").strip()
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                BillingOrder.out_trade_no.ilike(like)
                | BillingOrder.user_id.ilike(like)
                | BillingOrder.trade_no.ilike(like)
            )
        status_clean = (status or "").strip()
        if status_clean:
            stmt = stmt.where(BillingOrder.status == status_clean)
        total = (
            (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
            or 0
        )
        rows = (
            (
                await session.execute(
                    stmt.order_by(BillingOrder.created_at.desc(), BillingOrder.out_trade_no)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        # 批量取本页订单的退款记录，避免逐行 N+1；按时间倒序 setdefault 留最新一条
        order_nos = [row.out_trade_no for row in rows]
        latest_refunds: dict[str, Any] = {}
        if order_nos:
            refund_rows = (
                (
                    await session.execute(
                        select(BillingRefund)
                        .where(BillingRefund.out_trade_no.in_(order_nos))
                        .order_by(BillingRefund.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for refund_row in refund_rows:
                latest_refunds.setdefault(refund_row.out_trade_no, refund_row)

        items: list[dict[str, Any]] = []
        for row in rows:
            item = _order_summary(row)
            item["userId"] = row.user_id
            latest = latest_refunds.get(row.out_trade_no)
            item["refund"] = _refund_summary(latest) if latest is not None else None
            items.append(item)
        return {"items": items, "total": int(total), "page": page, "pageSize": page_size}


async def admin_get_order(out_trade_no: str) -> dict[str, Any]:
    """管理端订单详情：订单 + 全部退款记录 + 通知日志最近 20 条（只增表只读展示）。"""
    from db.models import BillingNotifyLog, BillingOrder, BillingRefund
    from db.session import session_scope

    async with session_scope() as session:
        await _close_expired_orders(session)
        order = (
            await session.execute(
                select(BillingOrder).where(BillingOrder.out_trade_no == out_trade_no)
            )
        ).scalars().first()
        if order is None:
            raise OrderNotFound()
        refund_rows = (
            (
                await session.execute(
                    select(BillingRefund)
                    .where(BillingRefund.out_trade_no == out_trade_no)
                    .order_by(BillingRefund.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        notify_rows = (
            (
                await session.execute(
                    select(BillingNotifyLog)
                    .where(BillingNotifyLog.out_trade_no == out_trade_no)
                    .order_by(BillingNotifyLog.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        payload = _order_summary(order)
        payload["userId"] = order.user_id
        payload["buyerLogonId"] = order.buyer_logon_id or None
        payload["refunds"] = [_refund_summary(r) for r in refund_rows]
        payload["notifyLogs"] = [
            {
                "tradeStatus": n.trade_status,
                "signVerified": n.sign_verified,
                "result": n.result,
                "createdAt": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notify_rows
        ]
        return payload


async def _validate_refundable(out_trade_no: str, reason: str) -> dict[str, Any]:
    """退款前置校验（本地可判定的全部规则），返回网关调用所需快照。

    充值订单按「未消耗余额」计算可退金额：只退当前余额覆盖的部分，已消耗的
    token 不退；套餐订单仍走整单全额退款 + 权益收回。
    """
    from db.models import AuthUser, BillingOrder
    from db.session import session_scope

    if not (reason or "").strip():
        raise RefundReasonRequired()

    async with session_scope() as session:
        order = (
            await session.execute(
                select(BillingOrder).where(BillingOrder.out_trade_no == out_trade_no)
            )
        ).scalars().first()
        if order is None:
            raise OrderNotFound()
        if order.status not in _REFUNDABLE_STATUSES:
            raise OrderNotRefundable(context={"status": order.status})

        order_type = getattr(order, "order_type", "plan")
        if order_type == "token_topup":
            from db.models import BillingRefund

            existing = (
                await session.execute(
                    select(BillingRefund).where(
                        BillingRefund.out_request_no == _refund_out_request_no(out_trade_no)
                    )
                )
            ).scalars().first()
            if existing is not None:
                # 幂等重放：沿用首次落档金额。退款成功后余额已减少，按当前余额
                # 重算会得到 0 并误报「无可退余额」，破坏「重复点击幂等」。
                existing_amount = money(existing.refund_amount, allow_negative=False)
                return {
                    "user_id": order.user_id,
                    "trade_no": order.trade_no or "",
                    "channel": getattr(order, "payment_channel", "alipay"),
                    "amount": money(order.amount, allow_negative=False),
                    "refund_amount": existing_amount,
                    "refundable_tokens": int(
                        (existing_amount * TOKEN_TOPUP_TOKENS_PER_YUAN).to_integral_value()
                    ),
                    "order_type": "token_topup",
                }
            balance = int(
                (
                    await session.execute(
                        select(AuthUser.token_balance).where(
                            AuthUser.user_id == order.user_id
                        )
                    )
                ).scalar()
                or 0
            )
            refundable = min(int(order.token_amount or 0), max(0, balance))
            # 金额精确到分（1 分 = 100 点）；不足 1 分的零头不参与退款。
            refundable = (refundable // 100) * 100
            if refundable <= 0:
                raise TopupNotRefundable(context={"orderNo": out_trade_no})
            return {
                "user_id": order.user_id,
                "trade_no": order.trade_no or "",
                "channel": getattr(order, "payment_channel", "alipay"),
                "amount": money(order.amount, allow_negative=False),
                "refund_amount": money(Decimal(refundable) / TOKEN_TOPUP_TOKENS_PER_YUAN),
                "refundable_tokens": refundable,
                "order_type": "token_topup",
            }

        if order_type == FIRST_MONTH_PROMO_CODE:
            return {
                "user_id": order.user_id,
                "trade_no": order.trade_no or "",
                "channel": getattr(order, "payment_channel", "alipay"),
                "amount": money(order.amount, allow_negative=False),
                "refund_amount": money(order.amount, allow_negative=False),
                "order_type": FIRST_MONTH_PROMO_CODE,
            }

        # 多有效订单拒绝：权益收回只在唯一有效**套餐**订单时可自动执行（跨套餐叠加靠人工）。
        # 点数充值与套餐正交（收回只动 plan_code/expires/anchor，不碰 token_balance），
        # 充值单不得拦套餐退款——否则点数耗尽的充值单会把套餐退款永久死锁。
        other_active = (
            await session.execute(
                select(func.count())
                .select_from(BillingOrder)
                .where(
                    BillingOrder.user_id == order.user_id,
                    BillingOrder.order_type == "plan",
                    BillingOrder.status.in_(_REFUNDABLE_STATUSES),
                    BillingOrder.out_trade_no != out_trade_no,
                )
            )
        ).scalar() or 0
        if other_active > 0:
            raise OtherActiveOrders(
                context={"userId": order.user_id, "activeOrders": int(other_active)}
            )
        return {
            "user_id": order.user_id,
            "trade_no": order.trade_no or "",
            "channel": getattr(order, "payment_channel", "alipay"),
            "amount": money(order.amount, allow_negative=False),
            "refund_amount": money(order.amount, allow_negative=False),
            "order_type": "plan",
        }


async def _ensure_submitted_refund_row(
    out_trade_no: str,
    *,
    order_info: dict[str, Any],
    operator_user_id: str,
    reason: str,
) -> str:
    """确保存在 submitted 状态的退款行，返回行 id。

    - 已有 success 行：返回空串（调用方按幂等成功处理）；
    - 已有保鲜期内 submitted 行：抛 REFUND_IN_PROGRESS（并发重入）；
    - failed / 超窗 submitted：复用该行重试（网关对同号幂等，不会二次退款）。
    并发双击同时插入由 out_request_no 唯一约束兜底。
    """
    import sqlalchemy as sa

    from core.money import money_text
    from db.models import BillingRefund
    from db.session import session_scope

    out_request_no = _refund_out_request_no(out_trade_no)
    async with session_scope() as session:
        row = (
            await session.execute(
                select(BillingRefund).where(BillingRefund.out_request_no == out_request_no)
            )
        ).scalars().first()
        inserted: Any = None
        if row is None:
            inserted = BillingRefund(
                out_trade_no=out_trade_no,
                trade_no=order_info["trade_no"],
                out_request_no=out_request_no,
                refund_amount=order_info.get("refund_amount") or order_info["amount"],
                status="submitted",
                operator_user_id=operator_user_id,
                reason=reason,
            )
            session.add(inserted)
            try:
                await session.flush()
            except sa.exc.IntegrityError:
                # 并发双击：另一请求刚插入同一行，回滚后按「已有行」重走判断
                await session.rollback()
                inserted = None
                row = (
                    await session.execute(
                        select(BillingRefund).where(
                            BillingRefund.out_request_no == out_request_no
                        )
                    )
                ).scalars().first()
                if row is None:
                    raise
        if row is not None:
            _check_refund_row_reusable(row)
            if row.status == "success":
                return ""
            return row.id
        return inserted.id


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite 方言返回 naive datetime，统一补 UTC 以便与 _now()（aware）相减。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _check_refund_row_reusable(row: Any) -> None:
    if row.status == "success":
        return
    if row.status == "submitted":
        created = _as_aware(row.created_at)
        age = (_now() - created).total_seconds() if created else 0.0
        if age < _SUBMITTED_FRESH_SECONDS:
            raise RefundInProgress()
    # failed 或超窗 submitted：允许重试（行保持 submitted，网关同号幂等）


async def _mark_refund_result(
    out_request_no: str,
    *,
    node: dict[str, Any] | None,
    error: str,
    gateway_code: str = "",
    sub_code: str = "",
) -> None:
    """网关返回后落档退款行终态（success / failed），raw_response 留原始业务节点。"""
    from datetime import datetime as _dt

    from db.models import BillingRefund
    from db.session import session_scope

    async with session_scope() as session:
        row = (
            await session.execute(
                select(BillingRefund).where(BillingRefund.out_request_no == out_request_no)
            )
        ).scalars().first()
        if row is None:
            logger.error("退款行丢失 %s（异常，人工排查）", out_request_no)
            return
        row.updated_at = _now()
        if node is not None:
            row.status = "success"
            row.gateway_code = str(node.get("code") or "10000")
            row.gateway_msg = str(node.get("msg") or "")
            row.gateway_sub_code = str(node.get("sub_code") or node.get("subCode") or "")
            row.raw_response = node
            gmt_refund = str(node.get("gmtRefundPay") or "").strip()
            if gmt_refund:
                try:
                    row.refunded_at = _dt.fromisoformat(gmt_refund + "+08:00")
                except ValueError:
                    row.refunded_at = _now()
            else:
                row.refunded_at = _now()
        else:
            row.status = "failed"
            row.gateway_code = gateway_code[:16]
            row.gateway_sub_code = sub_code[:64]
            row.gateway_msg = error[:255]


async def _revoke_plan_if_sole(user_id: str, out_trade_no: str) -> bool:
    """退款成功后收回套餐权益；该用户若出现其他有效订单（并发新购）则保留并告警人工处理。"""
    from core.plans import DEFAULT_PLAN_CODE
    from db.models import AuthUser, BillingOrder
    from db.session import session_scope
    from services.quota_service import clear_state_cache

    async with session_scope() as session:
        # 与退款前置守卫同口径：只数其他**套餐**订单，点数充值单不构成收回障碍
        other_active = (
            await session.execute(
                select(func.count())
                .select_from(BillingOrder)
                .where(
                    BillingOrder.user_id == user_id,
                    BillingOrder.order_type == "plan",
                    BillingOrder.status.in_(_REFUNDABLE_STATUSES),
                    BillingOrder.out_trade_no != out_trade_no,
                )
            )
        ).scalar() or 0
        if other_active > 0:
            logger.error(
                "订单 %s 退款成功但用户 %s 出现其他有效订单，权益未自动收回，需人工处理",
                out_trade_no, user_id,
            )
            return False
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalars().first()
        if user is None:
            return False
        user.plan_code = DEFAULT_PLAN_CODE
        user.plan_expires_at = None
        user.plan_anchor_at = None
        clear_state_cache()
        logger.info("退款收回权益 user=%s order=%s → trial", user_id, out_trade_no)
        return True


async def _revoke_first_month_promo_in_session(
    session: Any, user_id: str, out_trade_no: str
) -> None:
    """退款收回新人首月体验包剩余额度；保留 promo_purchased_at 防止退后再刷首单。"""
    from db.models import AuthUser
    from services.quota_service import clear_state_cache

    user = (
        await session.execute(
            select(AuthUser).where(AuthUser.user_id == user_id).with_for_update()
        )
    ).scalars().first()
    if user is None:
        return
    user.promo_token_balance = 0
    user.promo_expires_at = None
    clear_state_cache()
    logger.info("退款收回新人体验包权益 user=%s order=%s", user_id, out_trade_no)


async def _revoke_first_month_promo(user_id: str, out_trade_no: str) -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await _revoke_first_month_promo_in_session(session, user_id, out_trade_no)


async def _revoke_refund_order_benefit(
    order_info: dict[str, Any], out_trade_no: str, *, operator: str
) -> None:
    """按订单类型执行退款后的权益收回/扣回。"""
    order_type = order_info.get("order_type")
    if order_type == "token_topup":
        await _deduct_topup_refund_tokens(
            out_trade_no, order_info["refund_amount"], operator=operator
        )
    elif order_type == FIRST_MONTH_PROMO_CODE:
        await _revoke_first_month_promo(order_info["user_id"], out_trade_no)
    else:
        await _revoke_plan_if_sole(order_info["user_id"], out_trade_no)


async def _admin_refund_wechat_order(
    out_trade_no: str,
    order_info: dict[str, Any],
    out_request_no: str,
    clean_reason: str,
    operator_user_id: str = "",
) -> dict[str, Any]:
    """微信渠道退款：v3 受理（同步响应）→ 终态 SUCCESS/ABNORMAL/CLOSED 落档。

    PROCESSING（受理中）保持 submitted，终态由退款异步通知落档并收回权益；
    同步响应 SUCCESS 时立即落档并收回权益（通知随后幂等跳过）。充值订单按
    未消耗余额退，不退套餐权益。
    """
    from db.models import BillingRefund
    from db.session import session_scope
    from services import wechat_pay_client

    refund_amount = order_info.get("refund_amount") or order_info["amount"]
    total_cents = _amount_cents(order_info["amount"])
    refund_cents = _amount_cents(refund_amount)
    try:
        node = wechat_pay_client.create_refund(
            out_trade_no=out_trade_no,
            out_refund_no=out_request_no,
            total_cents=total_cents,
            refund_cents=refund_cents,
            reason=clean_reason,
        )
    except wechat_pay_client.WechatPayGatewayError as exc:
        await _mark_refund_result(
            out_request_no,
            node=None,
            error=str(exc),
            gateway_code=exc.code[:16],
            sub_code=f"HTTP{exc.http_status}"[:64],
        )
        logger.error(
            "退款被微信拒绝 order=%s outRequestNo=%s code=%s httpStatus=%s msg=%s",
            out_trade_no, out_request_no, exc.code, exc.http_status, exc.message,
        )
        raise RefundFailed(
            message=f"微信支付退款失败：{exc.message or exc.code or '微信未返回原因'}",
            context={"code": exc.code, "httpStatus": exc.http_status, "message": exc.message},
        ) from exc
    except wechat_pay_client.WechatPayProtocolError as exc:
        await _mark_refund_result(out_request_no, node=None, error=str(exc))
        logger.error(
            "退款调用失败（协议/网络）order=%s outRequestNo=%s error=%s",
            out_trade_no, out_request_no, exc,
        )
        raise RefundFailed(context={"message": str(exc)}) from exc

    refund_status = str(node.get("refund_status") or "").upper()
    async with session_scope() as session:
        row = (
            await session.execute(
                select(BillingRefund).where(BillingRefund.out_request_no == out_request_no)
            )
        ).scalars().first()
        if row is None:
            logger.error("退款行丢失 %s（异常，人工排查）", out_request_no)
            transitioned = False
        else:
            transitioned = await _settle_wechat_refund_row(
                session, row, refund_status=refund_status or "PROCESSING", node=node
            )
    if transitioned:
        await _revoke_refund_order_benefit(order_info, out_trade_no, operator=operator_user_id)
    if refund_status in ("ABNORMAL", "CLOSED"):
        raise RefundFailed(
            message=f"微信支付退款失败：{refund_status}",
            context={"refundStatus": refund_status},
        )
    return await admin_get_order(out_trade_no)


async def admin_refund_order(
    out_trade_no: str, operator_user_id: str, reason: str
) -> dict[str, Any]:
    """管理端全额退款（按订单渠道分发）：校验 → 落档 → 网关 → 收回权益。

    三段式：本地校验/落档（快事务）→ 网关慢 IO（事务外）→ 结果落档/权益收回。
    网关失败不触碰订单与权益；重试复用同一行，同号幂等不会二次退款。
    """
    from db.session import session_scope
    from services import alipay_client

    clean_reason = (reason or "").strip()
    order_info = await _validate_refundable(out_trade_no, clean_reason)

    out_request_no = _refund_out_request_no(out_trade_no)
    refund_row_id = await _ensure_submitted_refund_row(
        out_trade_no,
        order_info=order_info,
        operator_user_id=operator_user_id,
        reason=clean_reason,
    )
    if not refund_row_id:
        # 幂等：已有 success 行，直接回放结果。充值订单再兜一次「点数已扣回」
        # （首次扣回失败时，重试仍能补齐；该函数按 order_no+kind 幂等）；
        # 新人体验包同步确保剩余额度已清空。
        if order_info.get("order_type") in ("token_topup", FIRST_MONTH_PROMO_CODE):
            await _revoke_refund_order_benefit(
                order_info, out_trade_no, operator=operator_user_id
            )
        return await admin_get_order(out_trade_no)

    if order_info["channel"] == "wechat":
        return await _admin_refund_wechat_order(
            out_trade_no, order_info, out_request_no, clean_reason, operator_user_id
        )

    try:
        node = alipay_client.refund(
            out_trade_no,
            str(order_info["refund_amount"]),
            out_request_no=out_request_no,
            refund_reason=clean_reason,
        )
    except alipay_client.AlipayGatewayError as exc:
        await _mark_refund_result(
            out_request_no, node=None, error=str(exc), gateway_code=exc.code, sub_code=exc.sub_code
        )
        # 失败必须留可见日志：40004 Business Failed 本身没有信息量，原因在 sub_code/sub_msg
        reason_text = exc.sub_msg or exc.gateway_msg or "支付宝未返回原因"
        logger.error(
            "退款被支付宝拒绝 order=%s outRequestNo=%s code=%s subCode=%s subMsg=%s",
            out_trade_no,
            out_request_no,
            exc.code,
            exc.sub_code,
            reason_text,
        )
        raise RefundFailed(
            message=f"支付宝退款失败：{reason_text}",
            context={"subCode": exc.sub_code, "code": exc.code, "message": reason_text},
        ) from exc
    except alipay_client.AlipayProtocolError as exc:
        await _mark_refund_result(out_request_no, node=None, error=str(exc))
        logger.error(
            "退款调用失败（协议/网络）order=%s outRequestNo=%s error=%s",
            out_trade_no,
            out_request_no,
            exc,
        )
        raise RefundFailed(context={"message": str(exc)}) from exc

    await _mark_refund_result(out_request_no, node=node, error="")
    await _revoke_refund_order_benefit(order_info, out_trade_no, operator=operator_user_id)
    return await admin_get_order(out_trade_no)


async def admin_query_order(out_trade_no: str) -> dict[str, Any]:
    """只读校准：按订单渠道查网关真实状态（支付宝 trade.query / 微信 v3 查单）。

    绝不改本地任何数据。微信查不到该单（404 ORDER_NOT_EXIST）回 None，对齐
    支付宝 ACQ.TRADE_NOT_EXIST 口径。
    """
    from db.models import BillingOrder
    from db.session import session_scope

    async with session_scope() as session:
        order = (
            await session.execute(
                select(BillingOrder).where(BillingOrder.out_trade_no == out_trade_no)
            )
        ).scalars().first()
        if order is None:
            raise OrderNotFound()
        channel = getattr(order, "payment_channel", "alipay")

    if channel == "wechat":
        from services import wechat_pay_client

        try:
            gateway_trade = wechat_pay_client.query_order(out_trade_no)
        except wechat_pay_client.WechatPayGatewayError as exc:
            if exc.http_status == 404:
                gateway_trade = None
            else:
                raise
    else:
        from services import alipay_client

        gateway_trade = alipay_client.query_trade(out_trade_no)
    return {"orderNo": out_trade_no, "gatewayTrade": gateway_trade}
