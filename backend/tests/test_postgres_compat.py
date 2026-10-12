"""PostgreSQL 兼容层翻译回归测试。

测试套件跑在 SQLite 上，抓不住仅在生产 PG 暴露的方言缺口
（如 rowid、json_remove）。这里直接断言翻译结果。
"""
from services.database import _translate_postgres


def _assert_dialect_safe(pg_sql: str) -> None:
    lowered = pg_sql.lower()
    for fragment in ("json_set(", "json_remove(", "json_extract(", "insert or ignore", "rowid"):
        assert fragment not in lowered, f"翻译结果仍含 SQLite 专有语法 {fragment!r}: {pg_sql}"


def test_json_set_single_path_with_param():
    pg = _translate_postgres(
        "UPDATE entries SET payload = json_set(payload, '$.billingStatus', ?) "
        "WHERE id = ? AND company_id = ?"
    )
    assert "jsonb_set(payload::jsonb, '{billingStatus}', to_jsonb(CAST(? AS text)), true)" in pg
    _assert_dialect_safe(pg)


def test_json_set_multiple_paths():
    pg = _translate_postgres(
        "UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled', '$.paymentDate', ?) "
        "WHERE company_id = ? AND id IN (?, ?)"
    )
    assert pg.count("jsonb_set(") == 2
    assert pg.endswith("::text") or "::text" in pg
    _assert_dialect_safe(pg)


def test_json_remove_single_path():
    pg = _translate_postgres(
        "UPDATE entries SET payload = json_remove(payload, '$.billingStatus') WHERE id = ? AND company_id = ?"
    )
    assert "(payload::jsonb - 'billingStatus')::text" in pg
    _assert_dialect_safe(pg)


def test_json_remove_multiple_paths():
    pg = _translate_postgres(
        "UPDATE entries SET payload = json_remove(payload, '$.billingStatus', '$.paymentDate') WHERE id = ?"
    )
    assert "((payload::jsonb - 'billingStatus') - 'paymentDate')::text" in pg
    _assert_dialect_safe(pg)


def test_json_extract_translation():
    pg = _translate_postgres(
        "SELECT id FROM entries WHERE json_extract(payload, '$.billingStatus') = ?"
    )
    assert "jsonb_extract_path_text(payload::jsonb, 'billingStatus')" in pg
    _assert_dialect_safe(pg)


def test_insert_or_ignore_gets_conflict_clause():
    pg = _translate_postgres(
        "INSERT OR IGNORE INTO bank_statement_matches "
        "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
        "VALUES (?, ?, ?, NULL, NULL, ?)"
    )
    assert pg.upper().startswith("INSERT INTO")
    assert "ON CONFLICT DO NOTHING" in pg.upper()
    _assert_dialect_safe(pg)


def test_plain_statements_pass_through():
    pg = _translate_postgres(
        "SELECT entry_id FROM bank_statement_matches "
        "WHERE statement_id = ? AND company_id = ? ORDER BY created_at ASC, entry_id ASC"
    )
    assert pg == (
        "SELECT entry_id FROM bank_statement_matches "
        "WHERE statement_id = ? AND company_id = ? ORDER BY created_at ASC, entry_id ASC"
    )


def test_chart_accounts_is_active_uses_boolean_literal():
    """chart_accounts 在生产 PG 是 boolean 列，`is_active = 1` 直接 500
    （2026-10-12 生产 search_books 实测）。categories/employees 是 integer 列，
    其 `= 1` 是正确的，勿一起改——这里只盯 chart_accounts。"""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("services", "search_service.py").read_text(encoding="utf-8")
    assert "is_active = 1" not in source, "chart_accounts 查询禁止用整数字面量比较 boolean 列"
    assert "is_active = TRUE" in source
