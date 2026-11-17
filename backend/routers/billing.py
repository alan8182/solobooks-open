# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""平台自营收款路由（支付宝电脑网站支付）。

两组端点（仿 audit_share 双路由模式）：
- 用户侧 ``/api/billing``：require_auth，定价目录/创建订单/订单查询；
- 支付宝回调 ``/api/billing/alipay``：**免鉴权**——安全性由 RSA2 验签保证
  （仓库首个验签异步回调端点），通知处理幂等由 billing_service.apply_notify 保证。

notify 应答约定：处理成功（含幂等重复）返回纯文本 ``success``，否则 ``failure``
（支付宝会按约定重推，最多 8 次）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from config import APP_PUBLIC_URL
from deps.auth import AuthContext, require_auth
from services import billing_service

logger = logging.getLogger(__name__)

owner_router = APIRouter(prefix="/api/billing", tags=["billing"])
public_router = APIRouter(prefix="/api/billing/alipay", tags=["billing"])


class CreateOrderRequest(BaseModel):
    planCode: str = Field(..., description="套餐编码，见 GET /api/billing/pricing")


# ---------------------------------------------------------------------------
# 用户侧（require_auth）
# ---------------------------------------------------------------------------

@owner_router.get("/pricing")
async def billing_pricing(_ctx: AuthContext = Depends(require_auth)):
    """售卖目录：价格为后端唯一真相（core/plans.py），前端禁止自写价格。"""
    return billing_service.get_pricing()


@owner_router.post("/orders")
async def billing_create_order(
    req: CreateOrderRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """创建待支付订单，返回收银台跳转 URL（前端 location.href 即可）。"""
    return await billing_service.create_order(ctx.user_id, req.planCode)


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
