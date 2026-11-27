#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""补建迁移遗留的银行对账权威数据（source_transactions + reconciliation_links）。

背景（2026-11-19 线上诊断）：账套 c_f9c4c18df32d（芥子星辰，owner u_79b7515d5fc1）
的 5~6 月数据来自旧账套迁移（dedupFingerprint=legacy-entry:...）。迁移只搬了
legacy 投影（bank_statements.matched_entry_id / bank_statement_matches / entries
payload 的 billingStatus=reconciled），没有补建权威层的
source_transactions(source_type='bank') + reconciliation_links(confirmed)。
结账校验（closing_validation_service.bank_close_validation）只认权威层，
于是 5 月 6 张带 1002 分录的凭证全部被判 book_only：「有 6 张凭证的分录还没
找到银行流水」，5 月无法结账；6 月同理（8 张），结完 5 月会立刻撞上。

金额核对结论（诊断 SQL 存 backend/scripts/diag/）：各笔流水的金额、方向与
凭证 1002 影响完全一致；银行期末余额与账面一致（5 月 100000.44 / 6 月
99281.90）。本修复不改动任何凭证与金额，只补回匹配关系与流水行序。

动作（默认 dry-run；--apply 才落库）：

  1. 对 13 笔「流水 → 凭证」调用产品同款 sync_bank_reconciliation：
     建 source-{statement_id}（source_type='bank'，external_key=流水 id，
     raw_payload.signedAmount），建 confirmed 的 reconciliation_link，
     每笔写审计事件。已存在且 confirmed 的自动跳过（幂等）。
  2. 修正 9 条同日多笔流水的 source_row_index：迁移行该列为 NULL，排序
     回退按 id，把 5-22 三笔、5-30 / 5-31 / 6-04 各两笔的同日顺序排反，
     期初余额推错 → 「流水金额与银行期末余额不连续」必挂。
  3. 修正 2 条流水的 bank_statements.matched_entry_id：用户 07-10 已在
     bank_statement_matches 里补了正确的结算凭证，但单值指针仍指向无银行
     影响的收入计提凭证（A3：展示与账务同源）。

刻意不动：凭证与分录、entries 投影 payload、bank_statement_matches、其他
账套（含 8 个演示账套的同类遗留）、已关账的 7~9 月。

已知残留：VOU-202606-000030（DeepSeek 500 元，银行 5-31 出账、凭证记 6 月）
重记到 5 月之前，5 月结账仍会剩一笔 -500 的银行余额差——那是真实跨期差异，
只能靠冲销重记消除，不能靠本脚本抹平。

--apply 末尾会在同一事务里重跑两个月结账校验，结果不符合预期则整体回滚；
--verify 只读重跑校验；--rollback 按已知 id 精确逆向。

用法（生产 backend 容器内执行）::

    docker cp backend/scripts/repair_legacy_bank_links_may_june.py deploy-backend-1:/app/scripts/
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py                  # 主修复 dry-run
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py --apply         # 主修复落库
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py --rebook-d500   # -500 重记 dry-run
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py --rebook-d500 --apply  # 重记落库
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py --verify        # 复核
    docker exec deploy-backend-1 python /app/scripts/repair_legacy_bank_links_may_june.py --rollback      # 回滚主修复
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from core.money import ZERO, money, money_text  # noqa: E402
from db.models import JournalEntry, JournalLine  # noqa: E402
from db.session import get_session_factory, session_scope  # noqa: E402
from services.accounting_core_service import record_audit, sync_bank_reconciliation  # noqa: E402
from services.bank_service import RECONCILIATION_AMOUNT_TOLERANCE  # noqa: E402
from services.closing_validation_service import bank_close_validation  # noqa: E402

# ── 修复目标（显式清单，勿扩散）───────────────────────────────────────────────
COMPANY_ID = "c_f9c4c18df32d"
OPERATOR = "u_79b7515d5fc1"
REASON = "补建迁移遗留的 5~6 月银行对账权威数据（source/link/行序），解除 5 月结账阻断"
VERIFY_MONTHS = [date(2026, 5, 1), date(2026, 6, 1)]

# (statement_id, entry_id)——映射只表达用户在 legacy 投影里确认过的匹配；
# 金额、方向、凭证状态由脚本逐笔断言，不一致直接拒绝执行。
# bs-68c3 / bs-dd44 按用户 07-10 在 bank_statement_matches 里的更正取结算凭证。
TARGETS: list[tuple[str, str]] = [
    # 2026-05（6 张 book_only 凭证 + 1 笔跨期在途）
    ("bs-6e2a447458f94649a620", "efae2739-f298-4569-8f6a-f197263f1656"),  # 何静谷实缴 +20000
    ("bs-9f5ec7fac490425e9c7e", "190f71a1-6ddd-4d05-b66c-a9c90b8e73b3"),  # 杨云飞实缴 +50000
    ("bs-69a65105cbf3432fa6dc", "bf8bc3cd-664b-4b96-9690-e40e1e1a399c"),  # 投资款（资本公积）+30000
    ("bs-ec6b1ee808f346e094aa", "c308dc9c-98d9-4b6c-ad88-72bd76f8b9b4"),  # 微信开放平台验证款 -0.15
    ("bs-28f5708b171348c0ab2f", "017d77d3-fd5b-43ed-81ec-68adbd9b32c0"),  # 微信公众号退款 +0.15
    ("bs-7e215741689840299621", "e05a17e0-f7a3-47b4-b62a-02c69651e210"),  # 银联实名验证 +0.44
    # bs-c708（5-31 往来款 -500）暂不建 link：其凭证 VOU-202606-000030 记在 6 月，
    # 银行 5-31 已出账 → 5 月账实差 500。需先冲销重记到 5 月，再把新凭证 link 过来
    #（--rebook-d500，需用户确认后执行）。
    # 2026-06（8 张 book_only 凭证，提前一并补齐，避免结完 5 月撞同一堵墙）
    ("bs-61aa4be0fbb84e578394", "5314793e-851c-4612-867e-da790180e174"),  # 小程序验证 -0.74
    ("bs-4298246a87f64628aed3", "12737a38-ba3c-487b-b147-33956ca1a622"),  # 公众号退款 +0.74
    ("bs-dd9dd54f345f42049cbc", "64b75b33-e73f-4f94-8dad-3bc1d3e106b6"),  # 微信支付认证 +0.01
    ("bs-68c3e49b110347508a95", "0728ab57-d071-4f25-a734-60f81b2675f7"),  # 结算到账 +58.47
    ("bs-9a4c20fcc9854b9cad6d", "9e8eb5fb-d407-4bd5-8ab4-e9142b02b1d2"),  # 利息收入 +4.15
    ("bs-dd4493fa618f47e281f7", "8ac20b58-618b-4e67-89af-79def7bfca6b"),  # 结算到账 +18.83
    ("bs-5655bcddb05e432b9a47", "4a2ae570-6620-480f-9153-02c350faeb6b"),  # 报销垫付 -300
]

# 同日多笔的真实时序（由余额链唯一确定）。只动同日多笔的 9 行，单笔行不动。
ROW_INDEX_FIXES: list[tuple[str, int]] = [
    ("bs-6e2a447458f94649a620", 1),
    ("bs-9f5ec7fac490425e9c7e", 2),
    ("bs-69a65105cbf3432fa6dc", 3),
    ("bs-ec6b1ee808f346e094aa", 4),
    ("bs-28f5708b171348c0ab2f", 5),
    # 05-31 两笔按余额链排：+0.44 → 100000.44，再 -500 → 99500.44（唯一能闭环的顺序）
    ("bs-7e215741689840299621", 6),
    ("bs-c708bb054b9542f39f75", 7),
    ("bs-61aa4be0fbb84e578394", 1),
    ("bs-4298246a87f64628aed3", 2),
]

# matched_entry_id 错位修正：改前值在执行时读取并断言，改后为结算凭证。
MATCHED_ENTRY_FIXES: dict[str, str] = {
    "bs-68c3e49b110347508a95": "0728ab57-d071-4f25-a734-60f81b2675f7",
    "bs-dd4493fa618f47e281f7": "8ac20b58-618b-4e67-89af-79def7bfca6b",
}
# 回滚用：matched_entry_id 的迁移原值
MATCHED_ENTRY_ORIGINALS: dict[str, str] = {
    "bs-68c3e49b110347508a95": "a7ce2a80-02df-406f-bc4d-4e219c61f0be",
    "bs-dd4493fa618f47e281f7": "01b98cd7-e232-493d-89d4-46b3c731481c",
}

# ── -500 跨期重记（--rebook-d500，需用户确认）───────────────────────────────
# 银行 2026-05-31 出账 500（DeepSeek 技术服务费），凭证 VOU-202606-000030 按
# 开票日记在 6 月 → 5 月账面比银行多 500，永远无法通过余额核对。
# 处置：按产品默认口径红字冲销（落原期间 6 月），按付款日 2026-05-31 重记，
# 发票主数据改绑新凭证（_rebind_invoice_after_repost），5-31 流水 link 到新凭证。
D500_ENTRY_ID = "d30ee0b3-8a4b-49b0-aee6-ada53a405dde"
D500_STATEMENT_ID = "bs-c708bb054b9542f39f75"
D500_OCCUR = "2026-05-31"
D500_INVOICE_NUMBER = "26332000004655508586"
D500_REVERSAL_REASON = "银行流水2026-05-31出账、凭证原记6月期间错位，冲销后按付款日重记入5月"


class _DryRunRollback(Exception):
    """dry-run 用：事务内写完、验完强制回滚，不留任何持久化。"""


async def statement_row(session: AsyncSession, statement_id: str) -> dict:
    row = (await session.execute(text(
        "SELECT id, transaction_date, description, amount, balance, matched_entry_id, source_row_index "
        "FROM bank_statements WHERE id = :id AND company_id = :company"
    ), {"id": statement_id, "company": COMPANY_ID})).mappings().first()
    if row is None:
        raise SystemExit(f"[abort] 流水不存在：{statement_id}")
    return dict(row)


async def entry_bank_effect(session: AsyncSession, entry_id: str) -> Decimal:
    lines = (await session.execute(select(JournalLine).where(
        JournalLine.journal_entry_id == entry_id, JournalLine.company_id == COMPANY_ID,
        JournalLine.account_code == "1002",
    ))).scalars().all()
    return sum((money(l.amount) * (1 if l.direction == "debit" else -1) for l in lines), ZERO)


async def assert_target(session: AsyncSession, statement_id: str, entry_id: str) -> dict:
    """逐笔断言：金额方向一致、凭证已过账、归属本账套。返回打印用上下文。"""
    stmt = await statement_row(session, statement_id)
    entry = await session.get(JournalEntry, entry_id)
    if entry is None or entry.company_id != COMPANY_ID:
        raise SystemExit(f"[abort] 凭证不存在或不属于本账套：{entry_id}")
    if entry.status != "posted":
        raise SystemExit(f"[abort] 凭证未过账（{entry.status}）：{entry.voucher_no} {entry_id}")
    effect = await entry_bank_effect(session, entry_id)
    stmt_amount = money(stmt["amount"])
    if abs(effect - stmt_amount) > RECONCILIATION_AMOUNT_TOLERANCE:
        raise SystemExit(
            f"[abort] 金额不一致：流水 {statement_id} {money_text(stmt_amount)} vs "
            f"凭证 1002 影响 {money_text(effect)}（{entry.voucher_no}）"
        )
    # 权威层现状（幂等判断）
    source_id = f"source-{statement_id}"
    existing_link = (await session.execute(text(
        "SELECT status FROM reconciliation_links "
        "WHERE company_id = :company AND source_transaction_id = :source AND journal_entry_id = :entry"
    ), {"company": COMPANY_ID, "source": source_id, "entry": entry_id})).scalar()
    return {
        "stmt": stmt, "entry": entry, "effect": effect,
        "already": existing_link == "confirmed",
    }


def print_plan(rows: list[dict]) -> None:
    print(f"{'流水':<24} {'日期':<11} {'金额':>10}  {'凭证':<18} {'1002影响':>10}  状态")
    for r in rows:
        stmt, entry = r["stmt"], r["entry"]
        print(f"{stmt['id']:<24} {str(stmt['transaction_date']):<11} {money_text(money(stmt['amount'])):>10}  "
              f"{entry.voucher_no or entry.id:<18} {money_text(r['effect']):>10}  "
              f"{'已存在，跳过' if r['already'] else '将补建 source+link'}")


async def apply_fixes(session: AsyncSession, *, dry_run: bool) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"repair-legacy-bank-links:{COMPANY_ID}"})

    rows = []
    for statement_id, entry_id in TARGETS:
        rows.append(await assert_target(session, statement_id, entry_id))
    print("── 逐笔断言（金额/方向/过账状态）全部通过 ──")
    print_plan(rows)
    if all(r["already"] for r in rows):
        print(f"{len(TARGETS)} 笔匹配已全部存在，无需补建。")

    # 1) 权威层：产品同款 sync_bank_reconciliation（幂等）
    for r in rows:
        if r["already"]:
            continue
        stmt = r["stmt"]
        await sync_bank_reconciliation(
            session,
            company_id=COMPANY_ID,
            user_id=OPERATOR,
            statement_id=stmt["id"],
            entry_ids=[str(r["entry"].id)],
            transaction_date=stmt["transaction_date"],
            description=str(stmt["description"] or ""),
            signed_amount=money(stmt["amount"]),
            request_id=str(uuid4()),
        )

    # 2) 流水行序：同日多笔按余额链排（幂等：已是目标值则跳过）
    for statement_id, index in ROW_INDEX_FIXES:
        await session.execute(text(
            "UPDATE bank_statements SET source_row_index = :idx "
            "WHERE id = :id AND company_id = :company AND (source_row_index IS NULL OR source_row_index <> :idx)"
        ), {"idx": index, "id": statement_id, "company": COMPANY_ID})

    # 3) matched_entry_id 错位修正（A3：展示与账务同源）
    for statement_id, correct in MATCHED_ENTRY_FIXES.items():
        await session.execute(text(
            "UPDATE bank_statements SET matched_entry_id = :entry "
            "WHERE id = :id AND company_id = :company AND matched_entry_id IS DISTINCT FROM :entry"
        ), {"entry": correct, "id": statement_id, "company": COMPANY_ID})

    # 审计留痕（只增不改）
    await record_audit(
        session,
        company_id=COMPANY_ID,
        user_id=OPERATOR,
        action="repair_legacy_bank_links",
        entity_type="company",
        entity_id=COMPANY_ID,
        request_id=str(uuid4()),
        reason=REASON,
        after={
            "targets": [f"{sid}->{eid}" for sid, eid in TARGETS],
            "rowIndexFixes": ROW_INDEX_FIXES,
            "matchedEntryFixes": MATCHED_ENTRY_FIXES,
            "dryRun": dry_run,
        },
    )


async def run_closing_checks(session: AsyncSession, label: str) -> dict[str, list]:
    """跑 5 / 6 月 BOOK_TO_ACTUAL 校验，返回按月分组的 issues。"""
    grouped: dict[str, list] = {}
    for month_start in VERIFY_MONTHS:
        result = await bank_close_validation(session, COMPANY_ID, month_start)
        issues = result.get("issues", [])
        grouped[month_start.strftime("%Y-%m")] = issues
        types = {}
        for issue in issues:
            types[issue.get("type")] = types.get(issue.get("type"), 0) + 1
        status = "✅ 清零" if not issues else f"❌ {len(issues)} 项 {types}"
        print(f"[{label}] {month_start.strftime('%Y-%m')} BOOK_TO_ACTUAL：{status}")
        for issue in issues:
            print(f"    - {issue.get('type')}: {issue.get('reason')} "
                  f"(entry={issue.get('entryId')} stmt={issue.get('statementId')} "
                  f"diff={issue.get('difference')})")
    return grouped


def verify_expectations(grouped: dict[str, list], *, allow_d500_residuals: bool = False) -> list[str]:
    """校验结账检查结果是否符合预期。

    allow_d500_residuals=True（主修复后、重记前）：允许三项已知残留——
    5 月 -500 银行余额差、5 月 bs-c708 缺 source、6 月 d30ee0b3 book_only，
    它们都由 --rebook-d500 一并消除。重记模式必须全清（False）。
    """
    problems: list[str] = []
    for issue in grouped.get("2026-05", []):
        kind = issue.get("type")
        if allow_d500_residuals and kind == "bank_balance_mismatch" \
                and money(issue.get("difference") or "0") == money("-500.00"):
            continue
        if allow_d500_residuals and kind == "missing_source" \
                and issue.get("statementId") == D500_STATEMENT_ID:
            continue
        problems.append(f"5月仍有 {kind}: {issue.get('reason')}")
    for issue in grouped.get("2026-06", []):
        kind = issue.get("type")
        if allow_d500_residuals and kind == "book_only" and issue.get("entryId") == D500_ENTRY_ID:
            continue
        problems.append(f"6月仍有 {kind}: {issue.get('reason')}")
    return problems


async def main_apply_or_dry(*, apply: bool) -> int:
    factory = get_session_factory()
    session = factory()
    try:
        async with session.begin():
            await apply_fixes(session, dry_run=not apply)
            grouped = await run_closing_checks(session, "修复后(同事务实测)" if apply else "模拟结果(dry-run实测)")
            problems = verify_expectations(grouped, allow_d500_residuals=True)
            if problems:
                for p in problems:
                    print(f"    [预期外] {p}")
                if apply:
                    raise SystemExit("[abort] 应用后校验不符合预期，整体回滚")
            else:
                print("校验结果符合预期：除待重记的 -500 跨期差异（--rebook-d500 消除）外全部就绪。")
            if not apply:
                raise _DryRunRollback
    except _DryRunRollback:
        print("── dry-run：以上全部写入已回滚，未落库 ──")
    finally:
        await session.close()
    return 0


async def rebook_d500(session: AsyncSession) -> None:
    """冲销 VOU-202606-000030 并按 2026-05-31 重记（复用产品路径，幂等）。"""
    from services.accounting_core_service import (
        _rebind_invoice_after_repost,
        create_entry,
        reverse_journal,
    )

    invoice = (await session.execute(text(
        "SELECT id, entry_id FROM invoices WHERE company_id = :company AND invoice_number = :no LIMIT 1"
    ), {"company": COMPANY_ID, "no": D500_INVOICE_NUMBER})).first()
    if invoice is None:
        raise SystemExit(f"[abort] 找不到发票 {D500_INVOICE_NUMBER}")
    current_entry_id = str(invoice.entry_id or "")
    if current_entry_id != D500_ENTRY_ID:
        print(f"已重记过：发票已绑定 {current_entry_id}，跳过冲销重记。")
        target_entry_id = current_entry_id
    else:
        original = await session.get(JournalEntry, D500_ENTRY_ID)
        if original is None or original.company_id != COMPANY_ID:
            raise SystemExit("[abort] 待冲销凭证不存在")
        if original.status != "posted":
            raise SystemExit(f"[abort] 待冲销凭证状态为 {original.status}，仅支持已过账凭证")
        if original.period_start != date(2026, 6, 1):
            raise SystemExit(f"[abort] 凭证期间为 {original.period_start}，与诊断结论不符，拒绝执行")
        # 与 update_draft 的 autoReverseRepost 同构：冲销（红字落 6 月）→ 换 id/换日期重记
        await reverse_journal(
            session, D500_ENTRY_ID,
            company_id=COMPANY_ID, user_id=OPERATOR, request_id=str(uuid4()),
            reason=D500_REVERSAL_REASON,
        )
        payload = dict(original.metadata_json or {})
        for drop_key in ("id", "voucherLines", "voucherNo", "voucher_no", "dedupFingerprint",
                         "wechat_transaction_key", "invoiceNumber"):
            payload.pop(drop_key, None)
        payload["occurDate"] = D500_OCCUR
        payload["title"] = original.summary
        reposted = await create_entry(
            session, payload,
            company_id=COMPANY_ID, user_id=OPERATOR,
            request_id=str(uuid4()), confirm=True,
        )
        await _rebind_invoice_after_repost(
            session, company_id=COMPANY_ID,
            old_entry_id=D500_ENTRY_ID, new_entry=reposted,
            user_id=OPERATOR, request_id=str(uuid4()),
        )
        # legacy 投影同步（A3：展示与账务同源）——匹配表指向新凭证
        await session.execute(text(
            "DELETE FROM bank_statement_matches WHERE company_id = :company "
            "AND statement_id = :stmt AND entry_id = :entry"
        ), {"company": COMPANY_ID, "stmt": D500_STATEMENT_ID, "entry": D500_ENTRY_ID})
        await session.execute(text(
            "INSERT INTO bank_statement_matches (statement_id, company_id, entry_id, "
            "previous_billing_status, previous_payment_date, created_at) "
            "VALUES (:stmt, :company, :entry, 'reconciled', '2026-05-31', :now)"
        ), {"stmt": D500_STATEMENT_ID, "company": COMPANY_ID,
            "entry": str(reposted.id), "now": datetime.now(timezone.utc).isoformat()})
        await session.execute(text(
            "UPDATE bank_statements SET matched_entry_id = :entry "
            "WHERE id = :stmt AND company_id = :company"
        ), {"entry": str(reposted.id), "stmt": D500_STATEMENT_ID, "company": COMPANY_ID})
        target_entry_id = str(reposted.id)
        print(f"已冲销并重记：{original.voucher_no} → {reposted.voucher_no}（{D500_OCCUR}，5 月）")

    # 5-31 流水的权威匹配落到（新）凭证上——幂等：sync_bank_reconciliation 自会复用已有 source/link
    stmt = await statement_row(session, D500_STATEMENT_ID)
    await sync_bank_reconciliation(
        session,
        company_id=COMPANY_ID, user_id=OPERATOR,
        statement_id=D500_STATEMENT_ID,
        entry_ids=[target_entry_id],
        transaction_date=stmt["transaction_date"],
        description=str(stmt["description"] or ""),
        signed_amount=money(stmt["amount"]),
        request_id=str(uuid4()),
    )
    await record_audit(
        session,
        company_id=COMPANY_ID, user_id=OPERATOR,
        action="repair_rebook_d500", entity_type="journal_entry",
        entity_id=D500_ENTRY_ID, request_id=str(uuid4()),
        reason=D500_REVERSAL_REASON,
        before={"entryId": D500_ENTRY_ID, "period": "2026-06"},
        after={"targetEntryId": target_entry_id, "period": "2026-05", "statementId": D500_STATEMENT_ID},
    )


async def main_rebook(*, apply: bool) -> int:
    factory = get_session_factory()
    session = factory()
    try:
        async with session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                                  {"key": f"repair-legacy-bank-links:{COMPANY_ID}"})
            await rebook_d500(session)
            grouped = await run_closing_checks(session, "重记后(同事务实测)" if apply else "模拟结果(dry-run实测)")
            problems = verify_expectations(grouped)
            if problems:
                for p in problems:
                    print(f"    [预期外] {p}")
                if apply:
                    raise SystemExit("[abort] 重记后校验不符合预期，整体回滚")
            else:
                print("5 / 6 月银行核对全部清零，可以结账。")
            if not apply:
                raise _DryRunRollback
    except _DryRunRollback:
        print("── dry-run：以上全部写入已回滚，未落库 ──")
    finally:
        await session.close()
    return 0


async def main_verify() -> int:
    async with session_scope() as session:
        grouped = await run_closing_checks(session, "线上实测")
    problems = verify_expectations(grouped, allow_d500_residuals=True)
    residuals = verify_expectations(grouped)
    if problems:
        print("结论：存在预期外阻断项，见上方明细")
        for p in problems:
            print(f"    [预期外] {p}")
        return 1
    if residuals:
        print("结论：符合预期；-500 跨期差异待 --rebook-d500 重记，重记前 5 月仍不可结账。")
    else:
        print("结论：5 / 6 月银行核对全部清零，可以结账。")
    return 0


async def main_rollback() -> int:
    async with session_scope() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                              {"key": f"repair-legacy-bank-links:{COMPANY_ID}"})
        source_ids = [f"source-{sid}" for sid, _ in TARGETS]
        await session.execute(text(
            "DELETE FROM reconciliation_links WHERE company_id = :company AND source_transaction_id = ANY(:sources)"
        ), {"company": COMPANY_ID, "sources": source_ids})
        await session.execute(text(
            "DELETE FROM source_transactions WHERE company_id = :company AND id = ANY(:sources) "
            "AND source_type = 'bank'"
        ), {"company": COMPANY_ID, "sources": source_ids})
        for statement_id, original in MATCHED_ENTRY_ORIGINALS.items():
            await session.execute(text(
                "UPDATE bank_statements SET matched_entry_id = :entry "
                "WHERE id = :id AND company_id = :company"
            ), {"entry": original, "id": statement_id, "company": COMPANY_ID})
        for statement_id, _index in ROW_INDEX_FIXES:
            await session.execute(text(
                "UPDATE bank_statements SET source_row_index = NULL "
                "WHERE id = :id AND company_id = :company"
            ), {"id": statement_id, "company": COMPANY_ID})
        await record_audit(
            session,
            company_id=COMPANY_ID,
            user_id=OPERATOR,
            action="repair_legacy_bank_links_rollback",
            entity_type="company",
            entity_id=COMPANY_ID,
            request_id=str(uuid4()),
            reason="回滚 repair_legacy_bank_links_may_june.py 的写入",
            after={"sources": source_ids},
        )
    print("已回滚：删除本脚本建立的 source/link，恢复 matched_entry_id 原值，清空行序。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    parser.add_argument("--rebook-d500", action="store_true",
                        help="执行 -500 跨期冲销重记（需先跑完主修复；默认 dry-run，配 --apply 落库）")
    parser.add_argument("--verify", action="store_true", help="只读重跑 5/6 月银行结账校验")
    parser.add_argument("--rollback", action="store_true", help="回滚本脚本的写入")
    args = parser.parse_args()
    if args.verify:
        return asyncio.run(main_verify())
    if args.rollback:
        return asyncio.run(main_rollback())
    if args.rebook_d500:
        return asyncio.run(main_rebook(apply=args.apply))
    return asyncio.run(main_apply_or_dry(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
