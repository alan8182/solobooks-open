# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""暂估入账：月末票未到、业务已发生的成本费用。

标准三段式：
  ① 月末暂估   借 成本费用/存货（不含税） 贷 应付账款
  ② 次月初红冲 原凭证的反向分录（复用 reverse_journal）
  ③ 票到正式入账 借 成本费用 贷 其他应付款-发票待付款

三条硬约束：
- **暂估不确认进项税额**。无票不得抵扣，虚抵是税务风险；本产品面向小规模纳税人，
  价税合计直接进成本，不做进项拆分。
- **冲回不新造机制**：复用 `reverse_journal`，`uq_single_reversal` 保证一对一；
  因此「未冲回暂估」= `entry_type='accrual' AND status='posted'`。
- **科目不做子级**：`chart_is_leaf` 规定 4 位总账一律末级，且资产负债表用精确码
  求和 `2202`，加 `2202001` 会让暂估应付款从报表上静默消失。暂估身份由
  `entry_type='accrual'` + metadata 标识，不靠科目层级区分。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import JournalEntry
from services.accounting_core_service import (
    create_entry,
    get_journal,
    reverse_journal,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

ACCRUAL_ENTRY_TYPE = "accrual"
ACCRUAL_CREDIT_CODE = "2202"
ACCRUAL_CREDIT_NAME = "应付账款"
# 票到后按普通发票挂账处理，贷方沿用现有的「其他应付款-发票待付款」
INVOICE_PAYABLE_CODE = "2241"
INVOICE_PAYABLE_NAME = "其他应付款-发票待付款"

ACCRUAL_BASIS = {"contract", "receipt", "estimate"}


def _today() -> date:
    return datetime.now(SHANGHAI).date()


def _parse_date(value: Any, *, field: str) -> date:
    raw = str(value or "").strip()
    if not raw:
        raise AccountingError("INVALID_ACCOUNTING_DATE", f"{field} 不能为空")
    if len(raw) == 7:
        raw = f"{raw}-01"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise AccountingError("INVALID_ACCOUNTING_DATE", f"{field} 格式必须为 YYYY-MM-DD") from exc


def _assert_accrual_debit_account(code: str) -> None:
    """暂估借方只能是成本费用或存货类。

    货币资金、往来、税费这些科目做暂估没有业务含义（暂估的是"货到了票没到"），
    放进来基本是误操作，直接在入口拦掉。
    """
    if code.startswith(("1001", "1002", "1012", "1122", "1123", "2202", "2241", "2221")):
        raise AccountingError(
            "ACCRUAL_ACCOUNT_INVALID",
            "暂估的借方必须是成本费用或存货类科目，不能是货币资金、往来或税费科目",
            422,
            {"accountCode": code},
        )
    if not (code.startswith("1") or code.startswith("5") or code.startswith("4301")):
        raise AccountingError(
            "ACCRUAL_ACCOUNT_INVALID",
            "暂估的借方必须是成本费用或存货类科目",
            422,
            {"accountCode": code},
        )


async def create_accrual(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> JournalEntry:
    amount = money(payload.get("amount") or ZERO)
    if amount <= ZERO:
        raise AccountingError("ZERO_AMOUNT", "暂估金额必须大于零")
    vendor = str(payload.get("vendor") or "").strip()
    if not vendor:
        raise AccountingError("ACCRUAL_VENDOR_REQUIRED", "请填写供应商或对方名称", 422)
    account_code = str(payload.get("accountCode") or "").strip()
    account_name = str(payload.get("accountName") or "").strip()
    category = str(payload.get("category") or "").strip()
    if not account_code:
        if not category:
            raise AccountingError(
                "ACCRUAL_ACCOUNT_REQUIRED", "请选择暂估对应的成本费用科目或分类", 422
            )
        # 传分类就够了：复用记账同一套分类→科目映射，避免前端各写一份
        from services.account_mapper import get_voucher_hint

        hint = get_voucher_hint("expense", category, amount)
        debit_lines = hint.get("debit") or []
        line = next(
            (
                item
                for item in debit_lines
                if not str(item.get("accountCode") or "").startswith(("1001", "1002", "1012"))
            ),
            debit_lines[0] if debit_lines else None,
        )
        if line is None:
            raise AccountingError(
                "ACCRUAL_ACCOUNT_REQUIRED", "该分类没有对应的成本费用科目", 422, {"category": category}
            )
        account_code = str(line.get("accountCode") or "")
        account_name = account_name or str(line.get("accountName") or category)
    _assert_accrual_debit_account(account_code)

    business_date = _parse_date(
        payload.get("businessDate") or _today().isoformat(), field="业务日期"
    )
    expected = _parse_date(
        payload.get("expectedInvoiceDate") or "", field="预计取票日期"
    )
    basis = str(payload.get("basis") or "estimate").strip()
    if basis not in ACCRUAL_BASIS:
        raise AccountingError("ACCRUAL_BASIS_INVALID", "暂估依据只能是合同/入库单/估算", 422)

    category = str(payload.get("category") or account_name or "暂估成本").strip()
    summary = f"暂估：{vendor}（{account_name or category}）"
    entry = await create_entry(
        session,
        {
            "direction": "expense",
            "amount": money_text(amount),
            "title": summary,
            "category": category,
            "occurDate": business_date.isoformat(),
            "sourceType": "accrual",
            "entryType": ACCRUAL_ENTRY_TYPE,
            "payer": "company_account",
            "dedupFingerprint": str(payload.get("dedupFingerprint") or "").strip() or None,
            # 暂估身份与取票跟踪
            "isAccrual": True,
            "accrualVendor": vendor,
            "accrualBasis": basis,
            "expectedInvoiceDate": expected.isoformat(),
            "accrualNotes": str(payload.get("notes") or "").strip(),
            "voucherLines": {
                "debit": [
                    {
                        "accountCode": account_code,
                        "accountName": account_name or category,
                        "amount": money_text(amount),
                    }
                ],
                "credit": [
                    {
                        "accountCode": ACCRUAL_CREDIT_CODE,
                        "accountName": ACCRUAL_CREDIT_NAME,
                        "amount": money_text(amount),
                    }
                ],
            },
        },
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )
    return entry


def next_month_first(value: date) -> date:
    return date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)


async def reverse_accrual(
    session: AsyncSession,
    entry_id: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    reason: str | None = None,
    reversal_date: Any = None,
) -> JournalEntry:
    """红冲暂估。默认落在暂估期间的下一个月 1 日（月末暂估、次月初冲回）。"""
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.entry_type != ACCRUAL_ENTRY_TYPE:
        raise AccountingError("NOT_ACCRUAL_ENTRY", "只有暂估凭证可以执行冲回", 409)
    if entry.status != "posted":
        raise AccountingError("ACCRUAL_NOT_REVERSIBLE", "该暂估已冲回或尚未过账", 409)
    target = reversal_date if reversal_date not in (None, "") else next_month_first(entry.period_start)
    return await reverse_journal(
        session,
        entry_id,
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        reason=reason or "暂估次月红冲",
        reversal_date=target,
    )


async def settle_accrual(
    session: AsyncSession,
    entry_id: str,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """票到冲抵：同一事务里红冲暂估 + 按发票金额正式入账。

    两步必须原子——否则中途失败会留下「暂估已冲、发票没入」的缺口。
    金额与暂估有差额时，差额自然进当期成本（小企业简化口径，不单独做差额调整）。
    """
    accrual = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if accrual.entry_type != ACCRUAL_ENTRY_TYPE:
        raise AccountingError("NOT_ACCRUAL_ENTRY", "只有暂估凭证可以冲抵发票", 409)
    if accrual.status != "posted":
        raise AccountingError("ACCRUAL_NOT_REVERSIBLE", "该暂估已冲回或尚未过账", 409)

    from services.accounting_core_service import _entry_lines

    lines = await _entry_lines(session, accrual.id)
    debit_lines = [line for line in lines if line.direction == "debit"]
    if not debit_lines:
        raise AccountingError("ACCRUAL_LINES_MISSING", "暂估凭证缺少借方分录", 409)
    account_code = debit_lines[0].account_code
    account_name = debit_lines[0].account_name

    reversal = await reverse_accrual(
        session,
        entry_id,
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:reverse",
        reason=str(payload.get("reason") or "票到冲抵暂估"),
    )

    amount = money(payload.get("amount") or (accrual.metadata_json or {}).get("amount") or ZERO)
    if amount <= ZERO:
        raise AccountingError("ZERO_AMOUNT", "发票金额必须大于零")
    occur = _parse_date(payload.get("occurDate") or _today().isoformat(), field="发票日期")
    title = str(payload.get("title") or "").strip() or f"发票入账：{accrual.metadata_json.get('accrualVendor') or ''}".strip("：")
    invoice_number = str(payload.get("invoiceNumber") or "").strip()

    invoice_entry = await create_entry(
        session,
        {
            "direction": "expense",
            "amount": money_text(amount),
            "title": title,
            "category": str(payload.get("category") or (accrual.metadata_json or {}).get("category") or account_name),
            "occurDate": occur.isoformat(),
            "sourceType": "invoice",
            "dedupFingerprint": invoice_number or None,
            "invoiceNumber": invoice_number or None,
            "billingStatus": "has_invoice",
            "payer": "company_account",
            "settlesAccrualId": accrual.id,
            "voucherLines": {
                "debit": [
                    {
                        "accountCode": account_code,
                        "accountName": account_name,
                        "amount": money_text(amount),
                    }
                ],
                "credit": [
                    {
                        "accountCode": INVOICE_PAYABLE_CODE,
                        "accountName": INVOICE_PAYABLE_NAME,
                        "amount": money_text(amount),
                    }
                ],
            },
        },
        company_id=company_id,
        user_id=user_id,
        request_id=f"{request_id}:invoice",
        confirm=True,
    )
    return {
        "accrualEntryId": accrual.id,
        "reversalEntryId": reversal.id,
        "invoiceEntryId": invoice_entry.id,
        "delta": money_text(amount - money((accrual.metadata_json or {}).get("amount") or ZERO)),
    }


async def list_pending_accruals(
    session: AsyncSession,
    company_id: str,
    *,
    before_period: date | None = None,
    period: date | None = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    """未冲回的暂估（冲回后原凭证 status 变 reversed，所以只看 posted）。"""
    condition = [
        JournalEntry.company_id == company_id,
        JournalEntry.entry_type == ACCRUAL_ENTRY_TYPE,
        JournalEntry.status == "posted",
    ]
    if before_period is not None:
        condition.append(JournalEntry.period_start < before_period)
    if period is not None:
        condition.append(JournalEntry.period_start == period)
    entries = (
        await session.execute(
            select(JournalEntry)
            .where(*condition)
            .order_by(JournalEntry.period_start, JournalEntry.occurred_on)
        )
    ).scalars().all()
    records = [_serialize_accrual(entry) for entry in entries]
    total = sum((money(record["amount"]) for record in records), ZERO)
    return {
        "count": len(records),
        "amount": money_text(total),
        "records": records[:limit] if limit else records,
    }


def _serialize_accrual(entry: JournalEntry) -> dict[str, Any]:
    metadata = entry.metadata_json or {}
    return {
        "entryId": entry.id,
        "voucherRef": entry.voucher_no or "",
        "summary": entry.summary,
        "vendor": metadata.get("accrualVendor") or "",
        "amount": money_text(money(metadata.get("amount") or ZERO)),
        "businessDate": entry.occurred_on.isoformat(),
        "postingPeriod": entry.period_start.strftime("%Y-%m"),
        "expectedInvoiceDate": metadata.get("expectedInvoiceDate") or "",
        "basis": metadata.get("accrualBasis") or "estimate",
        "entryStatus": entry.status,
        "overdueDays": _overdue_days(metadata.get("expectedInvoiceDate")),
    }


def _overdue_days(raw: Any) -> int:
    if not raw:
        return 0
    try:
        expected = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return 0
    return max((_today() - expected).days, 0)


async def accrual_risk_report(
    session: AsyncSession,
    company_id: str,
    year: int,
) -> dict[str, Any]:
    """汇算清缴前用：暂估已入账但尚未取得发票的清单。

    依据《企业所得税税前扣除凭证管理办法》（2018 年 28 号公告）第六条：
    当年度汇算清缴期结束前应取得发票，否则不得税前扣除；以后年度取得可追补 5 年。
    """
    payload = await list_pending_accruals(session, company_id, limit=None)
    records = [record for record in payload["records"] if int(record["businessDate"][:4]) <= year]
    total = sum((money(record["amount"]) for record in records), ZERO)
    return {
        "year": year,
        "count": len(records),
        "amount": money_text(total),
        "overdueCount": sum(1 for record in records if record["overdueDays"] > 0),
        "records": records,
    }


ACCRUAL_CSV_HEADERS = ["暂估日期", "记账期间", "供应商", "金额", "预计取票日", "逾期天数", "凭证号", "摘要"]


def accrual_risk_csv(payload: dict[str, Any]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"{payload.get('year')} 年度暂估未取票清单"])
    writer.writerow([
        "政策依据：国家税务总局公告 2018 年第 28 号第六条——当年度汇算清缴期结束前应取得"
        "税前扣除凭证，未取得的需纳税调增；以后年度取得可追补至发生年度（5 年内）"
    ])
    writer.writerow([
        f"合计 {payload.get('count', 0)} 笔，金额 {payload.get('amount', '0.00')} 元，"
        f"其中已过预计取票日 {payload.get('overdueCount', 0)} 笔"
    ])
    writer.writerow([])
    writer.writerow(ACCRUAL_CSV_HEADERS)
    for record in payload.get("records") or []:
        writer.writerow([
            record.get("businessDate", ""),
            record.get("postingPeriod", ""),
            record.get("vendor", ""),
            record.get("amount", ""),
            record.get("expectedInvoiceDate", ""),
            record.get("overdueDays", 0),
            record.get("voucherRef", ""),
            record.get("summary", ""),
        ])
    return buffer.getvalue()
