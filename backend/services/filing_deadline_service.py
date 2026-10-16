# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""征期日历服务：小规模纳税人申报节奏（M3）。

征期规则为配置数据；节假日顺延日期按税局公告**手工维护**（DEADLINE_OVERRIDES），
每年初应复核一次。未命中 override 的税种按"届满月 15 日、遇周末顺延至次周一"兜底。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from db.models import FilingDeadline, new_id
from db.session import session_scope

logger = logging.getLogger(__name__)

# 税种定义：kind -> (名称, 频率, 指引)
TAX_KINDS: Dict[str, Dict[str, str]] = {
    "vat": {
        "name": "增值税及附加税费",
        "freq": "quarter",
        "guide": "电子税务局 → 我要办税 → 税费申报及缴纳 → 增值税小规模纳税人申报。按作业包逐字段填写核对后提交。",
    },
    "cit": {
        "name": "企业所得税预缴（A 类）",
        "freq": "quarter",
        "guide": "电子税务局 → 税费申报及缴纳 → 企业所得税月（季）度预缴申报（A 类）。按作业包 A200000 字段填写。",
    },
    "fin_statements": {
        "name": "财务报表报送",
        "freq": "quarter",
        "guide": "电子税务局 → 税费申报及缴纳 → 财务报表报送。利润表/资产负债表/现金流量表可在经营报表页导出后上传。",
    },
    "stamp_duty": {
        "name": "印花税",
        "freq": "quarter",
        "guide": "按季申报（税源发生时）。无应税合同/账簿税源时可做零申报；有营业账簿按实收资本万分之 2.5（减半后）。在作业包中确认本期税源。",
    },
    "iit_withholding": {
        "name": "个税扣缴",
        "freq": "month",
        "guide": "自然人电子税务局（扣缴端，桌面客户端）申报。无员工、无工资发放时可零申报或暂不处理；有员工必须每月申报。",
    },
    "annual_report": {
        "name": "工商年报（市监局）",
        "freq": "annual",
        "guide": "国家企业信用信息公示系统（gsxt.gov.cn），每年 1 月 1 日至 6 月 30 日报送上一年度年报。逾期列入经营异常名录。",
    },
    "cit_annual": {
        "name": "企业所得税汇算清缴",
        "freq": "annual_cit",
        "guide": "次年 5 月 31 日前完成上一年度汇算清缴（A 类年报）。需税局 UKey/账号，建议提前准备费用发票与扣除凭证。",
    },
}

# 节假日顺延后的实际截止日（按税局公告手工维护；YYYY: {kind: {period: date}})
DEADLINE_OVERRIDES: Dict[int, Dict[str, Dict[str, str]]] = {
    2026: {
        "quarter": {
            "2026-Q1": "2026-04-20",   # 清明+五一顺延（示例，按公告校准）
            "2026-Q2": "2026-07-15",
            "2026-Q3": "2026-10-22",   # 国庆中秋长假顺延（按公告校准）
            "2026-Q4": "2027-01-15",   # 元旦临近，按公告校准
        },
        "month": {},   # 月度按 15 日兜底顺延
    },
    2027: {
        "quarter": {
            "2027-Q1": "2027-04-15",
            "2027-Q2": "2027-07-15",
            "2027-Q3": "2027-10-15",
            "2027-Q4": "2028-01-17",
        },
        "month": {},
    },
}


def _next_weekday(d: date) -> date:
    """周末顺延到周一。"""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _quarter_label(period: str) -> Optional[tuple[int, int]]:
    text = (period or "").upper().replace("季度", "Q").replace("第", "").replace(" ", "")
    try:
        if "-Q" in text:
            year_text, q_text = text.split("-Q", 1)
            return int(year_text), int(q_text)
    except ValueError:
        pass
    return None


def compute_due_date(tax_kind: str, period: str) -> Optional[date]:
    """计算某税种某期间的征期。"""
    freq = TAX_KINDS.get(tax_kind, {}).get("freq")
    if not freq:
        return None
    overrides = DEADLINE_OVERRIDES.get(int(period[:4]) if period[:4].isdigit() else 0, {})

    if freq in ("quarter", "annual_cit"):
        parsed = _quarter_label(period)
        if not parsed:
            return None
        year, quarter = parsed
        if freq == "annual_cit":
            return date(year + 1, 5, 31)
        key = f"{year}-Q{quarter}"
        override = overrides.get("quarter", {}).get(key)
        if override:
            return date.fromisoformat(override)
        due_month = quarter * 3 + 1
        due = date(year + (1 if due_month > 12 else 0), (due_month - 1) % 12 + 1, 15)
        return _next_weekday(due)

    if freq == "month":
        try:
            year, month = int(period[:4]), int(period[5:7])
        except (TypeError, ValueError):
            return None
        override = overrides.get("month", {}).get(period)
        if override:
            return date.fromisoformat(override)
        # 月度税费纳税申报期为所属月份的次月 15 日（遇法定节假日顺延）
        due_year = year + (1 if month == 12 else 0)
        due_month = 1 if month == 12 else month + 1
        # 10 月国庆黄金周长假顺延校准
        if due_year == 2026 and due_month == 10:
            return date(2026, 10, 22)
        return _next_weekday(date(due_year, due_month, 15))

    if freq == "annual":
        try:
            year = int(period[:4])
        except (TypeError, ValueError):
            return None
        return date(year + 1, 6, 30)

    return None


def periods_for_year(year: int) -> Dict[str, List[str]]:
    """生成某年应覆盖的期间清单。"""
    return {
        "quarter": [f"{year}-Q{q}" for q in (1, 2, 3, 4)],
        "month": [f"{year}-{m:02d}" for m in range(1, 13)],
        "annual": [str(year)],
        "annual_cit": [f"{year}-Q4"],  # 汇算清缴挂在 Q4 期间上
    }


async def _company_onboard_date(company_id: str) -> Optional[date]:
    """公司建档日（系统内创建时间）。征期物化以其为起点：建档前已截止的期间不制造逾期。"""
    from db.models import Company

    async with session_scope() as session:
        row = (
            await session.execute(select(Company.created_at).where(Company.company_id == company_id))
        ).scalar_one_or_none()
    if not row:
        return None
    try:
        return date.fromisoformat(str(row)[:10])
    except ValueError:
        return None


async def refresh_deadlines(company_id: str, year: Optional[int] = None) -> Dict[str, Any]:
    """按规则物化某年的征期记录（幂等；已存在的保留状态并校准 due_date）。

    公司建档日之前已截止的期间不物化为待办；历史上已物化为 pending 的存量自动豁免
    （waived），避免首次打开页面即出现「逾期 176 天」的假噪音。
    """
    year = year or date.today().year
    onboard_date = await _company_onboard_date(company_id)
    created = 0
    auto_waived = 0
    period_map = periods_for_year(year)
    for tax_kind, meta in TAX_KINDS.items():
        if meta["freq"] in ("quarter", "annual_cit", "annual"):
            periods = period_map.get(meta["freq"], [])
        else:
            periods = period_map["month"]
        for period in periods:
            # 个税扣缴、汇算等仅在有意义的窗口生成：全年物化即可，展示层再过滤
            due = compute_due_date(tax_kind, period)
            if not due:
                continue
            if onboard_date and due < onboard_date:
                # 建档前已截止的期间：不再新建；此前误物化的 pending 存量自动豁免
                async with session_scope() as session:
                    legacy = (
                        await session.execute(
                            select(FilingDeadline).where(
                                FilingDeadline.company_id == company_id,
                                FilingDeadline.tax_kind == tax_kind,
                                FilingDeadline.period == period,
                            )
                        )
                    ).scalar_one_or_none()
                    if legacy and legacy.status == "pending":
                        legacy.status = "waived"
                        legacy.filed_evidence = {
                            "reason": "pre_onboarding",
                            "auto": True,
                            "waivedAt": datetime.utcnow().isoformat(),
                        }
                        auto_waived += 1
                continue
            async with session_scope() as session:
                existing_record = (
                    await session.execute(
                        select(FilingDeadline).where(
                            FilingDeadline.company_id == company_id,
                            FilingDeadline.tax_kind == tax_kind,
                            FilingDeadline.period == period,
                        )
                    )
                ).scalar_one_or_none()
                if existing_record:
                    # 若计算规则校准（如修正错误的所属月中旬为次月中旬），同步校准未锁定的 due_date
                    if existing_record.due_date != due:
                        existing_record.due_date = due
                    continue
                session.add(
                    FilingDeadline(
                        id=new_id(),
                        company_id=company_id,
                        tax_kind=tax_kind,
                        period=period,
                        due_date=due,
                        status="pending",
                    )
                )
                created += 1
    return {"year": year, "created": created, "autoWaived": auto_waived}


async def list_deadlines(
    company_id: str, *, upcoming_days: Optional[int] = None, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """列出征期（默认全部未完成+近期完成），附剩余天数。"""
    today = date.today()
    async with session_scope() as session:
        stmt = select(FilingDeadline).where(FilingDeadline.company_id == company_id)
        if status:
            stmt = stmt.where(FilingDeadline.status == status)
        stmt = stmt.order_by(FilingDeadline.due_date.asc()).limit(200)
        rows = (await session.execute(stmt)).scalars().all()
        out = []
        for row in rows:
            days_left = (row.due_date - today).days
            if upcoming_days is not None and days_left > upcoming_days:
                continue
            meta = TAX_KINDS.get(row.tax_kind, {})
            out.append(
                {
                    "id": row.id,
                    "taxKind": row.tax_kind,
                    "taxName": meta.get("name", row.tax_kind),
                    "guide": meta.get("guide", ""),
                    "period": row.period,
                    "dueDate": row.due_date.isoformat(),
                    "daysLeft": days_left,
                    "status": row.status,
                    "filedAt": row.filed_at.isoformat() if row.filed_at else None,
                    "filedEvidence": row.filed_evidence,
                }
            )
        return out


async def mark_filed(
    company_id: str,
    user_id: str,
    deadline_id: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """标记已申报并归档回执信息（闭环）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.id == deadline_id, FilingDeadline.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("征期记录不存在")
        row.status = "filed"
        row.filed_at = datetime.utcnow()
        row.filed_evidence = evidence or {}
        result = {
            "id": row.id,
            "taxKind": row.tax_kind,
            "period": row.period,
            "status": row.status,
            "filedAt": row.filed_at.isoformat(),
        }
    from services.audit_service import log_action

    await log_action(company_id, user_id, "filing_mark_filed", "filing_deadline", deadline_id, evidence or {})
    return result


async def waive_deadline(
    company_id: str,
    user_id: str,
    deadline_id: str,
    *,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """把某征期标记为「本期不适用」（豁免），用于建档前期间等确实无需申报的场景。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.id == deadline_id, FilingDeadline.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("征期记录不存在")
        if row.status != "pending":
            raise ValueError("仅待申报状态可标记为不适用")
        row.status = "waived"
        row.filed_evidence = {
            "reason": reason or "manual",
            "via": "tax-filing-page",
            "waivedAt": datetime.utcnow().isoformat(),
        }
        result = {
            "id": row.id,
            "taxKind": row.tax_kind,
            "period": row.period,
            "status": row.status,
        }
    from services.audit_service import log_action

    await log_action(company_id, user_id, "filing_waive", "filing_deadline", deadline_id, {"reason": reason or "manual"})
    return result


def deadline_todos(deadlines: List[Dict[str, Any]], *, window_days: int = 14) -> List[Dict[str, Any]]:
    """把临近征期（默认 14 天内/逾期 60 天内）转成 todo 形态，供 todo_service 注入。

    逾期超过 60 天的历史征期不再制造待办噪音（在税报页仍可见，可手动补记/豁免）。
    """
    todos: List[Dict[str, Any]] = []
    for d in deadlines:
        if d["status"] != "pending" or d["daysLeft"] > window_days or d["daysLeft"] < -60:
            continue
        overdue = d["daysLeft"] < 0
        days_text = f"已逾期 {-d['daysLeft']} 天" if overdue else f"剩 {d['daysLeft']} 天"
        todos.append(
            {
                "id": f"filing:{d['id']}",
                "todoKey": f"filing:{d['id']}",
                "type": "filing",
                "category": "filing",
                "level": "high" if (overdue or d["daysLeft"] <= 3) else "medium",
                "priority": "high" if (overdue or d["daysLeft"] <= 3) else "medium",
                "title": f"{'逾期未申报' if overdue else '征期临近'} - {d['taxName']}（{d['period']}）",
                "desc": f"截止 {d['dueDate']}（{days_text}）。{d['guide']}",
                "done": False,
                "dismissed": False,
                "amount": 0,
                "meta": {"deadlineId": d["id"], "ruleId": "filing_deadline"},
            }
        )
    return todos
