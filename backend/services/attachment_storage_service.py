# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""原始凭证附件服务（Phase 1 单据整理）：存储 + 匹配 + 作废 + 查验标记。

合规语义（财会〔2020〕6号）：
- 原件不可变（A2）：入库后不更新内容，作废只置 ``status='void'`` + 审计留痕，
  磁盘文件只增不减；
- 附件挂 ``invoices`` 主数据（invoice_id FK SET NULL），不直接挂凭证——冲销重记
  时 invoices.entry_id 被 ``_rebind_invoice_after_repost`` 迁移，附件随主数据走，
  不产生指向 reversed 凭证的悬空引用（A3）；
- 防重复：``(company_id, content_sha256)`` 唯一 + 发票号匹配，同文件/同号拦截；
- 未匹配 XML 暂存为 parked（invoice_id 为空），「一键生成凭证草稿」由前端取
  ``import_rows`` 走既有导入管线，确认权属于用户（PRODUCT.md）。
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError

from core.money import money, money_text
from db.models import Attachment, Invoice, JournalEntry
from db.session import session_scope
from services import einvoice_xml_service
from services.einvoice_xml_service import EInvoiceParseError
from services.account_service import get_company

_DEFAULT_ATTACHMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "attachments"
# 生产必须指向持久卷（deploy/docker-compose.yml），否则容器重建后原件全丢。
ATTACHMENTS_DIR = Path(os.environ.get("SOLOBOOKS_ATTACHMENTS_DIR", "").strip() or _DEFAULT_ATTACHMENTS_DIR)
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
INVOICE_SUPPORTED_EXTS = {"xml", "zip", "pdf", "ofd"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp"}
VOUCHER_SUPPORTED_EXTS = {"pdf", "ofd", *IMAGE_EXTS}
# 全量原件白名单：发票原件 + 通用凭证图/PDF（银行回单、支付截图、报销单、工资表等）
SUPPORTED_EXTS = {*INVOICE_SUPPORTED_EXTS, *IMAGE_EXTS}
DOCUMENT_TYPE_BY_EXT = {"xml": "einvoice_xml", "pdf": "einvoice_pdf", "ofd": "einvoice_ofd"}
INVOICE_DOCUMENT_TYPES = ("einvoice_xml", "einvoice_pdf", "einvoice_ofd")
VOUCHER_ATTACHMENT_TYPES = {
    "bank_receipt",
    "payment_screenshot",
    "reimbursement_form",
    "payroll_sheet",
    "contract",
    "other",
}


class AttachmentError(Exception):
    """附件操作失败（message 面向用户，status_code 用于 HTTP 映射）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _ext_of(file_name: str) -> str:
    return os.path.splitext(file_name)[1].lower().lstrip(".")


def _write_file(company_id: str, attachment_id: str, ext: str, payload: bytes) -> Path:
    """原子落盘：tmp + os.replace，目录按公司前缀分桶。"""
    directory = ATTACHMENTS_DIR / company_id[:12]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{attachment_id}.{ext or 'bin'}"
    tmp = target.with_name(f"{target.name}.{uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def _unlink_quiet(company_id: str, attachment_id: str, ext: str) -> None:
    target = ATTACHMENTS_DIR / company_id[:12] / f"{attachment_id}.{ext or 'bin'}"
    try:
        target.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - 清理失败不影响主流程（目录只增不减）
        pass


def _parsed_summary(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    return {
        "invoiceNumber": parsed.get("invoiceNumber", ""),
        "issueTime": parsed.get("issueTime", ""),
        "generalOrSpecial": parsed.get("generalOrSpecial", ""),
        "taxpayerType": parsed.get("taxpayerType", ""),
        "isBlue": parsed.get("isBlue", True),
        "sellerName": parsed.get("seller", {}).get("name", ""),
        "buyerName": parsed.get("buyer", {}).get("name", ""),
        "amountIncl": parsed.get("amounts", {}).get("incl", ""),
        "taxAmount": parsed.get("amounts", {}).get("tax", ""),
        "itemsCount": len(parsed.get("items", [])),
        "hasTaxBureauSignature": parsed.get("hasTaxBureauSignature", False),
        "withholdingNotice": parsed.get("withholdingNotice", False),
        "warnings": parsed.get("warnings", []),
    }


def _serialize(
    row: Attachment,
    *,
    with_import_rows: bool = False,
    relation: str | None = None,
) -> dict[str, Any]:
    parsed = row.parsed_json if isinstance(row.parsed_json, dict) else None
    is_xml_invoice = row.document_type == "einvoice_xml" or bool(parsed and "items" in parsed)
    data: dict[str, Any] = {
        "id": row.id,
        "invoiceId": row.invoice_id,
        "bankStatementId": row.bank_statement_id,
        "journalEntryId": row.journal_entry_id,
        "period": row.period,
        "parentAttachmentId": row.parent_attachment_id,
        "documentType": row.document_type,
        "fileName": row.file_name,
        "fileExt": row.file_ext,
        "sizeBytes": int(row.size_bytes or 0),
        "contentSha256": row.content_sha256,
        "status": row.status,
        "source": row.source,
        "voidedAt": row.voided_at.isoformat() if row.voided_at else "",
        "voidReason": row.void_reason,
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "parsedSummary": _parsed_summary(parsed) if is_xml_invoice else None,
        "parsedMeta": parsed if (parsed and not is_xml_invoice) else None,
    }
    if relation:
        data["relation"] = relation
    if with_import_rows and parsed and is_xml_invoice:
        data["importRows"] = einvoice_xml_service.build_import_rows(parsed, direction="expense")
    return data


def _detect_direction(parsed: dict[str, Any], company: dict[str, Any] | None) -> tuple[str, str | None]:
    """按公司税号判进/销项；判不出默认进项并提示（预览页可改方向）。"""
    tax_id = str((company or {}).get("taxId") or "").strip().upper()
    buyer_tax_id = str(parsed.get("buyer", {}).get("taxId") or "").strip().upper()
    seller_tax_id = str(parsed.get("seller", {}).get("taxId") or "").strip().upper()
    if tax_id and buyer_tax_id == tax_id:
        return "expense", None
    if tax_id and seller_tax_id == tax_id:
        return "income", None
    return "expense", "未能按公司税号判定方向，默认按进项（费用票）处理，请在预览中确认"


def _match_warnings(parsed: dict[str, Any], invoice: Invoice) -> list[str]:
    """XML 与台账行的一致性提示（仍挂上，状态可见——状态必须可解释）。"""
    warnings: list[str] = []
    incl = parsed.get("amounts", {}).get("incl", "")
    tax = parsed.get("amounts", {}).get("tax", "")
    try:
        if money(incl) != money(invoice.amount_incl_tax):
            warnings.append(f"价税合计与台账不一致（台账 {money_text(invoice.amount_incl_tax)} / XML {incl}）")
        if money(tax) != money(invoice.tax_amount):
            warnings.append(f"税额与台账不一致（台账 {money_text(invoice.tax_amount)} / XML {tax}）")
    except Exception:  # noqa: BLE001 - 金额字段异常不阻断挂接
        pass
    invoice_tax_id = invoice.buyer_tax_id if invoice.direction == "expense" else invoice.seller_tax_id
    xml_tax_id = parsed.get("buyer", {}).get("taxId") if invoice.direction == "expense" else parsed.get("seller", {}).get("taxId")
    if invoice_tax_id and xml_tax_id:
        if einvoice_xml_service.normalize_name(xml_tax_id) != einvoice_xml_service.normalize_name(invoice_tax_id):
            warnings.append("XML 对方税号与台账不一致")
    if not parsed.get("isBlue", True):
        warnings.append("红字发票原件已挂接，红冲状态以凭证为准")
    return warnings


async def _existing_by_sha(company_id: str, shas: list[str]) -> dict[str, Attachment]:
    if not shas:
        return {}
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Attachment).where(Attachment.company_id == company_id, Attachment.content_sha256.in_(shas))
            )
        ).scalars().all()
    return {row.content_sha256: row for row in rows}


async def _invoice_map_by_number(company_id: str) -> dict[str, Invoice]:
    """台账发票号索引（归一化键；uq_invoice_company_fingerprint 保证同号唯一）。"""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Invoice).where(Invoice.company_id == company_id, Invoice.invoice_number != "")
            )
        ).scalars().all()
    return {
        einvoice_xml_service.normalize_name(row.invoice_number): row
        for row in rows
        if einvoice_xml_service.normalize_name(row.invoice_number)
    }


async def _invoice_by_id(company_id: str, invoice_id: str) -> Invoice | None:
    async with session_scope() as session:
        return (
            await session.execute(
                select(Invoice).where(Invoice.company_id == company_id, Invoice.id == invoice_id)
            )
        ).scalar_one_or_none()


def _result(file_name: str, outcome: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "fileName": file_name,
        "outcome": outcome,
        "attachmentId": None,
        "invoiceId": None,
        "invoiceNumber": "",
        "warnings": [],
        "error": "",
        "matchedInvoice": None,
        "importRows": None,
    }
    item.update(extra)
    return item


async def _persist_one(
    company_id: str,
    user_id: str,
    file_name: str,
    ext: str,
    payload: bytes,
    digest: str,
    parsed: dict[str, Any] | None,
    company: dict[str, Any] | None,
    target_invoice: Invoice | None,
    invoice_map: dict[str, Invoice],
) -> dict[str, Any]:
    """单份原件：匹配 → 落盘 → 入库（写失败清文件；UQ 竞争按 duplicate）。"""
    document_type = DOCUMENT_TYPE_BY_EXT.get(ext, "other")
    invoice_number = (parsed or {}).get("invoiceNumber", "")
    warnings: list[str] = []
    matched: Invoice | None = None
    import_rows: list[dict[str, Any]] | None = None

    if target_invoice is not None:
        if invoice_number and einvoice_xml_service.normalize_name(invoice_number) != einvoice_xml_service.normalize_name(
            target_invoice.invoice_number
        ):
            return _result(file_name, "rejected", invoiceId=target_invoice.id, invoiceNumber=invoice_number,
                           error="原件发票号与目标发票不一致，请核对")
        matched = target_invoice
    elif parsed is not None:
        matched = invoice_map.get(einvoice_xml_service.normalize_name(invoice_number))
        if matched is not None:
            warnings.extend(_match_warnings(parsed, matched))
        else:
            direction, direction_warning = _detect_direction(parsed, company)
            if direction_warning:
                warnings.append(direction_warning)
            if not parsed.get("isBlue", True):
                warnings.append("红字发票暂不支持一键生成草稿，请走 Excel 导入的红冲行")
                import_rows = []
            else:
                import_rows = einvoice_xml_service.build_import_rows(parsed, direction=direction)

    attachment_id = f"att-{uuid4().hex[:24]}"
    stored_name = f"{attachment_id}.{ext}"
    _write_file(company_id, attachment_id, ext, payload)
    try:
        async with session_scope() as session:
            session.add(
                Attachment(
                    id=attachment_id,
                    company_id=company_id,
                    invoice_id=matched.id if matched is not None else None,
                    document_type=document_type,
                    file_name=file_name,
                    file_ext=ext,
                    size_bytes=len(payload),
                    content_sha256=digest,
                    stored_name=stored_name,
                    status="active",
                    parsed_json=parsed,
                    source="upload",
                    created_by=user_id,
                )
            )
            await session.flush()
    except IntegrityError:
        _unlink_quiet(company_id, attachment_id, ext)
        return _result(file_name, "duplicate", invoiceNumber=invoice_number,
                       error="该原件已上传过（同一文件不可重复归档）")
    except Exception:
        _unlink_quiet(company_id, attachment_id, ext)
        raise

    return {
        "fileName": file_name,
        "outcome": "matched" if matched is not None else ("stored" if parsed is None else "parked"),
        "attachmentId": attachment_id,
        "invoiceId": matched.id if matched is not None else None,
        "invoiceNumber": invoice_number,
        "warnings": warnings,
        "error": "",
        "matchedInvoice": {
            "id": matched.id,
            "invoiceNumber": matched.invoice_number,
            "direction": matched.direction,
            "amountInclTax": money_text(matched.amount_incl_tax),
        } if matched is not None else None,
        "importRows": import_rows,
    }


async def upload_attachments(
    company_id: str,
    user_id: str,
    files: list[dict[str, Any]],
    *,
    invoice_id: str | None = None,
) -> list[dict[str, Any]]:
    """上传原件（multipart 多文件）。files: [{file_name, payload}]。

    每文件返回一个结果：matched（挂台账）/ parked（未匹配暂存，附 import_rows）/
    stored（版式文件留存）/ duplicate（sha 重复）/ rejected（拒收，附原因）。
    ZIP 展开成多份子结果（跳过非 XML）。失败不落盘、不产生半行。
    """
    company = await get_company(company_id)
    target_invoice = await _invoice_by_id(company_id, invoice_id) if invoice_id else None
    if invoice_id and target_invoice is None:
        raise AttachmentError("目标发票不存在", 404)
    invoice_map = await _invoice_map_by_number(company_id)

    results: list[dict[str, Any]] = []
    seen_shas: set[str] = set()
    prepared: list[dict[str, Any]] = []

    # 第一轮：基本校验 + 批内去重（不落盘）
    for source in files:
        file_name = os.path.basename(str(source.get("file_name") or ""))
        payload = source.get("payload") or b""
        ext = _ext_of(file_name)
        if not file_name or ext not in INVOICE_SUPPORTED_EXTS:
            results.append(_result(file_name or "(未命名)", "rejected", error=f"仅支持 {'/'.join(sorted(INVOICE_SUPPORTED_EXTS))} 文件"))
            continue
        if not payload:
            results.append(_result(file_name, "rejected", error="空文件"))
            continue
        if len(payload) > MAX_ATTACHMENT_BYTES:
            results.append(_result(file_name, "rejected", error=f"超过单文件 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限"))
            continue
        digest = _sha256(payload)
        if digest in seen_shas:
            results.append(_result(file_name, "duplicate", error="与本次上传中另一份文件内容相同"))
            continue
        seen_shas.add(digest)
        prepared.append({"file_name": file_name, "payload": payload, "ext": ext, "sha": digest})

    # 第二轮：库内 sha 去重
    existing = await _existing_by_sha(company_id, [item["sha"] for item in prepared])
    for item in list(prepared):
        dup = existing.get(item["sha"])
        if dup is None:
            continue
        parsed_dup = dup.parsed_json if isinstance(dup.parsed_json, dict) else None
        results.append(_result(item["file_name"], "duplicate", attachmentId=dup.id, invoiceId=dup.invoice_id,
                               invoiceNumber=(parsed_dup or {}).get("invoiceNumber", ""),
                               error="该原件已上传过（同一文件不可重复归档）"))
        prepared.remove(item)

    # 第三轮：逐份处理
    for item in prepared:
        file_name = item["file_name"]
        ext = item["ext"]
        payload = item["payload"]
        if ext == "zip":
            try:
                entries = einvoice_xml_service.iter_zip_entries(payload)
            except EInvoiceParseError as exc:
                results.append(_result(file_name, "rejected", error=str(exc)))
                continue
            for entry in entries:
                if entry["status"] != "parsed":
                    results.append(_result(entry["fileName"], "rejected", error=entry.get("error", "已跳过")))
                    continue
                try:
                    sub_parsed = einvoice_xml_service.parse_einvoice_xml(entry["xml"])
                except EInvoiceParseError as exc:
                    results.append(_result(entry["fileName"], "rejected", error=str(exc)))
                    continue
                results.append(
                    await _persist_one(company_id, user_id, entry["fileName"], "xml", entry["xml"],
                                       _sha256(entry["xml"]), sub_parsed, company, target_invoice, invoice_map)
                )
            continue
        parsed: dict[str, Any] | None = None
        if ext == "xml":
            try:
                parsed = einvoice_xml_service.parse_einvoice_xml(payload)
            except EInvoiceParseError as exc:
                results.append(_result(file_name, "rejected", error=str(exc)))
                continue
        results.append(
            await _persist_one(company_id, user_id, file_name, ext, payload, item["sha"],
                               parsed, company, target_invoice, invoice_map)
        )

    return results


async def list_attachments(
    company_id: str,
    *,
    invoice_id: str | None = None,
    parked: bool = False,
) -> dict[str, Any]:
    """附件列表：按发票查（含 void，前端灰显）或查未匹配暂存（只看 active）。"""
    conditions = [Attachment.company_id == company_id]
    if invoice_id:
        conditions.append(Attachment.invoice_id == invoice_id)
    elif parked:
        conditions.append(Attachment.invoice_id.is_(None))
        conditions.append(Attachment.status == "active")
        conditions.append(Attachment.document_type.in_(INVOICE_DOCUMENT_TYPES))
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Attachment)
                .where(*conditions)
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            )
        ).scalars().all()
    items = [_serialize(row, with_import_rows=parked) for row in rows]
    return {"items": items, "total": len(items)}


async def parked_count(company_id: str) -> int:
    async with session_scope() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(Attachment)
                .where(
                    Attachment.company_id == company_id,
                    Attachment.invoice_id.is_(None),
                    Attachment.status == "active",
                    Attachment.document_type.in_(INVOICE_DOCUMENT_TYPES),
                )
            )
        ).scalar_one()
    return int(total or 0)


async def read_attachment_file(company_id: str, attachment_id: str) -> dict[str, Any]:
    """读原件：归属校验 + 下载时重算 sha256 与登记值比对（runbook 409 约定）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Attachment).where(Attachment.company_id == company_id, Attachment.id == attachment_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise AttachmentError("附件不存在", 404)
    path = ATTACHMENTS_DIR / company_id[:12] / row.stored_name
    if not path.exists():
        raise AttachmentError("附件文件缺失（存储卷被移动或未随备份恢复）", 410)
    data = path.read_bytes()
    if _sha256(data) != row.content_sha256:
        raise AttachmentError("原件完整性校验失败（sha256 与登记值不符），请从备份恢复后再试", 409)
    return {
        "fileName": row.file_name,
        "fileExt": row.file_ext,
        "data": data,
        "contentSha256": row.content_sha256,
        "documentType": row.document_type,
    }


async def void_attachment(company_id: str, user_id: str, attachment_id: str, reason: str) -> dict[str, Any]:
    """作废：只置状态标记 + 审计留痕，文件保留（A2 原件不可变、目录只增不减）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Attachment).where(Attachment.company_id == company_id, Attachment.id == attachment_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AttachmentError("附件不存在", 404)
        if row.status == "void":
            return _serialize(row)
        row.status = "void"
        row.voided_at = datetime.now(timezone.utc)
        row.void_reason = str(reason or "").strip()[:255]
        row.updated_at = datetime.now(timezone.utc)
        await session.flush()
        serialized = _serialize(row)
    from services.audit_service import log_action

    await log_action(company_id, user_id, "void", "attachment", attachment_id,
                     {"fileName": serialized["fileName"], "reason": serialized["voidReason"]})
    return serialized


async def relink_parked_attachments(company_id: str, user_id: str | None = None) -> dict[str, Any]:
    """把 parked 原件按发票号幂等回挂（导入成功后前端自动调用，也可手动触发）。"""
    async with session_scope() as session:
        parked_rows = (
            await session.execute(
                select(Attachment).where(
                    Attachment.company_id == company_id,
                    Attachment.invoice_id.is_(None),
                    Attachment.status == "active",
                    Attachment.document_type == "einvoice_xml",
                )
            )
        ).scalars().all()
        if not parked_rows:
            return {"relinked": 0, "scanned": 0}
        invoice_map = {
            einvoice_xml_service.normalize_name(row.invoice_number): row.id
            for row in (
                await session.execute(
                    select(Invoice).where(Invoice.company_id == company_id, Invoice.invoice_number != "")
                )
            ).scalars().all()
            if einvoice_xml_service.normalize_name(row.invoice_number)
        }

    relinked = 0
    for row in parked_rows:
        parsed = row.parsed_json if isinstance(row.parsed_json, dict) else None
        invoice_id = invoice_map.get(einvoice_xml_service.normalize_name((parsed or {}).get("invoiceNumber", "")))
        if invoice_id is None:
            continue
        async with session_scope() as session:
            fresh = (
                await session.execute(
                    select(Attachment).where(Attachment.company_id == company_id, Attachment.id == row.id)
                )
            ).scalar_one_or_none()
            # 幂等护栏：并发调用/已作废/已被其他发票占用都不覆盖
            if fresh is None or fresh.invoice_id is not None or fresh.status != "active":
                continue
            fresh.invoice_id = invoice_id
            fresh.updated_at = datetime.now(timezone.utc)
            await session.flush()
        relinked += 1
    if relinked and user_id:
        from services.audit_service import log_action

        await log_action(company_id, user_id, "relink", "attachment", None, {"relinked": relinked})
    return {"relinked": relinked, "scanned": len(parked_rows)}


async def attachment_aggregates(company_id: str, invoice_ids: list[str]) -> dict[str, dict[str, Any]]:
    """台账列表用的附件聚合：{invoice_id: {count, hasOriginalXml}}（只算 active）。"""
    if not invoice_ids:
        return {}
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Attachment.invoice_id,
                    func.count().label("total"),
                    func.max(case((Attachment.document_type == "einvoice_xml", 1), else_=0)).label("has_xml"),
                )
                .where(
                    Attachment.company_id == company_id,
                    Attachment.status == "active",
                    Attachment.invoice_id.in_(invoice_ids),
                )
                .group_by(Attachment.invoice_id)
            )
        ).all()
    return {
        row.invoice_id: {"count": int(row.total or 0), "hasOriginalXml": bool(row.has_xml)}
        for row in rows
    }


async def _statement_ids_by_entries(company_id: str, entry_ids: list[str]) -> dict[str, set[str]]:
    """查询凭证关联的银行流水 id（同时覆盖 bank_statements.matched_entry_id 与 1:N matches 表）。"""
    unique_ids = [entry_id for entry_id in dict.fromkeys(entry_ids or []) if entry_id]
    if not unique_ids:
        return {}
    from services.database import get_conn

    result: dict[str, set[str]] = {entry_id: set() for entry_id in unique_ids}
    placeholders = ",".join("?" for _ in unique_ids)
    try:
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                f"SELECT id, matched_entry_id FROM bank_statements "
                f"WHERE company_id = ? AND matched_entry_id IN ({placeholders})",
                (company_id, *unique_ids),
            )
            for row in await cursor.fetchall():
                eid = str(row["matched_entry_id"] or "")
                sid = str(row["id"] or "")
                if eid and sid and eid in result:
                    result[eid].add(sid)
            try:
                cursor2 = await conn.execute(
                    f"SELECT statement_id, entry_id FROM bank_statement_matches "
                    f"WHERE company_id = ? AND entry_id IN ({placeholders})",
                    (company_id, *unique_ids),
                )
                for row in await cursor2.fetchall():
                    eid = str(row["entry_id"] or "")
                    sid = str(row["statement_id"] or "")
                    if eid and sid and eid in result:
                        result[eid].add(sid)
            except Exception:  # noqa: BLE001 - 旧测试库可能未初始化 matches 表
                pass
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001 - 未跑银行模块时优雅降级为空
        return {}
    return result


async def resolve_entry_attachments(
    company_id: str,
    entry_ids: list[str],
    *,
    include_void: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """统一聚合凭证的真实原始附件（A3 真相唯一：发票原件 + 银行回单 + 直接挂载附件）。"""
    unique_ids = [entry_id for entry_id in dict.fromkeys(entry_ids or []) if entry_id]
    if not unique_ids:
        return {}
    stmt_map = await _statement_ids_by_entries(company_id, unique_ids)
    all_stmt_ids = sorted({sid for sids in stmt_map.values() for sid in sids})
    stmt_to_entries: dict[str, list[str]] = {}
    for eid, sids in stmt_map.items():
        for sid in sids:
            stmt_to_entries.setdefault(sid, []).append(eid)

    async with session_scope() as session:
        invoice_rows = (
            await session.execute(
                select(Invoice).where(Invoice.company_id == company_id, Invoice.entry_id.in_(unique_ids))
            )
        ).scalars().all()
        invoice_by_id = {inv.id: inv for inv in invoice_rows}
        invoice_ids = list(invoice_by_id.keys())

        or_filters = [Attachment.journal_entry_id.in_(unique_ids)]
        if invoice_ids:
            or_filters.append(Attachment.invoice_id.in_(invoice_ids))
        if all_stmt_ids:
            or_filters.append(Attachment.bank_statement_id.in_(all_stmt_ids))

        conditions = [Attachment.company_id == company_id, or_(*or_filters)]
        if not include_void:
            conditions.append(Attachment.status == "active")

        rows = (
            await session.execute(
                select(Attachment)
                .where(*conditions)
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            )
        ).scalars().all()

    grouped: dict[str, list[dict[str, Any]]] = {entry_id: [] for entry_id in unique_ids}
    seen_per_entry: dict[str, set[str]] = {entry_id: set() for entry_id in unique_ids}

    for row in rows:
        targets: list[tuple[str, str]] = []
        if row.journal_entry_id and row.journal_entry_id in grouped:
            targets.append((row.journal_entry_id, "direct"))
        if row.invoice_id and row.invoice_id in invoice_by_id:
            inv_entry_id = invoice_by_id[row.invoice_id].entry_id
            if inv_entry_id and inv_entry_id in grouped:
                targets.append((inv_entry_id, "invoice"))
        if row.bank_statement_id and row.bank_statement_id in stmt_to_entries:
            for stmt_entry_id in stmt_to_entries[row.bank_statement_id]:
                targets.append((stmt_entry_id, "bank_statement"))

        for entry_id, relation in targets:
            if row.id in seen_per_entry[entry_id]:
                continue
            seen_per_entry[entry_id].add(row.id)
            grouped[entry_id].append(
                {
                    "row": row,
                    "relation": relation,
                    "invoiceNumber": invoice_by_id[row.invoice_id].invoice_number if (row.invoice_id and row.invoice_id in invoice_by_id) else "",
                }
            )
    return grouped


async def list_entry_attachments(
    company_id: str,
    entry_id: str,
    *,
    include_void: bool = True,
) -> dict[str, Any]:
    """查询单张凭证下的全部关联附件（默认包含已作废件供页面灰显）。"""
    async with session_scope() as session:
        exists = await session.scalar(
            select(JournalEntry.id).where(JournalEntry.company_id == company_id, JournalEntry.id == entry_id)
        )
    if exists is None:
        raise AttachmentError("记账凭证不存在", 404)
    grouped = await resolve_entry_attachments(company_id, [entry_id], include_void=include_void)
    items = [
        {
            **_serialize(item["row"], relation=item["relation"]),
            "invoiceNumber": item["invoiceNumber"],
        }
        for item in grouped.get(entry_id, [])
    ]
    active_count = sum(1 for item in items if item["status"] == "active")
    return {"items": items, "total": len(items), "activeCount": active_count}


async def upload_voucher_attachments(
    company_id: str,
    user_id: str,
    entry_id: str,
    document_type: str,
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """上传通用原始凭证附件并直接挂载到指定记账凭证（支付截图/报销单/工资表/合同/回单/其他）。"""
    doc_type = str(document_type or "other").strip() or "other"
    if doc_type not in VOUCHER_ATTACHMENT_TYPES:
        raise AttachmentError(f"不支持的凭证附件类型：{doc_type}", 400)
    async with session_scope() as session:
        entry_row = (
            await session.execute(
                select(JournalEntry).where(JournalEntry.company_id == company_id, JournalEntry.id == entry_id)
            )
        ).scalar_one_or_none()
    if entry_row is None:
        raise AttachmentError("记账凭证不存在", 404)

    results: list[dict[str, Any]] = []
    seen_shas: set[str] = set()
    prepared: list[dict[str, Any]] = []

    for source in files:
        file_name = os.path.basename(str(source.get("file_name") or ""))
        payload = source.get("payload") or b""
        ext = _ext_of(file_name)
        if not file_name or ext not in VOUCHER_SUPPORTED_EXTS:
            results.append(
                {
                    "fileName": file_name or "(未命名)",
                    "outcome": "rejected",
                    "attachmentId": None,
                    "journalEntryId": entry_id,
                    "documentType": doc_type,
                    "error": f"仅支持 {'/'.join(sorted(VOUCHER_SUPPORTED_EXTS))} 文件",
                    "attachment": None,
                }
            )
            continue
        if not payload:
            results.append(
                {
                    "fileName": file_name,
                    "outcome": "rejected",
                    "attachmentId": None,
                    "journalEntryId": entry_id,
                    "documentType": doc_type,
                    "error": "空文件",
                    "attachment": None,
                }
            )
            continue
        if len(payload) > MAX_ATTACHMENT_BYTES:
            results.append(
                {
                    "fileName": file_name,
                    "outcome": "rejected",
                    "attachmentId": None,
                    "journalEntryId": entry_id,
                    "documentType": doc_type,
                    "error": f"超过单文件 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限",
                    "attachment": None,
                }
            )
            continue
        digest = _sha256(payload)
        if digest in seen_shas:
            results.append(
                {
                    "fileName": file_name,
                    "outcome": "duplicate",
                    "attachmentId": None,
                    "journalEntryId": entry_id,
                    "documentType": doc_type,
                    "error": "与本次上传中另一份文件内容相同",
                    "attachment": None,
                }
            )
            continue
        seen_shas.add(digest)
        prepared.append({"file_name": file_name, "payload": payload, "ext": ext, "sha": digest})

    existing = await _existing_by_sha(company_id, [item["sha"] for item in prepared])
    for item in list(prepared):
        dup = existing.get(item["sha"])
        if dup is None:
            continue
        results.append(
            {
                "fileName": item["file_name"],
                "outcome": "duplicate",
                "attachmentId": dup.id,
                "journalEntryId": dup.journal_entry_id or entry_id,
                "documentType": dup.document_type,
                "error": "该附件已上传过（同一文件不可重复归档）",
                "attachment": _serialize(dup),
            }
        )
        prepared.remove(item)

    uploaded_ids: list[str] = []
    for item in prepared:
        attachment_id = f"att-{uuid4().hex[:24]}"
        ext = item["ext"]
        payload = item["payload"]
        _write_file(company_id, attachment_id, ext, payload)
        try:
            async with session_scope() as session:
                row = Attachment(
                    id=attachment_id,
                    company_id=company_id,
                    journal_entry_id=entry_id,
                    document_type=doc_type,
                    file_name=item["file_name"],
                    file_ext=ext,
                    size_bytes=len(payload),
                    content_sha256=item["sha"],
                    stored_name=f"{attachment_id}.{ext}",
                    status="active",
                    parsed_json=None,
                    source="upload",
                    created_by=user_id,
                )
                session.add(row)
                await session.flush()
                serialized = _serialize(row, relation="direct")
        except IntegrityError:
            _unlink_quiet(company_id, attachment_id, ext)
            results.append(
                {
                    "fileName": item["file_name"],
                    "outcome": "duplicate",
                    "attachmentId": None,
                    "journalEntryId": entry_id,
                    "documentType": doc_type,
                    "error": "该附件已上传过（同一文件不可重复归档）",
                    "attachment": None,
                }
            )
            continue
        except Exception:
            _unlink_quiet(company_id, attachment_id, ext)
            raise
        uploaded_ids.append(attachment_id)
        results.append(
            {
                "fileName": item["file_name"],
                "outcome": "stored",
                "attachmentId": attachment_id,
                "journalEntryId": entry_id,
                "documentType": doc_type,
                "error": "",
                "attachment": serialized,
            }
        )

    if uploaded_ids:
        from services.audit_service import log_action

        await log_action(
            company_id,
            user_id,
            "upload_voucher_attachments",
            "journal_entry",
            entry_id,
            {"attachmentIds": uploaded_ids, "documentType": doc_type},
        )
    return results


async def attachment_counts_by_entries(company_id: str, entry_ids: list[str]) -> dict[str, int]:
    """凭证 → 真实附单据数（发票原件 + 匹配流水的银行回单 + 直接挂载附件，三路去重计数，A3 真相唯一）。"""
    grouped = await resolve_entry_attachments(company_id, entry_ids, include_void=False)
    return {entry_id: len(items) for entry_id, items in grouped.items() if items}


async def set_invoice_verify(company_id: str, user_id: str, invoice_id: str, *, verified: bool) -> dict[str, Any]:
    """查验状态标记（用户在电子税务局查验后的人工声明，财会〔2020〕6号第三条）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Invoice).where(Invoice.company_id == company_id, Invoice.id == invoice_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AttachmentError("发票不存在", 404)
        row.verify_status = "verified" if verified else "unverified"
        row.verified_at = datetime.now(timezone.utc) if verified else None
        row.updated_at = datetime.now(timezone.utc)
        await session.flush()
        result = {
            "id": row.id,
            "verifyStatus": row.verify_status,
            "verifiedAt": row.verified_at.isoformat() if row.verified_at else "",
        }
    from services.audit_service import log_action

    await log_action(company_id, user_id, "invoice_verify", "invoice", invoice_id,
                     {"verifyStatus": result["verifyStatus"]})
    return result
