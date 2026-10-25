# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""税费种认定：解析口径、征期接管、差异校准。

关键不变量：
- 认定表是征期的唯一真值来源；没有认定表时行为与旧版本完全一致；
- 待办按「申报表组」而非「征收项目」物化：城建税/教育费附加/地方教育附加
  三行只产生一条「增值税及附加税费」待办；
- 营业账簿印花税按年（次年 1-15），不再按季铺 4 条；
- 认定表里删掉的税种，其 pending 待办自动豁免，加回来能恢复。
"""
from __future__ import annotations

from datetime import date

import pytest

SCREENSHOT_ROWS = [
    {
        "征收项目": "残疾人就业保障金",
        "征收品目": "残疾人就业保障金",
        "征收子目": "残疾人就业保障金(按比例)",
        "纳税期限": "年",
        "申报期限": "次年9月30号",
        "征收率": "0",
        "税率或单位税额": "0.015",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "个人所得税",
        "征收品目": "工资薪金所得",
        "纳税期限": "月",
        "申报期限": "期满之日起15日内",
        "征收率": "0",
        "税率或单位税额": "0.03",
        "主附税标志": "主税",
        "征收代理方式": "代扣代缴",
    },
    {
        "征收项目": "文化事业建设费",
        "征收品目": "广告业文化事业建设费",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0.03",
        "税率或单位税额": "0.03",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "地方教育附加",
        "征收品目": "增值税地方教育附加",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0",
        "税率或单位税额": "0.02",
        "主附税标志": "增值税附税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "教育费附加",
        "征收品目": "增值税教育费附加",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0",
        "税率或单位税额": "0.03",
        "主附税标志": "增值税附税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "印花税",
        "征收品目": "买卖合同",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0",
        "税率或单位税额": "0.0003",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "城市维护建设税",
        "征收品目": "市区（增值税附征）",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0",
        "税率或单位税额": "0.07",
        "主附税标志": "增值税附税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "企业所得税",
        "征收品目": "应纳税所得额",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0.002",
        "税率或单位税额": "0.25",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "增值税",
        "征收品目": "商业(17%、16%、13%)",
        "纳税期限": "季",
        "申报期限": "期满之日起15日内",
        "征收率": "0.03",
        "税率或单位税额": "0.13",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
    {
        "征收项目": "印花税",
        "征收品目": "营业账簿",
        "纳税期限": "年",
        "申报期限": "期满之日起15日内",
        "征收率": "0.03",
        "税率或单位税额": "0.00025",
        "主附税标志": "主税",
        "征收代理方式": "自行申报",
    },
]


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_company(
    company_id: str, payload: dict | None = None, created_at: str = "2026-01-01T00:00:00+00:00"
) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-taxreg",
                payload=payload or {},
                created_at=created_at,
                updated_at=created_at,
            )
        )


async def _deadline_map(company_id: str) -> dict:
    from services import filing_deadline_service as svc

    return {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines(company_id)}


# ── 纯函数口径 ────────────────────────────────────────────────────────────


def test_parse_cycle_and_group_mapping():
    from services import tax_registration_service as svc

    assert svc.parse_cycle("季") == "quarter"
    assert svc.parse_cycle("年") == "year"
    assert svc.parse_cycle("月") == "month"
    assert svc.parse_cycle("按次") == "other"
    # 附加税必须归增值税申报表，不能各自成组
    assert svc.map_return_group("城市维护建设税", "市区（增值税附征）", "增值税附税") == "vat"
    assert svc.map_return_group("教育费附加", "增值税教育费附加") == "vat"
    # 「经营所得」要抢在「个人所得税」前面
    assert svc.map_return_group("个人所得税", "经营所得") == "business_income_iit_prepay"
    assert svc.map_return_group("个人所得税", "工资薪金所得") == "iit_withholding"
    assert svc.map_return_group("房产税", "") == "other"


def test_infer_deadline_rule_and_rate_parsing():
    from decimal import Decimal

    from services import tax_registration_service as svc

    assert svc.infer_deadline_rule("quarter", "期满之日起 15 日内")["deadlineRule"] == "quarter_15"
    assert svc.infer_deadline_rule("year", "期满之日起15日内")["deadlineRule"] == "year_15"
    residual = svc.infer_deadline_rule("year", "次年9月30号")
    assert residual == {"deadlineRule": "custom", "dueMonthOffset": 9, "dueDay": 30}
    assert svc.infer_deadline_rule("other", "按次")["deadlineRule"] == "none"

    assert svc.parse_rate("0.03") == Decimal("0.03")
    assert svc.parse_rate("3%") == Decimal("0.03")
    # 认定表里 0 表示「该列不适用」，不能落成 0 参与测算
    assert svc.parse_rate("0") is None
    assert svc.parse_rate("") is None


def test_template_follows_entity_type():
    from services import tax_registration_service as svc

    ltd = svc.build_template("ltd")
    individual = svc.build_template("individual_business")

    assert any(row["levy_item"] == "企业所得税" for row in ltd)
    assert not any(row["levy_item"] == "企业所得税" for row in individual)
    assert any(row["return_group"] == "business_income_iit_prepay" for row in individual)
    # 文化事业建设费不默认带出：只有广告/娱乐收入才核定
    assert not any(row["levy_item"] == "文化事业建设费" for row in ltd)


# ── 征期接管 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registration_drives_deadlines_like_screenshot():
    """税局那 10 行认定 → 36 条征期，附加税合并、营业账簿印花税按年。"""
    await _ensure_schema()
    await _create_company("c-taxreg-main")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-main", "u-taxreg", SCREENSHOT_ROWS)
    result = await svc.refresh_deadlines("c-taxreg-main", year=2026)
    by_key = await _deadline_map("c-taxreg-main")

    assert result["source"] == "registration"
    assert len(by_key) == 36
    # 营业账簿印花税按年：只有一条 "2026"，截止次年 1-15
    assert by_key[("stamp_duty", "2026")]["dueDate"] == "2027-01-15"
    assert ("stamp_duty", "2026-Q1") in by_key  # 买卖合同按季
    # 附加税三行并入 vat，不各出一条
    assert len([k for k in by_key if k[0] == "vat"]) == 4
    # 文化事业建设费 / 残保金只在认定表存在时物化
    assert ("culture_fee", "2026-Q3") in by_key
    assert by_key[("disability_fund", "2026")]["dueDate"] == "2027-09-30"
    # 财报报送与工商年报是法定申报义务，不能因为认定表没列就消失
    assert ("fin_statements", "2026-Q1") in by_key
    assert ("annual_report", "2026") in by_key
    assert ("cit_annual", "2026-Q4") in by_key


@pytest.mark.asyncio
async def test_no_registration_keeps_legacy_behaviour():
    """没同步认定表 = 旧行为：30 条，且不出现文化事业建设费/残保金。"""
    await _ensure_schema()
    await _create_company("c-taxreg-empty")

    from services import filing_deadline_service as svc

    result = await svc.refresh_deadlines("c-taxreg-empty", year=2026)
    by_key = await _deadline_map("c-taxreg-empty")

    assert result["source"] == "default"
    assert len(by_key) == 30
    assert not [k for k in by_key if k[0] in ("culture_fee", "disability_fund")]
    # 未同步认定表时印花税仍是内置的按季口径
    assert ("stamp_duty", "2026-Q4") in by_key


@pytest.mark.asyncio
async def test_removing_registration_waives_and_restores():
    """整表替换掉文化事业建设费：4 条 pending 自动豁免；加回来能恢复。"""
    await _ensure_schema()
    await _create_company("c-taxreg-switch")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-switch", "u-taxreg", SCREENSHOT_ROWS)
    await svc.refresh_deadlines("c-taxreg-switch", year=2026)
    before = await _deadline_map("c-taxreg-switch")
    assert before[("culture_fee", "2026-Q2")]["status"] == "pending"

    without_culture = [
        row for row in SCREENSHOT_ROWS if row["征收项目"] != "文化事业建设费"
    ]
    await reg_svc.import_registrations(
        "c-taxreg-switch", "u-taxreg", without_culture, mode="replace"
    )
    await svc.refresh_deadlines("c-taxreg-switch", year=2026)
    after = await _deadline_map("c-taxreg-switch")

    assert after[("culture_fee", "2026-Q2")]["status"] == "waived"
    assert after[("culture_fee", "2026-Q2")]["filedEvidence"]["reason"] == "registration_changed"

    # 加回来（merge）：被自动豁免的恢复 pending
    await reg_svc.import_registrations("c-taxreg-switch", "u-taxreg", SCREENSHOT_ROWS)
    await svc.refresh_deadlines("c-taxreg-switch", year=2026)
    restored = await _deadline_map("c-taxreg-switch")

    assert restored[("culture_fee", "2026-Q2")]["status"] == "pending"


@pytest.mark.asyncio
async def test_manual_waive_is_never_restored():
    """人工豁免（reason=manual）不能被认定表重算恢复。"""
    await _ensure_schema()
    await _create_company("c-taxreg-manual")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-manual", "u-taxreg", SCREENSHOT_ROWS)
    await svc.refresh_deadlines("c-taxreg-manual", year=2026)
    by_key = await _deadline_map("c-taxreg-manual")
    target = by_key[("culture_fee", "2026-Q1")]
    await svc.waive_deadline("c-taxreg-manual", "u-taxreg", target["id"], reason="manual")

    await svc.refresh_deadlines("c-taxreg-manual", year=2026)
    again = await _deadline_map("c-taxreg-manual")

    assert again[("culture_fee", "2026-Q1")]["status"] == "waived"


@pytest.mark.asyncio
async def test_entity_conflict_is_reported_not_materialized():
    """ltd 认定表里出现经营所得：进 conflicts，不物化该税种。"""
    await _ensure_schema()
    await _create_company("c-taxreg-conflict", {"type": "有限责任公司"})

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    rows = SCREENSHOT_ROWS + [
        {
            "征收项目": "个人所得税",
            "征收品目": "经营所得",
            "纳税期限": "季",
            "申报期限": "期满之日起15日内",
            "主附税标志": "主税",
            "征收代理方式": "自行申报",
        }
    ]
    await reg_svc.import_registrations("c-taxreg-conflict", "u-taxreg", rows)
    result = await svc.refresh_deadlines("c-taxreg-conflict", year=2026)

    assert any(c["returnGroup"] == "business_income_iit_prepay" for c in result["conflicts"])
    by_key = await _deadline_map("c-taxreg-conflict")
    assert not [k for k in by_key if k[0] == "business_income_iit_prepay"]


@pytest.mark.asyncio
async def test_obligations_follow_registration():
    """结账向导的本期义务来自认定表：9 月包含文化事业建设费与季度增值税。"""
    await _ensure_schema()
    await _create_company("c-taxreg-ob")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-ob", "u-taxreg", SCREENSHOT_ROWS)
    await svc.refresh_deadlines("c-taxreg-ob", year=2026)
    result = await svc.obligations_for_month("c-taxreg-ob", "2026-09")

    kinds = {o["taxKind"] for o in result["obligations"]}
    assert {"vat", "cit", "stamp_duty", "culture_fee", "iit_withholding"} <= kinds
    assert "business_income_iit_prepay" not in kinds
    vat = next(o for o in result["obligations"] if o["taxKind"] == "vat")
    assert vat["period"] == "2026-Q3"
    assert vat["cycle"] == "quarter"


@pytest.mark.asyncio
async def test_preview_and_diff_are_consistent():
    """预览与差异都基于期望表：导入后差异清零，刷新幂等。"""
    await _ensure_schema()
    await _create_company("c-taxreg-preview")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    summary = await reg_svc.import_registrations("c-taxreg-preview", "u-taxreg", SCREENSHOT_ROWS)
    assert summary["created"] == 10
    await svc.refresh_deadlines("c-taxreg-preview", year=2026)

    preview = await reg_svc.preview_schedule("c-taxreg-preview", 2026)
    assert preview["source"] == "registration"
    assert preview["summary"]["itemCount"] == 36
    assert any(group["returnGroup"] == "vat" for group in preview["groups"])
    # 按截止月看：Q1 的截止日是 2026-04-20（节假日顺延）
    april = next(bucket for bucket in preview["byDueMonth"] if bucket["month"] == "2026-04")
    assert any(item["returnGroup"] == "vat" for item in april["items"])

    diff = await reg_svc.diff_against_deadlines("c-taxreg-preview", 2026)
    assert diff["willCreateCount"] == 0
    assert diff["willWaiveCount"] == 0

    again = await svc.refresh_deadlines("c-taxreg-preview", year=2026)
    assert again["created"] == 0


@pytest.mark.asyncio
async def test_import_is_idempotent_and_ignores_duplicate_rows():
    """同一批里重复的行只落一条；重复导入不产生新行。"""
    await _ensure_schema()
    await _create_company("c-taxreg-idem")

    from services import tax_registration_service as reg_svc

    rows = SCREENSHOT_ROWS + [SCREENSHOT_ROWS[3]]  # 重复一条地方教育附加
    first = await reg_svc.import_registrations("c-taxreg-idem", "u-taxreg", rows)
    second = await reg_svc.import_registrations("c-taxreg-idem", "u-taxreg", rows)

    assert first["created"] == 10
    assert second["created"] == 0
    assert second["updated"] == 10
    items = await reg_svc.list_registrations("c-taxreg-idem")
    assert len(items) == 10


@pytest.mark.asyncio
async def test_invalid_row_rejected_with_row_number():
    await _ensure_schema()
    await _create_company("c-taxreg-invalid")

    from services import tax_registration_service as reg_svc

    with pytest.raises(ValueError) as exc:
        await reg_svc.import_registrations(
            "c-taxreg-invalid",
            "u-taxreg",
            [{"征收项目": "", "纳税期限": "季"}],
        )
    assert "第 1 行" in str(exc.value)


@pytest.mark.asyncio
async def test_registration_cycle_overrides_company_config():
    """公司档案写月报、认定表写季报 → 以认定表为准（认定表是税局核定结果）。"""
    await _ensure_schema()
    await _create_company("c-taxreg-cycle", {"vatFilingCycle": "month"})

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-cycle", "u-taxreg", SCREENSHOT_ROWS)
    result = await svc.refresh_deadlines("c-taxreg-cycle", year=2026)
    by_key = await _deadline_map("c-taxreg-cycle")

    assert result["cycles"]["vat"] == "quarter"
    assert ("vat", "2026-Q3") in by_key
    assert ("vat", "2026-09") not in by_key
    assert await svc.effective_vat_cycle("c-taxreg-cycle") == "quarter"


@pytest.mark.asyncio
async def test_diff_clears_after_refresh_for_mid_year_onboarding():
    """年中建档：建档前已截止的期间不物化，差异也必须同步过滤，否则永远清不掉。"""
    await _ensure_schema()
    await _create_company("c-taxreg-onboard", created_at="2026-07-08T00:00:00+00:00")

    from services import filing_deadline_service as svc
    from services import tax_registration_service as reg_svc

    await reg_svc.import_registrations("c-taxreg-onboard", "u-taxreg", SCREENSHOT_ROWS)
    await svc.refresh_deadlines("c-taxreg-onboard", year=2026)

    diff = await reg_svc.diff_against_deadlines("c-taxreg-onboard", 2026)
    assert diff["willCreateCount"] == 0
    assert diff["willWaiveCount"] == 0
    by_key = await _deadline_map("c-taxreg-onboard")
    # Q1 的 4-20 早于建档日 7-8，不物化
    assert ("vat", "2026-Q1") not in by_key
    assert ("vat", "2026-Q3") in by_key
