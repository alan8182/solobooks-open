# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from services.accounts_service import _build_seed_rows


def test_chart_account_seed_uses_database_booleans():
    rows = _build_seed_rows("company-test", "2026-07-11T00:00:00+00:00")

    assert rows
    for row in rows:
        assert type(row[8]) is bool
        assert type(row[9]) is bool
        assert type(row[10]) is bool
