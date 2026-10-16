# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""签发 MCP 个人访问令牌（PAT）。

用法（本地 SQLite 测试模式；生产配 DATABASE_URL 指向 PG）：
    cd backend && DATABASE_URL="sqlite+aiosqlite:///data/app.db" \
        ./.venv313/bin/python scripts/make_mcp_token.py \
        --user <user_id> --company <company_id> [--name "Claude Desktop"] \
        [--scopes read] [--expires-days 365] [--list] [--revoke <token_id>]

--list 列出该用户的全部令牌；--revoke 吊销指定令牌。
明文 token 仅在签发时打印一次，请立即复制保存。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _list_tokens(user_id: str) -> None:
    from sqlalchemy import select

    from db.models import MCPAccessToken
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(MCPAccessToken)
                .where(MCPAccessToken.user_id == user_id)
                .order_by(MCPAccessToken.created_at.desc())
            )
        ).scalars().all()
        if not rows:
            print("（无令牌）")
            return
        for row in rows:
            status = "已吊销" if row.revoked_at else ("已过期" if row.expires_at and row.expires_at.replace(tzinfo=None) < datetime.utcnow() else "有效")
            print(f"{row.id}  {status}  {row.name}  scopes={row.scopes}  company={row.company_id}  创建于 {row.created_at}")


async def _revoke(token_id: str, user_id: str) -> None:
    from services.mcp_token_service import revoke_token

    ok = await revoke_token(token_id=token_id, user_id=user_id)
    print("已吊销" if ok else "吊销失败（不存在/非本人/已吊销）")


async def _issue(args: argparse.Namespace) -> None:
    from db.models import AuthUser
    from db.session import session_scope
    from services.mcp_token_service import issue_token, validate_token

    async with session_scope() as session:
        user = await session.get(AuthUser, args.user)
        if user is None:
            raise SystemExit(f"用户不存在: {args.user}")
        if user.current_company_id and not args.company:
            args.company = user.current_company_id
        if not args.company:
            raise SystemExit("未指定 --company 且用户无 current_company_id")

    raw, row = await issue_token(
        user_id=args.user,
        company_id=args.company,
        name=args.name,
        scopes=args.scopes,
        expires_days=args.expires_days,
    )
    print(f"令牌已签发（id={row.id}, scopes={row.scopes}, company={row.company_id}）")
    print(f"TOKEN: {raw}")
    check = await validate_token(raw)
    print("自检:", "通过" if check else "失败！")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", help="user_id（默认取 test_user_001 测试身份）", default="test_user_001")
    parser.add_argument("--company", help="company_id（缺省取用户 current_company_id）", default="")
    parser.add_argument("--name", default="default", help="令牌名称，如 Claude Desktop")
    parser.add_argument("--scopes", default="read", help="逗号分隔，M0 仅支持 read")
    parser.add_argument("--expires-days", type=int, default=365)
    parser.add_argument("--list", action="store_true", help="列出该用户的令牌")
    parser.add_argument("--revoke", default="", help="吊销指定 token_id")
    args = parser.parse_args()

    if args.list:
        asyncio.run(_list_tokens(args.user))
    elif args.revoke:
        asyncio.run(_revoke(args.revoke, args.user))
    else:
        asyncio.run(_issue(args))


if __name__ == "__main__":
    main()
