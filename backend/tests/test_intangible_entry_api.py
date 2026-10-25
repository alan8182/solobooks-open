# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""来源交易 → 无形资产卡片 API 冒烟测试。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from services.asset_closure_service import preview_asset_closure


def test_confirm_intangible_purchase_then_create_card_from_entry(client, auth_headers):
    confirmed = client.post(
        "/api/record/confirm",
        headers={**auth_headers, "Idempotency-Key": f"ia-entry-{uuid4()}"},
        json={
            "direction": "expense",
            "amount": "120000.00",
            "title": f"购管理软件-{uuid4().hex[:6]}",
            "category": "无形资产",
            "occurDate": "2026-09-10",
            "payer": "company_account",
            "billingStatus": "has_invoice",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    entry_id = confirmed.json()["entry"]["id"]

    preview = asyncio.run(preview_asset_closure("c1", "test_user_001", "2026-10"))
    assert any(
        item["sourceEntryId"] == entry_id
        for item in preview["intangibles"]["missingCards"]
    )

    prefill = client.get(f"/api/intangible/assets/from-entry/{entry_id}", headers=auth_headers)
    assert prefill.status_code == 200, prefill.text
    body = prefill.json()
    assert body["sourceEntryId"] == entry_id
    assert body["existingAssetId"] is None
    assert body["originalValue"] == "120000.00"
    assert body["suggestedCategory"] == "software"

    created = client.post(
        "/api/intangible/assets/from-entry",
        headers=auth_headers,
        json={
            "sourceEntryId": entry_id,
            "name": body["name"],
            "category": body["suggestedCategory"],
            "acquisitionType": "purchase",
            "acquisitionDate": body["acquisitionDate"],
            "availableForUseDate": body["availableForUseDate"],
            "originalValue": body["originalValue"],
            "usefulLifeMonths": 120,
        },
    )
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["asset"]["sourceEntryId"] == entry_id
    assert result["asset"]["status"] == "active"
    assert len(result["duePeriods"]) >= 1

    preview = client.post(
        "/api/intangible/amortization/catch-up",
        headers=auth_headers,
        json={
            "throughPeriod": "2026-10",
            "assetIds": [result["asset"]["id"]],
            "dryRun": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["periods"][0]["period"] == "2026-09"

    repeated = client.post(
        "/api/intangible/assets/from-entry",
        headers=auth_headers,
        json={
            "sourceEntryId": entry_id,
            "name": body["name"],
            "category": body["suggestedCategory"],
            "acquisitionType": "purchase",
            "acquisitionDate": body["acquisitionDate"],
            "availableForUseDate": body["availableForUseDate"],
            "originalValue": body["originalValue"],
            "usefulLifeMonths": 120,
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "DUPLICATE_SOURCE"
