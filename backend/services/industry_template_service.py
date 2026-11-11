# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Backend-owned industry templates.

The catalog is shipped as a seed file and then persisted in
``industry_templates``. Administrators can version templates without changing
frontend code. A company stores only the template code/version that was applied;
its already seeded accounts remain under company control.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from db.models import Company, IndustryTemplate
from db.session import session_scope


SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "industry_templates.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountPreset(BaseModel):
    code: str = Field(pattern=r"^\d{7,9}$")
    name: str = Field(min_length=1, max_length=255)
    parent_code: str = Field(alias="parentCode", pattern=r"^\d{4,9}$")
    type: str = Field(default="", max_length=64)
    category: str = Field(default="", max_length=64)
    direction: str = Field(default="", pattern=r"^(|debit|credit)$")
    is_leaf: bool = Field(default=True, alias="isLeaf")

    @model_validator(mode="after")
    def validate_code_tree(self):
        if not self.code.startswith(self.parent_code):
            raise ValueError("科目编码必须以父级科目编码开头")
        return self


class IndustryTemplateConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    account_presets: list[AccountPreset] = Field(
        default_factory=list, alias="accountPresets"
    )
    modules: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("modules", "notes")
    @classmethod
    def normalize_text_list(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


class IndustryTemplateValues(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,95}$")
    name: str = Field(min_length=1, max_length=128)
    group_code: str = Field(default="", max_length=64, alias="groupCode")
    group_name: str = Field(default="", max_length=128, alias="groupName")
    description: str = Field(default="", max_length=2000)
    restricted_default: bool = Field(default=False, alias="restrictedDefault")
    hidden: bool = False
    enabled: bool = True
    sort_order: int = Field(default=0, alias="sortOrder")
    config: IndustryTemplateConfig = Field(default_factory=IndustryTemplateConfig)

    @field_validator("code", "name", "group_code", "group_name", "description", mode="before")
    @classmethod
    def strip_text_fields(cls, value: Any) -> str:
        return str(value or "").strip()


def _load_seed_items() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _normalize_config(config: Any) -> dict[str, Any]:
    parsed = IndustryTemplateConfig.model_validate(config or {})
    return parsed.model_dump(by_alias=True)


def serialize_template(template: IndustryTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "code": template.code,
        "name": template.name,
        "groupCode": template.group_code,
        "groupName": template.group_name,
        "description": template.description,
        "restrictedDefault": bool(template.restricted_default),
        "hidden": bool(template.hidden),
        "enabled": bool(template.enabled),
        "sortOrder": int(template.sort_order or 0),
        "version": int(template.version or 1),
        "config": _normalize_config(template.config),
        "isSystem": bool(template.is_system),
        "createdBy": template.created_by or "",
        "createdAt": template.created_at.isoformat() if template.created_at else None,
        "updatedAt": template.updated_at.isoformat() if template.updated_at else None,
    }


async def seed_industry_templates(session: AsyncSession) -> None:
    """Insert missing built-ins without overwriting administrator changes."""
    items = _load_seed_items()
    if not items:
        return
    existing = set(
        (
            await session.execute(
                select(IndustryTemplate.code).where(
                    IndustryTemplate.code.in_([str(item.get("code") or "") for item in items])
                )
            )
        ).scalars()
    )
    created = False
    for item in items:
        code = str(item.get("code") or "").strip()
        if not code or code in existing:
            continue
        config = _normalize_config(item.get("config"))
        session.add(
            IndustryTemplate(
                code=code,
                name=str(item.get("name") or code),
                group_code=str(item.get("groupCode") or ""),
                group_name=str(item.get("groupName") or ""),
                description=str(item.get("description") or ""),
                restricted_default=bool(item.get("restrictedDefault")),
                hidden=bool(item.get("hidden")),
                enabled=bool(item.get("enabled", True)),
                sort_order=int(item.get("sortOrder") or 0),
                version=1,
                config=config,
                is_system=bool(item.get("isSystem", True)),
            )
        )
        existing.add(code)
        created = True
    if created:
        await session.flush()


async def list_templates(
    session: AsyncSession,
    *,
    include_hidden: bool = True,
    include_disabled: bool = False,
    query: str = "",
) -> list[IndustryTemplate]:
    await seed_industry_templates(session)
    statement = select(IndustryTemplate)
    if not include_hidden:
        statement = statement.where(IndustryTemplate.hidden.is_(False))
    if not include_disabled:
        statement = statement.where(IndustryTemplate.enabled.is_(True))
    keyword = query.strip()
    if keyword:
        like = f"%{keyword}%"
        statement = statement.where(
            or_(
                IndustryTemplate.code.ilike(like),
                IndustryTemplate.name.ilike(like),
                IndustryTemplate.group_name.ilike(like),
            )
        )
    statement = statement.order_by(
        IndustryTemplate.sort_order.asc(),
        IndustryTemplate.group_name.asc(),
        IndustryTemplate.name.asc(),
    )
    return list((await session.execute(statement)).scalars().all())


async def find_template(
    session: AsyncSession,
    *,
    code: str = "",
    name: str = "",
    require_enabled: bool = True,
) -> IndustryTemplate | None:
    await seed_industry_templates(session)
    statement = select(IndustryTemplate)
    if code:
        statement = statement.where(IndustryTemplate.code == code)
    elif name:
        statement = statement.where(IndustryTemplate.name == name)
    else:
        return None
    if require_enabled:
        statement = statement.where(IndustryTemplate.enabled.is_(True))
    return (await session.execute(statement)).scalars().first()


async def normalize_company_industry_payload(
    payload: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonicalize company industry fields against the backend catalog."""
    normalized = dict(payload)
    selected_code = str(
        normalized.get("industryCode")
        or normalized.get("industryTemplateCode")
        or ""
    ).strip()
    selected_name = str(normalized.get("industry") or "").strip()
    if not selected_code and not selected_name:
        if previous is not None and (
            "industry" in payload or "industryCode" in payload
        ):
            normalized["industry"] = ""
            normalized["industryCode"] = ""
            normalized.pop("industryTemplateVersion", None)
            normalized.pop("industryTemplateAppliedAt", None)
        return normalized

    async with session_scope() as session:
        template = await find_template(
            session,
            code=selected_code,
            name=selected_name if not selected_code else "",
        )
        if template is None:
            if selected_code:
                raise AccountingError(
                    "INVALID_INDUSTRY_TEMPLATE",
                    "所选行业模板不存在或已停用，请刷新后重新选择",
                    422,
                    {"industryCode": selected_code},
                )
            # Legacy free-text values remain readable during the transition.
            normalized.pop("industryTemplateCode", None)
            normalized["industryCode"] = ""
            return normalized

        previous_code = str((previous or {}).get("industryCode") or "")
        previous_version = int((previous or {}).get("industryTemplateVersion") or 0)
        normalized["industry"] = template.name
        normalized["industryCode"] = template.code
        normalized["industryTemplateVersion"] = int(template.version)
        if (
            previous_code != template.code
            or previous_version != int(template.version)
            or not (previous or {}).get("industryTemplateAppliedAt")
        ):
            normalized["industryTemplateAppliedAt"] = _now().isoformat()
        if normalized.get("restrictedIndustry") is None:
            normalized["restrictedIndustry"] = bool(template.restricted_default)
        normalized.pop("industryTemplateCode", None)
        return normalized


async def template_for_company_in_session(
    session: AsyncSession, company_id: str
) -> IndustryTemplate | None:
    company = await session.get(Company, company_id)
    if company is None:
        return None
    payload = company.payload or {}
    code = str(payload.get("industryCode") or "")
    name = str(payload.get("industry") or "")
    return await find_template(
        session,
        code=code,
        name=name if not code else "",
        require_enabled=False,
    )


async def template_for_company(company_id: str) -> IndustryTemplate | None:
    async with session_scope() as session:
        template = await template_for_company_in_session(session, company_id)
        if template is None:
            return None
        # Detach a plain snapshot so callers do not retain a closed session.
        return IndustryTemplate(
            id=template.id,
            code=template.code,
            name=template.name,
            group_code=template.group_code,
            group_name=template.group_name,
            description=template.description,
            restricted_default=template.restricted_default,
            hidden=template.hidden,
            enabled=template.enabled,
            sort_order=template.sort_order,
            version=template.version,
            config=dict(template.config or {}),
            is_system=template.is_system,
            created_by=template.created_by,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )


async def create_template(
    session: AsyncSession, values: IndustryTemplateValues, *, user_id: str
) -> IndustryTemplate:
    await seed_industry_templates(session)
    duplicate = (
        await session.execute(
            select(IndustryTemplate.id).where(IndustryTemplate.code == values.code)
        )
    ).scalar_one_or_none()
    if duplicate:
        raise ValueError("行业模板编码已存在")
    template = IndustryTemplate(
        code=values.code,
        name=values.name.strip(),
        group_code=values.group_code.strip(),
        group_name=values.group_name.strip(),
        description=values.description.strip(),
        restricted_default=values.restricted_default,
        hidden=values.hidden,
        enabled=values.enabled,
        sort_order=values.sort_order,
        version=1,
        config=values.config.model_dump(by_alias=True),
        is_system=False,
        created_by=user_id,
    )
    session.add(template)
    await session.flush()
    return template


async def update_template(
    session: AsyncSession,
    template_id: str,
    values: IndustryTemplateValues,
    *,
    user_id: str,
) -> IndustryTemplate | None:
    template = await session.get(IndustryTemplate, template_id)
    if template is None:
        return None
    if template.code != values.code:
        # code 是企业保存的稳定外键；允许修改会让历史账套失去模板映射。
        raise ValueError("行业模板编码创建后不可修改")
    template.name = values.name.strip()
    template.group_code = values.group_code.strip()
    template.group_name = values.group_name.strip()
    template.description = values.description.strip()
    template.restricted_default = values.restricted_default
    template.hidden = values.hidden
    template.enabled = values.enabled
    template.sort_order = values.sort_order
    template.config = values.config.model_dump(by_alias=True)
    template.version = int(template.version or 1) + 1
    template.updated_at = _now()
    if not template.created_by:
        template.created_by = user_id
    await session.flush()
    return template


def account_presets(template: IndustryTemplate | None) -> Iterable[dict[str, Any]]:
    if template is None:
        return ()
    config = _normalize_config(template.config)
    return tuple(config.get("accountPresets") or ())
