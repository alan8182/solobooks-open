# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""政策检索（DeepSeek Search）服务与 agent 工具回归测试。

HTTP 层通过替换 web_search_service._post_search 打桩，不产生真实外呼。
"""
import asyncio

import pytest

from agent.tools import ToolContext, execute_tool
from services import web_search_service


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


def _ok_body() -> dict:
    return {
        "results": [
            {
                "title": "财政部 税务总局公告2023年第19号",
                "url": "https://fgk.chinatax.gov.cn/zcfgk/x",
                "site_name": "国家税务总局",
                "date": "2023-08-01",
                "authority": 4,
                "snippet": "月销售额10万元以下免征增值税",
            },
            # 无 URL 的脏结果应在解析层被过滤
            {"title": "无URL条目", "url": "", "site_name": "x", "date": "", "authority": 1, "snippet": ""},
        ]
    }


@pytest.fixture
def stub_usage():
    """拦截 fire-and-forget 用量写入，避免任务泄漏到已关闭的事件循环。"""
    recorded: list = []
    patch = pytest.MonkeyPatch()
    patch.setattr("services.llm._record_usage", lambda **kw: recorded.append(kw))
    yield recorded
    patch.undo()


@pytest.mark.asyncio
async def test_search_builds_whitelist_query_and_strips_operators(monkeypatch, stub_usage):
    """调用方 query 自带 site:/lang: 操作符必须被清洗，白名单由服务端强制拼接。"""
    captured: dict = {}

    async def fake_post(payload: dict) -> _FakeResponse:
        captured["payload"] = payload
        return _FakeResponse(200, _ok_body())

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    result = await web_search_service.search_tax_policy(
        "site:evil.com 关税 lang:en 设备器具 一次性扣除 2027", user_id="u1", company_id="c1"
    )

    assert result["ok"] is True
    query = result["searchedQuery"]
    assert "evil.com" not in query and "lang:en" not in query
    assert "site:chinatax.gov.cn" in query and "site:flk.npc.gov.cn" in query
    assert query.endswith("lang:zh")
    assert captured["payload"]["query"] == query
    assert captured["payload"]["need_content"] is False
    # 无 URL 的脏结果被过滤，字段完成映射
    assert len(result["results"]) == 1
    assert result["results"][0]["authority"] == 4
    assert result["results"][0]["siteName"] == "国家税务总局"


@pytest.mark.asyncio
async def test_search_disabled_without_key(monkeypatch):
    import config

    monkeypatch.setattr(config, "DEEPSEEK_SEARCH_API_KEY", "")
    called: list = []

    async def fake_post(payload: dict) -> _FakeResponse:
        called.append(payload)
        return _FakeResponse(200, _ok_body())

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    result = await web_search_service.search_tax_policy("小规模 优惠")

    assert result == {"ok": False, "reason": "disabled"}
    assert not called


@pytest.mark.asyncio
async def test_search_empty_results(monkeypatch, stub_usage):
    async def fake_post(payload: dict) -> _FakeResponse:
        return _FakeResponse(200, {"results": []})

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    result = await web_search_service.search_tax_policy("查无此政策xyz")

    assert result["ok"] is False and result["reason"] == "empty"


@pytest.mark.asyncio
async def test_search_retries_rate_limit_then_records_usage(monkeypatch, stub_usage):
    calls: list = []

    async def fake_post(payload: dict) -> _FakeResponse:
        calls.append(payload)
        return _FakeResponse(429, {})

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    monkeypatch.setattr(web_search_service, "_RETRY_DELAY_S", 0)

    result = await web_search_service.search_tax_policy("查点东西", user_id="u1", company_id="c1")

    assert result["ok"] is False and result["reason"] == "rate_limited"
    assert len(calls) == 2  # 429 可重试：重试一次后仍失败
    assert stub_usage and stub_usage[0]["scene"] == "policy_search"
    assert stub_usage[0]["status"] == "error" and stub_usage[0]["error_type"] == "rate_limited"
    # 服务层不传 token（tokens=0 由 _record_usage 默认补齐，e2e 用例在库中验证）
    assert stub_usage[0].get("total_tokens", 0) == 0


@pytest.mark.asyncio
async def test_search_maps_insufficient_balance(monkeypatch, stub_usage):
    async def fake_post(payload: dict) -> _FakeResponse:
        return _FakeResponse(402, {})

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    result = await web_search_service.search_tax_policy("x")

    assert result["ok"] is False and result["reason"] == "insufficient_balance"


@pytest.mark.asyncio
async def test_search_tool_end_to_end_and_usage_row(monkeypatch):
    """工具链路 + 用量落库（真实 _record_usage 异步写 sqlite）。"""
    await _ensure_schema()

    async def fake_post(payload: dict) -> _FakeResponse:
        return _FakeResponse(200, _ok_body())

    monkeypatch.setattr(web_search_service, "_post_search", fake_post)
    result = await execute_tool(
        "search_tax_policy",
        {"query": "增值税法实施条例 施行日期"},
        ToolContext(user_id="u-search", company_id="c-search"),
    )
    assert result["ok"] is True
    assert result["results"][0]["url"].startswith("https://fgk.chinatax.gov.cn")

    # 缺必填参数被服务端 schema 校验拦截
    with pytest.raises(ValueError):
        await execute_tool("search_tax_policy", {}, ToolContext(user_id="u", company_id="c"))

    await asyncio.sleep(0.3)  # fire-and-forget 用量落库
    from sqlalchemy import select

    from db.models import LlmUsage
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(LlmUsage).where(
                    LlmUsage.scene == "policy_search", LlmUsage.company_id == "c-search"
                )
            )
        ).scalars().all()
    assert rows and rows[-1].status == "ok" and rows[-1].total_tokens == 0


def test_prompt_references_search_tool():
    """提示词门控升级：检索工具、来源披露与检索纪律均已写入。"""
    from agent.prompts import AGENT_SYSTEM_PROMPT

    assert "search_tax_policy" in AGENT_SYSTEM_PROMPT
    assert "来源站点与日期" in AGENT_SYSTEM_PROMPT
    assert "严禁包含公司名、金额、人名" in AGENT_SYSTEM_PROMPT
