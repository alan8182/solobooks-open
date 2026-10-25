# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""MCP 接入 M2 测试：资源模板、提示词模板。"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_CORE_DB


@pytest.fixture(autouse=True)
async def _mcp_schema():
    from db.base import Base
    import db.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_CORE_DB.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    yield


from tests.test_mcp_server import _ensure_user, _issue_raw_token, _validate

EXPECTED_RESOURCES = {
    "solobooks://entries/{entry_id}",
    "solobooks://reports/{month}",
    "solobooks://close-checks/{month}",
    "solobooks://company/profile",
}


async def _set_identity(user_id: str, scopes: str = "read"):
    from agent.mcp_server import _mcp_identity

    identity = await _validate(await _issue_raw_token(user_id, scopes=scopes))
    token = _mcp_identity.set(identity)
    return identity, token


def _resource_text(result) -> str:
    """fastmcp read_resource 返回 ResourceResult；提取文本内容。"""
    if hasattr(result, "content") and isinstance(result.content, list) and result.content:
        part = result.content[0]
        return part.text if hasattr(part, "text") else str(part)
    return str(result)


class TestResourceTemplates:
    async def test_registered_templates(self):
        from agent.mcp_server import mcp

        templates = await mcp._list_resource_templates()
        assert {t.uri_template for t in templates} == EXPECTED_RESOURCES

    async def test_read_month_summary_resource(self):
        from agent.mcp_server import _mcp_identity, mcp

        identity, token = await _set_identity("mcp_m2_reader")
        try:
            result = await mcp.read_resource("solobooks://reports/2026-01")
        finally:
            _mcp_identity.reset(token)
        assert "2026-01" in _resource_text(result)

    async def test_resource_reads_require_read_scope(self):
        from agent.mcp_server import _mcp_identity, mcp

        # 无身份（未初始化）应抛错
        with pytest.raises(Exception, match="身份未初始化"):
            await mcp.read_resource("solobooks://reports/2026-01")

        identity, token = await _set_identity("mcp_m2_noreader", scopes="read")
        _mcp_identity.reset(token)
        assert identity.has_scope("read")

    async def test_resource_calls_are_logged(self):
        from agent.mcp_server import _mcp_identity, mcp

        identity, token = await _set_identity("mcp_m2_logged")
        try:
            await mcp.read_resource("solobooks://reports/2026-01")
        finally:
            _mcp_identity.reset(token)

        from sqlalchemy import select

        from db.models import MCPCallLog
        from db.session import session_scope

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(MCPCallLog)
                    .where(MCPCallLog.user_id == "mcp_m2_logged", MCPCallLog.tool == "resource:solobooks://reports/{month}")
                    .order_by(MCPCallLog.created_at.desc())
                )
            ).scalars().first()
        assert row is not None and row.success is True

    async def test_entry_detail_resource_returns_json(self):
        from agent.mcp_server import _mcp_identity, mcp

        identity, token = await _set_identity("mcp_m2_entry")
        try:
            result = await mcp.read_resource("solobooks://entries/not-exists-id")
        finally:
            _mcp_identity.reset(token)
        assert "found" in _resource_text(result)


class TestPrompts:
    async def test_prompts_registered(self):
        from agent.mcp_server import mcp

        prompts = await mcp.list_prompts()
        names = {p.name for p in prompts}
        assert {"closing_guide", "reconciliation_review"} <= names

    async def test_closing_guide_renders_month(self):
        from agent.mcp_server import mcp

        result = await mcp.render_prompt("closing_guide", {"month": "2026-09"})
        text = result.messages[0].content.text if hasattr(result, "messages") else str(result)
        assert "2026-09" in text
        assert "run_close_checks" in text
        assert "不要声称已结账" in text

    async def test_reconciliation_review_renders_month(self):
        from agent.mcp_server import mcp

        result = await mcp.render_prompt("reconciliation_review", {"month": "2026-10"})
        text = result.messages[0].content.text if hasattr(result, "messages") else str(result)
        assert "2026-10" in text
        assert "list_unmatched" in text
