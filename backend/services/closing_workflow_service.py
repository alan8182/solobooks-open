# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Canonical read model and action binding for month-end closing.

The accounting rules remain in ``accounting_core_service``.  This module turns
their result into one stable contract shared by the closing wizard, product
chat and MCP.  AI surfaces may select an authoritative finding and prepare a
draft action, but they never calculate amounts or close a period directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import APP_PUBLIC_URL
from core.errors import AccountingError, CLOSING_SNAPSHOT_STALE
from db.models import AccountingPeriod, JournalEntry

logger = logging.getLogger(__name__)

CLOSING_SCHEMA_VERSION = "closing.v1"
CLOSING_RULE_VERSION = "closing-workflow-v1"
CLOSING_ACTION_TYPES = frozenset(
    {"propose_match", "propose_statement_vouchers", "propose_accrual"}
)


def manual_close_guidance(period: str, can_close: bool) -> dict[str, Any]:
    """Return the login-protected web entry for the user's final close action.

    This guidance is deliberately excluded from ``snapshotHash``: it contains no
    accounting facts or authorization material, only the canonical period and
    the fixed wizard step.
    """
    month = str(period or "").strip()
    query = urlencode({"month": month, "step": 3})
    public_url = str(APP_PUBLIC_URL or "").strip().rstrip("/")
    ready = bool(can_close)
    return {
        "url": f"{public_url}/closing-wizard?{query}",
        "label": "打开网页完成最终锁账" if ready else "打开网页处理阻断项并重新检查",
        "status": "ready_to_close" if ready else "action_required",
        "requiresLogin": True,
        "message": (
            "AI 和 MCP 不会执行最终锁账；请在网页中重新检查并确认。"
            if ready
            else "当前仍有结账阻断项；请在网页中处理后重新检查，最终锁账需由登录用户确认。"
        ),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.01')):.2f}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_value(value: Any) -> Any:
    """Sort list values by their canonical representation before hashing.

    Query order is not an accounting fact.  Sorting here prevents a harmless
    database plan/index change from invalidating a user's closing preview.
    """
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        items = [_stable_value(item) for item in value]
        return sorted(items, key=canonical_json)
    return _json_safe(value)


def closing_snapshot_hash(payload: dict[str, Any]) -> str:
    """Hash only authoritative close data; timestamps and paging are excluded."""
    canonical = _stable_value({
        "period": payload.get("period"),
        "periodStatus": payload.get("periodStatus"),
        "periodVersion": int(payload.get("periodVersion") or 1),
        "ruleVersions": payload.get("ruleVersions") or {},
        "canClose": bool(payload.get("canClose")),
        "blockingCount": int(payload.get("blockingCount") or 0),
        "checks": payload.get("checks") or [],
        "notices": payload.get("notices") or [],
        "snapshot": payload.get("snapshot") or {},
        "findings": payload.get("findings") or [],
    })
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def action_payload_hash(action_type: str, payload: dict[str, Any]) -> str:
    raw = canonical_json({"actionType": action_type, "payload": payload})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _money_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"
    except Exception:
        return str(value)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _ids(**groups: Iterable[Any]) -> dict[str, list[str]]:
    return {
        "statementIds": _unique_strings(groups.get("statementIds") or []),
        "entryIds": _unique_strings(groups.get("entryIds") or []),
        "assetIds": _unique_strings(groups.get("assetIds") or []),
        "scheduleIds": _unique_strings(groups.get("scheduleIds") or []),
    }


def _finding(
    *,
    month: str,
    check_code: str,
    kind: str,
    title: str,
    severity: str,
    count: int,
    amount: Any,
    ids: dict[str, list[str]],
    allowed_repairs: Iterable[str] = (),
    details: Any = None,
    identity: Any = None,
) -> dict[str, Any]:
    signature = {
        "month": month,
        "checkCode": check_code,
        "kind": kind,
        "authoritativeIds": ids,
        "identity": _json_safe(identity),
    }
    finding_id = "clf_" + hashlib.sha256(canonical_json(signature).encode("utf-8")).hexdigest()[:24]
    return {
        "findingId": finding_id,
        "checkCode": check_code,
        "severity": severity,
        "kind": kind,
        "title": title,
        "count": max(0, int(count or 0)),
        "amount": _money_text(amount),
        "authoritativeIds": ids,
        "allowedRepairs": sorted(set(allowed_repairs)),
        "details": _json_safe(details),
    }


def _voucher_findings(month: str, check: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {
        "voucher_missing_source": {
            "title": "凭证缺少有效来源",
            "entry_ids": [],
            "details": [],
        },
        "voucher_pending_review": {
            "title": "凭证等待复核",
            "entry_ids": [],
            "details": [],
        },
        "voucher_pending_post": {
            "title": "凭证将在结账时自动过账",
            "entry_ids": [],
            "details": [],
        },
    }
    records = sorted(
        (row for row in (check.get("records") or []) if isinstance(row, dict)),
        key=lambda row: str(row.get("entryId") or ""),
    )
    for record in records:
        reasons = set(record.get("reasons") or [])
        if "missing_source" in reasons:
            kind = "voucher_missing_source"
        elif "pending_review" in reasons:
            kind = "voucher_pending_review"
        elif "pending_post" in reasons:
            kind = "voucher_pending_post"
        else:
            continue
        groups[kind]["entry_ids"].append(record.get("entryId"))
        if len(groups[kind]["details"]) < 20:
            groups[kind]["details"].append(record)

    findings: list[dict[str, Any]] = []
    for kind, group in groups.items():
        entry_ids = _unique_strings(group["entry_ids"])
        if not entry_ids:
            continue
        findings.append(
            _finding(
                month=month,
                check_code="VOUCHER_TO_SOURCE",
                kind=kind,
                title=group["title"],
                severity="blocking",
                count=len(entry_ids),
                amount=None,
                ids=_ids(entryIds=entry_ids),
                details=group["details"],
            )
        )
    return findings


def _book_to_actual_findings(month: str, check: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_by_issue = {
        "unreconciled": ("match", "statement_voucher"),
    }
    labels = {
        "unreconciled": "银行流水尚未关联凭证",
        "broken_link": "银行流水关联的凭证不存在",
        "duplicate_match": "同一凭证被多笔流水重复核销",
        "unposted_match": "已匹配凭证尚未过账",
        "possible_duplicate_income": "银行到账可能重复记为微信收入",
        "missing_direction": "银行流水缺少收支方向",
        "missing_source": "银行流水缺少有效权威来源",
        "book_only": "银行存款分录未关联流水",
        "statement_balance_mismatch": "流水余额不连续",
        "bank_balance_mismatch": "账面银行存款与银行余额不一致",
        "bank_effect_mismatch": "凭证银行科目影响与流水方向或金额不一致",
    }
    findings: list[dict[str, Any]] = []
    issues = sorted(
        (row for row in (check.get("issues") or []) if isinstance(row, dict)),
        key=canonical_json,
    )
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        kind = str(issue.get("type") or "book_to_actual_issue")
        statement_ids = _unique_strings(
            [
                issue.get("statementId"),
                *(issue.get("statementIds") or []),
            ]
        )
        entry_ids = _unique_strings(
            [
                issue.get("entryId"),
                *(issue.get("entryIds") or []),
                *(issue.get("candidateEntryIds") or []),
            ]
        )
        findings.append(
            _finding(
                month=month,
                check_code="BOOK_TO_ACTUAL",
                kind=kind,
                title=labels.get(kind, str(issue.get("reason") or "银行流水核对问题")),
                severity="blocking",
                count=1,
                amount=issue.get("difference") or issue.get("amount"),
                ids=_ids(statementIds=statement_ids, entryIds=entry_ids),
                allowed_repairs=allowed_by_issue.get(kind, ()),
                details=issue,
                identity={
                    "type": kind,
                    "statementIds": statement_ids,
                    "entryIds": entry_ids,
                    "reason": issue.get("reason"),
                },
            )
        )

    issue_statement_ids = {
        statement_id
        for finding in findings
        for statement_id in ((finding.get("authoritativeIds") or {}).get("statementIds") or [])
    }
    unreconciled_statement_ids = _unique_strings(
        check.get("unreconciledStatementIds") or []
    )
    summary_statement_ids = [
        statement_id
        for statement_id in unreconciled_statement_ids
        if statement_id not in issue_statement_ids
    ]
    unreconciled = int(check.get("unreconciledBankCount") or 0)
    summary_count = max(len(summary_statement_ids), unreconciled - len(issue_statement_ids))
    if summary_count > 0:
        findings.append(
            _finding(
                month=month,
                check_code="BOOK_TO_ACTUAL",
                kind="unreconciled_summary",
                title=f"仍有 {summary_count} 笔银行流水未核对",
                severity="blocking",
                count=summary_count,
                amount=None,
                ids=_ids(statementIds=summary_statement_ids),
                allowed_repairs=("match", "statement_voucher"),
                details=None,
                identity={"statementIds": summary_statement_ids},
            )
        )
    return findings


def _asset_or_payroll_finding(month: str, check: dict[str, Any]) -> dict[str, Any] | None:
    code = str(check.get("code") or "")
    if code not in {
        "INTANGIBLE_AMORTIZATION",
        "FIXED_ASSET_DEPRECIATION",
        "PAYROLL_ACCRUAL",
        "FIXED_ASSET_RECONCILIATION",
        "IMPAIRMENT_TEST_DUE",
    }:
        return None
    records = sorted(
        (row for row in (check.get("records") or []) if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("assetId") or ""),
            str(row.get("periodStart") or ""),
            str(row.get("amount") or ""),
        ),
    )
    asset_ids = _unique_strings(row.get("assetId") for row in records)
    allowed: tuple[str, ...] = ()
    kind = code.lower()
    if code == "INTANGIBLE_AMORTIZATION" and int(check.get("pendingCount") or 0) > 0:
        allowed = ("accrual:amortization",)
        kind = "intangible_amortization"
    elif code == "FIXED_ASSET_DEPRECIATION" and int(check.get("pendingCount") or 0) > 0:
        allowed = ("accrual:depreciation",)
        kind = "fixed_asset_depreciation"
    elif code == "PAYROLL_ACCRUAL":
        message = str(check.get("message") or "")
        if "待复核" not in message:
            allowed = ("accrual:salary",)
        kind = "payroll_accrual"
    elif code == "FIXED_ASSET_RECONCILIATION":
        kind = "fixed_asset_reconciliation"
    elif code == "IMPAIRMENT_TEST_DUE":
        kind = "impairment_test_due"
    return _finding(
        month=month,
        check_code=code,
        kind=kind,
        title=str(check.get("name") or code),
        severity="blocking" if check.get("status") == "blocking" else "warning",
        count=int(
            check.get("pendingCount")
            or check.get("count")
            or check.get("employeeCount")
            or len(records)
            or 1
        ),
        amount=check.get("totalAmount"),
        ids=_ids(assetIds=asset_ids),
        allowed_repairs=allowed,
        details=records[:20] or {
            key: value
            for key, value in check.items()
            if key not in {"records", "issues"}
        },
    )


def _asset_closure_findings(
    month: str,
    preview: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose the existing asset repair preview through the canonical snapshot."""
    fixed = preview.get("fixedAssets") or {}
    intangible = preview.get("intangibles") or {}
    missing = [
        *(fixed.get("missingCards") or []),
        *(intangible.get("missingCards") or []),
    ]
    due = [
        *(fixed.get("dueDepreciation") or []),
        *(intangible.get("dueAmortization") or []),
    ]
    blocked = [
        *(fixed.get("blockedItems") or []),
        *(intangible.get("blockedItems") or []),
    ]
    findings: list[dict[str, Any]] = []

    if missing or due:
        entry_ids = _unique_strings(
            item.get("sourceEntryId") or item.get("entryId")
            for item in missing
            if isinstance(item, dict)
        )
        periods = _unique_strings(
            item.get("period") for item in due if isinstance(item, dict)
        )
        amount = Decimal("0")
        for item in missing:
            try:
                amount += Decimal(str(item.get("originalValue") or 0))
            except Exception:
                pass
        for item in due:
            try:
                amount += Decimal(str(item.get("amount") or 0))
            except Exception:
                pass
        findings.append(
            _finding(
                month=month,
                check_code="ASSET_CLOSURE",
                kind="asset_closure",
                title="资产建卡与折旧摊销补提",
                severity="blocking" if due else "warning",
                count=len(missing) + sum(
                    int(item.get("count") or 1)
                    for item in due
                    if isinstance(item, dict)
                ),
                amount=amount,
                ids=_ids(entryIds=entry_ids),
                allowed_repairs=("asset_closure",),
                details=preview,
                identity={"entryIds": entry_ids, "periods": periods},
            )
        )

    if blocked:
        findings.append(
            _finding(
                month=month,
                check_code="ASSET_CLOSURE",
                kind="asset_closure_blocked",
                title="部分资产事项需人工处理",
                severity="warning",
                count=len(blocked),
                amount=None,
                ids=_ids(),
                details={"blockedItems": blocked},
                identity={"blockedItems": blocked},
            )
        )
    return findings


def build_closing_findings(month: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "")
        if status in {"passed", "not_required"}:
            continue
        code = str(check.get("code") or "")
        if code == "VOUCHER_TO_SOURCE":
            findings.extend(_voucher_findings(month, check))
            continue
        if code == "BOOK_TO_ACTUAL":
            findings.extend(_book_to_actual_findings(month, check))
            continue
        special = _asset_or_payroll_finding(month, check)
        if special is not None:
            findings.append(special)
            continue
        record_ids = [
            row.get("entryId")
            for row in (check.get("records") or [])
            if isinstance(row, dict) and row.get("entryId")
        ]
        findings.append(
            _finding(
                month=month,
                check_code=code,
                kind=f"{code.lower()}_issue",
                title=str(check.get("name") or code),
                severity="blocking" if status == "blocking" else "warning",
                count=int(
                    check.get("pendingCount")
                    or check.get("count")
                    or check.get("issueCount")
                    or len(record_ids)
                    or 1
                ),
                amount=check.get("totalAmount"),
                ids=_ids(entryIds=record_ids),
                details={
                    key: value
                    for key, value in check.items()
                    if key not in {"records", "issues"}
                },
            )
        )
    return sorted(findings, key=lambda item: (item["checkCode"], item["findingId"]))


def _compact_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        item = {
            key: value
            for key, value in check.items()
            if key not in {"records", "issues", "accounts"}
        }
        records = check.get("records")
        issues = check.get("issues")
        if isinstance(records, list):
            item["recordCount"] = len(records)
        if isinstance(issues, list):
            item["issueCount"] = len(issues)
        compact.append(_json_safe(item))
    return sorted(compact, key=lambda item: str(item.get("code") or ""))


async def _build_full_snapshot(
    session: AsyncSession,
    company_id: str,
    month: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    from services.accounting_core_service import period_close_checks, period_start_for

    month_start = period_start_for(month)
    canonical_month = month_start.strftime("%Y-%m")
    raw = await period_close_checks(session, company_id, month_start)
    period = (
        await session.execute(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == month_start,
            )
        )
    ).scalar_one_or_none()
    findings = build_closing_findings(canonical_month, raw.get("checks") or [])
    if user_id:
        try:
            from services.asset_closure_service import preview_asset_closure_in_session

            asset_preview = await preview_asset_closure_in_session(
                session,
                company_id,
                user_id,
                canonical_month,
            )
            findings.extend(_asset_closure_findings(canonical_month, asset_preview))
            findings.sort(key=lambda item: (item["checkCode"], item["findingId"]))
        except Exception:
            logger.exception(
                "closing_asset_preview_failed company_id=%s month=%s",
                company_id,
                canonical_month,
            )
            findings.append(
                _finding(
                    month=canonical_month,
                    check_code="ASSET_CLOSURE",
                    kind="asset_closure_preview_unavailable",
                    title="资产处理事项未能完成核验",
                    severity="warning",
                    count=1,
                    amount=None,
                    ids=_ids(),
                    details={"message": "资产预览暂时失败，请回资产管理页面人工核对"},
                )
            )
    period_version = int(period.version if period else 1)
    rule_versions = {
        "closing": CLOSING_RULE_VERSION,
        "accountMapping": "account-mapping-v2",
    }
    snapshot_hash = closing_snapshot_hash(
        {
            **raw,
            "periodVersion": period_version,
            "ruleVersions": rule_versions,
            "findings": findings,
        }
    )
    result = {
        **raw,
        "schemaVersion": CLOSING_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "periodVersion": period_version,
        "snapshotHash": snapshot_hash,
        "ruleVersions": rule_versions,
        "findings": findings,
    }
    logger.info(
        "closing_snapshot_generated company_id=%s month=%s period_version=%s "
        "snapshot_hash=%s finding_count=%s",
        company_id,
        canonical_month,
        period_version,
        snapshot_hash,
        len(findings),
    )
    return result


async def get_closing_snapshot(
    session: AsyncSession,
    company_id: str,
    month: str,
    *,
    user_id: str | None = None,
    compact: bool = False,
    finding_limit: int = 100,
    finding_cursor: int = 0,
) -> dict[str, Any]:
    """Return the canonical closing snapshot with deterministic finding paging."""
    snapshot = await _build_full_snapshot(
        session,
        company_id,
        str(month),
        user_id=user_id,
    )
    all_findings = snapshot["findings"]
    limit = max(1, min(int(finding_limit or 100), 100))
    cursor = max(0, int(finding_cursor or 0))
    page = all_findings[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    snapshot["findings"] = page
    snapshot["findingsTotal"] = len(all_findings)
    snapshot["nextFindingCursor"] = next_cursor if next_cursor < len(all_findings) else None
    snapshot["manualClose"] = manual_close_guidance(
        str(snapshot.get("period") or month),
        bool(snapshot.get("canClose")),
    )
    if compact:
        snapshot["checks"] = _compact_checks(snapshot.get("checks") or [])
    return snapshot


def _stale_error(
    message: str,
    *,
    snapshot: dict[str, Any] | None = None,
    finding_id: str | None = None,
) -> AccountingError:
    context: dict[str, Any] = {}
    if finding_id:
        context["findingId"] = finding_id
    if snapshot is not None:
        context["latestSnapshot"] = {
            key: snapshot.get(key)
            for key in (
                "schemaVersion",
                "period",
                "periodStatus",
                "periodVersion",
                "snapshotHash",
                "canClose",
                "blockingCount",
                "findingsTotal",
                "generatedAt",
            )
        }
    return AccountingError(CLOSING_SNAPSHOT_STALE, message, 409, context)


async def validate_snapshot_hash(
    session: AsyncSession,
    company_id: str,
    month: str,
    expected_snapshot_hash: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    snapshot = await _build_full_snapshot(
        session,
        company_id,
        str(month),
        user_id=user_id,
    )
    if not expected_snapshot_hash or snapshot["snapshotHash"] != expected_snapshot_hash:
        logger.warning(
            "closing_snapshot_stale company_id=%s month=%s expected=%s actual=%s",
            company_id,
            month,
            expected_snapshot_hash or "",
            snapshot["snapshotHash"],
        )
        raise _stale_error(
            "账务数据已发生变化，请重新运行结账检查后再确认",
            snapshot=snapshot,
        )
    return snapshot


def _repair_kind(action_type: str, payload: dict[str, Any]) -> str:
    if action_type == "propose_match":
        return "match"
    if action_type == "propose_statement_vouchers":
        return "statement_voucher"
    if action_type == "propose_vouchers" and any(
        isinstance(item, dict) and item.get("sourceType") == "bank_statement"
        for item in (payload.get("items") or [])
    ):
        return "statement_voucher"
    if action_type == "propose_accrual":
        kind = str(payload.get("kind") or "").strip()
        if kind == "asset_closure":
            return "asset_closure"
        return f"accrual:{kind}"
    return action_type


def _payload_ids(payload: dict[str, Any]) -> dict[str, list[str]]:
    statement_ids: list[Any] = []
    entry_ids: list[Any] = []
    asset_ids: list[Any] = list(payload.get("assetIds") or [])
    for item in payload.get("items") or []:
        if isinstance(item, dict):
            statement_ids.append(item.get("statementId"))
            entry_ids.append(item.get("entryId"))
            entry_ids.append(item.get("sourceEntryId"))
    statement_ids.append(payload.get("statementId"))
    entry_ids.extend(payload.get("entryIds") or [])
    entry_ids.append(payload.get("entryId"))
    entry_ids.extend(
        card.get("sourceEntryId")
        for key in ("fixedCards", "intangibleCards")
        for card in (payload.get(key) or [])
        if isinstance(card, dict)
    )
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
    for item in preview.get("assets") or []:
        if isinstance(item, dict):
            statement_ids.append(item.get("statementId"))
            entry_ids.append(item.get("entryId"))
            entry_ids.append(item.get("sourceEntryId"))
            asset_ids.append(item.get("assetId"))
    for key in ("fixedAssets", "intangibles"):
        group = preview.get(key)
        if not isinstance(group, dict):
            continue
        for item in group.get("missingCards") or []:
            if isinstance(item, dict):
                entry_ids.append(item.get("sourceEntryId") or item.get("entryId"))
        for item in (
            *(group.get("dueDepreciation") or []),
            *(group.get("dueAmortization") or []),
        ):
            if isinstance(item, dict):
                asset_ids.append(item.get("assetId"))
    return _ids(
        statementIds=statement_ids,
        entryIds=entry_ids,
        assetIds=asset_ids,
        scheduleIds=payload.get("scheduleIds") or [],
    )


async def validate_closing_action_binding(
    session: AsyncSession,
    company_id: str,
    action_type: str,
    payload: dict[str, Any],
    binding: dict[str, Any] | None,
    *,
    user_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not binding:
        raise _stale_error("该动作未绑定结账快照，请重新检查并发起")
    month = str(binding.get("month") or payload.get("month") or "")
    finding_id = str(binding.get("findingId") or "")
    expected_hash = str(binding.get("baseSnapshotHash") or "")
    snapshot = await _build_full_snapshot(
        session,
        company_id,
        month,
        user_id=user_id,
    )
    expected_period_version = int(binding.get("periodVersion") or 0)
    if not expected_period_version or snapshot["periodVersion"] != expected_period_version:
        raise _stale_error(
            "账期版本已变化，旧动作不能继续执行，请重新检查",
            snapshot=snapshot,
            finding_id=finding_id,
        )
    if not expected_hash or snapshot["snapshotHash"] != expected_hash:
        raise _stale_error(
            "结账数据已变化，旧动作不能继续执行，请重新检查",
            snapshot=snapshot,
            finding_id=finding_id,
        )
    finding = next(
        (item for item in snapshot.get("findings") or [] if item.get("findingId") == finding_id),
        None,
    )
    if finding is None:
        raise _stale_error(
            "该问题已处理或不再存在，请重新运行结账检查",
            snapshot=snapshot,
            finding_id=finding_id,
        )
    repair_kind = _repair_kind(action_type, payload)
    if repair_kind not in set(finding.get("allowedRepairs") or []):
        raise _stale_error(
            "该问题不允许通过当前动作处理，请回结账向导处理",
            snapshot=snapshot,
            finding_id=finding_id,
        )
    authoritative = finding.get("authoritativeIds") or {}
    requested = _payload_ids(payload)
    for key in ("statementIds", "assetIds", "scheduleIds"):
        allowed = set(authoritative.get(key) or [])
        requested_values = set(requested.get(key) or [])
        if requested_values and not requested_values.issubset(allowed):
            raise _stale_error(
                "动作引用的权威记录不在当前结账问题中，请重新检查",
                snapshot=snapshot,
                finding_id=finding_id,
            )

    requested_entries = set(requested.get("entryIds") or [])
    allowed_entries = set(authoritative.get("entryIds") or [])
    if requested_entries and allowed_entries and not requested_entries.issubset(allowed_entries):
        raise _stale_error(
            "动作引用的凭证已变化，请重新检查",
            snapshot=snapshot,
            finding_id=finding_id,
        )
    if requested_entries:
        # An unmatched statement often has no candidate voucher in the finding.
        # In that case the user may select a real voucher from this tenant, but
        # the ID still has to be checked against the database.
        existing_entries = set(
            (
                await session.execute(
                    select(JournalEntry.id).where(
                        JournalEntry.company_id == company_id,
                        JournalEntry.id.in_(requested_entries),
                    )
                )
            ).scalars().all()
        )
        if existing_entries != requested_entries:
            raise _stale_error(
                "动作引用的凭证不存在或不属于当前账套，请重新检查",
                snapshot=snapshot,
                finding_id=finding_id,
            )
    return snapshot, finding


def closing_action_binding(
    snapshot: dict[str, Any],
    finding: dict[str, Any],
    *,
    surface: str,
) -> dict[str, Any]:
    return {
        "month": snapshot.get("period"),
        "findingId": finding.get("findingId"),
        "baseSnapshotHash": snapshot.get("snapshotHash"),
        "periodVersion": int(snapshot.get("periodVersion") or 1),
        "surface": surface,
    }


async def close_period_from_snapshot(
    session: AsyncSession,
    company_id: str,
    month: str,
    *,
    expected_snapshot_hash: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    from services.accounting_core_service import close_period

    return await close_period(
        session,
        company_id,
        month,
        user_id=user_id,
        request_id=request_id,
        expected_snapshot_hash=expected_snapshot_hash,
    )
