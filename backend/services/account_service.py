# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Account and company persistence (async SQLite)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from db.session import database_url
from core.errors import AccountingError, START_DATE_CONFLICT
from services.database import get_conn


def _json_object(value: Any) -> Dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_vat_fields(payload: Dict) -> Dict:
    """把前端传来的增值税配置收敛成可计算的值，不做纳税人身份校验。

    纳税人身份（小规模/一般）在凭证生成与申报底稿处校验：档案可以先填写，
    但用错口径出账必须失败。
    """
    out: Dict[str, Any] = {}
    raw_rate = payload.get("vatRate")
    if raw_rate not in (None, ""):
        text = str(raw_rate).strip()
        if text.endswith("%"):
            text = format(float(text[:-1]) / 100, ".6f")
        try:
            value = float(text)
        except ValueError:
            value = -1
        if value <= 0 or value > 0.03:
            raise ValueError("小规模征收率只支持 1% 或 3%")
        out["vatRate"] = round(value, 6)
    raw_cycle = payload.get("vatFilingCycle")
    if raw_cycle not in (None, ""):
        text = str(raw_cycle).strip().lower()
        if text.startswith("month") or text in {"月", "月报", "月度"}:
            out["vatFilingCycle"] = "month"
        elif text.startswith("quarter") or text in {"季", "季报", "季度"}:
            out["vatFilingCycle"] = "quarter"
        else:
            raise ValueError("增值税申报周期只支持月报或季报")
    # 经营所得 A 表预缴周期：由用户按税局核定的征收方式设置（月/季），
    # 与增值税周期独立（两税种核定节奏可能不一致）。
    raw_iit_cycle = payload.get("businessIncomeIitFilingCycle")
    if raw_iit_cycle not in (None, ""):
        text = str(raw_iit_cycle).strip().lower()
        if text.startswith("month") or text in {"月", "月报", "月度"}:
            out["businessIncomeIitFilingCycle"] = "month"
        elif text.startswith("quarter") or text in {"季", "季报", "季度"}:
            out["businessIncomeIitFilingCycle"] = "quarter"
        else:
            raise ValueError("经营所得税预缴周期只支持月报或季报")
    return out


async def _ensure_schema() -> None:
    # Production PostgreSQL schema is owned by Alembic. This compatibility
    # initializer is deliberately limited to SQLite test/migration fixtures.
    if not database_url().startswith("sqlite+"):
        return
    conn = await get_conn()
    try:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id TEXT PRIMARY KEY,
                openid TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                phone_verified_at TEXT NOT NULL DEFAULT '',
                wechat_openid_bound_at TEXT NOT NULL DEFAULT '',
                current_company_id TEXT NOT NULL DEFAULT '',
                nickname TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL DEFAULT '',
                password_updated_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_auth_users_openid ON auth_users(openid);
            CREATE INDEX IF NOT EXISTS idx_auth_users_phone ON auth_users(phone);
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_auth_users_openid_nonempty
                ON auth_users(openid) WHERE openid != '';
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_auth_users_phone_nonempty
                ON auth_users(phone) WHERE phone != '';

            CREATE TABLE IF NOT EXISTS auth_companies (
                company_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_auth_companies_owner ON auth_companies(owner_user_id);

            CREATE TABLE IF NOT EXISTS auth_consents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                privacy_accepted_at TEXT NOT NULL,
                tos_version TEXT NOT NULL,
                privacy_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_consents_user ON auth_consents(user_id);
            """
        )
        await conn.commit()
    finally:
        await conn.close()


_INIT_DONE = False


async def _ensure_db_ready() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        # Double-check: if the DB was wiped the table won't exist anymore
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_users'"
            )
            exists = await cursor.fetchone()
        finally:
            await conn.close()
        if exists:
            return
        # Table gone (DB was deleted) — reset flag and recreate
        _INIT_DONE = False

    await _ensure_schema()
    _INIT_DONE = True


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_user(row) -> Dict:
    return {
        "user_id": row["user_id"],
        "openid": row["openid"] or "",
        "phone": row["phone"] or "",
        "phone_verified_at": row["phone_verified_at"] or "",
        "wechat_openid_bound_at": row["wechat_openid_bound_at"] or "",
        "current_company_id": row["current_company_id"] or "",
        "nickname": row["nickname"] or "",
        "avatar": row["avatar"] or "",
        "password_hash": row["password_hash"] or "",
        "password_updated_at": row["password_updated_at"] or "",
        "created_at": row["created_at"] or "",
        "last_login_at": row["last_login_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


async def _persist_user(conn, user: Dict) -> None:
    # 密码列只随 INSERT 落库、不进 DO UPDATE SET：登录等高频路径都会全量覆盖写用户，
    # 若纳入更新集，任何一处漏带字段都会把已设密码抹成空。改密码走 set_user_password。
    # is_platform_admin/is_disabled 同理只在 INSERT 给默认 0（PG 迁移有 server_default、
    # SQLite create_all 没有，显式写死让两边行为一致），管理后台改标志走 ORM 直写。
    await conn.execute(
        """
        INSERT INTO auth_users (
            user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
            current_company_id, nickname, avatar, password_hash, password_updated_at,
            is_platform_admin, is_disabled, created_at, last_login_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,FALSE,FALSE,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            openid=excluded.openid,
            phone=excluded.phone,
            phone_verified_at=excluded.phone_verified_at,
            wechat_openid_bound_at=excluded.wechat_openid_bound_at,
            current_company_id=excluded.current_company_id,
            nickname=excluded.nickname,
            avatar=excluded.avatar,
            last_login_at=excluded.last_login_at,
            updated_at=excluded.updated_at
        """,
        (
            user["user_id"],
            user.get("openid") or "",
            user.get("phone") or "",
            user.get("phone_verified_at") or "",
            user.get("wechat_openid_bound_at") or "",
            user.get("current_company_id") or "",
            user.get("nickname") or "",
            user.get("avatar") or "",
            user.get("password_hash") or "",
            user.get("password_updated_at") or "",
            user.get("created_at") or _now_iso(),
            user.get("last_login_at") or _now_iso(),
            user.get("updated_at") or "",
        ),
    )


async def _list_companies_for_user(conn, user_id: str) -> List[Dict]:
    cursor = await conn.execute(
        "SELECT payload FROM auth_companies WHERE owner_user_id = ? ORDER BY created_at ASC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    out: List[Dict] = []
    for r in rows:
        try:
            out.append(_json_object(r["payload"]))
        except Exception:
            continue
    return out


async def _get_company(conn, company_id: str) -> Dict | None:
    cursor = await conn.execute(
        "SELECT payload FROM auth_companies WHERE company_id = ?",
        (company_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    try:
        return _json_object(row["payload"])
    except Exception:
        return None


async def get_company(company_id: str) -> Dict | None:
    """读取主体档案（含 taxpayerType / vatRate / vatFilingCycle 等税务口径字段）。

    仅供内部服务读取配置使用；对外接口仍需走成员权限校验。
    """
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        return await _get_company(conn, company_id)
    finally:
        await conn.close()


async def _insert_company(conn, company: Dict) -> None:
    now = _now_iso()
    created = company.get("created_at") or now
    updated = company.get("updated_at") or ""
    payload = json.dumps(company, ensure_ascii=False)
    # 非空口径列显式给模型默认值：PG 迁移有 server_default，SQLite create_all 没有
    await conn.execute(
        """
        INSERT INTO auth_companies (
            company_id, owner_user_id, payload, base_currency, accounting_standard,
            posting_policy, timezone, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            company["company_id"],
            company["owner_user_id"],
            payload,
            company.get("base_currency") or "CNY",
            company.get("accounting_standard") or "small_enterprise_cn",
            company.get("posting_policy") or "owner_self_post",
            company.get("timezone") or "Asia/Shanghai",
            created,
            updated,
        ),
    )


async def _update_company_payload(conn, company: Dict) -> None:
    company["updated_at"] = company.get("updated_at") or _now_iso()
    await conn.execute(
        "UPDATE auth_companies SET payload = ?, updated_at = ? WHERE company_id = ?",
        (json.dumps(company, ensure_ascii=False), company["updated_at"], company["company_id"]),
    )


async def _ensure_user_company(conn, user: Dict) -> Dict | None:
    """返回当前账套；没有就返回 None。

    不再自动补一本「默认账本」：新用户由注册链路生成演示账套，
    删除演示账套时由删除逻辑补一本空白正式账套，系统始终保证有账可用。
    """
    companies = await _list_companies_for_user(conn, user["user_id"])
    cid = user.get("current_company_id") or ""
    company = next((c for c in companies if c.get("company_id") == cid), None)
    if company is None and companies:
        # 当前账套失效（如被删除）时回到第一本，避免登录后无账套
        company = companies[0]
        user["current_company_id"] = company["company_id"]
        await _persist_user(conn, user)
    return company


async def get_or_create_user(openid: str) -> Tuple[Dict, Dict | None, bool]:
    """返回 ``(user, company, is_new)``；company 可能为 None（新用户尚未建账套）。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE openid = ?", (openid,))
        row = await cursor.fetchone()
        is_new = row is None
        if row is None:
            user = {
                "user_id": _new_id("u"),
                "openid": openid,
                "phone": "",
                "phone_verified_at": "",
                "wechat_openid_bound_at": _now_iso(),
                "current_company_id": "",
                "nickname": "",
                "avatar": "",
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
                "updated_at": "",
            }
            await _persist_user(conn, user)
        else:
            user = _row_to_user(row)
            user["last_login_at"] = _now_iso()
            if not user.get("wechat_openid_bound_at"):
                user["wechat_openid_bound_at"] = _now_iso()
            await _persist_user(conn, user)

        company = await _ensure_user_company(conn, user)
        await conn.commit()
        return user, company, is_new
    finally:
        await conn.close()


async def get_or_create_user_by_phone(phone: str) -> Tuple[Dict, Dict | None, bool]:
    """返回 ``(user, company, is_new)``；company 可能为 None（新用户尚未建账套）。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE phone = ?", (phone,))
        row = await cursor.fetchone()
        is_new = row is None
        if row is None:
            user = {
                "user_id": _new_id("u"),
                "openid": "",
                "phone": phone,
                "phone_verified_at": _now_iso(),
                "wechat_openid_bound_at": "",
                "current_company_id": "",
                "nickname": "",
                "avatar": "",
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
                "updated_at": "",
            }
            await _persist_user(conn, user)
        else:
            user = _row_to_user(row)
            user["phone"] = phone
            user["phone_verified_at"] = _now_iso()
            user["last_login_at"] = _now_iso()
            await _persist_user(conn, user)

        company = await _ensure_user_company(conn, user)
        await conn.commit()
        return user, company, is_new
    finally:
        await conn.close()


async def bind_wechat_openid(user_id: str, openid: str) -> Dict:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError("用户不存在")
        cursor = await conn.execute(
            "SELECT user_id FROM auth_users WHERE openid = ? AND user_id != ?",
            (openid, user_id),
        )
        occupied = await cursor.fetchone()
        if occupied:
            raise ValueError("该微信已绑定其他账号")
        target = _row_to_user(row)
        target["openid"] = openid
        target["wechat_openid_bound_at"] = _now_iso()
        await _persist_user(conn, target)
        await conn.commit()
        return target
    finally:
        await conn.close()


async def bind_phone(user_id: str, phone: str) -> Dict:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError("用户不存在")
        cursor = await conn.execute(
            "SELECT user_id FROM auth_users WHERE phone = ? AND user_id != ?",
            (phone, user_id),
        )
        occupied = await cursor.fetchone()
        if occupied:
            raise ValueError("该手机号已绑定其他账号")
        target = _row_to_user(row)
        target["phone"] = phone
        target["phone_verified_at"] = _now_iso()
        await _persist_user(conn, target)
        await conn.commit()
        return target
    finally:
        await conn.close()


async def get_user_login_by_phone(phone: str) -> Dict | None:
    """按手机号只读查用户（密码登录用；不自动建号）。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE phone = ?", (phone,))
        row = await cursor.fetchone()
        return _row_to_user(row) if row else None
    finally:
        await conn.close()


async def set_user_password(user_id: str, password_hash: str) -> None:
    """写入密码哈希。密码列不参与 _persist_user 的覆盖更新，只能从这里改。"""
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "UPDATE auth_users SET password_hash = ?, password_updated_at = ?, updated_at = ? "
            "WHERE user_id = ?",
            (password_hash, _now_iso(), _now_iso(), user_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("用户不存在")
        await conn.commit()
    finally:
        await conn.close()


async def save_privacy_consent(
    user_id: str,
    privacy_accepted_at: str,
    tos_version: str,
    privacy_version: str,
) -> None:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO auth_consents (user_id, privacy_accepted_at, tos_version, privacy_version, created_at) VALUES (?,?,?,?,?)",
            (user_id, privacy_accepted_at, tos_version, privacy_version, _now_iso()),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_user_profile(user_id: str) -> Dict | None:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        user = _row_to_user(row)
        companies = await _list_companies_for_user(conn, user_id)
        current_company = None
        cid = user.get("current_company_id") or ""
        if cid:
            current_company = next(
                (c for c in companies if c.get("company_id") == cid),
                None,
            )
        if current_company is None and companies:
            current_company = companies[0]
        return {"user": user, "companies": companies, "current_company": current_company}
    finally:
        await conn.close()


async def set_user_current_company(user_id: str, company_id: str) -> Tuple[Dict, Dict] | None:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ? AND owner_user_id = ?",
            (company_id, user_id),
        )
        company = await cursor.fetchone()
        if not company:
            return None
        try:
            co = _json_object(company["payload"])
        except Exception:
            return None
        user = _row_to_user(row)
        # payload 可能是空对象（历史数据/精简创建路径），公司 id 以列值为准
        user["current_company_id"] = co.get("company_id") or company_id
        await _persist_user(conn, user)
        await conn.commit()
        return user, co
    finally:
        await conn.close()


async def update_user_profile(
    user_id: str, nickname: str | None = None, avatar: str | None = None
) -> Dict:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError("用户不存在")
        user = _row_to_user(row)
        if nickname is not None:
            user["nickname"] = nickname
        if avatar is not None:
            user["avatar"] = avatar
        user["updated_at"] = _now_iso()
        await _persist_user(conn, user)
        await conn.commit()
        return user
    finally:
        await conn.close()


async def create_company(user_id: str, name: str, extra: dict | None = None) -> Dict:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        # employeeCount 已废弃（人数从员工名册派生），老客户端传入时静默忽略
        extra_clean = {k: v for k, v in (extra or {}).items() if k != "employeeCount"}
        extra_clean.update(_normalize_vat_fields(extra_clean))
        company = {
            "company_id": _new_id("c"),
            "name": name,
            "owner_user_id": user_id,
            "created_at": _now_iso(),
            **extra_clean,
        }
        await _insert_company(conn, company)
        await conn.commit()
        return company
    finally:
        await conn.close()


async def _assert_start_date_not_before_entries(conn, company_id: str, start_date: Any) -> None:
    """启用日期后移不得越过已有最早分录，否则会出现"账未启用已有账"的矛盾口径。

    期初凭证（opening）承载启用日之前的历史，不参与比较。
    """
    raw = str(start_date or "").strip()
    if not raw:
        return
    try:
        new_start = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return
    cursor = await conn.execute(
        "SELECT MIN(occurred_on) FROM journal_entries WHERE company_id = ? AND entry_type != 'opening'",
        (company_id,),
    )
    row = await cursor.fetchone()
    earliest = row[0] if row else None
    if not earliest:
        return
    try:
        earliest_date = datetime.strptime(str(earliest)[:10], "%Y-%m-%d").date()
    except ValueError:
        return
    if earliest_date < new_start:
        raise AccountingError(
            START_DATE_CONFLICT,
            f"启用日期不能晚于已有记账记录的最早日期 {earliest_date.isoformat()}："
            "如需此后开始记账，请先冲销或调整启用日之前的凭证",
            422,
            {"startDate": new_start.isoformat(), "earliestEntryDate": earliest_date.isoformat()},
        )


async def update_company(user_id: str, company_id: str, updates: dict) -> Dict:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM auth_companies WHERE company_id = ? AND owner_user_id = ?",
            (company_id, user_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError("主体不存在或无权操作")
        try:
            company = _json_object(row["payload"])
        except Exception as e:
            raise ValueError("主体数据损坏") from e

        if company.get("isDemo"):
            raise AccountingError(
                "DEMO_READONLY",
                "演示账套仅供体验，不能修改账套信息。需要正式记账请新建账套",
                403,
            )

        for key in [
            "name", "type", "taxpayerType", "startDate", "taxId",
            "restrictedIndustry", "industry",
            "vatRate", "vatFilingCycle", "businessIncomeIitFilingCycle",
            "onboardingType",
        ]:
            if key in updates:
                company[key] = updates[key]
        # 只归一/校验本次提交的增值税字段：对存量 payload 整体重跑会让历史脏数据
        # 阻断任何无关字段的更新（如改名也 500）
        vat_update = {
            key: updates[key]
            for key in ("vatRate", "vatFilingCycle", "businessIncomeIitFilingCycle")
            if key in updates
        }
        if vat_update:
            company.update(_normalize_vat_fields(vat_update))

        await _assert_start_date_not_before_entries(conn, company_id, company.get("startDate"))
        company["updated_at"] = _now_iso()
        await _update_company_payload(conn, company)
        await conn.commit()
        return company
    finally:
        await conn.close()
