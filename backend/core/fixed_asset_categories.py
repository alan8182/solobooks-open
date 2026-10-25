# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产分类目录（单一权威来源，同 intangible_categories 模式）。

- 前端经 GET /api/asset/categories 拉取，不得本地硬编码；
- 创建/更新资产时后端按此目录校验分类、年限范围与折旧方法可用性。

口径依据（企业所得税法实施条例第六十条最低折旧年限）：
- 房屋建筑物 20 年；机器设备 10 年；运输工具（飞机/火车/轮船外）4 年；
- 电子设备 3 年；家具器具 5 年。
一次性折旧（财税〔2018〕54 号及延续政策）：单位价值不超过 500 万元的
设备、器具可一次性计入当期成本费用——房屋建筑物不适用，且要求残值为零。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.errors import AccountingError

EXPENSE_ACCOUNT_ADMIN = {"code": "5602007", "name": "管理费用-折旧费"}

# 一次性折旧政策上限：单位价值 ≤ 500 万元
ONE_TIME_DEPRECIATION_LIMIT = Decimal("5000000")

CATEGORY_CODES = ("building", "machine", "vehicle", "electronics", "furniture", "other")

# 语义化类别名 ↔ 编码（存量迁移用：legacy 卡片类别是中文自由文本）
CATEGORY_LABELS: dict[str, str] = {
    "房屋建筑物": "building",
    "机器设备": "machine",
    "运输工具": "vehicle",
    "电子设备": "electronics",
    "家具器具": "furniture",
    "其他": "other",
}


@dataclass(frozen=True)
class FixedAssetCategory:
    code: str
    name: str
    default_life_months: int
    life_basis: str
    life_min_months: int
    life_max_months: int
    one_time_allowed: bool
    expense_account_code: str = EXPENSE_ACCOUNT_ADMIN["code"]
    expense_account_name: str = EXPENSE_ACCOUNT_ADMIN["name"]


FIXED_ASSET_CATEGORIES: tuple[FixedAssetCategory, ...] = (
    FixedAssetCategory(
        code="building",
        name="房屋建筑物",
        default_life_months=240,
        life_basis="税法最低 20 年（房屋、建筑物）",
        life_min_months=240,
        life_max_months=600,
        one_time_allowed=False,
    ),
    FixedAssetCategory(
        code="machine",
        name="机器设备",
        default_life_months=120,
        life_basis="税法最低 10 年（机器、机械、生产设备）",
        life_min_months=12,
        life_max_months=240,
        one_time_allowed=True,
    ),
    FixedAssetCategory(
        code="vehicle",
        name="运输工具",
        default_life_months=48,
        life_basis="税法最低 4 年（飞机、火车、轮船以外的运输工具）",
        life_min_months=12,
        life_max_months=120,
        one_time_allowed=True,
    ),
    FixedAssetCategory(
        code="electronics",
        name="电子设备",
        default_life_months=36,
        life_basis="税法最低 3 年（电子设备）",
        life_min_months=12,
        life_max_months=120,
        one_time_allowed=True,
    ),
    FixedAssetCategory(
        code="furniture",
        name="家具器具",
        default_life_months=60,
        life_basis="税法最低 5 年（家具、器具、工具等）",
        life_min_months=12,
        life_max_months=120,
        one_time_allowed=True,
    ),
    FixedAssetCategory(
        code="other",
        name="其他",
        default_life_months=60,
        life_basis="参照税法同类固定资产最低年限",
        life_min_months=12,
        life_max_months=240,
        one_time_allowed=True,
    ),
)

_CATEGORY_MAP: dict[str, FixedAssetCategory] = {c.code: c for c in FIXED_ASSET_CATEGORIES}

DEPRECIATION_METHODS = ("straight_line", "one_time")


def get_category(code: str) -> FixedAssetCategory:
    category = _CATEGORY_MAP.get(str(code or "").strip())
    if category is None:
        raise AccountingError(
            "INVALID_FIXED_ASSET_CATEGORY",
            "无效的固定资产类别",
            422,
            {"allowed": list(CATEGORY_CODES)},
        )
    return category


def validate_life_months(code: str, life_months: int) -> FixedAssetCategory:
    category = get_category(code)
    if int(life_months) < category.life_min_months or int(life_months) > category.life_max_months:
        raise AccountingError(
            "INVALID_FIXED_ASSET_LIFE",
            f"{category.name}折旧年限须在 {category.life_min_months}-{category.life_max_months} 个月之间",
            422,
            {"category": category.code, "lifeMonths": int(life_months)},
        )
    return category


def validate_depreciation_method(code: str, method: str, original_value: Decimal, residual_value: Decimal) -> FixedAssetCategory:
    """一次性折旧校验：仅设备/器具、≤500 万、残值为零。直线法不设限。"""
    category = get_category(code)
    if method not in DEPRECIATION_METHODS:
        raise AccountingError(
            "INVALID_DEPRECIATION_METHOD",
            f"折旧方法无效，当前支持：{'、'.join(('年限平均法', '一次性折旧'))}",
            422,
        )
    if method == "one_time":
        if not category.one_time_allowed:
            raise AccountingError(
                "ONE_TIME_NOT_ALLOWED",
                f"{category.name}不适用一次性折旧政策（仅设备、器具）",
                422,
            )
        if original_value > ONE_TIME_DEPRECIATION_LIMIT:
            raise AccountingError(
                "ONE_TIME_LIMIT_EXCEEDED",
                f"单位价值超过 {ONE_TIME_DEPRECIATION_LIMIT:,.0f} 元的资产不适用一次性折旧政策",
                422,
            )
        if residual_value != Decimal("0"):
            raise AccountingError(
                "ONE_TIME_RESIDUAL_REQUIRED",
                "一次性折旧要求预计净残值为零（全额计提）",
                422,
            )
    return category


def category_payload() -> list[dict]:
    return [
        {
            "code": c.code,
            "name": c.name,
            "defaultLifeMonths": c.default_life_months,
            "lifeBasis": c.life_basis,
            "lifeMinMonths": c.life_min_months,
            "lifeMaxMonths": c.life_max_months,
            "oneTimeAllowed": c.one_time_allowed,
            "expenseAccount": {
                "code": c.expense_account_code,
                "name": c.expense_account_name,
            },
        }
        for c in FIXED_ASSET_CATEGORIES
    ]
