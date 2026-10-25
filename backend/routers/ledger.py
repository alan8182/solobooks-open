# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""账本路由"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import ChartAccount
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import AccountCreate, AccountUpdate, AccountNode
from services.accounts_service import (
    create_account, update_account, delete_account, ensure_sub_account,
)
from services.ledger_service import (
    get_balance_summary,
    query_advance_balances,
    query_chronological,
    query_general_ledger,
    query_multi_column,
    query_partner_balances,
    query_sub_ledger,
)
from services.ledger_book_service import (
    BookTooLarge,
    InvalidPeriod,
    build_ledger_book,
)
from services.ledger_book_snapshot_service import (
    SnapshotError,
    diff_snapshots,
    get_snapshot,
    issue_snapshot,
    list_snapshots,
    void_snapshot,
)
from services.accounting_core_service import ensure_tenant

router = APIRouter(prefix="/api/ledger", tags=["ledger"], dependencies=[Depends(require_auth)])

# Chart of accounts 数据（flat，向后兼容）
_CHART_OF_ACCOUNTS = [
    {"code": "1001", "name": "库存现金", "type": "流动资产"},
    {"code": "1002", "name": "银行存款", "type": "流动资产"},
    {"code": "1122", "name": "应收账款", "type": "流动资产"},
    {"code": "1221", "name": "其他应收款", "type": "流动资产"},
    {"code": "1601", "name": "固定资产", "type": "非流动资产"},
    {"code": "2202", "name": "应付账款", "type": "流动负债"},
    {"code": "2221", "name": "应交税费", "type": "流动负债"},
    {"code": "2241", "name": "其他应付款", "type": "流动负债"},
    {"code": "3001", "name": "实收资本", "type": "所有者权益"},
    {"code": "3103", "name": "本年利润", "type": "所有者权益"},
    {"code": "5401001", "name": "主营业务成本-人力", "type": "成本"},
    {"code": "4301", "name": "研发支出-费用化支出", "type": "成本"},
    {"code": "5602006", "name": "管理费用-差旅费", "type": "费用"},
    {"code": "5602004", "name": "管理费用-办公费", "type": "费用"},
    {"code": "5602002", "name": "管理费用-业务招待费", "type": "费用"},
    {"code": "5602013", "name": "管理费用-租赁费", "type": "费用"},
    {"code": "5602999", "name": "管理费用-其他", "type": "费用"},
    {"code": "5001", "name": "主营业务收入", "type": "收入"},
    {"code": "5051", "name": "其他业务收入", "type": "收入"},
    {"code": "5301", "name": "营业外收入", "type": "收入"},
    {"code": "5601016", "name": "销售费用-业务宣传费", "type": "费用"},
    {"code": "5603005", "name": "财务费用-利息收入", "type": "费用"},
]


@router.get("/accounts")
async def get_accounts(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """从权威公司科目表获取可用科目。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    accounts = (
        await session.execute(
            select(ChartAccount)
            .where(ChartAccount.company_id == ctx.company_id, ChartAccount.is_active.is_(True))
            .order_by(ChartAccount.code)
        )
    ).scalars().all()
    return {
        "accounts": [
            {
                "code": account.code,
                "name": account.name,
                "type": account.type,
                "parentCode": account.parent_code,
                "level": account.level,
                "isLeaf": account.is_leaf,
                "quantityEnabled": account.quantity_enabled,
                "unit": account.unit,
            }
            for account in accounts
        ]
    }


@router.get("/accounts/tree", response_model=list[AccountNode])
async def get_accounts_tree(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """从权威科目表构造分组树，并确保企业及系统科目已初始化。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    accounts = (
        await session.execute(
            select(ChartAccount)
            .where(ChartAccount.company_id == ctx.company_id, ChartAccount.is_active.is_(True))
            .order_by(ChartAccount.level, ChartAccount.code)
        )
    ).scalars().all()
    nodes = {
        account.code: {
            "id": account.id,
            "company_id": account.company_id,
            "code": account.code,
            "name": account.name,
            "parent_code": account.parent_code,
            "level": account.level,
            "category": account.category,
            "type": account.type,
            "is_leaf": account.is_leaf,
            "is_system": account.is_system,
            "is_active": account.is_active,
            "quantity_enabled": account.quantity_enabled,
            "unit": account.unit,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "children": [],
        }
        for account in accounts
    }
    category_order = ["资产类", "负债类", "权益类", "成本类", "损益-收入类", "损益-费用类"]
    categories = category_order + sorted({
        str(account.category) for account in accounts
        if account.category and account.category not in category_order
    })
    now = datetime.now(timezone.utc).isoformat()
    groups = {
        category: {
            "id": f"grp-{ctx.company_id}-{index}",
            "company_id": ctx.company_id,
            "code": category,
            "name": category,
            "parent_code": None,
            "level": 0,
            "category": None,
            "type": None,
            "is_leaf": False,
            "is_system": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            "children": [],
        }
        for index, category in enumerate(categories)
    }
    legacy_group_codes = {
        account.code
        for account in accounts
        if account.code in groups and not account.parent_code and not account.category
    }
    for account in accounts:
        if account.level == 0 or account.code in legacy_group_codes:
            continue
        node = nodes[account.code]
        if (
            account.parent_code
            and account.parent_code in nodes
            and account.parent_code not in legacy_group_codes
        ):
            nodes[account.parent_code]["children"].append(node)
        else:
            category = str(account.category or account.parent_code or "")
            if category in groups:
                groups[category]["children"].append(node)
    for node in nodes.values():
        node["children"].sort(key=lambda item: item["code"])
    return [groups[category] for category in categories if groups[category]["children"]]


@router.post("/accounts", response_model=AccountNode)
async def add_account(req: AccountCreate, ctx: AuthContext = Depends(require_auth)):
    """创建自定义科目"""
    acct = await create_account(
        ctx.company_id, req.code, req.name,
        parent_code=req.parent_code, level=req.level,
        category=req.category, type_=req.type,
    )
    if not acct:
        raise HTTPException(status_code=409, detail="科目代码已存在")
    return AccountNode(**acct)


class EnsureSubAccountRequest(BaseModel):
    parent_code: str
    name: str


@router.post("/accounts/ensure-sub", response_model=AccountNode)
async def ensure_sub_account_route(req: EnsureSubAccountRequest, ctx: AuthContext = Depends(require_auth)):
    """确保一级科目下存在二级科目（LLM 建议新明细时一键落地，自动生成末位 code）。"""
    acct = await ensure_sub_account(ctx.company_id, req.parent_code, req.name)
    if not acct:
        raise HTTPException(status_code=400, detail="父科目不存在或不是一级科目")
    return AccountNode(**acct)


@router.put("/accounts/{code}", response_model=AccountNode)
async def edit_account(code: str, req: AccountUpdate, ctx: AuthContext = Depends(require_auth)):
    """更新科目"""
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    acct = await update_account(ctx.company_id, code, updates)
    if not acct:
        raise HTTPException(status_code=404, detail="科目不存在")
    return AccountNode(**acct)


@router.delete("/accounts/{code}")
async def remove_account(code: str, ctx: AuthContext = Depends(require_auth)):
    """删除自定义科目（系统科目不可删除）"""
    ok = await delete_account(ctx.company_id, code)
    if not ok:
        raise HTTPException(status_code=400, detail="科目不存在或为系统科目/存在子科目，无法删除")
    return {"ok": True}


@router.get("/book")
async def ledger_book(
    month: str | None = None,
    year: int | None = None,
    scope: str = "month",
    show_zero_rows: bool = False,
    account_code: str | None = None,
    ctx: AuthContext = Depends(require_auth),
):
    """电子账簿整册：一次返回余额表、明细账、序时账、日记账（年度册含 12 行总账）。

    与 ``/balance-summary`` + 逐科目 ``/sub-ledger`` 的区别：整册来自同一时刻的
    同一批分录，账页之间不可能出现"余额表期末 100、明细账滚到 90"的矛盾。

    ``show_zero_rows`` 由服务端决定余额表是否带零发生额科目：分页页数按返回的
    行数计算，若在前端过滤，页码会与实际渲染的页数对不上。

    ``account_code``：单科目查询模式（明细账页/明细流水），details 只含该科目
    一页；父科目/容器科目由同一批分录前缀合成聚合页。
    """
    try:
        return await build_ledger_book(
            ctx.company_id,
            month=month,
            year=year,
            scope=scope,
            include_zero_rows=show_zero_rows,
            account_code=account_code,
        )
    except InvalidPeriod as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PERIOD", "message": str(exc)}) from exc
    except BookTooLarge as exc:
        raise HTTPException(status_code=413, detail={"code": "BOOK_TOO_LARGE", "message": str(exc)}) from exc


class IssueBookSnapshotRequest(BaseModel):
    month: str | None = None
    year: int | None = None
    scope: str = "month"


class VoidBookSnapshotRequest(BaseModel):
    reason: str


def _raise_snapshot_error(exc: SnapshotError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


@router.post("/book/snapshots")
async def issue_book_snapshot(
    req: IssueBookSnapshotRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
):
    """签发正式账簿：期间必须已结账，签发后内容定版（sha256 存证）。"""
    try:
        return await issue_snapshot(
            ctx.company_id,
            month=req.month,
            year=req.year,
            scope=req.scope,
            user_id=ctx.user_id,
            request_id=request.headers.get("Idempotency-Key") or "",
        )
    except SnapshotError as exc:
        _raise_snapshot_error(exc)
    except InvalidPeriod as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PERIOD", "message": str(exc)}) from exc
    except BookTooLarge as exc:
        raise HTTPException(status_code=413, detail={"code": "BOOK_TOO_LARGE", "message": str(exc)}) from exc


@router.get("/book/snapshots")
async def list_book_snapshots(
    period: str | None = None,
    ctx: AuthContext = Depends(require_auth),
):
    """已签发账簿列表（不含 payload），用于判断当期是否已签发、是否已过期。"""
    return {"items": await list_snapshots(ctx.company_id, period=period)}


@router.get("/book/snapshots/{snapshot_id}")
async def get_book_snapshot(snapshot_id: str, ctx: AuthContext = Depends(require_auth)):
    """取回已签发账簿的完整内容：重打时必须与当时逐字一致。"""
    try:
        return await get_snapshot(ctx.company_id, snapshot_id)
    except SnapshotError as exc:
        _raise_snapshot_error(exc)


@router.get("/book/snapshots/{snapshot_id}/diff")
async def diff_book_snapshot(
    snapshot_id: str,
    against: str,
    ctx: AuthContext = Depends(require_auth),
):
    """两册账簿的科目级差异（against 为对比基准册）。

    反结账补记后重签时，这是回答"新册和旧册差在哪"的唯一凭据：
    逐科目期初/发生/期末差额 + 新增与消失的凭证号。
    """
    try:
        return await diff_snapshots(ctx.company_id, snapshot_id, against)
    except SnapshotError as exc:
        _raise_snapshot_error(exc)


@router.post("/book/snapshots/{snapshot_id}/void")
async def void_book_snapshot(
    snapshot_id: str,
    req: VoidBookSnapshotRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
):
    """作废账簿：只标状态并留原因，content 保留可查。"""
    try:
        return await void_snapshot(
            ctx.company_id,
            snapshot_id,
            reason=req.reason,
            user_id=ctx.user_id,
            request_id=request.headers.get("Idempotency-Key") or "",
        )
    except SnapshotError as exc:
        _raise_snapshot_error(exc)


@router.get("/general", deprecated=True)
async def general_ledger(
    account_code: str,
    month: str,
    partner_id: str | None = None,
    ctx: AuthContext = Depends(require_auth),
):
    """总分类账：按科目和月份查询借贷余三栏；可按往来单位过滤。

    .. deprecated:: 2026-10
        已被 ``GET /api/ledger/book?month=&account_code=`` 取代（明细流水走
        ``details[0].lines``，粒度为凭证行）。保留两个版本周期，仅给未迁移的
        历史调用方兜底。
    """
    items = await query_general_ledger(ctx.company_id, account_code, month, partner_id=partner_id)
    return {"account_code": account_code, "month": month, "items": items}


@router.get("/sub-ledger", deprecated=True)
async def sub_ledger(
    account_code: str,
    month: str,
    partner_id: str | None = None,
    ctx: AuthContext = Depends(require_auth),
):
    """明细账：按科目和月份查询，含对方科目；可按往来单位过滤。

    .. deprecated:: 2026-10
        已被 ``GET /api/ledger/book?month=&account_code=`` 取代（``details[0]``，
        粒度为凭证行而非凭证；父科目由同一批分录前缀合成聚合页）。保留两个
        版本周期，仅给未迁移的历史调用方兜底。
    """
    items = await query_sub_ledger(ctx.company_id, account_code, month, partner_id=partner_id)
    return {"account_code": account_code, "month": month, "items": items}


@router.get("/chronological")
async def chronological(month: str, ctx: AuthContext = Depends(require_auth)):
    """序时账：本月全部已过账凭证分录按凭证顺序逐行平铺。"""
    return await query_chronological(ctx.company_id, month)


@router.get("/multi-column")
async def multi_column(account_code: str, month: str, ctx: AuthContext = Depends(require_auth)):
    """多栏账：总账科目按明细子科目展开多栏（收入类贷方多栏，其余借方多栏）。"""
    try:
        return await query_multi_column(ctx.company_id, account_code, month)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/partner-balances")
async def partner_balances(
    account_code: str,
    month: str,
    ctx: AuthContext = Depends(require_auth),
):
    """核算项目余额表：按往来单位汇总某科目的期初/本期/期末。"""
    return await query_partner_balances(ctx.company_id, account_code, month)


@router.get("/advance-balances")
async def advance_balances(month: str, ctx: AuthContext = Depends(require_auth)):
    """垫付台账：按垫付人（老板/员工）汇总其他应付款的垫付、报销与未报余额。"""
    try:
        return await query_advance_balances(ctx.company_id, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/balance-summary", deprecated=True)
async def balance_summary(month: str, ctx: AuthContext = Depends(require_auth)):
    """科目汇总（试算平衡表风格）

    .. deprecated:: 2026-10
        已被 ``GET /api/ledger/book?month=`` 的 ``balance`` 段取代（字段兼容，
        另含方向与零行标记）。保留两个版本周期，当前仅测试用作对账基准。
    """
    result = await get_balance_summary(ctx.company_id, month)
    return result


@router.get("/sync")
async def ledger_sync():
    """预留"""
    return {"message": "单机版暂不需要服务端同步"}
