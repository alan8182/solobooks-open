# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks Software License v2.0.

"""微信开放平台网站应用扫码登录协议层。

与 ``wechat_auth_service``（小程序 code2session）、``wechat_pay_client``（支付）
完全独立：
- 扫码入口：``https://open.weixin.qq.com/connect/qrconnect``（scope=snsapi_login）
- code 换 token：``https://api.weixin.qq.com/sns/oauth2/access_token``
- 用户资料：``https://api.weixin.qq.com/sns/userinfo``（昵称/头像，失败不阻断）

state 采用「随机 nonce + 签名 HttpOnly cookie」：微信 state 上限 128 字节，
不能把 redirect/consent 塞进去；cookie 值带 HMAC，防 OAuth 登录 CSRF / 篡改。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict
from urllib.parse import urlencode

import certifi
import httpx

from config import (
    JWT_SECRET,
    WECHAT_WEB_APPID,
    WECHAT_WEB_REDIRECT_URI,
    WECHAT_WEB_SECRET,
    WECHAT_WEB_STATE_TTL_SECONDS,
)

logger = logging.getLogger(__name__)

QR_CONNECT_URL = "https://open.weixin.qq.com/connect/qrconnect"
OAUTH_ACCESS_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
OAUTH_USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"

# 内嵌 qrconnect iframe 定制样式：去掉自带「微信登录」标题、内框线与重复应用名，收敛二维码尺寸
_QR_IFRAME_CSS = (
    ".impowerBox .title{display:none}"
    ".impowerBox .qrcode{width:200px;margin-top:8px;border:none}"
    ".impowerBox .info{width:220px}"
    ".impowerBox .status{padding:4px 8px}"
    "#wx_default_tip p:nth-child(2){display:none}"
)
_QR_IFRAME_HREF = "data:text/css;base64," + base64.b64encode(
    _QR_IFRAME_CSS.encode("ascii")
).decode("ascii")

_STATE_KEY = f"{JWT_SECRET}:wechat_web_state".encode("utf-8")


class WechatWebAuthError(Exception):
    """微信网页登录协议错误；``code`` 会映射成前端短错误码。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def _mask(value: str, prefix: int = 3, suffix: int = 3) -> str:
    if not value:
        return ""
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}***{value[-suffix:]}"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _sign(payload_b64: str) -> str:
    return hmac.new(_STATE_KEY, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def create_state(
    *,
    redirect: str,
    privacy_accepted_at: str,
    tos_version: str,
    privacy_version: str,
) -> tuple[str, str]:
    """生成 (微信 state, 待写入 HttpOnly cookie 的签名值)。"""
    nonce = secrets.token_urlsafe(24)
    payload = {
        "n": nonce,
        "r": redirect or "/",
        "p": privacy_accepted_at or "",
        "t": tos_version or "v1.0",
        "v": privacy_version or "v1.0",
        "e": int(time.time()) + WECHAT_WEB_STATE_TTL_SECONDS,
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return nonce, f"{payload_b64}.{_sign(payload_b64)}"


def parse_state_cookie(value: str) -> Dict[str, Any]:
    """校验签名与有效期，返回 state payload；失败抛 WechatWebAuthError。"""
    if not value or "." not in value:
        raise WechatWebAuthError("state_invalid", "缺少 state cookie")
    payload_b64, signature = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), signature):
        raise WechatWebAuthError("state_invalid", "state 签名无效")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise WechatWebAuthError("state_invalid", "state 解析失败") from exc
    if not isinstance(payload, dict):
        raise WechatWebAuthError("state_invalid", "state 结构错误")
    expires_at = int(payload.get("e") or 0)
    if expires_at <= int(time.time()):
        raise WechatWebAuthError("state_expired", "state 已过期")
    return payload


def build_authorize_url(state: str) -> str:
    """构造微信扫码页 URL（用户浏览器地址栏打开）。"""
    if not WECHAT_WEB_APPID:
        raise WechatWebAuthError("config_missing", "WECHAT_WEB_APPID 未配置")
    params = {
        "appid": WECHAT_WEB_APPID,
        "redirect_uri": WECHAT_WEB_REDIRECT_URI,
        "response_type": "code",
        "scope": "snsapi_login",
        "state": state,
        "href": _QR_IFRAME_HREF,
    }
    return f"{QR_CONNECT_URL}?{urlencode(params)}#wechat_redirect"


async def exchange_code_for_token(code: str, trace_id: str = "") -> Dict[str, str]:
    """网站应用 OAuth code 换 openid / unionid / access_token。"""
    trace = trace_id or secrets.token_hex(8)
    effective_code = (code or "").strip()
    if not effective_code:
        raise WechatWebAuthError("code_missing", f"缺少 code，trace_id={trace}")
    if not WECHAT_WEB_APPID or not WECHAT_WEB_SECRET:
        logger.error(
            "wechat web oauth config missing, trace_id=%s appid_set=%s secret_set=%s",
            trace,
            bool(WECHAT_WEB_APPID),
            bool(WECHAT_WEB_SECRET),
        )
        raise WechatWebAuthError("config_missing", f"网站应用配置缺失，trace_id={trace}")

    params = {
        "appid": WECHAT_WEB_APPID,
        "secret": WECHAT_WEB_SECRET,
        "code": effective_code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(
            timeout=10,
            verify=certifi.where(),
            trust_env=False,
        ) as client:
            resp = await client.get(OAUTH_ACCESS_TOKEN_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "wechat web oauth http error, trace_id=%s status=%s appid=%s",
            trace,
            exc.response.status_code,
            _mask(WECHAT_WEB_APPID),
        )
        raise WechatWebAuthError("network", f"微信登录服务请求失败，trace_id={trace}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(
            "wechat web oauth network/decode error, trace_id=%s error=%r",
            trace,
            exc,
        )
        raise WechatWebAuthError("network", f"微信登录服务请求失败，trace_id={trace}") from exc

    if data.get("errcode"):
        errcode = str(data.get("errcode"))
        logger.warning(
            "wechat web oauth business error, trace_id=%s errcode=%s errmsg=%s",
            trace,
            errcode,
            data.get("errmsg", ""),
        )
        # 40029/40163/40030：code 无效/已使用/不匹配，统一按需重新扫码处理
        code_name = "code_invalid" if errcode in {"40029", "40163", "40030"} else "wechat_error"
        raise WechatWebAuthError(code_name, f"微信登录失败，trace_id={trace}")

    openid = (data.get("openid") or "").strip()
    if not openid:
        logger.error("wechat web oauth missing openid, trace_id=%s", trace)
        raise WechatWebAuthError("wechat_error", f"微信登录未返回 openid，trace_id={trace}")

    logger.info(
        "wechat web oauth ok, trace_id=%s openid=%s unionid=%s",
        trace,
        _mask(openid),
        _mask((data.get("unionid") or "").strip()),
    )
    return {
        "openid": openid,
        "unionid": (data.get("unionid") or "").strip(),
        "access_token": (data.get("access_token") or "").strip(),
    }


async def fetch_userinfo(access_token: str, openid: str, trace_id: str = "") -> Dict[str, str]:
    """获取昵称/头像；任何失败都只记日志，不阻断登录。"""
    trace = trace_id or secrets.token_hex(8)
    if not access_token or not openid:
        return {}
    params = {"access_token": access_token, "openid": openid, "lang": "zh_CN"}
    try:
        async with httpx.AsyncClient(
            timeout=8,
            verify=certifi.where(),
            trust_env=False,
        ) as client:
            resp = await client.get(OAUTH_USERINFO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode"):
            logger.warning(
                "wechat web userinfo business error, trace_id=%s errcode=%s",
                trace,
                data.get("errcode"),
            )
            return {}
        return {
            "nickname": (data.get("nickname") or "").strip(),
            "avatar": (data.get("headimgurl") or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001 - 资料是可选增强
        logger.warning("wechat web userinfo failed, trace_id=%s error=%r", trace, exc)
        return {}
