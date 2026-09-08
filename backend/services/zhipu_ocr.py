"""
智谱 OCR（files/ocr）HTTP 封装：multipart 上传图片，解析 ``words_result`` 为纯文本。
文档：https://open.bigmodel.cn/api/paas/v4/files/ocr
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config import ZHIPU_API_KEY

logger = logging.getLogger(__name__)

ZHIPU_OCR_URL = "https://open.bigmodel.cn/api/paas/v4/files/ocr"
MAX_OCR_BYTES = 8 * 1024 * 1024

_ALLOWED_MIMES = frozenset({"image/png", "image/jpeg", "image/bmp"})


def validate_receipt_image_bytes(content: bytes, mime: str) -> None:
    """
    校验拍单据图片大小与 MIME（智谱 OCR 约束）。

    @param content 文件字节
    @param mime 归一化后的 ``image/*`` MIME
    @raises ValueError 不满足约束时的可读说明
    """
    if len(content) > MAX_OCR_BYTES:
        raise ValueError(f"图片超过智谱 OCR 限制（最大 {MAX_OCR_BYTES // (1024 * 1024)}MB），请压缩后重试")
    if mime not in _ALLOWED_MIMES:
        raise ValueError(f"不支持的图片类型「{mime}」，当前仅支持 PNG、JPG、JPEG、BMP")


def normalize_image_mime(content_type: str | None) -> str:
    """
    将客户端常见的 Content-Type 归一为智谱支持的 image/* 子类型。

    @param content_type 原始 MIME（可含 charset 等后缀）
    @returns 小写主类型，如 ``image/jpeg``
    """
    if not content_type:
        return "image/jpeg"
    main = content_type.split(";")[0].strip().lower()
    if main in ("image/jpg", "image/pjpeg"):
        return "image/jpeg"
    return main


def guess_mime_from_filename(filename: str | None) -> str | None:
    """
    根据扩展名推断图片 MIME（用于 ``application/octet-stream`` 等兜底）。

    @param filename 原始文件名
    @returns MIME 或 ``None``
    """
    if not filename:
        return None
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".bmp"):
        return "image/bmp"
    return None


def resolve_receipt_image_mime(content_type: str | None, filename: str | None) -> str:
    """
    综合 Content-Type 与文件名，得到用于校验与上传的 image MIME。

    @param content_type UploadFile.content_type
    @param filename UploadFile.filename
    @returns 归一化后的 MIME
    """
    norm = normalize_image_mime(content_type)
    if norm in _ALLOWED_MIMES:
        return norm
    guessed = guess_mime_from_filename(filename)
    if guessed:
        return guessed
    if norm == "application/octet-stream":
        return "image/jpeg"
    return norm


def ensure_upload_filename(filename: str | None, mime: str) -> str:
    """
    生成带合法扩展名的 multipart 文件名，便于对端识别格式。

    @param filename 原始文件名
    @param mime 归一化 MIME
    @returns 文件名
    """
    ext = { "image/png": ".png", "image/jpeg": ".jpg", "image/bmp": ".bmp" }.get(mime, ".jpg")
    base = (filename or "receipt").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base and base.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "bmp"):
        return base
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem}{ext}"


def _words_result_to_text(words_result: list[Any] | None) -> str:
    """
    将 ``words_result`` 按版面位置排序后合并为一段纯文本（行之间换行）。

    @param words_result 智谱返回的行列表
    @returns OCR 纯文本
    """
    if not words_result:
        return ""

    def sort_key(item: Any) -> tuple[int, int]:
        if not isinstance(item, dict):
            return (0, 0)
        loc = item.get("location") or {}
        return (int(loc.get("top", 0)), int(loc.get("left", 0)))

    rows: list[str] = []
    for item in sorted(words_result, key=sort_key):
        if not isinstance(item, dict):
            continue
        w = item.get("words")
        if w is None:
            continue
        s = str(w).strip()
        if s:
            rows.append(s)
    return "\n".join(rows)


async def zhipu_handwriting_ocr_bytes(
    image_bytes: bytes,
    filename: str,
    content_type: str,
    *,
    language_type: str = "CHN_ENG",
    probability: bool = False,
) -> dict[str, Any]:
    """
    调用智谱 ``/files/ocr`` 手写/印刷识别接口，返回 OCR 文本与原始元数据。

    @param image_bytes 图片二进制
    @param filename multipart 文件名（需含合法扩展名）
    @param content_type 文件 MIME（``image/png`` 等）
    @param language_type 语言类型，默认 ``CHN_ENG``
    @param probability 是否返回每行置信度（默认关闭以降低响应体积）
    @returns 包含 ``ocr_text``、``task_id``、``status``、``message``、``words_result_num`` 的字典
    @raises RuntimeError 业务失败或未配置密钥
    """
    if not ZHIPU_API_KEY:
        raise RuntimeError("未配置 ZHIPU_API_KEY")

    headers = {"Authorization": f"Bearer {ZHIPU_API_KEY.strip()}"}
    upload_name = ensure_upload_filename(filename, content_type)
    files = {"file": (upload_name, image_bytes, content_type)}
    data: dict[str, str] = {
        "tool_type": "hand_write",
        "language_type": language_type,
        "probability": "true" if probability else "false",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
        resp = await client.post(ZHIPU_OCR_URL, headers=headers, files=files, data=data)

    try:
        body: dict[str, Any] = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"智谱 OCR 响应非 JSON：HTTP {resp.status_code}") from exc

    if resp.status_code != 200:
        msg = body.get("error", body.get("message", resp.text[:300]))
        raise RuntimeError(f"智谱 OCR HTTP {resp.status_code}：{msg}")

    status = body.get("status")
    message = body.get("message") or ""
    task_id = body.get("task_id")

    if status != "succeeded":
        logger.warning("智谱 OCR 未成功: status=%s message=%s task_id=%s", status, message, task_id)
        raise RuntimeError(message or "智谱 OCR 识别失败")

    words_result = body.get("words_result") or []
    ocr_text = _words_result_to_text(words_result if isinstance(words_result, list) else [])

    return {
        "ocr_text": ocr_text,
        "task_id": task_id,
        "status": status,
        "message": message,
        "words_result_num": body.get("words_result_num", len(words_result) if isinstance(words_result, list) else 0),
    }
