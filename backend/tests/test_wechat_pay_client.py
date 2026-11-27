# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信支付 APIv3 协议层回归测试。

重点防御的根因（移植自 visanote 的线上事故，均有出处）：
- 回调 resource.nonce 必须按**原始字节**当 GCM IV——误用 bytes.fromhex 会让
  每一次回调解密都失败（nonce 形似 hex 的场景必须仍按原始字节）；
- 公钥模式回调验签：真签名通过、篡改必失败、serial 与配置不一致不武断拒绝
  （由签名本身决定真伪，兼容公钥轮换窗口）；
- Authorization 待签串五段口径（method/path/ts/nonce/body）与密钥归一化。
"""
from __future__ import annotations

import base64
import json
import re

import pytest
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import config
from services import wechat_pay_client

_TEST_MCH_ID = "1900000001"
_TEST_APP_ID = "wxtestappid1234"
# 32 位原始串（默认形态；64 位 hex 形态另有专项用例）
_TEST_API_V3_KEY = "0123456789abcdef0123456789abcdef"
_TEST_SERIAL = "TESTSERIAL0001"
_TEST_KEY_ID = "PUB_KEY_ID_TEST0001"
# 形似 hex 的 12 字符随机串（微信回调 nonce 的真实形态）
_LIKE_HEX_NONCE = "a1b2c3d4e5f6"


@pytest.fixture
def wechat_keys(monkeypatch):
    """微信支付测试密钥对（请求签名/回调验签/AESGCM 解密全配对），不依赖 backend/.env。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    monkeypatch.setattr(config, "WECHAT_PAY_MCH_ID", _TEST_MCH_ID)
    monkeypatch.setattr(config, "WECHAT_PAY_APP_ID", _TEST_APP_ID)
    monkeypatch.setattr(config, "WECHAT_PAY_API_V3_KEY", _TEST_API_V3_KEY)
    monkeypatch.setattr(config, "WECHAT_PAY_SERIAL_NO", _TEST_SERIAL)
    monkeypatch.setattr(config, "WECHAT_PAY_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(config, "WECHAT_PAY_PUBLIC_KEY", public_pem)
    monkeypatch.setattr(config, "WECHAT_PAY_PUBLIC_KEY_ID", _TEST_KEY_ID)
    return {"private_key": private_key, "public_pem": public_pem}


# ---------------------------------------------------------------------------
# 请求签名
# ---------------------------------------------------------------------------

def test_authorization_signature_verifies_with_pairing_public_key(wechat_keys):
    """Authorization 签名必须能用配对公钥按五段待签串（method\\npath\\nts\\nnonce\\nbody\\n）验证。"""
    body = json.dumps({"appid": _TEST_APP_ID})
    auth = wechat_pay_client._build_authorization("POST", "/v3/pay/transactions/native", body)

    match = re.match(
        r'WECHATPAY2-SHA256-RSA2048 mchid="(?P<mchid>[^"]+)",nonce_str="(?P<nonce>[^"]+)",'
        r'timestamp="(?P<ts>[^"]+)",serial_no="(?P<serial>[^"]+)",signature="(?P<sig>[^"]+)"',
        auth,
    )
    assert match is not None
    assert match.group("mchid") == _TEST_MCH_ID
    assert match.group("serial") == _TEST_SERIAL

    sign_str = f"POST\n/v3/pay/transactions/native\n{match.group('ts')}\n{match.group('nonce')}\n{body}\n"
    public_key = serialization.load_pem_public_key(wechat_keys["public_pem"].encode("ascii"))
    # 正确待签串必须验证通过；缺尾部 \n 的串必须失败（防待签串口径回归）
    public_key.verify(
        base64.b64decode(match.group("sig")),
        sign_str.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    with pytest.raises(InvalidSignature):
        public_key.verify(
            base64.b64decode(match.group("sig")),
            sign_str.rstrip("\n").encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )


def test_private_key_accepts_escaped_newline_and_single_line_base64(wechat_keys):
    """密钥归一化：.env 的两种存法（PEM 全文 \n 转义 / 单行 base64）都必须能加载。"""
    from services.wechat_pay_client import _load_private_key

    pem_text = wechat_keys["private_key"].private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    assert _load_private_key(pem_text.replace("\n", "\\n")) is not None
    body = "".join(line for line in pem_text.splitlines() if "-----" not in line)
    assert _load_private_key(body) is not None


# ---------------------------------------------------------------------------
# 回调解密（nonce 原始字节回归）
# ---------------------------------------------------------------------------

def _encrypt_resource(resource: dict, nonce: str, associated_data: str = "transaction") -> str:
    """按官方口径加密：IV = nonce 原始字节（12 字节），AAD = associated_data。"""
    plaintext = json.dumps(resource, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(
        AESGCM(_TEST_API_V3_KEY.encode("utf-8")).encrypt(
            nonce.encode("utf-8"), plaintext, associated_data.encode("utf-8")
        )
    ).decode("ascii")


def test_decrypt_notify_resource_roundtrip(wechat_keys):
    """标准回路：官方口径加密的回调资源必须能解出原文。"""
    resource = {"out_trade_no": "SB261119001", "trade_state": "SUCCESS"}
    ciphertext = _encrypt_resource(resource, _LIKE_HEX_NONCE)
    out = wechat_pay_client.decrypt_notify_resource(ciphertext, "transaction", _LIKE_HEX_NONCE)
    assert json.loads(out)["trade_state"] == "SUCCESS"


def test_decrypt_notify_resource_nonce_must_be_raw_bytes_not_hex(wechat_keys):
    """根因回归：nonce 被 bytes.fromhex 误读（6 字节）与原始字节（12 字节）不等长，
    GCM 校验必失败——正确实现（原始字节）必须成功，错误实现必须解不开。"""
    resource = {"out_trade_no": "SB261119002"}
    ciphertext = _encrypt_resource(resource, _LIKE_HEX_NONCE)
    # 正确口径：原始字节
    assert wechat_pay_client.decrypt_notify_resource(ciphertext, "transaction", _LIKE_HEX_NONCE)
    # 错误口径（历史事故实现）：hex 解码得到的 6 字节 IV 被 AESGCM 拒绝
    # （长度不足直接 ValueError；若 nonce 恰好 hex 解出 ≥8 字节则是 InvalidTag）
    with pytest.raises((InvalidTag, ValueError)):
        AESGCM(_TEST_API_V3_KEY.encode("utf-8")).decrypt(
            bytes.fromhex(_LIKE_HEX_NONCE),
            base64.b64decode(ciphertext),
            b"transaction",
        )


def test_decrypt_notify_resource_rejects_wrong_api_key(wechat_keys, monkeypatch):
    monkeypatch.setattr(config, "WECHAT_PAY_API_V3_KEY", "f" * 32)
    ciphertext = _encrypt_resource({"x": 1}, _LIKE_HEX_NONCE)
    with pytest.raises(Exception):
        wechat_pay_client.decrypt_notify_resource(ciphertext, "transaction", _LIKE_HEX_NONCE)


def test_resolve_api_v3_key_supports_hex_and_raw(monkeypatch):
    """APIv3 密钥两种形态：64 位 hex（fromhex）与 32 位原始串（utf-8）。"""
    hex_key = "3a" * 32
    monkeypatch.setattr(config, "WECHAT_PAY_API_V3_KEY", hex_key)
    assert wechat_pay_client._resolve_api_v3_key() == bytes.fromhex(hex_key)
    monkeypatch.setattr(config, "WECHAT_PAY_API_V3_KEY", _TEST_API_V3_KEY)
    assert wechat_pay_client._resolve_api_v3_key() == _TEST_API_V3_KEY.encode("utf-8")


# ---------------------------------------------------------------------------
# 回调验签（公钥模式）
# ---------------------------------------------------------------------------

def _notify_headers(wechat_keys, body: str, *, serial: str = _TEST_KEY_ID, tamper: bool = False):
    ts, nonce = "1760000000", "notify-nonce-01"
    sign_str = f"{ts}\n{nonce}\n{body}\n"
    sig = base64.b64encode(
        wechat_keys["private_key"].sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")
    if tamper:
        sig = "AA" + sig[2:]
    return ts, nonce, sig, serial


def test_verify_notify_signature_accepts_valid_rejects_tamper(wechat_keys):
    """真签名通过；篡改签名必失败——验签是微信回调的唯一身份（A2/A3 闸门）。"""
    body = json.dumps({"id": "evt-1", "resource": {"ciphertext": "x"}})
    ts, nonce, sig, serial = _notify_headers(wechat_keys, body)
    assert wechat_pay_client.verify_notify_signature(ts, nonce, body, sig, serial) is True
    tampered_ts, tampered_nonce, tampered_sig, _ = _notify_headers(wechat_keys, body, tamper=True)
    assert wechat_pay_client.verify_notify_signature(tampered_ts, tampered_nonce, body, tampered_sig, serial) is False
    # body 被替换（签名覆盖原文）同样必须失败
    assert wechat_pay_client.verify_notify_signature(ts, nonce, body + " ", sig, serial) is False


def test_verify_notify_signature_serial_mismatch_still_verifies_by_signature(wechat_keys):
    """serial 与配置的 PUBLIC_KEY_ID 不一致时不武断拒绝：签名有效即通过（轮换窗口兼容）。"""
    body = json.dumps({"id": "evt-2"})
    ts, nonce, sig, _ = _notify_headers(wechat_keys, body)
    assert wechat_pay_client.verify_notify_signature(ts, nonce, body, sig, "PUB_KEY_ID_OTHER") is True


def test_verify_notify_signature_empty_signature_rejected(wechat_keys):
    body = "{}"
    assert wechat_pay_client.verify_notify_signature("1", "n", body, "", _TEST_KEY_ID) is False


# ---------------------------------------------------------------------------
# 下单请求体与网关错误
# ---------------------------------------------------------------------------

class _FakeHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> dict:
        return self._payload


def test_create_native_order_builds_v3_body(wechat_keys, monkeypatch):
    """Native 下单请求体：appid/mchid/分金额/time_expire(+08:00)/notify_url 口径。"""
    from datetime import datetime, timedelta, timezone

    captured: dict = {}

    def fake_request(method, url, headers=None, content=None, timeout=None, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(content.decode("utf-8") if content else "{}")
        return _FakeHttpResponse({"code_url": "weixin://wxpay/bizpayurl?pr=test"})

    import httpx

    monkeypatch.setattr(httpx, "request", fake_request)
    from config import WECHAT_PAY_NOTIFY_URL

    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    node = wechat_pay_client.create_native_order(
        out_trade_no="SB261119TEST1",
        total_cents=59800,
        description="云端标准版（年付）",
        expires_at=expires_at,
    )
    assert node["code_url"].startswith("weixin://")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v3/pay/transactions/native")
    body = captured["body"]
    assert body["appid"] == _TEST_APP_ID
    assert body["mchid"] == _TEST_MCH_ID
    assert body["out_trade_no"] == "SB261119TEST1"
    assert body["amount"] == {"total": 59800, "currency": "CNY"}
    assert body["time_expire"].endswith("+08:00")
    assert body["notify_url"] == WECHAT_PAY_NOTIFY_URL
    assert "WECHATPAY2-SHA256-RSA2048" in captured["headers"]["Authorization"]


def test_gateway_error_carries_v3_code_and_message(wechat_keys, monkeypatch):
    """HTTP >= 400 必须抛 WechatPayGatewayError 且携带 v3 错误体 code/message（上层留档）。"""
    import httpx

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *a, **k: _FakeHttpResponse(
            {"code": "INVALID_REQUEST", "message": "参数错误"}, status_code=400
        ),
    )
    with pytest.raises(wechat_pay_client.WechatPayGatewayError) as exc_info:
        wechat_pay_client.query_order("SB-NOT-EXIST")
    assert exc_info.value.code == "INVALID_REQUEST"
    assert exc_info.value.http_status == 400
    assert "参数错误" in str(exc_info.value)


def test_map_trade_state(wechat_keys):
    assert wechat_pay_client.map_trade_state("SUCCESS") == "PAID"
    assert wechat_pay_client.map_trade_state("NOTPAY") == "PENDING"
    assert wechat_pay_client.map_trade_state("CLOSED") == "CLOSED"
    assert wechat_pay_client.map_trade_state("REFUND") == "REFUND"
    assert wechat_pay_client.map_trade_state("WEIRD") == "WEIRD"
    assert wechat_pay_client.map_trade_state("") == ""
