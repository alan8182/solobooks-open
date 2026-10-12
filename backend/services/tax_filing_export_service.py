"""Build and export official tax filing financial statement workbooks."""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Literal
from zoneinfo import ZoneInfo

from services.account_mapper import get_voucher_hint
from services.account_service import get_user_profile
from services.opening_balance_service import get_opening_balances
from core.money import ZERO, money
from db.session import session_scope
from services.accounting_core_service import ensure_tenant, posted_entries_for_reports

PeriodType = Literal["month", "quarter"]

# 锚定 backend/ 目录：本地为 <仓库>/backend，容器内 COPY backend/ . 后为 /app，两种布局一致
BACKEND_DIR = Path(__file__).resolve().parents[1]
MONTH_QUARTER_TEMPLATE = BACKEND_DIR / "assets" / "tax" / "财务报表报送与信息采集（小企业会计准则）月季报.xls"
EXPORT_DIR = BACKEND_DIR / "data" / "tax_exports"

CREDIT_NATURE_CODES = {"1602", "6001", "6051", "6111", "6301"}
CASH_ACCOUNT_CODES = {"1001", "1002", "1012"}


@dataclass
class PeriodRange:
    start: date
    end: date
    fiscal_year_start: date
    label: str


@dataclass
class StatementData:
    company_name: str = ""
    tax_id: str = ""
    period: PeriodRange | None = None
    balance_current: Dict[str, Decimal] = field(default_factory=dict)
    balance_opening: Dict[str, Decimal] = field(default_factory=dict)
    balance_period_opening: Dict[str, Decimal] = field(default_factory=dict)
    profit_current: Dict[str, Decimal] = field(default_factory=dict)
    profit_ytd: Dict[str, Decimal] = field(default_factory=dict)
    cash_current: Dict[str, Decimal] = field(default_factory=dict)
    cash_ytd: Dict[str, Decimal] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)


def default_completed_period(period_type: PeriodType, today: date | None = None) -> str:
    """Return the most recently completed monthly or quarterly filing period."""
    current = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if period_type == "month":
        previous_month_end = current.replace(day=1) - timedelta(days=1)
        return previous_month_end.strftime("%Y-%m")

    current_quarter = (current.month - 1) // 3 + 1
    if current_quarter == 1:
        return f"{current.year - 1}-Q4"
    return f"{current.year}-Q{current_quarter - 1}"


def parse_period(period: str, period_type: PeriodType) -> PeriodRange:
    if period_type == "month":
        try:
            year = int(period[:4])
            month = int(period[5:7])
        except (TypeError, ValueError) as e:
            raise ValueError("月度期间格式应为 YYYY-MM") from e
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return PeriodRange(start, end, date(year, 1, 1), period)

    normalized = (period or "").upper().replace("季度", "Q").replace("第", "").replace(" ", "")
    if "-Q" in normalized:
        year_text, quarter_text = normalized.split("-Q", 1)
    elif "Q" in normalized:
        year_text, quarter_text = normalized.split("Q", 1)
    else:
        raise ValueError("季度期间格式应为 YYYY-Q1、YYYY-Q2、YYYY-Q3 或 YYYY-Q4")
    try:
        year = int(year_text)
        quarter = int(quarter_text)
    except (TypeError, ValueError) as e:
        raise ValueError("季度期间格式应为 YYYY-Q1、YYYY-Q2、YYYY-Q3 或 YYYY-Q4") from e
    if quarter not in (1, 2, 3, 4):
        raise ValueError("季度必须为 Q1、Q2、Q3 或 Q4")
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return PeriodRange(
        date(year, start_month, 1),
        date(year, end_month, calendar.monthrange(year, end_month)[1]),
        date(year, 1, 1),
        f"{year}-Q{quarter}",
    )


def _d2(value: Any) -> Decimal:
    return money(value or ZERO)


def _parse_entry_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _sum_codes(balances: Dict[str, Decimal], codes: Iterable[str]) -> Decimal:
    return _d2(sum((balances.get(code, ZERO) for code in codes), ZERO))


def _sum_prefix(balances: Dict[str, Decimal], *prefixes: str) -> Decimal:
    return _d2(sum((v for code, v in balances.items() if code.startswith(prefixes)), ZERO))


def _normal_balance(code: str, debit: Decimal, credit: Decimal) -> Decimal:
    if code in CREDIT_NATURE_CODES or code.startswith(("2", "3", "4")):
        return credit - debit
    return debit - credit


def _opening_balance_map(items: list[dict[str, Any]]) -> Dict[str, Decimal]:
    out: Dict[str, Decimal] = {}
    for item in items:
        code = str(item.get("account_code") or "")
        if code:
            out[code] = _d2(_normal_balance(
                code,
                money(item.get("debit_balance") or ZERO),
                money(item.get("credit_balance") or ZERO),
            ))
    return out


def _voucher_for_entry(entry: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    voucher = entry.get("voucherLines")
    if isinstance(voucher, str):
        try:
            voucher = json.loads(voucher)
        except json.JSONDecodeError:
            voucher = None
    if isinstance(voucher, dict) and ("debit" in voucher or "credit" in voucher):
        return voucher
    return get_voucher_hint(
        entry.get("direction", "expense"),
        entry.get("category", "其他支出"),
        money(entry.get("amount") or ZERO),
        entry.get("payer", "company_account"),
    )


def _account_movements(entries: list[dict[str, Any]], start: date, end: date) -> Dict[str, dict[str, Any]]:
    totals: Dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("entryType") == "opening":
            continue
        occur_date = _parse_entry_date(entry.get("occurDate") or entry.get("date") or entry.get("time"))
        if not occur_date or occur_date < start or occur_date > end:
            continue
        voucher = _voucher_for_entry(entry)
        for side, field in (("debit", "debit"), ("credit", "credit")):
            for line in voucher.get(side, []) or []:
                code = str(line.get("accountCode") or "")
                if not code:
                    continue
                bucket = totals.setdefault(code, {"name": line.get("accountName", ""), "debit": ZERO, "credit": ZERO})
                bucket[field] += money(line.get("amount") or ZERO)
    return totals


def _current_balance_map(opening: Dict[str, Decimal], movements: Dict[str, dict[str, Any]]) -> Dict[str, Decimal]:
    out = dict(opening)
    for code, info in movements.items():
        out[code] = _d2(out.get(code, ZERO) + _normal_balance(code, info["debit"], info["credit"]))
    return out


def _profit_map(movements: Dict[str, dict[str, Any]]) -> Dict[str, Decimal]:
    balances = {code: _normal_balance(code, info["debit"], info["credit"]) for code, info in movements.items()}
    research = _sum_codes(balances, ["5301"])
    management = _sum_prefix(balances, "56") - _sum_codes(balances, ["5608"]) + research
    finance = _sum_codes(balances, ["5608", "6602", "6603"])
    out = {
        "revenue": _sum_codes(balances, ["6001", "6051"]),
        "cost": _sum_codes(balances, ["5001", "5101"]),
        "tax_surcharge": _sum_codes(balances, ["6401"]),
        "sales_expense": _sum_prefix(balances, "66") - _sum_codes(balances, ["6602", "6603"]),
        "advertising": _sum_codes(balances, ["6601"]),
        "management_expense": _d2(management),
        "entertainment": _sum_codes(balances, ["5603"]),
        "research_expense": research,
        "finance_expense": finance,
        "investment_income": _sum_codes(balances, ["6111"]),
        "non_operating_income": _sum_codes(balances, ["6301"]),
        "non_operating_expense": _sum_codes(balances, ["6711"]),
        "income_tax": _sum_codes(balances, ["6801"]),
    }
    out["operating_profit"] = _d2(
        out["revenue"] - out["cost"] - out["tax_surcharge"]
        - out["sales_expense"] - out["management_expense"]
        - out["finance_expense"] + out["investment_income"]
    )
    out["total_profit"] = _d2(
        out["operating_profit"] + out["non_operating_income"] - out["non_operating_expense"]
    )
    out["net_profit"] = _d2(out["total_profit"] - out["income_tax"])
    return out


def _cash_map(entries: list[dict[str, Any]], start: date, end: date, opening_cash: Decimal) -> Dict[str, Decimal]:
    out = {
        "sales_cash": ZERO,
        "other_operating_in": ZERO,
        "purchase_cash": ZERO,
        "salary_cash": ZERO,
        "tax_cash": ZERO,
        "other_operating_out": ZERO,
        "investment_in": ZERO,
        "investment_income": ZERO,
        "asset_disposal_cash": ZERO,
        "investment_out": ZERO,
        "fixed_asset_cash": ZERO,
        "borrow_cash": ZERO,
        "capital_cash": ZERO,
        "repay_borrow_cash": ZERO,
        "interest_cash": ZERO,
        "profit_distribution_cash": ZERO,
        "opening_cash": opening_cash,
    }
    purchase_categories = {"原材料采购", "商品采购", "生产成本"}
    fixed_asset_categories = {"固定资产", "设备采购"}
    salary_categories = {"人力成本", "工资", "薪酬", "社保", "公积金"}
    for entry in entries:
        if entry.get("entryType") == "opening":
            continue
        occur_date = _parse_entry_date(entry.get("occurDate") or entry.get("date") or entry.get("time"))
        if not occur_date or occur_date < start or occur_date > end:
            continue
        voucher = _voucher_for_entry(entry)
        cash_debit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("debit", [])
             if str(line.get("accountCode") or "") in CASH_ACCOUNT_CODES),
            ZERO,
        )
        cash_credit = sum(
            (money(line.get("amount") or ZERO) for line in voucher.get("credit", [])
             if str(line.get("accountCode") or "") in CASH_ACCOUNT_CODES),
            ZERO,
        )
        net_cash = _d2(cash_debit - cash_credit)
        if net_cash == ZERO:
            continue
        amount = abs(net_cash)
        counterpart_codes = {
            str(line.get("accountCode") or "")
            for side in ("debit", "credit")
            for line in voucher.get(side, [])
            if str(line.get("accountCode") or "") not in CASH_ACCOUNT_CODES
        }
        category = str(entry.get("category") or "")
        if counterpart_codes & {"6001", "6051"}:
            out["sales_cash"] += net_cash
            continue
        if counterpart_codes & {"6301", "6603"}:
            if net_cash > ZERO:
                out["other_operating_in"] += amount
            else:
                out["other_operating_out"] += amount
            continue
        if net_cash > ZERO:
            if counterpart_codes & {"3001", "3002", "4001", "4002"}:
                out["capital_cash"] += amount
            elif counterpart_codes & {"2001", "2501"}:
                out["borrow_cash"] += amount
            elif counterpart_codes & {"6111"}:
                out["investment_income"] += amount
            elif counterpart_codes & {"1601", "1604", "1701"}:
                out["asset_disposal_cash"] += amount
            elif counterpart_codes & {"1101", "1501", "1511"}:
                out["investment_in"] += amount
            elif any(code.startswith(("5", "6")) for code in counterpart_codes):
                # Cash refunds of previously recognised expenses reduce the
                # corresponding operating cash outflow.
                out["other_operating_out"] -= amount
            else:
                out["other_operating_in"] += amount
        else:
            if counterpart_codes & {"1601", "1604", "1701"} or category in fixed_asset_categories:
                out["fixed_asset_cash"] += amount
            elif counterpart_codes & {"1101", "1501", "1511"}:
                out["investment_out"] += amount
            elif counterpart_codes & {"2211"} or category in salary_categories:
                out["salary_cash"] += amount
            elif counterpart_codes & {"1403", "1405", "5001", "5101"} or category in purchase_categories:
                out["purchase_cash"] += amount
            elif any(code.startswith("2221") for code in counterpart_codes) or category == "税费缴纳":
                out["tax_cash"] += amount
            elif counterpart_codes & {"2001", "2501"}:
                out["repay_borrow_cash"] += amount
            elif counterpart_codes & {"2231"}:
                out["interest_cash"] += amount
            elif counterpart_codes & {"2232"} or category in {"老板取现", "利润分配"}:
                out["profit_distribution_cash"] += amount
            else:
                out["other_operating_out"] += amount
    out["operating_net"] = (
        out["sales_cash"] + out["other_operating_in"] - out["purchase_cash"]
        - out["salary_cash"] - out["tax_cash"] - out["other_operating_out"]
    )
    out["investing_net"] = (
        out["investment_in"] + out["investment_income"] + out["asset_disposal_cash"]
        - out["investment_out"] - out["fixed_asset_cash"]
    )
    out["financing_net"] = (
        out["borrow_cash"] + out["capital_cash"] - out["repay_borrow_cash"]
        - out["interest_cash"] - out["profit_distribution_cash"]
    )
    out["net_increase"] = out["operating_net"] + out["investing_net"] + out["financing_net"]
    out["ending_cash"] = opening_cash + out["net_increase"]
    return {key: _d2(value) for key, value in out.items()}


def _net_profit(profit: Dict[str, Decimal]) -> Decimal:
    return _d2(profit.get("net_profit", ZERO))


def _build_balance_rows(current: Dict[str, Decimal], opening: Dict[str, Decimal], profit_ytd: Dict[str, Decimal]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    def rows(src: Dict[str, Decimal], include_profit: bool) -> dict[str, Decimal]:
        retained = _sum_codes(src, ["3101", "3103", "3104"])
        if include_profit:
            retained = _d2(retained + _net_profit(profit_ytd))
        out = {
            "cash": _sum_codes(src, CASH_ACCOUNT_CODES),
            "short_investment": _sum_codes(src, ["1101"]),
            "notes_receivable": _sum_codes(src, ["1121"]),
            "accounts_receivable": _sum_codes(src, ["1122"]),
            "prepaid": _sum_codes(src, ["1123"]),
            "dividends_receivable": _sum_codes(src, ["1131"]),
            "interest_receivable": _sum_codes(src, ["1132"]),
            "other_receivable": _sum_codes(src, ["1221"]),
            "inventory": _sum_codes(src, ["1403", "1405"]),
            "raw_material": _sum_codes(src, ["1403"]),
            "goods": _sum_codes(src, ["1405"]),
            "other_current_asset": _sum_codes(src, ["1901"]),
            "long_bond_investment": _sum_codes(src, ["1501"]),
            "long_equity_investment": _sum_codes(src, ["1511"]),
            "fixed_asset_cost": _sum_codes(src, ["1601"]),
            "accumulated_depreciation": _sum_codes(src, ["1602"]),
            "construction": _sum_codes(src, ["1604"]),
            "intangible_asset": _sum_codes(src, ["1701"]),
            "long_prepaid": _sum_codes(src, ["1801"]),
            "short_borrow": _sum_codes(src, ["2001"]),
            "notes_payable": _sum_codes(src, ["2201"]),
            "accounts_payable": _sum_codes(src, ["2202"]),
            "advance_receipt": _sum_codes(src, ["2203"]),
            "salary_payable": _sum_codes(src, ["2211"]),
            "tax_payable": _sum_prefix(src, "2221"),
            "interest_payable": _sum_codes(src, ["2231"]),
            "profit_payable": _sum_codes(src, ["2232"]),
            "other_payable": _sum_codes(src, ["2241"]),
            "long_borrow": _sum_codes(src, ["2501"]),
            "long_payable": _sum_codes(src, ["2701"]),
            "deferred_income": _sum_codes(src, ["2401"]),
            "paid_in_capital": _sum_codes(src, ["3001", "4001"]),
            "capital_reserve": _sum_codes(src, ["3002", "4002"]),
            "surplus_reserve": _sum_codes(src, ["3102"]),
            "retained_earnings": retained,
        }
        out["fixed_asset_book_value"] = _d2(out["fixed_asset_cost"] - out["accumulated_depreciation"])
        out["current_asset_total"] = _d2(sum((out[key] for key in (
            "cash", "short_investment", "notes_receivable", "accounts_receivable", "prepaid",
            "dividends_receivable", "interest_receivable", "other_receivable", "inventory",
            "other_current_asset",
        )), ZERO))
        out["non_current_asset_total"] = _d2(sum((out[key] for key in (
            "long_bond_investment", "long_equity_investment", "fixed_asset_book_value",
            "construction", "intangible_asset", "long_prepaid",
        )), ZERO))
        out["asset_total"] = _d2(out["current_asset_total"] + out["non_current_asset_total"])
        out["current_liability_total"] = _d2(sum((out[key] for key in (
            "short_borrow", "notes_payable", "accounts_payable", "advance_receipt",
            "salary_payable", "tax_payable", "interest_payable", "profit_payable", "other_payable",
        )), ZERO))
        out["non_current_liability_total"] = _d2(sum((out[key] for key in (
            "long_borrow", "long_payable", "deferred_income",
        )), ZERO))
        out["liability_total"] = _d2(out["current_liability_total"] + out["non_current_liability_total"])
        out["equity_total"] = _d2(sum((out[key] for key in (
            "paid_in_capital", "capital_reserve", "surplus_reserve", "retained_earnings",
        )), ZERO))
        out["liability_equity_total"] = _d2(out["liability_total"] + out["equity_total"])
        return out
    return rows(current, True), rows(opening, False)


async def build_statement_data(user_id: str, company_id: str, period: str, period_type: PeriodType) -> StatementData:
    period_range = parse_period(period, period_type)
    profile = await get_user_profile(user_id)
    company = (profile or {}).get("current_company") or {}
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        entries = await posted_entries_for_reports(session, company_id)
        opening_items = await get_opening_balances(
            company_id,
            period_range.start.year,
            session=session,
        )
    return build_statement_data_from_entries(entries, opening_items, period_range, company)


def build_statement_data_from_entries(entries: list[dict], opening_items: list[dict], period_range: PeriodRange, company: dict) -> StatementData:
    """Use the same report mapping for exports and transactional close checks."""
    opening = _opening_balance_map(opening_items)
    movements_current = _account_movements(entries, period_range.start, period_range.end)
    movements_ytd = _account_movements(entries, period_range.fiscal_year_start, period_range.end)
    movements_before_period = _account_movements(
        entries,
        period_range.fiscal_year_start,
        period_range.start - timedelta(days=1),
    )
    current = _current_balance_map(opening, movements_ytd)
    period_opening = _current_balance_map(opening, movements_before_period)
    profit_current = _profit_map(movements_current)
    profit_ytd = _profit_map(movements_ytd)
    profit_before_period = _profit_map(movements_before_period)
    balance_current, balance_opening = _build_balance_rows(current, opening, profit_ytd)
    balance_period_opening, _ = _build_balance_rows(period_opening, opening, profit_before_period)
    opening_cash = balance_opening["cash"]
    period_opening_cash = _sum_codes(period_opening, CASH_ACCOUNT_CODES)
    data = StatementData(
        company_name=str(company.get("name") or ""),
        tax_id=str(company.get("taxId") or ""),
        period=period_range,
        balance_current=balance_current,
        balance_opening=balance_opening,
        balance_period_opening=balance_period_opening,
        profit_current=profit_current,
        profit_ytd=profit_ytd,
        cash_current=_cash_map(entries, period_range.start, period_range.end, period_opening_cash),
        cash_ytd=_cash_map(entries, period_range.fiscal_year_start, period_range.end, opening_cash),
    )
    data.checks = _build_checks(data, opening_items)
    return data


def _build_checks(data: StatementData, opening_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = [
        {"level": "ok" if data.tax_id else "error", "message": "纳税人识别号已填写" if data.tax_id else "缺少纳税人识别号，请先在公司设置中填写纳税人识别号"},
        {"level": "ok" if data.company_name else "error", "message": "纳税人名称已填写" if data.company_name else "缺少纳税人名称，请先在公司设置中填写公司名称"},
    ]
    opening_debit = sum((money(i.get("debit_balance") or ZERO) for i in opening_items), ZERO)
    opening_credit = sum((money(i.get("credit_balance") or ZERO) for i in opening_items), ZERO)
    checks.append({"level": "ok" if opening_debit == opening_credit else "error", "message": f"期初余额借贷差额 {_d2(opening_debit - opening_credit)}"})
    balance_difference = _d2(
        data.balance_current.get("asset_total", ZERO)
        - data.balance_current.get("liability_equity_total", ZERO)
    )
    checks.append({
        "level": "ok" if balance_difference == ZERO else "error",
        "message": f"资产负债表借贷差额 {balance_difference}",
    })
    cash_difference = _d2(
        data.cash_ytd.get("ending_cash", ZERO) - data.balance_current.get("cash", ZERO)
    )
    checks.append({
        "level": "ok" if cash_difference == ZERO else "error",
        "message": f"现金流量表与货币资金差额 {cash_difference}",
    })
    return checks



def _cell_ref_to_indexes(cell_ref: str) -> tuple[int, int]:
    letters = ""
    digits = ""
    for ch in cell_ref.upper():
        if ch.isalpha():
            letters += ch
        elif ch.isdigit():
            digits += ch
    if not letters or not digits:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return int(digits) - 1, col - 1

def _write_cell(in_book: Any, out_book: Any, out_sheet: Any, sheet_idx: int, cell_ref: str, value: Any) -> None:
    row, col = _cell_ref_to_indexes(cell_ref)
    if isinstance(value, Decimal):
        # xlwt has no Decimal cell type. Conversion happens only at the final
        # presentation boundary, after every accounting calculation is exact.
        value = float(value)
    # xlutils remaps XF indexes while copying a workbook. Reusing the source
    # index therefore applies an unrelated style (or falls back to the xlwt
    # default). Preserve the already-copied target cell's XF index instead.
    out_row = out_sheet.row(row)
    cells = out_row._Row__cells
    original_xf_index = cells[col].xf_idx if col in cells else None
    out_sheet.write(row, col, value)
    if original_xf_index is not None:
        cells[col].xf_idx = original_xf_index


def _copy_custom_palette(in_book: Any, out_book: Any) -> None:
    """Preserve the official template's custom BIFF8 colour palette."""
    for colour_index, rgb in in_book.colour_map.items():
        if 8 <= colour_index <= 63 and rgb is not None:
            out_book.set_colour_RGB(colour_index, *rgb)


def _validate_month_quarter_template(book: Any) -> None:
    expected_sheets = ["资产负债表", "利润表_月季报", "现金流量表_月季报"]
    if book.sheet_names() != expected_sheets:
        raise RuntimeError("税务局月季报模板工作表结构不匹配，请更新官方模板")
    expected_labels = {
        (0, "B1"): "资产负债表（适用执行小企业会计准则的企业）",
        (1, "B1"): "利润表_月季报（适用执行小企业会计准则的企业）",
        (2, "B1"): "现金流量表_月季报（适用执行小企业会计准则的企业）",
    }
    for (sheet_index, cell_ref), expected in expected_labels.items():
        row, col = _cell_ref_to_indexes(cell_ref)
        if book.sheet_by_index(sheet_index).cell_value(row, col) != expected:
            raise RuntimeError(f"税务局月季报模板关键单元格 {cell_ref} 不匹配，请更新官方模板")


def export_month_quarter_xls(data: StatementData) -> Path:
    try:
        import xlrd
        from xlutils.copy import copy as copy_workbook
    except ImportError as e:
        raise RuntimeError("缺少 xlrd/xlutils/xlwt 依赖，无法写入税务局 .xls 模板") from e
    if not MONTH_QUARTER_TEMPLATE.exists():
        raise FileNotFoundError(f"税务模板不存在：{MONTH_QUARTER_TEMPLATE}")
    if data.period is None:
        raise ValueError("缺少申报所属期")

    in_book = xlrd.open_workbook(str(MONTH_QUARTER_TEMPLATE), formatting_info=True)
    _validate_month_quarter_template(in_book)
    out_book = copy_workbook(in_book)
    _copy_custom_palette(in_book, out_book)
    sheets = [out_book.get_sheet(i) for i in range(3)]

    def w(sheet_idx: int, cell_ref: str, value: Any) -> None:
        _write_cell(in_book, out_book, sheets[sheet_idx], sheet_idx, cell_ref, value)

    # 头部布局差异：资产负债表名称/止期在 H 列，利润表与现金流量表在 F 列
    w(0, "D3", data.tax_id)
    w(0, "H3", data.company_name)
    w(0, "D4", data.period.start)
    w(0, "H4", data.period.end)
    for sheet_idx in (1, 2):
        w(sheet_idx, "D3", data.tax_id)
        w(sheet_idx, "F3", data.company_name)
        w(sheet_idx, "D4", data.period.start)
        w(sheet_idx, "F4", data.period.end)

    balance_cells = {
        "D7": "cash", "E7": "cash", "D8": "short_investment", "E8": "short_investment",
        "D9": "notes_receivable", "E9": "notes_receivable", "D10": "accounts_receivable", "E10": "accounts_receivable",
        "D11": "prepaid", "E11": "prepaid", "D12": "dividends_receivable", "E12": "dividends_receivable",
        "D13": "interest_receivable", "E13": "interest_receivable", "D14": "other_receivable", "E14": "other_receivable",
        "D15": "inventory", "E15": "inventory", "D16": "raw_material", "E16": "raw_material",
        "D18": "goods", "E18": "goods", "D20": "other_current_asset", "E20": "other_current_asset",
        "D21": "current_asset_total", "E21": "current_asset_total",
        "D23": "long_bond_investment", "E23": "long_bond_investment", "D24": "long_equity_investment", "E24": "long_equity_investment",
        "D25": "fixed_asset_cost", "E25": "fixed_asset_cost", "D26": "accumulated_depreciation", "E26": "accumulated_depreciation",
        "D27": "fixed_asset_book_value", "E27": "fixed_asset_book_value",
        "D28": "construction", "E28": "construction", "D32": "intangible_asset", "E32": "intangible_asset",
        "D34": "long_prepaid", "E34": "long_prepaid", "D36": "non_current_asset_total", "E36": "non_current_asset_total",
        "D37": "asset_total", "E37": "asset_total", "H7": "short_borrow", "I7": "short_borrow",
        "H8": "notes_payable", "I8": "notes_payable", "H9": "accounts_payable", "I9": "accounts_payable",
        "H10": "advance_receipt", "I10": "advance_receipt", "H11": "salary_payable", "I11": "salary_payable",
        "H12": "tax_payable", "I12": "tax_payable", "H13": "interest_payable", "I13": "interest_payable",
        "H14": "profit_payable", "I14": "profit_payable", "H15": "other_payable", "I15": "other_payable",
        "H17": "current_liability_total", "I17": "current_liability_total",
        "H19": "long_borrow", "I19": "long_borrow", "H20": "long_payable", "I20": "long_payable",
        "H21": "deferred_income", "I21": "deferred_income", "H23": "non_current_liability_total", "I23": "non_current_liability_total",
        "H24": "liability_total", "I24": "liability_total", "H32": "paid_in_capital", "I32": "paid_in_capital",
        "H33": "capital_reserve", "I33": "capital_reserve", "H34": "surplus_reserve", "I34": "surplus_reserve",
        "H35": "retained_earnings", "I35": "retained_earnings",
        "H36": "equity_total", "I36": "equity_total", "H37": "liability_equity_total", "I37": "liability_equity_total",
    }
    for cell, key in balance_cells.items():
        source = data.balance_current if cell[0] in ("D", "H") else data.balance_opening
        w(0, cell, source.get(key, ZERO))

    profit_cells = {
        "D6": "revenue", "E6": "revenue", "D7": "cost", "E7": "cost", "D8": "tax_surcharge", "E8": "tax_surcharge",
        "D16": "sales_expense", "E16": "sales_expense", "D18": "advertising", "E18": "advertising",
        "D19": "management_expense", "E19": "management_expense", "D21": "entertainment", "E21": "entertainment",
        "D22": "research_expense", "E22": "research_expense",
        "D23": "finance_expense", "E23": "finance_expense", "D25": "investment_income", "E25": "investment_income",
        "D26": "operating_profit", "E26": "operating_profit",
        "D27": "non_operating_income", "E27": "non_operating_income", "D29": "non_operating_expense", "E29": "non_operating_expense",
        "D35": "total_profit", "E35": "total_profit", "D36": "income_tax", "E36": "income_tax",
        "D37": "net_profit", "E37": "net_profit",
    }
    for cell, key in profit_cells.items():
        w(1, cell, (data.profit_current if cell[0] == "D" else data.profit_ytd).get(key, ZERO))

    cash_cells = {
        "D7": "sales_cash", "E7": "sales_cash", "D8": "other_operating_in", "E8": "other_operating_in",
        "D9": "purchase_cash", "E9": "purchase_cash", "D10": "salary_cash", "E10": "salary_cash",
        "D11": "tax_cash", "E11": "tax_cash", "D12": "other_operating_out", "E12": "other_operating_out",
        "D13": "operating_net", "E13": "operating_net",
        "D15": "investment_in", "E15": "investment_in",
        "D16": "investment_income", "E16": "investment_income",
        "D17": "asset_disposal_cash", "E17": "asset_disposal_cash",
        "D18": "investment_out", "E18": "investment_out",
        "D19": "fixed_asset_cash", "E19": "fixed_asset_cash",
        "D20": "investing_net", "E20": "investing_net",
        "D22": "borrow_cash", "E22": "borrow_cash", "D23": "capital_cash", "E23": "capital_cash",
        "D24": "repay_borrow_cash", "E24": "repay_borrow_cash", "D25": "interest_cash", "E25": "interest_cash",
        "D26": "profit_distribution_cash", "E26": "profit_distribution_cash", "D27": "financing_net", "E27": "financing_net",
        "D28": "net_increase", "E28": "net_increase", "D29": "opening_cash", "E29": "opening_cash",
        "D30": "ending_cash", "E30": "ending_cash",
    }
    for cell, key in cash_cells.items():
        w(2, cell, (data.cash_current if cell[0] == "D" else data.cash_ytd).get(key, ZERO))

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"税务财务报表_小企业会计准则_月季报_{data.period.label}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xls"
    output_path = EXPORT_DIR / filename
    out_book.save(str(output_path))
    return output_path
