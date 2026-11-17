# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读：冷启动试点「产品可用性」漏斗报告。

按户回放真实业务数据，回答「他们走到哪一步、卡在哪」：

1. 注册 / 最近登录（auth_users.created_at、last_login_at）
2. 建账套（auth_companies.created_at）
3. 上手就绪度（复用 setup_readiness_service，与用户界面同一份真值）
4. 首次导入（银行流水 / 微信账单 / 发票，取最早一条）
5. 首张凭证过账（journal_entries.posted_at，排除期初/结转）
6. 首次月结（accounting_periods.closed_at）
7. 月结阻断明细（period_close_runs.checks 里 status=blocking 的检查项）

**只读**：不写任何业务表，不触发 ensure_tenant，不做修复。
**无第三方埋点**：产品没有页面级事件流，本报告只用业务表 + 审计/用量表推导。

用法::

    cd backend && venv/bin/python scripts/pilot_funnel_report.py
    cd backend && venv/bin/python scripts/pilot_funnel_report.py --since 2026-11-01
    cd backend && venv/bin/python scripts/pilot_funnel_report.py --format csv
    cd backend && venv/bin/python scripts/pilot_funnel_report.py \\
        --attribution tmp/pilot_channels.csv      # 表头：phone,channel,cohort,batch

注意：报告含手机号与真实经营数据。只输出到本机终端或 tmp/（已被 .gitignore
忽略），不要提交、不要转发到外部群。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from sqlalchemy import func, or_, select  # noqa: E402

from db.models import (  # noqa: E402
    AccountingPeriod,
    AgentAction,
    AuthUser,
    Company,
    Invoice,
    JournalEntry,
    LegacyEntryProjection,
    LlmUsage,
    PeriodCloseRun,
    SourceTransaction,
)
from db.session import session_scope  # noqa: E402
from services.setup_readiness_service import compute_readiness  # noqa: E402

# 就绪度步骤的中文短名（长标题留给界面，报告里要能一列放下）
READINESS_SHORT = {
    "company_profile": "主体",
    "tax_registration": "税种",
    "opening_balance": "期初",
    "legacy_import": "旧账",
    "first_voucher": "首张凭证",
    "report_view": "报表",
    "chart_accounts": "科目",
}

PENDING_STATUSES = ("todo", "doing", "locked")
NON_BUSINESS_ENTRY_TYPES = ("opening", "closing")
BLOCKING = "blocking"
DASH = "—"


def _parse_dt(value: Any) -> datetime | None:
    """把 datetime 或 ISO 文本统一成带时区的 datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fmt_date(value: Any) -> str:
    parsed = _parse_dt(value)
    return parsed.date().isoformat() if parsed else DASH


def _days_between(start: Any, end: Any) -> float | None:
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    return max((end_dt - start_dt).total_seconds() / 86400, 0.0)


def _median_days(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return round(median(clean), 1) if clean else None


def _norm_phone(value: Any) -> str:
    """归一化手机号，容忍 +86 / 空格 / 短横线。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits


def load_attribution(path: Path) -> dict[str, dict[str, str]]:
    """报名渠道表：phone,channel,cohort,batch（其余列忽略）。"""
    mapping: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            phone = _norm_phone(raw.get("phone"))
            if not phone:
                continue
            mapping[phone] = {
                "channel": (raw.get("channel") or "").strip(),
                "cohort": (raw.get("cohort") or "").strip(),
                "batch": (raw.get("batch") or "").strip(),
            }
    return mapping


def _blocked_summary(checks: Any) -> tuple[list[str], dict[str, str]]:
    """从一次关账运行的 checks 里取出阻断项名称。

    返回 (阻断项名称列表, code→name 映射)。checks 结构见
    accounting_core_service.period_close_checks：每项含 code/name/status。
    """
    names: list[str] = []
    code_names: dict[str, str] = {}
    if not isinstance(checks, list):
        return names, code_names
    for item in checks:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or code or "未命名检查").strip()
        if code and name:
            code_names[code] = name
        if str(item.get("status") or "") == BLOCKING:
            names.append(name)
    return names, code_names


async def collect_report(
    session,
    *,
    since: str | None = None,
    company_id: str | None = None,
    include_demo: bool = False,
    attribution: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """生成漏斗报告数据（纯读，调用方负责 session 生命周期）。

    ``since`` 按 auth_companies.created_at（ISO 文本）做前缀比较，
    传 ``2026-11-01`` 即只看该日之后创建的账套。
    """
    attribution = attribution or {}
    stmt = select(Company).order_by(Company.created_at)
    if since:
        stmt = stmt.where(Company.created_at >= since)
    if company_id:
        stmt = stmt.where(Company.company_id == company_id)
    companies = list((await session.execute(stmt)).scalars().all())
    if not include_demo:
        companies = [
            company
            for company in companies
            if not (company.payload or {}).get("isDemo")
        ]

    company_ids = [company.company_id for company in companies]
    owner_ids = {company.owner_user_id for company in companies}

    users: dict[str, AuthUser] = {}
    if owner_ids:
        users = {
            user.user_id: user
            for user in (
                await session.execute(
                    select(AuthUser).where(AuthUser.user_id.in_(owner_ids))
                )
            ).scalars()
        }

    first_source: dict[str, Any] = {}
    first_wechat: dict[str, Any] = {}
    first_invoice: dict[str, Any] = {}
    first_posted: dict[str, Any] = {}
    posted_counts: dict[str, int] = {}
    first_closed: dict[str, Any] = {}
    closed_counts: dict[str, int] = {}
    llm_stats: dict[str, tuple[int, int]] = {}
    agent_counts: dict[str, int] = {}
    runs_by_company: dict[str, list[PeriodCloseRun]] = {}

    if company_ids:
        first_source = dict(
            (
                await session.execute(
                    select(
                        SourceTransaction.company_id,
                        func.min(SourceTransaction.created_at),
                    )
                    .where(SourceTransaction.company_id.in_(company_ids))
                    .group_by(SourceTransaction.company_id)
                )
            ).all()
        )
        first_wechat = dict(
            (
                await session.execute(
                    select(
                        LegacyEntryProjection.company_id,
                        func.min(LegacyEntryProjection.created_at),
                    )
                    .where(
                        LegacyEntryProjection.company_id.in_(company_ids),
                        or_(
                            LegacyEntryProjection.payload.like(
                                '%"wechat_transaction_key"%'
                            ),
                            LegacyEntryProjection.payload.like('%"wechat_order_no"%'),
                        ),
                    )
                    .group_by(LegacyEntryProjection.company_id)
                )
            ).all()
        )
        first_invoice = dict(
            (
                await session.execute(
                    select(Invoice.company_id, func.min(Invoice.created_at))
                    .where(Invoice.company_id.in_(company_ids))
                    .group_by(Invoice.company_id)
                )
            ).all()
        )
        posted_rows = (
            await session.execute(
                select(
                    JournalEntry.company_id,
                    func.min(JournalEntry.posted_at),
                    func.count(JournalEntry.id),
                )
                .where(
                    JournalEntry.company_id.in_(company_ids),
                    JournalEntry.posted_at.is_not(None),
                    JournalEntry.status.in_(("posted", "reversed")),
                    JournalEntry.entry_type.not_in(NON_BUSINESS_ENTRY_TYPES),
                )
                .group_by(JournalEntry.company_id)
            )
        ).all()
        for cid, posted_at, count in posted_rows:
            first_posted[cid] = posted_at
            posted_counts[cid] = int(count or 0)
        closed_rows = (
            await session.execute(
                select(
                    AccountingPeriod.company_id,
                    func.min(AccountingPeriod.closed_at),
                    func.count(AccountingPeriod.id),
                )
                .where(
                    AccountingPeriod.company_id.in_(company_ids),
                    AccountingPeriod.status == "closed",
                    AccountingPeriod.closed_at.is_not(None),
                )
                .group_by(AccountingPeriod.company_id)
            )
        ).all()
        for cid, closed_at, count in closed_rows:
            first_closed[cid] = closed_at
            closed_counts[cid] = int(count or 0)
        for cid, calls, tokens in (
            await session.execute(
                select(
                    LlmUsage.company_id,
                    func.count(LlmUsage.id),
                    func.coalesce(func.sum(LlmUsage.total_tokens), 0),
                )
                .where(LlmUsage.company_id.in_(company_ids))
                .group_by(LlmUsage.company_id)
            )
        ).all():
            llm_stats[cid] = (int(calls or 0), int(tokens or 0))
        agent_counts = dict(
            (
                await session.execute(
                    select(AgentAction.company_id, func.count(AgentAction.id))
                    .where(AgentAction.company_id.in_(company_ids))
                    .group_by(AgentAction.company_id)
                )
            ).all()
        )
        for run in (
            await session.execute(
                select(PeriodCloseRun)
                .where(PeriodCloseRun.company_id.in_(company_ids))
                .order_by(PeriodCloseRun.created_at)
            )
        ).scalars():
            runs_by_company.setdefault(run.company_id, []).append(run)

    pending_counter: Counter[str] = Counter()
    blocked_counter: Counter[str] = Counter()
    blocked_companies: dict[str, set[str]] = {}
    rows: list[dict[str, Any]] = []

    for company in companies:
        user = users.get(company.owner_user_id)
        payload = company.payload or {}
        signup_at = user.created_at if user else company.created_at
        last_login_at = user.last_login_at if user else None
        attr = attribution.get(_norm_phone(user.phone if user else ""), {})

        readiness_completed: int | None = None
        readiness_total: int | None = None
        pending_short: list[str] = []
        try:
            readiness = await compute_readiness(
                session,
                company.company_id,
                user_id=company.owner_user_id,
                view="boss",
            )
            readiness_completed = int(readiness["completed"])
            readiness_total = int(readiness["total"])
            for item in readiness["items"]:
                if item.get("informational"):
                    continue
                if item.get("status") in PENDING_STATUSES:
                    short = READINESS_SHORT.get(item["key"], item["key"])
                    pending_short.append(short)
                    pending_counter[short] += 1
        except Exception as exc:  # 单户异常不拖垮整份只读报告
            pending_short = [f"读取失败：{type(exc).__name__}"]

        import_candidates = [
            _parse_dt(first_source.get(company.company_id)),
            _parse_dt(first_wechat.get(company.company_id)),
            _parse_dt(first_invoice.get(company.company_id)),
        ]
        import_candidates = [item for item in import_candidates if item is not None]
        first_import = min(import_candidates) if import_candidates else None
        first_posted_at = _parse_dt(first_posted.get(company.company_id))
        first_closed_at = _parse_dt(first_closed.get(company.company_id))

        runs = runs_by_company.get(company.company_id, [])
        blocked_names: list[str] = []
        company_blocked = 0
        for run in runs:
            if run.status != "blocked":
                continue
            company_blocked += 1
            names, _ = _blocked_summary(run.checks)
            for name in names:
                blocked_names.append(name)
                blocked_counter[name] += 1
                blocked_companies.setdefault(name, set()).add(company.company_id)

        blocked_display = ""
        if blocked_names:
            parts = [
                f"{name}×{count}"
                for name, count in Counter(blocked_names).most_common()
            ]
            blocked_display = "、".join(parts)

        calls, tokens = llm_stats.get(company.company_id, (0, 0))
        rows.append(
            {
                "company_id": company.company_id,
                "phone": (user.phone if user else "") or "",
                "nickname": ((user.nickname if user else "") or str(payload.get("name") or "")),
                "channel": attr.get("channel", ""),
                "cohort": attr.get("cohort", ""),
                "batch": attr.get("batch", ""),
                "signup_at": _fmt_date(signup_at),
                "last_login_at": _fmt_date(last_login_at),
                "company_created_at": _fmt_date(company.created_at),
                "readiness": (
                    f"{readiness_completed}/{readiness_total}"
                    if readiness_total is not None
                    else DASH
                ),
                "readiness_pending": "、".join(pending_short),
                "first_import_at": _fmt_date(first_import),
                "first_posted_at": _fmt_date(first_posted_at),
                "first_closed_at": _fmt_date(first_closed_at),
                "close_attempts": len(runs),
                "close_blocked": company_blocked,
                "blocked_codes": blocked_display,
                "ai_calls": calls,
                "ai_tokens": tokens,
                "agent_actions": int(agent_counts.get(company.company_id, 0) or 0),
                "posted_entries": int(posted_counts.get(company.company_id, 0) or 0),
                "closed_periods": int(closed_counts.get(company.company_id, 0) or 0),
                # 内部计算用，不输出
                "_first_import": first_import,
                "_first_posted": first_posted_at,
                "_first_closed": first_closed_at,
                "_signup": _parse_dt(signup_at),
                "_last_login": _parse_dt(last_login_at),
            }
        )

    now = datetime.now(timezone.utc)
    active_7d = sum(
        1
        for row in rows
        if row["_last_login"] is not None
        and (now - row["_last_login"]) <= timedelta(days=7)
    )
    total = len(rows)
    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "since": since or "",
        "company_filter": company_id or "",
        "include_demo": include_demo,
        "households": total,
        "imported": sum(1 for row in rows if row["_first_import"]),
        "posted": sum(1 for row in rows if row["_first_posted"]),
        "closed": sum(1 for row in rows if row["_first_closed"]),
        "active_7d": active_7d,
        "median_days": {
            "import": _median_days(
                [_days_between(row["_signup"], row["_first_import"]) for row in rows]
            ),
            "post": _median_days(
                [_days_between(row["_signup"], row["_first_posted"]) for row in rows]
            ),
            "close": _median_days(
                [_days_between(row["_signup"], row["_first_closed"]) for row in rows]
            ),
        },
        "readiness_pending_top": pending_counter.most_common(),
        "blocked_top": [
            {
                "name": name,
                "count": count,
                "companies": len(blocked_companies.get(name, set())),
            }
            for name, count in blocked_counter.most_common()
        ],
        "rows": rows,
    }
    return summary


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.0f}%" if whole else DASH


def _channel_display(row: dict[str, Any]) -> str:
    bits = [bit for bit in (row.get("channel"), row.get("batch")) if bit]
    return " / ".join(bits) if bits else DASH


def _md_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else DASH)
    return text.replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report
    rows = summary["rows"]
    total = summary["households"]
    lines: list[str] = []
    lines.append("# 冷启动试点 · 产品可用性漏斗（只读）")
    lines.append("")
    lines.append(f"生成时间：{summary['generated_at']}")
    scope = f"公司创建于 {summary['since']} 之后" if summary["since"] else "全部账套"
    demo = "含演示账套" if summary["include_demo"] else "不含演示账套"
    lines.append(f"统计范围：{scope}，{demo}；共 {total} 户。")
    lines.append("")
    lines.append("> 含手机号与真实经营数据：只存本机 / `tmp/`，不要提交、不要外发。")
    lines.append("")
    lines.append("## 漏斗总览")
    lines.append("")
    lines.append("| 环节 | 户数 | 占建账套 |")
    lines.append("| --- | ---: | ---: |")
    lines.append(f"| 建账套 | {total} | {_pct(total, total)} |")
    lines.append(
        f"| 有导入（银行/微信/发票任一） | {summary['imported']} | "
        f"{_pct(summary['imported'], total)} |"
    )
    lines.append(
        f"| 有凭证过账 | {summary['posted']} | {_pct(summary['posted'], total)} |"
    )
    lines.append(
        f"| 有月结关账 | {summary['closed']} | {_pct(summary['closed'], total)} |"
    )
    lines.append(
        f"| 近 7 天有登录 | {summary['active_7d']} | "
        f"{_pct(summary['active_7d'], total)} |"
    )
    lines.append("")
    medians = summary["median_days"]
    lines.append("## 速度（中位，天）")
    lines.append("")
    lines.append("| 区间 | 中位天数 |")
    lines.append("| --- | ---: |")
    for label, key in (
        ("注册 → 首次导入", "import"),
        ("注册 → 首张凭证过账", "post"),
        ("注册 → 首次月结", "close"),
    ):
        value = medians[key]
        lines.append(f"| {label} | {value if value is not None else DASH} |")
    lines.append("")
    lines.append("## 上手未完成步骤 Top")
    lines.append("")
    if summary["readiness_pending_top"]:
        lines.append("| 步骤 | 未完成户数 |")
        lines.append("| --- | ---: |")
        for name, count in summary["readiness_pending_top"][:10]:
            lines.append(f"| {_md_cell(name)} | {count} |")
    else:
        lines.append("无（全部就绪或未采集到就绪度）。")
    lines.append("")
    lines.append("## 月结阻断 Top（period_close_runs）")
    lines.append("")
    if summary["blocked_top"]:
        lines.append("| 检查项 | 阻断次数 | 涉及户数 |")
        lines.append("| --- | ---: | ---: |")
        for item in summary["blocked_top"][:10]:
            lines.append(
                f"| {_md_cell(item['name'])} | {item['count']} | {item['companies']} |"
            )
    else:
        lines.append("无阻断记录（或尚未有人尝试月结）。")
    lines.append("")
    lines.append("## 分户明细")
    lines.append("")
    headers = [
        "手机号",
        "昵称",
        "渠道/批次",
        "注册",
        "最近登录",
        "就绪度",
        "未完成",
        "首次导入",
        "首张过账",
        "首次月结",
        "月结阻断",
        "AI调用",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join([" --- "] * len(headers)) + "|")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(row["phone"]),
                    _md_cell(row["nickname"]),
                    _md_cell(_channel_display(row)),
                    _md_cell(row["signup_at"]),
                    _md_cell(row["last_login_at"]),
                    _md_cell(row["readiness"]),
                    _md_cell(row["readiness_pending"]),
                    _md_cell(row["first_import_at"]),
                    _md_cell(row["first_posted_at"]),
                    _md_cell(row["first_closed_at"]),
                    _md_cell(
                        f"{row['close_blocked']} 次（{row['blocked_codes']}）"
                        if row["close_blocked"] and row["blocked_codes"]
                        else (f"{row['close_blocked']} 次" if row["close_blocked"] else "")
                    ),
                    _md_cell(row["ai_calls"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


CSV_COLUMNS = [
    ("company_id", "company_id"),
    ("phone", "phone"),
    ("nickname", "nickname"),
    ("channel", "channel"),
    ("cohort", "cohort"),
    ("batch", "batch"),
    ("signup_at", "signup_at"),
    ("last_login_at", "last_login_at"),
    ("company_created_at", "company_created_at"),
    ("readiness", "readiness"),
    ("readiness_pending", "readiness_pending"),
    ("first_import_at", "first_import_at"),
    ("first_posted_at", "first_posted_at"),
    ("first_closed_at", "first_closed_at"),
    ("close_attempts", "close_attempts"),
    ("close_blocked", "close_blocked"),
    ("blocked_codes", "blocked_codes"),
    ("ai_calls", "ai_calls"),
    ("ai_tokens", "ai_tokens"),
    ("agent_actions", "agent_actions"),
    ("posted_entries", "posted_entries"),
    ("closed_periods", "closed_periods"),
]


def render_csv(report: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in CSV_COLUMNS])
    for row in report["rows"]:
        writer.writerow([row.get(key, "") for key, _ in CSV_COLUMNS])
    return buffer.getvalue()


async def _run(args: argparse.Namespace) -> None:
    attribution = (
        load_attribution(Path(args.attribution)) if args.attribution else {}
    )
    async with session_scope() as session:
        report = await collect_report(
            session,
            since=args.since,
            company_id=args.company,
            include_demo=args.include_demo,
            attribution=attribution,
        )
    if args.format == "csv":
        sys.stdout.write(render_csv(report))
    else:
        sys.stdout.write(render_markdown(report))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="冷启动试点产品可用性漏斗（只读，不写库）"
    )
    parser.add_argument(
        "--since",
        help="只看该公司创建日期之后的账套，如 2026-11-01",
    )
    parser.add_argument("--company", help="只看单个公司 ID")
    parser.add_argument(
        "--format",
        choices=("md", "csv"),
        default="md",
        help="输出格式，默认 md",
    )
    parser.add_argument(
        "--attribution",
        help="报名渠道映射 CSV（phone,channel,cohort,batch）",
    )
    parser.add_argument(
        "--include-demo",
        action="store_true",
        help="包含演示账套（默认排除 payload.isDemo）",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
