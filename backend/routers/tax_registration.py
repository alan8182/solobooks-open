# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""税费种认定路由：认定行维护 + 粘贴/截图导入 + 申报节奏预览 + 与征期差异。"""
from __future__ import annotations

import asyncio
import base64
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from deps.auth import AuthContext, require_auth
from core.errors import AccountingError
from services import filing_deadline_service, tax_registration_service
from services.llm import chat_vision_json
from services.zhipu_ocr import resolve_receipt_image_mime, validate_receipt_image_bytes

router = APIRouter(
    prefix="/api/tax-registrations", tags=["tax-registrations"], dependencies=[Depends(require_auth)]
)


class ImportRequest(BaseModel):
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    mode: str = "merge"  # merge | replace
    source: str = "paste"  # paste | manual | template | ocr


class TemplateRequest(BaseModel):
    entityType: Optional[str] = None


RECOGNIZE_PROMPT = """你是中国电子税务局「税费种认定信息」表格的结构化解析器。
只输出 JSON，不要解释。结构：
{"rows":[{"levyItem":"征收项目","levyCategory":"征收品目","levySubItem":"征收子目",
"filingCycle":"月/季/年/次","deadlineText":"申报期限原文","levyRate":"征收率",
"taxRate":"税率或单位税额","mainFlag":"主税/增值税附税","collectionMethod":"自行申报/代扣代缴"}]}

规则：
1. 逐行提取，一行对应表格一行，不合并、不补全、不推断税种；
2. 征收率、税率填小数（0.03、0.0003），页面显示 0 就填 0；带单位的（如 8元/平方米）原样填；
3. filingCycle 只取「月」「季」「年」「次」；
4. 主附税标志、征收代理方式照抄原文；
5. 看不清的字段填空字符串，绝不猜测；
6. 表格外的合计行、按钮文字不要输出。"""


@router.get("")
async def list_registrations(
    include_inactive: bool = False, ctx: AuthContext = Depends(require_auth)
):
    items = await tax_registration_service.list_registrations(
        ctx.company_id, include_inactive=include_inactive
    )
    groups: Dict[str, int] = {}
    for item in items:
        groups[item["returnGroup"]] = groups.get(item["returnGroup"], 0) + 1
    return {
        "items": items,
        "stats": {"total": len(items), "active": len([i for i in items if i["status"] == "active"]), "groups": groups},
    }


@router.post("")
async def create_registration(payload: Dict[str, Any], ctx: AuthContext = Depends(require_auth)):
    try:
        return await tax_registration_service.create_registration(ctx.company_id, ctx.user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{registration_id}")
async def update_registration(
    registration_id: str, payload: Dict[str, Any], ctx: AuthContext = Depends(require_auth)
):
    try:
        return await tax_registration_service.update_registration(
            ctx.company_id, ctx.user_id, registration_id, payload
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{registration_id}/deactivate")
async def deactivate_registration(registration_id: str, ctx: AuthContext = Depends(require_auth)):
    try:
        return await tax_registration_service.deactivate_registration(
            ctx.company_id, ctx.user_id, registration_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/parse")
async def parse_rows(payload: ImportRequest, ctx: AuthContext = Depends(require_auth)):
    """只解析不落库：粘贴/识别结果先给用户确认。"""
    return tax_registration_service.parse_rows(payload.rows)


@router.post("/import")
async def import_registrations(payload: ImportRequest, ctx: AuthContext = Depends(require_auth)):
    try:
        summary = await tax_registration_service.import_registrations(
            ctx.company_id,
            ctx.user_id,
            payload.rows,
            mode=payload.mode if payload.mode in ("merge", "replace") else "merge",
            source=payload.source or "paste",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 导入后立即按新认定重算当年征期：用户改完就不用再点一次「应用到征期」
    refreshed = await filing_deadline_service.refresh_deadlines(ctx.company_id, date.today().year)
    return {**summary, "refresh": refreshed}


@router.post("/template")
async def build_template(payload: Optional[TemplateRequest] = None, ctx: AuthContext = Depends(require_auth)):
    """按主体类型带出默认认定行（不落库，预览后由用户确认导入）。"""
    entity_type = (payload.entityType if payload else None) or None
    if not entity_type:
        from services import company_entity_service

        entity_type = await company_entity_service.get_company_entity_type(ctx.company_id)
    rows = tax_registration_service.build_template(entity_type)
    return {
        "entityType": entity_type,
        "notice": "以下为产品默认值（小规模纳税人口径），正式申报以税局税费种认定表为准。",
        "items": [tax_registration_service.serialize_normalized(row) for row in rows],
        "rows": rows,
    }


@router.get("/preview")
async def preview(year: Optional[int] = None, ctx: AuthContext = Depends(require_auth)):
    return await tax_registration_service.preview_schedule(ctx.company_id, year or date.today().year)


@router.get("/diff")
async def diff(year: Optional[int] = None, ctx: AuthContext = Depends(require_auth)):
    return await tax_registration_service.diff_against_deadlines(ctx.company_id, year or date.today().year)


@router.post("/apply")
async def apply(year: Optional[int] = None, ctx: AuthContext = Depends(require_auth)):
    """按当前认定表重算征期（幂等）。"""
    return await filing_deadline_service.refresh_deadlines(ctx.company_id, year or date.today().year)


@router.post("/recognize")
async def recognize(file: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)):
    """上传税局认定表截图 → 多模态识别成行（仍需用户确认后导入）。"""
    content = await file.read()
    mime = resolve_receipt_image_mime(file.content_type, file.filename)
    try:
        validate_receipt_image_bytes(content, mime)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(status_code=413 if ("超过" in message or "最大" in message) else 415, detail=message) from exc

    data_url = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    try:
        result = await asyncio.to_thread(
            chat_vision_json,
            RECOGNIZE_PROMPT,
            data_url,
            max_tokens=4096,
            scene="tax_registration_ocr",
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
    except AccountingError:
        raise  # 配额超限保持 402，不转 502
    except Exception as exc:  # noqa: BLE001 —— 模型侧异常统一转 502，前端提示改用粘贴
        raise HTTPException(status_code=502, detail=f"识别失败，请改用复制粘贴：{exc}") from exc

    raw_rows = result.get("rows") or result.get("items") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise HTTPException(status_code=422, detail="未识别到认定表行，请换一张更清晰的截图")
    parsed = tax_registration_service.parse_rows([row for row in raw_rows if isinstance(row, dict)])
    return {**parsed, "rawCount": len(raw_rows)}
