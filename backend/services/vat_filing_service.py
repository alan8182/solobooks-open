# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Deterministic small-scale VAT filing assistance for 2026-2027."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from core.money import ZERO, money
from services.tax_filing_export_service import parse_period
# 政策常量与 vat_service 单一来源（政策续期只改一处）
from services.vat_service import (  # noqa: F401
    MONTH_THRESHOLD,
    QUARTER_THRESHOLD,
    REDUCED_RATE,
    STATUTORY_RATE,
    VAT_PAYABLE_ACCOUNT,
)

PeriodType = Literal["month", "quarter"]

POLICY_VERSION = "VAT-CN-2026-10+STA-2026-4"
VAT_REVENUE_ACCOUNTS = {"5001", "5051"}
GOODS_KEYWORDS = ("货物", "商品", "材料", "加工", "修理", "修配")


def _d2(value: Any) -> Decimal:
    return money(value or ZERO)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _voucher_lines(entry: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    value = entry.get("voucherLines") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _revenue_effect(entry: dict[str, Any]) -> Decimal:
    voucher = _voucher_lines(entry)
    credit = sum(
        (_d2(line.get("amount")) for line in voucher.get("credit", [])
         if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNTS),
        ZERO,
    )
    debit = sum(
        (_d2(line.get("amount")) for line in voucher.get("debit", [])
         if str(line.get("accountCode") or "") in VAT_REVENUE_ACCOUNTS),
        ZERO,
    )
    return _d2(credit - debit)


def _booked_vat(entry: dict[str, Any]) -> Decimal:
    """该笔收入已计提的小规模应交增值税额（未做价税分离的历史数据为 0）。"""
    voucher = _voucher_lines(entry)
    credit = sum(
        (_d2(line.get("amount")) for line in voucher.get("credit", [])
         if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
        ZERO,
    )
    debit = sum(
        (_d2(line.get("amount")) for line in voucher.get("debit", [])
         if str(line.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT),
        ZERO,
    )
    return _d2(credit - debit)


def _net_sales(entry: dict[str, Any], revenue: Decimal, rate_value: Decimal = REDUCED_RATE) -> tuple[Decimal, bool]:
    """返回（不含税销售额, 是否已由凭证完成价税分离）。

    新凭证在收入确认时已拆分，收入科目贷方就是不含税额；
    历史凭证按含税全额入收入，需要按公司配置的征收率还原
    （3% 征收率纳税人不能按 1% 优惠税率倒算）。
    """
    if entry.get("vatSeparated") or _booked_vat(entry) > ZERO:
        return revenue, True
    return _round_tax(revenue / (Decimal("1") + rate_value)), False


def _sales_bucket(entry: dict[str, Any]) -> Literal["goods_labor", "services"]:
    explicit = str(entry.get("vatSalesCategory") or "").strip()
    if explicit in {"goods_labor", "services"}:
        return explicit  # type: ignore[return-value]
    text = " ".join(str(entry.get(key) or "") for key in ("category", "title"))
    return "goods_labor" if any(keyword in text for keyword in GOODS_KEYWORDS) else "services"


def _round_tax(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_small_scale_vat_assist(
    entries: list[dict[str, Any]],
    period: str,
    period_type: PeriodType,
    rate_value: Decimal = REDUCED_RATE,
) -> dict[str, Any]:
    """生成小规模纳税人申报辅助字段。

    rate_value：公司配置的征收率（1% 优惠 / 3% 法定），用于历史含税凭证的
    价税分离还原；新凭证已拆分不受影响。
    """
    period_range = parse_period(period, period_type)
    threshold = MONTH_THRESHOLD if period_type == "month" else QUARTER_THRESHOLD
    goods_sales = ZERO
    service_sales = ZERO
    booked_vat_total = ZERO
    source_items: list[dict[str, Any]] = []
    missing_invoice_count = 0
    inferred_category_count = 0

    # 期末损益结转是汇总分录：借记收入科目把本期销售转平，按科目净额算销售额
    # 会恒等于 0。结转凭证与其反结账红字一并退出销售口径。
    closing_ids = {
        str(entry.get("id") or "")
        for entry in entries
        if str(entry.get("entryType") or "") == "closing"
    }

    for entry in entries:
        if str(entry.get("entryType") or "") == "closing" or entry.get("isClosingTransfer"):
            continue
        if str(entry.get("reversalOf") or "") in closing_ids:
            continue
        occurred_on = _parse_date(entry.get("occurDate") or entry.get("date"))
        if not occurred_on or not (period_range.start <= occurred_on <= period_range.end):
            continue
        revenue = _revenue_effect(entry)
        if revenue == ZERO:
            continue
        # 申报表销售额一律是不含税口径
        net_revenue, separated = _net_sales(entry, revenue, rate_value)
        booked_vat_total += _booked_vat(entry)
        bucket = _sales_bucket(entry)
        if not entry.get("vatSalesCategory"):
            inferred_category_count += 1
        invoice_type = str(entry.get("invoiceType") or "").strip()
        invoice_number = str(entry.get("invoiceNumber") or "").strip()
        billing_status = str(entry.get("billingStatus") or "").strip()
        # billingStatus 是老板在待办「开票状态确认」里给出的结论（未开票收入/已开票）：
        # 已有结论的销售不再计入「缺少发票类型或号码」拦截，否则待办处理完、
        # 申报页仍反复拦截，两处口径脱节（2026-10-25 真实账套反馈）。
        if billing_status not in {"no_invoice", "issued", "has_invoice"} and (
            not invoice_type or not invoice_number
        ):
            missing_invoice_count += 1
        if bucket == "goods_labor":
            goods_sales += net_revenue
        else:
            service_sales += net_revenue
        source_items.append({
            "entryId": str(entry.get("id") or ""),
            "occurDate": occurred_on.isoformat(),
            "title": str(entry.get("title") or entry.get("category") or "营业收入"),
            "salesCategory": bucket,
            "invoiceType": invoice_type or "未获取",
            "invoiceNumber": invoice_number,
            "salesAmount": net_revenue,
            "vatSeparated": separated,
        })

    goods_sales = _d2(goods_sales)
    service_sales = _d2(service_sales)
    total_sales = _d2(goods_sales + service_sales)
    booked_vat_total = _d2(booked_vat_total)
    below_threshold = total_sales < threshold

    if below_threshold:
        # 销售额未达起征点：全额免征。免税额优先取账面已计提的增值税
        # （新凭证已做价税分离），历史含税凭证退化为按 3% 征收率还原。
        taxable_base = total_sales
        vat_before_relief = _round_tax(taxable_base * STATUTORY_RATE)
        vat_reduction = ZERO
        vat_exemption = booked_vat_total if booked_vat_total > ZERO else vat_before_relief
        vat_payable = ZERO
    else:
        taxable_base = total_sales
        vat_before_relief = _round_tax(taxable_base * STATUTORY_RATE)
        vat_reduction = _round_tax(taxable_base * (STATUTORY_RATE - REDUCED_RATE))
        vat_exemption = ZERO
        vat_payable = _round_tax(vat_before_relief - vat_reduction)

    # Hangzhou urban rate 7%; small-scale taxpayers receive the 50% "six taxes
    # and two fees" reduction through 2027. When VAT is zero, all additions are zero.
    city_maintenance_tax = _round_tax(vat_payable * Decimal("0.07") * Decimal("0.5"))
    education_surcharge = _round_tax(vat_payable * Decimal("0.03") * Decimal("0.5"))
    local_education_surcharge = _round_tax(vat_payable * Decimal("0.02") * Decimal("0.5"))
    surcharge_total = _d2(city_maintenance_tax + education_surcharge + local_education_surcharge)

    warnings: list[str] = []
    blocking_issues: list[str] = []
    if missing_invoice_count:
        blocking_issues.append(
            f"{missing_invoice_count}笔销售缺少发票类型或号码，需确认是否开具专票、放弃免税或未开票"
        )
    if inferred_category_count:
        warnings.append(f"{inferred_category_count}笔销售按业务名称推断为服务类，请申报前复核")
    if total_sales < ZERO:
        blocking_issues.append("本期销售额为负数，请核对跨期退款和红字发票")

    goods_input_sales = goods_sales
    service_input_sales = service_sales
    exempt_goods_sales = goods_input_sales if below_threshold else ZERO
    exempt_service_sales = service_input_sales if below_threshold else ZERO

    filing_fields = [
        {"code": "simple.goods.rate_1", "section": "货物及劳务", "label": "1%征收率", "amount": goods_input_sales, "editable": True},
        {"code": "simple.goods.rate_3", "section": "货物及劳务", "label": "3%征收率", "amount": ZERO, "editable": True},
        {"code": "simple.goods.exempt", "section": "货物及劳务", "label": "免税销售额", "amount": exempt_goods_sales, "editable": False},
        {"code": "simple.services.rate_1", "section": "服务、不动产和无形资产", "label": "1%征收率", "amount": service_input_sales, "editable": True},
        {"code": "simple.services.rate_3", "section": "服务、不动产和无形资产", "label": "3%征收率", "amount": ZERO, "editable": True},
        {"code": "simple.services.rate_5", "section": "服务、不动产和无形资产", "label": "5%征收率", "amount": ZERO, "editable": True},
        {"code": "simple.services.property", "section": "服务、不动产和无形资产", "label": "其中：销售不动产", "amount": ZERO, "editable": True},
        {"code": "simple.services.housing_1_5", "section": "服务、不动产和无形资产", "label": "其中：出租住房减按1.5%", "amount": ZERO, "editable": True},
        {"code": "simple.services.exempt", "section": "服务、不动产和无形资产", "label": "免税销售额", "amount": exempt_service_sales, "editable": False},
        {"code": "simple.vat_reduction", "section": "税费试算", "label": "增值税减税额合计", "amount": vat_reduction, "editable": False},
        {"code": "simple.vat_exemption", "section": "税费试算", "label": "增值税免税额合计", "amount": vat_exemption, "editable": False},
        {"code": "simple.vat_payable", "section": "税费试算", "label": "本期应补（退）增值税额", "amount": vat_payable, "editable": False},
        {"code": "simple.city_maintenance", "section": "附加税费", "label": "城市维护建设税", "amount": city_maintenance_tax, "editable": False},
        {"code": "simple.education", "section": "附加税费", "label": "教育费附加", "amount": education_surcharge, "editable": False},
        {"code": "simple.local_education", "section": "附加税费", "label": "地方教育附加", "amount": local_education_surcharge, "editable": False},
    ]

    return {
        "period": period_range.label,
        "periodType": period_type,
        "periodStart": period_range.start.isoformat(),
        "periodEnd": period_range.end.isoformat(),
        "taxpayerType": "small_scale",
        "policyVersion": POLICY_VERSION,
        "threshold": threshold,
        "isBelowThreshold": below_threshold,
        "exemptionApplied": below_threshold,
        "goodsLaborSales": goods_sales,
        "serviceSales": service_sales,
        "totalSales": total_sales,
        "taxableBase": taxable_base,
        "vatBeforeRelief": vat_before_relief,
        "vatReduction": vat_reduction,
        "vatExemption": vat_exemption,
        "totalVatPayable": vat_payable,
        "cityMaintenanceTax": city_maintenance_tax,
        "educationSurcharge": education_surcharge,
        "localEducationSurcharge": local_education_surcharge,
        "surchargeTotal": surcharge_total,
        "items": source_items,
        "filingFields": filing_fields,
        "warnings": warnings,
        "blockingIssues": blocking_issues,
        "readyToFile": not blocking_issues,
        "sourceEntryCount": len(source_items),
    }
