# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from datetime import date

from decimal import Decimal

import pytest

from services.tax_filing_export_service import (
    StatementData,
    _build_balance_rows,
    _cash_map,
    _current_balance_map,
    _opening_balance_map,
    _profit_map,
    _account_movements,
    default_completed_period,
    export_month_quarter_xls,
    parse_period,
)


def test_parse_month_period():
    period = parse_period("2026-06", "month")
    assert period.start == date(2026, 6, 1)
    assert period.end == date(2026, 6, 30)
    assert period.fiscal_year_start == date(2026, 1, 1)


def test_parse_quarter_period():
    period = parse_period("2026-Q2", "quarter")
    assert period.start == date(2026, 4, 1)
    assert period.end == date(2026, 6, 30)
    assert period.label == "2026-Q2"


def test_default_completed_tax_periods():
    assert default_completed_period("month", date(2026, 7, 11)) == "2026-06"
    assert default_completed_period("quarter", date(2026, 7, 11)) == "2026-Q2"
    assert default_completed_period("quarter", date(2026, 1, 5)) == "2025-Q4"


def test_statement_maps_basic_voucher_lines():
    entries = [
        {
            "occurDate": "2026-06-05",
            "direction": "income",
            "category": "主营业务收入",
            "amount": 1000,
            "voucherLines": {
                "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 1000}],
                "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 1000}],
            },
        },
        {
            "occurDate": "2026-06-10",
            "direction": "expense",
            "category": "办公费",
            "amount": 200,
            "voucherLines": {
                "debit": [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": 200}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 200}],
            },
        },
    ]
    opening = _opening_balance_map([
        {"account_code": "1002", "debit_balance": 300, "credit_balance": 0},
        {"account_code": "3001", "debit_balance": 0, "credit_balance": 300},
    ])
    movements = _account_movements(entries, date(2026, 1, 1), date(2026, 6, 30))
    current = _current_balance_map(opening, movements)
    profit = _profit_map(movements)
    balance_current, balance_opening = _build_balance_rows(current, opening, profit)

    assert profit["revenue"] == 1000
    assert profit["management_expense"] == 200
    assert balance_opening["cash"] == 300
    assert balance_current["cash"] == 1100
    assert balance_current["paid_in_capital"] == 300
    assert balance_current["retained_earnings"] == 800


def test_cash_map_classifies_operating_and_capital_flows():
    entries = [
        {
            "occurDate": "2026-06-01", "direction": "income", "category": "主营业务收入", "amount": 1000,
            "voucherLines": {"debit": [{"accountCode": "1012", "amount": 1000}], "credit": [{"accountCode": "5001", "amount": 1000}]},
        },
        {
            "occurDate": "2026-06-02", "direction": "expense", "category": "人力成本", "amount": 300,
            "voucherLines": {"debit": [{"accountCode": "2211", "amount": 300}], "credit": [{"accountCode": "1002", "amount": 300}]},
        },
        {
            "occurDate": "2026-06-03", "direction": "expense", "category": "设备采购", "amount": 500,
            "voucherLines": {"debit": [{"accountCode": "1601", "amount": 500}], "credit": [{"accountCode": "1002", "amount": 500}]},
        },
        {
            "occurDate": "2026-06-04", "direction": "owner_transfer", "category": "老板投入", "amount": 2000,
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 2000}], "credit": [{"accountCode": "3001", "amount": 2000}]},
        },
        {
            "occurDate": "2026-06-05", "direction": "income", "category": "微信支付结算", "amount": 1000,
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 1000}], "credit": [{"accountCode": "1012", "amount": 1000}]},
        },
    ]
    cash = _cash_map(entries, date(2026, 6, 1), date(2026, 6, 30), 100)

    assert cash["sales_cash"] == 1000
    assert cash["salary_cash"] == 300
    assert cash["fixed_asset_cash"] == 500
    assert cash["capital_cash"] == 2000
    assert cash["opening_cash"] == 100
    assert cash["operating_net"] == 700
    assert cash["investing_net"] == -500
    assert cash["financing_net"] == 2000
    assert cash["ending_cash"] == 2300


def test_cash_map_classifies_investment_returns_asset_disposals_and_financing_interest():
    entries = [
        {
            "occurDate": "2026-06-01",
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 20}], "credit": [{"accountCode": "5111", "amount": 20}]},
        },
        {
            "occurDate": "2026-06-02",
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 300}], "credit": [{"accountCode": "1601", "amount": 300}]},
        },
        {
            "occurDate": "2026-06-03",
            "voucherLines": {"debit": [{"accountCode": "2231", "amount": 10}], "credit": [{"accountCode": "1002", "amount": 10}]},
        },
    ]

    cash = _cash_map(entries, date(2026, 6, 1), date(2026, 6, 30), 0)

    assert cash["investment_income"] == 20
    assert cash["asset_disposal_cash"] == 300
    assert cash["interest_cash"] == 10
    assert cash["investing_net"] == 320
    assert cash["financing_net"] == -10
    assert cash["ending_cash"] == 310


def test_statement_maps_research_interest_capital_reserve_and_totals():
    entries = [
        {
            "occurDate": "2026-06-01",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "amount": 100000}],
                "credit": [{"accountCode": "3002", "amount": 30000}, {"accountCode": "3001", "amount": 70000}],
            },
        },
        {
            "occurDate": "2026-06-02",
            "voucherLines": {
                "debit": [{"accountCode": "4301", "amount": 500}],
                "credit": [{"accountCode": "1002", "amount": 500}],
            },
        },
        {
            "occurDate": "2026-06-03",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "amount": 4.15}],
                "credit": [{"accountCode": "5603005", "amount": 4.15}],
            },
        },
    ]
    movements = _account_movements(entries, date(2026, 1, 1), date(2026, 6, 30))
    current = _current_balance_map({}, movements)
    profit = _profit_map(movements)
    balance, _ = _build_balance_rows(current, {}, profit)

    assert profit["research_expense"] == 500
    assert profit["management_expense"] == 500
    assert profit["sales_expense"] == 0
    assert profit["finance_expense"] == Decimal("-4.15")
    assert profit["net_profit"] == Decimal("-495.85")
    assert balance["capital_reserve"] == 30000
    assert balance["retained_earnings"] == Decimal("-495.85")
    assert balance["asset_total"] == Decimal("99504.15")
    assert balance["liability_equity_total"] == Decimal("99504.15")


def test_export_fills_header_on_all_sheets(monkeypatch, tmp_path):
    import xlrd

    import services.tax_filing_export_service as service

    monkeypatch.setattr(service, "EXPORT_DIR", tmp_path)
    data = StatementData(
        company_name="测试企业",
        tax_id="91330000TEST000001",
        period=parse_period("2026-06", "month"),
    )
    output_path = export_month_quarter_xls(data)
    book = xlrd.open_workbook(str(output_path))

    start_serial = xlrd.xldate.xldate_from_date_tuple((2026, 6, 1), book.datemode)
    end_serial = xlrd.xldate.xldate_from_date_tuple((2026, 6, 30), book.datemode)
    # 资产负债表名称/止期在 H 列，利润表与现金流量表在 F 列
    for sheet_index, name_col in ((0, 7), (1, 5), (2, 5)):
        sheet = book.sheet_by_index(sheet_index)
        assert sheet.cell_value(2, 3) == "91330000TEST000001"
        assert sheet.cell_value(2, name_col) == "测试企业"
        assert sheet.cell_value(3, 3) == start_serial
        assert sheet.cell_value(3, name_col) == end_serial


def test_export_preserves_official_template_styles(monkeypatch, tmp_path):
    import xlrd
    import services.tax_filing_export_service as service

    monkeypatch.setattr(service, "EXPORT_DIR", tmp_path)
    data = StatementData(
        company_name="测试企业",
        tax_id="91330000TEST000001",
        period=parse_period("2026-06", "month"),
    )
    output_path = export_month_quarter_xls(data)
    source = xlrd.open_workbook(str(service.MONTH_QUARTER_TEMPLATE), formatting_info=True)
    output = xlrd.open_workbook(str(output_path), formatting_info=True)

    assert output.colour_map[59] == source.colour_map[59] == (224, 224, 224)

    def signature(book, sheet_index, row, col):
        sheet = book.sheet_by_index(sheet_index)
        xf = book.xf_list[sheet.cell_xf_index(row, col)]
        font = book.font_list[xf.font_index]
        return (
            xf.background.fill_pattern,
            book.colour_map.get(xf.background.pattern_colour_index),
            xf.border.left_line_style,
            xf.border.right_line_style,
            xf.border.top_line_style,
            xf.border.bottom_line_style,
            xf.alignment.hor_align,
            xf.alignment.vert_align,
            book.format_map[xf.format_key].format_str,
            font.name,
            font.height,
            font.bold,
            font.italic,
            book.colour_map.get(font.colour_index),
        )

    # Written identifiers, dates and amounts must keep the source cell style.
    for sheet_index, row, col in ((0, 2, 3), (0, 3, 3), (0, 6, 3), (1, 5, 3), (2, 6, 3)):
        assert signature(output, sheet_index, row, col) == signature(source, sheet_index, row, col)

    # Every cell keeps the official template's fill, font and alignment.
    for sheet_index in range(3):
        source_sheet = source.sheet_by_index(sheet_index)
        output_sheet = output.sheet_by_index(sheet_index)
        for row in range(source_sheet.nrows):
            for col in range(source_sheet.ncols):
                source_xf = source.xf_list[source_sheet.cell_xf_index(row, col)]
                output_xf = output.xf_list[output_sheet.cell_xf_index(row, col)]
                source_font = source.font_list[source_xf.font_index]
                output_font = output.font_list[output_xf.font_index]
                assert (
                    source_xf.background.fill_pattern,
                    source.colour_map.get(source_xf.background.pattern_colour_index),
                ) == (
                    output_xf.background.fill_pattern,
                    output.colour_map.get(output_xf.background.pattern_colour_index),
                )
                assert (
                    source_font.name, source_font.height, source_font.bold, source_font.italic,
                    source.colour_map.get(source_font.colour_index),
                    source_xf.alignment.hor_align, source_xf.alignment.vert_align,
                ) == (
                    output_font.name, output_font.height, output_font.bold, output_font.italic,
                    output.colour_map.get(output_font.colour_index),
                    output_xf.alignment.hor_align, output_xf.alignment.vert_align,
                )



def test_statement_opening_column_prefers_year_start_snapshot():
    """年中建账：报表「年初余额」列取导入时存的年初快照，不是建账期初。

    回归的是报送表年初列与原始资产负债表对不上：账套 2026-10 启用，期初是
    9 月 30 日余额，年初列却按它填，整张表连同合计一起错位。
    """
    from services.tax_filing_export_service import build_statement_data_from_entries

    opening = [
        {"account_code": "1002", "debit_balance": "12315.09", "credit_balance": "0"},
        {"account_code": "3001", "debit_balance": "0", "credit_balance": "12315.09"},
    ]
    year_start = [
        {"account_code": "1002", "debit_balance": "18377.71", "credit_balance": "0"},
        {"account_code": "3001", "debit_balance": "0", "credit_balance": "18377.71"},
    ]
    data = build_statement_data_from_entries(
        [],
        opening,
        parse_period("2026-Q4", "quarter"),
        {"name": "测试企业", "taxId": "91330000TEST000001"},
        year_start_items=year_start,
    )
    assert data.balance_opening["cash"] == Decimal("18377.71")
    # 期末列仍按账套期初算，两者互不覆盖
    assert data.balance_current["cash"] == Decimal("12315.09")
    # 现金流量表的起算现金必须是账套期初：用年初现金会与期末货币资金对不上
    assert data.cash_ytd["ending_cash"] == Decimal("12315.09")
    assert all(c["level"] != "error" for c in data.checks)


def test_statement_marks_year_start_unknown_and_warns():
    from services.tax_filing_export_service import build_statement_data_from_entries

    opening = [
        {"account_code": "1002", "debit_balance": "100", "credit_balance": "0"},
        {"account_code": "3001", "debit_balance": "0", "credit_balance": "100"},
    ]
    data = build_statement_data_from_entries(
        [],
        opening,
        parse_period("2026-Q4", "quarter"),
        {"name": "测试企业", "taxId": "91330000TEST000001"},
        year_start_known=False,
    )
    assert data.year_start_known is False
    # 年初未知时不许拿期初冒充，年初列一律 0
    assert data.balance_opening["cash"] == 0
    assert any(c["level"] == "warning" and "年初" in c["message"] for c in data.checks)


@pytest.mark.asyncio
async def test_export_refuses_when_year_start_unknown(monkeypatch):
    """年初推不出来的账套必须拒绝导出，而不是交一张年初列全是 0 的报送表。"""
    from fastapi import HTTPException

    import routers.tax_report as tax_report

    data = StatementData(
        company_name="测试企业",
        tax_id="91330000TEST000001",
        period=parse_period("2026-Q3", "quarter"),
        year_start_known=False,
    )
    data.checks = [{"level": "ok", "message": "纳税人识别号已填写"}]

    async def fake_build(*args, **kwargs):
        return data

    monkeypatch.setattr(tax_report, "build_statement_data", fake_build)

    class Ctx:
        user_id = "u1"
        company_id = "c1"

    with pytest.raises(HTTPException) as exc:
        await tax_report.export_xiaoqiye_month_quarter(period="2026-Q3", period_type="quarter", ctx=Ctx())
    assert exc.value.status_code == 400
    assert "年初余额未导入" in exc.value.detail


def _statement(**kwargs):
    from services.tax_filing_export_service import build_statement_data_from_entries

    return build_statement_data_from_entries(
        kwargs.pop("entries", []),
        kwargs.pop("opening", []),
        parse_period("2026-10", "month"),
        {"name": "测试企业", "taxId": "91330000TEST000001"},
        **kwargs,
    )


def test_balance_sheet_reclassifies_partner_debit_payables_to_prepaid():
    """应付账款按客商方向重分类：某客商是借方余额时列预付账款，不得与贷方抵销。

    回归的是资产负债表按科目净额填列：2202 净额 7,000（10,000 贷 − 3,000 借），
    预付只剩 1123 的 5,000，资产、负债同时少列 3,000，与原始报表对不上。
    """
    data = _statement(
        opening=[
            {"account_code": "1002", "debit_balance": "2000", "credit_balance": "0"},
            {"account_code": "1123", "debit_balance": "5000", "credit_balance": "0"},
            {"account_code": "2202", "debit_balance": "3000", "credit_balance": "10000"},
        ],
        opening_partner_items=[
            {"account_code": "2202", "partner_name": "甲公司", "debit_balance": "0", "credit_balance": "10000"},
            {"account_code": "2202", "partner_name": "乙公司", "debit_balance": "3000", "credit_balance": "0"},
        ],
    )
    assert data.balance_current["accounts_payable"] == Decimal("10000")
    assert data.balance_current["prepaid"] == Decimal("8000")
    assert data.balance_current["cash"] == Decimal("2000")
    assert data.balance_current["asset_total"] == Decimal("10000")
    assert data.balance_current["liability_equity_total"] == Decimal("10000")


def test_balance_sheet_reclassifies_other_payable_debit_to_other_receivable():
    """其他应付款的借方明细（垫付给个人 / 公积金）要列其他应收款。"""
    data = _statement(
        opening=[
            {"account_code": "1002", "debit_balance": "2600", "credit_balance": "0"},
            {"account_code": "2241", "debit_balance": "2400", "credit_balance": "5000"},
        ],
        opening_partner_items=[
            {"account_code": "2241", "partner_name": "李晴", "debit_balance": "1200", "credit_balance": "0"},
            {"account_code": "2241", "partner_name": "", "debit_balance": "1200", "credit_balance": "0"},
            {"account_code": "2241", "partner_name": "供应商", "debit_balance": "0", "credit_balance": "5000"},
        ],
    )
    assert data.balance_current["other_payable"] == Decimal("5000")
    assert data.balance_current["other_receivable"] == Decimal("2400")
    assert data.balance_current["asset_total"] == Decimal("5000")
    assert data.balance_current["liability_equity_total"] == Decimal("5000")


def test_balance_sheet_reclassifies_partner_credit_receivable_to_advance_receipt():
    """应收账款某客商贷方余额（预收）要列预收账款；本期发生额同样按客商判断。"""
    data = _statement(
        opening=[
            {"account_code": "1002", "debit_balance": "0", "credit_balance": "0"},
            {"account_code": "1122", "debit_balance": "1000", "credit_balance": "0"},
            {"account_code": "3001", "debit_balance": "0", "credit_balance": "1000"},
        ],
        opening_partner_items=[
            {"account_code": "1122", "partner_name": "甲客户", "debit_balance": "1000", "credit_balance": "0"},
        ],
        entries=[{
            "occurDate": "2026-10-08", "direction": "income", "category": "预收款", "amount": 400,
            "voucherLines": {
                "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 400}],
                "credit": [{
                    "accountCode": "1122", "accountName": "应收账款", "amount": 400,
                    "auxiliary": {"partner_name": "乙客户"},
                }],
            },
        }],
    )
    assert data.balance_current["accounts_receivable"] == Decimal("1000")
    assert data.balance_current["advance_receipt"] == Decimal("400")
    assert data.balance_current["cash"] == Decimal("400")


def test_balance_sheet_year_start_uses_same_partner_reclass_rule():
    """年初列与期末同口径：年初快照带客商时也按方向重分类。"""
    data = _statement(
        opening=[
            {"account_code": "1002", "debit_balance": "100", "credit_balance": "0"},
            {"account_code": "3001", "debit_balance": "0", "credit_balance": "100"},
        ],
        year_start_items=[
            {"account_code": "1002", "debit_balance": "700", "credit_balance": "0"},
            {"account_code": "2202", "partner_name": "甲公司", "debit_balance": "0", "credit_balance": "1000"},
            {"account_code": "2202", "partner_name": "乙公司", "debit_balance": "300", "credit_balance": "0"},
        ],
    )
    assert data.balance_opening["prepaid"] == Decimal("300")
    assert data.balance_opening["accounts_payable"] == Decimal("1000")
    assert data.balance_opening["cash"] == Decimal("700")
    # 期初列（建账时点）不受年初快照影响
    assert data.balance_current["prepaid"] == Decimal("0")


def test_reclass_without_partner_detail_keeps_account_net():
    """没有客商维度时不硬拆：科目净额是贷方就留在应付，不能凭空造出预付。

    派生期初（跨年无期初凭证）没有明细方向，拆错方向比不拆更糟——报表会多出
    一笔不存在的资产。等有了客商明细再按方向分流。
    """
    data = _statement(
        opening=[
            {"account_code": "1002", "debit_balance": "700", "credit_balance": "0"},
            {"account_code": "2202", "debit_balance": "300", "credit_balance": "1000"},
        ],
    )
    assert data.balance_current["prepaid"] == Decimal("0")
    assert data.balance_current["accounts_payable"] == Decimal("700")
    assert data.balance_current["asset_total"] == data.balance_current["liability_equity_total"]


def _opening_voucher(entry_id: str, occur_date: str, status: str = "posted") -> dict:
    return {
        "id": entry_id,
        "occurDate": occur_date,
        "entryType": "opening",
        "voucherStatus": status,
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 1000}],
            "credit": [{"accountCode": "3001", "accountName": "实收资本", "amount": 1000}],
        },
    }


def _reversal_of(entry: dict, reversal_id: str) -> dict:
    return {
        "id": reversal_id,
        "occurDate": entry["occurDate"],
        "entryType": "reversal",
        "voucherStatus": "posted",
        "reversalOf": entry["id"],
        "voucherLines": {
            "debit": entry["voucherLines"]["credit"],
            "credit": entry["voucherLines"]["debit"],
        },
    }


def test_reversal_of_opening_excluded_from_report_movements_and_cash():
    """期初重设（冲销重导）后报表不得被冲销凭证清零。

    根因回归：报表发生额排除期初凭证却不排除「冲销期初的冲销凭证」，导致
    「导入 → 重设 → 重导」后期初现金被「期初映射 − 冲销」抵消成 0，
    报表货币资金归零、资产负债表拆成镜像对、LEDGER_TO_REPORT 阻断
    （2026-11 生产实测 reportCash=0.00 / ledgerCash=12315.09）。
    """
    v1 = _opening_voucher("opening-c1-2026", "2026-10-01", status="reversed")
    g2 = _opening_voucher("opening-c1-2026-g2", "2026-10-01")
    reversal = _reversal_of(v1, "rev-opening")
    entries = [v1, reversal, g2]

    movements = _account_movements(entries, date(2026, 1, 1), date(2026, 12, 31), include_ytd_opening=True)
    # 期初与冲销都不进发生额：银行存款零移动，期初映射单独代表期初数
    assert movements.get("1002") is None

    opening = _opening_balance_map([
        {"account_code": "1002", "debit_balance": 1000, "credit_balance": 0},
        {"account_code": "3001", "debit_balance": 0, "credit_balance": 1000},
    ])
    current = _current_balance_map(opening, movements)
    assert _build_balance_rows(current, opening, _profit_map(movements))[0]["cash"] == Decimal("1000")

    # 现金流量表同口径：冲销期初不产生"幽灵流出"
    cash = _cash_map(entries, date(2026, 10, 1), date(2026, 10, 31), Decimal("0"))
    assert cash["other_operating_out"] == Decimal("0")


def test_reversed_ytd_opening_not_double_counted():
    """已冲销的旧代 YTD 凭证不得计入本年累计（新代 g2-ytd 只算一次）。

    旧逻辑不看状态：v1-ytd（reversed）+ 其冲销 + g2-ytd 全进发生额，净额靠
    冲销凭证侥幸抵消，毛额层面收入/费用重复计入三倍。
    """
    ytd_lines = {
        "debit": [
            {"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": 300},
        ],
        "credit": [
            {"accountCode": "5001", "accountName": "主营业务收入", "amount": 500},
            {"accountCode": "3103", "accountName": "本年利润", "amount": 200},
        ],
    }
    v1_ytd = {
        "id": "opening-c1-2026-ytd",
        "occurDate": "2026-09-30",
        "entryType": "opening",
        "voucherStatus": "reversed",
        "voucherLines": ytd_lines,
    }
    g2_ytd = dict(v1_ytd, id="opening-c1-2026-g2-ytd", voucherStatus="posted")
    reversal = _reversal_of(v1_ytd, "rev-ytd")
    entries = [v1_ytd, reversal, g2_ytd]

    movements = _account_movements(entries, date(2026, 1, 1), date(2026, 12, 31), include_ytd_opening=True)
    assert movements["5001"]["credit"] == 500, "收入毛额只应来自 g2-ytd 一张凭证"
    assert movements["5602004"]["debit"] == 300
    assert movements["3103"]["credit"] == 200
    # 旧代 YTD 的冲销凭证（reversal of opening）不再单独计入
    assert movements["5001"]["debit"] == 0


def test_ytd_income_statement_reads_directional_gross_and_balance_stays_net():
    """利润表本年累计对 YTD 按方向毛额读取；资产负债表权益仍用净额口径。

    原账本每月结转损益时损益科目本年累计借贷等额（净额为 0），净额口径会把
    启用前月份的利润表读成全 0；但权益若也用毛额口径，会与用户期初挂账的
    3103/3104 重复计入。两个口径必须并存：profit_ytd（毛额）+ profit_ytd_net（净额）。
    """
    from services.tax_filing_export_service import build_statement_data_from_entries

    entries = [
        {  # YTD 过渡凭证（月结型老账：损益科目借贷对称毛额）
            "id": "opening-c1-2026-g2-ytd",
            "occurDate": "2026-09-30",
            "entryType": "opening",
            "voucherStatus": "posted",
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
        },
        {  # 10 月标准收入凭证
            "id": "std-oct-income",
            "occurDate": "2026-10-09",
            "voucherStatus": "posted",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 200}],
                "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 200}],
            },
        },
    ]
    opening_items = [
        {"account_code": "1002", "debit_balance": "1000.00", "credit_balance": "0"},
        {"account_code": "3103", "debit_balance": "300.00", "credit_balance": "0"},
        {"account_code": "3001", "debit_balance": "0", "credit_balance": "1300.00"},
    ]
    data = build_statement_data_from_entries(
        entries,
        opening_items,
        parse_period("2026-10", "month"),
        {"name": "测试企业", "taxId": "91330000TEST000002"},
    )
    # 利润表本年累计（毛额口径）：还原原账本 1-9 月
    assert data.profit_ytd["revenue"] == Decimal("700"), "YTD 贷方毛额 500 + 标准净额 200"
    assert data.profit_ytd["management_expense"] == Decimal("300")
    assert data.profit_ytd["net_profit"] == Decimal("400")
    # 资产负债表权益（净额口径）：YTD 对称毛额净额为 0，只剩标准凭证
    assert data.profit_ytd_net["net_profit"] == Decimal("200")
    assert data.balance_current["retained_earnings"] == Decimal("-100"), "期初 3103 借 300 + 净利 200"
    assert data.balance_current["cash"] == Decimal("1200")
    assert data.balance_current["asset_total"] == data.balance_current["liability_equity_total"]
