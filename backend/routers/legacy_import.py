# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""旧账导入路由（P0 WP4）。

parse 一律只读、返回 ``{valid, errors, warnings, mapping, totals}``；
confirm 才落库，且余额路径沿用期初的 step-up 二次认证（header ``Step-Up-Token``）。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header

from db.session import session_scope
from deps.auth import AuthContext, require_auth
from services.accounting_core_service import ensure_tenant
from services.audit_service import log_action
from services.legacy_import_service import (
    confirm_balance_import,
    confirm_voucher_import,
    link_opening_partners,
    parse_balance_rows,
    parse_voucher_rows,
)

router = APIRouter(prefix="/api/legacy-import", tags=["legacy-import"], dependencies=[Depends(require_auth)])


async def _company_start_date(company_id: str) -> str:
    from services.account_service import get_company

    company = await get_company(company_id) or {}
    return str(company.get("startDate") or "").strip()


async def _ensure_ready(company_id: str, user_id: str) -> None:
    """科目表按需初始化。

    必须用独立事务提交后再解析：parse 走自己的 session，若科目只在当前请求的
    未提交事务里，另一条连接看不到，会把每行都判成"科目不存在"。
    """
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)


@router.post("/balance/parse")
async def parse_balance(
    rows: list[dict[str, Any]] = Body(..., embed=True),
    mapping: Optional[dict[str, str]] = Body(None),
    ctx: AuthContext = Depends(require_auth),
):
    """科目余额表解析：科目存在性校验 + 按名称匹配建议 + 借贷合计。"""
    await _ensure_ready(ctx.company_id, ctx.user_id)
    return await parse_balance_rows(ctx.company_id, rows, mapping_override=mapping)


@router.post("/balance/confirm")
async def confirm_balance(
    items: list[dict[str, Any]] = Body(..., embed=True),
    fiscal_year: int = Body(...),
    start_period: Optional[str] = Body(None),
    mapping: Optional[dict[str, str]] = Body(None),
    step_up_token: Optional[str] = Header(None, alias="Step-Up-Token"),
    ctx: AuthContext = Depends(require_auth),
):
    """确认导入期初：包装 save_opening_balances（借贷平衡/YTD/幂等沿用既有保障）。"""
    result = await confirm_balance_import(
        ctx.company_id,
        ctx.user_id,
        items,
        fiscal_year=fiscal_year,
        step_up_token=step_up_token,
        start_period=start_period,
        mapping=mapping,
    )
    await log_action(
        ctx.company_id, ctx.user_id, "import", "opening_balance", None,
        {"count": len(items), "fiscalYear": fiscal_year, "source": "legacy_import"},
    )
    return result


@router.post("/vouchers/parse")
async def parse_vouchers(
    rows: list[dict[str, Any]] = Body(..., embed=True),
    mapping: Optional[dict[str, str]] = Body(None),
    ctx: AuthContext = Depends(require_auth),
):
    """凭证分录解析：逐凭证借贷平衡 + 科目存在性 + 启用日期校验。"""
    await _ensure_ready(ctx.company_id, ctx.user_id)
    return await parse_voucher_rows(
        ctx.company_id,
        rows,
        start_date=await _company_start_date(ctx.company_id),
        mapping_override=mapping,
    )


@router.post("/vouchers/confirm")
async def confirm_vouchers(
    vouchers: list[dict[str, Any]] = Body(..., embed=True),
    batch_id: str = Body(""),
    post: bool = Body(False),
    mapping: Optional[dict[str, str]] = Body(None),
    ctx: AuthContext = Depends(require_auth),
):
    """确认导入历史凭证：默认 draft 待审，post=true 才批量过账。"""
    result = await confirm_voucher_import(
        ctx.company_id,
        ctx.user_id,
        vouchers,
        batch_id=batch_id,
        post=post,
        mapping=mapping,
    )
    await log_action(
        ctx.company_id, ctx.user_id, "import", "legacy_vouchers", None,
        {
            "batchId": result.get("batchId"),
            "imported": result.get("imported"),
            "posted": result.get("posted"),
        },
    )
    return result


@router.post("/link-opening-partners")
async def link_partners(
    ctx: AuthContext = Depends(require_auth),
):
    """期初旧格式 partner_name 文本 → partners 主数据（幂等）。"""
    result = await link_opening_partners(ctx.company_id, ctx.user_id)
    await log_action(ctx.company_id, ctx.user_id, "link", "opening_partner", None, result)
    return result


@router.get("/mapping")
async def get_mapping(ctx: AuthContext = Depends(require_auth)):
    from services.legacy_import_service import load_mapping

    return {"mapping": await load_mapping(ctx.company_id)}
