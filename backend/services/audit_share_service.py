# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Solo Audit Share 协审链接——老板侧服务：快照构建、脱敏、令牌签发与管理。

职责划分（落地计划 D5 的结构性只读保证）：
- 本模块是**老板侧**：可以读账、可以 import 记账服务，只被 require_auth 路由调用；
- CPA 侧公开访问一律走 ``services/audit_share_public.py``——那里只允许触碰
  audit_shares / share_annotations 两张表，禁止 import 任何记账写路径。
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import ZERO, money, money_text
from db.models import AuditShare, JournalEntry, JournalLine, ShareAnnotation
from db.session import session_scope

# ---------------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------------

EXPIRY_CHOICES = (7, 30, 90, 0)  # 0 = 永久（不设 expires_at）


def generate_share_token() -> str:
    """明文 token 只出现在 URL 与签发响应各一次。"""
    return "sas_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读回的 naive 时间按 UTC 补时区（对齐 accounting_core_service 的既定做法）。"""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def require_entitlement(company_id: str, feature: str = "audit_share") -> bool:
    """云订阅阶段的付费门槛挂点。v1 全量开放，Solo Pro 上线后接 license 判定。"""
    return True


# ---------------------------------------------------------------------------
# 脱敏器（纯函数）：身份脱敏、金额保留
# ---------------------------------------------------------------------------

# 按字段名整体打码：往来单位 / 人名（保留首字）
_NAME_KEYS = {
    "partnername", "partner", "merchant", "counterparty", "employeename",
    "ownername", "personname", "contactname", "username", "nickname",
}

# 自由文本中的敏感号码：手机号 / 银行卡(16~19位) / 身份证(18位)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANKCARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def mask_name(value: str) -> str:
    """往来单位与人名：首字 + **。空串与超短串原样返回（金额字符串不走这里）。"""
    if not value:
        return value
    text = str(value).strip()
    if len(text) <= 1:
        return text
    return text[0] + "**"


def mask_content_numbers(text: str) -> str:
    text = _IDCARD_RE.sub(lambda m: m.group(0)[:3] + "***********" + m.group(0)[-4:], text)
    text = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
    text = _BANKCARD_RE.sub(lambda m: m.group(0)[:4] + " ******** " + m.group(0)[-4:], text)
    return text


def sanitize_value(value: Any, key: str = "") -> Any:
    """深拷贝式脱敏：dict/list 递归；命中名字段的字符串整体打码，其余字符串只打码号码。"""
    if isinstance(value, dict):
        return {k: sanitize_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item, key) for item in value]
    if isinstance(value, str):
        if key.lower() in _NAME_KEYS:
            return mask_name(value)
        return mask_content_numbers(value)
    return value


def sanitize_snapshot(snapshot: dict) -> dict:
    return sanitize_value(snapshot)


def json_safe(value: Any) -> Any:
    """快照落库前的深转换：Decimal→字符串（金额传输约定），date/datetime→ISO。"""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# 快照构建
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "本页面为账套数据健康度体检视图，仅供软件操作辅导与会计规则参考之用，"
    "不构成法定代理记账或审计意见；最终确认与纳税申报由经营者自主完成。"
)


async def _income_summary(session: AsyncSession, company_id: str, month_start) -> dict[str, str]:
    """当月损益概览：收入(5xxx) / 费用(6xxx，不含税金外成本重分类的简化口径)。"""
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (
        await session.execute(
            select(
                func.coalesce(func.sum(JournalLine.amount).filter(
                    JournalLine.account_code.like("5%"), JournalLine.direction == "credit"), 0),
                func.coalesce(func.sum(JournalLine.amount).filter(
                    JournalLine.account_code.like("6%"), JournalLine.direction == "debit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalLine.company_id == company_id,
                JournalEntry.period_start == month_start,
                JournalEntry.status.in_(("posted", "reversed")),
            )
        )
    ).one()
    revenue, expense = money(rows[0]), money(rows[1])
    return {
        "revenue": money_text(revenue),
        "expense": money_text(expense),
        "profit": money_text(revenue - expense),
    }


async def build_snapshot(session: AsyncSession, company_id: str, user_id: str, month: str) -> dict:
    """组装体检快照：五项检查(含试算平衡+账证明细) + 异常清单 + 票流账差异 + 计提 + 损益概览。

    数据源与 generate_close_report 同源（period_close_checks / todo_service /
    get_difference_report），保证 CPA 看到的与老板结账向导一致。
    """
    from services.accounting_core_service import period_close_checks, period_start_for
    from services.todo_service import list_todos
    from services.bank_service import get_difference_report
    from services.accrual_service import list_pending_accruals

    month = period_start_for(str(month)).strftime("%Y-%m")
    checks = await period_close_checks(session, company_id, month)

    anomalies = []
    try:
        todos = await list_todos(user_id, company_id)
    except Exception:  # noqa: BLE001 - 异常清单拿不到不阻断体检快照
        todos = []
    for todo in todos or []:
        meta = todo.get("meta") or {}
        anomalies.append({
            "id": todo.get("id") or todo.get("key") or "",
            "type": todo.get("type"),
            "level": todo.get("level"),
            "title": todo.get("title"),
            "description": todo.get("description") or todo.get("desc"),
            "entryId": meta.get("entryId") or todo.get("entryId"),
            "amount": todo.get("amount"),
        })

    unmatched: dict[str, Any] = {"status": "unavailable"}
    try:
        unmatched = await get_difference_report(company_id, month)
    except Exception:  # noqa: BLE001
        unmatched = {"status": "unavailable", "message": "票流账差异查询失败"}

    accruals: dict[str, Any] = {}
    try:
        pending = await list_pending_accruals(session, company_id, period=period_start_for(month))
        accruals["estimateCount"] = len(pending.get("items") or pending.get("entries") or [])
    except Exception:  # noqa: BLE001
        accruals["estimateCount"] = None

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "month": month,
        "disclaimer": DISCLAIMER,
        "checks": checks,
        "trialBalance": checks.get("accounts") or [],
        "anomalies": anomalies,
        "unmatched": unmatched,
        "accruals": accruals,
        "incomeSummary": await _income_summary(session, company_id, period_start_for(month)),
    }


def snapshot_stats(snapshot: dict) -> dict:
    """生成响应与预览共用的概览统计。"""
    checks = snapshot.get("checks") or {}
    check_rows = checks.get("checks") or []
    blocking = sum(1 for row in check_rows if row.get("status") == "blocking")
    return {
        "month": snapshot.get("month"),
        "checkCount": len(check_rows),
        "blockingCount": blocking,
        "anomalyCount": len(snapshot.get("anomalies") or []),
        "trialBalanceRows": len(snapshot.get("trialBalance") or []),
        "incomeSummary": snapshot.get("incomeSummary") or {},
    }


# ---------------------------------------------------------------------------
# 老板侧：签发 / 列表 / 撤销
# ---------------------------------------------------------------------------

async def _prepare_raw_schemas() -> None:
    """五项检查/差异报告/待办用的裸 SQL 表在全新账套可能还没建。

    必须在 session 事务开启**之前**执行：SQLite 是文件级锁，裸连接层
    busy timeout 30s——事务内再首次建表会白等 30 秒。
    """
    from services.audit_service import _ensure_schema as ensure_audit_schema
    from services.bank_service import _ensure_schema as ensure_bank_schema
    from services.todo_service import _ensure_schema as ensure_todo_schema

    await ensure_bank_schema()
    await ensure_todo_schema()
    await ensure_audit_schema()


async def create_share(
    company_id: str,
    user_id: str,
    month: str,
    *,
    anonymized: bool = True,
    expires_in_days: int = 30,
) -> tuple[dict, str]:
    """生成快照 + 签发链接。返回 (share 概览, 明文 token)。"""
    if not require_entitlement(company_id):
        raise PermissionError("该功能需要 Solo Pro 订阅")
    if expires_in_days not in EXPIRY_CHOICES:
        expires_in_days = 30

    await _prepare_raw_schemas()
    async with session_scope() as session:
        raw_snapshot = await build_snapshot(session, company_id, user_id, month)
        snapshot = json_safe(sanitize_snapshot(raw_snapshot) if anonymized else raw_snapshot)
        token = generate_share_token()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
            if expires_in_days else None
        )
        share = AuditShare(
            company_id=company_id,
            user_id=user_id,
            month=str(snapshot.get("month") or month)[:7],
            anonymized=anonymized,
            snapshot=snapshot,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
        session.add(share)
        await session.flush()
        overview = _share_overview(share, annotation_count=0)

    # 留痕必须在事务提交后：SQLite 文件级锁下，事务内开裸连接写 audit_log 会忙等 30s
    from services.audit_service import log_action

    await log_action(company_id, user_id, "audit_share_create", "audit_share", share.id,
                     {"month": share.month, "anonymized": anonymized,
                      "expiresInDays": expires_in_days})
    overview["stats"] = snapshot_stats(snapshot)
    return overview, token


async def preview_snapshot(company_id: str, user_id: str, month: str, *, anonymized: bool = True) -> dict:
    """生成前脱敏预览：统计 + 异常清单前 5 条样例（所见即所得）。"""
    await _prepare_raw_schemas()
    async with session_scope() as session:
        raw = await build_snapshot(session, company_id, user_id, month)
    snapshot = json_safe(sanitize_snapshot(raw) if anonymized else raw)
    preview = snapshot_stats(snapshot)
    preview["anonymized"] = anonymized
    preview["sampleAnomalies"] = (snapshot.get("anomalies") or [])[:5]
    return preview


def _share_overview(share: AuditShare, *, annotation_count: int) -> dict:
    now = datetime.now(timezone.utc)
    expires = _as_utc(share.expires_at)
    if share.revoked_at is not None:
        state = "revoked"
    elif expires is not None and expires < now:
        state = "expired"
    else:
        state = "active"
    return {
        "id": share.id,
        "month": share.month,
        "anonymized": share.anonymized,
        "state": state,
        "expiresAt": expires.isoformat() if expires else None,
        "revokedAt": share.revoked_at.isoformat() if share.revoked_at else None,
        "viewCount": share.view_count,
        "lastViewedAt": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "annotationCount": annotation_count,
        "createdAt": share.created_at.isoformat() if share.created_at else None,
    }


async def list_shares(company_id: str, user_id: str) -> list[dict]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditShare, func.count(ShareAnnotation.id))
                .outerjoin(ShareAnnotation, ShareAnnotation.share_id == AuditShare.id)
                .where(AuditShare.company_id == company_id)
                .group_by(AuditShare.id)
                .order_by(AuditShare.created_at.desc())
            )
        ).all()
        return [_share_overview(share, annotation_count=count) for share, count in rows]


async def get_share_detail(company_id: str, share_id: str) -> dict | None:
    async with session_scope() as session:
        share = await session.get(AuditShare, share_id)
        if share is None or share.company_id != company_id:
            return None
        annotations = await list_annotations_for_share(session, share_id)
        overview = _share_overview(share, annotation_count=len(annotations))
        overview["annotations"] = annotations
        return overview


async def list_annotations_for_share(session: AsyncSession, share_id: str) -> list[dict]:
    rows = (
        await session.execute(
            select(ShareAnnotation)
            .where(ShareAnnotation.share_id == share_id)
            .order_by(ShareAnnotation.created_at.asc())
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "shareId": row.share_id,
            "anchorType": row.anchor_type,
            "anchorId": row.anchor_id,
            "body": row.body,
            "authorName": row.author_name,
            "status": row.status,
            "resolutionNote": row.resolution_note,
            "resolvedBy": row.resolved_by,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def revoke_share(company_id: str, user_id: str, share_id: str) -> dict | None:
    async with session_scope() as session:
        share = await session.get(AuditShare, share_id)
        if share is None or share.company_id != company_id:
            return None
        if share.revoked_at is None:
            share.revoked_at = datetime.now(timezone.utc)
            await session.flush()
        overview = _share_overview(share, annotation_count=0)

    from services.audit_service import log_action

    await log_action(company_id, user_id, "audit_share_revoke", "audit_share", share_id, {})
    return overview


async def update_annotation_status(
    company_id: str,
    user_id: str,
    share_id: str,
    annotation_id: str,
    status: str,
    resolution_note: str | None = None,
) -> dict | None:
    if status not in ("acknowledged", "resolved", "dismissed", "open"):
        raise ValueError("非法的批注状态")
    async with session_scope() as session:
        annotation = await session.get(ShareAnnotation, annotation_id)
        if annotation is None or annotation.company_id != company_id or annotation.share_id != share_id:
            return None
        annotation.status = status
        annotation.resolution_note = resolution_note
        annotation.resolved_by = user_id
        await session.flush()
        return {
            "id": annotation.id,
            "status": annotation.status,
            "resolutionNote": annotation.resolution_note,
        }
