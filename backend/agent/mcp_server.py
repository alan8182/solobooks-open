# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""MCP Server：把 agent 工具层开放为 MCP 协议端点。

复用 `agent/tools.py` 的 Tool 四件套与 `execute_tool` 统一入口：
- OpenAI function schema 的 `parameters` 就是 JSON Schema，直接作为 MCP 工具的
  inputSchema 广播；handler 经 `execute_tool(name, args, ctx)` 转调，零业务改动。
- 鉴权用 contextvar 承载 PAT 身份：HTTP 路径由 PatAuthMiddleware 在请求入口
  校验一次并 set；stdio 路径由 scripts/mcp_stdio.py 启动时校验一次并 set。
  工具执行处于中间件同任务链内，contextvar 天然继承。
- 安全底线（方案 §3.1）：MCP 侧永不直接入账。写入类工具（propose_*）只产生
  pending agent_actions，产品内确认后才走既有写路径；scope `write:draft`
  才可调用，默认 PAT 只签发 `read`。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool, ToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent.tools import TOOLS, Tool
from services.mcp_token_service import SCOPE_WRITE, PatIdentity, hash_token, validate_token

logger = logging.getLogger(__name__)

# 纯只读：查询账目/流水/对账差异/结账检查/征期
MCP_READ_TOOLS = frozenset(
    {
        "get_month_summary",
        "list_entries",
        "list_invoice_entries",
        "list_bank_statements",
        "list_fixed_assets",
        "list_intangible_assets",
        "get_advance_balances",
        "list_private_expenses",
        "get_aging",
        "list_todos",
        "search_books",
        "get_company_profile",
        "get_entry_detail",
        "run_close_checks",
        "list_unmatched",
        "get_filing_deadlines",
        "generate_close_report",
        "generate_filing_package",
        # 纯预览，无副作用（只读）
        "preview_accruals",
        "preview_asset_actions",
    }
)

# 写入类：只产生待确认草稿（agent_actions），产品内人工确认。
# 注意：update_company_profile 刻意不对 MCP 开放——画像是驱动后续 AI 建议的
# 长期记忆，写入必须留在产品内（登录态 + 可见性），外部令牌直写等于给
# 「建议回路」开投毒通道（notes 还会被逐字拼进 agent system prompt）。
MCP_WRITE_TOOLS = frozenset(
    {
        "propose_voucher",
        "propose_vouchers",
        "propose_statement_vouchers",
        "propose_match",
        "propose_accrual",
        "propose_asset_closure",
        "record_private_expense",
    }
)

# 每个 PAT 每分钟工具调用上限（进程内滑动窗口；多实例部署需换集中存储）。
# 60 会拦住 AI 客户端的连环工具链（单任务连环 20+ 次调用常见，2026-10-25
# 对抗压测 20 并发全 429）；120 对人类用户仍是充足的保护阈值。
RATE_LIMIT_PER_MINUTE = int(os.getenv("MCP_RATE_LIMIT_PER_MINUTE", "120"))
RATE_LIMIT_ENABLED = os.getenv("MCP_RATE_LIMIT", "1").strip().lower() not in ("0", "false", "off")

_ARGUMENTS_DIGEST_LIMIT = 2000
_ERROR_TEXT_LIMIT = 500

# 当前请求的 PAT 身份（HTTP：中间件每请求 set；stdio：启动时 set 一次）
_mcp_identity: contextvars.ContextVar[PatIdentity | None] = contextvars.ContextVar(
    "mcp_identity", default=None
)

# token_id -> 最近调用时间戳（滑动窗口限流）
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

# token_id -> 最近一次验证通过的时间戳（stdio 长驻进程 30s 运行时复核）
_token_verified_at: dict[str, float] = {}


class PatAuthError(Exception):
    """PAT 校验失败，中间件转为 401。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RateLimitError(Exception):
    """限流命中，中间件转为 429。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _check_rate_limit(token_id: str) -> None:
    """滑动窗口限流；超限抛 RateLimitError。"""
    if not RATE_LIMIT_ENABLED:
        return
    now = time.monotonic()
    # ponytail: 限制字典体积，惰性逐出超过 60s 无调用的空/过期 bucket
    if len(_rate_buckets) > 128:
        stale = [k for k, q in _rate_buckets.items() if not q or now - q[-1] > 60.0]
        for k in stale:
            _rate_buckets.pop(k, None)

    bucket = _rate_buckets[token_id]
    while bucket and now - bucket[0] > 60.0:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        raise RateLimitError(f"调用过于频繁（每分钟上限 {RATE_LIMIT_PER_MINUTE} 次），请稍后重试")
    bucket.append(now)


def _client_ip(scope: dict, headers: dict) -> str:
    """取来源 IP：nginx 等反代优先看 X-Forwarded-For 首段，回退 ASGI client。"""
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return f"{client[0]}:{client[1]}" if client else "unknown"


def _token_fingerprint(header_value: str | None) -> str:
    """token 指纹（sha256 前 12 位，即 mcp_tokens.token_hash 前缀），绝不落明文。"""
    if not header_value or not header_value.strip():
        return "无"
    return hash_token(header_value)[:12]


async def authenticate_authorization(header_value: str | None) -> PatIdentity:
    """校验 Authorization 头中的 PAT，失败抛 PatAuthError。"""
    if not header_value or not header_value.strip():
        raise PatAuthError("缺少 Authorization 头")
    identity = await validate_token(header_value.strip())
    if identity is None:
        raise PatAuthError("无效或已失效的访问令牌")
    return identity


class PatAuthMiddleware:
    """纯 ASGI 中间件：/mcp 每个请求校验 PAT、限流并注入 contextvar。

    独立于 require_auth / AUTH_DEV_FALLBACK——开发兜底 token 对 /mcp 无效。
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # 本中间件只负责 /mcp。子应用被 mount 在根路径当兜底，未命中的 /api/*
        # （拼错的路由、还没加载的新路由）也会落到这里——若照样返回 401 JSON-RPC，
        # 前端 axios 拦截器会把「接口不存在」当成登录失效，直接把用户踢回登录页。
        # 交给内部路由处理，让它老老实实回 404。
        path = scope.get("path", "")
        if path != "/mcp" and not path.startswith("/mcp/"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client_ip = _client_ip(scope, headers)
        try:
            identity = await authenticate_authorization(headers.get("authorization"))
            _check_rate_limit(identity.token_id)
        except PatAuthError as exc:
            # 安全可观测：认证失败必须留痕（IP + token 指纹，不含明文）
            logger.warning(
                "MCP 认证失败（%s）来自 %s，token 指纹 %s",
                exc.message,
                client_ip,
                _token_fingerprint(headers.get("authorization")),
            )
            response = JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32001, "message": exc.message}, "id": None},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        except RateLimitError as exc:
            logger.warning("MCP 限流命中（token %s）来自 %s", identity.token_id, client_ip)
            response = JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32002, "message": exc.message}, "id": None},
                status_code=429,
            )
            await response(scope, receive, send)
            return
        _token_verified_at[identity.token_id] = time.monotonic()
        token = _mcp_identity.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _mcp_identity.reset(token)


def _current_identity() -> PatIdentity:
    identity = _mcp_identity.get()
    if identity is None:
        raise RuntimeError("MCP 身份未初始化（中间件缺失或 stdio 启动未注入）")
    return identity


async def _ensure_token_alive(identity: PatIdentity) -> None:
    """确认 token 是否仍有效。HTTP 经由中间件已刷新；stdio 进程长驻时每 30s 运行时复核。"""
    now = time.monotonic()
    # ponytail: stdio 模式长驻进程不会走 HTTP 中间件，加 30s 运行时复核，避免 token 吊销或用户禁用后仍被滥用
    if now - _token_verified_at.get(identity.token_id, 0.0) > 30.0:
        from services.mcp_token_service import verify_token_alive

        if not await verify_token_alive(identity.token_id):
            logger.warning("MCP 令牌已失效或被吊销：token %s", identity.token_id)
            raise PermissionError("访问令牌已失效或已被吊销，请在梭罗记账重新签发")
        _token_verified_at[identity.token_id] = now


def _required_scope(tool_name: str) -> str:
    return "read" if tool_name in MCP_READ_TOOLS else SCOPE_WRITE


def _arguments_digest(arguments: dict) -> str:
    try:
        raw = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(arguments)
    if len(raw) > _ARGUMENTS_DIGEST_LIMIT:
        raw = raw[:_ARGUMENTS_DIGEST_LIMIT] + "…(截断)"
    return raw


async def _log_call(identity: PatIdentity, tool_name: str, arguments: dict, started: float, exc: BaseException | None) -> None:
    from db.models import MCPCallLog
    from db.session import session_scope

    try:
        async with session_scope() as session:
            session.add(
                MCPCallLog(
                    token_id=identity.token_id,
                    user_id=identity.context.user_id,
                    company_id=identity.context.company_id,
                    tool=tool_name,
                    arguments_digest=_arguments_digest(arguments),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    success=exc is None,
                    error=None if exc is None else str(exc)[:_ERROR_TEXT_LIMIT],
                )
            )
    except Exception:  # noqa: BLE001 —— 日志失败绝不影响工具调用本身
        logger.exception("mcp_call_logs 写入失败")


def _build_resources(mcp: FastMCP) -> None:
    """只读资源模板：客户端可按 URI 直接订阅数据（等价于对应工具，read scope）。

    ponytail: 资源模板当前提供 4 个只读 URI 镜像，供客户端显式 @引用。为避免两套接口膨胀，
    暂不增加新资源模板（YAGNI：Agent 主交互完全由 Tools 承载）。
    """
    from fastmcp.resources.template import FunctionResourceTemplate

    templates = [
        {
            "uri_template": "solobooks://entries/{entry_id}",
            "name": "entry_detail",
            "description": "单笔账目详情（含凭证状态、分类、单据信息）",
            "tool": "get_entry_detail",
            "args": {"entry_id": "entry_id"},
            "params": {"entry_id": {"type": "string", "description": "账目 ID"}},
        },
        {
            "uri_template": "solobooks://reports/{month}",
            "name": "month_summary",
            "description": "某月账目汇总（借贷合计、平衡状态）",
            "tool": "get_month_summary",
            "args": {"month": "month"},
            "params": {"month": {"type": "string", "description": "月份，格式 YYYY-MM"}},
        },
        {
            "uri_template": "solobooks://close-checks/{month}",
            "name": "close_checks",
            "description": "某月 closing.v1 结账快照（含 snapshotHash、periodVersion 与 findings）",
            "tool": "run_close_checks",
            "args": {"month": "month"},
            "params": {"month": {"type": "string", "description": "月份，格式 YYYY-MM"}},
        },
        {
            "uri_template": "solobooks://company/profile",
            "name": "company_profile",
            "description": "公司画像（行业、纳税人身份、分类习惯等 Agent 记忆）",
            "tool": "get_company_profile",
            "args": {},
            "params": {},
        },
    ]

    for spec in templates:
        tool_name = spec["tool"]
        arg_map: dict = spec["args"]
        uri_template_display = spec["uri_template"]

        async def _read(_tool=tool_name, _arg_map=arg_map, _uri=uri_template_display, **kwargs: Any) -> str:
            identity = _current_identity()
            await _ensure_token_alive(identity)
            if not identity.has_scope("read"):
                logger.warning("MCP 越权尝试被拒：token %s 无 read 权限读资源 %s", identity.token_id, _uri)
                raise PermissionError("令牌缺少 read 权限")
            started = time.monotonic()
            tool_args = {dst: kwargs[src] for src, dst in _arg_map.items() if src in kwargs}
            try:
                payload = await _execute_with_identity(_tool, tool_args, identity)
                await _log_call(identity, f"resource:{_uri}", kwargs, started, None)
            except BaseException as exc:  # noqa: BLE001
                await _log_call(identity, f"resource:{_uri}", kwargs, started, exc)
                raise
            return json.dumps(payload, ensure_ascii=False, default=str)

        _read.__name__ = f"mcp_resource_{spec['name']}"
        mcp.add_template(
            FunctionResourceTemplate(
                uri_template=spec["uri_template"],
                name=spec["name"],
                description=spec["description"],
                mime_type="application/json",
                parameters=spec["params"],
                fn=_read,
            )
        )


def _build_prompts(mcp: FastMCP) -> None:
    """预置提示词模板：把产品方法论带给外部客户端。"""
    from fastmcp.prompts import PromptArgument
    from fastmcp.prompts.function_prompt import FunctionPrompt

    mcp.add_prompt(
        FunctionPrompt(
            name="closing_guide",
            description="月度结账安全检查：读取统一快照、解释 findings，并可在 write:draft 权限下创建修复草稿",
            arguments=[PromptArgument(name="month", description="月份，格式 YYYY-MM", required=True)],
            fn=lambda month: (
                f"请对梭罗记账 {month} 月度结账做安全检查。步骤：\n"
                "1. 调用 run_close_checks(month)，保留 schemaVersion、snapshotHash、periodVersion 和 findings；\n"
                "2. 用 list_unmatched、preview_accruals、preview_asset_actions 定位问题时，传入同一 snapshotHash；\n"
                "3. 只引用工具返回的金额、笔数、状态、findingId 和权威 ID，禁止自行加总或推测；\n"
                "4. 只有令牌具备 write:draft 时，才可按 findingId 调用 propose_match、propose_statement_vouchers、"
                "propose_accrual 或 propose_asset_closure（后者引用 ASSET_CLOSURE finding）；"
                "这些只创建待确认草稿，绝不表示已执行；\n"
                "5. 修复动作确认后重新运行 run_close_checks；最终锁账必须由用户在梭罗记账的「月度结账」向导中完成；\n"
                "6. 回复时必须原样展示工具返回的 manualClose.url，并渲染为可点击 Markdown 链接；说明该链接会要求登录，"
                "打开后仍会重新检查。禁止把链接说成已经锁账，也禁止寻找或调用任何锁账工具。\n"
                "最后用老板看得懂的语言汇总：本月当前能否结账、还差什么、哪些草稿待确认，并附上网页锁账入口。"
            ),
        )
    )

    mcp.add_prompt(
        FunctionPrompt(
            name="reconciliation_review",
            description="对账安全审查：基于同一结账快照列出差异，并可在 write:draft 权限下创建待确认草稿",
            arguments=[PromptArgument(name="month", description="月份，格式 YYYY-MM", required=True)],
            fn=lambda month: (
                f"请对梭罗记账 {month} 的银行/微信流水与账目做安全核对。步骤：\n"
                "1. 先调用 run_close_checks(month)，再以同一 snapshotHash 调用 list_unmatched；\n"
                "2. 逐笔检查金额、日期、方向；AI 对账建议只用于展示，执行前必须用 findingId、statementId、entryIds 复核；\n"
                "3. 已有凭证的匹配使用 propose_match；需要新凭证的流水使用 propose_statement_vouchers，"
                "金额、方向、日期和摘要由服务端从原始流水填充；\n"
                "4. 只有 write:draft 令牌可以创建草稿；创建后明确说明“待确认、尚未执行”；\n"
                "5. 输出简表：可生成草稿 / 存在重复风险 / 需要人工核对；把 run_close_checks 返回的 "
                "manualClose.url 原样展示为可点击 Markdown 链接，锁账仍由用户在「月度结账」向导确认。"
            ),
        )
    )



def build_mcp_server(name: str = "solobooks") -> FastMCP:
    """构建 FastMCP 实例并注册全部开放工具（读 + 写草稿）。"""
    mcp = FastMCP(
        name,
        instructions=(
            "梭罗记账 SoloBooks：一人公司及小微企业的 AI CFO。工具按令牌绑定的公司隔离。"
            "查询类工具可任意调用；propose_* 写入工具只产生待确认草稿，"
            "需要用户在梭罗记账产品内确认后才入账。最终锁账不对 MCP 暴露；"
            "结账检查结果中的 manualClose.url 是登录后网页入口。"
        ),
    )

    def make_handler(tool: Tool):
        required = _required_scope(tool.name)

        async def _handler(**kwargs: Any) -> Any:
            identity = _current_identity()
            await _ensure_token_alive(identity)
            if not identity.has_scope(required):
                # 越权尝试是安全信号：记 warning（token_id 可对 mcp_tokens 表定位），
                # 但不写 mcp_call_logs——留痕只记真实执行，与副作用口径一致
                logger.warning(
                    "MCP 越权尝试被拒：token %s 无 %s 权限调用 %s",
                    identity.token_id,
                    required,
                    tool.name,
                )
                raise PermissionError(f"令牌缺少 {required} 权限，请在梭罗记账「AI 连接」设置中重新签发")
            started = time.monotonic()
            try:
                result = await _execute_with_identity(tool.name, kwargs, identity)
                # 单一载荷：dict 返回值经 FastMCP 会同时生成 content(JSON 文本) +
                # structuredContent 两路，客户端两份都进上下文（token ×2）。
                # 收敛为仅 content 一路紧凑 JSON；default=str 兼容 Decimal/date。
                if isinstance(result, (dict, list)):
                    result = ToolResult(
                        content=json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str),
                        structured_content=None,
                    )
            except BaseException as exc:  # noqa: BLE001 —— 记录后原样抛出
                await _log_call(identity, tool.name, kwargs, started, exc)
                raise
            await _log_call(identity, tool.name, kwargs, started, None)
            return result

        _handler.__name__ = f"mcp_{tool.name}"
        _handler.__doc__ = tool.description
        return _handler

    for tool in TOOLS:
        if tool.name not in MCP_READ_TOOLS and tool.name not in MCP_WRITE_TOOLS:
            continue
        mcp.add_tool(
            FunctionTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                fn=make_handler(tool),
            )
        )
    _build_resources(mcp)
    _build_prompts(mcp)
    return mcp


async def _execute_with_identity(tool_name: str, arguments: dict, identity: PatIdentity) -> Any:
    from agent.tools import ToolContext, execute_tool

    result = await execute_tool(
        tool_name,
        arguments,
        ToolContext(
            user_id=identity.context.user_id,
            company_id=identity.context.company_id,
            source="mcp",
        ),
    )
    # 工具层业务错误（{"error": msg}，不抛异常）转 MCP isError 语义：
    # 客户端按错误渲染，留痕记 success=False 而非伪装成功
    if isinstance(result, dict) and result.get("error"):
        raise ToolError(str(result["error"]))
    # 防御：非 JSON 序列化对象统一转字符串，避免 MCP 序列化层崩
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        return result
    return str(result)


mcp = build_mcp_server()
# path 保持默认 /mcp：挂载到 FastAPI 根路径（main.py 里 mount("/")，业务路由先匹配，
# 未命中路径才落到这里），客户端直接用 http://host/mcp 访问，避免挂载前缀 307 重定向
mcp_app = mcp.http_app(stateless_http=True, json_response=True)
app = PatAuthMiddleware(mcp_app)
