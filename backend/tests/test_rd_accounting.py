from __future__ import annotations

from uuid import uuid4


def _active_project(client, auth_headers, *, start="2026-01-01", end="2026-12-31"):
    code = f"RD-{uuid4().hex[:8]}"
    response = client.post(
        "/api/rd/projects",
        headers=auth_headers,
        json={
            "code": code,
            "name": "智能记账研发项目",
            "manager": "研发负责人",
            "startDate": start,
            "endDate": end,
            "status": "active",
            "initiationDocumentStatus": "complete",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_rd_project_types_and_posted_ledger(client, auth_headers):
    types = client.get("/api/rd/expense-types", headers=auth_headers)
    assert types.status_code == 200
    assert [item["accountCode"] for item in types.json()["items"]] == [
        "530101", "530102", "530103", "530104", "530105", "530106", "530199"
    ]

    project = _active_project(client, auth_headers)
    accounts = client.get("/api/ledger/accounts", headers=auth_headers)
    assert accounts.status_code == 200
    account_codes = {item["code"] for item in accounts.json()["accounts"]}
    assert {"530101", "530102", "530103", "530104", "530105", "530106", "530199"} <= account_codes
    confirmed = client.post(
        "/api/record/confirm",
        headers={**auth_headers, "Idempotency-Key": f"rd-{uuid4()}"},
        json={
            "direction": "expense",
            "amount": "1200.00",
            "title": "研发原型材料",
            "category": "研发支出-费用化支出",
            "occurDate": "2026-06-15",
            "payer": "company_account",
            "rdProjectId": project["id"],
            "rdExpenseType": "direct_input",
            "rdOriginalAmount": "1200.00",
            "rdAllocationMethod": "direct",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    entry = confirmed.json()["entry"]
    rd_line = entry["voucherLines"]["debit"][0]
    assert rd_line["accountCode"] == "530102"
    assert rd_line["rdDetail"]["projectId"] == project["id"]
    assert rd_line["rdDetail"]["expenseType"] == "direct_input"

    ledger = client.get(
        "/api/rd/ledger?startDate=2026-04-01&endDate=2026-06-30",
        headers=auth_headers,
    )
    assert ledger.status_code == 200, ledger.text
    payload = ledger.json()
    assert payload["total"] == "1200.00"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["projectCode"] == project["code"]
    assert payload["items"][0]["expenseTypeLabel"] == "直接投入"

    parent_ledger = client.get(
        "/api/ledger/general?account_code=5301&month=2026-06",
        headers=auth_headers,
    )
    assert parent_ledger.status_code == 200
    assert parent_ledger.json()["items"][0]["debit"] == "1200.00"

    evidence = client.post(
        "/api/rd/evidence",
        headers=auth_headers,
        json={
            "projectId": project["id"],
            "entryId": entry["id"],
            "documentType": "invoice",
            "referenceNo": "INV-RD-2026-001",
            "status": "available",
        },
    )
    assert evidence.status_code == 200, evidence.text
    evidence_list = client.get("/api/rd/evidence", headers=auth_headers)
    assert evidence_list.status_code == 200
    assert any(item["referenceNo"] == "INV-RD-2026-001" for item in evidence_list.json()["items"])

    summary = client.get(
        "/api/rd/reconciliation-summary?startDate=2026-04-01&endDate=2026-06-30",
        headers=auth_headers,
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["ledgerTotal"] == "1200.00"
    assert summary.json()["generalLedgerTotal"] == "1200.00"
    assert summary.json()["isReconciled"] is True
    assert summary.json()["evidenceDocuments"][0]["referenceNo"] == "INV-RD-2026-001"


def test_rd_confirmation_requires_project_and_allocation_basis(client, auth_headers):
    missing = client.post(
        "/api/record/confirm",
        headers={**auth_headers, "Idempotency-Key": f"rd-{uuid4()}"},
        json={
            "direction": "expense",
            "amount": "100.00",
            "title": "研发材料",
            "category": "研发支出-费用化支出",
            "occurDate": "2026-06-16",
        },
    )
    assert missing.status_code == 422
    assert "研发项目" in missing.text

    project = _active_project(client, auth_headers)
    no_basis = client.post(
        "/api/record/confirm",
        headers={**auth_headers, "Idempotency-Key": f"rd-{uuid4()}"},
        json={
            "direction": "expense",
            "amount": "60.00",
            "title": "研发共用云服务",
            "category": "研发支出-费用化支出",
            "occurDate": "2026-06-16",
            "rdProjectId": project["id"],
            "rdExpenseType": "other",
            "rdOriginalAmount": "100.00",
            "rdAllocationMethod": "usage",
        },
    )
    assert no_basis.status_code == 422
    assert "分摊依据" in no_basis.text


def test_rd_project_rejects_invalid_date_range(client, auth_headers):
    response = client.post(
        "/api/rd/projects",
        headers=auth_headers,
        json={
            "code": f"RD-{uuid4().hex[:8]}",
            "name": "日期错误项目",
            "startDate": "2026-07-01",
            "endDate": "2026-06-01",
            "status": "active",
        },
    )
    assert response.status_code == 422
    assert "结束日期" in response.text


def test_salary_accrual_requires_and_uses_project_timesheet(client, auth_headers):
    project = _active_project(client, auth_headers, start="2026-01-01", end="2028-12-31")
    employee = client.post(
        "/api/salary/employees",
        headers=auth_headers,
        json={
            "name": f"研发员工-{uuid4().hex[:5]}",
            "department": "研发部",
            "base_salary": "6000.00",
            "social_security_base": "4000.00",
        },
    )
    assert employee.status_code == 200, employee.text
    employee_id = employee.json()["id"]
    month = f"2027-{(int(uuid4().hex[:2], 16) % 12) + 1:02d}"
    sheet = client.get(f"/api/salary/sheets/{month}", headers=auth_headers)
    assert sheet.status_code == 200, sheet.text

    blocked = client.post(f"/api/salary/sheets/{month}/accrue", headers=auth_headers)
    assert blocked.status_code == 400
    assert "必须登记研发项目工时" in blocked.text

    allocation = client.put(
        f"/api/salary/sheets/{month}/rd-allocations",
        headers=auth_headers,
        json={
            "allocations": [{
                "employeeId": employee_id,
                "projectId": project["id"],
                "allocationRate": "1.0",
                "allocationBasis": "研发工时表：本月全部工时投入该项目",
            }]
        },
    )
    assert allocation.status_code == 200, allocation.text
    accrued = client.post(f"/api/salary/sheets/{month}/accrue", headers=auth_headers)
    assert accrued.status_code == 200, accrued.text
    voucher_id = accrued.json()["accrual_voucher_id"]
    entries = client.get("/api/record/entries", headers=auth_headers).json()["entries"]
    voucher = next(entry for entry in entries if entry["id"] == voucher_id)
    rd_line = next(line for line in voucher["voucherLines"]["debit"] if line["accountCode"] == "530101")
    assert rd_line["rdDetail"]["projectId"] == project["id"]
    assert rd_line["rdDetail"]["allocationMethod"] == "timesheet"


def test_asset_depreciation_splits_rd_and_admin_usage(client, auth_headers):
    project = _active_project(client, auth_headers, start="2026-01-01", end="2028-12-31")
    asset = client.post(
        "/api/asset/assets",
        headers=auth_headers,
        json={
            "name": f"研发测试设备-{uuid4().hex[:5]}",
            "originalValue": "1200.00",
            "purchaseDate": "2026-05-01",
            "category": "电子设备",
            "usefulLife": 1,
            "residualValueRate": "0",
            "oneTimeDepreciation": False,
            "rdProjectId": project["id"],
            "rdUseRate": "0.60",
            "rdAllocationBasis": "设备使用日志：研发使用时长占60%",
        },
    )
    assert asset.status_code == 200, asset.text
    result = client.post("/api/asset/depreciation/run?period=2026-06", headers=auth_headers)
    assert result.status_code == 200, result.text
    entry_id = next(item["voucher_ref"] for item in result.json()["entries"] if item["asset_id"] == asset.json()["id"])
    entries = client.get("/api/record/entries", headers=auth_headers).json()["entries"]
    voucher = next(entry for entry in entries if entry["id"] == entry_id)
    debit = voucher["voucherLines"]["debit"]
    rd_line = next(line for line in debit if line["accountCode"] == "530103")
    admin_line = next(line for line in debit if line["accountCode"] == "5699")
    assert rd_line["amount"] == "60.00"
    assert rd_line["rdDetail"]["projectId"] == project["id"]
    assert admin_line["amount"] == "40.00"


def test_invoice_import_requires_and_applies_rd_detail(client, auth_headers):
    invoice_number = f"RDINV-{uuid4().hex[:10]}"
    base_row = {
        "invoice_number": invoice_number,
        "invoice_date": "2026-06-20",
        "amount": "800.00",
        "seller_name": "研发材料供应商",
        "direction": "expense",
        "category": "研发支出-费用化支出",
    }
    blocked = client.post(
        "/api/invoice/import",
        headers=auth_headers,
        json={"classify": False, "rows": [base_row]},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["imported"] == 0
    assert "研发项目" in blocked.json()["entries"][0]["error"]

    project = _active_project(client, auth_headers)
    imported = client.post(
        "/api/invoice/import",
        headers=auth_headers,
        json={
            "classify": False,
            "rows": [{
                **base_row,
                "rdProjectId": project["id"],
                "rdExpenseType": "direct_input",
            }],
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    entry_id = imported.json()["entries"][0]["id"]
    entries = client.get("/api/record/entries", headers=auth_headers).json()["entries"]
    voucher = next(entry for entry in entries if entry["id"] == entry_id)
    rd_line = voucher["voucherLines"]["debit"][0]
    assert rd_line["accountCode"] == "530102"
    assert rd_line["rdDetail"]["projectId"] == project["id"]


def test_draft_reclassification_rebuilds_voucher_with_rd_detail(client, auth_headers):
    imported = client.post(
        "/api/invoice/import",
        headers=auth_headers,
        json={
            "classify": False,
            "rows": [{
                "invoice_number": f"RECLASS-{uuid4().hex[:10]}",
                "invoice_date": "2026-06-21",
                "amount": "500.00",
                "seller_name": "技术服务供应商",
                "direction": "expense",
                "category": "信息技术服务费",
            }],
        },
    )
    assert imported.status_code == 200, imported.text
    entry_id = imported.json()["entries"][0]["id"]

    blocked = client.patch(
        f"/api/record/entries/{entry_id}",
        headers=auth_headers,
        json={"category": "研发支出-费用化支出", "direction": "expense"},
    )
    assert blocked.status_code == 422
    assert "研发项目" in blocked.text

    project = _active_project(client, auth_headers)
    updated = client.patch(
        f"/api/record/entries/{entry_id}",
        headers=auth_headers,
        json={
            "category": "研发支出-费用化支出",
            "direction": "expense",
            "rdProjectId": project["id"],
            "rdExpenseType": "outsourced",
            "rdOriginalAmount": "500.00",
            "rdAllocationMethod": "direct",
            "rdAllocationBasis": "技术开发合同 RD-2026-01",
        },
    )
    assert updated.status_code == 200, updated.text
    rd_line = updated.json()["entry"]["voucherLines"]["debit"][0]
    assert rd_line["accountCode"] == "530106"
    assert rd_line["rdDetail"]["projectId"] == project["id"]
    assert rd_line["rdDetail"]["allocationBasis"] == "技术开发合同 RD-2026-01"
