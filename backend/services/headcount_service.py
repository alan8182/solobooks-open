# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""从业人数唯一派生入口。

历史上「当前从业人数」是主体 payload 里的人工字段，与工资名册并存，导致
两套口径（小型微利判定用 payload 值、个税提示用名册人数）。本模块统一为：

    raw   = 在职员工名册人数（employees.is_active = 1）
    count = 公司制 max(raw, 1)；个人独资企业/个体工商户 raw（可为 0）

`count` 是税务口径（小型微利判定），公司制下限 1 对应「至少有法定代表人」；
`raw` 保留真实名册人数，供「请先录入员工」这类运营提示使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services import company_entity_service, salary_service


@dataclass(frozen=True)
class Headcount:
    """一次人数快照。"""

    raw: int  # 在职名册实际人数
    count: int  # 税务口径人数（公司制下限 1）
    entity_type: str
    is_corporate: bool

    @property
    def needs_employee_setup(self) -> bool:
        """公司制但名册为空：需要引导录入员工（法定代表人）。"""
        return self.is_corporate and self.raw == 0


async def count_active_employees(company_id: str) -> int:
    """在职员工数（与个税扣缴提示同口径：只算 is_active = 1）。"""
    return len(await salary_service.list_employees(company_id))


async def get_headcount(company_id: str, entity_type: Optional[str] = None) -> Headcount:
    """读取人数快照；主体类型可由调用方传入（避免重复查库）。"""
    raw = await count_active_employees(company_id)
    if entity_type:
        et = company_entity_service.normalize_entity_type(entity_type)
    else:
        et = await company_entity_service.get_company_entity_type(company_id)
    corporate = company_entity_service.pays_corporate_income_tax(et)
    return Headcount(
        raw=raw,
        count=max(raw, 1) if corporate else raw,
        entity_type=et,
        is_corporate=corporate,
    )
