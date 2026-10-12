"""MCP 接入 M0 测试：PAT 服务、工具注册、HTTP 鉴权与工具执行一致性。"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_CORE_DB


@pytest.fixture(autouse=True)
async def _mcp_schema():
    """独立可运行：对共享测试库幂等建表（与 client fixture 的 create_all 同源）。"""
    from db.base import Base
    import db.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_CORE_DB.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    yield


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_user(user_id: str, *, disabled: bool = False) -> None:
    from db.models import AuthUser, CompanyMember
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(AuthUser, user_id)
        if row is None:
            session.add(AuthUser(user_id=user_id, current_company_id="c1", created_at="2026-01-01T00:00:00Z", last_login_at="2026-01-01T00:00:00Z"))
        else:
            row.is_disabled = disabled
        # ensure_tenant 只在公司无成员时自动补建；共享测试库已有其他成员时，
        # 缺成员行的用户会被判 403（entry_service 等走租户校验的路径）
        member = (
            await session.execute(
                select(CompanyMember).where(
                    CompanyMember.user_id == user_id, CompanyMember.company_id == "c1"
                )
            )
        ).scalars().first()
        if member is None:
            session.add(CompanyMember(company_id="c1", user_id=user_id, role="owner", status="active"))


class TestPatTokenService:
    async def test_issue_and_validate_roundtrip(self):
        from services.mcp_token_service import issue_token, validate_token

        await _ensure_user("mcp_u_roundtrip")
        raw, row = await issue_token(user_id="mcp_u_roundtrip", company_id="c1", name="t")
        assert raw.startswith("sbk_")
        identity = await validate_token(raw)
        assert identity is not None
        assert identity.context.user_id == "mcp_u_roundtrip"
        assert identity.context.company_id == "c1"
        assert identity.has_scope("read")
        assert not identity.has_scope("write:draft")
        assert identity.token_id == row.id

    async def test_unknown_token_rejected(self):
        from services.mcp_token_service import validate_token

        assert await validate_token("sbk_not_issued_at_all") is None
        assert await validate_token("") is None
        assert await validate_token("Bearer sbk_garbage") is None

    async def test_revoked_token_rejected(self):
        from services.mcp_token_service import issue_token, revoke_token, validate_token

        await _ensure_user("mcp_u_revoke")
        raw, row = await issue_token(user_id="mcp_u_revoke", company_id="c1", name="t")
        assert await validate_token(raw) is not None
        assert await revoke_token(token_id=row.id, user_id="mcp_u_revoke") is True
        assert await validate_token(raw) is None
        # 重复吊销与跨用户吊销都返回 False
        assert await revoke_token(token_id=row.id, user_id="mcp_u_revoke") is False
        assert await revoke_token(token_id=row.id, user_id="someone_else") is False

    async def test_expired_token_rejected(self):
        from services.mcp_token_service import issue_token, validate_token

        await _ensure_user("mcp_u_expired")
        raw, _row = await issue_token(user_id="mcp_u_expired", company_id="c1", name="t", expires_days=-1)
        assert await validate_token(raw) is None

    async def test_disabled_user_token_rejected(self):
        from services.mcp_token_service import issue_token, validate_token

        await _ensure_user("mcp_u_disabled")
        raw, _row = await issue_token(user_id="mcp_u_disabled", company_id="c1", name="t")
        assert await validate_token(raw) is not None
        await _ensure_user("mcp_u_disabled", disabled=True)
        assert await validate_token(raw) is None

    async def test_missing_user_row_rejected(self):
        """用户行不存在时必须拒绝（PAT 不享受 require_auth 的兜底放行）。"""
        from services.mcp_token_service import issue_token, validate_token

        raw, _row = await issue_token(user_id="mcp_u_ghost", company_id="c1", name="t")
        assert await validate_token(raw) is None

    async def test_bearer_prefix_normalized(self):
        from services.mcp_token_service import issue_token, validate_token

        await _ensure_user("mcp_u_bearer")
        raw, _row = await issue_token(user_id="mcp_u_bearer", company_id="c1", name="t")
        assert await validate_token(f"Bearer {raw}") is not None


class TestMcpToolRegistration:
    async def test_open_tools_registered(self):
        """读 + 写草稿全集注册（19 = 内部 20 - 刻意不开放的 update_company_profile）。"""
        from agent.mcp_server import MCP_READ_TOOLS, MCP_WRITE_TOOLS, mcp

        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == MCP_READ_TOOLS | MCP_WRITE_TOOLS
        assert len(names) == 19

    async def test_company_profile_write_not_exposed(self):
        """画像写入不对 MCP 开放：工具仍在内部层，但 MCP 侧不可见不可调。"""
        from agent.mcp_server import mcp
        from agent.tools import TOOLS_BY_NAME

        # 内部层保有该工具（产品内 agent 继续使用）
        assert "update_company_profile" in TOOLS_BY_NAME
        tools = {t.name for t in await mcp.list_tools()}
        assert "update_company_profile" not in tools
        # 画像的读取端保持开放
        assert "get_company_profile" in tools

    async def test_write_tools_exposed_with_scope_gate(self):
        from agent.mcp_server import MCP_WRITE_TOOLS, _required_scope, mcp

        tools = {t.name for t in await mcp.list_tools()}
        assert MCP_WRITE_TOOLS <= tools
        for name in MCP_WRITE_TOOLS:
            assert _required_scope(name) == "write:draft"

    async def test_schema_matches_openai_tool_schema(self):
        from agent.mcp_server import mcp
        from agent.tools import TOOLS

        tools = {t.name: t for t in await mcp.list_tools()}
        for schema in TOOLS:
            if schema.name not in tools:
                continue
            assert tools[schema.name].parameters == schema.parameters
            assert tools[schema.name].description == schema.description


class TestToolExecutionParity:
    """经 MCP handler 与经内部 agent execute_tool 调用结果一致。"""

    async def test_parity_get_month_summary(self):
        from agent.mcp_server import _mcp_identity, mcp
        from agent.tools import execute_tool
        from deps.auth import AuthContext
        from services.mcp_token_service import issue_token

        await _ensure_user("mcp_u_parity")
        raw, _row = await issue_token(user_id="mcp_u_parity", company_id="c1", name="t")
        identity = await _validate(raw)

        token = _mcp_identity.set(identity)
        try:
            mcp_result = await _call_mcp_tool(mcp, "get_month_summary", {"month": "2026-01"})
        finally:
            _mcp_identity.reset(token)

        direct = await execute_tool("get_month_summary", {"month": "2026-01"}, AuthContext(user_id="mcp_u_parity", company_id="c1"))
        assert mcp_result == direct

    async def test_unbound_identity_raises(self):
        from agent.mcp_server import _mcp_identity, mcp
        from services.mcp_token_service import issue_token

        await _ensure_user("mcp_u_parity2")
        raw, _row = await issue_token(user_id="mcp_u_parity2", company_id="c1", name="t")
        identity = await _validate(raw)
        token = _mcp_identity.set(identity)
        _mcp_identity.reset(token)  # 立即还原：模拟身份缺失
        with pytest.raises(Exception):
            await _call_mcp_tool(mcp, "get_month_summary", {"month": "2026-01"})

    async def test_dict_result_is_single_payload(self):
        """dict 结果只发 content 一路：FastMCP 对 dict 返回值会自动双发
        content(JSON 文本) + structuredContent，客户端两份都进上下文（token ×2），
        handler 必须收敛为 ToolResult 单载荷。"""
        from agent.mcp_server import _mcp_identity, mcp
        from services.mcp_token_service import issue_token

        await _ensure_user("mcp_u_single")
        raw, _row = await issue_token(user_id="mcp_u_single", company_id="c1", name="t")
        identity = await _validate(raw)
        token = _mcp_identity.set(identity)
        try:
            result = await mcp.call_tool("get_month_summary", {"month": "2026-01"})
        finally:
            _mcp_identity.reset(token)
        assert result.structured_content is None
        assert len(result.content) == 1
        text_block = result.content[0]
        payload = json.loads(text_block.text)
        assert isinstance(payload, dict)
        # 精确等于紧凑序列化：separators 省字符、ensure_ascii=False 保中文不膨胀
        assert text_block.text == json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _validate(raw: str):
    from services.mcp_token_service import validate_token

    identity = await validate_token(raw)
    assert identity is not None
    return identity


async def _call_mcp_tool(mcp, name: str, arguments: dict):
    result = await mcp.call_tool(name, arguments)
    # 单载荷改造后：dict 结果只走 content 一路（structuredContent 恒为 None），
    # 这里把唯一文本块解析回 dict，保持与 execute_tool 直返 dict 的一致性断言
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    if hasattr(result, "data") and result.data is not None:
        return result.data
    blocks = list(getattr(result, "content", None) or [])
    if len(blocks) == 1 and hasattr(blocks[0], "text"):
        try:
            return json.loads(blocks[0].text)
        except ValueError:
            return blocks[0].text
    if isinstance(result, tuple):
        return result[-1]
    return result


class TestMcpMountOrder:
    def test_mcp_mount_is_last_route(self):
        """MCP mount 必须是 main.app 的最后一个路由。

        mount("/") 依赖「全部业务路由先于挂载注册」的隐式约定——谁往 main.py
        的 app.mount 之后追加路由，会被 MCP 应用静默截胡，这里用路由顺序断言
        把该约定机械化。
        """
        from starlette.routing import Mount

        from main import app

        last = app.router.routes[-1]
        assert isinstance(last, Mount), (
            "MCP mount 必须保持在 main.py 的最后：请在 app.mount(...) 之前注册新路由"
        )


class TestMcpHttpAuth:
    """挂载在 /mcp 上的 PAT 中间件鉴权（401 分支）。"""

    def test_missing_authorization_401(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == -32001

    def test_invalid_token_401(self, client):
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Authorization": "Bearer sbk_invalid"},
        )
        assert resp.status_code == 401

    def test_auth_failure_logged(self, client, caplog):
        """认证失败留 warning：来源 IP + token 指纹（token_hash 前缀），不落明文。"""
        import logging

        from services.mcp_token_service import hash_token

        with caplog.at_level(logging.WARNING, logger="agent.mcp_server"):
            resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Authorization": "Bearer sbk_not_a_real_token"},
            )
        assert resp.status_code == 401
        assert "MCP 认证失败" in caplog.text
        assert "testclient" in caplog.text  # ASGI client 来源 IP
        assert hash_token("sbk_not_a_real_token")[:12] in caplog.text
        assert "sbk_not_a_real_token" not in caplog.text

    def test_dev_fallback_token_rejected_on_mcp(self, client):
        """开发兜底 token 对 /mcp 无效——MCP 鉴权独立于 AUTH_DEV_FALLBACK。"""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Authorization": "Bearer test-token-unit-test"},
        )
        assert resp.status_code == 401

    def test_valid_token_passes_middleware(self, client):
        """有效 PAT 到达 MCP 协议层（initialize 返回 200 JSON-RPC）。"""
        from services.mcp_token_service import issue_token

        raw, _row = asyncio.run(_issue_for_test_user())
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
            headers={
                "Authorization": f"Bearer {raw}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["serverInfo"]["name"] == "solobooks"


async def _issue_for_test_user() -> tuple[str, object]:
    from services.mcp_token_service import issue_token, validate_token

    await _ensure_user("test_user_001")
    raw, row = await issue_token(user_id="test_user_001", company_id="c1", name="pytest")
    assert await validate_token(raw) is not None
    return raw, row


async def _issue_raw_token(user_id: str, scopes: str = "read") -> str:
    """为任意（自动创建的）用户签发指定 scopes 的 PAT，返回明文。"""
    from services.mcp_token_service import issue_token

    await _ensure_user(user_id)
    raw, _row = await issue_token(user_id=user_id, company_id="c1", name="t", scopes=scopes)
    return raw


_issue_for = _issue_raw_token


class TestOptimizations:
    async def test_verify_token_alive(self):
        from services.mcp_token_service import issue_token, revoke_token, verify_token_alive

        await _ensure_user("mcp_u_verify_alive")
        raw, row = await issue_token(user_id="mcp_u_verify_alive", company_id="c1", name="t")
        assert await verify_token_alive(row.id) is True

        # 吊销后复核应为 False
        await revoke_token(token_id=row.id, user_id="mcp_u_verify_alive")
        assert await verify_token_alive(row.id) is False

    async def test_stdio_runtime_revocation_blocked(self):
        """测试 stdio 长驻进程场景：Token 启动后被吊销，超时复核时应阻断工具调用。"""
        from agent.mcp_server import _mcp_identity, _token_verified_at, mcp
        from services.mcp_token_service import issue_token, revoke_token, validate_token

        await _ensure_user("mcp_u_stdio_revoke")
        raw, row = await issue_token(user_id="mcp_u_stdio_revoke", company_id="c1", name="t")
        identity = await validate_token(raw)
        assert identity is not None

        token = _mcp_identity.set(identity)
        try:
            # 首次调用通过
            await mcp.call_tool("get_month_summary", {"month": "2026-01"})
            # 模拟在 Web 端吊销 Token
            await revoke_token(token_id=row.id, user_id="mcp_u_stdio_revoke")
            # 模拟超过 30s 缓存窗口
            _token_verified_at[identity.token_id] = 0.0

            with pytest.raises(Exception, match="访问令牌已失效或已被吊销"):
                await mcp.call_tool("get_month_summary", {"month": "2026-01"})
        finally:
            _mcp_identity.reset(token)

    async def test_rate_bucket_pruning(self):
        from collections import deque
        from agent.mcp_server import _check_rate_limit, _rate_buckets

        now = 1000.0
        # 塞入 135 个过期 bucket
        for i in range(135):
            _rate_buckets[f"stale_{i}"] = deque([now - 100.0])

        assert len(_rate_buckets) >= 135
        # 触发检查
        _check_rate_limit("fresh_token")
        # 超过 128 时过期 key 会被修剪
        assert len(_rate_buckets) <= 128

    async def test_month_parameter_schema_pattern(self):
        from agent.tools import TOOLS_BY_NAME

        summary_param = TOOLS_BY_NAME["get_month_summary"].parameters["properties"]["month"]
        assert "pattern" in summary_param
        assert summary_param["pattern"] == r"^\d{4}-(0[1-9]|1[0-2])$"

        close_param = TOOLS_BY_NAME["run_close_checks"].parameters["properties"]["month"]
        assert "pattern" in close_param

    async def test_run_close_checks_ttl_cache(self, monkeypatch):
        from agent import tools
        from agent.tools import _ANALYSIS_CACHE, _run_close_checks, ToolContext

        ctx = ToolContext(user_id="mcp_cache_u", company_id="c1")
        _ANALYSIS_CACHE.clear()

        calls = 0

        async def fake_checks(session, company_id, month):
            nonlocal calls
            calls += 1
            return {"canClose": True, "count": calls}

        from services import accounting_core_service

        monkeypatch.setattr(accounting_core_service, "period_close_checks", fake_checks)

        r1 = await _run_close_checks(ctx, "2026-01")
        assert r1["count"] == 1
        r2 = await _run_close_checks(ctx, "2026-01")
        assert r2["count"] == 1  # 命中短缓存
        assert calls == 1

