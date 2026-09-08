"""临时连通性自检：不写日志、不打印密钥明文。用法：在 backend 目录下 python scripts/smoke_keys.py"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DASHSCOPE_API_KEY, ZHIPU_API_KEY  # noqa: E402


def mask(s: str | None) -> str:
    if not s:
        return "(未配置)"
    s = s.strip()
    if len(s) <= 8:
        return "***"
    return s[:4] + "…" + s[-4:]


def main() -> None:
    print("DASHSCOPE_API_KEY:", mask(DASHSCOPE_API_KEY))
    print("ZHIPU_API_KEY:", mask(ZHIPU_API_KEY))

    from services.llm import chat_text_json  # noqa: E402

    try:
        out = chat_text_json(
            'Reply with exactly this JSON object and nothing else: {"ok": true}',
            "ping",
            max_tokens=32,
            temperature=0,
        )
        print("DashScope(chat_text_json):", json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print("DashScope 失败:", e)
        raise SystemExit(1) from e

    if not ZHIPU_API_KEY or not ZHIPU_API_KEY.strip():
        print("未配置 ZHIPU_API_KEY，跳过智谱 OCR 请求。")
        return

    png = ROOT.parent / "solobooks" / "static" / "solo" / "user-avatar.png"
    if not png.is_file():
        print("跳过智谱 OCR：未找到示例图", png)
        return

    from services.zhipu_ocr import zhipu_handwriting_ocr_bytes  # noqa: E402

    async def run_ocr():
        raw = png.read_bytes()
        return await zhipu_handwriting_ocr_bytes(
            raw,
            png.name,
            "image/png",
            probability=False,
        )

    try:
        ocr = asyncio.run(run_ocr())
        print(
            "Zhipu OCR:",
            json.dumps(
                {
                    "status": ocr.get("status"),
                    "task_id": ocr.get("task_id"),
                    "words_result_num": ocr.get("words_result_num"),
                    "ocr_text_preview": (ocr.get("ocr_text") or "")[:120],
                },
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        print("智谱 OCR 失败:", e)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
