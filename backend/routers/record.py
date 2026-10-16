# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""V3 记账路由：文字/语音/拍单据/确认"""
import base64
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from deps.auth import AuthContext, require_auth
from models.schemas import (
    RecordTextRequest, RecordTextResponse,
    RecordReceiptResponse,
    RecordBatchRequest, RecordBatchResponse,
    ConfirmRequest, ConfirmResponse,
    EntryUpdateRequest,
    SpeechTextResponse,
    CloudFileRequest,
)
from config import ZHIPU_API_KEY
from services.asr import transcribe_uploaded_audio
from services.llm import _uid, chat_text_json, chat_vision_json
from services.classifier import CLASSIFIER_V3_PROMPT, BATCH_SPLIT_V3_PROMPT
from services.ocr import RECEIPT_FROM_OCR_V3_PROMPT, RECEIPT_VL_V3_PROMPT
from services.zhipu_ocr import (
    resolve_receipt_image_mime,
    validate_receipt_image_bytes,
    zhipu_handwriting_ocr_bytes,
)
from services.account_mapper import get_voucher_hint
from core.money import ZERO, money
from services.tax_rules import compute_vat, get_risk_tags, is_tax_deductible, sanitize_risk_tags
from db.session import get_db_session
from services.accounting_core_service import (
    RD_ACCOUNT_BY_TYPE,
    create_entry,
    delete_draft,
    ensure_tenant,
    get_idempotent_response,
    list_journals,
    save_idempotent_response,
    serialize_journal,
    update_draft,
)

RD_ACCOUNT_NAMES = {
    "personnel": "研发支出-人员人工",
    "direct_input": "研发支出-直接投入",
    "depreciation_amortization": "研发支出-折旧与摊销",
    "design": "研发支出-设计费",
    "testing": "研发支出-试验费",
    "outsourced": "研发支出-委外研发费",
    "other": "研发支出-其他研发费用",
}

router = APIRouter(prefix="/api/record", tags=["record"], dependencies=[Depends(require_auth)])

_MAX_OCR_TEXT_FOR_LLM = 10000
_MAX_RAW_TEXT_RESPONSE = 6000
_FETCH_TIMEOUT_SECONDS = 30.0


def _scalar_or(value, default):
    """
    将 LLM JSON 中的显式 null 转为业务默认值。

    说明：Python 中 ``result.get("merchant", "")`` 在键存在且值为 ``None`` 时仍返回 ``None``，
    会导致 ``RecordReceiptResponse`` 等 Pydantic 模型校验失败。
    """
    return default if value is None else value


def _float_or(value, default=0.0):
    """将 LLM 返回的置信度安全转为展示用 float。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _money_or(value, default=ZERO):
    """将 LLM 金额归一为两位小数 Decimal。"""
    try:
        return money(default if value is None else value)
    except ValueError:
        return money(default)


def _ensure_receipt_dict(raw: object) -> dict:
    """
    拍单据 VL 偶发返回 JSON 数组（如 ``[{...}]``），此处归一为 dict 供字段读取。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
    return {}


def _clean_entry_payload(entry: dict) -> dict:
    """清理历史或模型返回中的旧进项抵扣标签。"""
    data = dict(entry or {})
    data["riskTags"] = sanitize_risk_tags(data.get("riskTags", []))
    return data


async def _fetch_cloud_file(file_url: str) -> tuple[bytes, str]:
    """下载云存储临时文件 URL，返回文件字节和响应 content-type。"""
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            return resp.content, (resp.headers.get("content-type") or "").strip()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"下载云存储文件失败：{e}") from e


from typing import Optional

async def _parse_receipt_from_bytes(
    content: bytes, filename: str, content_type: Optional[str], ctx: AuthContext
) -> RecordReceiptResponse:
    """复用拍单据识别主流程，支持 uploadFile 与云存储 URL 两种入口。"""
    raw_ct = (content_type or "").strip().lower()
    main_ct = raw_ct.split(";")[0] if raw_ct else ""
    if main_ct == "application/pdf" or filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail="当前仅支持图片（PNG/JPG/JPEG/BMP），暂不支持 PDF。",
        )

    mime = resolve_receipt_image_mime(content_type, filename)
    try:
        validate_receipt_image_bytes(content, mime)
    except ValueError as e:
        msg = str(e)
        code = 413 if ("超过" in msg or "最大" in msg) else 415
        raise HTTPException(status_code=code, detail=msg) from e

    # ── 主路径：多模态模型直读图片（OpenAI 协议，qwen3.8-flash 等）────────
    data_url = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    try:
        result = _ensure_receipt_dict(
            chat_vision_json(
                RECEIPT_VL_V3_PROMPT,
                data_url,
                # 4096：DeepSeek 输出偏长，2048 会截断 JSON 导致误入 OCR 降级路径
                max_tokens=4096,
                scene="invoice_ocr",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
            )
        )
        ocr_text = str(result.get("rawText") or "") or result.get("explanation") or ""
    except Exception:
        # ── 降级路径：智谱 OCR 提取文字 + 文本模型结构化 ─────────────────
        try:
            ocr_out = await zhipu_handwriting_ocr_bytes(
                content,
                filename,
                mime,
                scene="invoice_ocr",
                user_id=ctx.user_id,
                company_id=ctx.company_id,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"智谱 OCR 网络错误：{e}") from e

        ocr_text = (ocr_out.get("ocr_text") or "").strip()
        if not ocr_text:
            raise HTTPException(status_code=422, detail="未能识别图片内容，请换一张更清晰的照片。")

        ocr_for_llm = ocr_text if len(ocr_text) <= _MAX_OCR_TEXT_FOR_LLM else ocr_text[:_MAX_OCR_TEXT_FOR_LLM]
        user_msg = f"【文件名】{filename}\n【OCR原始文本】\n{ocr_for_llm}"

        try:
            result = _ensure_receipt_dict(
                chat_text_json(
                    RECEIPT_FROM_OCR_V3_PROMPT,
                    user_msg,
                    max_tokens=1536,
                    temperature=0.2,
                    scene="invoice_ocr",
                    user_id=ctx.user_id,
                    company_id=ctx.company_id,
                )
            )
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=502, detail=f"记账模型解析失败：{e}") from e

    raw_out = ocr_text if len(ocr_text) <= _MAX_RAW_TEXT_RESPONSE else ocr_text[:_MAX_RAW_TEXT_RESPONSE]
    return RecordReceiptResponse(
        id=_uid(),
        merchant=_scalar_or(result.get("merchant"), "") or "",
        amount=_money_or(result.get("amount"), ZERO),
        date=result.get("date"),
        direction=_scalar_or(result.get("direction"), "expense") or "expense",
        suggestedCategory=_scalar_or(result.get("suggestedCategory"), "其他支出") or "其他支出",
        confidence=_float_or(result.get("confidence"), 0.5),
        explanation=_scalar_or(result.get("explanation"), "") or "",
        rawText=raw_out,
        invoiceType=result.get("invoiceType"),
        invoiceNumber=result.get("invoiceNumber"),
        billingStatus=result.get("billingStatus"),
        accountCode=result.get("accountCode"),
        accountName=result.get("accountName"),
        vatRate=result.get("vatRate"),
        vatAmount=result.get("vatAmount"),
        taxDeductible=result.get("taxDeductible"),
        riskTags=sanitize_risk_tags(result.get("riskTags", [])),
        askFollowUp=result.get("askFollowUp"),
        followUpOptions=result.get("followUpOptions"),
        payer=_scalar_or(result.get("payer"), "company_account") or "company_account",
        buyerName=result.get("buyerName"),
        buyerTaxId=result.get("buyerTaxId"),
        sellerName=result.get("sellerName"),
        sellerTaxId=result.get("sellerTaxId"),
    )


@router.post("/text", response_model=RecordTextResponse)
async def record_text(req: RecordTextRequest, ctx: AuthContext = Depends(require_auth)):
    """文字/语音记账 — LLM 分类 + 科目映射 + 税务标签（注入公司画像 top-K 分类习惯）"""
    user_msg = req.text
    if req.client_time:
        user_msg = f"【上下文 - 手机时间：{req.client_time}】\n用户输入：{user_msg}"

    try:
        from agent.memory import get_profile

        profile = await get_profile(ctx.company_id)
        prefs = profile.get("categoryPreferences") or {}
        if isinstance(prefs, dict) and prefs:
            ranked = sorted(
                prefs.items(),
                key=lambda kv: int((kv[1] or {}).get("count") if isinstance(kv[1], dict) else 0),
                reverse=True,
            )[:8]
            hints = "；".join(f"{k}→{v.get('category')}" for k, v in ranked if isinstance(v, dict))
            if hints:
                user_msg += f"\n【本公司分类习惯（优先参考）】{hints}"
    except Exception:
        pass  # 画像注入失败不阻塞记账主路径

    result = chat_text_json(
        CLASSIFIER_V3_PROMPT,
        user_msg,
        scene="classify",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    return RecordTextResponse(
        id=_uid(),
        direction=result["direction"],
        amount=result["amount"],
        title=result["title"],
        category=result["category"],
        confidence=result["confidence"],
        explanation=result["explanation"],
        accountCode=result.get("accountCode"),
        accountName=result.get("accountName"),
        vatRate=result.get("vatRate"),
        vatAmount=result.get("vatAmount"),
        taxDeductible=result.get("taxDeductible"),
        riskTags=sanitize_risk_tags(result.get("riskTags", [])),
        voucherHint=result.get("voucherHint"),
        askFollowUp=result.get("askFollowUp"),
        followUpOptions=result.get("followUpOptions"),
        payer=result.get("payer", "company_account"),
    )


@router.post("/receipt", response_model=RecordReceiptResponse)
async def record_receipt(image: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)):
    """
    拍单据 — 智谱 OCR 提取文字 + 通义文本模型结构化（科目/发票字段与 VL 版一致）。

    当前仅支持 PNG/JPG/JPEG/BMP，单文件 ≤8MB；不支持 PDF。
    """
    if not (ZHIPU_API_KEY and ZHIPU_API_KEY.strip()):
        raise HTTPException(
            status_code=503,
            detail="服务端未配置 ZHIPU_API_KEY，无法调用智谱 OCR。请在环境变量或 backend/.env 中配置。",
        )

    content = await image.read()
    filename = image.filename or "receipt.jpg"
    return await _parse_receipt_from_bytes(content, filename, image.content_type, ctx)


@router.post("/receipt-url", response_model=RecordReceiptResponse)
async def record_receipt_url(req: CloudFileRequest, ctx: AuthContext = Depends(require_auth)):
    """拍单据（云存储 URL 版）：下载临时文件后复用 OCR 流程。"""
    if not (ZHIPU_API_KEY and ZHIPU_API_KEY.strip()):
        raise HTTPException(
            status_code=503,
            detail="服务端未配置 ZHIPU_API_KEY，无法调用智谱 OCR。请在环境变量或 backend/.env 中配置。",
        )
    content, resp_ct = await _fetch_cloud_file(req.fileUrl)
    filename = req.filename or "receipt.jpg"
    content_type = req.contentType or resp_ct
    return await _parse_receipt_from_bytes(content, filename, content_type, ctx)


@router.post("/speech", response_model=SpeechTextResponse)
async def record_speech(audio: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)):
    """
    语音转文字：接收小程序 RecorderManager 等上传的短音频，调用 DashScope Paraformer。
    个人主体小程序无需微信同声传译插件。
    """
    content = await audio.read()
    try:
        text = transcribe_uploaded_audio(
            content,
            audio.filename,
            audio.content_type,
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SpeechTextResponse(text=text)


@router.post("/speech-url", response_model=SpeechTextResponse)
async def record_speech_url(req: CloudFileRequest, ctx: AuthContext = Depends(require_auth)):
    """语音转文字（云存储 URL 版）：下载临时文件后调用 ASR。"""
    content, resp_ct = await _fetch_cloud_file(req.fileUrl)
    filename = req.filename or "speech.wav"
    content_type = req.contentType or resp_ct
    try:
        text = transcribe_uploaded_audio(
            content,
            filename,
            content_type,
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SpeechTextResponse(text=text)


@router.post("/batch", response_model=RecordBatchResponse)
async def record_batch(req: RecordBatchRequest, ctx: AuthContext = Depends(require_auth)):
    """连续日志拆分"""
    result = chat_text_json(
        BATCH_SPLIT_V3_PROMPT,
        req.text,
        scene="batch_split",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    from models.schemas import BatchEntry
    return RecordBatchResponse(
        entries=[BatchEntry(**_clean_entry_payload(e)) for e in result.get("entries", [])]
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def record_confirm(
    req: ConfirmRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认入账：确定性校验后创建并按企业策略过账。"""
    req_company_id = req.company_id or req.companyId
    if req_company_id and req_company_id != ctx.company_id:
        raise HTTPException(status_code=403, detail="无权操作其他 company 数据")
    request_payload = req.model_dump()
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation="record_confirm",
        payload=request_payload,
    )
    if cached is not None:
        return ConfirmResponse(**cached)

    vat = compute_vat(req.amount, req.category, req.direction)
    risk_tags = get_risk_tags(req.category, req.direction)
    deductible = is_tax_deductible(req.category, req.direction)
    voucher = get_voucher_hint(req.direction, req.category, req.amount, payer=req.payer)
    is_rd = req.category == "研发支出-费用化支出" or bool(req.rdProjectId or req.rdExpenseType)
    if is_rd:
        if not req.rdProjectId or not req.rdExpenseType:
            raise HTTPException(status_code=422, detail="研发费用必须选择研发项目和费用类别")
        debit_lines = voucher.get("debit") or []
        if not debit_lines:
            raise HTTPException(status_code=422, detail="研发费用凭证缺少借方分录")
        debit_lines[0] = {
            **debit_lines[0],
            "accountCode": RD_ACCOUNT_BY_TYPE[req.rdExpenseType],
            "accountName": RD_ACCOUNT_NAMES[req.rdExpenseType],
            "rdDetail": {
                "projectId": req.rdProjectId,
                "expenseType": req.rdExpenseType,
                "originalAmount": req.rdOriginalAmount or req.amount,
                "allocationMethod": req.rdAllocationMethod or "direct",
                "allocationBasis": req.rdAllocationBasis,
                "notes": req.rdNotes,
            },
        }
    payload = {
        **request_payload,
        "id": req.id or _uid(),
        "payer": req.payer or "company_account",
        "vatRate": vat.get("vatRate"),
        "vatAmount": vat.get("vatAmount"),
        "taxDeductible": deductible,
        "riskTags": risk_tags,
        "voucherLines": voucher,
    }
    entry = await create_entry(
        session,
        payload,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=request.headers.get("X-Request-ID") or _uid(),
        confirm=True,
    )
    saved = _clean_entry_payload(await serialize_journal(session, entry))
    response = ConfirmResponse(
        id=entry.id,
        voucherStatus=entry.status,
        voucherRef=entry.voucher_no,
        duplicateRisk=None,
        entry=saved,
    )
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation="record_confirm",
        payload=request_payload,
        status=200,
        response=response.model_dump(),
    )
    return response


@router.get("/entries")
async def get_entries(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """读取当前 company 的已确认记录（服务端持久化）。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    return {"entries": [_clean_entry_payload(e) for e in await list_journals(session, ctx.company_id)]}


@router.patch("/entries/{entry_id}")
async def patch_entry(
    entry_id: str,
    req: EntryUpdateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Update mutable entry fields. Server data is the source of truth."""
    payload = req.model_dump(exclude_unset=True)
    operation = f"update_draft:{entry_id}"
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
    )
    if cached is not None:
        return cached
    updated = await update_draft(
        session,
        entry_id,
        payload,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=request.headers.get("X-Request-ID") or _uid(),
    )
    response = {"entry": _clean_entry_payload(await serialize_journal(session, updated))}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.delete("/entries/{entry_id}")
async def remove_entry(
    entry_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """删除指定记录（服务端持久化）。"""
    payload = {"entryId": entry_id}
    operation = f"delete_draft:{entry_id}"
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
    )
    if cached is not None:
        return cached
    await delete_draft(
        session,
        entry_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=request.headers.get("X-Request-ID") or _uid(),
    )
    response = {"ok": True}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response
