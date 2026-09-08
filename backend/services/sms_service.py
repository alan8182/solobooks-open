"""Aliyun Dypnsapi SMS verification service."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
import urllib.parse
import uuid
from typing import Dict

import httpx
from fastapi import HTTPException

from config import (
    ALIYUN_ACCESS_KEY_ID,
    ALIYUN_ACCESS_KEY_SECRET,
    ALIYUN_SMS_CODE_VALID_SECONDS,
    ALIYUN_SMS_INTERVAL_SECONDS,
    ALIYUN_SMS_SCHEME_NAME,
    ALIYUN_SMS_SIGN_NAME,
    ALIYUN_SMS_TEMPLATE_CODE,
)

ALIYUN_SMS_ENDPOINT = "https://dypnsapi.aliyuncs.com/"
ALIYUN_API_VERSION = "2017-05-25"

_SEND_CACHE: Dict[str, float] = {}


def _percent_encode(value: str) -> str:
    return urllib.parse.quote(value, safe="~")


def _build_signature(params: Dict[str, str]) -> str:
    sorted_params = "&".join(f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted(params))
    string_to_sign = f"GET&%2F&{_percent_encode(sorted_params)}"
    key = f"{ALIYUN_ACCESS_KEY_SECRET}&".encode("utf-8")
    digest = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def _base_params(action: str) -> Dict[str, str]:
    return {
        "Action": action,
        "Format": "JSON",
        "Version": ALIYUN_API_VERSION,
        "AccessKeyId": ALIYUN_ACCESS_KEY_ID,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
    }


def _ensure_sms_config() -> None:
    if not ALIYUN_ACCESS_KEY_ID or not ALIYUN_ACCESS_KEY_SECRET:
        raise HTTPException(status_code=503, detail="短信服务未配置 AccessKey")
    if not ALIYUN_SMS_SIGN_NAME or not ALIYUN_SMS_TEMPLATE_CODE:
        raise HTTPException(status_code=503, detail="短信服务未配置签名或模板")


def _validate_phone(phone: str) -> str:
    value = (phone or "").strip()
    if not value.isdigit() or len(value) != 11:
        raise HTTPException(status_code=400, detail="手机号格式错误")
    return value


async def _call_aliyun(params: Dict[str, str]) -> Dict:
    signed = dict(params)
    signed["Signature"] = _build_signature(signed)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALIYUN_SMS_ENDPOINT, params=signed)
        body = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="短信服务网络请求失败") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="短信服务返回解析失败") from exc

    if resp.status_code >= 400:
        # 避免透出签名 URL 与敏感参数，只返回服务侧简要错误。
        msg = body.get("Message") if isinstance(body, dict) else ""
        raise HTTPException(status_code=400, detail=f"短信服务请求失败: {msg or 'HTTP 400'}")

    if body.get("Code") not in ("OK", "Success") and body.get("Success") is not True:
        detail = body.get("Message") or body.get("Code") or "短信服务调用失败"
        raise HTTPException(status_code=400, detail=f"短信服务错误: {detail}")
    return body


async def send_verify_code(phone: str, client_ip: str = "") -> Dict:
    """Send verification code by Aliyun SMS."""
    _ensure_sms_config()
    mobile = _validate_phone(phone)
    now = time.time()
    last_sent = _SEND_CACHE.get(f"phone:{mobile}", 0)
    if now - last_sent < ALIYUN_SMS_INTERVAL_SECONDS:
        wait_seconds = int(ALIYUN_SMS_INTERVAL_SECONDS - (now - last_sent))
        raise HTTPException(status_code=429, detail=f"发送过于频繁，请{wait_seconds}秒后再试")
    if client_ip:
        ip_last_sent = _SEND_CACHE.get(f"ip:{client_ip}", 0)
        if now - ip_last_sent < ALIYUN_SMS_INTERVAL_SECONDS:
            wait_seconds = int(ALIYUN_SMS_INTERVAL_SECONDS - (now - ip_last_sent))
            raise HTTPException(status_code=429, detail=f"请求过于频繁，请{wait_seconds}秒后再试")

    template_param = json.dumps({"code": "##code##", "min": str(max(1, ALIYUN_SMS_CODE_VALID_SECONDS // 60))}, ensure_ascii=False)
    params = _base_params("SendSmsVerifyCode")
    params.update(
        {
            "CountryCode": "86",
            "PhoneNumber": mobile,
            "SignName": ALIYUN_SMS_SIGN_NAME,
            "TemplateCode": ALIYUN_SMS_TEMPLATE_CODE,
            "TemplateParam": template_param,
            "CodeLength": "6",
            "ValidTime": str(ALIYUN_SMS_CODE_VALID_SECONDS),
            "Interval": str(ALIYUN_SMS_INTERVAL_SECONDS),
            "CodeType": "1",
            "AutoRetry": "1",
        }
    )
    if ALIYUN_SMS_SCHEME_NAME:
        params["SchemeName"] = ALIYUN_SMS_SCHEME_NAME

    body = await _call_aliyun(params)
    _SEND_CACHE[f"phone:{mobile}"] = now
    if client_ip:
        _SEND_CACHE[f"ip:{client_ip}"] = now
    model = body.get("Model") or {}
    return {"biz_id": model.get("BizId"), "request_id": body.get("RequestId")}


async def check_verify_code(phone: str, code: str, biz_id: str | None = None) -> None:
    """Check verification code with Aliyun service."""
    _ensure_sms_config()
    mobile = _validate_phone(phone)
    verify_code = (code or "").strip()
    if not verify_code:
        raise HTTPException(status_code=400, detail="验证码不能为空")

    params = _base_params("CheckSmsVerifyCode")
    params.update(
        {
            "PhoneNumber": mobile,
            "VerifyCode": verify_code,
        }
    )
    if biz_id:
        params["BizId"] = biz_id
    if ALIYUN_SMS_SCHEME_NAME:
        params["SchemeName"] = ALIYUN_SMS_SCHEME_NAME

    body = await _call_aliyun(params)
    model = body.get("Model") or {}
    if model.get("VerifyResult") != "PASS":
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
