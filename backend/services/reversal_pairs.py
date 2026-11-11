# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""同期间冲销对识别：发生额聚合口径的共享工具。

余额按净额口径永远正确（冲销对借贷自相抵）；但借贷「发生额」若把冲销对
原样计入，一次「冲销重记」就会让当期发生额成倍虚增（期初余额核对重设场景
实测：10 月发生额 = 3 × 期初凭证）。月度汇总、账账核对与账簿册的发生额
聚合统一剔除「同期间冲销对」：

- 被冲销原凭证（status=reversed 且 reversal_of_id 非空）
- 其冲销凭证（entry_type=reversal，reversal_of_id 指向原凭证，uq_single_reversal 保证一对一）
- 两者 period_start 落在同一个聚合期间桶内 → 两张都不计入该桶发生额

跨期冲销（冲销凭证落在更晚期间）保留原样——那是更正当期的真实动作。
行级账簿（明细账/序时账/日记账）不剔除：每张凭证都必须完整留痕（A1）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def same_period_reversal_exclusions(entries: Iterable[Any]) -> set[str]:
    """返回应从发生额聚合中剔除的凭证 id 集（同期间冲销对的两侧）。

    入参为 JournalEntry ORM 对象（或属性同名的对象），需要可读
    id / period_start / status / reversal_of_id / entry_type。
    只传单个期间桶内的凭证即可：跨桶的冲销对本来就不满足「同期间」条件。
    """
    entry_list = list(entries)
    reversal_by_original: dict[str, Any] = {}
    for entry in entry_list:
        original_id = getattr(entry, "reversal_of_id", None)
        if original_id and getattr(entry, "entry_type", None) == "reversal":
            reversal_by_original[str(original_id)] = entry

    excluded: set[str] = set()
    for entry in entry_list:
        if getattr(entry, "status", None) != "reversed":
            continue
        original_id = str(getattr(entry, "id", "") or "")
        if not original_id:
            continue
        reversal = reversal_by_original.get(original_id)
        if reversal is None:
            continue
        if getattr(reversal, "period_start", None) == getattr(entry, "period_start", None):
            excluded.add(original_id)
            excluded.add(str(getattr(reversal, "id", "") or ""))
    return {entry_id for entry_id in excluded if entry_id}
