# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Closing.v1 contract tests shared by the wizard, product AI and MCP."""
from __future__ import annotations

import pytest

from services.closing_workflow_service import (
    _asset_closure_findings,
    action_payload_hash,
    build_closing_findings,
    closing_snapshot_hash,
    manual_close_guidance,
)


def test_snapshot_hash_is_stable_and_changes_with_findings():
    finding_a = {
        "findingId": "clf_a",
        "count": 1,
        "authoritativeIds": {"statementIds": ["s1", "s2"]},
    }
    finding_b = {
        "findingId": "clf_b",
        "count": 2,
        "authoritativeIds": {"statementIds": ["s3"]},
    }
    base = {
        "period": "2026-05",
        "periodStatus": "open",
        "periodVersion": 3,
        "ruleVersions": {"closing": "v1"},
        "canClose": False,
        "blockingCount": 1,
        "checks": [{"code": "BOOK_TO_ACTUAL", "status": "blocking"}],
        "notices": [],
        "snapshot": {"debit": "100.00"},
        "findings": [finding_a, finding_b],
    }
    reordered = {
        **base,
        "findings": [
            {**finding_b, "authoritativeIds": {"statementIds": ["s3"]}},
            {**finding_a, "authoritativeIds": {"statementIds": ["s2", "s1"]}},
        ],
    }
    changed = {
        **base,
        "findings": [finding_a, {**finding_b, "count": 3}],
    }

    assert closing_snapshot_hash(base) == closing_snapshot_hash(reordered)
    assert closing_snapshot_hash(base) != closing_snapshot_hash(changed)


def test_manual_close_guidance_points_to_fixed_wizard_step(monkeypatch):
    from services import closing_workflow_service

    monkeypatch.setattr(closing_workflow_service, "APP_PUBLIC_URL", "http://localhost:5173/")

    ready = manual_close_guidance("2026-10", True)
    assert ready["url"] == "http://localhost:5173/closing-wizard?month=2026-10&step=3"
    assert ready["status"] == "ready_to_close"
    assert ready["requiresLogin"] is True
    assert "AI 和 MCP 不会执行最终锁账" in ready["message"]

    blocked = manual_close_guidance("2026-10", False)
    assert blocked["status"] == "action_required"
    assert "处理阻断项" in blocked["label"]
    assert "token" not in blocked["url"].lower()
    assert "company" not in blocked["url"].lower()
    assert "snapshot" not in blocked["url"].lower()


def test_closing_findings_expose_only_authorized_repairs():
    checks = [
        {
            "code": "BOOK_TO_ACTUAL",
            "name": "账实核对",
            "status": "blocking",
            "unreconciledBankCount": 1,
            "issues": [
                {
                    "type": "unreconciled",
                    "statementId": "stmt-1",
                    "candidateEntryIds": ["entry-1"],
                }
            ],
        },
        {
            "code": "VOUCHER_TO_SOURCE",
            "name": "账证核对",
            "status": "blocking",
            "records": [
                {
                    "entryId": "entry-missing-source",
                    "reasons": ["missing_source"],
                }
            ],
        },
        {
            "code": "FIXED_ASSET_DEPRECIATION",
            "name": "固定资产折旧",
            "status": "blocking",
            "pendingCount": 1,
            "totalAmount": "88.00",
            "records": [{"assetId": "asset-1", "amount": "88.00"}],
        },
    ]

    findings = build_closing_findings("2026-05", checks)
    by_code = {}
    for finding in findings:
        by_code.setdefault(finding["checkCode"], []).append(finding)

    bank = by_code["BOOK_TO_ACTUAL"][0]
    assert bank["authoritativeIds"]["statementIds"] == ["stmt-1"]
    assert bank["authoritativeIds"]["entryIds"] == ["entry-1"]
    assert bank["allowedRepairs"] == ["match", "statement_voucher"]

    source = by_code["VOUCHER_TO_SOURCE"][0]
    assert source["authoritativeIds"]["entryIds"] == ["entry-missing-source"]
    assert source["allowedRepairs"] == []

    depreciation = by_code["FIXED_ASSET_DEPRECIATION"][0]
    assert depreciation["authoritativeIds"]["assetIds"] == ["asset-1"]
    assert depreciation["allowedRepairs"] == ["accrual:depreciation"]


def test_asset_closure_preview_becomes_one_bound_finding():
    preview = {
        "month": "2026-05",
        "fixedAssets": {
            "missingCards": [
                {
                    "entryId": "entry-fixed",
                    "sourceEntryId": "entry-fixed",
                    "name": "服务器",
                    "originalValue": 12000,
                }
            ],
            "dueDepreciation": [{"period": "2026-05", "amount": 200, "count": 1}],
            "blockedItems": [],
        },
        "intangibles": {
            "missingCards": [],
            "dueAmortization": [],
            "blockedItems": [],
        },
    }

    findings = _asset_closure_findings("2026-05", preview)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["checkCode"] == "ASSET_CLOSURE"
    assert finding["allowedRepairs"] == ["asset_closure"]
    assert finding["authoritativeIds"]["entryIds"] == ["entry-fixed"]
    assert finding["count"] == 2
    assert finding["amount"] == "12200.00"


def test_action_hash_binds_payload_and_snapshot_finding():
    payload = {"kind": "salary", "month": "2026-05", "preview": {"employeeCount": 2}}
    binding = {
        "month": "2026-05",
        "findingId": "clf_salary",
        "baseSnapshotHash": "a" * 64,
        "periodVersion": 4,
    }
    first = action_payload_hash("propose_accrual", {"payload": payload, "closingBinding": binding})
    same = action_payload_hash("propose_accrual", {"payload": payload, "closingBinding": binding})
    changed = action_payload_hash(
        "propose_accrual",
        {"payload": {**payload, "month": "2026-06"}, "closingBinding": binding},
    )

    assert first == same
    assert first != changed


@pytest.mark.asyncio
async def test_closing_action_serializes_binding_and_deduplicates():
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine
    from agent.actions import create_action

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    binding = {
        "month": "2026-05",
        "findingId": "clf_test",
        "baseSnapshotHash": "b" * 64,
        "periodVersion": 2,
        "surface": "mcp",
    }
    payload = {"statementId": "stmt-1", "entryIds": ["entry-1"]}
    first = await create_action(
        company_id="c-closing-action-contract",
        user_id="u-closing-action-contract",
        action_type="propose_match",
        title="对账草稿",
        payload=payload,
        evidence={
            "closingBinding": binding,
            "actionVersion": 1,
            "created_via": "agent_closing_repair",
        },
        conversation_id="conv-contract",
    )
    replay = await create_action(
        company_id="c-closing-action-contract",
        user_id="u-closing-action-contract",
        action_type="propose_match",
        title="对账草稿",
        payload=payload,
        evidence={
            "closingBinding": binding,
            "actionVersion": 1,
            "created_via": "agent_closing_repair",
        },
        conversation_id="conv-contract",
    )

    assert replay["id"] == first["id"]
    assert first["closingBinding"] == binding
    assert first["actionVersion"] == 1
    assert first["expiresAt"]
