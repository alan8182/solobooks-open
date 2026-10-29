# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Solo Audit Share——CPA 侧公开访问（免注册，凭 URL token）。

结构性只读保证（落地计划 D5）：
- 本模块只允许触碰 audit_shares / share_annotations 两张表；
- 禁止 import 任何记账写路径（entry_service / bank_service / accounting_core_service …），
  backend/tests/test_audit_share.py 有 import 静态断言守住这条线；
- 快照是生成时冻结的脱敏数据集，这里不读任何业务账表。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditShare, Company, ShareAnnotation
from db.session import session_scope

ALLOWED_ANCHOR_TYPES = ("overall", "entry", "check_item", "report_section")
MAX_BODY_LENGTH = 500
MAX_AUTHOR_LENGTH = 64
DAILY_ANNOTATION_LIMIT = 50

# 简单内存限流（每进程）：token -> 最近请求时间戳。窗口 60s 内最多 60 次。
# 云部署多进程时换 Redis。
_REQUEST_WINDOW_SECONDS = 60.0
_REQUEST_WINDOW_MAX = 60
_recent_requests: dict[str, list[datetime]] = {}


class ShareAccessError(Exception):
    """token 无效 / 已撤销 / 已过期。args[0] 为 reason: not_found|revoked|expired。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_rate_limit(token_hash: str) -> bool:
    now = datetime.now(timezone.utc)
    window = _recent_requests.setdefault(token_hash, [])
    window[:] = [ts for ts in window if (now - ts).total_seconds() < _REQUEST_WINDOW_SECONDS]
    if len(window) >= _REQUEST_WINDOW_MAX:
        return False
    window.append(now)
    return True


def _share_state(share: AuditShare) -> str:
    now = datetime.now(timezone.utc)
    expires = share.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # SQLite naive → UTC
    if share.revoked_at is not None:
        return "revoked"
    if expires is not None and expires < now:
        return "expired"
    return "active"


async def resolve_share(session: AsyncSession, token: str) -> AuditShare:
    from services.audit_share_service import hash_token

    share = (
        await session.execute(select(AuditShare).where(AuditShare.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if share is None:
        raise ShareAccessError("not_found")
    state = _share_state(share)
    if state == "revoked":
        raise ShareAccessError("revoked")
    if state == "expired":
        raise ShareAccessError("expired")
    return share


def _public_meta(share: AuditShare, company_name: str | None) -> dict:
    return {
        "shareId": share.id,
        "month": share.month,
        "anonymized": share.anonymized,
        "companyName": company_name or "",
        "expiresAt": share.expires_at.isoformat() if share.expires_at else None,
        "viewCount": share.view_count,
        "generatedAt": (share.snapshot or {}).get("generatedAt"),
        "disclaimer": (share.snapshot or {}).get("disclaimer") or "",
    }


async def get_share_view(token: str) -> dict[str, Any]:
    """CPA 打开链接：校验 token，返回 {share, snapshot}；顺带记一次访问。"""
    from services.audit_share_service import hash_token

    if not _check_rate_limit(hash_token(token)):
        raise TimeoutError("rate_limited")
    async with session_scope() as session:
        share = await resolve_share(session, token)
        share.view_count = (share.view_count or 0) + 1
        share.last_viewed_at = datetime.now(timezone.utc)
        # 主体名不脱敏（CPA 需知道体检对象）：只读 auth_companies 一行，不触碰账表
        company = await session.get(Company, share.company_id)
        meta = _public_meta(share, getattr(company, "name", None) if company else None)
        snapshot = share.snapshot or {}

    from services.audit_service import log_action

    await log_action(share.company_id, "share_guest", "share_view", "audit_share", share.id,
                     {"month": share.month})
    return {"share": meta, "snapshot": snapshot}


async def list_annotations(token: str) -> list[dict]:
    async with session_scope() as session:
        share = await resolve_share(session, token)
        rows = (
            await session.execute(
                select(ShareAnnotation)
                .where(ShareAnnotation.share_id == share.id)
                .order_by(ShareAnnotation.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "anchorType": row.anchor_type,
                "anchorId": row.anchor_id,
                "body": row.body,
                "authorName": row.author_name,
                "status": row.status,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


async def add_annotation(
    token: str,
    *,
    author_name: str,
    anchor_type: str,
    anchor_id: str | None,
    body: str,
) -> dict:
    """CPA 写批注——协审链接唯一的写操作，且只写 share_annotations。"""
    if anchor_type not in ALLOWED_ANCHOR_TYPES:
        raise ValueError("非法的批注锚点类型")
    body = (body or "").strip()
    if not body:
        raise ValueError("批注内容不能为空")
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"批注最多 {MAX_BODY_LENGTH} 字")
    author_name = (author_name or "").strip()[:MAX_AUTHOR_LENGTH] or "协审会计师"

    async with session_scope() as session:
        share = await resolve_share(session, token)
        # created_at 在 SQLite 里读回是 naive，跨库一致的做法是取回后在 Python 侧比较
        recent = (
            await session.execute(
                select(ShareAnnotation.created_at).where(ShareAnnotation.share_id == share.id)
            )
        ).scalars().all()
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        today_count = sum(
            1
            for created in recent
            if created is not None
            and (created if created.tzinfo else created.replace(tzinfo=timezone.utc)) >= cutoff
        )
        if today_count >= DAILY_ANNOTATION_LIMIT:
            raise PermissionError("今日批注已达上限")

        annotation = ShareAnnotation(
            share_id=share.id,
            company_id=share.company_id,
            anchor_type=anchor_type,
            anchor_id=anchor_id,
            body=body,
            author_name=author_name,
        )
        session.add(annotation)
        await session.flush()
        result = {
            "id": annotation.id,
            "anchorType": annotation.anchor_type,
            "anchorId": annotation.anchor_id,
            "body": annotation.body,
            "authorName": annotation.author_name,
            "status": annotation.status,
            "createdAt": annotation.created_at.isoformat() if annotation.created_at else None,
        }
        company_id, share_id = share.company_id, share.id

    # 留痕在事务提交后（SQLite 文件级锁：事务内开裸连接写 audit_log 会忙等 30s）
    from services.audit_service import log_action

    await log_action(company_id, "share_guest", "share_comment", "audit_share", share_id,
                     {"anchorType": anchor_type, "annotationId": result["id"]})
    return result
