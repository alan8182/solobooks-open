# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""跨年结转台账（广告宣传费 / 职工教育经费 / 公益性捐赠）确定性滚动测试。"""
from __future__ import annotations

from decimal import Decimal

from services.cit_adjustment_service import build_cit_adjustments
from services.cit_carryforward_service import compute_carryforward_ledger


def _income(entry_id: str, amount: str, occur: str) -> dict:
    return {
        "id": entry_id,
        "direction": "income",
        "amount": amount,
        "title": "销售收入",
        "category": "主营业务收入",
        "occurDate": occur,
        "voucherLines": {
            "debit": [{"accountCode": "1002", "amount": amount}],
            "credit": [{"accountCode": "5001", "amount": amount}],
        },
    }


def _expense(
    entry_id: str,
    amount: str,
    title: str,
    occur: str,
    *,
    billing: str = "has_invoice",
    account_code: str = "5602",
) -> dict:
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": title,
        "category": title,
        "occurDate": occur,
        "billingStatus": billing,
        "voucherLines": {
            "debit": [{"accountCode": account_code, "amount": amount}],
            "credit": [{"accountCode": "1002", "amount": amount}],
        },
    }


def _salary(entry_id: str, gross: str, occur: str) -> dict:
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": gross,
        "title": "计提工资",
        "category": "人力成本",
        "sourceType": "salary_accrual",
        "occurDate": occur,
        "billingStatus": "has_receipt",
        "voucherLines": {
            "credit": [{"accountCode": "221101", "amount": gross}],
        },
    }


class TestAdvertisingAndEducationCarryforward:
    def test_advertising_multi_year_rollforward_and_deduction(self):
        """2025 广宣超限额 50,000 → 2026 收入 1,000,000 限额 150,000 发生 120,000 → 抵减 30,000、年末结转 20,000。"""
        entries = [
            _income("inc-2025", "1000000.00", "2025-06-01"),
            _expense("adv-2025", "200000.00", "广告宣传费", "2025-06-15"),
            _income("inc-2026", "1000000.00", "2026-05-01"),
            _expense("adv-2026", "120000.00", "推广费", "2026-05-15"),
        ]
        target_profit = {"revenue": Decimal("1000000.00"), "total_profit": Decimal("500000.00")}
        ledger = compute_carryforward_ledger(entries, 2026, target_profit)
        adv = ledger["byKind"]["advertising"]
        assert adv["openingBalance"] == Decimal("50000.00")
        assert adv["currentCap"] == Decimal("150000.00")
        assert adv["currentAmount"] == Decimal("120000.00")
        assert adv["deductedFromPrior"] == Decimal("30000.00")
        assert adv["currentExcess"] == Decimal("0.00")
        assert adv["closingBalance"] == Decimal("20000.00")
        assert ledger["totalDecrease"] == Decimal("30000.00")

        year_2026_entries = [e for e in entries if e["occurDate"].startswith("2026")]
        adj = build_cit_adjustments(year_2026_entries, target_profit, carryforward_ledger=ledger)
        assert adj["totalIncrease"] == Decimal("0.00")
        assert adj["totalDecrease"] == Decimal("30000.00")
        assert adj["netAdjustment"] == Decimal("-30000.00")

    def test_education_carryforward_cit_vs_iit_rates(self):
        """职工教育经费：企税按 8%、个体户按 2.5% 滚动抵减。"""
        entries = [
            _salary("sal-2025", "100000.00", "2025-06-30"),
            # 2025 教育经费 10,000：企税限额 8,000 → 结转 2,000；个体户限额 2,500 → 结转 7,500
            _expense("edu-2025", "10000.00", "职工教育经费", "2025-07-01"),
            _salary("sal-2026", "100000.00", "2026-06-30"),
            # 2026 无新增教育经费
        ]
        cit_ledger = compute_carryforward_ledger(
            entries, 2026, {"revenue": "300000.00", "total_profit": "100000.00"}, entity_type="ltd"
        )
        cit_edu = cit_ledger["byKind"]["education"]
        assert cit_edu["openingBalance"] == Decimal("2000.00")
        assert cit_edu["deductedFromPrior"] == Decimal("2000.00")
        assert cit_edu["closingBalance"] == Decimal("0.00")

        iit_ledger = compute_carryforward_ledger(
            entries,
            2026,
            {"revenue": "300000.00", "total_profit": "100000.00"},
            entity_type="individual_business",
        )
        iit_edu = iit_ledger["byKind"]["education"]
        assert iit_edu["openingBalance"] == Decimal("7500.00")
        assert iit_edu["deductedFromPrior"] == Decimal("2500.00")
        assert iit_edu["closingBalance"] == Decimal("5000.00")
        assert "donation" not in iit_ledger["byKind"]


class TestDonationCarryforwardFifo:
    def test_donation_fifo_deduction_and_three_year_expiry(self):
        """公益捐赠 3 年 FIFO：2022 桶在 2026 年作废；2024 桶在 2025 年优先抵扣。"""
        memo = {
            "startPeriod": "2025-01",
            "carryforwardDonations": [
                {"year": 2022, "amount": "10000.00"},
                {"year": 2024, "amount": "10000.00"},
            ],
        }
        # 2025 年：利润总额 100,000（12% 限额 12,000），当年无捐赠；
        # 有效桶为 2022(10k) 与 2024(10k)，FIFO 先抵 2022 年 10,000，再抵 2024 年 2,000 → 剩 2024 年 8,000
        # 2026 年：利润总额 100,000（限额 12,000），当年捐赠 5,000；
        # 2024 年桶剩余 8,000（2026-2024=2 <= 3 有效）优先抵 8,000，剩 4,000 限额全抵当年 5,000 → 新增 2026 桶 1,000
        entries_2025 = [
            _income("inc-25", "200000.00", "2025-06-01"),
            _expense("cost-25", "100000.00", "办公费", "2025-06-02"),
            _income("inc-26", "200000.00", "2026-05-01"),
            _expense("don-26", "5000.00", "捐赠支出", "2026-05-10"),
        ]
        ledger_2026 = compute_carryforward_ledger(
            entries_2025,
            2026,
            {"revenue": Decimal("200000.00"), "total_profit": Decimal("100000.00")},
            opening_memo=memo,
        )
        don = ledger_2026["byKind"]["donation"]
        assert don["openingBalance"] == Decimal("8000.00")
        assert don["deductedFromPrior"] == Decimal("8000.00")
        assert don["currentExcess"] == Decimal("1000.00")
        assert don["closingBalance"] == Decimal("1000.00")
        assert don["buckets"] == [{"year": 2026, "amount": Decimal("1000.00")}]

        year_2026_entries = [e for e in entries_2025 if e["occurDate"].startswith("2026")]
        adj = build_cit_adjustments(
            year_2026_entries,
            {"revenue": Decimal("200000.00"), "total_profit": Decimal("100000.00")},
            carryforward_ledger=ledger_2026,
        )
        assert adj["totalIncrease"] == Decimal("1000.00")
        assert adj["totalDecrease"] == Decimal("8000.00")
        assert adj["netAdjustment"] == Decimal("-7000.00")

    def test_donation_expires_after_three_years(self):
        """2022 年结转在 2026 年超 3 年（2026 - 2022 = 4 > 3）自动作废，不再参与抵减。"""
        memo = {
            "startPeriod": "2026-01",
            "carryforwardDonations": [{"year": 2022, "amount": "15000.00"}],
        }
        ledger = compute_carryforward_ledger(
            [],
            2026,
            {"revenue": Decimal("500000.00"), "total_profit": Decimal("200000.00")},
            opening_memo=memo,
        )
        don = ledger["byKind"]["donation"]
        assert don["expiredAmount"] == Decimal("15000.00")
        assert don["openingBalance"] == Decimal("0.00")
        assert don["deductedFromPrior"] == Decimal("0.00")
        assert don["closingBalance"] == Decimal("0.00")


class TestUndocumentedExcludedFromCarryforward:
    def test_undocumented_expenses_never_enter_carryforward_pool(self):
        """无票广宣/教育/捐赠只走无票调增，绝不进入可结转结余池。"""
        entries = [
            _income("inc-1", "100000.00", "2025-06-01"),
            _expense("adv-nodoc", "50000.00", "广告宣传费", "2025-06-15", billing="no_document"),
            _expense("don-nodoc", "20000.00", "捐赠支出", "2025-06-20", billing="no_document"),
        ]
        ledger = compute_carryforward_ledger(
            entries,
            2026,
            {"revenue": Decimal("500000.00"), "total_profit": Decimal("200000.00")},
        )
        assert ledger["byKind"]["advertising"]["openingBalance"] == Decimal("0.00")
        assert ledger["byKind"]["donation"]["openingBalance"] == Decimal("0.00")
        assert ledger["hasActivity"] is False
