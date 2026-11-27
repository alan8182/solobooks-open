# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""人民币大写转换（core/money_upper）。

与前端 amountToChineseCapital 同口径，凭证册封面/合计行必须与屏幕版式一致；
锁定：分/角/整、零中间位、万亿分组、负数、浮点字符串进不来精度误差。
"""
from __future__ import annotations

from core.money_upper import amount_to_chinese_capital


def test_basic_amounts():
    assert amount_to_chinese_capital("19622.55") == "壹万玖仟陆佰贰拾贰圆伍角伍分"
    assert amount_to_chinese_capital("54.00") == "伍拾肆圆整"
    assert amount_to_chinese_capital("0.23") == "零圆贰角叁分"
    assert amount_to_chinese_capital("0") == "零圆整"
    assert amount_to_chinese_capital("0.00") == "零圆整"


def test_zero_intermediate_digits():
    # 中间有 0：1002.05 → 壹仟零贰圆零角伍分（无角有分带零）
    assert amount_to_chinese_capital("1002.05") == "壹仟零贰圆零伍分"
    # 组内连续 0：1010 → 壹仟零壹拾圆整
    assert amount_to_chinese_capital("1010") == "壹仟零壹拾圆整"


def test_group_boundaries():
    # 万位分组：12345678.90（有角无分不加「整」，与前端版式同口径）
    assert amount_to_chinese_capital("12345678.90") == "壹仟贰佰叁拾肆万伍仟陆佰柒拾捌圆玖角"
    # 亿级
    assert amount_to_chinese_capital("100000000") == "壹亿圆整"


def test_negative_and_rounding():
    assert amount_to_chinese_capital("-54.00") == "负伍拾肆圆整"
    # ROUND_HALF_UP 与 core.money 一致：0.005 → 0.01
    assert amount_to_chinese_capital("0.005") == "零圆壹分"


def test_float_input_goes_through_decimal():
    # float 直接进来也要走 money() 的两位语义，不泄露二进制误差
    assert amount_to_chinese_capital(19622.55) == "壹万玖仟陆佰贰拾贰圆伍角伍分"
