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
        # 纯预览，无副作用（方案原归 write，实现确认只读后归 read）
        "preview_accruals",
    }
)

# 写入类：只产生待确认草稿（agent_actions），产品内人工确认。
# 注意：update_company_profile 刻意不对 MCP 开放——画像是驱动后续 AI 建议的
# 长期记忆，写入必须留在产品内（登录态 + 可见性），外部令牌直写等于给
# 「建议回路」开投毒通道（notes 还会被逐字拼进 agent system prompt）。
MCP_WRITE_TOOLS = frozenset(
    {
        "propose_match",
        "propose_accrual",
        "propose_voucher",
        "propose_vouchers",
    }
)

# 每个 PAT 每分钟工具调用上限（进程内滑动窗口；多实例部署需换集中存储）
RATE_LIMIT_PER_MINUTE = int(os.getenv("MCP_RATE_LIMIT_PER_MINUTE", "60"))
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
            "description": "某月结账五项检查结果",
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
            description="月度结账引导：跑结账检查并逐项解读，给出可执行建议",
            arguments=[PromptArgument(name="month", description="月份，格式 YYYY-MM", required=True)],
            fn=lambda month: (
                f"请作为梭罗记账的 AI 会计，引导我完成 {month} 的月度结账检查。步骤：\n"
                "1. 调用 run_close_checks(month) 获取五项检查（账证/账账/账实/账表/税表）结果；\n"
                "2. 对每一项 blocked/needs_review 的检查，用 list_unmatched(month)、list_todos 等工具定位具体问题；\n"
                "3. 给出修复建议。可以用 propose_voucher / propose_match 起草修复凭证或对账（我只会在产品内确认后才入账）；\n"
                "4. 不要声称已结账——真正的关账操作（close）需要我在产品内执行。\n"
                "最后用老板看得懂的语言汇总：本月能不能结账、还差什么。"
            ),
        )
    )

    mcp.add_prompt(
        FunctionPrompt(
            name="reconciliation_review",
            description="对账核对：逐笔核对流水与账目，起草匹配建议",
            arguments=[PromptArgument(name="month", description="月份，格式 YYYY-MM", required=True)],
            fn=lambda month: (
                f"请作为梭罗记账的对账助手，帮我核对 {month} 的银行/微信流水与账目。步骤：\n"
                "1. 调用 list_unmatched(month) 获取未匹配流水与 AI 建议；\n"
                "2. 逐笔检查：金额、日期、方向是否与账目对应；微信到账要特别留意是否与已有收入重复（重复入账会被系统拦截）；\n"
                "3. 对确认匹配的，用 propose_match 起草对账（我会产品内确认）；对无票流水建议我手工分类；\n"
                "4. 输出一张简表：已匹配 / 建议匹配 / 需要我看一眼，各多少笔。"
            ),
        )
    )


def build_mcp_server(name: str = "solobooks") -> FastMCP:
    """构建 FastMCP 实例并注册全部开放工具（读 + 写草稿）。"""
    mcp = FastMCP(
        name,
        instructions=(
            "梭罗记账 SoloBooks：一人公司及小微企业的 AI 会计。工具按令牌绑定的公司隔离。"
            "查询类工具可任意调用；propose_* 写入工具只产生待确认草稿，"
            "需要用户在梭罗记账产品内确认后才入账。"
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
            except BaseException as exc:  # noqa: BLE001 —— 记录后原样抛出
                await _log_call(identity, tool.name, kwargs, started, exc)
                raise
            await _log_call(identity, tool.name, kwargs, started, None)
            # 单一载荷：dict 返回值经 FastMCP 会同时生成 content(JSON 文本) +
            # structuredContent 两路，客户端两份都进上下文（token ×2）。
            # 收敛为仅 content 一路紧凑 JSON；字符串结果原样返回（本就单路）。
            if isinstance(result, (dict, list)):
                return ToolResult(
                    content=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    structured_content=None,
                )
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
    from agent.tools import execute_tool

    result = await execute_tool(tool_name, arguments, identity.context)
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
