# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""密码登录：PBKDF2-SHA256 哈希（标准库，零三方依赖）与失败锁定。

存储格式为自描述串 `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`，
迭代次数随串保存，后续调 PASSWORD_PBKDF2_ITERATIONS 不影响旧密码校验。
失败锁定是进程内计数（与 sms_service._SEND_CACHE 同一模式），多实例部署需换集中存储。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from typing import Dict, Tuple

from fastapi import HTTPException

from config import (
    PASSWORD_LOCKOUT_SECONDS,
    PASSWORD_LOGIN_MAX_FAILURES,
    PASSWORD_MIN_LENGTH,
    PASSWORD_PBKDF2_ITERATIONS,
)

_ALGO = "pbkdf2_sha256"
_SALT_BYTES = 16

# key=(kind, value) -> (连续失败次数, 计数窗口截止时间戳)
_FAILURES: Dict[Tuple[str, str], Tuple[int, float]] = {}


def _hash_sync(password: str, salt: bytes, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${digest.hex()}"


async def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    return await asyncio.to_thread(_hash_sync, password, salt, PASSWORD_PBKDF2_ITERATIONS)


async def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations_raw, salt_hex, hash_hex = (encoded or "").split("$", 3)
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    if algo != _ALGO or iterations < 1:
        return False
    result = await asyncio.to_thread(
        hashlib.pbkdf2_hmac, "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(result, expected)


def ensure_password_acceptable(password: str) -> str:
    value = (password or "").strip()
    if len(value) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码长度至少 {PASSWORD_MIN_LENGTH} 位")
    return value


def _prune_expired(now: float) -> None:
    for key, (_, expires_at) in list(_FAILURES.items()):
        if expires_at <= now:
            _FAILURES.pop(key, None)


def assert_not_locked(*keys: Tuple[str, str]) -> None:
    """任一 key 处于锁定期则 429；统一文案不透露剩余精确秒数。"""
    now = time.time()
    _prune_expired(now)
    for key in keys:
        entry = _FAILURES.get(key)
        if entry and entry[0] >= PASSWORD_LOGIN_MAX_FAILURES:
            minutes = max(1, int(PASSWORD_LOCKOUT_SECONDS // 60))
            raise HTTPException(
                status_code=429,
                detail=f"失败次数过多，账号已临时锁定，请约 {minutes} 分钟后重试",
            )


def register_failure(*keys: Tuple[str, str]) -> None:
    now = time.time()
    for key in keys:
        count, _ = _FAILURES.get(key, (0, 0.0))
        _FAILURES[key] = (count + 1, now + PASSWORD_LOCKOUT_SECONDS)


def clear_failures(*keys: Tuple[str, str]) -> None:
    for key in keys:
        _FAILURES.pop(key, None)
