# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票台账：把发票从 entries.payload 的影子数据提升为一等实体。

设计要点（对齐 P0 计划 WP1）：
- 写路径双写：发票导入成功后同批写入 ``invoices``，``entry_id`` 关联凭证；
  唯一约束 ``(company_id, dedup_fingerprint)`` 让重跑天然幂等，不必先查后写。
- 去重换表：解析阶段由 ``existing_invoice_numbers`` 走索引查询，
  不再 ``json_extract(payload, '$.invoiceNumber')`` 全表扫。
- 存量回填：老账套的发票只在 payload 里。首次访问台账时按公司幂等回填一次，
  回填完成在公司 payload 打 ``invoice_backfill_done`` 标记，之后不再扫。
- 读侧不做 UNION：回填是幂等的且在公司粒度上一次性完成，
  双读兼容层只会长期存在、掩盖漂移，不如让表成为唯一事实源。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import func, or_, select

from core.money import ZERO, money, money_text
from db.models import Invoice, JournalEntry, LegacyEntryProjection
from db.session import session_scope
from services.database import get_conn

INVOICE_DIRECTIONS = ("income", "expense")
INVOICE_STATUSES = {"normal", "red", "reversed"}
_BACKFILL_FLAG = "invoice_backfill_done"
logger = logging.getLogger(__name__)


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(value: Any, limit: int = 255) -> str:
    return str(value or "").strip()[:limit]


def _money(value: Any) -> Decimal:
    try:
        return money(str(value).replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return ZERO


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "income" if text == "income" else "expense"


def build_fingerprint(
    *,
    direction: str,
    invoice_number: str,
    invoice_date: date | None,
    counterparty: str,
    amount: Decimal,
) -> str:
    """去重指纹：发票号是最强业务键，缺失时退化为 日期+对方+金额+方向。"""
    number = _text(invoice_number, 64)
    if number:
        return number
    return ":".join(
        [
            "inv",
            invoice_date.isoformat() if invoice_date else "",
            _text(counterparty, 128).replace("（", "(").replace("）", ")").lower(),
            money_text(amount),
            direction,
        ]
    )


def invoice_fields_from_entry(payload: dict[str, Any], *, direction: str | None = None) -> dict[str, Any]:
    """从凭证 payload 提取发票字段（导入与回填共用同一套口径）。"""
    resolved_direction = _normalize_direction(direction or payload.get("direction"))
    amount_incl = _money(payload.get("amount"))
    tax_amount = _money(payload.get("tax_amount") if payload.get("tax_amount") not in (None, "") else payload.get("vatAmount"))
    amount_excl = payload.get("amountExcludingTax")
    if amount_excl in (None, ""):
        amount_excl = amount_incl - tax_amount
    invoice_date = _as_date(payload.get("invoice_date") or payload.get("occurDate") or payload.get("date"))
    seller = _text(payload.get("seller_name") or payload.get("merchant"))
    buyer = _text(payload.get("buyer_name"))
    counterparty = buyer if resolved_direction == "income" else seller
    return {
        "direction": resolved_direction,
        "invoice_number": _text(payload.get("invoiceNumber") or payload.get("invoice_number"), 64),
        "invoice_code": _text(payload.get("invoice_code"), 64),
        "invoice_type": _text(payload.get("invoice_type") or payload.get("invoiceType"), 64),
        # 台账状态是系统枚举（ck_invoice_status）；Excel 带来的"正常/红冲"等原值
        # 只在命中枚举时透传，否则一律落 normal，红字口径由金额符号表达
        "invoice_status": _text(payload.get("invoice_status"), 32) if _text(payload.get("invoice_status"), 32) in INVOICE_STATUSES else "normal",
        "invoice_date": invoice_date,
        "buyer_name": buyer,
        "buyer_tax_id": _text(payload.get("buyer_tax_id") or payload.get("buyerTaxId"), 64),
        "seller_name": seller,
        "seller_tax_id": _text(payload.get("seller_tax_id") or payload.get("sellerTaxId"), 64),
        "amount_excl_tax": money(_money(amount_excl)),
        "tax_amount": money(tax_amount),
        "amount_incl_tax": money(amount_incl),
        "tax_rate": _text(payload.get("tax_rate") or payload.get("vatRate"), 16),
        "item_name": _text(payload.get("item_name")),
        "remark": _text(payload.get("remark"), 1000),
        "dedup_fingerprint": build_fingerprint(
            direction=resolved_direction,
            invoice_number=_text(payload.get("invoiceNumber") or payload.get("invoice_number"), 64),
            invoice_date=invoice_date,
            counterparty=counterparty,
            amount=amount_incl,
        ),
    }


def _apply_fields(invoice: Invoice, fields: dict[str, Any], *, entry_id: str | None, partner_id: str | None) -> None:
    for key, value in fields.items():
        setattr(invoice, key, value)
    if entry_id:
        invoice.entry_id = entry_id
    if partner_id:
        invoice.partner_id = partner_id
    invoice.updated_at = datetime.now(timezone.utc)


async def record_invoices_bulk(
    company_id: str,
    records: Iterable[tuple[str | None, dict[str, Any], str | None]],
) -> int:
    """批量写发票主数据：``(entry_id, entry_payload, partner_id)``。

    单 session、按指纹幂等——已存在的行只补 entry_id/partner_id，不覆盖金额，
    避免回填/重导把人工修正过的台账数据冲掉。
    """
    prepared: list[tuple[str | None, dict[str, Any], str | None]] = []
    for entry_id, payload, partner_id in records:
        if not isinstance(payload, dict):
            continue
        fields = invoice_fields_from_entry(payload)
        if not fields["invoice_number"] and fields["amount_incl_tax"] == ZERO:
            continue
        prepared.append((entry_id, fields, partner_id))
    if not prepared:
        return 0

    written = 0
    async with session_scope() as session:
        fingerprints = {fields["dedup_fingerprint"] for _, fields, _ in prepared}
        existing = {
            row.dedup_fingerprint: row
            for row in (
                await session.execute(
                    select(Invoice).where(
                        Invoice.company_id == company_id,
                        Invoice.dedup_fingerprint.in_(fingerprints),
                    )
                )
            ).scalars().all()
        }
        for entry_id, fields, partner_id in prepared:
            invoice = existing.get(fields["dedup_fingerprint"])
            if invoice is None:
                invoice = Invoice(id=f"inv-{uuid4().hex[:16]}", company_id=company_id)
                _apply_fields(invoice, fields, entry_id=entry_id, partner_id=partner_id)
                session.add(invoice)
                existing[fields["dedup_fingerprint"]] = invoice
            else:
                if entry_id and not invoice.entry_id:
                    invoice.entry_id = entry_id
                if partner_id and not invoice.partner_id:
                    invoice.partner_id = partner_id
            written += 1
        await session.flush()
    return written


async def existing_invoice_numbers(company_id: str) -> set[str]:
    """去重预检：只查发票号列（走 (company_id, invoice_number) 索引）。"""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice.invoice_number).where(
                    Invoice.company_id == company_id,
                    Invoice.invoice_number != "",
                )
            )
        ).scalars().all()
    return {str(row).strip() for row in rows if row}


async def existing_fingerprints(company_id: str) -> set[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice.dedup_fingerprint).where(Invoice.company_id == company_id)
            )
        ).scalars().all()
    return {str(row) for row in rows if row}


async def backfill_invoices(company_id: str, *, force: bool = False) -> dict[str, Any]:
    """把存量 payload 里的发票回填进 invoices 表（幂等）。"""
    async with session_scope() as session:
        known = {
            str(value)
            for value in (
                await session.execute(
                    select(Invoice.dedup_fingerprint).where(Invoice.company_id == company_id)
                )
            ).scalars().all()
            if value
        }
        projections = (
            await session.execute(
                select(LegacyEntryProjection).where(LegacyEntryProjection.company_id == company_id)
            )
        ).scalars().all()

    candidates: list[tuple[str, dict[str, Any], None]] = []
    for projection in projections:
        try:
            payload = json.loads(projection.payload) if isinstance(projection.payload, str) else (projection.payload or {})
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        # 只回填"确实是发票"的行：有发票号，或来源是发票导入
        if not (payload.get("invoiceNumber") or payload.get("source") == "invoice_import"):
            continue
        fields = invoice_fields_from_entry(payload)
        if fields["dedup_fingerprint"] in known:
            continue
        known.add(fields["dedup_fingerprint"])
        candidates.append((projection.id, payload, None))

    created = await record_invoices_bulk(company_id, candidates)
    if not force:
        await _mark_backfilled(company_id)
    return {"scanned": len(projections), "created": created}


async def _mark_backfilled(company_id: str) -> None:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        payload = row["payload"]
        data = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        if data.get(_BACKFILL_FLAG):
            return
        data[_BACKFILL_FLAG] = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "UPDATE auth_companies SET payload = ? WHERE company_id = ?",
            (json.dumps(data, ensure_ascii=False), company_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def ensure_backfilled(company_id: str) -> None:
    """首次访问台账时补一次回填；已打标则零成本跳过。"""
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    if row:
        payload = row["payload"]
        data = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        if not data.get(_BACKFILL_FLAG):
            await backfill_invoices(company_id)
    else:
        await backfill_invoices(company_id)
    try:
        from services.demo_book_service import ensure_demo_invoices

        await ensure_demo_invoices(company_id)
    except Exception:  # noqa: BLE001 - 演示样本增强失败不影响真实台账
        logger.warning("演示账套：发票补齐失败（company=%s）", company_id, exc_info=True)


def _serialize(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "entryId": invoice.entry_id,
        "direction": invoice.direction,
        "invoiceNumber": invoice.invoice_number,
        "invoiceCode": invoice.invoice_code,
        "invoiceType": invoice.invoice_type,
        "invoiceStatus": invoice.invoice_status,
        "invoiceDate": invoice.invoice_date.isoformat() if invoice.invoice_date else "",
        "buyerName": invoice.buyer_name,
        "buyerTaxId": invoice.buyer_tax_id,
        "sellerName": invoice.seller_name,
        "sellerTaxId": invoice.seller_tax_id,
        "amountExclTax": money_text(invoice.amount_excl_tax),
        "taxAmount": money_text(invoice.tax_amount),
        "amountInclTax": money_text(invoice.amount_incl_tax),
        "taxRate": invoice.tax_rate,
        "itemName": invoice.item_name,
        "remark": invoice.remark,
        "partnerId": invoice.partner_id,
        "source": invoice.source,
    }


def _month_bounds(month_from: str | None, month_to: str | None) -> tuple[date | None, date | None]:
    start = None
    end = None
    if month_from:
        try:
            year, month = (int(part) for part in month_from[:7].split("-", 1))
            start = date(year, month, 1)
        except (TypeError, ValueError):
            start = None
    if month_to:
        try:
            year, month = (int(part) for part in month_to[:7].split("-", 1))
            import calendar

            end = date(year, month, calendar.monthrange(year, month)[1])
        except (TypeError, ValueError):
            end = None
    return start, end


async def list_invoices(
    company_id: str,
    *,
    month_from: str | None = None,
    month_to: str | None = None,
    direction: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    posting: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    await ensure_backfilled(company_id)
    start, end = _month_bounds(month_from, month_to)
    conditions = [Invoice.company_id == company_id]
    if start:
        conditions.append(Invoice.invoice_date >= start)
    if end:
        conditions.append(Invoice.invoice_date <= end)
    if direction in INVOICE_DIRECTIONS:
        conditions.append(Invoice.direction == direction)
    if status:
        conditions.append(Invoice.invoice_status == status)
    if posting == "pending":
        # 待过账：凭证还是草稿/待复核，或台账行尚未关联到凭证
        sub = select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.status.in_(("draft", "pending_review")),
        )
        conditions.append(or_(Invoice.entry_id.is_(None), Invoice.entry_id.in_(sub)))
    elif posting == "posted":
        sub = select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.status == "posted",
        )
        conditions.append(Invoice.entry_id.in_(sub))
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(
                Invoice.invoice_number.like(like),
                Invoice.seller_name.like(like),
                Invoice.buyer_name.like(like),
                Invoice.item_name.like(like),
            )
        )

    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 20)))
    async with session_scope() as session:
        total = (
            await session.execute(select(func.count()).select_from(Invoice).where(*conditions))
        ).scalar_one()
        rows = (
            await session.execute(
                select(Invoice, JournalEntry.voucher_no)
                .outerjoin(JournalEntry, JournalEntry.id == Invoice.entry_id)
                .where(*conditions)
                .order_by(
                    Invoice.invoice_date.is_(None),
                    Invoice.invoice_date.desc(),
                    Invoice.created_at.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    return {
        "items": [{**_serialize(invoice), "voucherRef": voucher_no or ""} for invoice, voucher_no in rows],
        "total": int(total or 0),
        "page": page,
        "pageSize": page_size,
    }


async def invoice_summary(
    company_id: str,
    *,
    month_from: str | None = None,
    month_to: str | None = None,
) -> dict[str, Any]:
    """按月汇总进/销项（金额在 Python 侧聚合，避免各数据库日期函数差异）。"""
    await ensure_backfilled(company_id)
    start, end = _month_bounds(month_from, month_to)
    conditions = [Invoice.company_id == company_id]
    if start:
        conditions.append(Invoice.invoice_date >= start)
    if end:
        conditions.append(Invoice.invoice_date <= end)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Invoice.direction,
                    Invoice.invoice_date,
                    Invoice.amount_incl_tax,
                    Invoice.tax_amount,
                ).where(*conditions)
            )
        ).all()

    months: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "month": "",
            "incomeCount": 0,
            "incomeAmount": ZERO,
            "incomeTax": ZERO,
            "expenseCount": 0,
            "expenseAmount": ZERO,
            "expenseTax": ZERO,
        }
    )
    totals = {
        "incomeCount": 0,
        "incomeAmount": ZERO,
        "incomeTax": ZERO,
        "expenseCount": 0,
        "expenseAmount": ZERO,
        "expenseTax": ZERO,
    }
    for direction, invoice_date, amount, tax in rows:
        if invoice_date is None:
            continue
        key = invoice_date.strftime("%Y-%m")
        bucket = months[key]
        bucket["month"] = key
        prefix = "income" if direction == "income" else "expense"
        bucket[f"{prefix}Count"] += 1
        bucket[f"{prefix}Amount"] += money(amount)
        bucket[f"{prefix}Tax"] += money(tax)
        totals[f"{prefix}Count"] += 1
        totals[f"{prefix}Amount"] += money(amount)
        totals[f"{prefix}Tax"] += money(tax)

    output = []
    for key in sorted(months, reverse=True):
        bucket = months[key]
        output.append(
            {
                **{name: (money_text(value) if isinstance(value, Decimal) else value) for name, value in bucket.items()}
            }
        )
    return {
        "months": output,
        "totals": {
            **{name: (money_text(value) if isinstance(value, Decimal) else value) for name, value in totals.items()},
            "netTax": money_text(totals["incomeTax"] - totals["expenseTax"]),
        },
    }


async def attach_invoice_partners(company_id: str, mapping: dict[str, str]) -> None:
    """发票导入后回填往来单位：``{发票号: partner_id}``。"""
    if not mapping:
        return
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice).where(
                    Invoice.company_id == company_id,
                    Invoice.invoice_number.in_(list(mapping.keys())),
                )
            )
        ).scalars().all()
        for invoice in rows:
            partner_id = mapping.get(invoice.invoice_number)
            if partner_id:
                invoice.partner_id = partner_id
        await session.flush()


async def voucher_ids_for_invoices(company_id: str) -> dict[str, str]:
    """发票号 → 凭证号，台账行点击跳转用。"""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice.invoice_number, JournalEntry.voucher_no)
                .join(JournalEntry, JournalEntry.id == Invoice.entry_id)
                .where(Invoice.company_id == company_id)
            )
        ).all()
    return {str(number): str(voucher or "") for number, voucher in rows if number}
