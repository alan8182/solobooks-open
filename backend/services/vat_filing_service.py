"""Deterministic small-scale VAT filing assistance for 2026-2027."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from core.money import ZERO, money
from services.tax_filing_export_service import parse_period

PeriodType = Literal["month", "quarter"]

POLICY_VERSION = "VAT-CN-2026-10+STA-2026-4"
REDUCED_RATE = Decimal("0.01")
STATUTORY_RATE = Decimal("0.03")
MONTH_THRESHOLD = Decimal("100000.00")
QUARTER_THRESHOLD = Decimal("300000.00")
VAT_REVENUE_ACCOUNTS = {"6001", "6051"}
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
) -> dict[str, Any]:
    period_range = parse_period(period, period_type)
    threshold = MONTH_THRESHOLD if period_type == "month" else QUARTER_THRESHOLD
    goods_sales = ZERO
    service_sales = ZERO
    source_items: list[dict[str, Any]] = []
    missing_invoice_count = 0
    inferred_category_count = 0

    for entry in entries:
        occurred_on = _parse_date(entry.get("occurDate") or entry.get("date"))
        if not occurred_on or not (period_range.start <= occurred_on <= period_range.end):
            continue
        revenue = _revenue_effect(entry)
        if revenue == ZERO:
            continue
        bucket = _sales_bucket(entry)
        if not entry.get("vatSalesCategory"):
            inferred_category_count += 1
        invoice_type = str(entry.get("invoiceType") or "").strip()
        invoice_number = str(entry.get("invoiceNumber") or "").strip()
        if not invoice_type or not invoice_number:
            missing_invoice_count += 1
        if bucket == "goods_labor":
            goods_sales += revenue
        else:
            service_sales += revenue
        source_items.append({
            "entryId": str(entry.get("id") or ""),
            "occurDate": occurred_on.isoformat(),
            "title": str(entry.get("title") or entry.get("category") or "营业收入"),
            "salesCategory": bucket,
            "invoiceType": invoice_type or "未获取",
            "invoiceNumber": invoice_number,
            "salesAmount": revenue,
        })

    goods_sales = _d2(goods_sales)
    service_sales = _d2(service_sales)
    total_sales = _d2(goods_sales + service_sales)
    below_threshold = total_sales < threshold

    if below_threshold:
        taxable_base = total_sales
        vat_before_relief = _round_tax(taxable_base * STATUTORY_RATE)
        vat_reduction = ZERO
        vat_exemption = vat_before_relief
        vat_payable = ZERO
    else:
        taxable_base = _round_tax(total_sales / (Decimal("1") + REDUCED_RATE))
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

    goods_input_sales = goods_sales if below_threshold else _round_tax(goods_sales / (Decimal("1") + REDUCED_RATE))
    service_input_sales = service_sales if below_threshold else _round_tax(service_sales / (Decimal("1") + REDUCED_RATE))
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
