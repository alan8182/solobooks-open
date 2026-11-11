# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""获客钩子「AI 账本体检器」公开路由 (Health Scan Router).

免登录公开访问，免存即焚，配备内存级轻量防刷保护。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from services.health_scan_service import run_health_scan

router = APIRouter(prefix="/api/health-scan", tags=["health-scan"])

# 内存级简易频次限制：同 IP 每 60 秒最多 20 次请求
_RATE_LIMIT_CACHE: Dict[str, List[float]] = {}
_MAX_REQUESTS_PER_MINUTE = 20
_CLEANUP_INTERVAL = 300.0
_LAST_CLEANUP = time.monotonic()


def _check_rate_limit(client_ip: str) -> None:
    global _LAST_CLEANUP
    now = time.monotonic()

    # 简单定期清理过期记录
    if now - _LAST_CLEANUP > _CLEANUP_INTERVAL:
        expired_ips = [ip for ip, timestamps in _RATE_LIMIT_CACHE.items() if not timestamps or now - timestamps[-1] > 60]
        for ip in expired_ips:
            _RATE_LIMIT_CACHE.pop(ip, None)
        _LAST_CLEANUP = now

    timestamps = _RATE_LIMIT_CACHE.setdefault(client_ip, [])
    # 保留最近 60 秒内的请求时间戳
    timestamps[:] = [t for t in timestamps if now - t < 60]

    if len(timestamps) >= _MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="体检请求过于频繁，请稍后再试。")

    timestamps.append(now)


class HealthScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: StrictStr = Field(..., description="balance 或 quiz")
    rows: Optional[List[Dict[str, Any]]] = Field(None, max_length=10000)
    answers: Optional[Dict[str, StrictStr]] = None
    profile: Optional[Dict[str, Any]] = None
    balanceMeta: Optional[Dict[str, Any]] = None


@router.post("/analyze")
async def analyze_health_scan(req: HealthScanRequest, request: Request):
    """公开免登录：执行 AI 账本体检分析，免存即焚。"""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    if req.mode not in ("balance", "quiz"):
        raise HTTPException(status_code=400, detail="不支持的体检模式，必须为 balance 或 quiz")

    if req.mode == "balance" and not req.rows:
        raise HTTPException(status_code=400, detail="科目余额表数据不能为空")

    try:
        report = run_health_scan(
            mode=req.mode,
            rows=req.rows,
            answers=req.answers,
            profile=req.profile,
            balanceMeta=req.balanceMeta,
            client_ip=client_ip,
        )
        return {"ok": True, "report": report}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="体检分析失败，请稍后重试。") from exc
