"""Employee Salary and Social Security database service."""
from __future__ import annotations

import os
import calendar
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.database import get_conn
from services.json_utils import json_dumps
from services.entry_service import save_entry
from services.llm import _uid
from core.money import ZERO, money, money_text, rate

SALARY_MONEY_FIELDS = {
    "base_salary", "social_security_base", "bonus", "allowance", "deduction",
    "personal_social_security", "personal_tax", "company_social_security",
    "net_salary", "total_cost",
}

# 凭证来源指纹：计提/发放/缴社保/缴个税各一个，崩溃重试时凭指纹找回已建凭证，
# 避免"凭证已入账但工资表状态未更新"场景下重复入账（两段式写入的恢复机制）。
SOURCE_ACCRUAL = "salary_accrual"
SOURCE_PAY = "salary_pay"
SOURCE_SS = "salary_social"
SOURCE_TAX = "salary_iit"


def _fingerprint(kind: str, sheet_id: str) -> str:
    return f"{kind}-{sheet_id}"


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


def _serialize_money_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: money_text(value) if key in SALARY_MONEY_FIELDS else value
        for key, value in row.items()
    }

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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
        # 增量列：社保/个税缴纳凭证（2026-09-30 补丁；对已有库幂等）
        for column in ("ss_paid_voucher_id", "tax_paid_voucher_id"):
            try:
                await conn.execute(f"ALTER TABLE salary_sheets ADD COLUMN {column} TEXT")
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
                          base_salary: Any, social_security_base: Any) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        eid = f"emp-{_uid()}"
        now = _now_iso()
        base_salary = money(base_salary, allow_negative=False)
        social_security_base = money(social_security_base, allow_negative=False)
        await conn.execute(
            "INSERT INTO employees (id, company_id, name, department, base_salary, social_security_base, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (eid, company_id, name, department, base_salary, social_security_base, now, now),
        )
        await conn.commit()
        return {
            "id": eid, "company_id": company_id, "name": name, "department": department,
            "base_salary": money_text(base_salary), "social_security_base": money_text(social_security_base),
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
        for key in ("name", "department", "base_salary", "social_security_base", "is_active"):
            if key in updates:
                fields.append(f"{key} = ?")
                values.append(
                    money(updates[key], allow_negative=False)
                    if key in {"base_salary", "social_security_base"}
                    else updates[key]
                )
        if not fields:
            return None
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

# --- Salary Sheets & Records ---

def _clamp_ss_base(base_amount: Decimal) -> Decimal:
    """社保公积金缴费基数限幅（当地社平的 60%-300%，由配置决定；0 表示不限制）。"""
    from config import SOCIAL_SECURITY_BASE_MAX, SOCIAL_SECURITY_BASE_MIN

    if SOCIAL_SECURITY_BASE_MIN > 0 and base_amount < money(str(SOCIAL_SECURITY_BASE_MIN)):
        return money(str(SOCIAL_SECURITY_BASE_MIN))
    if SOCIAL_SECURITY_BASE_MAX > 0 and base_amount > money(str(SOCIAL_SECURITY_BASE_MAX)):
        return money(str(SOCIAL_SECURITY_BASE_MAX))
    return base_amount


def calculate_initial_social_security(base: Any) -> tuple[Decimal, Decimal]:
    """
    计算社保公积金草稿金额（费率由 config 配置，默认杭州口径）。

    个人 = 社保 10.5%（养老8%+医疗2%+失业0.5%）+ 公积金 8%
    单位 = 社保 25%（养老16%+医疗8%+失业0.5%+工伤0.5%）+ 公积金 8%
    基数按 SOCIAL_SECURITY_BASE_MIN/MAX 限幅（0 表示不限制）。
    返回 (个人合计, 单位合计)，含在 personal_social_security / company_social_security
    列内（表头即"个人社保公积金 / 单位社保公积金"）。
    """
    from config import (
        HF_COMPANY_RATE,
        HF_PERSONAL_RATE,
        SOCIAL_COMPANY_RATE,
        SOCIAL_PERSONAL_RATE,
    )

    base_amount = _clamp_ss_base(money(base, allow_negative=False))
    personal = money(
        base_amount * rate(str(SOCIAL_PERSONAL_RATE)) + base_amount * rate(str(HF_PERSONAL_RATE))
    )
    company = money(
        base_amount * rate(str(SOCIAL_COMPANY_RATE)) + base_amount * rate(str(HF_COMPANY_RATE))
    )
    return personal, company

async def _sync_draft_sheet(conn, sheet: Dict[str, Any]) -> Dict[str, Any]:
    """读取已有工资表时的自愈逻辑：

    - status=pending_review 且计提凭证已复核过账 → 修正为 accrued（修 maker_checker 死链）；
    - status=draft 但计提凭证已存在（崩溃发生在状态更新前）→ 找回凭证并恢复状态；
    - status=draft 时把名单里新增的在职员工补进工资表（月中入职场景）。
    """
    sheet_id = sheet["id"]
    status = str(sheet.get("status") or "draft")
    accrual_voucher_id = sheet.get("accrual_voucher_id")

    if status == "pending_review" and accrual_voucher_id:
        entry_status = await _entry_status(sheet["company_id"], accrual_voucher_id)
        if entry_status == "posted":
            now = _now_iso()
            await conn.execute(
                "UPDATE salary_sheets SET status = 'accrued', updated_at = ? WHERE id = ?",
                (now, sheet_id),
            )
            await conn.commit()
            sheet["status"] = "accrued"
            status = "accrued"

    if status == "draft":
        recovered = await _find_entry_by_source(
            sheet["company_id"], SOURCE_ACCRUAL, _fingerprint("salary-accrual", sheet_id)
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
        missing = [e for e in employees if str(e["id"]) not in existing_ids]
        if missing:
            now = _now_iso()
            records = list(sheet.get("records") or [])
            for emp in missing:
                base_sal = money(emp["base_salary"])
                ss_base = emp["social_security_base"]
                pers_ss, comp_ss = calculate_initial_social_security(ss_base)
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
            return sheet
            
        # If not, create a new draft sheet
        sheet_id = f"sht-{_uid()}"
        now = _now_iso()
        
        await conn.execute(
            "INSERT INTO salary_sheets (id, company_id, month, status, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?)",
            (sheet_id, company_id, month, now, now),
        )
        
        # Load all active employees
        cursor_emp = await conn.execute(
            "SELECT * FROM employees WHERE company_id = ? AND is_active = 1",
            (company_id,),
        )
        employees = await cursor_emp.fetchall()
        
        records = []
        for emp in employees:
            base_sal = emp["base_salary"]
            ss_base = emp["social_security_base"]
            pers_ss, comp_ss = calculate_initial_social_security(ss_base)
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
        return {
            "id": sheet_id, "company_id": company_id, "month": month, "status": "draft",
            "accrual_voucher_id": None, "payment_voucher_id": None,
            "created_at": now, "updated_at": now, "records": records
        }
    finally:
        await conn.close()

async def update_salary_records(company_id: str, month: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        # Check sheet status first
        cursor = await conn.execute(
            "SELECT id, status FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
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
            "UPDATE salary_sheets SET updated_at = ? WHERE id = ?",
            (now, sheet_id),
        )
        await conn.commit()
        
        # Load updated sheet
        cursor_sheet = await conn.execute("SELECT * FROM salary_sheets WHERE id = ?", (sheet_id,))
        sheet = dict(await cursor_sheet.fetchone())
        cursor_recs = await conn.execute("SELECT * FROM salary_records WHERE sheet_id = ?", (sheet_id,))
        recs = await cursor_recs.fetchall()
        sheet["records"] = [_serialize_money_fields(dict(rec)) for rec in recs]
        return sheet
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
        sheet = dict(sheet_row)
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
        from config import RD_DEPARTMENTS

        rd_debit_lines: list[dict[str, Any]] = []
        admin_cost = ZERO
        net_salary_total = ZERO
        tax_total = ZERO
        ss_total = ZERO
        
        for r in records:
            gross = money(r["base_salary"]) + money(r["bonus"]) + money(r["allowance"]) - money(r["deduction"])
            r_cost = money(gross + money(r["company_social_security"]))
            
            employee_allocations = allocations_by_employee.get(str(r["employee_id"]), [])
            total_rate = sum((Decimal(str(item["allocationRate"])) for item in employee_allocations), Decimal("0"))
            is_rd_department = str(r["department"] or "").strip() in RD_DEPARTMENTS
            if employee_allocations and total_rate > Decimal("1"):
                raise ValueError(f"员工“{r['employee_name']}”的项目工时比例合计不能超过 100%")
            if is_rd_department and not employee_allocations:
                raise ValueError(f"研发部门员工“{r['employee_name']}”必须登记研发项目工时（可在工资表中为其分配项目）")
            allocated_cost = ZERO
            for index, allocation in enumerate(employee_allocations):
                allocation_rate = Decimal(str(allocation["allocationRate"]))
                is_last_full_allocation = index == len(employee_allocations) - 1 and total_rate == Decimal("1")
                amount = money(r_cost - allocated_cost) if is_last_full_allocation else money(r_cost * allocation_rate)
                allocated_cost = money(allocated_cost + amount)
                if amount <= ZERO:
                    continue
                rd_debit_lines.append({
                    "accountCode": "530101",
                    "accountName": "研发支出-人员人工",
                    "amount": amount,
                    "rdDetail": {
                        "projectId": allocation["projectId"],
                        "expenseType": "personnel",
                        "originalAmount": r_cost,
                        "allocationMethod": "timesheet",
                        "allocationBasis": allocation["allocationBasis"],
                        "notes": f"{month} {r['employee_name']}工资及单位社保",
                    },
                })
            admin_cost = money(admin_cost + r_cost - allocated_cost)
                
            net_salary_total += money(r["net_salary"])
            tax_total += money(r["personal_tax"])
            ss_total += money(r["personal_social_security"]) + money(r["company_social_security"])
            
        admin_cost = money(admin_cost)
        net_salary_total = money(net_salary_total)
        tax_total = money(tax_total)
        ss_total = money(ss_total)
        
        # Build balanced voucher lines
        debit_lines = list(rd_debit_lines)
        if admin_cost > ZERO:
            debit_lines.append({
                "accountCode": "5609",
                "accountName": "管理费用-福利费",
                "amount": admin_cost
            })
            
        credit_lines = []
        if net_salary_total > ZERO:
            credit_lines.append({
                "accountCode": "2211",
                "accountName": "应付职工薪酬",
                "amount": net_salary_total
            })
        if tax_total > ZERO:
            credit_lines.append({
                "accountCode": "2221",
                "accountName": "应交税费",
                "amount": tax_total
            })
        if ss_total > ZERO:
            credit_lines.append({
                "accountCode": "2211",
                "accountName": "应付职工薪酬",
                "amount": ss_total
            })
            
        # Post to ledger via save_entry
        total_voucher_sum = money(sum((money(line["amount"]) for line in rd_debit_lines), ZERO) + admin_cost)
        year, m = map(int, month.split("-"))
        last_day = calendar.monthrange(year, m)[1]
        occur_date = f"{month}-{last_day:02d}"
        
        voucher_payload = {
            "direction": "expense",
            "amount": total_voucher_sum,
            "title": f"{month}月计提员工工资及社保",
            "category": "人力成本",
            "accountCode": "5621",
            "accountName": "研发费用",
            "occurDate": occur_date,
            "sourceType": SOURCE_ACCRUAL,
            "dedupFingerprint": _fingerprint("salary-accrual", sheet["id"]),
            "payer": "company_account",
            "voucherLines": {
                "debit": debit_lines,
                "credit": credit_lines
            },
            "voucherStatus": "confirmed",
            "voucherAuthority": "system",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
            )
        except Exception as exc:
            # 两段式写入的崩溃恢复：凭证已建但工资表状态未更新时，重试会撞来源唯一性
            # 校验（"该来源业务已入账"）。此时按指纹找回已建凭证继续走状态更新。
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(
                company_id, SOURCE_ACCRUAL, _fingerprint("salary-accrual", sheet["id"])
            )
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}
        
        # Update sheet status and link voucher
        now = _now_iso()
        next_status = "accrued" if posted_entry["voucherStatus"] == "posted" else "pending_review"
        await conn.execute(
            "UPDATE salary_sheets SET status = ?, accrual_voucher_id = ?, updated_at = ? WHERE id = ?",
            (next_status, posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()
        
        sheet["status"] = next_status
        sheet["accrual_voucher_id"] = posted_entry["id"]
        sheet["records"] = records
        return sheet
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
        sheet = dict(sheet_row)
        if sheet["status"] != "accrued":
            raise ValueError("Sheet must be accrued before it can be paid")
            
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
            "accountCode": "2211",
            "accountName": "应付职工薪酬",
            "occurDate": occur_date,
            "sourceType": SOURCE_PAY,
            "dedupFingerprint": _fingerprint("salary-pay", sheet["id"]),
            "payer": "company_account",
            "voucherLines": {
                "debit": [{
                    "accountCode": "2211",
                    "accountName": "应付职工薪酬",
                    "amount": net_salary_total
                }],
                "credit": [{
                    "accountCode": "1002",
                    "accountName": "银行存款",
                    "amount": net_salary_total
                }]
            },
            "voucherStatus": "confirmed",
            "voucherAuthority": "system",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
            )
        except Exception as exc:
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(
                company_id, SOURCE_PAY, _fingerprint("salary-pay", sheet["id"])
            )
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}
        
        # Update sheet status and link payment voucher
        now = _now_iso()
        await conn.execute(
            "UPDATE salary_sheets SET status = 'paid', payment_voucher_id = ?, updated_at = ? WHERE id = ?",
            (posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()
        
        sheet["status"] = "paid"
        sheet["payment_voucher_id"] = posted_entry["id"]
        sheet["records"] = records
        return sheet
    finally:
        await conn.close()

async def pay_payroll_liability(company_id: str, user_id: str, month: str, kind: str) -> Dict[str, Any]:
    """缴纳社保（公司+个人部分）或个税，生成清偿挂账的凭证。

    计提分录贷方挂 2211（净工资+社保总额）与 2221（个税）：
    - kind=social_security → 借 2211 应付职工薪酬 / 贷 1002 银行存款（社保总额）
    - kind=tax             → 借 2221 应交税费 / 贷 1002 银行存款（个税总额）
    occur_date 用实际缴纳日（今天），与真实银行流出对齐。
    """
    await _ensure_db_ready()
    if kind not in ("social_security", "tax"):
        raise ValueError("kind 必须是 social_security 或 tax")
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM salary_sheets WHERE company_id = ? AND month = ?",
            (company_id, month),
        )
        sheet_row = await cursor.fetchone()
        if not sheet_row:
            raise ValueError("Salary sheet does not exist")
        sheet = dict(sheet_row)
        if sheet["status"] not in ("accrued", "paid"):
            raise ValueError("工资表计提后才能缴纳社保/个税")

        column = "ss_paid_voucher_id" if kind == "social_security" else "tax_paid_voucher_id"
        if sheet.get(column):
            raise ValueError("本月该项已缴纳，请勿重复操作")

        cursor_recs = await conn.execute(
            "SELECT * FROM salary_records WHERE sheet_id = ?",
            (sheet["id"],),
        )
        records = [dict(r) for r in await cursor_recs.fetchall()]

        if kind == "social_security":
            amount = money(sum(
                (money(r["personal_social_security"]) + money(r["company_social_security"]) for r in records),
                ZERO,
            ))
            title = f"缴纳{month}月社保公积金（单位+个人）"
            source_type, fp_kind = SOURCE_SS, "salary-ss"
            debit_account = ("2211", "应付职工薪酬")
        else:
            amount = money(sum((money(r["personal_tax"]) for r in records), ZERO))
            title = f"缴纳{month}月个人所得税"
            source_type, fp_kind = SOURCE_TAX, "salary-tax"
            debit_account = ("2221", "应交税费")

        if amount <= ZERO:
            raise ValueError("本月该项金额为 0，无需缴纳")

        voucher_payload = {
            "direction": "expense",
            "amount": amount,
            "title": title,
            "category": "人力成本",
            "accountCode": debit_account[0],
            "accountName": debit_account[1],
            "occurDate": datetime.now(timezone.utc).date().isoformat(),
            "sourceType": source_type,
            "dedupFingerprint": _fingerprint(fp_kind, sheet["id"]),
            "payer": "company_account",
            "voucherLines": {
                "debit": [{
                    "accountCode": debit_account[0],
                    "accountName": debit_account[1],
                    "amount": amount
                }],
                "credit": [{
                    "accountCode": "1002",
                    "accountName": "银行存款",
                    "amount": amount
                }]
            },
            "voucherStatus": "confirmed",
            "voucherAuthority": "system",
        }

        try:
            posted_entry = await save_entry(
                voucher_payload,
                user_id=user_id,
                company_id=company_id,
                confirm=True,
            )
        except Exception as exc:
            from core.errors import AccountingError

            if not (isinstance(exc, AccountingError) and getattr(exc, "code", "") == "DUPLICATE_SOURCE"):
                raise
            recovered = await _find_entry_by_source(company_id, source_type, _fingerprint(fp_kind, sheet["id"]))
            if not recovered:
                raise
            posted_entry = {"id": recovered["id"], "voucherStatus": recovered["status"]}

        now = _now_iso()
        await conn.execute(
            f"UPDATE salary_sheets SET {column} = ?, updated_at = ? WHERE id = ?",
            (posted_entry["id"], now, sheet["id"]),
        )
        await conn.commit()

        from services.audit_service import log_action

        await log_action(company_id, user_id, f"salary_{kind}_paid", "salary_sheet", sheet["id"], {"amount": money_text(amount)})
        sheet[column] = posted_entry["id"]
        sheet["records"] = records
        return sheet
    finally:
        await conn.close()
