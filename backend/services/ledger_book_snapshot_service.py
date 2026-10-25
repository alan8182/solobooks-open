# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿签发与留痕。

一册账簿只有在期间结账之后才能签发：未结账的账页随时会因补记、反结账而变，
盖章装订就是在给一个会变的数字背书。签发之后：

- 完整 payload 落库，重打与原版逐字一致；
- sha256 存证，改一个分也不一样；
- 作废不删册，只标状态并留原因，旧册永远可查。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from core.money import money, money_text
from db.models import AccountingPeriod, AuthUser, LedgerBookSnapshot
from db.session import session_scope
from services.accounting_core_service import record_audit
from services.ledger_book_service import build_ledger_book, content_hash


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
) -> dict[str, Any]:
    book_kind, period = _book_period(month=month, year=year, scope=scope)
    period_start = f"{period}-01" if book_kind == "monthly" else f"{period}-01-01"

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
            "期间尚未结账，不能签发正式账簿。请先在月度结账里完成本期结账。",
            status_code=422,
        )

    book = await build_ledger_book(company_id, month=month, year=year, scope=scope)
    content_hash = _content_hash(book)
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
            "contentHash": content_hash,
            "pageCount": book["pagination"]["totalPages"],
        }
    )
    line_count = sum(len(page["lines"]) for page in book["details"])

    async with session_scope() as session:
        existing = await session.scalar(
            select(func.count(LedgerBookSnapshot.id)).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.book_kind == book_kind,
                LedgerBookSnapshot.period == period,
            )
        )
        book_no = f"{period}-{int(existing or 0) + 1:04d}"
        book["meta"]["bookNo"] = book_no

        row = LedgerBookSnapshot(
            company_id=company_id,
            book_kind=book_kind,
            period=period,
            book_no=book_no,
            status="issued",
            payload=book,
            content_hash=content_hash,
            page_count=book["pagination"]["totalPages"],
            line_count=line_count,
            anchor={
                "periodStatus": book["meta"]["periodStatus"],
                "entryCount": book["meta"]["entryCount"],
                "totalDebit": book["balance"]["totalDebit"],
                "totalCredit": book["balance"]["totalCredit"],
                "contentHash": content_hash,
            },
            generated_by=generated_by,
            generated_at=now,
        )
        session.add(row)
        await session.flush()
        await record_audit(
            session,
            company_id=company_id,
            user_id=user_id,
            action="issue_ledger_book",
            entity_type="ledger_book_snapshot",
            entity_id=row.id,
            request_id=request_id or f"issue-book-{book_no}",
            after={"bookNo": book_no, "contentHash": content_hash, "pageCount": row.page_count},
        )
        result = _row_to_dict(row, with_payload=True)
    return result


async def list_snapshots(company_id: str, *, period: str | None = None) -> list[dict[str, Any]]:
    async with session_scope() as session:
        query = select(LedgerBookSnapshot).where(LedgerBookSnapshot.company_id == company_id)
        if period:
            query = query.where(LedgerBookSnapshot.period == period)
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
