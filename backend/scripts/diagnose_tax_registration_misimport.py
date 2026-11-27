# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：税费种认定被粘贴进错误账套（2026-11-21 建账未自动切换事故）。

用法::

    cd backend && ./venv/Scripts/python.exe scripts/diagnose_tax_registration_misimport.py \
        --company c_6048d58f7a00 --window-start 2026-11-21T09:05:00 --window-end 2026-11-21T09:10:00

只读，不写库。输出：

1. 目标主体身份（名称 / 启用日期 / 建档时间）
2. 该主体名下全部税费种认定行（标注哪些落在事故时间窗内）
3. 该主体名下 2026 年征期行，按「错导入新建 / 错导入被误豁免 / 未受影响」分类
4. 建议的清理范围（id 清单，供修复脚本核对）

背景：2026-11-21 09:05 主体管理页新建正式账套后未自动切换（前端已修复），
用户 62 秒后在新账套的成功弹窗里点「立即前往同步税费种认定」，粘贴数据落进了
当时仍停留的旧账套，import 端点随后用错误认定重算了旧账套的 2026 年征期。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from sqlalchemy import select  # noqa: E402

from db.models import Company, FilingDeadline, TaxRegistration  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.filing_deadline_service import AUTO_WAIVE_REASONS  # noqa: E402


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # 库里 timestamptz 经 asyncpg 返回带时区；naive 视为 UTC（服务端统一 utcnow 落库）
        return value.replace(tzinfo=timezone_utc())
    return value.astimezone(timezone_utc())


def timezone_utc():
    from datetime import timezone

    return timezone.utc


def _fmt(value: datetime | str | None) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        return value[:19].replace("T", " ")
    return value.strftime("%Y-%m-%d %H:%M:%S")


async def main(company_id: str, window_start: datetime, window_end: datetime) -> None:
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        if company is None:
            print(f"找不到主体 {company_id}")
            return
        payload = company.payload or {}
        print("=" * 72)
        print(f"主体 {company_id}")
        print(f"  名称       {payload.get('name')}")
        print(f"  启用日期   {payload.get('startDate')}")
        print(f"  建档时间   {_fmt(company.created_at)} (row) / {payload.get('created_at')} (payload)")
        print(f"  事故时间窗 {window_start:%Y-%m-%d %H:%M:%S} ~ {window_end:%Y-%m-%d %H:%M:%S} (UTC)")

        # ── 1. 税费种认定行 ────────────────────────────────────────────────
        regs = (
            await session.execute(
                select(TaxRegistration)
                .where(TaxRegistration.company_id == company_id)
                .order_by(TaxRegistration.created_at, TaxRegistration.seq)
            )
        ).scalars().all()
        in_window_regs: list[TaxRegistration] = []
        outside_regs: list[TaxRegistration] = []
        for row in regs:
            created = _as_utc(row.created_at)
            (in_window_regs if created and window_start <= created <= window_end else outside_regs).append(row)
        print("-" * 72)
        print(f"税费种认定行 共 {len(regs)} 条：时间窗内 {len(in_window_regs)} 条，窗外 {len(outside_regs)} 条")
        for label, rows in (("窗内（事故导入）", in_window_regs), ("窗外（更早存量）", outside_regs)):
            for row in rows:
                created = _as_utc(row.created_at)
                print(
                    f"  [{label}] seq={row.seq} {row.levy_item}/{row.levy_category}"
                    f"{('/' + row.levy_sub_item) if row.levy_sub_item else ''}"
                    f" 周期={row.filing_cycle} 状态={row.status} 来源={row.source}"
                    f" id={row.id} created={_fmt(created)}"
                )

        # ── 2. 征期行分类 ──────────────────────────────────────────────────
        deadlines = (
            await session.execute(
                select(FilingDeadline)
                .where(FilingDeadline.company_id == company_id)
                .order_by(FilingDeadline.created_at, FilingDeadline.tax_kind, FilingDeadline.period)
            )
        ).scalars().all()
        created_in_window: list[FilingDeadline] = []
        waived_in_window: list[FilingDeadline] = []
        untouched: list[FilingDeadline] = []
        for row in deadlines:
            created = _as_utc(row.created_at)
            evidence = row.filed_evidence if isinstance(row.filed_evidence, dict) else {}
            waived_at = _parse_iso(str(evidence.get("waivedAt") or ""))
            in_window = (
                (created and window_start <= created <= window_end)
                or (waived_at and window_start <= waived_at <= window_end)
            )
            if in_window:
                if created and window_start <= created <= window_end:
                    created_in_window.append(row)
                else:
                    waived_in_window.append(row)
            else:
                untouched.append(row)

        print("-" * 72)
        print(f"征期行 共 {len(deadlines)} 条：错导入后新建 {len(created_in_window)} 条，"
              f"错导入被误豁免 {len(waived_in_window)} 条，未受影响 {len(untouched)} 条")
        for label, rows in (
            ("错导入后新建", created_in_window),
            ("错导入被误豁免（原应为待申报）", waived_in_window),
            ("未受影响", untouched),
        ):
            for row in rows:
                evidence = row.filed_evidence if isinstance(row.filed_evidence, dict) else {}
                print(
                    f"  [{label}] {row.tax_kind} {row.period} 截止={row.due_date} 状态={row.status}"
                    f" filed_at={_fmt(_as_utc(row.filed_at))} created={_fmt(_as_utc(row.created_at))}"
                    f" evidence={json.dumps(evidence, ensure_ascii=False) if evidence else '-'}"
                    f" id={row.id}"
                )

        # ── 3. 建议清理范围 ────────────────────────────────────────────────
        print("-" * 72)
        print("建议清理范围（修复脚本需逐条核对 id 后执行，删除前先备份）")
        print(f"  tax_registrations 删除 {len(in_window_regs)} 条：")
        for row in in_window_regs:
            print(f"    {row.id}")
        print(f"  filing_deadlines 删除（错导入后新建且未申报）{sum(1 for r in created_in_window if r.status != 'filed')} 条：")
        for row in created_in_window:
            if row.status != "filed":
                print(f"    {row.id} ({row.tax_kind} {row.period} {row.status})")
        print(f"  filing_deadlines 恢复待申报 {len(waived_in_window)} 条：")
        for row in waived_in_window:
            print(f"    {row.id} ({row.tax_kind} {row.period})")
        if not in_window_regs and not created_in_window and not waived_in_window:
            print("  （时间窗内未发现事故痕迹：可能已清理，或时间窗设置不对）")
        else:
            print("  清理后需对 2026 年重跑 refresh_deadlines，按内置兜底清单重建正确征期。")


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, help="主体 company_id")
    parser.add_argument("--window-start", default="2026-11-21T09:05:00", help="事故窗口开始（UTC，含秒）")
    parser.add_argument("--window-end", default="2026-11-21T09:10:00", help="事故窗口结束（UTC，含秒）")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.window_start).replace(tzinfo=timezone_utc())
    end = datetime.fromisoformat(args.window_end).replace(tzinfo=timezone_utc())
    if end < start:
        raise SystemExit("window-end 早于 window-start")
    asyncio.run(main(args.company, start, end))
