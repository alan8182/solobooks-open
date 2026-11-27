# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""代账转入账套期初建账门禁与待办测试。"""
from __future__ import annotations

import asyncio
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.errors import AccountingError
from db.base import Base
import db.models  # noqa: F401
from db.models import AuditEvent, AuthUser, Company, CompanyMember, JournalEntry
from db.session import get_engine, session_scope
from services.accounting_core_service import create_entry, ensure_tenant, issue_step_up_token
from services.setup_readiness_service import migration_opening_status
from services.token_service import create_access_token


async def _ensure_schema() -> None:
    from services.audit_service import _ensure_schema as audit_schema
    from services.bank_service import _ensure_schema as bank_schema
    from services.salary_service import _ensure_db_ready as salary_ready
    from services.todo_service import _ensure_db_ready as todo_ready

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await audit_schema()
    await bank_schema()
    await salary_ready()
    await todo_ready()


async def _seed_company(
    *,
    onboarding_type: str = "migration",
    start_date: str = "2026-06-01",
) -> tuple[str, str, dict[str, str], str]:
    await _ensure_schema()
    suffix = uuid4().hex[:8]
    user_id = f"u-gate-{suffix}"
    company_id = f"c-gate-{suffix}"
    now = "2026-06-01T00:00:00+00:00"
    async with session_scope() as session:
        session.add(
            AuthUser(
                user_id=user_id,
                nickname="测试用户",
                current_company_id=company_id,
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Company(
                company_id=company_id,
                owner_user_id=user_id,
                payload={
                    "company_id": company_id,
                    "owner_user_id": user_id,
                    "name": f"测试转入公司-{suffix}",
                    "type": "有限责任公司",
                    "taxpayerType": "小规模纳税人",
                    "taxId": "91330100MA2TEST001",
                    "startDate": start_date,
                    "onboardingType": onboarding_type,
                    "created_at": now,
                    "updated_at": now,
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            CompanyMember(
                company_id=company_id,
                user_id=user_id,
                role="owner",
                status="active",
            )
        )
        await session.flush()
        await ensure_tenant(session, company_id, user_id)
        step_up_token = await issue_step_up_token(
            session,
            company_id=company_id,
            user_id=user_id,
            action="adjust_opening_balance",
        )
    token = create_access_token({"sub": user_id, "company_id": company_id})
    headers = {"Authorization": f"Bearer {token}"}
    return user_id, company_id, headers, step_up_token


def test_migration_company_emits_non_dismissible_opening_todo_and_clears_after_opening_posted(client):
    user_id, company_id, headers, step_up_token = asyncio.run(
        _seed_company(onboarding_type="migration", start_date="2026-01-01")
    )

    me_before = client.get("/api/auth/me", headers=headers)
    assert me_before.status_code == 200
    assert me_before.json()["opening_balance_required"] is True
    assert me_before.json()["opening_balance_reason"]

    todos_resp = client.get("/api/todos", headers=headers)
    assert todos_resp.status_code == 200
    todos = todos_resp.json()["todos"]
    todo_key = f"opening-balance-required:{company_id}"
    opening_todo = next((t for t in todos if t["todoKey"] == todo_key), None)
    assert opening_todo is not None
    assert opening_todo["category"] == "setup"
    assert opening_todo["priority"] == "high"
    assert opening_todo["meta"]["ruleId"] == "opening_balance_required"
    assert opening_todo["meta"]["nonDismissible"] is True

    # 不可被忽略或手工标记完成
    dismiss_resp = client.patch(
        f"/api/todos/{todo_key}",
        json={"dismissed": True},
        headers=headers,
    )
    assert dismiss_resp.status_code == 422
    assert dismiss_resp.json()["detail"]["code"] == "TODO_NON_DISMISSIBLE"

    done_resp = client.patch(
        f"/api/todos/{todo_key}",
        json={"done": True},
        headers=headers,
    )
    assert done_resp.status_code == 422

    # 录入期初并过账后，待办自动消失且拦截解除
    opening_resp = client.put(
        "/api/opening/balances",
        json={
            "fiscal_year": 2026,
            "start_period": "2026-01",
            "items": [
                {
                    "account_code": "1002",
                    "account_name": "银行存款",
                    "debit_balance": "10000.00",
                    "credit_balance": "0.00",
                },
                {
                    "account_code": "3001",
                    "account_name": "实收资本",
                    "debit_balance": "0.00",
                    "credit_balance": "10000.00",
                },
            ],
        },
        headers={**headers, "Step-Up-Token": step_up_token},
    )
    assert opening_resp.status_code == 200, opening_resp.text

    me_after = client.get("/api/auth/me", headers=headers)
    assert me_after.status_code == 200
    assert me_after.json()["opening_balance_required"] is False
    assert me_after.json()["opening_balance_reason"] is None

    todos_after = client.get("/api/todos", headers=headers).json()["todos"]
    assert all(t["todoKey"] != todo_key for t in todos_after)

    bank_after = client.post(
        "/api/bank/import-json",
        json={
            "rows": [
                {
                    "date": "2026-06-05",
                    "description": "客户货款",
                    "income": "1000.00",
                    "expense": "0.00",
                    "balance": "11000.00",
                }
            ]
        },
        headers=headers,
    )
    assert bank_after.status_code == 200, bank_after.text

    confirm_after = client.post(
        "/api/record/confirm",
        json={
            "direction": "expense",
            "amount": "100.00",
            "title": "办公用品",
            "category": "办公费",
            "occurDate": "2026-06-05",
            "payer": "company_account",
        },
        headers=headers,
    )
    assert confirm_after.status_code == 200, confirm_after.text


def test_migration_company_without_opening_blocks_imports_and_daily_entries(client):
    user_id, company_id, headers, _ = asyncio.run(
        _seed_company(onboarding_type="migration", start_date="2026-06-01")
    )

    def _assert_blocked(resp, label: str) -> None:
        assert resp.status_code == 409, f"{label} expected 409, got {resp.status_code}: {resp.text}"
        detail = resp.json()["detail"]
        assert detail["code"] == "OPENING_BALANCE_REQUIRED", f"{label}: {detail}"
        assert detail["context"]["deepLink"] == "/mine/opening"

    _assert_blocked(
        client.post("/api/bank/import", json={"csv_content": "日期,摘要,收入,支出,余额\n"}, headers=headers),
        "bank/import",
    )
    _assert_blocked(
        client.post("/api/bank/import-json", json={"rows": []}, headers=headers),
        "bank/import-json",
    )
    _assert_blocked(
        client.post("/api/bank/parse-columns", json={"columns": ["日期", "金额"]}, headers=headers),
        "bank/parse-columns",
    )
    _assert_blocked(
        client.post("/api/invoice/parse", json={"rows": []}, headers=headers),
        "invoice/parse",
    )
    _assert_blocked(
        client.post("/api/invoice/import", json={"rows": []}, headers=headers),
        "invoice/import",
    )
    _assert_blocked(
        client.post("/api/invoice/parse-columns", json={"columns": ["发票号码"]}, headers=headers),
        "invoice/parse-columns",
    )
    _assert_blocked(
        client.post(
            "/api/invoices/attachments",
            files=[("files", ("test.xml", b"<xml/>", "application/xml"))],
            headers=headers,
        ),
        "invoices/attachments",
    )
    _assert_blocked(
        client.post(
            "/api/wechat-bill/parse",
            files={"file": ("bill.csv", b"a,b\n1,2\n", "text/csv")},
            headers=headers,
        ),
        "wechat-bill/parse",
    )
    _assert_blocked(
        client.post("/api/wechat-bill/import", json={"rows": []}, headers=headers),
        "wechat-bill/import",
    )
    _assert_blocked(
        client.post(
            "/api/platform-bill/parse",
            files={"file": ("bill.csv", b"a,b\n1,2\n", "text/csv")},
            headers=headers,
        ),
        "platform-bill/parse",
    )
    _assert_blocked(
        client.post(
            "/api/platform-bill/import",
            json={"platform": "wechat", "rows": []},
            headers=headers,
        ),
        "platform-bill/import",
    )
    _assert_blocked(
        client.post("/api/record/text", json={"text": "今天打车 30 元"}, headers=headers),
        "record/text",
    )
    _assert_blocked(
        client.post("/api/record/batch", json={"text": "打车 30 元"}, headers=headers),
        "record/batch",
    )
    _assert_blocked(
        client.post(
            "/api/record/confirm",
            json={
                "direction": "expense",
                "amount": "100.00",
                "title": "办公用品",
                "category": "办公费",
                "occurDate": "2026-06-05",
                "payer": "company_account",
            },
            headers=headers,
        ),
        "record/confirm",
    )
    _assert_blocked(
        client.post(
            "/api/asset/assets",
            json={
                "name": "办公电脑",
                "category": "电子设备",
                "acquisitionDate": "2026-06-05",
                "availableForUseDate": "2026-06-05",
                "originalValue": "5000.00",
                "usefulLifeMonths": 36,
            },
            headers=headers,
        ),
        "asset/assets",
    )
    _assert_blocked(
        client.post(
            "/api/intangible/assets",
            json={
                "name": "商标权",
                "category": "商标权",
                "acquisitionDate": "2026-06-05",
                "availableForUseDate": "2026-06-05",
                "originalValue": "3000.00",
                "usefulLifeMonths": 120,
            },
            headers=headers,
        ),
        "intangible/assets",
    )

    # 核心写凭证路径兜底拦截（AI / MCP / 内部服务写入全部阻断）
    async def _direct_create():
        async with session_scope() as session:
            await create_entry(
                session,
                {
                    "occurDate": "2026-06-05",
                    "title": "直接记账",
                    "direction": "expense",
                    "amount": "100.00",
                    "category": "办公费",
                    "voucherLines": {
                        "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "100.00"}],
                        "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100.00"}],
                    },
                },
                company_id=company_id,
                user_id=user_id,
                request_id="req-direct-blocked",
                confirm=True,
            )

    with pytest.raises(AccountingError) as excinfo:
        asyncio.run(_direct_create())
    assert excinfo.value.code == "OPENING_BALANCE_REQUIRED"


def test_migration_company_without_opening_allows_tax_registration_opening_and_legacy_import(client):
    _, company_id, headers, _ = asyncio.run(
        _seed_company(onboarding_type="migration", start_date="2026-01-01")
    )

    # 1. 税费种认定放行
    reg_resp = client.post(
        "/api/tax-registrations",
        json={"levyItem": "增值税", "returnGroup": "vat", "filingCycle": "quarter"},
        headers=headers,
    )
    assert reg_resp.status_code == 200, reg_resp.text

    # 2. 旧账导入（余额表解析 / 历史凭证解析与导入）放行
    bal_parse = client.post(
        "/api/legacy-import/balance/parse",
        json={
            "rows": [
                {"科目代码": "1002", "科目名称": "银行存款", "借方余额": "5000", "贷方余额": "0"},
                {"科目代码": "3001", "科目名称": "实收资本", "借方余额": "0", "贷方余额": "5000"},
            ]
        },
        headers=headers,
    )
    assert bal_parse.status_code == 200, bal_parse.text

    v_parse = client.post(
        "/api/legacy-import/vouchers/parse",
        json={
            "rows": [
                {
                    "凭证号": "记-001",
                    "日期": "2026-01-15",
                    "摘要": "历史办公支出",
                    "科目代码": "5602999",
                    "借方": "100",
                    "贷方": "0",
                },
                {
                    "凭证号": "记-001",
                    "日期": "2026-01-15",
                    "摘要": "历史办公支出",
                    "科目代码": "1002",
                    "借方": "0",
                    "贷方": "100",
                },
            ]
        },
        headers=headers,
    )
    assert v_parse.status_code == 200, v_parse.text
    parsed_vouchers = v_parse.json()["valid"]
    assert parsed_vouchers

    v_confirm = client.post(
        "/api/legacy-import/vouchers/confirm",
        json={"vouchers": parsed_vouchers, "batch_id": "b-allow-1", "post": False},
        headers=headers,
    )
    assert v_confirm.status_code == 200, v_confirm.text
    assert v_confirm.json()["imported"] == 1


def test_migration_company_escape_hatch_switches_to_new_with_audit_and_unblocks(client):
    _, company_id, headers, _ = asyncio.run(
        _seed_company(onboarding_type="migration", start_date="2026-06-01")
    )
    todo_key = f"opening-balance-required:{company_id}"

    switch_resp = client.put(
        f"/api/auth/companies/{company_id}",
        json={
            "onboardingType": "new",
            "onboardingChangeReason": "zero_opening_confirmed",
        },
        headers=headers,
    )
    assert switch_resp.status_code == 200, switch_resp.text
    assert switch_resp.json()["onboardingType"] == "new"

    # 校验不可变审计事件留痕
    async def _fetch_audit():
        async with session_scope() as session:
            return (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.company_id == company_id,
                        AuditEvent.action == "change_onboarding_type",
                    )
                )
            ).scalars().all()

    events = asyncio.run(_fetch_audit())
    assert len(events) == 1
    assert events[0].before == {"onboardingType": "migration"}
    assert events[0].after == {"onboardingType": "new"}
    assert events[0].reason == "zero_opening_confirmed"

    # /me 与待办均解除
    me_after = client.get("/api/auth/me", headers=headers).json()
    assert me_after["opening_balance_required"] is False
    todos_after = client.get("/api/todos", headers=headers).json()["todos"]
    assert all(t["todoKey"] != todo_key for t in todos_after)

    # 导入与日常记账均放行
    bank_resp = client.post(
        "/api/bank/import-json",
        json={"rows": []},
        headers=headers,
    )
    assert bank_resp.status_code == 200, bank_resp.text


def test_onboarding_type_change_rejected_once_business_entries_exist(client):
    _, company_id, headers, _ = asyncio.run(
        _seed_company(onboarding_type="new", start_date="2026-06-01")
    )

    confirm_resp = client.post(
        "/api/record/confirm",
        json={
            "direction": "expense",
            "amount": "200.00",
            "title": "日常办公",
            "category": "办公费",
            "occurDate": "2026-06-05",
            "payer": "company_account",
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 200, confirm_resp.text

    # 已有业务凭证后禁止切换建账方式
    rejected = client.put(
        f"/api/auth/companies/{company_id}",
        json={"onboardingType": "migration"},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "ONBOARDING_TYPE_LOCKED"

    # 保持原建账方式改公司名不受影响
    same_type = client.put(
        f"/api/auth/companies/{company_id}",
        json={"onboardingType": "new", "name": "改名后新公司"},
        headers=headers,
    )
    assert same_type.status_code == 200, same_type.text
    assert same_type.json()["name"] == "改名后新公司"


def test_reversed_prior_year_opening_entry_not_counted_as_done():
    user_id, company_id, _, _ = asyncio.run(
        _seed_company(onboarding_type="migration", start_date="2026-01-01")
    )

    async def _check() -> dict:
        async with session_scope() as session:
            session.add(
                JournalEntry(
                    id=f"prior-rev-{company_id}",
                    company_id=company_id,
                    occurred_on=date(2025, 12, 31),
                    period_start=date(2025, 12, 1),
                    summary="上年已冲销凭证",
                    status="reversed",
                    entry_type="opening",
                    currency="CNY",
                    created_by=user_id,
                )
            )
            await session.flush()
            return await migration_opening_status(session, company_id, today=date(2026, 6, 15))

    status = asyncio.run(_check())
    assert status["required"] is True
    assert status["status"] != "done"
