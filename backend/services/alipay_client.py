# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝开放平台协议层（电脑网站支付 alipay.trade.page.pay，公钥模式 RSA2）。

不引官方 SDK：官方 Python SDK 多年未维护且拉一串旧依赖；本仓库惯例是协议薄层
自实现（JWT HS256、PBKDF2 均为标准库手写），RSA2 用已钉版的 cryptography。

签名规则（支付宝统一）：
- 待签串 = 除 sign/sign_type 外全部参数，按 key ASCII 升序拼 ``k=v`` 再用 & 相连，
  值取「原始值」（不 URL 编码）；空值参数不参与签名；
- 请求用应用私钥签名（RSA2 = SHA256withRSA），验通知用支付宝公钥。
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

from config import (
    ALIPAY_APP_ID,
    ALIPAY_GATEWAY,
    ALIPAY_NOTIFY_URL,
    ALIPAY_ORDER_TIMEOUT,
    ALIPAY_RETURN_URL,
)

logger = logging.getLogger(__name__)

# 北京时间：支付宝协议字段（timestamp）按 GMT+8 传
_TZ_CN = timezone(timedelta(hours=8))


class AlipayProtocolError(Exception):
    """协议构造/验签失败（配置缺失、密钥格式错误、验签不通过）。"""


def _normalize_private_key(raw: str) -> bytes:
    """把 .env 里的私钥归一化为 PEM bytes：容忍单行 base64 与转义 \n 两种存法。"""
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode("ascii")
    # 单行 base64：按 64 字符折行包上 PKCS8 头
    body = text.replace(" ", "").replace("\n", "")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----".encode("ascii")


def _load_private_key(raw: str) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(_normalize_private_key(raw), password=None)
    except (ValueError, TypeError) as exc:
        raise AlipayProtocolError("ALIPAY_PRIVATE_KEY 格式无效（需 PKCS8 PEM 或单行 base64）") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise AlipayProtocolError("ALIPAY_PRIVATE_KEY 必须是 RSA 私钥")
    return key


def _normalize_public_key(raw: str) -> bytes:
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode("ascii")
    body = text.replace(" ", "").replace("\n", "")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{wrapped}\n-----END PUBLIC KEY-----".encode("ascii")


def _sign_content(params: dict[str, Any]) -> str:
    """待签串：剔除 sign/sign_type 与空值，key 升序 k=v & 相连（值不转义）。"""
    items = sorted(
        (key, str(value))
        for key, value in params.items()
        if key not in ("sign", "sign_type") and value is not None and str(value) != ""
    )
    return "&".join(f"{key}={value}" for key, value in items)


def sign_params(params: dict[str, Any], private_key_raw: str | None = None) -> str:
    """对参数集生成 RSA2 签名（base64）。生产用 .env 私钥；测试可注入临时密钥。"""
    from config import ALIPAY_PRIVATE_KEY

    key = _load_private_key(private_key_raw or ALIPAY_PRIVATE_KEY)
    try:
        signature = key.sign(
            _sign_content(params).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001
        raise AlipayProtocolError(f"签名失败: {exc}") from exc
    return base64.b64encode(signature).decode("ascii")


def verify_notify_signature(params: dict[str, Any], public_key_raw: str | None = None) -> bool:
    """验支付宝异步通知签名：不合格式/验签失败一律 False（调用方拒绝处理）。

    通知参数是服务端已 URL 解码的 form 字典；待签串与请求侧同规则，
    剔除 sign/sign_type 后按 key 排序拼原始值。
    """
    from config import ALIPAY_PUBLIC_KEY

    raw_public = public_key_raw or ALIPAY_PUBLIC_KEY
    sign = str(params.get("sign") or "").strip()
    if not sign or not raw_public:
        return False
    try:
        public_key = serialization.load_pem_public_key(_normalize_public_key(raw_public))
        public_key.verify(
            base64.b64decode(sign),
            _sign_content(params).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def build_page_pay_url(
    *,
    out_trade_no: str,
    total_amount: str,
    subject: str,
    private_key_raw: str | None = None,
    extra_biz: dict[str, Any] | None = None,
) -> str:
    """构造 alipay.trade.page.pay 收银台跳转 URL（GET 直接 302/location 即可）。

    total_amount 必须已是两位小数字符串（core.money.money_text 口径）；
    notify_url/return_url/timeout_express 走公共参数。
    """
    if not ALIPAY_APP_ID:
        raise AlipayProtocolError("ALIPAY_APP_ID 未配置")
    biz_content: dict[str, Any] = {
        "out_trade_no": out_trade_no,
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "total_amount": total_amount,
        "subject": subject,
    }
    if ALIPAY_ORDER_TIMEOUT:
        # timeout_express：收银台倒计时（如 "2h"）
        biz_content["timeout_express"] = ALIPAY_ORDER_TIMEOUT
    if extra_biz:
        biz_content.update(extra_biz)

    params: dict[str, str] = {
        "app_id": ALIPAY_APP_ID,
        "method": "alipay.trade.page.pay",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now(_TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": ALIPAY_NOTIFY_URL,
        "return_url": ALIPAY_RETURN_URL,
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }

    params["sign"] = sign_params(params, private_key_raw)
    query = "&".join(
        f"{key}={quote(params[key], safe='')}" for key in sorted(params) if params[key] != ""
    )
    return f"{ALIPAY_GATEWAY}?{query}"
