# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票导入服务"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re

from services.database import get_conn
from services.entry_service import save_entry
from services.account_mapper import (
    CATEGORY_TO_ACCOUNT,
    EMPLOYEE_PAYER_PREFIX,
    INTERNAL_SETTLEMENT_CATEGORIES,
    PAYER_BOSS,
    get_voucher_hint,
    is_alias_category,
    lookup as lookup_account,
    normalize_payer,
)
from services.invoice_llm_prompts import INVOICE_CLASSIFY_PROMPT
from services.llm import safe_chat_text_json
from core.money import ZERO, money, money_text, rate
from services.account_service import _json_object, get_company
from services.vat_service import get_vat_config, is_vat_sales_entry, split_income_vat


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


_USCC_PATTERN = re.compile(r"\b9[1239]\d{6}[0-9A-HJ-NPQRTUWXY]{10}\b")


def _mask_own_company_in_prompt(text: str, company_name: str = "") -> str:
    """脱敏发给 LLM 的提示词文本：剥离我方企业全称与 18 位统一社会信用代码。"""
    if not text:
        return text
    masked = _USCC_PATTERN.sub("[统一社会信用代码已脱敏]", text)
    target = company_name.strip()
    if target:
        variants = {
            target,
            target.replace("（", "(").replace("）", ")"),
            target.replace("(", "（").replace(")", "）"),
        }
        for variant in variants:
            if variant:
                masked = masked.replace(variant, "我方企业")
    return masked


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(str(value or "")[:10])
        return True
    except ValueError:
        return False


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

    # 去重预检：改查 invoices 表（走 (company_id, invoice_number) 索引），
    # 不再对 entries.payload 做 json_extract 全表扫。
    existing_invoices = set()
    existing_fingerprints = set()
    if company_id:
        from services.invoice_ledger_service import existing_fingerprints as _fingerprints
        from services.invoice_ledger_service import existing_invoice_numbers

        try:
            existing_invoices = await existing_invoice_numbers(company_id)
            existing_fingerprints = await _fingerprints(company_id)
        except Exception:
            existing_invoices = set()
            existing_fingerprints = set()

    for i, row in enumerate(rows):
        invoice_number = str(row.get("invoice_number") or row.get("发票号码") or "").strip()
        amount = safe_money(row.get("total_amount") or row.get("amount") or row.get("价税合计"))
        invoice_date = str(row.get("invoice_date") or row.get("date") or row.get("开票日期") or "").strip()
        seller_name = str(row.get("seller_name") or row.get("seller") or row.get("销售方") or "").strip()
        buyer_name = str(row.get("buyer_name") or row.get("buyer") or row.get("购买方") or "").strip()

        # 发票要素门槛：任何真实发票至少能给出号码或开票日期。只带金额的行是
        # 银行流水/其他表格被误映射进来的典型形态（2026-11 杭州银行流水上传到
        # 发票导入，账户余额列被当成金额，批量生成了 16 条无号码无日期的草稿）。
        # 这里按行拒绝，避免调用方绕过前端文件校验直接打 /api/invoice/import。
        if not invoice_number and not invoice_date:
            errors.append({
                "row": i,
                "reason": "缺少发票号码和开票日期，无法识别为发票行；银行流水请用银行流水导入",
            })
            continue
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

        if invoice_number:
            is_duplicate = invoice_number in existing_invoices
        else:
            # 无发票号：退化为指纹判重（日期+对方+金额+方向），与写路径同一口径
            from services.invoice_ledger_service import build_fingerprint

            fingerprint = build_fingerprint(
                direction=direction if direction in ("income", "expense") else "expense",
                invoice_number="",
                invoice_date=date.fromisoformat(invoice_date[:10]) if _is_iso_date(invoice_date) else None,
                counterparty=seller_name,
                amount=amount,
            )
            is_duplicate = fingerprint in existing_fingerprints

        item_name = str(row.get("item_name") or "").strip()
        suggested_category = match_category_by_item(item_name, direction) or ""
        valid.append({
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "amount": amount,
            "seller_name": seller_name,
            "buyer_name": buyer_name,
            "direction": direction,
            "is_duplicate": is_duplicate,
            "title": f"发票-{seller_name or invoice_number}",
            # 电票 XML/PDF 管线没有分类列，预览默认值不能一律兜底「其他支出」：
            # 品名已写明「软件测试服务」这类无争议科目时，先给确定性建议，用户仍可改。
            # 只有支出方向才给建议（收入/往来另有科目口径）。
            "suggestedCategory": suggested_category,
            "suggestedCategoryReason": (
                f"按发票品名「{item_name}」建议" if suggested_category else ""
            ),
            **{k: v for k, v in row.items() if k not in ("invoice_number", "invoice_date", "amount", "seller_name", "buyer_name", "direction", "title")},
        })

    return {"valid": valid, "errors": errors, "total": len(rows), "valid_count": len(valid)}


# 收入类科目：销项票的类别必须落在这里，贷方科目才是收入/增值税，挂账凭证才能过收支校验
INCOME_CATEGORIES = (
    "主营业务收入",
    "其他业务收入",
    "营业外收入",
    "非流动资产处置净收益",
    "政府补助",
    "捐赠收益",
    "盘盈收益",
)


def _category_fits_direction(category: str, direction: str) -> bool:
    """分类是否与方向同侧：销项必须落收入类，进项不能落收入类。"""
    if not category or category not in CATEGORY_TO_ACCOUNT:
        return False
    if direction == "income":
        return category in INCOME_CATEGORIES
    if direction == "expense":
        return category not in INCOME_CATEGORIES
    return True


def match_category_by_item(item_name: str, direction: str) -> str | None:
    """按发票品名（货物/劳务名称）确定性匹配到规范二级科目分类。

    只覆盖无争议的高频品名，命中返回的分类名必须是 ``CATEGORY_TO_ACCOUNT`` 里的 key；
    否则返回 None 交给 LLM 分类。收入/往来方向不走这里（另有方向与科目口径）。
    """
    if direction != "expense":
        return None
    text = str(item_name or "").lower()
    rules = (
        (("房租", "租金", "租赁", "物业"), "租金物业"),
        (("机票", "火车", "高铁", "动车", "铁路", "客票", "票价", "住宿", "酒店", "差旅"), "差旅费"),
        (("打车", "出租车", "网约车", "交通"), "交通费"),
        (("办公用品", "文具", "打印", "纸张", "耗材", "办公"), "办公费"),
        (("餐饮", "宴请", "招待", "饭"), "业务招待费"),
        (("咨询", "顾问", "审计", "律师"), "咨询费"),
        (("快递", "物流", "运输", "运费"), "运费及物流"),
        (("电费", "水费", "水电", "燃气"), "水电通讯费"),
        (("宽带", "话费", "通讯", "电信"), "通讯费"),
        # 支付通道费先判定：微信/支付宝的「支付通道服务费」是交易手续费，
        # 不是信息技术服务，不能被下面的 IT 规则抢走。
        (("支付通道", "通道服务费", "支付服务费", "手续费", "汇兑"), "银行及金融手续费"),
        (("软件", "系统", "域名", "云", "服务器", "技术服务", "信息技术", "saas", "接口", "短信", "验证码"), "信息技术服务费"),
    )
    for keywords, category in rules:
        if any(kw in text for kw in keywords):
            return category
    return None


async def classify_invoice(
    row: Dict[str, Any], company_name: str = "", *, user_id: str = "", company_id: str = ""
) -> Dict[str, Any]:
    """Use LLM to suggest direction and category for an invoice row."""
    buyer = str(row.get("buyer_name") or "").strip()
    seller = str(row.get("seller_name") or "").strip()
    item = str(row.get("item_name") or "").strip()
    amount = row.get("amount", 0)
    total_amount = row.get("total_amount", 0)

    # 调用方显式给了方向（导入预览里用户确认过/parse 按购销方判定）就以它为准：
    # 否则 LLM 可能把销项票判成支出，配出贷方费用科目的怪凭证。
    requested_direction = str(row.get("direction") or "").strip()
    explicit_direction = (
        requested_direction
        if requested_direction in ("expense", "income", "owner_transfer")
        else ""
    )
    suggested_direction_str = ""
    default_direction = explicit_direction or "expense"
    seller_for_llm = seller
    buyer_for_llm = buyer

    if company_name and not explicit_direction:
        co_norm = _normalize_name(company_name)
        seller_norm = _normalize_name(seller)
        buyer_norm = _normalize_name(buyer)
        if co_norm and seller_norm and (co_norm in seller_norm or seller_norm in co_norm):
            suggested_direction_str = "\n(注：此发票销售方为我方企业，属于我方销售开票，因此这笔交易方向应为 income)"
            default_direction = "income"
            seller_for_llm = "我方企业（销方）"
        elif co_norm and buyer_norm and (co_norm in buyer_norm or buyer_norm in co_norm):
            suggested_direction_str = "\n(注：此发票购买方为我方企业，属于我方采购进项，因此这笔交易方向应为 expense)"
            default_direction = "expense"
            buyer_for_llm = "我方企业（购方）"

    business_context = str(row.get("business_context") or "").strip()

    # 品名确定性匹配优先：仅在无用户补充说明时命中规范二级科目即直接返回，
    # 避免批量导入被 LLM 归到兜底「其他支出」；用户显式补充了业务说明时
    # 说明正是为了请大模型结合背景重新判断，不能再被品名规则短路。
    if not business_context:
        matched = match_category_by_item(item, default_direction)
        if matched:
            return {"direction": default_direction, "category": matched, "reason": f"品名关键词「{item}」匹配到规范分类"}

    # 根据真实方向过滤候选科目池，防止大模型跨类别乱分类（剔除口语别名和内部结算分类）
    all_categories = [
        c for c in CATEGORY_TO_ACCOUNT.keys()
        if not is_alias_category(c) and c not in INTERNAL_SETTLEMENT_CATEGORIES
    ]
    income_cats = list(INCOME_CATEGORIES)
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

    context_str = f"\n用户补充的业务说明/用途: {business_context}" if business_context else ""
    invoice_info = _mask_own_company_in_prompt(
        f"销售方: {seller_for_llm}\n购买方: {buyer_for_llm}\n特定业务/项目: {row.get('特定业务类型') or '-'}\n货物/应税劳务项目: {item}\n规格型号: {row.get('specification') or '-'}\n单位: {row.get('unit') or '-'}\n数量: {row.get('quantity') or '-'}\n单价: {row.get('unit_price') or '-'}\n不含税金额: {amount}\n税率: {row.get('tax_rate') or '-'}\n税额: {row.get('tax_amount') or 0}\n价税合计: {total_amount}\n备注: {row.get('remark') or '-'}{suggested_direction_str}{context_str}",
        company_name,
    )

    prompt = INVOICE_CLASSIFY_PROMPT.format(
        seller_name=invoice_info,
        categories=", ".join(categories),
    )
    fallback = {"direction": default_direction, "category": "其他支出" if default_direction == "expense" else "主营业务收入", "reason": "LLM分类不可用，使用默认值"}
    result = await asyncio.to_thread(
        safe_chat_text_json,
        "你是一个会计分类助手。请返回JSON。",
        prompt,
        fallback=fallback,
        max_retries=1,
        scene="invoice",
        user_id=user_id,
        company_id=company_id,
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


INVOICE_PENDING_PAYABLE = {"accountCode": "2241", "accountName": "其他应付款-发票待付款"}
INVOICE_PENDING_RECEIVABLE = {"accountCode": "1122", "accountName": "应收账款-发票待收"}
INCOME_CREDIT_ACCOUNTS = {"5001", "5051"}
VAT_PAYABLE_ACCOUNT_CODE = "2221026"


def _is_personal_advance_payer(payer: str) -> bool:
    """付款方是否是具体的垫付人（老板，或带姓名的员工）。

    裸 ``employee_personal`` 不知道是谁的钱，`resolve_entry_person` 也不会建档；
    此时仍按原逻辑挂供应商，避免 2241 行两头落空。
    """
    if payer == PAYER_BOSS:
        return True
    if payer.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        return bool(payer.split(":", 1)[1].strip())
    return False


def _attach_partner_auxiliary(
    voucher: Dict[str, Any],
    partner: Dict[str, str],
    *,
    skip_person_advance_lines: bool = False,
) -> Dict[str, Any]:
    """把往来单位写进挂账分录（1122/2241 等往来科目）的 auxiliary。

    只挂往来科目：费用/收入行挂客户没有意义，挂上去反而污染按往来单位的费用分析。

    ``skip_person_advance_lines``：发票由老板/员工垫付时，2241 的对手方是垫付人
    而不是供应商，这里留空，交给 ``_add_lines`` 按 metadata["payer"] 解析个人
    往来单位（行上已有 partner 时它不会覆盖）。
    """
    from core.chart_policy import is_person_advance_account

    auxiliary = {"partner_id": partner["id"], "partner_name": partner["name"]}
    output: Dict[str, Any] = {}
    for side in ("debit", "credit"):
        output[side] = [
            {
                **line,
                "auxiliary": {
                    **(line.get("auxiliary") or {}),
                    **auxiliary,
                },
            }
            if str(line.get("accountCode") or "").startswith(("1122", "1123", "2202", "2203", "1221", "2241"))
            and not (
                skip_person_advance_lines
                and is_person_advance_account(line.get("accountCode"))
            )
            else line
            for line in (voucher.get(side) or [])
        ]
    return output


async def _find_offset_wanglai(
    company_id: str, direction: str, amount: Decimal, invoice_date: str
) -> Optional[Dict[str, Any]]:
    """找以前期间等额、未被发票冲抵的往来暂付/暂收凭证。

    "先付款、后收票"的闭环：5 月银行付款认可入账会挂 借 122199（往来暂付款）；
    6 月发票导入时命中该暂付款，则发票凭证的往来侧直接落 122199（费用票贷方 /
    收入票借方）完成对冲，而不是挂 2241/1122 待收付。命中返回
    {id, accountName, occurredOn}；无候选返回 None。
    FIFO 取最老的一笔（未冲抵的按发生期升序）。
    """
    from sqlalchemy import select

    from db.models import JournalEntry, JournalLine
    from db.session import session_scope
    from services.accounting_core_service import ACTIVE_ENTRY_STATUSES

    stmt_amount = money(amount)
    if stmt_amount <= ZERO:
        return None
    invoice_day = str(invoice_date or "")[:10]
    if not invoice_day:
        return None
    line_code = "122199" if direction == "expense" else "2241"
    line_direction = "debit" if direction == "expense" else "credit"

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry.id, JournalEntry.summary, JournalEntry.occurred_on, JournalLine.account_name)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(tuple(ACTIVE_ENTRY_STATUSES)),
                    JournalEntry.entry_type == "standard",
                    JournalEntry.occurred_on < date.fromisoformat(invoice_day),
                    JournalEntry.metadata_json["category"].as_string() == "往来款",
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == line_code,
                    JournalLine.direction == line_direction,
                    JournalLine.amount == stmt_amount,
                    # 排除已被发票冲抵的暂付款（linkedInvoiceEntryId 缺失或为空）
                    JournalEntry.metadata_json["linkedInvoiceEntryId"].as_string().is_(None)
                    | (JournalEntry.metadata_json["linkedInvoiceEntryId"].as_string() == ""),
                )
                .order_by(JournalEntry.occurred_on.asc())
                .limit(1)
            )
        ).first()
        if not rows:
            return None
        return {
            "id": rows[0],
            "summary": rows[1],
            "occurredOn": str(rows[2]),
            "accountName": rows[3] or ("其他应收款-待收款项" if direction == "expense" else "其他应付款-其他"),
        }


def _validate_invoice_voucher(
    direction: str, voucher: Dict[str, Any], *, reversal: bool = False, offset: bool = False
) -> Dict[str, Any]:
    """发票凭证的科目白名单：票是凭证，不是资金记录。

    - 费用票：贷方只能是 2241 应付（未付款状态），不允许直接贷 1002
    - 收入票：借方只能是 1122 应收，贷方只能是收入科目 + 应交增值税
    这条校验是"发票挂账被覆盖"事故的回归护栏，不通过就拒绝入账而不是静默降级。

    reversal=True 是红冲进项冲销凭证：借贷已对调、金额已取正，
    借方必须是 2241（冲销原应付），贷方是费用科目。

    offset=True 是"先付款、后收票"的往来冲抵凭证：费用票贷方挂 122199
    （冲此前往来暂付款）、收入票借方挂 2241（冲此前往来暂收款），
    替代默认的 2241/1122 挂账。
    """
    debit = voucher.get("debit") or []
    credit = voucher.get("credit") or []
    debit_total = sum((money(line.get("amount") or ZERO) for line in debit), ZERO)
    credit_total = sum((money(line.get("amount") or ZERO) for line in credit), ZERO)
    if debit_total != credit_total:
        raise ValueError(f"发票凭证借贷不平：借 {debit_total} 贷 {credit_total}")
    if direction == "expense":
        codes = {str(line.get("accountCode") or "") for line in (debit if reversal else credit)}
        allowed_payable = {INVOICE_PENDING_PAYABLE["accountCode"]} | ({"122199"} if offset else set())
        if codes - allowed_payable:
            raise ValueError(
                "红冲进项发票必须借 2241 其他应付款-发票待付款，冲销原应付"
                if reversal
                else "费用发票必须挂贷方 2241 其他应付款-发票待付款（或冲抵往来暂付款 122199），不能直接冲减银行存款"
            )
    elif direction == "income":
        debit_codes = {str(line.get("accountCode") or "") for line in debit}
        allowed_receivable = {INVOICE_PENDING_RECEIVABLE["accountCode"]} | ({"2241"} if offset else set())
        if debit_codes - allowed_receivable:
            raise ValueError(
                "收入发票必须挂借方 1122 应收账款（或冲抵往来暂收款 2241），不能直接增加银行存款"
            )
        allowed = INCOME_CREDIT_ACCOUNTS | {VAT_PAYABLE_ACCOUNT_CODE}
        extra = {str(line.get("accountCode") or "") for line in credit} - allowed
        if extra:
            raise ValueError(f"收入发票贷方出现非收入/非增值税科目：{sorted(extra)}")
    return voucher


async def import_invoices(company_id: str, user_id: str, rows: List[Dict[str, Any]],
                          classify: bool = True) -> Dict[str, Any]:
    """Import validated invoice rows as entries."""
    import asyncio

    try:
        vat_config = get_vat_config(await get_company(company_id))
    except ValueError as exc:
        return {
            "imported": 0, "total": len(rows), "ignored_duplicates": 0,
            "errors": [{"row": 0, "error": str(exc)}], "results": [],
            "blocked": True, "blockedReason": str(exc),
        }

    # Retrieve company name to help with direction auto-matching
    company_name = await get_real_company_name(company_id, rows)

    parse_result = await parse_invoice_rows(rows, company_id=company_id)
    # Filter out duplicate rows to prevent duplicate importing
    valid_rows = [r for r in parse_result["valid"] if not r.get("is_duplicate")]

    # 往来单位自动建档（WP2）：在写凭证之前解析，才能把 partner 挂进 1122/2241 分录，
    # 让发票一入账往来账就有主。建档失败不阻断导入（往来可后续补挂）。
    partner_by_index: list[dict[str, Any] | None] = [None] * len(valid_rows)
    try:
        from services.auxiliary_service import ensure_partners_for_invoice_rows

        partner_by_index = await ensure_partners_for_invoice_rows(company_id, user_id, valid_rows)
    except Exception:
        partner_by_index = [None] * len(valid_rows)

    # Concurrency control: max 5 concurrent LLM calls
    sem = asyncio.Semaphore(5)

    async def process_row(index: int, row: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            if classify:
                direction = str(row.get("direction") or "expense").strip() or "expense"
                explicit_category = str(row.get("category") or "").strip()
                suggested_category = str(row.get("suggestedCategory") or "").strip()
                if explicit_category and _category_fits_direction(explicit_category, direction):
                    # 预览里用户确认过（或按品名规则给出）的分类优先，不让 LLM 改写；
                    # 只有缺分类 / 分类与方向不同侧时才补分类。
                    suggestion = {
                        "direction": direction,
                        "category": explicit_category,
                        "reason": "沿用导入行已确认分类",
                    }
                    category_source = "manual"
                elif suggested_category and _category_fits_direction(suggested_category, direction):
                    # 调用方没有提交 category，但 parse 已按品名给出建议：直接采用
                    # 规则结论，省掉一次 LLM 调用。
                    suggestion = {
                        "direction": direction,
                        "category": suggested_category,
                        "reason": "沿用品名规则建议分类",
                    }
                    category_source = "rule"
                elif direction == "income":
                    # 销项票没有分类列时沿用旧口径，不为每一行发一次 LLM 请求；
                    # 其他业务收入等特殊情况由用户在预览里显式改。
                    suggestion = {
                        "direction": "income",
                        "category": "主营业务收入",
                        "reason": "销项票按主营业务收入兜底",
                    }
                    category_source = "fallback"
                else:
                    suggestion = await classify_invoice(
                        row, company_name, user_id=user_id, company_id=company_id
                    )
                    reason = str(suggestion.get("reason") or "")
                    if reason.startswith("品名关键词"):
                        category_source = "rule"
                    elif "默认值" in reason:
                        category_source = "fallback"
                    else:
                        category_source = "llm"
            else:
                direction = str(row.get("direction") or "expense")
                category = str(row.get("category") or "").strip()
                raw_category = category
                # 类别必须与方向同侧，否则挂账凭证会被收支校验拒绝（销项票误标支出类别
                # 会配出贷方支出科目）：
                #   - 销项票缺类别 / 误标支出类别 → 主营业务收入
                #   - 支出票误标收入类别 → 其他支出
                if direction == "income" and category not in INCOME_CATEGORIES:
                    category = "主营业务收入"
                elif direction == "expense" and category in INCOME_CATEGORIES:
                    category = "其他支出"
                suggestion = {"direction": direction, "category": category}
                category_source = "manual" if raw_category else "fallback"

        seller = str(row.get("seller_name") or "").strip()
        item = str(row.get("item_name") or "").strip()
        # ponytail: 支付平台手续费发票自动识别为仅归档底稿，避免重复计入手续费并虚增银行支出对账差额
        # 品名词只认"手续费/支付服务费"这类支付通道特征；"技术服务费"是普通经营采购
        # （SaaS/AI 服务等）的规范品名，裸命中会把正常供应商发票误归档——2026-10-31
        # DeepSeek 技术服务费发票被误归档，导致待办撮合池漏票、付款侧永远报"待补票据"。
        is_platform_fee = bool(
            re.search(r"财付通|微信支付|支付宝|连连支付|网银在线", seller)
            or re.search(r"手续费|支付服务费", f"{item} {row.get('title', '')}")
        )
        is_archive = bool(row.get("is_archive_only") or row.get("isArchiveOnly") or is_platform_fee)

        # 导入确认卡片的付款方：默认公司账户，用户选垫付才改。垫付=发票已由个人
        # 付清，2241 的对手方是垫付人；未付款票的对手方仍是供应商，不能被抢走。
        payer = normalize_payer(row.get("payer"))
        payment_status = str(
            row.get("paymentStatus") or row.get("payment_status") or ""
        ).strip().lower()
        personal_advance = (
            suggestion["direction"] == "expense"
            and _is_personal_advance_payer(payer)
            and payment_status != "unpaid"
        )

        entry_data = {
            "title": row.get("title", f"发票-{row.get('seller_name', '')}"),
            "amount": row.get("amount", 0),
            "direction": suggestion["direction"],
            "category": suggestion["category"],
            "occurDate": row.get("invoice_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "merchant": row.get("seller_name", ""),
            "source": "invoice_import",
            # 分类来源留痕：manual=用户/调用方确认，rule=品名规则，llm=模型补分类，
            # fallback=仍落到兜底科目（导入后需复核）。诊断脚本与后续复核都读它。
            "categorySource": category_source,
            "categoryReason": str(suggestion.get("reason") or ""),
            "invoiceNumber": row.get("invoice_number", ""),
            "billingStatus": "has_invoice",
            "is_archive_only": is_archive,
            "isArchiveOnly": is_archive,
            "payer": payer,
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
        if payment_status:
            entry_data["paymentStatus"] = payment_status
        elif personal_advance:
            # 老板/员工已经掏钱付了这张票，不是待付款；结账核对时按非公户结算处理。
            entry_data["paymentStatus"] = "paid"

        # 去重指纹：发票号是最强业务键；缺失时退化为 日期+销售方+金额+方向 的组合键。
        # 否则 external_key 为 NULL，唯一约束不生效，同一张发票跨渠道/重复导入无法拦截。
        invoice_no = str(row.get("invoice_number") or "").strip()
        if not entry_data.get("dedupFingerprint"):
            if invoice_no:
                entry_data["dedupFingerprint"] = invoice_no
            else:
                entry_data["dedupFingerprint"] = ":".join([
                    "inv",
                    str(entry_data.get("occurDate") or "")[:10],
                    _normalize_name(str(entry_data.get("merchant") or "")),
                    money_text(safe_money(row.get("amount", 0))),
                    str(suggestion["direction"]),
                ])

        # 发票是原始凭证不是资金记录——导入时费用挂应付(2241)、销项挂应收(1122)，
        # 与银行流水配对确认付款后才转资金科目，杜绝"直付1002"在配对前虚减银行存款。
        # 注意：这些凭证行必须由服务端以 trusted_voucher=True 写入，否则会被
        # entry_service 的确定性重算覆盖回"贷 1002 银行直付"。
        if suggestion["direction"] in ("expense", "income"):
            vat_hint_rate = None
            invoice_tax = safe_money(row.get("tax_amount"))
            if suggestion["direction"] == "income" and is_vat_sales_entry(suggestion["category"]):
                if invoice_tax > ZERO:
                    # 发票上载明的税额最权威（可能不是公司默认征收率）
                    net_amount, tax_amount = split_income_vat(row.get("amount", 0), explicit_tax=invoice_tax)
                    vat_hint_rate = None
                    entry_data["vatRate"] = parse_vat_rate(row.get("tax_rate", ""))
                    entry_data["vatAmount"] = tax_amount
                    entry_data["amountExcludingTax"] = net_amount
                    entry_data["vatSeparated"] = True
                else:
                    vat_hint_rate = vat_config.rate
                    net_amount, tax_amount = split_income_vat(row.get("amount", 0), vat_config.rate)
                    entry_data["vatRate"] = vat_config.rate
                    entry_data["vatAmount"] = tax_amount
                    entry_data["amountExcludingTax"] = net_amount
                    entry_data["vatSeparated"] = True
            hint = get_voucher_hint(
                suggestion["direction"],
                suggestion["category"],
                row.get("amount", 0),
                str(entry_data.get("payer") or "company_account"),
                vat_rate=vat_hint_rate,
                vat_amount=entry_data.get("vatAmount") if vat_hint_rate is None and entry_data.get("vatSeparated") else None,
            )
            # 先付款、后收票：命中以前期间等额、未冲抵的往来暂付/暂收 → 往来侧
            # 直接对冲（费用票贷 122199 / 收入票借 2241），不再挂 2241/1122 待收付；
            # 红冲（负金额）不走对冲。探测失败静默回落默认挂账。
            # 个人垫付的票不存在"公司先付过"的暂付款，不参与对冲，否则会静默把
            # 应挂股东的 2241 改成 122199。
            is_reversal = suggestion["direction"] == "expense" and money(row.get("amount", 0)) < ZERO
            offset_wanglai = None
            if suggestion["direction"] in ("expense", "income") and not is_reversal and not personal_advance:
                try:
                    offset_wanglai = await _find_offset_wanglai(
                        company_id,
                        suggestion["direction"],
                        safe_money(row.get("amount", 0)),
                        str(entry_data.get("occurDate") or ""),
                    )
                except Exception:
                    pass
            if suggestion["direction"] == "expense" and hint.get("credit"):
                if offset_wanglai:
                    hint["credit"] = [dict(line, accountCode="122199", accountName=offset_wanglai["accountName"]) for line in hint["credit"]]
                    entry_data["offsetFromEntryId"] = offset_wanglai["id"]
                else:
                    # 垫付票的 2241 是「公司欠垫付人」，不是「发票待付款」。
                    payable_name = "其他应付款" if personal_advance else "其他应付款-发票待付款"
                    hint["credit"] = [dict(line, accountCode="2241", accountName=payable_name) for line in hint["credit"]]
            elif suggestion["direction"] == "income" and hint.get("debit"):
                if offset_wanglai:
                    hint["debit"] = [dict(line, accountCode="2241", accountName=offset_wanglai["accountName"]) for line in hint["debit"]]
                    entry_data["offsetFromEntryId"] = offset_wanglai["id"]
                else:
                    hint["debit"] = [dict(line, accountCode="1122", accountName="应收账款-发票待收") for line in hint["debit"]]
            # 红冲进项（负金额）：借贷对调、金额取正，形成冲销凭证（借 2241 / 贷费用）。
            # 销项红冲的增值税冲销暂不处理，仍按原样失败。
            if is_reversal:
                hint = {
                    "debit": [{**line, "amount": abs(money(line.get("amount") or ZERO))} for line in hint.get("credit", [])],
                    "credit": [{**line, "amount": abs(money(line.get("amount") or ZERO))} for line in hint.get("debit", [])],
                }
            if hint.get("debit") and hint.get("credit"):
                entry_data["voucherLines"] = _validate_invoice_voucher(
                    suggestion["direction"], hint, reversal=is_reversal, offset=bool(offset_wanglai)
                )

        # 往来单位挂到挂账分录（费用票贷 2241 / 收入票借 1122）上：
        # 往来账、账龄、核算项目余额表都以行级 auxiliary 为数据源。
        partner = partner_by_index[index] if index < len(partner_by_index) else None
        if partner and entry_data.get("voucherLines"):
            entry_data["voucherLines"] = _attach_partner_auxiliary(
                entry_data["voucherLines"],
                partner,
                skip_person_advance_lines=personal_advance,
            )

        try:
            if suggestion["category"] == "研发支出-费用化支出" and not (
                entry_data["rdProjectId"] and entry_data["rdExpenseType"]
            ):
                raise ValueError("研发费用必须选择研发项目和费用类别")
            entry = await save_entry(
                entry_data, user_id=user_id, company_id=company_id, trusted_voucher=True
            )
            # 冲抵落账后给暂付款回写 linkedInvoiceEntryId（防二次冲抵的标记）。
            # draft 暂付款走 metadata，已过账走 POSTED_PROJECTION_FIELDS 投影，
            # update_draft 内部自动分流。标记失败不阻断导入——账务已正确，
            # 最坏情况是该暂付款可能被再次选中，人工改凭证即可纠正。
            if offset_wanglai:
                try:
                    from services.accounting_core_service import update_draft
                    from db.session import session_scope

                    async with session_scope() as session:
                        await update_draft(
                            session,
                            offset_wanglai["id"],
                            {"linkedInvoiceEntryId": entry.get("id", "")},
                            company_id=company_id,
                            user_id=user_id,
                            request_id=f"offset-inv-{entry.get('id', '')}",
                        )
                except Exception:
                    pass
            return {
                "id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "success": True,
                # 发票主数据双写用的快照（返回前剔除，不进 API 响应）
                "_invoicePayload": entry_data,
                "_partnerId": (partner or {}).get("id"),
                "_offset": {
                    "invoiceEntryId": entry.get("id", ""),
                    "offsetEntryId": offset_wanglai["id"],
                    "offsetTitle": offset_wanglai.get("summary", ""),
                    "amount": money_text(safe_money(row.get("amount", 0))),
                    "occurredOn": offset_wanglai.get("occurredOn", ""),
                } if offset_wanglai else None,
            }
        except Exception as e:
            return {"title": entry_data["title"], "success": False, "error": str(e)}

    # Process all valid rows concurrently
    results = await asyncio.gather(
        *(process_row(index, row) for index, row in enumerate(valid_rows))
    )

    imported = sum(1 for r in results if r["success"])

    # ── 发票主数据双写 ────────────────────────────────────────────────────
    # 凭证已落库后再写台账：唯一约束 (company_id, dedup_fingerprint) 保证重跑幂等，
    # 单批一次 session，避免逐行开关事务。
    invoice_records: list[tuple[str | None, dict[str, Any], str | None]] = []
    offsets: list[Dict[str, Any]] = []
    for result in results:
        payload = result.pop("_invoicePayload", None)
        partner_id = result.pop("_partnerId", None)
        offset_info = result.pop("_offset", None)
        if offset_info:
            offsets.append(offset_info)
        if result.get("success") and result.get("id") and isinstance(payload, dict):
            invoice_records.append((str(result["id"]), payload, partner_id))
    invoice_written = 0
    if invoice_records:
        from services.invoice_ledger_service import record_invoices_bulk

        try:
            invoice_written = await record_invoices_bulk(company_id, invoice_records)
        except Exception:
            # 台账写入失败不能让整批导入失败：凭证是权威，台账可回填
            invoice_written = 0

    entry_results = [r for r in results]

    return {
        "imported": imported,
        "total": len(parse_result["valid"]),
        "ignored_duplicates": len(parse_result["valid"]) - len(valid_rows),
        "errors": parse_result["errors"],
        "entries": entry_results,
        "invoicesWritten": invoice_written,
        "partnersLinked": sum(1 for _, _, partner_id in invoice_records if partner_id),
        "offsets": offsets,
    }
