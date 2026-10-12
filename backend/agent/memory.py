"""公司画像：agent 的长期记忆（M0 只读注入，写入回路在 M1 补）。"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select

from db.models import CompanyProfile
from db.session import session_scope


_DEFAULT_PROFILE: Dict[str, Any] = {
    "industry": "",
    "taxpayerType": "",  # small_scale | general
    "categoryPreferences": {},  # 业务描述 -> 分类（M1 写入回路维护）
    "contacts": [],  # 常往来客户/供应商名单
    "notes": "",
}


async def get_profile(company_id: str) -> Dict[str, Any]:
    """读取公司画像；不存在时返回默认结构（不落库，避免只读对话产生写放大）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return dict(_DEFAULT_PROFILE)
        merged = dict(_DEFAULT_PROFILE)
        merged.update(row.profile or {})
        return merged


async def save_profile(company_id: str, profile: Dict[str, Any]) -> None:
    """整体写入画像（upsert）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(CompanyProfile).where(CompanyProfile.company_id == company_id)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(CompanyProfile(company_id=company_id, profile=profile))
        else:
            row.profile = profile


async def record_category_preference(
    company_id: str, merchant: str, category: str, *, weight: int = 1
) -> None:
    """沉淀"商家/事项 → 分类"映射（每次人工确认草稿时回写，count 越大权重越高）。

    这是 agent 越用越准的核心回路：用户确认的每一笔都在训练画像。
    """
    merchant = (merchant or "").strip()
    category = (category or "").strip()
    if not merchant or not category:
        return
    profile = await get_profile(company_id)
    prefs = dict(profile.get("categoryPreferences") or {})
    entry = dict(prefs.get(merchant) or {})
    same_category = entry.get("category") == category
    entry["category"] = category
    entry["count"] = int(entry.get("count") or 0) + (weight if same_category else 0) + 1
    prefs[merchant] = entry
    profile["categoryPreferences"] = prefs
    await save_profile(company_id, profile)


async def record_category_correction(
    company_id: str, merchant: str, proposed: str, final: str
) -> None:
    """用户修正了 AI 的分类建议：记录修正信号（权重更高），并沉淀最终映射。"""
    if (proposed or "").strip() and (final or "").strip() and proposed != final:
        await record_category_preference(company_id, f"修正:{merchant}", final, weight=3)
    await record_category_preference(company_id, merchant, final)
