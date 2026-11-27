# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册数据构建（Phase 2 单据整理）：按月装订已过账记账凭证。

口径：
- 只装订**已过账**凭证（posted/reversed）——草稿未定版不入册（与账簿序时账
  同一 POSTED_STATUSES 口径）；
- 期间按**记账期间**（period_start），与账簿/关账口径一致；
- 「附单据 X 张」来自真实附件聚合（invoices→active attachments，A3），
  不用 billingStatus 推导；
- 原件归档目录是证据索引（文件名/sha256），不打印原件内容——XML 不可打印，
  系统内可逐件校验下载（Phase 1 attachments）。
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, select

from core.money import money, money_text
from core.money_upper import amount_to_chinese_capital
from db.models import AccountingPeriod, Attachment, Company, Invoice, JournalEntry, JournalLine
from db.session import session_scope
from services.attachment_storage_service import (
    IMAGE_EXTS,
    attachment_counts_by_entries,
    resolve_entry_attachments,
)
from services.ledger_book_service import POSTED_STATUSES, content_hash

MAX_VOUCHERS_PER_BOOK = 200
# 单册页数上限的另一道闸：原件目录分页条数
MAX_ORIGINAL_ROWS = 600

_VOUCHER_PAGE_LINES = 6  # 与前端 VoucherSheet 展示行数一致（不足补空行的口径）


class VoucherBookTooLarge(ValueError):
    """凭证数超单册上限（413），提示等待分册能力。"""


def _auxiliary_text(auxiliary: dict[str, Any] | None) -> str:
    if not auxiliary:
        return ""
    parts: list[str] = []
    for key, label in (
        ("partner_name", "往来"),
        ("employee_name", "职员"),
        ("department_name", "部门"),
        ("project_name", "项目"),
        ("rd_project_name", "研发项目"),
    ):
        value = str(auxiliary.get(key) or "").strip()
        if value:
            parts.append(f"{label}:{value}")
    return "；".join(parts)


def _voucher_seq(voucher_no: str | None) -> str:
    """「记字第X号」：取凭证号尾号数字（与前端 VoucherSheet 同口径）。"""
    matched = re.search(r"(\d+)\s*$", str(voucher_no or ""))
    return str(int(matched.group(1))) if matched else (voucher_no or "—")


def _paginate(vouchers: list[dict[str, Any]], originals: list[dict[str, Any]]) -> dict[str, Any]:
    """分页真值：封面 1 页 + 每凭证 1 页 + 每份 XML 原件 1 张复现页 + 目录页数 + 签章 1 页。

    渲染器按同一公式出页，实际页数 ≠ totalPages 拒绝出册（同账簿防线）。
    """
    voucher_pages = sum(1 + len(v.get("attachments", [])) for v in vouchers)
    original_pages = max(1, -(-len(originals) // 12)) if originals else 1
    return {
        "sections": [
            {"key": "cover", "title": "凭证册封面", "pages": 1},
            {"key": "vouchers", "title": "记账凭证", "pages": voucher_pages},
            {"key": "originals", "title": "原件归档目录", "pages": original_pages},
            {"key": "signoff", "title": "装订签章", "pages": 1},
        ],
        "totalPages": 1 + voucher_pages + original_pages + 1,
        "originalPages": original_pages,
    }


async def _period_status(company_id: str, period_start: date) -> str:
    async with session_scope() as session:
        return str(
            await session.scalar(
                select(AccountingPeriod.status).where(
                    AccountingPeriod.company_id == company_id,
                    AccountingPeriod.period_start == period_start,
                )
            )
            or "open"
        )


async def build_voucher_book(company_id: str, *, month: str) -> dict[str, Any]:
    """按月构建凭证册 payload（与账簿 build_ledger_book 同层的权威数据源）。"""
    try:
        year, mon = (int(part) for part in month[:7].split("-", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"期间格式无效：{month}") from exc
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    period_end = date(year, mon, calendar.monthrange(year, mon)[1])

    by_entry: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    entries_meta: dict[str, JournalEntry] = {}
    lines_by_entry: dict[str, list[Any]] = defaultdict(list)

    async with session_scope() as session:
        voucher_count = await session.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.period_start >= start,
                JournalEntry.period_start < end,
            )
        )
        if voucher_count and voucher_count > MAX_VOUCHERS_PER_BOOK:
            raise VoucherBookTooLarge(
                f"本月已过账凭证 {voucher_count} 张，超过单册上限 {MAX_VOUCHERS_PER_BOOK} 张"
            )
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.period_start >= start,
                    JournalEntry.period_start < end,
                    JournalLine.company_id == company_id,
                )
                .order_by(
                    JournalEntry.period_start,
                    JournalEntry.voucher_no,
                    JournalEntry.created_at,
                    JournalLine.line_no,
                )
            )
        ).all()
        company = await session.get(Company, company_id)
        company_name = str((company.payload or {}).get("name") or "") if company else ""

    for entry, line in rows:
        entries_meta[entry.id] = entry
        lines_by_entry[entry.id].append(line)
        if entry.id not in by_entry:
            order.append(entry.id)
            by_entry[entry.id] = {
                "entryId": entry.id,
                "voucherNo": entry.voucher_no or "",
                "voucherSeq": _voucher_seq(entry.voucher_no),
                "date": entry.occurred_on.isoformat(),
                "period": entry.period_start.isoformat(),
                "title": entry.summary or "",
                "backfill": entry.period_start != entry.occurred_on.replace(day=1),
            }

    entry_ids = order
    target_month = f"{year:04d}-{mon:02d}"
    attachment_counts = await attachment_counts_by_entries(company_id, entry_ids) if entry_ids else {}

    # 发票号与原件目录：凭证 → 全来源活跃附件（直连 + 发票 + 银行回单）
    invoice_numbers_by_entry: dict[str, list[str]] = defaultdict(list)
    originals: list[dict[str, Any]] = []
    seen_original_ids: set[str] = set()
    attachments_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if entry_ids:
        async with session_scope() as session:
            invoice_rows = (
                await session.execute(
                    select(Invoice).where(Invoice.company_id == company_id, Invoice.entry_id.in_(entry_ids))
                )
            ).scalars().all()
            for invoice in invoice_rows:
                if invoice.entry_id and invoice.invoice_number:
                    invoice_numbers_by_entry[invoice.entry_id].append(invoice.invoice_number)

        grouped_attachments = await resolve_entry_attachments(company_id, entry_ids, include_void=False)
        for entry_id in order:
            for item in grouped_attachments.get(entry_id, []):
                row = item["row"]
                parsed = row.parsed_json if isinstance(row.parsed_json, dict) else None
                if row.id not in seen_original_ids:
                    seen_original_ids.add(row.id)
                    originals.append(
                        {
                            "attachmentId": row.id,
                            "entryId": entry_id,
                            "voucherNo": entries_meta[entry_id].voucher_no or "" if entry_id in entries_meta else "",
                            "invoiceNumber": item.get("invoiceNumber") or "",
                            "fileName": row.file_name,
                            "documentType": row.document_type,
                            "contentSha256": row.content_sha256,
                            "sizeBytes": int(row.size_bytes or 0),
                        }
                    )
                # XML 原件带解析结果：凭证册在凭证页后渲染发票复现页（方案 A）
                if row.document_type == "einvoice_xml" and parsed:
                    attachments_by_entry[entry_id].append(
                        {
                            "attachmentId": row.id,
                            "invoiceNumber": parsed.get("invoiceNumber", ""),
                            "issueTime": parsed.get("issueTime", ""),
                            "documentType": row.document_type,
                            "contentSha256": row.content_sha256,
                            "fileName": row.file_name,
                            "parsed": parsed,
                        }
                    )
                # 图片原件（银行回单/支付截图/报销单/工资表等）：凭证页后紧跟原件图页
                elif str(row.file_ext or "").lower() in IMAGE_EXTS:
                    file_ext = str(row.file_ext or "").lower()
                    attachments_by_entry[entry_id].append(
                        {
                            "attachmentId": row.id,
                            "invoiceNumber": item.get("invoiceNumber") or "",
                            "documentType": row.document_type,
                            "fileExt": file_ext,
                            "contentSha256": row.content_sha256,
                            "fileName": row.file_name,
                            "storedPath": f"{company_id[:12]}/{row.id}.{file_ext}",
                            "parsedMeta": parsed,
                        }
                    )

    # 覆盖当月的月度银行标准对账单（归入原件归档目录，供逐件下载核验）
    async with session_scope() as session:
        stmt_pdfs = (
            await session.execute(
                select(Attachment)
                .where(
                    Attachment.company_id == company_id,
                    Attachment.status == "active",
                    Attachment.document_type == "bank_statement_pdf",
                )
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            )
        ).scalars().all()
    for stmt_row in stmt_pdfs:
        if stmt_row.id in seen_original_ids:
            continue
        parsed = stmt_row.parsed_json if isinstance(stmt_row.parsed_json, dict) else {}
        periods = parsed.get("periods") if isinstance(parsed.get("periods"), list) else []
        if stmt_row.period == target_month or target_month in [str(p) for p in periods]:
            seen_original_ids.add(stmt_row.id)
            originals.append(
                {
                    "attachmentId": stmt_row.id,
                    "entryId": "",
                    "voucherNo": "期间对账单",
                    "invoiceNumber": "",
                    "fileName": stmt_row.file_name,
                    "documentType": stmt_row.document_type,
                    "contentSha256": stmt_row.content_sha256,
                    "sizeBytes": int(stmt_row.size_bytes or 0),
                }
            )

    vouchers: list[dict[str, Any]] = []
    debit_total = money("0")
    for entry_id in order:
        entry = entries_meta[entry_id]
        lines = sorted(lines_by_entry[entry_id], key=lambda item: item.line_no)
        line_items: list[dict[str, Any]] = []
        debit_sum = money("0")
        credit_sum = money("0")
        for line in lines:
            amount = money(line.amount)
            if line.direction == "debit":
                debit_sum += amount
            else:
                credit_sum += amount
            line_items.append(
                {
                    "summary": entry.summary or "",
                    "code": line.account_code,
                    "name": line.account_name,
                    "auxiliaryText": _auxiliary_text(line.auxiliary),
                    # 原对象给前端屏幕预览（VoucherSheet 的 formatAuxiliaryLineText 消费）
                    "auxiliary": line.auxiliary or {},
                    "side": line.direction,
                    "amount": money_text(amount),
                    "quantity": money_text(line.quantity) if line.quantity is not None else "",
                    "unit": line.unit or "",
                    "unitPrice": money_text(line.unit_price) if line.unit_price is not None else "",
                }
            )
        total = debit_sum if debit_sum >= credit_sum else credit_sum
        debit_total += total
        vouchers.append(
            {
                **by_entry[entry_id],
                "lines": line_items,
                "blankRows": max(0, _VOUCHER_PAGE_LINES - len(line_items)),
                "debitTotal": money_text(debit_sum),
                "creditTotal": money_text(credit_sum),
                "capitalAmount": amount_to_chinese_capital(total),
                "attachmentCount": attachment_counts.get(entry_id, 0),
                "invoiceNumbers": invoice_numbers_by_entry.get(entry_id, []),
                # XML 原件解析结果：凭证页后逐份渲染发票复现页（方案 A）
                "attachments": attachments_by_entry.get(entry_id, []),
            }
        )

    period_status = await _period_status(company_id, start)
    payload: dict[str, Any] = {
        "meta": {
            "companyName": company_name,
            "month": month[:7],
            "periodStart": start.isoformat(),
            "periodEnd": period_end.isoformat(),
            "periodStatus": period_status,
            "voucherCount": len(vouchers),
            "attachmentTotal": sum(item["attachmentCount"] for item in vouchers),
            "xmlOriginalTotal": sum(1 for item in originals if item["documentType"] == "einvoice_xml"),
            "invoicePageTotal": sum(len(item["attachments"]) for item in vouchers),
            "voucherRange": {
                "first": vouchers[0]["voucherSeq"] if vouchers else "—",
                "last": vouchers[-1]["voucherSeq"] if vouchers else "—",
            },
            "debitTotal": money_text(debit_total),
        },
        "pagination": _paginate(vouchers, originals),
        "vouchers": vouchers,
        "originals": originals[:MAX_ORIGINAL_ROWS],
        "originalsMissing": [
            {"voucherNo": item["voucherNo"], "title": item["title"], "date": item["date"]}
            for item in vouchers
            if item["attachmentCount"] == 0
        ],
    }
    payload["meta"]["contentHash"] = content_hash(payload)
    return payload
