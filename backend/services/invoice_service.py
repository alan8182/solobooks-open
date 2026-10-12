"""发票导入服务"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
import json
import re

from services.database import get_conn
from services.entry_service import save_entry
from services.account_mapper import CATEGORY_TO_ACCOUNT, get_voucher_hint
from services.invoice_llm_prompts import INVOICE_CLASSIFY_PROMPT
from services.llm import safe_chat_text_json
from core.money import ZERO, money, rate
from services.account_service import _json_object


def safe_money(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    try:
        cleaned = str(val).replace(",", "").strip()
        return money(cleaned)
    except (ValueError, TypeError):
        return ZERO


def _normalize_name(name: str) -> str:
    """统一中英文括号并转为小写，以便进行高容错字串包含判断"""
    if not name:
        return ""
    return name.replace("（", "(").replace("）", ")").strip().lower()


async def get_real_company_name(company_id: str, rows: List[Dict[str, Any]]) -> str:
    """Retrieve company name from DB. If '默认账本' or empty, auto-detect it from rows and DB context."""
    conn = await get_conn()
    db_company_name = ""
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        if row:
            co = _json_object(row["payload"])
            db_company_name = co.get("name") or co.get("company_name") or ""
    except Exception:
        pass
    finally:
        await conn.close()

    # If the company name in DB is valid and not "默认账本", we use it!
    if db_company_name and db_company_name != "默认账本":
        return db_company_name

    # Otherwise, extract all unique buyer and seller names from rows
    from collections import Counter
    counts = Counter()
    candidates = set()
    for r in rows:
        b = str(r.get("buyer_name") or r.get("购买方名称") or r.get("购买方") or "").strip()
        s = str(r.get("seller_name") or r.get("销售方名称") or r.get("销售方") or "").strip()
        if b:
            candidates.add(b)
            counts[b] += 1
        if s:
            candidates.add(s)
            counts[s] += 1

    if not candidates:
        return db_company_name or "默认账本"

    # Query counterparties from bank_statements & entries to break ties / narrow candidates
    conn = await get_conn()
    counterparties = set()
    try:
        cursor = await conn.execute("SELECT description FROM bank_statements WHERE company_id = ?", (company_id,))
        bs_rows = await cursor.fetchall()
        for bs in bs_rows:
            desc = bs["description"]
            if "(对方:" in desc:
                try:
                    parts = desc.split("(对方:")
                    if len(parts) > 1:
                        name = parts[1].split(")")[0].strip()
                        if name:
                            counterparties.add(name)
                except Exception:
                    pass
        
        cursor = await conn.execute("SELECT payload FROM entries WHERE company_id = ?", (company_id,))
        entry_rows = await cursor.fetchall()
        for er in entry_rows:
            import json
            try:
                p = json.loads(er["payload"])
                m = p.get("merchant") or p.get("merchant_name") or ""
                if m:
                    counterparties.add(str(m).strip())
            except Exception:
                pass
    except Exception:
        pass
    finally:
        await conn.close()

    # Filter out candidates that are known counterparties
    valid_candidates = [c for c in candidates if c not in counterparties]
    
    if valid_candidates:
        # Sort by frequency desc
        valid_candidates.sort(key=lambda x: counts[x], reverse=True)
        detected_name = valid_candidates[0]
    else:
        # Fallback to absolute highest frequency in counts
        most_common = counts.most_common(1)
        detected_name = most_common[0][0] if most_common else ""

    # If we successfully detected a company name and the DB currently has "默认账本", update the DB!
    if detected_name and detected_name != "默认账本" and (not db_company_name or db_company_name == "默认账本"):
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT payload FROM auth_companies WHERE company_id = ?",
                (company_id,),
            )
            row = await cursor.fetchone()
            if row:
                co = _json_object(row["payload"])
                co["name"] = detected_name
                co["company_name"] = detected_name
                import datetime
                updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                await conn.execute(
                    "UPDATE auth_companies SET payload = ?, updated_at = ? WHERE company_id = ?",
                    (json.dumps(co), updated_at, company_id),
                )
                await conn.commit()
        except Exception:
            pass
        finally:
            await conn.close()
            
        return detected_name

    return db_company_name or detected_name or "默认账本"


async def parse_invoice_rows(rows: List[Dict[str, Any]], company_id: str = "") -> Dict[str, Any]:
    """Validate and prepare invoice rows for import."""
    valid = []
    errors = []

    # Get company name to predict direction
    company_name = ""
    if company_id:
        company_name = await get_real_company_name(company_id, rows)

    # Query existing invoices in DB
    existing_invoices = set()
    if company_id:
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT json_extract(payload, '$.invoiceNumber') FROM entries WHERE company_id = ? AND json_extract(payload, '$.invoiceNumber') IS NOT NULL",
                (company_id,)
            )
            db_rows = await cursor.fetchall()
            existing_invoices = {str(r[0]).strip() for r in db_rows if r[0]}
        except Exception:
            pass
        finally:
            await conn.close()

    for i, row in enumerate(rows):
        invoice_number = str(row.get("invoice_number") or row.get("发票号码") or "").strip()
        amount = safe_money(row.get("total_amount") or row.get("amount") or row.get("价税合计"))
        invoice_date = str(row.get("invoice_date") or row.get("date") or row.get("开票日期") or "").strip()
        seller_name = str(row.get("seller_name") or row.get("seller") or row.get("销售方") or "").strip()
        buyer_name = str(row.get("buyer_name") or row.get("buyer") or row.get("购买方") or "").strip()

        if not invoice_number and not amount:
            errors.append({"row": i, "reason": "缺少发票号码和金额"})
            continue

        explicit_direction = str(row.get("direction") or "").strip()
        direction = explicit_direction if explicit_direction in {"expense", "income", "owner_transfer"} else "expense"
        if company_name:
            co_norm = _normalize_name(company_name)
            seller_norm = _normalize_name(seller_name)
            buyer_norm = _normalize_name(buyer_name)
            if not explicit_direction:
                if co_norm and seller_norm and (co_norm in seller_norm or seller_norm in co_norm):
                    direction = "income"
                elif co_norm and buyer_norm and (co_norm in buyer_norm or buyer_norm in co_norm):
                    direction = "expense"

        is_duplicate = invoice_number in existing_invoices if invoice_number else False

        valid.append({
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "amount": amount,
            "seller_name": seller_name,
            "buyer_name": buyer_name,
            "direction": direction,
            "is_duplicate": is_duplicate,
            "title": f"发票-{seller_name or invoice_number}",
            **{k: v for k, v in row.items() if k not in ("invoice_number", "invoice_date", "amount", "seller_name", "buyer_name", "direction", "title")},
        })

    return {"valid": valid, "errors": errors, "total": len(rows), "valid_count": len(valid)}


async def classify_invoice(row: Dict[str, Any], company_name: str = "") -> Dict[str, Any]:
    """Use LLM to suggest direction and category for an invoice row."""
    buyer = str(row.get("buyer_name") or "").strip()
    seller = str(row.get("seller_name") or "").strip()
    item = str(row.get("item_name") or "").strip()
    amount = row.get("amount", 0)
    total_amount = row.get("total_amount", 0)

    suggested_direction_str = ""
    default_direction = "expense"
    
    if company_name:
        co_norm = _normalize_name(company_name)
        seller_norm = _normalize_name(seller)
        buyer_norm = _normalize_name(buyer)
        if co_norm and seller_norm and (co_norm in seller_norm or seller_norm in co_norm):
            suggested_direction_str = "\n(注：此发票销售方包含我司名称，属于我司销售开票，因此这笔交易方向应为 income)"
            default_direction = "income"
        elif co_norm and buyer_norm and (co_norm in buyer_norm or buyer_norm in co_norm):
            suggested_direction_str = "\n(注：此发票购买方包含我司名称，属于我司采购进项，因此这笔交易方向应为 expense)"
            default_direction = "expense"

    # 根据真实方向过滤候选科目池，防止大模型跨类别乱分类
    all_categories = list(CATEGORY_TO_ACCOUNT.keys())
    income_cats = ["主营业务收入", "其他业务收入", "营业外收入"]
    owner_cats = ["股东投入", "股东垫付", "资本公积", "老板投入", "老板垫付"]
    
    if default_direction == "income":
        categories = [c for c in all_categories if c in income_cats]
    elif default_direction == "owner_transfer":
        categories = [c for c in all_categories if c in owner_cats]
    else:
        categories = [c for c in all_categories if c not in income_cats and c not in owner_cats]

    # 如果候选列表为空，则保底使用全量列表
    if not categories:
        categories = all_categories

    context_str = f"\n用户补充的业务说明/用途: {row.get('business_context')}" if row.get("business_context") else ""
    invoice_info = f"销售方: {seller}\n购买方: {buyer}\n特定业务/项目: {row.get('特定业务类型') or '-'}\n货物/应税劳务项目: {item}\n规格型号: {row.get('specification') or '-'}\n单位: {row.get('unit') or '-'}\n数量: {row.get('quantity') or '-'}\n单价: {row.get('unit_price') or '-'}\n不含税金额: {amount}\n税率: {row.get('tax_rate') or '-'}\n税额: {row.get('tax_amount') or 0}\n价税合计: {total_amount}{suggested_direction_str}{context_str}"

    prompt = INVOICE_CLASSIFY_PROMPT.format(
        seller_name=invoice_info,
        categories=", ".join(categories),
    )
    fallback = {"direction": default_direction, "category": "其他支出" if default_direction == "expense" else "主营业务收入", "reason": "LLM分类不可用，使用默认值"}
    result = safe_chat_text_json(
        "你是一个会计分类助手。请返回JSON。",
        prompt,
        fallback=fallback,
        max_retries=1,
        scene="invoice",
    )
    if isinstance(result, dict):
        direction = default_direction
        category = result.get("category", "其他支出" if default_direction == "expense" else "主营业务收入")
        if category not in CATEGORY_TO_ACCOUNT:
            category = "其他支出" if default_direction == "expense" else "主营业务收入"
        return {"direction": direction, "category": category, "reason": result.get("reason", "")}
    return fallback


def parse_vat_rate(rate_val: Any) -> Decimal:
    if rate_val is None or rate_val == "":
        return rate("0")
    if isinstance(rate_val, (int, float, Decimal)):
        parsed = rate(rate_val)
        return parsed if parsed < Decimal("1") else rate(parsed / Decimal("100"))
    
    rate_str = str(rate_val).strip().replace("%", "")
    match = re.search(r"\d+(\.\d+)?", rate_str)
    if not match:
        return rate("0")
    try:
        value = rate(match.group())
        if value >= Decimal("1"):
            return rate(value / Decimal("100"))
        return value
    except ValueError:
        return rate("0")


async def import_invoices(company_id: str, user_id: str, rows: List[Dict[str, Any]],
                          classify: bool = True) -> Dict[str, Any]:
    """Import validated invoice rows as entries."""
    import asyncio

    # Retrieve company name to help with direction auto-matching
    company_name = await get_real_company_name(company_id, rows)

    parse_result = await parse_invoice_rows(rows, company_id=company_id)
    # Filter out duplicate rows to prevent duplicate importing
    valid_rows = [r for r in parse_result["valid"] if not r.get("is_duplicate")]

    # Concurrency control: max 5 concurrent LLM calls
    sem = asyncio.Semaphore(5)

    async def process_row(row: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            if classify:
                suggestion = await classify_invoice(row, company_name)
            else:
                suggestion = {"direction": row.get("direction", "expense"), "category": row.get("category", "其他支出")}

        seller = str(row.get("seller_name") or "").strip()
        item = str(row.get("item_name") or "").strip()
        # ponytail: 支付平台手续费发票自动识别为仅归档底稿，避免重复计入手续费并虚增银行支出对账差额
        is_platform_fee = bool(
            re.search(r"财付通|微信支付|支付宝|连连支付|网银在线", seller)
            or re.search(r"手续费|支付服务费|技术服务费", f"{item} {row.get('title', '')}")
        )
        is_archive = bool(row.get("is_archive_only") or row.get("isArchiveOnly") or is_platform_fee)

        entry_data = {
            "title": row.get("title", f"发票-{row.get('seller_name', '')}"),
            "amount": row.get("amount", 0),
            "direction": suggestion["direction"],
            "category": suggestion["category"],
            "occurDate": row.get("invoice_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "merchant": row.get("seller_name", ""),
            "source": "invoice_import",
            "invoiceNumber": row.get("invoice_number", ""),
            "billingStatus": "has_invoice",
            "is_archive_only": is_archive,
            "isArchiveOnly": is_archive,
            "payer": row.get("payer", "company_account"),
            # Set vatAmount and vatRate in the entry payload
            "vatAmount": safe_money(row.get("tax_amount")),
            "vatRate": parse_vat_rate(row.get("tax_rate", "")),
            # Preserve all important fields from the Excel file in the payload
            "invoice_code": row.get("invoice_code", ""),
            "buyer_name": row.get("buyer_name", ""),
            "seller_tax_id": row.get("seller_tax_id", ""),
            "buyer_tax_id": row.get("buyer_tax_id", ""),
            "tax_amount": row.get("tax_amount", 0),
            "tax_rate": row.get("tax_rate", ""),
            "item_name": row.get("item_name", ""),
            "specification": row.get("specification", ""),
            "unit": row.get("unit", ""),
            "quantity": row.get("quantity", None),
            "unit_price": row.get("unit_price", None),
            "invoice_source": row.get("invoice_source", ""),
            "invoice_type": row.get("invoice_type", ""),
            "invoice_status": row.get("invoice_status", ""),
            "drawer": row.get("drawer", ""),
            "remark": row.get("remark") or ("支付平台手续费发票（已自动归档为底稿，避免重复扣费与银行对账差额）" if is_platform_fee else ""),
            "rdProjectId": row.get("rdProjectId"),
            "rdExpenseType": row.get("rdExpenseType"),
            "rdOriginalAmount": row.get("rdOriginalAmount") or row.get("amount", 0),
            "rdAllocationMethod": row.get("rdAllocationMethod") or "direct",
            "rdAllocationBasis": row.get("rdAllocationBasis"),
            "rdNotes": row.get("rdNotes"),
        }

        # ponytail: 发票是原始凭证不是资金记录——导入时费用挂应付(2241)、销项挂应收(1122)，
        # 与银行流水配对确认付款后才转资金科目，杜绝"直付1002"在配对前虚减银行存款。
        if suggestion["direction"] in ("expense", "income"):
            hint = get_voucher_hint(
                suggestion["direction"],
                suggestion["category"],
                row.get("amount", 0),
                payer=str(entry_data.get("payer") or "company_account"),
            )
            if suggestion["direction"] == "expense" and hint.get("credit"):
                hint["credit"] = [dict(line, accountCode="2241", accountName="其他应付款-发票待付款") for line in hint["credit"]]
            elif suggestion["direction"] == "income" and hint.get("debit"):
                hint["debit"] = [dict(line, accountCode="1122", accountName="应收账款-发票待收") for line in hint["debit"]]
            if hint.get("debit") and hint.get("credit"):
                entry_data["voucherLines"] = hint

        try:
            if suggestion["category"] == "研发支出-费用化支出" and not (
                entry_data["rdProjectId"] and entry_data["rdExpenseType"]
            ):
                raise ValueError("研发费用必须选择研发项目和费用类别")
            entry = await save_entry(entry_data, user_id=user_id, company_id=company_id)
            return {"id": entry.get("id", ""), "title": entry.get("title", ""), "success": True}
        except Exception as e:
            return {"title": entry_data["title"], "success": False, "error": str(e)}

    # Process all valid rows concurrently
    results = await asyncio.gather(*(process_row(row) for row in valid_rows))

    imported = sum(1 for r in results if r["success"])
    entry_results = [r for r in results]

    return {
        "imported": imported,
        "total": len(parse_result["valid"]),
        "ignored_duplicates": len(parse_result["valid"]) - len(valid_rows),
        "errors": parse_result["errors"],
        "entries": entry_results,
    }
