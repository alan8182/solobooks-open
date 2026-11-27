# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：已签发电子账簿的 PDF 归档件是否齐全、可校验。

用法::

    cd backend && venv/bin/python scripts/diagnose_ledger_book_pdf.py
    cd backend && venv/bin/python scripts/diagnose_ledger_book_pdf.py --company c_xxx

只读，不写库、不改文件。逐册输出四类问题：

1. ``pdf_path`` 为空（本功能上线前签发的历史册）
2. ``pdf_path`` 指向的文件不存在（容器重建/清理丢失）
3. 文件 sha256 与 ``pdf_sha256`` 不一致（归档件损坏或被替换）
4. 文件大小与 ``pdf_size_bytes`` 不一致

修复用 ``scripts/repair_ledger_book_pdf.py``，先诊断后修复，脚本本身不做任何修改。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
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
from db.session import get_engine, get_session_factory  # noqa: E402


def classify(row: LedgerBookSnapshot) -> str | None:
    """返回问题类型；None 表示归档件完好。"""
    if not row.pdf_path:
        return "MISSING_FIELD"
    path = Path(row.pdf_path)
    try:
        data = path.read_bytes()
    except OSError:
        return "MISSING_FILE"
    if row.pdf_sha256 and hashlib.sha256(data).hexdigest() != row.pdf_sha256:
        return "HASH_MISMATCH"
    if row.pdf_size_bytes is not None and len(data) != int(row.pdf_size_bytes):
        return "SIZE_MISMATCH"
    return None


async def diagnose(company: str | None) -> int:
    factory = get_session_factory()
    total = 0
    async with factory() as session:
        query = select(LedgerBookSnapshot).order_by(
            LedgerBookSnapshot.company_id,
            LedgerBookSnapshot.book_kind,
            LedgerBookSnapshot.period,
            LedgerBookSnapshot.book_no,
        )
        if company:
            query = query.where(LedgerBookSnapshot.company_id == company)
        rows = (await session.execute(query)).scalars().all()

    for row in rows:
        issue = classify(row)
        if issue is None:
            continue
        total += 1
        print(
            f"[{issue}] company={row.company_id} {row.book_kind} {row.period} "
            f"bookNo={row.book_no} status={row.status} pdf={row.pdf_path or '-'}"
        )

    if total == 0:
        print(f"共检查 {len(rows)} 册已签发账簿，PDF 归档件全部齐全且校验通过。")
    else:
        print(f"\n共检查 {len(rows)} 册，发现 {total} 册归档件有问题，请运行 repair_ledger_book_pdf.py 处理。")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="只读诊断电子账簿 PDF 归档件")
    parser.add_argument("--company", default=None, help="只诊断指定账套（默认全部）")
    args = parser.parse_args()
    asyncio.run(diagnose(args.company))


if __name__ == "__main__":
    main()
