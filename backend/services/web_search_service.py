# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""DeepSeek Search 封装：agent 政策实时核验的唯一对外检索出口。

- 官方域名白名单由服务端拼接，并清洗调用方 query 自带的 site:/lang: 操作符（防绕过）；
- 任何失败都返回结构化 {"ok": False, "reason": ...}，绝不抛进 agent loop，
  调用方按"未核实"降级（见 agent/prompts.py 税务政策回答规范）；
- 每次实际调用 fire-and-forget 记 LlmUsage（scene=policy_search，token=0 不占用户配额）；
- 不传 ori_question：用户原话不出境，query 由调用方按提示词纪律只给政策关键词。

计费：每 1000 次成功请求 5 元，失败/无结果不计费（文档 /guides/search）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

import config

logger = logging.getLogger(__name__)

_SCENE = "policy_search"
_MODEL = "deepseek-search"
_RETRY_DELAY_S = 0.5
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_MAX_CONTENT_CHARS = 1500

# 调用方 query 里混入的检索操作符一律剔除：白名单与语言由服务端唯一决定。
_OPERATOR_RE = re.compile(r"-?site:\S+|-?lang:\S+", re.IGNORECASE)


def policy_search_enabled() -> bool:
    return (
        bool(config.DEEPSEEK_SEARCH_API_KEY)
        and not config.POLICY_SEARCH_OFF
        and bool(config.POLICY_SEARCH_DOMAINS)
    )


def build_whitelisted_query(raw: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", _OPERATOR_RE.sub(" ", raw or "")).strip()
    domains = " OR ".join(f"site:{d}" for d in config.POLICY_SEARCH_DOMAINS)
    return f"{cleaned} ({domains}) lang:zh"


async def _post_search(payload: Dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=config.POLICY_SEARCH_TIMEOUT_S) as client:
        return await client.post(
            f"{config.DEEPSEEK_SEARCH_BASE_URL}/tools/search",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_SEARCH_API_KEY}"},
            json=payload,
        )


def _parse_results(resp: httpx.Response) -> List[Dict[str, Any]]:
    try:
        body = resp.json()
    except ValueError:
        return []
    items = body.get("results") if isinstance(body, dict) else None
    if items is None and isinstance(body, dict):
        items = body.get("data")
    if isinstance(items, dict):
        items = items.get("results")
    parsed: List[Dict[str, Any]] = []
    for item in (items or [])[:50]:
        if not isinstance(item, dict):
            continue
        entry = {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "siteName": str(item.get("site_name") or "").strip(),
            "date": str(item.get("date") or "").strip(),
            "authority": item.get("authority"),
            "snippet": str(item.get("snippet") or item.get("summary") or "").strip(),
        }
        if item.get("content"):
            entry["content"] = str(item["content"])[:_MAX_CONTENT_CHARS]
        if entry["url"]:
            parsed.append(entry)
    return parsed


async def search_tax_policy(
    query: str,
    *,
    count: int = 5,
    need_content: bool = False,
    user_id: Optional[str] = None,
    company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """检索税务政策。永不抛异常；失败返回 {"ok": False, "reason": ...}。

    reason：disabled / timeout / network_error / auth_error / insufficient_balance /
    rate_limited / http_5xx / http_{code} / empty。
    """
    started = time.monotonic()
    if not policy_search_enabled():
        return {"ok": False, "reason": "disabled"}

    searched_query = build_whitelisted_query(query)
    payload = {
        "query": searched_query,
        "count": max(1, min(int(count or 5), 50)),
        "need_content": bool(need_content),
    }

    async def _record(status: str, error_type: Optional[str] = None) -> None:
        from services.llm import _record_usage

        _record_usage(
            model=_MODEL,
            scene=_SCENE,
            user_id=user_id,
            company_id=company_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error_type=error_type,
        )

    reason = "network_error"
    for attempt in range(2):
        try:
            resp = await _post_search(payload)
        except httpx.TimeoutException:
            reason = "timeout"
        except Exception as exc:  # noqa: BLE001
            logger.warning("policy search request failed: %s", exc)
            reason = "network_error"
        else:
            if resp.status_code == 200:
                results = _parse_results(resp)
                if not results:
                    await _record("error", "empty")
                    return {"ok": False, "reason": "empty"}
                await _record("ok")
                return {"ok": True, "searchedQuery": searched_query, "results": results}
            if resp.status_code == 402:
                reason = "insufficient_balance"
                break
            if resp.status_code in (401, 403):
                reason = "auth_error"
                break
            if resp.status_code in _RETRYABLE_STATUS:
                reason = "http_5xx" if resp.status_code >= 500 else "rate_limited"
            else:
                reason = f"http_{resp.status_code}"
                break
        if attempt == 0:
            await asyncio.sleep(_RETRY_DELAY_S)

    await _record("error", reason)
    return {"ok": False, "reason": reason}
