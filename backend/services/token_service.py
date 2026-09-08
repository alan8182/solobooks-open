"""JWT token service (HS256, no third-party dependency)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict

from config import JWT_EXPIRES_IN, JWT_SECRET


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _sign(signing_input: str) -> str:
    digest = hmac.new(
        JWT_SECRET.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def create_access_token(payload: Dict[str, Any], expires_in: int | None = None) -> str:
    """Create an HS256 JWT for API authentication."""
    now = int(time.time())
    exp = now + int(expires_in or JWT_EXPIRES_IN)
    header = {"alg": "HS256", "typ": "JWT"}
    body = {**payload, "iat": now, "exp": exp}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{body_b64}"
    signature = _sign(signing_input)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str) -> Dict[str, Any]:
    """Verify and decode JWT payload, raises ValueError on invalid token."""
    try:
        header_b64, body_b64, signature = token.split(".")
    except ValueError as exc:
        raise ValueError("token 格式错误") from exc

    signing_input = f"{header_b64}.{body_b64}"
    if not hmac.compare_digest(_sign(signing_input), signature):
        raise ValueError("token 签名无效")

    try:
        payload = json.loads(_b64url_decode(body_b64))
    except Exception as exc:
        raise ValueError("token 解析失败") from exc

    exp = int(payload.get("exp", 0))
    if exp <= int(time.time()):
        raise ValueError("token 已过期")
    return payload
