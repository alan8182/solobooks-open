"""存量发票"直付"模式迁移（P2 一次性）。

背景：发票导入曾直接生成 贷1002（银行直付）凭证，配对前虚减银行存款；
其中支付平台手续费发票（财付通等）更是与微信账单手续费的重复记账。

处理规则：
1. 未配对（billingStatus != reconciled）且非归档、贷方含 1002 的 invoice_import：
   - 支付平台手续费发票（seller/item 命中关键词）→ 归档（is_archive_only=true）
     + posted 红字冲销 / draft 删除草稿
   - 其他费用发票（draft）→ 贷方改 2241 应付（待付款模式，走正常配对转销）
   - 其他费用发票（posted）→ 仅报告，人工决策
2. 已归档但仍 posted 的手续费发票 → 红字冲销

用法：本地 venv 运行，走 service 层保证审计与投影一致：
    python scripts/migrate_invoice_pending.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db.session import session_scope  # noqa: E402
from services.accounting_core_service import (  # noqa: E402
    delete_draft,
    reverse_journal,
    update_draft,
)

PLATFORM_FEE_SELLER = re.compile(r"财付通|微信支付|支付宝|连连支付|网银在线")
PLATFORM_FEE_ITEM = re.compile(r"手续费|支付服务费|技术服务费")


def _credit_codes(payload: dict) -> list[str]:
    voucher = payload.get("voucherLines") or {}
    return [str(line.get("accountCode") or "") for line in (voucher.get("credit") or [])]


def _is_platform_fee(payload: dict) -> bool:
    seller = str(payload.get("merchant") or payload.get("seller_name") or "")
    item = str(payload.get("item_name") or "")
    return bool(PLATFORM_FEE_SELLER.search(seller) or PLATFORM_FEE_ITEM.search(f"{item} {payload.get('title', '')}"))


async def migrate(dry_run: bool = False) -> None:
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT id, company_id, user_id, payload FROM entries "
            "WHERE payload::jsonb->>'source' = 'invoice_import'"
        ))).mappings().all()
        report = {"archived_reversed": [], "archived_deleted": [], "draft_to_payable": [], "posted_manual": []}
        for row in rows:
            payload = json.loads(row["payload"])
            entry_id, company_id, user_id = row["id"], row["company_id"], row["user_id"]
            already_archived = bool(payload.get("is_archive_only") or payload.get("isArchiveOnly"))
            reconciled = str(payload.get("billingStatus") or "") == "reconciled"
            credits = _credit_codes(payload)
            has_bank_credit = "1002" in credits
            if reconciled or not (has_bank_credit or already_archived):
                continue

            je = (await session.execute(text(
                "SELECT status FROM journal_entries WHERE id = :id"
            ), {"id": entry_id})).scalar_one_or_none()
            if je is None:
                continue

            if _is_platform_fee(payload):
                if je == "posted":
                    if not already_archived:
                        report["archived_reversed"].append((entry_id, payload.get("title"), str(payload.get("amount"))))
                    if not dry_run:
                        await reverse_journal(session, entry_id, company_id=company_id, user_id=user_id,
                                              request_id=f"migrate-invoice-{entry_id[:8]}",
                                              reason="支付平台手续费发票与微信账单手续费重复记账，归档冲销")
                        await _mark_archived(session, entry_id)
                elif je == "draft":
                    report["archived_deleted"].append((entry_id, payload.get("title"), str(payload.get("amount"))))
                    if not dry_run:
                        await delete_draft(session, entry_id, company_id=company_id, user_id=user_id,
                                           request_id=f"migrate-invoice-{entry_id[:8]}")
                        await _mark_archived(session, entry_id)
            elif je == "draft" and has_bank_credit:
                report["draft_to_payable"].append((entry_id, payload.get("title"), str(payload.get("amount"))))
                if not dry_run:
                    voucher = json.loads(json.dumps(payload.get("voucherLines") or {}))
                    for line in voucher.get("credit") or []:
                        if str(line.get("accountCode")) == "1002":
                            line["accountCode"] = "2241"
                            line["accountName"] = "其他应付款-发票待付款"
                    await update_draft(session, entry_id, {"voucherLines": voucher},
                                       company_id=company_id, user_id=user_id,
                                       request_id=f"migrate-invoice-{entry_id[:8]}")
            elif has_bank_credit and not already_archived:
                report["posted_manual"].append((entry_id, payload.get("title"), str(payload.get("amount"))))

        print(json.dumps(report, ensure_ascii=False, indent=2))
        action = "DRY-RUN（未执行）" if dry_run else "已执行"
        print(f"{action}: 冲销 {len(report['archived_reversed'])}、删草稿 {len(report['archived_deleted'])}、"
              f"转应付 {len(report['draft_to_payable'])}、待人工 {len(report['posted_manual'])}")


async def _mark_archived(session, entry_id: str) -> None:
    row = (await session.execute(text("SELECT payload FROM entries WHERE id = :id"), {"id": entry_id})).scalar_one_or_none()
    if row is None:
        return
    payload = json.loads(row)
    payload["is_archive_only"] = True
    payload["isArchiveOnly"] = True
    from db.models import LegacyEntryProjection

    projection = await session.get(LegacyEntryProjection, entry_id)
    if projection is not None:
        projection.payload = json.dumps(payload, ensure_ascii=False)
    await session.flush()


if __name__ == "__main__":
    asyncio.run(migrate(dry_run="--dry-run" in sys.argv))
