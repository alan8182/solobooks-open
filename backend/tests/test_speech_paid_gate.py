# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Regression test: 语音识别接口仅对有效付费会员开放（trial / 已过期套餐返回 403）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from deps.auth import AuthContext, require_auth
from main import app


def _override_ctx(user_id: str = "u-speech-1"):
    return lambda: AuthContext(user_id=user_id, company_id="c-speech-1")


def test_record_speech_blocks_trial_user_with_403():
    app.dependency_overrides[require_auth] = _override_ctx()
    try:
        client = TestClient(app)
        with patch("routers.record.is_paid_plan_active", new=AsyncMock(return_value=False)):
            resp = client.post(
                "/api/record/speech",
                files={"audio": ("speech.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")},
            )
        assert resp.status_code == 403
        assert "付费会员专属" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_record_speech_allows_paid_member():
    app.dependency_overrides[require_auth] = _override_ctx()
    try:
        client = TestClient(app)
        with (
            patch("routers.record.is_paid_plan_active", new=AsyncMock(return_value=True)),
            patch("routers.record.transcribe_uploaded_audio", return_value="本月收入一万元"),
        ):
            resp = client.post(
                "/api/record/speech",
                files={"audio": ("speech.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.json()["text"] == "本月收入一万元"
    finally:
        app.dependency_overrides.pop(require_auth, None)
