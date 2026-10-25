# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""主体类型（组织形式）归一化与税种适用性判定。

`auth_companies.payload.type` 历史存中文原文（有限责任公司/个人独资企业/
个体工商户），产品里长期只作展示。但税种判定必须按组织形式分支：

- 有限责任公司：企业所得税（CIT）
- 个人独资企业 / 个体工商户：经营所得个人所得税（5%–35%），**不缴企业所得税**

本模块是唯一归一化入口，避免各调用方直接依赖中文字符串。未知/缺省按
`ltd` 处理（存量用户绝大多数为有限公司，保持既有行为，不制造行为突变）。
"""
from __future__ import annotations

from typing import Any, Dict

ENTITY_LTD = "ltd"
ENTITY_SOLE_PROPRIETORSHIP = "sole_proprietorship"
ENTITY_INDIVIDUAL_BUSINESS = "individual_business"

ENTITY_LABELS: Dict[str, str] = {
    ENTITY_LTD: "有限责任公司",
    ENTITY_SOLE_PROPRIETORSHIP: "个人独资企业",
    ENTITY_INDIVIDUAL_BUSINESS: "个体工商户",
}

# 中文原文（前端下拉现有取值）+ 枚举值（新数据建议直接存枚举）
_ALIASES: Dict[str, str] = {
    "有限责任公司": ENTITY_LTD,
    "有限公司": ENTITY_LTD,
    "个人独资企业": ENTITY_SOLE_PROPRIETORSHIP,
    "个人独资": ENTITY_SOLE_PROPRIETORSHIP,
    "个体工商户": ENTITY_INDIVIDUAL_BUSINESS,
    "个体户": ENTITY_INDIVIDUAL_BUSINESS,
    "ltd": ENTITY_LTD,
    "llc": ENTITY_LTD,
    "company": ENTITY_LTD,
    "sole_proprietorship": ENTITY_SOLE_PROPRIETORSHIP,
    "sole": ENTITY_SOLE_PROPRIETORSHIP,
    "individual_business": ENTITY_INDIVIDUAL_BUSINESS,
    "individual": ENTITY_INDIVIDUAL_BUSINESS,
    "self_employed": ENTITY_INDIVIDUAL_BUSINESS,
}


def normalize_entity_type(raw: Any) -> str:
    """任意历史/枚举写法 → 三种标准枚举；未知一律按有限责任公司兜底。"""
    text = str(raw or "").strip()
    if not text:
        return ENTITY_LTD
    if text in _ALIASES:
        return _ALIASES[text]
    return _ALIASES.get(text.lower(), ENTITY_LTD)


def pays_corporate_income_tax(entity_type: str) -> bool:
    """是否缴纳企业所得税（仅有限责任公司）。"""
    return normalize_entity_type(entity_type) == ENTITY_LTD


async def get_company_entity_type(company_id: str) -> str:
    """读取公司主体类型（payload.type），缺失/异常按 ltd 兜底。"""
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Company, company_id)
        payload = row.payload if row is not None else None
    if not isinstance(payload, dict):
        return ENTITY_LTD
    return normalize_entity_type(payload.get("type"))


def income_tax_notice(entity_type: str) -> Dict[str, Any]:
    """所得税适用性说明：API / 前端 / AI 工具共用同一口径。"""
    et = normalize_entity_type(entity_type)
    applicable = et == ENTITY_LTD
    label = ENTITY_LABELS[et]
    if applicable:
        return {
            "entityType": et,
            "label": label,
            "citApplicable": True,
            "incomeTaxKind": "cit",
            "incomeTaxLabel": "企业所得税",
            "notice": "",
        }
    return {
        "entityType": et,
        "label": label,
        "citApplicable": False,
        "incomeTaxKind": "business_income_iit",
        "incomeTaxLabel": "经营所得个人所得税",
        "notice": (
            f"{label}不缴纳企业所得税，按经营所得缴纳个人所得税（5%–35% 五级超额累进）。"
            "经营所得申报作业包（A 表预缴）已支持，汇算清缴（B 表）按查账征收口径准备；"
            "核定征收（定期定额/核定应税所得率）不属于本系统服务范围，"
            "请勿按企业所得税预缴或汇算清缴申报。"
        ),
    }
