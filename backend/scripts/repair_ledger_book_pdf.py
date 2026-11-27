# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""显式修复：为已签发电子账簿补落 PDF 归档件。

用法::

    # 先看会改什么（默认 dry-run，不落盘、不写库）
    cd backend && venv/bin/python scripts/repair_ledger_book_pdf.py
    cd backend && venv/bin/python scripts/repair_ledger_book_pdf.py --company c_xxx

    # 确认后执行（打印每一册「改前 → 改后」并写审计事件）
    cd backend && venv/bin/python scripts/repair_ledger_book_pdf.py --apply

修复口径：

- 只处理 ``pdf_path`` 为空、文件丢失、哈希/大小不符的册；
- 内容一律来自库内冻结的 ``payload``，不重算余额（A3 真相唯一）；
- 已签发册渲染时带 ``issued`` 状态，作废册保留作废水印；
- 幂等：重复执行第二次不会再改任何行；
- 不删除任何旧文件：哈希不符时新写一份归档件并改指针，旧文件留作取证；
- 回滚：删除本次新生成的文件，再把 ``pdf_path/pdf_sha256/pdf_size_bytes/pdf_generated_at``
  置空即可；或直接恢复执行前的数据库与归档目录备份。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from sqlalchemy import select  # noqa: E402

from db.models import LedgerBookSnapshot  # noqa: E402
from db.session import get_session_factory  # noqa: E402
from services.accounting_core_service import record_audit  # noqa: E402
from services.ledger_book_pdf_service import render_ledger_book_pdf  # noqa: E402
from services.ledger_book_snapshot_service import (  # noqa: E402
    _write_pdf_archive,
    safe_book_filename_part,
)
from scripts.diagnose_ledger_book_pdf import classify  # noqa: E402

REQUEST_ID = "repair-ledger-book-pdf"


async def repair(company: str | None, *, apply: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        query = select(LedgerBookSnapshot).order_by(
            LedgerBookSnapshot.company_id,
            LedgerBookSnapshot.period,
            LedgerBookSnapshot.book_no,
        )
        if company:
            query = query.where(LedgerBookSnapshot.company_id == company)
        rows = (await session.execute(query)).scalars().all()

        changed = 0
        for row in rows:
            issue = classify(row)
            if issue is None:
                continue
            before = {
                "pdfPath": row.pdf_path,
                "pdfSha256": row.pdf_sha256,
                "pdfSizeBytes": row.pdf_size_bytes,
            }
            if not apply:
                print(f"[dry-run] [{issue}] {row.book_no} {row.status} -> 将重新生成并落盘")
                changed += 1
                continue

            payload = row.payload or {}
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
            path = _write_pdf_archive(row.company_id, payload, row.book_no, rendered.pdf_bytes)
            now = datetime.now(timezone.utc)
            row.pdf_path = str(path)
            row.pdf_sha256 = rendered.pdf_sha256
            row.pdf_size_bytes = len(rendered.pdf_bytes)
            row.pdf_generated_at = now
            await record_audit(
                session,
                company_id=row.company_id,
                user_id="system:repair-ledger-pdf",
                action="repair_ledger_book_pdf",
                entity_type="ledger_book_snapshot",
                entity_id=row.id,
                request_id=REQUEST_ID,
                before=before,
                after={
                    "pdfPath": row.pdf_path,
                    "pdfSha256": row.pdf_sha256,
                    "pdfSizeBytes": row.pdf_size_bytes,
                    "previousIssue": issue,
                },
                reason="补齐/重建已签发电子账簿 PDF 归档件",
            )
            changed += 1
            print(
                f"[fixed] {row.book_no} ({issue}) -> {path.name} "
                f"sha256={rendered.pdf_sha256[:16]}… size={len(rendered.pdf_bytes)}"
            )

        if apply and changed:
            await session.commit()
        elif not apply:
            await session.rollback()

    if changed == 0:
        print("没有需要修复的账簿归档件。")
    elif apply:
        print(f"\n已修复 {changed} 册，审计事件 action=repair_ledger_book_pdf request_id={REQUEST_ID}。")
    else:
        print(f"\n[dry-run] 共 {changed} 册待修复；确认后加 --apply 执行。")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐已签发电子账簿的 PDF 归档件")
    parser.add_argument("--company", default=None, help="只修复指定账套（默认全部）")
    parser.add_argument("--apply", action="store_true", help="真正写文件与数据库（默认只 dry-run）")
    args = parser.parse_args()
    asyncio.run(repair(args.company, apply=args.apply))


if __name__ == "__main__":
    main()
