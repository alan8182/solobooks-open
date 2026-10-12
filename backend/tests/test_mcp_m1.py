"""MCP 接入 M1 测试：写入工具 scope 门禁、审批流落点、调用留痕、限流、token CRUD API。"""
import asyncio

import pytest
from sqlalchemy import select

from tests.conftest import TEST_CORE_DB
from tests.test_mcp_server import _ensure_user, _issue_raw_token, _issue_for, _call_mcp_tool, _validate


@pytest.fixture(autouse=True)
async def _mcp_schema():
    from sqlalchemy.ext.asyncio import create_async_engine

    from db.base import Base
    import db.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_CORE_DB.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    yield


PROPOSAL_ARGS = {
    "direction": "income",
    "amount": 800.0,
    "title": "咨询服务收入",
    "category": "主营业务收入",
}


async def _seed_disabled_member(user_id: str, company_id: str) -> None:
    """造「已是成员但被 owner 停用」的场景：签发门禁应与访问门禁同口径拒绝。"""
    from db.models import Company, CompanyMember
    from db.session import session_scope

    await _ensure_user(user_id)
    async with session_scope() as session:
        if await session.get(Company, company_id) is None:
            session.add(
                Company(
                    company_id=company_id,
                    owner_user_id=user_id,
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                )
            )
        existing = await session.get(CompanyMember, {"company_id": company_id, "user_id": user_id})
        if existing is None:
            session.add(
                CompanyMember(company_id=company_id, user_id=user_id, role="viewer", status="disabled")
            )


async def _pending_actions(user_id: str) -> list:
    from db.models import AgentAction
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AgentAction).where(
                    AgentAction.user_id == user_id, AgentAction.status == "pending"
                )
            )
        ).scalars().all()
        return list(rows)


async def _latest_call_log(tool: str, user_id: str):
    from db.models import MCPCallLog
    from db.session import session_scope

    async with session_scope() as session:
        row = (
            await session.execute(
                select(MCPCallLog)
                .where(MCPCallLog.tool == tool, MCPCallLog.user_id == user_id)
                .order_by(MCPCallLog.created_at.desc())
            )
        ).scalars().first()
        return row


class TestWriteToolScopeGate:
    async def test_read_scope_token_cannot_call_write_tool(self, caplog):
        import logging

        from agent.mcp_server import _mcp_identity, mcp

        identity = await _validate(await _issue_raw_token("mcp_m1_readonly", scopes="read"))
        token = _mcp_identity.set(identity)
        try:
            with caplog.at_level(logging.WARNING, logger="agent.mcp_server"):
                with pytest.raises(Exception, match="write:draft"):
                    await _call_mcp_tool(mcp, "propose_voucher", dict(PROPOSAL_ARGS))
        finally:
            _mcp_identity.reset(token)
        # 越权尝试留 warning（含 token_id 可对库定位），但不写 mcp_call_logs
        assert "越权尝试" in caplog.text
        assert identity.token_id in caplog.text
        # 门禁在前：没有产生任何待确认动作，也没有调用留痕
        assert await _pending_actions("mcp_m1_readonly") == []
        assert await _latest_call_log("propose_voucher", "mcp_m1_readonly") is None

    async def test_write_scope_token_creates_pending_action(self):
        from agent.mcp_server import _mcp_identity, mcp

        identity = await _validate(await _issue_raw_token("mcp_m1_writer", scopes="read,write:draft"))
        token = _mcp_identity.set(identity)
        try:
            result = await _call_mcp_tool(mcp, "propose_voucher", dict(PROPOSAL_ARGS))
        finally:
            _mcp_identity.reset(token)

        assert result["status"] == "pending"
        assert "草稿" in result["message"]
        actions = await _pending_actions("mcp_m1_writer")
        assert len(actions) == 1
        assert actions[0].action_type == "propose_voucher"
        assert actions[0].company_id == "c1"
        assert actions[0].payload["amount"] == 800.0
        # 调用留痕成功一行
        log = await _latest_call_log("propose_voucher", "mcp_m1_writer")
        assert log is not None and log.success is True

    async def test_preview_accruals_is_read_scope(self):
        from agent.mcp_server import _required_scope

        assert _required_scope("preview_accruals") == "read"


class TestCallLogging:
    async def test_failed_call_logged(self, monkeypatch):
        from agent import mcp_server
        from agent.mcp_server import _mcp_identity, mcp

        async def _boom(tool_name, arguments, identity):
            raise RuntimeError("模拟执行失败")

        monkeypatch.setattr(mcp_server, "_execute_with_identity", _boom)
        identity = await _validate(await _issue_raw_token("mcp_m1_fail", scopes="read"))
        token = _mcp_identity.set(identity)
        try:
            with pytest.raises(Exception):
                await _call_mcp_tool(mcp, "get_month_summary", {"month": "2026-01"})
        finally:
            _mcp_identity.reset(token)

        log = await _latest_call_log("get_month_summary", "mcp_m1_fail")
        assert log is not None
        assert log.success is False
        assert "模拟执行失败" in (log.error or "")

    async def test_business_error_dict_becomes_tool_error(self, monkeypatch):
        """工具层返回 {"error": msg}（不抛异常）→ 转 ToolError，留痕 success=False。"""
        import agent.tools as tools_mod
        from fastmcp.exceptions import ToolError

        from agent.mcp_server import _mcp_identity, mcp

        async def _biz_error(tool_name, arguments, ctx):
            return {"error": "账目记录不存在"}

        monkeypatch.setattr(tools_mod, "execute_tool", _biz_error)
        identity = await _validate(await _issue_raw_token("mcp_m1_bizerr", scopes="read"))
        token = _mcp_identity.set(identity)
        try:
            with pytest.raises(ToolError, match="账目记录不存在"):
                await _call_mcp_tool(mcp, "get_entry_detail", {"entry_id": "e_missing"})
        finally:
            _mcp_identity.reset(token)

        log = await _latest_call_log("get_entry_detail", "mcp_m1_bizerr")
        assert log is not None
        assert log.success is False
        assert "账目记录不存在" in (log.error or "")


class TestRateLimit:
    def test_sliding_window_rejects_after_limit(self, monkeypatch):
        from agent import mcp_server

        monkeypatch.setattr(mcp_server, "RATE_LIMIT_PER_MINUTE", 3)
        mcp_server._rate_buckets.clear()
        for _ in range(3):
            mcp_server._check_rate_limit("tok-limited")
        with pytest.raises(mcp_server.RateLimitError):
            mcp_server._check_rate_limit("tok-limited")
        # 其他 token 不受影响
        mcp_server._check_rate_limit("tok-other")

    def test_disabled_when_env_off(self, monkeypatch):
        from agent import mcp_server

        monkeypatch.setattr(mcp_server, "RATE_LIMIT_ENABLED", False)
        mcp_server._rate_buckets.clear()
        for _ in range(200):
            mcp_server._check_rate_limit("tok-unlimited")


class TestTokenCrudApi:
    """token CRUD API（JWT 鉴权走 test-token 兜底身份 test_user_001/c1）。"""

    def test_create_list_revoke_lifecycle(self, client, auth_headers):
        # 签发
        resp = client.post(
            "/api/mcp/tokens",
            json={"name": "界面签发", "scopes": "read,write:draft", "expires_days": 30},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["token"].startswith("sbk_")
        assert body["item"]["scopes"] == "read,write:draft"
        token_id = body["item"]["id"]

        # 列表可见
        listing = client.get("/api/mcp/tokens", headers=auth_headers).json()["tokens"]
        match = [t for t in listing if t["id"] == token_id]
        assert len(match) == 1
        assert match[0]["revokedAt"] is None

        # 吊销 → 列表带 revokedAt → 重复吊销 404
        assert client.delete(f"/api/mcp/tokens/{token_id}", headers=auth_headers).status_code == 200
        listing = client.get("/api/mcp/tokens", headers=auth_headers).json()["tokens"]
        assert [t for t in listing if t["id"] == token_id][0]["revokedAt"] is not None
        assert client.delete(f"/api/mcp/tokens/{token_id}", headers=auth_headers).status_code == 404

    def test_invalid_scope_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/mcp/tokens", json={"name": "x", "scopes": "admin"}, headers=auth_headers
        )
        assert resp.status_code == 400

    def test_write_scope_requires_read(self, client, auth_headers):
        resp = client.post(
            "/api/mcp/tokens", json={"name": "x", "scopes": "write:draft"}, headers=auth_headers
        )
        assert resp.status_code == 400

    def test_foreign_company_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/mcp/tokens",
            json={"name": "x", "scopes": "read", "company_id": "c_not_member"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_disabled_member_company_rejected(self, client, auth_headers):
        """被停用的成员不能再为该公司签发令牌（与 ensure_tenant 的 active 口径一致）。"""
        asyncio.run(_seed_disabled_member("test_user_001", "c_disabled_member"))
        resp = client.post(
            "/api/mcp/tokens",
            json={"name": "x", "scopes": "read", "company_id": "c_disabled_member"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        assert client.get("/api/mcp/tokens").status_code == 401


class TestHttpWriteToolCall:
    """HTTP 全链路：有效 write:draft 令牌经 /mcp 调 propose_voucher 产生草稿。"""

    def test_tools_call_creates_pending_action(self, client):
        raw = asyncio.run(_issue_for("mcp_m1_http", scopes="read,write:draft"))
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "propose_voucher", "arguments": dict(PROPOSAL_ARGS)},
            },
            headers={
                "Authorization": f"Bearer {raw}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert resp.status_code == 200
        actions = asyncio.run(_pending_actions("mcp_m1_http"))
        assert len(actions) == 1
        assert actions[0].payload["title"] == "咨询服务收入"
