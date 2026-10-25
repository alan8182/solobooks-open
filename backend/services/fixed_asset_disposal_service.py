# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产处置单服务：出售 / 报废清理 / 捐赠 / 对外投资转出。

照无形资产处置单（intangible_disposal_service）状态机：
draft → pending_review → approved → posted；旁路 rejected / cancelled。
- 创建即锁快照（累计折旧、账面净值），资产置 disposing，当月 planned 折旧行
  置 skipped（处置当月不再计提——保证处置快照与 1602 余额口径一致）；
- 审批通过即生成处置凭证（confirm=True）并过账，凭证 posted 后资产置
  disposed / scrapped 并回写处置日期与方式；
- 部分唯一索引保证一张在途资产同时只有一张有效处置单。

分录（经 1606 固定资产清理中转，单凭证内同向分列，展示传统清理过程）：
- 共同转入清理：借 1606（净值）+ 1602（累计折旧）｜ 贷 1601（原值）
- sale：        借 1002/1121/1122（价税合计）｜ 贷 1606（价款）+ 222100105（销项）
                净损益：借 1606（收益）｜ 贷 5301001；或借 5711001（损失）｜ 贷 1606
- transfer_out：同 sale，但借方为 1511 长期股权投资（投资作价）
- scrap：       残料收入借 1002｜贷 1606；净值损失借 5711001｜贷 1606
- donate：      借 5711003（净值 + 销项）｜ 贷 1606（净值）+ 222100105（销项，视同销售）
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from core.money import ZERO, money, money_text
from db.models import FixedAsset, FixedAssetDepreciationSchedule, FixedAssetDisposal
from services.accounting_core_service import (
    POSTING_ROLES,
    WRITE_ROLES,
    create_entry,
    ensure_tenant,
    record_audit,
    require_role,
)
from services.fixed_asset_service import _parse_date, serialize_asset

CREDIT_SALE_TAX_ACCOUNT = ("222100105", "销项税额")
DISPOSAL_INCOME_ACCOUNT = ("5301001", "非流动资产处置净收益")
DISPOSAL_LOSS_ACCOUNT = ("5711001", "非流动资产处置净损失")
DONATION_EXPENSE_ACCOUNT = ("5711003", "捐赠支出")
CLEARING_ACCOUNT = ("1606", "固定资产清理")
ACCUMULATED_ACCOUNT = ("1602", "累计折旧")
ORIGINAL_ACCOUNT = ("1601", "固定资产")
EQUITY_INVESTMENT_ACCOUNT = ("1511", "长期股权投资")
CASH_ACCOUNTS = {
    "company_account": ("1002", "银行存款"),
    "notes_receivable": ("1121", "应收票据"),
    "accounts_receivable": ("1122", "应收账款"),
}
DISPOSAL_TYPES = ("sale", "scrap", "donate", "transfer_out")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_disposal(disposal: FixedAssetDisposal, asset: FixedAsset | None = None) -> dict[str, Any]:
    return {
        "id": disposal.id,
        "assetId": disposal.asset_id,
        "assetCode": asset.code if asset else "",
        "assetName": asset.name if asset else "",
        "disposalType": disposal.disposal_type,
        "disposalDate": disposal.disposal_date.isoformat(),
        "proceedsAmount": money(disposal.proceeds_amount),
        "outputVatAmount": money(disposal.output_vat_amount),
        "accumulatedDepreciationSnapshot": money(disposal.accumulated_depreciation_snapshot),
        "netBookValueSnapshot": money(disposal.net_book_value_snapshot),
        "gainLossAmount": money(disposal.gain_loss_amount),
        "status": disposal.status,
        "reason": disposal.reason,
        "journalEntryId": disposal.journal_entry_id,
        "initiatedBy": disposal.initiated_by,
        "approvedBy": disposal.approved_by,
        "createdAt": disposal.created_at.isoformat() if disposal.created_at else "",
    }


async def _get_disposal(session: AsyncSession, disposal_id: str, company_id: str) -> FixedAssetDisposal:
    disposal = (
        await session.execute(
            select(FixedAssetDisposal).where(
                FixedAssetDisposal.id == disposal_id,
                FixedAssetDisposal.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if disposal is None:
        raise AccountingError(NOT_FOUND, "处置单不存在", 404)
    return disposal


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


def _disposal_entry_payload(disposal: FixedAssetDisposal, asset: FixedAsset) -> dict[str, Any]:
    original = money(asset.original_value)
    accumulated = money(disposal.accumulated_depreciation_snapshot)
    nbv = money(disposal.net_book_value_snapshot)
    proceeds = money(disposal.proceeds_amount)
    vat = money(disposal.output_vat_amount) if disposal.disposal_type in ("sale", "donate", "transfer_out") else ZERO
    entry_id = f"fixed-asset-disposal-{disposal.id}"
    source_type = "fixed_asset_disposal"
    title_map = {
        "sale": f"出售固定资产-{asset.name}",
        "scrap": f"报废固定资产-{asset.name}",
        "donate": f"捐赠固定资产-{asset.name}",
        "transfer_out": f"投资转出固定资产-{asset.name}",
    }

    debit: list[dict[str, Any]] = []
    if nbv > ZERO:
        debit.append({"accountCode": CLEARING_ACCOUNT[0], "accountName": CLEARING_ACCOUNT[1], "amount": money_text(nbv)})
    if accumulated > ZERO:
        debit.append({"accountCode": ACCUMULATED_ACCOUNT[0], "accountName": ACCUMULATED_ACCOUNT[1], "amount": money_text(accumulated)})
    credit: list[dict[str, Any]] = [
        {"accountCode": ORIGINAL_ACCOUNT[0], "accountName": ORIGINAL_ACCOUNT[1], "amount": money_text(original)}
    ]
    gain_loss = ZERO

    if disposal.disposal_type == "donate":
        vat = vat if vat > ZERO else ZERO
        expense = money(nbv + vat)
        debit.append(
            {"accountCode": DONATION_EXPENSE_ACCOUNT[0], "accountName": DONATION_EXPENSE_ACCOUNT[1], "amount": money_text(expense)}
        )
        if nbv > ZERO:
            credit.append({"accountCode": CLEARING_ACCOUNT[0], "accountName": CLEARING_ACCOUNT[1], "amount": money_text(nbv)})
        if vat > ZERO:
            credit.append(
                {"accountCode": CREDIT_SALE_TAX_ACCOUNT[0], "accountName": CREDIT_SALE_TAX_ACCOUNT[1], "amount": money_text(vat)}
            )
        source_type = "fixed_asset_donation"
        entry_id = f"fixed-asset-donate-{disposal.id}"
    else:
        if disposal.disposal_type == "sale":
            if proceeds <= ZERO:
                raise AccountingError("DISPOSAL_PROCEEDS_REQUIRED", "出售处置必须填写处置价款", 422)
            cash_code, cash_name = CASH_ACCOUNTS["company_account"]
        elif disposal.disposal_type == "transfer_out":
            if proceeds <= ZERO:
                raise AccountingError("DISPOSAL_PROCEEDS_REQUIRED", "投资转出必须填写投资作价", 422)
            cash_code, cash_name = EQUITY_INVESTMENT_ACCOUNT
        else:  # scrap：残料变价收入可有可无
            cash_code, cash_name = CASH_ACCOUNTS["company_account"]
        if proceeds > ZERO:
            debit.append({"accountCode": cash_code, "accountName": cash_name, "amount": money_text(money(proceeds + vat))})
            credit.append({"accountCode": CLEARING_ACCOUNT[0], "accountName": CLEARING_ACCOUNT[1], "amount": money_text(proceeds)})
        if vat > ZERO:
            credit.append(
                {"accountCode": CREDIT_SALE_TAX_ACCOUNT[0], "accountName": CREDIT_SALE_TAX_ACCOUNT[1], "amount": money_text(vat)}
            )
        gain_loss = money(proceeds - nbv)
        if gain_loss > ZERO:
            debit.append({"accountCode": CLEARING_ACCOUNT[0], "accountName": CLEARING_ACCOUNT[1], "amount": money_text(gain_loss)})
            credit.append(
                {"accountCode": DISPOSAL_INCOME_ACCOUNT[0], "accountName": DISPOSAL_INCOME_ACCOUNT[1], "amount": money_text(gain_loss)}
            )
        elif gain_loss < ZERO:
            loss = money(-gain_loss)
            debit.append({"accountCode": DISPOSAL_LOSS_ACCOUNT[0], "accountName": DISPOSAL_LOSS_ACCOUNT[1], "amount": money_text(loss)})
            credit.append({"accountCode": CLEARING_ACCOUNT[0], "accountName": CLEARING_ACCOUNT[1], "amount": money_text(loss)})

    # 1606 清理科目必须轧平（借方合计 == 贷方合计），净损益全部转出
    clearing_debit = money(sum(Decimal(str(l["amount"])) for l in debit if l["accountCode"] == CLEARING_ACCOUNT[0]))
    clearing_credit = money(sum(Decimal(str(l["amount"])) for l in credit if l["accountCode"] == CLEARING_ACCOUNT[0]))
    if clearing_debit != clearing_credit:
        raise AccountingError(
            "DISPOSAL_CLEARING_UNBALANCED",
            f"固定资产清理未轧平：借 {money_text(clearing_debit)} / 贷 {money_text(clearing_credit)}，请检查价款与税额",
            422,
        )

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
        "amount": money_text(debit_total),
        "title": title_map[disposal.disposal_type],
        "category": "固定资产处置",
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
) -> FixedAssetDisposal:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = await _get_asset(session, str(payload.get("assetId") or ""), company_id)
    if asset.status not in ("active", "idle", "fully_depreciated"):
        raise AccountingError(
            "ASSET_NOT_DISPOSABLE", "仅折旧中 / 已停用 / 已折旧完毕的固定资产可以发起处置", 409
        )
    disposal_type = str(payload.get("disposalType") or "")
    if disposal_type not in DISPOSAL_TYPES:
        raise AccountingError("INVALID_DISPOSAL_TYPE", "无效的处置方式", 422)
    disposal_date = _parse_date(payload.get("disposalDate"), field="处置日期")

    accumulated = money(asset.accumulated_depreciation or ZERO)
    nbv = money(money(asset.original_value) - accumulated)
    proceeds = money(payload.get("proceedsAmount") or ZERO, allow_negative=False)
    vat = money(payload.get("outputVatAmount") or ZERO, allow_negative=False)
    if disposal_type == "sale":
        gain_loss = money(proceeds - nbv)
    elif disposal_type == "donate":
        gain_loss = money(-(nbv + money(vat)))
    else:
        gain_loss = money(proceeds - nbv) if proceeds > ZERO else money(-nbv)

    disposal = FixedAssetDisposal(
        company_id=company_id,
        asset_id=asset.id,
        disposal_type=disposal_type,
        disposal_date=disposal_date,
        proceeds_amount=proceeds,
        output_vat_amount=vat,
        accumulated_depreciation_snapshot=accumulated,
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

    # 资产置处置中；处置当月 planned 折旧行置 skipped（当月不再计提）
    previous_status = asset.status
    asset.status = "disposing"
    asset.updated_by = user_id
    asset.updated_at = _now()
    asset.version += 1
    disposal_period = f"{disposal_date.year:04d}-{disposal_date.month:02d}"
    await session.execute(
        FixedAssetDepreciationSchedule.__table__.update()
        .where(
            FixedAssetDepreciationSchedule.asset_id == asset.id,
            FixedAssetDepreciationSchedule.period_start == disposal_period,
            FixedAssetDepreciationSchedule.status == "planned",
        )
        .values(status="skipped")
    )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="fixed_asset.disposal.create",
        entity_type="fixed_asset_disposal",
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
        select(FixedAssetDisposal, FixedAsset)
        .join(FixedAsset, FixedAsset.id == FixedAssetDisposal.asset_id)
        .where(FixedAssetDisposal.company_id == company_id)
        .order_by(FixedAssetDisposal.created_at.desc())
    )
    if asset_id:
        query = query.where(FixedAssetDisposal.asset_id == asset_id)
    if status:
        query = query.where(FixedAssetDisposal.status == status)
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
        action="fixed_asset.disposal.submit",
        entity_type="fixed_asset_disposal",
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
        action="fixed_asset.disposal.approve",
        entity_type="fixed_asset_disposal",
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
        asset.status = (
            "active"
            if asset.accumulated_depreciation < asset.original_value - asset.residual_value
            else "fully_depreciated"
        )
        asset.updated_by = user_id
        asset.updated_at = _now()
        asset.version += 1
    # 恢复当月 planned 折旧行
    disposal_period = f"{disposal.disposal_date.year:04d}-{disposal.disposal_date.month:02d}"
    await session.execute(
        FixedAssetDepreciationSchedule.__table__.update()
        .where(
            FixedAssetDepreciationSchedule.asset_id == disposal.asset_id,
            FixedAssetDepreciationSchedule.period_start == disposal_period,
            FixedAssetDepreciationSchedule.status == "skipped",
        )
        .values(status="planned")
    )
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="fixed_asset.disposal.cancel",
        entity_type="fixed_asset_disposal",
        entity_id=disposal.id,
        request_id=request_id,
        after={"status": disposal.status, "assetStatus": asset.status},
        entity_version=disposal.version,
    )
    return {"ok": True, "status": disposal.status, "assetStatus": asset.status}
