"""Tests for token_service.py — pure function tests, no DB, no mock."""
import pytest
from services.token_service import create_access_token, decode_access_token


class TestTokenService:
    def test_create_and_decode(self):
        payload = {"sub": "test_user", "company_id": "c1"}
        token = create_access_token(payload, expires_in=3600)

        decoded = decode_access_token(token)
        assert decoded["sub"] == "test_user"
        assert decoded["company_id"] == "c1"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] - decoded["iat"] == 3600

    def test_expired_token_raises(self):
        payload = {"sub": "test_user", "company_id": "c1"}
        # expires_in=0 is falsy, uses JWT_EXPIRES_IN default (604800)
        # use negative to guarantee expiry
        token = create_access_token(payload, expires_in=-3600)
        with pytest.raises(ValueError, match="已过期"):
            decode_access_token(token)

    def test_invalid_signature_raises(self):
        payload = {"sub": "test_user", "company_id": "c1"}
        token = create_access_token(payload, expires_in=3600)
        # tamper with the token body
        parts = token.split(".")
        tampered = f"{parts[0]}.{parts[1]}x.{parts[2]}"
        with pytest.raises(ValueError, match="签名无效"):
            decode_access_token(tampered)

    def test_malformed_token_raises(self):
        with pytest.raises(ValueError, match="格式错误"):
            decode_access_token("invalid-token")

    def test_different_payloads(self):
        token = create_access_token(
            {"sub": "user_a", "company_id": "c2", "role": "admin"},
            expires_in=60,
        )
        decoded = decode_access_token(token)
        assert decoded["sub"] == "user_a"
        assert decoded["company_id"] == "c2"
        assert decoded["role"] == "admin"
