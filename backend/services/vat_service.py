# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""小规模纳税人增值税口径（单一事实来源）。

本模块只做三件事，任何地方需要"这笔收入含多少增值税"都必须走这里，
避免 `record` / `invoice` / `vat_filing` / `report` 各自按不同税率和反算方式处理：

1. ``get_vat_config``：从公司档案读出纳税人身份、征收率、申报周期、起征点。
2. ``split_income_vat``：含税金额拆成不含税收入与增值税额（税额取差额，保证借贷必平）。
3. ``is_vat_sales_entry``：判断一条业务是否属于增值税销售行为（只有销售类收入才计提）。

口径边界：只支持小规模纳税人（征收率 1% / 3%、按期起征点免税）。
遇到 ``general`` 直接抛错，而不是用错口径继续记账——错口径的报表比没有报表更危险。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.money import ZERO, money, rate

SMALL_SCALE = "small_scale"
GENERAL = "general"

# 2023 年第 19 号公告延续至 2027-12-31：小规模 3% 征收率减按 1%。
REDUCED_RATE = Decimal("0.01")
STATUTORY_RATE = Decimal("0.03")
MONTH_THRESHOLD = Decimal("100000.00")
QUARTER_THRESHOLD = Decimal("300000.00")
SUPPORTED_RATES = (REDUCED_RATE, STATUTORY_RATE)

# 应交税费-应交增值税（小规模纳税人专用）。
# 注意：小规模简易计税不设"销项税额""进项税额"专栏，那是增值税一般纳税人的科目用法。
VAT_PAYABLE_ACCOUNT = "2221026"
VAT_PAYABLE_ACCOUNT_NAME = "应交增值税（小规模纳税人专用）"
VAT_EXEMPT_INCOME_ACCOUNT = "5301"
VAT_EXEMPT_INCOME_ACCOUNT_NAME = "营业外收入"

# 只有销售商品/服务类收入才计提增值税；营业外收入、利息收入、往来款不计提。
VAT_SALES_CATEGORIES = {"主营业务收入", "其他业务收入"}

_TAXPAYER_ALIASES = {
    "small_scale": SMALL_SCALE,
    "smallscale": SMALL_SCALE,
    "小规模": SMALL_SCALE,
    "小规模纳税人": SMALL_SCALE,
    "general": GENERAL,
    "一般纳税人": GENERAL,
    "general_taxpayer": GENERAL,
}


class UnsupportedTaxpayerType(ValueError):
    """一般纳税人尚未实现，必须显式失败而不是静默用错误口径。"""


@dataclass(frozen=True)
class VatConfig:
    taxpayer_type: str = SMALL_SCALE
    rate: Decimal = REDUCED_RATE
    filing_cycle: str = "quarter"
    threshold: Decimal = QUARTER_THRESHOLD

    @property
    def is_small_scale(self) -> bool:
        return self.taxpayer_type == SMALL_SCALE

    def threshold_for(self, period_type: str | None = None) -> Decimal:
        cycle = (period_type or self.filing_cycle or "quarter").lower()
        return MONTH_THRESHOLD if cycle.startswith("month") else QUARTER_THRESHOLD


def normalize_taxpayer_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return SMALL_SCALE
    return _TAXPAYER_ALIASES.get(text.lower(), _TAXPAYER_ALIASES.get(text, SMALL_SCALE))


def _normalize_cycle(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("month") or text in {"月", "月报", "月度"}:
        return "month"
    return "quarter"


def get_vat_config(company: dict[str, Any] | None) -> VatConfig:
    """从公司档案推导增值税配置。缺省即小规模、1%、季报。"""
    payload = company or {}
    taxpayer_type = normalize_taxpayer_type(payload.get("taxpayerType"))
    if taxpayer_type == GENERAL:
        raise UnsupportedTaxpayerType(
            "当前版本只支持小规模纳税人；一般纳税人请使用专业财税软件，"
            "避免以错误口径生成申报底稿"
        )
    raw_rate = payload.get("vatRate")
    if raw_rate in (None, ""):
        configured_rate = REDUCED_RATE
    else:
        configured_rate = rate(raw_rate)
    if configured_rate not in SUPPORTED_RATES:
        raise UnsupportedTaxpayerType(
            f"小规模征收率只支持 1% 或 3%，当前配置为 {configured_rate}"
        )
    return VatConfig(
        taxpayer_type=taxpayer_type,
        rate=configured_rate,
        filing_cycle=_normalize_cycle(payload.get("vatFilingCycle")),
        threshold=QUARTER_THRESHOLD,
    )


def is_vat_sales_entry(category: Any) -> bool:
    return str(category or "").strip() in VAT_SALES_CATEGORIES


async def get_vat_config_from_session(session: Any, company_id: str) -> VatConfig:
    """在已打开的事务会话内读公司档案，避免额外连接导致 SQLite 锁竞争。"""
    from sqlalchemy import select

    from db.models import Company

    company = (
        await session.execute(select(Company).where(Company.company_id == company_id))
    ).scalar_one_or_none()
    return get_vat_config(company.payload if company else None)


async def get_vat_config_by_id(company_id: str) -> VatConfig:
    from services.account_service import get_company

    return get_vat_config(await get_company(company_id))


def split_income_vat(
    amount: Any,
    rate_value: Any = None,
    explicit_tax: Any = None,
) -> tuple[Decimal, Decimal]:
    """把含税金额拆成（不含税收入, 增值税额）。

    税额一律取 ``含税 - 不含税`` 的差额，而不是单独四舍五入 ``net * rate``，
    否则两条分录相加会出现 1 分钱差额，凭证直接被借贷平衡校验拒绝。
    """
    gross = money(amount)
    if gross < ZERO:
        raise ValueError("含税金额不能为负数")
    if explicit_tax not in (None, ""):
        tax = money(explicit_tax)
        if tax < ZERO or tax > gross:
            raise ValueError("发票税额超出含税金额范围")
        return money(gross - tax), tax
    applied_rate = rate(REDUCED_RATE if rate_value in (None, "") else rate_value)
    if applied_rate <= 0:
        return gross, ZERO
    net = money(gross / (Decimal("1") + applied_rate))
    return net, money(gross - net)


def vat_amount_from_voucher_lines(voucher: dict[str, Any] | None) -> Decimal:
    """从凭证行里取已计提的小规模增值税额（用于免税转出与申报取数）。"""
    lines = (voucher or {}).get("credit") or []
    if isinstance(lines, str):
        return ZERO
    total = sum(
        (money(line.get("amount") or ZERO) for line in lines
         if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
        ZERO,
    )
    debit_side = sum(
        (money(line.get("amount") or ZERO) for line in (voucher or {}).get("debit") or []
         if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
        ZERO,
    )
    return money(total - debit_side)
