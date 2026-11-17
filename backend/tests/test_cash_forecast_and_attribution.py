# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""测试「AI 财务官」现金流滚动预测与业务归因分析引擎。

严格遵循会计不变量与 SoloBooks 规则：
- 金额两位小数语义；
- 冲销凭证与被冲销原凭证成对抵消，不影响预测与归因；
- 当月已实际发薪时不重复扣减当月预测发薪；
- 利现差额桥各项加总精准核对现金变动。
"""
from __future__ import annotations

from decimal import Decimal
import pytest

from services.accounting_core_service import create_entry, ensure_tenant, reverse_journal
from services.auxiliary_service import create_master
from services.cash_forecast_service import (
    calculate_cash_forecast,
    get_cash_forecast_config,
    save_cash_forecast_config,
)
from services.variance_attribution_service import get_business_attribution
from agent.tools import ToolContext, _get_cash_forecast, _get_business_attribution


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str) -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "test-user-1")


@pytest.mark.asyncio
async def test_cash_forecast_config_persistence():
    company_id = "test-comp-cf-config"
    await _setup_company(company_id)

    default_cfg = await get_cash_forecast_config(company_id)
    assert default_cfg["defaultArDays"] == 30
    assert default_cfg["defaultApDays"] == 30

    # 保存自定义配置
    new_cfg = {
        "defaultArDays": 45,
        "defaultApDays": 60,
        "partnerCreditDays": {"pt-client-1": 15},
        "safetyCashThreshold": "20000.00",
    }
    saved = await save_cash_forecast_config(company_id, new_cfg)
    assert saved["defaultArDays"] == 45
    assert saved["defaultApDays"] == 60
    assert saved["safetyCashThreshold"] == "20000.00"

    # 重新读取
    reloaded = await get_cash_forecast_config(company_id)
    assert reloaded["defaultArDays"] == 45
    assert reloaded["partnerCreditDays"]["pt-client-1"] == 15


@pytest.mark.asyncio
async def test_cash_forecast_calculation_and_reversal_exclusion():
    company_id = "test-comp-cf-calc"
    await _setup_company(company_id)

    pa = await create_master(
        company_id,
        "partner",
        {"code": "KH-A", "name": "甲方客户A", "type": "customer"},
        user_id="test-user-1",
    )
    vb = await create_master(
        company_id,
        "partner",
        {"code": "GYS-B", "name": "云供应商B", "type": "supplier"},
        user_id="test-user-1",
    )

    from db.session import session_scope

    async with session_scope() as session:
        # 1. 存入初始公户资金 100,000 元 (2026-10-01)
        await create_entry(
            session,
            {
                "occurDate": "2026-10-01",
                "title": "股东投资入资",
                "amount": "100000.00",
                "direction": "income",
                "category": "实收资本",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100000.00"}],
                    "credit": [{"accountCode": "4001", "accountName": "实收资本", "amount": "100000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-cf-1",
            confirm=True,
        )

        # 2. 销售开票挂应收 50,000 元 (2026-10-05)，账期 30 天，预计 2026-11-04 到账
        await create_entry(
            session,
            {
                "occurDate": "2026-10-05",
                "title": "向甲方提供软件研发服务",
                "amount": "50000.00",
                "direction": "income",
                "category": "主营业务收入",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [
                        {
                            "accountCode": "1122",
                            "accountName": "应收账款",
                            "amount": "50000.00",
                            "auxiliary": {"partner_id": pa["id"]},
                        }
                    ],
                    "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "50000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-cf-2",
            confirm=True,
        )

        # 3. 产生一笔应付账款 20,000 元 (2026-10-10)，账期 30 天，预计 2026-11-09 支付
        await create_entry(
            session,
            {
                "occurDate": "2026-10-10",
                "title": "采购云服务器及技术支持",
                "amount": "20000.00",
                "direction": "expense",
                "category": "技术服务费",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "20000.00"}],
                    "credit": [
                        {
                            "accountCode": "2202",
                            "accountName": "应付账款",
                            "amount": "20000.00",
                            "auxiliary": {"partner_id": vb["id"]},
                        }
                    ],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-cf-3",
            confirm=True,
        )

        # 4. 录入一笔错误录入并随后冲销的记录（确保冲销不计入预测）
        e_err = await create_entry(
            session,
            {
                "occurDate": "2026-10-12",
                "title": "录错的应收",
                "amount": "99999.00",
                "direction": "income",
                "category": "主营业务收入",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [
                        {
                            "accountCode": "1122",
                            "accountName": "应收账款",
                            "amount": "99999.00",
                            "auxiliary": {"partner_id": pa["id"]},
                        }
                    ],
                    "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "99999.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-cf-err",
            confirm=True,
        )
        # 红冲
        await reverse_journal(
            session,
            e_err.id,
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-cf-rev",
            reason="录错冲销",
        )

    # 执行基准现金流预测（从 2026-10-15 测算未来 60 天）
    result = await calculate_cash_forecast(
        company_id=company_id,
        as_of="2026-10-15",
        horizon_days=60,
        scenario="neutral",
    )

    # 验证核心指标
    assert result["currentCash"] == Decimal("100000.00")
    # 冲销的 99,999.00 绝不能出现在应收中
    # 预计到账 50,000，预计应付 20,000，期末余额应为 130,000.00
    assert result["endingCash"] == Decimal("130000.00")
    assert len(result["dailyPoints"]) == 60
    # 验证未跌破警戒线
    assert result["isDeficitProjected"] is False
    assert result["minBalance"] >= Decimal("100000.00")


@pytest.mark.asyncio
async def test_cash_forecast_burn_rate_and_runway():
    company_id = "test-comp-cf-burn-rate"
    await _setup_company(company_id)

    from db.session import session_scope

    async with session_scope() as session:
        # 入资 100,000
        await create_entry(
            session,
            {
                "occurDate": "2026-10-01",
                "title": "入资",
                "amount": "100000.00",
                "direction": "income",
                "category": "实收资本",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100000.00"}],
                    "credit": [{"accountCode": "4001", "accountName": "实收资本", "amount": "100000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-burn-init",
            confirm=True,
        )

        # 2026-10-05 发生管理费用 60,000 元（公户直接付现，进入近 90 天 Burn Rate 统计）
        await create_entry(
            session,
            {
                "occurDate": "2026-10-05",
                "title": "支付办公及技术运营费",
                "amount": "60000.00",
                "direction": "expense",
                "category": "管理费用",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "60000.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "60000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-burn-paid",
            confirm=True,
        )

    # 从 2026-10-10 开始预测未来 30 天
    result = await calculate_cash_forecast(
        company_id=company_id,
        as_of="2026-10-10",
        horizon_days=30,
        scenario="neutral",
    )

    # 10月10日时银行当前可用现金为 100,000 - 60,000 = 40,000 元
    assert result["currentCash"] == Decimal("40000.00")

    # 过去 90 天客观支出 60,000 元，折合月均消耗速率 (Burn Rate) = 20,000.00 元/月
    assert result["monthlyBurnRate"] == Decimal("20000.00")

    # 资金安全跑道 (Runway) = 40,000 / 20,000 = 2.0 个月
    assert result["runwayMonths"] == Decimal("2.0")

    # 验证每日消耗 (20,000 / 30 = 666.67/天)，未来 30 天期末余额准确平摊
    assert len(result["dailyPoints"]) == 30
    assert result["endingCash"] == Decimal("19999.90")


@pytest.mark.asyncio
async def test_variance_attribution_and_profit_to_cash_bridge():
    company_id = "test-comp-attribution"
    await _setup_company(company_id)

    pa = await create_master(
        company_id,
        "partner",
        {"code": "KH-OLD-A", "name": "老客户A", "type": "customer"},
        user_id="test-user-1",
    )
    pb = await create_master(
        company_id,
        "partner",
        {"code": "KH-NEW-B", "name": "新客户B", "type": "customer"},
        user_id="test-user-1",
    )

    from db.session import session_scope

    async with session_scope() as session:
        # 1. 2026-09 上月业务：收入 40,000，成本 10,000，净利 30,000，全走公户现结
        await create_entry(
            session,
            {
                "occurDate": "2026-09-10",
                "title": "老客户A九月服务费",
                "amount": "40000.00",
                "direction": "income",
                "category": "技术服务收入",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "40000.00"}],
                    "credit": [
                        {
                            "accountCode": "5001",
                            "accountName": "主营业务收入",
                            "amount": "40000.00",
                            "auxiliary": {"partner_id": pa["id"]},
                        }
                    ],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-att-sep-1",
            confirm=True,
        )

        await create_entry(
            session,
            {
                "occurDate": "2026-09-15",
                "title": "九月服务器租用",
                "amount": "10000.00",
                "direction": "expense",
                "category": "服务器租赁",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "10000.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "10000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-att-sep-2",
            confirm=True,
        )

        # 2. 2026-10 本月业务：
        # - 新客户B贡献 60,000 元（挂应收账款，未收到现钱！）
        await create_entry(
            session,
            {
                "occurDate": "2026-10-10",
                "title": "新客户B软件开发合同首期",
                "amount": "60000.00",
                "direction": "income",
                "category": "技术服务收入",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [
                        {
                            "accountCode": "1122",
                            "accountName": "应收账款",
                            "amount": "60000.00",
                            "auxiliary": {"partner_id": pb["id"]},
                        }
                    ],
                    "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "60000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-att-oct-1",
            confirm=True,
        )

        # - 发生费用 15,000 元（公户直接付现）
        await create_entry(
            session,
            {
                "occurDate": "2026-10-18",
                "title": "十月外包研发采购",
                "amount": "15000.00",
                "direction": "expense",
                "category": "技术服务费",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "15000.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "15000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-att-oct-2",
            confirm=True,
        )

        # - 计提固定资产折旧 5,000 元（非付现费用）
        await create_entry(
            session,
            {
                "occurDate": "2026-10-31",
                "title": "十月固定资产折旧计提",
                "amount": "5000.00",
                "direction": "expense",
                "category": "折旧费用",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "5000.00"}],
                    "credit": [{"accountCode": "1602", "accountName": "累计折旧", "amount": "5000.00"}],
                },
            },
            company_id=company_id,
            user_id="test-user-1",
            request_id="req-att-oct-3",
            confirm=True,
        )

    # 执行业务归因与利现桥计算
    att = await get_business_attribution(company_id=company_id, base_month="2026-10", compare_mode="mom")

    # 1. 验证归因结果
    # 本月收入 60,000，费用 20,000 (15,000外包 + 5,000折旧)，净利润 = 40,000 元
    # 上月净利 = 30,000 元，净利变动 = +10,000 元
    assert att["baseProfit"] == Decimal("40000.00")
    assert att["compareProfit"] == Decimal("30000.00")
    assert att["profitDelta"] == Decimal("10000.00")

    # 验证新客户标签识别
    income_drivers = att["topIncomeDrivers"]
    new_cust_b = next((d for d in income_drivers if d["name"] == "新客户B"), None)
    assert new_cust_b is not None
    assert new_cust_b["tag"] == "新客户"
    assert new_cust_b["deltaAmount"] == Decimal("60000.00")

    # 2. 验证利润-现金差额桥（Profit-to-Cash Bridge）
    # 本月账面净利 = 40,000.00
    # 但实际现金变动：只有一笔付款 -15,000.00，净现金流 = -15,000.00
    # 差额 = -15,000 - 40,000 = -55,000.00
    assert att["netCashFlow"] == Decimal("-15000.00")
    assert att["profitToCashDiscrepancy"] == Decimal("-55000.00")

    # 校验桥梁步骤：
    # 净利润 (40,000) + 折旧 (5,000) - 应收占用 (60,000) = 45,000 - 60,000 = -15,000 (精确吻合!)
    steps = att["bridgeSteps"]
    depr_step = next((s for s in steps if "折旧" in s["name"]), None)
    ar_step = next((s for s in steps if "应收账款" in s["name"]), None)
    cash_step = steps[-1]

    assert depr_step is not None and depr_step["amount"] == Decimal("5000.00")
    assert ar_step is not None and ar_step["amount"] == Decimal("-60000.00")
    assert cash_step["amount"] == Decimal("-15000.00")
    assert "占用" in att["plainSummary"]


@pytest.mark.asyncio
async def test_agent_tools_integration():
    company_id = "test-comp-agent-tools"
    await _setup_company(company_id)

    ctx = ToolContext(user_id="test-user-1", company_id=company_id)

    # 验证现金流预测工具
    fc_result = await _get_cash_forecast(ctx, horizon_days=30, scenario="neutral")
    assert "currentCash" in fc_result
    assert "minBalance" in fc_result
    assert "scenarioSummary" in fc_result

    # 验证业务归因工具
    att_result = await _get_business_attribution(ctx, month="2026-10", compare_mode="mom")
    assert "baseProfit" in att_result
    assert "topIncomeDrivers" in att_result
    assert "bridgeSummary" in att_result
