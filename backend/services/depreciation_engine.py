from services.json_utils import json_dumps
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.database import get_conn
from core.money import ZERO, money, rate
from services.entry_service import save_entry

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS depreciation_schedule ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  asset_id TEXT NOT NULL,"
            "  period TEXT NOT NULL,"
            "  depreciation_amount NUMERIC(20,2) NOT NULL DEFAULT 0,"
            "  accumulated_before NUMERIC(20,2) NOT NULL DEFAULT 0,"
            "  accumulated_after NUMERIC(20,2) NOT NULL DEFAULT 0,"
            "  voucher_ref TEXT,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dep_schedule_company ON depreciation_schedule(company_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dep_schedule_asset ON depreciation_schedule(asset_id)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dep_schedule_unique ON depreciation_schedule(asset_id, period)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def calculate_monthly_depreciation(asset: Dict[str, Any]) -> Decimal:
    """Calculate monthly depreciation for a single asset using straight-line method."""
    original_value = money(asset.get("originalValue") or ZERO)
    if original_value <= ZERO:
        return ZERO

    # One-time depreciation: full amount
    if asset.get("oneTimeDepreciation"):
        return original_value

    useful_life = int(asset.get("usefulLife", 5))
    if useful_life <= 0:
        return ZERO

    residual_rate = rate(asset.get("residualValueRate") or "0.05")
    salvage = original_value * residual_rate
    depreciable_base = original_value - salvage
    return money(depreciable_base / Decimal(useful_life * 12))


async def run_depreciation(company_id: str, period: str, *, user_id: str) -> dict:
    """Run depreciation calculation for all in_use assets for the given period (YYYY-MM).

    Creates depreciation schedule entries and returns summary.
    """
    await _ensure_schema()

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload, id FROM fixed_assets WHERE company_id = ?",
            (company_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    assets: List[Dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            payload["_id"] = row["id"]
            assets.append(payload)
        except (json.JSONDecodeError, TypeError):
            continue

    total_depreciation = ZERO
    processed_count = 0
    skipped_count = 0
    entries: List[Dict[str, Any]] = []

    for asset in assets:
        status = asset.get("status", "in_use")
        if status != "in_use":
            skipped_count += 1
            continue

        # Check if already calculated for this period
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT id FROM depreciation_schedule WHERE asset_id = ? AND period = ?",
                (asset["_id"], period),
            )
            existing = await cursor.fetchone()
        finally:
            await conn.close()

        if existing:
            skipped_count += 1
            continue

        last_dep = asset.get("lastDepreciationDate", "")

        monthly = await calculate_monthly_depreciation(asset)
        if monthly <= ZERO:
            skipped_count += 1
            continue

        accumulated_before = money(asset.get("accumulatedDepreciation") or ZERO)
        accumulated_after = money(accumulated_before + monthly)
        total_depreciation = money(total_depreciation + monthly)

        # Create schedule record
        await _ensure_schema()
        now = _now_iso()
        schedule_id = f"dep-{asset['_id']}-{period}"

        voucher_entry_id = f"depreciation-{asset['_id']}-{period}"
        rd_project_id = str(asset.get("rdProjectId") or "").strip()
        rd_use_rate = rate(asset.get("rdUseRate") or ZERO)
        rd_basis = str(asset.get("rdAllocationBasis") or "").strip()
        debit_lines = []
        rd_depreciation = ZERO
        if rd_project_id and rd_use_rate > ZERO:
            rd_depreciation = money(monthly * rd_use_rate)
            if rd_depreciation > ZERO:
                debit_lines.append({
                    "accountCode": "530103",
                    "accountName": "研发支出-折旧与摊销",
                    "amount": rd_depreciation,
                    "rdDetail": {
                        "projectId": rd_project_id,
                        "expenseType": "depreciation_amortization",
                        "originalAmount": monthly,
                        "allocationMethod": "usage",
                        "allocationBasis": rd_basis,
                        "notes": f"{period} {asset.get('name', '')}研发使用折旧",
                    },
                })
        admin_depreciation = money(monthly - rd_depreciation)
        if admin_depreciation > ZERO:
            debit_lines.append({"accountCode": "5699", "accountName": "管理费用-折旧费", "amount": admin_depreciation})
        await save_entry(
            {
                "id": voucher_entry_id,
                "title": f"计提折旧-{asset.get('name', '')}",
                "amount": monthly,
                "direction": "expense",
                "category": "折旧",
                "occurDate": f"{period}-01",
                "source": "fixed_asset_depreciation",
                "sourceType": "fixed_asset_depreciation",
                "dedupFingerprint": voucher_entry_id,
                "voucherAuthority": "system",
                "voucherLines": {
                    "debit": debit_lines,
                    "credit": [{"accountCode": "1602", "accountName": "累计折旧", "amount": monthly}],
                },
            },
            user_id=user_id,
            company_id=company_id,
        )

        conn = await get_conn()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO depreciation_schedule "
                "(id, company_id, asset_id, period, depreciation_amount, accumulated_before, accumulated_after, voucher_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (schedule_id, company_id, asset["_id"], period, monthly, accumulated_before, accumulated_after, voucher_entry_id, now),
            )
            await conn.commit()
        finally:
            await conn.close()

        # Update asset accumulated depreciation
        asset["accumulatedDepreciation"] = accumulated_after
        asset["lastDepreciationDate"] = period
        asset["updated_at"] = now

        conn = await get_conn()
        try:
            await conn.execute(
                "UPDATE fixed_assets SET payload = ?, updated_at = ? WHERE id = ?",
                (json_dumps(asset, ensure_ascii=False), now, asset["_id"]),
            )
            await conn.commit()
        finally:
            await conn.close()

        entries.append({
            "asset_id": asset["_id"],
            "asset_name": asset.get("name", ""),
            "amount": monthly,
            "accumulated_before": accumulated_before,
            "accumulated_after": accumulated_after,
            "voucher_ref": voucher_entry_id,
        })
        processed_count += 1

    return {
        "period": period,
        "total_depreciation": money(total_depreciation),
        "processed_count": processed_count,
        "skipped_count": skipped_count,
        "entries": entries,
    }


async def get_depreciation_schedule(company_id: str, asset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get depreciation schedule for an asset or all assets."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        if asset_id:
            cursor = await conn.execute(
                "SELECT * FROM depreciation_schedule WHERE company_id = ? AND asset_id = ? ORDER BY period ASC",
                (company_id, asset_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM depreciation_schedule WHERE company_id = ? ORDER BY period ASC",
                (company_id,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()
