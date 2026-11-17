# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""模拟支付宝异步通知（本地/沙箱联调用，生产禁用）。

背景：notify_url 必须公网可达，本地开发收不到支付宝真实回调。本脚本用应用
私钥对通知参数做 RSA2 签名后 POST 到本地端点——只要 .env 的 ALIPAY_PUBLIC_KEY
与签名私钥配对（本地默认如此），验签即通过，全链路（验签→幂等→激活）可演练。

⚠️ 前提：backend/.env 本地联调配置 ALIPAY_PUBLIC_KEY=应用公钥（见 .env 注释）。
生产环境 ALIPAY_PUBLIC_KEY 必须换成平台下发的支付宝公钥，本脚本签名将不再
通过验签——这是预期行为，不是 bug。

用法：
    venv/bin/python scripts/alipay_simulate_notify.py \
        --order-no SB261113... --amount 598.00 \
        [--status TRADE_SUCCESS] [--url http://127.0.0.1:8001] [--repeat 2]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.alipay_client import sign_params  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟支付宝异步通知")
    parser.add_argument("--order-no", required=True, help="商户订单号 out_trade_no")
    parser.add_argument("--amount", required=True, help="金额（两位小数，如 598.00）")
    parser.add_argument("--status", default="TRADE_SUCCESS")
    parser.add_argument("--app-id", default="")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--repeat", type=int, default=1, help="重复推送次数（验证幂等）")
    args = parser.parse_args()

    from config import ALIPAY_APP_ID

    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    params: dict[str, str] = {
        "app_id": args.app_id or ALIPAY_APP_ID,
        "trade_no": f"SIM{now.strftime('%Y%m%d%H%M%S')}",
        "out_trade_no": args.order_no,
        "trade_status": args.status,
        "total_amount": args.amount,
        "buyer_logon_id": "sim****@alipay.com",
        "gmt_create": now.strftime("%Y-%m-%d %H:%M:%S"),
        "gmt_payment": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sign_type": "RSA2",
        "charset": "utf-8",
        "notify_type": "trade_status_sync",
        "notify_id": now.strftime("%Y%m%d%H%M%S%f"),
    }
    params["sign"] = sign_params(params)
    body = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())

    for i in range(max(args.repeat, 1)):
        req = Request(
            f"{args.url.rstrip('/')}/api/billing/alipay/notify",
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:  # noqa: S310 — 本地联调目标地址由参数显式给出
            print(f"[{i + 1}/{args.repeat}] HTTP {resp.status} body={resp.read().decode('utf-8')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
