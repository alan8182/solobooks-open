"""微信支付账单解析与导入服务"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Set

from services.database import get_conn
from services.entry_service import save_entries_bulk
from core.money import ZERO, money

WECHAT_CLEARING_CODE = "1012"
WECHAT_CLEARING_NAME = "微信支付待结算"
REVENUE_CODE = "6001"
REVENUE_NAME = "主营业务收入"
FEE_CODE = "6602"
FEE_NAME = "财务费用-手续费"


def _strip_backtick(s: str) -> str:
    return s.lstrip("`").strip()


def _money(value: Any) -> Decimal:
    try:
        return money(value or ZERO)
    except (TypeError, ValueError):
        return ZERO


def _add_days(date_str: str, days: int) -> str:
    try:
        return (datetime.strptime(date_str[:10], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return date_str


def _voucher(debit: List[Dict[str, Any]], credit: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"debit": debit, "credit": credit}


def _read_csv_row(line: str) -> List[str]:
    try:
        return next(csv.reader([line]))
    except csv.Error:
        return line.split(",")


def _wechat_transaction_key(row: Dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip()
    order_no = str(row.get("order_no") or "").strip()
    merchant_order_no = str(row.get("merchant_order_no") or "").strip()
    base_order = order_no or merchant_order_no
    if not base_order:
        base_order = str(row.get("transaction_time") or row.get("date") or "").strip()

    if status == "REFUND":
        refund_no = str(row.get("refund_order_no") or "").strip()
        merchant_refund_no = str(row.get("merchant_refund_no") or "").strip()
        refund_identity = refund_no or merchant_refund_no
        if not refund_identity:
            refund_identity = f"{row.get('transaction_time') or row.get('date') or ''}:{_money(row.get('refund_amount', 0))}"
        return f"refund:{base_order}:{refund_identity}"
    if status == "SUCCESS":
        return f"success:{base_order}"
    return f"{status.lower()}:{base_order}"


def _fee_transaction_key(transaction_key: str) -> str:
    """手续费条目的来源 key：一笔交易会产生收入+手续费两条凭证，
    必须使用不同 external_key，否则同一批次内会计核心的来源唯一性校验会自碰撞。"""
    return f"{transaction_key}:fee"


def _legacy_wechat_transaction_key(order_no: Any, title: Any, amount: Any) -> str:
    order_text = str(order_no or "").strip()
    if not order_text:
        return ""
    title_text = str(title or "").strip().lower()
    amount_value = _money(amount)
    is_refund = amount_value < 0 or "refund" in title_text or "\u9000\u6b3e" in title_text or "\u9000\u8fd8" in title_text
    return f"{'refund' if is_refund else 'success'}:{order_text}"


async def _load_existing_wechat_transaction_keys(company_id: str) -> set[str]:
    if not company_id:
        return set()

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT "
            "json_extract(payload, '$.wechat_transaction_key') AS transaction_key, "
            "json_extract(payload, '$.wechat_order_no') AS order_no, "
            "json_extract(payload, '$.title') AS title, "
            "json_extract(payload, '$.amount') AS amount "
            "FROM entries "
            "WHERE company_id = ? "
            "AND (json_extract(payload, '$.wechat_transaction_key') IS NOT NULL "
            "OR json_extract(payload, '$.wechat_order_no') IS NOT NULL)",
            (company_id,),
        )
        rows = await cursor.fetchall()
    except Exception:
        return set()
    finally:
        await conn.close()

    keys: set[str] = set()
    for row in rows:
        transaction_key = str(row["transaction_key"] or "").strip()
        if transaction_key:
            keys.add(transaction_key)
            continue
        legacy_key = _legacy_wechat_transaction_key(row["order_no"], row["title"], row["amount"])
        if legacy_key:
            keys.add(legacy_key)
    return keys


async def _load_existing_wechat_order_history(
    company_id: str,
    order_nos: Set[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Load persisted WeChat entries and whether each one already reached a bank statement."""
    clean_order_nos = {str(order_no).strip() for order_no in order_nos if str(order_no).strip()}
    if not company_id or not clean_order_nos:
        return {}

    rows = []
    conn = await get_conn()
    try:
        # Keep below SQLite's parameter limit for unusually large monthly bills.
        ordered = sorted(clean_order_nos)
        for start in range(0, len(ordered), 400):
            batch = ordered[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            cursor = await conn.execute(
                f"SELECT id, payload FROM entries WHERE company_id = ? "
                f"AND json_extract(payload, '$.wechat_order_no') IN ({placeholders})",
                (company_id, *batch),
            )
            rows.extend(await cursor.fetchall())
    finally:
        await conn.close()

    # Import lazily to keep the bill parser independent of bank schema setup.
    from services.bank_service import get_matched_entry_ids

    matched_entry_ids = await get_matched_entry_ids(company_id)
    history: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        order_no = str(payload.get("wechat_order_no") or "").strip()
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
    """Aggregate business amounts once per WeChat transaction, ignoring companion fee entries."""
    totals_by_order: Dict[str, Dict[str, Decimal]] = {}
    for order_no, items in history.items():
        seen_keys: set[str] = set()
        totals = totals_by_order.setdefault(order_no, {"success": ZERO, "refund": ZERO})
        for item in items:
            payload = item.get("payload") or {}
            if str(payload.get("direction") or "").strip() != "income":
                continue
            transaction_key = str(payload.get("wechat_transaction_key") or "").strip()
            fallback_key = f"legacy:{payload.get('id') or item.get('id') or ''}"
            dedupe_key = transaction_key or fallback_key
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            amount = _money(payload.get("amount"))
            title = str(payload.get("title") or "").lower()
            is_refund = transaction_key.startswith("refund:") or amount < 0 or "退款" in title or "refund" in title
            if is_refund:
                totals["refund"] += abs(amount)
            elif amount > ZERO:
                totals["success"] += amount
    return totals_by_order


async def parse_wechat_bill(content: bytes, company_id: str = "") -> Dict[str, Any]:
    """解析微信支付账单 CSV，返回 {valid, errors, summary}。

    微信账单特征：
    - 每行数据前有 ` 前缀
    - 末尾有汇总行（以"总交易单数"开头）
    - 编码 UTF-8 BOM
    """
    text = content.decode("utf-8-sig", errors="replace")
    raw_lines = text.splitlines()

    # 分离数据行和汇总行
    data_lines: List[str] = []
    summary_lines: List[str] = []
    in_summary = False
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith("总交易单数") or in_summary:
            in_summary = True
            summary_lines.append(stripped)
        else:
            # 去掉每列的反引号前缀
            cleaned_row = [_strip_backtick(cell) for cell in _read_csv_row(line)]
            cleaned_io = io.StringIO()
            csv.writer(cleaned_io, lineterminator="").writerow(cleaned_row)
            data_lines.append(cleaned_io.getvalue())

    # 解析汇总行
    summary: Dict[str, Any] = {}
    if len(summary_lines) >= 2:
        keys = [k.strip() for k in _read_csv_row(summary_lines[0])]
        vals = [_strip_backtick(v) for v in _read_csv_row(summary_lines[1])]
        for k, v in zip(keys, vals):
            try:
                summary[k] = _money(v)
            except (ValueError, TypeError):
                summary[k] = v

    # Load existing WeChat transaction keys for dedupe
    existing_wechat = await _load_existing_wechat_transaction_keys(company_id)

    # 解析数据行（CSV）
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    valid: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for i, row in enumerate(reader):
        status = row.get("交易状态", "").strip()
        if status not in ("SUCCESS", "REFUND"):
            continue

        try:
            tx_time = row.get("交易时间", "").strip()
            settlement_time = (
                row.get("入账时间", "")
                or row.get("入账日期", "")
                or row.get("结算时间", "")
                or row.get("结算日期", "")
            ).strip()
            product = row.get("商品名称", "").strip()
            order_amount = _money(row.get("订单金额", "0"))
            settled_amount = _money(row.get("应结订单金额", "0"))
            fee_raw = row.get("手续费", "0") or "0"
            fee = abs(_money(fee_raw))
            fee_rate = row.get("费率", "").strip()
            refund_amount = _money(row.get("退款金额", "0"))
            order_no = row.get("微信订单号", "").strip()
            merchant_order_no = row.get("商户订单号", "").strip()
            refund_order_no = row.get("微信退款单号", "").strip()
            merchant_refund_no = row.get("商户退款单号", "").strip()

            # 日期标准化为 YYYY-MM-DD
            date_str = tx_time[:10] if tx_time else ""
            settlement_date = settlement_time[:10] if settlement_time else _add_days(date_str, 1)
            item = {
                "status": status,
                "transaction_time": tx_time,
                "settlement_date": settlement_date,
                "date": date_str,
                "product_name": product,
                "order_amount": order_amount,
                "settled_amount": settled_amount,
                "fee": fee,
                "fee_rate": fee_rate,
                "refund_amount": refund_amount,
                "order_no": order_no,
                "merchant_order_no": merchant_order_no,
                "refund_order_no": refund_order_no,
                "merchant_refund_no": merchant_refund_no,
            }
            transaction_key = _wechat_transaction_key(item)
            item["wechat_transaction_key"] = transaction_key
            item["is_duplicate"] = transaction_key in existing_wechat or (
                status == "REFUND" and f"refund:{order_no}" in existing_wechat
            )
            valid.append(item)
        except (ValueError, KeyError) as e:
            errors.append({"row": i, "reason": str(e)})

    return {"valid": valid, "errors": errors, "summary": summary, "total": len(valid)}


async def import_wechat_bill(
    company_id: str,
    user_id: str,
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """将解析后的微信账单行批量保存为记账条目。

    每笔 SUCCESS 生成两条 entry：
      1. 主营业务收入（订单金额）
      2. 银行及金融手续费（手续费，负向修正）

    每笔 REFUND 生成一条负收入 entry（退款金额）。
    手续费退还也记入手续费科目（负手续费）。
    """
    # Load latest existing WeChat transaction keys for dedupe
    existing_wechat = await _load_existing_wechat_transaction_keys(company_id)
    requested_order_nos = {
        str(row.get("order_no") or "").strip()
        for row in rows
        if str(row.get("order_no") or "").strip()
    }
    order_history = await _load_existing_wechat_order_history(company_id, requested_order_nos)

    imported = 0
    ignored_duplicates = 0
    failed = 0
    results: List[Dict[str, Any]] = []
    seen_transaction_keys = set()
    pending_entries: List[Dict[str, Any]] = []
    pending_results: List[Dict[str, Any]] = []
    order_totals = _historical_order_totals(order_history)
    aggregate_seen_keys = set()
    for row in rows:
        order_no = str(row.get("order_no") or "").strip()
        transaction_key = row.get("wechat_transaction_key") or _wechat_transaction_key(row)
        legacy_refund_key = f"refund:{order_no}" if row.get("status") == "REFUND" and order_no else ""
        if (
            not order_no
            or transaction_key in aggregate_seen_keys
            or transaction_key in existing_wechat
            or (legacy_refund_key and legacy_refund_key in existing_wechat)
        ):
            continue
        aggregate_seen_keys.add(transaction_key)
        totals = order_totals.setdefault(order_no, {"success": ZERO, "refund": ZERO})
        if row.get("status") == "SUCCESS":
            totals["success"] += _money(row.get("order_amount", 0))
        elif row.get("status") == "REFUND":
            totals["refund"] += _money(row.get("refund_amount", 0))
    bank_settled_orders = {
        order_no
        for order_no, items in order_history.items()
        if any(bool(item.get("bank_matched")) for item in items)
    }
    internally_refunded_orders = {
        order_no for order_no, totals in order_totals.items()
        if totals["success"] > ZERO and totals["refund"] >= totals["success"] - Decimal("0.01")
        and order_no not in bank_settled_orders
    }
    existing_entry_updates = [
        {
            "id": item.get("id"),
            "billingStatus": "reconciled",
            "reconciliationType": "internal_refund",
        }
        for order_no in internally_refunded_orders
        for item in order_history.get(order_no, [])
        if item.get("id")
    ]

    for row in rows:
        status = row.get("status", "")
        date_str = row.get("date", "")
        product = row.get("product_name", "未知商品")
        order_amount = _money(row.get("order_amount", 0))
        fee = _money(row.get("fee", 0))
        refund_amount = _money(row.get("refund_amount", 0))
        order_no = row.get("order_no", "")
        transaction_key = row.get("wechat_transaction_key") or _wechat_transaction_key(row)
        reconciliation_fields = (
            {"billingStatus": "reconciled", "reconciliationType": "internal_refund"}
            if order_no in internally_refunded_orders else {}
        )

        # Back-end dedupe: SUCCESS and REFUND under the same order are separate transactions.
        legacy_refund_key = f"refund:{order_no}" if status == "REFUND" and order_no else ""
        if transaction_key and (
            transaction_key in existing_wechat
            or transaction_key in seen_transaction_keys
            or (legacy_refund_key and legacy_refund_key in existing_wechat)
        ):
            ignored_duplicates += 1
            continue
        if transaction_key:
            seen_transaction_keys.add(transaction_key)

        try:
            if status == "SUCCESS":
                # 1. 主收入条目（订单金额）
                pending_entries.append({
                    "title": product,
                    "amount": order_amount,
                    "direction": "income",
                    "category": "主营业务收入",
                    "occurDate": date_str,
                    "merchant": "微信支付",
                    "source": "wechat_bill",
                    "voucherAuthority": "system",
                    "wechat_order_no": order_no,
                    "wechat_transaction_key": transaction_key,
                    "refund_order_no": row.get("refund_order_no", ""),
                    "merchant_refund_no": row.get("merchant_refund_no", ""),
                    "remark": f"微信收款 {row.get('fee_rate','')}",
                    **reconciliation_fields,
                    "voucherLines": _voucher(
                        [{"accountCode": WECHAT_CLEARING_CODE, "accountName": WECHAT_CLEARING_NAME, "amount": order_amount}],
                        [{"accountCode": REVENUE_CODE, "accountName": REVENUE_NAME, "amount": order_amount}],
                    ),
                })

                # 2. 手续费条目（支出）
                if fee > ZERO:
                    pending_entries.append({
                        "title": f"微信手续费-{product}",
                        "amount": fee,
                        "direction": "expense",
                        "category": "银行及金融手续费",
                        "occurDate": date_str,
                        "merchant": "微信支付",
                        "source": "wechat_bill",
                        "voucherAuthority": "system",
                        "wechat_order_no": order_no,
                        "wechat_transaction_key": _fee_transaction_key(transaction_key),
                        "refund_order_no": row.get("refund_order_no", ""),
                        "merchant_refund_no": row.get("merchant_refund_no", ""),
                        "remark": f"手续费率 {row.get('fee_rate','')}",
                        **reconciliation_fields,
                        "voucherLines": _voucher(
                            [{"accountCode": FEE_CODE, "accountName": FEE_NAME, "amount": fee}],
                            [{"accountCode": WECHAT_CLEARING_CODE, "accountName": WECHAT_CLEARING_NAME, "amount": fee}],
                        ),
                    })

                pending_results.append({"status": "success", "title": product, "amount": order_amount})

            elif status == "REFUND":
                # 退款：冲销收入（负金额用 income + 负数表示）
                pending_entries.append({
                    "title": f"退款-{product}",
                    "amount": -refund_amount,
                    "direction": "income",
                    "category": "主营业务收入",
                    "occurDate": date_str,
                    "merchant": "微信支付",
                    "source": "wechat_bill",
                    "voucherAuthority": "system",
                    "wechat_order_no": order_no,
                    "wechat_transaction_key": transaction_key,
                    "refund_order_no": row.get("refund_order_no", ""),
                    "merchant_refund_no": row.get("merchant_refund_no", ""),
                    "remark": "微信退款冲销",
                    **reconciliation_fields,
                    "voucherLines": _voucher(
                        [{"accountCode": REVENUE_CODE, "accountName": REVENUE_NAME, "amount": refund_amount}],
                        [{"accountCode": WECHAT_CLEARING_CODE, "accountName": WECHAT_CLEARING_NAME, "amount": refund_amount}],
                    ),
                })

                # 手续费退还：冲销原手续费，仍在微信待结算科目内闭合，不影响银行。
                if fee > ZERO:
                    pending_entries.append({
                        "title": f"微信手续费退还-{product}",
                        "amount": -fee,
                        "direction": "expense",
                        "category": "银行及金融手续费",
                        "occurDate": date_str,
                        "merchant": "微信支付",
                        "source": "wechat_bill",
                        "voucherAuthority": "system",
                        "wechat_order_no": order_no,
                        "wechat_transaction_key": _fee_transaction_key(transaction_key),
                        "refund_order_no": row.get("refund_order_no", ""),
                        "merchant_refund_no": row.get("merchant_refund_no", ""),
                        "remark": "退款手续费退还",
                        **reconciliation_fields,
                        "voucherLines": _voucher(
                            [{"accountCode": WECHAT_CLEARING_CODE, "accountName": WECHAT_CLEARING_NAME, "amount": fee}],
                            [{"accountCode": FEE_CODE, "accountName": FEE_NAME, "amount": fee}],
                        ),
                    })

                pending_results.append({"status": "success", "title": f"退款-{product}", "amount": -refund_amount})

        except Exception as e:
            failed += 1
            results.append({"status": "failed", "title": product, "error": str(e)})

    if pending_entries:
        try:
            if existing_entry_updates:
                await save_entries_bulk(
                    pending_entries,
                    user_id=user_id,
                    company_id=company_id,
                    existing_entry_updates=existing_entry_updates,
                )
            else:
                await save_entries_bulk(pending_entries, user_id=user_id, company_id=company_id)
            imported += len(pending_results)
            results.extend(pending_results)
        except Exception as e:
            failed += len(pending_results)
            results.extend([
                {"status": "failed", "title": item.get("title", ""), "error": str(e)}
                for item in pending_results
            ])

    return {"imported": imported, "failed": failed, "ignored_duplicates": ignored_duplicates, "results": results}


async def list_wechat_bill_records(company_id: str, month: str) -> Dict[str, Any]:
    """当月已导入微信账单的只读记录，按订单号聚合收入、退款与手续费条目。

    导入时每笔交易拆成收入+手续费（或退款+手续费退还）多条 entry，
    这里聚合回 CSV 视图：订单金额、手续费、到账净额（1012 清算净额）。
    """
    month = str(month or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month 格式必须为 YYYY-MM")
    empty_summary = {"total": 0, "totalOrderAmount": 0.0, "totalFee": 0.0, "totalSettled": 0.0}
    if not company_id:
        return {"month": month, "items": [], "summary": empty_summary}

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries "
            "WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = 'wechat_bill' "
            "AND json_extract(payload, '$.occurDate') LIKE ?",
            (company_id, f"{month}-%"),
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
        order_no = str(payload.get("wechat_order_no") or "").strip()
        if not order_no:
            continue
        occur_date = str(payload.get("occurDate") or "")[:10]
        amount = _money(payload.get("amount"))
        category = str(payload.get("category") or "")
        title = str(payload.get("title") or "")
        bucket = orders.get(order_no)
        if bucket is None:
            bucket = {
                "orderNo": order_no,
                "date": occur_date,
                "title": "",
                "status": "SUCCESS",
                "orderAmount": ZERO,
                "fee": ZERO,
            }
            orders[order_no] = bucket
        if occur_date and (not bucket["date"] or occur_date < bucket["date"]):
            bucket["date"] = occur_date
        if category == FEE_NAME or "手续费" in category:
            bucket["fee"] += amount
            if not bucket["title"]:
                bucket["title"] = title.removeprefix("微信手续费退还-").removeprefix("微信手续费-")
        elif amount < ZERO or category == REVENUE_NAME:
            bucket["orderAmount"] += amount
            if not bucket["title"]:
                bucket["title"] = title.removeprefix("退款-")
            if amount < ZERO:
                bucket["status"] = "REFUND"

    summary = {
        "total": len(orders),
        "totalOrderAmount": float(sum((item["orderAmount"] for item in orders.values()), ZERO)),
        "totalFee": float(sum((item["fee"] for item in orders.values()), ZERO)),
        "totalSettled": float(sum((item["orderAmount"] - item["fee"] for item in orders.values()), ZERO)),
    }
    items = sorted(orders.values(), key=lambda item: (item["date"], item["orderNo"]))
    for item in items:
        settled = item["orderAmount"] - item["fee"]
        item["orderAmount"] = float(item["orderAmount"])
        item["fee"] = float(item["fee"])
        item["settledAmount"] = float(settled)
    return {"month": month, "items": items, "summary": summary}
