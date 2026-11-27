# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""修复：清理 2026-11-21 误粘贴进「陕西澜晞」的税费种认定及其派生征期。

事故：主体管理新建正式账套后未自动切换（前端已修复），用户在新账套成功弹窗点
「立即前往同步税费种认定」，芥子星辰的认定表粘贴进了当时停留的澜晞账套，
import 端点随后用错误认定重算了澜晞的 2026 年征期。

用法::

    cd backend && ./venv/Scripts/python.exe scripts/fix_tax_registration_misimport.py            # dry-run（默认）
    cd backend && ./venv/Scripts/python.exe scripts/fix_tax_registration_misimport.py --execute  # 实际执行
    cd backend && ./venv/Scripts/python.exe scripts/fix_tax_registration_misimport.py --restore --backup <备份json>  # 回滚

行为：
- 只动诊断脚本圈定的 id 清单，且逐条校验归属主体（company_id 不符即中止）
- --execute 前先把删除行完整备份到 data/backups/*.json（--restore 可原样恢复，含原 id）
- 删除后重跑 refresh_deadlines(2026)：认定表清空后按内置兜底清单校准征期（幂等）
- 幂等：重复执行时 id 已不存在，0 改动
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# 清理范围来自 scripts/diagnose_tax_registration_misimport.py 的只读诊断输出（2026-11-21）
MISIMPORT_COMPANY_ID = "c_6048d58f7a00"
TAX_REGISTRATION_IDS = (
    "f45b1b2e-f48a-4abd-881a-7600ac9730df",  # 残疾人就业保障金 year
    "8f8ea1d3-e815-4348-87ef-facf20b0204c",  # 个人所得税/工资薪金所得 month
    "f9a89da2-8a9a-40e8-b563-479956040449",  # 文化事业建设费/广告业 quarter
    "da1065fc-dcd1-40be-a7ea-843b5131d895",  # 地方教育附加 quarter
    "3c744583-0505-49da-8764-2c2b0194e7f2",  # 教育费附加 quarter
    "acf065bd-ef91-4ecb-918c-cd7bbf7f9871",  # 印花税/买卖合同 quarter
    "b0d6f531-3ab2-4c6e-9fcd-c026bf967241",  # 城市维护建设税 quarter
    "a078984c-9617-4cac-b086-04e070cb2a54",  # 企业所得税 quarter
    "68f5374b-63c7-496e-87d0-616a109f4797",  # 增值税/商业(17%、16%、13%) quarter —— 一般纳税人税率，澜晞是小规模
    "3bacf228-605a-4d16-90aa-71095597689d",  # 印花税/营业账簿 year
)
FILING_DEADLINE_IDS = (
    "4ccf453d-b435-4234-ac1b-2ddb62128163",  # culture_fee 2026-Q2
    "4eaac16b-24a6-4fa3-a3fd-b3e3ed487655",  # culture_fee 2026-Q3
    "e0b5abee-3cd6-4c48-bd51-bab76fcae239",  # culture_fee 2026-Q4
    "e00a66e0-ade4-405f-a8aa-66afca7f56c6",  # disability_fund 2026
    "4145dd1e-39b8-4493-bd0c-3635658b17e3",  # stamp_duty 2026（年报，与既有季报并行是错导入产物）
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from db.models import Company, FilingDeadline, TaxRegistration  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.filing_deadline_service import refresh_deadlines  # noqa: E402
from services.tax_registration_service import serialize_registration  # noqa: E402

BACKUP_DIR = BACKEND_DIR / "data" / "backups"

# serialize_registration 键 → TaxRegistration 列（restore 用）
_REG_FIELDS = {
    "levyItem": "levy_item",
    "levyCategory": "levy_category",
    "levySubItem": "levy_sub_item",
    "filingCycle": "filing_cycle",
    "filingCycleText": "filing_cycle_text",
    "deadlineText": "deadline_text",
    "deadlineRule": "deadline_rule",
    "dueMonthOffset": "due_month_offset",
    "dueDay": "due_day",
    "levyRate": "levy_rate",
    "taxRate": "tax_rate",
    "rateUnit": "rate_unit",
    "mainFlag": "main_flag",
    "collectionMethod": "collection_method",
    "returnGroup": "return_group",
    "status": "status",
    "source": "source",
    "note": "note",
}


def _deadline_backup(row: FilingDeadline) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "tax_kind": row.tax_kind,
        "period": row.period,
        "due_date": row.due_date.isoformat(),
        "status": row.status,
        "filed_at": row.filed_at.isoformat() if row.filed_at else None,
        "filed_evidence": row.filed_evidence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _snapshot(session) -> tuple[list[TaxRegistration], list[FilingDeadline], list[str]]:
    """按 id 清单取行；返回（认定行，征期行，校验错误）。"""
    problems: list[str] = []
    reg_rows: list[TaxRegistration] = []
    for reg_id in TAX_REGISTRATION_IDS:
        row = await session.get(TaxRegistration, reg_id)
        if row is None:
            continue  # 幂等：可能已清理过
        if row.company_id != MISIMPORT_COMPANY_ID:
            problems.append(f"认定行 {reg_id} 归属 {row.company_id}，不是目标主体，中止")
        reg_rows.append(row)
    deadline_rows: list[FilingDeadline] = []
    for dl_id in FILING_DEADLINE_IDS:
        row = await session.get(FilingDeadline, dl_id)
        if row is None:
            continue
        if row.company_id != MISIMPORT_COMPANY_ID:
            problems.append(f"征期行 {dl_id} 归属 {row.company_id}，不是目标主体，中止")
        if row.status == "filed" or row.filed_at:
            problems.append(f"征期行 {dl_id} 已申报（status={row.status}），不应删除，中止")
        deadline_rows.append(row)
    return reg_rows, deadline_rows, problems


async def run(execute: bool) -> int:
    async with session_scope() as session:
        company = await session.get(Company, MISIMPORT_COMPANY_ID)
        name = (company.payload or {}).get("name") if company else MISIMPORT_COMPANY_ID
        reg_rows, deadline_rows, problems = await _snapshot(session)
        if problems:
            for p in problems:
                print(f"  ✗ {p}")
            return 1

        if not reg_rows and not deadline_rows:
            print(f"目标行已全部清理（{name}），无需重复执行。")
            return 0

        print(f"主体 {name} ({MISIMPORT_COMPANY_ID})")
        print(f"  将删除税费种认定 {len(reg_rows)} 条 / 派生征期 {len(deadline_rows)} 条")
        for row in reg_rows:
            print(f"    - 认定 {row.levy_item}/{row.levy_category} {row.filing_cycle} id={row.id}")
        for row in deadline_rows:
            print(f"    - 征期 {row.tax_kind} {row.period} {row.status} 截止={row.due_date} id={row.id}")

        if not execute:
            print("\ndry-run：未写库。确认无误后加 --execute 执行。")
            return 0

        # 备份先行：整行快照 + 原 id，--restore 可原样恢复
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / (
            f"tax_misimport_{MISIMPORT_COMPANY_ID}_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
        )
        backup = {
            "company_id": MISIMPORT_COMPANY_ID,
            "reason": "2026-11-21 建账未自动切换，税费种认定误粘贴进旧账套",
            "tax_registrations": [
                {**serialize_registration(row), "createdAt": row.created_at.isoformat() if row.created_at else None}
                for row in reg_rows
            ],
            "filing_deadlines": [_deadline_backup(row) for row in deadline_rows],
        }
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n备份已写入 {backup_path}")

        for row in deadline_rows:
            await session.delete(row)
        for row in reg_rows:
            await session.delete(row)
        await session.flush()
        print(f"已删除 认定 {len(reg_rows)} 条 / 征期 {len(deadline_rows)} 条")

    # 征期重算放在事务外（与 import 端点同一行为）：认定表清空后回落内置兜底清单
    refreshed = await refresh_deadlines(MISIMPORT_COMPANY_ID, 2026)
    print(
        "refresh_deadlines(2026)："
        f"created={refreshed['created']} autoWaived={refreshed['autoWaived']} source={refreshed['source']}"
    )
    print("\n完成。改前 → 改后：认定行 10 → 0，错导入征期 5 → 0；其余征期行未触碰。")
    return 0


async def restore(backup_path: Path) -> int:
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    if data.get("company_id") != MISIMPORT_COMPANY_ID:
        print("备份不属于目标主体，中止")
        return 1
    async with session_scope() as session:
        restored = 0
        for reg in data["tax_registrations"]:
            if await session.get(TaxRegistration, reg["id"]) is not None:
                continue  # 幂等
            kwargs: dict[str, Any] = {}
            for key, column in _REG_FIELDS.items():
                value = reg.get(key)
                if column in ("levy_rate", "tax_rate") and value is not None:
                    value = Decimal(str(value))
                kwargs[column] = value
            session.add(
                TaxRegistration(
                    id=reg["id"],
                    company_id=MISIMPORT_COMPANY_ID,
                    seq=reg.get("seq") or 0,
                    created_at=datetime.fromisoformat(reg["createdAt"]) if reg.get("createdAt") else None,
                    **kwargs,
                )
            )
            restored += 1
        for dl in data["filing_deadlines"]:
            if await session.get(FilingDeadline, dl["id"]) is not None:
                continue
            session.add(
                FilingDeadline(
                    id=dl["id"],
                    company_id=dl["company_id"],
                    tax_kind=dl["tax_kind"],
                    period=dl["period"],
                    due_date=datetime.fromisoformat(dl["due_date"]).date(),
                    status=dl["status"],
                    filed_at=datetime.fromisoformat(dl["filed_at"]) if dl["filed_at"] else None,
                    filed_evidence=dl["filed_evidence"],
                )
            )
            restored += 1
        await session.flush()
        print(f"从 {backup_path.name} 恢复 {restored} 行（已存在的跳过）。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="实际执行（默认 dry-run）")
    parser.add_argument("--restore", action="store_true", help="从备份回滚")
    parser.add_argument("--backup", help="回滚用的备份 JSON 路径")
    args = parser.parse_args()
    if args.restore:
        if not args.backup:
            raise SystemExit("--restore 需要 --backup <备份json路径>")
        raise SystemExit(asyncio.run(restore(Path(args.backup))))
    raise SystemExit(asyncio.run(run(args.execute)))
