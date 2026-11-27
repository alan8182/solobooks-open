# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信支付 APIv3 协议层（Native 扫码收款，第二支付渠道）。

- v3/pay/transactions/native：下单，返回 code_url（前端渲染二维码，手机微信扫）；
- v3/pay/transactions/out-trade-no/{no}：查单（回调丢失时的兜底真相源）；
- v3/pay/transactions/out-trade-no/{no}/close：关单；
- v3/refund/domestic/refunds：退款（同步受理，终态以退款异步通知/查退款为准）；
- 回调验签：微信支付公钥模式优先（WECHAT_PAY_PUBLIC_KEY，新版商户平台默认），
  未配置回退平台证书模式（GET /v3/certificates 运行时拉取 + 内存缓存 1h，
  不落盘、免轮换）。

不引官方 SDK：与 alipay_client 同仓库惯例，协议薄层自实现，请求签名
（SHA256withRSA，Authorization: WECHATPAY2-SHA256-RSA2048）与回调解密
（AEAD_AES_256_GCM）均用已钉版的 cryptography。商户号与支付宝收款同一主体，
配置变量口径与 visanote 项目一致（.env 值可直接拷贝）。

移植自 visanote 时固定下来的两条协议事实（均有回归测试）：
- 回调 resource.nonce 是 12 字符随机串，必须按**原始字节**当 GCM IV
  （nonce.encode()）；只有平台证书接口的 encrypt_certificate.nonce 才是 hex 串
  （bytes.fromhex）。误用 hex 解码会让每一次回调解密都失败；
- v3 同步响应不验签（与支付宝 gateway_post 不同）：v3 响应签名走平台证书体系，
  Native 下单/查单/退款受理均依赖 TLS 传输安全，权威结算以验签后的异步通知为准。
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# 微信支付 APIv3 网关（生产）；协议字段按 GMT+8 传
_BASE_URL = "https://api.mch.weixin.qq.com"
_TZ_CN = timezone(timedelta(hours=8))

# 同步网关调用超时（秒）：下单/查单/退款受理都是轻量接口
_GATEWAY_TIMEOUT_SECONDS = 15.0

# 平台证书内存缓存（公钥模式未配置时的回退）；轮换靠 TTL 过期重拉
_platform_certs: list[dict] | None = None
_platform_certs_fetched_at: float = 0.0
_CERTS_CACHE_TTL_SECONDS = 3600.0


class WechatPayProtocolError(Exception):
    """协议构造/验签/解密失败（配置缺失、密钥格式错误、验签不通过）。"""


@dataclass
class WechatPayGatewayError(WechatPayProtocolError):
    """网关受理失败（HTTP >= 400），v3 错误体为 {"code","message"}，供上层留档。"""

    code: str = ""
    message: str = ""
    http_status: int = 0

    def __str__(self) -> str:
        return f"微信支付网关错误 {self.http_status}/{self.code}：{self.message}"


# ---------------------------------------------------------------------------
# 密钥归一化（口径同 alipay_client：容忍 PEM 全文（\n 转义）与单行 base64 两种存法）
# ---------------------------------------------------------------------------


def _normalize_private_key(raw: str) -> bytes:
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode("ascii")
    body = text.replace(" ", "").replace("\n", "")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----".encode("ascii")


def _load_private_key(raw: str) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(_normalize_private_key(raw), password=None)
    except (ValueError, TypeError) as exc:
        raise WechatPayProtocolError("WECHAT_PAY_PRIVATE_KEY 格式无效（需 PKCS8 PEM 或单行 base64）") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise WechatPayProtocolError("WECHAT_PAY_PRIVATE_KEY 必须是 RSA 私钥")
    return key


def _normalize_public_key(raw: str) -> bytes:
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode("ascii")
    body = text.replace(" ", "").replace("\n", "")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{wrapped}\n-----END PUBLIC KEY-----".encode("ascii")


def _resolve_api_v3_key(api_v3_key_raw: str | None = None) -> bytes:
    """APIv3 密钥转 32 字节：64 位 hex 串按 bytes.fromhex，32 位原始串按 utf-8。"""
    from config import WECHAT_PAY_API_V3_KEY

    raw = (api_v3_key_raw or WECHAT_PAY_API_V3_KEY or "").strip()
    if not raw:
        raise WechatPayProtocolError("WECHAT_PAY_API_V3_KEY 未配置")
    if len(raw) == 64:
        return bytes.fromhex(raw)
    return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# 请求签名与同步网关调用
# ---------------------------------------------------------------------------


def _build_authorization(method: str, path: str, body_str: str, *, private_key_raw: str | None = None) -> str:
    """构造 WECHATPAY2-SHA256-RSA2048 Authorization 头。

    待签串固定五段：method\\npath(含 query)\\ntimestamp\\nnonce\\nbody\\n。
    """
    from config import WECHAT_PAY_MCH_ID, WECHAT_PAY_SERIAL_NO

    key = _load_private_key(private_key_raw or _config_private_key())
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sign_str = f"{method}\n{path}\n{ts}\n{nonce}\n{body_str}\n"
    signature = key.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode("ascii")
    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{WECHAT_PAY_MCH_ID}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{ts}",'
        f'serial_no="{WECHAT_PAY_SERIAL_NO}",'
        f'signature="{sig_b64}"'
    )


def _config_private_key() -> str:
    """调用时读 config（而非模块导入时快照），对齐仓库「延迟导入方便 monkeypatch」惯例。"""
    from config import WECHAT_PAY_PRIVATE_KEY

    return WECHAT_PAY_PRIVATE_KEY


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步网关调用：签名 → httpx → 解析 JSON；HTTP >= 400 抛 WechatPayGatewayError。"""
    import httpx

    from config import WECHAT_PAY_MCH_ID

    if not WECHAT_PAY_MCH_ID:
        raise WechatPayProtocolError("WECHAT_PAY_MCH_ID 未配置")
    body_str = json.dumps(body, ensure_ascii=True) if body else ""
    try:
        response = httpx.request(
            method,
            f"{_BASE_URL}{path}",
            headers={
                "Authorization": _build_authorization(method, path, body_str),
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "solobooks/1.0",
            },
            content=body_str.encode("utf-8") if body_str else None,
            timeout=_GATEWAY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise WechatPayProtocolError(f"微信支付网关请求失败: {exc}") from exc
    data: dict[str, Any] = {}
    if response.content:
        try:
            data = response.json()
        except ValueError as exc:
            raise WechatPayProtocolError("微信支付网关响应不是合法 JSON") from exc
    if response.status_code >= 400:
        raise WechatPayGatewayError(
            code=str(data.get("code") or ""),
            message=str(data.get("message") or "微信支付网关错误"),
            http_status=response.status_code,
        )
    return data


def _rfc3339_cn(dt: datetime) -> str:
    """RFC3339 +08:00（微信 time_expire 口径）；naive 值按北京时间解释。"""
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return dt.astimezone(_TZ_CN).strftime("%Y-%m-%dT%H:%M:%S+08:00")


# ---------------------------------------------------------------------------
# 下单 / 查单 / 关单 / 退款
# ---------------------------------------------------------------------------


def create_native_order(
    *,
    out_trade_no: str,
    total_cents: int,
    description: str,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Native 下单（POST /v3/pay/transactions/native），返回含 code_url 的响应。

    total_cents 为分（billing_service 从 core.money Decimal 换算，绝不接受前端传价）。
    """
    from config import WECHAT_PAY_APP_ID, WECHAT_PAY_MCH_ID, WECHAT_PAY_NOTIFY_URL

    body: dict[str, Any] = {
        "appid": WECHAT_PAY_APP_ID,
        "mchid": WECHAT_PAY_MCH_ID,
        "description": description,
        "out_trade_no": out_trade_no,
        "notify_url": WECHAT_PAY_NOTIFY_URL,
        "amount": {"total": total_cents, "currency": "CNY"},
    }
    if expires_at is not None:
        body["time_expire"] = _rfc3339_cn(expires_at)
    return _request("POST", "/v3/pay/transactions/native", body)


def query_order(out_trade_no: str) -> dict[str, Any]:
    """按商户订单号查单（GET /v3/pay/transactions/out-trade-no/{no}?mchid=...）。

    query 参与签名（Authorization 待签串的 path 含 query），与官方 SDK 口径一致。
    """
    from config import WECHAT_PAY_MCH_ID

    path = f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={WECHAT_PAY_MCH_ID}"
    return _request("GET", path)


def close_order(out_trade_no: str) -> dict[str, Any]:
    """关单（POST /v3/pay/transactions/out-trade-no/{no}/close）。"""
    from config import WECHAT_PAY_MCH_ID

    return _request("POST", f"/v3/pay/transactions/out-trade-no/{out_trade_no}/close", {"mchid": WECHAT_PAY_MCH_ID})


def create_refund(
    *,
    out_trade_no: str,
    out_refund_no: str,
    total_cents: int,
    refund_cents: int,
    reason: str,
) -> dict[str, Any]:
    """申请退款（POST /v3/refund/domestic/refunds）：同步受理，终态以退款异步通知为准。

    out_refund_no 商户退款单号（唯一）；全额退款固定 {out_trade_no}R1（billing_service
    的幂等键），网关对同号幂等，重试不会二次退款。
    """
    from config import WECHAT_PAY_REFUND_NOTIFY_URL

    body: dict[str, Any] = {
        "out_trade_no": out_trade_no,
        "out_refund_no": out_refund_no,
        "reason": reason,
        "notify_url": WECHAT_PAY_REFUND_NOTIFY_URL,
        "amount": {"refund": refund_cents, "total": total_cents, "currency": "CNY"},
    }
    return _request("POST", "/v3/refund/domestic/refunds", body)


def query_refund(out_refund_no: str) -> dict[str, Any]:
    """查询退款状态（GET /v3/refund/domestic/refunds/{no}），管理端只读校准用。"""
    return _request("GET", f"/v3/refund/domestic/refunds/{out_refund_no}")


# ---------------------------------------------------------------------------
# 回调验签与解密
# ---------------------------------------------------------------------------


def verify_notify_signature(
    timestamp: str,
    nonce: str,
    body: str,
    signature: str,
    serial_no: str,
    *,
    public_key_raw: str | None = None,
) -> bool:
    """验回调通知签名（Wechatpay-Timestamp/Nonce/Signature/Serial 四头）。

    微信支付公钥模式优先：serial_no 与配置的 PUBLIC_KEY_ID 不一致只告警不拒绝，
    由签名本身决定真伪（公钥轮换窗口期序列号可能先于配置更新）。
    不合格式/验签失败一律 False（调用方拒绝处理）。
    """
    from config import WECHAT_PAY_PUBLIC_KEY, WECHAT_PAY_PUBLIC_KEY_ID

    raw_public = (public_key_raw or WECHAT_PAY_PUBLIC_KEY or "").strip()
    sign = (signature or "").strip()
    if not sign:
        return False

    if raw_public:
        if WECHAT_PAY_PUBLIC_KEY_ID and serial_no != WECHAT_PAY_PUBLIC_KEY_ID:
            logger.warning(
                "微信回调 serial_no=%s 与 WECHAT_PAY_PUBLIC_KEY_ID=%s 不一致，继续按签名验真伪",
                serial_no,
                WECHAT_PAY_PUBLIC_KEY_ID,
            )
        try:
            public_key = serialization.load_pem_public_key(_normalize_public_key(raw_public))
            sign_str = f"{timestamp}\n{nonce}\n{body}\n"
            public_key.verify(
                base64.b64decode(sign),
                sign_str.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    # 平台证书模式回退：按 serial_no 匹配运行时拉取的平台证书后验签
    try:
        cert_pem = _load_platform_cert_pem(serial_no)
        cert_obj = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
        public_key = cert_obj.public_key()
        sign_str = f"{timestamp}\n{nonce}\n{body}\n"
        public_key.verify(
            base64.b64decode(sign),
            sign_str.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError, TypeError, WechatPayProtocolError):
        return False


def _fetch_platform_certificates() -> list[dict]:
    """拉取并缓存平台证书列表（GET /v3/certificates，1h 内存缓存）。"""
    global _platform_certs, _platform_certs_fetched_at
    now = time.time()
    if _platform_certs and (now - _platform_certs_fetched_at) < _CERTS_CACHE_TTL_SECONDS:
        return _platform_certs
    data = _request("GET", "/v3/certificates")
    certs = list(data.get("data") or [])
    if not certs:
        raise WechatPayProtocolError("微信支付平台证书列表为空")
    _platform_certs = certs
    _platform_certs_fetched_at = now
    return certs


def _load_platform_cert_pem(serial_no: str) -> str:
    """按 serial_no 找到平台证书并用 APIv3 密钥解出 PEM 全文。"""
    for cert_info in _fetch_platform_certificates():
        enc_cert = cert_info.get("encrypt_certificate") or {}
        cert_serial = enc_cert.get("serial_no") or cert_info.get("serial_no")
        if cert_serial == serial_no:
            # 平台证书接口的 nonce 是 hex 串（与回调 resource.nonce 不同，见 decrypt_notify_resource）
            plain = AESGCM(_resolve_api_v3_key()).decrypt(
                bytes.fromhex(str(enc_cert["nonce"])),
                base64.b64decode(str(enc_cert["ciphertext"])),
                str(enc_cert.get("associated_data") or "").encode("utf-8"),
            )
            return plain.decode("utf-8")
    raise WechatPayProtocolError(f"平台证书列表中找不到 serial_no={serial_no}")


def decrypt_notify_resource(
    ciphertext: str,
    associated_data: str,
    nonce: str,
    *,
    api_v3_key_raw: str | None = None,
) -> str:
    """AES-256-GCM 解密回调通知 resource.ciphertext，返回解密后的 JSON 字符串。

    ⚠️ 回调的 resource.nonce 是 12 字符随机串，必须按**原始字节**当 IV
    （nonce.encode()）——不是 hex 串。早前误用 bytes.fromhex 导致每一次回调
    解密都失败（visanote 事故，回归测试防再犯）。
    """
    plaintext = AESGCM(_resolve_api_v3_key(api_v3_key_raw)).decrypt(
        nonce.encode("utf-8"),
        base64.b64decode(ciphertext),
        (associated_data or "").encode("utf-8"),
    )
    return plaintext.decode("utf-8")


def map_trade_state(raw_state: str) -> str:
    """微信 trade_state → 本地口径：PAID / PENDING / CLOSED / REFUND。"""
    mapping = {
        "SUCCESS": "PAID",
        "NOTPAY": "PENDING",
        "USERPAYING": "PENDING",
        "CLOSED": "CLOSED",
        "PAYERROR": "CLOSED",
        "REVOKED": "CLOSED",
        "REFUND": "REFUND",
    }
    return mapping.get((raw_state or "").upper(), (raw_state or "").upper())
