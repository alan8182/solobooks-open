# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""演示账套：一键生成 / 删除一本带样本数据的独立账套。

与真实账套**物理隔离**：``payload.isDemo = true``，删除时强校验这个标记，
任何情况下都不会碰到真实账套。

日期必须按「今天」动态生成——``scripts/seed_demo_book.py`` 里硬编的
2026-04~09 到了 2027 年会撞 ``assert_book_started``（记账日期早于启用日期），
所以这里统一先定 ``startDate``、再按相对月份生成业务日期。
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NOT_FOUND, AccountingError
from db.models import AuthUser, Company, Invoice, JournalEntry, LegacyEntryProjection
from db.session import session_scope
from services.account_service import create_company, set_user_current_company
from services.bank_service import import_statements_json
from services.company_delete_service import purge_company
from services.entry_service import list_entries, save_entry
from services import tax_registration_service

logger = logging.getLogger(__name__)

DEMO_NAME = "【演示】杭州南山科技有限公司"
EMPTY_COMPANY_NAME = "我的账套"
DEMO_SEED_VERSION = 4
MAX_MONTHS = 6
COOLDOWN_SECONDS = 60
DEMO_OWNER_NAME = "陈默"
DEMO_OWNER_DEPARTMENT = "总经办"
DEMO_OWNER_SALARY = Decimal("6500.00")
DEMO_OWNER_SS_BASE = Decimal("5000.00")
DEMO_OWNER_HF_RATE = Decimal("0.12")
_DEMO_INVOICES_FLAG = "demo_invoices_seeded"
_DEMO_PRIVATE_EXPENSES_FLAG = "demo_private_expenses_seeded"
_DEMO_STATEMENTS_FLAG = "demo_statements_seeded"
_BANK_ACCOUNT_PREFIX = "1002"

_CLIENTS = (
    "杭州云启贸易有限公司",
    "杭州白鹭文化传媒有限公司",
    "上海临溪信息技术有限公司",
)
_REVENUE_DAYS = (8, 15, 24)
# 每个营业月的三笔收入：(金额, 开票状态)。按月份递增取尾部，模拟经营波动。
# 开票状态只用系统认可的取值：has_invoice（有票）/ no_invoice（无票收入）。
_REVENUE = (
    ((12000, "has_invoice"), (6800, "has_invoice"), (4500, "has_invoice")),
    ((15000, "has_invoice"), (5200, "no_invoice"), (3800, "has_invoice")),
    ((9600, "no_invoice"), (6400, "no_invoice"), (3200, "has_invoice")),
    ((13500, "has_invoice"), (4800, "has_invoice"), (2600, "no_invoice")),
    ((11200, "has_invoice"), (5600, "has_invoice"), (3000, "no_invoice")),
    ((14800, "has_invoice"), (5400, "no_invoice"), (3300, "has_invoice")),
)
_CLOUD = (360.0, 360.0, 412.5, 431.25, 431.25, 380.0)
_TRAFFIC: tuple[float | None, ...] = (None, 128.0, 86.0, 214.0, 96.0, 140.0)

_COOLDOWN: dict[str, float] = {}


def _month_sequence(today: date, months: int) -> list[tuple[int, int]]:
    """从 today 往前推 months 个月，返回正序 [(年, 月)]。"""
    out: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(months):
        out.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def demo_owner_payroll_amounts() -> dict[str, Decimal]:
    """演示老板工资：杭州社保口径，公积金个人/单位各 12%。"""
    from config import SOCIAL_COMPANY_RATE, SOCIAL_PERSONAL_RATE

    base = DEMO_OWNER_SS_BASE
    personal_social = (base * Decimal(str(SOCIAL_PERSONAL_RATE))).quantize(Decimal("0.01"))
    company_social = (base * Decimal(str(SOCIAL_COMPANY_RATE))).quantize(Decimal("0.01"))
    personal_hf = (base * DEMO_OWNER_HF_RATE).quantize(Decimal("0.01"))
    company_hf = (base * DEMO_OWNER_HF_RATE).quantize(Decimal("0.01"))
    personal_total = personal_social + personal_hf
    company_total = company_social + company_hf
    net_salary = DEMO_OWNER_SALARY - personal_total
    return {
        "personal_social": personal_social,
        "company_social": company_social,
        "personal_hf": personal_hf,
        "company_hf": company_hf,
        "personal_total": personal_total,
        "company_total": company_total,
        "net_salary": net_salary,
    }


def _apply_running_balance(statements: list[dict[str, Any]]) -> None:
    statements.sort(key=lambda item: (item["transaction_date"], item.get("id", "")))
    balance = 0.0
    for statement in statements:
        balance = round(balance + statement["amount"], 2)
        statement["balance"] = balance


def build_demo_book(
    today: date | None = None, months: int = 3
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str, str]], date]:
    """生成演示账本的记账条目、银行流水、配对关系与启用日期。

    返回 ``(entries, statements, pairs, start_date)``。纯函数，不碰数据库，
    因此可以在测试里直接断言，不需要真建账套。
    """
    today = today or date.today()
    months = max(1, min(months, MAX_MONTHS))
    window = _month_sequence(today, months)
    start_date = date(window[0][0], window[0][1], 1)
    revenue = _REVENUE[-months:]
    cloud = _CLOUD[-months:]
    traffic = _TRAFFIC[-months:]

    entries: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    pairs: list[tuple[str, str, str]] = []
    seq = 0

    def add(
        year_month: tuple[int, int],
        day: int,
        title: str,
        amount: float,
        direction: str,
        category: str,
        *,
        billing: str = "",
        payer: str = "company_account",
        source: str = "",
        invoice_no: str = "",
        match: bool = True,
        desc: str = "",
        voucher_lines: dict[str, Any] | None = None,
    ) -> str:
        nonlocal seq
        seq += 1
        year, month = year_month
        occurred = f"{year:04d}-{month:02d}-{day:02d}"
        entry_id = f"demo-{year:04d}{month:02d}{day:02d}-{seq:03d}"
        entry: dict[str, Any] = {
            "id": entry_id,
            "title": title,
            "amount": f"{amount:.2f}",
            "direction": direction,
            "category": category,
            "payer": payer,
            "occurDate": occurred,
            # 来源要说实话：走公户认领来的就是 bank_import，纯手工补录才是 manual。
            # 前端「发票 / 手工账」的标签和来源统计都读这个字段。
            "source": source or ("bank_import" if match else "manual"),
        }
        if billing:
            entry["billingStatus"] = billing
        if match:
            entry["paymentDate"] = occurred
        if invoice_no:
            entry["invoiceNumber"] = invoice_no
        if voucher_lines:
            # 显式凭证行：绕过科目映射的默认对手科目（默认贷 1002 会误动银行存款）
            entry["voucherLines"] = voucher_lines
        entries.append(entry)
        if match:
            incoming = direction in ("income", "owner_transfer")
            summary = desc or f"招行对公 | {title}"
            statements.append(
                {
                    "id": f"bs-demo-{seq:03d}",
                    "transaction_date": occurred,
                    "description": summary,
                    "amount": round(amount if incoming else -amount, 2),
                }
            )
            pairs.append((occurred, summary, entry_id))
        return entry_id

    add(
        window[0],
        1,
        "股东投入注册资本",
        200000,
        "owner_transfer",
        "股东投入",
        desc="招行对公 | 股东投资款入账",
    )
    add(
        window[0],
        10,
        "购办公电脑及显示器",
        16800,
        "expense",
        "固定资产",
        billing="has_invoice",
        desc="招行对公 | 转出-办公设备采购",
    )

    for index, year_month in enumerate(window):
        _, month = year_month
        for position, (amount, billing) in enumerate(revenue[index]):
            client = _CLIENTS[position]
            add(
                year_month,
                _REVENUE_DAYS[position],
                f"技术服务费-{client}",
                amount,
                "income",
                "主营业务收入",
                billing=billing,
                desc=f"招行对公 | 转入-项目结算({client})",
            )
        add(
            year_month,
            5,
            "办公室租金",
            2000,
            "expense",
            "房租物业",
            billing="no_invoice",
            desc="招行对公 | 转出-办公室租金",
        )
        add(
            year_month,
            12,
            "办公用品采购",
            80 + month * 12,
            "expense",
            "办公费",
            billing="has_invoice",
            desc="招行对公 | 转出-办公用品",
        )
        add(
            year_month,
            20,
            "银行账户手续费",
            15 + month,
            "expense",
            "银行及金融手续费",
            billing="no_invoice",
            desc="招行对公 | 扣除-账户管理费",
        )
        if cloud[index] is not None:
            add(
                year_month,
                18,
                "腾讯云服务器费用",
                cloud[index],
                "expense",
                "信息技术服务费",
                billing="has_invoice",
                desc="招行对公 | 转出-云服务费",
            )
        if traffic[index] is not None:
            add(
                year_month,
                22,
                "市内交通费报销",
                traffic[index],
                "expense",
                "交通",
                payer="employee_personal:林晓",
                billing="has_invoice",
                match=False,
                desc="员工垫付-市内交通",
            )
        add(
            year_month,
            28,
            f"{month} 月员工工资发放",
            18000,
            "expense",
            "人力成本",
            billing="no_invoice",
            desc="招行对公 | 代发工资",
        )

    last_year, last_month = window[-1]
    # 最后一个月：发票已收到、尚未付款 → 「待付款」演示；两笔已到账未记账 → 「待处理流水」演示
    # 日期不能落到未来：流水不可能来自明天，所以当月末两笔按「今天」封顶。
    today = date.today()
    day_cap = today.day if (last_year, last_month) == (today.year, today.month) else 28
    add(
        window[-1],
        18,
        "发票-腾讯云计算（北京）有限责任公司",
        431.25,
        "expense",
        "信息技术服务费",
        billing="has_invoice",
        source="invoice_import",
        invoice_no="26517000000318574962",
        match=False,
        voucher_lines={
            "debit": [
                {
                    "accountCode": "5602015",
                    "accountName": "管理费用-信息技术服务费",
                    "amount": 431.25,
                }
            ],
            "credit": [
                {"accountCode": "2241", "accountName": "其他应付款", "amount": 431.25}
            ],
        },
    )
    statements.extend(
        [
            {
                "id": "bs-demo-u001",
                "transaction_date": f"{last_year:04d}-{last_month:02d}-{min(28, day_cap):02d}",
                "description": "招行对公 | 转入-项目尾款(杭州白鹭文化传媒有限公司)",
                "amount": 6200,
            },
            {
                "id": "bs-demo-u002",
                "transaction_date": f"{last_year:04d}-{last_month:02d}-{min(29, day_cap):02d}",
                "description": "招行对公 | 转入-技术服务预付款(个人客户)",
                "amount": 2400,
            },
        ]
    )

    _apply_running_balance(statements)
    return entries, statements, pairs, start_date


async def _seed_owner_payroll(
    company_id: str, user_id: str, month: str
) -> dict[str, Any]:
    """给演示账套补一位老板员工，并按杭州口径完成工资、社保、公积金闭环。"""
    from services.database import get_conn
    from services.salary_service import (
        accrue_salary_sheet,
        create_employee,
        get_or_create_salary_sheet,
        pay_payroll_liability,
        pay_salary_sheet,
        update_salary_records,
    )

    amounts = demo_owner_payroll_amounts()
    employee = await create_employee(
        company_id,
        DEMO_OWNER_NAME,
        DEMO_OWNER_DEPARTMENT,
        DEMO_OWNER_SALARY,
        DEMO_OWNER_SS_BASE,
        is_owner=True,
    )
    sheet = await get_or_create_salary_sheet(company_id, month)
    record = sheet["records"][0]
    await update_salary_records(
        company_id,
        month,
        [
            {
                **record,
                "personal_social_security": str(amounts["personal_total"]),
                "company_social_security": str(amounts["company_total"]),
                "personal_tax": "0.00",
            }
        ],
    )

    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE salary_sheets SET personal_housing_fund_total = ?, "
            "company_housing_fund_total = ? WHERE company_id = ? AND month = ?",
            (
                str(amounts["personal_hf"]),
                str(amounts["company_hf"]),
                company_id,
                month,
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    year, month_no = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_no)[1]
    salary_day = f"{month}-{min(28, last_day):02d}"
    social_day = f"{month}-{min(29, last_day):02d}"
    housing_day = f"{month}-{min(30, last_day):02d}"

    await accrue_salary_sheet(company_id, user_id, month)
    paid = await pay_salary_sheet(company_id, user_id, month, pay_date=salary_day)
    social = await pay_payroll_liability(
        company_id, user_id, month, "social_security", pay_date=social_day
    )
    housing = await pay_payroll_liability(
        company_id, user_id, month, "housing_fund", pay_date=housing_day
    )

    return {
        "employeeId": employee["id"],
        "month": month,
        "salaryVoucherId": paid["payment_voucher_id"],
        "salaryAmount": amounts["net_salary"],
        "salaryDate": salary_day,
        "socialVoucherId": social["ss_paid_voucher_id"],
        "socialAmount": amounts["personal_social"] + amounts["company_social"],
        "socialDate": social_day,
        "housingVoucherId": housing["hf_paid_voucher_id"],
        "housingAmount": amounts["personal_hf"] + amounts["company_hf"],
        "housingDate": housing_day,
        "voucherCount": 4,
    }


_DEMO_INVOICE_COMPANY = "杭州南山科技有限公司"
_DEMO_INVOICE_TAX_ID = "91330100MA2DEMO01X"


def _demo_invoice_number(entry_id: str, existing: str = "") -> str:
    if existing:
        return existing
    digest = hashlib.sha1(entry_id.encode("utf-8")).hexdigest()
    return f"2651{int(digest[:12], 16):012d}"


def _demo_invoice_rate(title: str, direction: str) -> str:
    if direction == "income":
        return "1%"
    if "电脑" in title or "办公用品" in title:
        return "13%"
    return "6%"


def _demo_invoice_seller(title: str) -> str:
    if "腾讯云" in title:
        return "腾讯云计算（北京）有限责任公司"
    if "电脑" in title:
        return "杭州数码港科技有限公司"
    if "办公用品" in title:
        return "杭州文印办公用品有限公司"
    return _DEMO_INVOICE_COMPANY


def _demo_invoice_candidate(entry: dict[str, Any]) -> bool:
    title = str(entry.get("title") or "")
    direction = str(entry.get("direction") or "")
    billing = str(entry.get("billingStatus") or "")
    if direction == "income":
        return title.startswith("技术服务费-") and billing not in ("", "no_invoice")
    if direction == "expense":
        return title in {"购办公电脑及显示器", "办公用品采购", "腾讯云服务器费用"} or title.startswith("发票-腾讯云")
    return False


async def _seed_demo_invoices(company_id: str, user_id: str) -> int:
    """为演示账套补一组进销项发票；按 entry_id 幂等，不依赖台账回填标记。"""
    entries = await list_entries(user_id, company_id)
    async with session_scope() as session:
        existing_entry_ids = {
            str(value)
            for value in (
                await session.execute(
                    select(Invoice.entry_id).where(
                        Invoice.company_id == company_id,
                        Invoice.entry_id.is_not(None),
                    )
                )
            ).scalars().all()
            if value
        }

    records: list[tuple[str | None, dict[str, Any], str | None]] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id or entry_id in existing_entry_ids:
            continue
        if not _demo_invoice_candidate(entry):
            continue
        amount = Decimal(str(entry.get("amount") or 0))
        if amount <= 0:
            continue
        title = str(entry.get("title") or "演示发票")
        direction = str(entry.get("direction") or "expense")
        rate_text = _demo_invoice_rate(title, direction)
        rate = Decimal(rate_text.rstrip("%")) / Decimal("100")
        tax_amount = (amount * rate / (Decimal("1") + rate)).quantize(Decimal("0.01"))
        amount_excl = amount - tax_amount
        if direction == "income":
            seller = _DEMO_INVOICE_COMPANY
            buyer = title.split("-", 1)[1].strip() if "-" in title else ""
        else:
            seller = _demo_invoice_seller(title)
            buyer = _DEMO_INVOICE_COMPANY
        item_name = title.removeprefix("发票-")
        payload = {
            "direction": direction,
            "amount": str(amount),
            "invoiceNumber": _demo_invoice_number(
                entry_id, str(entry.get("invoiceNumber") or "")
            ),
            "invoice_type": "电子普通发票",
            "invoice_status": "normal",
            "invoice_date": str(entry.get("occurDate") or ""),
            "buyer_name": buyer,
            "buyer_tax_id": "",
            "seller_name": seller,
            "seller_tax_id": _DEMO_INVOICE_TAX_ID if seller == _DEMO_INVOICE_COMPANY else "",
            "tax_rate": rate_text,
            "tax_amount": str(tax_amount),
            "amountExcludingTax": str(amount_excl),
            "item_name": item_name,
            "remark": "演示账套虚拟发票",
            "source": "demo_seed",
        }
        records.append((entry_id, payload, None))

    if not records:
        return 0
    from services.invoice_ledger_service import record_invoices_bulk

    return await record_invoices_bulk(company_id, records)


async def _backfill_invoice_numbers(company_id: str) -> int:
    """把发票台账里的票号回写到凭证，让「这笔有没有票」在账目列表里直接可见。

    发票台账是独立表；凭证 payload 不带票号时，月结页只能按 ``source`` 猜，
    于是有票的单据也被标成「手工账」——演示账套过去就是这样：库里 16 张发票，
    界面上却一张都看不到。
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice.entry_id, Invoice.invoice_number).where(
                    Invoice.company_id == company_id,
                    Invoice.entry_id.is_not(None),
                    Invoice.invoice_number != "",
                )
            )
        ).all()

        updated = 0
        for entry_id, number in rows:
            entry_id = str(entry_id or "")
            number = str(number or "").strip()
            if not entry_id or not number:
                continue
            journal = await session.get(JournalEntry, entry_id)
            if journal is not None:
                metadata = dict(journal.metadata_json or {})
                if metadata.get("invoiceNumber") != number:
                    metadata["invoiceNumber"] = number
                    journal.metadata_json = metadata
                    updated += 1
            projection = await session.get(LegacyEntryProjection, entry_id)
            if projection is not None:
                try:
                    legacy = (
                        json.loads(projection.payload)
                        if isinstance(projection.payload, str)
                        else dict(projection.payload or {})
                    )
                except (json.JSONDecodeError, TypeError):
                    legacy = {}
                if isinstance(legacy, dict) and legacy.get("invoiceNumber") != number:
                    legacy["invoiceNumber"] = number
                    projection.payload = json.dumps(legacy, ensure_ascii=False)
        return updated


async def ensure_demo_invoices(company_id: str) -> int:
    """演示账套一次性补发票；已补过则零成本返回（但仍会补齐票号回写）。"""
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        payload = (company.payload if company is not None else None) or {}
        if not payload.get("isDemo"):
            return 0
        already_seeded = payload.get(_DEMO_INVOICES_FLAG) == DEMO_SEED_VERSION
        owner_user_id = company.owner_user_id if company is not None else ""

    if already_seeded:
        await _backfill_invoice_numbers(company_id)
        return 0

    written = await _seed_demo_invoices(company_id, owner_user_id)
    await _backfill_invoice_numbers(company_id)
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        if company is None:
            return written
        payload = dict(company.payload or {})
        if payload.get("isDemo"):
            payload[_DEMO_INVOICES_FLAG] = DEMO_SEED_VERSION
            payload["demoSeedVersion"] = DEMO_SEED_VERSION
            company.payload = payload
    return written


def _demo_private_date(today: date, month_offset: int, day: int) -> str:
    total = today.year * 12 + today.month - 1 + month_offset
    year, month_index = divmod(total, 12)
    month = month_index + 1
    last_day = calendar.monthrange(year, month)[1]
    safe_day = min(day, last_day)
    if month_offset == 0:
        safe_day = min(safe_day, today.day)
    return f"{year:04d}-{month:02d}-{safe_day:02d}"


async def _seed_demo_private_expenses(company_id: str, user_id: str) -> int:
    from services.database import get_conn
    from services.private_expense_service import _ensure_schema

    await _ensure_schema()
    today = date.today()
    rows = [
        {
            "title": "私账收到设计费",
            "amount": "1500.00",
            "direction": "income",
            "category": "私下收款",
            "occur_date": _demo_private_date(today, 0, 14),
            "payer": "wechat",
            "owner_name": "本人",
            "private_account": "个人微信",
            "is_available": 1,
            "memo": "客户通过个人微信转入",
            "invoice_status": "no_invoice",
        },
        {
            "title": "林晓垫付快递费",
            "amount": "32.00",
            "direction": "expense",
            "category": "快递物流",
            "occur_date": _demo_private_date(today, 0, 9),
            "payer": "wechat",
            "owner_name": "林晓",
            "private_account": "个人微信",
            "is_available": 1,
            "memo": "寄送合同垫付，待报销",
            "invoice_status": "no_invoice",
        },
        {
            "title": "私账收到项目尾款",
            "amount": "3200.00",
            "direction": "income",
            "category": "项目尾款",
            "occur_date": _demo_private_date(today, -1, 22),
            "payer": "wechat",
            "owner_name": "本人",
            "private_account": "个人微信",
            "is_available": 1,
            "memo": "项目尾款先到私账",
            "invoice_status": "no_invoice",
        },
        {
            "title": "客户咖啡",
            "amount": "68.00",
            "direction": "expense",
            "category": "业务招待",
            "occur_date": _demo_private_date(today, -1, 18),
            "payer": "wechat",
            "owner_name": "本人",
            "private_account": "个人微信",
            "is_available": 1,
            "memo": "沟通项目需求",
            "invoice_status": "no_invoice",
        },
        {
            "title": "小红书平台保证金",
            "amount": "1000.00",
            "direction": "expense",
            "category": "押金保证金",
            "occur_date": _demo_private_date(today, -1, 5),
            "payer": "alipay",
            "owner_name": "本人",
            "private_account": "平台保证金",
            "is_available": 0,
            "memo": "平台保证金，暂不可用",
            "invoice_status": "no_invoice",
        },
        {
            "title": "打印标书",
            "amount": "45.00",
            "direction": "expense",
            "category": "打印复印",
            "occur_date": _demo_private_date(today, -2, 12),
            "payer": "wechat",
            "owner_name": "本人",
            "private_account": "个人微信",
            "is_available": 1,
            "memo": "已取得电子普通发票",
            "invoice_status": "has_invoice",
        },
        {
            "title": "滴滴打车-拜访客户",
            "amount": "56.00",
            "direction": "expense",
            "category": "交通差旅",
            "occur_date": _demo_private_date(today, -2, 20),
            "payer": "alipay",
            "owner_name": "本人",
            "private_account": "个人支付宝",
            "is_available": 1,
            "memo": "临时打车",
            "invoice_status": "no_invoice",
        },
        {
            "title": "平台待结算货款",
            "amount": "880.00",
            "direction": "income",
            "category": "平台提现",
            "occur_date": _demo_private_date(today, -2, 26),
            "payer": "wechat",
            "owner_name": "本人",
            "private_account": "平台待结算",
            "is_available": 0,
            "memo": "平台结算中，暂不可用",
            "invoice_status": "no_invoice",
        },
    ]

    now = datetime.now(timezone.utc).isoformat()
    conn = await get_conn()
    try:
        for index, row in enumerate(rows):
            await conn.execute(
                "INSERT INTO private_expenses "
                "(id, company_id, user_id, title, amount, direction, category, occur_date, payer, "
                "owner_name, private_account, is_available, memo, invoice_status, substitute_entry_id, "
                "substitute_memo, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    f"pe-demo-{company_id}-{index + 1}",
                    company_id,
                    user_id,
                    row["title"],
                    row["amount"],
                    row["direction"],
                    row["category"],
                    row["occur_date"],
                    row["payer"],
                    row["owner_name"],
                    row["private_account"],
                    row["is_available"],
                    row["memo"],
                    row["invoice_status"],
                    now,
                    now,
                ),
            )
        await conn.commit()
    finally:
        await conn.close()
    return len(rows)


async def ensure_demo_private_expenses(company_id: str) -> int:
    """演示账套一次性补小私账；已补过则零成本返回。"""
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        payload = (company.payload if company is not None else None) or {}
        if not payload.get("isDemo"):
            return 0
        if payload.get(_DEMO_PRIVATE_EXPENSES_FLAG) == DEMO_SEED_VERSION:
            return 0
        owner_user_id = company.owner_user_id if company is not None else ""

    written = await _seed_demo_private_expenses(company_id, owner_user_id)
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        if company is None:
            return written
        payload = dict(company.payload or {})
        if payload.get("isDemo"):
            payload[_DEMO_PRIVATE_EXPENSES_FLAG] = DEMO_SEED_VERSION
            payload["demoSeedVersion"] = DEMO_SEED_VERSION
            company.payload = payload
    return written


async def _find_demo_company(user_id: str) -> dict[str, Any] | None:
    """每用户最多一本演示账本。在 Python 侧判断，避免 payload JSON 路径的方言差异。"""
    async with session_scope() as session:
        rows = (
            await session.execute(select(Company).where(Company.owner_user_id == user_id))
        ).scalars().all()
    for company in rows:
        payload = company.payload or {}
        if payload.get("isDemo"):
            return {"companyId": company.company_id, "name": str(payload.get("name") or DEMO_NAME)}
    return None


def _bank_effect(entry: dict[str, Any]) -> Decimal:
    """凭证对银行存款（1002*）的净影响：借增贷减，正数=钱进公户。"""
    lines = entry.get("voucherLines") or {}
    if not isinstance(lines, dict):
        lines = {}
    effect = Decimal("0")
    for direction, sign in (("debit", 1), ("credit", -1)):
        for line in lines.get(direction) or entry.get(direction) or []:
            if not isinstance(line, dict):
                continue
            if str(line.get("accountCode") or "").startswith(_BANK_ACCOUNT_PREFIX):
                try:
                    effect += sign * Decimal(str(line.get("amount") or "0"))
                except (ArithmeticError, ValueError):
                    continue
    return effect.quantize(Decimal("0.01"))


async def _covered_entry_ids(session: AsyncSession, company_id: str) -> set[str]:
    """已配对到流水的凭证 id。裸表不存在时（全新库、还没导过流水）返回空集。"""
    try:
        rows = await session.execute(
            text(
                "SELECT entry_id FROM bank_statement_matches WHERE company_id = :c "
                "UNION SELECT matched_entry_id FROM bank_statements "
                "WHERE company_id = :c AND matched_entry_id IS NOT NULL"
            ),
            {"c": company_id},
        )
        return {str(row[0]) for row in rows.fetchall() if row[0]}
    except Exception:  # noqa: BLE001 - 流水裸表缺失等同于「还没有任何配对」
        logger.debug("演示账套：读取流水配对失败（company=%s）", company_id, exc_info=True)
        return set()


async def _match_statements(
    session: AsyncSession, company_id: str, pairs: list[tuple[str, str, str]]
) -> int:
    """把演示流水与凭证配对，并把凭证标记为「已对账」。

    和真实认领路径保持一致：``billingStatus`` 改成 ``reconciled``、补上
    ``paymentDate``，原状态存进 ``bank_statement_matches.previous_billing_status``
    （解绑时用于还原）。只写投影 payload，不动权威凭证行。
    """
    matched = 0
    for occurred, summary, entry_id in pairs:
        statement_id = await session.scalar(
            text(
                "SELECT id FROM bank_statements WHERE company_id = :c "
                "AND transaction_date = :d AND description = :s LIMIT 1"
            ),
            {"c": company_id, "d": occurred, "s": summary},
        )
        if not statement_id:
            continue

        projection = await session.get(LegacyEntryProjection, entry_id)
        payload: dict[str, Any] = {}
        if projection is not None:
            try:
                loaded = json.loads(projection.payload) if isinstance(projection.payload, str) else projection.payload
                payload = dict(loaded or {}) if isinstance(loaded, dict) else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}

        await session.execute(
            text(
                "INSERT INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, "
                "previous_payment_date, created_at) "
                "VALUES (:sid, :c, :e, :billing, :payment, :ts) ON CONFLICT DO NOTHING"
            ),
            {
                "sid": statement_id,
                "c": company_id,
                "e": entry_id,
                "billing": payload.get("billingStatus"),
                "payment": payload.get("paymentDate"),
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        await session.execute(
            text(
                "UPDATE bank_statements SET matched_entry_id = :e "
                "WHERE id = :sid AND company_id = :c"
            ),
            {"e": entry_id, "sid": statement_id, "c": company_id},
        )
        if projection is not None:
            payload["billingStatus"] = "reconciled"
            payload.setdefault("paymentDate", occurred)
            projection.payload = json.dumps(payload, ensure_ascii=False)
        matched += 1
    await session.flush()
    return matched


async def _recompute_balances(company_id: str) -> None:
    """按日期重算全部流水的运行余额（后补流水会让旧余额失真）。"""
    from services.database import get_conn

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, amount FROM bank_statements WHERE company_id = ? "
            "ORDER BY transaction_date, id",
            (company_id,),
        )
        balance = Decimal("0")
        updates = []
        for row in await cursor.fetchall():
            balance += Decimal(str(row["amount"] or "0"))
            updates.append((str(balance.quantize(Decimal("0.01"))), row["id"], company_id))
        for value, sid, cid in updates:
            await conn.execute(
                "UPDATE bank_statements SET balance = ? WHERE id = ? AND company_id = ?",
                (value, sid, cid),
            )
        await conn.commit()
    finally:
        await conn.close()


async def _derive_missing_statements(company_id: str, user_id: str) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """按「凭证动过银行存款」反推缺失的流水。

    比照抄种子数组可靠：历史演示账套（流水整批丢失的）也能靠它修好；
    员工垫付、未付款发票这类不动公户的业务不会凭空长出流水。
    """
    entries = await list_entries(user_id, company_id)
    async with session_scope() as session:
        covered = await _covered_entry_ids(session, company_id)

    statements: list[dict[str, Any]] = []
    pairs: list[tuple[str, str, str]] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id or entry_id in covered:
            continue
        effect = _bank_effect(entry)
        if effect == 0:
            continue
        occurred = str(entry.get("occurDate") or entry.get("occur_date") or "")[:10]
        if not occurred:
            continue
        summary = f"招行对公 | {entry.get('title') or entry.get('summary') or '银行收支'}"
        statements.append(
            {
                "transaction_date": occurred,
                "description": summary,
                "amount": float(effect),
                "balance": 0.0,
            }
        )
        pairs.append((occurred, summary, entry_id))
    return statements, pairs


async def _demo_unclaimed_statements(company_id: str, user_id: str) -> list[dict[str, Any]]:
    """「已到账但还没记账」的两笔流水——演示里唯一需要老板动手认领的场景。

    日期按最近一笔业务所在月、且不晚于今天（流水不可能来自未来）。
    """
    entries = await list_entries(user_id, company_id)
    months = sorted(
        {
            str(entry.get("occurDate") or entry.get("occur_date") or "")[:7]
            for entry in entries
            if entry.get("occurDate") or entry.get("occur_date")
        }
    )
    if not months:
        return []
    last_month = months[-1]
    today = date.today()
    day_cap = today.day if last_month == today.strftime("%Y-%m") else 28
    first_day = min(28, day_cap)
    second_day = min(29, day_cap)
    return [
        {
            "transaction_date": f"{last_month}-{first_day:02d}",
            "description": "招行对公 | 转入-项目尾款(杭州白鹭文化传媒有限公司)",
            "amount": 6200.0,
            "balance": 0.0,
        },
        {
            "transaction_date": f"{last_month}-{second_day:02d}",
            "description": "招行对公 | 转入-技术服务预付款(个人客户)",
            "amount": 2400.0,
            "balance": 0.0,
        },
    ]


async def ensure_demo_statements(
    company_id: str,
    *,
    extra_statements: list[dict[str, Any]] | None = None,
    extra_pairs: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """给演示账套补齐银行流水与配对（幂等，可修复历史账套）。

    顺序：先灌种子流水 → 配对 → 再按凭证兜底反推缺口 → 配对 → 校验。
    任何一步对不上都**抛错**而不是静默跳过：过去流水整批丢失就是因为
    异常被 ``except Exception`` 吞掉，接口还照报成功条数。
    """
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        payload = (company.payload if company is not None else None) or {}
        if not payload.get("isDemo"):
            return {"imported": 0, "matched": 0, "derived": 0, "skipped": True}
        owner_user_id = str(company.owner_user_id or "")
        already_done = payload.get(_DEMO_STATEMENTS_FLAG) == DEMO_SEED_VERSION

    statements = list(extra_statements or [])
    pairs = list(extra_pairs or [])
    needs_repair = not already_done
    if needs_repair and not statements:
        # 老账套补修：连「已到账未认领」这两笔一起补，别修完反而少了演示场景
        statements = await _demo_unclaimed_statements(company_id, owner_user_id)

    imported = 0
    matched = 0
    derived_count = 0

    # 只在「首次生成 / 需要补修」时动手：账本已经健康就别再扫描，
    # 否则用户自己在演示账里补的一笔凭证会被自动"补"出一条假流水。
    if needs_repair or statements:
        if statements:
            result = await import_statements_json(company_id, statements)
            imported = int(result.get("imported") or 0) + int(result.get("skipped") or 0)
            if imported != len(statements):
                raise AccountingError(
                    "DEMO_STATEMENTS_FAILED",
                    f"演示账套流水导入失败：{imported}/{len(statements)} 条"
                    + (f"（{result.get('firstError')}）" if result.get("firstError") else ""),
                    500,
                )

        if pairs:
            async with session_scope() as session:
                matched += await _match_statements(session, company_id, pairs)

        # 种子流水配对完成后再兜底：此时 covered 已包含刚配对上的凭证，
        # 只会补出真正缺口（工资/社保/公积金等由服务生成的凭证）。
        derived_statements, derived_pairs = await _derive_missing_statements(
            company_id, owner_user_id
        )
        derived_count = len(derived_statements)
        if derived_statements:
            result = await import_statements_json(company_id, derived_statements)
            written = int(result.get("imported") or 0) + int(result.get("skipped") or 0)
            if written != len(derived_statements):
                raise AccountingError(
                    "DEMO_STATEMENTS_FAILED",
                    f"演示账套补录流水失败：{written}/{len(derived_statements)} 条"
                    + (f"（{result.get('firstError')}）" if result.get("firstError") else ""),
                    500,
                )
            async with session_scope() as session:
                matched += await _match_statements(session, company_id, derived_pairs)

        await _recompute_balances(company_id)

        # 校验：每一笔动过公户的凭证都必须有配对，否则演示账仍是「有账无流水」
        async with session_scope() as session:
            covered = await _covered_entry_ids(session, company_id)
        entries = await list_entries(owner_user_id, company_id)
        uncovered = [
            str(entry.get("id"))
            for entry in entries
            if str(entry.get("id")) not in covered and _bank_effect(entry) != 0
        ]
        if uncovered:
            raise AccountingError(
                "DEMO_STATEMENTS_INCOMPLETE",
                f"演示账套仍有 {len(uncovered)} 笔公户收支没有流水",
                500,
                {"entryIds": uncovered[:10]},
            )

        async with session_scope() as session:
            company = await session.get(Company, company_id)
            if company is not None:
                payload = dict(company.payload or {})
                if payload.get("isDemo"):
                    payload[_DEMO_STATEMENTS_FLAG] = DEMO_SEED_VERSION
                    payload["demoSeedVersion"] = DEMO_SEED_VERSION
                    company.payload = payload

    return {
        "imported": imported,
        "derived": derived_count,
        "matched": matched,
        "skipped": not needs_repair and not statements,
    }


async def create_demo_company(user_id: str, *, months: int = 3) -> dict[str, Any]:
    started = time.monotonic()
    existing = await _find_demo_company(user_id)
    if existing:
        company_id = existing["companyId"]
        try:
            await ensure_demo_invoices(company_id)
        except Exception:  # noqa: BLE001 - 发票样本是增强项，不阻断复用
            logger.warning("演示账套：虚拟发票补齐跳过（company=%s）", company_id, exc_info=True)
        try:
            await ensure_demo_private_expenses(company_id)
        except Exception:  # noqa: BLE001 - 私账样本是增强项，不阻断复用
            logger.warning("演示账套：小私账补齐跳过（company=%s）", company_id, exc_info=True)
        # 历史演示账套可能一条流水都没有（Decimal 绑定失败被静默吞掉），
        # 复用路径必须顺手把它修好，否则用户点进来看到的还是「有账无流水」。
        repaired = await ensure_demo_statements(company_id)
        return {
            "companyId": company_id,
            "name": existing["name"],
            "entries": 0,
            "statements": 0,
            "repairedStatements": repaired.get("derived", 0),
            "durationMs": 0,
            "reused": True,
        }

    now = time.monotonic()
    last = _COOLDOWN.get(user_id)
    if last is not None and now - last < COOLDOWN_SECONDS:
        raise AccountingError(
            "DEMO_COOLDOWN",
            "演示账套刚刚生成过，请稍后再试",
            429,
        )
    _COOLDOWN[user_id] = now

    # 记下当前正在用的账套：演示账套只是样本，不该顶替用户本来在用的那本
    async with session_scope() as session:
        user = await session.get(AuthUser, user_id)
        previous_company_id = (user.current_company_id if user is not None else "") or ""

    entries, statements, pairs, start_date = build_demo_book(months=months)
    company = await create_company(
        user_id,
        DEMO_NAME,
        {
            "isDemo": True,
            "demoSeedVersion": DEMO_SEED_VERSION,
            "taxpayerType": "small_scale",
            "type": "有限责任公司",
            "industry": "软件和信息技术服务业",
            "startDate": start_date.isoformat(),
            "taxId": "91330100MA2DEMO01X",
        },
    )
    company_id = str(company["company_id"])

    # JournalEntry 主键全局唯一，而演示 id 只按日期递增；注册即生成后多个用户
    # 会撞同一批 demo-YYYYMMDD-NNN，这里统一加上账套前缀做隔离。
    id_map: dict[str, str] = {}
    for entry in entries:
        old_id = str(entry["id"])
        id_map[old_id] = f"{company_id}-{old_id}"
        entry["id"] = id_map[old_id]
    pairs = [(occurred, summary, id_map[entry_id]) for occurred, summary, entry_id in pairs]

    for entry in entries:
        # 带显式凭证行的样本必须按「服务端已核定的凭证」入账：否则
        # _deterministic_entry 会按科目映射重算，把「发票已到、钱还没付」的
        # 那笔默默贷记成银行存款——账上说付了，流水里却没有。
        await save_entry(
            entry,
            user_id=user_id,
            company_id=company_id,
            confirm=True,
            trusted_voucher=bool(entry.get("voucherLines")),
        )

    payroll_vouchers = 0
    payroll_month = _month_sequence(date.today(), max(1, min(months, MAX_MONTHS)))[-1]
    payroll_month_text = f"{payroll_month[0]}-{payroll_month[1]:02d}"
    try:
        owner_payroll = await _seed_owner_payroll(company_id, user_id, payroll_month_text)
        salary_desc = f"招行对公 | 代发工资（{DEMO_OWNER_NAME}）"
        social_desc = f"招行对公 | 社保扣款（{DEMO_OWNER_NAME}）"
        housing_desc = f"招行对公 | 公积金扣款（{DEMO_OWNER_NAME}）"
        statements.extend(
            [
                {
                    "transaction_date": owner_payroll["salaryDate"],
                    "description": salary_desc,
                    "amount": -float(owner_payroll["salaryAmount"]),
                    "balance": 0.0,
                },
                {
                    "transaction_date": owner_payroll["socialDate"],
                    "description": social_desc,
                    "amount": -float(owner_payroll["socialAmount"]),
                    "balance": 0.0,
                },
                {
                    "transaction_date": owner_payroll["housingDate"],
                    "description": housing_desc,
                    "amount": -float(owner_payroll["housingAmount"]),
                    "balance": 0.0,
                },
            ]
        )
        pairs.extend(
            [
                (owner_payroll["salaryDate"], salary_desc, owner_payroll["salaryVoucherId"]),
                (owner_payroll["socialDate"], social_desc, owner_payroll["socialVoucherId"]),
                (owner_payroll["housingDate"], housing_desc, owner_payroll["housingVoucherId"]),
            ]
        )
        _apply_running_balance(statements)
        payroll_vouchers = int(owner_payroll["voucherCount"])
    except Exception:  # noqa: BLE001 - 工资样本是增强项，失败不该让整本演示账失败
        logger.warning("演示账套：老板工资/社保/公积金生成跳过（company=%s）", company_id, exc_info=True)

    try:
        await ensure_demo_invoices(company_id)
    except Exception:  # noqa: BLE001 - 发票样本是增强项，失败不该让整本演示账失败
        logger.warning("演示账套：虚拟发票生成跳过（company=%s）", company_id, exc_info=True)

    try:
        await ensure_demo_private_expenses(company_id)
    except Exception:  # noqa: BLE001 - 私账样本是增强项，失败不该让整本演示账失败
        logger.warning("演示账套：小私账生成跳过（company=%s）", company_id, exc_info=True)

    try:
        # 流水 → 配对 → 校验一条龙：任何缺口都抛错，不再静默产出一本「有账无流水」的演示账
        statement_result = await ensure_demo_statements(
            company_id, extra_statements=statements, extra_pairs=pairs
        )

        # 带上默认税费种认定：演示账套要能展示「已就绪」与征期日历，否则用户看到
        # 一本做全了事的演示账还差一项，反而更困惑。
        try:
            await tax_registration_service.import_registrations(
                company_id,
                user_id,
                tax_registration_service.build_template("ltd"),
                mode="merge",
                source="template",
            )
        except Exception:  # noqa: BLE001 - 认定表是增强项，失败不该清掉整本演示账
            logger.warning("演示账套：税费种认定导入跳过（company=%s）", company_id, exc_info=True)

        return {
            "companyId": company_id,
            "name": DEMO_NAME,
            "entries": len(entries) + payroll_vouchers,
            "statements": int(statement_result.get("imported") or 0)
            + int(statement_result.get("derived") or 0),
            "durationMs": int((time.monotonic() - started) * 1000),
            "reused": False,
        }
    finally:
        # save_entry 的 ensure_tenant 会为首次出现的账号写 current_company_id，
        # 这里显式恢复，避免用户生成演示账套后被静默切走（生成失败时同样要恢复）
        if previous_company_id and previous_company_id != company_id:
            await set_user_current_company(user_id, previous_company_id)


async def delete_demo_company(user_id: str, company_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        payload = (company.payload if company is not None else None) or {}
        if (
            company is None
            or company.owner_user_id != user_id
            or not payload.get("isDemo")
        ):
            raise AccountingError(NOT_FOUND, "演示账套不存在或无权删除", 404)
        name = str(payload.get("name") or DEMO_NAME)
        user = await session.get(AuthUser, user_id)
        is_current = user is not None and user.current_company_id == company_id
        others = [
            row
            for row in (
                await session.execute(
                    select(Company).where(Company.owner_user_id == user_id)
                )
            ).scalars().all()
            if row.company_id != company_id and not (row.payload or {}).get("isDemo")
        ]

    if not is_current:
        await purge_company(user_id, company_id, name)
        return {"deleted": True, "companyId": company_id, "nextCompanyId": None}

    # 删掉当前演示账套后必须仍有账可用：没有真实账套时补一本空白正式账套，
    # 而不是把用户留在无账套状态（登录签发不出带 company_id 的令牌）。
    if others:
        next_company_id = others[0].company_id
    else:
        replacement = await create_company(user_id, EMPTY_COMPANY_NAME)
        next_company_id = str(replacement["company_id"])
    # purge_company 拒绝删除「当前公司」，所以必须先切走再删
    await set_user_current_company(user_id, next_company_id)
    await purge_company(user_id, company_id, name)
    return {
        "deleted": True,
        "companyId": company_id,
        "nextCompanyId": next_company_id,
    }
