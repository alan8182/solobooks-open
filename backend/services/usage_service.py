# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""用户侧 AI 用量统计（只读）：从 llm_usage 埋点聚合本人（跨账套）的 token 消耗。

口径与 services/llm.py 埋点一致：一行 = 一次逻辑调用；失败调用 token 为 0，
计入调用次数不计入 token；ASR/OCR 无 token 数（记 0）。
月度边界按 UTC 自然月（与管理端 routers/admin.py 的统计口径一致）；
多维度管理统计见 /api/admin/usage/*，本服务仅提供用户本人的汇总视图。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from db.models import LlmUsage
from db.session import session_scope


def _utc_day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def _empty_bucket() -> dict:
    return {"calls": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0}


def _add(bucket: dict, *, prompt: int, completion: int, calls: int = 1) -> None:
    bucket["calls"] += int(calls)
    bucket["promptTokens"] += int(prompt)
    bucket["completionTokens"] += int(completion)
    bucket["totalTokens"] += int(prompt) + int(completion)


async def my_usage_summary(user_id: str, days: int = 30) -> dict:
    """本人用量汇总：本月/上月 tokens、本月分场景、近 N 天日序列（缺日补 0）。

    本月/上月聚合取近两个月窗口的行在应用层按 UTC 月归属——个人量级行数很小，
    且避免使用 date_trunc 等 PG 专有函数（测试环境跑 SQLite）。
    """
    from services.quota_service import quota_state as _quota_state

    today = datetime.now(timezone.utc).date()
    this_month_first = _month_start(today)
    last_month_first = _month_start(this_month_first - timedelta(days=1))
    window_start = _utc_day_start(today - timedelta(days=days - 1))
    window_end = _utc_day_start(today + timedelta(days=1))  # 含今天（左闭右开）

    current: dict = _empty_bucket()
    previous: dict = _empty_bucket()
    by_scene: dict[str, dict] = {}
    daily: dict[str, dict] = {}

    async with session_scope() as session:
        recent_rows = (
            await session.execute(
                select(
                    LlmUsage.created_at,
                    LlmUsage.prompt_tokens,
                    LlmUsage.completion_tokens,
                ).where(
                    LlmUsage.user_id == user_id,
                    LlmUsage.created_at >= _utc_day_start(last_month_first),
                )
            )
        ).all()

        scene_rows = (
            await session.execute(
                select(
                    LlmUsage.scene,
                    func.count().label("calls"),
                    func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt"),
                    func.coalesce(func.sum(LlmUsage.completion_tokens), 0).label("completion"),
                ).where(
                    LlmUsage.user_id == user_id,
                    LlmUsage.created_at >= _utc_day_start(this_month_first),
                )
                .group_by(LlmUsage.scene)
            )
        ).all()

        daily_rows = (
            await session.execute(
                select(
                    func.date(LlmUsage.created_at).label("day"),
                    func.count().label("calls"),
                    func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("total"),
                ).where(
                    LlmUsage.user_id == user_id,
                    LlmUsage.created_at >= window_start,
                    LlmUsage.created_at < window_end,
                )
                .group_by("day")
            )
        ).all()

    for created_at, prompt, completion in recent_rows:
        day = created_at.date() if isinstance(created_at, datetime) else created_at
        bucket = current if day >= this_month_first else previous
        _add(bucket, prompt=prompt, completion=completion)

    for scene, calls, prompt, completion in scene_rows:
        key = str(scene or "other")
        slot = by_scene.setdefault(key, _empty_bucket())
        _add(slot, prompt=prompt, completion=completion, calls=calls)

    for day, calls, total in daily_rows:
        key = str(day)[:10]
        slot = daily.setdefault(key, {"date": key, "calls": 0, "totalTokens": 0})
        slot["calls"] += int(calls)
        slot["totalTokens"] += int(total)

    # 补齐连续日序列，图表不需要再处理断档
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        slot = daily.get(key)
        series.append(
            {
                "date": key,
                "calls": slot["calls"] if slot else 0,
                "totalTokens": slot["totalTokens"] if slot else 0,
            }
        )

    return {
        "month": _month_key(today),
        "currentMonth": {**current, "month": _month_key(today)},
        "previousMonth": {**previous, "month": _month_key(last_month_first)},
        "byScene": sorted(
            (
                {"scene": scene, **bucket}
                for scene, bucket in by_scene.items()
            ),
            key=lambda row: row["totalTokens"],
            reverse=True,
        ),
        "daily": series,
        # 配额状态（core/plans.py 额度未设限时 limitTokens/remainingTokens 为 null；
        # 用户行缺失时为 None）。前端仅在 limitTokens 非空时渲染额度进度。
        "quota": await _quota_state(user_id),
    }
