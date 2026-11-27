# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行电子回单与标准对账单归档服务（ZIP 拆包 / 多联切图 / OCR 要素提取 / 自动匹配流水）。

设计约束：
- 原件不可变（A2）：整份多笔回单 PDF 先原样落盘为 ``bank_receipt_batch``，
  每笔切分回单以 ``bank_receipt`` + ``parent_attachment_id`` 落盘并保留原始联结；
- 银行回单挂 ``bank_statement_id``（而非写死单张 ``journal_entry_id``）：
  对账重配、取消配对、红冲重记后回单自动随流水迁移（A3）；
- 银行标准对账单（含月度流水明细、电子回单专用章）识别为 ``bank_statement_pdf``，
  按 ``period``（``YYYY-MM``）及 ``parsed_json["periods"]`` 归档到期间原件目录；
- 去重双防线：文件 ``content_sha256`` 唯一约束 + 回单编号 ``receiptNo`` 业务判重
  （同一回单不同打印时间戳重下不重复入库）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import fitz
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.money import ZERO, money, money_text
from db.models import Attachment
from db.session import session_scope
from services.account_service import get_company
from services.attachment_storage_service import (
    IMAGE_EXTS,
    MAX_ATTACHMENT_BYTES,
    AttachmentError,
    _existing_by_sha,
    _ext_of,
    _serialize,
    _sha256,
    _unlink_quiet,
    _write_file,
    read_attachment_file,
)
from services.database import get_conn

BANK_UPLOAD_EXTS = {"zip", "pdf", *IMAGE_EXTS}
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_ZIP_UNPACKED_BYTES = 120 * 1024 * 1024

_VISION_PROMPT = """你是一位中国企业出纳/会计单据识别专家。请识别这张银行电子回单图片中的关键业务字段，并严格输出 JSON（不要 markdown 代码块，不要额外解释）：
{
  "receiptNo": "回单编号（纯字母数字字符串，没有则返回空串）",
  "txNo": "交易流水号（没有则返回空串）",
  "txDate": "记账日期，格式 YYYY-MM-DD（没有则返回空串）",
  "amount": "金额小写数字，两位小数字符串如 300.00（没有则返回空串）",
  "direction": "income（我方为收款人/回单标题含收款/利息/贷记）或 expense（我方为付款人/回单标题含付款/借记/TIPS扣款），无法判断返回空串",
  "payerName": "付款人名称/户名（没有则返回空串）",
  "payeeName": "收款人名称/户名（没有则返回空串）",
  "summary": "摘要与备注拼接（例如 汇兑 deepseek调用服务费，没有则返回空串）",
  "bankName": "银行名称，如 杭州银行（没有则返回空串）",
  "receiptType": "回单类型，如 账务回单-付款 / 账务回单-收款 / 利息传票 / TIPS业务回单 / 国际结算业务回单"
}"""


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except Exception:
        return info.filename


def iter_bank_zip_entries(payload: bytes) -> list[dict[str, Any]]:
    """展开银行 ZIP 包，兼容国内网银常见的 GBK/CP437 中文文件名。"""
    if len(payload) > MAX_ZIP_BYTES:
        raise AttachmentError(f"ZIP 文件超过 {MAX_ZIP_BYTES // (1024 * 1024)}MB 上限", 400)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise AttachmentError("无法解压 ZIP 文件，请确认压缩包未损坏", 400) from exc

    entries: list[dict[str, Any]] = []
    unpacked_total = 0
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ZIP_MEMBERS:
            raise AttachmentError(f"ZIP 内文件数超过 {MAX_ZIP_MEMBERS} 个上限", 400)
        for info in infos:
            raw_name = _decode_zip_name(info)
            base_name = os.path.basename(raw_name.replace("\\", "/")).strip()
            if not base_name or raw_name.startswith("__MACOSX/") or base_name.startswith("._"):
                continue
            ext = _ext_of(base_name)
            if ext not in {"pdf", *IMAGE_EXTS}:
                entries.append({"file_name": base_name, "ext": ext, "payload": b"", "error": f"不支持的包内文件格式：{ext or '无'}"})
                continue
            unpacked_total += int(info.file_size or 0)
            if unpacked_total > MAX_ZIP_UNPACKED_BYTES:
                raise AttachmentError("ZIP 解压总大小超过限制，请分批上传", 400)
            data = archive.read(info)
            if len(data) > MAX_ATTACHMENT_BYTES:
                entries.append({"file_name": base_name, "ext": ext, "payload": b"", "error": "单个文件超过 20MB 上限"})
                continue
            entries.append({"file_name": base_name, "ext": ext, "payload": data, "error": ""})
    return entries


def _detect_statement_pdf(file_name: str, pdf_bytes: bytes) -> tuple[bool, list[str]]:
    """识别是否为「月度标准银行对账单」（带流水明细表），并提取覆盖期间 YYYY-MM。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts: list[str] = []
    try:
        for page in doc:
            texts.append(page.get_text("text") or "")
    finally:
        doc.close()

    full_text = "\n".join(texts)
    compact_name = re.sub(r"\s+", "", file_name)
    compact_text = re.sub(r"\s+", "", full_text[:1500])

    is_statement = (
        ("对账单" in compact_name and "回单" not in compact_name)
        or "交易标准对账单" in compact_text
        or "客户交易对账单" in compact_text
        or ("对账单编号" in compact_text and "借方发生额" in compact_text)
    )
    if not is_statement:
        return False, []

    periods_set: set[str] = set()
    for match in re.finditer(r"(20\d{2})-(0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])", full_text):
        periods_set.add(f"{match.group(1)}-{match.group(2)}")
    if not periods_set:
        span_match = re.search(r"(20\d{2})(0[1-9]|1[0-2])_(20\d{2})(0[1-9]|1[0-2])", compact_name)
        if span_match:
            y1, m1 = int(span_match.group(1)), int(span_match.group(2))
            y2, m2 = int(span_match.group(3)), int(span_match.group(4))
            cur_y, cur_m = y1, m1
            while (cur_y, cur_m) <= (y2, m2) and len(periods_set) < 36:
                periods_set.add(f"{cur_y:04d}-{cur_m:02d}")
                cur_m += 1
                if cur_m > 12:
                    cur_m = 1
                    cur_y += 1
    if not periods_set:
        ym_match = re.search(r"(20\d{2})[-_]?(0[1-9]|1[0-2])", compact_name)
        if ym_match:
            periods_set.add(f"{ym_match.group(1)}-{ym_match.group(2)}")

    return True, sorted(periods_set)


def _split_pixmap_by_horizontal_whitespace(page: fitz.Page) -> list[dict[str, Any]]:
    """矢量 PDF 或整页扫描图的通用切分：按水平空白带把多联回单拆成单张 PNG。"""
    zoom = min(2.0, 1600.0 / max(1.0, float(page.rect.width)))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    w, h = pix.width, pix.height
    if w <= 0 or h <= 0:
        return []
    samples = pix.samples
    row_stride = w * pix.n
    is_content = [False] * h
    for y in range(h):
        row = samples[y * row_stride:(y + 1) * row_stride]
        # 抽样检查该行是否存在深色像素（均值/步长采样兼顾速度与精度）
        dark_count = 0
        step = max(1, (w // 120) * pix.n)
        for idx in range(0, len(row), step):
            if row[idx] < 244 or row[idx + 1] < 244 or row[idx + 2] < 244:
                dark_count += 1
                if dark_count >= 2:
                    is_content[y] = True
                    break

    if not any(is_content):
        return []

    gap_threshold = max(14, int(h * 0.018))
    min_block_h = max(80, int(h * 0.12))

    # 合并短于 gap_threshold 的行内小空白（如标题与表格之间）
    spans: list[tuple[int, int]] = []
    start: int | None = None
    blank_run = 0
    last_content_y = 0
    for y, active in enumerate(is_content):
        if active:
            if start is None:
                start = y
            last_content_y = y
            blank_run = 0
        elif start is not None:
            blank_run += 1
            if blank_run >= gap_threshold:
                if last_content_y - start + 1 >= min_block_h:
                    spans.append((start, last_content_y + 1))
                start = None
                blank_run = 0
    if start is not None and last_content_y - start + 1 >= min_block_h:
        spans.append((start, last_content_y + 1))

    if not spans:
        spans = [(0, h)]

    slices: list[dict[str, Any]] = []
    pad = max(8, int(h * 0.01))
    for y0_px, y1_px in spans:
        clip = fitz.Rect(
            0.0,
            max(0.0, (y0_px - pad) / zoom),
            float(page.rect.width),
            min(float(page.rect.height), (y1_px + pad) / zoom),
        )
        sub_pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(zoom, zoom), alpha=False)
        text = (page.get_text("text", clip=clip) or "").strip()
        slices.append({"png": sub_pix.tobytes("png"), "text": text})
    return slices


def split_receipt_pdf_pages(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """把多笔/多页银行回单 PDF 切分为单笔回单 PNG 列表（保留页内从上到下顺序）。

    优先级：
    1) 页内包含独立大图块（典型如杭州银行每页 3 张 2447x1153 嵌入式 PNG）时，
       按矩形 y0 升序直取无损 PNG（零重编码失真、速度极快）；
    2) 否则按水平空白带切分矢量/整页扫描页面，并附带同区域文字层供免 LLM 提取。
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise AttachmentError("无法打开 PDF 文件，请确认格式有效", 400) from exc

    slices: list[dict[str, Any]] = []
    try:
        for page_index, page in enumerate(doc, start=1):
            page_w = max(1.0, float(page.rect.width))
            page_h = max(1.0, float(page.rect.height))
            img_blocks: list[tuple[fitz.Rect, int, int]] = []
            seen_rects: list[tuple[float, float, float, float]] = []

            for img_info in page.get_images(full=True):
                xref = int(img_info[0])
                smask = int(img_info[1] or 0)
                for rect in page.get_image_rects(xref):
                    if rect.width < max(200.0, page_w * 0.45) or rect.height < max(100.0, page_h * 0.12):
                        continue
                    key = (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))
                    if key in seen_rects:
                        continue
                    seen_rects.append(key)
                    img_blocks.append((rect, xref, smask))

            img_blocks.sort(key=lambda item: (round(item[0].y0, 1), round(item[0].x0, 1)))
            has_single_full_scan = (
                len(img_blocks) == 1
                and img_blocks[0][0].width >= page_w * 0.90
                and img_blocks[0][0].height >= page_h * 0.88
            )

            page_slices: list[dict[str, Any]] = []
            if img_blocks and not has_single_full_scan:
                for rect, xref, smask in img_blocks:
                    text = (page.get_text("text", clip=rect) or "").strip()
                    png_bytes: bytes | None = None
                    if not text and smask == 0:
                        extracted = doc.extract_image(xref)
                        if extracted and extracted.get("ext") == "png" and extracted.get("image"):
                            png_bytes = extracted["image"]
                    if png_bytes is None:
                        zoom = min(2.0, 1600.0 / max(1.0, float(rect.width)))
                        pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(zoom, zoom), alpha=False)
                        png_bytes = pix.tobytes("png")
                    page_slices.append({"png": png_bytes, "text": text})
            else:
                page_slices = _split_pixmap_by_horizontal_whitespace(page)

            total_on_page = len(page_slices)
            for idx, item in enumerate(page_slices, start=1):
                slices.append(
                    {
                        "pageNo": page_index,
                        "sliceIndex": idx,
                        "sliceTotal": total_on_page,
                        "png": item["png"],
                        "text": item["text"],
                    }
                )
    finally:
        doc.close()

    return slices


def _normalize_date(raw: str) -> str:
    text = str(raw or "").strip()
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not m:
        return ""
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _normalize_amount(raw: Any) -> str:
    text = str(raw or "").replace(",", "").replace("¥", "").replace("￥", "").strip()
    if not text:
        return ""
    try:
        val = abs(money(text))
        return money_text(val) if val > ZERO else ""
    except Exception:
        return ""


def _extract_receipt_from_text(text: str) -> dict[str, Any] | None:
    """从自带文字层的回单中免模型提取关键字段。"""
    clean = str(text or "").strip()
    if len(clean) < 20:
        return None

    receipt_no = ""
    if m := re.search(r"回单编号[:：]\s*([A-Za-z0-9_-]{6,})", clean):
        receipt_no = m.group(1).strip()
    tx_no = ""
    if m := re.search(r"(?:交易流水号|流水号)[:：]\s*([A-Za-z0-9_-]{6,})", clean):
        tx_no = m.group(1).strip()

    tx_date = ""
    if m := re.search(r"(?:记账日期|交易日期|日期)[:：]\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})", clean):
        tx_date = _normalize_date(m.group(1))
    if not tx_date:
        tx_date = _normalize_date(clean)

    amount = ""
    for pat in (
        r"(?:小写|小\s*写)[)）:：]*\s*[¥￥]?\s*([0-9,]+\.\d{2})",
        r"金额[^0-9\n]{0,20}[¥￥]?\s*([0-9,]+\.\d{2})",
        r"[¥￥]\s*([0-9,]+\.\d{2})",
    ):
        if m := re.search(pat, clean):
            amount = _normalize_amount(m.group(1))
            if amount:
                break

    direction = ""
    if re.search(r"[-—]\s*收款|收款回单|利息传票", clean):
        direction = "income"
    elif re.search(r"[-—]\s*付款|付款回单|TIPS业务回单", clean):
        direction = "expense"

    payer_name = ""
    if m := re.search(r"付款[^\n]{0,6}(?:名称|户名)[:：]\s*([^\n]+)", clean):
        payer_name = m.group(1).strip()
    payee_name = ""
    if m := re.search(r"收款[^\n]{0,6}(?:名称|户名)[:：]\s*([^\n]+)", clean):
        payee_name = m.group(1).strip()

    summary_parts: list[str] = []
    for pat in (r"摘要[:：]\s*([^\n]+)", r"备注[:：]\s*([^\n]+)"):
        if m := re.search(pat, clean):
            part = m.group(1).strip()
            if part and part not in summary_parts:
                summary_parts.append(part)

    if not tx_date or not amount:
        return None

    return {
        "receiptNo": receipt_no,
        "txNo": tx_no,
        "txDate": tx_date,
        "amount": amount,
        "direction": direction,
        "payerName": payer_name,
        "payeeName": payee_name,
        "summary": " ".join(summary_parts),
        "bankName": "杭州银行" if "杭州银行" in clean else "",
        "receiptType": "电子回单",
        "extractSource": "text",
    }


def _normalize_vision_meta(raw: dict[str, Any], company_name: str) -> dict[str, Any]:
    direction = str(raw.get("direction") or "").strip().lower()
    if direction not in ("income", "expense"):
        direction = ""
    receipt_type = str(raw.get("receiptType") or "").strip()
    if not direction:
        if "收款" in receipt_type:
            direction = "income"
        elif "付款" in receipt_type:
            direction = "expense"
    payer = str(raw.get("payerName") or "").strip()
    payee = str(raw.get("payeeName") or "").strip()
    if not direction and company_name:
        co_clean = re.sub(r"\W+", "", company_name)
        if co_clean and co_clean in re.sub(r"\W+", "", payee):
            direction = "income"
        elif co_clean and co_clean in re.sub(r"\W+", "", payer):
            direction = "expense"

    return {
        "receiptNo": str(raw.get("receiptNo") or "").strip(),
        "txNo": str(raw.get("txNo") or "").strip(),
        "txDate": _normalize_date(str(raw.get("txDate") or "")),
        "amount": _normalize_amount(raw.get("amount")),
        "direction": direction,
        "payerName": payer,
        "payeeName": payee,
        "summary": str(raw.get("summary") or "").strip(),
        "bankName": str(raw.get("bankName") or "").strip(),
        "receiptType": receipt_type or "电子回单",
        "extractSource": "vision",
    }


async def extract_receipt_metadata(
    png_bytes: bytes,
    *,
    text_hint: str = "",
    company_name: str = "",
    company_id: str = "",
    user_id: str = "",
    use_vision: bool = True,
) -> dict[str, Any]:
    """提取单笔回单要素：优先文本层正则，纯图片时走视觉模型，失败降级留图待手选。"""
    text_meta = _extract_receipt_from_text(text_hint)
    if text_meta is not None:
        return text_meta

    empty_meta = {
        "receiptNo": "",
        "txNo": "",
        "txDate": "",
        "amount": "",
        "direction": "",
        "payerName": "",
        "payeeName": "",
        "summary": "",
        "bankName": "",
        "receiptType": "电子回单",
        "extractSource": "none",
    }
    if not use_vision:
        return empty_meta

    from services.llm import chat_vision_json

    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    try:
        raw = await asyncio.to_thread(
            chat_vision_json,
            _VISION_PROMPT,
            data_url,
            max_tokens=2048,
            scene="bank_receipt",
            user_id=user_id,
            company_id=company_id,
        )
        if isinstance(raw, dict) and raw:
            return _normalize_vision_meta(raw, company_name)
    except Exception:  # noqa: BLE001 - 视觉模型不可用/熔断时仍保留高清原件图片，后续可补录或重跑
        pass
    return empty_meta


def _clean_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]", "", str(text or "")).lower()


def _counterparty_score(meta: dict[str, Any], stmt: dict[str, Any], company_name: str) -> int:
    desc = str(stmt.get("description") or "")
    desc_clean = _clean_text(desc)
    direction = str(meta.get("direction") or "")
    payer = str(meta.get("payerName") or "").strip()
    payee = str(meta.get("payeeName") or "").strip()
    co_clean = _clean_text(company_name)

    if direction == "expense":
        cp = payee
    elif direction == "income":
        cp = payer
    else:
        cp = payee if (co_clean and _clean_text(payer) == co_clean) else payer

    score = 0
    cp_clean = _clean_text(cp)
    if cp_clean and len(cp_clean) >= 2:
        if cp_clean in desc_clean:
            score += 10
        else:
            common = set(re.findall(r"[\u4e00-\u9fa5]", cp_clean)) & set(re.findall(r"[\u4e00-\u9fa5]", desc_clean))
            if len(common) >= 2:
                score += len(common)

    summary = str(meta.get("summary") or "").strip()
    for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", summary):
        if token in ("入账", "汇兑", "转账", "付款申请交易", "汇兑业务受理", "利息"):
            continue
        if token.lower() in desc_clean:
            score += 3
    return score


def match_statement_for_receipt(
    meta: dict[str, Any],
    statements: list[dict[str, Any]],
    *,
    company_name: str = "",
) -> str | None:
    """用「记账日期 + 金额 + 收付方向 + 对手方/摘要消歧」匹配唯一银行流水。"""
    tx_date = str(meta.get("txDate") or "").strip()[:10]
    amt_str = str(meta.get("amount") or "").strip()
    if not tx_date or not amt_str or not statements:
        return None
    try:
        target_amt = abs(money(amt_str))
    except Exception:
        return None
    if target_amt <= ZERO:
        return None

    direction = str(meta.get("direction") or "").strip()
    candidates: list[dict[str, Any]] = []
    for stmt in statements:
        stmt_date = str(stmt.get("transaction_date") or "").strip()[:10]
        if stmt_date != tx_date:
            continue
        stmt_amt = money(stmt.get("amount") or 0)
        if abs(abs(stmt_amt) - target_amt) >= Decimal("0.01"):
            continue
        if direction == "income" and stmt_amt <= ZERO:
            continue
        if direction == "expense" and stmt_amt >= ZERO:
            continue
        candidates.append(stmt)

    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0]["id"])

    scored = sorted(
        ((_counterparty_score(meta, stmt, company_name), str(stmt["id"])) for stmt in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
        return scored[0][1]
    return None


async def _load_company_statements(company_id: str) -> list[dict[str, Any]]:
    try:
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT id, transaction_date, description, amount, balance, matched_entry_id "
                "FROM bank_statements WHERE company_id = ? ORDER BY transaction_date DESC, id DESC",
                (company_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001 - 尚未建 bank_statements 表时安全降级为空
        return []


async def _existing_receipt_numbers(company_id: str) -> set[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Attachment.parsed_json).where(
                    Attachment.company_id == company_id,
                    Attachment.document_type == "bank_receipt",
                    Attachment.status == "active",
                )
            )
        ).scalars().all()
    numbers: set[str] = set()
    for parsed in rows:
        if isinstance(parsed, dict):
            no = str(parsed.get("receiptNo") or "").strip()
            if no:
                numbers.add(no)
    return numbers


async def _persist_simple_attachment(
    *,
    company_id: str,
    user_id: str,
    file_name: str,
    ext: str,
    payload: bytes,
    digest: str,
    document_type: str,
    period: str | None = None,
    bank_statement_id: str | None = None,
    parent_attachment_id: str | None = None,
    parsed_json: dict[str, Any] | None = None,
) -> tuple[Attachment | None, bool]:
    """落盘单个附件行；返回 (row, is_duplicate)。"""
    attachment_id = f"att-{uuid4().hex[:24]}"
    stored_name = f"{attachment_id}.{ext}"
    _write_file(company_id, attachment_id, ext, payload)
    try:
        async with session_scope() as session:
            row = Attachment(
                id=attachment_id,
                company_id=company_id,
                bank_statement_id=bank_statement_id,
                period=period,
                parent_attachment_id=parent_attachment_id,
                document_type=document_type,
                file_name=file_name[:255],
                file_ext=ext,
                size_bytes=len(payload),
                content_sha256=digest,
                stored_name=stored_name,
                status="active",
                parsed_json=parsed_json,
                source="upload",
                created_by=user_id,
            )
            session.add(row)
            await session.flush()
            session.expunge(row)
            return row, False
    except IntegrityError:
        _unlink_quiet(company_id, attachment_id, ext)
        return None, True
    except Exception:
        _unlink_quiet(company_id, attachment_id, ext)
        raise


def _receipt_display_name(meta: dict[str, Any], base_name: str, page_no: int, slice_idx: int, slice_total: int) -> str:
    tx_date = str(meta.get("txDate") or "").strip()
    amt = str(meta.get("amount") or "").strip()
    cp = ""
    if meta.get("direction") == "income":
        cp = str(meta.get("payerName") or "").strip()
    elif meta.get("direction") == "expense":
        cp = str(meta.get("payeeName") or "").strip()
    if tx_date and amt:
        cp_part = f"_{cp[:16]}" if cp else ""
        return f"银行回单_{tx_date}{cp_part}_{amt}.png"
    stem = os.path.splitext(base_name)[0][:40]
    if slice_total > 1:
        return f"{stem}_p{page_no}_{slice_idx}.png"
    return f"{stem}_p{page_no}.png"


async def upload_bank_receipts(
    company_id: str,
    user_id: str,
    files: list[dict[str, Any]],
    *,
    use_vision: bool = True,
) -> dict[str, Any]:
    """批量上传银行回单/对账单（支持 ZIP / 多联 PDF / 单张图片）。"""
    company = await get_company(company_id)
    company_name = str((company or {}).get("name") or "").strip()
    statements = await _load_company_statements(company_id)
    existing_receipt_nos = await _existing_receipt_numbers(company_id)

    doc_queue: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []

    for source in files:
        file_name = os.path.basename(str(source.get("file_name") or "").replace("\\", "/"))
        payload = source.get("payload") or b""
        ext = _ext_of(file_name)
        if not file_name or ext not in BANK_UPLOAD_EXTS:
            file_results.append({"fileName": file_name or "(未命名)", "outcome": "rejected", "error": "仅支持 ZIP / PDF / 图片文件"})
            continue
        if not payload:
            file_results.append({"fileName": file_name, "outcome": "rejected", "error": "空文件"})
            continue
        if ext == "zip":
            try:
                expanded = iter_bank_zip_entries(payload)
            except AttachmentError as exc:
                file_results.append({"fileName": file_name, "outcome": "rejected", "error": str(exc)})
                continue
            for entry in expanded:
                if entry["error"]:
                    file_results.append({"fileName": entry["file_name"], "outcome": "rejected", "error": entry["error"]})
                else:
                    doc_queue.append(entry)
        else:
            if len(payload) > MAX_ATTACHMENT_BYTES:
                file_results.append({"fileName": file_name, "outcome": "rejected", "error": "超过单文件 20MB 上限"})
                continue
            doc_queue.append({"file_name": file_name, "ext": ext, "payload": payload})

    seen_doc_shas: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for item in doc_queue:
        digest = _sha256(item["payload"])
        if digest in seen_doc_shas:
            file_results.append({"fileName": item["file_name"], "outcome": "duplicate", "error": "同批次存在重复文件"})
            continue
        seen_doc_shas.add(digest)
        prepared.append({**item, "sha": digest})

    existing_map = await _existing_by_sha(company_id, [item["sha"] for item in prepared])
    receipts_out: list[dict[str, Any]] = []
    statements_out: list[dict[str, Any]] = []
    batch_count = 0
    matched_count = 0
    parked_count = 0
    duplicate_count = 0
    seen_unit_shas: set[str] = set()

    vision_sem = asyncio.Semaphore(4)

    async def _extract(png_bytes: bytes, text_hint: str) -> dict[str, Any]:
        async with vision_sem:
            return await extract_receipt_metadata(
                png_bytes,
                text_hint=text_hint,
                company_name=company_name,
                company_id=company_id,
                user_id=user_id,
                use_vision=use_vision,
            )

    for item in prepared:
        file_name = item["file_name"]
        ext = item["ext"]
        payload = item["payload"]
        digest = item["sha"]

        if digest in existing_map:
            dup = existing_map[digest]
            duplicate_count += 1
            file_results.append({"fileName": file_name, "outcome": "duplicate", "attachmentId": dup.id, "error": "该原件已上传过"})
            continue

        if ext == "pdf":
            is_stmt_pdf, periods = _detect_statement_pdf(file_name, payload)
            if is_stmt_pdf:
                row, is_dup = await _persist_simple_attachment(
                    company_id=company_id,
                    user_id=user_id,
                    file_name=file_name,
                    ext="pdf",
                    payload=payload,
                    digest=digest,
                    document_type="bank_statement_pdf",
                    period=periods[-1] if periods else None,
                    parsed_json={"periods": periods, "bankName": "杭州银行" if "杭州银行" in file_name else ""},
                )
                if is_dup or row is None:
                    duplicate_count += 1
                    file_results.append({"fileName": file_name, "outcome": "duplicate", "error": "该对账单已上传过"})
                else:
                    serialized = _serialize(row)
                    statements_out.append(serialized)
                    file_results.append({"fileName": file_name, "outcome": "stored_statement", "attachmentId": row.id, "periods": periods})
                continue

            # 多笔回单 PDF：先保存不可变母件，再逐联切图提取
            slices = split_receipt_pdf_pages(payload)
            batch_row, batch_dup = await _persist_simple_attachment(
                company_id=company_id,
                user_id=user_id,
                file_name=file_name,
                ext="pdf",
                payload=payload,
                digest=digest,
                document_type="bank_receipt_batch",
                parsed_json={"sliceCount": len(slices)},
            )
            if batch_dup or batch_row is None:
                duplicate_count += 1
                file_results.append({"fileName": file_name, "outcome": "duplicate", "error": "该回单合集已上传过"})
                continue
            batch_count += 1
            unit_items = [
                {
                    "png": s["png"],
                    "ext": "png",
                    "text": s["text"],
                    "parent_id": batch_row.id,
                    "file_name": file_name,
                    "pageNo": s["pageNo"],
                    "sliceIndex": s["sliceIndex"],
                    "sliceTotal": s["sliceTotal"],
                }
                for s in slices
            ]
        else:
            unit_items = [
                {
                    "png": payload,
                    "ext": ext,
                    "text": "",
                    "parent_id": None,
                    "file_name": file_name,
                    "pageNo": 1,
                    "sliceIndex": 1,
                    "sliceTotal": 1,
                }
            ]

        metas = await asyncio.gather(*[_extract(u["png"], u["text"]) for u in unit_items])
        unit_shas = [_sha256(u["png"]) for u in unit_items]
        unit_existing = await _existing_by_sha(company_id, unit_shas)
        created_in_doc = 0
        for u, meta, u_sha in zip(unit_items, metas, unit_shas):
            receipt_no = str(meta.get("receiptNo") or "").strip()
            if u_sha in unit_existing or u_sha in seen_unit_shas:
                duplicate_count += 1
                continue
            if receipt_no and receipt_no in existing_receipt_nos:
                duplicate_count += 1
                continue
            seen_unit_shas.add(u_sha)

            matched_stmt_id = match_statement_for_receipt(meta, statements, company_name=company_name)
            display_name = (
                _receipt_display_name(meta, u["file_name"], u["pageNo"], u["sliceIndex"], u["sliceTotal"])
                if u["parent_id"]
                else u["file_name"]
            )
            slice_meta = {**meta, "pageNo": u["pageNo"], "sliceIndex": u["sliceIndex"], "sliceTotal": u["sliceTotal"]}
            row, is_dup = await _persist_simple_attachment(
                company_id=company_id,
                user_id=user_id,
                file_name=display_name,
                ext=u["ext"],
                payload=u["png"],
                digest=u_sha,
                document_type="bank_receipt",
                period=str(meta.get("txDate") or "")[:7] or None,
                bank_statement_id=matched_stmt_id,
                parent_attachment_id=u["parent_id"],
                parsed_json=slice_meta,
            )
            if is_dup or row is None:
                duplicate_count += 1
                continue
            if receipt_no:
                existing_receipt_nos.add(receipt_no)
            if matched_stmt_id:
                matched_count += 1
            else:
                parked_count += 1
            created_in_doc += 1
            receipts_out.append(_serialize(row))

        file_results.append(
            {
                "fileName": file_name,
                "outcome": "split" if len(unit_items) > 1 else "stored",
                "sliceCount": len(unit_items),
                "createdCount": created_in_doc,
            }
        )

    if receipts_out or statements_out:
        from services.audit_service import log_action

        await log_action(
            company_id,
            user_id,
            "upload_bank_receipts",
            "attachment",
            None,
            {
                "receipts": len(receipts_out),
                "matched": matched_count,
                "parked": parked_count,
                "statements": len(statements_out),
            },
        )

    return {
        "files": file_results,
        "receipts": receipts_out,
        "statements": statements_out,
        "summary": {
            "batchCount": batch_count,
            "receiptTotal": len(receipts_out),
            "matchedCount": matched_count,
            "parkedCount": parked_count,
            "duplicateCount": duplicate_count,
            "statementPdfCount": len(statements_out),
            "rejectedCount": sum(1 for f in file_results if f.get("outcome") == "rejected"),
        },
    }


async def list_bank_receipts(
    company_id: str,
    *,
    parked: bool = False,
    month: str | None = None,
    statement_id: str | None = None,
) -> dict[str, Any]:
    """查询银行回单列表（支持待匹配 parked、按月份或指定流水过滤）。"""
    conditions = [
        Attachment.company_id == company_id,
        Attachment.document_type == "bank_receipt",
        Attachment.status == "active",
    ]
    if statement_id:
        conditions.append(Attachment.bank_statement_id == statement_id)
    elif parked:
        conditions.append(Attachment.bank_statement_id.is_(None))
    if month:
        conditions.append(Attachment.period == month[:7])

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Attachment)
                .where(*conditions)
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            )
        ).scalars().all()
    items = [_serialize(row) for row in rows]
    return {"items": items, "total": len(items)}


async def relink_bank_receipts(
    company_id: str,
    user_id: str | None = None,
    *,
    reextract_missing: bool = True,
) -> dict[str, Any]:
    """导入银行流水后，自动补提缺失要素的待匹配回单并按日期+金额+方向+对手方回挂到流水。"""
    company = await get_company(company_id)
    company_name = str((company or {}).get("name") or "").strip()
    statements = await _load_company_statements(company_id)
    if not statements:
        return {"relinked": 0, "reextracted": 0, "scanned": 0}

    async with session_scope() as session:
        parked_rows = (
            await session.execute(
                select(Attachment).where(
                    Attachment.company_id == company_id,
                    Attachment.document_type == "bank_receipt",
                    Attachment.bank_statement_id.is_(None),
                    Attachment.status == "active",
                )
            )
        ).scalars().all()

    refreshed_metas: dict[str, dict[str, Any]] = {}
    if reextract_missing and parked_rows:
        sem = asyncio.Semaphore(4)

        async def _reextract_row(row: Attachment) -> tuple[str, dict[str, Any] | None]:
            old_meta = row.parsed_json if isinstance(row.parsed_json, dict) else {}
            if old_meta.get("txDate") and old_meta.get("amount"):
                return row.id, None
            async with sem:
                try:
                    file_data = await read_attachment_file(company_id, row.id)
                except AttachmentError:
                    return row.id, None
                new_meta = await extract_receipt_metadata(
                    file_data["data"],
                    company_name=company_name,
                    company_id=company_id,
                    user_id=user_id or "",
                    use_vision=True,
                )
                if not new_meta.get("txDate") and not new_meta.get("amount"):
                    return row.id, None
                merged = dict(new_meta)
                for key in ("pageNo", "sliceIndex", "sliceTotal"):
                    if key in old_meta:
                        merged[key] = old_meta[key]
                return row.id, merged

        for row_id, new_meta in await asyncio.gather(*[_reextract_row(r) for r in parked_rows]):
            if new_meta is not None:
                refreshed_metas[row_id] = new_meta

    stmt_dates = {str(s["id"]): str(s.get("transaction_date") or "")[:7] for s in statements}
    relinked = 0
    reextracted = 0
    for row in parked_rows:
        old_meta = row.parsed_json if isinstance(row.parsed_json, dict) else {}
        new_meta = refreshed_metas.get(row.id)
        effective_meta = new_meta if new_meta is not None else old_meta
        stmt_id = match_statement_for_receipt(effective_meta, statements, company_name=company_name)
        if not stmt_id and new_meta is None:
            continue
        async with session_scope() as session:
            fresh = (
                await session.execute(
                    select(Attachment).where(Attachment.company_id == company_id, Attachment.id == row.id)
                )
            ).scalar_one_or_none()
            if fresh is None or fresh.bank_statement_id is not None or fresh.status != "active":
                continue
            if new_meta is not None:
                fresh.parsed_json = new_meta
                if new_meta.get("txDate"):
                    fresh.period = str(new_meta["txDate"])[:7]
                reextracted += 1
            if stmt_id:
                fresh.bank_statement_id = stmt_id
                if not fresh.period and stmt_dates.get(stmt_id):
                    fresh.period = stmt_dates[stmt_id]
                relinked += 1
            fresh.updated_at = datetime.now(timezone.utc)
            await session.flush()

    if (relinked or reextracted) and user_id:
        from services.audit_service import log_action

        await log_action(
            company_id,
            user_id,
            "relink_bank_receipts",
            "attachment",
            None,
            {"relinked": relinked, "reextracted": reextracted},
        )
    return {"relinked": relinked, "reextracted": reextracted, "scanned": len(parked_rows)}


async def bind_bank_receipt(
    company_id: str,
    user_id: str,
    attachment_id: str,
    statement_id: str,
) -> dict[str, Any]:
    """手动绑定（或改绑）单笔银行回单到指定银行流水。"""
    statements = await _load_company_statements(company_id)
    target_stmt = next((s for s in statements if str(s["id"]) == str(statement_id)), None)
    if target_stmt is None:
        raise AttachmentError("目标银行流水不存在", 404)

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Attachment).where(
                    Attachment.company_id == company_id,
                    Attachment.id == attachment_id,
                    Attachment.document_type == "bank_receipt",
                    Attachment.status == "active",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise AttachmentError("银行回单不存在或已作废", 404)
        old_stmt_id = row.bank_statement_id
        row.bank_statement_id = str(statement_id)
        tx_month = str(target_stmt.get("transaction_date") or "")[:7]
        if tx_month and not row.period:
            row.period = tx_month
        row.updated_at = datetime.now(timezone.utc)
        await session.flush()
        serialized = _serialize(row)

    from services.audit_service import log_action

    await log_action(
        company_id,
        user_id,
        "bind_bank_receipt",
        "attachment",
        attachment_id,
        {"from": old_stmt_id, "to": statement_id},
    )
    return serialized
