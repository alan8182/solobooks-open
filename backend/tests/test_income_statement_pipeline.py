# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""报表中心利润表平行管道（/api/report/income-statement）的口径单元测试。

与申报报表（tax_filing_export_service）是两条取数路径，口径必须同源：
1. 期初凭证与其冲销凭证都不进利润表——期初重设后冲销凭证不得反向冲一遍；
2. 已冲销的旧代 YTD 不计数，只认「已过账」的新代；
3. YTD 过渡凭证按方向毛额读取——原账本每月结转损益时损益科目本年累计借贷
   等额（净额为 0），signed-net 会把启用前月份读成全 0；
4. 标准凭证保持 signed-net（红字退款冲减收入）。
"""
from __future__ import annotations

from routers.report import _income_statement_entry_values


def _ytd_entry(status: str = "posted") -> dict:
    return {
        "id": "opening-c1-2026-g2-ytd",
        "entryType": "opening",
        "voucherStatus": status,
        "occurDate": "2026-09-30",
        "voucherLines": {
            "debit": [
                {"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": 300},
                {"accountCode": "5001001", "accountName": "主营业务收入-咨询服务", "amount": 500},
            ],
            "credit": [
                {"accountCode": "5001001", "accountName": "主营业务收入-咨询服务", "amount": 500},
                {"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": 300},
            ],
        },
    }


def test_ytd_opening_reads_directional_gross():
    """YTD 对称毛额：收入取贷方、费用取借方，净额口径会把它们读成 0。"""
    values = _income_statement_entry_values(_ytd_entry())
    assert values["operating_revenue"] == 500
    assert values["admin_expenses"] == 300


def test_reversed_ytd_opening_not_counted():
    values = _income_statement_entry_values(_ytd_entry(status="reversed"))
    assert values["operating_revenue"] == 0
    assert values["admin_expenses"] == 0


def test_reversal_of_opening_not_counted():
    """冲销期初凭证的冲销凭证与期初同进同出。"""
    entry = {
        "id": "rev-opening",
        "entryType": "reversal",
        "voucherStatus": "posted",
        "reversalOf": "opening-c1-2026",
        "occurDate": "2026-10-01",
        "voucherLines": {
            "debit": [{"accountCode": "3001", "accountName": "实收资本", "amount": 1000}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 1000}],
        },
    }
    values = _income_statement_entry_values(entry)
    assert all(value == 0 for value in values.values())


def test_standard_entries_keep_signed_net():
    """方向毛额只作用于 YTD：标准凭证的红字退款仍按 signed-net 冲减收入。"""
    entry = {
        "id": "std-refund",
        "voucherStatus": "posted",
        "occurDate": "2026-10-11",
        "voucherLines": {
            "debit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 100}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 100}],
        },
    }
    values = _income_statement_entry_values(entry)
    assert values["operating_revenue"] == -100
