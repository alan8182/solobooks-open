# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""AI 用量套餐目录（配额体系预留，额度数值待商业决策）。

额度字段为 None 表示「未设限」：services/quota_service.py 的卡口对未设限
套餐直接放行。启用限制时只需把数值填上并部署，机制（周期推算、计数、
卡口、402 通道）已全量就位，无需改任何其他代码。

周期口径：
- monthly：从锚点日（plan_anchor_at，缺省注册时间）起的月周年，每月同日重置；
- annual：同逻辑按 12 个月，免费体验版从注册日起一年。

额外说明：
- 配额按用户账号计（跨账套共享），MCP PAT 产生的调用归属 PAT 持有人；
- ASR/OCR 不经 LLM 门面、token 记 0，天然不消耗 token 额度。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanDef:
    code: str
    name: str
    # 周期类型：monthly（订阅版） / annual（免费体验版）
    period_type: str
    # 周期内 token 上限；None = 未设限（当前状态，不启用限制）
    token_quota: int | None


PLAN_CATALOG: dict[str, PlanDef] = {
    # 免费体验版：年度体验量，数值待定
    "trial": PlanDef(code="trial", name="免费体验版", period_type="annual", token_quota=None),
    # 订阅版：月度用量，数值待定
    "plus": PlanDef(code="plus", name="订阅版", period_type="monthly", token_quota=None),
}

# 新用户默认套餐
DEFAULT_PLAN_CODE = "trial"


def get_plan(plan_code: str | None) -> PlanDef:
    """按 code 取套餐；未知/为空回退默认套餐（不抛错，保证卡口永不因脏数据崩溃）。"""
    return PLAN_CATALOG.get(plan_code or "") or PLAN_CATALOG[DEFAULT_PLAN_CODE]


def any_limit_configured() -> bool:
    """目录里是否存在任何已设限的套餐——为 False 时卡口零开销直通。"""
    return any(plan.token_quota is not None for plan in PLAN_CATALOG.values())
