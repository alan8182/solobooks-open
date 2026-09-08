from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, DUPLICATE_SOURCE, NOT_FOUND
from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine, RdEvidenceDocument, RdJournalLineDetail, RdProject
from services.accounting_core_service import (
    POSTING_ROLES,
    RD_ACCOUNT_BY_TYPE,
    RD_ACCOUNT_NAMES,
    _entry_lines,
    create_entry,
    ensure_tenant,
    get_journal,
    record_audit,
    require_role,
    reverse_journal,
    serialize_journal,
    update_draft,
)


RD_EXPENSE_TYPES = [
    {"value": "personnel", "label": "人员人工", "accountCode": "530101"},
    {"value": "direct_input", "label": "直接投入", "accountCode": "530102"},
    {"value": "depreciation_amortization", "label": "折旧与摊销", "accountCode": "530103"},
    {"value": "design", "label": "设计费", "accountCode": "530104"},
    {"value": "testing", "label": "试验费", "accountCode": "530105"},
    {"value": "outsourced", "label": "委外研发费", "accountCode": "530106"},
    {"value": "other", "label": "其他研发费用", "accountCode": "530199"},
]
RD_POLICY_VERSION = "cn-rd-subsidy-generic-2026-v1"
RD_EVIDENCE_TYPES = [
    {"value": "initiation", "label": "立项资料"},
    {"value": "invoice", "label": "发票"},
    {"value": "receipt", "label": "收据"},
    {"value": "contract", "label": "合同"},
    {"value": "timesheet", "label": "工时表"},
    {"value": "material_issue", "label": "领料单"},
    {"value": "usage_log", "label": "设备/资源使用记录"},
    {"value": "acceptance", "label": "验收资料"},
    {"value": "other", "label": "其他证据"},
]


def serialize_project(project: RdProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "code": project.code,
        "name": project.name,
        "manager": project.manager,
        "startDate": project.start_date,
        "endDate": project.end_date,
        "status": project.status,
        "initiationDocumentStatus": project.initiation_document_status,
        "notes": project.notes,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
    }


def serialize_evidence(document: RdEvidenceDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "projectId": document.project_id,
        "entryId": document.journal_entry_id,
        "documentType": document.document_type,
        "referenceNo": document.reference_no,
        "fileUrl": document.file_url,
        "status": document.status,
        "notes": document.notes,
        "createdAt": document.created_at,
    }


async def list_evidence_documents(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    *,
    project_id: str | None = None,
    entry_id: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    query = select(RdEvidenceDocument).where(RdEvidenceDocument.company_id == company_id)
    if project_id:
        query = query.where(RdEvidenceDocument.project_id == project_id)
    if entry_id:
        query = query.where(RdEvidenceDocument.journal_entry_id == entry_id)
    rows = (await session.execute(query.order_by(RdEvidenceDocument.created_at.desc()))).scalars().all()
    return [serialize_evidence(row) for row in rows]


async def create_evidence_document(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, {"owner", "accountant", "preparer"})
    project_id = str(payload.get("projectId") or "").strip()
    project = await session.get(RdProject, project_id)
    if project is None or project.company_id != company_id:
        raise AccountingError(NOT_FOUND, "研发项目不存在", 404)
    entry_id = str(payload.get("entryId") or "").strip() or None
    if entry_id:
        entry = await session.get(JournalEntry, entry_id)
        if entry is None or entry.company_id != company_id:
            raise AccountingError(NOT_FOUND, "关联凭证不存在", 404)
    reference_no = str(payload.get("referenceNo") or "").strip() or None
    file_url = str(payload.get("fileUrl") or "").strip() or None
    if not reference_no and not file_url:
        raise AccountingError("RD_EVIDENCE_REFERENCE_REQUIRED", "证据编号或文件链接至少填写一项", 422)
    document = RdEvidenceDocument(
        id=str(uuid4()), company_id=company_id, project_id=project_id,
        journal_entry_id=entry_id, document_type=str(payload.get("documentType") or "other"),
        reference_no=reference_no, file_url=file_url,
        status=str(payload.get("status") or "available"),
        notes=str(payload.get("notes") or "").strip() or None, created_by=user_id,
    )
    session.add(document)
    await session.flush()
    await record_audit(
        session, company_id=company_id, user_id=user_id, action="create_rd_evidence",
        entity_type="rd_evidence", entity_id=document.id, request_id=request_id,
        after=serialize_evidence(document),
    )
    return serialize_evidence(document)


async def list_projects(session: AsyncSession, company_id: str, user_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    query = select(RdProject).where(RdProject.company_id == company_id)
    if not include_archived:
        query = query.where(RdProject.status != "archived")
    projects = (await session.execute(query.order_by(RdProject.code))).scalars().all()
    return [serialize_project(project) for project in projects]


async def create_project(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, {"owner", "accountant", "preparer"})
    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not code or not name:
        raise AccountingError("INVALID_RD_PROJECT", "研发项目编号和名称不能为空", 422)
    duplicate = (
        await session.execute(
            select(RdProject.id).where(RdProject.company_id == company_id, RdProject.code == code)
        )
    ).scalar_one_or_none()
    if duplicate:
        raise AccountingError(DUPLICATE_SOURCE, "研发项目编号已存在", 409, {"code": code})
    start_date = payload["startDate"]
    end_date = payload.get("endDate")
    if end_date and end_date < start_date:
        raise AccountingError("INVALID_RD_PROJECT_DATES", "项目结束日期不能早于开始日期", 422)
    project = RdProject(
        id=str(uuid4()),
        company_id=company_id,
        code=code,
        name=name,
        manager=str(payload.get("manager") or "").strip() or None,
        start_date=start_date,
        end_date=end_date,
        status=str(payload.get("status") or "draft"),
        initiation_document_status=str(payload.get("initiationDocumentStatus") or "missing"),
        notes=str(payload.get("notes") or "").strip() or None,
        created_by=user_id,
    )
    session.add(project)
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="create_rd_project",
        entity_type="rd_project",
        entity_id=project.id,
        request_id=request_id,
        after=serialize_project(project),
    )
    return serialize_project(project)


async def update_project(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, {"owner", "accountant", "preparer"})
    project = await session.get(RdProject, project_id)
    if project is None or project.company_id != company_id:
        raise AccountingError(NOT_FOUND, "研发项目不存在", 404)
    before = serialize_project(project)
    mapping = {
        "name": "name",
        "manager": "manager",
        "startDate": "start_date",
        "endDate": "end_date",
        "status": "status",
        "initiationDocumentStatus": "initiation_document_status",
        "notes": "notes",
    }
    for source, target in mapping.items():
        if source in payload:
            setattr(project, target, payload[source])
    if project.end_date and project.end_date < project.start_date:
        raise AccountingError("INVALID_RD_PROJECT_DATES", "项目结束日期不能早于开始日期", 422)
    project.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="update_rd_project",
        entity_type="rd_project",
        entity_id=project.id,
        request_id=request_id,
        before=before,
        after=serialize_project(project),
    )
    return serialize_project(project)


async def rd_ledger(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    *,
    start_date: date,
    end_date: date,
    project_id: str | None = None,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    query = (
        select(RdJournalLineDetail, RdProject, JournalLine, JournalEntry)
        .join(RdProject, RdProject.id == RdJournalLineDetail.project_id)
        .join(JournalLine, JournalLine.id == RdJournalLineDetail.journal_line_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            RdJournalLineDetail.company_id == company_id,
            JournalEntry.status.in_(("posted", "reversed")),
            JournalEntry.occurred_on >= start_date,
            JournalEntry.occurred_on <= end_date,
        )
        .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no, JournalLine.line_no)
    )
    if project_id:
        query = query.where(RdJournalLineDetail.project_id == project_id)
    rows = (await session.execute(query)).all()
    type_labels = {item["value"]: item["label"] for item in RD_EXPENSE_TYPES}
    items = []
    total = 0
    for detail, project, line, entry in rows:
        signed_amount = detail.rd_amount if line.direction == "debit" else -detail.rd_amount
        total += signed_amount
        items.append({
            "date": entry.occurred_on,
            "voucherNo": entry.voucher_no,
            "entryId": entry.id,
            "summary": entry.summary,
            "projectId": project.id,
            "projectCode": project.code,
            "projectName": project.name,
            "expenseType": detail.expense_type,
            "expenseTypeLabel": type_labels[detail.expense_type],
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": money_text(signed_amount),
            "originalAmount": money_text(detail.original_amount),
            "allocationRate": str(detail.allocation_rate),
            "allocationMethod": detail.allocation_method,
            "allocationBasis": detail.allocation_basis,
            "notes": detail.notes,
        })
    return {"items": items, "total": money_text(total), "startDate": start_date, "endDate": end_date}


async def unallocated_rd_lines(
    session: AsyncSession,
    company_id: str,
    user_id: str,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    rows = (
        await session.execute(
            select(JournalEntry, JournalLine)
            .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .outerjoin(RdJournalLineDetail, RdJournalLineDetail.journal_line_id == JournalLine.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.status.in_(("draft", "pending_review", "posted")),
                JournalEntry.entry_type != "reversal",
                JournalLine.company_id == company_id,
                JournalLine.account_code.startswith("5301"),
                RdJournalLineDetail.journal_line_id.is_(None),
            )
            .order_by(JournalEntry.occurred_on.desc(), JournalEntry.voucher_no.desc())
        )
    ).all()
    items = [
        {
            "entryId": entry.id,
            "status": entry.status,
            "date": entry.occurred_on,
            "voucherNo": entry.voucher_no,
            "summary": entry.summary,
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": money_text(line.amount if line.direction == "debit" else -line.amount),
            "reason": "缺少研发项目或费用类别",
        }
        for entry, line in rows
    ]
    return {"items": items, "count": len(items)}


async def correct_unallocated_entry(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    entry_id: str,
    allocations: list[dict[str, Any]],
    *,
    correction_date: date,
    reason: str,
    request_id: str,
) -> dict[str, Any]:
    """Replace an unallocated 5301 line with one or more project allocations."""
    entry = await get_journal(session, entry_id, company_id=company_id, lock=True)
    if entry.entry_type == "reversal" or entry.status == "reversed":
        raise AccountingError("RD_CORRECTION_NOT_ALLOWED", "冲销凭证或已冲销凭证不能再次归集", 409)
    if not allocations:
        raise AccountingError("RD_ALLOCATIONS_REQUIRED", "至少需要一条研发项目分配", 422)
    if entry.status == "posted":
        await require_role(session, company_id, user_id, POSTING_ROLES)

    original_lines = await _entry_lines(session, entry.id)
    existing_detail_ids = set((await session.execute(
        select(RdJournalLineDetail.journal_line_id).where(
            RdJournalLineDetail.journal_line_id.in_([line.id for line in original_lines])
        )
    )).scalars().all()) if original_lines else set()
    target_lines = [
        line for line in original_lines
        if line.direction == "debit" and line.account_code.startswith("5301") and line.id not in existing_detail_ids
    ]
    if not target_lines:
        raise AccountingError("RD_UNALLOCATED_LINE_NOT_FOUND", "该凭证没有可补充归集的研发费用行", 409)
    target_total = sum((line.amount for line in target_lines), ZERO)

    normalized_allocations: list[dict[str, Any]] = []
    allocation_total = ZERO
    for raw in allocations:
        project_id = str(raw.get("projectId") or "").strip()
        expense_type = str(raw.get("expenseType") or "").strip()
        amount = money(raw.get("amount") or ZERO)
        method = str(raw.get("allocationMethod") or "direct").strip()
        basis = str(raw.get("allocationBasis") or "").strip() or None
        if not project_id or expense_type not in RD_ACCOUNT_BY_TYPE or amount <= ZERO:
            raise AccountingError("INVALID_RD_ALLOCATION", "研发项目、费用类别和分配金额必须有效", 422)
        if len(allocations) > 1 and not basis:
            raise AccountingError("RD_ALLOCATION_BASIS_REQUIRED", "多项目拆分必须填写每条分摊依据", 422)
        allocation_total += amount
        normalized_allocations.append({
            "projectId": project_id,
            "expenseType": expense_type,
            "amount": amount,
            "allocationMethod": method,
            "allocationBasis": basis,
            "notes": str(raw.get("notes") or "").strip() or None,
        })
    if allocation_total != target_total:
        raise AccountingError(
            "RD_ALLOCATION_TOTAL_MISMATCH",
            "项目分配合计必须等于待归集研发金额",
            422,
            {"expected": money_text(target_total), "actual": money_text(allocation_total)},
        )

    target_ids = {line.id for line in target_lines}
    rebuilt_lines: list[dict[str, Any]] = []
    allocations_inserted = False
    for line in original_lines:
        if line.id in target_ids:
            if allocations_inserted:
                continue
            for allocation in normalized_allocations:
                expense_type = allocation["expenseType"]
                rebuilt_lines.append({
                    "direction": "debit",
                    "accountCode": RD_ACCOUNT_BY_TYPE[expense_type],
                    "accountName": RD_ACCOUNT_NAMES[expense_type],
                    "amount": allocation["amount"],
                    "rdDetail": {
                        "projectId": allocation["projectId"],
                        "expenseType": expense_type,
                        "originalAmount": target_total,
                        "allocationMethod": allocation["allocationMethod"],
                        "allocationBasis": allocation["allocationBasis"],
                        "notes": allocation["notes"],
                    },
                })
            allocations_inserted = True
            continue
        rebuilt_lines.append({
            "direction": line.direction,
            "accountCode": line.account_code,
            "accountName": line.account_name,
            "amount": line.amount,
            "auxiliary": line.auxiliary,
        })

    before_status = entry.status
    if entry.status in {"draft", "pending_review"}:
        corrected = await update_draft(
            session,
            entry.id,
            {"voucherLines": {
                "debit": [line for line in rebuilt_lines if line["direction"] == "debit"],
                "credit": [line for line in rebuilt_lines if line["direction"] == "credit"],
            }},
            company_id=company_id,
            user_id=user_id,
            request_id=request_id,
        )
        reversal = None
    else:
        reversal = await reverse_journal(
            session,
            entry.id,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}-reverse",
            reason=reason or "补充研发项目与费用类别",
            reversal_date=correction_date,
        )
        metadata = dict(entry.metadata_json or {})
        for key in ("id", "voucherRef", "voucherStatus", "voucherLines", "dedupFingerprint", "sourceTransactionId"):
            metadata.pop(key, None)
        corrected = await create_entry(
            session,
            {
                **metadata,
                "title": f"更正：{entry.summary}",
                "occurDate": correction_date,
                "category": "研发支出-费用化支出",
                "sourceType": "rd_correction",
                "correctionOf": entry.id,
                "voucherAuthority": "system",
                "voucherLines": {
                    "debit": [line for line in rebuilt_lines if line["direction"] == "debit"],
                    "credit": [line for line in rebuilt_lines if line["direction"] == "credit"],
                },
            },
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}-corrected",
            confirm=True,
        )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="correct_rd_allocation",
        entity_type="journal_entry",
        entity_id=entry.id,
        request_id=request_id,
        before={"status": before_status, "unallocatedAmount": money_text(target_total)},
        after={"correctedEntryId": corrected.id, "reversalEntryId": reversal.id if reversal else None},
        reason=reason or "补充研发归集",
    )
    return {
        "originalEntryId": entry.id,
        "reversalEntryId": reversal.id if reversal else None,
        "correctedEntry": await serialize_journal(session, corrected),
    }


async def rd_reconciliation_summary(
    session: AsyncSession,
    company_id: str,
    user_id: str,
    *,
    start_date: date,
    end_date: date,
    policy_version: str = RD_POLICY_VERSION,
    region: str | None = None,
    program_batch: str | None = None,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    if policy_version != RD_POLICY_VERSION:
        raise AccountingError("INVALID_RD_POLICY_VERSION", "不支持的研发政策规则版本", 422)
    ledger = await rd_ledger(
        session, company_id, user_id, start_date=start_date, end_date=end_date
    )
    project_totals: dict[str, dict[str, Any]] = {}
    type_totals = {item["value"]: {**item, "amount": ZERO} for item in RD_EXPENSE_TYPES}
    evidence_issues: list[dict[str, Any]] = []
    seen_issues: set[tuple[str, str]] = set()
    project_rows = (await session.execute(
        select(RdProject).where(RdProject.company_id == company_id)
    )).scalars().all()
    projects = {project.id: project for project in project_rows}
    entry_rows = (await session.execute(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id.in_([item["entryId"] for item in ledger["items"]] or ["__none__"]),
        )
    )).scalars().all()
    entry_metadata = {entry.id: dict(entry.metadata_json or {}) for entry in entry_rows}
    evidence_rows = (await session.execute(
        select(RdEvidenceDocument).where(
            RdEvidenceDocument.company_id == company_id,
            RdEvidenceDocument.status == "available",
        )
    )).scalars().all()
    evidence_by_entry: dict[str, set[str]] = {}
    evidence_by_project: dict[str, set[str]] = {}
    for document in evidence_rows:
        evidence_by_project.setdefault(document.project_id, set()).add(document.document_type)
        if document.journal_entry_id:
            evidence_by_entry.setdefault(document.journal_entry_id, set()).add(document.document_type)

    def add_issue(item: dict[str, Any], issue_type: str, message: str) -> None:
        key = (item["entryId"], issue_type)
        if key in seen_issues:
            return
        seen_issues.add(key)
        evidence_issues.append({
            "entryId": item["entryId"], "voucherNo": item.get("voucherNo"),
            "projectId": item["projectId"], "projectCode": item["projectCode"],
            "type": issue_type, "message": message, "amount": item["amount"],
        })

    for item in ledger["items"]:
        amount = money(item["amount"])
        project_total = project_totals.setdefault(item["projectId"], {
            "projectId": item["projectId"], "projectCode": item["projectCode"],
            "projectName": item["projectName"], "amount": ZERO,
        })
        project_total["amount"] += amount
        type_totals[item["expenseType"]]["amount"] += amount
        project = projects.get(item["projectId"])
        project_evidence = evidence_by_project.get(item["projectId"], set())
        entry_evidence = evidence_by_entry.get(item["entryId"], set())
        if project and project.initiation_document_status != "complete" and "initiation" not in project_evidence:
            add_issue(item, "project_document", "研发项目立项资料不完整")
        if item["allocationMethod"] != "direct" and not item.get("allocationBasis"):
            add_issue(item, "allocation_basis", "非直接归集缺少分摊依据")
        if item["expenseType"] == "personnel" and item["allocationMethod"] != "timesheet" and "timesheet" not in entry_evidence:
            add_issue(item, "timesheet", "人员人工缺少工时归集标识")
        if item["expenseType"] == "outsourced" and not item.get("allocationBasis") and "contract" not in entry_evidence:
            add_issue(item, "outsourced_contract", "委外研发费缺少合同或验收依据")
        metadata = entry_metadata.get(item["entryId"], {})
        if item["expenseType"] in {"direct_input", "design", "testing", "outsourced", "other"}:
            if (
                metadata.get("billingStatus") not in {"has_invoice", "has_receipt"}
                and not metadata.get("invoiceNumber")
                and not entry_evidence.intersection({"invoice", "receipt", "contract", "material_issue", "acceptance"})
            ):
                add_issue(item, "source_document", "缺少发票、收据或来源单据标识")

    gl_total = (await session.execute(
        select(func.coalesce(func.sum(
            JournalLine.amount * case((JournalLine.direction == "debit", 1), else_=-1)
        ), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.status.in_(("posted", "reversed")),
            JournalEntry.occurred_on >= start_date,
            JournalEntry.occurred_on <= end_date,
            JournalLine.account_code.startswith("5301"),
        )
    )).scalar_one()
    ledger_total = money(ledger["total"])
    gl_total = money(gl_total)
    difference = gl_total - ledger_total
    unallocated = await unallocated_rd_lines(session, company_id, user_id)
    policy_status = "suggested" if not evidence_issues and difference == ZERO and unallocated["count"] == 0 else "waiting_review"
    issue_messages_by_entry: dict[str, list[str]] = {}
    for issue in evidence_issues:
        issue_messages_by_entry.setdefault(issue["entryId"], []).append(issue["message"])
    policy_review_items = [{
        "entryId": item["entryId"], "voucherNo": item.get("voucherNo"),
        "projectCode": item["projectCode"], "expenseTypeLabel": item["expenseTypeLabel"],
        "amount": item["amount"],
        "status": "waiting_review" if item["entryId"] in issue_messages_by_entry else "suggested",
        "reason": "；".join(issue_messages_by_entry.get(item["entryId"], [])) or "会计归集及通用资料检查通过，仍需按专项政策复核",
    } for item in ledger["items"]]
    return {
        "policyVersion": policy_version,
        "policyLabel": "通用研发补助资料准备规则（2026版）",
        "policyDisclaimer": "仅用于资料完整性预审，最终资格以申报地区和批次正式政策为准。",
        "region": region,
        "programBatch": program_batch,
        "startDate": start_date,
        "endDate": end_date,
        "ledgerTotal": money_text(ledger_total),
        "generalLedgerTotal": money_text(gl_total),
        "difference": money_text(difference),
        "isReconciled": difference == ZERO,
        "projectSummary": [{**item, "amount": money_text(item["amount"])} for item in project_totals.values()],
        "expenseTypeSummary": [{**item, "amount": money_text(item["amount"])} for item in type_totals.values()],
        "evidenceIssues": evidence_issues,
        "unallocatedCount": unallocated["count"],
        "policyStatus": policy_status,
        "policyReviewItems": policy_review_items,
        "evidenceDocuments": [serialize_evidence(document) for document in evidence_rows],
    }
