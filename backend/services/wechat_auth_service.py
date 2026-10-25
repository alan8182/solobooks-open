# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""WeChat code2session service."""
from __future__ import annotations

import logging
import time
from uuid import uuid4

import certifi
import httpx
from fastapi import HTTPException

from config import WECHAT_APPID, WECHAT_SECRET

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
logger = logging.getLogger(__name__)


def _mask(value: str, prefix: int = 3, suffix: int = 3) -> str:
    """Mask sensitive string for safe logging."""
    if not value:
        return ""
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}***{value[-suffix:]}"


async def exchange_code_for_openid(code: str, trace_id: str | None = None) -> dict:
    """Exchange mini-program login code for openid/session_key."""
    effective_trace_id = trace_id or uuid4().hex
    if not code:
        raise HTTPException(status_code=400, detail=f"缺少 code，trace_id={effective_trace_id}")
    if not WECHAT_APPID or not WECHAT_SECRET:
        logger.error(
            "wechat config missing, trace_id=%s appid_set=%s secret_set=%s",
            effective_trace_id,
            bool(WECHAT_APPID),
            bool(WECHAT_SECRET),
        )
        raise HTTPException(
            status_code=503,
            detail=f"服务端未配置 WECHAT_APPID 或 WECHAT_SECRET，trace_id={effective_trace_id}",
        )

    params = {
        "appid": WECHAT_APPID,
        "secret": WECHAT_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=10,
            verify=certifi.where(),
            trust_env=False,
        ) as client:
            resp = await client.get(WECHAT_CODE2SESSION_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "wechat code2session http status error, trace_id=%s status=%s elapsed_ms=%s appid=%s code=%s response=%s",
            effective_trace_id,
            exc.response.status_code,
            elapsed_ms,
            _mask(WECHAT_APPID),
            _mask(code),
            exc.response.text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=f"微信登录服务请求失败（HTTP状态异常），trace_id={effective_trace_id}",
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "wechat code2session network error, trace_id=%s elapsed_ms=%s appid=%s code=%s error=%s",
            effective_trace_id,
            elapsed_ms,
            _mask(WECHAT_APPID),
            _mask(code),
            repr(exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"微信登录服务请求失败（网络异常），trace_id={effective_trace_id}",
        ) from exc
    except ValueError as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "wechat code2session decode error, trace_id=%s elapsed_ms=%s appid=%s code=%s body=%s",
            effective_trace_id,
            elapsed_ms,
            _mask(WECHAT_APPID),
            _mask(code),
            resp.text[:500] if "resp" in locals() else "",
        )
        raise HTTPException(
            status_code=502,
            detail=f"微信登录服务返回解析失败，trace_id={effective_trace_id}",
        ) from exc

    if data.get("errcode"):
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        err_code = data.get("errcode")
        err_msg = data.get("errmsg", "unknown error")
        logger.warning(
            "wechat code2session business error, trace_id=%s errcode=%s errmsg=%s elapsed_ms=%s appid=%s code=%s",
            effective_trace_id,
            err_code,
            err_msg,
            elapsed_ms,
            _mask(WECHAT_APPID),
            _mask(code),
        )
        raise HTTPException(
            status_code=401,
            detail=f"微信登录失败: errcode={err_code}, errmsg={err_msg}, trace_id={effective_trace_id}",
        )

    openid = data.get("openid")
    if not openid:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "wechat code2session missing openid, trace_id=%s elapsed_ms=%s data_keys=%s",
            effective_trace_id,
            elapsed_ms,
            list(data.keys()),
        )
        raise HTTPException(status_code=401, detail=f"微信登录失败: 未返回 openid，trace_id={effective_trace_id}")

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "wechat code2session success, trace_id=%s elapsed_ms=%s openid=%s",
        effective_trace_id,
        elapsed_ms,
        _mask(openid),
    )
    return data
