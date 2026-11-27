# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票导入预览的分类建议。

电票 XML/PDF 管线 ``build_import_rows`` 不产出分类列，前端预览把空分类一律
兜底「其他支出」，并且以 ``classify=false`` 导入，绕过了后端按品名的确定性
匹配（软件 → 信息技术服务费）。结果对公支付的服务费被记进 5602999
管理费用-其他（2026-10 王翼 ¥300 软件测试服务即此形态）。

锁定的口径：
1. 品名命中的行由后端给出 ``suggestedCategory``，前端据此预填；
2. 仅支出方向给建议（收入/往来另有科目口径）；
3. 品名未命中时不硬猜，返回空建议，仍由用户选择或走 LLM 分类；
4. 行本身带的显式 ``category`` 原样透传，不被建议覆盖。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from services import einvoice_xml_service, invoice_service
from services.invoice_service import (
    import_invoices,
    match_category_by_item,
    parse_invoice_rows,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "einvoice"


def _xml_row(fixture: str, direction: str = "expense") -> dict:
    parsed = einvoice_xml_service.parse_einvoice_xml((FIXTURES / fixture).read_bytes())
    rows = einvoice_xml_service.build_import_rows(parsed, direction=direction)
    assert rows, "夹具应产出至少一行"
    return rows[0]


async def test_person_agent_service_invoice_suggests_it_service_category():
    """自然人代开的「软件测试服务」应建议信息技术服务费，而不是落到管理费用-其他。"""
    row = _xml_row("exempt_person_agent.xml")
    assert "category" not in row

    result = await parse_invoice_rows([row])
    assert result["errors"] == []
    valid = result["valid"][0]
    assert valid["suggestedCategory"] == "信息技术服务费"
    assert "软件测试服务" in valid["suggestedCategoryReason"]


async def test_income_direction_gets_no_expense_suggestion():
    row = _xml_row("enterprise_blue.xml", direction="income")
    valid = (await parse_invoice_rows([row]))["valid"][0]
    assert valid["suggestedCategory"] == ""
    assert valid["suggestedCategoryReason"] == ""


async def test_unmatched_item_name_returns_empty_suggestion():
    """品名没有确定性规则时不硬猜，仍交给用户或 LLM。"""
    row = {
        "invoice_number": "26442000000000000009",
        "invoice_date": "2026-10-31",
        "total_amount": "88.00",
        "seller_name": "某供应商",
        "buyer_name": "芥子星辰（杭州）智能科技有限公司",
        "item_name": "综合服务",
        "direction": "expense",
    }
    valid = (await parse_invoice_rows([row]))["valid"][0]
    assert valid["suggestedCategory"] == ""
    assert valid["suggestedCategoryReason"] == ""


def test_platform_service_item_defers_to_llm_while_explicit_it_matches_rule():
    """宽泛「平台服务/认证」品名不再被硬规则截胡；明确 IT 与通道手续费仍走规则。"""
    assert match_category_by_item("*生产生活服务*微信平台服务", "expense") is None
    assert match_category_by_item("*认证服务*企业资质认证", "expense") is None
    assert match_category_by_item("*信息技术服务*云服务器", "expense") == "信息技术服务费"
    assert match_category_by_item("*软件*软件测试服务", "expense") == "信息技术服务费"
    # 支付通道费是交易手续费，命中银行及金融手续费
    assert (
        match_category_by_item("*生产生活服务*微信支付通道服务费", "expense")
        == "银行及金融手续费"
    )
    # 铁路电子客票与机票款均命中差旅费；市内网约车仍走交通费
    assert match_category_by_item("*交通运输服务*票价", "expense") == "差旅费"
    assert match_category_by_item("*生产生活服务*代订机票款", "expense") == "差旅费"
    assert match_category_by_item("*运输服务*网约车客运服务费", "expense") == "交通费"


async def _import_one(monkeypatch, row: dict) -> tuple[dict, dict]:
    saved: list[dict] = []

    async def fake_get_company_name(company_id, rows):
        return ""

    async def fake_save_entry(entry, **kwargs):
        saved.append(entry)
        return {**entry, "id": "entry-1"}

    async def fake_record_invoices_bulk(company_id, records):
        return 0

    async def fake_ensure_partners(company_id, user_id, rows):
        return [None] * len(rows)

    monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
    monkeypatch.setattr(invoice_service, "save_entry", fake_save_entry)
    # 单测不写台账/往来档案，避免污染测试库并让同批次后续用例被判重复
    monkeypatch.setattr(
        "services.invoice_ledger_service.record_invoices_bulk", fake_record_invoices_bulk
    )
    monkeypatch.setattr(
        "services.auxiliary_service.ensure_partners_for_invoice_rows", fake_ensure_partners
    )
    result = await import_invoices("c1", "u1", [row], classify=True)
    assert result["imported"] == 1, result
    return saved[0], result


def _expense_row(**overrides) -> dict:
    return {
        # 每次唯一：同一进程里前一个用例导入过的号码会被判重
        "invoice_number": f"test-cat-{uuid4().hex}",
        "invoice_date": "2026-06-02",
        "seller_name": "深圳市腾讯计算机系统有限公司",
        "buyer_name": "芥子星辰（杭州）智能科技有限公司",
        "amount": 300,
        "total_amount": 300,
        "direction": "expense",
        "item_name": "*生产生活服务*微信平台服务",
        **overrides,
    }


async def test_import_without_category_uses_item_rule(monkeypatch):
    """没提交分类且命中明确 IT 品名时按品名规则补分类，并记录 categorySource=rule。"""
    entry, _ = await _import_one(
        monkeypatch,
        _expense_row(item_name="*信息技术服务*云服务器"),
    )
    assert entry["category"] == "信息技术服务费"
    assert entry["categorySource"] == "rule"
    assert entry["categoryReason"]
    credit_codes = [line["accountCode"] for line in entry["voucherLines"]["credit"]]
    assert credit_codes == ["2241"]


async def test_import_platform_certification_invoice_uses_llm_with_remark(monkeypatch):
    """宽泛「微信平台服务」+ 认证备注不再被规则截胡，须携带 remark 调 LLM 并按小企业实务归入办公费。"""
    captured: dict[str, str] = {}

    def fake_llm(system_prompt: str, user_prompt: str, **kwargs):
        captured["system"] = system_prompt
        captured["prompt"] = user_prompt
        return {
            "direction": "expense",
            "category": "办公费",
            "reason": "微信平台认证年费按小企业实务计入办公费",
        }

    monkeypatch.setattr(invoice_service, "safe_chat_text_json", fake_llm)
    entry, _ = await _import_one(
        monkeypatch,
        _expense_row(
            item_name="*生产生活服务*微信平台服务",
            remark="微信认证费已付，请勿转账",
        ),
    )
    assert "微信认证费已付，请勿转账" in captured["prompt"]
    assert "办公费" in captured["prompt"]
    assert entry["category"] == "办公费"
    assert entry["categorySource"] == "llm"
    assert entry["categoryReason"] == "微信平台认证年费按小企业实务计入办公费"
    assert any(line["accountCode"] == "5602004" for line in entry["voucherLines"]["debit"])


async def test_import_confirmed_category_is_not_sent_to_llm(monkeypatch):
    """预览里用户确认过的分类必须原样保留，不能被 LLM 改写。"""
    async def fail_classify(*args, **kwargs):
        raise AssertionError("已确认分类不应再调用 LLM 分类")

    monkeypatch.setattr(invoice_service, "classify_invoice", fail_classify)
    entry, _ = await _import_one(monkeypatch, _expense_row(category="办公费"))
    assert entry["category"] == "办公费"
    assert entry["categorySource"] == "manual"


async def test_import_income_without_category_defaults_to_revenue(monkeypatch):
    """销项票缺分类沿用主营业务收入兜底，不逐行触发 LLM。"""
    async def fail_classify(*args, **kwargs):
        raise AssertionError("销项票缺分类不应调用 LLM")

    monkeypatch.setattr(invoice_service, "classify_invoice", fail_classify)
    entry, _ = await _import_one(
        monkeypatch,
        _expense_row(
            direction="income",
            seller_name="芥子星辰（杭州）智能科技有限公司",
            buyer_name="测试客户有限公司",
            item_name="",
        ),
    )
    assert entry["direction"] == "income"
    assert entry["category"] == "主营业务收入"
    assert entry["categorySource"] == "fallback"


async def test_import_unknown_item_calls_llm_and_marks_source(monkeypatch):
    """品名规则未命中才交给 LLM，结果与来源一并留痕。"""
    async def fake_classify(row, *args, **kwargs):
        return {"direction": "expense", "category": "咨询费", "reason": "模型判断"}

    monkeypatch.setattr(invoice_service, "classify_invoice", fake_classify)
    entry, _ = await _import_one(monkeypatch, _expense_row(item_name="综合服务"))
    assert entry["category"] == "咨询费"
    assert entry["categorySource"] == "llm"


async def test_explicit_category_is_not_overridden_by_suggestion():
    """Excel 导入自带分类列时以用户填报为准，建议只作补充信息。"""
    row = {
        "invoice_number": "26442000000000000010",
        "invoice_date": "2026-10-31",
        "total_amount": "120.00",
        "seller_name": "某云服务商",
        "buyer_name": "芥子星辰（杭州）智能科技有限公司",
        "item_name": "*信息技术服务*云服务器",
        "category": "水电通讯费",
        "direction": "expense",
    }
    valid = (await parse_invoice_rows([row]))["valid"][0]
    assert valid["category"] == "水电通讯费"
    # 建议仍带出供前端比对，但前端预填顺序是 r.category || r.suggestedCategory
    assert valid["suggestedCategory"] == "信息技术服务费"


async def test_classify_invoice_with_business_context_bypasses_item_rule_and_calls_llm(monkeypatch):
    """用户填写业务说明来询问 AI 时，即使品名命中了规则也必须交给 LLM 结合说明研判，不能被规则截胡。"""
    captured: dict[str, str] = {}
    question = "这是请的兼职来帮我们体验测试工具的，是应该算到咨询服务还是应该算到信息技术服务？"

    def fake_llm(system_prompt: str, user_prompt: str, **kwargs):
        captured["system"] = system_prompt
        captured["prompt"] = user_prompt
        return {
            "direction": "expense",
            "category": "信息技术服务费",
            "reason": "体验软件测试工具与技术测试外包服务更贴近信息技术服务费",
        }

    monkeypatch.setattr(invoice_service, "safe_chat_text_json", fake_llm)
    row = _expense_row(
        item_name="*信息技术服务*软件测试服务",
        remark="测试服务费",
        business_context=question,
    )
    res = await invoice_service.classify_invoice(row, "芥子星辰（杭州）智能科技有限公司")

    assert question in captured["prompt"]
    assert "测试服务费" in captured["prompt"]
    assert res["category"] == "信息技术服务费"
    assert "体验软件测试工具" in res["reason"]
