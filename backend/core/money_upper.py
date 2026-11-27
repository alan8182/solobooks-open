# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""金额转人民币大写（凭证合计行/凭证册封面用）。

移植自 web/src/utils/format.ts 的 ``amountToChineseCapital``（前端凭证版式
同款算法），但走 Decimal 两位小数语义而不是 float toFixed——后端聚合金额
一律来自 core/money.py，不允许二进制浮点进大写转换（A1）。
19622.55 → 壹万玖仟陆佰贰拾贰圆伍角伍分；整数支持到万亿级。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from core.money import money

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_SMALL_UNITS = ("", "拾", "佰", "仟")
_BIG_UNITS = ("", "万", "亿", "万亿")


def amount_to_chinese_capital(value: Any) -> str:
    """金额 → 人民币大写。负数带「负」前缀；零 → 零圆整。"""
    amount = money(value)
    negative = amount < 0
    quantity = abs(amount)
    int_part = int(quantity)
    cents = int((quantity - int_part) * 100)
    jiao, fen = divmod(cents, 10)

    int_text = _int_to_capital(int_part)
    if jiao == 0 and fen == 0:
        dec_text = "整"
    else:
        dec_text = ""
        if jiao > 0:
            dec_text += _DIGITS[jiao] + "角"
        elif fen > 0 and int_text:
            dec_text += "零"
        if fen > 0:
            dec_text += _DIGITS[fen] + "分"

    sign = "负" if negative else ""
    return f"{sign}{int_text or '零'}圆{dec_text}"


def _int_to_capital(value: int) -> str:
    """整数部分按 4 位一组，从高位组往低位组拼（与前端同款分组规则）。"""
    if value == 0:
        return ""
    digits: list[str] = []
    rest = value
    while rest > 0:
        digits.append(str(rest % 10000))
        rest //= 10000
    # digits[0] 是最低位组
    text = ""
    zero_pending = False
    for gi in range(len(digits) - 1, -1, -1):
        group = digits[gi].rjust(4, "0") if gi < len(digits) - 1 else str(int(digits[gi]))
        group_text = _group_to_capital(group)
        if group_text:
            if zero_pending and text:
                text += "零"
            text += group_text + _BIG_UNITS[gi]
            zero_pending = False
        else:
            zero_pending = True
    return text


def _group_to_capital(group: str) -> str:
    text = ""
    zero_pending = False
    for i, char in enumerate(group):
        digit = int(char)
        if digit == 0:
            zero_pending = True
            continue
        if zero_pending and text:
            text += "零"
        zero_pending = False
        text += _DIGITS[digit] + _SMALL_UNITS[len(group) - 1 - i]
    return text
