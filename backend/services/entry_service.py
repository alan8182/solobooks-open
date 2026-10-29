# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Compatibility facade over the authoritative structured accounting core.

Importers keep using this module, but every write now lands in
``source_transactions`` + ``journal_entries`` + ``journal_lines`` in one
transaction. The legacy ``entries`` table is only a generated read projection.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from db.base import Base
from db.session import get_engine, session_scope
import db.models  # noqa: F401
from db.models import JournalEntry, SourceTransaction
from services.account_mapper import get_voucher_hint
from services.accounting_core_service import (
    _sync_projection,
    apply_rd_detail_to_voucher,
    create_entry,
    delete_draft,
    ensure_tenant,
    get_journal,
    list_journals,
    serialize_journal,
    update_draft,
)


def _deterministic_entry(entry: Dict[str, Any], *, trusted_voucher: bool = False) -> Dict[str, Any]:
    """Discard untrusted voucher lines while preserving known system vouchers.

    信任只能由服务端代码以函数参数传入，**不能读 payload 里的 ``voucherAuthority``**：
    payload 会经过 HTTP / MCP / Agent 多层透传，任何客户端都能塞这个字段，
    一旦把它当信任标志，就等于让外部决定借贷科目。

    ``wechat_settlement`` 保持例外：它的凭证行由微信账单服务在本进程内构造，
    且 source 值不是客户端可自由指定的通用入口。
    """
    prepared = dict(entry)
    trusted = trusted_voucher or prepared.get("source") == "wechat_settlement"
    prepared.pop("voucherAuthority", None)
    if not trusted:
        prepared["voucherLines"] = apply_rd_detail_to_voucher(prepared, get_voucher_hint(
            str(prepared.get("direction") or "expense"),
            str(prepared.get("category") or "其他支出"),
            prepared.get("amount") or 0,
            str(prepared.get("payer") or "company_account"),
            vat_rate=prepared.get("vatRate") if prepared.get("vatSeparated") else None,
        ))
    else:
        prepared = {**prepared, "voucherLines": apply_rd_detail_to_voucher(
            prepared, prepared.get("voucherLines") or {}
        )}
    return prepared


async def _ensure_schema() -> None:
    """Tests may create the schema; production schema is Alembic-only."""
    if os.getenv("ACCOUNTING_TEST_MODE") == "1":
        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


async def save_entry(
    entry: Dict[str, Any],
    *,
    user_id: str,
    company_id: str,
    confirm: bool = False,
    trusted_voucher: bool = False,
    allow_backfill: bool = False,
    backfill_reason: Optional[str] = None,
) -> Dict[str, Any]:
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        entry = _deterministic_entry(entry, trusted_voucher=trusted_voucher)
        entry_id = str(entry.get("id") or uuid4())
        entry = {**entry, "id": entry_id}
        if allow_backfill:
            entry["allowBackfill"] = True
            entry["backfillReason"] = backfill_reason or (
                f"关账后导入（业务日期 {str(entry.get('occurDate') or '')[:10]}）"
            )
        try:
            existing = await get_journal(session, entry_id, company_id=company_id, lock=True)
        except AccountingError as exc:
            if exc.code != NOT_FOUND:
                raise
            existing = None
        if existing is not None:
            if existing.status in {"draft", "pending_review"}:
                existing = await update_draft(
                    session,
                    entry_id,
                    entry,
                    company_id=company_id,
                    user_id=user_id,
                    request_id=f"compat-update-{uuid4()}",
                )
            return await serialize_journal(session, existing)
        created = await create_entry(
            session,
            entry,
            company_id=company_id,
            user_id=user_id,
            request_id=f"compat-create-{uuid4()}",
            confirm=confirm,
        )
        return await serialize_journal(session, created)


async def save_entries_bulk(
    entries: List[Dict[str, Any]],
    *,
    user_id: str,
    company_id: str,
    existing_entry_updates: Optional[List[Dict[str, Any]]] = None,
    trusted_voucher: bool = False,
    allow_backfill: bool = False,
    backfill_reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """批量落库（微信账单等历史数据导入）。

    allow_backfill：单据日期所属期间已关账时是否补记到最早的未关账期间。
    批量导入是用户对"一批历史流水"的显式动作，UI 无法逐行询问，因此由调用方
    一次性决定；补记行都会进 period_backfills 台账，并在结账检查里提示。
    """
    existing_entry_updates = existing_entry_updates or []
    results: list[dict[str, Any]] = []
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        for raw in entries:
            raw = _deterministic_entry(raw, trusted_voucher=trusted_voucher)
            entry_id = str(raw.get("id") or uuid4())
            payload = {**raw, "id": entry_id}
            if allow_backfill:
                payload["allowBackfill"] = True
                payload["backfillReason"] = backfill_reason or (
                    f"关账后批量导入（业务日期 {str(raw.get('occurDate') or '')[:10]}）"
                )
            try:
                existing = await get_journal(session, entry_id, company_id=company_id, lock=True)
            except AccountingError as exc:
                if exc.code != NOT_FOUND:
                    raise
                existing = None
            if existing is None:
                existing = await create_entry(
                    session,
                    payload,
                    company_id=company_id,
                    user_id=user_id,
                    request_id=f"bulk-create-{uuid4()}",
                    confirm=False,
                )
            results.append(await serialize_journal(session, existing))

        for update in existing_entry_updates:
            entry_id = str(update.get("id") or "").strip()
            if not entry_id:
                continue
            try:
                target = await get_journal(session, entry_id, company_id=company_id, lock=True)
            except AccountingError as exc:
                if exc.code == NOT_FOUND:
                    continue
                raise
            if target.source_transaction_id:
                source = await session.get(SourceTransaction, target.source_transaction_id)
                if source:
                    raw_payload = dict(source.raw_payload or {})
                    raw_payload.update(
                        {
                            "billingStatus": str(update.get("billingStatus") or "reconciled"),
                            "reconciliationType": str(update.get("reconciliationType") or "internal_refund"),
                        }
                    )
                    source.raw_payload = raw_payload
                    source.status = "reconciled"
                    await session.flush()
                    await _sync_projection(session, target)
    return results


async def delete_entry(entry_id: str, *, user_id: str, company_id: str) -> bool:
    async with session_scope() as session:
        try:
            await delete_draft(
                session,
                entry_id,
                company_id=company_id,
                user_id=user_id,
                request_id=f"compat-delete-{uuid4()}",
            )
            return True
        except AccountingError as exc:
            if exc.code == NOT_FOUND:
                return False
            raise


async def get_entry(entry_id: str, *, user_id: str, company_id: str) -> Optional[Dict[str, Any]]:
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        try:
            entry = await get_journal(session, entry_id, company_id=company_id)
        except AccountingError as exc:
            if exc.code == NOT_FOUND:
                return None
            raise
        return await serialize_journal(session, entry)


async def update_entry(
    entry_id: str,
    updates: Dict[str, Any],
    *,
    user_id: str,
    company_id: str,
) -> Optional[Dict[str, Any]]:
    async with session_scope() as session:
        try:
            entry = await update_draft(
                session,
                entry_id,
                updates,
                company_id=company_id,
                user_id=user_id,
                request_id=f"compat-update-{uuid4()}",
            )
        except AccountingError as exc:
            if exc.code == NOT_FOUND:
                return None
            raise
        return await serialize_journal(session, entry)


async def generate_entry_vouchers(
    entry_ids: List[str],
    *,
    user_id: str,
    company_id: str,
) -> List[Dict[str, Any]]:
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry_id in entry_ids:
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            try:
                entry = await get_journal(session, entry_id, company_id=company_id)
            except AccountingError as exc:
                if exc.code == NOT_FOUND:
                    continue
                raise
            output.append(await serialize_journal(session, entry))
        return output


async def list_entries(user_id: str, company_id: str, *, lite: bool = False) -> List[Dict[str, Any]]:
    """lite=True：跳过分录行序列化（待办派生等只读 metadata 级字段的消费方）。"""
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        return await list_journals(session, company_id, lite=lite)
