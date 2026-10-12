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
                "credit": [{"accountCode": "6001", "accountName": "主营业务收入", "amount": 1000}],
            },
        },
        {
            "occurDate": "2026-06-10",
            "direction": "expense",
            "category": "办公费",
            "amount": 200,
            "voucherLines": {
                "debit": [{"accountCode": "5602", "accountName": "管理费用-办公费", "amount": 200}],
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
            "voucherLines": {"debit": [{"accountCode": "1012", "amount": 1000}], "credit": [{"accountCode": "6001", "amount": 1000}]},
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
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 2000}], "credit": [{"accountCode": "4001", "amount": 2000}]},
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
            "voucherLines": {"debit": [{"accountCode": "1002", "amount": 20}], "credit": [{"accountCode": "6111", "amount": 20}]},
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
                "credit": [{"accountCode": "4002", "amount": 30000}, {"accountCode": "4001", "amount": 70000}],
            },
        },
        {
            "occurDate": "2026-06-02",
            "voucherLines": {
                "debit": [{"accountCode": "5301", "amount": 500}],
                "credit": [{"accountCode": "1002", "amount": 500}],
            },
        },
        {
            "occurDate": "2026-06-03",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "amount": 4.15}],
                "credit": [{"accountCode": "6603", "amount": 4.15}],
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

