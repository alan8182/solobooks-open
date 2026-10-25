# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


CENT = Decimal("0.01")
RATE_QUANTUM = Decimal("0.000001")
ZERO = Decimal("0.00")


class MoneyError(ValueError):
    pass


def money(value: Any, *, allow_negative: bool = True) -> Decimal:
    """Return a canonical CNY amount with two decimal places.

    Floats are converted through ``str`` so their binary representation never
    leaks into accounting calculations. More than two decimals are rounded
    using the project-wide ROUND_HALF_UP policy.
    """
    if value is None or isinstance(value, bool):
        raise MoneyError("金额不能为空")
    try:
        result = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MoneyError("金额格式无效") from exc
    if not result.is_finite():
        raise MoneyError("金额必须是有限数值")
    if not allow_negative and result < ZERO:
        raise MoneyError("金额不能为负数")
    return result


def positive_money(value: Any) -> Decimal:
    result = money(value, allow_negative=False)
    if result <= ZERO:
        raise MoneyError("金额必须大于零")
    return result


def money_text(value: Any) -> str:
    return format(money(value), ".2f")


def rate(value: Any) -> Decimal:
    """Return a canonical tax/exchange rate with six decimal places."""
    if value is None or isinstance(value, bool):
        raise MoneyError("税率或汇率不能为空")
    try:
        result = Decimal(str(value)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MoneyError("税率或汇率格式无效") from exc
    if not result.is_finite():
        raise MoneyError("税率或汇率必须是有限数值")
    return result
