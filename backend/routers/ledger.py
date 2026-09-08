"""账本路由"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import ChartAccount
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import AccountCreate, AccountUpdate, AccountNode
from services.accounts_service import (
    create_account, update_account, delete_account,
)
from services.ledger_service import query_general_ledger, query_sub_ledger, get_balance_summary
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
    {"code": "5001", "name": "主营业务成本-人力", "type": "成本"},
    {"code": "5301", "name": "研发支出-费用化支出", "type": "成本"},
    {"code": "5601", "name": "管理费用-差旅费", "type": "费用"},
    {"code": "5602", "name": "管理费用-办公费", "type": "费用"},
    {"code": "5603", "name": "管理费用-业务招待费", "type": "费用"},
    {"code": "5604", "name": "管理费用-租赁物业", "type": "费用"},
    {"code": "5699", "name": "管理费用-其他", "type": "费用"},
    {"code": "6001", "name": "主营业务收入", "type": "收入"},
    {"code": "6051", "name": "其他业务收入", "type": "收入"},
    {"code": "6301", "name": "营业外收入", "type": "收入"},
    {"code": "6601", "name": "销售费用-推广费", "type": "费用"},
    {"code": "6603", "name": "财务费用-利息收入", "type": "费用"},
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


@router.get("/general")
async def general_ledger(account_code: str, month: str, ctx: AuthContext = Depends(require_auth)):
    """总分类账：按科目和月份查询借贷余三栏"""
    items = await query_general_ledger(ctx.company_id, account_code, month)
    return {"account_code": account_code, "month": month, "items": items}


@router.get("/sub-ledger")
async def sub_ledger(account_code: str, month: str, ctx: AuthContext = Depends(require_auth)):
    """明细账：按科目和月份查询，含对方科目"""
    items = await query_sub_ledger(ctx.company_id, account_code, month)
    return {"account_code": account_code, "month": month, "items": items}


@router.get("/balance-summary")
async def balance_summary(month: str, ctx: AuthContext = Depends(require_auth)):
    """科目汇总（试算平衡表风格）"""
    result = await get_balance_summary(ctx.company_id, month)
    return result


@router.get("/sync")
async def ledger_sync():
    """预留"""
    return {"message": "单机版暂不需要服务端同步"}
