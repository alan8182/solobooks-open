# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""会计科目分类的单一事实来源。

期末结转、报表口径、资产负债表齐备性检查三处都必须用同一套判断，
否则会出现"利润表算了、结转没结"这类账表不一致（研发支出 4301 就是历史案例）。
"""
from __future__ import annotations

from typing import Any

# 研发支出-资本化支出：尚未转入无形资产，不进当期损益，也不参与损益结转。
RD_CAPITALIZED_CODE = "4301002"
# 研发支出-费用化支出段：性质属于损益（管理费用），必须参与结转。
RD_EXPENSE_PREFIX = "4301001"
RD_ROOT_CODE = "4301"

# 资产负债表里"开发支出"取研发支出资本化部分的借方余额。
BALANCE_SHEET_DEV_EXPENSE_CODE = RD_CAPITALIZED_CODE


def is_profit_loss_code(code: Any) -> bool:
    """判断科目是否属于损益类，参与期末结转本年利润。

    - 5xxx 全段：收入、成本、费用、税金及附加、营业外收支、所得税费用
    - 4301 及其下的费用化明细（4301001*）：研发费用按管理费用列报
    - 4301002 资本化支出：资产性质，排除
    """
    text = str(code or "").strip()
    if not text:
        return False
    if text.startswith("5"):
        return True
    if text == RD_CAPITALIZED_CODE or text.startswith("4301002"):
        return False
    if text == RD_ROOT_CODE or text.startswith(RD_EXPENSE_PREFIX):
        return True
    return False


def is_rd_capitalized_code(code: Any) -> bool:
    text = str(code or "").strip()
    return text == RD_CAPITALIZED_CODE or text.startswith("4301002")


def is_research_code(code: Any) -> bool:
    return str(code or "").strip().startswith(RD_ROOT_CODE)
