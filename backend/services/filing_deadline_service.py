# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""征期日历服务：小规模纳税人申报节奏（M3）。

征期规则为配置数据；节假日顺延日期按税局公告**手工维护**（DEADLINE_OVERRIDES），
每年初应复核一次。未命中 override 的税种按"届满月 15 日、遇周末顺延至次周一"兜底。
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from db.models import FilingDeadline, new_id
from db.session import session_scope
from services import company_entity_service

logger = logging.getLogger(__name__)

# 仅有限责任公司适用的税种：个人独资企业/个体工商户不缴企业所得税
CORPORATE_INCOME_TAX_KINDS = frozenset({"cit", "cit_annual"})

# 经营所得个税（个体户/个独）：预缴 A 表（月/季动态）+ 汇算 B 表（次年 3-31）。
# 与企税完全互斥：ltd 不物化、非 ltd 不物化 cit。
BUSINESS_INCOME_IIT_KINDS = frozenset({"business_income_iit_prepay", "business_income_iit_annual"})

# 申报周期跟公司配置走（而不是写死 freq）的税种：
#   vat -> vatFilingCycle；business_income_iit_prepay -> businessIncomeIitFilingCycle
DYNAMIC_CYCLE_TAX_KINDS = frozenset({"vat", "business_income_iit_prepay"})

# 只有「税费种认定表」里出现才物化的税种：税局没认定就不该有这条待办，
# 无认定表的历史公司仍走 TAX_KINDS 默认清单，行为不变。
REGISTRATION_ONLY_KINDS = frozenset({"culture_fee", "disability_fund", "other"})

# 不属于税费种认定、但同属法定申报义务的事项：认定表接管征期后仍必须保留，
# 否则财务报表报送会在用户同步认定表后凭空消失。
NON_REGISTRATION_OBLIGATIONS = ("fin_statements", "annual_report")

# 申报事项展示顺序（结账向导/税报页按此排序，避免字典序把附加税排到增值税前面）
RETURN_GROUP_ORDER = (
    "vat",
    "cit",
    "cit_annual",
    "business_income_iit_prepay",
    "business_income_iit_annual",
    "iit_withholding",
    "stamp_duty",
    "culture_fee",
    "disability_fund",
    "fin_statements",
    "annual_report",
    "other",
)

# 税种定义：kind -> (名称, 频率, 指引)
TAX_KINDS: Dict[str, Dict[str, str]] = {
    "vat": {
        "name": "增值税及附加税费",
        "freq": "quarter",
        "guide": "电子税务局 → 我要办税 → 税费申报及缴纳 → 增值税小规模纳税人申报。按作业包逐字段填写核对后提交。",
    },
    "cit": {
        "name": "企业所得税预缴（A 类）",
        "freq": "quarter",
        "guide": "电子税务局 → 税费申报及缴纳 → 企业所得税月（季）度预缴申报（A 类）。按作业包 A200000 字段填写。",
    },
    "fin_statements": {
        "name": "财务报表报送",
        "freq": "quarter",
        "guide": "电子税务局 → 税费申报及缴纳 → 财务报表报送。利润表/资产负债表/现金流量表可在经营报表页导出后上传。",
    },
    "stamp_duty": {
        "name": "印花税",
        "freq": "quarter",
        "guide": "按季申报（税源发生时）。无应税合同/账簿税源时可做零申报；有营业账簿按实收资本万分之 2.5（减半后）。在作业包中确认本期税源。",
    },
    "iit_withholding": {
        "name": "个税扣缴",
        "freq": "month",
        "guide": "自然人电子税务局（扣缴端，桌面客户端）申报。无员工、无工资发放时可零申报或暂不处理；有员工必须每月申报。",
    },
    "annual_report": {
        "name": "工商年报（市监局）",
        "freq": "annual",
        "guide": "国家企业信用信息公示系统（gsxt.gov.cn），每年 1 月 1 日至 6 月 30 日报送上一年度年报。逾期列入经营异常名录。",
    },
    "cit_annual": {
        "name": "企业所得税汇算清缴",
        "freq": "annual_cit",
        "guide": "次年 5 月 31 日前完成上一年度汇算清缴（A 类年报）。需税局 UKey/账号，建议提前准备费用发票与扣除凭证。",
    },
    "business_income_iit_prepay": {
        "name": "经营所得个税预缴（A 表）",
        # freq 仅作 compute_due_date 的解析占位：月报公司按月度期间物化（回落月度规则），
        # 季报公司按季度期间物化。实际周期来自公司配置 businessIncomeIitFilingCycle。
        "freq": "quarter",
        "guide": "自然人电子税务局 → 经营所得个人所得税月（季）度预缴申报（A 表）。按作业包的累计应纳税所得额核对后申报，查账征收适用。",
    },
    "business_income_iit_annual": {
        "name": "经营所得个税汇算清缴（B 表）",
        "freq": "annual_iit",
        "guide": "次年 3 月 31 日前完成上一年度经营所得汇算清缴（B 表）。需准备成本费用票据，业主本人工资不得税前扣除需调增；两处以上取得经营所得的需合并办理汇总申报（C 表）。",
    },
    "culture_fee": {
        "name": "文化事业建设费",
        "freq": "quarter",
        "guide": "电子税务局 → 税费申报及缴纳 → 文化事业建设费申报。仅就广告服务、娱乐服务销售额计征（费率 3%），无应税销售额可零申报。",
    },
    "disability_fund": {
        "name": "残疾人就业保障金",
        "freq": "annual_due_0930",
        "guide": "电子税务局 → 税费申报及缴纳 → 残疾人就业保障金申报。按上年度在职职工年平均工资与安排残疾人就业比例差额计征，次年 9 月 30 日前申报；30 人以下小微企业免征。",
    },
    "other": {
        "name": "其他核定申报事项",
        "freq": "quarter",
        "guide": "税局核定的其他税种（房产税、城镇土地使用税、车船税等），按税费种认定表的纳税期限申报。",
    },
}

# 节假日顺延后的实际截止日（按税局公告手工维护；YYYY: {kind: {period: date}})
DEADLINE_OVERRIDES: Dict[int, Dict[str, Dict[str, str]]] = {
    2026: {
        "quarter": {
            "2026-Q1": "2026-04-20",   # 清明+五一顺延（示例，按公告校准）
            "2026-Q2": "2026-07-15",
            "2026-Q3": "2026-10-22",   # 国庆中秋长假顺延（按公告校准）
            "2026-Q4": "2027-01-15",   # 元旦临近，按公告校准
        },
        "month": {},   # 月度按 15 日兜底顺延
    },
    2027: {
        "quarter": {
            "2027-Q1": "2027-04-15",
            "2027-Q2": "2027-07-15",
            "2027-Q3": "2027-10-15",
            "2027-Q4": "2028-01-17",
        },
        "month": {},
    },
}


def _next_weekday(d: date) -> date:
    """周末顺延到周一。"""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _quarter_label(period: str) -> Optional[tuple[int, int]]:
    text = (period or "").upper().replace("季度", "Q").replace("第", "").replace(" ", "")
    try:
        if "-Q" in text:
            year_text, q_text = text.split("-Q", 1)
            return int(year_text), int(q_text)
    except ValueError:
        pass
    return None


def compute_due_date(tax_kind: str, period: str) -> Optional[date]:
    """计算某税种某期间的征期。"""
    freq = TAX_KINDS.get(tax_kind, {}).get("freq")
    if not freq:
        return None
    overrides = DEADLINE_OVERRIDES.get(int(period[:4]) if period[:4].isdigit() else 0, {})

    if freq in ("quarter", "annual_cit"):
        parsed = _quarter_label(period)
        if not parsed:
            # 动态周期税种（增值税、经营所得 A 表）月报时 period 为月度格式，
            # 回落到月度申报期规则（次月 15 日）。
            if tax_kind in DYNAMIC_CYCLE_TAX_KINDS:
                return _month_due_date(period, overrides)
            return None
        year, quarter = parsed
        if freq == "annual_cit":
            return date(year + 1, 5, 31)
        key = f"{year}-Q{quarter}"
        override = overrides.get("quarter", {}).get(key)
        if override:
            return date.fromisoformat(override)
        due_month = quarter * 3 + 1
        due = date(year + (1 if due_month > 12 else 0), (due_month - 1) % 12 + 1, 15)
        return _next_weekday(due)

    if freq == "month":
        return _month_due_date(period, overrides)

    if freq == "annual_iit":
        # 经营所得汇算清缴（B 表）：次年 3 月 31 日。
        # 与企税汇算（5-31）是两个税种，到期日规则不可复用。
        try:
            year = int(period[:4])
        except (TypeError, ValueError):
            return None
        return date(year + 1, 3, 31)

    if freq == "annual":
        try:
            year = int(period[:4])
        except (TypeError, ValueError):
            return None
        return date(year + 1, 6, 30)

    return None


def _month_due_date(period: str, overrides: Dict[int, Dict[str, Dict[str, str]]]) -> Optional[date]:
    """月度税费纳税申报期：所属月份的次月 15 日（遇法定节假日顺延）。"""
    try:
        year, month = int(period[:4]), int(period[5:7])
    except (TypeError, ValueError):
        return None
    override = overrides.get("month", {}).get(period)
    if override:
        return date.fromisoformat(override)
    due_year = year + (1 if month == 12 else 0)
    due_month = 1 if month == 12 else month + 1
    # 10 月国庆黄金周长假顺延校准
    if due_year == 2026 and due_month == 10:
        return date(2026, 10, 22)
    return _next_weekday(date(due_year, due_month, 15))


def periods_for_year(year: int) -> Dict[str, List[str]]:
    """生成某年应覆盖的期间清单。"""
    return {
        "quarter": [f"{year}-Q{q}" for q in (1, 2, 3, 4)],
        "month": [f"{year}-{m:02d}" for m in range(1, 13)],
        "annual": [str(year)],
        "annual_cit": [f"{year}-Q4"],  # 汇算清缴挂在 Q4 期间上
    }


def periods_for_cycle(cycle: str, year: int) -> List[str]:
    """按纳税期限展开某年的属期清单（认定表口径）。

    `other`（按次/半年等）不铺开：这类事项没有固定属期，硬造会变成永久待办。
    """
    if cycle == "month":
        return [f"{year}-{m:02d}" for m in range(1, 13)]
    if cycle == "quarter":
        return [f"{year}-Q{q}" for q in (1, 2, 3, 4)]
    if cycle == "year":
        return [str(year)]
    return []


def _period_end(cycle: str, period: str) -> Optional[date]:
    """属期最后一天。"""
    try:
        if cycle == "month":
            year, month = int(period[:4]), int(period[5:7])
            return date(year, month, monthrange(year, month)[1])
        if cycle == "quarter":
            parsed = _quarter_label(period)
            if not parsed:
                return None
            year, quarter = parsed
            month = quarter * 3
            return date(year, month, monthrange(year, month)[1])
        if cycle == "year":
            return date(int(period[:4]), 12, 31)
    except (TypeError, ValueError):
        return None
    return None


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def compute_due_date_for_rule(
    rule: str,
    cycle: str,
    period: str,
    *,
    month_offset: Optional[int] = None,
    day: Optional[int] = None,
) -> Optional[date]:
    """按认定表的「申报期限规则」算截止日。

    与 `compute_due_date`（按写死的 freq）不同，这里只看认定行自己的规则，
    所以「营业账簿印花税按年、次年 1-15」这种与税种默认周期不一致的认定也能算对。
    """
    if rule == "none" or not rule:
        return None
    year_text = (period or "")[:4]
    if not year_text.isdigit():
        return None
    year = int(year_text)

    fixed_rules = {
        "next_year_0331": (3, 31),
        "next_year_0531": (5, 31),
        "next_year_0630": (6, 30),
        "next_year_0930": (9, 30),
    }
    if rule in fixed_rules:
        month, day_of_month = fixed_rules[rule]
        return date(year + 1, month, day_of_month)

    overrides = DEADLINE_OVERRIDES.get(year, {})
    if rule == "month_15":
        return _month_due_date(period, overrides)
    if rule == "quarter_15":
        parsed = _quarter_label(period)
        if not parsed:
            return None
        q_year, quarter = parsed
        key = f"{q_year}-Q{quarter}"
        override = overrides.get("quarter", {}).get(key)
        if override:
            return date.fromisoformat(override)
        due_month = quarter * 3 + 1
        due = date(q_year + (1 if due_month > 12 else 0), (due_month - 1) % 12 + 1, 15)
        return _next_weekday(due)
    if rule == "year_15":
        return _next_weekday(date(year + 1, 1, 15))
    if rule == "custom":
        end = _period_end(cycle, period)
        if end is None or month_offset is None or day is None:
            return None
        base = _add_months(end, int(month_offset))
        return date(base.year, base.month, min(int(day), monthrange(base.year, base.month)[1]))
    return None


async def _company_onboard_date(company_id: str) -> Optional[date]:
    """公司建档日（系统内创建时间）。征期物化以其为起点：建档前已截止的期间不制造逾期。"""
    from db.models import Company

    async with session_scope() as session:
        row = (
            await session.execute(select(Company.created_at).where(Company.company_id == company_id))
        ).scalar_one_or_none()
    if not row:
        return None
    try:
        return date.fromisoformat(str(row)[:10])
    except ValueError:
        return None


async def company_onboard_date(company_id: str) -> Optional[date]:
    """对外暴露建档日：差异对比要与征期物化用同一条截止口径。"""
    return await _company_onboard_date(company_id)


async def _waive_inapplicable_income_tax(company_id: str) -> int:
    """主体不缴企业所得税时，把已物化的 cit/cit_annual pending 存量自动豁免。

    处理「先按有限公司建档、后把主体类型改为个体户/个独」的存量：否则旧的
    企业所得税待办会一直挂在征期日历里，口径与税种判定自相矛盾。
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.company_id == company_id,
                    FilingDeadline.tax_kind.in_(list(CORPORATE_INCOME_TAX_KINDS)),
                    FilingDeadline.status == "pending",
                )
            )
        ).scalars().all()
        for row in rows:
            row.status = "waived"
            row.filed_evidence = {
                "reason": "entity_type_not_applicable",
                "auto": True,
                "waivedAt": datetime.utcnow().isoformat(),
            }
        return len(rows)


async def _waive_inapplicable_iit(company_id: str) -> int:
    """主体缴企业所得税时（ltd），把已物化的经营所得个税 pending 存量自动豁免。

    与 _waive_inapplicable_income_tax 对称：处理「先按个体户/个独建档、
    后把主体类型改为有限公司」的存量。
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.company_id == company_id,
                    FilingDeadline.tax_kind.in_(list(BUSINESS_INCOME_IIT_KINDS)),
                    FilingDeadline.status == "pending",
                )
            )
        ).scalars().all()
        for row in rows:
            row.status = "waived"
            row.filed_evidence = {
                "reason": "entity_type_not_applicable",
                "auto": True,
                "waivedAt": datetime.utcnow().isoformat(),
            }
        return len(rows)


async def _company_cycle(company_id: str, payload_key: str) -> str:
    """读取公司档案里的申报周期（月报/季报），缺省季报。

    vatFilingCycle 与 businessIncomeIitFilingCycle 各自独立：税局对增值税和经营
    所得个税的核定节奏可能不一致，不能用一个字段代表全部税种。
    """
    from services.account_service import get_company

    payload = await get_company(company_id) or {}
    cycle = str(payload.get(payload_key) or "").strip().lower()
    if cycle.startswith("month") or cycle in {"月", "月报", "月度"}:
        return "month"
    return "quarter"


async def _company_iit_cycle(company_id: str) -> str:
    """经营所得 A 表预缴周期（用户按税局核定设置；缺省季报）。"""
    return await _company_cycle(company_id, "businessIncomeIitFilingCycle")


def _cycle_from_registrations(registrations: List[Dict[str, Any]], group: str) -> Optional[str]:
    """从认定行推断某申报表组的申报周期（月报优先于季报）。"""
    cycles = {reg["filingCycle"] for reg in registrations if reg["returnGroup"] == group}
    if "month" in cycles:
        return "month"
    if "quarter" in cycles:
        return "quarter"
    return None


async def _company_vat_cycle(company_id: str) -> str:
    """公司档案里的增值税申报周期（认定表未同步时的兜底）。"""
    return await _company_cycle(company_id, "vatFilingCycle")


async def effective_vat_cycle(company_id: str) -> str:
    """增值税实际申报周期：税费种认定表 > 公司档案 > 默认季报。

    认定表是税局核定结果，公司档案那两个手填字段只是没同步前的兜底；
    两者不一致时以认定表为准，否则「公司信息里写着季报、征期按认定表按月铺」
    会让作业包和日历对不上。
    """
    from services import tax_registration_service

    registrations = await tax_registration_service.list_registrations(company_id)
    inferred = _cycle_from_registrations(registrations, "vat")
    if inferred:
        return inferred
    return await _company_vat_cycle(company_id)


async def effective_iit_cycle(company_id: str) -> str:
    """经营所得 A 表预缴周期：税费种认定表 > 公司档案 > 默认季报。"""
    from services import tax_registration_service

    registrations = await tax_registration_service.list_registrations(company_id)
    inferred = _cycle_from_registrations(registrations, "business_income_iit_prepay")
    if inferred:
        return inferred
    return await _company_iit_cycle(company_id)


AUTO_WAIVE_REASONS = frozenset({"filing_cycle_changed", "registration_changed"})


def _auto_waived(row: FilingDeadline) -> bool:
    evidence = row.filed_evidence or {}
    return (
        row.status == "waived"
        and isinstance(evidence, dict)
        and evidence.get("reason") in AUTO_WAIVE_REASONS
    )


def _reconcile_rows(
    existing: Dict[tuple, FilingDeadline],
    expected: Dict[tuple, date],
    source: str,
) -> int:
    """按 expected 校准当年征期：多出来的 pending 豁免，切回来时再恢复。

    例：季报改月报，2026-Q3 这条待办已无意义（改由 07/08/09 三条月度待办覆盖）；
    若当年又改回季报，Q3 应当恢复为待申报，而不是永久停在「已豁免」。
    只动系统自动豁免的记录：已申报（filed）、建档前豁免（pre_onboarding）、
    主体不适用豁免（entity_type_not_applicable）与人工豁免的保持原样。
    """
    reason = "registration_changed" if source == "registration" else "filing_cycle_changed"
    changed = 0
    for key, row in existing.items():
        if key in expected:
            if _auto_waived(row):
                row.status = "pending"
                row.filed_evidence = None
                row.filed_at = None
                changed += 1
            continue
        if row.status != "pending":
            continue
        row.status = "waived"
        row.filed_evidence = {
            "reason": reason,
            "auto": True,
            "waivedAt": datetime.utcnow().isoformat(),
        }
        changed += 1
    return changed


def _legacy_periods(tax_kind: str, meta: Dict[str, str], year: int, cycles: Dict[str, str]) -> List[str]:
    """内置清单口径下某税种该物化哪些期间（认定表为空时的兜底）。"""
    period_map = periods_for_year(year)
    if tax_kind in DYNAMIC_CYCLE_TAX_KINDS:
        return period_map.get(cycles.get(tax_kind, "quarter"), period_map["quarter"])
    freq = meta.get("freq")
    if freq == "annual_iit":
        return period_map["annual"]
    if freq in ("quarter", "annual_cit", "annual"):
        return period_map.get(freq, [])
    if freq == "month":
        return period_map["month"]
    return []


async def expected_schedule(company_id: str, year: Optional[int] = None) -> Dict[str, Any]:
    """某年「应该有哪些申报事项」的期望表。

    优先级：税费种认定表 > 公司档案申报周期 > 内置默认（季报）。
    返回 `items={(tax_kind, period): due_date}`，是征期物化、差异对比、
    结账向导本期义务的唯一真值来源。
    """
    from services import tax_registration_service

    year = year or date.today().year
    entity_type = await company_entity_service.get_company_entity_type(company_id)
    cit_applicable = company_entity_service.pays_corporate_income_tax(entity_type)
    registrations = await tax_registration_service.list_registrations(company_id)
    cycles = {
        "vat": await _company_vat_cycle(company_id),
        "business_income_iit_prepay": (
            await _company_iit_cycle(company_id) if not cit_applicable else "quarter"
        ),
    }
    if registrations:
        vat_from_reg = _cycle_from_registrations(registrations, "vat")
        if vat_from_reg:
            cycles["vat"] = vat_from_reg
        iit_from_reg = _cycle_from_registrations(registrations, "business_income_iit_prepay")
        if iit_from_reg and not cit_applicable:
            cycles["business_income_iit_prepay"] = iit_from_reg
    items: Dict[tuple, date] = {}
    rows_by_group: Dict[str, List[Dict[str, Any]]] = {}

    source = "registration" if registrations else "default"
    # 主体类型与认定表冲突的行不生成待办（ltd 认定经营所得 / 个体户认定企税），
    # 页面另给黄条告警——静默生成会让用户在错误的税种上对着待办发呆。
    conflicts: List[Dict[str, Any]] = []

    if registrations:
        for reg in registrations:
            group = reg["returnGroup"]
            if group in CORPORATE_INCOME_TAX_KINDS and not cit_applicable:
                conflicts.append(reg)
                continue
            if group in BUSINESS_INCOME_IIT_KINDS and cit_applicable:
                conflicts.append(reg)
                continue
            cycle = reg["filingCycle"]
            periods = periods_for_cycle(cycle, year)
            if not periods:
                continue
            rows_by_group.setdefault(group, []).append(reg)
            for period in periods:
                due = compute_due_date_for_rule(
                    reg["deadlineRule"],
                    cycle,
                    period,
                    month_offset=reg.get("dueMonthOffset"),
                    day=reg.get("dueDay"),
                )
                if not due:
                    continue
                key = (group, period)
                if key not in items or due < items[key]:
                    items[key] = due
        # 认定表不体现的法定申报义务：财报报送、工商年报、汇算清缴
        extra_kinds = list(NON_REGISTRATION_OBLIGATIONS)
        extra_kinds.append("cit_annual" if cit_applicable else "business_income_iit_annual")
        for tax_kind in extra_kinds:
            meta = TAX_KINDS.get(tax_kind)
            if not meta:
                continue
            for period in _legacy_periods(tax_kind, meta, year, cycles):
                due = compute_due_date(tax_kind, period)
                if due:
                    items.setdefault((tax_kind, period), due)
    else:
        for tax_kind, meta in TAX_KINDS.items():
            if tax_kind in REGISTRATION_ONLY_KINDS:
                continue
            if not cit_applicable and tax_kind in CORPORATE_INCOME_TAX_KINDS:
                continue
            if cit_applicable and tax_kind in BUSINESS_INCOME_IIT_KINDS:
                continue
            for period in _legacy_periods(tax_kind, meta, year, cycles):
                due = compute_due_date(tax_kind, period)
                if due:
                    items[(tax_kind, period)] = due

    return {
        "year": year,
        "items": items,
        "cycles": cycles,
        "source": source,
        "registrations": registrations,
        "rowsByGroup": rows_by_group,
        "conflicts": conflicts,
    }


async def refresh_deadlines(company_id: str, year: Optional[int] = None) -> Dict[str, Any]:
    """按期望表物化某年的征期记录（幂等；已存在的保留状态并校准 due_date）。

    期望表来自 `expected_schedule`：有税费种认定表就完全按认定表铺期间，
    没有则回落到内置清单（老公司行为不变）。
    公司建档日之前已截止的期间不物化为待办；历史上已物化为 pending 的存量自动豁免
    （waived），避免首次打开页面即出现「逾期 176 天」的假噪音。
    """
    year = year or date.today().year
    onboard_date = await _company_onboard_date(company_id)
    entity_type = await company_entity_service.get_company_entity_type(company_id)
    cit_applicable = company_entity_service.pays_corporate_income_tax(entity_type)
    if cit_applicable:
        auto_waived = await _waive_inapplicable_iit(company_id)
    else:
        auto_waived = await _waive_inapplicable_income_tax(company_id)

    schedule = await expected_schedule(company_id, year)
    items: Dict[tuple, date] = schedule["items"]
    created = 0
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.company_id == company_id,
                    FilingDeadline.period.like(f"{year}%"),
                )
            )
        ).scalars().all()
        existing = {(row.tax_kind, row.period): row for row in rows}
        for (tax_kind, period), due in items.items():
            row = existing.get((tax_kind, period))
            if onboard_date and due < onboard_date:
                # 建档前已截止的期间：不再新建；此前误物化的 pending 存量自动豁免
                if row is not None and row.status == "pending":
                    row.status = "waived"
                    row.filed_evidence = {
                        "reason": "pre_onboarding",
                        "auto": True,
                        "waivedAt": datetime.utcnow().isoformat(),
                    }
                    auto_waived += 1
                continue
            if row is not None:
                # 若计算规则校准（如修正错误的所属月中旬为次月中旬），同步校准未锁定的 due_date
                if row.due_date != due:
                    row.due_date = due
                continue
            session.add(
                FilingDeadline(
                    id=new_id(),
                    company_id=company_id,
                    tax_kind=tax_kind,
                    period=period,
                    due_date=due,
                    status="pending",
                )
            )
            created += 1
        auto_waived += _reconcile_rows(existing, items, schedule["source"])

    return {
        "year": year,
        "created": created,
        "autoWaived": auto_waived,
        "cycles": schedule["cycles"],
        "source": schedule["source"],
        "conflicts": [{"levyItem": c["levyItem"], "returnGroup": c["returnGroup"]} for c in schedule["conflicts"]],
    }


def _covers_month(period: str, year: int, month: int) -> bool:
    if period == f"{year}-{month:02d}":
        return True
    return period == f"{year}-Q{(month - 1) // 3 + 1}"


async def obligations_for_month(company_id: str, month: str) -> Dict[str, Any]:
    """某个月份对应的「本期申报义务」。

    结账向导要回答的是"我这个月结完，接下来该报什么"，而不是"这个月有什么税"。
    因此按**申报期间**归集：季报税种显示覆盖该月的季度，月报税种显示该月。
    期间来源是 `expected_schedule`——认定表里没有的税种不会出现在这里。
    """
    try:
        year, month_no = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        raise ValueError("month 需为 YYYY-MM 格式") from None
    quarter = (month_no - 1) // 3 + 1
    quarter_period = f"{year}-Q{quarter}"

    schedule = await expected_schedule(company_id, year)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.company_id == company_id,
                    FilingDeadline.period.like(f"{year}%"),
                )
            )
        ).scalars().all()
    by_key = {(row.tax_kind, row.period): row for row in rows}

    records: list[Dict[str, Any]] = []
    for (tax_kind, period), due in schedule["items"].items():
        if not _covers_month(period, year, month_no):
            continue
        meta = TAX_KINDS.get(tax_kind)
        if not meta:
            continue
        is_month_period = len(period) == 7 and period[4] == "-" and period[5:].isdigit()
        row = by_key.get((tax_kind, period))
        records.append(
            {
                "taxKind": tax_kind,
                "name": meta["name"],
                "period": period,
                "cycle": "month" if is_month_period else "quarter",
                "dueDate": due.isoformat(),
                "daysLeft": (due - date.today()).days,
                "status": row.status if row else "not_materialized",
                "deadlineId": row.id if row else None,
                "source": schedule["source"],
            }
        )
    order = {kind: index for index, kind in enumerate(RETURN_GROUP_ORDER)}
    records.sort(key=lambda r: (order.get(r["taxKind"], 99), r["period"]))
    cycles = schedule["cycles"]
    cycle_label = lambda c: "月报" if c == "month" else "季报"  # noqa: E731
    return {
        "month": month,
        "quarter": quarter_period,
        "cycles": {"vat": cycles["vat"], "businessIncomeIit": cycles["business_income_iit_prepay"]},
        "vatCycleLabel": cycle_label(cycles["vat"]),
        "obligations": records,
        "source": schedule["source"],
    }


async def list_deadlines(
    company_id: str, *, upcoming_days: Optional[int] = None, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """列出征期（默认全部未完成+近期完成），附剩余天数。"""
    today = date.today()
    async with session_scope() as session:
        stmt = select(FilingDeadline).where(FilingDeadline.company_id == company_id)
        if status:
            stmt = stmt.where(FilingDeadline.status == status)
        stmt = stmt.order_by(FilingDeadline.due_date.asc()).limit(200)
        rows = (await session.execute(stmt)).scalars().all()
        out = []
        for row in rows:
            days_left = (row.due_date - today).days
            if upcoming_days is not None and days_left > upcoming_days:
                continue
            meta = TAX_KINDS.get(row.tax_kind, {})
            out.append(
                {
                    "id": row.id,
                    "taxKind": row.tax_kind,
                    "taxName": meta.get("name", row.tax_kind),
                    "guide": meta.get("guide", ""),
                    "period": row.period,
                    "dueDate": row.due_date.isoformat(),
                    "daysLeft": days_left,
                    "status": row.status,
                    "filedAt": row.filed_at.isoformat() if row.filed_at else None,
                    "filedEvidence": row.filed_evidence,
                }
            )
        return out


async def mark_filed(
    company_id: str,
    user_id: str,
    deadline_id: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """标记已申报并归档回执信息（闭环）。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.id == deadline_id, FilingDeadline.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("征期记录不存在")
        row.status = "filed"
        row.filed_at = datetime.utcnow()
        row.filed_evidence = evidence or {}
        result = {
            "id": row.id,
            "taxKind": row.tax_kind,
            "period": row.period,
            "status": row.status,
            "filedAt": row.filed_at.isoformat(),
        }
    from services.audit_service import log_action

    await log_action(company_id, user_id, "filing_mark_filed", "filing_deadline", deadline_id, evidence or {})
    return result


async def waive_deadline(
    company_id: str,
    user_id: str,
    deadline_id: str,
    *,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """把某征期标记为「本期不适用」（豁免），用于建档前期间等确实无需申报的场景。"""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(FilingDeadline).where(
                    FilingDeadline.id == deadline_id, FilingDeadline.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("征期记录不存在")
        if row.status != "pending":
            raise ValueError("仅待申报状态可标记为不适用")
        row.status = "waived"
        row.filed_evidence = {
            "reason": reason or "manual",
            "via": "tax-filing-page",
            "waivedAt": datetime.utcnow().isoformat(),
        }
        result = {
            "id": row.id,
            "taxKind": row.tax_kind,
            "period": row.period,
            "status": row.status,
        }
    from services.audit_service import log_action

    await log_action(company_id, user_id, "filing_waive", "filing_deadline", deadline_id, {"reason": reason or "manual"})
    return result


def deadline_todos(deadlines: List[Dict[str, Any]], *, window_days: int = 14) -> List[Dict[str, Any]]:
    """把临近征期（默认 14 天内/逾期 60 天内）转成 todo 形态，供 todo_service 注入。

    逾期超过 60 天的历史征期不再制造待办噪音（在税报页仍可见，可手动补记/豁免）。
    """
    todos: List[Dict[str, Any]] = []
    for d in deadlines:
        if d["status"] != "pending" or d["daysLeft"] > window_days or d["daysLeft"] < -60:
            continue
        overdue = d["daysLeft"] < 0
        days_text = f"已逾期 {-d['daysLeft']} 天" if overdue else f"剩 {d['daysLeft']} 天"
        todos.append(
            {
                "id": f"filing:{d['id']}",
                "todoKey": f"filing:{d['id']}",
                "type": "filing",
                "category": "filing",
                "level": "high" if (overdue or d["daysLeft"] <= 3) else "medium",
                "priority": "high" if (overdue or d["daysLeft"] <= 3) else "medium",
                "title": f"{'逾期未申报' if overdue else '征期临近'} - {d['taxName']}（{d['period']}）",
                "desc": f"截止 {d['dueDate']}（{days_text}）。{d['guide']}",
                "done": False,
                "dismissed": False,
                "amount": 0,
                "meta": {"deadlineId": d["id"], "ruleId": "filing_deadline"},
            }
        )
    return todos
