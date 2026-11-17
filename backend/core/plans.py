# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""AI 用量套餐目录。

额度字段为 None 表示「未设限」：services/quota_service.py 的卡口对未设限
套餐直接放行。所有文本推理与多模态视觉识别（发票/单据/截图识别）统一走
services/llm.py 门面并计入套餐 token 消耗。

周期口径：
- monthly：从锚点日（plan_anchor_at，缺省注册时间）起的月周年，每月同日重置；
- annual：同逻辑按 12 个月。

额外说明：
- 配额按用户账号计（跨账套共享），MCP PAT 产生的调用归属 PAT 持有人；
- 多模态视觉模型（chat_vision / chat_vision_json）的输入图片 token 与输出 token 统一计入额度。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanDef:
    code: str
    name: str
    # 周期类型：monthly / annual
    period_type: str
    # 周期内 token 上限；None = 未设限
    token_quota: int | None
    # 年付售价（元，两位小数字符串）；None = 不在售卖目录（trial 体验版 / plus 历史编码）
    price_annual: str | None = None
    # 最多可创建的账套（公司）数；None = 不限（预留给未来代账机构版）
    max_companies: int | None = 3


PLAN_CATALOG: dict[str, PlanDef] = {
    # 免费体验版：每月 10 万 token 体验额度，最多 3 个账套
    "trial": PlanDef(
        code="trial", name="免费体验版", period_type="monthly", token_quota=100_000,
        max_companies=3,
    ),
    # 云端标准版（598元/年）：每年 500 万 token，最多 3 个账套
    "standard": PlanDef(
        code="standard", name="云端标准版", period_type="annual", token_quota=5_000_000,
        price_annual="598.00", max_companies=3,
    ),
    # 人机双签版（2980元/年）：每年 500 万 token，最多 3 个账套
    "pro": PlanDef(
        code="pro", name="人机双签版", period_type="annual", token_quota=5_000_000,
        price_annual="2980.00", max_companies=3,
    ),
    # 兼容历史 plus 编码
    "plus": PlanDef(
        code="plus", name="订阅版", period_type="annual", token_quota=5_000_000,
        max_companies=3,
    ),
}

# 新用户默认套餐
DEFAULT_PLAN_CODE = "trial"


def get_plan(plan_code: str | None) -> PlanDef:
    """按 code 取套餐；未知/为空回退默认套餐（不抛错，保证卡口永不因脏数据崩溃）。"""
    return PLAN_CATALOG.get(plan_code or "") or PLAN_CATALOG[DEFAULT_PLAN_CODE]


def sellable_plans() -> list[PlanDef]:
    """支付宝收款上架的套餐（有年付价格），按价格升序；价格唯一真相在此。"""
    return sorted(
        (plan for plan in PLAN_CATALOG.values() if plan.price_annual is not None),
        key=lambda plan: float(plan.price_annual),
    )


def any_limit_configured() -> bool:
    """目录里是否存在任何已设限的套餐——为 False 时卡口零开销直通。"""
    return any(plan.token_quota is not None for plan in PLAN_CATALOG.values())
