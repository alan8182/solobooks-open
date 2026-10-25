# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Solo Audit Share 协审链接：脱敏器、令牌、公开访问与批注回流全链路。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.audit_share_service import (
    hash_token,
    mask_content_numbers,
    mask_name,
    sanitize_value,
)


# ---------------------------------------------------------------------------
# 脱敏器（纯函数）
# ---------------------------------------------------------------------------

def test_mask_name_keeps_first_char():
    assert mask_name("杭州芥子星辰智能科技有限公司") == "杭**"
    assert mask_name("王晓明") == "王**"
    assert mask_name("李") == "李"
    assert mask_name("") == ""


def test_mask_content_numbers():
    text = "联系 13812345678 收款卡 6222020200112233445 证件 330106199001011234"
    masked = mask_content_numbers(text)
    assert "13812345678" not in masked
    assert "6222020200112233445" not in masked
    assert "330106199001011234" not in masked
    assert "138****5678" in masked


def test_sanitize_value_masks_name_keys_and_recurses():
    data = {
        "partnerName": "杭州某科技有限公司",
        "merchant": "美团平台",
        "summary": "打给张三 13812345678",
        "amount": "1200.00",
        "lines": [{"employeeName": "刘强", "note": "报销 6222020200112233445"}],
    }
    out = sanitize_value(data)
    assert out["partnerName"] == "杭**"
    assert out["merchant"] == "美**"
    assert "13812345678" not in out["summary"]
    assert out["amount"] == "1200.00"  # 金额原样保留
    assert out["lines"][0]["employeeName"] == "刘**"
    assert "6222020200112233445" not in out["lines"][0]["note"]


# ---------------------------------------------------------------------------
# 结构性只读：公开模块禁止 import 记账写路径
# ---------------------------------------------------------------------------

FORBIDDEN_PUBLIC_IMPORTS = (
    "entry_service", "bank_service", "accounting_core_service", "todo_service",
    "accrual_service", "account_mapper", "save_entries", "propose_", "post_journal",
)


BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_public_module_imports_no_write_paths():
    """只检查 import 行（docstring 里举例提到的模块名不算违规）。"""
    source = (BACKEND_DIR / "services" / "audit_share_public.py").read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines)
    for forbidden in FORBIDDEN_PUBLIC_IMPORTS:
        assert forbidden not in joined, f"公开模块 import 了写路径：{forbidden}"


# ---------------------------------------------------------------------------
# API 全链路（老板侧 require_auth + CPA 侧免注册）
# ---------------------------------------------------------------------------

def _create_share(client, auth_headers, month="2026-09", **overrides):
    payload = {"month": month, "anonymized": True, "expiresInDays": 30, **overrides}
    resp = client.post("/api/audit-shares", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_share_returns_token_once_and_stores_hash(client, auth_headers):
    data = _create_share(client, auth_headers)
    assert data["token"].startswith("sas_")
    assert data["url"] == f"/share/{data['token']}"
    assert data["month"] == "2026-09"
    assert data["state"] == "active"
    assert data["stats"]["month"] == "2026-09"

    # 再查列表/详情：明文 token 不再出现
    listing = client.get("/api/audit-shares", headers=auth_headers).json()
    assert len(listing) >= 1
    assert all("token" not in row and "url" not in row for row in listing)

    # 库里只存 sha256 哈希，明文 token 不落库（同步读测试 SQLite 文件，避免跨事件循环）
    import sqlite3

    conn = sqlite3.connect(BACKEND_DIR / "data" / ".pytest_accounting.db")
    try:
        rows = conn.execute(
            "SELECT token_hash, snapshot FROM audit_shares WHERE id = ?", (data["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert rows and rows[0][0] == hash_token(data["token"])
    assert data["token"] not in (rows[0][1] or "")


def test_public_view_annotation_and_revocation_flow(client, auth_headers):
    data = _create_share(client, auth_headers)

    # CPA 免注册打开（无 Authorization 头）
    view = client.get(f"/api/share/{data['token']}")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["share"]["month"] == "2026-09"
    assert body["share"]["anonymized"] is True
    assert "checks" in body["snapshot"] and "anomalies" in body["snapshot"]

    # 视图带免责声明（非代理记账边界）
    assert "不构成法定代理记账" in body["share"]["disclaimer"]

    # CPA 写批注
    ann = client.post(
        f"/api/share/{data['token']}/annotations",
        json={"authorName": "注册会计师·陈", "anchorType": "entry",
              "anchorId": "e1", "body": "此笔建议补充发票复印件"},
    )
    assert ann.status_code == 200, ann.text
    annotation_id = ann.json()["id"]
    assert ann.json()["status"] == "open"

    # 总体意见锚点也合法
    overall = client.post(
        f"/api/share/{data['token']}/annotations",
        json={"anchorType": "overall", "body": "账实基本相符，注意招待费限额"},
    )
    assert overall.status_code == 200

    # 老板端看到批注并处理
    detail = client.get(f"/api/audit-shares/{data['id']}", headers=auth_headers).json()
    assert detail["annotationCount"] == 2
    assert detail["annotations"][0]["body"].startswith("此笔")

    handled = client.post(
        f"/api/audit-shares/{data['id']}/annotations/{annotation_id}/status",
        json={"status": "resolved", "resolutionNote": "已补票"},
        headers=auth_headers,
    )
    assert handled.status_code == 200
    assert handled.json()["status"] == "resolved"

    # 撤销后公开访问立即失效
    revoked = client.post(f"/api/audit-shares/{data['id']}/revoke", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "revoked"
    assert client.get(f"/api/share/{data['token']}").status_code == 410
    assert client.post(
        f"/api/share/{data['token']}/annotations", json={"body": "还能写吗"}
    ).status_code == 410


def test_expired_share_returns_410(client, auth_headers):
    data = _create_share(client, auth_headers, expiresInDays=0)
    assert data["expiresAt"] is None  # 永久链接

    # 直接把过期时间改到过去，模拟过期（同步改测试 SQLite 文件）
    import sqlite3

    conn = sqlite3.connect(BACKEND_DIR / "data" / ".pytest_accounting.db")
    try:
        conn.execute(
            "UPDATE audit_shares SET expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), data["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    resp = client.get(f"/api/share/{data['token']}")
    assert resp.status_code == 410


def test_unknown_token_is_404(client):
    assert client.get("/api/share/sas_nosuchtoken").status_code == 404


def test_annotation_daily_limit(client, auth_headers, monkeypatch):
    from services import audit_share_public

    monkeypatch.setattr(audit_share_public, "DAILY_ANNOTATION_LIMIT", 2)
    data = _create_share(client, auth_headers)
    for _i in range(2):
        resp = client.post(
            f"/api/share/{data['token']}/annotations", json={"body": "批注"}
        )
        assert resp.status_code == 200
    third = client.post(
        f"/api/share/{data['token']}/annotations", json={"body": "第三条"}
    )
    assert third.status_code == 429


def test_annotation_validation(client, auth_headers):
    data = _create_share(client, auth_headers)
    assert client.post(
        f"/api/share/{data['token']}/annotations",
        json={"anchorType": "bogus", "body": "x"},
    ).status_code == 400
    assert client.post(
        f"/api/share/{data['token']}/annotations",
        json={"anchorType": "entry", "body": "   "},
    ).status_code == 400


def test_preview_returns_sanitized_samples(client, auth_headers):
    resp = client.post(
        "/api/audit-shares/preview",
        json={"month": "2026-09", "anonymized": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    assert preview["anonymized"] is True
    assert isinstance(preview["sampleAnomalies"], list)
    assert "incomeSummary" in preview
