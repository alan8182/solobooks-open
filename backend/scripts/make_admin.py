# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""首个平台管理员引导工具（管理后台 M0）。

注册流程不提供管理员入口，首个管理员用本脚本（或等价 SQL）置位：

    cd backend
    python scripts/make_admin.py <user_id>              # 设为平台管理员
    python scripts/make_admin.py <user_id> --revoke     # 撤销平台管理员
    python scripts/make_admin.py --list                 # 查看现有管理员

连接串取自 DATABASE_URL 环境变量 / .env（与后端服务同一口径）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from db.models import AuthUser  # noqa: E402
from db.session import session_scope  # noqa: E402


async def _list_admins() -> None:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(AuthUser.user_id, AuthUser.nickname, AuthUser.is_disabled)
                    .where(AuthUser.is_platform_admin.is_(True))
                    .order_by(AuthUser.user_id)
                )
            )
            .all()
        )
    if not rows:
        print("当前没有平台管理员。用 `python scripts/make_admin.py <user_id>` 置位首个管理员。")
        return
    for user_id, nickname, disabled in rows:
        state = "已禁用" if disabled else "正常"
        print(f"{user_id}  {nickname or '-'}  [{state}]")


async def _set_admin(user_id: str, grant: bool) -> None:
    verb = "设为平台管理员" if grant else "撤销平台管理员"
    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalar_one_or_none()
        if user is None:
            print(f"错误：用户 {user_id} 不存在。可用 user_id 参考 auth_users 表。", file=sys.stderr)
            raise SystemExit(1)
        if user.is_disabled and grant:
            print("警告：该账号处于禁用状态，设为管理员后仍无法调用接口；如需启用请在 M2 用户管理中处理。")
        user.is_platform_admin = grant
    print(f"完成：{user_id} 已{verb}。重新登录无需操作，下一次请求即生效（权限每次请求查库校验）。")


def main() -> None:
    # Windows 默认 ProactorEventLoop 与 psycopg 异步连接不兼容
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description="平台管理员引导工具")
    parser.add_argument("user_id", nargs="?", help="目标用户 ID（auth_users.user_id）")
    parser.add_argument("--revoke", action="store_true", help="撤销管理员而不是授予")
    parser.add_argument("--list", action="store_true", help="列出现有平台管理员")
    args = parser.parse_args()

    if args.list:
        asyncio.run(_list_admins())
        return
    if not args.user_id:
        parser.error("需要提供 user_id，或使用 --list 查看现有管理员")
    asyncio.run(_set_admin(args.user_id, grant=not args.revoke))


if __name__ == "__main__":
    main()
