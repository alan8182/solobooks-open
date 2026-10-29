# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""LLM 服务门面（供应商无关）。

所有 LLM 调用（文本、视觉/多模态、agent 工具循环）统一走 OpenAI 兼容协议：
  - 端点  config.LLM_BASE_URL      （qwen: maas.qianwenaiapi.com/compatible-mode/v1）
  - 密钥  config.LLM_API_KEY       （默认复用 DASHSCOPE_API_KEY）
  - 模型  config.QWEN_TEXT_MODEL / config.QWEN_VL_MODEL

未来切换 DeepSeek / GLM 等多模态供应商时，仅需修改 .env 中上述四项，
业务代码与工具层零改动（前提：目标供应商提供 OpenAI 兼容端点，二者均有）。

不在本门面内的 AI 通道（各自独立协议，与 LLM 供应商解耦）：
  - ASR 语音转写：dashscope Paraformer（services/asr.py）
  - 单据 OCR：智谱 OCR API（services/zhipu_ocr.py）
"""
import base64
import json
import logging
import mimetypes
import os
import uuid
import time
import asyncio
import threading
from typing import AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI, OpenAI
from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_SEND_ENABLE_THINKING,
    QWEN_TEXT_MODEL,
    QWEN_VL_MODEL,
    QWEN_ENABLE_THINKING,
)
from core.errors import AccountingError
from services.quota_service import ensure_within_quota, ensure_within_quota_async

logger = logging.getLogger(__name__)

_oai_sync_client: Optional[OpenAI] = None
_oai_async_client: Optional[AsyncOpenAI] = None


def _oai() -> OpenAI:
    global _oai_sync_client
    if _oai_sync_client is None:
        _oai_sync_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=90)
    return _oai_sync_client


def _oai_async() -> AsyncOpenAI:
    global _oai_async_client
    if _oai_async_client is None:
        _oai_async_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=180)
    return _oai_async_client


def _uid():
    return uuid.uuid4().hex[:12]


def _retry(fn, max_retries=3, delay=1.0):
    """简单重试机制"""
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_msg = str(e).lower()
            if "403" in err_msg or "401" in err_msg or "quota" in err_msg or "exhausted" in err_msg:
                raise e
            if attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_err


def _extract_json(text: str) -> dict:
    """
    从 LLM 响应中提取 JSON（#14 增强版）
    支持：
    - 纯 JSON 字符串
    - ```json ... ``` 代码块
    - ``` ... ``` 代码块
    - 文本前后有多余说明的情况（提取第一个完整 JSON 对象）
    """
    text = text.strip()
    # 处理 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        # 跳过第一行（```json 或 ```）
        inner_lines = lines[1:]
        # 移除末尾的 ```
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        text = "\n".join(inner_lines).strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取第一个 JSON 对象或数组（处理前后有文字说明的情况）
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        end_idx = text.rfind(end_char)
        if end_idx > start_idx:
            try:
                return json.loads(text[start_idx:end_idx + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"无法从 LLM 响应中提取有效 JSON，原始内容：{text[:300]}")


_LLM_DISABLED = False
_LLM_DISABLED_AT: Optional[float] = None
# 熔断后半开冷却窗：超时后放行下一次真实请求试探，成功即自动恢复（无需重启进程）
_LLM_CIRCUIT_COOLDOWN_SECONDS = 600.0


def _circuit_is_open() -> bool:
    """熔断是否仍然打开；冷却窗已过则转入半开（放行试探并复位）。"""
    global _LLM_DISABLED, _LLM_DISABLED_AT
    if not _LLM_DISABLED:
        return False
    if _LLM_DISABLED_AT is not None and (
        time.monotonic() - _LLM_DISABLED_AT >= _LLM_CIRCUIT_COOLDOWN_SECONDS
    ):
        _LLM_DISABLED = False
        _LLM_DISABLED_AT = None
        logger.info("LLM 熔断冷却结束（%.0fs），恢复调用（半开试探）", _LLM_CIRCUIT_COOLDOWN_SECONDS)
        return False
    return True


# ---------------------------------------------------------------------------
# 用量埋点（管理后台 M1 + 配额计数）：一次逻辑调用记一行；fire-and-forget，
# 绝不影响业务。调用方通过 kwargs 传归属：scene / user_id / company_id（不转发给供应商）。
# ---------------------------------------------------------------------------


async def _write_usage_async(row: dict) -> None:
    from db.models import LlmUsage
    from db.session import session_scope

    from services.quota_service import record_consumption_async

    async with session_scope() as session:
        session.add(LlmUsage(**row))
        if _should_count_quota(row):
            await record_consumption_async(session, user_id=row["user_id"], tokens=row["total_tokens"])


def _write_usage_sync(row: dict) -> None:
    from sqlalchemy.orm import Session

    from db.models import LlmUsage
    from db.session import get_sync_engine

    from services.quota_service import record_consumption_sync

    engine = get_sync_engine()
    with Session(engine) as session:
        session.add(LlmUsage(**row))
        if _should_count_quota(row):
            record_consumption_sync(session, user_id=row["user_id"], tokens=row["total_tokens"])
        session.commit()


def _should_count_quota(row: dict) -> bool:
    """仅成功调用且有归属时累计周期计数（失败与无 user 的调用不占额度）。"""
    return bool(row.get("user_id")) and row.get("status") == "ok" and int(row.get("total_tokens") or 0) > 0


def _swallow_task_error(task: "asyncio.Task") -> None:
    try:
        task.result()
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_usage 写入失败（忽略）: %s", exc)


def _record_usage(
    *,
    model: str,
    scene: Optional[str] = None,
    user_id: Optional[str] = None,
    company_id: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: int = 0,
    status: str = "ok",
    error_type: Optional[str] = None,
) -> None:
    """记录一次 LLM/OCR/ASR 调用。任何失败都不抛出、不阻塞调用方。"""
    row = {
        "user_id": (user_id or None)[:128] if user_id else None,
        "company_id": (company_id or None)[:128] if company_id else None,
        "scene": (scene or "other")[:64],
        "model": (model or "")[:128],
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int((prompt_tokens or 0) + (completion_tokens or 0)),
        "duration_ms": int(max(0, duration_ms or 0)),
        "status": status,
        "error_type": (error_type or None)[:128] if error_type else None,
    }

    def _log_failure(exc: Exception) -> None:
        logger.debug("llm_usage 写入失败（忽略）: %s", exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 线程池里的同步路由：无事件循环，用独立同步引擎写，避免跨 loop 连接问题
        threading.Thread(
            target=lambda: _guarded_sync_write(row, _log_failure), daemon=True
        ).start()
        return
    loop.create_task(_guarded_async_write(row, _log_failure)).add_done_callback(_swallow_task_error)


async def _guarded_async_write(row: dict, _log) -> None:
    try:
        await _write_usage_async(row)
    except Exception as exc:  # noqa: BLE001
        _log(exc)


def _guarded_sync_write(row: dict, _log) -> None:
    try:
        _write_usage_sync(row)
    except Exception as exc:  # noqa: BLE001
        _log(exc)


def _usage_kwargs(kwargs: dict) -> dict:
    """提取埋点归属参数（不转发给供应商）。"""
    return {
        "scene": kwargs.get("scene"),
        "user_id": kwargs.get("user_id"),
        "company_id": kwargs.get("company_id"),
    }


def llm_circuit_state() -> dict:
    """熔断器只读状态（管理后台概览展示用，不提供写入口）。"""
    return {"tripped": _LLM_DISABLED}


def chat_text(system_prompt: str, user_message: str, **kwargs) -> str:
    global _LLM_DISABLED
    # 配额卡口：套餐未设限（当前默认）时零开销直通；设限后超限抛 QuotaExceededError(402)
    ensure_within_quota(kwargs.get("user_id"))
    model = kwargs.get("model", QWEN_TEXT_MODEL)
    attribution = _usage_kwargs(kwargs)
    started = time.monotonic()

    if _circuit_is_open():
        _record_usage(model=model, duration_ms=0, status="circuit_open", error_type="circuit_open", **attribution)
        raise RuntimeError("LLM calls are temporarily suspended due to previous quota/authorization errors.")

    def _call():
        resp = _oai().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=kwargs.get("temperature", 0.3),
            max_tokens=kwargs.get("max_tokens", 2048),
            # 同步 JSON 提取路径固定关闭深度思考，控制时延（Qwen 专有参数，其他供应商不发送）
            **({"extra_body": {"enable_thinking": False}} if LLM_SEND_ENABLE_THINKING else {}),
        )
        content = resp.choices[0].message.content
        if not content:
            # 空回复按可重试错误抛出：偶发空响应重打一次即可，
            # 放在 _retry 内才吃得到自动重试，直接 raise 会让调用方白白失败
            raise RuntimeError("LLM returned empty content")
        return resp

    try:
        resp = _retry(_call, max_retries=kwargs.pop("max_retries", 3))
        content = resp.choices[0].message.content
        usage = getattr(resp, "usage", None)
        _record_usage(
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="ok",
            **attribution,
        )
        return content
    except Exception as exc:  # noqa: BLE001
        _record_usage(
            model=model,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="error",
            error_type=type(exc).__name__,
            **attribution,
        )
        raise


def chat_text_json(system_prompt: str, user_message: str, *, retry_on_parse: bool = False, **kwargs) -> dict:
    """调用 LLM 并解析 JSON，失败时抛出异常。

    ``retry_on_parse=True`` 时，若首次返回无法解析为 JSON，追加"只输出 JSON"的
    约束重试一次（仅覆盖解析失败；网络错误由 chat_text 的 max_retries 处理）。
    """
    text = chat_text(system_prompt, user_message, **kwargs)
    try:
        return _extract_json(text)
    except ValueError:
        if not retry_on_parse:
            raise
    strict_message = (
        f"{user_message}\n\n上一次回复无法解析为 JSON。"
        "请只输出一个 JSON 对象，不要解释、不要 Markdown、不要代码块。"
    )
    text = chat_text(system_prompt, strict_message, **kwargs)
    return _extract_json(text)


def safe_chat_text_json(system_prompt: str, user_message: str, fallback: dict, **kwargs) -> dict:
    """
    调用 LLM 并解析 JSON，解析失败时返回 fallback 而非抛出异常
    适用于非关键路径（如健康评分、月度总结等）
    """
    global _LLM_DISABLED
    if _circuit_is_open():
        _record_usage(
            model=kwargs.get("model", QWEN_TEXT_MODEL),
            duration_ms=0,
            status="circuit_open",
            error_type="circuit_open",
            **_usage_kwargs(kwargs),
        )
        return fallback

    try:
        return chat_text_json(system_prompt, user_message, **kwargs)
    except AccountingError:
        # 业务/配额类异常必须穿透兜底：配额超限要变成 402 让前端引导升级，
        # 静默降级会让用户以为功能坏了而不是额度用完了
        raise
    except Exception as e:
        logger.warning(f"LLM JSON 解析失败，使用 fallback: {e}")
        err_msg = str(e).lower()
        global _LLM_DISABLED, _LLM_DISABLED_AT
        if "403" in err_msg or "401" in err_msg or "quota" in err_msg or "exhausted" in err_msg:
            _LLM_DISABLED = True
            _LLM_DISABLED_AT = time.monotonic()
            logger.warning(
                "已触发 LLM 熔断 (Circuit Breaker)，%.0f 秒内直接使用 fallback；之后自动半开试探恢复。",
                _LLM_CIRCUIT_COOLDOWN_SECONDS,
            )
        return fallback


def _to_image_data_url(source: str) -> str:
    """把图片来源归一化为 OpenAI 协议可用的 URL：http(s)/data URI 透传，本地文件转 base64 data URI。"""
    if source.startswith(("http://", "https://", "data:")):
        return source
    path = source
    if path.startswith("file://"):
        from urllib.parse import unquote, urlparse

        path = os.path.abspath(os.path.join(urlparse(path).netloc, unquote(urlparse(path).path)))
    if not os.path.isfile(path):
        raise ValueError(f"图片来源无法识别（非 URL 且文件不存在）：{source[:120]}")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def chat_vision(system_prompt: str, image_url: str, **kwargs) -> str:
    """多模态调用（OpenAI 协议）：image_url 支持 http(s)/data URI/本地文件路径。"""
    global _LLM_DISABLED
    ensure_within_quota(kwargs.get("user_id"))
    model = kwargs.get("model", QWEN_VL_MODEL)
    attribution = _usage_kwargs(kwargs)
    started = time.monotonic()

    if _circuit_is_open():
        _record_usage(model=model, duration_ms=0, status="circuit_open", error_type="circuit_open", **attribution)
        raise RuntimeError("LLM calls are temporarily suspended due to previous quota/authorization errors.")

    data_url = _to_image_data_url(image_url)

    def _call():
        return _oai().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": "请识别并分析这张单据图片。"},
                    ],
                },
            ],
            max_tokens=kwargs.get("max_tokens", 2048),
            **({"extra_body": {"enable_thinking": False}} if LLM_SEND_ENABLE_THINKING else {}),
        )

    try:
        resp = _retry(_call, max_retries=kwargs.pop("max_retries", 2))
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("VL LLM returned empty content")
        usage = getattr(resp, "usage", None)
        _record_usage(
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="ok",
            **attribution,
        )
        return content
    except Exception as exc:  # noqa: BLE001
        _record_usage(
            model=model,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="error",
            error_type=type(exc).__name__,
            **attribution,
        )
        raise


def chat_vision_json(system_prompt: str, image_url: str, **kwargs) -> dict:
    text = chat_vision(system_prompt, image_url, **kwargs)
    return _extract_json(text)


# ---------------------------------------------------------------------------
# V4：Agent 循环支持（async + function calling + 流式）
# ---------------------------------------------------------------------------

async def _retry_async(factory, max_retries=3, delay=1.0):
    """对连接建立阶段做异步重试；流已建立后的中断不重试。"""
    last_err = None
    for attempt in range(max_retries):
        try:
            return await factory()
        except Exception as e:
            last_err = e
            err_msg = str(e).lower()
            if "403" in err_msg or "401" in err_msg or "quota" in err_msg or "exhausted" in err_msg:
                raise
            if attempt < max_retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
    raise last_err


async def chat_tools_stream_async(
    system_prompt: str,
    messages: List[dict],
    tools: Optional[List[dict]] = None,
    **kwargs,
) -> AsyncIterator[dict]:
    """
    Agent 单回合流式调用（OpenAI 兼容模式 + function calling），逐块产出归一化事件：

      {'type': 'delta', 'content': str}     正文增量
      {'type': 'reasoning', 'content': str} 思考过程增量（前端可忽略）
      {'type': 'final', 'content': str, 'tool_calls': [{'id','name','arguments'}]}

    回合结束必然产出且仅产出一个 final 事件。
    """
    global _LLM_DISABLED
    # 配额卡口：设限后每回合（一次 LLM 调用）检查一次，超限抛 QuotaExceededError(402)
    await ensure_within_quota_async(kwargs.get("user_id"))
    model = kwargs.get("model", QWEN_TEXT_MODEL)
    attribution = _usage_kwargs(kwargs)
    started = time.monotonic()

    if _circuit_is_open():
        _record_usage(model=model, duration_ms=0, status="circuit_open", error_type="circuit_open", **attribution)
        raise RuntimeError("LLM calls are temporarily suspended due to previous quota/authorization errors.")

    payload: Dict[str, object] = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + list(messages),
        "stream": True,
        "max_tokens": kwargs.get("max_tokens", 4096),
        # 用量采集（M1）：OpenAI 兼容协议标准字段，从最后一个 chunk 读 usage
        "stream_options": {"include_usage": True},
    }
    # Qwen 思考开关；优先使用传入参数，未传则回退到配置；DeepSeek 等不需要时置 0
    enable_thinking = kwargs.get("enable_thinking")
    if enable_thinking is None:
        enable_thinking = QWEN_ENABLE_THINKING
    if LLM_SEND_ENABLE_THINKING:
        payload["extra_body"] = {"enable_thinking": bool(enable_thinking)}
    if tools:
        payload["tools"] = tools

    async def _open():
        try:
            return await _oai_async().chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            # 兼容性兜底：个别供应商不认 stream_options 时去掉该参数重试（只影响统计，不影响业务）
            if "stream_options" in str(exc).lower() or "include_usage" in str(exc).lower():
                payload.pop("stream_options", None)
                logger.info("provider rejected stream_options, retrying without usage capture: %s", exc)
                return await _oai_async().chat.completions.create(**payload)
            raise

    try:
        responses = await _retry_async(_open, max_retries=kwargs.pop("max_retries", 3))
    except Exception as exc:  # noqa: BLE001
        # 连接建立阶段失败（鉴权/配额/网络）：同样记一行 error，便于后台排查
        _record_usage(
            model=model,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="error",
            error_type=type(exc).__name__,
            **attribution,
        )
        raise

    content_parts: List[str] = []
    pending_tools: Dict[int, dict] = {}
    stream_usage: Optional[object] = None

    try:
        async for chunk in responses:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                stream_usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield {"type": "reasoning", "content": reasoning}
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "delta", "content": delta.content}
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = tc.index or 0
                slot = pending_tools.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        _record_usage(
            model=model,
            prompt_tokens=getattr(stream_usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(stream_usage, "completion_tokens", 0) or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="ok",
            **attribution,
        )
    except Exception as exc:  # noqa: BLE001
        # 消费方提前断开（GeneratorExit/CancelledError）不算调用失败，不重复记一行
        if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
            raise
        _record_usage(
            model=model,
            duration_ms=int((time.monotonic() - started) * 1000),
            status="error",
            error_type=type(exc).__name__,
            **attribution,
        )
        raise

    tool_calls = [
        {"id": slot["id"] or f"call_{i}", "name": slot["name"], "arguments": slot["arguments"] or "{}"}
        for i, slot in sorted(pending_tools.items())
        if slot["name"]
    ]
    yield {"type": "final", "content": "".join(content_parts), "tool_calls": tool_calls}
