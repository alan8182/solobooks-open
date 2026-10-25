# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产分类目录（单一权威来源）。

设计方案：《无形资产模块设计方案.md》第 2 章。
- 前端经 GET /api/intangible/categories 拉取，不得本地硬编码；
- 创建/更新资产时后端按此目录校验分类、年限下限与权证必填项。

口径依据：
- 年限平均法、残值视为零（《小企业会计准则》）；
- 税法摊销年限不低于 10 年；法律规定或合同约定了使用年限的从其约定
  （企业所得税法实施条例）；
- 外购软件凭财税政策可缩短至 24 个月（财税〔2012〕27 号）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import AccountingError

EXPENSE_ACCOUNT_ADMIN = {"code": "5602008", "name": "管理费用-摊销费"}

CATEGORY_CODES = (
    "patent",
    "trademark",
    "copyright",
    "land_use_right",
    "software",
    "franchise",
    "non_patent_technology",
    "other",
)


@dataclass(frozen=True)
class IntangibleCategory:
    code: str
    name: str
    default_life_months: int
    life_basis: str
    remind_expiry: bool
    cert_label: str
    life_min_months: int
    life_max_months: int
    expense_account_code: str = EXPENSE_ACCOUNT_ADMIN["code"]
    expense_account_name: str = EXPENSE_ACCOUNT_ADMIN["name"]


INTANGIBLE_CATEGORIES: tuple[IntangibleCategory, ...] = (
    IntangibleCategory(
        code="patent",
        name="专利权",
        default_life_months=120,
        life_basis="法定保护期与经济寿命孰短（发明20年/实用新型10年/外观设计15年）",
        remind_expiry=True,
        cert_label="专利号",
        life_min_months=12,
        life_max_months=240,
    ),
    IntangibleCategory(
        code="trademark",
        name="商标权",
        default_life_months=120,
        life_basis="注册有效期10年，可续展",
        remind_expiry=True,
        cert_label="商标注册号",
        life_min_months=12,
        life_max_months=120,
    ),
    IntangibleCategory(
        code="copyright",
        name="著作权",
        default_life_months=120,
        life_basis="法人作品保护期（发表后50年），实际按经济寿命",
        remind_expiry=True,
        cert_label="著作权登记号",
        life_min_months=12,
        life_max_months=600,
    ),
    IntangibleCategory(
        code="land_use_right",
        name="土地使用权",
        default_life_months=600,
        life_basis="土地使用权证载年限（40/50/70年），土地与地上建筑物分别核算",
        remind_expiry=True,
        cert_label="土地使用权证号",
        life_min_months=12,
        life_max_months=840,
    ),
    IntangibleCategory(
        code="software",
        name="软件",
        default_life_months=60,
        life_basis="税法不低于10年；外购软件凭财税政策可缩短至24个月",
        remind_expiry=False,
        cert_label="软件著作权登记号（选填）",
        life_min_months=24,
        life_max_months=120,
    ),
    IntangibleCategory(
        code="franchise",
        name="特许权",
        default_life_months=60,
        life_basis="特许经营/授权合同期限",
        remind_expiry=True,
        cert_label="合同编号",
        life_min_months=12,
        life_max_months=240,
    ),
    IntangibleCategory(
        code="non_patent_technology",
        name="非专利技术",
        default_life_months=120,
        life_basis="经济寿命（无法定保护期）；税前扣除口径一般不低于10年",
        remind_expiry=False,
        cert_label="—",
        life_min_months=24,
        life_max_months=240,
    ),
    IntangibleCategory(
        code="other",
        name="其他",
        default_life_months=120,
        life_basis="税法摊销年限不低于10年（法律或合同另有约定的从其约定）",
        remind_expiry=False,
        cert_label="—",
        life_min_months=24,
        life_max_months=600,
    ),
)

_CATEGORY_MAP: dict[str, IntangibleCategory] = {c.code: c for c in INTANGIBLE_CATEGORIES}

ACQUISITION_TYPES = ("purchase", "self_developed", "investor_contribution", "donation")


def get_category(code: str) -> IntangibleCategory:
    category = _CATEGORY_MAP.get(str(code or "").strip())
    if category is None:
        raise AccountingError(
            "INVALID_INTANGIBLE_CATEGORY",
            "无效的无形资产类别",
            422,
            {"allowed": list(CATEGORY_CODES)},
        )
    return category


def validate_life_months(code: str, life_months: int) -> IntangibleCategory:
    category = get_category(code)
    if int(life_months) < category.life_min_months or int(life_months) > category.life_max_months:
        raise AccountingError(
            "INVALID_INTANGIBLE_LIFE",
            f"{category.name}摊销年限须在 {category.life_min_months}-{category.life_max_months} 个月之间",
            422,
            {"category": category.code, "lifeMonths": int(life_months)},
        )
    return category


def category_payload() -> list[dict]:
    return [
        {
            "code": c.code,
            "name": c.name,
            "defaultLifeMonths": c.default_life_months,
            "lifeBasis": c.life_basis,
            "expenseAccount": {
                "code": c.expense_account_code,
                "name": c.expense_account_name,
            },
            "remindExpiry": c.remind_expiry,
            "certLabel": c.cert_label,
            "lifeMinMonths": c.life_min_months,
            "lifeMaxMonths": c.life_max_months,
        }
        for c in INTANGIBLE_CATEGORIES
    ]
