# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产存量迁移：legacy payload-blob 表 → 核心 ORM 卡片 + 折旧计划。

同发票回填模式（invoice_ledger_service）：
- 一次性按公司回填，完成在 auth_companies.payload 打 ``fixed_asset_backfill``
  标记，之后零成本跳过；force=True 可重跑（幂等：卡片 id 取
  ``fa-mig-{legacy_id}``，已存在即跳过）；
- 回填前三方勾稽：legacy 卡片原值合计 vs 1601 余额、已提折旧合计 vs 1602
  余额（复用期初的 0.05 容差），不平拒绝迁移并打 mismatch 标记——legacy 表
  只读保留，账先修平再回填；
- legacy depreciation_schedule 行原样重建为 posted（journal_entry_id 指向
  原 legacy 折旧凭证），planned 行从下一期续建、末期兜平；
- 一次性折旧卡：已全额计提 → fully_depreciated 无剩余计划；未计提 → 1 期
  全额计划。

口径说明：迁移不改历史凭证；卡片累计折旧以 legacy payload 为准，计划行的
accumulated 链从卡片累计续写。类别按中文目录映射（core/fixed_asset_categories），
映射不到的落 other；年限超出新目录范围的存量卡片收紧到目录边界（只影响
剩余期间的分布，历史已提不变）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.fixed_asset_categories import CATEGORY_LABELS, get_category
from core.money import ZERO, money
from db.models import FixedAsset, FixedAssetDepreciationSchedule, JournalEntry, RdProject
from services.database import get_conn
from services.json_utils import json_dumps
from services.fixed_asset_service import (
    _add_months,
    _period_diff,
    _period_of,
    _next_code,
    build_schedule_rows,
    first_depreciation_period,
)

_BACKFILL_FLAG = "fixed_asset_backfill"
_TOLERANCE = Decimal("0.05")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_date(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()[:10]
    if len(raw) == 10 and raw[4] == "-":
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            pass
    return fallback


def _card_cost(payload: dict[str, Any]) -> Decimal:
    return money(payload.get("cost") or payload.get("originalValue") or ZERO)


def _card_accumulated(payload: dict[str, Any]) -> Decimal:
    return money(payload.get("accumulated_depreciation") or payload.get("accumulatedDepreciation") or ZERO)


def _card_months(payload: dict[str, Any]) -> int:
    months = payload.get("useful_months") or payload.get("usefulMonths")
    if not months:
        years = payload.get("usefulLife") or 5
        months = int(years) * 12
    return max(1, int(months))


async def _read_flag(company_id: str) -> dict[str, Any] | None:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    if not row:
        return None
    try:
        data = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"] or {})
    except (json.JSONDecodeError, TypeError):
        return None
    return data.get(_BACKFILL_FLAG) or None


async def _write_flag(company_id: str, value: dict[str, Any]) -> None:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        data = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"] or {})
        data[_BACKFILL_FLAG] = value
        await conn.execute(
            "UPDATE auth_companies SET payload = ? WHERE company_id = ?",
            (json_dumps(data, ensure_ascii=False), company_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _read_legacy_cards(company_id: str) -> list[dict[str, Any]]:
    """legacy_fixed_assets 只读；表不存在（新账套/已退役）返回空。"""
    conn = await get_conn()
    try:
        try:
            cursor = await conn.execute(
                "SELECT id, payload FROM legacy_fixed_assets WHERE company_id = ? ORDER BY created_at",
                (company_id,),
            )
        except Exception:
            return []
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    cards: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else (row["payload"] or {})
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            payload["_legacy_id"] = row["id"]
            cards.append(payload)
    return cards


async def _read_legacy_schedule(company_id: str, legacy_asset_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not legacy_asset_ids:
        return {}
    conn = await get_conn()
    result: dict[str, list[dict[str, Any]]] = {}
    try:
        try:
            cursor = await conn.execute(
                "SELECT asset_id, period, depreciation_amount, accumulated_before, accumulated_after, voucher_ref "
                "FROM legacy_depreciation_schedule WHERE company_id = ? ORDER BY period",
                (company_id,),
            )
            rows = await cursor.fetchall()
        except Exception:
            return {}
        wanted = set(legacy_asset_ids)
        for row in rows:
            if row["asset_id"] in wanted:
                result.setdefault(row["asset_id"], []).append(dict(row))
    finally:
        await conn.close()
    return result


async def _account_balance(session: AsyncSession, company_id: str, code: str) -> Decimal:
    from db.models import JournalLine
    from services.ledger_service import POSTED_STATUSES

    totals = (
        await session.execute(
            select(
                sa_func.coalesce(sa_func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                sa_func.coalesce(sa_func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.account_code == code,
                JournalEntry.status.in_(POSTED_STATUSES),
            )
        )
    ).one()
    return money(Decimal(str(totals[0])) - Decimal(str(totals[1])))


async def backfill_fixed_assets(company_id: str, *, force: bool = False) -> dict[str, Any]:
    """把 legacy 固定资产卡片与折旧计划回填进 ORM（幂等、勾稽不过拒绝迁移）。"""
    if not force:
        flag = await _read_flag(company_id)
        if flag:
            return {"ok": flag.get("status") == "done", "skipped": True, **flag}

    from db.session import session_scope

    legacy_cards = await _read_legacy_cards(company_id)
    if not legacy_cards:
        result = {"ok": True, "status": "done", "migrated": 0, "note": "无 legacy 固定资产"}
        await _write_flag(company_id, {"at": _now().isoformat(), **result})
        return result

    # ── 回填前勾稽：卡片 vs 总账（复用期初 0.05 容差，不平拒绝迁移）──────
    async with session_scope() as session:
        balance_1601 = await _account_balance(session, company_id, "1601")
        balance_1602 = money(-await _account_balance(session, company_id, "1602"))  # 1602 贷方余额为正
        known_rd = {
            p.id
            for p in (
                await session.execute(select(RdProject.id).where(RdProject.company_id == company_id))
            ).scalars().all()
        }
        known_entries: set[str] = set()

        total_cost = sum((_card_cost(p) for p in legacy_cards), ZERO)
        total_depr = sum((_card_accumulated(p) for p in legacy_cards), ZERO)
        if abs(total_cost - balance_1601) > _TOLERANCE or abs(total_depr - balance_1602) > _TOLERANCE:
            result = {
                "ok": False,
                "status": "mismatch",
                "cardCostTotal": money(total_cost),
                "ledger1601": money(balance_1601),
                "cardDepreciationTotal": money(total_depr),
                "ledger1602": money(balance_1602),
                "note": "卡片与总账勾稽不平，已拒绝迁移；请先修平账务后手动重跑",
            }
            await _write_flag(company_id, {"at": _now().isoformat(), **result})
            return result

        legacy_ids = [str(p.get("_legacy_id")) for p in legacy_cards]
        legacy_schedule = await _read_legacy_schedule(company_id, legacy_ids)

        # 已迁移卡片（幂等重跑）
        existing_ids = set(
            (
                await session.execute(
                    select(FixedAsset.id).where(
                        FixedAsset.company_id == company_id,
                        FixedAsset.id.like("fa-mig-%"),
                    )
                )
            ).scalars().all()
        )
        existing_entry_ids = set()
        candidate_sources = [
            str(p.get("sourceEntryId") or "") for p in legacy_cards if p.get("sourceEntryId")
        ]
        if candidate_sources:
            existing_entry_ids = set(
                (
                    await session.execute(
                        select(FixedAsset.source_entry_id).where(
                            FixedAsset.company_id == company_id,
                            FixedAsset.source_entry_id.in_(candidate_sources),
                            FixedAsset.deleted_at.is_(None),
                        )
                    )
                ).scalars().all()
            )

        current_period = _period_of(_now().date())
        migrated = 0
        errors: list[str] = []
        for payload in legacy_cards:
            legacy_id = str(payload.get("_legacy_id"))
            card_id = f"fa-mig-{legacy_id}"
            if card_id in existing_ids:
                continue
            source_entry_id = str(payload.get("sourceEntryId") or "").strip() or None
            if source_entry_id and source_entry_id in existing_entry_ids:
                # 同一期初凭证已挂过卡（重复 legacy 行），只记跳过
                errors.append(f"{payload.get('name') or legacy_id}: 期初凭证已关联其他卡片，跳过")
                continue
            entry = await session.get(JournalEntry, source_entry_id) if source_entry_id else None
            if source_entry_id and (entry is None or entry.company_id != company_id):
                source_entry_id = None  # 引用失效的来源凭证不迁移挂接

            original = _card_cost(payload)
            if original <= ZERO:
                errors.append(f"{payload.get('name') or legacy_id}: 原值非正，跳过")
                continue
            accumulated = _card_accumulated(payload)
            category_label = str(payload.get("category") or "").strip()
            category = CATEGORY_LABELS.get(category_label, "other")
            category_def = get_category(category)

            # 一次性折旧语义迁移：已提 → fully_depreciated；未提 → one_time；
            # 部分已提（历史执行了多次 run 的脏数据）回退直线法
            months = _card_months(payload)
            is_one_time = bool(payload.get("oneTimeDepreciation"))
            method = "one_time" if is_one_time else "straight_line"
            depreciable = money(original - money(original * Decimal(str(payload.get("salvageRate") or payload.get("residualValueRate") or 0))))
            if is_one_time:
                residual = ZERO
                depreciable = money(original)
                if accumulated > ZERO and accumulated < depreciable:
                    method = "straight_line"
                    is_one_time = False
            else:
                residual = money(original * Decimal(str(payload.get("salvageRate") or payload.get("residualValueRate") or 0)))
                if residual >= original:
                    residual = ZERO

            status_raw = str(payload.get("status") or "in_use")
            status_map = {"in_use": "active", "idle": "idle", "disposed": "disposed", "scrapped": "scrapped"}
            status = status_map.get(status_raw, "active")
            if accumulated >= depreciable and status == "active":
                status = "fully_depreciated"

            acquisition_date = _legacy_date(
                payload.get("purchaseDate") or payload.get("purchase_date"), f"{current_period}-01"
            )
            available_date = _legacy_date(payload.get("startDate") or payload.get("start_date"), acquisition_date)

            # 年限收紧到目录边界（只影响剩余期间分布）
            first_period = first_depreciation_period(datetime.strptime(available_date, "%Y-%m-%d").date())
            posted_rows = legacy_schedule.get(legacy_id, [])
            life = months
            if method == "straight_line":
                life = max(category_def.life_min_months, min(category_def.life_max_months, months))
                if posted_rows:
                    # 至少容纳已提期次 + 1 期剩余
                    life = max(life, len(posted_rows) + 1)

            rd_project_id = str(payload.get("rdProjectId") or "").strip() or None
            rd_use_rate = payload.get("rdUseRate")
            rd_basis = str(payload.get("rdAllocationBasis") or "").strip() or None
            if rd_project_id and rd_project_id not in known_rd:
                rd_project_id = rd_use_rate = rd_basis = None

            posted_rows = legacy_schedule.get(legacy_id, [])
            last_posted_period = posted_rows[-1]["period"] if posted_rows else None
            if last_posted_period is None and accumulated > ZERO:
                # 无计划历史但有累计（期初带入）：锚在「当前期」的前一期，
                # 剩余计划从本期起继续计提
                last_posted_period = _add_months(current_period, -1)

            code = await _next_code(session, company_id, datetime.strptime(acquisition_date, "%Y-%m-%d").date())
            now = _now()
            asset = FixedAsset(
                id=card_id,
                company_id=company_id,
                code=code,
                name=str(payload.get("name") or f"固定资产-{legacy_id[-6:]}")[:255],
                category=category,
                acquisition_type="purchase",
                acquisition_date=datetime.strptime(acquisition_date, "%Y-%m-%d").date(),
                available_for_use_date=datetime.strptime(available_date, "%Y-%m-%d").date(),
                original_value=original,
                residual_value=residual,
                residual_rate=(residual / original) if original else ZERO,
                useful_life_months=life,
                depreciation_method=method,
                accumulated_depreciation=money(accumulated),
                last_depreciated_period=last_posted_period,
                status=status,
                source_entry_id=source_entry_id,
                location=str(payload.get("location") or "").strip() or None,
                custodian=str(payload.get("custodian") or "").strip() or None,
                rd_project_id=rd_project_id,
                rd_use_rate=(Decimal(str(rd_use_rate)) if rd_use_rate is not None else None),
                rd_allocation_basis=rd_basis,
                created_by="system:backfill",
                updated_by="system:backfill",
                created_at=now,
                updated_at=now,
            )
            session.add(asset)
            await session.flush()

            # 重建 posted 计划行：期次号按起提期推算（legacy 行不一定从第 1 期开始）
            posted_max_seq = 0
            prev_seq = 0
            for row in posted_rows:
                amount = money(row.get("depreciation_amount") or 0)
                if amount <= ZERO:
                    continue
                row_period = str(row["period"])[:7]
                seq = max(prev_seq + 1, max(1, _period_diff(first_period, row_period) + 1))
                prev_seq = seq
                posted_max_seq = max(posted_max_seq, seq)
                voucher_ref = str(row.get("voucher_ref") or "") or None
                session.add(
                    FixedAssetDepreciationSchedule(
                        company_id=company_id,
                        asset_id=asset.id,
                        period_start=row_period,
                        sequence_no=seq,
                        amount=amount,
                        accumulated_before=money(row.get("accumulated_before") or 0),
                        accumulated_after=money(row.get("accumulated_after") or 0),
                        status="posted",
                        journal_entry_id=voucher_ref,
                        posted_at=now,
                        created_at=now,
                    )
                )

            # 剩余 planned 行：从最后已提期的下一期续建，末期兜平
            if status in ("active", "idle"):
                if method == "one_time":
                    # 未计提的一次性折旧卡：全额计划锚当前期（首期已过去，
                    # 锚历史期会让 planned 行永远跑不到）
                    one_time_start = current_period if first_period < current_period else first_period
                    for planned in build_schedule_rows(asset, start_period=one_time_start):
                        session.add(FixedAssetDepreciationSchedule(**planned))
                else:
                    planned_start = (
                        _add_months(str(last_posted_period), 1)
                        if last_posted_period
                        else (current_period if accumulated > ZERO else None)
                    )
                    for planned in build_schedule_rows(
                        asset,
                        start_period=planned_start,
                        sequence_start=posted_max_seq + 1,
                        accumulated_start=money(accumulated),
                    ):
                        session.add(FixedAssetDepreciationSchedule(**planned))

            if source_entry_id:
                existing_entry_ids.add(source_entry_id)
            existing_ids.add(card_id)
            migrated += 1

        await session.flush()

    result = {
        "ok": True,
        "status": "done",
        "migrated": migrated,
        "skippedIssues": errors[:20],
        "issueCount": len(errors),
    }
    await _write_flag(company_id, {"at": _now().isoformat(), **result})
    return result


async def ensure_backfilled(company_id: str) -> None:
    """首次访问台账时补一次回填；已打标则零成本跳过。"""
    flag = await _read_flag(company_id)
    if flag:
        return
    try:
        await backfill_fixed_assets(company_id)
    except AccountingError:
        return  # 勾稽不平的公司保持未打标，可修数后手动重跑
