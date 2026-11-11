# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.
"""Regression tests for evidence, coverage and safe health-scan output."""
import pytest
from fastapi.testclient import TestClient

from main import app
from routers.health_scan import _RATE_LIMIT_CACHE
from services.health_scan_service import run_health_scan

client = TestClient(app)
QUIZ = {f"q{i}": "A" for i in range(1, 7)}
PROFILE = {"companyType": "LTD", "period": "2026-10", "accountingStandard": "SMALL_ENTERPRISE", "vatType": "GENERAL"}
META = {"period": "2026-10", "balanceType": "ending", "currencyUnit": "元", "confirmed": True, "complete": True}
ROWS = [
    {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100000.00", "credit_balance": "0"},
    {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000.00"},
]


def checks(report):
    return {c["code"]: c for c in report["checks"]}


def scan_quiz(answers=None, profile=PROFILE):
    return run_health_scan(mode="quiz", answers=QUIZ if answers is None else answers, profile=profile)


def scan_balance(rows=ROWS, answers=None, meta=META, profile=PROFILE):
    return run_health_scan(mode="balance", rows=rows, answers=answers, profile=profile, balanceMeta=meta)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _RATE_LIMIT_CACHE.clear()


def test_q1_reimbursed_and_q4_research_options_follow_real_question():
    report = scan_quiz({**QUIZ, "q1": "B", "q4": "B"})
    assert checks(report)["Q-01"]["status"] == "PASS"
    assert checks(report)["Q-04"]["status"] == "PASS"
    assert not report["flags"]
    report = scan_quiz({**QUIZ, "q4": "A"})
    assert checks(report)["Q-04"]["status"] == "RISK"
    assert checks(report)["Q-04"]["deduction"] == 10
    report = scan_quiz({**QUIZ, "q4": "C"})
    assert checks(report)["Q-04"]["status"] == "NA"
    assert report["coverage"] == {"completed": 5, "applicable": 5, "percent": 100.0}
    assert next(d for d in report["dimensions"] if d["key"] == "governance")["score"] is None


def test_quiz_dimension_with_no_applicable_check_explains_score_unavailability():
    report = scan_quiz({**QUIZ, "q4": "C"})
    assert checks(report)["Q-04"]["status"] == "NA"
    assert report["totalScore"] is None
    assert report["levelLabel"] == "维度无适用检查，暂不评分"
    assert "无适用检查" in report["oneSentenceVerdict"]
    assert "无适用检查" in next(d for d in report["dimensions"] if d["key"] == "governance")["detail"]
    assert any("无适用检查" in reason for reason in report["scoreBreakdown"]["reason"])
    assert "资料不足" in scan_quiz({**QUIZ, "q4": "UNKNOWN"})["levelLabel"]
    assert "资料不足" in scan_quiz({**QUIZ, "q4": "C", "q2": "UNKNOWN"})["levelLabel"]


def test_unknown_answer_not_defaulted_to_safe_and_dimension_unscored():
    report = scan_quiz({**QUIZ, "q2": "UNKNOWN"})
    assert checks(report)["Q-02"]["status"] == "UNKNOWN"
    assert report["coverage"] == {"completed": 5, "applicable": 6, "percent": 83.33}
    assert report["totalScore"] is None
    assert next(d for d in report["dimensions"] if d["key"] == "voucher")["score"] is None
    assert scan_quiz({"q1": "A"})["totalScore"] is None
    assert scan_quiz(QUIZ, profile=None)["totalScore"] is None
    assert checks(scan_quiz({**QUIZ, "q4": "NA"}))["Q-04"]["status"] == "UNKNOWN"


def test_risks_and_breakdown_are_reproducible():
    report = scan_quiz({**QUIZ, "q1": "C", "q2": "B", "q3": "B"})
    breakdown = report["scoreBreakdown"]
    assert breakdown["totalDeduction"] == sum(i["deduction"] for i in breakdown["deductions"])
    assert report["totalScore"] == sum(dim["score"] * breakdown["weights"][dim["key"]] for dim in report["dimensions"])
    for dim in report["dimensions"]:
        if dim["score"] is not None:
            assert dim["score"] == max(0, 100 - sum(i["deduction"] for i in breakdown["deductions"] if i["dimension"] == dim["key"]))


@pytest.mark.parametrize("bad", [None, "", "abc", "1e2", "NaN", "Infinity", "1.001", True, "1,000", "1234567890123456"])
def test_invalid_amount_never_silently_becomes_zero(bad):
    response = client.post("/api/health-scan/analyze", json={"mode": "balance", "rows": [{**ROWS[0], "debit_balance": bad}]})
    assert response.status_code == 422
    assert "abc" not in response.text


def test_decimal_exact_balance_and_parent_child_rejected():
    rows = [{"account_code": "1001", "account_name": "现金", "debit_balance": "0.10", "credit_balance": "0"},
            {"account_code": "1002", "account_name": "银行", "debit_balance": "0.20", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "0.30"}]
    assert checks(scan_balance(rows))["R-04"]["status"] == "PASS"
    rows[-1]["credit_balance"] = "0.29"
    assert checks(scan_balance(rows))["R-04"]["status"] == "RISK"
    with pytest.raises(ValueError):
        scan_balance(rows + [{**rows[0], "account_code": "100101"}])


def test_balance_core_metadata_and_all_zero_gate():
    assert scan_balance(meta=None)["totalScore"] is None
    assert scan_balance(meta={**META, "period": "2026-09"})["totalScore"] is None
    assert scan_balance(meta={**META, "balanceType": "opening"})["totalScore"] is None
    assert scan_balance(meta={**META, "currencyUnit": "万元"})["totalScore"] is None
    assert scan_balance(meta={**META, "confirmed": False})["totalScore"] is None
    zero = [{**r, "debit_balance": "0", "credit_balance": "0"} for r in ROWS]
    assert scan_balance(zero)["totalScore"] is None
    assert checks(scan_balance(zero))["R-04"]["status"] == "UNKNOWN"


def test_missing_balance_accounts_never_claims_unpaid_capital_or_rd():
    result = scan_balance(meta=None)
    assert checks(result)["R-09"]["status"] != "RISK"
    assert checks(result)["R-05"]["status"] == "UNKNOWN"
    assert not any(d["score"] == 100 for d in result["dimensions"])


def test_followup_evidence_needed_and_period_occurrence_not_cross_year():
    rows = [{"account_code": "1221", "account_name": "其他应收款-**", "debit_balance": "60000", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "60000"}]
    result = scan_balance(rows)
    assert checks(result)["R-01"]["status"] == "UNKNOWN"
    assert any(flag["code"] == "R-01" for flag in result["flags"])
    assert "loanOccurrence" in checks(result)["R-01"]["missing"]
    answers = {"loanCounterparty": "SHAREHOLDER", "loanOccurrence": "YES", "loanRepayment": "PARTIAL", "loanPurpose": "BUSINESS"}
    result = scan_balance(rows, answers)
    assert checks(result)["R-01"]["status"] == "RISK"
    assert "loanOccurrence=YES" in checks(result)["R-01"]["observed"]
    assert "跨年" not in str(result) and "税额" not in str(result["flags"])
    assert "impactAmount" not in str(result)


def test_followups_rd_asset_and_advance_use_actual_evidence():
    rows = [
        {"account_code": "1601", "account_name": "固定资产", "debit_balance": "30000", "credit_balance": "0"},
        {"account_code": "1602", "account_name": "累计折旧", "debit_balance": "0", "credit_balance": "0"},
        {"account_code": "2203", "account_name": "预收账款", "debit_balance": "0", "credit_balance": "60000"},
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "30000", "credit_balance": "0"},
    ]
    result = scan_balance(rows)
    assert all(checks(result)[code]["status"] == "UNKNOWN" for code in ("R-05", "R-06", "R-08"))
    result = scan_balance(rows, {"rdActivities": "YES", "rdAuxiliaryLedger": "NO", "assetExists": "YES", "assetPutIntoUse": "YES", "advanceReceived": "YES", "advancePerformance": "PARTIAL"})
    assert all(checks(result)[code]["status"] == "RISK" for code in ("R-05", "R-06", "R-08"))
    result = scan_balance(rows, {"rdActivities": "NO", "assetExists": "YES", "assetPutIntoUse": "NO", "advanceReceived": "YES", "advancePerformance": "NO"})
    assert checks(result)["R-05"]["status"] == "NA"
    assert checks(result)["R-06"]["status"] == "NA"
    assert checks(result)["R-08"]["status"] == "PASS"


def test_capital_check_never_uses_small_enterprise_production_cost():
    cost = {"account_code": "4001", "account_name": "生产成本", "debit_balance": "50000", "credit_balance": "0"}
    missing = scan_balance([ROWS[0], cost])
    assert checks(missing)["R-09"]["status"] == "UNKNOWN"
    assert not any(f["code"] == "R-09" for f in missing["flags"])
    capital = {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000"}
    check = checks(scan_balance([ROWS[0], cost, capital]))["R-09"]
    assert check["status"] == "PASS"
    assert "100000.00" in check["observed"]


@pytest.mark.parametrize("code,name,standard,expected", [
    ("3001", "实收资本", "SMALL_ENTERPRISE", "RISK"),
    ("3001001", "实收资本-股东", "SMALL_ENTERPRISE", "RISK"),
    ("3001", "", "SMALL_ENTERPRISE", "RISK"),
    ("4001", "实收资本", "SMALL_ENTERPRISE", "RISK"),  # 历史混合编码
    ("4001", "股本", "ENTERPRISE", "RISK"),
    ("", "实收资本", "UNKNOWN", "RISK"),
    ("4001", "", "ENTERPRISE", "RISK"),
    ("4001", "", "SMALL_ENTERPRISE", "UNKNOWN"),
    ("4001", "", "UNKNOWN", "UNKNOWN"),
    ("3001", "", "ENTERPRISE", "UNKNOWN"),
    ("3001", "生产成本", "SMALL_ENTERPRISE", "UNKNOWN"),
    ("4001", "生产成本", "ENTERPRISE", "UNKNOWN"),
    ("9999", "应付股本", "SMALL_ENTERPRISE", "UNKNOWN"),
    ("9999", "实收资本溢价", "SMALL_ENTERPRISE", "UNKNOWN"),
])
def test_capital_identification_requires_trustworthy_name_or_standard(code, name, standard, expected):
    row = {"account_code": code, "account_name": name, "debit_balance": "0", "credit_balance": "0"}
    report = scan_balance([ROWS[0], row], profile={**PROFILE, "accountingStandard": standard})
    check = checks(report)["R-09"]
    assert check["status"] == expected
    if expected == "UNKNOWN":
        assert check["deduction"] == 0
        assert not any(f["code"] == "R-09" for f in report["flags"])


def test_capital_ambiguous_row_prevents_false_pass_from_other_capital():
    capital = {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100000"}
    ambiguous = {"account_code": "4001", "account_name": "", "debit_balance": "0", "credit_balance": "0"}
    check = checks(scan_balance([ROWS[0], capital, ambiguous]))["R-09"]
    assert check["status"] == "UNKNOWN"
    assert check["deduction"] == 0


@pytest.mark.parametrize("company_type", ["INDIVIDUAL", "SOLE_PROPRIETOR"])
def test_non_company_capital_check_is_inapplicable(company_type):
    assert checks(scan_balance(profile={**PROFILE, "companyType": company_type}))["R-09"]["status"] == "NA"


@pytest.mark.parametrize("key", ["q1", "q2", "q3", "q4", "q5", "q6"])
def test_unsubstantiated_na_is_unknown(key):
    report = scan_quiz({**QUIZ, key: "NA"})
    assert checks(report)[f"Q-0{key[-1]}"]["status"] == "UNKNOWN"
    assert report["totalScore"] is None


def test_endpoint_accepts_incomplete_without_llm_and_no_raw_exception(monkeypatch):
    response = client.post("/api/health-scan/analyze", json={"mode": "quiz", "answers": {}, "profile": {"companyType": "UNKNOWN"}})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["totalScore"] is None and report["meta"]["ephemeral"]
    assert report["meta"]["analysisSource"] == "deterministic"
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
