# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""营业执照 OCR：识别结果只做预填，落库仍要用户核对后提交。

锁住四件事：
- 统一社会信用代码按 GB 32100-2015 校验位判断，校验不过只提示、不丢字段
  （老版 15 位注册号、模型看错一位都会出现，最终由用户核对）；
- 模型给的日期各种写法都收敛成 YYYY-MM-DD，解析不了留空；
- 识别失败/空结果的错误码与文案，前端据此走「改手填」而不是卡住新建主体；
- 配额超限保持 402（引导升级），不能被打包成 502「识别失败」。
"""
from __future__ import annotations

import pytest

from core.errors import QuotaExceededError
from services.company_license_service import (
    is_valid_uscc,
    normalize_date,
    normalize_license_result,
)

_AUTH = {"Authorization": "Bearer test-token-unit-test"}

# 一行合法 PNG 头足够：接口只校验大小与 MIME，不解析图像内容
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_uscc_checksum_matches_gb32100():
    # 真实营业执照的信用代码：最后一位是校验位，不能只看长度
    assert is_valid_uscc("91330110MA2KEHHR0T")
    assert is_valid_uscc(" 91330110ma2kehhr0t ")  # 空白与大小写不应影响判断
    assert not is_valid_uscc("91330110MA2KEHHR0X")  # 校验位错
    assert not is_valid_uscc("91330110MA2KEHHR0")  # 17 位
    assert not is_valid_uscc("91330110MA2KEHHRST")  # 含字符表里不存在的 S
    assert not is_valid_uscc("")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2019-05-20", "2019-05-20"),
        ("2019年5月20日", "2019-05-20"),
        ("2019/05/20", "2019-05-20"),
        ("20190520", "2019-05-20"),
        ("2019.5.20", "2019-05-20"),
        ("", ""),
        ("2019年13月40日", ""),  # 非法日期留空，不要瞎猜成 12-31
        ("看不出", ""),
    ],
)
def test_normalize_date_variants(raw, expected):
    assert normalize_date(raw) == expected


def test_normalize_full_license_keeps_all_fields():
    result = normalize_license_result(
        {
            "name": " 杭州芥子星辰科技有限公司 ",
            "unifiedSocialCreditCode": "91330110 ma2kehhr0t",
            "entityType": "有限责任公司(自然人独资)",
            "incorporationDate": "2019年5月20日",
            "address": "浙江省杭州市余杭区仓前街道文一西路 1500 号",
            "businessScope": "技术服务、技术开发",
            "legalRepresentative": "张三",
            "confidence": 0.93,
        }
    )
    assert result["name"] == "杭州芥子星辰科技有限公司"
    assert result["taxId"] == "91330110MA2KEHHR0T"
    assert result["taxIdValid"] is True
    assert result["entityType"] == "有限责任公司(自然人独资)"
    assert result["incorporationDate"] == "2019-05-20"
    assert result["address"].startswith("浙江省杭州市余杭区")
    assert result["confidence"] == pytest.approx(0.93)
    assert result["warnings"] == []


def test_normalize_invalid_checksum_warns_but_keeps_code():
    result = normalize_license_result(
        {
            "name": "测试主体",
            "unifiedSocialCreditCode": "91330110MA2KEHHR0X",
            "entityType": "个体工商户",
        }
    )
    # 校验位不通过不丢税号：用户还要拿它和执照逐位核对
    assert result["taxId"] == "91330110MA2KEHHR0X"
    assert result["taxIdValid"] is False
    assert "校验位不通过，请逐位核对" in "".join(result["warnings"])


def test_normalize_letter_o_warns_about_confusable_digits():
    # 实测视觉模型会把「HHR0T」的 0 读成字母 O；字符表不含 O，提示要指向这条
    result = normalize_license_result(
        {
            "name": "杭州芥子星辰科技有限公司",
            "unifiedSocialCreditCode": "91330110MA2KEHHROT",
            "entityType": "有限责任公司",
        }
    )
    assert result["taxIdValid"] is False
    assert "O/I/S/V/Z" in "".join(result["warnings"])


def test_normalize_non_18_length_warns_length_not_checksum():
    # 老版 15 位注册号不该被说成「校验位不通过」
    result = normalize_license_result(
        {"name": "测试主体", "unifiedSocialCreditCode": "330106000123456", "entityType": "个体工商户"}
    )
    assert result["taxIdValid"] is False
    warnings = "".join(result["warnings"])
    assert "15 位纳税人识别号" in warnings
    assert "校验位" not in warnings


def test_normalize_empty_fields_all_become_warnings():
    result = normalize_license_result({})
    assert result["name"] == ""
    assert result["taxId"] == ""
    assert result["taxIdValid"] is False
    assert result["incorporationDate"] == ""
    assert result["warnings"] == [
        "未识别到公司名称",
        "未识别到统一社会信用代码",
        "未识别到主体类型",
    ]
    # 模型乱给 confidence 时回落到 0，不抛异常
    assert normalize_license_result({"confidence": "很高"})["confidence"] == 0.0
    assert normalize_license_result(None)["name"] == ""


def _stub_llm(monkeypatch, *, result=None, error=None):
    """替换 router 里的 chat_vision_json，记录调用参数。"""
    import routers.auth as auth_router

    calls: dict = {}

    def fake(prompt, image_url, **kwargs):
        calls["scene"] = kwargs.get("scene")
        calls["image_url_prefix"] = image_url[:22]
        calls["prompt"] = prompt
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(auth_router, "chat_vision_json", fake)
    return calls


def _upload(client, filename="license.png", content_type="image/png", data=_PNG):
    return client.post(
        "/api/auth/companies/license-ocr",
        files={"file": (filename, data, content_type)},
        headers=_AUTH,
    )


def test_ocr_endpoint_returns_prefill_payload(client, monkeypatch):
    calls = _stub_llm(
        monkeypatch,
        result={
            "name": "杭州芥子星辰科技有限公司",
            "unifiedSocialCreditCode": "91330110MA2KEHHR0T",
            "entityType": "有限责任公司",
            "incorporationDate": "2019-05-20",
            "address": "浙江省杭州市余杭区",
        },
    )
    resp = _upload(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "杭州芥子星辰科技有限公司"
    assert body["taxId"] == "91330110MA2KEHHR0T"
    assert body["taxIdValid"] is True
    assert calls["scene"] == "company_license_ocr"
    assert calls["image_url_prefix"] == "data:image/png;base64,"


def test_ocr_endpoint_rejects_non_image(client, monkeypatch):
    _stub_llm(monkeypatch, result={"name": "不该被调用"})
    resp = _upload(client, filename="license.txt", content_type="text/plain")
    assert resp.status_code == 415
    assert "不支持的图片类型" in resp.json()["detail"]


def test_ocr_endpoint_502_on_model_failure(client, monkeypatch):
    _stub_llm(monkeypatch, error=RuntimeError("vision api timeout"))
    resp = _upload(client)
    assert resp.status_code == 502
    # 失败文案要指回手动填写，而不是让用户反复上传
    assert "手动填写" in resp.json()["detail"]


def test_ocr_endpoint_422_when_nothing_recognized(client, monkeypatch):
    _stub_llm(monkeypatch, result={"name": "", "unifiedSocialCreditCode": ""})
    resp = _upload(client)
    assert resp.status_code == 422
    assert "手动填写" in resp.json()["detail"]


def test_ocr_endpoint_keeps_quota_error_as_402(client, monkeypatch):
    _stub_llm(monkeypatch, error=QuotaExceededError())
    resp = _upload(client)
    assert resp.status_code == 402
    assert "识别失败" not in resp.text
