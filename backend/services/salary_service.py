# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Employee Salary and Social Security database service."""
from __future__ import annotations

import os
import json
import calendar
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, List, Optional

from services.database import get_conn
from services.json_utils import json_dumps
from services.entry_service import save_entry
from services.llm import _uid
from core.money import CENT, ZERO, money, money_text, rate

SALARY_MONEY_FIELDS = {
    "base_salary", "social_security_base", "bonus", "allowance", "deduction",
    "personal_social_security", "personal_tax", "company_social_security",
    "net_salary", "total_cost", "special_additional_deduction",
}

# 凭证来源指纹：计提/发放/缴社保/缴个税各一个，崩溃重试时凭指纹找回已建凭证，
# 避免"凭证已入账但工资表状态未更新"场景下重复入账（两段式写入的恢复机制）。
SOURCE_ACCRUAL = "salary_accrual"
SOURCE_PAY = "salary_pay"
SOURCE_SS = "salary_social"
SOURCE_HF = "salary_housing_fund"
SOURCE_TAX = "salary_iit"

ACTIVE_VOUCHER_STATUSES = {"posted", "pending_review"}

# ---------------------------------------------------------------------------
# 工资分录科目（与 data/chart_of_accounts.json 保持一致）
#
# 凭证闭环：G=应发工资, P=个人社保公积金, T=个人个税, C=单位社保公积金, net=G-P-T
#   计提  借 5602001/5602016/430100101  贷 221101/221102/221104/2221012
#   发放  借 221101                    贷 1002
#   缴社保 借 221102 + 221104          贷 1002
#   缴公积金 借 221102 + 221104        贷 1002
#   缴个税 借 2221012                  贷 1002
# 个人代扣项是负债而非公司债权，使用 221104 应付职工薪酬-代扣社保公积金。
# ---------------------------------------------------------------------------
ACCT_ADMIN_SALARY = ("5602001", "管理人员职工薪酬")
ACCT_ADMIN_SOCIAL = ("5602016", "管理费用-单位社保")
ACCT_RD_PERSONNEL = ("430100101", "研发支出-人员人工")
ACCT_PAYROLL_SALARY = ("221101", "应付职工薪酬-工资")
ACCT_PAYROLL_COMPANY_SS = ("221102", "应付职工薪酬-单位社保")
ACCT_SS_WITHHELD = ("221104", "应付职工薪酬-代扣社保公积金")
ACCT_IIT_PAYABLE = ("2221012", "应交税费-应交个人所得税")
ACCT_BANK = ("1002", "银行存款")


def _line(account: tuple[str, str], amount: Decimal, *, rd: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """构造一条凭证分录行。金额向上取整到分，避免四舍五入尾差。"""
    line: dict[str, Any] = {
        "accountCode": account[0],
        "accountName": account[1],
        "amount": money(amount),
    }
    if rd:
        line["rdDetail"] = rd
    return line


def _split_pool(pool: Decimal, weights: List[Decimal]) -> List[Decimal]:
    """把一笔金额按权重拆成整数分，保证拆分结果合计精确等于 pool。

    逐项独立四舍五入会溢出：5000.00 × 33.3333% × 3 = 5000.01，多出的 1 分会
    把管理费用摊销压成负数、被 ``> ZERO`` 丢弃，最终借贷不平、整月无法计提。
    这里用最大余数法（Hamilton）：先按精确比例取整分，余数按小数部分从大到小
    （并列时索引小的优先）逐分补足，Σshares ≡ pool 恒成立。
    """
    total_cents = int((money(pool) / CENT).to_integral_value())
    if total_cents <= 0 or not weights:
        return [ZERO for _ in weights]
    fraction_weights = [Fraction(str(weight)) for weight in weights]
    total_weight = sum(fraction_weights, Fraction(0))
    if total_weight <= 0:
        return [ZERO for _ in weights]
    exact = [Fraction(total_cents) * weight / total_weight for weight in fraction_weights]
    shares = [int(value) for value in exact]
    remainder = total_cents - sum(shares)
    for index in sorted(
        range(len(weights)),
        key=lambda i: (-(exact[i] - shares[i]), i),
    )[:remainder]:
        shares[index] += 1
    return [Decimal(share).scaleb(-2).quantize(CENT) for share in shares]


def _fingerprint(kind: str, sheet_id: str, generation: int = 0) -> str:
    base = f"{kind}-{sheet_id}"
    return base if generation <= 0 else f"{base}-g{generation}"


async def _find_entry_by_source(company_id: str, source_type: str, external_key: str) -> Optional[Dict[str, Any]]:
    """按来源指纹找回已存在的凭证（id + status）。"""
    from sqlalchemy import select

    from db.models import JournalEntry, SourceTransaction
    from db.session import session_scope

    async with session_scope() as session:
        source_id = (
            await session.execute(
                select(SourceTransaction.id).where(
                    SourceTransaction.company_id == company_id,
                    SourceTransaction.source_type == source_type,
                    SourceTransaction.external_key == external_key,
                )
            )
        ).scalar_one_or_none()
        if not source_id:
            return None
        row = (
            await session.execute(
                select(JournalEntry.id, JournalEntry.status).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.source_transaction_id == source_id,
                )
            )
        ).first()
        if not row:
            return None
        return {"id": str(row[0]), "status": str(row[1])}


async def _entry_status(company_id: str, entry_id: Optional[str]) -> Optional[str]:
    if not entry_id:
        return None
    from sqlalchemy import select

    from db.models import JournalEntry
    from db.session import session_scope

    async with session_scope() as session:
        return (
            await session.execute(
                select(JournalEntry.status).where(
                    JournalEntry.company_id == company_id, JournalEntry.id == entry_id
                )
            )
        ).scalar_one_or_none()


async def _entry_statuses(company_id: str, entry_ids: list[Optional[str]]) -> dict[str, str]:
    ids = {str(entry_id) for entry_id in entry_ids if entry_id}
    if not ids:
        return {}
    from sqlalchemy import select

    from db.models import JournalEntry
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry.id, JournalEntry.status).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.id.in_(ids),
                )
            )
        ).all()
    return {str(row[0]): str(row[1]) for row in rows}


def _generation(sheet: Dict[str, Any], column: str) -> int:
    try:
        return max(int(sheet.get(column) or 0), 0)
    except (TypeError, ValueError):
        return 0


async def _bump_generation(conn, sheet: Dict[str, Any], column: str) -> None:
    allowed = {
        "accrual_generation", "payment_generation", "ss_generation",
        "tax_generation", "hf_generation",
    }
    if column not in allowed:
        raise ValueError("invalid salary generation column")
    next_value = _generation(sheet, column) + 1
    await conn.execute(
        f"UPDATE salary_sheets SET {column} = ? WHERE id = ?",
        (next_value, sheet["id"]),
    )
    sheet[column] = next_value


def _housing_fund_amounts(base: Any, rates: Optional[dict[str, Decimal]] = None) -> tuple[Decimal, Decimal]:
    """按账套费率算 (个人公积金, 单位公积金)。"""
    rates = rates or default_payroll_rates()
    base_amount = _clamp_ss_base(money(base, allow_negative=False))
    return (
        money(base_amount * rates["housingPersonal"]),
        money(base_amount * rates["housingCompany"]),
    )


# ---------------------------------------------------------------------------
# 账套级社保公积金费率
#
# config 里的 SOCIAL_*/HF_* 是全局 env，只能表达一套口径；但公积金比例随城市和
# 单位而定（杭州 5%~12%，常见 8%/12%）。若费率与实际不符，代扣合计拆分出来的
# 「缴公积金」凭证会少算、「缴社保」凭证会多算，与银行扣款对不上。
# 因此费率优先取账套档案 payload.payrollRates，缺省才回落 config。
# ---------------------------------------------------------------------------
PAYROLL_RATE_KEYS = ("socialPersonal", "socialCompany", "housingPersonal", "housingCompany")


def default_payroll_rates() -> dict[str, Decimal]:
    from config import (
        HF_COMPANY_RATE,
        HF_PERSONAL_RATE,
        SOCIAL_COMPANY_RATE,
        SOCIAL_PERSONAL_RATE,
    )

    return {
        "socialPersonal": rate(str(SOCIAL_PERSONAL_RATE)),
        "socialCompany": rate(str(SOCIAL_COMPANY_RATE)),
        "housingPersonal": rate(str(HF_PERSONAL_RATE)),
        "housingCompany": rate(str(HF_COMPANY_RATE)),
    }


def _merge_payroll_rates(raw: Any) -> dict[str, Decimal]:
    """把账套档案里的费率覆盖到默认口径上；非法值静默忽略（保留默认）。"""
    merged = default_payroll_rates()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        return merged
    for key in PAYROLL_RATE_KEYS:
        if raw.get(key) is None:
            continue
        try:
            value = rate(str(raw[key]))
        except ValueError:
            continue
        # 比例必须落在 [0, 1]；0 表示该险种不缴（如无公积金）
        if ZERO <= value <= Decimal("1"):
            merged[key] = value
    return merged


async def load_payroll_rates(conn, company_id: str) -> dict[str, Decimal]:
    """读账套级社保公积金费率，缺省回落 config（杭州口径）。"""
    cursor = await conn.execute(
        "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return default_payroll_rates()
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, ValueError):
            payload = {}
    if not isinstance(payload, dict):
        return default_payroll_rates()
    return _merge_payroll_rates(payload.get("payrollRates"))


async def get_payroll_rates(company_id: str) -> dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        rates = await load_payroll_rates(conn, company_id)
    finally:
        await conn.close()
    defaults = default_payroll_rates()
    return {
        "rates": {key: str(value) for key, value in rates.items()},
        "defaults": {key: str(value) for key, value in defaults.items()},
        # 账套是否已自定义过费率：前端据此提示「按默认口径估算」
        "isCustomized": rates != defaults,
    }


async def save_payroll_rates(company_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """保存账套级费率；只接受 PAYROLL_RATE_KEYS 内的键。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError("账套不存在")
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload or "{}")
            except (TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        current = _merge_payroll_rates(payload.get("payrollRates"))
        merged = dict(current)
        for key in PAYROLL_RATE_KEYS:
            if updates.get(key) is None:
                continue
            try:
                value = rate(str(updates[key]))
            except ValueError as exc:
                raise ValueError("费率格式无效，请填写 0~1 之间的小数") from exc
            if not (ZERO <= value <= Decimal("1")):
                raise ValueError("费率必须落在 0~1 之间（如 8% 填 0.08）")
            merged[key] = value

        payload["payrollRates"] = {key: str(value) for key, value in merged.items()}
        payload["updated_at"] = _now_iso()
        await conn.execute(
            "UPDATE auth_companies SET payload = ?, updated_at = ? WHERE company_id = ?",
            (json_dumps(payload), payload["updated_at"], company_id),
        )
        await conn.commit()
    finally:
        await conn.close()
    return await get_payroll_rates(company_id)


async def recalculate_draft_withholdings(company_id: str, month: str) -> Dict[str, Any]:
    """按账套费率重算某月草稿工资表的社保公积金两列并回写实发/成本。

    改了比例但工资表里存的还是旧数时，「缴公积金」凭证金额仍然是错的。
    已计提/已发放的月份一律不动（凭证已入账，要改必须先冲销计提）。
    """
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError("该月工资表不存在")
        sheet = await _sync_sheet_lifecycle(conn, dict(row))
        if str(sheet.get("status") or "draft") != "draft":
            raise ValueError("已计提的工资表不能按新比例重算，请先冲销计提凭证")

        rates = await load_payroll_rates(conn, company_id)
        cursor = await conn.execute(
            "SELECT sr.id, sr.base_salary, sr.bonus, sr.allowance, sr.deduction, "
            "sr.personal_tax, e.social_security_base "
            "FROM salary_records sr JOIN employees e ON e.id = sr.employee_id "
            "WHERE sr.sheet_id = ?",
            (sheet["id"],),
        )
        records = [dict(r) for r in await cursor.fetchall()]
        now = _now_iso()
        for record in records:
            pers_ss, comp_ss = calculate_initial_social_security(
                record["social_security_base"], rates
            )
            gross = money(
                money(record["base_salary"])
                + money(record["bonus"])
                + money(record["allowance"])
                - money(record["deduction"])
            )
            net = money(gross - pers_ss - money(record["personal_tax"]))
            cost = money(gross + comp_ss)
            await conn.execute(
                "UPDATE salary_records SET personal_social_security = ?, "
                "company_social_security = ?, net_salary = ?, total_cost = ?, updated_at = ? "
                "WHERE id = ? AND sheet_id = ?",
                (pers_ss, comp_ss, net, cost, now, record["id"], sheet["id"]),
            )
        # 清掉旧的公积金拆分缓存，下次读取按新费率重新拆
        await conn.execute(
            "UPDATE salary_sheets SET personal_housing_fund_total = 0.0, "
            "company_housing_fund_total = 0.0, updated_at = ? WHERE id = ?",
            (now, sheet["id"]),
        )
        await conn.commit()
    finally:
        await conn.close()
    return await get_or_create_salary_sheet(company_id, month)



def _expected_withholding(
    base: Decimal, rates: dict[str, Decimal], *, company: bool = False
) -> tuple[Decimal, Decimal]:
    """按账套费率算 (社保, 公积金) 期望额，用于把合并列拆回社保与公积金。"""
    if company:
        return (
            money(base * rates["socialCompany"]),
            money(base * rates["housingCompany"]),
        )
    return (
        money(base * rates["socialPersonal"]),
        money(base * rates["housingPersonal"]),
    )


def _split_withheld(total: Decimal, ss_expected: Decimal, hf_expected: Decimal) -> tuple[Decimal, Decimal]:
    """把「社保公积金」合计拆成 (社保, 公积金)。

    界面只有一列合计数（历史设计），拆回两项才能分别生成缴社保 / 缴公积金凭证。
    旧实现取 ``min(公积金期望, 合计)``：当录入合计小于公积金期望时会把社保压成 0，
    且费率填错（例如实际 12%、配置 8%）时会整体偏到社保侧。这里改为按期望比例
    缩放分摊，保证「社保 + 公积金 ≡ 合计」恒成立，费率正确时即精确等于实际。
    """
    total = money(total)
    if total <= ZERO:
        return ZERO, ZERO
    ss_expected = money(ss_expected, allow_negative=False)
    hf_expected = money(hf_expected, allow_negative=False)
    expected = ss_expected + hf_expected
    if hf_expected <= ZERO or expected <= ZERO:
        return total, ZERO
    if ss_expected <= ZERO:
        return ZERO, total
    hf_part = money(total * hf_expected / expected)
    if hf_part > total:
        hf_part = total
    return money(total - hf_part), hf_part


async def _housing_fund_totals(
    conn, sheet: Dict[str, Any], records: list[Dict[str, Any]]
) -> tuple[Decimal, Decimal]:
    personal_total = money(sum((money(r["personal_social_security"]) for r in records), ZERO))
    company_total = money(sum((money(r["company_social_security"]) for r in records), ZERO))
    stored_personal = money(sheet.get("personal_housing_fund_total") or ZERO)
    stored_company = money(sheet.get("company_housing_fund_total") or ZERO)
    if stored_personal > ZERO or stored_company > ZERO:
        return min(stored_personal, personal_total), min(stored_company, company_total)
    if personal_total <= ZERO and company_total <= ZERO:
        return ZERO, ZERO

    rates = await load_payroll_rates(conn, str(sheet["company_id"]))
    employee_ids = sorted({str(r["employee_id"]) for r in records})
    bases: dict[str, Any] = {}
    if employee_ids:
        placeholders = ", ".join("?" for _ in employee_ids)
        rows = await (
            await conn.execute(
                f"SELECT id, social_security_base FROM employees WHERE company_id = ? AND id IN ({placeholders})",
                [sheet["company_id"], *employee_ids],
            )
        ).fetchall()
        bases = {str(row["id"]): row["social_security_base"] for row in rows}

    personal_hf = ZERO
    company_hf = ZERO
    for record in records:
        base = _clamp_ss_base(money(bases.get(str(record["employee_id"]), ZERO), allow_negative=False))
        ss_personal, hf_personal = _expected_withholding(base, rates)
        _, personal_part = _split_withheld(
            money(record["personal_social_security"]), ss_personal, hf_personal
        )
        personal_hf = money(personal_hf + personal_part)

        ss_company, hf_company = _expected_withholding(base, rates, company=True)
        _, company_part = _split_withheld(
            money(record["company_social_security"]), ss_company, hf_company
        )
        company_hf = money(company_hf + company_part)

    if personal_hf > ZERO or company_hf > ZERO:
        await conn.execute(
            "UPDATE salary_sheets SET personal_housing_fund_total = ?, company_housing_fund_total = ? WHERE id = ?",
            (personal_hf, company_hf, sheet["id"]),
        )
        sheet["personal_housing_fund_total"] = personal_hf
        sheet["company_housing_fund_total"] = company_hf
    return personal_hf, company_hf


async def _sync_sheet_lifecycle(conn, sheet: Dict[str, Any]) -> Dict[str, Any]:
    """按凭证真实状态修复工资表，而不是相信可能过期的 status 字段。

    删除草稿、冲销凭证、驳回/删除审核单都会让凭证脱离 posted/pending_review。
    此时清空对应凭证 ID、推进 generation，让下次计提/发放使用新指纹，避免丢失后
    永远无法重做；同时把工资表退回可操作状态。
    """
    company_id = str(sheet["company_id"])
    id_columns = {
        "accrual": "accrual_voucher_id",
        "payment": "payment_voucher_id",
        "ss": "ss_paid_voucher_id",
        "hf": "hf_paid_voucher_id",
        "tax": "tax_paid_voucher_id",
    }
    generation_columns = {
        "accrual": "accrual_generation",
        "payment": "payment_generation",
        "ss": "ss_generation",
        "hf": "hf_generation",
        "tax": "tax_generation",
    }
    statuses = await _entry_statuses(
        company_id, [sheet.get(column) for column in id_columns.values()]
    )
    status_of = {
        key: statuses.get(str(sheet.get(column))) if sheet.get(column) else None
        for key, column in id_columns.items()
    }

    updates: dict[str, Any] = {}
    for key, column in id_columns.items():
        if sheet.get(column) and status_of[key] not in ACTIVE_VOUCHER_STATUSES:
            await _bump_generation(conn, sheet, generation_columns[key])
            updates[column] = None
            sheet[column] = None
            status_of[key] = None

    current = str(sheet.get("status") or "draft")
    accrual_status = status_of["accrual"]
    payment_status = status_of["payment"]
    if accrual_status == "posted":
        current = "paid" if payment_status == "posted" else "accrued"
    elif accrual_status == "pending_review":
        current = "pending_review"
    else:
        current = "draft"

    if current != str(sheet.get("status") or "draft"):
        updates["status"] = current
        sheet["status"] = current
    if updates:
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        await conn.execute(
            f"UPDATE salary_sheets SET {set_clause} WHERE id = ?",
            [*updates.values(), sheet["id"]],
        )
        await conn.commit()
    sheet["voucherStatuses"] = {
        lookup_id: status_of[key]
        for key, lookup_id in (
            (key, str(sheet.get(column)) if sheet.get(column) else None)
            for key, column in id_columns.items()
        )
        if lookup_id and status_of[key]
    }
    return sheet


async def _enrich_salary_sheet(conn, sheet: Dict[str, Any]) -> Dict[str, Any]:
    records = list(sheet.get("records") or [])
    statuses = sheet.get("voucherStatuses") or await _entry_statuses(
        str(sheet["company_id"]),
        [
            sheet.get("accrual_voucher_id"),
            sheet.get("payment_voucher_id"),
            sheet.get("ss_paid_voucher_id"),
            sheet.get("hf_paid_voucher_id"),
            sheet.get("tax_paid_voucher_id"),
        ],
    )
    lookup = {
        "accrual": str(sheet.get("accrual_voucher_id") or ""),
        "payment": str(sheet.get("payment_voucher_id") or ""),
        "ss": str(sheet.get("ss_paid_voucher_id") or ""),
        "hf": str(sheet.get("hf_paid_voucher_id") or ""),
        "tax": str(sheet.get("tax_paid_voucher_id") or ""),
    }
    vault_status = {key: statuses.get(entry_id) if entry_id else None for key, entry_id in lookup.items()}
    sheet["accrualVoucherStatus"] = vault_status["accrual"]
    sheet["paymentVoucherStatus"] = vault_status["payment"]

    personal_total = money(sum((money(r["personal_social_security"]) for r in records), ZERO))
    company_total = money(sum((money(r["company_social_security"]) for r in records), ZERO))
    personal_hf, company_hf = await _housing_fund_totals(conn, sheet, records)
    personal_social = money(personal_total - personal_hf)
    company_social = money(company_total - company_hf)
    tax_total = money(sum((money(r["personal_tax"]) for r in records), ZERO))
    if (
        sheet.get("ss_payment_scope") != "social"
        and vault_status["ss"] == "posted"
        and not vault_status["hf"]
    ):
        # 旧版本把社保和公积金合并为一笔缴纳，历史凭证已覆盖公积金，不能再要求补缴。
        vault_status["hf"] = "posted"

    def payment_state(due: Decimal, entry_status: Optional[str]) -> str:
        if due <= ZERO:
            return "posted" if entry_status == "posted" else "not_required"
        return entry_status or "unpaid"

    sheet["socialSecurityDue"] = money_text(money(company_social + personal_social))
    sheet["housingFundDue"] = money_text(money(company_hf + personal_hf))
    sheet["taxDue"] = money_text(tax_total)
    sheet["ssPaymentStatus"] = payment_state(
        money(company_social + personal_social), vault_status["ss"]
    )
    sheet["hfPaymentStatus"] = payment_state(
        money(company_hf + personal_hf), vault_status["hf"]
    )
    sheet["taxPaymentStatus"] = payment_state(tax_total, vault_status["tax"])
    if vault_status["payment"] == "posted":
        sheet["paymentStatus"] = "posted"
    elif vault_status["payment"] == "pending_review":
        sheet["paymentStatus"] = "pending_review"
    else:
        sheet["paymentStatus"] = "unpaid"
    return sheet


def _serialize_money_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: money_text(value) if key in SALARY_MONEY_FIELDS else value
        for key, value in row.items()
    }

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_hire_date(value: Any) -> Optional[str]:
    """入职日期统一存为 YYYY-MM-DD 文本（与库内其他日期列一致）；空值返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("入职日期格式应为 YYYY-MM-DD") from exc


def _hire_month(employee: Dict[str, Any]) -> Optional[str]:
    text = str(employee.get("hire_date") or "").strip()
    return text[:7] if len(text) >= 7 else None


def _employed_in_month(employee: Dict[str, Any], month: str) -> bool:
    """入职日期为空视为不限制（兼容存量员工）；否则只从入职当月起计入工资表。"""
    hire_month = _hire_month(employee)
    return hire_month is None or hire_month <= month

async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        # 1. Employees table
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS employees ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  department TEXT NOT NULL,"
            "  base_salary NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  social_security_base NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  is_owner INTEGER NOT NULL DEFAULT 0,"
            "  hire_date TEXT,"
            "  is_active INTEGER NOT NULL DEFAULT 1,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        # 2. Salary sheets table
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS salary_sheets ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  month TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'draft',"
            "  accrual_voucher_id TEXT,"
            "  payment_voucher_id TEXT,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  UNIQUE(company_id, month)"
            ")"
        )
        # 3. Salary records table
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS salary_records ("
            "  id TEXT PRIMARY KEY,"
            "  sheet_id TEXT NOT NULL,"
            "  employee_id TEXT NOT NULL,"
            "  employee_name TEXT NOT NULL,"
            "  base_salary NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  bonus NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  allowance NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  deduction NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  personal_social_security NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  personal_tax NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  company_social_security NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  net_salary NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  total_cost NUMERIC(20,2) NOT NULL DEFAULT 0.0,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_company ON employees(company_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_records_sheet ON salary_records(sheet_id)")
        # 增量列：社保/个税缴纳凭证（2026-09-30 补丁）；业主标记（经营所得 B 表
        # 业主工资自动识别，2026-10-18 补丁）。对已有库幂等。
        for table, column, ddl in (
            ("salary_sheets", "ss_paid_voucher_id", "TEXT"),
            ("salary_sheets", "tax_paid_voucher_id", "TEXT"),
            ("salary_sheets", "hf_paid_voucher_id", "TEXT"),
            ("salary_sheets", "ss_payment_scope", "TEXT NOT NULL DEFAULT 'combined'"),
            ("salary_sheets", "accrual_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("salary_sheets", "payment_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("salary_sheets", "ss_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("salary_sheets", "tax_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("salary_sheets", "hf_generation", "INTEGER NOT NULL DEFAULT 0"),
            ("salary_sheets", "personal_housing_fund_total", "NUMERIC(20,2) NOT NULL DEFAULT 0.0"),
            ("salary_sheets", "company_housing_fund_total", "NUMERIC(20,2) NOT NULL DEFAULT 0.0"),
            ("employees", "is_owner", "INTEGER NOT NULL DEFAULT 0"),
            ("employees", "hire_date", "TEXT"),
            # 月度专项附加扣除合计（子女教育/继续教育/房贷/租金/赡养老人/婴幼儿照护）：
            # 个税累计预扣试算的输入，2026-10-23 补丁
            ("employees", "special_additional_deduction", "NUMERIC(20,2) NOT NULL DEFAULT 0.0"),
        ):
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except Exception as exc:
                message = str(exc).lower()
                if "duplicate column" in message or "already exists" in message:
                    continue
                raise
        await conn.commit()
    finally:
        await conn.close()

async def _ensure_db_ready() -> None:
    await _ensure_schema()

# ---------------------------------------------------------------------------
# 账套级部门字典
#
# 部门不只是标签：它决定工资借方走研发支出（430100101）还是管理费用，也决定
# 该员工是否必须登记研发项目工时。原先只有前端写死的三个选项
# （研发部/销售部/行政部）+ config.RD_DEPARTMENTS（默认只有"研发部"），
# 历史数据里的「总经办」「技术部」在界面上既选不出来、也无法标成研发部门。
# 这里把部门做成账套档案字典：payload.departments = [{"name": ..., "isRd": bool}]。
# ---------------------------------------------------------------------------
DEFAULT_DEPARTMENTS: tuple[dict[str, Any], ...] = (
    {"name": "研发部", "isRd": True},
    {"name": "销售部", "isRd": False},
    {"name": "行政部", "isRd": False},
)


def _default_departments() -> list[dict[str, Any]]:
    """默认三部门 + config.RD_DEPARTMENTS 里额外配置的研发部门。"""
    from config import RD_DEPARTMENTS

    out = [dict(item) for item in DEFAULT_DEPARTMENTS]
    seen = {item["name"] for item in out}
    for name in RD_DEPARTMENTS:
        if name not in seen:
            out.append({"name": name, "isRd": True})
            seen.add(name)
    return out


def _guess_is_rd(name: str) -> bool:
    """新部门是否按研发口径处理：只看 config 里显式配置的名单，不猜名字。"""
    from config import RD_DEPARTMENTS

    return name in RD_DEPARTMENTS


def _normalize_departments(raw: Any) -> list[dict[str, Any]]:
    """解析部门字典：支持 ["研发部"] 与 [{"name","isRd"}] 两种形态，名称去重。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name, is_rd = item, False
        elif isinstance(item, dict):
            name, is_rd = item.get("name"), bool(item.get("isRd"))
        else:
            continue
        name = str(name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "isRd": is_rd})
    return out


async def _load_company_payload(conn, company_id: str) -> Optional[Dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT payload FROM auth_companies WHERE company_id = ?", (company_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, ValueError):
            payload = {}
    return payload if isinstance(payload, dict) else {}


async def _write_company_payload(conn, company_id: str, payload: Dict[str, Any]) -> None:
    payload["updated_at"] = _now_iso()
    await conn.execute(
        "UPDATE auth_companies SET payload = ?, updated_at = ? WHERE company_id = ?",
        (json_dumps(payload), payload["updated_at"], company_id),
    )


async def load_departments(conn, company_id: str) -> list[dict[str, Any]]:
    """读账套部门字典；未配置时返回默认三部门（含 env 里额外的研发部门）。"""
    payload = await _load_company_payload(conn, company_id)
    if payload is None:
        return _default_departments()
    stored = _normalize_departments(payload.get("departments"))
    return stored or _default_departments()


async def get_departments(company_id: str) -> Dict[str, Any]:
    """部门列表 + 使用情况。员工在用的部门即使没登记也要出现在选项里。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        payload = await _load_company_payload(conn, company_id)
        stored = _normalize_departments((payload or {}).get("departments"))
        departments = stored or _default_departments()
        cursor = await conn.execute(
            "SELECT department, COUNT(*) AS headcount FROM employees "
            "WHERE company_id = ? AND is_active = 1 GROUP BY department",
            (company_id,),
        )
        usage = {
            str(row["department"] or "").strip(): int(row["headcount"])
            for row in await cursor.fetchall()
        }
    finally:
        await conn.close()

    known = {item["name"] for item in departments}
    options = list(departments)
    unregistered = []
    for name, headcount in sorted(usage.items()):
        if not name or name in known:
            continue
        # 名册里已有但没登记的部门（历史数据），按 config 推断研发属性后并入选项
        options.append({"name": name, "isRd": _guess_is_rd(name), "implicit": True})
        unregistered.append({"name": name, "headcount": headcount})

    return {
        "departments": [
            {**item, "headcount": usage.get(item["name"], 0)} for item in options
        ],
        "unregistered": unregistered,
        "customized": bool(stored),
    }


async def save_departments(company_id: str, items: Any) -> Dict[str, Any]:
    """保存部门字典。仍被在职员工使用的部门不允许移除，避免工资表出现游离部门。"""
    await _ensure_db_ready()
    departments = _normalize_departments(items)
    if not departments:
        raise ValueError("至少保留一个部门")

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT department, COUNT(*) AS headcount FROM employees "
            "WHERE company_id = ? AND is_active = 1 GROUP BY department",
            (company_id,),
        )
        usage = {
            str(row["department"] or "").strip(): int(row["headcount"])
            for row in await cursor.fetchall()
        }
        kept = {item["name"] for item in departments}
        blocked = [f"{name}（{count} 人）" for name, count in sorted(usage.items()) if name and name not in kept]
        if blocked:
            raise ValueError(
                "以下部门还有在职员工，请先调整员工部门再删除：" + "、".join(blocked)
            )

        payload = await _load_company_payload(conn, company_id)
        if payload is None:
            raise ValueError("账套不存在")
        payload["departments"] = departments
        await _write_company_payload(conn, company_id, payload)
        await conn.commit()
    finally:
        await conn.close()
    return await get_departments(company_id)


async def _ensure_department_registered(conn, company_id: str, department: str) -> None:
    """员工建档或改部门时，把新部门自动登记进账套字典。"""
    name = str(department or "").strip()
    if not name:
        return
    departments = await load_departments(conn, company_id)
    if any(item["name"] == name for item in departments):
        return
    departments.append({"name": name, "isRd": _guess_is_rd(name)})
    payload = await _load_company_payload(conn, company_id)
    if payload is None:
        return
    payload["departments"] = departments
    await _write_company_payload(conn, company_id, payload)


# --- Employee Management ---

async def list_employees(company_id: str) -> List[Dict[str, Any]]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM employees WHERE company_id = ? AND is_active = 1 ORDER BY department ASC, name ASC",
            (company_id,),
        )
        rows = await cursor.fetchall()
        return [_serialize_money_fields(dict(r)) for r in rows]
    finally:
        await conn.close()

async def create_employee(company_id: str, name: str, department: str,
                          base_salary: Any, social_security_base: Any,
                          is_owner: bool = False,
                          hire_date: Any = None,
                          special_additional_deduction: Any = ZERO) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        eid = f"emp-{_uid()}"
        now = _now_iso()
        base_salary = money(base_salary, allow_negative=False)
        social_security_base = money(social_security_base, allow_negative=False)
        hire_date = _normalize_hire_date(hire_date)
        special = money(special_additional_deduction, allow_negative=False)
        # 新部门直接落进账套字典，用户不必先去「部门设置」登记
        await _ensure_department_registered(conn, company_id, department)
        await conn.execute(
            "INSERT INTO employees (id, company_id, name, department, base_salary, social_security_base, is_owner, hire_date, special_additional_deduction, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (eid, company_id, name, department, base_salary, social_security_base, 1 if is_owner else 0, hire_date, special, now, now),
        )
        await conn.commit()
        return {
            "id": eid, "company_id": company_id, "name": name, "department": department,
            "base_salary": money_text(base_salary), "social_security_base": money_text(social_security_base),
            "is_owner": 1 if is_owner else 0, "hire_date": hire_date,
            "special_additional_deduction": money_text(special),
            "is_active": 1, "created_at": now, "updated_at": now
        }
    finally:
        await conn.close()

async def update_employee(company_id: str, employee_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        fields = []
        values = []
        for key in (
            "name", "department", "base_salary", "social_security_base",
            "is_active", "is_owner", "hire_date", "special_additional_deduction",
        ):
            if key in updates:
                fields.append(f"{key} = ?")
                if key in {"base_salary", "social_security_base", "special_additional_deduction"}:
                    values.append(money(updates[key], allow_negative=False))
                elif key == "hire_date":
                    values.append(_normalize_hire_date(updates[key]))
                else:
                    values.append(updates[key])
        if not fields:
            return None
        if "department" in updates:
            # 改到新部门时同样自动登记，避免工资表出现字典外的部门
            await _ensure_department_registered(conn, company_id, str(updates["department"]))
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.extend([company_id, employee_id])
        
        await conn.execute(
            f"UPDATE employees SET {', '.join(fields)} WHERE company_id = ? AND id = ?",
            values,
        )
        await conn.commit()
        
        cursor = await conn.execute("SELECT * FROM employees WHERE company_id = ? AND id = ?", (company_id, employee_id))
        row = await cursor.fetchone()
        return _serialize_money_fields(dict(row)) if row else None
    finally:
        await conn.close()

async def archive_employee(company_id: str, employee_id: str) -> bool:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        now = _now_iso()
        cursor = await conn.execute(
            "UPDATE employees SET is_active = 0, updated_at = ? WHERE company_id = ? AND id = ?",
            (now, company_id, employee_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def sum_owner_salary_cost(company_id: str, fiscal_year: int) -> Decimal:
    """业主/投资者本人年度工资薪金（应发口径 = 基本工资 + 奖金 + 津贴）。

    经营所得 B 表"业主工资不得税前扣除"调增的自动预填来源：名册里标记
    is_owner 的员工的年度工资记录合计。无业主标记或无记录时返回 0。
    注意这是"计入费用的应发工资"，个人社保与个税是代扣项，不属于调增口径。
    """
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(sr.base_salary + sr.bonus + sr.allowance - sr.deduction), 0) AS total "
            "FROM salary_records sr "
            "JOIN employees e ON e.id = sr.employee_id "
            "JOIN salary_sheets ss ON ss.id = sr.sheet_id "
            "WHERE e.company_id = ? AND e.is_owner = 1 AND ss.month LIKE ? "
            "AND ss.status IN ('accrued', 'paid')",
            (company_id, f"{fiscal_year}-%"),
        )
        row = await cursor.fetchone()
        return money(row["total"] if row else 0)
    finally:
        await conn.close()

# --- Salary Sheets & Records ---

def _clamp_ss_base(base_amount: Decimal) -> Decimal:
    """社保公积金缴费基数限幅（当地社平的 60%-300%，由配置决定；0 表示不限制）。"""
    from config import SOCIAL_SECURITY_BASE_MAX, SOCIAL_SECURITY_BASE_MIN

    if SOCIAL_SECURITY_BASE_MIN > 0 and base_amount < money(str(SOCIAL_SECURITY_BASE_MIN)):
        return money(str(SOCIAL_SECURITY_BASE_MIN))
    if SOCIAL_SECURITY_BASE_MAX > 0 and base_amount > money(str(SOCIAL_SECURITY_BASE_MAX)):
        return money(str(SOCIAL_SECURITY_BASE_MAX))
    return base_amount


def calculate_initial_social_security(
    base: Any, rates: Optional[dict[str, Decimal]] = None
) -> tuple[Decimal, Decimal]:
    """
    计算社保公积金草稿金额（费率优先取账套档案，缺省回落 config 杭州口径）。

    个人 = 社保（养老+医疗+失业）+ 公积金；单位 = 社保（养老+医疗+失业+工伤）+ 公积金。
    基数按 SOCIAL_SECURITY_BASE_MIN/MAX 限幅（0 表示不限制）。
    返回 (个人合计, 单位合计)，含在 personal_social_security / company_social_security
    列内（表头即"个人社保公积金 / 单位社保公积金"）。
    """
    rates = rates or default_payroll_rates()
    base_amount = _clamp_ss_base(money(base, allow_negative=False))
    personal = money(
        base_amount * rates["socialPersonal"] + base_amount * rates["housingPersonal"]
    )
    company = money(
        base_amount * rates["socialCompany"] + base_amount * rates["housingCompany"]
    )
    return personal, company

async def _sync_draft_sheet(conn, sheet: Dict[str, Any]) -> Dict[str, Any]:
    """读取已有工资表时的自愈逻辑：

    - status=pending_review 且计提凭证已复核过账 → 修正为 accrued（修 maker_checker 死链）；
    - status=draft 但计提凭证已存在（崩溃发生在状态更新前）→ 找回凭证并恢复状态；
    - status=draft 时把名单里新增的在职员工补进工资表（月中入职场景）。
    """
    sheet_id = sheet["id"]
    sheet = await _sync_sheet_lifecycle(conn, sheet)
    status = str(sheet.get("status") or "draft")
    accrual_generation = _generation(sheet, "accrual_generation")

    if status == "draft":
        recovered = await _find_entry_by_source(
            sheet["company_id"],
            SOURCE_ACCRUAL,
            _fingerprint("salary-accrual", sheet_id, accrual_generation),
        )
        if recovered:
            now = _now_iso()
            healed_status = "accrued" if recovered["status"] == "posted" else "pending_review"
            await conn.execute(
                "UPDATE salary_sheets SET status = ?, accrual_voucher_id = ?, updated_at = ? WHERE id = ?",
                (healed_status, recovered["id"], now, sheet_id),
            )
            await conn.commit()
            sheet["status"] = healed_status
            sheet["accrual_voucher_id"] = recovered["id"]
            status = healed_status

    if status == "draft":
        # 补齐月中新入职员工（幂等：只补缺失的 employee_id）
        cursor_emp = await conn.execute(
            "SELECT * FROM employees WHERE company_id = ? AND is_active = 1",
            (sheet["company_id"],),
        )
        employees = [dict(r) for r in await cursor_emp.fetchall()]
        cursor_ids = await conn.execute(
            "SELECT employee_id FROM salary_records WHERE sheet_id = ?",
            (sheet_id,),
        )
        existing_ids = {str(row["employee_id"]) for row in await cursor_ids.fetchall()}
        missing = [
            e for e in employees
            if str(e["id"]) not in existing_ids and _employed_in_month(e, str(sheet["month"]))
        ]
        if missing:
            now = _now_iso()
            records = list(sheet.get("records") or [])
            rates = await load_payroll_rates(conn, str(sheet["company_id"]))
            for emp in missing:
                base_sal = money(emp["base_salary"])
                ss_base = emp["social_security_base"]
                pers_ss, comp_ss = calculate_initial_social_security(ss_base, rates)
                net_sal = money(base_sal - pers_ss)
                tot_cost = money(base_sal + comp_ss)
                rec_id = f"srec-{_uid()}"
                await conn.execute(
                    "INSERT INTO salary_records (id, sheet_id, employee_id, employee_name, base_salary, bonus, allowance, deduction, personal_social_security, personal_tax, company_social_security, net_salary, total_cost, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, ?, 0.0, ?, ?, ?, ?, ?)",
                    (rec_id, sheet_id, emp["id"], emp["name"], base_sal, pers_ss, comp_ss, net_sal, tot_cost, now, now),
                )
                records.append({
                    "id": rec_id, "sheet_id": sheet_id, "employee_id": emp["id"], "employee_name": emp["name"],
                    "base_salary": money_text(base_sal), "bonus": "0.00", "allowance": "0.00", "deduction": "0.00",
                    "personal_social_security": money_text(pers_ss), "personal_tax": "0.00", "company_social_security": money_text(comp_ss),
                    "net_salary": money_text(net_sal), "total_cost": money_text(tot_cost), "created_at": now, "updated_at": now
                })
            await conn.execute("UPDATE salary_sheets SET updated_at = ? WHERE id = ?", (now, sheet_id))
            await conn.commit()
            sheet["records"] = records
    return sheet


async def get_or_create_salary_sheet(company_id: str, month: str) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        # Check if sheet exists
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()

        if sheet_row:
            sheet = dict(sheet_row)
            cursor_recs = await conn.execute(
                "SELECT * FROM salary_records WHERE sheet_id = ?",
                (sheet["id"],),
            )
            recs = await cursor_recs.fetchall()
            sheet["records"] = [_serialize_money_fields(dict(r)) for r in recs]
            sheet = await _sync_draft_sheet(conn, sheet)
            return await _enrich_salary_sheet(conn, sheet)
            
        # If not, create a new draft sheet
        sheet_id = f"sht-{_uid()}"
        now = _now_iso()
        
        await conn.execute(
            "INSERT INTO salary_sheets (id, company_id, month, status, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?)",
            (sheet_id, company_id, month, now, now),
        )
        
        # Load all active employees hired in or before this month
        cursor_emp = await conn.execute(
            "SELECT * FROM employees WHERE company_id = ? AND is_active = 1",
            (company_id,),
        )
        employees = [
            dict(emp) for emp in await cursor_emp.fetchall()
            if _employed_in_month(dict(emp), month)
        ]
        
        records = []
        rates = await load_payroll_rates(conn, company_id)
        for emp in employees:
            base_sal = emp["base_salary"]
            ss_base = emp["social_security_base"]
            pers_ss, comp_ss = calculate_initial_social_security(ss_base, rates)
            base_sal = money(base_sal)
            net_sal = money(base_sal - pers_ss) # Initial individual tax is 0.00
            tot_cost = money(base_sal + comp_ss)
            
            rec_id = f"srec-{_uid()}"
            await conn.execute(
                "INSERT INTO salary_records (id, sheet_id, employee_id, employee_name, base_salary, bonus, allowance, deduction, personal_social_security, personal_tax, company_social_security, net_salary, total_cost, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, ?, 0.0, ?, ?, ?, ?, ?)",
                (rec_id, sheet_id, emp["id"], emp["name"], base_sal, pers_ss, comp_ss, net_sal, tot_cost, now, now),
            )
            records.append({
                "id": rec_id, "sheet_id": sheet_id, "employee_id": emp["id"], "employee_name": emp["name"],
                "base_salary": money_text(base_sal), "bonus": "0.00", "allowance": "0.00", "deduction": "0.00",
                "personal_social_security": money_text(pers_ss), "personal_tax": "0.00", "company_social_security": money_text(comp_ss),
                "net_salary": money_text(net_sal), "total_cost": money_text(tot_cost), "created_at": now, "updated_at": now
            })
            
        await conn.commit()
        sheet = {
            "id": sheet_id, "company_id": company_id, "month": month, "status": "draft",
            "accrual_voucher_id": None, "payment_voucher_id": None,
            "ss_paid_voucher_id": None, "hf_paid_voucher_id": None, "tax_paid_voucher_id": None,
            "accrual_generation": 0, "payment_generation": 0, "ss_generation": 0,
            "hf_generation": 0, "tax_generation": 0,
            "personal_housing_fund_total": None, "company_housing_fund_total": None,
            "created_at": now, "updated_at": now, "records": records
        }
        return await _enrich_salary_sheet(conn, sheet)
    finally:
        await conn.close()

async def update_salary_records(company_id: str, month: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        # Check sheet status first
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
        sheet_row = await _sync_sheet_lifecycle(conn, dict(sheet_row))
        if sheet_row["status"] != "draft":
            raise ValueError("Cannot update records of an accrued or paid salary sheet")
            
        sheet_id = sheet_row["id"]
        now = _now_iso()
        
        for r in records:
            # Recalculate net salary & total cost to be safe
            base = money(r.get("base_salary") or ZERO, allow_negative=False)
            bonus = money(r.get("bonus") or ZERO, allow_negative=False)
            allowance = money(r.get("allowance") or ZERO, allow_negative=False)
            deduction = money(r.get("deduction") or ZERO, allow_negative=False)
            p_ss = money(r.get("personal_social_security") or ZERO, allow_negative=False)
            p_tax = money(r.get("personal_tax") or ZERO, allow_negative=False)
            c_ss = money(r.get("company_social_security") or ZERO, allow_negative=False)
            
            gross = base + bonus + allowance - deduction
            net = money(gross - p_ss - p_tax)
            cost = money(gross + c_ss)
            
            await conn.execute(
                "UPDATE salary_records SET base_salary=?, bonus=?, allowance=?, deduction=?, personal_social_security=?, personal_tax=?, company_social_security=?, net_salary=?, total_cost=?, updated_at=? "
                "WHERE id = ? AND sheet_id = ?",
                (base, bonus, allowance, deduction, p_ss, p_tax, c_ss, net, cost, now, r["id"], sheet_id),
            )
            
        await conn.execute(
            "UPDATE salary_sheets SET updated_at = ?, personal_housing_fund_total = 0.0, company_housing_fund_total = 0.0 WHERE id = ?",
            (now, sheet_id),
        )
        await conn.commit()
        
        # Load updated sheet
        cursor_sheet = await conn.execute("SELECT * FROM salary_sheets WHERE id = ?", (sheet_id,))
        sheet = dict(await cursor_sheet.fetchone())
        cursor_recs = await conn.execute("SELECT * FROM salary_records WHERE sheet_id = ?", (sheet_id,))
        recs = await cursor_recs.fetchall()
        sheet["records"] = [_serialize_money_fields(dict(rec)) for rec in recs]
        return await _enrich_salary_sheet(conn, sheet)
    finally:
        await conn.close()

# --- Bookkeeping Integration ---

async def accrue_salary_sheet(company_id: str, user_id: str, month: str) -> Dict[str, Any]:
    """Create salary accrual double-entry voucher and update sheet status."""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        # Check sheet
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
        sheet = await _sync_sheet_lifecycle(conn, dict(sheet_row))
        if sheet["status"] != "draft":
            raise ValueError("Sheet is already accrued or paid")
            
        # Load records and link employee department
        cursor_recs = await conn.execute(
            "SELECT sr.*, emp.department FROM salary_records sr "
            "JOIN employees emp ON sr.employee_id = emp.id "
            "WHERE sr.sheet_id = ?",
            (sheet["id"],),
        )
        records = [dict(r) for r in await cursor_recs.fetchall()]
        if not records:
            raise ValueError("No records found in this salary sheet")
        await _housing_fund_totals(conn, sheet, records)
        await conn.commit()
            
        from services.rd_allocation_service import get_salary_allocations
        rd_allocations = await get_salary_allocations(company_id, user_id, month)
        allocations_by_employee: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for allocation in rd_allocations:
            allocations_by_employee[allocation["employeeId"]].append(allocation)
        record_employee_ids = {str(record["employee_id"]) for record in records}
        unknown_employee_ids = sorted(set(allocations_by_employee) - record_employee_ids)
        if unknown_employee_ids:
            raise ValueError("研发工资分配包含不属于本月工资表的员工")

        # Accumulate totals
        # 研发部门判定改读账套部门字典（默认含「研发部」），不再只看 config 的固定名单：
        # 否则「技术部」「研发中心」这类自定义部门永远进不了研发支出。
        department_is_rd = {
            str(item["name"]): bool(item.get("isRd"))
            for item in await load_departments(conn, company_id)
        }

        rd_debit_lines: list[dict[str, Any]] = []
        admin_salary = ZERO
        admin_social = ZERO
        net_salary_total = ZERO
        tax_total = ZERO
        company_ss_total = ZERO
        personal_ss_total = ZERO

        for r in records:
            gross = money(money(r["base_salary"]) + money(r["bonus"]) + money(r["allowance"]) - money(r["deduction"]))
            c_ss = money(r["company_social_security"])
            p_ss = money(r["personal_social_security"])
            p_tax = money(r["personal_tax"])
            net = money(r["net_salary"])

            # 纵深防御：实发必须等于应发 − 个人社保 − 个税，否则凭证必然不平
            if net != money(gross - p_ss - p_tax):
                raise ValueError(f"员工“{r['employee_name']}”实发工资与应发/代扣项不一致，请检查工资明细")

            employee_allocations = allocations_by_employee.get(str(r["employee_id"]), [])
            total_rate = sum((Decimal(str(item["allocationRate"])) for item in employee_allocations), Decimal("0"))
            is_rd_department = department_is_rd.get(str(r["department"] or "").strip(), False)
            if employee_allocations and total_rate > Decimal("1"):
                raise ValueError(f"员工“{r['employee_name']}”的项目工时比例合计不能超过 100%")
            if is_rd_department and not employee_allocations:
                raise ValueError(f"研发部门员工“{r['employee_name']}”必须登记研发项目工时（可在工资表中为其分配项目）")

            # 工资池与单位社保池分别按工时比例分摊。分摊用最大余数法（_split_pool）：
            # Σ分摊 ≡ RD 池 恒成立，管理费用摊销 = 池 − RD 池 必为非负，
            # 不会出现逐项四舍五入溢出（如 5000×33.3333%×3=5000.01）把借方压成负数。
            # 费率合计不足 100% 时，残差部分归管理费用（与旧口径一致）。
            allocation_rates = [Decimal(str(item["allocationRate"])) for item in employee_allocations]
            salary_shares = _split_pool(money(gross * total_rate), allocation_rates)
            social_shares = _split_pool(money(c_ss * total_rate), allocation_rates)
            allocated_salary = money(sum(salary_shares, ZERO))
            allocated_social = money(sum(social_shares, ZERO))
            for index, allocation in enumerate(employee_allocations):
                salary_amount = salary_shares[index]
                social_amount = social_shares[index]
                amount = money(salary_amount + social_amount)
                if amount <= ZERO:
                    continue
                rd_debit_lines.append({
                    "accountCode": ACCT_RD_PERSONNEL[0],
                    "accountName": ACCT_RD_PERSONNEL[1],
                    "amount": amount,
                    "rdDetail": {
                        "projectId": allocation["projectId"],
                        "expenseType": "personnel",
                        "originalAmount": money(gross + c_ss),
                        "allocationMethod": "timesheet",
                        "allocationBasis": allocation["allocationBasis"],
                        "notes": f"{month} {r['employee_name']}工资及单位社保",
                    },
                })
            # 残差（比例合计不足 100% 的部分）自然归入管理费用明细，无需额外调平
            admin_salary = money(admin_salary + gross - allocated_salary)
            admin_social = money(admin_social + c_ss - allocated_social)
            if admin_salary < ZERO or admin_social < ZERO:
                raise ValueError(
                    f"员工“{r['employee_name']}”的研发分摊比例与金额产生负摊销，请检查工时分配"
                )

            net_salary_total = money(net_salary_total + net)
            tax_total = money(tax_total + p_tax)
            company_ss_total = money(company_ss_total + c_ss)
            personal_ss_total = money(personal_ss_total + p_ss)

        # Build balanced voucher lines
        debit_lines = list(rd_debit_lines)
        if admin_salary > ZERO:
            debit_lines.append(_line(ACCT_ADMIN_SALARY, admin_salary))
        if admin_social > ZERO:
            debit_lines.append(_line(ACCT_ADMIN_SOCIAL, admin_social))

        credit_lines = []
        if net_salary_total > ZERO:
            credit_lines.append(_line(ACCT_PAYROLL_SALARY, net_salary_total))
        if company_ss_total > ZERO:
            credit_lines.append(_line(ACCT_PAYROLL_COMPANY_SS, company_ss_total))
        if personal_ss_total > ZERO:
            credit_lines.append(_line(ACCT_SS_WITHHELD, personal_ss_total))
        if tax_total > ZERO:
            credit_lines.append(_line(ACCT_IIT_PAYABLE, tax_total))

        total_voucher_sum = money(sum((money(line["amount"]) for line in debit_lines), ZERO))
        credit_sum = money(sum((money(line["amount"]) for line in credit_lines), ZERO))
        if total_voucher_sum != credit_sum:
            raise ValueError(
                f"工资计提凭证借贷不平衡：借 {money_text(total_voucher_sum)} / 贷 {money_text(credit_sum)}，"
                "请检查工资明细（实发工资 ≠ 应发 − 个人社保 − 个税）"
            )
        if not debit_lines or not credit_lines:
            raise ValueError("本月工资金额为 0，无需生成计提凭证")

        year, m = map(int, month.split("-"))
        last_day = calendar.monthrange(year, m)[1]
        occur_date = f"{month}-{last_day:02d}"

        voucher_payload = {
            "direction": "expense",
            "amount": total_voucher_sum,
            "title": f"{month}月计提员工工资及社保",
            "category": "人力成本",
            "accountCode": "5602",
            "accountName": "管理费用",
            "occurDate": occur_date,
            "sourceType": SOURCE_ACCRUAL,
            "dedupFingerprint": _fingerprint(
                "salary-accrual", sheet["id"], _generation(sheet, "accrual_generation")
            ),
            "payer": "company_account",
            "voucherLines": {
                "debit": debit_lines,
                "credit": credit_lines
            },
            "voucherStatus": "confirmed",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
                trusted_voucher=True,
            )
        except Exception as exc:
            # 两段式写入的崩溃恢复：凭证已建但工资表状态未更新时，重试会撞来源唯一性
            # 校验（"该来源业务已入账"）。此时按指纹找回已建凭证继续走状态更新。
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(
                company_id,
                SOURCE_ACCRUAL,
                _fingerprint(
                    "salary-accrual", sheet["id"], _generation(sheet, "accrual_generation")
                ),
            )
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}
        
        # Update sheet status and link voucher
        if posted_entry["voucherStatus"] not in ACTIVE_VOUCHER_STATUSES:
            raise ValueError("工资计提凭证未进入已过账/待复核状态，请重试")
        now = _now_iso()
        next_status = "accrued" if posted_entry["voucherStatus"] == "posted" else "pending_review"
        await conn.execute(
            "UPDATE salary_sheets SET status = ?, accrual_voucher_id = ?, updated_at = ? WHERE id = ?",
            (next_status, posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()
        
        sheet["status"] = next_status
        sheet["accrual_voucher_id"] = posted_entry["id"]
        sheet["records"] = [_serialize_money_fields(r) for r in records]
        sheet.pop("voucherStatuses", None)
        return await _enrich_salary_sheet(conn, sheet)
    finally:
        await conn.close()

async def pay_salary_sheet(company_id: str, user_id: str, month: str, *, pay_date: Optional[str] = None) -> Dict[str, Any]:
    """Create salary payment double-entry voucher and update sheet status.

    pay_date：实际发薪日（YYYY-MM-DD）。真实发薪通常在工资所属期的次月，
    默认取操作当天，避免凭证日期落在工资月末导致银行流水对不上账。
    """
    await _ensure_db_ready()
    pay_occur_date = (pay_date or "").strip() or datetime.now(timezone.utc).date().isoformat()
    try:
        datetime.strptime(pay_occur_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("pay_date 格式应为 YYYY-MM-DD") from exc
    conn = await get_conn()
    try:
        # Check sheet
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
        sheet = await _sync_sheet_lifecycle(conn, dict(sheet_row))
        if sheet["status"] != "accrued":
            raise ValueError("Sheet must be accrued before it can be paid")
        if sheet.get("payment_voucher_id"):
            raise ValueError("本月工资已发放或发放凭证待复核，请勿重复操作")
            
        # Load records
        cursor_recs = await conn.execute(
            "SELECT * FROM salary_records WHERE sheet_id = ?",
            (sheet["id"],),
        )
        records = [dict(r) for r in await cursor_recs.fetchall()]
        net_salary_total = money(sum((money(r["net_salary"]) for r in records), ZERO))
        if net_salary_total <= ZERO:
            raise ValueError("Net salary payable must be greater than 0")
            
        # Post payment voucher to ledger
        occur_date = pay_occur_date
        
        voucher_payload = {
            "direction": "expense",
            "amount": net_salary_total,
            "title": f"银行代发{month}月员工工资",
            "category": "人力成本",
            "accountCode": ACCT_PAYROLL_SALARY[0],
            "accountName": ACCT_PAYROLL_SALARY[1],
            "occurDate": occur_date,
            "sourceType": SOURCE_PAY,
            "dedupFingerprint": _fingerprint(
                "salary-pay", sheet["id"], _generation(sheet, "payment_generation")
            ),
            "payer": "company_account",
            # 发放只结清应付职工薪酬-工资。个人社保公积金（221104）与个税（2221012）
            # 已在计提时单独挂账，分别由缴社保公积金、缴个税凭证清偿。
            "voucherLines": {
                "debit": [_line(ACCT_PAYROLL_SALARY, net_salary_total)],
                "credit": [_line(ACCT_BANK, net_salary_total)],
            },
            "voucherStatus": "confirmed",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
                trusted_voucher=True,
            )
        except Exception as exc:
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(
                company_id,
                SOURCE_PAY,
                _fingerprint(
                    "salary-pay", sheet["id"], _generation(sheet, "payment_generation")
                ),
            )
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}
        
        if posted_entry["voucherStatus"] not in ACTIVE_VOUCHER_STATUSES:
            raise ValueError("工资发放凭证未进入已过账/待复核状态，请重试")
        now = _now_iso()
        next_status = "paid" if posted_entry["voucherStatus"] == "posted" else "accrued"
        await conn.execute(
            "UPDATE salary_sheets SET status = ?, payment_voucher_id = ?, updated_at = ? WHERE id = ?",
            (next_status, posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()
        
        sheet["status"] = next_status
        sheet["payment_voucher_id"] = posted_entry["id"]
        sheet["records"] = [_serialize_money_fields(r) for r in records]
        sheet.pop("voucherStatuses", None)
        return await _enrich_salary_sheet(conn, sheet)
    finally:
        await conn.close()

SALARY_PAYMENT_KEYWORDS = ("工资", "薪资", "薪酬", "薪金", "代发", "奖金")


def _salary_payment_window(month: str) -> tuple[str, str]:
    """工资可当月发，也可次月发；窗口只到次月末。

    这样 7 月工资的流水不会一路匹配到 9 月，避免把 8 月工资的付款
    错认成 7 月工资；9 月流水应由 8 月工资表的窗口承接。
    """
    year, value = map(int, month.split("-"))
    start = f"{year:04d}-{value:02d}-01"
    next_year, next_month = (year + 1, 1) if value == 12 else (year, value + 1)
    last_day = calendar.monthrange(next_year, next_month)[1]
    return start, f"{next_year:04d}-{next_month:02d}-{last_day:02d}"


async def list_salary_payment_suggestions(company_id: str) -> Dict[str, Any]:
    """把已计提未发放的工资表与银行付款流水做候选匹配。

    只给建议，不自动入账。高置信需同时满足：金额精确到分、摘要含工资类
    关键词、窗口内同金额候选唯一；其余精确金额候选降为 medium，交人工确认。
    """
    await _ensure_db_ready()
    from services.bank_service import _ensure_schema as _ensure_bank_schema

    await _ensure_bank_schema()

    conn = await get_conn()
    try:
        sheets = [
            dict(row)
            for row in await (
                await conn.execute(
                    "SELECT ss.id, ss.month, COALESCE(SUM(sr.net_salary), 0) AS net_salary "
                    "FROM salary_sheets ss "
                    "LEFT JOIN salary_records sr ON sr.sheet_id = ss.id "
                    "JOIN journal_entries acc ON acc.id = ss.accrual_voucher_id "
                    "  AND acc.company_id = ss.company_id AND acc.status = 'posted' "
                    "WHERE ss.company_id = ? "
                    "AND (ss.payment_voucher_id IS NULL OR ss.payment_voucher_id = '') "
                    "GROUP BY ss.id, ss.month ORDER BY ss.month ASC",
                    (company_id,),
                )
            ).fetchall()
        ]
        suggestions: list[Dict[str, Any]] = []
        used_statement_ids: set[str] = set()
        for sheet in sheets:
            month = str(sheet["month"])
            net_salary = money(sheet["net_salary"])
            if net_salary <= ZERO:
                continue
            start, end = _salary_payment_window(month)
            statements = [
                dict(row)
                for row in await (
                    await conn.execute(
                        "SELECT id, transaction_date, description, amount "
                        "FROM bank_statements WHERE company_id = ? "
                        "AND (matched_entry_id IS NULL OR matched_entry_id = '') "
                        "AND amount < 0 AND transaction_date >= ? AND transaction_date <= ? "
                        "AND id NOT IN ("
                        "  SELECT statement_id FROM bank_statement_matches WHERE company_id = ?"
                        ") ORDER BY transaction_date ASC",
                        (company_id, start, end, company_id),
                    )
                ).fetchall()
                if str(row["id"]) not in used_statement_ids
            ]
            exact = [
                row for row in statements
                if money(abs(money(row["amount"]))) == net_salary
            ]
            if not exact:
                continue
            keyword_matches = [
                row for row in exact
                if any(keyword in str(row["description"] or "") for keyword in SALARY_PAYMENT_KEYWORDS)
            ]
            pool = keyword_matches or exact
            candidate = sorted(
                pool,
                key=lambda row: (
                    0 if str(row["transaction_date"])[:7] == _next_month(month) else 1,
                    str(row["transaction_date"]),
                ),
            )[0]
            has_keyword = candidate in keyword_matches
            unique = len(exact) == 1
            confidence = "high" if has_keyword and unique else "medium"
            transaction_month = str(candidate["transaction_date"])[:7]
            reasons = ["金额精确匹配"]
            if transaction_month != month:
                reasons.append(f"流水在 {transaction_month}，按 {month} 工资核销")
            if has_keyword:
                reasons.append("摘要含工资关键词")
            else:
                reasons.append("摘要未识别到工资关键词")
            if unique:
                reasons.append("窗口内同金额流水唯一")
            else:
                reasons.append(f"窗口内有 {len(exact)} 笔同金额流水，需人工确认")
            used_statement_ids.add(str(candidate["id"]))
            suggestions.append(
                {
                    "sheetId": str(sheet["id"]),
                    "month": month,
                    "netSalary": money_text(net_salary),
                    "payDate": str(candidate["transaction_date"])[:10],
                    "confidence": confidence,
                    "reason": "；".join(reasons),
                    "statement": {
                        "id": str(candidate["id"]),
                        "date": str(candidate["transaction_date"])[:10],
                        "description": str(candidate["description"] or ""),
                        "amount": money_text(money(candidate["amount"])),
                    },
                }
            )
        return {"total": len(suggestions), "suggestions": suggestions}
    finally:
        await conn.close()


def _next_month(month: str) -> str:
    year, value = map(int, month.split("-"))
    return f"{year + 1:04d}-01" if value == 12 else f"{year:04d}-{value + 1:02d}"


async def apply_salary_payment_from_statement(
    company_id: str, user_id: str, month: str, statement_id: str, request_id: str
) -> Dict[str, Any]:
    """确认某笔银行付款流水就是工资发放：生成发放凭证并按流水日期核销。

    发放凭证生成后若银行核销失败，工资仍保持已发放，返回 linked=false 和原因，
    由用户在流水页手工关联，避免重复发放。
    """
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        sheet_row = await (
            await conn.execute(
                "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
                (company_id, month),
            )
        ).fetchone()
        if not sheet_row:
            raise ValueError("工资表不存在")
        sheet = await _sync_sheet_lifecycle(conn, dict(sheet_row))
        if sheet["status"] != "accrued" or sheet.get("payment_voucher_id"):
            raise ValueError("工资表必须处于已计提未发放状态")
        statement_row = await (
            await conn.execute(
                "SELECT id, transaction_date, description, amount, matched_entry_id "
                "FROM bank_statements WHERE company_id = ? AND id = ?",
                (company_id, statement_id),
            )
        ).fetchone()
        if not statement_row:
            raise ValueError("银行流水不存在")
        statement = dict(statement_row)
        if statement.get("matched_entry_id"):
            raise ValueError("该银行流水已匹配其他账目")
        records = [
            dict(row)
            for row in await (
                await conn.execute(
                    "SELECT net_salary FROM salary_records WHERE sheet_id = ?", (sheet["id"],)
                )
            ).fetchall()
        ]
        net_salary = money(sum((money(row["net_salary"]) for row in records), ZERO))
        if net_salary <= ZERO:
            raise ValueError("工资金额不大于 0，无需发放")
        statement_amount = money(statement["amount"])
        if statement_amount >= ZERO:
            raise ValueError("工资发放只能匹配银行付款流水（金额为负）")
        if money(abs(statement_amount)) != net_salary:
            raise ValueError(
                f"流水金额 {money_text(abs(statement_amount))} "
                f"与实发工资 {money_text(net_salary)} 不一致"
            )
        transaction_date = str(statement["transaction_date"])[:10]
        window_start, window_end = _salary_payment_window(month)
        if not (window_start <= transaction_date <= window_end):
            raise ValueError(
                f"流水日期 {transaction_date} 不在 {month} 工资发放窗口 "
                f"{window_start} 至 {window_end} 内"
            )
    finally:
        await conn.close()

    paid = await pay_salary_sheet(
        company_id, user_id, month, pay_date=transaction_date
    )
    payment_entry_id = str(paid.get("payment_voucher_id") or "")
    result = {
        **paid,
        "statementId": statement_id,
        "linked": False,
    }
    if not payment_entry_id:
        return {**result, "linkError": "发放凭证未返回凭证 ID"}

    from db.session import session_scope
    from services.bank_settlement_service import confirm_statement_match

    try:
        async with session_scope() as session:
            await confirm_statement_match(
                session,
                company_id=company_id,
                user_id=user_id,
                statement_id=statement_id,
                entry_ids=[payment_entry_id],
                request_id=request_id,
            )
    except Exception as exc:  # 发放已完成，核销失败留给用户手工处理
        return {**result, "linkError": str(exc)}
    return {**result, "linked": True}


async def pay_payroll_liability(
    company_id: str, user_id: str, month: str, kind: str,
    *, pay_date: Optional[str] = None,
) -> Dict[str, Any]:
    """缴纳社保、公积金或个税，生成清偿挂账的凭证。

    - kind=social_security → 借 221102 + 221104，贷 1002（仅社保，不含公积金）
    - kind=housing_fund    → 借 221102 + 221104，贷 1002（仅公积金）
    - kind=tax             → 借 2221012，贷 1002
    社保与公积金可以分两笔银行扣款分别核销；金额为 0 的税种直接返回
    not_required，不再要求生成 0 元凭证。
    """
    await _ensure_db_ready()
    if kind not in ("social_security", "housing_fund", "tax"):
        raise ValueError("kind 必须是 social_security、housing_fund 或 tax")
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
        sheet = await _sync_sheet_lifecycle(conn, dict(sheet_row))
        if sheet["status"] not in ("accrued", "paid"):
            raise ValueError("工资表计提后才能缴纳社保/公积金/个税")

        columns = {
            "social_security": ("ss_paid_voucher_id", "ss_generation", SOURCE_SS, "salary-ss"),
            "housing_fund": ("hf_paid_voucher_id", "hf_generation", SOURCE_HF, "salary-hf"),
            "tax": ("tax_paid_voucher_id", "tax_generation", SOURCE_TAX, "salary-tax"),
        }
        column, generation_column, source_type, fp_kind = columns[kind]
        if sheet.get(column):
            raise ValueError("本月该项已缴纳或凭证待复核，请勿重复操作")
        if (
            kind == "housing_fund"
            and sheet.get("ss_payment_scope") != "social"
            and (sheet.get("voucherStatuses") or {}).get(str(sheet.get("ss_paid_voucher_id") or "")) == "posted"
        ):
            raise ValueError("本月公积金已随历史社保合并缴纳，请勿重复操作")

        cursor_recs = await conn.execute(
            "SELECT * FROM salary_records WHERE sheet_id = ?",
            (sheet["id"],),
        )
        records = [dict(r) for r in await cursor_recs.fetchall()]

        personal_total = money(sum((money(r["personal_social_security"]) for r in records), ZERO))
        company_total = money(sum((money(r["company_social_security"]) for r in records), ZERO))
        personal_hf, company_hf = await _housing_fund_totals(conn, sheet, records)
        await conn.commit()
        personal_social = money(personal_total - personal_hf)
        company_social = money(company_total - company_hf)

        debit_lines = []
        if kind == "social_security":
            amount = money(company_social + personal_social)
            title = f"缴纳{month}月社会保险（单位+个人）"
            if company_social > ZERO:
                debit_lines.append(_line(ACCT_PAYROLL_COMPANY_SS, company_social))
            if personal_social > ZERO:
                debit_lines.append(_line(ACCT_SS_WITHHELD, personal_social))
        elif kind == "housing_fund":
            amount = money(company_hf + personal_hf)
            title = f"缴纳{month}月住房公积金（单位+个人）"
            if company_hf > ZERO:
                debit_lines.append(_line(ACCT_PAYROLL_COMPANY_SS, company_hf))
            if personal_hf > ZERO:
                debit_lines.append(_line(ACCT_SS_WITHHELD, personal_hf))
        else:
            amount = money(sum((money(r["personal_tax"]) for r in records), ZERO))
            title = f"缴纳{month}月个人所得税"
            if amount > ZERO:
                debit_lines = [_line(ACCT_IIT_PAYABLE, amount)]

        if amount <= ZERO:
            sheet["records"] = [_serialize_money_fields(r) for r in records]
            return await _enrich_salary_sheet(conn, sheet)

        pay_occur_date = (pay_date or "").strip() or datetime.now(timezone.utc).date().isoformat()
        try:
            datetime.strptime(pay_occur_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("pay_date 格式应为 YYYY-MM-DD") from exc

        voucher_payload = {
            "direction": "expense",
            "amount": amount,
            "title": title,
            "category": "人力成本",
            "accountCode": debit_lines[0]["accountCode"],
            "accountName": debit_lines[0]["accountName"],
            "occurDate": pay_occur_date,
            "sourceType": source_type,
            "dedupFingerprint": _fingerprint(
                fp_kind, sheet["id"], _generation(sheet, generation_column)
            ),
            "payer": "company_account",
            "voucherLines": {
                "debit": debit_lines,
                "credit": [_line(ACCT_BANK, amount)],
            },
            "voucherStatus": "confirmed",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
                trusted_voucher=True,
            )
        except Exception as exc:
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(
                company_id,
                source_type,
                _fingerprint(fp_kind, sheet["id"], _generation(sheet, generation_column)),
            )
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}

        if posted_entry["voucherStatus"] not in ACTIVE_VOUCHER_STATUSES:
            raise ValueError("缴纳凭证未进入已过账/待复核状态，请重试")
        now = _now_iso()
        scope_update = ", ss_payment_scope = 'social'" if kind == "social_security" else ""
        await conn.execute(
            f"UPDATE salary_sheets SET {column} = ?, updated_at = ?{scope_update} WHERE id = ?",
            (posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()

        from services.audit_service import log_action

        await log_action(company_id, user_id, f"salary_{kind}_paid", "salary_sheet", sheet["id"], {"amount": money_text(amount)})
        if kind == "social_security":
            sheet["ss_payment_scope"] = "social"
        sheet[column] = posted_entry["id"]
        sheet["records"] = [_serialize_money_fields(r) for r in records]
        sheet.pop("voucherStatuses", None)
        return await _enrich_salary_sheet(conn, sheet)
    finally:
        await conn.close()


async def list_payroll_liability_payment_suggestions(company_id: str) -> Dict[str, Any]:
    """社保、公积金、个税与银行付款流水的候选匹配（只建议，不自动入账）。"""
    await _ensure_db_ready()
    from services.bank_service import _ensure_schema as _ensure_bank_schema

    await _ensure_bank_schema()
    conn = await get_conn()
    try:
        rows = await (
            await conn.execute(
                "SELECT ss.* FROM salary_sheets ss "
                "JOIN journal_entries acc ON acc.id = ss.accrual_voucher_id "
                "  AND acc.company_id = ss.company_id AND acc.status = 'posted' "
                "WHERE ss.company_id = ? ORDER BY ss.month ASC",
                (company_id,),
            )
        ).fetchall()
        suggestions: list[Dict[str, Any]] = []
        used_statement_ids: set[str] = set()
        labels = {
            "social_security": "社保",
            "housing_fund": "公积金",
            "tax": "个税",
        }
        keywords = {
            "social_security": ("社保", "社会保险"),
            "housing_fund": ("公积金", "住房公积金"),
            "tax": ("个税", "个人所得税", "税款", "税务"),
        }
        for row in rows:
            sheet = dict(row)
            statuses = await _entry_statuses(
                company_id,
                [
                    sheet.get("ss_paid_voucher_id"),
                    sheet.get("hf_paid_voucher_id"),
                    sheet.get("tax_paid_voucher_id"),
                ],
            )
            records = [
                dict(record)
                for record in await (
                    await conn.execute(
                        "SELECT * FROM salary_records WHERE sheet_id = ?", (sheet["id"],)
                    )
                ).fetchall()
            ]
            personal_total = money(sum((money(r["personal_social_security"]) for r in records), ZERO))
            company_total = money(sum((money(r["company_social_security"]) for r in records), ZERO))
            personal_hf, company_hf = await _housing_fund_totals(conn, sheet, records)
            personal_social = money(personal_total - personal_hf)
            company_social = money(company_total - company_hf)
            tax_total = money(sum((money(r["personal_tax"]) for r in records), ZERO))

            dues = {
                "social_security": money(company_social + personal_social),
                "housing_fund": money(company_hf + personal_hf),
                "tax": tax_total,
            }
            paid_ids = {
                "social_security": sheet.get("ss_paid_voucher_id"),
                "housing_fund": sheet.get("hf_paid_voucher_id"),
                "tax": sheet.get("tax_paid_voucher_id"),
            }
            if (
                sheet.get("ss_payment_scope") != "social"
                and statuses.get(str(sheet.get("ss_paid_voucher_id") or "")) == "posted"
            ):
                paid_ids["housing_fund"] = paid_ids["housing_fund"] or paid_ids["social_security"]
            month = str(sheet["month"])
            start, end = _salary_payment_window(month)
            for kind, due in dues.items():
                if due <= ZERO or paid_ids[kind]:
                    continue
                statements = [
                    dict(statement)
                    for statement in await (
                        await conn.execute(
                            "SELECT id, transaction_date, description, amount "
                            "FROM bank_statements WHERE company_id = ? "
                            "AND (matched_entry_id IS NULL OR matched_entry_id = '') "
                            "AND amount < 0 AND transaction_date >= ? AND transaction_date <= ? "
                            "AND id NOT IN ("
                            "  SELECT statement_id FROM bank_statement_matches WHERE company_id = ?"
                            ") ORDER BY transaction_date ASC",
                            (company_id, start, end, company_id),
                        )
                    ).fetchall()
                    if str(statement["id"]) not in used_statement_ids
                ]
                exact = [statement for statement in statements if money(abs(money(statement["amount"]))) == due]
                if not exact:
                    continue
                keyword_matches = [
                    statement for statement in exact
                    if any(word in str(statement["description"] or "") for word in keywords[kind])
                ]
                pool = keyword_matches or exact
                candidate = sorted(
                    pool,
                    key=lambda statement: (
                        0 if str(statement["transaction_date"])[:7] == _next_month(month) else 1,
                        str(statement["transaction_date"]),
                    ),
                )[0]
                has_keyword = candidate in keyword_matches
                unique = len(exact) == 1
                confidence = "high" if has_keyword and unique else "medium"
                reasons = ["金额精确匹配"]
                if has_keyword:
                    reasons.append(f"摘要含{labels[kind]}关键词")
                else:
                    reasons.append(f"摘要未识别到{labels[kind]}关键词")
                if unique:
                    reasons.append("窗口内同金额流水唯一")
                else:
                    reasons.append(f"窗口内有 {len(exact)} 笔同金额流水，需人工确认")
                used_statement_ids.add(str(candidate["id"]))
                suggestions.append({
                    "sheetId": str(sheet["id"]),
                    "month": month,
                    "kind": kind,
                    "label": labels[kind],
                    "amount": money_text(due),
                    "payDate": str(candidate["transaction_date"])[:10],
                    "confidence": confidence,
                    "reason": "；".join(reasons),
                    "statement": {
                        "id": str(candidate["id"]),
                        "date": str(candidate["transaction_date"])[:10],
                        "description": str(candidate["description"] or ""),
                        "amount": money_text(money(candidate["amount"])),
                    },
                })
        return {"total": len(suggestions), "suggestions": suggestions}
    finally:
        await conn.close()


async def apply_payroll_liability_from_statement(
    company_id: str, user_id: str, month: str, kind: str,
    statement_id: str, request_id: str,
) -> Dict[str, Any]:
    """确认某笔流水为社保/公积金/个税缴纳，生成凭证并按流水日期核销。"""
    if kind not in ("social_security", "housing_fund", "tax"):
        raise ValueError("kind 必须是 social_security、housing_fund 或 tax")
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        statement_row = await (
            await conn.execute(
                "SELECT id, transaction_date, amount, matched_entry_id "
                "FROM bank_statements WHERE company_id = ? AND id = ?",
                (company_id, statement_id),
            )
        ).fetchone()
    finally:
        await conn.close()
    if not statement_row:
        raise ValueError("银行流水不存在")
    statement = dict(statement_row)
    if statement.get("matched_entry_id"):
        raise ValueError("该银行流水已匹配其他账目")
    statement_amount = money(statement["amount"])
    if statement_amount >= ZERO:
        raise ValueError("社保/公积金/个税缴纳只能匹配银行付款流水（金额为负）")
    transaction_date = str(statement["transaction_date"])[:10]
    window_start, window_end = _salary_payment_window(month)
    if not (window_start <= transaction_date <= window_end):
        raise ValueError(
            f"流水日期 {transaction_date} 不在 {month} 缴纳窗口 "
            f"{window_start} 至 {window_end} 内"
        )

    paid = await pay_payroll_liability(
        company_id, user_id, month, kind, pay_date=transaction_date
    )
    column = {
        "social_security": "ss_paid_voucher_id",
        "housing_fund": "hf_paid_voucher_id",
        "tax": "tax_paid_voucher_id",
    }[kind]
    entry_id = str(paid.get(column) or "")
    result = {**paid, "statementId": statement_id, "linked": False}
    if not entry_id:
        return {**result, "linkError": "缴纳金额为 0，未生成凭证"}

    from db.session import session_scope
    from services.bank_settlement_service import confirm_statement_match

    try:
        async with session_scope() as session:
            await confirm_statement_match(
                session,
                company_id=company_id,
                user_id=user_id,
                statement_id=statement_id,
                entry_ids=[entry_id],
                request_id=request_id,
            )
    except Exception as exc:
        return {**result, "linkError": str(exc)}
    return {**result, "linked": True}


# --- 导出：工资表 / 工资条 ---

def _csv_cell(value: Any) -> str:
    """CSV 单元格转义（姓名、部门可能含逗号或引号）。"""
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_rows(rows: list[list[Any]]) -> str:
    return "\r\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows)


async def _require_sheet(conn, company_id: str, month: str) -> None:
    cursor = await conn.execute(
        "SELECT id FROM salary_sheets WHERE company_id = ? AND month = ?",
        (company_id, month),
    )
    if not await cursor.fetchone():
        raise ValueError(f"{month} 还没有工资表，请先在工资页生成当月工资表")


async def export_salary_sheet_csv(company_id: str, month: str) -> tuple[str, str]:
    """导出整月工资表（含合计行）。返回 (csv 文本, 文件名)。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        await _require_sheet(conn, company_id, month)
    finally:
        await conn.close()

    sheet = await get_or_create_salary_sheet(company_id, month)
    records = sheet.get("records") or []
    payroll_rates = await get_payroll_rates(company_id)

    # salary_records 本身不存部门，导出要从名册补（工资表接口也不返回部门）
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, department FROM employees WHERE company_id = ?", (company_id,)
        )
        departments = {str(row["id"]): str(row["department"]) for row in await cursor.fetchall()}
    finally:
        await conn.close()

    header = [
        "姓名", "部门", "基本工资", "奖金", "补助/津贴", "考勤扣款", "应发工资",
        "个人社保公积金", "个税代扣", "实发工资", "单位社保公积金", "企业总成本",
    ]
    rows: list[list[Any]] = [[f"{month} 工资表"], [], header]
    totals = {"gross": ZERO, "personal": ZERO, "tax": ZERO, "net": ZERO, "company": ZERO, "cost": ZERO}
    for record in records:
        gross = money(
            money(record["base_salary"])
            + money(record["bonus"])
            + money(record["allowance"])
            - money(record["deduction"])
        )
        personal = money(record["personal_social_security"])
        tax = money(record["personal_tax"])
        net = money(record["net_salary"])
        company_ss = money(record["company_social_security"])
        cost = money(record["total_cost"])
        totals["gross"] = money(totals["gross"] + gross)
        totals["personal"] = money(totals["personal"] + personal)
        totals["tax"] = money(totals["tax"] + tax)
        totals["net"] = money(totals["net"] + net)
        totals["company"] = money(totals["company"] + company_ss)
        totals["cost"] = money(totals["cost"] + cost)
        rows.append([
            record["employee_name"],
            record.get("department") or departments.get(str(record["employee_id"]), ""),
            money_text(record["base_salary"]),
            money_text(record["bonus"]),
            money_text(record["allowance"]),
            money_text(record["deduction"]),
            money_text(gross),
            money_text(personal),
            money_text(tax),
            money_text(net),
            money_text(company_ss),
            money_text(cost),
        ])
    rows.append([
        "合计", f"{len(records)} 人", "", "", "", "",
        money_text(totals["gross"]), money_text(totals["personal"]), money_text(totals["tax"]),
        money_text(totals["net"]), money_text(totals["company"]), money_text(totals["cost"]),
    ])

    social_rates = payroll_rates["rates"]
    rows.append([])
    rows.append([
        "费率口径",
        "个人社保", social_rates["socialPersonal"],
        "个人公积金", social_rates["housingPersonal"],
        "单位社保", social_rates["socialCompany"],
        "单位公积金", social_rates["housingCompany"],
        "（账套自定义）" if payroll_rates.get("isCustomized") else "（默认杭州口径）",
    ])
    return _csv_rows(rows), f"工资表-{month}.csv"


async def export_payslip_csv(company_id: str, month: str, record_id: str) -> tuple[str, str]:
    """导出单人工资条。返回 (csv 文本, 文件名)。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        await _require_sheet(conn, company_id, month)
        cursor = await conn.execute(
            "SELECT sr.*, e.department FROM salary_records sr "
            "JOIN salary_sheets ss ON ss.id = sr.sheet_id "
            "LEFT JOIN employees e ON e.id = sr.employee_id "
            "WHERE ss.company_id = ? AND ss.month = ? AND sr.id = ?",
            (company_id, month, record_id),
        )
        record = await cursor.fetchone()
    finally:
        await conn.close()
    if not record:
        raise ValueError("工资条不存在")
    record = dict(record)

    gross = money(
        money(record["base_salary"])
        + money(record["bonus"])
        + money(record["allowance"])
        - money(record["deduction"])
    )
    rows: list[list[Any]] = [
        [f"{month} 工资条"],
        [],
        ["姓名", record["employee_name"]],
        ["部门", record.get("department") or ""],
        [],
        ["项目", "金额（元）"],
        ["基本工资", money_text(record["base_salary"])],
        ["奖金", money_text(record["bonus"])],
        ["补助/津贴", money_text(record["allowance"])],
        ["考勤扣款", money_text(record["deduction"])],
        ["应发工资", money_text(gross)],
        ["个人社保公积金", money_text(record["personal_social_security"])],
        ["个税代扣", money_text(record["personal_tax"])],
        ["实发工资", money_text(record["net_salary"])],
        ["单位社保公积金", money_text(record["company_social_security"])],
        ["企业总成本", money_text(record["total_cost"])],
    ]
    return _csv_rows(rows), f"工资条-{month}-{record['employee_name']}.csv"
