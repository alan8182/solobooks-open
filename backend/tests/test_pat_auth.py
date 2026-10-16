# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""验证 require_auth 对 PAT 令牌（sbk_...）的校验支持。"""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine

from deps.auth import require_auth
from services.mcp_token_service import issue_token
from tests.conftest import TEST_CORE_DB


@pytest.fixture(autouse=True)
async def _mcp_schema():
    from db.base import Base
    import db.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_CORE_DB.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    yield


async def _ensure_user(user_id: str):
    from db.models import AuthUser
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(AuthUser, user_id)
        if row is None:
            session.add(AuthUser(
                user_id=user_id,
                current_company_id="c1",
                created_at="2026-01-01T00:00:00Z",
                last_login_at="2026-01-01T00:00:00Z",
            ))


@pytest.mark.asyncio
async def test_require_auth_accepts_valid_pat():
    await _ensure_user("user_pat_001")
    raw_token, _ = await issue_token(
        user_id="user_pat_001",
        company_id="company_pat_001",
        name="Chrome Extension Test",
    )
    auth_header = f"Bearer {raw_token}"
    ctx = await require_auth(auth_header)
    assert ctx.user_id == "user_pat_001"
    assert ctx.company_id == "company_pat_001"


@pytest.mark.asyncio
async def test_require_auth_rejects_invalid_pat():
    auth_header = "Bearer sbk_invalid_token_1234567890abcdef"
    with pytest.raises(HTTPException) as exc_info:
        await require_auth(auth_header)
    assert exc_info.value.status_code == 401
    assert "PAT" in exc_info.value.detail
