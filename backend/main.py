"""SoloBooks FastAPI 入口。"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate the production database; only tests may create SQLite tables."""
    from sqlalchemy import text
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import database_url, dispose_engine, get_engine

    url = database_url()
    test_mode = os.getenv("ACCOUNTING_TEST_MODE") == "1"
    if not test_mode and not url.startswith("postgresql+"):
        raise RuntimeError("正式运行时 DATABASE_URL 必须使用 PostgreSQL")
    async with get_engine().begin() as connection:
        if test_mode:
            await connection.run_sync(Base.metadata.create_all)
        else:
            await connection.execute(text("SELECT 1"))
    # MCP 子应用（/mcp）的 session manager 生命周期必须显式接管：
    # FastAPI 不会自动运行挂载子应用的 lifespan；lifespan(app) 返回异步上下文
    from agent.mcp_server import mcp_app

    async with mcp_app.lifespan(_app):
        yield
    await dispose_engine()


app = FastAPI(title="梭罗记账 API", version="3.0.0", lifespan=lifespan)


from core.errors import AccountingError


@app.exception_handler(AccountingError)
async def accounting_error_handler(_request: Request, exc: AccountingError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail()})

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers.record import router as record_router
from routers.copilot import router as copilot_router
from routers.ledger import router as ledger_router
from routers.report import router as report_router
from routers.voucher import router as voucher_router
from routers.auth import router as auth_router
from routers.asset import router as asset_router
from routers.todo import router as todo_router
from routers.category import router as category_router
from routers.opening import router as opening_router
from routers.aging import router as aging_router
from routers.bank import router as bank_router
from routers.recurring import router as recurring_router
from routers.search import router as search_router
from routers.invoice import router as invoice_router
from routers.audit import router as audit_router
from routers.private_expense import router as private_expense_router
from routers.salary import router as salary_router
from routers.tax_report import router as tax_report_router
from routers.wechat_bill import router as wechat_bill_router
from routers.accounting import router as accounting_router
from routers.rd import router as rd_router
from routers.agent import router as agent_router
from routers.tax_filing import router as tax_filing_router
from routers.mcp_tokens import router as mcp_tokens_router

app.include_router(record_router)
app.include_router(copilot_router)
app.include_router(ledger_router)
app.include_router(report_router)
app.include_router(voucher_router)
app.include_router(auth_router)
app.include_router(asset_router)
app.include_router(todo_router)
app.include_router(category_router)
app.include_router(opening_router)
app.include_router(aging_router)
app.include_router(bank_router)
app.include_router(recurring_router)
app.include_router(search_router)
app.include_router(invoice_router)
app.include_router(audit_router)
app.include_router(private_expense_router)
app.include_router(salary_router)
app.include_router(tax_report_router)
app.include_router(wechat_bill_router)
app.include_router(accounting_router)
app.include_router(rd_router)
app.include_router(agent_router)
app.include_router(tax_filing_router)
app.include_router(mcp_tokens_router)


@app.get("/api/health")
async def health():
    from sqlalchemy import text
    from db.session import get_engine

    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "service": "SoloBooks",
        "database": "test" if os.getenv("ACCOUNTING_TEST_MODE") == "1" else "postgresql",
    }


# MCP（Model Context Protocol）端点：PAT 鉴权由 agent/mcp_server.PatAuthMiddleware
# 独立完成，不走 AUTH_DEV_FALLBACK 开发兜底。挂载在根路径，且必须位于全部业务
# 路由（含 /api/health）之后——未命中 /api/* 的请求才落到 MCP 应用（内部路径 /mcp）
from agent.mcp_server import app as mcp_asgi_app  # noqa: E402

app.mount("/", mcp_asgi_app)
