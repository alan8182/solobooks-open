# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""三方支付平台账单的共用口径。

各平台适配器（微信、支付宝、后续平台）只负责「文件 → 规范行」，
金额解析、价税分离、去重键读取等会计口径统一放在这里，避免每个平台
各自实现一套导致口径漂移。
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Iterable

from services.database import get_conn
from services.vat_service import split_income_vat
from core.money import ZERO, money

# 平台待结算科目：微信/支付宝共用一套「先挂待结算、提现再转银行」的资金路径。
# 1012 是历史编码（其他货币资金），保留以兼容存量凭证。
WECHAT_CLEARING_CODE = "1012001"
ALIPAY_CLEARING_CODE = "1012002"
LEGACY_PLATFORM_CLEARING_CODE = "1012"
PLATFORM_CLEARING_CODES = (
    WECHAT_CLEARING_CODE,
    ALIPAY_CLEARING_CODE,
    LEGACY_PLATFORM_CLEARING_CODE,
)

# 平台净额结算凭证的来源值：由银行对账服务按净额生成，不能像普通凭证一样改分录。
SETTLEMENT_SOURCES = frozenset({
    "wechat_settlement",
    "alipay_settlement",
    "platform_settlement",
})


def parse_money(value: Any) -> Decimal:
    """账单金额 → 两位小数 Decimal；空值/脏值按 0 处理。"""
    try:
        return money(value or ZERO)
    except (TypeError, ValueError):
        return ZERO


def split_revenue_vat(gross: Decimal, vat_rate: Any) -> tuple[Decimal, Decimal]:
    """按征收率对收入做价税分离，返回（不含税净额，增值税额）。

    退款红冲（gross 为负）按绝对值拆分后镜像符号，保持与收入金额同号，
    这样借贷两侧都能直接用 ``abs`` 落行；``split_income_vat`` 本身不接受负数。
    """
    if gross < ZERO:
        net, tax = split_income_vat(-gross, vat_rate)
        return money(-net), money(-tax)
    net, tax = split_income_vat(gross, vat_rate)
    return net, tax


async def load_existing_source_keys(
    company_id: str,
    source: str,
    *,
    legacy_fields: Iterable[str] = (),
) -> set[str]:
    """读取某个平台已落库的去重键。

    新数据统一写 ``dedupFingerprint``；``legacy_fields`` 兼容历史平台字段
    （如微信的 ``wechat_transaction_key``）。查询按 ``source`` 隔离，避免
    同一账套下微信与支付宝的同名键互相误判。
    """
    if not company_id:
        return set()

    fields = ["$.dedupFingerprint", *(f"$.{field}" for field in legacy_fields)]
    clauses = " OR ".join(
        f"json_extract(payload, '{field}') IS NOT NULL" for field in fields
    )
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries "
            "WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = ? "
            f"AND ({clauses})",
            (company_id, source),
        )
        rows = await cursor.fetchall()
    except Exception:
        return set()
    finally:
        await conn.close()

    keys: set[str] = set()
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not isinstance(payload, dict):
            continue
        for field in ("dedupFingerprint", *legacy_fields):
            value = str(payload.get(field) or "").strip()
            if value:
                keys.add(value)
    return keys
