# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""三方平台账单适配器注册表。

新增平台时只需要：
1. 在 ``services/`` 下实现该平台的 parse/import/records（或复用现有实现）；
2. 在 ``_ADAPTERS`` 里登记一个 ``PlatformBillAdapter``；
3. 在 ``content_markers`` 里写下能认出该平台表头的列名。

路由、前端上传卡和去重/凭证口径不需要再改一遍。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from services.alipay_bill_service import (
    import_alipay_bill,
    import_alipay_bill_summary,
    list_alipay_bill_records,
    parse_alipay_bill,
)
from services.wechat_bill_service import (
    import_wechat_bill,
    import_wechat_bill_summary,
    list_wechat_bill_records,
    parse_wechat_bill,
)

_DETAIL_IMPORT = Callable[..., Awaitable[Dict[str, Any]]]
_SUMMARY_IMPORT = Callable[..., Awaitable[Dict[str, Any]]]
_RECORDS_LIST = Callable[[str, str], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class PlatformBillAdapter:
    """一个平台的账单能力集合；平台差异全部收敛在适配器内。"""

    key: str
    label: str
    content_markers: tuple[str, ...]
    parse: Callable[..., Awaitable[Dict[str, Any]]]
    import_detail: _DETAIL_IMPORT
    import_summary: _SUMMARY_IMPORT
    list_records: _RECORDS_LIST


async def _parse_wechat(content: bytes, company_id: str = "") -> Dict[str, Any]:
    result = await parse_wechat_bill(content, company_id=company_id)
    return {**result, "platform": "wechat"}


async def _list_wechat(company_id: str, month: str) -> Dict[str, Any]:
    result = await list_wechat_bill_records(company_id, month)
    return {
        **result,
        "platform": "wechat",
        "items": [{**item, "platform": "wechat"} for item in result.get("items") or []],
    }


_ADAPTERS: Dict[str, PlatformBillAdapter] = {
    "wechat": PlatformBillAdapter(
        key="wechat",
        label="微信支付",
        content_markers=("微信订单号", "总交易单数"),
        parse=_parse_wechat,
        import_detail=import_wechat_bill,
        import_summary=import_wechat_bill_summary,
        list_records=_list_wechat,
    ),
    "alipay": PlatformBillAdapter(
        key="alipay",
        label="支付宝",
        content_markers=("支付宝交易号", "支付宝业务明细"),
        parse=parse_alipay_bill,
        import_detail=import_alipay_bill,
        import_summary=import_alipay_bill_summary,
        list_records=list_alipay_bill_records,
    ),
}

PLATFORM_LABELS = {key: adapter.label for key, adapter in _ADAPTERS.items()}


def detect_platform(content: bytes) -> str:
    """按文件内容识别平台；识别不了返回空串，由调用方报 422。"""
    sample = content[:65536]
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        for key, adapter in _ADAPTERS.items():
            if any(marker in text for marker in adapter.content_markers):
                return key
    return ""


def _adapter(platform: str) -> PlatformBillAdapter:
    adapter = _ADAPTERS.get(platform)
    if adapter is None:
        raise ValueError(f"暂不支持的平台：{platform}")
    return adapter


async def parse_platform_bill(platform: str, content: bytes, company_id: str = "") -> Dict[str, Any]:
    return await _adapter(platform).parse(content, company_id=company_id)


async def import_platform_bill(
    platform: str,
    company_id: str,
    user_id: str,
    *,
    import_mode: str,
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any] | None,
    occur_date: str = "",
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
) -> Dict[str, Any]:
    adapter = _adapter(platform)
    if import_mode == "summary" and summary:
        return await adapter.import_summary(
            company_id, user_id, summary,
            occur_date=occur_date,
            allow_backfill=allow_backfill, backfill_reason=backfill_reason,
        )
    return await adapter.import_detail(
        company_id, user_id, rows,
        allow_backfill=allow_backfill, backfill_reason=backfill_reason,
    )


async def list_platform_bill_records(company_id: str, month: str, platform: str = "") -> Dict[str, Any]:
    if platform:
        return await _adapter(platform).list_records(company_id, month)

    results = await asyncio.gather(*(adapter.list_records(company_id, month) for adapter in _ADAPTERS.values()))
    items = [item for result in results for item in result.get("items") or []]
    items.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("orderNo") or "")))
    summary = {
        "total": len(items),
        "totalOrderAmount": float(sum(result["summary"].get("totalOrderAmount", 0) for result in results)),
        "totalFee": float(sum(result["summary"].get("totalFee", 0) for result in results)),
        "totalSettled": float(sum(result["summary"].get("totalSettled", 0) for result in results)),
        "totalSubsidy": float(sum(result["summary"].get("totalSubsidy", 0) for result in results)),
    }
    return {"month": month, "platform": "", "items": items, "summary": summary}
