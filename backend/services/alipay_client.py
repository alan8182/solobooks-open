# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝开放平台协议层（公钥模式 RSA2）。

- alipay.trade.page.pay：收银台跳转 URL（GET 302，浏览器直连网关）
- alipay.trade.refund / alipay.trade.query：同步网关调用（gateway_post，httpx）

不引官方 SDK：官方 Python SDK 多年未维护且拉一串旧依赖；本仓库惯例是协议薄层
自实现（JWT HS256、PBKDF2 均为标准库手写），RSA2 用已钉版的 cryptography。

签名规则（支付宝官方 SDK AlipaySignature 同款，两侧不对称）：
- 请求待签串 = 剔除 sign 与空值后的全部参数，**包含 sign_type**（getSignatureContent），
  按 key ASCII 升序拼 ``k=v`` 再用 & 相连，值取「原始值」（不 URL 编码）；
- 通知验签串 = 请求口径上再剔除 sign_type（rsaCheckV1）；
- 请求用应用私钥签名（RSA2 = SHA256withRSA），验通知用支付宝公钥。
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
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
from core.money import money_text

logger = logging.getLogger(__name__)

# 北京时间：支付宝协议字段（timestamp）按 GMT+8 传
_TZ_CN = timezone(timedelta(hours=8))

# 同步网关调用超时（秒）：退款/查询都是轻量接口，15s 足够宽裕
_GATEWAY_TIMEOUT_SECONDS = 15.0


class AlipayProtocolError(Exception):
    """协议构造/验签失败（配置缺失、密钥格式错误、验签不通过）。"""


@dataclass
class AlipayGatewayError(AlipayProtocolError):
    """网关受理失败（同步响应 code != 10000），携带网关错误码供上层留档。

    支付宝 JSON 报文的错误字段是 snake_case（sub_code/sub_msg），官方 SDK 才
    映射成 subCode/subMsg；这里保留 sub_msg，否则 40004 Business Failed 这类
    通用错误会把「真正原因」丢成空串（2026-11-18 线上退款失败排查事故）。
    """

    code: str = ""
    sub_code: str = ""
    gateway_msg: str = ""
    sub_msg: str = ""

    def __str__(self) -> str:
        detail = self.sub_msg or self.gateway_msg
        suffix = f"：{detail}" if detail else ""
        return f"支付宝网关错误 {self.code}/{self.sub_code}{suffix}"


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


def _sign_content(params: dict[str, Any], *, keep_sign_type: bool) -> str:
    """待签串：剔除 sign 与空值，key 升序 k=v & 相连（值不转义）。

    支付宝规则不对称（2026-11-18 线上 invalid-signature 的根因）：请求签名
    的待签串包含 sign_type；异步通知验签要再剔除 sign_type。
    """
    excluded = ("sign",) if keep_sign_type else ("sign", "sign_type")
    items = sorted(
        (key, str(value))
        for key, value in params.items()
        if key not in excluded and value is not None and str(value) != ""
    )
    return "&".join(f"{key}={value}" for key, value in items)


def sign_params(params: dict[str, Any], private_key_raw: str | None = None) -> str:
    """对参数集生成 RSA2 签名（base64）。生产用 .env 私钥；测试可注入临时密钥。

    请求口径：待签串包含 sign_type（只剔除 sign 与空值）。
    """
    from config import ALIPAY_PRIVATE_KEY

    key = _load_private_key(private_key_raw or ALIPAY_PRIVATE_KEY)
    try:
        signature = key.sign(
            _sign_content(params, keep_sign_type=True).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001
        raise AlipayProtocolError(f"签名失败: {exc}") from exc
    return base64.b64encode(signature).decode("ascii")


def verify_notify_signature(params: dict[str, Any], public_key_raw: str | None = None) -> bool:
    """验支付宝异步通知签名：不合格式/验签失败一律 False（调用方拒绝处理）。

    通知参数是服务端已 URL 解码的 form 字典；待签串按通知口径（rsaCheckV1）：
    剔除 sign 与 sign_type 后按 key 排序拼原始值。
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
            _sign_content(params, keep_sign_type=False).encode("utf-8"),
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


# ---------------------------------------------------------------------------
# 同步网关调用（退款 / 交易查询）
# ---------------------------------------------------------------------------


def _response_node_name(method: str) -> str:
    """alipay.trade.refund -> alipay_trade_refund_response（网关响应节点名）。"""
    return method.replace(".", "_") + "_response"


def _extract_node_raw(body_text: str, node_name: str) -> str:
    """从响应原文按括号配平截取业务节点的 JSON 原文。

    同步响应的 sign 覆盖的是节点 JSON 的原始字节；重新序列化（键序/空白差异）
    会导致验签必失败，所以必须用原文子串（与官方 SDK 做法一致）。
    """
    marker = f'"{node_name}"'
    key_pos = body_text.find(marker)
    if key_pos < 0:
        raise AlipayProtocolError(f"网关响应缺少 {node_name} 节点")
    brace_pos = body_text.find("{", key_pos + len(marker))
    if brace_pos < 0:
        raise AlipayProtocolError(f"网关响应 {node_name} 节点格式无效")
    depth = 0
    in_string = False
    escaped = False
    for pos in range(brace_pos, len(body_text)):
        ch = body_text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body_text[brace_pos : pos + 1]
    raise AlipayProtocolError(f"网关响应 {node_name} 节点 JSON 不完整")


def _verify_response_sign(node_raw: str, sign: str, public_key_raw: str | None = None) -> None:
    """验同步响应签名：RSA2 覆盖节点 JSON 原文，失败一律抛错不返回结果。"""
    from config import ALIPAY_PUBLIC_KEY

    raw_public = public_key_raw or ALIPAY_PUBLIC_KEY
    public_key = serialization.load_pem_public_key(_normalize_public_key(raw_public))
    try:
        public_key.verify(
            base64.b64decode(sign),
            node_raw.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise AlipayProtocolError("网关响应验签失败") from exc


def gateway_post(
    method: str,
    biz_content: dict[str, Any],
    *,
    notify_url: str | None = None,
    private_key_raw: str | None = None,
) -> dict[str, Any]:
    """同步网关调用：签名 POST → 解析 JSON → 验签 → 返回业务节点 dict。

    code != 10000 抛 AlipayGatewayError（带 code/sub_code/msg 供上层留档）；
    响应带 sign 时必须验签通过才返回（验签失败按协议错误处理）。
    """
    import httpx

    if not ALIPAY_APP_ID:
        raise AlipayProtocolError("ALIPAY_APP_ID 未配置")
    params: dict[str, str] = {
        "app_id": ALIPAY_APP_ID,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now(_TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if notify_url:
        params["notify_url"] = notify_url
    params["sign"] = sign_params(params, private_key_raw)

    try:
        response = httpx.post(
            ALIPAY_GATEWAY,
            data={key: params[key] for key in sorted(params) if params[key] != ""},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            timeout=_GATEWAY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body_text = response.text
    except httpx.HTTPError as exc:
        raise AlipayProtocolError(f"网关请求失败: {exc}") from exc

    node_name = _response_node_name(method)
    try:
        payload = json.loads(body_text)
    except ValueError as exc:
        raise AlipayProtocolError("网关响应不是合法 JSON") from exc
    node = payload.get(node_name)
    if not isinstance(node, dict):
        raise AlipayProtocolError(f"网关响应缺少 {node_name} 业务节点")

    sign = str(payload.get("sign") or "").strip()
    if sign:
        # 用原文截取子串验签；部分错误响应可能不带 sign，此时跳过验签走错误分支
        _verify_response_sign(_extract_node_raw(body_text, node_name), sign)
    code = str(node.get("code") or "")
    if code != "10000":
        raise AlipayGatewayError(
            code=code,
            # 线上报文用 sub_code/sub_msg；兼容早期 camelCase 夹具
            sub_code=str(node.get("sub_code") or node.get("subCode") or ""),
            gateway_msg=str(node.get("msg") or ""),
            sub_msg=str(node.get("sub_msg") or node.get("subMsg") or ""),
        )
    return node


def refund(
    out_trade_no: str,
    refund_amount: str,
    *,
    out_request_no: str,
    refund_reason: str,
) -> dict[str, Any]:
    """alipay.trade.refund 全额退款（同步结果为准；不传 notify_url，不新增通知类型）。

    refund_amount 必须是两位小数字符串（money_text 口径）；同笔交易多次退款
    靠 out_request_no 区分，全额退款固定 R1 序号即可幂等重放。
    """
    return gateway_post(
        "alipay.trade.refund",
        {
            "out_trade_no": out_trade_no,
            "refund_amount": money_text(refund_amount),
            "out_request_no": out_request_no,
            "refund_reason": refund_reason,
        },
    )


def query_trade(out_trade_no: str) -> dict[str, Any] | None:
    """alipay.trade.query 只读查询；网关无此交易返回 None（供订单管理校准）。"""
    try:
        return gateway_post("alipay.trade.query", {"out_trade_no": out_trade_no})
    except AlipayGatewayError as exc:
        if exc.sub_code == "ACQ.TRADE_NOT_EXIST":
            return None
        raise
