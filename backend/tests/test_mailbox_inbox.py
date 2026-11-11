# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""邮件单据通道测试：专属地址、附件暂存去重/白名单、邮件→收件箱全链路。

覆盖的根因（回归锚点）：
- 同一附件重复投递只留一条（(company_id, sha256) 幂等）；
- 白名单外（zip/pdf）不进待导入、note 说明引导手动上传；
- 收件地址按本地部分定位账套，未匹配的邮件整封丢弃；
- 落盘路径不使用用户文件名（路径注入防护）。
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from db.models import Company
from db.session import session_scope
from services import mail_inbox_service as svc
from services import mail_fetcher

TEST_DOMAIN = "in.solobooks.cn"


@pytest.fixture
async def company_c1():
    """建 schema + 测试公司 c1（与 auth_headers 兜底身份同上下文），存在则复用。"""
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_scope() as session:
        from sqlalchemy import select

        row = (
            await session.execute(select(Company).where(Company.company_id == "c1"))
        ).scalars().first()
        if row is None:
            session.add(
                Company(
                    company_id="c1",
                    owner_user_id="test_user_001",
                    payload={"name": "邮件通道测试主体"},
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
    return "c1"


@pytest.fixture
def inbox_dir(tmp_path, monkeypatch):
    """附件落盘指向临时目录，避免污染 data/inbox。"""
    monkeypatch.setattr(svc, "INBOX_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def inbound_domain(monkeypatch):
    """同时补丁两个模块内的域名常量（fetcher 匹配收件人、service 组装地址）。"""
    monkeypatch.setattr(mail_fetcher, "MAIL_INBOUND_DOMAIN", TEST_DOMAIN)
    monkeypatch.setattr(svc, "MAIL_INBOUND_DOMAIN", TEST_DOMAIN)
    return TEST_DOMAIN


async def test_ensure_address_idempotent(company_c1, inbox_dir):
    first = await svc.ensure_address("c1")
    second = await svc.ensure_address("c1")
    assert first.address_code == second.address_code
    assert first.company_id == "c1"
    assert len(first.address_code) == 10
    # 地址码字符集：去易混淆字符
    assert all(c in svc._CODE_ALPHABET for c in first.address_code)


async def test_reset_address_changes_code(company_c1, inbox_dir):
    first = await svc.ensure_address("c1")
    reset = await svc.reset_address("c1")
    assert reset.address_code != first.address_code
    again = await svc.ensure_address("c1")
    assert again.address_code == reset.address_code


async def _ensure_company(company_id: str, owner: str = "test_user_001") -> None:
    """测试用公司行（地址表外键要求 auth_companies 存在）。"""
    async with session_scope() as session:
        from sqlalchemy import select

        if (
            await session.execute(
                select(Company).where(Company.company_id == company_id)
            )
        ).scalars().first() is None:
            session.add(
                Company(
                    company_id=company_id,
                    owner_user_id=owner,
                    payload={"name": company_id},
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )


def test_normalize_mobile():
    assert svc.normalize_mobile("17679108061") == "17679108061"
    assert svc.normalize_mobile(" 176-7910-8061 ") == "17679108061"  # 去分隔符
    assert svc.normalize_mobile("12345678901") == ""  # 非手机号格式
    assert svc.normalize_mobile("") == ""
    assert svc.normalize_mobile(None) == ""


async def test_address_code_prefers_mobile_phone(company_c1, inbox_dir, inbound_domain):
    """有手机号的用户：专属地址 = 手机号@域名，可手填。"""
    await _ensure_company("c_phone")
    row = await svc.ensure_address("c_phone", phone="17679108061")
    assert row.address_code == "17679108061"
    assert svc.build_full_address(row.address_code).endswith(f"@{TEST_DOMAIN}")


async def test_same_phone_second_book_gets_suffix(company_c1, inbox_dir):
    """同一手机号多个账套：第 2 个账套起加 -2 后缀，不与第一个冲突。"""
    await _ensure_company("c_ph1")
    await _ensure_company("c_ph2")
    first = await svc.ensure_address("c_ph1", phone="13800000001")
    second = await svc.ensure_address("c_ph2", phone="13800000001")
    assert first.address_code == "13800000001"
    assert second.address_code == "13800000001-2"


async def test_invalid_phone_falls_back_to_random(company_c1, inbox_dir):
    await _ensure_company("c_phbad")
    row = await svc.ensure_address("c_phbad", phone="12345678901")
    assert len(row.address_code) == 10  # 随机码形态


async def test_reset_with_phone_produces_different_address(company_c1, inbox_dir):
    """手机号账套重置：必须换新地址（作废旧地址），得到 手机号-2。"""
    await _ensure_company("c_phreset")
    first = await svc.ensure_address("c_phreset", phone="13900000002")
    reset = await svc.reset_address("c_phreset", phone="13900000002")
    assert reset.address_code == "13900000002-2"
    assert reset.address_code != first.address_code


async def test_store_attachment_dedupes_by_content(company_c1, inbox_dir):
    payload = b"col1,col2\n1,2\n"
    first = await svc.store_attachment("c1", filename="a.csv", payload=payload)
    second = await svc.store_attachment("c1", filename="重命名同内容.csv", payload=payload)
    assert first["outcome"] == "stored"
    assert first["document"]["status"] == "pending"
    assert second["outcome"] == "duplicate"
    items = await svc.list_documents("c1")
    assert len(items) == 1
    assert items[0]["filename"] == "a.csv"

    # 不同账套同内容互不去重
    await svc.store_attachment("other_company", filename="a.csv", payload=payload)
    assert len(await svc.list_documents("other_company")) == 1


async def test_store_attachment_whitelist_and_notes(company_c1, inbox_dir):
    zip_doc = await svc.store_attachment(
        "c1", filename="银行流水.zip", payload=b"PK\x03\x04fake"
    )
    assert zip_doc["document"]["status"] == "unsupported"
    assert "手动上传" in zip_doc["document"]["note"]

    pdf_doc = await svc.store_attachment("c1", filename="回单.pdf", payload=b"%PDF-1.4")
    assert pdf_doc["document"]["status"] == "unsupported"

    xlsx_doc = await svc.store_attachment("c1", filename="销项发票.xlsx", payload=b"PK\x03\x04xlsx")
    assert xlsx_doc["document"]["status"] == "pending"
    assert xlsx_doc["document"]["importKind"] == "invoice"


async def test_store_attachment_rejects_oversize(company_c1, inbox_dir, monkeypatch):
    monkeypatch.setattr(svc, "MAX_ATTACHMENT_BYTES", 8)
    result = await svc.store_attachment("c1", filename="大文件.csv", payload=b"123456789")
    assert result["document"]["status"] == "unsupported"
    assert "上限" in result["document"]["note"]


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("微信支付账单(2026-06).csv", "wechat_bill"),
        ("招商银行对账单.xlsx", "bank"),
        ("2026进项发票明细.xlsx", "invoice"),
        ("随手记导出.csv", "unknown"),
    ],
)
def test_guess_import_kind(filename, expected):
    assert svc.guess_import_kind(filename) == expected


def _build_email(
    to_addr: str,
    filename: str,
    payload: bytes,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = "电子税务局 <noreply@chinatax.gov.cn>"
    message["To"] = to_addr
    message["Subject"] = "税局导出发票清单"
    message["Date"] = "Wed, 05 Nov 2026 10:00:00 +0800"
    for key, value in (extra_headers or {}).items():
        message[key] = value
    message.set_content("正文")
    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename=filename,
    )
    return message.as_bytes()


async def test_process_message_stores_attachment_for_matching_recipient(
    company_c1, inbox_dir, inbound_domain
):
    address = await svc.ensure_address("c1")
    raw = _build_email(f"{address.address_code}@{TEST_DOMAIN}", "进项发票.xlsx", b"PK\x03\x04data")

    result = await mail_fetcher.process_message(raw)

    assert result["matched"] is True
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["outcome"] == "stored"
    items = await svc.list_documents("c1", status="pending")
    mine = [item for item in items if item["filename"] == "进项发票.xlsx"]
    assert len(mine) == 1
    assert mine[0]["importKind"] == "invoice"
    assert mine[0]["emailFrom"].find("chinatax") != -1
    assert mine[0]["emailSubject"] == "税局导出发票清单"


async def test_process_message_uses_delivered_to_header(
    company_c1, inbox_dir, inbound_domain
):
    """转发链上 To 被改写时，Delivered-To 仍能定位账套。"""
    address = await svc.ensure_address("c1")
    raw = _build_email(
        "someone-else@qq.com",
        "流水.xlsx",
        b"PK\x03\x04bank",
        extra_headers={"Delivered-To": f"{address.address_code}@{TEST_DOMAIN}"},
    )

    result = await mail_fetcher.process_message(raw)
    assert result["matched"] is True
    assert result["attachments"][0]["status"] == "pending"


async def test_process_message_drops_unmatched_recipient(company_c1, inbox_dir, inbound_domain):
    await svc.ensure_address("c1")
    baseline = await svc.list_documents("c1")
    raw = _build_email("stranger@qq.com", "发票.xlsx", b"PK\x03\x04x")
    result = await mail_fetcher.process_message(raw)
    assert result["matched"] is False
    # 未匹配收件人不产生新记录（同库可能有其他测试的存量，按增量断言）
    assert await svc.list_documents("c1") == baseline


async def test_router_mailbox_endpoints(client, auth_headers, company_c1, inbox_dir, inbound_domain):
    # 地址：幂等生成
    resp = client.get("/api/mailbox", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["address"].endswith(f"@{TEST_DOMAIN}")
    assert body["enabled"] in (True, False)

    # 附件入库（模拟邮件通道收件）
    csv_bytes = "日期,金额\n2026-06-01,100.00".encode("utf-8")
    stored = await svc.store_attachment(
        "c1", filename="银行流水.csv", payload=csv_bytes
    )
    doc_id = stored["document"]["id"]

    # 列表可见（同库可能有其他测试存量，按包含断言）
    resp = client.get("/api/mailbox/documents", headers=auth_headers)
    items = resp.json()["items"]
    assert doc_id in [item["id"] for item in items]

    # 文件内容可拉回（前端构造 File 走导入向导）
    resp = client.get(f"/api/mailbox/documents/{doc_id}/file", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.content == csv_bytes

    # 导入成功后标记 consumed
    resp = client.post(f"/api/mailbox/documents/{doc_id}/consumed", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "consumed"

    # 删除：记录与文件一并移除
    resp = client.delete(f"/api/mailbox/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200
    remaining = [item["id"] for item in await svc.list_documents("c1")]
    assert doc_id not in remaining
    assert doc_id not in [p.name for p in inbox_dir.iterdir()]

    # 重置地址
    old_code = body["addressCode"]
    resp = client.post("/api/mailbox/address/reset", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["addressCode"] != old_code


async def test_router_rejects_foreign_document(client, auth_headers, company_c1, inbox_dir):
    """跨账套访问他人的收件附件应 404（A3：真相唯一、数据隔离）。"""
    stored = await svc.store_attachment("other_company", filename="别人的.csv", payload=b"x,y")
    doc_id = stored["document"]["id"]
    resp = client.get(f"/api/mailbox/documents/{doc_id}/file", headers=auth_headers)
    assert resp.status_code == 404


class FakeIMAPClient:
    """imaplib 替身：只实现 fetch_once 用到的同步接口。"""

    def __init__(self, messages: list[bytes]):
        self._messages = messages
        self.deleted: list[str] = []

    def select(self, _box):
        return ("OK", [b"1"])

    def search(self, *_args):
        ids = b" ".join(str(i + 1).encode() for i in range(len(self._messages)))
        return ("OK", [ids])

    def fetch(self, mail_id, _spec):
        return ("OK", [(b"(RFC822)", self._messages[int(mail_id) - 1])])

    def store(self, mail_id, *_args):
        self.deleted.append(mail_id)
        return ("OK", None)

    def expunge(self):
        return ("OK", None)

    def logout(self):
        return ("BYE", None)


async def test_fetch_once_processes_and_deletes(company_c1, inbox_dir, inbound_domain, monkeypatch):
    address = await svc.ensure_address("c1")
    matched = _build_email(f"{address.address_code}@{TEST_DOMAIN}", "对账单.csv", b"d1")
    unmatched = _build_email("stranger@qq.com", "别的.csv", b"d2")
    fake = FakeIMAPClient([matched, unmatched])
    monkeypatch.setattr(mail_fetcher, "_connect", lambda: fake)

    summary = await mail_fetcher.fetch_once()

    assert summary == {"fetched": 2, "stored": 1, "duplicated": 0, "dropped": 1}
    # 成功处理（含丢弃）的邮件都要删信，保持邮箱中转站清洁
    assert sorted(fake.deleted) == [b"1", b"2"]


async def test_fetch_once_keeps_failed_message_for_retry(
    company_c1, inbox_dir, inbound_domain, monkeypatch
):
    address = await svc.ensure_address("c1")
    matched = _build_email(f"{address.address_code}@{TEST_DOMAIN}", "对账单.csv", b"d1")
    fake = FakeIMAPClient([matched])
    monkeypatch.setattr(mail_fetcher, "_connect", lambda: fake)

    async def _boom(_raw):
        raise RuntimeError("处理失败")

    monkeypatch.setattr(mail_fetcher, "process_message", _boom)
    summary = await mail_fetcher.fetch_once()

    assert summary["fetched"] == 1 and summary["stored"] == 0
    # 失败的邮件保留在邮箱里，下一轮重试（附件 sha256 幂等兜住重复）
    assert fake.deleted == []


async def test_purge_expired_only_touches_processed(company_c1, inbox_dir):
    """过期清理只动 consumed/dismissed；pending/unsupported 保留防静默丢单。"""
    from datetime import timedelta

    from sqlalchemy import update

    from db.models import InboxDocument
    from db.session import session_scope

    old = svc._utcnow() - timedelta(days=31)

    consumed = await svc.store_attachment("c1", filename="旧-已导入.csv", payload=b"a1")
    pending = await svc.store_attachment("c1", filename="旧-未处理.csv", payload=b"a2")
    unsupported = await svc.store_attachment("c1", filename="旧-压缩包.zip", payload=b"a3")
    await svc.mark_consumed("c1", consumed["document"]["id"])

    async with session_scope() as session:
        await session.execute(
            update(InboxDocument)
            .where(InboxDocument.id.in_(
                [consumed["document"]["id"], pending["document"]["id"], unsupported["document"]["id"]]
            ))
            .values(received_at=old)
        )

    purged = await svc.purge_expired(days=30)

    assert purged == 1
    remaining_ids = {item["id"] for item in await svc.list_documents("c1")}
    assert consumed["document"]["id"] not in remaining_ids
    assert pending["document"]["id"] in remaining_ids
    assert unsupported["document"]["id"] in remaining_ids
    # 被清理条目的落盘文件一并删除，保留条目的文件不受影响
    assert not (inbox_dir / f"{consumed['document']['id']}.csv").exists()
    assert (inbox_dir / f"{pending['document']['id']}.csv").exists()
    assert (inbox_dir / f"{unsupported['document']['id']}.zip").exists()
