# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""试点漏斗只读脚本的取数测试。

锁住三件事：

1. 漏斗时间点来自真实业务表（导入/过账/关账），不是人工填的；
2. 月结阻断原因从 period_close_runs.checks 正确归因；
3. 演示账套默认排除、渠道归因按手机号归一化匹配。
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from db.base import Base
from db.models import (
    AccountingPeriod,
    AuthUser,
    Company,
    JournalEntry,
    LlmUsage,
    PeriodCloseRun,
    SourceTransaction,
)
from scripts.pilot_funnel_report import collect_report, load_attribution

USER_ID = "u-pilot-1"
COMPANY_ID = "c-pilot-1"
DEMO_COMPANY_ID = "c-pilot-demo"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed(session) -> None:
    session.add(
        AuthUser(
            user_id=USER_ID,
            phone="+86 138 0000 0001",
            nickname="老王",
            created_at="2026-11-01T00:00:00+00:00",
            last_login_at="2026-11-10T00:00:00+00:00",
        )
    )
    session.add(
        Company(
            company_id=COMPANY_ID,
            owner_user_id=USER_ID,
            payload={
                "name": "老王工作室",
                "startDate": "2026-11-01",
                "taxId": "91330100MA2TEST01X",
                "type": "个人独资企业",
                "taxpayerType": "small_scale",
            },
            created_at="2026-11-01T00:00:00+00:00",
        )
    )
    session.add(
        SourceTransaction(
            id="st-1",
            company_id=COMPANY_ID,
            source_type="bank",
            occurred_on=date(2026, 11, 2),
            description="客户回款",
            amount=Decimal("1000.00"),
            created_by=USER_ID,
            created_at=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        JournalEntry(
            id="je-1",
            company_id=COMPANY_ID,
            occurred_on=date(2026, 11, 2),
            period_start=date(2026, 11, 1),
            summary="客户回款",
            status="posted",
            entry_type="standard",
            currency="CNY",
            created_by=USER_ID,
            posted_at=datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        AccountingPeriod(
            id="ap-1",
            company_id=COMPANY_ID,
            period_start=date(2026, 11, 1),
            status="closed",
            closed_by=USER_ID,
            closed_at=datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        PeriodCloseRun(
            id="pcr-1",
            company_id=COMPANY_ID,
            period_start=date(2026, 11, 1),
            status="blocked",
            checks=[
                {"code": "BOOK_TO_ACTUAL", "name": "账实核对", "status": "blocking"},
                {"code": "LEDGER_TO_LEDGER", "name": "账账核对", "status": "passed"},
            ],
            snapshot={},
            snapshot_hash="hash-1",
            requested_by=USER_ID,
            created_at=datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        LlmUsage(
            user_id=USER_ID,
            company_id=COMPANY_ID,
            scene="copilot",
            model="test-model",
            total_tokens=120,
            status="ok",
        )
    )
    # 演示账套：即使有活动也必须被默认排除
    session.add(
        Company(
            company_id=DEMO_COMPANY_ID,
            owner_user_id=USER_ID,
            payload={"name": "演示账套", "isDemo": True},
            created_at="2026-11-02T00:00:00+00:00",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_funnel_reads_business_tables_and_blocked_checks(session):
    await _seed(session)

    report = await collect_report(
        session,
        attribution={
            "13800000001": {"channel": "OPC-A", "cohort": "2026-11", "batch": "B1"}
        },
    )

    assert report["households"] == 1  # 演示账套被排除
    assert report["imported"] == 1
    assert report["posted"] == 1
    assert report["closed"] == 1

    row = report["rows"][0]
    assert row["company_id"] == COMPANY_ID
    assert row["channel"] == "OPC-A"
    assert row["cohort"] == "2026-11"
    assert row["batch"] == "B1"
    assert row["first_import_at"] == "2026-11-02"
    assert row["first_posted_at"] == "2026-11-03"
    assert row["first_closed_at"] == "2026-11-05"
    assert row["close_attempts"] == 1
    assert row["close_blocked"] == 1
    assert "账实核对×1" in row["blocked_codes"]
    assert row["ai_calls"] == 1
    assert row["ai_tokens"] == 120
    assert "税种" in row["readiness_pending"]
    assert int(row["readiness"].split("/")[1]) >= 4

    # 注册基线 2026-11-01：导入 +1 天、过账 +2 天、关账 +4 天
    assert report["median_days"] == {"import": 1.0, "post": 2.0, "close": 4.0}
    assert report["blocked_top"] == [
        {"name": "账实核对", "count": 1, "companies": 1}
    ]
    assert ("税种", 1) in report["readiness_pending_top"]


@pytest.mark.asyncio
async def test_since_filter_excludes_older_households(session):
    await _seed(session)

    report = await collect_report(session, since="2026-11-02")

    assert report["households"] == 0
    assert report["rows"] == []


def test_load_attribution_normalizes_phone_and_headers(tmp_path):
    path = tmp_path / "channels.csv"
    path.write_text(
        "phone,channel,cohort,batch\n+86 138-0000-0001,OPC-A,2026-11,B1\n",
        encoding="utf-8-sig",
    )

    mapping = load_attribution(path)

    assert mapping["13800000001"] == {
        "channel": "OPC-A",
        "cohort": "2026-11",
        "batch": "B1",
    }
