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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.chart_policy import auxiliary_kinds_for_account
from core.errors import AccountingError
from db.models import Department, JournalEntry, JournalLine, Partner, Project
from db.session import session_scope

KINDS = ("partner", "department", "project")
MODELS = {"partner": Partner, "department": Department, "project": Project}
NAME_FIELD = {"partner": "name", "department": "name", "project": "name"}
ID_KEY = {"partner": "partner_id", "department": "department_id", "project": "project_id"}
NAME_KEY = {"partner": "partner_name", "department": "department_name", "project": "project_name"}
CODE_PREFIX = {"customer": "C", "supplier": "S", "both": "B"}
POSTED_STATUSES = ("posted", "reversed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("（", "(").replace("）", ")").lower()


def _serialize(model: Any) -> dict[str, Any]:
    data = {
        "id": model.id,
        "companyId": model.company_id,
        "code": model.code,
        "name": model.name,
        "isActive": bool(model.is_active),
        "createdAt": model.created_at.isoformat() if model.created_at else "",
        "updatedAt": model.updated_at.isoformat() if model.updated_at else "",
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
        conditions.append(model.is_active.is_(True))
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        fields = [model.name.like(like), model.code.like(like)]
        if isinstance(model, Partner):
            fields.extend([Partner.mnemonic.like(like), Partner.tax_id.like(like)])
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
                .order_by(model.code)
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
        str(code)
        for code in (
            await session.execute(select(model.code).where(model.company_id == company_id))
        ).scalars().all()
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
        prefix = "PT" if kind == "partner" else ("DP" if kind == "department" else "PJ")
        code = str(payload.get("code") or "").strip()
        if kind == "partner":
            partner_type = str(payload.get("type") or "both").strip()
            if partner_type not in ("customer", "supplier", "both"):
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
            if "type" in payload and payload.get("type") in ("customer", "supplier", "both"):
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
        if "isActive" in payload or "is_active" in payload:
            row.is_active = bool(payload.get("isActive", payload.get("is_active")))
        row.updated_at = _now()
        await session.flush()
        result = _serialize(row)
    await _audit(company_id, user_id, "update", kind, item_id, payload)
    return result


async def deactivate_master(company_id: str, kind: str, item_id: str, *, user_id: str = "") -> dict[str, Any]:
    """停用：历史凭证继续显示原名称，新凭证不能再选。"""
    return await update_master(company_id, kind, item_id, {"isActive": False}, user_id=user_id)


async def delete_master(company_id: str, kind: str, item_id: str, *, user_id: str = "") -> dict[str, Any]:
    """删除：仅允许未被任何凭证行引用。被引用只能停用。"""
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
        if references:
            raise AccountingError(
                "AUXILIARY_IN_USE",
                f"该档案已被 {references} 条凭证分录引用，只能停用不能删除",
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
            select(model).where(model.company_id == company_id, model.is_active.is_(True))
        )
    ).scalars().all()
    target = _normalize_name(name)
    for row in rows:
        if _normalize_name(row.name) == target:
            return row
    return None


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


async def assign_entry_auxiliary(
    company_id: str,
    user_id: str,
    entry_id: str,
    auxiliary: dict[str, Any],
) -> dict[str, Any]:
    """给草稿凭证补挂辅助核算（已过账凭证不可改，只能红冲后重做）。

    挂载科目仍由 chart_policy 白名单决定：只挂"该科目允许的维度"，
    不会把往来单位挂到管理费用这种无意义组合上。
    """
    from services.accounting_core_service import _entry_lines, record_audit

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
            normalized = await normalize_line_auxiliary(session, company_id, line.account_code, auxiliary)
            if normalized:
                line.auxiliary = {**(line.auxiliary or {}), **normalized}
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
