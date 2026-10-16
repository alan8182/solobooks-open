# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Tests for decoupled monthly report and on-demand AI diagnosis."""
from unittest.mock import patch


def test_monthly_report_defaults_to_no_llm_summary(client, auth_headers):
    # ponytail: verify that default monthly report returns instantly without LLM call
    with patch("routers.report.chat_text_json") as mock_chat:
        resp = client.post(
            "/api/report/monthly",
            json={"month": "2026-06", "periodType": "month"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] is None
        mock_chat.assert_not_called()


def test_monthly_report_generates_summary_when_requested(client, auth_headers):
    with patch("routers.report.chat_text_json", return_value={"summary": "测试经营总结"}) as mock_chat:
        resp = client.post(
            "/api/report/monthly",
            json={"month": "2026-06", "periodType": "month", "includeSummary": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "测试经营总结"
        mock_chat.assert_called_once()


def test_report_diagnosis_endpoint_returns_summary(client, auth_headers):
    with patch("routers.report.chat_text_json", return_value={"summary": "单独触发诊断建议"}) as mock_chat:
        resp = client.post(
            "/api/report/diagnosis",
            json={"period": "2026-06", "periodType": "month"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "单独触发诊断建议"
        mock_chat.assert_called_once()
