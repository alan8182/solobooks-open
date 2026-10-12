"""Account and company persistence (async SQLite)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from db.session import database_url
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


async def _ensure_user_company(conn, user: Dict) -> Dict:
    companies = await _list_companies_for_user(conn, user["user_id"])
    company = None
    cid = user.get("current_company_id") or ""
    if cid:
        company = next((c for c in companies if c.get("company_id") == cid), None)
    if company is None:
        company = {
            "company_id": _new_id("c"),
            "name": "默认账本",
            "owner_user_id": user["user_id"],
            "created_at": _now_iso(),
        }
        await _insert_company(conn, company)
        user["current_company_id"] = company["company_id"]
        await _persist_user(conn, user)
    return company


async def get_or_create_user(openid: str) -> Tuple[Dict, Dict]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE openid = ?", (openid,))
        row = await cursor.fetchone()
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
        return user, company
    finally:
        await conn.close()


async def get_or_create_user_by_phone(phone: str) -> Tuple[Dict, Dict]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM auth_users WHERE phone = ?", (phone,))
        row = await cursor.fetchone()
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
        return user, company
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
        user["current_company_id"] = co["company_id"]
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
        company = {
            "company_id": _new_id("c"),
            "name": name,
            "owner_user_id": user_id,
            "created_at": _now_iso(),
            **(extra or {}),
        }
        await _insert_company(conn, company)
        await conn.commit()
        return company
    finally:
        await conn.close()


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

        for key in [
            "name", "type", "taxpayerType", "startDate", "taxId",
            "restrictedIndustry", "employeeCount",
        ]:
            if key in updates:
                company[key] = updates[key]

        company["updated_at"] = _now_iso()
        await _update_company_payload(conn, company)
        await conn.commit()
        return company
    finally:
        await conn.close()
