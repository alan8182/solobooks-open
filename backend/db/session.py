# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


DEFAULT_DATABASE_URL = "postgresql+asyncpg://solobooks:solobooks@postgres:5432/solobooks"


def database_url() -> str:
    raw = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL).strip()
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://") and "+" not in raw.split("://", 1)[0]:
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    # 应用引擎统一用 asyncpg：psycopg3 异步连接在 Windows 默认事件循环（Proactor）上不可用，
    # 避免环境变量误带 psycopg:// 时（如迁移脚本导出后启动服务）启动即崩。
    if raw.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + raw[len("postgresql+psycopg://"):]
    return raw


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    url = database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite+"):
        kwargs.pop("pool_pre_ping", None)
        kwargs["connect_args"] = {"timeout": 30}
    return create_async_engine(url, **kwargs)


_sync_engine_instance = None


def get_sync_engine():
    """线程池/同步上下文用的独立同步引擎（psycopg/sqlite），避免污染全局异步连接池。

    同步 URL 由异步 URL 重写而来（asyncpg→psycopg、aiosqlite→sqlite）；
    配额计数、用量埋点等 fire-and-forget 写路径共用此引擎。
    """
    global _sync_engine_instance
    if _sync_engine_instance is None:
        from sqlalchemy import create_engine

        url = database_url()
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql+psycopg://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("sqlite+aiosqlite://"):
            # 前缀等长替换：sqlite+aiosqlite:// → sqlite://（多拼一个斜杠会把
            # 空主机名解析成空端口，int('') 崩；此 bug 曾被埋点静默吞错掩盖）
            url = "sqlite" + url[len("sqlite+aiosqlite"):]
        _sync_engine_instance = create_engine(url, pool_pre_ping=True)
    return _sync_engine_instance


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    session = get_session_factory()()
    try:
        async with session.begin():
            yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session
