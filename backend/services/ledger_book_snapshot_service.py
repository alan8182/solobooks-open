# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿与凭证册签发与留痕。

一册账簿/凭证册只有在期间结账之后才能签发：未结账的账页随时会因补记、
反结账而变，盖章装订就是在给一个会变的数字背书。签发之后：

- 完整 payload 落库，重打与原版逐字一致；
- sha256 存证，改一个分也不一样；
- 作废不删册，只标状态并留原因，旧册永远可查。

Phase 2 起 book_kind 泛化：``monthly``/``yearly``（电子账簿）与 ``voucher``
（凭证册装订）共用同一张快照表、同一套签发/作废/下载事务；差异只在
build 与 PDF 渲染入口（按 kind 分发）。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from core.money import money, money_text
from db.models import AccountingPeriod, AuthUser, LedgerBookSnapshot
from db.session import session_scope
from services.accounting_core_service import record_audit
from services.ledger_book_pdf_service import (
    render_ledger_book_pdf,
    render_ledger_book_pdf_async,
)
from services.ledger_book_service import build_ledger_book, content_hash
from services.voucher_book_pdf_service import render_voucher_book_pdf, render_voucher_book_pdf_async
from services.voucher_book_service import build_voucher_book

logger = logging.getLogger(__name__)

# 文档称谓（文件名/响应/审计里区分两类册）
_KIND_LABELS = {"monthly": "电子账簿", "yearly": "电子账簿", "voucher": "凭证册"}


class SnapshotError(ValueError):
    """带错误码的业务异常，路由据此决定 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _content_hash(book: dict[str, Any]) -> str:
    """与实时册同一算法（见 ledger_book_service.content_hash）。"""
    return content_hash(book)


def _book_period(*, month: str | None, year: int | None, scope: str) -> tuple[str, str]:
    if scope == "year":
        if year is None:
            raise SnapshotError("INVALID_PERIOD", "年度账簿需要 year 参数")
        return "yearly", str(int(year))
    if not month:
        raise SnapshotError("INVALID_PERIOD", "月度账簿需要 month 参数")
    return "monthly", month


def _row_to_dict(row: LedgerBookSnapshot, *, with_payload: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "bookKind": row.book_kind,
        "period": row.period,
        "bookNo": row.book_no,
        "status": row.status,
        "contentHash": row.content_hash,
        "pageCount": row.page_count,
        "lineCount": row.line_count,
        "anchor": row.anchor or {},
        "generatedBy": row.generated_by,
        "generatedAt": row.generated_at.isoformat() if row.generated_at else None,
        "hasPdf": bool(row.pdf_path),
        "pdfSha256": row.pdf_sha256,
        "pdfSizeBytes": row.pdf_size_bytes,
        "pdfGeneratedAt": row.pdf_generated_at.isoformat() if row.pdf_generated_at else None,
        "voidReason": row.void_reason,
        "voidedBy": row.voided_by,
        "voidedAt": row.voided_at.isoformat() if row.voided_at else None,
    }
    if with_payload:
        data["payload"] = row.payload or {}
    return data


async def issue_snapshot(
    company_id: str,
    *,
    month: str | None = None,
    year: int | None = None,
    scope: str = "month",
    user_id: str,
    request_id: str = "",
    user_name: str | None = None,
    book_kind: str | None = None,
) -> dict[str, Any]:
    """签发正式册（book_kind=monthly/yearly 电子账簿；voucher 凭证册装订）。

    凭证册签发与账簿共用同一事务骨架：关账拦截 → build → 渲染 → 落盘 →
    插行 → 审计；差异只在 build/render 入口、行数统计、anchor 与文件称谓。
    """
    resolved_kind = book_kind or ("yearly" if scope == "year" else "monthly")
    if resolved_kind == "voucher":
        if not month:
            raise SnapshotError("INVALID_PERIOD", "凭证册需要 month 参数")
        period = month[:7]
        period_start = f"{period}-01"
    else:
        resolved_kind, period = _book_period(month=month, year=year, scope=scope)
        period_start = f"{period}-01" if resolved_kind == "monthly" else f"{period}-01-01"

    async with session_scope() as session:
        status = await session.scalar(
            select(AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == _as_date(period_start),
            )
        )
    if status != "closed":
        raise SnapshotError(
            "PERIOD_NOT_CLOSED",
            "期间尚未结账，不能签发正式册。请先在月度结账里完成本期结账。",
            status_code=422,
        )

    if resolved_kind == "voucher":
        book = await build_voucher_book(company_id, month=period)
    else:
        book = await build_ledger_book(company_id, month=month, year=year, scope=scope)
    content_hash_value = _content_hash(book)
    now = datetime.now(timezone.utc)

    generated_by = user_name
    if not generated_by:
        async with session_scope() as session:
            generated_by = await session.scalar(
                select(AuthUser.nickname).where(AuthUser.user_id == user_id)
            )
    generated_by = generated_by or user_id
    book["meta"].update(
        {
            "generatedAt": now.isoformat(),
            "generatedBy": generated_by,
            "contentHash": content_hash_value,
            "pageCount": book["pagination"]["totalPages"],
        }
    )
    if resolved_kind == "voucher":
        line_count = sum(len(voucher.get("lines", [])) for voucher in book.get("vouchers", []))
        anchor = {
            "periodStatus": book["meta"]["periodStatus"],
            "voucherCount": book["meta"]["voucherCount"],
            "attachmentTotal": book["meta"]["attachmentTotal"],
            "debitTotal": book["meta"]["debitTotal"],
            "contentHash": content_hash_value,
        }
    else:
        line_count = sum(len(page["lines"]) for page in book["details"])
        anchor = {
            "periodStatus": book["meta"]["periodStatus"],
            "entryCount": book["meta"]["entryCount"],
            "totalDebit": book["balance"]["totalDebit"],
            "totalCredit": book["balance"]["totalCredit"],
            "contentHash": content_hash_value,
        }

    async with session_scope() as session:
        existing = await session.scalar(
            select(func.count(LedgerBookSnapshot.id)).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.book_kind == resolved_kind,
                LedgerBookSnapshot.period == period,
            )
        )
        book_no = f"{period}-{int(existing or 0) + 1:04d}"
        book["meta"]["bookNo"] = book_no

        # 签发即归档：PDF 与 JSON 快照同一事务落库。渲染失败就回滚签发，
        # 不留下「已签发但拿不出归档件」的册（A2/A3）。
        if resolved_kind == "voucher":
            rendered = await render_voucher_book_pdf_async(
                book,
                snapshot_status="issued",
                book_no=book_no,
                content_hash=content_hash_value,
                generated_by=generated_by,
                generated_at=now.isoformat(),
            )
        else:
            rendered = await render_ledger_book_pdf_async(
                book,
                snapshot_status="issued",
                book_no=book_no,
                content_hash=content_hash_value,
                generated_by=generated_by,
                generated_at=now.isoformat(),
            )
        pdf_file = _write_pdf_archive(
            company_id, book, book_no, rendered.pdf_bytes, doc_label=_KIND_LABELS.get(resolved_kind, "电子账簿")
        )

        row = LedgerBookSnapshot(
            company_id=company_id,
            book_kind=resolved_kind,
            period=period,
            book_no=book_no,
            status="issued",
            payload=book,
            content_hash=content_hash_value,
            page_count=book["pagination"]["totalPages"],
            line_count=line_count,
            pdf_path=str(pdf_file),
            pdf_sha256=rendered.pdf_sha256,
            pdf_size_bytes=len(rendered.pdf_bytes),
            pdf_generated_at=now,
            anchor=anchor,
            generated_by=generated_by,
            generated_at=now,
        )
        session.add(row)
        await session.flush()
        audit_action = "issue_voucher_book" if resolved_kind == "voucher" else "issue_ledger_book"
        await record_audit(
            session,
            company_id=company_id,
            user_id=user_id,
            action=audit_action,
            entity_type="ledger_book_snapshot",
            entity_id=row.id,
            request_id=request_id or f"issue-{resolved_kind}-{book_no}",
            after={
                "bookKind": resolved_kind,
                "bookNo": book_no,
                "contentHash": content_hash_value,
                "pageCount": row.page_count,
                "pdfSha256": rendered.pdf_sha256,
                "pdfSizeBytes": row.pdf_size_bytes,
            },
        )
        result = _row_to_dict(row, with_payload=True)
    return result


async def issue_voucher_book_snapshot(
    company_id: str,
    *,
    month: str,
    user_id: str,
    request_id: str = "",
    user_name: str | None = None,
) -> dict[str, Any]:
    """签发月度凭证册（薄封装：语义与审计 action 与账簿区分）。"""
    return await issue_snapshot(
        company_id,
        month=month,
        scope="month",
        user_id=user_id,
        request_id=request_id,
        user_name=user_name,
        book_kind="voucher",
    )


async def list_snapshots(
    company_id: str,
    *,
    period: str | None = None,
    book_kind: str | None = None,
    include_kinds: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """快照列表。``book_kind`` 精确过滤；``include_kinds`` 用于账簿页要排除
    凭证册（monthly+yearly）这类组合过滤。两者都缺省 = 全部（兼容旧调用）。"""
    async with session_scope() as session:
        query = select(LedgerBookSnapshot).where(LedgerBookSnapshot.company_id == company_id)
        if period:
            query = query.where(LedgerBookSnapshot.period == period)
        if book_kind:
            query = query.where(LedgerBookSnapshot.book_kind == book_kind)
        elif include_kinds:
            query = query.where(LedgerBookSnapshot.book_kind.in_(include_kinds))
        rows = (
            await session.execute(query.order_by(LedgerBookSnapshot.generated_at.desc(), LedgerBookSnapshot.id))
        ).scalars().all()
    return [_row_to_dict(row) for row in rows]


async def get_snapshot(company_id: str, snapshot_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        row = await session.scalar(
            select(LedgerBookSnapshot).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.id == snapshot_id,
            )
        )
    if row is None:
        raise SnapshotError("SNAPSHOT_NOT_FOUND", "账簿快照不存在", status_code=404)
    return _row_to_dict(row, with_payload=True)


def safe_book_filename_part(name: str | None, fallback: str = "账套") -> str:
    cleaned = "".join(ch for ch in str(name or "") if ch not in '\\/:*?"<>|').strip()
    return cleaned or fallback


def _archive_dir() -> Path:
    """运行时取目录，便于测试 monkeypatch 与生产 ``SOLOBOOKS_ARCHIVE_DIR``。"""
    from services import backup_archive_service

    return Path(backup_archive_service.ARCHIVE_DIR)


def _write_pdf_archive(
    company_id: str, book: dict[str, Any], book_no: str, pdf_bytes: bytes, *, doc_label: str = "电子账簿"
) -> Path:
    directory = _archive_dir()
    directory.mkdir(parents=True, exist_ok=True)
    company = safe_book_filename_part((book.get("meta") or {}).get("companyName"), doc_label)
    file_name = f"{company}_{doc_label}_{book_no}_{uuid4().hex[:8]}.pdf"
    path = directory / f"{company_id[:12]}_{file_name}"
    path.write_bytes(pdf_bytes)
    return path


def _snapshot_result(
    row: LedgerBookSnapshot,
    *,
    pdf_bytes: bytes,
    pdf_sha256: str,
    page_count: int,
    archived: bool,
) -> dict[str, Any]:
    meta = (row.payload or {}).get("meta") or {}
    doc_label = _KIND_LABELS.get(row.book_kind, "电子账簿")
    company_name = safe_book_filename_part(meta.get("companyName"), doc_label)
    return {
        "pdfBytes": pdf_bytes,
        "fileName": f"{company_name}_{doc_label}_{row.book_no}.pdf",
        "bookNo": row.book_no,
        "contentHash": row.content_hash,
        "pdfSha256": pdf_sha256,
        "pageCount": page_count,
        "status": row.status,
        "period": row.period,
        "bookKind": row.book_kind,
        "voucherCount": int(meta.get("entryCount") or meta.get("voucherCount") or 0),
        "pdfSizeBytes": len(pdf_bytes),
        "archivedPdf": archived,
        # 服务内部复用：归档记录可直接指向签发件，避免同一份 PDF 反复落盘
        "archivePath": str(row.pdf_path) if archived and row.pdf_path else None,
    }


def _stored_pdf(row: LedgerBookSnapshot) -> dict[str, Any] | None:
    """读取签发时落盘的归档件；缺失或损坏时返回 None / 抛错，不在这里重写数据库。"""
    if not row.pdf_path:
        return None
    path = Path(row.pdf_path)
    try:
        data = path.read_bytes()
    except OSError:
        logger.warning("已签发账簿 %s 的 PDF 归档件丢失：%s", row.book_no, row.pdf_path)
        return None
    digest = hashlib.sha256(data).hexdigest()
    if row.pdf_sha256 and digest != row.pdf_sha256:
        raise SnapshotError(
            "PDF_ARCHIVE_CORRUPTED",
            f"账簿 {row.book_no} 的 PDF 归档件校验不通过（sha256 不符），请从备份恢复后重试",
            status_code=409,
        )
    return _snapshot_result(
        row,
        pdf_bytes=data,
        pdf_sha256=digest,
        page_count=int(row.page_count or 1),
        archived=True,
    )


def render_snapshot_pdf_from_row(row: LedgerBookSnapshot) -> dict[str, Any]:
    """用已冻结快照取 PDF：优先读签发时落盘的归档件，缺失时只读现渲染。

    渲染器按 book_kind 分发：voucher 册冻结 payload 必须用凭证册渲染器，
    否则页数真值对不上。
    """
    stored = _stored_pdf(row)
    if stored is not None:
        return stored
    payload = row.payload or {}
    if row.book_kind == "voucher":
        rendered = render_voucher_book_pdf(
            payload,
            snapshot_status=row.status,
            book_no=row.book_no,
            content_hash=row.content_hash,
            generated_by=row.generated_by,
            generated_at=row.generated_at.isoformat() if row.generated_at else None,
            void_reason=row.void_reason,
            voided_at=row.voided_at.isoformat() if row.voided_at else None,
        )
    else:
        rendered = render_ledger_book_pdf(
            payload,
            snapshot_status=row.status,
            book_no=row.book_no,
            content_hash=row.content_hash,
            generated_by=row.generated_by,
            generated_at=row.generated_at.isoformat() if row.generated_at else None,
            void_reason=row.void_reason,
            voided_at=row.voided_at.isoformat() if row.voided_at else None,
        )
    return _snapshot_result(
        row,
        pdf_bytes=rendered.pdf_bytes,
        pdf_sha256=rendered.pdf_sha256,
        page_count=rendered.page_count,
        archived=False,
    )


async def render_snapshot_pdf_from_row_async(row: LedgerBookSnapshot) -> dict[str, Any]:
    """异步版本：归档件命中就走文件读取，未命中再进线程池渲染。"""
    stored = _stored_pdf(row)
    if stored is not None:
        return stored
    payload = row.payload or {}
    if row.book_kind == "voucher":
        rendered = await render_voucher_book_pdf_async(
            payload,
            snapshot_status=row.status,
            book_no=row.book_no,
            content_hash=row.content_hash,
            generated_by=row.generated_by,
            generated_at=row.generated_at.isoformat() if row.generated_at else None,
            void_reason=row.void_reason,
            voided_at=row.voided_at.isoformat() if row.voided_at else None,
        )
    else:
        rendered = await render_ledger_book_pdf_async(
            payload,
            snapshot_status=row.status,
            book_no=row.book_no,
            content_hash=row.content_hash,
            generated_by=row.generated_by,
            generated_at=row.generated_at.isoformat() if row.generated_at else None,
            void_reason=row.void_reason,
            voided_at=row.voided_at.isoformat() if row.voided_at else None,
        )
    return _snapshot_result(
        row,
        pdf_bytes=rendered.pdf_bytes,
        pdf_sha256=rendered.pdf_sha256,
        page_count=rendered.page_count,
        archived=False,
    )


async def render_snapshot_pdf(company_id: str, snapshot_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        row = await session.scalar(
            select(LedgerBookSnapshot).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.id == snapshot_id,
            )
        )
    if row is None:
        raise SnapshotError("SNAPSHOT_NOT_FOUND", "账簿快照不存在", status_code=404)
    return await render_snapshot_pdf_from_row_async(row)


async def find_snapshot_pdf_sha_by_path(company_id: str, path: str) -> str | None:
    """归档记录下载前的完整性凭据：该文件若是签发件，返回其登记 sha256。"""
    if not path:
        return None
    async with session_scope() as session:
        return await session.scalar(
            select(LedgerBookSnapshot.pdf_sha256).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.pdf_path == path,
                LedgerBookSnapshot.pdf_sha256.is_not(None),
            )
        )


async def find_matching_issued_snapshot(
    company_id: str,
    *,
    book_kind: str,
    period: str,
    content_hash_value: str,
) -> LedgerBookSnapshot | None:
    """查找同期间、未作废且内容指纹与当前实时账完全一致的最新签发快照。"""
    if not content_hash_value:
        return None
    async with session_scope() as session:
        return (
            await session.execute(
                select(LedgerBookSnapshot)
                .where(
                    LedgerBookSnapshot.company_id == company_id,
                    LedgerBookSnapshot.book_kind == book_kind,
                    LedgerBookSnapshot.period == period,
                    LedgerBookSnapshot.status == "issued",
                    LedgerBookSnapshot.content_hash == content_hash_value,
                )
                .order_by(LedgerBookSnapshot.generated_at.desc(), LedgerBookSnapshot.id)
            )
        ).scalars().first()


async def void_snapshot(
    company_id: str,
    snapshot_id: str,
    *,
    reason: str,
    user_id: str,
    request_id: str = "",
) -> dict[str, Any]:
    if not reason.strip():
        raise SnapshotError("REASON_REQUIRED", "作废必须填写原因")
    async with session_scope() as session:
        row = await session.scalar(
            select(LedgerBookSnapshot).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.id == snapshot_id,
            )
        )
        if row is None:
            raise SnapshotError("SNAPSHOT_NOT_FOUND", "账簿快照不存在", status_code=404)
        if row.status == "void":
            return _row_to_dict(row)
        before = {"status": row.status}
        row.status = "void"
        row.void_reason = reason.strip()
        row.voided_by = user_id
        row.voided_at = datetime.now(timezone.utc)
        await record_audit(
            session,
            company_id=company_id,
            user_id=user_id,
            action="void_ledger_book",
            entity_type="ledger_book_snapshot",
            entity_id=row.id,
            request_id=request_id or f"void-book-{row.book_no}",
            before=before,
            after={"status": "void", "reason": row.void_reason},
            reason=reason.strip(),
        )
        await session.flush()
        return _row_to_dict(row)


def _as_date(value: str):
    from datetime import date

    try:
        year, month, day = (int(part) for part in value.split("-", 2))
        return date(year, month, day)
    except (TypeError, ValueError) as exc:
        raise SnapshotError("INVALID_PERIOD", "期间格式应为 YYYY-MM") from exc


def _balance_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["accountCode"]: item for item in (payload.get("balance") or {}).get("items", [])}


def _zero_row(code: str) -> dict[str, Any]:
    return {
        "accountCode": code,
        "accountName": code,
        "openingBalance": "0.00",
        "debitTotal": "0.00",
        "creditTotal": "0.00",
        "closingBalance": "0.00",
    }


async def diff_snapshots(company_id: str, snapshot_id: str, against_id: str) -> dict[str, Any]:
    """两册账簿的科目级差异。

    反结账补记后重签，只回答"新册和旧册差在哪"是不够的——审计与复核要的是
    逐科目的差额和新增/减少的凭证号。这里按科目编码做并集比较（某册因口径
    过滤没列出的科目按 0 参与计算），只在真的有差异时才返回该行。
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(LedgerBookSnapshot).where(
                    LedgerBookSnapshot.company_id == company_id,
                    LedgerBookSnapshot.id.in_((snapshot_id, against_id)),
                )
            )
        ).scalars().all()

    by_id = {row.id: row for row in rows}
    missing = [i for i in (snapshot_id, against_id) if i not in by_id]
    if missing:
        raise SnapshotError("SNAPSHOT_NOT_FOUND", f"账簿快照不存在：{', '.join(missing)}", status_code=404)

    base_row = by_id[against_id]
    target_row = by_id[snapshot_id]
    base = base_row.payload or {}
    target = target_row.payload or {}

    base_items = _balance_index(base)
    target_items = _balance_index(target)
    deltas: list[dict[str, Any]] = []
    for code in sorted(set(base_items) | set(target_items)):
        left = base_items.get(code) or _zero_row(code)
        right = target_items.get(code) or _zero_row(code)
        diff = {
            key: money(right[key]) - money(left[key])
            for key in ("openingBalance", "debitTotal", "creditTotal", "closingBalance")
        }
        if any(diff.values()):
            deltas.append(
                {
                    "accountCode": code,
                    "accountName": right.get("accountName") or left.get("accountName") or code,
                    "openingDelta": money_text(diff["openingBalance"]),
                    "debitDelta": money_text(diff["debitTotal"]),
                    "creditDelta": money_text(diff["creditTotal"]),
                    "closingDelta": money_text(diff["closingBalance"]),
                }
            )

    base_vouchers = {i.get("voucherRef") for i in (base.get("chronological") or {}).get("items", []) if i.get("voucherRef")}
    target_vouchers = {i.get("voucherRef") for i in (target.get("chronological") or {}).get("items", []) if i.get("voucherRef")}

    return {
        "period": target_row.period,
        "base": {
            "id": base_row.id,
            "bookNo": base_row.book_no,
            "contentHash": base_row.content_hash,
            "generatedAt": base_row.generated_at.isoformat() if base_row.generated_at else None,
        },
        "target": {
            "id": target_row.id,
            "bookNo": target_row.book_no,
            "contentHash": target_row.content_hash,
            "generatedAt": target_row.generated_at.isoformat() if target_row.generated_at else None,
        },
        "hashEqual": base_row.content_hash == target_row.content_hash,
        "pageCountDelta": (target.get("meta") or {}).get("pageCount", 0) - (base.get("meta") or {}).get("pageCount", 0),
        "balance": deltas,
        "vouchersAdded": sorted(target_vouchers - base_vouchers),
        "vouchersRemoved": sorted(base_vouchers - target_vouchers),
    }
