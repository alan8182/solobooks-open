# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""营业执照识别：把执照照片转成新建主体表单能用的字段。

只做「抄写」，不做推断：模型照图取字，识别不到就留空，由用户在表单里补。
主体类型与注册地的映射放在前端（支持哪些类型、省份表都在那边），这里只回原文，
避免同一份口径在两端各写一遍。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

LICENSE_PROMPT = """你是中国营业执照（含电子营业执照）的结构化信息提取器。

要求：
1. 只抄写图片上真实出现的文字，严禁推测、补全或联想。看不清的字段一律返回空字符串。
2. 统一社会信用代码只保留数字与大写字母，去掉空格与分隔符。
3. 成立日期统一成 YYYY-MM-DD；图上写「2019年5月20日」也要转成「2019-05-20」。
4. 「类型」照抄执照原文，例如「有限责任公司(自然人独资)」「股份有限公司」「个体工商户」。
5. 「住所」照抄执照上的完整地址。
6. 只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块。

输出 JSON 结构：
{
  "name": "公司名称",
  "unifiedSocialCreditCode": "18 位统一社会信用代码",
  "entityType": "类型原文",
  "incorporationDate": "YYYY-MM-DD",
  "address": "住所原文",
  "businessScope": "经营范围原文",
  "legalRepresentative": "法定代表人/经营者姓名",
  "confidence": 0.0
}

confidence 取 0~1，表示整体抄写的把握程度。"""

# GB 32100-2015 统一社会信用代码：31 位字符表（不含 I、O、S、V、Z）与加权因子
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)

_DATE_PATTERNS = (
    re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
)


def is_valid_uscc(code: str) -> bool:
    """校验 18 位统一社会信用代码的校验位（不是所有 18 位都合法）。"""
    value = (code or "").strip().upper()
    if len(value) != 18:
        return False
    total = 0
    for char, weight in zip(value[:17], _USCC_WEIGHTS):
        index = _USCC_CHARS.find(char)
        if index < 0:
            return False
        total += index * weight
    check = 31 - (total % 31)
    if check == 31:
        check = 0
    return _USCC_CHARS[check] == value[17]


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_date(value: Any) -> str:
    """把执照上的各种日期写法归一成 YYYY-MM-DD；解析不了返回空串。"""
    text = _text(value)
    if not text:
        return ""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""
    return ""


def normalize_license_result(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把模型输出收敛成固定结构，并给出可读的核对提示。

    注意：税号校验位不通过只记 warning，不丢字段——老版执照、15 位注册号、
    模型把某一位看错，都可能出现，最终要由用户在表单里核对。
    """
    data = raw if isinstance(raw, dict) else {}
    name = _text(data.get("name"))
    tax_id = re.sub(r"\s+", "", _text(data.get("unifiedSocialCreditCode") or data.get("taxId"))).upper()
    entity_type = _text(data.get("entityType"))
    address = _text(data.get("address"))

    warnings = []
    if not name:
        warnings.append("未识别到公司名称")
    if not tax_id:
        warnings.append("未识别到统一社会信用代码")
    elif not is_valid_uscc(tax_id):
        # 只提示、不改：老版 15 位注册号、20 位纳税人识别号、模型逐位看错都会走到这里，
        # 最终由用户对照执照核对。区分几种典型情况，给出可执行的核对方向。
        if len(tax_id) != 18:
            warnings.append(f"识别到 {len(tax_id)} 位纳税人识别号（非 18 位统一社会信用代码），请核对")
        elif any(char not in _USCC_CHARS for char in tax_id):
            warnings.append("统一社会信用代码含不允许的字符（O/I/S/V/Z 等），常见是数字 0/1/5 被看成字母，请逐位核对")
        else:
            warnings.append("统一社会信用代码校验位不通过，请逐位核对")
    if not entity_type:
        warnings.append("未识别到主体类型")

    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "name": name,
        "taxId": tax_id,
        "taxIdValid": bool(tax_id) and is_valid_uscc(tax_id),
        "entityType": entity_type,
        "incorporationDate": normalize_date(data.get("incorporationDate")),
        "address": address,
        "businessScope": _text(data.get("businessScope")),
        "legalRepresentative": _text(data.get("legalRepresentative")),
        "confidence": max(0.0, min(1.0, confidence)),
        "warnings": warnings,
    }
