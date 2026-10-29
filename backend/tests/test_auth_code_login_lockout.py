# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""验证码登录/step-up 的防爆破：与密码登录共用失败锁定（按手机号+IP）。

6 位短信验证码只有百万组合，无锁定时可以在线穷举；这里锁定的是
「验证码校验失败」这一事件本身，与短信通道是否可用无关。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_throttle():
    """失败锁定是进程内全局缓存，测试前后各清一次避免用例间与 IP 键污染。"""
    from services import password_service

    password_service._FAILURES.clear()
    yield
    password_service._FAILURES.clear()


def _phone_login(client, phone: str, code: str = "000000"):
    return client.post(
        "/api/auth/phone/login",
        json={"phone": phone, "code": code, "biz_id": "biz-test"},
    )


def test_phone_login_locks_after_max_failures(client):
    """连续失败达到上限后，即使换成正确验证码也返回 429（锁定期内不再校验）。"""
    from services import password_service

    phone = "13988880001"
    for _ in range(password_service.PASSWORD_LOGIN_MAX_FAILURES):
        resp = _phone_login(client, phone)
        # 测试环境无短信配置 → 503；关键是每次都计入失败
        assert resp.status_code in (400, 503), resp.text

    locked = _phone_login(client, phone)
    assert locked.status_code == 429
    assert "锁定" in locked.json()["detail"]

    # 计数清零后恢复（对应窗口过期/进程重启语义）
    password_service._FAILURES.clear()
    assert _phone_login(client, phone).status_code != 429


def test_phone_login_failure_keys_are_phone_and_ip(client):
    """失败同时计入手机号与 IP：换手机号继续打同一 IP 也会被锁。"""
    from services import password_service

    max_failures = password_service.PASSWORD_LOGIN_MAX_FAILURES
    for idx in range(max_failures):
        _phone_login(client, f"1398888010{idx}")

    # 全新手机号，但同一来源 IP 已达上限
    assert _phone_login(client, "13988880199").status_code == 429


def test_dev_fallback_phone_does_not_consume_quota(client):
    """开发兜底身份（13800000000 / 123456）不占失败额度，避免本地联调自锁。"""
    from services import password_service

    for _ in range(password_service.PASSWORD_LOGIN_MAX_FAILURES + 2):
        resp = client.post(
            "/api/auth/phone/login",
            json={"phone": "13800000000", "code": "123456", "biz_id": "dev"},
        )
        assert resp.status_code == 200, resp.text

    assert password_service._FAILURES == {}
