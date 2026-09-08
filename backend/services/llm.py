"""通义千问 LLM 服务封装（V3 — 加重试）"""
import json
import logging
import uuid
import time
from dashscope import Generation
from dashscope import MultiModalConversation
from config import DASHSCOPE_API_KEY, QWEN_TEXT_MODEL, QWEN_VL_MODEL

logger = logging.getLogger(__name__)


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


def chat_text(system_prompt: str, user_message: str, **kwargs) -> str:
    global _LLM_DISABLED
    if _LLM_DISABLED:
        raise RuntimeError("LLM calls are temporarily suspended due to previous quota/authorization errors.")

    def _call():
        resp = Generation.call(
            api_key=DASHSCOPE_API_KEY,
            model=kwargs.get("model", QWEN_TEXT_MODEL),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            result_format="message",
            temperature=kwargs.get("temperature", 0.3),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        if resp.status_code == 200:
            return resp.output.choices[0].message.content
        raise RuntimeError(f"LLM error {resp.status_code}: {resp.message}")

    return _retry(_call, max_retries=kwargs.pop("max_retries", 3))


def chat_text_json(system_prompt: str, user_message: str, **kwargs) -> dict:
    """调用 LLM 并解析 JSON 响应，失败时抛出异常"""
    text = chat_text(system_prompt, user_message, **kwargs)
    return _extract_json(text)


def safe_chat_text_json(system_prompt: str, user_message: str, fallback: dict, **kwargs) -> dict:
    """
    调用 LLM 并解析 JSON，解析失败时返回 fallback 而非抛出异常
    适用于非关键路径（如健康评分、月度总结等）
    """
    global _LLM_DISABLED
    if _LLM_DISABLED:
        return fallback

    try:
        return chat_text_json(system_prompt, user_message, **kwargs)
    except Exception as e:
        logger.warning(f"LLM JSON 解析失败，使用 fallback: {e}")
        err_msg = str(e).lower()
        if "403" in err_msg or "401" in err_msg or "quota" in err_msg or "exhausted" in err_msg:
            _LLM_DISABLED = True
            logger.warning("已触发 LLM 熔断 (Circuit Breaker)，后续请求将直接使用 fallback，不再发起网络请求。")
        return fallback


def chat_vision(system_prompt: str, image_url: str, **kwargs) -> str:
    global _LLM_DISABLED
    if _LLM_DISABLED:
        raise RuntimeError("LLM calls are temporarily suspended due to previous quota/authorization errors.")

    def _call():
        messages = [
            {"role": "system", "content": [{"text": system_prompt}]},
            {
                "role": "user",
                "content": [
                    {"image": image_url},
                    {"text": "请识别并分析这张单据图片。"},
                ],
            },
        ]
        resp = MultiModalConversation.call(
            api_key=DASHSCOPE_API_KEY,
            model=kwargs.get("model", QWEN_VL_MODEL),
            messages=messages,
        )
        if resp.status_code == 200:
            return resp.output.choices[0].message.content[0]["text"]
        raise RuntimeError(f"Vision LLM error {resp.status_code}: {resp.message}")

    return _retry(_call, max_retries=kwargs.pop("max_retries", 2))


def chat_vision_json(system_prompt: str, image_url: str, **kwargs) -> dict:
    text = chat_vision(system_prompt, image_url, **kwargs)
    return _extract_json(text)
