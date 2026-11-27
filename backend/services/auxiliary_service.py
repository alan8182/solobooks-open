# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""辅助核算：主数据 CRUD + 凭证行 auxiliary 的服务端收口（P0 WP2）。

三条硬规则（与 P0 计划一致）：
1. **行级仍存 JSON**（``journal_lines.auxiliary``），不建行级子表——管道已全通
   （透传→落库→序列化→红冲复制），加子表只会引入双写一致性负担。
2. **id 引用主数据，name 冗余显示名**：主数据改名/停用后历史凭证显示不断裂。
   name 一律由服务端按 id 回填，客户端传的 name 直接丢弃（不信客户端行数据）。
3. **删除只允许未被引用的单位**：被引用只能停用，避免历史凭证变成悬空 id。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.chart_policy import auxiliary_kinds_for_account
from core.errors import AccountingError
from db.models import Department, Employee, JournalEntry, JournalLine, Partner, Project, RdSalaryAllocation
from db.session import session_scope
from services.account_mapper import EMPLOYEE_PAYER_PREFIX, PAYER_BOSS, normalize_payer

KINDS = ("partner", "department", "project", "employee")
MODELS = {"partner": Partner, "department": Department, "project": Project, "employee": Employee}
NAME_FIELD = {"partner": "name", "department": "name", "project": "name", "employee": "name"}
ID_KEY = {
    "partner": "partner_id",
    "department": "department_id",
    "project": "project_id",
    "employee": "employee_id",
}
NAME_KEY = {
    "partner": "partner_name",
    "department": "department_name",
    "project": "project_name",
    "employee": "employee_name",
}
CODE_PREFIX = {"customer": "C", "supplier": "S", "both": "B", "person": "P"}
# 个人（股东/员工）不是客户也不是供应商，单独一类；三处校验共用，避免各写一份漂移
PARTNER_TYPES = ("customer", "supplier", "both", "person")
POSTED_STATUSES = ("posted", "reversed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_clause(model: Any, active: bool = True) -> Any:
    if model is Employee:
        return Employee.is_active == (1 if active else 0)
    return model.is_active.is_(active)


def _iso_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("（", "(").replace("）", ")").lower()


def _serialize(model: Any) -> dict[str, Any]:
    data = {
        "id": model.id,
        "companyId": model.company_id,
        "code": model.code or "",
        "name": model.name,
        "isActive": bool(model.is_active),
        "createdAt": _iso_text(model.created_at),
        "updatedAt": _iso_text(model.updated_at),
    }
    if isinstance(model, Partner):
        data.update(
            {
                "type": model.type,
                "taxId": model.tax_id,
                "contact": model.contact,
                "phone": model.phone,
                "address": model.address,
                "mnemonic": model.mnemonic,
            }
        )
    elif isinstance(model, Employee):
        data.update(
            {
                "department": model.department or "",
                "isOwner": bool(model.is_owner),
            }
        )
    return data


# ── 主数据 CRUD ───────────────────────────────────────────────────────────────


async def list_master(
    company_id: str,
    kind: str,
    *,
    keyword: str = "",
    include_inactive: bool = False,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    model = _model(kind)
    conditions = [model.company_id == company_id]
    if not include_inactive:
        conditions.append(_active_clause(model, True))
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        fields = [model.name.like(like), model.code.like(like)]
        if isinstance(model, Partner):
            fields.extend([Partner.mnemonic.like(like), Partner.tax_id.like(like)])
        elif isinstance(model, Employee):
            fields.append(Employee.department.like(like))
        from sqlalchemy import or_

        conditions.append(or_(*fields))
    page = max(1, int(page or 1))
    page_size = min(500, max(1, int(page_size or 100)))
    async with session_scope() as session:
        total = (
            await session.execute(select(func.count()).select_from(model).where(*conditions))
        ).scalar_one()
        rows = (
            await session.execute(
                select(model)
                .where(*conditions)
                .order_by(model.code, model.name)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    return {"items": [_serialize(row) for row in rows], "total": int(total or 0), "page": page, "pageSize": page_size}


def _model(kind: str) -> Any:
    if kind not in MODELS:
        raise AccountingError("INVALID_AUXILIARY_KIND", f"不支持的辅助核算维度：{kind}", 422)
    return MODELS[kind]


async def _next_code(session: AsyncSession, company_id: str, model: Any, prefix: str) -> str:
    existing = {
        str(code).strip()
        for code in (
            await session.execute(select(model.code).where(model.company_id == company_id))
        ).scalars().all()
        if str(code or "").strip()
    }
    index = 1
    while True:
        candidate = f"{prefix}{index:04d}"
        if candidate not in existing:
            return candidate
        index += 1


async def create_master(
    company_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    user_id: str = "",
) -> dict[str, Any]:
    model = _model(kind)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AccountingError("INVALID_AUXILIARY_NAME", "名称不能为空", 422)
    async with session_scope() as session:
        duplicate = (
            await session.execute(
                select(model).where(model.company_id == company_id, model.name == name)
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise AccountingError(
                "DUPLICATE_AUXILIARY", f"「{name}」已存在", 409, {"id": duplicate.id}
            )
        prefix = {"partner": "PT", "department": "DP", "project": "PJ", "employee": "EM"}[kind]
        code = str(payload.get("code") or "").strip()
        if kind == "partner":
            partner_type = str(payload.get("type") or "both").strip()
            if partner_type not in PARTNER_TYPES:
                partner_type = "both"
            prefix = CODE_PREFIX.get(partner_type, "B")
        else:
            partner_type = ""
        if not code:
            code = await _next_code(session, company_id, model, prefix)
        elif (
            await session.execute(
                select(model).where(model.company_id == company_id, model.code == code)
            )
        ).scalar_one_or_none():
            raise AccountingError("DUPLICATE_AUXILIARY_CODE", f"编码 {code} 已存在", 409)

        if kind == "employee":
            now_iso = _now().isoformat()
            row = Employee(
                company_id=company_id,
                code=code,
                name=name,
                department=str(payload.get("department") or "").strip(),
                is_owner=1 if payload.get("isOwner") else 0,
                is_active=1,
                created_at=now_iso,
                updated_at=now_iso,
            )
        else:
            row = model(company_id=company_id, code=code, name=name)
            if kind == "partner":
                row.type = partner_type
                row.tax_id = str(payload.get("taxId") or payload.get("tax_id") or "").strip()
                row.contact = str(payload.get("contact") or "").strip()
                row.phone = str(payload.get("phone") or "").strip()
                row.address = str(payload.get("address") or "").strip()
                row.mnemonic = str(payload.get("mnemonic") or _mnemonic(name)).strip()
        session.add(row)
        await session.flush()
        result = _serialize(row)
    await _audit(company_id, user_id, "create", kind, result["id"], {"name": name})
    return result


async def update_master(
    company_id: str,
    kind: str,
    item_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = "",
) -> dict[str, Any]:
    model = _model(kind)
    async with session_scope() as session:
        row = (
            await session.execute(
                select(model).where(model.company_id == company_id, model.id == item_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AccountingError("AUXILIARY_NOT_FOUND", "辅助核算档案不存在", 404)
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise AccountingError("INVALID_AUXILIARY_NAME", "名称不能为空", 422)
            if name != row.name:
                duplicate = (
                    await session.execute(
                        select(model).where(
                            model.company_id == company_id,
                            model.name == name,
                            model.id != item_id,
                        )
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    raise AccountingError("DUPLICATE_AUXILIARY", f"「{name}」已存在", 409)
                row.name = name
        if "code" in payload:
            code = str(payload.get("code") or "").strip()
            if code and code != row.code:
                duplicate = (
                    await session.execute(
                        select(model).where(
                            model.company_id == company_id,
                            model.code == code,
                            model.id != item_id,
                        )
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    raise AccountingError("DUPLICATE_AUXILIARY_CODE", f"编码 {code} 已存在", 409)
                row.code = code
        if isinstance(row, Partner):
            if "type" in payload and payload.get("type") in PARTNER_TYPES:
                row.type = str(payload["type"])
            for field, key in (
                ("tax_id", "taxId"),
                ("contact", "contact"),
                ("phone", "phone"),
                ("address", "address"),
                ("mnemonic", "mnemonic"),
            ):
                if key in payload:
                    setattr(row, field, str(payload.get(key) or "").strip())
        elif isinstance(row, Employee):
            if "department" in payload:
                row.department = str(payload.get("department") or "").strip()
            if "isOwner" in payload or "is_owner" in payload:
                row.is_owner = 1 if payload.get("isOwner", payload.get("is_owner")) else 0
        if "isActive" in payload or "is_active" in payload:
            active_val = bool(payload.get("isActive", payload.get("is_active")))
            row.is_active = (1 if active_val else 0) if isinstance(row, Employee) else active_val
        row.updated_at = _now().isoformat() if isinstance(row, Employee) else _now()
        await session.flush()
        result = _serialize(row)
    await _audit(company_id, user_id, "update", kind, item_id, payload)
    return result


async def deactivate_master(company_id: str, kind: str, item_id: str, *, user_id: str = "") -> dict[str, Any]:
    """停用：历史凭证继续显示原名称，新凭证不能再选。"""
    return await update_master(company_id, kind, item_id, {"isActive": False}, user_id=user_id)


async def _employee_salary_references(session: AsyncSession, company_id: str, employee_id: str) -> int:
    """检查员工是否已被工资表或研发人员工时分摊引用，避免误删工资底稿主数据。"""
    refs = int(
        (
            await session.execute(
                select(func.count())
                .select_from(RdSalaryAllocation)
                .where(
                    RdSalaryAllocation.company_id == company_id,
                    RdSalaryAllocation.employee_id == employee_id,
                )
            )
        ).scalar_one()
        or 0
    )
    has_salary_records = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table("salary_records")
    )
    if has_salary_records:
        sr_count = (
            await session.execute(
                text("SELECT COUNT(*) FROM salary_records WHERE employee_id = :eid"),
                {"eid": employee_id},
            )
        ).scalar_one()
        refs += int(sr_count or 0)
    return refs


async def delete_master(company_id: str, kind: str, item_id: str, *, user_id: str = "") -> dict[str, Any]:
    """删除：仅允许未被任何凭证行（及员工工资/研发记录）引用。被引用只能停用。"""
    model = _model(kind)
    async with session_scope() as session:
        row = (
            await session.execute(
                select(model).where(model.company_id == company_id, model.id == item_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AccountingError("AUXILIARY_NOT_FOUND", "辅助核算档案不存在", 404)
        references = await count_references(session, company_id, kind, item_id)
        if kind == "employee":
            references += await _employee_salary_references(session, company_id, item_id)
        if references:
            raise AccountingError(
                "AUXILIARY_IN_USE",
                f"该档案已被 {references} 条业务/凭证记录引用，只能停用不能删除",
                409,
                {"references": references},
            )
        await session.delete(row)
        await session.flush()
    await _audit(company_id, user_id, "delete", kind, item_id, {})
    return {"ok": True, "id": item_id}


async def count_references(session: AsyncSession, company_id: str, kind: str, item_id: str) -> int:
    """引用检查：扫本公司的辅助核算 JSON（量级小，Python 过滤比 json_extract 更可移植）。"""
    key = ID_KEY[kind]
    rows = (
        await session.execute(
            select(JournalLine.auxiliary).where(
                JournalLine.company_id == company_id,
                JournalLine.auxiliary.isnot(None),
            )
        )
    ).scalars().all()
    return sum(1 for aux in rows if isinstance(aux, dict) and aux.get(key) == item_id)


async def _audit(company_id: str, user_id: str, action: str, kind: str, item_id: str, detail: dict[str, Any]) -> None:
    if not user_id:
        return
    try:
        from services.audit_service import log_action

        await log_action(company_id, user_id, action, f"auxiliary_{kind}", item_id, detail)
    except Exception:
        pass


def _mnemonic(name: str) -> str:
    """助记码：中文取拼音首字母不可行（无依赖），退化为名称首字 + 去符号前缀。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", name)
    return cleaned[:8]


# ── 凭证行 auxiliary 收口 ─────────────────────────────────────────────────────


async def normalize_line_auxiliary(
    session: AsyncSession,
    company_id: str,
    account_code: str,
    auxiliary: dict[str, Any] | None,
) -> dict[str, Any]:
    """校验并规范化单行 auxiliary：id 必须存在且未停用，name 由服务端回填。

    - 只保留该科目允许的维度（白名单见 ``core.chart_policy``），多余维度直接丢弃，
      避免客户端把"部门"挂到应收账款这种无意义组合上。
    - 只有 name 没有 id（历史/期初导入的旧格式）时按名称精确匹配一次，
      命中补 id，未命中保留纯文本——不强制建档，兼容存量。
    """
    if not auxiliary or not isinstance(auxiliary, dict):
        return {}
    allowed = auxiliary_kinds_for_account(account_code)
    output: dict[str, Any] = {}
    for kind in KINDS:
        if kind not in allowed:
            continue
        item_id = str(auxiliary.get(ID_KEY[kind]) or "").strip()
        raw_name = str(auxiliary.get(NAME_KEY[kind]) or "").strip()
        if not item_id:
            if not raw_name:
                continue
            matched = await _match_by_name(session, company_id, kind, raw_name)
            if matched is None:
                output[NAME_KEY[kind]] = raw_name
                continue
            item_id = matched.id
        model = MODELS[kind]
        row = (
            await session.execute(select(model).where(model.company_id == company_id, model.id == item_id))
        ).scalar_one_or_none()
        if row is None:
            raise AccountingError(
                "INVALID_AUXILIARY",
                f"辅助核算项不存在：{kind}={item_id}",
                422,
                {"kind": kind, "id": item_id},
            )
        if not row.is_active:
            raise AccountingError(
                "INVALID_AUXILIARY",
                f"辅助核算项「{row.name}」已停用，不能用于新凭证",
                422,
                {"kind": kind, "id": item_id, "name": row.name},
            )
        output[ID_KEY[kind]] = row.id
        output[NAME_KEY[kind]] = row.name
    return output


async def _match_by_name(session: AsyncSession, company_id: str, kind: str, name: str) -> Any | None:
    model = MODELS[kind]
    rows = (
        await session.execute(
            select(model).where(model.company_id == company_id, _active_clause(model, True))
        )
    ).scalars().all()
    target = _normalize_name(name)
    for row in rows:
        if _normalize_name(row.name) == target:
            return row
    return None


def attach_auxiliary_to_voucher(voucher: dict[str, Any], auxiliary: dict[str, str | None]) -> dict[str, Any]:
    """把辅助核算 id 挂到允许的科目行上（白名单见 core.chart_policy）。

    客户端只传 id；name 由服务端在落库时按主数据回填（accounting_core._add_lines），
    这里不复制任何客户端文案。``confirm`` 与 Agent 动作入账共用。
    """
    requested = {key: value for key, value in auxiliary.items() if value}
    if not requested:
        return voucher
    output: dict[str, Any] = {}
    for side in ("debit", "credit"):
        lines = []
        for line in voucher.get(side) or []:
            allowed = auxiliary_kinds_for_account(str(line.get("accountCode") or ""))
            matched = {key: value for key, value in requested.items() if key.removesuffix("_id") in allowed}
            lines.append({**line, "auxiliary": {**(line.get("auxiliary") or {}), **matched}} if matched else line)
        output[side] = lines
    return output


async def resolve_entry_employee(
    session: AsyncSession,
    company_id: str,
    *,
    payer: Any,
) -> dict[str, str] | None:
    """员工垫付（``employee_personal:<姓名>``）同步匹配在职员工名册，补挂 employee_id。"""
    normalized = normalize_payer(payer)
    if not normalized.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        return None
    name = normalized.split(":", 1)[1].strip()
    if not name:
        return None
    matched = await _match_by_name(session, company_id, "employee", name)
    if matched is None:
        return None
    return {"id": str(matched.id), "name": str(matched.name)}


async def resolve_auxiliary_options(company_id: str, keyword: str = "", limit: int = 20) -> dict[str, Any]:
    """录一笔账时的远程搜索下拉（三个维度一次返回）。"""
    output: dict[str, Any] = {}
    for kind in KINDS:
        result = await list_master(company_id, kind, keyword=keyword, page_size=limit)
        output[f"{kind}s"] = result["items"]
    return output


# ── 发票导入自动建档 ─────────────────────────────────────────────────────────


async def ensure_partners_for_invoice_rows(
    company_id: str,
    user_id: str,
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, str] | None]:
    """按发票的对方名称匹配/创建往来单位，返回与 rows 对齐的 [{id,name}]。

    客户/供应商合一：方向只影响新建时的 type（income→customer / expense→supplier），
    已存在则原样复用，不改既有 type——一个单位既客户又供应商是常态。
    """
    results: list[dict[str, str] | None] = []
    async with session_scope() as session:
        partners = (
            await session.execute(select(Partner).where(Partner.company_id == company_id))
        ).scalars().all()
        by_name = {_normalize_name(row.name): row for row in partners}
        for row in rows:
            direction = str(row.get("direction") or "expense")
            name = str(
                (row.get("buyer_name") if direction == "income" else row.get("seller_name")) or ""
            ).strip()
            if not name:
                results.append(None)
                continue
            key = _normalize_name(name)
            partner = by_name.get(key)
            if partner is None:
                partner_type = "customer" if direction == "income" else "supplier"
                code = await _next_code(session, company_id, Partner, CODE_PREFIX[partner_type])
                partner = Partner(
                    company_id=company_id,
                    code=code,
                    name=name,
                    type=partner_type,
                    mnemonic=_mnemonic(name),
                )
                session.add(partner)
                await session.flush()
                by_name[key] = partner
            results.append({"id": partner.id, "name": partner.name})
        await session.flush()
    if user_id:
        created = sum(1 for item in results if item)
        if created:
            await _audit(company_id, user_id, "auto_create", "partner", "", {"count": created})
    return results


# ── 往来单位余额（核算项目余额表的数据源）────────────────────────────────────


async def ensure_partner_by_name(
    company_id: str,
    user_id: str,
    name: str,
    *,
    partner_type: str = "both",
) -> dict[str, str] | None:
    """按名称取往来单位，不存在则建档。返回 ``{"id","name"}``。"""
    target = str(name or "").strip()
    if not target:
        return None
    async with session_scope() as session:
        rows = (
            await session.execute(select(Partner).where(Partner.company_id == company_id))
        ).scalars().all()
        normalized = _normalize_name(target)
        for row in rows:
            if _normalize_name(row.name) == normalized:
                return {"id": row.id, "name": row.name}
        resolved_type = partner_type if partner_type in ("customer", "supplier", "both") else "both"
        code = await _next_code(session, company_id, Partner, CODE_PREFIX[resolved_type])
        partner = Partner(
            company_id=company_id,
            code=code,
            name=target,
            type=resolved_type,
            mnemonic=_mnemonic(target),
        )
        session.add(partner)
        await session.flush()
        result = {"id": partner.id, "name": partner.name}
    if user_id:
        await _audit(company_id, user_id, "auto_create", "partner", result["id"], {"name": target})
    return result


BOSS_PARTNER_CODE = "P-BOSS"
BOSS_PARTNER_FALLBACK_NAME = "股东"


async def _boss_display_name(session: AsyncSession, company_id: str) -> str:
    """老板姓名：优先员工档案里 ``is_owner=1`` 的人，没有档案就叫「股东」。

    员工表由工资模块用裸 SQL 建（salary_service._ensure_schema），测试库或
    尚未启用工资模块的账套里可能不存在。这里先探表再查，任何异常都退回
    「股东」——建账不能因为取不到老板姓名而失败。
    """
    try:
        # 只探表不够：老库的 employees 可能缺 is_owner/is_active 列，查询报错会
        # 毒化 PostgreSQL 事务（异常被吞掉也没用），所以连列一起确认。
        columns = await session.run_sync(
            lambda conn: (
                {col["name"] for col in inspect(conn).get_columns("employees")}
                if inspect(conn).has_table("employees")
                else set()
            )
        )
        if {"name", "is_owner", "is_active"} <= columns:
            row = (
                await session.execute(
                    text(
                        "SELECT name FROM employees WHERE company_id = :company_id "
                        "AND is_owner = 1 AND is_active = 1 ORDER BY name LIMIT 1"
                    ),
                    {"company_id": company_id},
                )
            ).first()
            if row and str(row[0] or "").strip():
                return str(row[0]).strip()
    except Exception:
        pass
    return BOSS_PARTNER_FALLBACK_NAME


async def ensure_person_partner(
    session: AsyncSession,
    company_id: str,
    name: str,
    *,
    code: str = "",
    user_id: str = "",
) -> dict[str, str] | None:
    """按姓名取「个人」往来单位，不存在则建档，**复用调用方事务**。

    与 ``ensure_partner_by_name`` 的区别：本函数不自己开 ``session_scope``，
    供记账主流程（``_add_lines``）在既有事务里调用，避免辅助档案与凭证分两个
    事务提交。老板固定编码 ``P-BOSS``，通讯录里改了名字也还能命中，不会因为
    改名再建一个「股东」。
    """
    target = str(name or "").strip()
    code_taken = False
    if code:
        row = (
            await session.execute(
                select(Partner).where(Partner.company_id == company_id, Partner.code == code)
            )
        ).scalar_one_or_none()
        if row is not None and row.is_active:
            return {"id": row.id, "name": row.name}
        # 停用的老板档案不能复用：normalize_line_auxiliary 会以 INACTIVE 拒绝
        # 整张凭证。记下编码已被占用，另建一个启用中的新档。
        code_taken = row is not None
    if not target:
        return None
    rows = (
        await session.execute(select(Partner).where(Partner.company_id == company_id))
    ).scalars().all()
    normalized = _normalize_name(target)
    for row in rows:
        if row.is_active and _normalize_name(row.name) == normalized:
            return {"id": row.id, "name": row.name}
    partner = Partner(
        company_id=company_id,
        code=(
            code
            if code and not code_taken
            else await _next_code(session, company_id, Partner, CODE_PREFIX["person"])
        ),
        name=target,
        type="person",
        mnemonic=_mnemonic(target),
    )
    session.add(partner)
    await session.flush()
    result = {"id": partner.id, "name": partner.name}
    if user_id:
        await _audit(company_id, user_id, "auto_create", "partner", result["id"], {"name": target})
    return result


# ── 银行流水对手方 → 往来单位 ──────────────────────────────────────────────

# 个人姓名：2–8 个汉字（允许少数民族姓名里的间隔点）。公司名会命中下面的
# 组织类关键词，长名/英文名按单位处理，宁可归到「客户/供应商」也不误建个人。
_PERSON_NAME_RE = re.compile(r"^[一-龥][一-龥·]{1,7}$")
_ORG_NAME_KEYWORDS = (
    "公司", "企业", "集团", "银行", "中心", "事务所", "合作社", "工作室", "经营部",
    "商行", "商店", "门店", "协会", "学会", "大学", "学校", "医院",
    "平台", "商贸", "贸易", "实业", "控股", "投资", "基金", "证券", "保险", "金库",
    "税务", "政府", "委员会", "传媒", "文化", "科技", "网络", "信息技术", "人工智能",
    "研究院", "研究所",
)
# 银行附言里的泛称/科目名，不是真实交易对手：建了档案只会让账龄表多出假单位。
_PLACEHOLDER_COUNTERPARTIES = frozenset(
    {"个人", "个人客户", "客户", "供应商", "对方", "对方单位", "未知", "不详", "无",
     "应付利息", "应收利息", "利息", "-", "—"}
)


def counterparty_is_confident(name: Any) -> bool:
    """名字是否明显是「单位」或「个人」——用于从摘要末尾括号里保守地取对手方。

    只有命中组织关键词或纯中文姓名才算数；「(5元)」「(个人客户)」这类
    说明性内容不会被误当成交易对手建档案。
    """
    text = re.sub(r"\s+", "", str(name or ""))
    if not text or text in _PLACEHOLDER_COUNTERPARTIES:
        return False
    return any(keyword in text for keyword in _ORG_NAME_KEYWORDS) or bool(_PERSON_NAME_RE.match(text))


def counterparty_partner_type(name: Any) -> str | None:
    """判断银行对手方该建成什么类型的往来单位；``None`` 表示不该建。

    个人（股东实缴、员工报销）走 ``person``；公司/组织走 ``both``——同一个
    单位既可能是客户也可能是供应商，方向只影响筛选展示，不该在流水阶段猜错。
    """
    text = re.sub(r"\s+", "", str(name or ""))
    if not text or text in _PLACEHOLDER_COUNTERPARTIES:
        return None
    if any(keyword in text for keyword in _ORG_NAME_KEYWORDS):
        return "both"
    if _PERSON_NAME_RE.match(text):
        return "person"
    return "both"


async def ensure_counterparty_partner(
    session: AsyncSession,
    company_id: str,
    name: Any,
    *,
    user_id: str = "",
) -> dict[str, str] | None:
    """按银行对手方取/建往来单位，**复用调用方事务**（与 ensure_person_partner 同理）。

    同名（规范化后）已启用档案直接复用，不因流水里的括号/空格差异重复建档。
    """
    partner_type = counterparty_partner_type(name)
    if partner_type is None:
        return None
    # 建档保留原始（去首尾空格）名称，与发票导入/手工建档的口径一致；
    # 仅判定类型时忽略内部空格，避免英文单位名被粘成一串。
    target = str(name or "").strip()
    rows = (
        await session.execute(select(Partner).where(Partner.company_id == company_id))
    ).scalars().all()
    normalized = _normalize_name(target)
    for row in rows:
        if row.is_active and _normalize_name(row.name) == normalized:
            return {"id": row.id, "name": row.name}
    code = await _next_code(session, company_id, Partner, CODE_PREFIX[partner_type])
    partner = Partner(
        company_id=company_id,
        code=code,
        name=target,
        type=partner_type,
        mnemonic=_mnemonic(target),
    )
    session.add(partner)
    await session.flush()
    result = {"id": partner.id, "name": partner.name}
    if user_id:
        await _audit(company_id, user_id, "auto_create", "partner", result["id"], {"name": target})
    return result


# 这些分类无论付款人字段写的是什么，对手方都是股东本人（股东取现通常走公户）
SHAREHOLDER_CATEGORIES = frozenset(
    {"股东取现", "老板取现", "股东垫付", "老板垫付", "股东垫付报销", "老板垫付报销"}
)


async def resolve_entry_person(
    session: AsyncSession,
    company_id: str,
    *,
    payer: Any,
    category: str = "",
    payment_status: Any = "",
    source: str = "",
    user_id: str = "",
) -> dict[str, str] | None:
    """把一笔业务解析成个人往来单位；只有垫付类业务才有对手方。

    返回 None 的几种情况都是刻意的：
    - 裸 ``employee_personal``（没带姓名）：不知道是谁的垫付，挂一个「员工」
      大杂烩只会让往来余额表多出一个假分组。
    - ``unpaid``：挂账的对手方是供应商/客户，老板垫付是另一笔债权，不能抢
      走发票导入挂上的供应商。

    ``invoice_import`` 不再一刀切跳过：导入行若已按票面销售方挂好往来单位，
    调用方（``_add_lines``）不会覆盖；没挂上的（如老板垫付的腾讯云发票）
    正是要靠这里补出「欠老板的钱」。
    """
    if str(payment_status or "").strip().lower() == "unpaid":
        return None
    normalized = normalize_payer(payer)
    if normalized == PAYER_BOSS or str(category or "").strip() in SHAREHOLDER_CATEGORIES:
        name = await _boss_display_name(session, company_id)
        return await ensure_person_partner(
            session, company_id, name, code=BOSS_PARTNER_CODE, user_id=user_id
        )
    if normalized.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        name = normalized.split(":", 1)[1].strip()
        if name:
            return await ensure_person_partner(session, company_id, name, user_id=user_id)
    return None


async def resolve_payer_partner(
    session: AsyncSession,
    company_id: str,
    payer: Any,
    *,
    user_id: str = "",
) -> dict[str, str] | None:
    """兼容入口：只按付款人解析（历史回填脚本/旧调用方使用）。"""
    return await resolve_entry_person(session, company_id, payer=payer, user_id=user_id)


async def assign_entry_auxiliary(
    company_id: str,
    user_id: str,
    entry_id: str,
    auxiliary: dict[str, Any],
) -> dict[str, Any]:
    """给草稿凭证补挂或清除辅助核算（已过账凭证不可改，只能红冲后重做）。

    - 传入非空 id：校验有效后挂载到科目白名单允许的行；
    - 显式传入 None 或空串：从行上移除该维度（id 与 name 一并清除）。
    """
    from services.accounting_core_service import _entry_lines, record_audit

    to_set = {k: str(v).strip() for k, v in auxiliary.items() if v is not None and str(v).strip()}
    to_clear = {
        k for k, v in auxiliary.items() if k in ID_KEY.values() and (v is None or not str(v).strip())
    }
    if not to_set and not to_clear:
        return {"ok": True, "updatedLines": 0}

    async with session_scope() as session:
        entry = await session.get(JournalEntry, entry_id)
        if entry is None or entry.company_id != company_id:
            raise AccountingError("ENTRY_NOT_FOUND", "凭证不存在", 404)
        if entry.status not in ("draft", "pending_review"):
            raise AccountingError(
                "ENTRY_POSTED_IMMUTABLE",
                "已过账凭证不能修改辅助核算，请红冲后重新制单",
                409,
                {
                    "entryId": entry.id,
                    "occurredOn": entry.occurred_on.isoformat(),
                    "voucherRef": str(entry.voucher_no or entry.summary or "")[:60],
                    "status": entry.status,
                    "entryType": entry.entry_type,
                },
            )
        lines = await _entry_lines(session, entry_id)
        updated = 0
        for line in lines:
            current = dict(line.auxiliary or {})
            next_aux = dict(current)
            for kind in KINDS:
                id_key, name_key = ID_KEY[kind], NAME_KEY[kind]
                if id_key in to_clear:
                    next_aux.pop(id_key, None)
                    next_aux.pop(name_key, None)
            if to_set:
                normalized = await normalize_line_auxiliary(session, company_id, line.account_code, to_set)
                if normalized:
                    next_aux.update(normalized)
            if next_aux != current:
                line.auxiliary = next_aux
                updated += 1
        if updated:
            entry.version += 1
            entry.updated_at = _now()
            await session.flush()
            from services.accounting_core_service import _sync_projection

            await _sync_projection(session, entry)
            await record_audit(
                session,
                company_id=company_id,
                user_id=user_id,
                action="assign_auxiliary",
                entity_type="journal_entry",
                entity_id=entry_id,
                request_id=f"aux-{entry_id}",
                after={"auxiliary": auxiliary, "lines": updated},
                entity_version=entry.version,
            )
    return {"ok": True, "updatedLines": updated}


async def partner_reference_map(company_id: str) -> dict[str, str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Partner.id, Partner.name).where(Partner.company_id == company_id)
            )
        ).all()
    return {str(item_id): str(name) for item_id, name in rows}
