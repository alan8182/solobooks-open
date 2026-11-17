# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""期初税务备忘 → 企业所得税预缴测算（A200000 第 24/30 行）。

备忘随期初保存（见 ``opening_balance_service.save_opening_balances``），
只代表 ``startPeriod`` 所属纳税年度的一条数据：跨年沿用会把同一笔亏损 /
预缴税额重复抵扣，所以年度不匹配时一律按 0 处理。

第 24 行「弥补以前年度亏损」不能超过当年所得额，多出的亏损继续留在税局
亏损台账里，本系统不结转（下年期初由会计按剩余额重新登记）。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select

from core.money import ZERO, money
from db.models import CompanyProfile
from db.session import session_scope

logger = logging.getLogger(__name__)


def tax_opening_fiscal_year(memo: Optional[dict[str, Any]]) -> Optional[int]:
    """备忘所属纳税年度：取 startPeriod（如 ``2026-06``）的年份。"""
    start = str((memo or {}).get("startPeriod") or "").strip()
    head = start[:4]
    return int(head) if head.isdigit() else None


def apply_tax_opening(
    profit_before_loss: Decimal,
    memo: Optional[dict[str, Any]],
    fiscal_year: int,
) -> dict[str, Any]:
    """把期初备忘折算成本期可用的第 24/30 行金额。"""
    applies = bool(memo) and tax_opening_fiscal_year(memo) == fiscal_year
    # 脏数据（负数）按 0 处理：负的「亏损/已预缴」会把应纳税所得额反向做大
    prior_loss = max(money(memo.get("priorYearLoss") or 0), ZERO) if applies else ZERO
    prepaid = max(money(memo.get("prepaidCitTax") or 0), ZERO) if applies else ZERO
    loss_used = min(prior_loss, max(money(profit_before_loss), ZERO))
    return {
        "applies": applies,
        "priorYearLoss": prior_loss,
        "lossDeducted": loss_used,
        "prepaidCitTax": prepaid,
    }


async def get_tax_opening_memo(
    company_id: str, *, session=None,
) -> Optional[dict[str, Any]]:
    """读取企业档案里的 taxOpening 备忘；读不到时按「无备忘」处理。"""

    async def _load(db) -> Optional[dict[str, Any]]:
        row = (
            await db.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        memo = (row.profile or {}).get("taxOpening") if row and row.profile else None
        if not isinstance(memo, dict) or not memo:
            return None
        return {
            "startPeriod": str(memo.get("startPeriod") or "").strip(),
            "priorYearLoss": memo.get("priorYearLoss") or 0,
            "prepaidCitTax": memo.get("prepaidCitTax") or 0,
        }

    try:
        if session is not None:
            return await _load(session)
        async with session_scope() as db:
            return await _load(db)
    except Exception:  # noqa: BLE001 —— 备忘读取失败只降级为「未登记」，不拖垮申报测算
        logger.warning("tax opening memo lookup failed company=%s", company_id, exc_info=True)
        return None
