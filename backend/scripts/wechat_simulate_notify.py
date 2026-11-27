# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""模拟微信支付回调通知（本地联调用，生产禁用）。

背景：notify_url 必须公网可达，本地开发收不到微信真实回调。微信 v3 通知验签
用的是「微信支付公钥」（WECHAT_PAY_PUBLIC_KEY）——生产环境它是微信下发的真公钥；
本地联调时把它换成一把**测试密钥对的公钥**，本脚本用配对私钥对通知原文签名并
构造加密 resource（AESGCM，nonce 按原始字节当 IV），POST 到本地端点后验签即通过，
全链路（验签→解密→幂等→激活）可演练。与支付宝模拟脚本的「应用公钥」技巧同源。

⚠️ 前提：backend/.env 配置
    WECHAT_PAY_MCH_ID / WECHAT_PAY_APP_ID / WECHAT_PAY_API_V3_KEY（随便 32 位串）
    WECHAT_PAY_PUBLIC_KEY = 测试公钥 PEM 全文（首跑本脚本会生成并打印）
    （WECHAT_PAY_PRIVATE_KEY / SERIAL_NO 是商户请求签名用的，模拟回调不需要）
生产环境 WECHAT_PAY_PUBLIC_KEY 换回微信支付公钥后，本脚本签名将不再通过验签
——这是预期行为，不是 bug。

用法：
    venv/bin/python scripts/wechat_simulate_notify.py \
        --order-no SB261119... --amount 598.00 \
        [--trade-state SUCCESS] [--url http://127.0.0.1:8001] [--repeat 2]

退款通知演练（对应管理端退款 PROCESSING 之后的终态）：
    venv/bin/python scripts/wechat_simulate_notify.py \
        --order-no SB261119... --amount 598.00 --refund --out-refund-no SB261119...R1
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试密钥对落盘位置（.gitignore 已覆盖；首跑自动生成并打印公钥配置指引）
_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "wechat_sim_notify_key.pem"


def _ensure_test_keypair() -> str:
    """确保存在测试私钥（PEM），返回 PEM 全文；首跑生成并打印配置指引。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if _KEY_PATH.exists():
        return _KEY_PATH.read_text(encoding="ascii")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_text(private_pem, encoding="ascii")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    print("=" * 72)
    print("已生成模拟回调测试密钥对。请把下面这段公钥粘进 backend/.env 后重启后端：")
    print()
    print(public_pem)
    print('对应配置项：WECHAT_PAY_PUBLIC_KEY="<上面整段 PEM 单行转义或原文>"')
    print("=" * 72)
    return private_pem


def _encrypt_resource(resource: dict, api_v3_key: str) -> dict:
    """构造 v3 回调 resource：AESGCM，IV = nonce 原始字节（12 字符随机串）。

    associated_data 按事件类型：支付通知 transaction，退款通知 refund（v3 口径）。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    is_refund = "refund_status" in resource
    associated = "refund" if is_refund else "transaction"
    nonce = uuid.uuid4().hex[:12]
    plaintext = json.dumps(resource, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(api_v3_key.encode("utf-8")).encrypt(
        nonce.encode("utf-8"), plaintext, associated.encode("utf-8")
    )
    return {
        "algorithm": "AEAD_AES_256_GCM",
        "associated_data": associated,
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "original_type": "refund" if is_refund else "transaction",
        "nonce": nonce,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟微信支付/退款回调通知")
    parser.add_argument("--order-no", required=True, help="商户订单号 out_trade_no")
    parser.add_argument("--amount", required=True, help="金额（元，两位小数如 598.00，转分入 resource）")
    parser.add_argument("--trade-state", default="SUCCESS", help="trade_state（默认 SUCCESS）")
    parser.add_argument("--transaction-id", default="")
    parser.add_argument("--refund", action="store_true", help="发送退款结果通知（REFUND.SUCCESS）")
    parser.add_argument("--out-refund-no", default="", help="退款单号（--refund 时必填）")
    parser.add_argument("--refund-status", default="SUCCESS")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--repeat", type=int, default=1, help="重复推送次数（验证幂等）")
    args = parser.parse_args()

    from config import WECHAT_PAY_API_V3_KEY, WECHAT_PAY_APP_ID, WECHAT_PAY_MCH_ID

    if not WECHAT_PAY_API_V3_KEY or not WECHAT_PAY_APP_ID or not WECHAT_PAY_MCH_ID:
        print("错误：backend/.env 需先配置 WECHAT_PAY_MCH_ID / APP_ID / API_V3_KEY（见脚本 docstring）")
        return 2

    private_pem = _ensure_test_keypair()
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)

    cents = int(round(float(args.amount) * 100))
    now = datetime.now(timezone(timedelta(hours=8)))
    if args.refund:
        if not args.out_refund_no:
            print("错误：--refund 时必须提供 --out-refund-no")
            return 2
        resource: dict = {
            "mchid": WECHAT_PAY_MCH_ID,
            "out_trade_no": args.order_no,
            "out_refund_no": args.out_refund_no,
            "refund_status": args.refund_status,
            "success_time": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "amount": {"refund": cents, "total": cents, "currency": "CNY"},
        }
        event_type = f"REFUND.{args.refund_status}"
        endpoint = "/api/billing/wechat/refund-notify"
    else:
        resource = {
            "mchid": WECHAT_PAY_MCH_ID,
            "appid": WECHAT_PAY_APP_ID,
            "out_trade_no": args.order_no,
            "transaction_id": args.transaction_id or f"SIM{now.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}",
            "trade_state": args.trade_state,
            "success_time": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "amount": {"total": cents, "currency": "CNY", "payer_total": cents},
        }
        event_type = f"TRANSACTION.{args.trade_state}"
        endpoint = "/api/billing/wechat/notify"

    envelope = {
        "id": f"evt-{uuid.uuid4().hex[:24]}",
        "create_time": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "resource_type": "encrypt-resource",
        "event_type": event_type,
        "summary": "模拟回调",
        "resource": _encrypt_resource(resource, WECHAT_PAY_API_V3_KEY),
    }
    # 签名必须覆盖发送原文的精确字节：先序列化成最终 body，再签名、再发送
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(now.timestamp()))
    nonce = uuid.uuid4().hex
    signature = key.sign(
        f"{ts}\n{nonce}\n{body}\n".encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    headers = {
        "Content-Type": "application/json",
        "Wechatpay-Timestamp": ts,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": base64.b64encode(signature).decode("ascii"),
        "Wechatpay-Serial": "PUB_KEY_ID_SIMULATED",
    }

    for i in range(max(args.repeat, 1)):
        req = Request(
            f"{args.url.rstrip('/')}{endpoint}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:  # noqa: S310 — 本地联调目标地址由参数显式给出
                print(f"[{i + 1}/{args.repeat}] HTTP {resp.status} body={resp.read().decode('utf-8')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{i + 1}/{args.repeat}] 请求失败：{exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
