# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿整册取数：一次查询，内存分组。

与 ``ledger_service`` 的分工：那边是"单科目一次请求"（报表中心按需钻取），
这里是"一册一次请求"。两者的差别不只是性能——

1. 一册里的余额表、明细账、序时账、总账、日记账必须来自**同一时刻**的分录集合，
   否则并发取数期间有人过账，账页之间会自相矛盾（余额表说期末 100，明细账滚到 90）。
2. 原实现每个科目各查一次 ``period_start <= 月末``，N 个科目就重扫 N 遍全历史，
   成本 O(科目数 × 全历史行数)。这里压成 1 次扫描 + 内存按科目切片。

口径与法定账表完全一致：只认 ``posted`` / ``reversed``，按 ``period_start`` 归集，
容器科目（1221/2211/2221）只出子级合计行且不计入合计。这几条由
``tests/test_ledger_book_service.py`` 与旧接口逐字段对比锁死。

金额一律以字符串输出（``money_text``），避免浮点进入前端；余额方向由后端判定，
前端只负责显示——贷余科目在资产类里也存在，方向规则不该散落到视图层。
"""
from __future__ import annotations

import calendar
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from core.chart_policy import CONTAINER_CODES
from core.money import ZERO, money, money_text
from db.models import AccountingPeriod, ChartAccount, Company, JournalEntry, JournalLine
from db.session import session_scope

POSTED_STATUSES = ("posted", "reversed")
MAX_LINES = 200_000

# 固定分页：页码要能与目录对应，就不能交给浏览器自动分页（CSS counter 拿不到
# 总页数）。这里是唯一真值，前端按同样行数切块，页脚直接用后端给的页码。
# 行数不是按纸面高度估的，是 Chrome 真机打印量出来的（2026-10-20）：
# A4 可用高度 = 297 − 16(上) − 14(下) = 267mm ≈ 1009px，每页固定开销
# （标题 + 表头 + 页脚 + 边框）约 110px，明细账页还多一期初行与两行合计。
# 实测每行约 38px（对方科目列会换行，行高比纯数字高），因此：
#   余额表/日记账/序时账 18 行 → 约 794px
#   明细账 16 行 + 期初 + 合计 → 约 810px
# 取值一旦顶满可用高度，Chrome 会把整页推到下一页，页码与物理页立刻错位
#（实测 24 行时 20 页的册子打出 31 页）。改字号或纸型必须同步改这里。
PAGE_SIZE = {
    "balance": 12,
    # detail 曾取 11：年度册中「2026-05 银行存款」一类对方科目密集的页面会撑到
    # 1060px（可用 1009px），把后续所有页推后一页、页码错位（真机量得 72 vs 71）。
    # 降到 10 后该类页面实测 ≤ 970px。改字号或纸型必须同步复核这里。
    "detail": 10,
    "journal": 12,
    "chronological": 12,
}

# 参与 hash 的业务段。meta 里含生成时间这类每次都不同的字段，不能进 hash，
# 否则同一册重算就对不上，"校验通过"就失去意义。
HASH_SECTIONS = ("balance", "details", "chronological", "journals", "general")


def content_hash(book: dict[str, Any]) -> str:
    """账簿内容指纹：实时册与已签发册用同一算法，前端据此判断"是否已过期"。"""
    payload = {key: book.get(key) for key in HASH_SECTIONS}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# 日记账科目：法定账簿要求现金、银行存款单独按日登记
JOURNAL_ACCOUNTS = (
    ("cash", "1001", "现金日记账"),
    ("bank", "1002", "银行存款日记账"),
)

# 其他货币资金（1012）下的平台待结算科目是事实上的"账户"维度：微信、支付宝
# 各自需要独立日记账（与银行账户同理）。未来若建立银行账户档案（辅助核算
# bank_account），在 _journal_targets 里追加按账户拆页即可，页面与分页零改动。
OTHER_CASH_PREFIX = "1012"


class LedgerBookError(ValueError):
    """电子账簿取数的业务错误基类。"""


class InvalidPeriod(LedgerBookError):
    """月份/年度格式不合法。"""


class BookTooLarge(LedgerBookError):
    """单册分录行数超过保护上限。"""


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        year, month_number = (int(part) for part in month.split("-", 1))
        return date(year, month_number, 1), date(
            year, month_number, calendar.monthrange(year, month_number)[1]
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidPeriod("账期格式应为 YYYY-MM") from exc


def _scope_bounds(
    month: str | None, year: int | None, scope: str
) -> tuple[date, date, str]:
    if scope == "year":
        if year is None:
            if month is None:
                raise InvalidPeriod("年度账簿需要 year 参数")
            start, _ = _month_bounds(month)
            year = start.year
        try:
            year = int(year)
            return date(year, 1, 1), date(year, 12, 31), str(year)
        except (TypeError, ValueError) as exc:
            raise InvalidPeriod("年度格式应为 YYYY") from exc
    if not month:
        raise InvalidPeriod("月度账簿需要 month 参数")
    start, end = _month_bounds(month)
    return start, end, month


def _direction_of(value: Decimal) -> str:
    """借余为正、贷余为负；0 记 flat。"""
    if value > ZERO:
        return "debit"
    if value < ZERO:
        return "credit"
    return "flat"


def _signed_text(value: Decimal) -> str:
    return money_text(value)


def _abs_text(value: Decimal) -> str:
    return money_text(abs(value))


def _fmt_quantity(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _empty_bucket(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "debit": ZERO,
        "credit": ZERO,
        "qty_in": Decimal(0),
        "qty_out": Decimal(0),
        "unit": None,
    }


def _add_line(store: dict[str, dict[str, Any]], line: JournalLine, amount: Decimal) -> None:
    bucket = store.get(line.account_code)
    if bucket is None:
        bucket = store[line.account_code] = _empty_bucket(line.account_name)
    if line.unit and not bucket["unit"]:
        bucket["unit"] = line.unit
    if line.direction == "debit":
        bucket["debit"] += amount
        if line.quantity is not None:
            bucket["qty_in"] += line.quantity
    else:
        bucket["credit"] += amount
        if line.quantity is not None:
            bucket["qty_out"] += line.quantity


def _net(bucket: dict[str, Any] | None) -> Decimal:
    if bucket is None:
        return ZERO
    return bucket["debit"] - bucket["credit"]


def _belongs(code: str, requested: str) -> bool:
    """该分录科目是否属于要查的科目（含下级）。

    不做白名单：科目编码是层级编码，更长的编码必然是下级。原先
    ``AGGREGATE_PARENT_CODES`` 只列了 8 个码，5001/5051/5402 一旦有子科目，
    查它们的账页会静默返回空——白名单是会漂移的第二个真值。
    """
    return code == requested or code.startswith(requested)


async def build_ledger_book(
    company_id: str,
    *,
    month: str | None = None,
    year: int | None = None,
    scope: str = "month",
    include_zero_rows: bool = False,
    account_code: str | None = None,
) -> dict[str, Any]:
    """构建一整册电子账簿。

    ``scope="month"``：余额表 + 本月明细账 + 本月序时账 + 本月日记账
    ``scope="year"``：余额表（截至年末）+ 全年明细账 + 全年序时账 + 12 行总账 + 全年日记账

    ``include_zero_rows``：余额表是否带出零发生额科目。**必须由后端决定而不是前端过滤**——
    固定分页的页数按 ``balance.items`` 计算，前端自己滤一遍的话，页数会按全量算、
    页面只渲染过滤后的行，目录说余额表占 8 页、实际只有 1 页，页码直接跳号。

    ``account_code``：单科目查询模式（仅月度册）。明细账页/明细流水只需一个科目
    的账页，传此参数后 ``details`` 只含该科目一页：精确命中直接用册内页；父科目
    或容器科目用同一批分录前缀合成聚合页。这样报表中心与账簿走同一条取数路径，
    归集口径改动只改一处。
    """
    if scope not in ("month", "year"):
        raise InvalidPeriod("scope 只能是 month 或 year")
    start, end, label = _scope_bounds(month, year, scope)
    year_start = date(end.year, 1, 1)

    async with session_scope() as session:
        total = await session.scalar(
            select(func.count(JournalLine.id))
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.period_start <= end,
            )
        )
        if total and total > MAX_LINES:
            raise BookTooLarge(
                f"本期累计分录 {total} 行，超过单册上限 {MAX_LINES} 行，请按季度或年度分册导出"
            )

        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start <= end,
                    JournalLine.company_id == company_id,
                )
                .order_by(
                    JournalEntry.period_start,
                    JournalEntry.voucher_no,
                    JournalLine.line_no,
                )
            )
        ).all()

        chart_rows = (
            await session.execute(
                select(
                    ChartAccount.code,
                    ChartAccount.name,
                    ChartAccount.category,
                    ChartAccount.is_leaf,
                    ChartAccount.is_active,
                    ChartAccount.quantity_enabled,
                    ChartAccount.unit,
                )
                .where(ChartAccount.company_id == company_id)
                .order_by(ChartAccount.code)
            )
        ).all()

        company = await session.get(Company, company_id)
        period_status = await session.scalar(
            select(AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == start,
            )
        )

    chart: dict[str, dict[str, Any]] = {
        code: {
            "name": name,
            "category": category,
            "is_leaf": bool(is_leaf),
            "is_active": bool(is_active),
            "quantity_enabled": bool(quantity_enabled),
            "unit": unit,
        }
        for code, name, category, is_leaf, is_active, quantity_enabled, unit in chart_rows
    }
    company_name = str((company.payload or {}).get("name") or "") if company else ""

    # ── 一次遍历，切成三份：期初（scope 之前）/ 本期 / 本年累计 ──────────────
    opening: dict[str, dict[str, Any]] = {}
    period: dict[str, dict[str, Any]] = {}
    ytd: dict[str, dict[str, Any]] = {}
    by_entry: dict[str, list[JournalLine]] = defaultdict(list)
    chronological: list[dict[str, Any]] = []
    backfill_entries: set[str] = set()
    # 本册期间的凭证张数（不是"截至本月的累计"，封面上的张数要和序时账对得上）
    scope_entry_ids: set[str] = set()

    for entry, line in rows:
        amount = money(line.amount)
        by_entry[entry.id].append(line)
        if entry.period_start < start:
            _add_line(opening, line, amount)
            continue
        _add_line(period, line, amount)
        # 明细账页要按凭证顺序逐笔滚动，桶里同时留一份有序行
        period[line.account_code].setdefault("lines", []).append((entry, line, amount))
        if entry.period_start >= year_start:
            _add_line(ytd, line, amount)
        scope_entry_ids.add(entry.id)
        if entry.occurred_on < start:
            # 业务日期在前、记账期间在本期：补记，册内需提示（否则读者会以为日期写错）
            backfill_entries.add(entry.id)
        chronological.append(
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "period": entry.period_start.isoformat(),
                "date": entry.occurred_on.isoformat(),
                "summary": entry.summary,
                "accountCode": line.account_code,
                "accountName": line.account_name,
                "direction": line.direction,
                "debit": money_text(amount) if line.direction == "debit" else "0.00",
                "credit": money_text(amount) if line.direction == "credit" else "0.00",
                "backfill": entry.period_start != entry.occurred_on.replace(day=1),
            }
        )

    balance = _build_balance(company_id, chart, opening, period)
    balance["totalRows"] = len(balance["items"])
    if not include_zero_rows:
        balance["items"] = [
            row for row in balance["items"] if row["hasActivity"] or row["hasBalance"]
        ]
    details = _build_details(chart, opening, period, ytd, by_entry)
    if account_code and scope == "month":
        matched = [d for d in details if d["code"] == account_code]
        if not matched:
            page = _build_detail_page(
                account_code, chart, opening, period, ytd, by_entry, aggregate=True
            )
            matched = [page] if page else []
        details = matched
    journals = _build_journals(chart, rows, start, end, opening)

    result: dict[str, Any] = {
        "meta": {
            "companyName": company_name,
            "month": label if scope == "month" else None,
            "year": end.year if scope == "year" else None,
            "scope": scope,
            "periodStart": start.isoformat(),
            "periodEnd": end.isoformat(),
            "periodStatus": period_status,
            "periodStatusLabel": _period_status_label(period_status),
            "units": "元",
            "accountingStandard": _standard_label(company.accounting_standard if company else None),
            "entryCount": len(scope_entry_ids),
            "journalBasis": "occurred_on",
            "generatedAt": None,
            "generatedBy": None,
            "snapshotId": None,
            "bookNo": None,
            "contentHash": None,
            "pageCount": None,
        },
        "balance": balance,
        "details": details,
        "chronological": {
            "items": chronological,
            "voucherCount": len({item["entryId"] for item in chronological}),
            "totalDebit": money_text(sum((money(i["debit"]) for i in chronological), ZERO)),
            "totalCredit": money_text(sum((money(i["credit"]) for i in chronological), ZERO)),
            "isBalanced": _is_chronological_balanced(chronological),
        },
        "journals": journals,
        "general": _build_general(chart, rows, year_start, end, opening) if scope == "year" else None,
        "warnings": _build_warnings(
            scope=scope,
            period_status=period_status,
            details=details,
            backfill_count=len(backfill_entries),
            chronological=chronological,
        ),
    }
    result["pagination"] = _paginate(result)
    # 实时册也要有指纹：否则前端拿 null 去比已签发册的 hash，永远判"已过期"
    result["meta"]["contentHash"] = content_hash(result)
    result["meta"]["pageCount"] = result["pagination"]["totalPages"]
    return result


def _period_status_label(status: str | None) -> str:
    return {
        "open": "未结账",
        "closing": "结账中",
        "closed": "已结账",
        "reopened": "已反结账",
    }.get(str(status or ""), "未结账")


def _standard_label(value: str | None) -> str:
    return {
        "small_enterprise_cn": "小企业会计准则",
        "general_cn": "企业会计准则",
    }.get(str(value or ""), "小企业会计准则")


def _is_chronological_balanced(items: list[dict[str, Any]]) -> bool:
    debit = sum((money(i["debit"]) for i in items), ZERO)
    credit = sum((money(i["credit"]) for i in items), ZERO)
    return debit == credit


def _build_balance(
    company_id: str,
    chart: dict[str, dict[str, Any]],
    opening: dict[str, dict[str, Any]],
    period: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """科目余额表：与 ``ledger_service.get_balance_summary`` 同口径，另加方向与标记。"""
    codes: set[str] = set(opening) | set(period)
    # 补全无发生额的启用末级科目（0 行），保证科目表完整性可核对。
    # 容器科目单独补一行子级合计，作为 isParent 展示行。
    for code, info in chart.items():
        if not info["is_active"]:
            continue
        if code in CONTAINER_CODES or info["is_leaf"]:
            codes.add(code)

    items: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO
    opening_debit_total = ZERO
    opening_credit_total = ZERO
    closing_debit_total = ZERO
    closing_credit_total = ZERO
    hidden_zero = 0

    for code in sorted(codes):
        is_parent = code in CONTAINER_CODES
        info = chart.get(code)
        name = (info or {}).get("name") or (opening.get(code) or period.get(code) or {}).get("name") or code
        category = (info or {}).get("category") or ""

        if is_parent:
            # 容器行 = 自身（历史直记，若有）+ 全部子级合计；仅展示，不计入合计，
            # 与试算平衡不重复计数。
            members = [c for c in codes if c.startswith(code)]
            open_value = sum((_net(opening.get(c)) for c in members), ZERO)
            debit_value = sum((period.get(c, {}).get("debit", ZERO) for c in members), ZERO)
            credit_value = sum((period.get(c, {}).get("credit", ZERO) for c in members), ZERO)
        else:
            open_value = _net(opening.get(code))
            debit_value = period.get(code, {}).get("debit", ZERO)
            credit_value = period.get(code, {}).get("credit", ZERO)

        close_value = open_value + debit_value - credit_value
        has_activity = bool(debit_value or credit_value)
        has_balance = bool(open_value or close_value)
        if not has_activity and not has_balance:
            hidden_zero += 1

        item: dict[str, Any] = {
            "accountCode": code,
            "accountName": name,
            "category": category,
            "openingBalance": _signed_text(open_value),
            "openingAbs": _abs_text(open_value),
            "openingDirection": _direction_of(open_value),
            "debitTotal": money_text(debit_value),
            "creditTotal": money_text(credit_value),
            "balance": money_text(debit_value - credit_value),
            "closingBalance": _signed_text(close_value),
            "closingAbs": _abs_text(close_value),
            "closingDirection": _direction_of(close_value),
            "isParent": is_parent,
            "hasActivity": has_activity,
            "hasBalance": has_balance,
        }
        quantity_info = (info or {}).get("quantity_enabled")
        if not is_parent and quantity_info:
            open_qty = opening.get(code, {}).get("qty_in", Decimal(0)) - opening.get(code, {}).get("qty_out", Decimal(0))
            qty_in = period.get(code, {}).get("qty_in", Decimal(0))
            qty_out = period.get(code, {}).get("qty_out", Decimal(0))
            item.update(
                {
                    "hasQuantity": True,
                    "unit": (info or {}).get("unit") or (period.get(code) or {}).get("unit"),
                    "quantityOpening": _fmt_quantity(open_qty),
                    "quantityIn": _fmt_quantity(qty_in),
                    "quantityOut": _fmt_quantity(qty_out),
                    "quantityClosing": _fmt_quantity(open_qty + qty_in - qty_out),
                }
            )
        items.append(item)

        if not is_parent:
            total_debit += debit_value
            total_credit += credit_value
            if open_value > ZERO:
                opening_debit_total += open_value
            else:
                opening_credit_total += -open_value
            if close_value > ZERO:
                closing_debit_total += close_value
            else:
                closing_credit_total += -close_value

    return {
        "items": items,
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
        "isBalanced": total_debit == total_credit,
        "openingDebitTotal": money_text(opening_debit_total),
        "openingCreditTotal": money_text(opening_credit_total),
        "closingDebitTotal": money_text(closing_debit_total),
        "closingCreditTotal": money_text(closing_credit_total),
        "isOpeningBalanced": opening_debit_total == opening_credit_total,
        "isClosingBalanced": closing_debit_total == closing_credit_total,
        "hiddenZeroCount": hidden_zero,
    }


def _build_detail_page(
    code: str,
    chart: dict[str, dict[str, Any]],
    opening: dict[str, dict[str, Any]],
    period: dict[str, dict[str, Any]],
    ytd: dict[str, dict[str, Any]],
    by_entry: dict[str, list[JournalLine]],
    *,
    aggregate: bool = False,
) -> dict[str, Any] | None:
    """构建单个科目的明细账页——册内明细页与单科目查询共用同一实现。

    ``aggregate=False``（默认）：精确科目页。账簿册内明细页的既有口径——只含
    该科目自身的行、自身期初与自身累计。科目既有直接记账又有子科目时（5602），
    页面余额必须与余额表该科目行勾稽，所以不能把子级并进来。

    ``aggregate=True``：前缀聚合页。供账簿外的单科目查询（明细账页、明细流水）
    使用：父科目/容器科目查询时，把全部下级行合并成一页滚动；对方科目排除整个
    科目族；期初与累计按下级桶求和。返回 None 表示该科目无任何可展示内容。
    """

    def _member(candidate: str) -> bool:
        return candidate == code or (aggregate and candidate.startswith(code))

    if aggregate:
        member_codes = {c for c in set(opening) | set(period) | set(ytd) if _member(c)}
        if not member_codes:
            return None
        open_value = sum((_net(opening[c]) for c in opening if _member(c)), ZERO)
        debit_value = sum((period[c].get("debit", ZERO) for c in period if _member(c)), ZERO)
        credit_value = sum((period[c].get("credit", ZERO) for c in period if _member(c)), ZERO)
        ytd_debit = sum((ytd[c].get("debit", ZERO) for c in ytd if _member(c)), ZERO)
        ytd_credit = sum((ytd[c].get("credit", ZERO) for c in ytd if _member(c)), ZERO)
        lines = sorted(
            (tpl for c in member_codes for tpl in (period.get(c, {}).get("lines") or [])),
            key=lambda tpl: (tpl[0].occurred_on, tpl[0].voucher_no, tpl[1].line_no),
        )
        if not lines and not open_value and not debit_value and not credit_value:
            return None
    else:
        bucket = period.get(code) or {}
        lines = bucket.get("lines") or []
        if not lines:
            return None
        open_value = _net(opening.get(code))
        debit_value = bucket.get("debit", ZERO)
        credit_value = bucket.get("credit", ZERO)
        ytd_bucket = ytd.get(code)
        ytd_debit = ytd_bucket["debit"] if ytd_bucket else debit_value
        ytd_credit = ytd_bucket["credit"] if ytd_bucket else credit_value

    info = chart.get(code) or {}
    name = info.get("name") or (period.get(code) or {}).get("name") or code
    opening_bucket = opening.get(code)
    opening_qty = sum(
        (
            opening.get(c, {}).get("qty_in", Decimal(0)) - opening.get(c, {}).get("qty_out", Decimal(0))
            for c in ([code] if not aggregate else (c for c in opening if _member(c)))
        ),
        Decimal(0),
    )
    running = open_value
    rendered: list[dict[str, Any]] = []
    for entry, line, amount in lines:
        signed = amount if line.direction == "debit" else -amount
        running += signed
        row: dict[str, Any] = {
            "date": entry.occurred_on.isoformat(),
            "period": entry.period_start.isoformat(),
            "voucherRef": entry.voucher_no,
            "summary": entry.summary,
            "debit": money_text(amount) if line.direction == "debit" else "0.00",
            "credit": money_text(amount) if line.direction == "credit" else "0.00",
            "balance": _signed_text(running),
            "balanceAbs": _abs_text(running),
            "balanceDirection": _direction_of(running),
            "counterparties": [
                {
                    "code": other.account_code,
                    "name": other.account_name,
                    "amount": money_text(money(other.amount)),
                }
                for other in by_entry.get(entry.id, [])
                if not _member(other.account_code)
            ],
            "backfill": entry.period_start != entry.occurred_on.replace(day=1),
        }
        if line.quantity is not None:
            row["quantity"] = _fmt_quantity(line.quantity)
            row["unitPrice"] = money_text(line.unit_price) if line.unit_price is not None else None
            row["unit"] = line.unit
        rendered.append(row)

    return {
        "code": code,
        "name": name,
        "category": info.get("category") or "",
        "opening": _signed_text(open_value),
        "openingAbs": _abs_text(open_value),
        "openingDirection": _direction_of(open_value),
        "quantityOpening": _fmt_quantity(opening_qty),
        "lines": rendered,
        "periodTotals": {
            "debit": money_text(debit_value),
            "credit": money_text(credit_value),
        },
        "yearTotals": {
            "debit": money_text(ytd_debit),
            "credit": money_text(ytd_credit),
        },
        "closing": _signed_text(running),
        "closingAbs": _abs_text(running),
        "closingDirection": _direction_of(running),
        "lineCount": len(rendered),
        "hasQuantity": bool(info.get("quantity_enabled")),
        "unit": info.get("unit") or (period.get(code) or {}).get("unit"),
    }


def _build_details(
    chart: dict[str, dict[str, Any]],
    opening: dict[str, dict[str, Any]],
    period: dict[str, dict[str, Any]],
    ytd: dict[str, dict[str, Any]],
    by_entry: dict[str, list[JournalLine]],
) -> list[dict[str, Any]]:
    """明细账页：本期有发生额的科目逐笔滚动余额（含对方科目与累计）。"""
    details: list[dict[str, Any]] = []
    for code in sorted(period):
        page = _build_detail_page(code, chart, opening, period, ytd, by_entry)
        if page:
            details.append(page)
    return details


def _journal_targets(
    chart: dict[str, dict[str, Any]],
    rows: list[Any],
    start: date,
    end: date,
) -> list[tuple[str, str, str]]:
    """枚举本册应出日记账页的 (kind, 科目前缀, 标题)。

    现金与银行存款固定出页（惯例，即使本期无发生也保留空页供签章）；其他
    货币资金的末级子科目只在有本期发生时出页——没有流水的平台账户出空白页
    只会让目录虚胖。
    """
    targets = list(JOURNAL_ACCOUNTS)
    period_codes = {
        line.account_code
        for entry, line in rows
        if start <= entry.period_start <= end
    }
    # 1012 混合科目：既有直记（chart_is_leaf=4 位恒末级）又有子科目时两种都算
    if OTHER_CASH_PREFIX in period_codes:
        targets.append(
            (
                OTHER_CASH_PREFIX,
                OTHER_CASH_PREFIX,
                f"{(chart.get(OTHER_CASH_PREFIX) or {}).get('name') or '其他货币资金'}日记账",
            )
        )
    for code in sorted(chart):
        info = chart[code]
        if not code.startswith(OTHER_CASH_PREFIX) or code == OTHER_CASH_PREFIX:
            continue
        if not info.get("is_active"):
            continue
        if not any(c == code or c.startswith(code) for c in period_codes):
            continue
        targets.append((code, code, f"{info.get('name') or code}日记账"))
    return targets


def _build_journals(
    chart: dict[str, dict[str, Any]],
    rows: list[Any],
    start: date,
    end: date,
    opening: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """现金 / 银行存款日记账：按业务发生日期逐日登记，日结存。

    筛选按记账期间（与明细账同一批凭证，期末必然勾稽），排序与分组按业务日期
    （日记账的读者关心"哪天收付的"）。补记凭证会出现早于册期的日期，行上带
    ``backfill`` 标记。
    """
    journals: list[dict[str, Any]] = []
    for kind, code_prefix, title in _journal_targets(chart, rows, start, end):
        open_value = sum(
            (_net(opening.get(code)) for code in opening if code.startswith(code_prefix)),
            ZERO,
        )
        day_buckets: dict[date, dict[str, Any]] = {}
        for entry, line in rows:
            if entry.period_start < start or entry.period_start > end:
                continue
            if not line.account_code.startswith(code_prefix):
                continue
            amount = money(line.amount)
            bucket = day_buckets.setdefault(
                entry.occurred_on,
                {"debit": ZERO, "credit": ZERO, "vouchers": set(), "backfill": False},
            )
            if line.direction == "debit":
                bucket["debit"] += amount
            else:
                bucket["credit"] += amount
            bucket["vouchers"].add(entry.id)
            if entry.period_start != entry.occurred_on.replace(day=1):
                bucket["backfill"] = True

        running = open_value
        day_rows: list[dict[str, Any]] = []
        for day in sorted(day_buckets):
            bucket = day_buckets[day]
            running += bucket["debit"] - bucket["credit"]
            day_rows.append(
                {
                    "date": day.isoformat(),
                    "summary": "本日合计",
                    "debit": money_text(bucket["debit"]),
                    "credit": money_text(bucket["credit"]),
                    "balance": _signed_text(running),
                    "balanceAbs": _abs_text(running),
                    "balanceDirection": _direction_of(running),
                    "voucherCount": len(bucket["vouchers"]),
                    "backfill": bucket["backfill"],
                }
            )
        journals.append(
            {
                "kind": kind,
                "code": code_prefix,
                "name": title,
                "accountName": (chart.get(code_prefix) or {}).get("name") or title,
                "opening": _signed_text(open_value),
                "openingAbs": _abs_text(open_value),
                "openingDirection": _direction_of(open_value),
                "rows": day_rows,
                "closing": _signed_text(running),
                "closingAbs": _abs_text(running),
                "closingDirection": _direction_of(running),
                "totalDebit": money_text(sum((money(r["debit"]) for r in day_rows), ZERO)),
                "totalCredit": money_text(sum((money(r["credit"]) for r in day_rows), ZERO)),
            }
        )
    return journals


def _build_general(
    chart: dict[str, dict[str, Any]],
    rows: list[Any],
    year_start: date,
    year_end: date,
    opening_all: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """总账页：每科目 12 行（按月借/贷/余额），复用同一批分录，不新增查询。"""
    month_debit: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    month_credit: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for entry, line in rows:
        if entry.period_start < year_start or entry.period_start > year_end:
            continue
        amount = money(line.amount)
        target = month_debit if line.direction == "debit" else month_credit
        target[line.account_code][entry.period_start.month] += amount

    codes: set[str] = set(month_debit) | set(month_credit)
    # 容器科目通常不直接记账，codes 里只有子级；总账页要有父级小计行。
    # 迭代中不能改 set，先收集再并。
    codes |= {
        code
        for code in CONTAINER_CODES
        if any(other != code and other.startswith(code) for other in codes)
    }

    items: list[dict[str, Any]] = []
    for code in sorted(codes):
        is_parent = code in CONTAINER_CODES
        info = chart.get(code) or {}
        members = [other for other in codes if other == code or other.startswith(code)]
        # 年初余额：早于本年的全部已过账分录，含下级
        open_value = sum(
            (_net(bucket) for other, bucket in opening_all.items() if _belongs(other, code)),
            ZERO,
        )
        running = open_value
        months: list[dict[str, Any]] = []
        total_debit = ZERO
        total_credit = ZERO
        for month in range(1, 13):
            debit = sum((month_debit[c][month] for c in members), ZERO)
            credit = sum((month_credit[c][month] for c in members), ZERO)
            running += debit - credit
            total_debit += debit
            total_credit += credit
            months.append(
                {
                    "month": f"{year_end.year}-{month:02d}",
                    "debit": money_text(debit),
                    "credit": money_text(credit),
                    "balance": _signed_text(running),
                    "balanceAbs": _abs_text(running),
                    "balanceDirection": _direction_of(running),
                    "hasActivity": bool(debit or credit),
                }
            )
        items.append(
            {
                "accountCode": code,
                "accountName": info.get("name") or code,
                "category": info.get("category") or "",
                "opening": _signed_text(open_value),
                "openingAbs": _abs_text(open_value),
                "openingDirection": _direction_of(open_value),
                "months": months,
                "totalDebit": money_text(total_debit),
                "totalCredit": money_text(total_credit),
                "closing": _signed_text(running),
                "closingAbs": _abs_text(running),
                "closingDirection": _direction_of(running),
                "isParent": is_parent,
            }
        )
    return {
        "year": year_end.year,
        "items": items,
    }


def _build_warnings(
    *,
    scope: str,
    period_status: str | None,
    details: list[dict[str, Any]],
    backfill_count: int,
    chronological: list[dict[str, Any]],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if period_status != "closed":
        warnings.append(
            {
                "code": "PERIOD_NOT_CLOSED",
                "message": "本期尚未结账，账簿会随后续补记变化，不能作为正式账簿归档",
            }
        )
    if not details:
        warnings.append(
            {
                "code": "NO_ACTIVITY",
                "message": "本册期间没有已过账凭证，因此只出科目余额表，不产生明细账页",
            }
        )
    if backfill_count:
        warnings.append(
            {
                "code": "BACKFILL_IN_PERIOD",
                "message": f"本册含 {backfill_count} 张补记凭证（业务日期早于记账期间），账页上已标记「补记」",
            }
        )
    if scope == "month" and chronological:
        unbalanced = _is_chronological_balanced(chronological) is False
        if unbalanced:
            warnings.append(
                {
                    "code": "NOT_BALANCED",
                    "message": "本期借方与贷方发生额不相等，请先核对凭证再出账",
                }
            )
    return warnings


def _page_count(rows: int, kind: str) -> int:
    size = PAGE_SIZE[kind]
    return max(1, (max(rows, 1) + size - 1) // size)


def _paginate(book: dict[str, Any]) -> dict[str, Any]:
    """固定分页：目录页码、页脚「第 X 页 共 Y 页」都以这里为唯一真值。

    前端按同样的行数切块渲染（``chunk(rows, pageSize[kind])``），所以页码不会
    因为浏览器纸型、字号、缩放而漂移——这正是纸质账簿"连续编号"能自证的前提。
    """
    sections: list[dict[str, Any]] = []
    page = 1

    def add(key: str, kind: str, title: str, page_count: int, **extra: Any) -> None:
        nonlocal page
        sections.append(
            {
                "key": key,
                "kind": kind,
                "title": title,
                "pageNo": page,
                "pageCount": page_count,
                **extra,
            }
        )
        page += page_count

    add("cover", "cover", "封面", 1)
    add("toc", "toc", "目录", 1)
    add("balance", "balance", "科目余额表", _page_count(len(book["balance"]["items"]), "balance"))

    general = book.get("general")
    if general and general.get("items"):
        # 总账一科目一页（12 个月 + 本年合计），不被跨页割开
        add("general", "general", "总账", max(1, len(general["items"])))

    for detail in book["details"]:
        add(
            f"detail:{detail['code']}",
            "detail",
            f"{detail['code']} {detail['name']} 明细账",
            _page_count(len(detail["lines"]), "detail"),
            code=detail["code"],
        )

    for journal in book["journals"]:
        if not journal["rows"]:
            continue
        add(
            f"journal:{journal['kind']}",
            "journal",
            journal["name"],
            _page_count(len(journal["rows"]), "journal"),
            kind_code=journal["kind"],
        )

    if book["chronological"]["items"]:
        add(
            "chronological",
            "chronological",
            "序时账",
            _page_count(len(book["chronological"]["items"]), "chronological"),
        )

    add("signoff", "signoff", "落款", 1)

    return {
        "sections": sections,
        "totalPages": page - 1,
        "pageSize": dict(PAGE_SIZE),
    }
