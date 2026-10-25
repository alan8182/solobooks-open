# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""旧账导入（P0 WP4）：科目余额表 → 期初，凭证分录 → 历史凭证。

两条路径共用一套"解析 → 预览 → 确认"形状（对齐发票/银行导入的 parse 惯例）：
``{valid, errors:[{row, reason}], warnings, mapping, totals}``。

红线（与 P0 计划一致）：
- 凭证行一律由服务端以 ``trusted_voucher=True`` 写入，绝不信任客户端行数据；
- 逐凭证校验借贷平衡，不平衡整张拒绝（不做"差一分自动补"这类危险动作）；
- 默认导入为 draft 待审，只有显式勾选才批量过账；
- 每批导入生成 batch_id，``dedupFingerprint`` 让重导同批自动跳过。
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import ChartAccount, JournalEntry, SourceTransaction, VoucherSequence
from db.session import session_scope
from services.accounting_core_service import post_journal
from services.database import get_conn
from services.entry_service import save_entries_bulk
from services.opening_balance_service import save_opening_balances

SOURCE_TYPE = "legacy_import"
MAPPING_KEY = "legacy_import_mapping"

BALANCE_ALIASES = {
    "accountCode": ("科目编码", "科目代码", "科目编号", "account_code", "accountcode", "code"),
    "accountName": ("科目名称", "科目", "account_name", "accountname", "name"),
    "direction": ("方向", "余额方向", "direction"),
    "debit": ("期初余额(借)", "期初余额（借）", "期初借方", "借方期初", "debit_balance", "opening_debit"),
    "credit": ("期初余额(贷)", "期初余额（贷）", "期初贷方", "贷方期初", "credit_balance", "opening_credit"),
    "ytdDebit": ("本年累计借方", "本年累计借方发生额", "ytd_debit", "debit_ytd"),
    "ytdCredit": ("本年累计贷方", "本年累计贷方发生额", "ytd_credit", "credit_ytd"),
    "partnerName": ("往来单位", "客户", "供应商", "partner_name", "partnername"),
}

VOUCHER_ALIASES = {
    "voucherNo": ("凭证号", "凭证编号", "凭证字号", "voucher_no", "voucherno"),
    "date": ("日期", "记账日期", "业务日期", "date", "occur_date"),
    "summary": ("摘要", "说明", "summary", "description"),
    "accountCode": ("科目编码", "科目代码", "科目编号", "account_code", "accountcode", "code"),
    "accountName": ("科目名称", "account_name", "accountname", "name"),
    "debit": ("借方金额", "借方", "debit", "debit_amount"),
    "credit": ("贷方金额", "贷方", "credit", "credit_amount"),
    "partnerName": ("往来单位", "客户", "供应商", "partner_name"),
    "departmentName": ("部门", "department"),
    "projectName": ("项目", "project"),
    # 数量金额式（P1 WP-D）：三列全可选，只有勾了数量核算的科目才会落库
    "quantity": ("数量", "quantity", "qty"),
    "unitPrice": ("单价", "unit_price", "unitprice"),
    "unit": ("单位", "计量单位", "unit"),
}


def _lookup(row: dict[str, Any], aliases: Iterable[str]) -> Any:
    normalized = {str(key).strip().lower().replace(" ", ""): value for key, value in row.items()}
    for alias in aliases:
        key = alias.strip().lower().replace(" ", "")
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return None


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return ZERO
    try:
        return money(str(value).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _optional_number(value: Any) -> Decimal | None:
    """数量/单价宽松解析：空或非法视为未填，0 也视为未填。"""
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed != ZERO else None


def _fmt_number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _pick(row: dict[str, Any], aliases: dict[str, tuple[str, ...]], key: str) -> Any:
    return _lookup(row, aliases[key])


async def _account_index(company_id: str) -> tuple[dict[str, ChartAccount], dict[str, list[ChartAccount]]]:
    async with session_scope() as session:
        accounts = (
            await session.execute(select(ChartAccount).where(ChartAccount.company_id == company_id))
        ).scalars().all()
    by_code = {account.code: account for account in accounts}
    by_name: dict[str, list[ChartAccount]] = {}
    for account in accounts:
        by_name.setdefault(account.name.strip(), []).append(account)
    return by_code, by_name


async def load_mapping(company_id: str) -> dict[str, str]:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    if not row:
        return {}
    payload = row["payload"]
    data = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    mapping = data.get(MAPPING_KEY) or {}
    return {str(key): str(value) for key, value in mapping.items()} if isinstance(mapping, dict) else {}


async def save_mapping(company_id: str, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        payload = row["payload"]
        data = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        current = data.get(MAPPING_KEY) or {}
        if not isinstance(current, dict):
            current = {}
        current.update({str(key): str(value) for key, value in mapping.items()})
        data[MAPPING_KEY] = current
        await conn.execute(
            "UPDATE auth_companies SET payload = ? WHERE company_id = ?",
            (json.dumps(data, ensure_ascii=False), company_id),
        )
        await conn.commit()
    finally:
        await conn.close()


def _resolve_code(
    raw_code: str,
    raw_name: str,
    by_code: dict[str, ChartAccount],
    by_name: dict[str, list[ChartAccount]],
    mapping: dict[str, str],
) -> tuple[str, str]:
    """返回 (最终科目编码, 建议编码)。

    只自动应用"用户已确认的映射"和"编码精确命中"；名称匹配仅作**建议**，
    不自动落库——不同软件编码体系差异大，静默按名字改码会把金额记到错误科目，
    这类错误在报表层面极难发现。建议由前端映射表逐条确认后回传 mapping。
    """
    code = str(raw_code or "").strip()
    if code in mapping and mapping[code] in by_code:
        return mapping[code], ""
    if code and code in by_code:
        return code, ""
    name = str(raw_name or "").strip()
    if name and name in by_name:
        candidates = by_name[name]
        # 同名多科目时优先末级（记账只能落末级）
        leaf = [item for item in candidates if item.is_leaf and item.is_active]
        chosen = (leaf or candidates)[0]
        return code, chosen.code
    return code, ""


# ── 路径 A：科目余额表 → 期初 ────────────────────────────────────────────────


async def parse_balance_rows(
    company_id: str,
    rows: list[dict[str, Any]],
    *,
    mapping_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    by_code, by_name = await _account_index(company_id)
    mapping = await load_mapping(company_id)
    if mapping_override:
        # 用户在预览页逐条确认的映射优先于历史映射
        mapping.update({str(key): str(value) for key, value in mapping_override.items() if value})
    valid: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    total_debit = ZERO
    total_credit = ZERO

    for index, row in enumerate(rows):
        excel_row = index + 2  # 第 1 行是表头，错误行号对齐 Excel
        raw_code = str(_pick(row, BALANCE_ALIASES, "accountCode") or "").strip()
        raw_name = str(_pick(row, BALANCE_ALIASES, "accountName") or "").strip()
        debit = _money(_pick(row, BALANCE_ALIASES, "debit"))
        credit = _money(_pick(row, BALANCE_ALIASES, "credit"))
        ytd_debit = _money(_pick(row, BALANCE_ALIASES, "ytdDebit"))
        ytd_credit = _money(_pick(row, BALANCE_ALIASES, "ytdCredit"))
        partner_name = str(_pick(row, BALANCE_ALIASES, "partnerName") or "").strip()

        if not raw_code and not raw_name:
            continue  # 空行跳过
        if debit == ZERO and credit == ZERO and ytd_debit == ZERO and ytd_credit == ZERO:
            continue

        code, suggestion = _resolve_code(raw_code, raw_name, by_code, by_name, mapping)
        if code not in by_code:
            reason = f"科目 {raw_code or raw_name} 不存在于本账套科目表"
            errors.append({"row": excel_row, "reason": reason})
            suggestions.append(
                {
                    "row": excel_row,
                    "sourceCode": raw_code,
                    "sourceName": raw_name,
                    "suggestedCode": suggestion,
                }
            )
            continue
        account = by_code[code]
        if not account.is_leaf:
            errors.append(
                {
                    "row": excel_row,
                    "reason": f"科目 {code}（{account.name}）是容器科目，请改用末级明细",
                }
            )
            continue
        if debit > ZERO and credit > ZERO:
            warnings.append(
                {"row": excel_row, "reason": f"科目 {code} 同时填了借方和贷方期初，按净额处理"}
            )
            net = debit - credit
            debit, credit = (net, ZERO) if net >= ZERO else (ZERO, -net)

        valid.append(
            {
                "row": excel_row,
                "accountCode": code,
                "accountName": account.name,
                "debitBalance": money_text(debit),
                "creditBalance": money_text(credit),
                "ytdDebit": money_text(ytd_debit),
                "ytdCredit": money_text(ytd_credit),
                "partnerName": partner_name,
            }
        )
        total_debit += debit
        total_credit += credit

    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "mapping": mapping,
        "totals": {
            "debit": money_text(total_debit),
            "credit": money_text(total_credit),
            "balanced": total_debit == total_credit,
            "count": len(valid),
        },
        "total": len(rows),
    }


async def confirm_balance_import(
    company_id: str,
    user_id: str,
    items: list[dict[str, Any]],
    *,
    fiscal_year: int,
    step_up_token: str | None,
    start_period: str | None = None,
    mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """包装 save_opening_balances：借贷平衡、年中建账 YTD、step-up 幂等全部沿用既有保障。"""
    if not items:
        raise AccountingError("EMPTY_IMPORT", "没有可导入的余额行", 422)

    # 往来单位自动建档（未命中不阻断导入，保留纯文本）
    from services.auxiliary_service import ensure_partner_by_name

    resolved: list[dict[str, Any]] = []
    for item in items:
        payload = {
            "account_code": str(item.get("accountCode") or item.get("account_code") or "").strip(),
            "account_name": str(item.get("accountName") or item.get("account_name") or "").strip(),
            "debit_balance": money(_money(item.get("debitBalance", item.get("debit_balance")))),
            "credit_balance": money(_money(item.get("creditBalance", item.get("credit_balance")))),
        }
        partner_name = str(item.get("partnerName") or item.get("partner_name") or "").strip()
        if partner_name:
            partner = await ensure_partner_by_name(company_id, user_id, partner_name)
            payload["partner_name"] = partner["name"] if partner else partner_name
            if partner:
                payload["partner_id"] = partner["id"]
        resolved.append(payload)

    ytd_items = [
        {
            "account_code": str(item.get("accountCode") or "").strip(),
            "account_name": str(item.get("accountName") or "").strip(),
            "debit_balance": money(_money(item.get("ytdDebit"))),
            "credit_balance": money(_money(item.get("ytdCredit"))),
        }
        for item in items
        if _money(item.get("ytdDebit")) != ZERO or _money(item.get("ytdCredit")) != ZERO
    ]

    result = await save_opening_balances(
        company_id,
        int(fiscal_year),
        resolved,
        user_id=user_id,
        step_up_token=step_up_token,
        request_id=f"legacy-balance-{uuid4()}",
        start_period=start_period,
        ytd_items=ytd_items or None,
    )
    if mapping:
        await save_mapping(company_id, mapping)
    return result


# ── 路径 B：凭证分录 → 历史凭证 ──────────────────────────────────────────────


async def parse_voucher_rows(
    company_id: str,
    rows: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    mapping_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    by_code, by_name = await _account_index(company_id)
    mapping = await load_mapping(company_id)
    if mapping_override:
        mapping.update({str(key): str(value) for key, value in mapping_override.items() if value})
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []

    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, row in enumerate(rows):
        excel_row = index + 2
        voucher_no = str(_pick(row, VOUCHER_ALIASES, "voucherNo") or "").strip()
        raw_date = _pick(row, VOUCHER_ALIASES, "date")
        summary = str(_pick(row, VOUCHER_ALIASES, "summary") or "").strip()
        raw_code = str(_pick(row, VOUCHER_ALIASES, "accountCode") or "").strip()
        raw_name = str(_pick(row, VOUCHER_ALIASES, "accountName") or "").strip()
        debit = _money(_pick(row, VOUCHER_ALIASES, "debit"))
        credit = _money(_pick(row, VOUCHER_ALIASES, "credit"))

        if not voucher_no and not raw_code:
            continue
        if debit == ZERO and credit == ZERO:
            continue
        if debit > ZERO and credit > ZERO:
            errors.append({"row": excel_row, "reason": "同一行不能同时有借方和贷方金额"})
            continue

        occurred_on = _parse_date(raw_date)
        if occurred_on is None:
            errors.append({"row": excel_row, "reason": f"日期无法解析：{raw_date}"})
            continue
        if start_date and occurred_on.isoformat() < start_date[:10]:
            errors.append(
                {
                    "row": excel_row,
                    "reason": f"日期 {occurred_on.isoformat()} 早于账套启用日期 {start_date[:10]}，"
                    "启用日期前的数据请走「科目余额表导账」",
                }
            )
            continue

        code, suggestion = _resolve_code(raw_code, raw_name, by_code, by_name, mapping)
        if code not in by_code:
            errors.append({"row": excel_row, "reason": f"科目 {raw_code or raw_name} 不存在于本账套"})
            suggestions.append(
                {"row": excel_row, "sourceCode": raw_code, "sourceName": raw_name, "suggestedCode": suggestion}
            )
            continue
        account = by_code[code]
        if not account.is_leaf:
            errors.append({"row": excel_row, "reason": f"科目 {code}（{account.name}）是容器科目"})
            continue

        key = voucher_no or f"{occurred_on.isoformat()}::{summary}"
        if key not in groups:
            groups[key] = {
                "key": key,
                "voucherNo": voucher_no,
                "date": occurred_on.isoformat(),
                "summary": summary or f"旧账导入 {occurred_on.isoformat()}",
                "lines": [],
                "rows": [],
            }
            order.append(key)
        group = groups[key]
        if group["date"] != occurred_on.isoformat() and voucher_no:
            errors.append({"row": excel_row, "reason": f"同一凭证号 {voucher_no} 出现多个日期"})
            continue
        quantity = _optional_number(_pick(row, VOUCHER_ALIASES, "quantity"))
        unit_price = _optional_number(_pick(row, VOUCHER_ALIASES, "unitPrice"))
        group["lines"].append(
            {
                "accountCode": code,
                "accountName": account.name,
                "direction": "debit" if debit > ZERO else "credit",
                "amount": money_text(debit if debit > ZERO else credit),
                "partnerName": str(_pick(row, VOUCHER_ALIASES, "partnerName") or "").strip(),
                "departmentName": str(_pick(row, VOUCHER_ALIASES, "departmentName") or "").strip(),
                "projectName": str(_pick(row, VOUCHER_ALIASES, "projectName") or "").strip(),
                "quantity": _fmt_number(quantity),
                "unitPrice": _fmt_number(unit_price),
                "unit": str(_pick(row, VOUCHER_ALIASES, "unit") or "").strip() or None,
            }
        )
        group["rows"].append(excel_row)

    valid: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        debit_total = sum(
            (money(line["amount"]) for line in group["lines"] if line["direction"] == "debit"), ZERO
        )
        credit_total = sum(
            (money(line["amount"]) for line in group["lines"] if line["direction"] == "credit"), ZERO
        )
        if debit_total != credit_total:
            errors.append(
                {
                    "row": group["rows"][0] if group["rows"] else 0,
                    "reason": f"凭证 {group['voucherNo'] or group['summary']} 借贷不平："
                    f"借 {money_text(debit_total)} 贷 {money_text(credit_total)}",
                }
            )
            continue
        if len(group["lines"]) < 2:
            errors.append(
                {"row": group["rows"][0] if group["rows"] else 0, "reason": "凭证至少需要两条分录"}
            )
            continue
        valid.append(
            {
                **group,
                "debitTotal": money_text(debit_total),
                "creditTotal": money_text(credit_total),
            }
        )

    existing_voucher_nos = await _existing_voucher_nos(company_id, [item["voucherNo"] for item in valid])
    for item in valid:
        if item["voucherNo"] and item["voucherNo"] in existing_voucher_nos:
            warnings.append(
                {"row": item["rows"][0] if item["rows"] else 0, "reason": f"凭证号 {item['voucherNo']} 已存在，导入时将跳过"}
            )

    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "mapping": mapping,
        "totals": {
            "vouchers": len(valid),
            "lines": sum(len(item["lines"]) for item in valid),
            "amount": money_text(sum((money(item["debitTotal"]) for item in valid), ZERO)),
        },
        "total": len(rows),
    }


async def _existing_voucher_nos(company_id: str, voucher_nos: list[str]) -> set[str]:
    wanted = {value for value in voucher_nos if value}
    if not wanted:
        return set()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry.voucher_no).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.voucher_no.in_(list(wanted)),
                )
            )
        ).scalars().all()
    return {str(value) for value in rows if value}


def _auxiliary_for_line(
    line: dict[str, Any],
    partners: dict[str, dict[str, str]],
    departments: dict[str, str],
    projects: dict[str, str],
) -> dict[str, Any]:
    auxiliary: dict[str, Any] = {}
    partner_name = str(line.get("partnerName") or "").strip()
    if partner_name:
        partner = partners.get(partner_name)
        if partner:
            auxiliary.update({"partner_id": partner["id"], "partner_name": partner["name"]})
        else:
            auxiliary["partner_name"] = partner_name
    department_name = str(line.get("departmentName") or "").strip()
    if department_name and department_name in departments:
        auxiliary.update({"department_id": departments[department_name], "department_name": department_name})
    project_name = str(line.get("projectName") or "").strip()
    if project_name and project_name in projects:
        auxiliary.update({"project_id": projects[project_name], "project_name": project_name})
    return auxiliary


async def _auxiliary_lookup(company_id: str, vouchers: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    from services.auxiliary_service import ensure_partner_by_name, list_master

    partner_names = {
        str(line.get("partnerName") or "").strip()
        for voucher in vouchers
        for line in voucher.get("lines", [])
        if str(line.get("partnerName") or "").strip()
    }
    partners: dict[str, dict[str, str]] = {}
    for name in partner_names:
        partner = await ensure_partner_by_name(company_id, "", name)
        if partner:
            partners[name] = partner

    async def _index(kind: str) -> dict[str, str]:
        result = await list_master(company_id, kind, page_size=500)
        return {item["name"]: item["id"] for item in result["items"]}

    return partners, await _index("department"), await _index("project")


async def confirm_voucher_import(
    company_id: str,
    user_id: str,
    vouchers: list[dict[str, Any]],
    *,
    batch_id: str = "",
    post: bool = False,
    mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """批量导入历史凭证（默认 draft 待审）。

    幂等：``dedupFingerprint = legacy:{batch}:{voucherNo}``，重导同批直接跳过；
    换批重导同一张凭证由 voucher_no 唯一约束拦下（提示改号或跳过）。
    """
    if not vouchers:
        raise AccountingError("EMPTY_IMPORT", "没有可导入的凭证", 422)
    batch_id = (batch_id or uuid4().hex[:12]).strip()
    partners, departments, projects = await _auxiliary_lookup(company_id, vouchers)

    fingerprints = {
        f"legacy:{batch_id}:{voucher.get('voucherNo') or voucher.get('key')}" for voucher in vouchers
    }
    async with session_scope() as session:
        existing = {
            str(value)
            for value in (
                await session.execute(
                    select(SourceTransaction.external_key).where(
                        SourceTransaction.company_id == company_id,
                        SourceTransaction.source_type == SOURCE_TYPE,
                        SourceTransaction.external_key.in_(list(fingerprints)),
                    )
                )
            ).scalars().all()
            if value
        }

    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for voucher in vouchers:
        fingerprint = f"legacy:{batch_id}:{voucher.get('voucherNo') or voucher.get('key')}"
        if fingerprint in existing:
            skipped.append({"voucherNo": voucher.get("voucherNo"), "reason": "本批次已导入，跳过"})
            continue
        debit_lines = []
        credit_lines = []
        for line in voucher.get("lines", []):
            payload = {
                "accountCode": line["accountCode"],
                "accountName": line.get("accountName") or line["accountCode"],
                "amount": line["amount"],
            }
            if line.get("quantity") is not None:
                payload["quantity"] = line["quantity"]
            if line.get("unitPrice") is not None:
                payload["unitPrice"] = line["unitPrice"]
            if line.get("unit"):
                payload["unit"] = line["unit"]
            auxiliary = _auxiliary_for_line(line, partners, departments, projects)
            if auxiliary:
                payload["auxiliary"] = auxiliary
            (debit_lines if line["direction"] == "debit" else credit_lines).append(payload)
        entries.append(
            {
                "id": f"legacy-{company_id}-{batch_id}-{voucher.get('voucherNo') or voucher.get('key')}",
                "title": voucher.get("summary") or "旧账导入",
                "amount": voucher.get("debitTotal") or "0",
                "occurDate": voucher.get("date"),
                "direction": "expense",
                "category": "其他支出",
                "source": SOURCE_TYPE,
                "sourceType": SOURCE_TYPE,
                "dedupFingerprint": fingerprint,
                "voucherNo": voucher.get("voucherNo") or "",
                "voucherLines": {"debit": debit_lines, "credit": credit_lines},
            }
        )

    saved = await save_entries_bulk(entries, user_id=user_id, company_id=company_id, trusted_voucher=True)
    posted = 0
    if post and saved:
        async with session_scope() as session:
            for entry in saved:
                await post_journal(
                    session,
                    entry["id"],
                    company_id=company_id,
                    user_id=user_id,
                    request_id=f"legacy-post-{uuid4()}",
                )
                posted += 1

    await _advance_voucher_sequences(company_id, [voucher.get("voucherNo") for voucher in vouchers])
    if mapping:
        await save_mapping(company_id, mapping)
    return {
        "batchId": batch_id,
        "imported": len(saved),
        "posted": posted,
        "skipped": skipped,
        "mapping": mapping or {},
    }


async def _advance_voucher_sequences(company_id: str, voucher_nos: list[str]) -> None:
    """把 voucher_sequences 推进到 max(原号)，避免后续新增凭证与导入号段相撞。"""
    numbers: dict[str, int] = {}
    for value in voucher_nos:
        text = str(value or "")
        if not text:
            continue
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            continue
        # 取尾部连续数字作为序号
        tail = ""
        for ch in reversed(text):
            if ch.isdigit():
                tail = ch + tail
            else:
                break
        if tail:
            numbers[text] = int(tail)
    if not numbers:
        return
    async with session_scope() as session:
        sequences = (
            await session.execute(select(VoucherSequence).where(VoucherSequence.company_id == company_id))
        ).scalars().all()
        by_period = {sequence.period_start: sequence for sequence in sequences}
        if not by_period:
            return
        maximum = max(numbers.values()) + 1
        for sequence in by_period.values():
            if sequence.next_number < maximum:
                sequence.next_number = maximum
        await session.flush()


async def link_opening_partners(company_id: str, user_id: str = "") -> dict[str, Any]:
    """把期初旧格式里的 partner_name 文本匹配到 partners 主数据（一次性、幂等）。

    匹配上补 partner_id；匹配不上保留纯文本——不强制建档，避免把
    "某笔说不清的往来"变成一张假档案。
    """
    from services.auxiliary_service import ensure_partner_by_name

    async with session_scope() as session:
        entries = (
            await session.execute(
                select(JournalEntry).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.entry_type == "opening",
                )
            )
        ).scalars().all()
        entry_ids = [entry.id for entry in entries]
        if not entry_ids:
            return {"scanned": 0, "linked": 0}
        from db.models import JournalLine

        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id.in_(entry_ids))
            )
        ).scalars().all()

    scanned = 0
    linked = 0
    for line in lines:
        auxiliary = dict(line.auxiliary or {})
        if auxiliary.get("partner_id") or not auxiliary.get("partner_name"):
            continue
        scanned += 1
        partner = await ensure_partner_by_name(company_id, user_id, str(auxiliary["partner_name"]))
        if not partner:
            continue
        auxiliary.update({"partner_id": partner["id"], "partner_name": partner["name"]})
        async with session_scope() as session:
            row = await session.get(JournalLine, line.id)
            if row is not None:
                row.auxiliary = auxiliary
                await session.flush()
        linked += 1
    return {"scanned": scanned, "linked": linked}
