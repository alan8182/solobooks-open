#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""清理「结账向导重新选择」自动冲销重记产生的重复凭证链。

事故（2026-11-03）：结账向导第二步「重新选择去向」走了 autoReverseRepost，
对同一张发票 / 同一笔报销反复重记；而冲销凭证本身又被当成普通业务记录再次
冲销，生成 VOU-202606-000010 ~ VOU-202606-000017 的层层冲销链。后果：

  - 发票主数据 ``inv-17d96ea762a044f4.entry_id`` 指向已冲销凭证 ``bdd1da27...``
  - 银行流水 ``bs-...-16`` 的 ``matched_entry_id`` 指向已冲销凭证 ``reim-...``
  - 科目净额被重复记账污染：2241 多借 900、1002 多贷 900、5602999 多借 300

动作（默认 dry-run；``--apply`` 才落库）：

  1. 删除 10 条 bug 生成的凭证（连同分录、entries 投影）
  2. 删除 ``reconciliation_links`` 上指向这些凭证的关联行
  3. 删除发票主数据 ``inv-17d96ea762a044f4``，由用户重新导入发票
  4. 把 6 条 ``source_transactions`` 置 ``void``，重导不被 DUPLICATE_SOURCE 拦
  5. 清空银行流水 ``bs-...-16`` 的匹配（保留原始流水行）
  6. 写一条审计事件留痕

刻意不动：

  - 银行流水原始行本身（删掉会破坏 6 月流水连续性），只解除匹配
  - ``audit_events`` 历史（只增不改）
  - 其他账套、其他凭证、5 月已关账期间

用法::

    cd backend && venv/bin/python scripts/repair_closing_wizard_repost_chain.py          # dry-run
    cd backend && venv/bin/python scripts/repair_closing_wizard_repost_chain.py --apply  # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import text  # noqa: E402

from db.session import session_scope  # noqa: E402

# ── 修复目标（显式清单，勿扩散）───────────────────────────────────────────────
COMPANY_ID = "c_6cbe720cfb23"
OPERATOR = "u_f70aacab854b"
REASON = "清理结账向导自动冲销重记产生的重复凭证链，发票与报销由用户重新导入"

# 发票与报销两条线，共 10 条凭证。全部产生于 2026-11-03 08:21~08:38。
BUG_ENTRIES = {
    # ── 发票线：原始发票凭证被自动冲销，活凭证是重记出来的 000011 ──────────────
    "bdd1da27-b2c9-4d9d-9f8b-2852515580f3": "发票原凭证（reversed）",
    "8241b5f5-30b4-46cd-947a-be502d6287a2": "冲销原发票凭证（reversal）",
    "6fb58936-2cdf-4e99-ba76-d1ea69d5b814": "重记发票凭证（活凭证）",
    # ── 报销线：报销凭证被反复重记，留下 3 条并存活凭证 + 冲销链 ───────────────
    "reim-bs-c_6cbe720cfb23-1793694097001614-16-bdd1da27-b2c9-4d9d-9f8b-2852515580f3": (
        "报销原凭证（reversed）"
    ),
    "d60e6339-2d9d-4571-aa3e-aacf6de29f29": "冲销报销原凭证（reversal, reversed）",
    "7c4ce8ca-45d3-414f-a228-b2361a7214e6": "报销重记 1（活凭证）",
    "e2f79c81-d8f1-4bf2-9690-90ac3246909a": "冲销重记 1（reversal, reversed）",
    "d84a0971-328e-4dea-8e05-a815017413b4": "报销重记 2（活凭证）",
    "0421fcad-219d-4cb8-846b-cdf5d3b999e5": "冲销重记 2（reversal）",
    "bc8b766c-d462-446d-9d6c-3f5e89f6c464": "报销重记 3（活凭证）",
}

# 需要"先删"的冲销凭证：它们自身带 reversal_of_id，若在父凭证之后删除会被
# NO ACTION 外键拦下。按此顺序删除即可避免顺序问题。
REVERSAL_ENTRIES = [
    "8241b5f5-30b4-46cd-947a-be502d6287a2",
    "d60e6339-2d9d-4571-aa3e-aacf6de29f29",
    "e2f79c81-d8f1-4bf2-9690-90ac3246909a",
    "0421fcad-219d-4cb8-846b-cdf5d3b999e5",
]

INVOICE_ID = "inv-17d96ea762a044f4"
STATEMENT_ID = "bs-c_6cbe720cfb23-1793694097001614-16"

# 这 6 条来源记录只被待删凭证引用（users=1）。置 void 后 create_entry 允许复活，
# 否则重导发票会撞 uq_source_external_key / DUPLICATE_SOURCE。
SOURCE_TRANSACTION_IDS = [
    "8019edc5-9bdb-4dab-a4da-0857e92b488b",
    "78b8c2db-3ddf-4f2d-a6ed-95f03e3fc582",
    "bbc8ce97-415b-4116-94f3-2572d9ffb0c0",
    "98c82c21-12a6-408c-9635-33f9c6b93163",
    "e6c9fe32-441a-472f-9cc2-c416071f4e0f",
    "275ccda6-c9e7-43f6-abe9-1358207f436b",
]


def _ids_params(ids: list[str]) -> tuple[str, dict[str, str]]:
    placeholders = ", ".join(f":e{n}" for n in range(len(ids)))
    params = {f"e{n}": v for n, v in enumerate(ids)}
    return placeholders, params


async def _preflight(session) -> dict:
    """校验目标与预期一致，任何不符立即中止，不做部分清理。"""
    ids = list(BUG_ENTRIES)
    placeholders, params = _ids_params(ids)

    rows = (
        await session.execute(
            text(
                "SELECT id, voucher_no, status, entry_type FROM journal_entries "
                f"WHERE company_id = :cid AND id IN ({placeholders})"
            ),
            {"cid": COMPANY_ID, **params},
        )
    ).mappings().all()
    found = {row["id"]: dict(row) for row in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise SystemExit(
            "[abort] 以下凭证不存在或不属于本账套，事务未做任何改动：\n  "
            + "\n  ".join(f"{i}  {BUG_ENTRIES[i]}" for i in missing)
        )

    invoice = (
        await session.execute(
            text("SELECT id, entry_id, invoice_number FROM invoices WHERE company_id = :cid AND id = :iid"),
            {"cid": COMPANY_ID, "iid": INVOICE_ID},
        )
    ).mappings().first()
    statement = (
        await session.execute(
            text("SELECT id, matched_entry_id, amount FROM bank_statements WHERE company_id = :cid AND id = :sid"),
            {"cid": COMPANY_ID, "sid": STATEMENT_ID},
        )
    ).mappings().first()

    line_count = (
        await session.execute(
            text(f"SELECT count(*) FROM journal_lines WHERE journal_entry_id IN ({placeholders})"),
            params,
        )
    ).scalar_one()
    projection_count = (
        await session.execute(
            text(f"SELECT count(*) FROM entries WHERE id IN ({placeholders})"),
            params,
        )
    ).scalar_one()
    link_count = (
        await session.execute(
            text(f"SELECT count(*) FROM reconciliation_links WHERE journal_entry_id IN ({placeholders})"),
            params,
        )
    ).scalar_one()
    source_count = (
        await session.execute(
            text(
                "SELECT count(*) FROM source_transactions WHERE id IN "
                + "(" + ", ".join(f":s{n}" for n in range(len(SOURCE_TRANSACTION_IDS))) + ")"
            ),
            {f"s{n}": v for n, v in enumerate(SOURCE_TRANSACTION_IDS)},
        )
    ).scalar_one()

    print(f"[plan] 账套 {COMPANY_ID}")
    print(f"[plan] 待删凭证 {len(found)} 条，分录 {line_count} 行，entries 投影 {projection_count} 行")
    for entry_id in ids:
        row = found[entry_id]
        print(f"       - {row['voucher_no']} {row['status']:9} {row['entry_type']:9} {BUG_ENTRIES[entry_id]}")
    print("[plan] 清理前受影响科目净额：")
    for row in (
        await session.execute(
            text(
                "SELECT l.account_code, l.account_name, l.direction, SUM(l.amount) AS amt "
                "FROM journal_lines l WHERE l.journal_entry_id IN (" + placeholders + ") "
                "GROUP BY l.account_code, l.account_name, l.direction ORDER BY l.account_code"
            ),
            params,
        )
    ).mappings().all():
        print(f"       {row['account_code']} {row['account_name']} {row['direction']} {row['amt']}")
    print(f"[plan] reconciliation_links 待删 {link_count} 行")
    print(f"[plan] 发票主数据 {INVOICE_ID} 删除；当前 entry_id={invoice['entry_id'] if invoice else '(不存在)'}")
    print(
        f"[plan] 银行流水 {STATEMENT_ID} 保留，仅清空匹配（当前 matched_entry_id="
        f"{statement['matched_entry_id'] if statement else '(不存在)'}）"
    )
    print(f"[plan] source_transactions 置 void {source_count} 行")

    if invoice is None:
        print("[warn] 发票主数据已不存在，跳过第 3 步")
    if statement is None:
        raise SystemExit("[abort] 银行流水不存在，事务未做任何改动")

    return {
        "ids": ids,
        "line_count": int(line_count),
        "projection_count": int(projection_count),
        "link_count": int(link_count),
        "source_count": int(source_count),
        "invoice_exists": invoice is not None,
    }


async def _apply(session, plan: dict) -> None:
    from services.accounting_core_service import record_audit

    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        # 不可变触发器会无条件拒绝删除已过账/已冲销凭证，这里用与「永久删除主体」
        # 相同的会话级开关受控放行，只在本次事务内生效。
        await session.execute(text("SELECT set_config('solobooks.allow_tenant_purge', 'on', true)"))

    ids = plan["ids"]
    placeholders, params = _ids_params(ids)

    # 1) 关联表先清：reconciliation_links 是 NO ACTION，journal_lines/entries 随主表走
    result = await session.execute(
        text(f"DELETE FROM reconciliation_links WHERE company_id = :cid AND journal_entry_id IN ({placeholders})"),
        {"cid": COMPANY_ID, **params},
    )
    print(f"[delete] reconciliation_links: {result.rowcount} 行")

    # 2) 凭证：冲销单先删（它们带 reversal_of_id），再删其余
    reversal_ph, reversal_params = _ids_params(REVERSAL_ENTRIES)
    result = await session.execute(
        text(f"DELETE FROM journal_entries WHERE company_id = :cid AND id IN ({reversal_ph})"),
        {"cid": COMPANY_ID, **reversal_params},
    )
    print(f"[delete] journal_entries（冲销单）: {result.rowcount} 行")
    result = await session.execute(
        text(f"DELETE FROM journal_entries WHERE company_id = :cid AND id IN ({placeholders})"),
        {"cid": COMPANY_ID, **params},
    )
    print(f"[delete] journal_entries（其余）: {result.rowcount} 行")

    # 3) entries 兼容投影
    result = await session.execute(
        text(f"DELETE FROM entries WHERE company_id = :cid AND id IN ({placeholders})"),
        {"cid": COMPANY_ID, **params},
    )
    print(f"[delete] entries 投影: {result.rowcount} 行")

    # 4) 发票主数据：由用户重新导入
    if plan["invoice_exists"]:
        result = await session.execute(
            text("DELETE FROM invoices WHERE company_id = :cid AND id = :iid"),
            {"cid": COMPANY_ID, "iid": INVOICE_ID},
        )
        print(f"[delete] invoices: {result.rowcount} 行")

    # 5) 来源记录置 void：允许重导复活，不被 DUPLICATE_SOURCE 拦
    source_ph, source_params = _ids_params(SOURCE_TRANSACTION_IDS)
    result = await session.execute(
        text(f"UPDATE source_transactions SET status = 'void' WHERE company_id = :cid AND id IN ({source_ph})"),
        {"cid": COMPANY_ID, **source_params},
    )
    print(f"[update] source_transactions -> void: {result.rowcount} 行")

    # 6) 银行流水解除匹配（保留原始流水行，避免破坏流水连续性）
    result = await session.execute(
        text("DELETE FROM bank_statement_matches WHERE company_id = :cid AND statement_id = :sid"),
        {"cid": COMPANY_ID, "sid": STATEMENT_ID},
    )
    print(f"[delete] bank_statement_matches: {result.rowcount} 行")
    result = await session.execute(
        text(
            "UPDATE bank_statements SET matched_entry_id = NULL "
            "WHERE company_id = :cid AND id = :sid"
        ),
        {"cid": COMPANY_ID, "sid": STATEMENT_ID},
    )
    print(f"[update] bank_statements.matched_entry_id -> NULL: {result.rowcount} 行")

    # 7) 留痕
    await record_audit(
        session,
        company_id=COMPANY_ID,
        user_id=OPERATOR,
        action="purge_repost_chain",
        entity_type="company",
        entity_id=COMPANY_ID,
        request_id="repair-repost-chain-" + uuid4().hex[:8],
        reason=REASON,
        before={"entries": ids, "invoice": INVOICE_ID, "statementMatch": STATEMENT_ID},
        after={
            "deletedEntries": len(ids),
            "deletedInvoice": plan["invoice_exists"],
            "voidedSources": plan["source_count"],
            "statementUnmatched": True,
        },
    )
    print("[audit] 已写入清理留痕")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行清理（默认 dry-run）")
    args = parser.parse_args()

    async with session_scope() as session:
        plan = await _preflight(session)
        if not args.apply:
            print("\n[dry-run] 以上为清理计划。确认无误后加 --apply 执行。")
            return
        print("\n[apply] 开始清理……")
        await _apply(session, plan)
    print("\n[done] 清理完成。请到 UI 重新导入发票 / 重新匹配银行流水，并复核 6 月余额核对。")


if __name__ == "__main__":
    asyncio.run(main())
