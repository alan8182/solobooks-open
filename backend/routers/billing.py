# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""平台自营收款路由（支付宝电脑网站支付 + 微信支付 Native 扫码）。

三组端点（仿 audit_share 双路由模式）：
- 用户侧 ``/api/billing``：require_auth，定价目录/创建订单/订单查询；
- 支付宝回调 ``/api/billing/alipay``：**免鉴权**——安全性由 RSA2 验签保证
  （仓库首个验签异步回调端点），通知处理幂等由 billing_service.apply_notify 保证；
- 微信回调 ``/api/billing/wechat``：**免鉴权**——安全性由 APIv3 验签保证，
  幂等由 billing_service.apply_wechat_notify 保证。

notify 应答约定（两渠道**语义相反**，勿混用）：
- 支付宝：永远 200 + 纯文本 ``success``/``failure``（支付宝按文本决定重推，最多 8 次）；
- 微信 v3：只看 HTTP 状态码——处理成功（含幂等重复）200 + ``{"code":"SUCCESS"}``，
  任何失败必须**非 200** + ``{"code":"FAIL"}`` 微信才会重试；失败回 200 会静默丢通知。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from config import APP_PUBLIC_URL
from core.plans import TOKEN_TOPUP_MAX_YUAN, TOKEN_TOPUP_MIN_YUAN
from deps.auth import AuthContext, require_auth
from services import billing_service

logger = logging.getLogger(__name__)

owner_router = APIRouter(prefix="/api/billing", tags=["billing"])
public_router = APIRouter(prefix="/api/billing/alipay", tags=["billing"])
public_wechat_router = APIRouter(prefix="/api/billing/wechat", tags=["billing"])

# 视为「已成功受理」的微信通知结论（200 SUCCESS，微信停止重试）
_WECHAT_ACK_RESULTS = frozenset({
    "processed",
    "duplicate",
    "closed_by_notify",
    "ignored_state",
    "refund_processed",
    "refund_duplicate",
    "refund_pending",
    # ABNORMAL/CLOSED 已落档为 failed 终态，重试无意义，同样确认
    "refund_failed",
})
# 失败结论 → HTTP 状态（微信按状态码决定是否重试；401=验签失败）
_WECHAT_FAIL_STATUS = {
    "rejected_bad_sign": 401,
    "rejected_decrypt": 500,
    "rejected_app_id": 500,
    "order_not_found": 500,
    "refund_not_found": 500,
    "rejected_amount": 500,
    "rejected_status_conflict": 500,
    "handler_error": 500,
}


class CreateOrderRequest(BaseModel):
    planCode: str = Field(..., description="套餐编码，见 GET /api/billing/pricing")
    channel: str = Field("alipay", description="支付渠道：alipay（默认）/ wechat")


class CreateTopupRequest(BaseModel):
    amountYuan: int = Field(
        ..., ge=TOKEN_TOPUP_MIN_YUAN, le=TOKEN_TOPUP_MAX_YUAN,
        description="充值金额（整数元）；到账点数由后端按 1 元 = 10,000 点换算",
    )
    channel: str = Field("alipay", description="支付渠道：alipay（默认）/ wechat")


class CreateFirstMonthPromoRequest(BaseModel):
    channel: str = Field("alipay", description="支付渠道：alipay（默认）/ wechat")


# ---------------------------------------------------------------------------
# 用户侧（require_auth）
# ---------------------------------------------------------------------------

@owner_router.get("/pricing")
async def billing_pricing(ctx: AuthContext = Depends(require_auth)):
    """售卖目录：价格为后端唯一真相（core/plans.py），前端禁止自写价格。"""
    return await billing_service.get_pricing_for_user(ctx.user_id)


@owner_router.post("/orders")
async def billing_create_order(
    req: CreateOrderRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """创建待支付订单，返回支付凭据：支付宝 payUrl（location.href 跳收银台）/
    微信 codeUrl（前端渲染二维码）。"""
    return await billing_service.create_order(ctx.user_id, req.planCode, req.channel)


@owner_router.post("/token-topup")
async def billing_create_topup(
    req: CreateTopupRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """创建 AI 点数充值订单（1 元 = 10,000 点，余额永久有效）。"""
    return await billing_service.create_topup_order(ctx.user_id, req.amountYuan, req.channel)


@owner_router.post("/first-month-promo")
async def billing_create_first_month_promo(
    req: CreateFirstMonthPromoRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """创建「新人首月 AI 体验包」订单（¥1 买 80 万 Token，30 天有效，限购 1 次）。"""
    return await billing_service.create_first_month_promo_order(ctx.user_id, req.channel)


@owner_router.get("/orders")
async def billing_list_orders(
    limit: int = 20,
    ctx: AuthContext = Depends(require_auth),
):
    """本人订单列表（我的页面展示 + 回跳轮询兜底）。"""
    return {"items": await billing_service.list_orders_for_user(ctx.user_id, limit)}


@owner_router.get("/orders/{order_no}")
async def billing_get_order(
    order_no: str,
    ctx: AuthContext = Depends(require_auth),
):
    """订单详情（按商户订单号，仅本人可见）：收银台同步回跳后的轮询端点。"""
    return await billing_service.get_order_for_user(ctx.user_id, order_no)


# ---------------------------------------------------------------------------
# 支付宝回调（免鉴权，验签即身份）
# ---------------------------------------------------------------------------

@public_router.post("/notify")
async def alipay_notify(request: Request):
    """异步通知：验签 → 幂等处理 → 落档。永远 200 + 纯文本，防支付宝误判网络异常。"""
    form = await request.form()
    params = {str(k): str(v) for k, v in form.items()}
    try:
        outcome = await billing_service.apply_notify(params)
    except Exception:  # noqa: BLE001 — 处理异常时回 failure 让支付宝重推
        logger.exception("支付宝通知处理异常 out_trade_no=%s", params.get("out_trade_no"))
        outcome = "failure"
    return PlainTextResponse(outcome, status_code=200)


@public_router.get("/return")
async def alipay_return(request: Request):
    """同步跳转：支付完成后浏览器回到这里，302 到产品页由前端轮询订单状态。

    同步跳转不可信（任何人可构造），只做展示引导；权益以 notify 为准。
    """
    order_no = str(request.query_params.get("out_trade_no") or "")
    target = f"{APP_PUBLIC_URL}/mine?orderNo={order_no}" if order_no else f"{APP_PUBLIC_URL}/mine"
    return RedirectResponse(target, status_code=302)


# ---------------------------------------------------------------------------
# 微信支付回调（免鉴权，验签即身份；应答语义与支付宝相反，见模块 docstring）
# ---------------------------------------------------------------------------

def _wechat_ack(result: str) -> JSONResponse:
    """处理结论 → v3 应答体：受理成功 200 SUCCESS；失败非 200 FAIL（触发重试）。"""
    if result in _WECHAT_ACK_RESULTS:
        return JSONResponse({"code": "SUCCESS", "message": "成功"}, status_code=200)
    status = _WECHAT_FAIL_STATUS.get(result, 500)
    return JSONResponse({"code": "FAIL", "message": f"通知处理未成功：{result}"}, status_code=status)


async def _wechat_notify_request(request: Request) -> tuple[dict[str, str], str]:
    headers = {str(k).lower(): str(v) for k, v in request.headers.items()}
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    return headers, raw_body


@public_wechat_router.post("/notify")
async def wechat_notify(request: Request):
    """微信支付结果通知（TRANSACTION.SUCCESS）：验签 → 解密 → 幂等结算。"""
    headers, raw_body = await _wechat_notify_request(request)
    try:
        result = await billing_service.apply_wechat_notify(headers=headers, raw_body=raw_body)
    except Exception:  # noqa: BLE001 — 处理异常回非 200 让微信重试
        logger.exception("微信支付通知处理异常")
        result = "handler_error"
    return _wechat_ack(result)


@public_wechat_router.post("/refund-notify")
async def wechat_refund_notify(request: Request):
    """微信退款结果通知（REFUND.*）：验签 → 解密 → 退款行终态落档（含权益收回）。"""
    headers, raw_body = await _wechat_notify_request(request)
    try:
        result = await billing_service.apply_wechat_refund_notify(headers=headers, raw_body=raw_body)
    except Exception:  # noqa: BLE001 — 处理异常回非 200 让微信重试
        logger.exception("微信退款通知处理异常")
        result = "handler_error"
    return _wechat_ack(result)
