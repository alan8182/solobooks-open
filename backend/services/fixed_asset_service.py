# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产服务：卡片 CRUD、提交审批（凭证驱动）、折旧计划预生成、期初/迁移入卡。

照无形资产服务（intangible_asset_service）模式，硬规则一致：
- 数据隔离只按 company_id，不引入 user_id 过滤；
- 资产 status 只由凭证 / 处置单驱动，任何 API 不接受直接改状态；
- 取得凭证 confirm=True：owner_self_post 直接 posted，maker_checker 落
  pending_review——绝不留 draft（close_period 会自动过账草稿，绕过复核）；
- 折旧计划在提交时预生成全周期 planned 行。

与无形资产的口径差异（设计方案 P1场景扩展落地计划.md WP-B）：
- 次月起提：可供使用次月开始计提折旧（无形资产是当月起摊）；
- 一次性折旧（one_time）：≤500 万设备器具全额计提，仅生成 1 期计划；
- ingest_card 供期初衔接与存量迁移直建 active 卡片（不生成取得凭证——
  1601/1602 已由期初凭证/历史凭证承载，卡片挂 source_entry_id 幂等）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from core.fixed_asset_categories import (
    DEPRECIATION_METHODS,
    get_category,
    validate_depreciation_method,
    validate_life_months,
)
from core.money import ZERO, money, money_text, rate
from db.models import Company, FixedAsset, FixedAssetDepreciationSchedule, JournalEntry, JournalLine, RdProject
from services.accounting_core_service import (
    POSTING_ROLES,
    WRITE_ROLES,
    _advisory_lock,
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
ACQUISITION_TYPES = ("purchase", "self_built", "investor_contribution", "donation")

# 更精确的损益叶子科目（5301/5301003 语义与无形资产一致）
FIXED_ASSET_ACCOUNT = ("1601", "固定资产")
SELF_BUILT_CREDIT_ACCOUNT = ("1604", "在建工程")
INVESTOR_CREDIT_ACCOUNT = ("3001", "实收资本")
DONATION_CREDIT_ACCOUNT = ("5301003", "捐赠收益")


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _period_diff(start_period: str, end_period: str) -> int:
    """end − start 的月数差（可为负）。"""
    return (int(end_period[:4]) * 12 + int(end_period[5:7])) - (int(start_period[:4]) * 12 + int(start_period[5:7]))


def _validate_period(period: Any) -> str:
    raw = str(period or "").strip()
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:7].isdigit() or not 1 <= int(raw[5:7]) <= 12:
        raise AccountingError("INVALID_PERIOD", "期间格式无效，应为 YYYY-MM", 422)
    return raw


def first_depreciation_period(available_for_use_date: date) -> str:
    """次月起提：当月增加当月不提，自可供使用次月起计提。"""
    return _add_months(_period_of(available_for_use_date), 1)


# ── 序列化 ────────────────────────────────────────────────────────────────

def serialize_asset(asset: FixedAsset) -> dict[str, Any]:
    accumulated = money(asset.accumulated_depreciation or ZERO)
    original = money(asset.original_value)
    depreciable = money(original - money(asset.residual_value or ZERO))
    if asset.last_depreciated_period and asset.depreciation_end_period:
        remaining = max(0, _period_diff(asset.last_depreciated_period, asset.depreciation_end_period))
    elif asset.depreciation_end_period:
        first = first_depreciation_period(asset.available_for_use_date)
        remaining = max(0, _period_diff(first, asset.depreciation_end_period) + 1)
    else:
        remaining = 1 if asset.depreciation_method == "one_time" else int(asset.useful_life_months)
    if asset.depreciation_method == "one_time":
        monthly = money(depreciable - accumulated) if remaining > 0 else ZERO
    else:
        monthly = money(depreciable / Decimal(int(asset.useful_life_months))) if asset.useful_life_months else ZERO
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
        "depreciationMethod": asset.depreciation_method,
        "monthlyDepreciation": monthly,
        "accumulatedDepreciation": accumulated,
        "netValue": money(original - accumulated),
        "remainingMonths": remaining,
        "lastDepreciatedPeriod": asset.last_depreciated_period,
        "depreciationEndPeriod": asset.depreciation_end_period,
        "status": asset.status,
        "sourceEntryId": asset.source_entry_id,
        "departmentId": asset.department_id,
        "custodian": asset.custodian,
        "location": asset.location,
        "rdProjectId": asset.rd_project_id,
        "rdUseRate": asset.rd_use_rate,
        "rdAllocationBasis": asset.rd_allocation_basis,
        "taxLifeMonths": asset.tax_life_months,
        "disposedOn": asset.disposed_on.isoformat() if asset.disposed_on else None,
        "disposalType": asset.disposal_type,
        "createdAt": asset.created_at.isoformat() if asset.created_at else "",
        "updatedAt": asset.updated_at.isoformat() if asset.updated_at else "",
    }


# ── 校验 ─────────────────────────────────────────────────────────────────

def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """公共校验，返回规范化字段。create / update / ingest 复用。"""
    category = get_category(str(payload.get("category") or ""))
    original = money(payload.get("originalValue") or ZERO, allow_negative=False)
    if original <= ZERO:
        raise AccountingError("INVALID_ORIGINAL_VALUE", "固定资产原值必须大于零", 422)

    residual = money(payload.get("residualValue") or ZERO, allow_negative=False)
    residual_rate = payload.get("residualRate")
    if residual == ZERO and residual_rate is not None:
        residual = money(original * rate(residual_rate))
    if residual >= original:
        raise AccountingError("INVALID_RESIDUAL_VALUE", "预计净残值必须小于原值", 422)

    life_months = int(payload.get("usefulLifeMonths") or 0)
    if life_months <= 0:
        raise AccountingError("INVALID_LIFE", "折旧年限（月）必须大于零", 422)
    validate_life_months(category.code, life_months)

    method = str(payload.get("depreciationMethod") or "straight_line")
    validate_depreciation_method(category.code, method, original, residual)

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
        "method": method,
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
    prefix = f"FA-{acquisition_date.year:04d}-"
    await _advisory_lock(session, f"fixed-asset-code:{company_id}:{acquisition_date.year}")
    rows = (
        await session.execute(
            select(FixedAsset.code).where(
                FixedAsset.company_id == company_id,
                FixedAsset.code.like(f"{prefix}%"),
                FixedAsset.deleted_at.is_(None),  # 软删除编码可复用
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


# ── 折旧计划预生成 ────────────────────────────────────────────────────────

def build_schedule_rows(
    asset: FixedAsset,
    *,
    life_months: int | None = None,
    residual_value: Decimal | None = None,
    start_period: str | None = None,
    sequence_start: int = 1,
    accumulated_start: Decimal = ZERO,
    plan_version: int = 1,
) -> list[dict[str, Any]]:
    """planned 行生成：次月起提、末期尾差兜平、Σamount == 应提基数。

    one_time 只生成 1 期（sequence_start=1 时），全额 = 原值 − 残值（残值恒 0）。
    迁移/期初通过 sequence_start / accumulated_start 只生成未执行期间。
    """
    base = money(money(asset.original_value) - money(residual_value if residual_value is not None else (asset.residual_value or ZERO)))
    if asset.depreciation_method == "one_time":
        if sequence_start > 1 or base - accumulated_start <= ZERO:
            return []
        accumulated = money(accumulated_start)
        return [
            {
                "company_id": asset.company_id,
                "asset_id": asset.id,
                "period_start": start_period or first_depreciation_period(asset.available_for_use_date),
                "sequence_no": 1,
                "amount": money(base - accumulated),
                "accumulated_before": accumulated,
                "accumulated_after": money(base),
                "status": "planned",
                "plan_version": plan_version,
            }
        ]

    n = int(life_months if life_months is not None else asset.useful_life_months)
    remaining = n - (sequence_start - 1)
    if remaining <= 0:
        raise AccountingError("INVALID_SCHEDULE", "剩余折旧期数必须大于零", 422)
    if base - accumulated_start <= ZERO:
        raise AccountingError("INVALID_SCHEDULE", "剩余应折旧总额必须大于零", 422)
    monthly = money((base - accumulated_start) / Decimal(remaining))
    first_period = start_period or first_depreciation_period(asset.available_for_use_date)
    rows: list[dict[str, Any]] = []
    accumulated = money(accumulated_start)
    for seq in range(sequence_start, n + 1):
        # 末期兜平：最后一期 = 基数 − 已写入累计
        amount = money(base - accumulated) if seq == n else monthly
        if amount <= ZERO:
            raise AccountingError("INVALID_SCHEDULE", "折旧计划出现非正数期次，请检查年限与残值", 422)
        before = accumulated
        accumulated = money(accumulated + amount)
        rows.append(
            {
                "company_id": asset.company_id,
                "asset_id": asset.id,
                # start_period 语义 = sequence_start 那一期的期间（迁移续建显式传
                # 最后已提期的下一期）；缺省锚定起提首期
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


async def regenerate_schedule(session: AsyncSession, asset: FixedAsset) -> None:
    """重建 planned 计划（draft/pending 阶段或计划为空时的补偿入口）。"""
    await session.execute(
        FixedAssetDepreciationSchedule.__table__.delete().where(
            FixedAssetDepreciationSchedule.asset_id == asset.id,
            FixedAssetDepreciationSchedule.status == "planned",
        )
    )
    for row in build_schedule_rows(asset):
        session.add(FixedAssetDepreciationSchedule(**row))
    first = first_depreciation_period(asset.available_for_use_date)
    if asset.depreciation_method == "one_time":
        asset.depreciation_end_period = first
    else:
        asset.depreciation_end_period = _add_months(first, asset.useful_life_months - 1)
    await session.flush()


# ── 取得凭证 ─────────────────────────────────────────────────────────────

def _acquisition_entry_payload(asset: FixedAsset) -> dict[str, Any]:
    original = money(asset.original_value)
    debit = [{"accountCode": "1601", "accountName": "固定资产", "amount": money_text(original)}]
    credit_amount = original
    if asset.acquisition_type == "self_built":
        credit = [SELF_BUILT_CREDIT_ACCOUNT]
    elif asset.acquisition_type == "investor_contribution":
        credit = [INVESTOR_CREDIT_ACCOUNT]
    elif asset.acquisition_type == "donation":
        credit = [DONATION_CREDIT_ACCOUNT]
    else:
        # 小规模纳税人不抵扣进项税额，固定资产按价税合计入账
        credit = [ASSET_CREDIT_ACCOUNTS[asset.payment_method or "company_account"]]
    return {
        "id": f"fixed-asset-acq-{asset.id}",
        "direction": "expense",
        "amount": money_text(credit_amount),
        "title": f"取得固定资产-{asset.name}",
        "category": "固定资产",
        "occurDate": asset.acquisition_date.isoformat(),
        "sourceType": f"fixed_asset_{asset.acquisition_type}",
        "dedupFingerprint": f"fixed_asset_acquisition:{asset.company_id}:{asset.id}",
        "voucherAuthority": "system",
        "voucherLines": {
            "debit": debit,
            "credit": [
                {"accountCode": code, "accountName": name, "amount": money_text(credit_amount)}
                for code, name in credit
            ],
        },
    }


# ── 来源交易 → 资产卡片 ──────────────────────────────────────────────────

def _suggest_fixed_asset_category(name: str) -> str:
    text = str(name or "")
    rules = (
        (("电脑", "显示器", "打印机", "服务器", "电子", "相机", "空调"), "electronics"),
        (("汽车", "车辆", "货车", "运输", "摩托"), "vehicle"),
        (("房屋", "建筑", "厂房", "办公室"), "building"),
        (("机器", "机械", "生产线", "生产设备"), "machine"),
        (("家具", "桌椅", "柜", "沙发"), "furniture"),
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
                if line.direction == "debit" and line.account_code == FIXED_ASSET_ACCOUNT[0]
            ),
            ZERO,
        )
    )
    existing = (
        await session.execute(
            select(FixedAsset.id).where(
                FixedAsset.company_id == company_id,
                FixedAsset.source_entry_id == entry_id,
                FixedAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    acquired_on = entry.occurred_on.isoformat()
    credit_line = next((l for l in lines if l.direction == "credit"), None)
    source_account = credit_line.account_name if credit_line else "银行/资金账户"
    return {
        "sourceEntryId": entry_id,
        "existingAssetId": existing,
        "name": entry.summary,
        "acquisitionDate": acquired_on,
        "availableForUseDate": acquired_on,
        "originalValue": original,
        "suggestedCategory": _suggest_fixed_asset_category(entry.summary),
        "entryStatus": entry.status,
        "entryType": entry.entry_type,
        "voucherNo": entry.voucher_no,
        "summary": entry.summary,
        "sourceAccount": source_account,
    }


async def get_fixed_asset_entry_prefill(
    session: AsyncSession, entry_id: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    return await _source_entry_values(session, entry_id, company_id)


async def create_asset_from_entry(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> FixedAsset:
    """把已过账的固定资产采购凭证接入资产卡片，不重复生成取得凭证。"""
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)

    source_entry_id = str(payload.get("sourceEntryId") or "").strip()
    if not source_entry_id:
        raise AccountingError("SOURCE_ENTRY_REQUIRED", "缺少来源凭证", 422)
    source = await _source_entry_values(session, source_entry_id, company_id)
    if source["existingAssetId"]:
        raise AccountingError(
            "DUPLICATE_SOURCE",
            "该采购凭证已关联固定资产卡片",
            409,
            {"existingAssetId": source["existingAssetId"], "sourceEntryId": source_entry_id},
        )
    if source["entryStatus"] != "posted" or source["entryType"] in {"reversal", "opening", "closing"}:
        raise AccountingError(
            "ENTRY_NOT_POSTED",
            "来源凭证尚未过账，不能建立固定资产卡片",
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
            "来源凭证已冲销，不能建立固定资产卡片",
            409,
            {"sourceEntryId": source_entry_id, "reversalEntryId": reversal_id},
        )
    if source["originalValue"] <= ZERO:
        raise AccountingError(
            "NOT_FIXED_ASSET_ENTRY",
            "来源凭证没有 1601 固定资产借方金额",
            422,
            {"sourceEntryId": source_entry_id},
        )

    data = _validate_payload(payload)
    if abs(data["original"] - source["originalValue"]) > Decimal("0.01"):
        raise AccountingError(
            "FIXED_ASSET_VALUE_MISMATCH",
            f"资产原值 {money_text(data['original'])} 与来源凭证 1601 借方 {money_text(source['originalValue'])} 不一致",
            422,
            {
                "sourceEntryId": source_entry_id,
                "originalValue": money_text(source["originalValue"]),
            },
        )

    asset = await ingest_card(
        session,
        {**payload, "acquisitionType": "purchase"},
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
        action="fixed_asset.create_from_entry",
        entity_type="fixed_asset",
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
) -> FixedAsset:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    data = _validate_payload(payload)
    await _validate_rd_project(session, company_id, data["rd_project_id"])

    acquisition_type = str(payload.get("acquisitionType") or "purchase")
    if acquisition_type not in ACQUISITION_TYPES:
        raise AccountingError("INVALID_ACQUISITION_TYPE", "无效的取得方式", 422)

    code = await _next_code(session, company_id, data["acquisition_date"])
    now = _now()
    asset = FixedAsset(
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
        depreciation_method=data["method"],
        accumulated_depreciation=ZERO,
        status="draft",
        department_id=str(payload.get("departmentId") or "").strip() or None,
        custodian=str(payload.get("custodian") or "").strip() or None,
        location=str(payload.get("location") or "").strip() or None,
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
        action="fixed_asset.create",
        entity_type="fixed_asset",
        entity_id=asset.id,
        request_id=request_id,
        after=serialize_asset(asset),
        entity_version=asset.version,
    )
    return asset


async def ingest_card(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    source_entry_id: str | None,
    accumulated_depreciation: Decimal = ZERO,
    last_depreciated_period: str | None = None,
    status: str = "active",
    acquisition_type: str = "purchase",
    payment_method: str = "company_account",
) -> FixedAsset:
    """期初衔接 / 存量迁移直建卡片：不生成取得凭证，直接挂 source_entry_id 幂等。

    累计折旧允许非零（期初已提折旧/迁移已提期次），计划只重建未执行期间。
    """
    data = _validate_payload(payload)
    await _validate_rd_project(session, company_id, data["rd_project_id"])
    if acquisition_type not in ACQUISITION_TYPES:
        raise AccountingError("INVALID_ACQUISITION_TYPE", "无效的取得方式", 422)

    accumulated = money(accumulated_depreciation or ZERO, allow_negative=False)
    depreciable = money(data["original"] - data["residual"])
    if accumulated >= depreciable:
        raise AccountingError(
            "INVALID_ACCUMULATED_DEPRECIATION",
            f"累计折旧 {money_text(accumulated)} 须小于应折旧总额 {money_text(depreciable)}；已提完的资产状态应为已折旧完毕",
            422,
        )

    # 已提期数：显式给 last_depreciated_period 按 available 次月起算，否则 0
    first_period = first_depreciation_period(data["available_date"])
    if last_depreciated_period:
        last_depreciated_period = _validate_period(last_depreciated_period)
        amortized_count = _period_diff(first_period, last_depreciated_period) + 1
        if amortized_count < 0:
            # =0 合法：期初已提折旧但新计划尚未起提（期初月 = 起提月的前一个月）
            raise AccountingError(
                "INVALID_LAST_PERIOD",
                f"最后已提期间早于起提期间 {first_period}，请检查迁移数据",
                422,
            )
    else:
        amortized_count = 0

    # 幂等：同 source_entry_id 已有卡片说明已入过（部分唯一索引兜底），先查后插
    if source_entry_id:
        existing = (
            await session.execute(
                select(FixedAsset.id).where(
                    FixedAsset.company_id == company_id,
                    FixedAsset.source_entry_id == source_entry_id,
                    FixedAsset.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise AccountingError("DUPLICATE_SOURCE", "该期初/取得凭证已关联固定资产卡片", 409)

    code = await _next_code(session, company_id, data["acquisition_date"])
    now = _now()
    asset = FixedAsset(
        company_id=company_id,
        code=code,
        name=str(payload.get("name") or "").strip() or "固定资产",
        category=data["category"].code,
        acquisition_type=acquisition_type,
        acquisition_date=data["acquisition_date"],
        available_for_use_date=data["available_date"],
        original_value=data["original"],
        residual_value=data["residual"],
        residual_rate=(data["residual"] / data["original"]) if data["original"] else ZERO,
        useful_life_months=data["life_months"],
        depreciation_method=data["method"],
        accumulated_depreciation=accumulated,
        last_depreciated_period=last_depreciated_period,
        status=status,
        source_entry_id=source_entry_id,
        department_id=str(payload.get("departmentId") or "").strip() or None,
        custodian=str(payload.get("custodian") or "").strip() or None,
        location=str(payload.get("location") or "").strip() or None,
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

    if asset.depreciation_method == "one_time" and accumulated > ZERO:
        # 一次性折旧已全额计提：无剩余计划
        asset.depreciation_end_period = first_period
    else:
        for row in build_schedule_rows(
            asset,
            # 起点 = 最后已提期的下一期（amortized_count=0 时即起提首期）
            start_period=_add_months(first_period, amortized_count),
            sequence_start=amortized_count + 1,
            accumulated_start=accumulated,
        ):
            session.add(FixedAssetDepreciationSchedule(**row))
        first = first_depreciation_period(asset.available_for_use_date)
        asset.depreciation_end_period = (
            first if asset.depreciation_method == "one_time" else _add_months(first, asset.useful_life_months - 1)
        )
    await session.flush()
    return asset


async def _get_asset(session: AsyncSession, asset_id: str, company_id: str) -> FixedAsset:
    asset = (
        await session.execute(
            select(FixedAsset).where(
                FixedAsset.id == asset_id,
                FixedAsset.company_id == company_id,
                FixedAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError(NOT_FOUND, "固定资产不存在", 404)
    return asset


async def get_asset(session: AsyncSession, asset_id: str, *, company_id: str, user_id: str) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    return serialize_asset(await _get_asset(session, asset_id, company_id))


async def update_asset(
    session: AsyncSession, asset_id: str, payload: dict[str, Any], *, company_id: str, user_id: str, request_id: str
) -> FixedAsset:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status not in ("draft",):
        raise AccountingError("ASSET_NOT_DRAFT", "仅草稿状态的固定资产可以编辑", 409)

    before = serialize_asset(asset)
    merged = {
        "category": payload.get("category") or asset.category,
        "depreciationMethod": payload.get("depreciationMethod") or asset.depreciation_method,
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
    asset.category = data["category"].code
    asset.depreciation_method = data["method"]
    asset.residual_value = data["residual"]
    asset.residual_rate = (data["residual"] / data["original"]) if data["original"] else ZERO
    asset.useful_life_months = data["life_months"]
    updatable = {
        "departmentId": "department_id",
        "custodian": "custodian",
        "location": "location",
        "taxLifeMonths": "tax_life_months",
    }
    for field, column in updatable.items():
        if payload.get(field) is not None:
            setattr(asset, column, payload[field])
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await session.flush()
    await regenerate_schedule(session, asset)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="fixed_asset.update",
        entity_type="fixed_asset",
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
            "ASSET_NOT_DRAFT", "已提交的固定资产不能删除；已入账资产请走处置/报废流程", 409
        )
    schedule_count = (
        await session.execute(
            select(sa_func.count())
            .select_from(FixedAssetDepreciationSchedule)
            .where(
                FixedAssetDepreciationSchedule.asset_id == asset.id,
                FixedAssetDepreciationSchedule.status != "planned",
            )
        )
    ).scalar_one()
    if int(schedule_count) > 0:
        raise AccountingError("ASSET_HAS_POSTED_SCHEDULE", "该资产已有过账折旧记录，不能删除", 409)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="fixed_asset.delete",
        entity_type="fixed_asset",
        entity_id=asset.id,
        request_id=request_id,
        before=serialize_asset(asset),
    )
    await session.execute(
        FixedAssetDepreciationSchedule.__table__.delete().where(
            FixedAssetDepreciationSchedule.asset_id == asset.id
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
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    query = select(FixedAsset).where(
        FixedAsset.company_id == company_id,
        FixedAsset.deleted_at.is_(None),
    )
    if status:
        if status == "in_use":
            query = query.where(FixedAsset.status.in_(("active", "idle", "fully_depreciated")))
        else:
            query = query.where(FixedAsset.status == status)
    if category:
        query = query.where(FixedAsset.category == category)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where((FixedAsset.name.like(like)) | (FixedAsset.code.like(like)))

    total = (
        await session.execute(select(sa_func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(FixedAsset.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
    _, member = await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, asset_id, company_id)
    if asset.status not in ("draft",):
        raise AccountingError("ASSET_NOT_DRAFT", "仅草稿状态可以提交审核", 409)

    payload = _acquisition_entry_payload(asset)
    entry_id = str(payload["id"])
    existing = await session.get(JournalEntry, entry_id)
    if existing is not None and existing.company_id == company_id and existing.status in {"draft", "pending_review"}:
        # 驳回后重提：更新既有草稿凭证（来源去重键不变，不能重建）
        entry = await update_draft(
            session, entry_id, payload, company_id=company_id, user_id=user_id, request_id=request_id
        )
        if entry.status == "draft":
            company = await session.get(Company, company_id)
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
        action="fixed_asset.submit",
        entity_type="fixed_asset",
        entity_id=asset.id,
        request_id=request_id,
        after={"status": asset.status, "journalEntryId": entry.id, "entryStatus": entry.status},
        entity_version=asset.version,
    )
    return {"asset": serialize_asset(asset), "entryStatus": entry.status, "journalEntryId": entry.id}


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
        action="fixed_asset.approve",
        entity_type="fixed_asset",
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
        from services.accounting_core_service import _sync_projection

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
        action="fixed_asset.reject",
        entity_type="fixed_asset",
        entity_id=asset.id,
        request_id=request_id,
        reason=reason.strip(),
        after={"status": asset.status},
        entity_version=asset.version,
    )
    return {"asset": serialize_asset(asset)}


def asset_status_of(entry_status: str) -> str:
    """凭证状态 → 资产状态（派生规则，同无形资产 4.1）。"""
    return {"posted": "active", "pending_review": "pending_review", "draft": "draft"}.get(entry_status, "draft")
