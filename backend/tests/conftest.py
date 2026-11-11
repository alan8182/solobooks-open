# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Shared test fixtures and FastAPI TestClient."""
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

TEST_CORE_DB = Path(__file__).resolve().parent.parent / "data" / ".pytest_accounting.db"
if TEST_CORE_DB.exists():
    TEST_CORE_DB.unlink()
os.environ.setdefault("ACCOUNTING_TEST_MODE", "1")
# 测试用 test-token 兜底身份，与本地开发行为保持一致（生产 compose 显式关闭）
os.environ.setdefault("AUTH_DEV_FALLBACK", "1")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{TEST_CORE_DB.as_posix()}")
sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))

# Ensure backend/ is on sys.path so imports like `from main import app` work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_archive_dir(monkeypatch, tmp_path):
    """归档产物一律写进临时目录（D-013 防再犯）。

    此前测试触发 build_archive 时直接往真实 backend/data/archives 写文件，
    本地已累积数百个测试产物。服务在调用时才解析模块常量 ARCHIVE_DIR，
    monkeypatch 模块属性即可让所有测试路径生效。
    """
    from services import backup_archive_service

    target = tmp_path / "archives"
    target.mkdir()
    monkeypatch.setattr(backup_archive_service, "ARCHIVE_DIR", target)


@pytest.fixture
def client():
    """FastAPI TestClient isolated from pytest-asyncio event loops."""
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    """Headers with a test-token that bypasses JWT verification."""
    return {"Authorization": "Bearer test-token-unit-test"}


@pytest.fixture
def sample_entries():
    """A minimal set of bookkeeping entries for report tests."""
    return [
        {
            "id": "e1",
            "direction": "income",
            "amount": 10000.0,
            "category": "主营业务收入",
            "title": "服务收入",
            "date": "2026-06-01",
            "occurDate": "2026-06-01",
            "company_id": "c1",
            "payer": "company_account",
            "vatAmount": 0.0,
            "billingStatus": "has_invoice",
        },
        {
            "id": "e2",
            "direction": "expense",
            "amount": 3000.0,
            "category": "办公费",
            "title": "办公用品采购",
            "date": "2026-06-05",
            "occurDate": "2026-06-05",
            "company_id": "c1",
            "payer": "company_account",
            "vatAmount": 0.0,
            "billingStatus": "has_invoice",
            "taxDeductible": True,
        },
        {
            "id": "e3",
            "direction": "expense",
            "amount": 500.0,
            "category": "业务招待费",
            "title": "客户餐饮",
            "date": "2026-06-10",
            "occurDate": "2026-06-10",
            "company_id": "c1",
            "payer": "company_account",
            "taxDeductible": True,
        },
    ]
