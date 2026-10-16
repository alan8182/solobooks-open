# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""通义（DashScope）Paraformer 语音转文字 — 短录音文件识别"""
import os
import shutil
import subprocess
import tempfile
import time
from http import HTTPStatus
from typing import Optional

import httpx
import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, Transcription
from dashscope.utils.oss_utils import OssUtils

from config import DASHSCOPE_API_KEY

# 与百炼文档一致：https://help.aliyun.com/zh/model-studio/paraformer-realtime-speech-recognition-python-sdk
_ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "paraformer-realtime-v2")
# 文件转写（录音文件 API），作实时识别无文本时的兜底：https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk
_FILE_ASR_MODEL = os.getenv("DASHSCOPE_FILE_ASR_MODEL", "paraformer-v2")
_MAX_AUDIO_BYTES = int(os.getenv("MAX_SPEECH_UPLOAD_BYTES", str(3 * 1024 * 1024)))
_FFMPEG_BIN = os.getenv("FFMPEG_PATH", "ffmpeg")
# 设为 0/false 可关闭 OSS 文件转写（仅走实时识别，省一次上传与任务费用）
_FILE_ASR_FALLBACK = os.getenv("DASHSCOPE_FILE_ASR_FALLBACK", "1").strip().lower() not in ("0", "false", "no")
# 使用 ``oss://`` 作为 ``file_urls`` 时，必须告知 DashScope 先解析 OSS 资源，否则会报 ``FILE_DOWNLOAD_FAILED``
_OSS_RESOLVE_HEADERS = {"X-DashScope-OssResourceResolve": "enable"}


class _NoOpAsrCallback(RecognitionCallback):
    """同步 ``Recognition.call`` 仍需传入 callback；流式事件在此忽略。"""

    pass


def _is_mp4_family(content: bytes) -> bool:
    """
    检测是否为 ISO BMFF（如微信真机上 aac 常实为 ``.m4a`` / MP4 封装）。

    DashScope 实时识别的 ``format='aac'`` 期望裸 AAC 流，误传 m4a 易触发 ``NO_VALID_AUDIO_ERROR``。
    """
    return len(content) >= 12 and content[4:8] == b"ftyp"


def _guess_audio_format(filename: Optional[str], content_type: Optional[str], content: Optional[bytes] = None) -> str:
    """
    根据文件名或 Content-Type 推断 DashScope ``format`` 参数。

    支持：pcm、wav、mp3、opus、speex、aac、amr（以 SDK 文档为准）。
    """
    blob = content or b""
    # 优先基于文件头判断，避免微信临时文件缺少后缀导致格式猜错
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WAVE":
        return "wav"
    if len(blob) >= 3 and blob[:3] == b"ID3":
        return "mp3"
    if len(blob) >= 2 and blob[0] == 0xFF and (blob[1] & 0xE0) == 0xE0:
        return "mp3"
    if len(blob) >= 6 and blob[:6] == b"#!AMR\n":
        return "amr"
    if len(blob) >= 4 and blob[:4] == b"OggS":
        return "opus"
    if len(blob) >= 12 and blob[4:8] == b"ftyp":
        return "m4a"

    name = (filename or "").lower()
    ct = (content_type or "").lower()
    for ext, fmt in (
        (".mp3", "mp3"),
        (".wav", "wav"),
        (".aac", "aac"),
        (".m4a", "m4a"),
        (".amr", "amr"),
        (".opus", "opus"),
        (".pcm", "pcm"),
        (".speex", "speex"),
    ):
        if name.endswith(ext):
            return fmt
    if "mpeg" in ct or "mp3" in ct:
        return "mp3"
    if "wav" in ct:
        return "wav"
    if "aac" in ct or "m4a" in ct:
        return "aac"
    if "amr" in ct:
        return "amr"
    return "mp3"


def _resolve_ffmpeg() -> Optional[str]:
    """解析可执行的 ffmpeg 路径（支持 ``FFMPEG_PATH`` 为绝对路径）。"""
    raw = (_FFMPEG_BIN or "ffmpeg").strip().strip('"')
    if os.path.isfile(raw):
        return raw
    base = os.path.basename(raw)
    return shutil.which(base) or shutil.which("ffmpeg")


def _ffmpeg_to_wav_16k_mono(src_path: str, dst_path: str) -> None:
    """
    使用 ffmpeg 将任意可读音频转为 16kHz 单声道 PCM WAV，供 Paraformer 识别。

    :param src_path: 源文件路径
    :param dst_path: 输出 ``.wav`` 路径
    :raises RuntimeError: 未找到 ffmpeg 或转码失败
    """
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "服务器未找到 ffmpeg，无法解码 m4a/MP4 录音。"
            "请将小程序录音改为 mp3，或在服务器安装 ffmpeg 并配置 PATH（或环境变量 FFMPEG_PATH）。"
        )

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        src_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        dst_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("音频转码超时") from e
    except OSError as e:
        raise RuntimeError("无法执行 ffmpeg") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"音频转码失败: {tail.strip() or proc.returncode}")


def _sentences_to_text(sentence: object) -> str:
    """将 ``RecognitionResult.get_sentence()`` 转为单行文本。"""
    if sentence is None:
        return ""
    parts: list[str] = []
    if isinstance(sentence, list):
        for item in sentence:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]).strip())
    elif isinstance(sentence, dict) and sentence.get("text"):
        parts.append(str(sentence["text"]).strip())
    return " ".join(parts).strip()


def _walk_collect_text(obj: object, acc: list[str]) -> None:
    """
    深度遍历识别 ``output``，收集非空 ``text``（跳过 ``heartbeat`` 节点）。

    实时识别偶发仅返回无 ``end_time`` 的句片，SDK 的 ``sentences`` 列表为空，但 ``output`` 里仍有 ``text``。
    """
    if obj is None:
        return
    if isinstance(obj, dict):
        if obj.get("heartbeat"):
            return
        t = obj.get("text")
        if t is not None and str(t).strip():
            acc.append(str(t).strip())
        for v in obj.values():
            _walk_collect_text(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            _walk_collect_text(x, acc)


def _rich_realtime_text(result: object) -> str:
    """合并 ``get_sentence()`` 与 ``output`` 深度扫描的文本，去重保序。"""
    primary = _sentences_to_text(result.get_sentence())
    if primary.strip():
        return primary.strip()
    acc: list[str] = []
    out = getattr(result, "output", None)
    if out is not None:
        if isinstance(out, dict) and "sentence" in out:
            _walk_collect_text(out["sentence"], acc)
        _walk_collect_text(out, acc)
    seen: set[str] = set()
    ordered: list[str] = []
    for p in acc:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return " ".join(ordered).strip()


def _file_transcribe_paraformer_v2(local_path: str) -> str:
    """
    使用录音文件转写 API：先上传至百炼临时 OSS，再 ``paraformer-v2`` 异步任务拉取全文。

    :param local_path: 本地音频路径
    :returns: 识别文本，可能为空（纯静音等）
    :raises RuntimeError: 任务失败或网络错误
    """
    dashscope.api_key = DASHSCOPE_API_KEY
    model = _FILE_ASR_MODEL
    try:
        oss_url, _cert = OssUtils.upload(
            model=model,
            file_path=local_path,
            api_key=DASHSCOPE_API_KEY,
        )
    except Exception as e:
        raise RuntimeError(f"上传录音到识别服务失败: {e}") from e

    task = Transcription.async_call(
        model=model,
        file_urls=[oss_url],
        language_hints=["zh", "en"],
        api_key=DASHSCOPE_API_KEY,
        headers=dict(_OSS_RESOLVE_HEADERS),
    )
    done = Transcription.wait(task, api_key=DASHSCOPE_API_KEY)
    if done.status_code != HTTPStatus.OK:
        raise RuntimeError(done.message or f"文件识别失败 code={getattr(done, 'code', None)}")

    out = dict(done.output) if done.output else {}
    task_status = out.get("task_status")
    top_code = (out.get("code") or out.get("message") or "").strip()
    if task_status != "SUCCEEDED":
        # 纯静音/过短等：任务失败但属于「无有效语音」，按空文本处理而非抛错
        if top_code == "SUCCESS_WITH_NO_VALID_FRAGMENT":
            return ""
        raise RuntimeError(
            out.get("message")
            or out.get("code")
            or f"文件识别未成功: {task_status}"
        )

    texts: list[str] = []
    results = out.get("results") or []
    if not results:
        return ""

    for item in results:
        if item.get("subtask_status") == "FAILED":
            em = item.get("message") or item.get("error_message") or item.get("code") or ""
            if str(em).strip() == "SUCCESS_WITH_NO_VALID_FRAGMENT":
                continue
            raise RuntimeError(str(em) or "子任务失败")
        tu = item.get("transcription_url")
        if not tu:
            if item.get("text"):
                texts.append(str(item["text"]).strip())
            continue
        try:
            r = httpx.get(tu, timeout=60.0)
            r.raise_for_status()
            doc = r.json()
        except Exception as e:
            raise RuntimeError(f"拉取识别结果失败: {e}") from e
        for tr in doc.get("transcripts") or []:
            bt = (tr.get("text") or "").strip()
            if bt:
                texts.append(bt)

    return " ".join(texts).strip()


def transcribe_uploaded_audio(
    content: bytes,
    filename: Optional[str],
    content_type: Optional[str],
    scene: str = "asr",
    user_id: Optional[str] = None,
    company_id: Optional[str] = None,
) -> str:
    """
    将上传的音频字节写入临时文件并调用 Paraformer 非流式识别。

    :param content: 音频原始字节
    :param filename: 原始文件名（用于推断格式）
    :param content_type: HTTP Content-Type
    :returns: 识别出的纯文本；无有效句子时返回空字符串
    :raises RuntimeError: 识别失败或 DashScope 返回错误
    """
    if not content:
        raise RuntimeError("音频内容为空")
    if len(content) > _MAX_AUDIO_BYTES:
        raise RuntimeError(f"音频超过大小限制（{_MAX_AUDIO_BYTES} 字节）")

    from services.llm import _record_usage  # 用量统计与 LLM 同一出口（token 数 ASR 接口不提供，记 0）

    _asr_started = time.monotonic()

    def _record_asr(status: str, error_type: Optional[str] = None) -> None:
        _record_usage(
            model=_FILE_ASR_MODEL,
            scene=scene,
            user_id=user_id,
            company_id=company_id,
            duration_ms=int((time.monotonic() - _asr_started) * 1000),
            status=status,
            error_type=error_type,
        )

    fmt = _guess_audio_format(filename, content_type, content)
    suffix = ".m4a" if fmt == "m4a" else (f".{fmt}" if fmt else ".mp3")
    dashscope.api_key = DASHSCOPE_API_KEY

    tmp_path: Optional[str] = None
    wav_path: Optional[str] = None
    text_out = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)

        work_path = tmp_path
        work_fmt = fmt
        # m4a / 微信「aac」真机：ISO BMFF 须按容器处理，不能按裸 aac 送 DashScope
        if _is_mp4_family(content) or fmt == "m4a":
            fd_w, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd_w)
            _ffmpeg_to_wav_16k_mono(tmp_path, wav_path)
            work_path = wav_path
            work_fmt = "wav"

        rec = Recognition(
            model=_ASR_MODEL,
            callback=_NoOpAsrCallback(),
            format=work_fmt,
            sample_rate=16000,
        )
        result = rec.call(work_path, language_hints=["zh", "en"])
        if result.status_code != HTTPStatus.OK:
            msg = result.message or f"语音识别失败 code={result.code}"
            if msg == "NO_VALID_AUDIO_ERROR" and fmt == "aac" and not _is_mp4_family(content):
                msg += "（请尝试改用 mp3 录音或检查文件是否损坏）"
            raise RuntimeError(msg)

        text_out = _rich_realtime_text(result)
        if (not text_out.strip()) and _FILE_ASR_FALLBACK:
            text_out = _file_transcribe_paraformer_v2(work_path)
        _record_asr("ok")
    except Exception as exc:  # noqa: BLE001
        _record_asr("error", error_type=type(exc).__name__)
        raise
    finally:
        for p in (wav_path, tmp_path):
            if p and os.path.isfile(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    return text_out.strip()
