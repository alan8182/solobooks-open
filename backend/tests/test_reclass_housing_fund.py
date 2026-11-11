# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""reclass_housing_fund.py 的端到端回归测试。

覆盖 AGENTS.md A2 对数据修复脚本的要求：dry-run 只诊断不落库、apply 生成
平衡调整凭证、幂等重跑跳过、拆账后新口径计提不被二次搬移。
"""
from __future__ import annotations

import importlib.util
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "reclass_housing_fund.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("reclass_housing_fund", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sum(lines: list[dict], code: str) -> Decimal:
    return sum((Decimal(str(l["amount"])) for l in lines if l["accountCode"] == code), Decimal("0"))


async def _setup_company(company_id: str) -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine, session_scope
    from services.accounting_core_service import ensure_tenant

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")
    from services.salary_service import _ensure_db_ready

    await _ensure_db_ready()


async def _seed_new_style_sheet(company_id: str, month: str) -> None:
    """用拆账后的新代码真实计提一张工资表（公积金应已直接落 221105）。

    gross=12000 → 单位社保 3000（221102）、单位公积金 960（221105）。
    """
    from services.salary_service import accrue_salary_sheet, create_employee, get_or_create_salary_sheet

    await create_employee(company_id, "新口径员工", "技术部", 12000, 12000)
    await get_or_create_salary_sheet(company_id, month)
    await accrue_salary_sheet(company_id, "owner-1", month)


async def _seed_legacy_sheet(company_id: str, month: str, housing_fund: Decimal) -> str:
    """手工构造一张拆账前口径的计提凭证 + 工资表行（221102 含公积金、无 221105 行）。"""
    from db.session import session_scope
    from sqlalchemy import text
    from services.accounting_core_service import create_entry

    wage = Decimal("8000.00")
    company_social = Decimal("2000.00")  # 含公积金
    personal = Decimal("1480.00")
    net = wage - personal
    payload = {
        "id": f"legacy-accrual-{company_id}-{month}",
        "direction": "expense",
        "amount": wage + company_social,
        "title": f"{month}月计提员工工资及社保",
        "category": "人力成本",
        "occurDate": f"{month}-28",
        "sourceType": "salary_accrual",
        "dedupFingerprint": f"salary-accrual-legacy-{company_id}-{month}-g1",
        "entryType": "standard",
        "voucherLines": {
            "debit": [{"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": wage},
                      {"accountCode": "5602016", "accountName": "管理费用-单位社保", "amount": company_social}],
            "credit": [{"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": net},
                       {"accountCode": "221102", "accountName": "应付职工薪酬-单位社保", "amount": company_social},
                       {"accountCode": "221104", "accountName": "应付职工薪酬-代扣社保公积金", "amount": personal}],
        },
        "voucherStatus": "confirmed",
    }
    async with session_scope() as session:
        await create_entry(
            session, payload,
            company_id=company_id, user_id="owner-1",
            request_id=f"seed-legacy-{company_id}-{month}", confirm=True,
        )
        await session.execute(
            text(
                "INSERT INTO salary_sheets (id, company_id, month, status, accrual_voucher_id, "
                " hf_paid_voucher_id, company_housing_fund_total, created_at, updated_at) "
                "VALUES (:id, :cid, :month, 'accrued', :eid, NULL, :hf, '2027-01-01', '2027-01-01')"
            ),
            {
                "id": f"sheet-{company_id}-{month}",
                "cid": company_id,
                "month": month,
                "eid": payload["id"],
                "hf": str(housing_fund),
            },
        )
    return payload["id"]


@pytest.mark.asyncio
async def test_reclass_dry_run_diagnoses_without_posting():
    await _setup_company("c-reclass-dry")
    await _seed_new_style_sheet("c-reclass-dry", "2027-05")
    await _seed_legacy_sheet("c-reclass-dry", "2027-06", Decimal("1200.00"))

    module = _load_script()
    report = await module.reclass(dry_run=True, only_company="c-reclass-dry")

    # 只有旧口径 sheet 进诊断；新口径计提（221105 贷方行已存在）不被二次搬移
    assert len(report["reclassified"]) == 1
    plan = report["reclassified"][0]
    assert plan["applied"] is False
    assert Decimal(str(plan["amount"])) == Decimal("1200.00")
    assert plan["pendingByMonth"] == [{"month": "2027-06", "amount": 1200.0}]
    assert Decimal(str(plan["balancesBefore"]["221102"])) == Decimal("5000.00")  # 3000 新口径社保 + 2000 旧口径含公积金
    assert Decimal(str(plan["balancesBefore"]["221105"])) == Decimal("960.00")

    # dry-run 不落库：无调整凭证、余额未动
    from sqlalchemy import select
    from db.models import JournalEntry
    from db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(
            select(JournalEntry.id).where(
                JournalEntry.company_id == "c-reclass-dry",
                JournalEntry.id == "hf-reclass-c-reclass-dry-v1",
            )
        )).all()
        assert rows == []
        balances = await module._liability_balances(session, "c-reclass-dry")
        assert balances["221102"] == Decimal("5000.00")
        assert balances["221105"] == Decimal("960.00")


@pytest.mark.asyncio
async def test_reclass_apply_posts_balanced_entry_and_is_idempotent():
    await _setup_company("c-reclass-apply")
    await _seed_new_style_sheet("c-reclass-apply", "2027-05")
    await _seed_legacy_sheet("c-reclass-apply", "2027-06", Decimal("1200.00"))

    module = _load_script()
    report = await module.reclass(dry_run=False, only_company="c-reclass-apply")
    assert len(report["reclassified"]) == 1
    assert report["reclassified"][0]["applied"] is True

    from db.session import session_scope
    from services.entry_service import get_entry

    entry = await get_entry("hf-reclass-c-reclass-apply-v1", user_id="owner-1", company_id="c-reclass-apply")
    lines = entry["voucherLines"]
    assert _sum(lines["debit"], "221102") == Decimal("1200.00")
    assert _sum(lines["credit"], "221105") == Decimal("1200.00")
    assert sum((Decimal(str(l["amount"])) for l in lines["debit"]), Decimal("0")) == \
        sum((Decimal(str(l["amount"])) for l in lines["credit"]), Decimal("0"))

    # 重分类后：旧口径公积金归位 221105；新口径社保部分留在 221102
    async with session_scope() as session:
        balances = await module._liability_balances(session, "c-reclass-apply")
    assert balances["221102"] == Decimal("3800.00")  # 5000 − 1200
    assert balances["221105"] == Decimal("2160.00")  # 960 + 1200

    # 幂等：重复执行命中 DUPLICATE_SOURCE 跳过
    report2 = await module.reclass(dry_run=False, only_company="c-reclass-apply")
    assert report2["reclassified"] == []
    assert report2["skipped_adjusted"] == ["c-reclass-apply"]


@pytest.mark.asyncio
async def test_reclass_skips_company_without_pending_housing_fund():
    await _setup_company("c-reclass-zero")
    await _seed_new_style_sheet("c-reclass-zero", "2027-05")

    module = _load_script()
    report = await module.reclass(dry_run=False, only_company="c-reclass-zero")
    assert report["reclassified"] == []
    assert report["skipped_zero"] == [
        {"company_id": "c-reclass-zero", "balances": {"221102": 3000.0, "221105": 960.0}}
    ]
