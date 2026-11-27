# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝商家账单解析与导入服务。

支付宝「业务明细」CSV 的口径与微信账单差异较大，解析时全部在这里收口：

- **GBK 编码**、CRLF、列值末尾可能带制表符；
- 文件首尾是 ``#`` 开头的说明/合计行，中间才是 CSV；
- 退款行与原交易**共用支付宝交易号**，靠「退款批次号/请求号」区分，
  且退款行的创建时间是原交易时间，业务日期必须取「完成时间」；
- 「商家实收」已包含平台补贴，「支付宝红包/集分宝/支付宝优惠/券核销」
  是平台补贴，按用户口径**不计入主营业务收入**，贷记营业外收入；
- 「服务费」是负数，入账时转成正数费用；「分润」暂不支持，非零时拦住。
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Set

from core.money import ZERO
from services.database import get_conn
from services.entry_service import save_entries_bulk
from services.platform_bill_common import (
    ALIPAY_CLEARING_CODE,
    load_existing_source_keys,
    parse_money as _money,
    split_revenue_vat as _split_revenue_vat,
)
from services.vat_service import (
    VAT_PAYABLE_ACCOUNT,
    VAT_PAYABLE_ACCOUNT_NAME,
    get_vat_config_by_id,
    is_vat_sales_entry,
)

logger = logging.getLogger(__name__)

ALIPAY_SOURCE = "alipay_bill"
ALIPAY_PLATFORM = "alipay"
ALIPAY_CLEARING_NAME = "支付宝待结算"
ALIPAY_MERCHANT = "支付宝"

REVENUE_CODE = "5001"
REVENUE_NAME = "主营业务收入"
FEE_CODE = "5603002"
FEE_NAME = "财务费用-手续费"
SUBSIDY_CODE = "5301"
SUBSIDY_NAME = "营业外收入"

# 平台补贴列：这些钱由支付宝/活动方承担，商家实收里能看到，但不是销售收入。
_SUBSIDY_COLUMNS = (
    "支付宝红包（元）",
    "集分宝（元）",
    "支付宝优惠（元）",
    "券核销金额（元）",
)

_COL = {
    "trade_no": "支付宝交易号",
    "merchant_order_no": "商户订单号",
    "business_type": "业务类型",
    "product": "商品名称",
    "created_at": "创建时间",
    "completed_at": "完成时间",
    "counterparty": "对方账户",
    "order_amount": "订单金额（元）",
    "received": "商家实收（元）",
    "merchant_discount": "商家优惠（元）",
    "merchant_red": "商家红包消费金额（元）",
    "card_amount": "卡消费金额（元）",
    "refund_ref": "退款批次号/请求号",
    "service_fee": "服务费（元）",
    "profit_share": "分润（元）",
    "remark": "备注",
}

_BUSINESS_TYPES = {"交易": "SUCCESS", "退款": "REFUND"}


def _decode_text(content: bytes) -> str:
    """支付宝默认 GBK；仍优先尝试 UTF-8，避免未来改成 UTF-8 后乱码。"""
    for encoding in ("utf-8-sig", "gb18030", "gbk"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "�" not in text:
            return text
    return content.decode("gb18030", errors="replace")


def _read_cells(line: str) -> List[str]:
    try:
        return [cell.strip() for cell in next(csv.reader([line]))]
    except csv.Error:
        return [cell.strip() for cell in line.split(",")]


def _parse_cn_date(value: str) -> date | None:
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_header_meta(lines: List[str]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"merchant_account": "", "period_start": None, "period_end": None}
    for line in lines:
        if not line.startswith("#"):
            continue
        match = re.search(r"#账号[：:]\s*\[([^\]]+)\]", line)
        if match:
            meta["merchant_account"] = match.group(1).strip()
        match = re.search(r"#起始日期[：:]\s*\[([^\]]+)\]", line)
        if match:
            meta["period_start"] = _parse_cn_date(match.group(1))
        # 终止日期与起始日期在同一行，前面没有 # 前缀
        match = re.search(r"终止日期[：:]\s*\[([^\]]+)\]", line)
        if match:
            meta["period_end"] = _parse_cn_date(match.group(1))
    return meta


def _parse_footer(lines: List[str]) -> Dict[str, Any]:
    """解析 ``#交易合计`` / ``#退款合计``，只用来勾稽，不替代明细汇总。"""
    footer: Dict[str, Any] = {}
    for line in lines:
        match = re.search(
            r"#交易合计[：:]\s*(\d+)\s*笔[，,]\s*商家实收共\s*(-?[\d.]+)\s*元",
            line,
        )
        if match:
            footer["transaction_count"] = int(match.group(1))
            footer["transaction_received"] = _money(match.group(2))
        match = re.search(
            r"#退款合计[：:]\s*(\d+)\s*笔[，,]\s*商家实收退款共\s*(-?[\d.]+)\s*元",
            line,
        )
        if match:
            footer["refund_count"] = int(match.group(1))
            footer["refund_received"] = _money(match.group(2))
    return footer


def _transaction_key(status: str, item: Dict[str, Any]) -> str:
    trade_no = str(item.get("trade_no") or "").strip()
    merchant_order_no = str(item.get("merchant_order_no") or "").strip()
    identity = trade_no or merchant_order_no
    if status == "REFUND":
        refund_ref = str(item.get("refund_ref") or "").strip()
        if refund_ref:
            return f"refund:{identity}:{refund_ref}"
        return f"refund:{identity}:{item.get('occurred_on')}:{item.get('received_amount')}"
    return f"success:{identity}"


async def _load_existing_alipay_keys(company_id: str) -> set[str]:
    return await load_existing_source_keys(
        company_id,
        ALIPAY_SOURCE,
        legacy_fields=("alipay_transaction_key",),
    )


async def _load_existing_alipay_summary_periods(company_id: str) -> List[tuple[str, str]]:
    """已导入的支付宝汇总凭证期间，用于拦截重叠账单，防止同一段时间重复入账。"""
    if not company_id:
        return []
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries "
            "WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = ? "
            # JSON 布尔的两库表示不同：SQLite json_extract 取出整数 1，PG 取出文本
            # 'true'。`= 1` 在 PG 上永远不命中，重叠拦截静默失效（2026-11-20 实测）。
            # 本服务对该键只写 true、从不写 false/0，用 IS NOT NULL 判存在才可移植。
            "AND (json_extract(payload, '$.is_monthly_summary') IS NOT NULL "
            "OR json_extract(payload, '$.isMonthlySummary') IS NOT NULL)",
            (company_id, ALIPAY_SOURCE),
        )
        rows = await cursor.fetchall()
    except Exception:
        # 静默吞掉方言错误正是这次事故的放大器：查询坏了 → 返回空 → 拦截失效。
        # 必须留痕，否则 PG 上坏掉的 SQL 只会表现为「能重复导入」。
        logger.exception("load_alipay_summary_periods_failed company=%s", company_id)
        return []
    finally:
        await conn.close()

    periods: List[tuple[str, str]] = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not isinstance(payload, dict):
            continue
        start = str(payload.get("billPeriodStart") or "")[:10]
        end = str(payload.get("billPeriodEnd") or "")[:10]
        if start and end:
            periods.append((start, end))
    return periods


async def _has_alipay_detail_in_period(company_id: str, start: str, end: str) -> bool:
    """期间内是否已有逐笔明细凭证（用于阻止「先明细、后汇总」重复入账）。"""
    if not company_id or not start or not end:
        return False
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM entries "
            "WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = ? "
            "AND json_extract(payload, '$.occurDate') >= ? "
            "AND json_extract(payload, '$.occurDate') <= ? "
            # 不能把 json_extract 的结果与整数字面量一起塞进 COALESCE：
            # PG 翻译后是 COALESCE(text, integer)，类型不匹配直接报错
            # （2026-11-20 用户实测 500）。
            # 该键只写 true，逐笔明细不写该键，IS NULL 即「非汇总」。
            "AND json_extract(payload, '$.is_monthly_summary') IS NULL "
            "LIMIT 1",
            (company_id, ALIPAY_SOURCE, start, end),
        )
        return await cursor.fetchone() is not None
    finally:
        await conn.close()


def _overlapping_period(
    start: str, end: str, periods: List[tuple[str, str]],
) -> tuple[str, str] | None:
    for period_start, period_end in periods:
        if start <= period_end and period_start <= end:
            return period_start, period_end
    return None


async def _load_existing_alipay_order_history(
    company_id: str,
    order_nos: Set[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """读取已落库的支付宝订单条目，供跨批次退款核销判断使用。"""
    clean_order_nos = {str(order_no).strip() for order_no in order_nos if str(order_no).strip()}
    if not company_id or not clean_order_nos:
        return {}

    rows = []
    conn = await get_conn()
    try:
        ordered = sorted(clean_order_nos)
        for start in range(0, len(ordered), 400):
            batch = ordered[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            cursor = await conn.execute(
                f"SELECT id, payload FROM entries WHERE company_id = ? "
                f"AND json_extract(payload, '$.source') = ? "
                f"AND json_extract(payload, '$.platform_order_no') IN ({placeholders})",
                (company_id, ALIPAY_SOURCE, *batch),
            )
            rows.extend(await cursor.fetchall())
    finally:
        await conn.close()

    from services.bank_service import get_matched_entry_ids

    matched_entry_ids = await get_matched_entry_ids(company_id)
    history: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        order_no = str(payload.get("platform_order_no") or "").strip()
        if not order_no:
            continue
        entry_id = str(row["id"] or "").strip()
        history.setdefault(order_no, []).append({
            "id": entry_id,
            "payload": payload,
            "bank_matched": entry_id in matched_entry_ids,
        })
    return history


def _historical_order_totals(
    history: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Decimal]]:
    totals_by_order: Dict[str, Dict[str, Decimal]] = {}
    for order_no, items in history.items():
        seen_keys: set[str] = set()
        totals = totals_by_order.setdefault(order_no, {"success": ZERO, "refund": ZERO})
        for item in items:
            payload = item.get("payload") or {}
            if str(payload.get("direction") or "").strip() != "income":
                continue
            key = str(payload.get("dedupFingerprint") or "").strip()
            fallback = f"legacy:{item.get('id') or ''}"
            dedupe_key = key or fallback
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            amount = _money(payload.get("amount"))
            if key.startswith("refund:") or amount < ZERO:
                totals["refund"] += abs(amount)
            else:
                totals["success"] += amount
    return totals_by_order


async def parse_alipay_bill(content: bytes, company_id: str = "") -> Dict[str, Any]:
    """解析支付宝商家「业务明细」CSV，返回预览数据（不入账）。"""
    text = _decode_text(content)
    lines = text.splitlines()

    header_index = None
    for index, line in enumerate(lines):
        cleaned = line.lstrip("﻿").strip()
        if cleaned.startswith(f"{_COL['trade_no']},"):
            header_index = index
            break
    if header_index is None:
        raise ValueError("未找到支付宝业务明细表头，请确认导出的是「业务明细」CSV")

    header = _read_cells(lines[header_index])
    col_index = {name: idx for idx, name in enumerate(header)}
    required = (
        _COL["trade_no"], _COL["business_type"], _COL["product"],
        _COL["completed_at"], _COL["received"], _COL["service_fee"],
    )
    missing = [name for name in required if name not in col_index]
    if missing:
        raise ValueError(f"支付宝业务明细缺少必需列：{'、'.join(missing)}")

    meta = _parse_header_meta(lines[:header_index])
    footer = _parse_footer(lines[header_index + 1:])
    existing_keys = await _load_existing_alipay_keys(company_id)

    def cell(cells: List[str], name: str) -> str:
        index = col_index.get(name)
        if index is None or index >= len(cells):
            return ""
        return str(cells[index] or "").strip()

    valid: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for offset, line in enumerate(lines[header_index + 1:]):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row_no = header_index + offset + 2  # 1-based 文件行号，方便用户定位
        cells = _read_cells(line)
        if len(cells) != len(header):
            errors.append({"row": row_no, "reason": f"列数不一致：表头 {len(header)} 列，本行 {len(cells)} 列"})
            continue
        business_type = cell(cells, _COL["business_type"])
        status = _BUSINESS_TYPES.get(business_type)
        if status is None:
            errors.append({"row": row_no, "reason": f"暂不支持的业务类型：{business_type or '空'}"})
            continue

        try:
            product = cell(cells, _COL["product"])
            trade_no = cell(cells, _COL["trade_no"])
            merchant_order_no = cell(cells, _COL["merchant_order_no"])
            created_at = cell(cells, _COL["created_at"])
            completed_at = cell(cells, _COL["completed_at"])
            refund_ref = cell(cells, _COL["refund_ref"])
            remark = cell(cells, _COL["remark"])

            received = _money(cell(cells, _COL["received"]))
            subsidy = sum(
                (_money(cell(cells, column)) for column in _SUBSIDY_COLUMNS),
                ZERO,
            )
            service_fee = _money(cell(cells, _COL["service_fee"]))
            profit_share = _money(cell(cells, _COL["profit_share"]))
            merchant_red = _money(cell(cells, _COL["merchant_red"]))
            card_amount = _money(cell(cells, _COL["card_amount"]))

            if profit_share != ZERO:
                errors.append({"row": row_no, "reason": f"本期存在分润 {profit_share} 元，暂不支持自动记账，请人工处理"})
                continue
            if merchant_red != ZERO or card_amount != ZERO:
                errors.append({
                    "row": row_no,
                    "reason": f"存在商家红包 {merchant_red} 元或卡消费 {card_amount} 元，暂不支持自动记账，请人工处理",
                })
                continue

            if not trade_no and not merchant_order_no:
                errors.append({"row": row_no, "reason": "缺少支付宝交易号与商户订单号，无法去重"})
                continue

            occurred_on = (completed_at or created_at)[:10]
            if not occurred_on:
                errors.append({"row": row_no, "reason": "缺少创建/完成时间，无法确定记账日期"})
                continue

            revenue = _money(received - subsidy)
            fee = _money(-service_fee)
            item: Dict[str, Any] = {
                "platform": ALIPAY_PLATFORM,
                "status": status,
                "transaction_time": created_at,
                "settlement_time": completed_at,
                "date": occurred_on,
                "occurred_on": occurred_on,
                "product_name": product or "支付宝交易",
                "order_amount": received,
                "received_amount": received,
                "refund_amount": abs(received) if status == "REFUND" else ZERO,
                "platform_subsidy": subsidy,
                "revenue_amount": revenue,
                "fee": fee,
                "settled_amount": _money(received - fee),
                "net_settled_amount": _money(received - fee),
                "order_no": trade_no or merchant_order_no,
                "trade_no": trade_no,
                "merchant_order_no": merchant_order_no,
                "refund_ref": refund_ref,
                "remark": remark,
                "raw": {
                    "business_type": business_type,
                    "order_amount": str(_money(cell(cells, _COL["order_amount"]))),
                    "merchant_discount": str(_money(cell(cells, _COL["merchant_discount"]))),
                },
            }
            item["dedupFingerprint"] = _transaction_key(status, item)
            item["alipay_transaction_key"] = item["dedupFingerprint"]
            item["platform_order_no"] = item["order_no"]
            item["is_duplicate"] = item["dedupFingerprint"] in existing_keys
            valid.append(item)
        except (ValueError, TypeError) as exc:
            errors.append({"row": row_no, "reason": str(exc)})

    success_rows = [row for row in valid if row["status"] == "SUCCESS"]
    refund_rows = [row for row in valid if row["status"] == "REFUND"]
    gross_sales = sum((_money(row["received_amount"]) for row in success_rows), ZERO)
    refund_received = sum((_money(row["received_amount"]) for row in refund_rows), ZERO)
    received_net = _money(gross_sales + refund_received)
    subsidy_total = sum((_money(row["platform_subsidy"]) for row in valid), ZERO)
    fee_total = sum((_money(row["fee"]) for row in valid), ZERO)
    revenue_total = _money(received_net - subsidy_total)
    net_total = _money(received_net - fee_total)

    period_start = meta.get("period_start")
    period_end = meta.get("period_end")
    if period_end and period_start and period_end > period_start:
        # 文件头的「终止日期」是开区间（如 11-19 00:00:00 表示到 11-18 为止）
        period_end = period_end - timedelta(days=1)

    row_dates = [date.fromisoformat(row["occurred_on"]) for row in valid if row["occurred_on"]]
    if row_dates:
        # 文件头缺失或开区间换算偏一天时，用明细日期兜底，保证期间覆盖全部行
        min_date, max_date = min(row_dates), max(row_dates)
        period_start = min(period_start, min_date) if period_start else min_date
        period_end = max(period_end, max_date) if period_end else max_date
    if not period_start or not period_end:
        dates = [row["occurred_on"] for row in valid if row["occurred_on"]]
        period_start = period_start or (date.fromisoformat(min(dates)) if dates else None)
        period_end = period_end or (date.fromisoformat(max(dates)) if dates else None)

    summary: Dict[str, Any] = {
        "platform": ALIPAY_PLATFORM,
        "periodStart": period_start.isoformat() if period_start else "",
        "periodEnd": period_end.isoformat() if period_end else "",
        "merchantAccount": meta.get("merchant_account") or "",
        "totalCount": len(valid),
        "transactionCount": len(success_rows),
        "refundCount": len(refund_rows),
        "receivedAmount": received_net,
        "refundReceivedAmount": refund_received,
        "subsidyAmount": subsidy_total,
        "feeAmount": fee_total,
        "revenueAmount": revenue_total,
        "netSettledAmount": net_total,
        # 结账向导现有汇总卡直接读这几个键（与微信账单同形）
        "总交易单数": len(valid),
        "订单总金额": gross_sales,
        "退款总金额": abs(refund_received),
        "手续费总金额": fee_total,
        "平台补贴合计": subsidy_total,
        "商家实收合计": received_net,
        "净结算合计": net_total,
    }

    if "transaction_count" in footer and footer["transaction_count"] != len(success_rows):
        warnings.append({
            "reason": f"文件尾部交易合计 {footer['transaction_count']} 笔，明细解析 {len(success_rows)} 笔",
        })
    if "transaction_received" in footer and footer["transaction_received"] != gross_sales:
        warnings.append({
            "reason": f"文件尾部商家实收合计 {footer['transaction_received']}，明细汇总 {gross_sales}",
        })
    if "refund_count" in footer and footer["refund_count"] != len(refund_rows):
        warnings.append({
            "reason": f"文件尾部退款合计 {footer['refund_count']} 笔，明细解析 {len(refund_rows)} 笔",
        })
    if "refund_received" in footer and footer["refund_received"] != refund_received:
        warnings.append({
            "reason": f"文件尾部退款实收合计 {footer['refund_received']}，明细汇总 {refund_received}",
        })

    return {
        "platform": ALIPAY_PLATFORM,
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "total": len(valid),
    }


def _add_line(
    debit: List[Dict[str, Any]],
    credit: List[Dict[str, Any]],
    code: str,
    name: str,
    amount: Any,
    natural_side: str,
) -> None:
    value = _money(amount)
    if value == ZERO:
        return
    if natural_side == "debit":
        target = debit if value > ZERO else credit
    else:
        target = credit if value > ZERO else debit
    target.append({"accountCode": code, "accountName": name, "amount": abs(value)})


def _voucher(debit: List[Dict[str, Any]], credit: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"debit": debit, "credit": credit}


def _lines_total(lines: List[Dict[str, Any]]) -> Decimal:
    return sum((_money(line.get("amount")) for line in lines), ZERO)


async def import_alipay_bill_summary(
    company_id: str,
    user_id: str,
    summary: Dict[str, Any],
    *,
    occur_date: str = "",
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
) -> Dict[str, Any]:
    """按账单期间生成一张汇总凭证。

    借：1012002 支付宝待结算  （商家实收 - 服务费）
    借：5603002 财务费用-手续费（服务费）
    贷：5001 主营业务收入      （商家实收 - 平台补贴，价税分离）
    贷：2221026 应交增值税
    贷：5301 营业外收入        （平台补贴）
    """
    vat_config = await get_vat_config_by_id(company_id)
    accrue_vat = is_vat_sales_entry(REVENUE_NAME, REVENUE_CODE)

    period_start = str(summary.get("periodStart") or "")[:10]
    period_end = str(summary.get("periodEnd") or occur_date or "")[:10]
    if not period_end:
        period_end = datetime.now().strftime("%Y-%m-%d")
    month_str = period_end[:7]
    transaction_key = (
        f"monthly_summary:{period_start}:{period_end}"
        if period_start else f"monthly_summary:{month_str}"
    )

    existing = await _load_existing_alipay_keys(company_id)
    if transaction_key in existing:
        return {
            "imported": 0,
            "failed": 0,
            "ignored_duplicates": 1,
            "results": [{
                "status": "duplicate",
                "title": f"支付宝账单汇总-{period_start or month_str}~{period_end}",
                "error": "该账单期间的汇总凭证已存在",
            }],
        }

    if period_start:
        for existing_start, existing_end in await _load_existing_alipay_summary_periods(company_id):
            if period_start <= existing_end and existing_start <= period_end:
                return {
                    "imported": 0,
                    "failed": 1,
                    "ignored_duplicates": 0,
                    "results": [{
                        "status": "failed",
                        "title": f"支付宝账单汇总-{period_start}~{period_end}",
                        "error": (
                            f"与已导入账单期间 {existing_start}~{existing_end} 重叠，"
                            "为避免重复记账已中止；如为更正，请先红冲原汇总凭证"
                        ),
                    }],
                }

    if period_start and await _has_alipay_detail_in_period(company_id, period_start, period_end):
        # 汇总凭证与逐笔凭证是同一批账单的两种记法，同时存在会重复计收入。
        return {
            "imported": 0,
            "failed": 1,
            "ignored_duplicates": 0,
            "results": [{
                "status": "failed",
                "title": f"支付宝账单汇总-{period_start}~{period_end}",
                "error": (
                    f"账单期间 {period_start}~{period_end} 已有逐笔明细入账，"
                    "不能再按汇总导入；如为更正，请先冲销原明细凭证"
                ),
            }],
        }

    received = _money(summary.get("receivedAmount") or 0)
    subsidy = _money(summary.get("subsidyAmount") or 0)
    fee = _money(summary.get("feeAmount") or 0)
    revenue = _money(received - subsidy)
    net_clearing = _money(received - fee)
    total_count = int(_money(summary.get("totalCount") or 0))

    if received == ZERO and subsidy == ZERO and fee == ZERO:
        return {
            "imported": 0,
            "failed": 0,
            "ignored_duplicates": 0,
            "results": [{
                "status": "duplicate",
                "title": f"支付宝账单汇总-{period_start or month_str}~{period_end}",
                "error": "本期账单无发生额，无需入账",
            }],
        }

    revenue_net, revenue_tax = revenue, ZERO
    if accrue_vat and revenue != ZERO:
        revenue_net, revenue_tax = _split_revenue_vat(revenue, vat_config.rate)

    debit: List[Dict[str, Any]] = []
    credit: List[Dict[str, Any]] = []
    _add_line(debit, credit, ALIPAY_CLEARING_CODE, ALIPAY_CLEARING_NAME, net_clearing, "debit")
    _add_line(debit, credit, FEE_CODE, FEE_NAME, fee, "debit")
    _add_line(debit, credit, REVENUE_CODE, REVENUE_NAME, revenue_net, "credit")
    if accrue_vat:
        _add_line(debit, credit, VAT_PAYABLE_ACCOUNT, VAT_PAYABLE_ACCOUNT_NAME, revenue_tax, "credit")
    _add_line(debit, credit, SUBSIDY_CODE, SUBSIDY_NAME, subsidy, "credit")

    if _lines_total(debit) != _lines_total(credit):
        return {
            "imported": 0,
            "failed": 1,
            "ignored_duplicates": 0,
            "results": [{
                "status": "failed",
                "title": f"支付宝账单汇总-{period_start or month_str}~{period_end}",
                "error": "汇总凭证借贷不平，已中止入账",
            }],
        }

    amount = received if received != ZERO else fee
    title = f"支付宝账单汇总-{period_start}~{period_end}" if period_start else f"支付宝账单汇总-{month_str}"
    summary_entry: Dict[str, Any] = {
        "title": title,
        "amount": amount,
        "direction": "income" if amount >= ZERO else "expense",
        "category": REVENUE_NAME,
        "occurDate": period_end,
        "merchant": ALIPAY_MERCHANT,
        "source": ALIPAY_SOURCE,
        "platform": ALIPAY_PLATFORM,
        "dedupFingerprint": transaction_key,
        "is_monthly_summary": True,
        "billPeriodStart": period_start,
        "billPeriodEnd": period_end,
        "receivedAmount": received,
        "platformSubsidy": subsidy,
        "feeAmount": fee,
        "netSettledAmount": net_clearing,
        "remark": (
            f"支付宝账单汇总（{period_start or month_str}~{period_end}，共 {total_count} 笔，"
            f"商家实收 {received}，平台补贴 {subsidy}，服务费 {fee}）"
        ),
        "voucherLines": _voucher(debit, credit),
    }
    if accrue_vat:
        summary_entry.update({
            "vatRate": vat_config.rate,
            "vatAmount": revenue_tax,
            "vatSeparated": True,
            "amountExcludingTax": revenue_net,
        })

    try:
        await save_entries_bulk(
            [summary_entry],
            user_id=user_id,
            company_id=company_id,
            trusted_voucher=True,
            allow_backfill=allow_backfill,
            backfill_reason=backfill_reason,
        )
        return {
            "imported": 1,
            "failed": 0,
            "ignored_duplicates": 0,
            "results": [{"status": "success", "title": title, "amount": amount}],
        }
    except Exception as exc:
        return {
            "imported": 0,
            "failed": 1,
            "ignored_duplicates": 0,
            "results": [{"status": "failed", "title": title, "error": str(exc)}],
        }


def _business_entry(
    row: Dict[str, Any],
    *,
    vat_config: Any,
    accrue_vat: bool,
    reconciliation_fields: Dict[str, Any],
) -> Dict[str, Any] | None:
    received = _money(row.get("received_amount"))
    subsidy = _money(row.get("platform_subsidy"))
    revenue = _money(row.get("revenue_amount"))
    fee = _money(row.get("fee"))
    status = str(row.get("status") or "")
    product = str(row.get("product_name") or "支付宝交易")
    occurred_on = str(row.get("occurred_on") or row.get("date") or "")[:10]
    key = str(row.get("dedupFingerprint") or "").strip()
    if not key or received == ZERO:
        return None

    revenue_net, revenue_tax = revenue, ZERO
    if accrue_vat and revenue != ZERO:
        revenue_net, revenue_tax = _split_revenue_vat(revenue, vat_config.rate)

    debit: List[Dict[str, Any]] = []
    credit: List[Dict[str, Any]] = []
    _add_line(debit, credit, ALIPAY_CLEARING_CODE, ALIPAY_CLEARING_NAME, received, "debit")
    _add_line(debit, credit, REVENUE_CODE, REVENUE_NAME, revenue_net, "credit")
    if accrue_vat:
        _add_line(debit, credit, VAT_PAYABLE_ACCOUNT, VAT_PAYABLE_ACCOUNT_NAME, revenue_tax, "credit")
    _add_line(debit, credit, SUBSIDY_CODE, SUBSIDY_NAME, subsidy, "credit")

    entry: Dict[str, Any] = {
        "title": product if status == "SUCCESS" else f"退款-{product}",
        "amount": received,
        "direction": "income" if received >= ZERO else "expense",
        "category": REVENUE_NAME,
        "occurDate": occurred_on,
        "merchant": ALIPAY_MERCHANT,
        "source": ALIPAY_SOURCE,
        "platform": ALIPAY_PLATFORM,
        "dedupFingerprint": key,
        "alipay_transaction_key": key,
        "platform_order_no": str(row.get("platform_order_no") or row.get("order_no") or ""),
        "merchant_order_no": str(row.get("merchant_order_no") or ""),
        "refund_ref": str(row.get("refund_ref") or ""),
        "platform_subsidy": subsidy,
        "fee_amount": fee,
        "net_settled_amount": _money(received - fee),
        "remark": "支付宝退款冲销" if status == "REFUND" else f"支付宝收款 {row.get('remark') or ''}".strip(),
        **reconciliation_fields,
        "voucherLines": _voucher(debit, credit),
    }
    if accrue_vat:
        entry.update({
            "vatRate": vat_config.rate,
            "vatAmount": revenue_tax,
            "vatSeparated": True,
            "amountExcludingTax": revenue_net,
        })
    return entry


def _fee_entry(
    row: Dict[str, Any],
    *,
    reconciliation_fields: Dict[str, Any],
) -> Dict[str, Any] | None:
    fee = _money(row.get("fee"))
    if fee == ZERO:
        return None
    key = str(row.get("dedupFingerprint") or "").strip()
    if not key:
        return None
    product = str(row.get("product_name") or "支付宝交易")
    occurred_on = str(row.get("occurred_on") or row.get("date") or "")[:10]
    debit: List[Dict[str, Any]] = []
    credit: List[Dict[str, Any]] = []
    _add_line(debit, credit, FEE_CODE, FEE_NAME, fee, "debit")
    _add_line(debit, credit, ALIPAY_CLEARING_CODE, ALIPAY_CLEARING_NAME, fee, "credit")
    return {
        "title": f"支付宝服务费-{product}" if fee > ZERO else f"支付宝服务费退还-{product}",
        "amount": fee,
        "direction": "expense",
        "category": "银行及金融手续费",
        "occurDate": occurred_on,
        "merchant": ALIPAY_MERCHANT,
        "source": ALIPAY_SOURCE,
        "platform": ALIPAY_PLATFORM,
        "dedupFingerprint": f"{key}:fee",
        "alipay_transaction_key": f"{key}:fee",
        "platform_order_no": str(row.get("platform_order_no") or row.get("order_no") or ""),
        "merchant_order_no": str(row.get("merchant_order_no") or ""),
        "refund_ref": str(row.get("refund_ref") or ""),
        "platform_subsidy": ZERO,
        "fee_amount": fee,
        "net_settled_amount": _money(-fee),
        "remark": "支付宝服务费" if fee > ZERO else "支付宝服务费退还",
        **reconciliation_fields,
        "voucherLines": _voucher(debit, credit),
    }


async def import_alipay_bill(
    company_id: str,
    user_id: str,
    rows: List[Dict[str, Any]],
    *,
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
) -> Dict[str, Any]:
    """逐笔导入支付宝账单；每笔交易拆成收入（含补贴）与服务费两条凭证。"""
    vat_config = await get_vat_config_by_id(company_id)
    accrue_vat = is_vat_sales_entry(REVENUE_NAME, REVENUE_CODE)

    existing = await _load_existing_alipay_keys(company_id)
    detail_dates = sorted(
        value for value in (
            str(row.get("occurred_on") or row.get("date") or "")[:10] for row in rows
        ) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
    )
    if detail_dates:
        overlap = _overlapping_period(
            detail_dates[0], detail_dates[-1],
            await _load_existing_alipay_summary_periods(company_id),
        )
        if overlap:
            return {
                "imported": 0,
                "failed": len(rows),
                "ignored_duplicates": 0,
                "results": [{
                    "status": "failed",
                    "title": "支付宝账单明细",
                    "error": (
                        f"账单期间 {overlap[0]}~{overlap[1]} 已按汇总入账，"
                        "不能再逐笔导入；如为更正，请先冲销原汇总凭证"
                    ),
                }],
            }
    requested_order_nos = {
        str(row.get("platform_order_no") or row.get("order_no") or "").strip()
        for row in rows
        if str(row.get("platform_order_no") or row.get("order_no") or "").strip()
    }
    history = await _load_existing_alipay_order_history(company_id, requested_order_nos)
    order_totals = _historical_order_totals(history)

    # 先算本批次可导入行，再与历史合并判断「整单已退」
    seen_keys: set[str] = set()
    for row in rows:
        key = str(row.get("dedupFingerprint") or "").strip()
        order_no = str(row.get("platform_order_no") or row.get("order_no") or "").strip()
        if not key or not order_no or key in existing or key in seen_keys:
            continue
        seen_keys.add(key)
        totals = order_totals.setdefault(order_no, {"success": ZERO, "refund": ZERO})
        received = _money(row.get("received_amount") if "received_amount" in row else row.get("order_amount"))
        if str(row.get("status") or "") == "SUCCESS":
            totals["success"] += received
        elif str(row.get("status") or "") == "REFUND":
            totals["refund"] += abs(received)

    bank_settled_orders = {
        order_no
        for order_no, items in history.items()
        if any(bool(item.get("bank_matched")) for item in items)
    }
    internally_refunded_orders = {
        order_no for order_no, totals in order_totals.items()
        if totals["success"] > ZERO
        and totals["refund"] >= totals["success"] - Decimal("0.01")
        and order_no not in bank_settled_orders
    }
    existing_entry_updates = [
        {
            "id": item.get("id"),
            "billingStatus": "reconciled",
            "reconciliationType": "internal_refund",
        }
        for order_no in internally_refunded_orders
        for item in history.get(order_no, [])
        if item.get("id")
    ]

    imported = 0
    ignored_duplicates = 0
    failed = 0
    results: List[Dict[str, Any]] = []
    seen_transaction_keys: set[str] = set()
    pending_entries: List[Dict[str, Any]] = []
    pending_results: List[Dict[str, Any]] = []

    for row in rows:
        key = str(row.get("dedupFingerprint") or "").strip()
        order_no = str(row.get("platform_order_no") or row.get("order_no") or "").strip()
        if not key:
            failed += 1
            results.append({"status": "failed", "title": row.get("product_name", ""), "error": "缺少去重键"})
            continue
        if key in existing or key in seen_transaction_keys:
            ignored_duplicates += 1
            continue
        seen_transaction_keys.add(key)
        reconciliation_fields = (
            {"billingStatus": "reconciled", "reconciliationType": "internal_refund"}
            if order_no in internally_refunded_orders else {}
        )
        try:
            entry = _business_entry(
                row,
                vat_config=vat_config,
                accrue_vat=accrue_vat,
                reconciliation_fields=reconciliation_fields,
            )
            fee_entry = _fee_entry(row, reconciliation_fields=reconciliation_fields)
            if entry is None and fee_entry is None:
                ignored_duplicates += 1
                continue
            if entry is not None:
                pending_entries.append(entry)
            if fee_entry is not None:
                pending_entries.append(fee_entry)
            pending_results.append({
                "status": "success",
                "title": row.get("product_name", ""),
                "amount": row.get("received_amount", row.get("order_amount", 0)),
            })
        except Exception as exc:
            failed += 1
            results.append({"status": "failed", "title": row.get("product_name", ""), "error": str(exc)})

    if pending_entries or existing_entry_updates:
        try:
            await save_entries_bulk(
                pending_entries,
                user_id=user_id,
                company_id=company_id,
                existing_entry_updates=existing_entry_updates or None,
                trusted_voucher=True,
                allow_backfill=allow_backfill,
                backfill_reason=backfill_reason,
            )
            imported += len(pending_results)
            results.extend(pending_results)
        except Exception as exc:
            failed += len(pending_results)
            results.extend([
                {"status": "failed", "title": item.get("title", ""), "error": str(exc)}
                for item in pending_results
            ])

    return {
        "imported": imported,
        "failed": failed,
        "ignored_duplicates": ignored_duplicates,
        "results": results,
    }


async def list_alipay_bill_records(company_id: str, month: str) -> Dict[str, Any]:
    """当月已导入支付宝账单的只读记录，按支付宝交易号聚合。"""
    month = str(month or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month 格式必须为 YYYY-MM")
    empty_summary = {
        "total": 0,
        "totalOrderAmount": 0.0,
        "totalFee": 0.0,
        "totalSettled": 0.0,
        "totalSubsidy": 0.0,
    }
    if not company_id:
        return {"month": month, "platform": ALIPAY_PLATFORM, "items": [], "summary": empty_summary}

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries "
            "WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = ? "
            "AND json_extract(payload, '$.occurDate') LIKE ?",
            (company_id, ALIPAY_SOURCE, f"{month}-%"),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await conn.close()

    orders: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        occur_date = str(payload.get("occurDate") or "")[:10]
        if payload.get("is_monthly_summary") or payload.get("isMonthlySummary"):
            period_start = str(payload.get("billPeriodStart") or "")
            period_end = str(payload.get("billPeriodEnd") or occur_date)
            order_no = f"SUMMARY-{period_start}~{period_end}" if period_start else f"SUMMARY-{occur_date}"
            received = _money(payload.get("receivedAmount"))
            fee = _money(payload.get("feeAmount"))
            orders[order_no] = {
                "orderNo": order_no,
                "platform": ALIPAY_PLATFORM,
                "date": occur_date,
                "title": "支付宝账单汇总",
                "status": "SUMMARY",
                "orderAmount": received,
                "fee": fee,
                "subsidy": _money(payload.get("platformSubsidy")),
                "settledAmount": _money(payload.get("netSettledAmount") or (received - fee)),
            }
            continue

        order_no = str(payload.get("platform_order_no") or payload.get("order_no") or "").strip()
        if not order_no:
            continue
        amount = _money(payload.get("amount"))
        category = str(payload.get("category") or "")
        title = str(payload.get("title") or "")
        bucket = orders.get(order_no)
        if bucket is None:
            bucket = {
                "orderNo": order_no,
                "platform": ALIPAY_PLATFORM,
                "date": occur_date,
                "title": "",
                "status": "SUCCESS",
                "orderAmount": ZERO,
                "fee": ZERO,
                "subsidy": ZERO,
            }
            orders[order_no] = bucket
        if occur_date and (not bucket["date"] or occur_date < bucket["date"]):
            bucket["date"] = occur_date
        if category == FEE_NAME or "服务费" in title or "手续费" in category:
            bucket["fee"] += amount
            if not bucket["title"]:
                bucket["title"] = title.removeprefix("支付宝服务费退还-").removeprefix("支付宝服务费-")
        else:
            bucket["orderAmount"] += amount
            bucket["subsidy"] += _money(payload.get("platform_subsidy"))
            if not bucket["title"]:
                bucket["title"] = title.removeprefix("退款-")
            if amount < ZERO:
                bucket["status"] = "REFUND"

    summary = {
        "total": len(orders),
        "totalOrderAmount": float(sum((item["orderAmount"] for item in orders.values()), ZERO)),
        "totalFee": float(sum((item["fee"] for item in orders.values()), ZERO)),
        "totalSettled": float(sum((item["orderAmount"] - item["fee"] for item in orders.values()), ZERO)),
        "totalSubsidy": float(sum((item["subsidy"] for item in orders.values()), ZERO)),
    }
    items = sorted(orders.values(), key=lambda item: (item["date"], item["orderNo"]))
    for item in items:
        item["settledAmount"] = _money(item["orderAmount"] - item["fee"])
        item["orderAmount"] = float(item["orderAmount"])
        item["fee"] = float(item["fee"])
        item["subsidy"] = float(item["subsidy"])
        item["settledAmount"] = float(item["settledAmount"])
    return {"month": month, "platform": ALIPAY_PLATFORM, "items": items, "summary": summary}
