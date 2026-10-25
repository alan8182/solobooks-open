# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""期间解析：把「业务日期」翻译成「记账期间」。

背景：`JournalEntry.occurred_on` 是业务日期（开票日、交易日），`period_start`
是记账期间（凭证号、关账锁、期末结转）。两者在正常情况下同月，但单据迟到时
会分叉——8 月的发票 9 月才拿到，8 月已关账，于是这笔业务只能记到后面的未关账
期间。

准则口径：《小企业会计准则》第 88 条——会计差错在「发现当期」更正，不追溯。
「发现当期」就是会计手上正在做的账期，也就是最早的未关账期间，不是最新的月份。
落到最新月份会跨季度，同时改写两个季度的所得税预缴，并可能击穿小规模纳税人
按季 30 万的免税判断，所以这里取 min 而不是 max。

本模块只做解析，不写库。是否补记、台账怎么落，由调用方决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    BACKFILL_CROSS_YEAR,
    BACKFILL_NO_OPEN_PERIOD,
    BACKFILL_TARGET_INVALID,
    PERIOD_CLOSED,
    AccountingError,
)
from db.models import AccountingPeriod

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 与 accounting_core_service.assert_period_writable 保持同一口径
CLOSED_PERIOD_STATUSES = ("closing", "closed")

# 系统凭证没有「业务日期」概念，不参与补记判定
SYSTEM_ENTRY_TYPES = ("opening", "closing", "reversal")


@dataclass(frozen=True)
class PeriodResolution:
    """一次记账的期间归属结论。"""

    business_date: date
    business_period: date
    posting_period: date
    is_backfill: bool
    cross_quarter: bool
    cross_year: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "businessDate": self.business_date.isoformat(),
            "businessPeriod": self.business_period.strftime("%Y-%m"),
            "postingPeriod": self.posting_period.strftime("%Y-%m"),
            "isBackfill": self.is_backfill,
            "crossQuarter": self.cross_quarter,
            "crossYear": self.cross_year,
            "reason": self.reason,
        }


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise AccountingError("INVALID_ACCOUNTING_DATE", "会计日期不能为空")
    if len(raw) == 7:
        raw = f"{raw}-01"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise AccountingError("INVALID_ACCOUNTING_DATE", "会计日期格式必须为 YYYY-MM-DD") from exc


def quarter_of(value: date) -> int:
    return (value.month - 1) // 3 + 1


def _current_month() -> date:
    return datetime.now(SHANGHAI).date().replace(day=1)


async def _period_statuses(session: AsyncSession, company_id: str) -> dict[date, str]:
    rows = (
        await session.execute(
            select(AccountingPeriod.period_start, AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _next_month(value: date) -> date:
    return date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)


def earliest_open_period(statuses: Mapping[date, str], floor: date) -> date | None:
    """最早的未关账期间（>= floor）。

    期间行是懒创建的：只有真正发生过业务或结过账的月份才有记录。所以「没有行」
    等同于「未关账」，不能只从已有的 open 行里挑，否则在 10 月补记 8 月单据时
    会因为 9 月还没建期间行而被误判为 10 月。
    """
    cursor = floor
    limit = max(_current_month(), floor)
    for _ in range(120):  # 最多向后找 10 年，防御性上限
        if cursor > limit:
            return None
        if statuses.get(cursor) not in CLOSED_PERIOD_STATUSES:
            return cursor
        cursor = _next_month(cursor)
    return None


async def resolve_posting_period(
    session: AsyncSession,
    company_id: str,
    business_date: Any,
    *,
    requested_period: Any = None,
    allow_backfill: bool = True,
    allow_cross_year: bool = False,
) -> PeriodResolution:
    """决定一笔业务记到哪个期间。

    - 业务日期所属期间未关账 → 就用它。
    - 已关账且 ``allow_backfill`` → 落到最早的未关账期间；跨季度只标记不阻断，
      跨年默认阻断。
    - 已关账但调用方未声明可补记 → 仍按原口径抛 PERIOD_CLOSED，并在 context 里
      给出 ``canBackfill`` 与 ``suggestedPostingPeriod``，让前端据此提示用户。
      补记会把凭证写进用户没指定的月份，必须由调用方显式同意，不能静默发生。
    - 显式指定期间 → 必须未关账且不早于业务期间。
    """
    occurred_on = _as_date(business_date)
    business_period = occurred_on.replace(day=1)
    statuses = await _period_statuses(session, company_id)

    requested: date | None = None
    if requested_period not in (None, ""):
        requested = _as_date(requested_period).replace(day=1)
        if requested < business_period:
            raise AccountingError(
                BACKFILL_TARGET_INVALID,
                "记账期间不能早于业务日期所属期间",
                422,
                {"businessPeriod": business_period.strftime("%Y-%m"), "postingPeriod": requested.strftime("%Y-%m")},
            )
        if statuses.get(requested) in CLOSED_PERIOD_STATUSES:
            raise AccountingError(
                PERIOD_CLOSED,
                f"{requested.strftime('%Y-%m')} 已关账，不能指定为记账期间",
                409,
                {"period": requested.strftime("%Y-%m")},
            )

    if requested is not None:
        posting_period = requested
    elif statuses.get(business_period) not in CLOSED_PERIOD_STATUSES:
        posting_period = business_period
    else:
        target = earliest_open_period(statuses, business_period)
        # 未声明可补记时，答案就是「期间已关账」，与补记有没有落点无关：
        # 老调用方（工资、结算等）依赖这个错误码判断能否继续。
        if not allow_backfill:
            raise AccountingError(
                PERIOD_CLOSED,
                f"该会计期间 {business_period.strftime('%Y-%m')} 已锁账，不能修改账务",
                409,
                {
                    "period": business_period.strftime("%Y-%m"),
                    "canBackfill": target is not None,
                    "suggestedPostingPeriod": target.strftime("%Y-%m") if target else None,
                    "crossQuarter": bool(target) and quarter_of(target) != quarter_of(business_period),
                    "crossYear": bool(target) and target.year != business_period.year,
                },
            )
        if target is None:
            raise AccountingError(
                BACKFILL_NO_OPEN_PERIOD,
                "没有可用的未关账期间，无法补记；请先检查账套启用日期或反结账",
                409,
                {"businessPeriod": business_period.strftime("%Y-%m")},
            )
        posting_period = target

    is_backfill = posting_period != business_period
    cross_year = is_backfill and posting_period.year != business_period.year
    cross_quarter = is_backfill and (
        posting_period.year != business_period.year or quarter_of(posting_period) != quarter_of(business_period)
    )

    if cross_year and not allow_cross_year:
        raise AccountingError(
            BACKFILL_CROSS_YEAR,
            (
                f"业务日期 {occurred_on.isoformat()} 属于 {business_period.year} 年，"
                f"系统默认不跨年补记。请由财税人员按以前年度应扣未扣支出做专项申报"
                f"（追补期 5 年），或先期初/年初调整后再入账"
            ),
            409,
            {
                "businessDate": occurred_on.isoformat(),
                "businessPeriod": business_period.strftime("%Y-%m"),
                "postingPeriod": posting_period.strftime("%Y-%m"),
            },
        )

    if is_backfill:
        reason = (
            f"业务日期 {occurred_on.isoformat()} 所属期间 {business_period.strftime('%Y-%m')} 已关账，"
            f"按未来适用法补记至 {posting_period.strftime('%Y-%m')}"
        )
    else:
        reason = ""

    return PeriodResolution(
        business_date=occurred_on,
        business_period=business_period,
        posting_period=posting_period,
        is_backfill=is_backfill,
        cross_quarter=cross_quarter,
        cross_year=cross_year,
        reason=reason,
    )
