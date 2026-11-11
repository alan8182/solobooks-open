# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""邮件单据通道：账套专属收件地址 + 收件箱附件暂存。

安全与产品边界：
- 地址不是凭证：随机码只为不可枚举，投进来的附件只暂存，入账仍走登录用户的
  确认流程（解析复用前端 SheetJS 管线，本服务不做任何账务解析）。
- 附件级幂等：同一账套下按内容 sha256 去重，重复投递只留一条。
- 落盘文件名一律用内部 id，不把用户文件名拼进路径。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import MAIL_INBOUND_DOMAIN, MAIL_INBOUND_MAX_ATTACHMENT_MB
from db.models import AuthUser, InboxDocument, MailboxAddress
from db.session import session_scope

INBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "inbox"
MAX_ATTACHMENT_BYTES = MAIL_INBOUND_MAX_ATTACHMENT_MB * 1024 * 1024

# 与现有导入管线（前端 SheetJS）一致的附件白名单；加密压缩包等一律留档不导入
SUPPORTED_EXTS = {"xlsx", "xls", "csv"}

# 地址码字符集：小写字母数字、去易混淆字符（0/o/1/i/l），10 位 ≈ 47 bit 熵
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_CODE_LENGTH = 10
# 手机号地址码：^1[3-9]\d{9}$（中国大陆手机号），好记、用户可直接手填
_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_address_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def normalize_mobile(raw: str | None) -> str:
    """归一化手机号：去非数字后须整体命中大陆手机号格式；否则返回空串。"""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    return digits if _MOBILE_RE.match(digits) else ""


async def get_user_mobile(user_id: str) -> str:
    """取用户已验证的手机号（未绑定则空串，地址回落随机码）。"""
    async with session_scope() as session:
        row = await session.get(AuthUser, user_id)
    return row.phone if row is not None else ""


async def _generate_free_code(session, base: str, *, also_taken: set[str] | None = None) -> str:
    """取一个未被占用的地址码；base 冲突时依次尝试 base-2、base-3…

    also_taken：额外视为已占用的码。重置地址时传本行旧码——重置必须产出
    与旧地址不同的新地址，即使全库没有其他冲突（否则重置会原地返回旧码）。
    """
    taken = set((await session.execute(select(MailboxAddress.address_code))).scalars())
    if also_taken:
        taken |= also_taken
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def guess_import_kind(filename: str) -> str:
    """按文件名启发式猜单据类型，仅作前端导入入口的建议路由。

    与手动上传同一套判断口径由前端导入组件兜底，猜错时用户在向导里改类型
    即可，因此这里只求「常见命名直接命中」，不做模糊兜底。
    """
    name = filename.lower()
    if "微信" in filename or "wechat" in name:
        return "wechat_bill"
    if any(k in filename for k in ("流水", "对账单", "银行")) or any(
        k in name for k in ("bank", "statement")
    ):
        return "bank"
    if any(k in filename for k in ("发票", "进项", "销项", "开票")) or "invoice" in name:
        return "invoice"
    if name.endswith(".csv") and "账单" in filename:
        return "wechat_bill"
    return "unknown"


def _normalize_ext(filename: str) -> str:
    match = re.search(r"\.([A-Za-z0-9]+)$", filename.strip())
    return match.group(1).lower() if match else ""


def build_full_address(address_code: str) -> str:
    return f"{address_code}@{MAIL_INBOUND_DOMAIN}" if MAIL_INBOUND_DOMAIN else address_code


async def ensure_address(company_id: str, phone: str = "") -> MailboxAddress:
    """取账套专属地址行，无则生成（幂等）。

    地址码优先用用户手机号（如 13800000000@solobooks.cn），好记、可直接手填；
    未绑定手机号或格式非法时回落 10 位随机码。同手机号第 2 个账套起加 -2 后缀。
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                select(MailboxAddress).where(MailboxAddress.company_id == company_id)
            )
        ).scalars().first()
        if row is not None:
            return row
        base = normalize_mobile(phone) or generate_address_code()
        row = MailboxAddress(
            company_id=company_id,
            address_code=await _generate_free_code(session, base),
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # 并发初始化：另一请求已插入，取回既有行
            await session.rollback()
            row = (
                await session.execute(
                    select(MailboxAddress).where(MailboxAddress.company_id == company_id)
                )
            ).scalars().first()
            if row is None:
                raise
        return row


async def reset_address(company_id: str, phone: str = "") -> MailboxAddress:
    """重置专属地址：原地换码，旧地址随之失配自动失效。

    必须产出与旧地址不同的新地址（重置的意义就是作废旧地址），因此同基码的
    冲突检查排除自身旧码——手机号账套重置后会得到 手机号-2、手机号-3…
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                select(MailboxAddress).where(MailboxAddress.company_id == company_id)
            )
        ).scalars().first()
        base = normalize_mobile(phone) or generate_address_code()
        if row is None:
            row = MailboxAddress(
                company_id=company_id,
                address_code=await _generate_free_code(session, base),
            )
            session.add(row)
        else:
            row.address_code = await _generate_free_code(session, base, also_taken={row.address_code})
            row.updated_at = _utcnow()
        await session.flush()
        return row


def address_payload(row: MailboxAddress) -> dict:
    return {
        "addressCode": row.address_code,
        "domain": MAIL_INBOUND_DOMAIN,
        "address": build_full_address(row.address_code),
    }


async def store_attachment(
    company_id: str,
    *,
    filename: str,
    payload: bytes,
    email_from: str = "",
    email_subject: str = "",
    email_message_id: str = "",
    received_at: datetime | None = None,
) -> dict:
    """附件入库：去重 → 判定状态 → 落盘。返回 {outcome, document}。

    outcome: stored（新入库，含白名单外的留档）/ duplicate（内容重复，跳过）。
    """
    safe_name = (filename or "attachment").strip()[:255] or "attachment"
    ext = _normalize_ext(safe_name)
    digest = hashlib.sha256(payload).hexdigest()
    received = received_at or _utcnow()

    async with session_scope() as session:
        exists = (
            await session.execute(
                select(InboxDocument.id).where(
                    InboxDocument.company_id == company_id,
                    InboxDocument.content_sha256 == digest,
                )
            )
        ).first()
        if exists is not None:
            return {"outcome": "duplicate", "document": None}

        size = len(payload)
        if ext in SUPPORTED_EXTS and size <= MAX_ATTACHMENT_BYTES:
            status, note, kind = "pending", "", guess_import_kind(safe_name)
        else:
            status = "unsupported"
            kind = "unknown"
            if ext in ("zip", "7z", "rar"):
                note = "压缩包暂不支持自动导入，请下载后手动上传"
            elif size > MAX_ATTACHMENT_BYTES:
                note = f"附件超过 {MAIL_INBOUND_MAX_ATTACHMENT_MB}MB 上限，仅留档可下载"
            else:
                note = f"暂不支持 .{ext or '未知'} 类型，仅留档可下载"

        row = InboxDocument(
            company_id=company_id,
            filename=safe_name,
            file_ext=ext,
            size_bytes=size,
            content_sha256=digest,
            stored_name="placeholder",  # 需先有 id 才能定内部文件名
            import_kind=kind,
            status=status,
            note=note,
            email_from=(email_from or "")[:255],
            email_subject=(email_subject or "")[:512],
            email_message_id=(email_message_id or "")[:512],
            received_at=received,
        )
        session.add(row)
        await session.flush()
        row.stored_name = f"{row.id}.{ext or 'bin'}"

    # 落盘放在事务外：文件写失败不该回滚入库记录（记录里 note 可补错误），
    # 但要避免「有记录无文件」长期存在——写失败直接抛错让拉取层重试整封邮件。
    path = INBOX_DIR / row.stored_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"outcome": "stored", "document": serialize_document(row)}


def serialize_document(row: InboxDocument) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "fileExt": row.file_ext,
        "sizeBytes": row.size_bytes,
        "importKind": row.import_kind,
        "status": row.status,
        "note": row.note,
        "source": row.source,
        "emailFrom": row.email_from,
        "emailSubject": row.email_subject,
        "receivedAt": row.received_at.isoformat() if row.received_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
    }


async def list_documents(company_id: str, *, status: str | None = None) -> list[dict]:
    async with session_scope() as session:
        stmt = select(InboxDocument).where(InboxDocument.company_id == company_id)
        if status:
            stmt = stmt.where(InboxDocument.status == status)
        rows = (
            await session.execute(stmt.order_by(InboxDocument.created_at.desc()))
        ).scalars().all()
        return [serialize_document(row) for row in rows]


async def get_document_file(company_id: str, document_id: str) -> dict | None:
    """读附件内容（校验归属），返回 {filename, ext, data}。"""
    async with session_scope() as session:
        row = await session.get(InboxDocument, document_id)
        if row is None or row.company_id != company_id:
            return None
    path = INBOX_DIR / row.stored_name
    if not path.exists():
        return None
    return {"filename": row.filename, "ext": row.file_ext, "data": path.read_bytes()}


async def mark_consumed(company_id: str, document_id: str) -> dict | None:
    """导入成功后由前端回调标记，收件箱里转为已导入。"""
    async with session_scope() as session:
        row = await session.get(InboxDocument, document_id)
        if row is None or row.company_id != company_id:
            return None
        row.status = "consumed"
        row.consumed_at = _utcnow()
        return serialize_document(row)


async def delete_document(company_id: str, document_id: str) -> bool:
    """删除收件箱条目（记录 + 落盘文件）。审计留痕由路由层负责。"""
    async with session_scope() as session:
        row = await session.get(InboxDocument, document_id)
        if row is None or row.company_id != company_id:
            return False
        stored_name = row.stored_name
        await session.delete(row)
    path = INBOX_DIR / stored_name
    if path.exists():
        path.unlink()
    return True


async def purge_expired(days: int = 30) -> int:
    """清理用户已处理完的收件箱条目（consumed/dismissed 超期），返回清理条数。

    只清「用户确认导入过或主动忽略」的记录；pending（还没导入）与 unsupported
    （用户可能还要下载手动处理）一律保留，避免静默弄丢用户没见过的单据。
    由 mail_poll_loop 每轮调用——邮箱未配置时轮询不跑，存量也不再增长，可接受。
    """
    cutoff = _utcnow() - timedelta(days=days)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(InboxDocument).where(
                    InboxDocument.status.in_(("consumed", "dismissed")),
                    InboxDocument.received_at < cutoff,
                )
            )
        ).scalars().all()
        stale = [(row.id, row.stored_name) for row in rows]
        for row_id, _name in stale:
            row = await session.get(InboxDocument, row_id)
            if row is not None:
                await session.delete(row)
    for _doc_id, stored_name in stale:
        path = INBOX_DIR / stored_name
        if path.exists():
            path.unlink()
    return len(stale)
