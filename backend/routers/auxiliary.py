# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""辅助核算主数据路由（P0 WP2）：往来单位 / 部门 / 项目。

三个维度的形状高度一致，用一张路由表生成 CRUD，避免三份复制粘贴的端点
在字段校验上慢慢漂移。业务规则全部在 ``services/auxiliary_service``。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query

from deps.auth import AuthContext, require_auth
from services.auxiliary_service import (
    create_master,
    deactivate_master,
    delete_master,
    list_master,
    resolve_auxiliary_options,
    update_master,
)

router = APIRouter(prefix="/api", tags=["auxiliary"], dependencies=[Depends(require_auth)])

# kind → 路由前缀；与 auxiliary_service.KINDS 保持一致
_ROUTES = (("partners", "partner"), ("departments", "department"), ("projects", "project"))


@router.get("/auxiliary/options")
async def auxiliary_options(
    keyword: str = Query("", description="名称/编码模糊匹配"),
    limit: int = Query(20, ge=1, le=100),
    ctx: AuthContext = Depends(require_auth),
):
    """录一笔账时的辅助核算下拉（三维度一次返回）。"""
    return await resolve_auxiliary_options(ctx.company_id, keyword=keyword, limit=limit)


for _path, _kind in _ROUTES:

    def _list_endpoint(
        kind: str = _kind,
    ):
        async def endpoint(
            keyword: str = Query(""),
            include_inactive: bool = Query(False, alias="includeInactive"),
            page: int = Query(1, ge=1),
            page_size: int = Query(100, ge=1, le=500, alias="pageSize"),
            ctx: AuthContext = Depends(require_auth),
        ) -> dict[str, Any]:
            return await list_master(
                ctx.company_id,
                kind,
                keyword=keyword,
                include_inactive=include_inactive,
                page=page,
                page_size=page_size,
            )

        return endpoint

    def _create_endpoint(kind: str = _kind):
        async def endpoint(
            payload: dict[str, Any] = Body(default_factory=dict),
            ctx: AuthContext = Depends(require_auth),
        ) -> dict[str, Any]:
            return await create_master(ctx.company_id, kind, payload, user_id=ctx.user_id)

        return endpoint

    def _update_endpoint(kind: str = _kind):
        async def endpoint(
            item_id: str,
            payload: dict[str, Any] = Body(default_factory=dict),
            ctx: AuthContext = Depends(require_auth),
        ) -> dict[str, Any]:
            return await update_master(
                ctx.company_id, kind, item_id, payload, user_id=ctx.user_id
            )

        return endpoint

    def _deactivate_endpoint(kind: str = _kind):
        async def endpoint(item_id: str, ctx: AuthContext = Depends(require_auth)) -> dict[str, Any]:
            return await deactivate_master(ctx.company_id, kind, item_id, user_id=ctx.user_id)

        return endpoint

    def _delete_endpoint(kind: str = _kind):
        async def endpoint(item_id: str, ctx: AuthContext = Depends(require_auth)) -> dict[str, Any]:
            return await delete_master(ctx.company_id, kind, item_id, user_id=ctx.user_id)

        return endpoint

    router.add_api_route(
        f"/{_path}", _list_endpoint(), methods=["GET"], name=f"list_{_path}",
        summary=f"辅助核算-{_kind}列表",
    )
    router.add_api_route(
        f"/{_path}", _create_endpoint(), methods=["POST"], name=f"create_{_path}",
        summary=f"辅助核算-新建{_kind}",
    )
    router.add_api_route(
        f"/{_path}/{{item_id}}", _update_endpoint(), methods=["PUT"], name=f"update_{_path}",
        summary=f"辅助核算-更新{_kind}",
    )
    router.add_api_route(
        f"/{_path}/{{item_id}}/deactivate", _deactivate_endpoint(), methods=["POST"],
        name=f"deactivate_{_path}", summary=f"辅助核算-停用{_kind}",
    )
    router.add_api_route(
        f"/{_path}/{{item_id}}", _delete_endpoint(), methods=["DELETE"], name=f"delete_{_path}",
        summary=f"辅助核算-删除{_kind}（未被引用才允许）",
    )
