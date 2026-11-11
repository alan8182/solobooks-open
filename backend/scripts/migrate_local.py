# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""本地开发库 Alembic 迁移入口（以 solobooks_owner 身份连 PostgreSQL）。

背景：alembic 读取不到 DATABASE_URL 时会静默落到 alembic.ini 的 SQLite 副本，
本地 PG 库因此曾漂移 4 个迁移，导致运行期 ``_ensure_schema`` 全局 500。
本脚本强制使用 backend/.env 里的 ``MIGRATION_DATABASE_URL``（solobooks_owner
专用连接串，权限模型见 docs/finance-core-runbook.md），拒绝任何静默回退。

用法（backend venv 下，参数原样透传给 alembic CLI）::

    python scripts/migrate_local.py                 # 等价于 upgrade head
    python scripts/migrate_local.py current         # 查看当前版本
    python scripts/migrate_local.py downgrade -1    # 本地调试回退一步
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _load_env_var(name: str) -> str:
    """先读进程环境，再读 backend/.env（key=value 逐行）。"""
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    migration_url = _load_env_var("MIGRATION_DATABASE_URL")
    if not migration_url:
        sys.exit(
            "缺少 MIGRATION_DATABASE_URL（应配置在 backend/.env，solobooks_owner 专用连接串）。\n"
            "拒绝执行：缺省时 alembic 会静默连到 alembic.ini 的 SQLite 副本，"
            "本地 PostgreSQL 将继续漂移。"
        )
    if migration_url.startswith("sqlite"):
        sys.exit("MIGRATION_DATABASE_URL 指向 SQLite；本地迁移必须指向本地 PostgreSQL。")

    env = {**os.environ, "DATABASE_URL": migration_url}
    args = sys.argv[1:] or ["upgrade", "head"]
    print(f"[migrate_local] alembic {' '.join(args)}（连接串已从 MIGRATION_DATABASE_URL 注入，不回显）", flush=True)
    completed = subprocess.call(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
    )
    return completed


if __name__ == "__main__":
    sys.exit(main())
