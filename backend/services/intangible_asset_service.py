# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产服务：卡片 CRUD、提交审批（凭证驱动）、摊销计划预生成。

设计方案：《无形资产模块设计方案.md》第 4-6 章。硬规则：
- 数据隔离只按 company_id，不引入 user_id 过滤；
- 资产 status 只由凭证 / 处置单驱动，任何 API 不接受直接改状态；
- 取得凭证 confirm=True：owner_self_post 直接 posted，maker_checker 落
  pending_review——绝不留 draft（close_period 会自动过账草稿，绕过复核）；
- 摊销计划在提交时预生成全周期 planned 行，按 available_for_use_date 当月起摊。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from core.intangible_categories import (
    ACQUISITION_TYPES,
    get_category,
    validate_life_months,
)
from core.money import ZERO, money, money_text, rate
from db.models import (
    Company,
    IntangibleAmortizationSchedule,
    IntangibleAsset,
    JournalEntry,
    JournalLine,
    RdProject,
)
from services.accounting_core_service import (
    POSTING_ROLES,
    WRITE_ROLES,
    _advisory_lock,
    _sync_projection,
    approve_journal,
    create_entry,
    ensure_tenant,
    get_journal,
    post_journal,
    record_audit,
    require_role,
    submit_journal_for_review,
    update_draft,
)

ASSET_CREDIT_ACCOUNTS = {
    "company_account": ("1002", "银行存款"),
    "account_payable": ("2202", "应付账款"),
    "personal_advance": ("2241", "其他应付款"),
}

# 更精确的损益叶子科目（5301/5711 非末级，不可直接使用）
INTANGIBLE_ACCOUNT = ("1701", "无形资产")


def _parse_date(value: Any, *, field: str) -> date:
    raw = str(value or "").strip()
    if len(raw) != 10:
        raise AccountingError("INVALID_DATE", f"{field}格式无效，应为 YYYY-MM-DD", 422)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise AccountingError("INVALID_DATE", f"{field}格式无效，应为 YYYY-MM-DD", 422) from exc


def _period_of(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _add_months(period: str, months: int) -> str:
    year, month = int(period[:4]), int(period[5:7])
    total = (year * 12 + (month - 1)) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


# ── 序列化 ────────────────────────────────────────────────────────────────

def serialize_asset(asset: IntangibleAsset) -> dict[str, Any]:
    accumulated = money(asset.accumulated_amortization or ZERO)
    original = money(asset.original_value)
    if asset.last_amortized_period and asset.amortization_end_period:
        last = int(asset.last_amortized_period[:4]) * 12 + int(asset.last_amortized_period[5:7])
        end = int(asset.amortization_end_period[:4]) * 12 + int(asset.amortization_end_period[5:7])
        remaining = max(0, end - last)
    else:
        remaining = int(asset.useful_life_months)
    return {
        "id": asset.id,
        "code": asset.code,
        "name": asset.name,
        "category": asset.category,
        "categoryName": get_category(asset.category).name,
        "acquisitionType": asset.acquisition_type,
        "acquisitionDate": asset.acquisition_date.isoformat(),
        "availableForUseDate": asset.available_for_use_date.isoformat(),
        "originalValue": original,
        "residualValue": money(asset.residual_value or ZERO),
        "usefulLifeMonths": asset.useful_life_months,
        "amortizationMethod": asset.amortization_method,
        "accumulatedAmortization": accumulated,
        "netValue": money(original - accumulated),
        "remainingMonths": remaining,
        "lastAmortizedPeriod": asset.last_amortized_period,
        "amortizationEndPeriod": asset.amortization_end_period,
        "status": asset.status,
        "sourceEntryId": asset.source_entry_id,
        "legalCertNo": asset.legal_cert_no,
        "grantOrg": asset.grant_org,
        "expiryDate": asset.expiry_date.isoformat() if asset.expiry_date else None,
        "rdProjectId": asset.rd_project_id,
        "rdUseRate": asset.rd_use_rate,
        "rdAllocationBasis": asset.rd_allocation_basis,
        "departmentId": asset.department_id,
        "custodian": asset.custodian,
        "disposedOn": asset.disposed_on.isoformat() if asset.disposed_on else None,
        "disposalType": asset.disposal_type,
        "createdAt": asset.created_at.isoformat() if asset.created_at else "",
        "updatedAt": asset.updated_at.isoformat() if asset.updated_at else "",
    }


# ── 校验 ─────────────────────────────────────────────────────────────────

def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """公共校验，返回规范化字段。create 与 update 复用。"""
    category = get_category(str(payload.get("category") or ""))
    original = money(payload.get("originalValue") or ZERO, allow_negative=False)
    if original <= ZERO:
        raise AccountingError("INVALID_ORIGINAL_VALUE", "无形资产原值必须大于零", 422)

    residual = money(payload.get("residualValue") or ZERO, allow_negative=False)
    residual_rate = payload.get("residualRate")
    if residual == ZERO and residual_rate is not None:
        residual = money(original * rate(residual_rate))
    if residual >= original:
        raise AccountingError("INVALID_RESIDUAL_VALUE", "预计净残值必须小于原值（小企业会计准则默认视为零）", 422)

    life_months = int(payload.get("usefulLifeMonths") or 0)
    if life_months <= 0:
        raise AccountingError("INVALID_LIFE", "摊销年限（月）必须大于零", 422)
    validate_life_months(category.code, life_months)

    acquisition_date = _parse_date(payload.get("acquisitionDate"), field="取得日期")
    available_date = _parse_date(payload.get("availableForUseDate") or payload.get("acquisitionDate"), field="可供使用日期")
    if available_date < acquisition_date:
        raise AccountingError("INVALID_AVAILABLE_DATE", "可供使用日期不得早于取得日期", 422)

    rd_project_id = str(payload.get("rdProjectId") or "").strip()
    rd_rate = payload.get("rdUseRate")
    rd_basis = str(payload.get("rdAllocationBasis") or "").strip()
    if rd_project_id or (rd_rate is not None and rate(rd_rate) > ZERO) or rd_basis:
        if not rd_project_id or rd_rate is None or not rd_basis:
            raise AccountingError("RD_ASSET_ALLOCATION_INCOMPLETE", "研发使用资产必须填写项目、使用比例和分摊依据", 422)
        if not (ZERO < rate(rd_rate) <= Decimal("1")):
            raise AccountingError("INVALID_RD_ALLOCATION", "研发使用比例必须大于0且不超过100%", 422)

    return {
        "category": category,
        "original": original,
        "residual": residual,
        "life_months": life_months,
        "acquisition_date": acquisition_date,
        "available_date": available_date,
        "rd_project_id": rd_project_id or None,
        "rd_use_rate": rate(rd_rate) if rd_rate is not None else None,
        "rd_allocation_basis": rd_basis or None,
    }


async def _validate_rd_project(session: AsyncSession, company_id: str, project_id: str | None) -> None:
    if not project_id:
        return
    project = await session.get(RdProject, project_id)
    if project is None or project.company_id != company_id:
        raise AccountingError("INVALID_RD_PROJECT", "请选择当前企业有效的研发项目", 422)


async def _next_code(session: AsyncSession, company_id: str, acquisition_date: date) -> str:
    prefix = f"IA-{acquisition_date.year:04d}-"
    await _advisory_lock(session, f"intangible-code:{company_id}:{acquisition_date.year}")
    rows = (
        await session.execute(
            select(IntangibleAsset.code).where(
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.code.like(f"{prefix}%"),
                IntangibleAsset.deleted_at.is_(None),  # 软删除编码可复用
            )
        )
    ).scalars().all()
    max_seq = 0
    for code in rows:
        try:
            max_seq = max(max_seq, int(code[len(prefix):]))
        except ValueError:
            continue
    return f"{prefix}{max_seq + 1:04d}"


# ── 摊销计划预生成 ────────────────────────────────────────────────────────

def build_schedule_rows(
    asset: IntangibleAsset,
    *,
    life_months: int | None = None,
    residual_value: Decimal | None = None,
    start_period: str | None = None,
    sequence_start: int = 1,
    accumulated_start: Decimal = ZERO,
    plan_version: int = 1,
) -> list[dict[str, Any]]:
    """planned 行生成：当月起摊、末期尾差兜平、Σamount == 基数。

    默认参数生成全周期计划（提交/草稿重建用）；变更单通过 start_period /
    sequence_start / accumulated_start / plan_version 只重建未执行期间。
    """
    n = int(life_months if life_months is not None else asset.useful_life_months)
    residual = money(residual_value if residual_value is not None else (asset.residual_value or ZERO))
    base = money(money(asset.original_value) - residual)
    remaining = n - (sequence_start - 1)
    if remaining <= 0:
        raise AccountingError("INVALID_SCHEDULE", "剩余摊销期数必须大于零", 422)
    if base - accumulated_start <= ZERO:
        raise AccountingError("INVALID_SCHEDULE", "剩余应摊销总额必须大于零", 422)
    monthly = money((base - accumulated_start) / Decimal(remaining))
    first_period = start_period or _period_of(asset.available_for_use_date)
    rows: list[dict[str, Any]] = []
    accumulated = money(accumulated_start)
    for seq in range(sequence_start, n + 1):
        # 末期兜平：最后一期 = 基数 − 已写入累计
        amount = money(base - accumulated) if seq == n else monthly
        if amount <= ZERO:
            raise AccountingError("INVALID_SCHEDULE", "摊销计划出现非正数期次，请检查年限与残值", 422)
        before = accumulated
        accumulated = money(accumulated + amount)
        rows.append(
            {
                "company_id": asset.company_id,
                "asset_id": asset.id,
                "period_start": _add_months(first_period, seq - sequence_start),
                "sequence_no": seq,
                "amount": amount,
                "accumulated_before": before,
                "accumulated_after": accumulated,
                "status": "planned",
                "plan_version": plan_version,
            }
        )
    return rows


async def regenerate_schedule(session: AsyncSession, asset: IntangibleAsset) -> None:
    """重建 planned 计划（draft/pending 阶段或计划为空时的补偿入口）。"""
    await session.execute(
        IntangibleAmortizationSchedule.__table__.delete().where(
            IntangibleAmortizationSchedule.asset_id == asset.id,
            IntangibleAmortizationSchedule.status == "planned",
        )
    )
    for row in build_schedule_rows(asset):
        session.add(IntangibleAmortizationSchedule(**row))
    asset.amortization_end_period = _add_months(_period_of(asset.available_for_use_date), asset.useful_life_months - 1)
    await session.flush()


# ── 取得凭证 ─────────────────────────────────────────────────────────────

def _acquisition_entry_payload(asset: IntangibleAsset) -> dict[str, Any]:
    original = money(asset.original_value)
    if asset.acquisition_type == "self_developed":
        return {
            "id": f"intangible-cap-{asset.id}",
            "direction": "expense",
            "amount": money_text(original),
            "title": f"研发资本化结转-{asset.name}",
            "category": "无形资产",
            "occurDate": asset.acquisition_date.isoformat(),
            "sourceType": "intangible_capitalization",
            "dedupFingerprint": f"intangible_capitalization:{asset.company_id}:{asset.id}",
            "voucherAuthority": "system",
            "voucherLines": {
                "debit": [{"accountCode": "1701", "accountName": "无形资产", "amount": money_text(original)}],
                "credit": [
                    {"accountCode": "4301002", "accountName": "研发支出-资本化支出", "amount": money_text(original)}
                ],
            },
        }
    if asset.acquisition_type == "investor_contribution":
        credit = [("3001", "实收资本")]
    elif asset.acquisition_type == "donation":
        credit = [("5301003", "捐赠收益")]
    else:
        credit = [ASSET_CREDIT_ACCOUNTS["company_account"]]  # paymentMethod 在提交时改写

    debit = [{"accountCode": "1701", "accountName": "无形资产", "amount": money_text(original)}]
    credit_amount = original
    if asset.acquisition_type == "purchase":
        # 小规模纳税人不抵扣进项税额，无形资产按价税合计入账
        credit = [ASSET_CREDIT_ACCOUNTS[asset.payment_method or "company_account"]]
    return {
        "id": f"intangible-acq-{asset.id}",
        "direction": "expense",
        "amount": money_text(credit_amount),
        "title": f"取得无形资产-{asset.name}",
        "category": "无形资产",
        "occurDate": asset.acquisition_date.isoformat(),
        "sourceType": "intangible_purchase" if asset.acquisition_type == "purchase" else f"intangible_{asset.acquisition_type}",
        "dedupFingerprint": f"intangible_acquisition:{asset.company_id}:{asset.id}",
        "voucherAuthority": "system",
        "voucherLines": {
            "debit": debit,
            "credit": [
                {"accountCode": code, "accountName": name, "amount": money_text(credit_amount)}
                for code, name in credit
            ],
        },
    }


async def _validate_rd_capitalization_balance(session: AsyncSession, company_id: str, amount: Decimal) -> None:
    """资本化结转额不得超过 4301002 已过账余额。"""
    from db.models import JournalLine

    async def _sum_lines(direction: str) -> Decimal:
        value = (
            await session.execute(
                select(sa_func.coalesce(sa_func.sum(JournalLine.amount), 0))
                .select_from(JournalLine)
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == "4301002",
                    JournalLine.direction == direction,
                    JournalEntry.status == "posted",
                )
            )
        ).scalar() or 0
        return Decimal(str(value))

    balance = money(await _sum_lines("debit") - await _sum_lines("credit"))
    if balance < amount:
        raise AccountingError(
            "RD_CAPITALIZATION_BALANCE_INSUFFICIENT",
            f"研发支出-资本化支出余额 {money_text(balance)} 不足以结转 {money_text(amount)}，请先归集研发支出",
            422,
        )


# ── 来源交易 → 无形资产卡片 ─────────────────────────────────────────────

def _suggest_intangible_category(name: str) -> str:
    text = str(name or "")
    rules = (
        (("软件", "系统", "平台", "APP", "小程序"), "software"),
        (("专利",), "patent"),
        (("商标",), "trademark"),
        (("著作权", "版权"), "copyright"),
        (("土地使用权", "土地"), "land_use_right"),
        (("特许", "授权", "加盟"), "franchise"),
        (("技术", "配方", "工艺"), "non_patent_technology"),
    )
    for keywords, code in rules:
        if any(keyword in text for keyword in keywords):
            return code
    return "other"


async def _source_entry_values(
    session: AsyncSession, entry_id: str, company_id: str
) -> dict[str, Any]:
    entry = await session.get(JournalEntry, entry_id)
    if entry is None or entry.company_id != company_id:
        raise AccountingError(NOT_FOUND, "来源凭证不存在", 404)
    lines = (
        await session.execute(
            select(JournalLine).where(
                JournalLine.journal_entry_id == entry_id,
                JournalLine.company_id == company_id,
            )
        )
    ).scalars().all()
    original = money(
        sum(
            (
                money(line.amount)
                for line in lines
                if line.direction == "debit" and line.account_code == INTANGIBLE_ACCOUNT[0]
            ),
            ZERO,
        )
    )
    existing = (
        await session.execute(
            select(IntangibleAsset.id).where(
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.source_entry_id == entry_id,
                IntangibleAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    acquired_on = entry.occurred_on.isoformat()
    credit_line = next((l for l in lines if l.direction == "credit"), None)
    return {
        "sourceEntryId": entry_id,
        "existingAssetId": existing,
        "name": entry.summary,
        "acquisitionDate": acquired_on,
        "availableForUseDate": acquired_on,
        "originalValue": original,
        "suggestedCategory": _suggest_intangible_category(entry.summary),
        "entryStatus": entry.status,
        "entryType": entry.entry_type,
        "voucherNo": entry.voucher_no,
        "summary": entry.summary,
        "sourceAccount": credit_line.account_name if credit_line else "银行/资金账户",
    }


async def ingest_card(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    source_entry_id: str,
    status: str = "active",
    acquisition_type: str = "purchase",
    payment_method: str = "company_account",
) -> IntangibleAsset:
    """把已入账来源凭证接入无形资产卡片，不生成取得凭证。"""
    data = _validate_payload(payload)
    await _validate_rd_project(session, company_id, data["rd_project_id"])
    if acquisition_type not in ACQUISITION_TYPES:
        raise AccountingError("INVALID_ACQUISITION_TYPE", "无效的取得方式", 422)
    existing = (
        await session.execute(
            select(IntangibleAsset.id).where(
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.source_entry_id == source_entry_id,
                IntangibleAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise AccountingError(
            "DUPLICATE_SOURCE",
            "该采购凭证已关联无形资产卡片",
            409,
            {"existingAssetId": existing, "sourceEntryId": source_entry_id},
        )

    code = await _next_code(session, company_id, data["acquisition_date"])
    now = _now()
    asset = IntangibleAsset(
        company_id=company_id,
        code=code,
        name=str(payload.get("name") or "").strip(),
        category=data["category"].code,
        acquisition_type=acquisition_type,
        acquisition_date=data["acquisition_date"],
        available_for_use_date=data["available_date"],
        original_value=data["original"],
        residual_value=data["residual"],
        residual_rate=(data["residual"] / data["original"]) if data["original"] else ZERO,
        useful_life_months=data["life_months"],
        amortization_method="straight_line",
        accumulated_amortization=ZERO,
        status=status,
        source_entry_id=source_entry_id,
        legal_cert_no=str(payload.get("legalCertNo") or "").strip() or None,
        grant_org=str(payload.get("grantOrg") or "").strip() or None,
        expiry_date=_parse_date(payload["expiryDate"], field="权利到期日") if payload.get("expiryDate") else None,
        department_id=str(payload.get("departmentId") or "").strip() or None,
        custodian=str(payload.get("custodian") or "").strip() or None,
        rd_project_id=data["rd_project_id"],
        rd_use_rate=data["rd_use_rate"],
        rd_allocation_basis=data["rd_allocation_basis"],
        payment_method=payment_method,
        tax_life_months=int(payload["taxLifeMonths"]) if payload.get("taxLifeMonths") else None,
        created_by=user_id,
        updated_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    await session.flush()
    await regenerate_schedule(session, asset)
    return asset


async def get_intangible_asset_entry_prefill(
    session: AsyncSession, entry_id: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    return await _source_entry_values(session, entry_id, company_id)


async def create_intangible_asset_from_entry(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> IntangibleAsset:
    """从已过账无形资产采购凭证建卡，不重复确认 1701 和银行支出。"""
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)

    source_entry_id = str(payload.get("sourceEntryId") or "").strip()
    if not source_entry_id:
        raise AccountingError("SOURCE_ENTRY_REQUIRED", "缺少来源凭证", 422)
    source = await _source_entry_values(session, source_entry_id, company_id)
    if source["existingAssetId"]:
        raise AccountingError(
            "DUPLICATE_SOURCE",
            "该采购凭证已关联无形资产卡片",
            409,
            {"existingAssetId": source["existingAssetId"], "sourceEntryId": source_entry_id},
        )
    if source["entryStatus"] != "posted" or source["entryType"] in {"reversal", "opening", "closing"}:
        raise AccountingError(
            "ENTRY_NOT_POSTED",
            "来源凭证尚未过账，不能建立无形资产卡片",
            409,
            {"sourceEntryId": source_entry_id, "entryStatus": source["entryStatus"]},
        )
    reversal_id = await session.scalar(
        select(JournalEntry.id)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id == source_entry_id,
        )
        .limit(1)
    )
    if reversal_id:
        raise AccountingError(
            "ENTRY_REVERSED",
            "来源凭证已冲销，不能建立无形资产卡片",
            409,
            {"sourceEntryId": source_entry_id, "reversalEntryId": reversal_id},
        )
    if source["originalValue"] <= ZERO:
        raise AccountingError(
            "NOT_INTANGIBLE_ASSET_ENTRY",
            "来源凭证没有 1701 无形资产借方金额",
            422,
            {"sourceEntryId": source_entry_id},
        )

    data = _validate_payload(payload)
    if abs(data["original"] - source["originalValue"]) > Decimal("0.01"):
        raise AccountingError(
            "INTANGIBLE_ASSET_VALUE_MISMATCH",
            f"资产原值 {money_text(data['original'])} 与来源凭证 1701 借方 {money_text(source['originalValue'])} 不一致",
            422,
            {
                "sourceEntryId": source_entry_id,
                "originalValue": money_text(source["originalValue"]),
            },
        )

    asset = await ingest_card(
        session,
        payload,
        company_id=company_id,
        user_id=user_id,
        source_entry_id=source_entry_id,
        status="active",
        acquisition_type="purchase",
        payment_method="company_account",
    )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.create_from_entry",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        after=serialize_asset(asset),
        entity_version=asset.version,
    )
    return asset


# ── CRUD ─────────────────────────────────────────────────────────────────

async def create_asset(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    payment_method: str = "company_account",
) -> IntangibleAsset:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    data = _validate_payload(payload)
    await _validate_rd_project(session, company_id, data["rd_project_id"])

    acquisition_type = str(payload.get("acquisitionType") or "purchase")
    if acquisition_type not in ACQUISITION_TYPES:
        raise AccountingError("INVALID_ACQUISITION_TYPE", "无效的取得方式", 422)
    if acquisition_type == "self_developed" and not data["rd_project_id"]:
        raise AccountingError("RD_PROJECT_REQUIRED", "自行研发资本化必须关联研发项目", 422)

    code = await _next_code(session, company_id, data["acquisition_date"])
    now = _now()
    asset = IntangibleAsset(
        company_id=company_id,
        code=code,
        name=str(payload.get("name") or "").strip(),
        category=data["category"].code,
        acquisition_type=acquisition_type,
        acquisition_date=data["acquisition_date"],
        available_for_use_date=data["available_date"],
        original_value=data["original"],
        residual_value=data["residual"],
        residual_rate=(data["residual"] / data["original"]) if data["original"] else ZERO,
        useful_life_months=data["life_months"],
        amortization_method="straight_line",
        accumulated_amortization=ZERO,
        status="draft",
        legal_cert_no=str(payload.get("legalCertNo") or "").strip() or None,
        grant_org=str(payload.get("grantOrg") or "").strip() or None,
        expiry_date=_parse_date(payload["expiryDate"], field="权利到期日") if payload.get("expiryDate") else None,
        department_id=str(payload.get("departmentId") or "").strip() or None,
        custodian=str(payload.get("custodian") or "").strip() or None,
        rd_project_id=data["rd_project_id"],
        rd_use_rate=data["rd_use_rate"],
        rd_allocation_basis=data["rd_allocation_basis"],
        payment_method=payment_method,
        tax_life_months=int(payload["taxLifeMonths"]) if payload.get("taxLifeMonths") else None,
        created_by=user_id,
        updated_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.create",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        after=serialize_asset(asset),
        entity_version=asset.version,
    )
    return asset


async def _get_asset(session: AsyncSession, asset_id: str, company_id: str) -> IntangibleAsset:
    asset = (
        await session.execute(
            select(IntangibleAsset).where(
                IntangibleAsset.id == asset_id,
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError(NOT_FOUND, "无形资产不存在", 404)
    return asset


async def get_asset(session: AsyncSession, asset_id: str, *, company_id: str, user_id: str) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    return serialize_asset(await _get_asset(session, asset_id, company_id))


async def update_asset(
    session: AsyncSession, asset_id: str, payload: dict[str, Any], *, company_id: str, user_id: str, request_id: str
) -> IntangibleAsset:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status not in ("draft",):
        raise AccountingError("ASSET_NOT_DRAFT", "仅草稿状态的无形资产可以编辑", 409)

    before = serialize_asset(asset)
    merged = {
        "category": asset.category,
        "originalValue": asset.original_value,
        "residualValue": payload.get("residualValue") if payload.get("residualValue") is not None else asset.residual_value,
        "residualRate": None,
        "usefulLifeMonths": payload.get("usefulLifeMonths") or asset.useful_life_months,
        "acquisitionDate": asset.acquisition_date.isoformat(),
        "availableForUseDate": asset.available_for_use_date.isoformat(),
        "rdProjectId": payload.get("rdProjectId", asset.rd_project_id),
        "rdUseRate": payload.get("rdUseRate", asset.rd_use_rate),
        "rdAllocationBasis": payload.get("rdAllocationBasis", asset.rd_allocation_basis),
    }
    data = _validate_payload(merged)
    await _validate_rd_project(session, company_id, data["rd_project_id"])
    if payload.get("name") is not None:
        asset.name = str(payload["name"]).strip()
    asset.residual_value = data["residual"]
    asset.residual_rate = (data["residual"] / data["original"]) if data["original"] else ZERO
    asset.useful_life_months = data["life_months"]
    updatable = {
        "legalCertNo": "legal_cert_no",
        "grantOrg": "grant_org",
        "departmentId": "department_id",
        "custodian": "custodian",
        "taxLifeMonths": "tax_life_months",
    }
    for field, column in updatable.items():
        if payload.get(field) is not None:
            setattr(asset, column, payload[field])
    if payload.get("expiryDate"):
        asset.expiry_date = _parse_date(payload["expiryDate"], field="权利到期日")
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await session.flush()
    await regenerate_schedule(session, asset)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.update",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        before=before,
        after=serialize_asset(asset),
        entity_version=asset.version,
    )
    return asset


async def delete_asset(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str, request_id: str
) -> None:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status != "draft":
        raise AccountingError(
            "ASSET_NOT_DRAFT", "已提交的无形资产不能删除；已入账资产请走处置/核销流程", 409
        )
    schedule_count = (
        await session.execute(
            select(sa_func.count())
            .select_from(IntangibleAmortizationSchedule)
            .where(
                IntangibleAmortizationSchedule.asset_id == asset.id,
                IntangibleAmortizationSchedule.status != "planned",
            )
        )
    ).scalar_one()
    if int(schedule_count) > 0:
        raise AccountingError("ASSET_HAS_POSTED_SCHEDULE", "该资产已有过账摊销记录，不能删除", 409)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.delete",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        before=serialize_asset(asset),
    )
    await session.execute(
        IntangibleAmortizationSchedule.__table__.delete().where(
            IntangibleAmortizationSchedule.asset_id == asset.id
        )
    )
    asset.deleted_at = _now()
    asset.updated_by = user_id
    asset.version += 1
    await session.flush()


# ── 列表 ─────────────────────────────────────────────────────────────────

async def list_assets(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    status: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    expiring_in_days: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    query = select(IntangibleAsset).where(
        IntangibleAsset.company_id == company_id,
        IntangibleAsset.deleted_at.is_(None),
    )
    if status:
        if status == "in_use":
            query = query.where(IntangibleAsset.status.in_(("active", "idle", "fully_amortized")))
        else:
            query = query.where(IntangibleAsset.status == status)
    if category:
        query = query.where(IntangibleAsset.category == category)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(
            (IntangibleAsset.name.like(like)) | (IntangibleAsset.code.like(like)) | (IntangibleAsset.legal_cert_no.like(like))
        )
    if expiring_in_days:
        from datetime import timedelta

        deadline = _now().date() + timedelta(days=expiring_in_days)
        query = query.where(
            IntangibleAsset.expiry_date.is_not(None),
            IntangibleAsset.expiry_date <= deadline,
            IntangibleAsset.status.in_(("active", "idle", "fully_amortized")),
        )

    total = (
        await session.execute(select(sa_func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(IntangibleAsset.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [serialize_asset(a) for a in rows],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
    }


# ── 提交 / 审批 / 驳回（状态由凭证驱动）──────────────────────────────────

async def submit_asset(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    _, member = await _member_context(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status not in ("draft",):
        raise AccountingError("ASSET_NOT_DRAFT", "仅草稿状态可以提交审核", 409)
    if asset.acquisition_type == "self_developed":
        await _validate_rd_capitalization_balance(session, company_id, money(asset.original_value))

    payload = _acquisition_entry_payload(asset)
    entry_id = str(payload["id"])
    existing = await session.get(JournalEntry, entry_id)
    if existing is not None and existing.company_id == company_id and existing.status in {"draft", "pending_review"}:
        # 驳回后重提：更新既有草稿凭证（来源去重键不变，不能重建）
        entry = await update_draft(
            session, entry_id, payload, company_id=company_id, user_id=user_id, request_id=request_id
        )
        if entry.status == "draft":
            company = await _company(session, company_id)
            if member.role in POSTING_ROLES and company.posting_policy == "owner_self_post":
                entry = await post_journal(
                    session, entry.id, company_id=company_id, user_id=user_id, request_id=request_id
                )
            else:
                entry = await submit_journal_for_review(
                    session, entry.id, company_id=company_id, user_id=user_id, request_id=request_id
                )
    else:
        entry = await create_entry(
            session,
            payload,
            company_id=company_id,
            user_id=user_id,
            request_id=request_id,
            confirm=True,
        )
    asset.status = "active" if entry.status == "posted" else "pending_review"
    asset.source_entry_id = entry.id
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await regenerate_schedule(session, asset)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.submit",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        after={"status": asset.status, "journalEntryId": entry.id, "entryStatus": entry.status},
        entity_version=asset.version,
    )
    return {"asset": serialize_asset(asset), "entryStatus": entry.status, "journalEntryId": entry.id}


async def _member_context(session: AsyncSession, company_id: str, user_id: str):
    return await ensure_tenant(session, company_id, user_id)


async def _company(session: AsyncSession, company_id: str):
    return await session.get(Company, company_id)


async def approve_asset(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, POSTING_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status != "pending_review" or not asset.source_entry_id:
        raise AccountingError("ASSET_NOT_PENDING", "该资产不在待审核状态", 409)
    entry = await approve_journal(
        session, asset.source_entry_id, company_id=company_id, user_id=user_id, request_id=request_id
    )
    asset.status = "active" if entry.status == "posted" else asset.status
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.approve",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        after={"status": asset.status},
        entity_version=asset.version,
    )
    return {"asset": serialize_asset(asset), "entryStatus": entry.status}


async def reject_asset(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str, request_id: str, reason: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, POSTING_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status != "pending_review" or not asset.source_entry_id:
        raise AccountingError("ASSET_NOT_PENDING", "该资产不在待审核状态", 409)
    if not reason or not reason.strip():
        raise AccountingError("REJECT_REASON_REQUIRED", "驳回必须填写原因", 422)
    # 驳回 = 待复核凭证退回草稿（保留确定性 id 与来源记录，重提时走 update_draft）
    entry = await get_journal(session, asset.source_entry_id, company_id=company_id, lock=True)
    if entry.status == "pending_review":
        entry.status = "draft"
        entry.version += 1
        await session.flush()
        await _sync_projection(session, entry)
    elif entry.status != "draft":
        raise AccountingError("ASSET_ENTRY_CONFLICT", "取得凭证状态异常，无法驳回", 409)
    asset.status = "draft"
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.asset.reject",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        reason=reason.strip(),
        after={"status": asset.status},
        entity_version=asset.version,
    )
    return {"asset": serialize_asset(asset)}


def asset_status_of(entry_status: str) -> str:
    """凭证状态 → 资产状态（派生规则，设计方案 4.1）。"""
    return {"posted": "active", "pending_review": "pending_review", "draft": "draft"}.get(entry_status, "draft")


# ── 变更单（会计估计变更，未来适用法；设计方案 5.3 规则 6）────────────────

async def apply_life_change(
    session: AsyncSession,
    asset_id: str,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """年限/残值变更：只重算未执行 planned 行，posted/skipped 冻结，plan_version+1。

    原值不允许直接改（追加资本化走新资产或新凭证）。生效期间默认为最后已摊期的
    下一期；变更期早于最后已摊期则拒绝（不得追溯调整已过账期间）。
    """
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, POSTING_ROLES)  # 变更=会计估计变更，仅 owner/accountant
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status not in ("active", "idle"):
        raise AccountingError("ASSET_NOT_CHANGEABLE", "仅摊销中 / 已停用的无形资产可以发起年限变更", 409)

    new_life = int(payload.get("usefulLifeMonths") or 0)
    category = get_category(asset.category)
    if new_life < category.life_min_months or new_life < 1:
        raise AccountingError(
            "INVALID_INTANGIBLE_LIFE",
            f"{category.name}摊销年限不得少于 {category.life_min_months} 个月",
            422,
        )

    new_residual = money(payload["residualValue"], allow_negative=False) if payload.get("residualValue") is not None else money(asset.residual_value or ZERO)
    original = money(asset.original_value)
    if new_residual >= original:
        raise AccountingError("INVALID_RESIDUAL_VALUE", "预计净残值必须小于原值", 422)

    first_period = _period_of(asset.available_for_use_date)
    if asset.last_amortized_period:
        amortized_count = _period_diff(first_period, asset.last_amortized_period) + 1
    else:
        amortized_count = 0
    accumulated = money(asset.accumulated_amortization or ZERO)

    effective_period = str(payload.get("effectivePeriod") or "").strip()
    if effective_period:
        effective_period = _validate_period(effective_period)
        if asset.last_amortized_period and _period_diff(asset.last_amortized_period, effective_period) <= 0:
            raise AccountingError(
                "LIFE_CHANGE_CONFLICT",
                f"生效期间不得早于最后已摊期 {asset.last_amortized_period} 的下一期",
                422,
            )
        if _period_diff(first_period, effective_period) + 1 != amortized_count + 1:
            raise AccountingError(
                "LIFE_CHANGE_CONFLICT",
                "生效期间与已摊期数不一致：变更只能作用于尚未执行的期间",
                422,
            )
    else:
        effective_period = _add_months(first_period, amortized_count)

    remaining_months = new_life - amortized_count
    remaining_base = money(original - new_residual - accumulated)
    if remaining_months <= 0 or remaining_base <= ZERO:
        raise AccountingError(
            "LIFE_CHANGE_CONFLICT",
            "变更后无剩余摊销期间或剩余应摊销总额为零，请改用处置/核销流程",
            422,
        )

    max_version = (
        await session.execute(
            select(sa_func.max(IntangibleAmortizationSchedule.plan_version)).where(
                IntangibleAmortizationSchedule.company_id == company_id,
                IntangibleAmortizationSchedule.asset_id == asset.id,
            )
        )
    ).scalar_one()
    next_version = int(max_version or 1) + 1

    before = {
        "usefulLifeMonths": asset.useful_life_months,
        "residualValue": money_text(money(asset.residual_value or ZERO)),
        "amortizationEndPeriod": asset.amortization_end_period,
    }
    await session.execute(
        IntangibleAmortizationSchedule.__table__.delete().where(
            IntangibleAmortizationSchedule.asset_id == asset.id,
            IntangibleAmortizationSchedule.status == "planned",
        )
    )
    rows = build_schedule_rows(
        asset,
        life_months=new_life,
        residual_value=new_residual,
        start_period=effective_period,
        sequence_start=amortized_count + 1,
        accumulated_start=accumulated,
        plan_version=next_version,
    )
    for row in rows:
        session.add(IntangibleAmortizationSchedule(**row))

    asset.useful_life_months = new_life
    asset.residual_value = new_residual
    asset.residual_rate = (new_residual / original) if original else ZERO
    asset.amortization_end_period = rows[-1]["period_start"]
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.change.apply",
        entity_type="intangible_asset",
        entity_id=asset.id,
        request_id=request_id,
        before=before,
        after={
            "usefulLifeMonths": new_life,
            "residualValue": money_text(new_residual),
            "amortizationEndPeriod": asset.amortization_end_period,
            "effectivePeriod": effective_period,
            "planVersion": next_version,
            "note": "会计估计变更，未来适用法，已过账期次不变",
        },
        entity_version=asset.version,
    )
    return {
        "asset": serialize_asset(asset),
        "effectivePeriod": effective_period,
        "planVersion": next_version,
        "remainingMonths": remaining_months,
        "monthlyAmount": money_text(money(remaining_base / Decimal(remaining_months))),
    }


def _period_diff(start_period: str, end_period: str) -> int:
    """end − start 的月数差（可为负）。"""
    return (int(end_period[:4]) * 12 + int(end_period[5:7])) - (int(start_period[:4]) * 12 + int(start_period[5:7]))


def _validate_period(period: Any) -> str:
    raw = str(period or "").strip()
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:7].isdigit() or not 1 <= int(raw[5:7]) <= 12:
        raise AccountingError("INVALID_PERIOD", "期间格式无效，应为 YYYY-MM", 422)
    return raw
