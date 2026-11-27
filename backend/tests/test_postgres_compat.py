# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

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


def test_alipay_summary_guards_avoid_dialect_traps():
    """支付宝汇总守卫查询的两条方言红线（2026-11-20 本地 PG 实测）：

    1. `COALESCE(json_extract(...), 0)` 翻译后是 COALESCE(text, integer)，
       PG 直接报 DatatypeMismatchError → 用户点「生成账单汇总凭证」就 500。
    2. `is_monthly_summary = 1` 在 PG 上是 text 'true' = '1'，永远不命中，
       重叠拦截静默失效 → 同一段账单可以重复入账。
    两处都已改为 IS NULL / IS NOT NULL 存在性判断，这里锁死不许回退。
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath(
        "services", "alipay_bill_service.py").read_text(encoding="utf-8")
    assert "COALESCE(json_extract" not in source, \
        "json_extract 结果不能进 COALESCE 与整数字面量混用（PG text/integer 不兼容）"
    assert "is_monthly_summary') = 1" not in source and "isMonthlySummary') = 1" not in source, \
        "JSON 布尔禁止用 = 1 比较：SQLite 取出 1、PG 取出 'true'，PG 上永不命中"


def test_alipay_summary_guard_queries_translate_cleanly():
    """存在性判断翻译到 PG 后必须仍是合法谓词（jsonb_extract_path_text IS [NOT] NULL）。"""
    pg = _translate_postgres(
        "SELECT 1 FROM entries WHERE company_id = ? "
        "AND json_extract(payload, '$.source') = ? "
        "AND json_extract(payload, '$.is_monthly_summary') IS NULL LIMIT 1"
    )
    assert "jsonb_extract_path_text(payload::jsonb, 'is_monthly_summary') IS NULL" in pg
    _assert_dialect_safe(pg)

    pg2 = _translate_postgres(
        "SELECT payload FROM entries WHERE company_id = ? "
        "AND json_extract(payload, '$.source') = ? "
        "AND (json_extract(payload, '$.is_monthly_summary') IS NOT NULL "
        "OR json_extract(payload, '$.isMonthlySummary') IS NOT NULL)"
    )
    assert "jsonb_extract_path_text(payload::jsonb, 'is_monthly_summary') IS NOT NULL" in pg2
    assert "jsonb_extract_path_text(payload::jsonb, 'isMonthlySummary') IS NOT NULL" in pg2
    _assert_dialect_safe(pg2)
