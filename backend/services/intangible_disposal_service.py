# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产处置单服务：出售 / 报废清理 / 到期核销 / 捐赠 / 转出。

设计方案：《无形资产模块设计方案.md》第 4 章⑤⑥、第 6 章、第 7 章。

状态机：draft → pending_review → approved → posted；旁路 rejected / cancelled。
- 创建即锁快照（累计摊销、账面净值），资产置 disposing，当月 planned 摊销行置
  skipped（处置当月不再摊销）；
- 审批通过即生成处置凭证（confirm=True）并过账，凭证 posted 后资产置
  disposed / scrapped 并回写处置日期与方式；
- 凭证 reversed → 处置单 cancelled、资产回 active、skipped 行恢复 planned；
- 部分唯一索引保证一张在途资产同时只有一张有效处置单。

分录（小企业会计准则，损益走营业外收支）：
- sale：  借 1002/1122（含税价款）+ 1702 ｜ 贷 1701 + 222100105 销项税额
          + 5301001 收益（或借 5711001 损失）
- expire：借 1702 + 5711001（残值）｜ 贷 1701
- scrap： 借 1702 + 5711001（账面净值）｜ 贷 1701
- donate：借 1702 + 5711003 捐赠支出（净值 + 销项）｜ 贷 1701
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from core.money import ZERO, money, money_text
from db.models import IntangibleAmortizationSchedule, IntangibleAsset, IntangibleDisposal
from services.accounting_core_service import (
    POSTING_ROLES,
    WRITE_ROLES,
    create_entry,
    ensure_tenant,
    record_audit,
    require_role,
)
from services.intangible_asset_service import _parse_date, serialize_asset

CREDIT_SALE_TAX_ACCOUNT = ("222100105", "销项税额")
DISPOSAL_INCOME_ACCOUNT = ("5301001", "非流动资产处置净收益")
DISPOSAL_LOSS_ACCOUNT = ("5711001", "非流动资产处置净损失")
DONATION_EXPENSE_ACCOUNT = ("5711003", "捐赠支出")
CASH_ACCOUNTS = {
    "company_account": ("1002", "银行存款"),
    "notes_receivable": ("1121", "应收票据"),
    "accounts_receivable": ("1122", "应收账款"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_disposal(disposal: IntangibleDisposal, asset: IntangibleAsset | None = None) -> dict[str, Any]:
    return {
        "id": disposal.id,
        "assetId": disposal.asset_id,
        "assetCode": asset.code if asset else "",
        "assetName": asset.name if asset else "",
        "disposalType": disposal.disposal_type,
        "disposalDate": disposal.disposal_date.isoformat(),
        "proceedsAmount": money(disposal.proceeds_amount),
        "outputVatAmount": money(disposal.output_vat_amount),
        "accumulatedAmortizationSnapshot": money(disposal.accumulated_amortization_snapshot),
        "netBookValueSnapshot": money(disposal.net_book_value_snapshot),
        "gainLossAmount": money(disposal.gain_loss_amount),
        "status": disposal.status,
        "reason": disposal.reason,
        "journalEntryId": disposal.journal_entry_id,
        "initiatedBy": disposal.initiated_by,
        "approvedBy": disposal.approved_by,
        "createdAt": disposal.created_at.isoformat() if disposal.created_at else "",
    }


async def _get_disposal(session: AsyncSession, disposal_id: str, company_id: str) -> IntangibleDisposal:
    disposal = (
        await session.execute(
            select(IntangibleDisposal).where(
                IntangibleDisposal.id == disposal_id,
                IntangibleDisposal.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if disposal is None:
        raise AccountingError(NOT_FOUND, "处置单不存在", 404)
    return disposal


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


def _disposal_entry_payload(disposal: IntangibleDisposal, asset: IntangibleAsset) -> dict[str, Any]:
    original = money(asset.original_value)
    accumulated = money(disposal.accumulated_amortization_snapshot)
    nbv = money(disposal.net_book_value_snapshot)
    proceeds = money(disposal.proceeds_amount)
    vat = money(disposal.output_vat_amount)
    entry_id = f"intangible-disposal-{disposal.id}"
    source_type = "intangible_disposal"
    debit: list[dict[str, Any]] = []
    credit: list[dict[str, Any]] = [{"accountCode": "1701", "accountName": "无形资产", "amount": money_text(original)}]

    if disposal.disposal_type == "sale":
        if proceeds <= ZERO:
            raise AccountingError("DISPOSAL_PROCEEDS_REQUIRED", "出售处置必须填写处置价款", 422)
        cash_code, cash_name = CASH_ACCOUNTS["company_account"]
        debit.append({"accountCode": cash_code, "accountName": cash_name, "amount": money_text(proceeds + vat)})
        if accumulated > ZERO:
            debit.append({"accountCode": "1702", "accountName": "累计摊销", "amount": money_text(accumulated)})
        if vat > ZERO:
            credit.append({"accountCode": CREDIT_SALE_TAX_ACCOUNT[0], "accountName": CREDIT_SALE_TAX_ACCOUNT[1], "amount": money_text(vat)})
        gain = money(proceeds - nbv)
        if gain > ZERO:
            credit.append({"accountCode": DISPOSAL_INCOME_ACCOUNT[0], "accountName": DISPOSAL_INCOME_ACCOUNT[1], "amount": money_text(gain)})
        elif gain < ZERO:
            debit.append({"accountCode": DISPOSAL_LOSS_ACCOUNT[0], "accountName": DISPOSAL_LOSS_ACCOUNT[1], "amount": money_text(-gain)})
        title = f"处置无形资产-{asset.name}"
    elif disposal.disposal_type == "donate":
        vat = vat if vat > ZERO else ZERO
        expense = money(nbv + vat)
        debit.append({"accountCode": "1702", "accountName": "累计摊销", "amount": money_text(accumulated)})
        debit.append({"accountCode": DONATION_EXPENSE_ACCOUNT[0], "accountName": DONATION_EXPENSE_ACCOUNT[1], "amount": money_text(expense)})
        amount = money(original)
        if vat > ZERO:
            credit.append({"accountCode": CREDIT_SALE_TAX_ACCOUNT[0], "accountName": CREDIT_SALE_TAX_ACCOUNT[1], "amount": money_text(vat)})
            # 捐赠视同销售：贷方多一笔销项，借方捐赠支出已含税
            amount = money(original + vat)
        title = f"捐赠无形资产-{asset.name}"
        source_type = "intangible_donation"
        entry_id = f"intangible-donate-{disposal.id}"
    else:  # expire / scrap / transfer_out
        debit.append({"accountCode": "1702", "accountName": "累计摊销", "amount": money_text(accumulated)})
        if nbv > ZERO:
            debit.append({"accountCode": DISPOSAL_LOSS_ACCOUNT[0], "accountName": DISPOSAL_LOSS_ACCOUNT[1], "amount": money_text(nbv)})
        amount = money(original)
        title = f"核销无形资产-{asset.name}" if disposal.disposal_type == "expire" else f"报废无形资产-{asset.name}"
        source_type = "intangible_retirement" if disposal.disposal_type == "expire" else "intangible_scrap"
        entry_id = f"intangible-retire-{disposal.id}" if disposal.disposal_type == "expire" else f"intangible-scrap-{disposal.id}"

    # 校验借贷平衡（元级）
    debit_total = money(sum(Decimal(str(l["amount"])) for l in debit))
    credit_total = money(sum(Decimal(str(l["amount"])) for l in credit))
    if debit_total != credit_total:
        raise AccountingError(
            "DISPOSAL_ENTRY_UNBALANCED",
            f"处置分录借贷不平：借 {money_text(debit_total)} / 贷 {money_text(credit_total)}，请检查价款与税额",
            422,
        )
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": money_text(amount if disposal.disposal_type != "sale" else credit_total),
        "title": title,
        "category": "无形资产处置",
        "occurDate": disposal.disposal_date.isoformat(),
        "sourceType": source_type,
        "dedupFingerprint": f"{source_type}:{disposal.company_id}:{disposal.id}",
        "voucherAuthority": "system",
        "voucherLines": {"debit": debit, "credit": credit},
    }


async def create_disposal(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> IntangibleDisposal:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, str(payload.get("assetId") or ""), company_id)
    if asset.status not in ("active", "idle", "fully_amortized"):
        raise AccountingError(
            "ASSET_NOT_DISPOSABLE", "仅摊销中 / 已停用 / 已摊完的无形资产可以发起处置", 409
        )
    disposal_type = str(payload.get("disposalType") or "")
    if disposal_type not in ("sale", "scrap", "expire", "donate", "transfer_out"):
        raise AccountingError("INVALID_DISPOSAL_TYPE", "无效的处置方式", 422)
    disposal_date = _parse_date(payload.get("disposalDate"), field="处置日期")

    accumulated = money(asset.accumulated_amortization or ZERO)
    nbv = money(money(asset.original_value) - accumulated)
    proceeds = money(payload.get("proceedsAmount") or ZERO, allow_negative=False)
    vat = money(payload.get("outputVatAmount") or ZERO, allow_negative=False)
    if disposal_type == "sale":
        gain_loss = money(proceeds - nbv)
    else:
        gain_loss = money(-nbv)

    disposal = IntangibleDisposal(
        company_id=company_id,
        asset_id=asset.id,
        disposal_type=disposal_type,
        disposal_date=disposal_date,
        proceeds_amount=proceeds,
        output_vat_amount=vat,
        accumulated_amortization_snapshot=accumulated,
        net_book_value_snapshot=nbv,
        gain_loss_amount=gain_loss,
        status="draft",
        reason=str(payload.get("reason") or "").strip() or None,
        initiated_by=user_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(disposal)
    await session.flush()

    # 资产置处置中；处置当月 planned 摊销行置 skipped（当月不再摊销）
    previous_status = asset.status
    asset.status = "disposing"
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    disposal_period = f"{disposal_date.year:04d}-{disposal_date.month:02d}"
    await session.execute(
        IntangibleAmortizationSchedule.__table__.update()
        .where(
            IntangibleAmortizationSchedule.asset_id == asset.id,
            IntangibleAmortizationSchedule.period_start == disposal_period,
            IntangibleAmortizationSchedule.status == "planned",
        )
        .values(status="skipped")
    )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.disposal.create",
        entity_type="intangible_disposal",
        entity_id=disposal.id,
        request_id=request_id,
        after={**serialize_disposal(disposal, asset), "assetPreviousStatus": previous_status},
    )
    return disposal


async def list_disposals(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    asset_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    query = (
        select(IntangibleDisposal, IntangibleAsset)
        .join(IntangibleAsset, IntangibleAsset.id == IntangibleDisposal.asset_id)
        .where(IntangibleDisposal.company_id == company_id)
        .order_by(IntangibleDisposal.created_at.desc())
    )
    if asset_id:
        query = query.where(IntangibleDisposal.asset_id == asset_id)
    if status:
        query = query.where(IntangibleDisposal.status == status)
    rows = (await session.execute(query)).all()
    return {"items": [serialize_disposal(d, a) for d, a in rows]}


async def submit_disposal(
    session: AsyncSession, disposal_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    disposal = await _get_disposal(session, disposal_id, company_id)
    if disposal.status != "draft":
        raise AccountingError("DISPOSAL_NOT_DRAFT", "仅草稿处置单可以提交", 409)
    disposal.status = "pending_review"
    disposal.updated_at = _now()
    disposal.version += 1
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.disposal.submit",
        entity_type="intangible_disposal",
        entity_id=disposal.id,
        request_id=request_id,
        after={"status": disposal.status},
        entity_version=disposal.version,
    )
    return {"ok": True, "status": disposal.status}


async def approve_disposal(
    session: AsyncSession, disposal_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    """审批处置单 = 生成并过账处置凭证 + 回写资产状态（同一事务）。"""
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, POSTING_ROLES)
    disposal = await _get_disposal(session, disposal_id, company_id)
    if disposal.status != "pending_review":
        raise AccountingError("DISPOSAL_NOT_PENDING", "仅待复核处置单可以审批", 409)
    asset = await _get_asset(session, disposal.asset_id, company_id)

    # 同一资产不能存在第二张已过账处置凭证（冲销后回滚由 reverse 联动处理）
    payload = _disposal_entry_payload(disposal, asset)
    entry = await create_entry(
        session,
        payload,
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )
    if entry.status != "posted":
        raise AccountingError(
            "DISPOSAL_REVIEW_REQUIRED", "处置凭证已生成但未过账，请先在凭证页复核过账后再审批", 409,
            {"journalEntryId": entry.id},
        )
    disposal.status = "posted"
    disposal.journal_entry_id = entry.id
    disposal.approved_by = user_id
    disposal.approved_at = _now()
    disposal.posted_at = _now()
    disposal.updated_at = _now()
    disposal.version += 1

    asset.status = "disposed" if disposal.disposal_type in ("sale", "transfer_out", "donate") else "scrapped"
    asset.disposed_on = disposal.disposal_date
    asset.disposal_type = disposal.disposal_type
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.disposal.approve",
        entity_type="intangible_disposal",
        entity_id=disposal.id,
        request_id=request_id,
        after={"status": disposal.status, "assetStatus": asset.status, "journalEntryId": entry.id},
        entity_version=disposal.version,
    )
    return {"ok": True, "status": disposal.status, "assetStatus": asset.status, "journalEntryId": entry.id}


async def cancel_disposal(
    session: AsyncSession, disposal_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    """取消未过账处置单；已过账请走凭证冲销（reverse 后资产状态以凭证为准回滚）。"""
    await ensure_tenant(session, company_id, user_id)
    disposal = await _get_disposal(session, disposal_id, company_id)
    if disposal.status == "posted":
        raise AccountingError(
            "DISPOSAL_ALREADY_POSTED", "已过账处置单不能取消，请对处置凭证做冲销", 409
        )
    if disposal.status == "pending_review":
        await require_role(session, company_id, user_id, POSTING_ROLES)
    elif disposal.status != "draft":
        raise AccountingError("DISPOSAL_NOT_CANCELLABLE", "当前状态的处置单不能取消", 409)
    else:
        await require_role(session, company_id, user_id, WRITE_ROLES)

    disposal.status = "cancelled"
    disposal.updated_at = _now()
    disposal.version += 1
    asset = await _get_asset(session, disposal.asset_id, company_id)
    if asset.status == "disposing":
        asset.status = "active" if asset.accumulated_amortization < asset.original_value - asset.residual_value else "fully_amortized"
        asset.updated_by = user_id
        asset.updated_at = _now()
        asset.version += 1
    # 恢复当月 planned 摊销行
    disposal_period = f"{disposal.disposal_date.year:04d}-{disposal.disposal_date.month:02d}"
    await session.execute(
        IntangibleAmortizationSchedule.__table__.update()
        .where(
            IntangibleAmortizationSchedule.asset_id == disposal.asset_id,
            IntangibleAmortizationSchedule.period_start == disposal_period,
            IntangibleAmortizationSchedule.status == "skipped",
        )
        .values(status="planned")
    )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.disposal.cancel",
        entity_type="intangible_disposal",
        entity_id=disposal.id,
        request_id=request_id,
        after={"status": disposal.status, "assetStatus": asset.status},
        entity_version=disposal.version,
    )
    return {"ok": True, "status": disposal.status, "assetStatus": asset.status}
