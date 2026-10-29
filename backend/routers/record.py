# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""V3 记账路由：文字/语音/拍单据/确认"""
import asyncio
import base64
import ipaddress
import socket
from urllib.parse import urlparse
import httpx
from decimal import Decimal
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from deps.auth import AuthContext, require_auth
from models.schemas import (
    RecordTextRequest, RecordTextResponse,
    RecordReceiptResponse,
    RecordBatchRequest, RecordBatchResponse,
    ConfirmRequest, ConfirmResponse,
    EntryUpdateRequest,
    VoucherLinesAdjustRequest,
    SpeechTextResponse,
    CloudFileRequest,
)
from config import ZHIPU_API_KEY
from core.errors import AccountingError
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
from services.vat_service import get_vat_config_from_session, is_vat_sales_entry, split_income_vat
from core.money import ZERO, money
from services.tax_rules import compute_vat, get_risk_tags, is_tax_deductible, sanitize_risk_tags
from db.session import get_db_session
from services.accounting_core_service import (
    RD_ACCOUNT_BY_TYPE,
    adjust_voucher_lines,
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


def _attach_auxiliary(voucher: dict, auxiliary: dict[str, str | None]) -> dict:
    """把辅助核算挂到允许的科目行上（白名单见 core.chart_policy）。

    客户端只传 id；name 由服务端在落库时按主数据回填（accounting_core._add_lines），
    这里不复制任何客户端文案。
    """
    from core.chart_policy import auxiliary_kinds_for_account

    requested = {key: value for key, value in auxiliary.items() if value}
    if not requested:
        return voucher
    output: dict = {}
    for side in ("debit", "credit"):
        lines = []
        for line in voucher.get(side) or []:
            allowed = auxiliary_kinds_for_account(str(line.get("accountCode") or ""))
            matched = {key: value for key, value in requested.items() if key.removesuffix("_id") in allowed}
            lines.append({**line, "auxiliary": {**(line.get("auxiliary") or {}), **matched}} if matched else line)
        output[side] = lines
    return output


async def _attach_quantity(
    session: AsyncSession,
    company_id: str,
    voucher: dict,
    quantity: Decimal | None,
    unit_price: Decimal | None,
    unit: str | None,
) -> dict:
    """把数量/单价/单位挂到勾选了数量核算的科目行上（P1 WP-D）。

    confirm 的金额行走确定性凭证生成，数量没有行级入口，所以在服务端按科目
    配置挂到第一行数量科目（采购/销售凭证通常只有一行存货）；没有数量就不挂，
    非数量科目不挂，单价/单位不单独触发。
    """
    from sqlalchemy import select

    from db.models import ChartAccount

    if quantity is None:
        return voucher
    codes = [
        str(line.get("accountCode") or "")
        for side in ("debit", "credit")
        for line in voucher.get(side) or []
    ]
    codes = [code for code in codes if code]
    if not codes:
        return voucher
    rows = await session.execute(
        select(ChartAccount.code).where(
            ChartAccount.company_id == company_id,
            ChartAccount.code.in_(set(codes)),
            ChartAccount.quantity_enabled.is_(True),
        )
    )
    enabled = set(rows.scalars().all())
    if not enabled:
        return voucher
    output: dict = {}
    attached = False
    extra = {key: value for key, value in (("quantity", quantity), ("unitPrice", unit_price), ("unit", unit)) if value is not None}
    for side in ("debit", "credit"):
        lines = []
        for line in voucher.get(side) or []:
            if not attached and str(line.get("accountCode") or "") in enabled:
                line = {**line, **extra}
                attached = True
            lines.append(line)
        output[side] = lines
    return output


_SSRF_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata.goog"}


async def _assert_public_https_url(file_url: str) -> None:
    """防 SSRF：仅放行 https 公网地址（云存储临时链接均为 https）。

    拒绝非 https、保留/环回/链路本地/私网目标（含 DNS 解析后的真实 IP）与
    云厂商元数据主机名，防止客户端借服务端探测内网或云元数据。
    """
    parsed = urlparse(file_url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="文件地址必须为 https 链接")
    host = (parsed.hostname or "").strip().lower()
    if not host or host in _SSRF_BLOCKED_HOSTNAMES or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="文件地址不受支持")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail="文件地址域名无法解析") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise HTTPException(status_code=400, detail="文件地址不受支持")


async def _fetch_cloud_file(file_url: str) -> tuple[bytes, str]:
    """下载云存储临时文件 URL，返回文件字节和响应 content-type。"""
    await _assert_public_https_url(file_url)
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
            await asyncio.to_thread(
                chat_vision_json,
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
    except AccountingError:
        # 配额超限要变成 402 引导升级，不能静默降级到智谱 OCR 继续消耗
        raise
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
                await asyncio.to_thread(
                    chat_text_json,
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
async def record_text(
    req: RecordTextRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
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

    result = await asyncio.to_thread(
        chat_text_json,
        CLASSIFIER_V3_PROMPT,
        user_msg,
        scene="classify",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )

    direction = result.get("direction")
    category = result.get("category") or "其他支出"
    amount = result.get("amount") or 0
    payer = result.get("payer", "company_account")

    vat_rate = result.get("vatRate")
    vat_amount = result.get("vatAmount")
    voucher_hint = result.get("voucherHint")
    explanation = result.get("explanation") or ""

    # 确定性增值税价税分离覆盖：销售收入不依赖大模型随意发挥，直接按小规模纳税人征收率精准拆分
    if direction == "income" and is_vat_sales_entry(category) and amount:
        try:
            vat_config = await get_vat_config_from_session(session, ctx.company_id)
            net_amount, tax_amount = split_income_vat(amount, vat_config.rate)
            vat_rate = vat_config.rate
            vat_amount = tax_amount
            voucher_hint = get_voucher_hint(
                direction, category, amount, payer=payer, vat_rate=vat_config.rate
            )
            explanation = (
                f"{explanation}（按小规模征收率价税分离，计提增值税 ¥{tax_amount}；"
                "季度未超 30 万元时结账将全额免税转入营业外收入）"
            )
        except Exception:
            pass

    return RecordTextResponse(
        id=_uid(),
        direction=direction,
        amount=amount,
        title=result["title"],
        category=category,
        confidence=result["confidence"],
        explanation=explanation,
        accountCode=result.get("accountCode"),
        accountName=result.get("accountName"),
        vatRate=vat_rate,
        vatAmount=vat_amount,
        taxDeductible=result.get("taxDeductible"),
        riskTags=sanitize_risk_tags(result.get("riskTags", [])),
        voucherHint=voucher_hint,
        askFollowUp=result.get("askFollowUp"),
        followUpOptions=result.get("followUpOptions"),
        payer=payer,
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
        text = await asyncio.to_thread(
            transcribe_uploaded_audio,
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
        text = await asyncio.to_thread(
            transcribe_uploaded_audio,
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
    result = await asyncio.to_thread(
        chat_text_json,
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

    # 增值税口径唯一来源：小规模征收率、起征点、申报周期都从公司档案读。
    # 一般纳税人直接抛错，避免以错误口径生成凭证与底稿。
    try:
        vat_config = await get_vat_config_from_session(session, ctx.company_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    income_with_vat = req.direction == "income" and is_vat_sales_entry(req.category)
    if income_with_vat:
        net_amount, tax_amount = split_income_vat(req.amount, vat_config.rate)
        vat = {
            "vatRate": vat_config.rate,
            "vatAmount": tax_amount,
            "amountExcludingTax": net_amount,
            "vatExempt": False,
        }
        voucher = get_voucher_hint(
            req.direction, req.category, req.amount, payer=req.payer, vat_rate=vat_config.rate
        )
    else:
        vat = compute_vat(req.amount, req.category, req.direction)
        voucher = get_voucher_hint(req.direction, req.category, req.amount, payer=req.payer)
    risk_tags = get_risk_tags(req.category, req.direction)
    deductible = is_tax_deductible(
        req.category,
        req.direction,
        billing_status=req.billingStatus,
        has_invoice_number=bool(str(req.invoiceNumber or "").strip()),
    )
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
        "vatSeparated": bool(income_with_vat),
        "amountExcludingTax": net_amount if income_with_vat else None,
        "taxDeductible": deductible,
        "riskTags": risk_tags,
        "voucherLines": await _attach_quantity(
            session,
            ctx.company_id,
            _attach_auxiliary(
                voucher,
                {
                    "partner_id": req.partner_id or req.partnerId,
                    "department_id": req.department_id or req.departmentId,
                    "project_id": req.project_id or req.projectId,
                },
            ),
            req.quantity,
            req.unitPrice,
            req.unit,
        ),
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


class EntryAuxiliaryRequest(BaseModel):
    partner_id: Optional[str] = None
    department_id: Optional[str] = None
    project_id: Optional[str] = None


@router.put("/entries/{entry_id}/auxiliary")
async def assign_entry_auxiliary_route(
    entry_id: str,
    req: EntryAuxiliaryRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """给草稿凭证补挂辅助核算（已过账凭证不可改，只能红冲后重做）。"""
    from services.auxiliary_service import assign_entry_auxiliary

    auxiliary = {
        key: value
        for key, value in {
            "partner_id": req.partner_id,
            "department_id": req.department_id,
            "project_id": req.project_id,
        }.items()
        if value
    }
    if not auxiliary:
        raise HTTPException(status_code=422, detail="请至少选择一个辅助核算项")
    return await assign_entry_auxiliary(ctx.company_id, ctx.user_id, entry_id, auxiliary)


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


@router.patch("/entries/{entry_id}/voucher-lines")
async def adjust_entry_voucher_lines(
    entry_id: str,
    req: VoucherLinesAdjustRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """会计手工调整分录（科目/方向/金额/增删行）。

    只对未过账凭证开放；借贷平衡、「借方合计 = 交易金额」、末级可用科目三项
    由服务端复核，前端校验只是提前反馈。每次调整写审计留痕。
    """
    payload = req.model_dump(exclude_unset=True)
    operation = f"adjust_voucher_lines:{entry_id}"
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
    )
    if cached is not None:
        return cached
    updated = await adjust_voucher_lines(
        session,
        entry_id,
        lines=[line.model_dump() for line in req.lines] if req.lines else None,
        reason=req.reason or "",
        reset_to_system=bool(req.resetToSystem),
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
