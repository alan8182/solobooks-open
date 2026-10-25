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
