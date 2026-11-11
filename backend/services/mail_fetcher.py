# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""邮件收取引擎：catch-all 邮箱 IMAP 拉取 → 附件入收件箱。

邮箱只是中转站：拉取成功（含重复/白名单外留档/不匹配账套的丢弃）即删信，
长期存储在本地 data/inbox/。处理中途失败的邮件不删，下一轮重试；附件级
幂等由 (company_id, sha256) 唯一约束兜底，重试不会产生重复条目。

标准库 imaplib 是同步阻塞实现，统一放线程池执行（asyncio.to_thread）。
"""

from __future__ import annotations

import asyncio
import email
import logging
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from sqlalchemy import select

from config import (
    MAIL_INBOUND_DOMAIN,
    MAIL_INBOUND_IMAP_HOST,
    MAIL_INBOUND_IMAP_PASSWORD,
    MAIL_INBOUND_IMAP_PORT,
    MAIL_INBOUND_IMAP_USER,
    MAIL_POLL_SECONDS,
)
from db.models import MailboxAddress
from db.session import session_scope
from services import mail_inbox_service
from services.mail_inbox_service import store_attachment

logger = logging.getLogger(__name__)

# 按收件地址定位账套时依次尝试的头：转发链上 To 可能被改写，
# Delivered-To / X-Original-To 更接近真实投递地址
_RECIPIENT_HEADERS = ("Delivered-To", "X-Original-To", "To")


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _extract_address_codes(message: email.message.Message) -> set[str]:
    """取投递到本域名的收件地址本地部分（小写），用于匹配账套地址码。"""
    codes: set[str] = set()
    if not MAIL_INBOUND_DOMAIN:
        return codes
    for header in _RECIPIENT_HEADERS:
        values = message.get_all(header) or []
        for _name, addr in getaddresses([str(v) for v in values]):
            local, _, domain = addr.strip().lower().partition("@")
            if local and domain == MAIL_INBOUND_DOMAIN:
                codes.add(local)
    return codes


async def _match_company_id(codes: set[str]) -> str | None:
    if not codes:
        return None
    async with session_scope() as session:
        row = (
            await session.execute(
                select(MailboxAddress.company_id).where(
                    MailboxAddress.address_code.in_(codes)
                )
            )
        ).first()
    return row[0] if row else None


async def process_message(raw: bytes) -> dict:
    """解析一封邮件：定位账套 → 附件逐个入库。

    返回 {matched, attachments: [{filename, outcome, status}]}。
    不匹配任何账套的邮件返回 matched=False，由调用方直接丢弃。
    """
    message = email.message_from_bytes(raw)
    codes = _extract_address_codes(message)
    company_id = await _match_company_id(codes)
    if company_id is None:
        logger.info("邮件收取：收件地址 %s 未匹配任何账套，丢弃", sorted(codes) or "(无本域收件人)")
        return {"matched": False, "attachments": []}

    received_at = None
    try:
        date_raw = message.get("Date")
        if date_raw:
            parsed = parsedate_to_datetime(date_raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            received_at = parsed
    except Exception:
        received_at = None

    sender = _decode_header_value(message.get("From"))
    subject = _decode_header_value(message.get("Subject"))
    message_id = (message.get("Message-ID") or "")[:512]

    results = []
    for part in message.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_header_value(filename)
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        stored = await store_attachment(
            company_id,
            filename=filename,
            payload=payload,
            email_from=sender,
            email_subject=subject,
            email_message_id=message_id,
            received_at=received_at,
        )
        doc = stored["document"] or {}
        results.append(
            {
                "filename": filename,
                "outcome": stored["outcome"],
                "status": doc.get("status"),
            }
        )
    return {"matched": True, "attachments": results}


def _connect():
    import imaplib

    client = imaplib.IMAP4_SSL(MAIL_INBOUND_IMAP_HOST, MAIL_INBOUND_IMAP_PORT)
    client.login(MAIL_INBOUND_IMAP_USER, MAIL_INBOUND_IMAP_PASSWORD)
    return client


async def fetch_once() -> dict:
    """执行一轮收取：拉全部邮件 → 逐封处理 → 删信。

    IMAP 是同步阻塞库，单条网络操作放线程池；process_message 直接在本
    事件循环上 await（复用全局 async engine 的连接池，不跨 loop）。
    处理抛异常的邮件不删（下一轮重试）；成功（含丢弃）即删。
    返回 {fetched, stored, duplicated, dropped}。
    """
    summary = {"fetched": 0, "stored": 0, "duplicated": 0, "dropped": 0}
    client = await asyncio.to_thread(_connect)
    try:
        typ, _ = await asyncio.to_thread(client.select, "INBOX")
        if typ != "OK":
            return summary
        typ, data = await asyncio.to_thread(client.search, None, "ALL")
        if typ != "OK":
            return summary
        ids = data[0].split()
        for mail_id in ids:
            typ, msg_data = await asyncio.to_thread(client.fetch, mail_id, "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            summary["fetched"] += 1
            try:
                result = await process_message(raw)
            except Exception:
                # 单封失败不阻塞整轮；保留邮件下轮重试
                logger.exception("邮件收取：处理邮件 %s 失败，保留待重试", mail_id)
                continue
            if not result["matched"]:
                summary["dropped"] += 1
            for item in result["attachments"]:
                if item["outcome"] == "duplicate":
                    summary["duplicated"] += 1
                else:
                    summary["stored"] += 1
            await asyncio.to_thread(client.store, mail_id, "+FLAGS", "\\Deleted")
        await asyncio.to_thread(client.expunge)
    finally:
        try:
            await asyncio.to_thread(client.logout)
        except Exception:
            pass
    return summary


async def mail_poll_loop() -> None:
    """后台轮询循环：由 main.py lifespan 启动/取消。"""
    logger.info("邮件收取：轮询启动，间隔 %ss", MAIL_POLL_SECONDS)
    while True:
        try:
            summary = await fetch_once()
            if any(summary.values()):
                logger.info("邮件收取：%s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("邮件收取：本轮拉取失败")
        try:
            # 顺手清理已处理完的过期收件箱条目；失败不影响收取
            purged = await mail_inbox_service.purge_expired()
            if purged:
                logger.info("邮件收取：清理过期收件箱条目 %s 条", purged)
        except Exception:
            logger.exception("邮件收取：过期清理失败")
        await asyncio.sleep(MAIL_POLL_SECONDS)


def start_polling() -> asyncio.Task | None:
    """配置齐全时创建轮询任务，否则返回 None（功能入口提示未开通）。"""
    from config import mail_inbound_enabled

    if not mail_inbound_enabled() or MAIL_POLL_SECONDS <= 0:
        return None
    return asyncio.create_task(mail_poll_loop())
