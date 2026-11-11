# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""参赛视频录制用演示账套（SQLite 本地模式）。

与 ``services.demo_book_service.create_demo_company`` 同一条流水线，唯一差别：
只给「历史月份」做工资计提+发放——当月工资表保持未计提状态，结账向导
第二步才能现场演示「一键计提」。

用法（backend venv，backend 目录下）::

    ACCOUNTING_TEST_MODE=1 DATABASE_URL=sqlite+aiosqlite:///data/app.db \\
        python scripts/seed_demo_video.py u_17200624f777
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

MONTHS = 3
DEMO_SEED_VERSION = "video-2026-10"


async def main(user_id: str) -> None:
    from db.models import AuthUser
    from db.session import session_scope
    from services import tax_registration_service
    from services.account_service import create_company, set_user_current_company
    from services.demo_book_service import (
        DEMO_NAME,
        DEMO_OWNER_NAME,
        MAX_MONTHS,
        _apply_running_balance,
        _month_sequence,
        _seed_owner_payroll,
        build_demo_book,
        ensure_demo_invoices,
        ensure_demo_private_expenses,
    )
    from services.bank_service import import_statements_core
    from services.bank_settlement_service import confirm_statement_match
    from services.entry_service import save_entry

    async with session_scope() as session:
        user = await session.get(AuthUser, user_id)
        previous_company_id = (user.current_company_id if user is not None else "") or ""

    months = max(1, min(MONTHS, MAX_MONTHS))
    window = _month_sequence(date.today(), months)
    entries, statements, pairs, start_date = build_demo_book(months=months)
    company = await create_company(user_id, DEMO_NAME, {
        "isDemo": True,
        "demoSeedVersion": DEMO_SEED_VERSION,
        "taxpayerType": "small_scale",
        "type": "有限责任公司",
        "industry": "软件和信息技术服务业",
        "startDate": start_date.isoformat(),
        "taxId": "91330100MA2DEMO01X",
    })
    cid = str(company["company_id"])
    print(f"[seed] 演示账套已创建: {DEMO_NAME} company_id={cid}")

    id_map: dict[str, str] = {}
    for entry in entries:
        old_id = str(entry["id"])
        id_map[old_id] = f"{cid}-{old_id}"
        entry["id"] = id_map[old_id]
    pairs = [(occurred, summary, id_map[entry_id]) for occurred, summary, entry_id in pairs]

    for entry in entries:
        await save_entry(
            entry,
            user_id=user_id,
            company_id=cid,
            confirm=True,
            trusted_voucher=bool(entry.get("voucherLines")),
        )
    print(f"[seed] 记账 {len(entries)} 笔（已过账）")

    # 两笔当月「例外账目」：默认公户 payer 且无对应流水 → 结账向导第二步
    # 会把它们列进待处理例外，现场演示「老板垫付」「客户未付款」大白话入账。
    video_year, video_month = window[-1]
    for seq_no, (day, title, amount, direction, category) in enumerate([
        (24, "技术服务费-杭州启明品牌设计", 4500, "income", "主营业务收入"),
        (26, "团队加班餐费", 386, "expense", "餐饮费"),
    ], start=1):
        await save_entry(
            {
                "id": f"{cid}-video-extra-{seq_no}",
                "title": title,
                "amount": f"{amount:.2f}",
                "direction": direction,
                "category": category,
                "payer": "company_account",
                "occurDate": f"{video_year:04d}-{video_month:02d}-{day:02d}",
                "source": "manual",
            },
            user_id=user_id,
            company_id=cid,
            confirm=True,
        )
    print("[seed] 例外账目 2 笔（老板垫付 / 客户未付款 演示用）")

    payroll_vouchers = 0
    for year_no, month_no in window[:-1]:
        payroll_month = f"{year_no}-{month_no:02d}"
        owner_payroll = await _seed_owner_payroll(cid, user_id, payroll_month)
        salary_desc = f"招行对公 | 代发工资（{DEMO_OWNER_NAME}）"
        social_desc = f"招行对公 | 社保扣款（{DEMO_OWNER_NAME}）"
        housing_desc = f"招行对公 | 公积金扣款（{DEMO_OWNER_NAME}）"
        statements.extend([
            {
                "transaction_date": owner_payroll["salaryDate"],
                "description": salary_desc,
                "amount": -float(owner_payroll["salaryAmount"]),
                "balance": 0.0,
            },
            {
                "transaction_date": owner_payroll["socialDate"],
                "description": social_desc,
                "amount": -float(owner_payroll["socialAmount"]),
                "balance": 0.0,
            },
            {
                "transaction_date": owner_payroll["housingDate"],
                "description": housing_desc,
                "amount": -float(owner_payroll["housingAmount"]),
                "balance": 0.0,
            },
        ])
        pairs.extend([
            (owner_payroll["salaryDate"], salary_desc, owner_payroll["salaryVoucherId"]),
            (owner_payroll["socialDate"], social_desc, owner_payroll["socialVoucherId"]),
            (owner_payroll["housingDate"], housing_desc, owner_payroll["housingVoucherId"]),
        ])
        payroll_vouchers += int(owner_payroll["voucherCount"])
    _apply_running_balance(statements)
    print(f"[seed] 历史月份工资已计提并发放 {len(window) - 1} 期（当月工资留待演示）")

    await ensure_demo_invoices(cid)
    await ensure_demo_private_expenses(cid)

    # 流水走文件导入同款路径（import_statements_core 会同步写 SourceTransaction
    # 权威来源），配对走真实认领路径（confirm_statement_match 建核销链）——
    # 否则结账「账实核对」会以 missing_source / 未核销 卡住。
    async with session_scope() as session:
        statement_result = await import_statements_core(
            session, company_id=cid, user_id=user_id, rows=statements,
            request_id="seed-demo-video",
        )
        await session.commit()
    imported = int(statement_result.get("imported") or 0)
    print(f"[seed] 流水导入 {imported}（含权威来源记录）")

    matched = 0
    async with session_scope() as session:
        for occurred, summary, entry_id in pairs:
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM bank_statements WHERE company_id = :c "
                        "AND transaction_date = :d AND description = :s LIMIT 1"
                    ),
                    {"c": cid, "d": occurred, "s": summary},
                )
            ).first()
            if row is None:
                print(f"[seed] 找不到流水: {occurred} {summary}")
                continue
            try:
                await confirm_statement_match(
                    session,
                    company_id=cid,
                    user_id=user_id,
                    statement_id=str(row[0]),
                    entry_ids=[entry_id],
                    request_id="seed-demo-video",
                )
                matched += 1
            except Exception as exc:  # noqa: BLE001 - 单笔失败不阻断整本
                print(f"[seed] 配对失败 {occurred} {summary}: {exc}")
        await session.commit()
    print(f"[seed] 流水配对 {matched}/{len(pairs)}（其余留给录制现场认领）")

    try:
        await tax_registration_service.import_registrations(
            cid,
            user_id,
            tax_registration_service.build_template("ltd"),
            mode="merge",
            source="template",
        )
    except Exception:  # noqa: BLE001 - 认定表是增强项
        print("[seed] 税费种认定导入跳过")

    if previous_company_id and previous_company_id != cid:
        await set_user_current_company(user_id, previous_company_id)
    print(f"[seed] 完成 company_id={cid}（当前账套保持不变）")


if __name__ == "__main__":
    user_arg = sys.argv[1] if len(sys.argv) > 1 else "u_17200624f777"
    asyncio.run(main(user_arg))
