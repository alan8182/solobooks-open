import aiosqlite
import pytest

from services import private_expense_service as service


@pytest.mark.asyncio
async def test_summary_offsets_only_available_income_for_same_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "private-expenses.db"

    async def get_test_conn():
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        return conn

    async def ignore_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "get_conn", get_test_conn)
    monkeypatch.setattr(service, "log_action", ignore_audit)

    await service.create_expense("c1", "u1", {
        "title": "A代收款", "amount": 100, "direction": "income",
        "owner_name": "股东A", "private_account": "个人微信", "is_available": True,
    })
    await service.create_expense("c1", "u1", {
        "title": "A垫付款", "amount": 60, "direction": "expense",
        "owner_name": "股东A", "private_account": "个人微信",
    })
    await service.create_expense("c1", "u1", {
        "title": "B垫付款", "amount": 80, "direction": "expense",
        "owner_name": "股东B", "private_account": "个人支付宝",
    })
    await service.create_expense("c1", "u1", {
        "title": "平台扣缴收入", "amount": 50, "direction": "income",
        "owner_name": "平台", "private_account": "平台保证金", "is_available": False,
    })

    summary = await service.get_expense_summary("c1")
    owners = {item["owner_name"]: item for item in summary["owners"]}

    assert summary["income_amount"] == "150.00"
    assert summary["available_income_amount"] == "100.00"
    assert summary["expense_amount"] == "140.00"
    assert summary["offset_amount"] == "60.00"
    assert summary["balance_amount"] == "-40.00"
    assert owners["股东A"]["offset_amount"] == "60.00"
    assert owners["股东B"]["offset_amount"] == "0.00"
    assert owners["平台"]["available_income_amount"] == "0.00"


@pytest.mark.asyncio
async def test_owner_filter_applies_to_list_and_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "private-expenses-filter.db"

    async def get_test_conn():
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        return conn

    async def ignore_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "get_conn", get_test_conn)
    monkeypatch.setattr(service, "log_action", ignore_audit)

    for owner in ("股东A", "股东B"):
        await service.create_expense("c1", "u1", {
            "title": f"{owner}记录", "amount": 10, "direction": "income",
            "owner_name": owner, "private_account": "个人微信", "is_available": True,
        })

    listed = await service.list_expenses("c1", owner_name="股东B")
    summary = await service.get_expense_summary("c1", owner_name="股东B")

    assert listed["total"] == 1
    assert listed["items"][0]["owner_name"] == "股东B"
    assert summary["count"] == 1
    assert summary["owners"][0]["owner_name"] == "股东B"
