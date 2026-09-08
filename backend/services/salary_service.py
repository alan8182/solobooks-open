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

def calculate_initial_social_security(base: Any) -> tuple[Decimal, Decimal]:
    """
    Calculate draft social security amounts.
    Personal: 10.5% (approx 8% pension + 2% medical + 0.5% unemployment)
    Company: 28% (approx 16% pension + 8% medical + 0.5% unemployment + 0.5% injury + 3% housing fund)
    """
    base_amount = money(base, allow_negative=False)
    personal = money(base_amount * rate("0.105"))
    company = money(base_amount * rate("0.28"))
    return personal, company

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
            if r["department"] == "研发部" and total_rate != Decimal("1"):
                raise ValueError(f"研发部员工“{r['employee_name']}”的项目工时比例必须合计100%")
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
            "sourceType": "web",
            "payer": "company_account",
            "voucherLines": {
                "debit": debit_lines,
                "credit": credit_lines
            },
            "voucherStatus": "confirmed",
            "voucherAuthority": "system",
        }
        
        posted_entry = await save_entry(
            voucher_payload,
            user_id=user_id,
            company_id=company_id,
            confirm=True,
        )
        
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

async def pay_salary_sheet(company_id: str, user_id: str, month: str) -> Dict[str, Any]:
    """Create salary payment double-entry voucher and update sheet status."""
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
        year, m = map(int, month.split("-"))
        last_day = calendar.monthrange(year, m)[1]
        occur_date = f"{month}-{last_day:02d}"
        
        voucher_payload = {
            "direction": "expense",
            "amount": net_salary_total,
            "title": f"银行代发{month}月员工工资",
            "category": "人力成本",
            "accountCode": "2211",
            "accountName": "应付职工薪酬",
            "occurDate": occur_date,
            "sourceType": "web",
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
        
        posted_entry = await save_entry(
            voucher_payload,
            user_id=user_id,
            company_id=company_id,
            confirm=True,
        )
        
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
