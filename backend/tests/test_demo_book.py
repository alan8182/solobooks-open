# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""演示账套测试。

两个重点：日期必须跟着「今天」走（硬编年份会在次年撞启用日期校验），
删除必须只认 `isDemo` 标记（永远不能碰到真实账套）。
"""

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from deps.auth import AuthContext, require_auth
from main import app
from services.demo_book_service import (
    DEMO_NAME,
    DEMO_OWNER_DEPARTMENT,
    DEMO_OWNER_NAME,
    build_demo_book,
)


@pytest.fixture(autouse=True)
def _clear_auth_override():
    """每个用例后必须摘掉依赖覆盖：它会泄漏到后续测试文件，让别的用例用错身份。"""
    yield
    app.dependency_overrides.pop(require_auth, None)


def run_as(user_id: str):
    """每个用例用独立用户：演示账套的「当前账套」与 60 秒冷却都是按用户隔离的。"""
    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=user_id, company_id="c-none"
    )


def test_dates_follow_today_not_hardcoded_year():
    entries, statements, pairs, start = build_demo_book(today=date(2027, 2, 15), months=3)

    assert start == date(2026, 12, 1)
    occurred = [item["occurDate"] for item in entries]
    assert min(occurred) >= "2026-12-01"
    assert max(occurred).startswith("2027-02")
    # 启用日期必须不晚于任何一笔业务，否则 save_entry 会报 ENTRY_BEFORE_START_DATE
    assert all(item >= start.isoformat() for item in occurred)
    assert statements and pairs


def test_statements_have_running_balance_and_pairs_match():
    entries, statements, pairs, _ = build_demo_book(today=date(2026, 6, 20), months=2)

    assert len(entries) > 10
    assert len(pairs) <= len(statements)
    entry_ids = {item["id"] for item in entries}
    assert all(entry_id in entry_ids for _, _, entry_id in pairs)
    total = 0.0
    for statement in statements:
        total = round(total + statement["amount"], 2)
        assert statement["balance"] == total


def test_month_window_is_clamped():
    _, _, _, start = build_demo_book(today=date(2026, 3, 31), months=99)
    assert start == date(2025, 10, 1)  # 最多 6 个月


def test_demo_name_is_marked_so_ui_can_flag_it():
    assert DEMO_NAME.startswith("【演示】")


def test_create_reuse_then_delete_after_switching_away(client, auth_headers):
    """端到端：生成 → 幂等复用 → 切到真实账套后删除。"""
    run_as("u_demo_switch")
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["reused"] is False
    assert body["entries"] > 0
    assert body["name"] == DEMO_NAME

    again = client.post("/api/setup/demo", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert again.json()["companyId"] == body["companyId"]

    real = client.post(
        "/api/auth/companies",
        headers=auth_headers,
        json={"name": "真实账套", "startDate": "2026-01-01", "taxId": "91330100MA2REAL01X"},
    )
    assert real.status_code == 200, real.text
    real_id = real.json()["company_id"]
    switched = client.post(
        "/api/auth/switch-company", headers=auth_headers, json={"company_id": real_id}
    )
    assert switched.status_code == 200, switched.text

    deleted = client.delete(f"/api/setup/demo/{body['companyId']}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    missing = client.delete(f"/api/setup/demo/{body['companyId']}", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_demo_seeds_owner_payroll_with_hangzhou_rates(client, auth_headers):
    """演示账套自带老板工资：应发 6500、社保基数 5000、公积金个人/单位各 12%。"""
    user_id = "u_demo_owner_payroll"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    company_id = created.json()["companyId"]

    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=user_id, company_id=company_id
    )
    employees = client.get("/api/salary/employees", headers=auth_headers)
    assert employees.status_code == 200, employees.text
    owner = next(item for item in employees.json() if item["is_owner"] == 1)
    assert owner["name"] == DEMO_OWNER_NAME
    assert owner["department"] == DEMO_OWNER_DEPARTMENT
    assert Decimal(owner["base_salary"]) == Decimal("6500.00")
    assert Decimal(owner["social_security_base"]) == Decimal("5000.00")

    month = date.today().strftime("%Y-%m")
    response = client.get(f"/api/salary/sheets/{month}", headers=auth_headers)
    assert response.status_code == 200, response.text
    sheet = response.json()
    record = sheet["records"][0]
    assert record["employee_name"] == DEMO_OWNER_NAME
    assert Decimal(record["personal_social_security"]) == Decimal("1125.00")
    assert Decimal(record["company_social_security"]) == Decimal("1850.00")
    assert Decimal(record["net_salary"]) == Decimal("5375.00")
    assert Decimal(record["total_cost"]) == Decimal("8350.00")
    assert Decimal(sheet["socialSecurityDue"]) == Decimal("1775.00")
    assert Decimal(sheet["housingFundDue"]) == Decimal("1200.00")
    assert sheet["paymentStatus"] == "posted"
    assert sheet["ssPaymentStatus"] == "posted"
    assert sheet["hfPaymentStatus"] == "posted"

    statements = client.get("/api/bank/statements", headers=auth_headers)
    assert statements.status_code == 200, statements.text
    payroll_statements = [
        item for item in statements.json()["items"]
        if DEMO_OWNER_NAME in item["description"]
        and item["transaction_date"].startswith(month)
    ]
    assert len(payroll_statements) == 3
    assert all(item["matched_entry_ids"] for item in payroll_statements)


def test_demo_seeds_invoice_ledger_and_is_idempotent(client, auth_headers):
    """演示账套带进销项发票；复用账套不会重复插入。"""
    user_id = "u_demo_invoices"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    company_id = created.json()["companyId"]

    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=user_id, company_id=company_id
    )
    month = date.today().strftime("%Y-%m")

    def invoices():
        response = client.get(
            f"/api/invoices?month_from={month}&month_to={month}&page_size=100",
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    first = invoices()
    assert first["total"] >= 2
    directions = {item["direction"] for item in first["items"]}
    assert {"income", "expense"} <= directions
    assert all(item["entryId"] for item in first["items"])
    assert all(item["invoiceNumber"] for item in first["items"])
    assert any(item["sellerName"] for item in first["items"])

    again = client.post("/api/setup/demo", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["reused"] is True
    second = invoices()
    assert second["total"] == first["total"]


def test_existing_demo_without_invoices_gets_seeded_on_ledger_access(client, auth_headers):
    """老演示账套没有发票时，第一次打开发票台账会自动补齐。"""
    from sqlalchemy import delete

    from db.models import Company, Invoice
    from db.session import session_scope

    async def _reset(company_id: str):
        async with session_scope() as session:
            await session.execute(delete(Invoice).where(Invoice.company_id == company_id))
            company = await session.get(Company, company_id)
            payload = dict(company.payload or {})
            payload.pop("demo_invoices_seeded", None)
            payload.pop("invoice_backfill_done", None)
            payload["demoSeedVersion"] = 2
            company.payload = payload

    user_id = "u_demo_invoice_upgrade"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    company_id = created.json()["companyId"]
    asyncio.run(_reset(company_id))

    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=user_id, company_id=company_id
    )
    month = date.today().strftime("%Y-%m")
    response = client.get(
        f"/api/invoices?month_from={month}&month_to={month}&page_size=100",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] >= 2


def test_demo_seeds_private_expenses_and_upgrades_existing(client, auth_headers):
    """演示账套带小私账；老账套首次打开私账页时自动补齐。"""
    from db.models import Company
    from db.session import session_scope
    from services.database import get_conn

    async def _reset(company_id: str):
        conn = await get_conn()
        try:
            await conn.execute(
                "DELETE FROM private_expenses WHERE company_id = ?", (company_id,)
            )
            await conn.commit()
        finally:
            await conn.close()
        async with session_scope() as session:
            company = await session.get(Company, company_id)
            payload = dict(company.payload or {})
            payload.pop("demo_private_expenses_seeded", None)
            payload["demoSeedVersion"] = 2
            company.payload = payload

    user_id = "u_demo_private_expenses"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    company_id = created.json()["companyId"]
    asyncio.run(_reset(company_id))

    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=user_id, company_id=company_id
    )

    def list_expenses():
        response = client.get("/api/private-expenses?page_size=100", headers=auth_headers)
        assert response.status_code == 200, response.text
        return response.json()

    first = list_expenses()
    assert first["total"] >= 8
    assert {"income", "expense"} <= {item["direction"] for item in first["items"]}
    assert any(item["owner_name"] == "林晓" for item in first["items"])

    summary = client.get("/api/private-expenses/summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    totals = summary.json()
    assert totals["count"] >= 8
    assert float(totals["income_amount"]) > 0
    assert float(totals["expense_amount"]) > 0
    assert float(totals["deposit_amount"]) > 0

    again = client.post("/api/setup/demo", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert list_expenses()["total"] == first["total"]


def test_demo_book_has_bank_statements_and_no_manual_only_entries(client, auth_headers):
    """演示账套不能退化成「一堆手工账、一条流水都没有」。

    回归用例：``safe_money`` 返回 Decimal，SQLite 绑定直接抛 ProgrammingError，
    整批流水被 ``except Exception`` 静默吞掉，接口还照报成功条数——用户点进
    月结页看到的就是「本月共 0 笔流水 + 全部手工账」。
    """
    from db.session import session_scope
    from services.database import get_conn
    from services.demo_book_service import _bank_effect, _covered_entry_ids
    from services.entry_service import list_entries

    user_id = "u_demo_statements"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    body = created.json()
    company_id = body["companyId"]
    assert body["statements"] > 0

    async def snapshot():
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT count(*), "
                "sum(CASE WHEN matched_entry_id IS NULL OR matched_entry_id = '' THEN 1 ELSE 0 END) "
                "FROM bank_statements WHERE company_id = ?",
                (company_id,),
            )
            total, unmatched = await cursor.fetchone()
        finally:
            await conn.close()
        async with session_scope() as session:
            covered = await _covered_entry_ids(session, company_id)
        entries = await list_entries(user_id, company_id)
        uncovered = [
            str(entry.get("id"))
            for entry in entries
            if str(entry.get("id")) not in covered and _bank_effect(entry) != 0
        ]
        return int(total or 0), int(unmatched or 0), uncovered, entries

    total, unmatched, uncovered, entries = asyncio.run(snapshot())

    assert total > 0, "演示账套一条流水都没有"
    assert uncovered == [], f"这些公户收支没有对应流水: {uncovered}"
    # 两笔「已到账未认领」要留着，否则演示里没有需要老板动手的场景
    assert unmatched == 2

    # 来源要如实标注：走公户认领的是 bank_import，不能全打成 manual
    sources = {str(entry.get("source") or "") for entry in entries}
    assert "bank_import" in sources
    assert "invoice_import" in sources

    # 有发票的凭证必须能在账目上看到票号（月结页据此区分「发票 / 账目」）
    invoiced = [entry for entry in entries if entry.get("invoiceNumber")]
    assert invoiced, "演示账套的发票没有回写到凭证"


def test_demo_reuse_repairs_books_created_without_statements(client, auth_headers):
    """老演示账套（流水整批丢失）在再次生成时自动补齐流水与配对。"""
    from services.database import get_conn

    user_id = "u_demo_repair"
    run_as(user_id)
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    company_id = created.json()["companyId"]

    async def drop_statements():
        conn = await get_conn()
        try:
            await conn.execute(
                "DELETE FROM bank_statement_matches WHERE company_id = ?", (company_id,)
            )
            await conn.execute(
                "DELETE FROM bank_statements WHERE company_id = ?", (company_id,)
            )
            await conn.execute(
                "UPDATE auth_companies SET payload = json_remove(payload, '$.demo_statements_seeded') "
                "WHERE company_id = ?",
                (company_id,),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def count_statements():
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT count(*) FROM bank_statements WHERE company_id = ?", (company_id,)
            )
            return int((await cursor.fetchone())[0] or 0)
        finally:
            await conn.close()

    asyncio.run(drop_statements())
    assert asyncio.run(count_statements()) == 0

    again = client.post("/api/setup/demo", headers=auth_headers)
    assert again.status_code == 200, again.text
    assert again.json()["reused"] is True
    assert asyncio.run(count_statements()) > 0


def test_deleting_the_only_demo_book_creates_empty_replacement(client, auth_headers):
    """演示账套是唯一账套时允许删除，并自动补一本空白正式账套承接。"""
    run_as("u_demo_only")
    demo_id = client.post("/api/setup/demo", headers=auth_headers).json()["companyId"]

    response = client.delete(f"/api/setup/demo/{demo_id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["nextCompanyId"] and body["nextCompanyId"] != demo_id


def test_deleting_demo_switches_to_company_with_empty_payload(client, auth_headers):
    """目标账套 payload 缺少 company_id 时（历史/精简创建），删除演示账套不应 500。"""
    import asyncio

    run_as("u_demo_empty_payload")
    demo_id = client.post("/api/setup/demo", headers=auth_headers).json()["companyId"]
    real = client.post(
        "/api/auth/companies",
        headers=auth_headers,
        json={"name": "精简账套", "startDate": "2026-01-01", "taxId": "91330100MA2SLIM01X"},
    ).json()
    real_id = real["company_id"]

    from services.database import get_conn

    async def blank_payload():
        conn = await get_conn()
        try:
            await conn.execute(
                "UPDATE auth_companies SET payload = ? WHERE company_id = ?", ("{}", real_id)
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(blank_payload())

    response = client.delete(f"/api/setup/demo/{demo_id}", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    assert response.json()["nextCompanyId"] == real_id


def test_phone_signup_creates_demo_book(client, monkeypatch):
    """新手机号首次验证码登录 = 注册：只生成一本演示账套并设为当前账套。"""
    from routers import auth as auth_router

    async def _fake_check(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _fake_check)

    resp = client.post(
        "/api/auth/phone/login", json={"phone": "13977776666", "code": "123456"}
    )
    assert resp.status_code == 200, resp.text

    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
    )
    assert me.status_code == 200
    body = me.json()
    demos = [item for item in body["companies"] if item.get("isDemo")]
    assert len(demos) == 1
    assert len(body["companies"]) == 1
    assert body["current_company_id"] == demos[0]["company_id"]


def test_demo_book_cannot_be_updated(client, auth_headers):
    """演示账套不能修改主体信息（不能转成正式账套）。"""
    run_as("u_demo_readonly")
    created = client.post("/api/setup/demo", headers=auth_headers)
    assert created.status_code == 200, created.text
    demo_id = created.json()["companyId"]

    response = client.put(
        f"/api/auth/companies/{demo_id}",
        headers=auth_headers,
        json={"name": "改成正式账套"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEMO_READONLY"


def test_cannot_delete_a_non_demo_company(client, auth_headers):
    run_as("u_demo_other")
    """把真实账套 id 传给演示删除接口，必须被 isDemo 校验挡住。"""
    response = client.delete("/api/setup/demo/c1", headers=auth_headers)
    assert response.status_code == 404


def test_switch_company_tolerates_empty_payload(client, auth_headers):
    """payload 缺少 company_id 的历史账套也应能切换（签发的令牌用请求里的 id）。"""
    import asyncio

    run_as("u_switch_empty_payload")

    from db.models import AuthUser
    from db.session import session_scope

    async def seed_user():
        async with session_scope() as session:
            session.add(AuthUser(
                user_id="u_switch_empty_payload",
                created_at="2026-01-01T00:00:00+00:00",
                last_login_at="2026-01-01T00:00:00+00:00",
            ))

    asyncio.run(seed_user())

    created = client.post(
        "/api/auth/companies",
        headers=auth_headers,
        json={"name": "空 payload 账套", "startDate": "2026-01-01", "taxId": "91330100MA2SWIT01X"},
    ).json()
    company_id = created["company_id"]

    from services.database import get_conn

    async def blank_payload():
        conn = await get_conn()
        try:
            await conn.execute(
                "UPDATE auth_companies SET payload = ? WHERE company_id = ?", ("{}", company_id)
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(blank_payload())

    response = client.post(
        "/api/auth/switch-company", headers=auth_headers, json={"company_id": company_id}
    )

    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == company_id
