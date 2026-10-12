"""MCP stdio 本地入口：单进程调试用（ZCode / Claude Code 本地配置）。

用法（本地 SQLite 测试模式；生产配 DATABASE_URL 指向 PG）：
    cd backend && DATABASE_URL="sqlite+aiosqlite:///data/app.db" \
        MCP_STDIO_TOKEN=<sbk_...> ./.venv313/bin/python scripts/mcp_stdio.py

stdio 传输没有 HTTP 中间件，启动时校验一次 MCP_STDIO_TOKEN 并把 PAT 身份
注入 contextvar，进程生命周期内所有工具调用共用该身份。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _bootstrap():
    """校验 MCP_STDIO_TOKEN，返回 PAT 身份（不要在这里 set contextvar：
    本协程所在事件循环会销毁，set 只落在该任务的上下文副本里）。"""
    from agent.mcp_server import _mcp_identity  # noqa: F401  (供文档引用)
    from services.mcp_token_service import validate_token

    raw = os.getenv("MCP_STDIO_TOKEN", "").strip()
    if not raw:
        print("缺少 MCP_STDIO_TOKEN 环境变量（用 scripts/make_mcp_token.py 签发）", file=sys.stderr)
        return None
    return await validate_token(raw)


def main() -> None:
    import asyncio

    identity = asyncio.run(_bootstrap())
    if identity is None:
        raise SystemExit(1)

    # 在线程当前上下文里 set：mcp.run() 的新事件循环根任务会复制该上下文，
    # 工具执行时 _mcp_identity 可见
    from agent.mcp_server import _mcp_identity, mcp

    _mcp_identity.set(identity)
    mcp.run()  # 默认 stdio 传输，直到客户端断开


if __name__ == "__main__":
    main()
