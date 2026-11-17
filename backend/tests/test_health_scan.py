# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.
"""Regression tests for 3-minute pain-point health scan, adaptive scoring, and AI polish."""
import json
import pytest
from fastapi.testclient import TestClient

from main import app
from routers.health_scan import _RATE_LIMIT_CACHE
from services.health_scan_service import run_health_scan

client = TestClient(app)
QUIZ = {f"q{i}": "A" for i in range(1, 7)}
ROWS = [
    {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100000.00", "credit_balance": "0"},
    {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000.00"},
]


def checks(report):
    return {c["code"]: c for c in report["checks"]}


def scan_quiz(answers=None, profile=None, use_ai=False):
    return run_health_scan(mode="quiz", answers=QUIZ if answers is None else answers, profile=profile, use_ai=use_ai)


def scan_balance(rows=ROWS, answers=None, meta=None, profile=None, use_ai=False):
    return run_health_scan(mode="balance", rows=rows, answers=answers, profile=profile, balanceMeta=meta, use_ai=use_ai)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _RATE_LIMIT_CACHE.clear()


def test_quiz_scores_without_profile_and_skips_na_dimension():
    # q6=C（无研发投入）标为 NA，但不能导致整份报告暂不评分
    report = scan_quiz({**QUIZ, "q6": "C"})
    assert checks(report)["Q-06"]["status"] == "NA"
    assert report["totalScore"] == 100
    assert report["healthLevel"] == "HEALTHY"
    assert next(d for d in report["dimensions"] if d["key"] == "governance")["status"] == "NA"
    assert report["scoreBreakdown"]["weights"]["governance"] == 0


def test_quiz_requires_at_least_three_meaningful_answers_to_score():
    too_few = scan_quiz({"q1": "A", "q2": "A", "q3": "UNKNOWN", "q4": "UNKNOWN"})
    assert too_few["totalScore"] is None
    assert "有效信息过少" in too_few["levelLabel"]

    enough = scan_quiz({"q1": "C", "q2": "A", "q3": "A"})
    assert enough["totalScore"] is not None
    assert any(f["code"] == "Q-01" and f["severity"] == "CRITICAL" for f in enough["flags"])
    assert enough["taxBureauView"]["riskLevel"] == "high"


def test_quiz_pain_points_drive_tax_and_bank_perspectives():
    report = scan_quiz({**QUIZ, "q1": "C", "q3": "C", "q4": "C"})
    assert report["totalScore"] < 85
    assert report["taxBureauView"]["riskLevel"] == "high"
    assert report["bankFunderView"]["creditLevel"] == "low"
    assert len(report["flags"]) == 3
    assert report["flags"][0]["deduction"] >= report["flags"][1]["deduction"]


@pytest.mark.parametrize("bad", [None, "", "abc", "1e2", "NaN", "Infinity", "1.001", True, "1,000", "1234567890123456"])
def test_invalid_amount_never_silently_becomes_zero(bad):
    response = client.post("/api/health-scan/analyze", json={"mode": "balance", "rows": [{**ROWS[0], "debit_balance": bad}]})
    assert response.status_code == 422
    assert "abc" not in response.text


def test_decimal_exact_balance_and_parent_child_rejected():
    rows = [
        {"account_code": "1001", "account_name": "现金", "debit_balance": "0.10", "credit_balance": "0"},
        {"account_code": "1002", "account_name": "银行", "debit_balance": "0.20", "credit_balance": "0"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "0.30"},
    ]
    assert checks(scan_balance(rows))["R-04"]["status"] == "PASS"
    rows[-1]["credit_balance"] = "0.29"
    unbalanced = scan_balance(rows)
    assert checks(unbalanced)["R-04"]["status"] == "RISK"
    assert unbalanced["totalScore"] is not None
    with pytest.raises(ValueError):
        scan_balance(rows + [{**rows[0], "account_code": "100101"}])


def test_balance_scores_without_confirmation_gate_and_rejects_all_zero():
    # 不再卡死确认勾选框/期间一致，上传非零余额表即可出分
    report = scan_balance(ROWS, meta=None, profile=None)
    assert report["totalScore"] == 100
    assert report["healthLevel"] == "HEALTHY"

    opening_report = scan_balance(ROWS, meta={"balanceType": "opening"})
    assert opening_report["totalScore"] == 100
    assert opening_report["meta"]["isOpeningBalance"] is True
    assert "期初余额" in opening_report["oneSentenceVerdict"]

    zero = [{**r, "debit_balance": "0", "credit_balance": "0"} for r in ROWS]
    zero_report = scan_balance(zero)
    assert zero_report["totalScore"] is None
    assert "余额全零" in zero_report["levelLabel"]


def test_balance_shareholder_loan_computes_20_percent_tax_exposure():
    rows = [
        {"account_code": "1221", "account_name": "其他应收款", "debit_balance": "100000.00", "credit_balance": "0"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000.00"},
    ]
    report = scan_balance(rows, answers={"loanCounterparty": "SHAREHOLDER"})
    r01_flag = next(f for f in report["flags"] if f["code"] == "R-01")
    assert r01_flag["amount"] == "¥100,000.00"
    assert "20%" in r01_flag["estimatedExposure"]
    assert "20,000.00" in r01_flag["estimatedExposure"]

    # 员工小额备用金不触发风险
    small_emp_rows = [
        {"account_code": "1221", "account_name": "备用金", "debit_balance": "8000.00", "credit_balance": "0"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "8000.00"},
    ]
    emp_report = scan_balance(small_emp_rows, answers={"loanCounterparty": "EMPLOYEE", "loanRepayment": "YES"})
    assert checks(emp_report)["R-01"]["status"] == "PASS"


def test_balance_followups_rd_asset_and_advance():
    rows = [
        {"account_code": "1601", "account_name": "固定资产", "debit_balance": "30000", "credit_balance": "0"},
        {"account_code": "1602", "account_name": "累计折旧", "debit_balance": "0", "credit_balance": "0"},
        {"account_code": "2203", "account_name": "预收账款", "debit_balance": "0", "credit_balance": "60000"},
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "30000", "credit_balance": "0"},
    ]
    report = scan_balance(rows, {"rdActivities": "YES", "rdAuxiliaryLedger": "NO", "advancePerformance": "PARTIAL"})
    assert all(checks(report)[code]["status"] == "RISK" for code in ("R-05", "R-06", "R-08"))

    clean = scan_balance(rows, {"rdActivities": "NO", "assetPutIntoUse": "NO", "advancePerformance": "NO"})
    assert checks(clean)["R-05"]["status"] == "NA"
    assert checks(clean)["R-06"]["status"] == "NA"
    assert checks(clean)["R-08"]["status"] == "PASS"


@pytest.mark.parametrize("code,name,standard,expected", [
    ("3001", "实收资本", "SMALL_ENTERPRISE", "RISK"),
    ("4001", "股本", "ENTERPRISE", "RISK"),
    ("3001", "生产成本", "SMALL_ENTERPRISE", "NA"),
    ("4001", "生产成本", "ENTERPRISE", "NA"),
    ("9999", "应付股本", "SMALL_ENTERPRISE", "NA"),
])
def test_capital_identification_remains_conservative(code, name, standard, expected):
    row = {"account_code": code, "account_name": name, "debit_balance": "0", "credit_balance": "0"}
    report = scan_balance([ROWS[0], row], profile={"companyType": "LTD", "accountingStandard": standard})
    assert checks(report)["R-09"]["status"] == expected


@pytest.mark.parametrize("company_type", ["INDIVIDUAL", "SOLE_PROPRIETOR"])
def test_non_company_capital_check_is_inapplicable(company_type):
    assert checks(scan_balance(profile={"companyType": company_type}))["R-09"]["status"] == "NA"


def test_ai_polish_only_receives_anonymized_flags_and_preserves_score_sovereignty(monkeypatch):
    import services.health_scan_service as svc
    import services.llm as llm

    captured = {}

    def fake_chat_json(system_prompt, user_message, fallback, **kwargs):
        captured["payload"] = json.loads(user_message)
        return {
            "oneSentenceVerdict": "AI润色：公转私跨年挂账是当前最大涉税雷区。",
            "taxBureauDetails": "AI润色：税局重点关注其他应收款长期挂账。",
            "bankFunderDetails": "AI润色：建议清理往来后申请授信。",
            "urgentAction": "AI润色：本周内梳理其他应收款明细。",
            "flagNarratives": {
                "R-01": {"painTitle": "AI标题：股东借款个税风险", "desc": "AI解释：跨年未还易被视同分红。", "fix": "AI建议：走正规报销或归还公户。"},
                "FAKE-99": {"painTitle": "不应注入未触发规则"},
            },
        }

    monkeypatch.setattr(svc, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm, "safe_chat_text_json", fake_chat_json)

    rows = [
        {"account_code": "1221", "account_name": "其他应收款", "debit_balance": "100000.00", "credit_balance": "0"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000.00"},
    ]
    baseline = scan_balance(rows, answers={"loanCounterparty": "SHAREHOLDER"}, use_ai=False)
    polished = scan_balance(rows, answers={"loanCounterparty": "SHAREHOLDER"}, use_ai=True)

    # 分数、等级、扣分绝对主权归确定性规则，LLM 不得篡改
    assert polished["totalScore"] == baseline["totalScore"]
    assert polished["healthLevel"] == baseline["healthLevel"]
    assert polished["meta"]["analysisSource"] == "llm"
    assert polished["oneSentenceVerdict"].startswith("AI润色：")
    assert all(f["code"] != "FAKE-99" for f in polished["flags"])
    assert "rows" not in captured["payload"]
    assert "银行存款" not in json.dumps(captured["payload"], ensure_ascii=False)


def test_ai_polish_falls_back_gracefully_on_empty_llm_result(monkeypatch):
    import services.health_scan_service as svc
    import services.llm as llm

    monkeypatch.setattr(svc, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm, "safe_chat_text_json", lambda *args, **kwargs: {})

    report = scan_quiz({**QUIZ, "q1": "C"}, use_ai=True)
    assert report["meta"]["analysisSource"] == "deterministic-fallback"
    assert report["totalScore"] is not None
    assert "20%" in report["flags"][0]["desc"]


def test_endpoint_accepts_use_ai_and_hides_internal_errors(monkeypatch):
    response = client.post("/api/health-scan/analyze", json={"mode": "quiz", "answers": {}, "useAi": False})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["totalScore"] is None and report["meta"]["ephemeral"]

    import routers.health_scan as router
    monkeypatch.setattr(router, "run_health_scan", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret-input")))
    response = client.post("/api/health-scan/analyze", json={"mode": "quiz"})
    assert response.status_code == 500 and "secret-input" not in response.text


def test_api_validation_does_not_coerce_answer_and_preserves_zero():
    response = client.post("/api/health-scan/analyze", json={"mode": "quiz", "answers": {"q1": 5}})
    assert response.status_code == 422
    response = client.post("/api/health-scan/analyze", json={"mode": "quiz", "answers": {"q1": "INVALID"}})
    assert response.status_code == 422
    rows = [{**ROWS[0], "debit_balance": 0}, {**ROWS[1], "credit_balance": 0}]
    assert client.post("/api/health-scan/analyze", json={"mode": "balance", "rows": rows}).status_code == 200
