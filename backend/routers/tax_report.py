# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Official tax report export routes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from deps.auth import AuthContext, require_auth
from services.tax_filing_export_service import (
    build_statement_data,
    default_completed_period,
    export_month_quarter_xls,
)

router = APIRouter(prefix="/api/tax-report", tags=["tax-report"], dependencies=[Depends(require_auth)])
logger = logging.getLogger(__name__)


@router.get("/xiaoqiye/month-quarter/preview")
async def preview_xiaoqiye_month_quarter(
    period: str | None = Query(None, description="YYYY-MM or YYYY-Qn; defaults to the latest completed period"),
    period_type: Literal["month", "quarter"] = Query("month"),
    ctx: AuthContext = Depends(require_auth),
):
    """Preview checks and high-level totals before exporting the official XLS template."""
    try:
        resolved_period = period or default_completed_period(period_type)
        data = await build_statement_data(ctx.user_id, ctx.company_id, resolved_period, period_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "period": data.period.label if data.period else resolved_period,
        "companyName": data.company_name,
        "taxId": data.tax_id,
        "checks": data.checks,
        "totals": {
            "cash": data.balance_current.get("cash", 0),
            "revenue": data.profit_current.get("revenue", 0),
            "managementExpense": data.profit_current.get("management_expense", 0),
            "salesCash": data.cash_current.get("sales_cash", 0),
        },
    }


@router.get("/xiaoqiye/month-quarter/export")
async def export_xiaoqiye_month_quarter(
    period: str | None = Query(None, description="YYYY-MM or YYYY-Qn; defaults to the latest completed period"),
    period_type: Literal["month", "quarter"] = Query("month"),
    ctx: AuthContext = Depends(require_auth),
):
    """Export the official small-business accounting-standard monthly/quarterly XLS."""
    try:
        resolved_period = period or default_completed_period(period_type)
        data = await build_statement_data(ctx.user_id, ctx.company_id, resolved_period, period_type)
        blocking_checks = [check["message"] for check in data.checks if check.get("level") == "error"]
        if blocking_checks:
            raise ValueError("报税前校验未通过：" + "；".join(blocking_checks))
        path = export_month_quarter_xls(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (FileNotFoundError, RuntimeError) as e:
        # FileNotFoundError/RuntimeError 可能携带内部路径等敏感信息，仅记日志不下发
        logger.exception("小企业报表导出失败 company=%s period=%s", ctx.company_id, period)
        raise HTTPException(status_code=500, detail="报表文件生成失败，请稍后重试或联系管理员") from e

    filename = Path(path).name
    quoted = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"}
    return FileResponse(
        path,
        media_type="application/vnd.ms-excel",
        filename=filename,
        headers=headers,
    )

