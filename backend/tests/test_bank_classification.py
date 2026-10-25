# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行流水 AI 分类兜底：方向归一化与解析重试的单元测试。"""

import pytest

from services.bank_service import (
    _ensure_classification_direction,
    _normalize_statement_direction,
)
from services import llm


def _stmt(amount: float, description: str = "转入-技术服务预付款") -> dict:
    return {"amount": amount, "description": description}


def test_normalizes_chinese_and_english_directions():
    assert _normalize_statement_direction("收入") == "income"
    assert _normalize_statement_direction(" 支出 ") == "expense"
    assert _normalize_statement_direction("OWNER-TRANSFER") == "owner_transfer"
    assert _normalize_statement_direction("进账") == "income"
    assert _normalize_statement_direction("unknown") is None
    assert _normalize_statement_direction(None) is None


def test_missing_direction_keeps_model_category_and_infers_from_sign():
    result = _ensure_classification_direction(_stmt(2400), {
        "category": "其他业务收入",
        "accountCode": "5051",
        "accountName": "其他业务收入",
        "title": "技术服务预付款",
        "confidence": 0.9,
        "reason": "预收服务款",
    })

    assert result["direction"] == "income"
    assert result["category"] == "其他业务收入"
    assert result["title"] == "技术服务预付款"
    assert result["confidence"] == 0.8           # 方向靠推断，置信度封顶
    assert "推断" in result["reason"]


def test_contradicting_direction_still_falls_back():
    """方向与流水相反时分类不可信，维持整条兜底。"""
    result = _ensure_classification_direction(_stmt(0.74), {
        "direction": "expense",
        "category": "办公费",
        "accountCode": "5602004",
        "accountName": "管理费用-办公费",
        "confidence": 0.95,
        "reason": "wrong direction",
    })

    assert result["direction"] == "income"
    assert result["category"] == "其他业务收入"
    assert result["accountCode"] == "5051"
    assert result["confidence"] < 0.95


def test_missing_category_falls_back():
    result = _ensure_classification_direction(_stmt(-200), {
        "direction": "expense",
        "title": "办公室租金",
        "confidence": 0.9,
        "reason": "租金",
    })

    assert result["category"] == "其他支出"
    assert "未给出分类" in result["reason"]


def test_string_confidence_is_coerced():
    result = _ensure_classification_direction(_stmt(-200), {
        "direction": "expense",
        "category": "租金支出",
        "accountCode": "5602013",
        "accountName": "管理费用-租赁费",
        "title": "办公室租金",
        "confidence": "0.9",
    })

    assert result["confidence"] == 0.9
    assert isinstance(result["confidence"], float)


def test_chat_text_json_retries_once_on_parse_failure(monkeypatch):
    calls = []

    def fake_chat(system_prompt, user_message, **kwargs):
        calls.append(user_message)
        return "这不是 JSON" if len(calls) == 1 else '{"category": "其他业务收入"}'

    monkeypatch.setattr(llm, "chat_text", fake_chat)

    result = llm.chat_text_json("system", "原始问题", retry_on_parse=True)

    assert result == {"category": "其他业务收入"}
    assert len(calls) == 2
    assert "只输出一个 JSON" in calls[1]


def test_chat_text_json_without_retry_raises(monkeypatch):
    monkeypatch.setattr(llm, "chat_text", lambda *a, **k: "这不是 JSON")

    with pytest.raises(ValueError):
        llm.chat_text_json("system", "原始问题")
