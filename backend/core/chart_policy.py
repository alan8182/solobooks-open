# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""科目表末级判定唯一口径。

容器化改造后，记账的末级校验出现三份并行实现（seed_accounts / alembic 迁移 /
sync_chart_from_json），口径漂移会让存量租户的 4 位总账被误翻成非末级，
期初余额、期末结转、历史凭证冲销全部 422。此处收敛为单一来源：

- 纯容器科目（1221 其他应收款 / 2211 应付职工薪酬 / 2221 应交税费）：
  记账一律落到子级，父级不能直接作为分录科目。
- 其余 4 位总账（5602 管理费用、1012 其他货币资金等）：历史上被直接记账，
  即使 JSON 声明 isLeaf=false 也必须保持末级，否则存量记账路径被打断。
- 4 位以上的明细科目按 COA JSON 声明。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTAINER_CODES = frozenset({"1221", "2211", "2221"})

# ── 辅助核算挂载口径（P0 WP2）─────────────────────────────────────────────────
# 只做三个维度：往来单位 / 部门 / 项目。
# 职员往来已由 122101/122103 子科目覆盖、存货走后续进销存、现金流维度与
# 本产品"公式法现金流量表"冲突，均不做。
#
# 挂载白名单放在这里而不是散在路由里：与 AGGREGATE_PARENT_CODES 的归集口径
# 同源，避免"能挂的科目"和"能出账的科目"两套口径漂移。
PARTNER_ACCOUNT_PREFIXES = ("1122", "1123", "2202", "2203", "1221", "2241")
DEPARTMENT_PROJECT_ACCOUNT_PREFIXES = ("5001", "5051", "5401", "5402", "5601", "5602", "5603", "4301")

# ── 数量金额式核算（P1 WP-D）─────────────────────────────────────────────────
# 数量标志是 chart_accounts.quantity_enabled（用户可开可关），这里只定义
# seed 与迁移的"默认开启"清单：存货三科目。单位（个/件/箱）因商品而异，
# 不预置，由用户在科目管理里填。
DEFAULT_QUANTITY_ACCOUNT_CODES = frozenset({"1403", "1405", "1408"})


def auxiliary_kinds_for_account(code: str) -> frozenset[str]:
    """该科目允许挂哪些辅助核算维度。空集 = 不挂。"""
    value = str(code or "")
    if not value:
        return frozenset()
    kinds: set[str] = set()
    if any(value.startswith(prefix) for prefix in PARTNER_ACCOUNT_PREFIXES):
        kinds.add("partner")
    if any(value.startswith(prefix) for prefix in DEPARTMENT_PROJECT_ACCOUNT_PREFIXES):
        kinds.update({"department", "project"})
    return frozenset(kinds)


def chart_is_leaf(code: str, info: Mapping[str, Any]) -> bool:
    """判断科目是否可作为分录科目（末级）。

    ``info`` 为 COA JSON 中的科目定义（键为 ``isLeaf``）或已持久化行的属性映射
    （键为 ``is_leaf``），两种形态都接受。
    """
    code = str(code)
    if code in CONTAINER_CODES:
        return False
    if len(code) <= 4:
        # 4 位总账一律保持末级：历史上它们被当作可直接记账的科目使用。
        return True
    return bool(info.get("isLeaf", info.get("is_leaf", True)))
