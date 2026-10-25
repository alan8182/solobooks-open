# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""税费种认定：认定行维护、粘贴/截图导入、申报节奏预览、与征期差异。

数据分层（不要混）：

- `tax_registrations` 一行 = 电子税务局「税费种认定信息」页面上的一行，字段忠实原样；
- `return_group` = 这张认定行最终在哪张申报表里报（= `filing_deadlines.tax_kind`）；
- 征期由 `filing_deadline_service.expected_schedule` 按 `return_group` 物化。

所以城建税/教育费附加/地方教育附加三行只会产生一条「增值税及附加税费」待办，
印花税多品目会合并成「财产和行为税纳税申报表」——与电子税务局的操作台账一致，
而不是按征收项目堆出一屏同截止日的待办。
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select

from db.models import TaxRegistration, new_id
from db.session import session_scope
from services import filing_deadline_service

# 申报表组 → 人类可读名称（页面上「归属申报表」列）
GROUP_LABELS: Dict[str, str] = {
    "vat": "增值税及附加税费申报表",
    "cit": "企业所得税月（季）度预缴申报表",
    "cit_annual": "企业所得税年度申报表",
    "business_income_iit_prepay": "经营所得个税预缴（A 表）",
    "business_income_iit_annual": "经营所得个税汇算（B 表）",
    "iit_withholding": "个人所得税扣缴申报表",
    "stamp_duty": "财产和行为税纳税申报表",
    "culture_fee": "文化事业建设费申报表",
    "disability_fund": "残疾人就业保障金申报表",
    "fin_statements": "财务报表报送",
    "annual_report": "工商年报（市监局）",
    "other": "其他核定申报事项",
}

CYCLE_LABELS = {"month": "月", "quarter": "季", "year": "年", "other": "按次"}

DEADLINE_RULE_LABELS = {
    "month_15": "期满之日起 15 日内（次月 15 日）",
    "quarter_15": "期满之日起 15 日内（季末次月 15 日）",
    "year_15": "期满之日起 15 日内（次年 1 月 15 日）",
    "next_year_0331": "次年 3 月 31 日",
    "next_year_0531": "次年 5 月 31 日",
    "next_year_0630": "次年 6 月 30 日",
    "next_year_0930": "次年 9 月 30 日",
    "custom": "自定义",
    "none": "无固定期限",
}

# 征收项目关键词 → 申报表组。顺序有意义：「经营所得」必须排在「个人所得税」前面，
# 否则「经营所得个人所得税」会被误判成工资薪金扣缴。
_GROUP_KEYWORDS: Sequence[tuple[str, tuple[str, ...]]] = (
    ("disability_fund", ("残疾人就业保障金", "残保金")),
    ("culture_fee", ("文化事业建设费",)),
    ("vat", ("城市维护建设税", "教育费附加", "地方教育附加", "增值税")),
    ("cit", ("企业所得税",)),
    ("business_income_iit_prepay", ("经营所得",)),
    ("iit_withholding", ("个人所得税", "工资薪金", "劳务报酬", "稿酬", "特许权使用费")),
    ("stamp_duty", ("印花税",)),
)

_CYCLE_ALIASES: Sequence[tuple[str, tuple[str, ...]]] = (
    ("quarter", ("季度", "季报", "按季", "quarter", "季")),
    ("month", ("月度", "月报", "按月", "month", "月")),
    ("year", ("年度", "年报", "按年", "year", "年")),
    ("other", ("按次", "次", "半年", "once", "half")),
)

_DATE_MD = re.compile(r"(次年)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")

_FIELD_ALIASES: Dict[str, tuple[str, ...]] = {
    "levy_item": ("levyItem", "levy_item", "征收项目"),
    "levy_category": ("levyCategory", "levy_category", "征收品目"),
    "levy_sub_item": ("levySubItem", "levy_sub_item", "征收子目"),
    "filing_cycle": ("filingCycle", "filing_cycle", "纳税期限"),
    "deadline_text": ("deadlineText", "deadline_text", "申报期限"),
    "levy_rate": ("levyRate", "levy_rate", "征收率"),
    "tax_rate": ("taxRate", "tax_rate", "税率或单位税额", "税率"),
    "main_flag": ("mainFlag", "main_flag", "主附税标志"),
    "collection_method": ("collectionMethod", "collection_method", "征收代理方式"),
    "note": ("note", "备注"),
}


def _pick(raw: Dict[str, Any], field: str) -> Any:
    for key in _FIELD_ALIASES[field]:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("　", "")


def parse_cycle(value: Any) -> str:
    """纳税期限原文 → month / quarter / year / other。"""
    text = _compact(value).lower()
    if not text:
        return "quarter"
    for cycle, aliases in _CYCLE_ALIASES:
        if any(alias in text for alias in aliases):
            return cycle
    return "quarter"


def map_return_group(levy_item: str, levy_category: str = "", main_flag: str = "main") -> str:
    """征收项目/品目 → 申报表组。"""
    text = f"{levy_item or ''}|{levy_category or ''}"
    for group, keywords in _GROUP_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return group
    if main_flag == "vat_attachment":
        return "vat"
    return "other"


def infer_deadline_rule(cycle: str, deadline_text: Any) -> Dict[str, Any]:
    """申报期限原文 → (规则, 自定义偏移)。

    「期满之日起 15 日内」占绝大多数；年度事项常写成「次年 9 月 30 号」，落 custom。
    年度行若只写「6 月 30 日」而不写次年，按次年理解——年报类申报都在次年。
    """
    if cycle == "other":
        return {"deadlineRule": "none", "dueMonthOffset": None, "dueDay": None}
    text = _compact(deadline_text)
    if not text:
        default = {"month": "month_15", "quarter": "quarter_15", "year": "year_15"}[cycle]
        return {"deadlineRule": default, "dueMonthOffset": None, "dueDay": None}
    if "15日" in text or "15号" in text:
        default = {"month": "month_15", "quarter": "quarter_15", "year": "year_15"}[cycle]
        return {"deadlineRule": default, "dueMonthOffset": None, "dueDay": None}
    match = _DATE_MD.search(text)
    if match and cycle == "year":
        month, day = int(match.group(2)), int(match.group(3))
        # 以 12 月为属期结束月：次年 X 月 → 偏移 X 个月
        return {"deadlineRule": "custom", "dueMonthOffset": month, "dueDay": day}
    if match and cycle in ("month", "quarter"):
        # 月度/季度写成具体日期时，按「属期结束后第 N 个月的第 D 日」处理
        base_month = 1 if cycle == "month" else 3
        month = int(match.group(2))
        offset = month if not match.group(1) else 12 + month
        return {
            "deadlineRule": "custom",
            "dueMonthOffset": offset - base_month + 1,
            "dueDay": int(match.group(3)),
        }
    default = {"month": "month_15", "quarter": "quarter_15", "year": "year_15"}[cycle]
    return {"deadlineRule": default, "dueMonthOffset": None, "dueDay": None}


def parse_rate(value: Any) -> Optional[Decimal]:
    """征收率/税率 → Decimal；认定表里「0」表示该列不适用，返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        parsed = Decimal(str(value))
    else:
        text = _compact(value).replace(",", "").replace("，", "")
        if text in ("", "-", "—", "/", "不适用", "免"):
            return None
        percent = text.endswith("%")
        if percent:
            text = text[:-1]
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
        if percent:
            parsed = parsed / Decimal(100)
    if parsed == 0:
        return None
    return parsed


def normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把一行原始输入（前端表格 / OCR 结果 / 手工表单）归一化成落库字段。"""
    levy_item = _compact(_pick(raw, "levy_item"))
    if not levy_item:
        raise ValueError("征收项目不能为空")
    levy_category = _compact(_pick(raw, "levy_category"))
    levy_sub_item = _compact(_pick(raw, "levy_sub_item"))
    cycle_text = _pick(raw, "filing_cycle")
    cycle = parse_cycle(cycle_text)
    deadline_text = _compact(_pick(raw, "deadline_text"))

    main_flag_raw = _compact(_pick(raw, "main_flag"))
    main_flag = "vat_attachment" if ("附" in main_flag_raw or "attachment" in main_flag_raw.lower()) else "main"
    collection_raw = _compact(_pick(raw, "collection_method"))
    collection_method = "withholding" if ("代扣" in collection_raw or "代征" in collection_raw or "withhold" in collection_raw.lower()) else "self"

    raw_tax_rate = _pick(raw, "tax_rate")
    tax_rate = parse_rate(raw_tax_rate)
    rate_unit = ""
    if tax_rate is None and raw_tax_rate not in (None, "", 0, "0"):
        rate_unit = _compact(raw_tax_rate)[:32]

    rule = infer_deadline_rule(cycle, deadline_text)
    return_group = map_return_group(levy_item, levy_category, main_flag)
    return {
        "levy_item": levy_item,
        "levy_category": levy_category,
        "levy_sub_item": levy_sub_item,
        "filing_cycle": cycle,
        "filing_cycle_text": _compact(cycle_text)[:32] or CYCLE_LABELS.get(cycle, ""),
        "deadline_text": deadline_text[:64],
        "deadline_rule": rule["deadlineRule"],
        "due_month_offset": rule["dueMonthOffset"],
        "due_day": rule["dueDay"],
        "levy_rate": parse_rate(_pick(raw, "levy_rate")),
        "tax_rate": tax_rate,
        "rate_unit": rate_unit,
        "main_flag": main_flag,
        "collection_method": collection_method,
        "return_group": return_group,
        "note": _compact(_pick(raw, "note"))[:255],
    }


def _row_key(row: Dict[str, Any]) -> tuple:
    return (
        row["levy_item"],
        row["levy_category"],
        row["levy_sub_item"],
        row["filing_cycle"],
    )


def serialize_normalized(norm: Dict[str, Any]) -> Dict[str, Any]:
    """未落库的归一化行 → 与 serialize_registration 同形的展示对象（无 id）。"""
    return {
        "id": None,
        "seq": 0,
        "levyItem": norm["levy_item"],
        "levyCategory": norm["levy_category"],
        "levySubItem": norm["levy_sub_item"],
        "filingCycle": norm["filing_cycle"],
        "filingCycleText": norm["filing_cycle_text"],
        "deadlineText": norm["deadline_text"],
        "deadlineRule": norm["deadline_rule"],
        "deadlineRuleLabel": DEADLINE_RULE_LABELS.get(norm["deadline_rule"], norm["deadline_rule"]),
        "dueMonthOffset": norm["due_month_offset"],
        "dueDay": norm["due_day"],
        "levyRate": float(norm["levy_rate"]) if norm["levy_rate"] is not None else None,
        "taxRate": float(norm["tax_rate"]) if norm["tax_rate"] is not None else None,
        "rateUnit": norm["rate_unit"],
        "mainFlag": norm["main_flag"],
        "collectionMethod": norm["collection_method"],
        "returnGroup": norm["return_group"],
        "returnGroupLabel": GROUP_LABELS.get(norm["return_group"], norm["return_group"]),
        "status": "active",
        "source": "preview",
        "note": norm["note"],
    }


def parse_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """只解析不落库：给导入弹窗做「确认前预览」，逐行返回归一化结果与错误。"""
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index, raw in enumerate(rows, start=1):
        try:
            items.append(serialize_normalized(normalize_row(raw)))
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    return {"items": items, "errors": errors}


def serialize_registration(row: TaxRegistration) -> Dict[str, Any]:
    return {
        "id": row.id,
        "seq": row.seq,
        "levyItem": row.levy_item,
        "levyCategory": row.levy_category,
        "levySubItem": row.levy_sub_item,
        "filingCycle": row.filing_cycle,
        "filingCycleText": row.filing_cycle_text or CYCLE_LABELS.get(row.filing_cycle, ""),
        "deadlineText": row.deadline_text,
        "deadlineRule": row.deadline_rule,
        "deadlineRuleLabel": DEADLINE_RULE_LABELS.get(row.deadline_rule, row.deadline_rule),
        "dueMonthOffset": row.due_month_offset,
        "dueDay": row.due_day,
        "levyRate": float(row.levy_rate) if row.levy_rate is not None else None,
        "taxRate": float(row.tax_rate) if row.tax_rate is not None else None,
        "rateUnit": row.rate_unit,
        "mainFlag": row.main_flag,
        "collectionMethod": row.collection_method,
        "returnGroup": row.return_group,
        "returnGroupLabel": GROUP_LABELS.get(row.return_group, row.return_group),
        "status": row.status,
        "source": row.source,
        "note": row.note,
    }


def _apply(row: TaxRegistration, norm: Dict[str, Any], *, source: Optional[str] = None) -> None:
    for field, value in norm.items():
        setattr(row, field, value)
    if source:
        row.source = source
    row.status = "active"


async def list_registrations(company_id: str, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
    async with session_scope() as session:
        stmt = select(TaxRegistration).where(TaxRegistration.company_id == company_id)
        if not include_inactive:
            stmt = stmt.where(TaxRegistration.status == "active")
        stmt = stmt.order_by(TaxRegistration.seq.asc(), TaxRegistration.created_at.asc())
        rows = (await session.execute(stmt)).scalars().all()
        return [serialize_registration(row) for row in rows]


async def _log(company_id: str, user_id: str, action: str, entity_id: Optional[str], details: Dict[str, Any]) -> None:
    from services.audit_service import log_action

    try:
        await log_action(company_id, user_id, action, "tax_registration", entity_id, details)
    except Exception:  # noqa: BLE001 —— 审计失败不拖垮认定表写入
        pass


async def create_registration(company_id: str, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    norm = normalize_row(payload)
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(TaxRegistration).where(TaxRegistration.company_id == company_id)
            )
        ).scalars().all()
        index = {_row_key({k: getattr(r, k) for k in ("levy_item", "levy_category", "levy_sub_item", "filing_cycle")}): r for r in existing}
        if _row_key(norm) in index:
            raise ValueError("同一征收项目/品目/子目/纳税期限的认定行已存在，请直接编辑")
        seq = max((r.seq for r in existing), default=0) + 1
        row = TaxRegistration(
            id=new_id(),
            company_id=company_id,
            seq=int(payload.get("seq") or seq),
            **norm,
        )
        session.add(row)
        await session.flush()
        result = serialize_registration(row)
    await _log(company_id, user_id, "tax_registration_create", result["id"], {"after": norm})
    return result


async def update_registration(
    company_id: str, user_id: str, registration_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    norm = normalize_row(payload)
    async with session_scope() as session:
        row = await session.get(TaxRegistration, registration_id)
        if row is None or row.company_id != company_id:
            raise LookupError("认定行不存在")
        others = (
            await session.execute(
                select(TaxRegistration).where(
                    TaxRegistration.company_id == company_id,
                    TaxRegistration.id != registration_id,
                    TaxRegistration.status == "active",
                )
            )
        ).scalars().all()
        for other in others:
            if _row_key(norm) == (
                other.levy_item,
                other.levy_category,
                other.levy_sub_item,
                other.filing_cycle,
            ):
                raise ValueError("与已有认定行重复（征收项目/品目/子目/纳税期限相同）")
        before = serialize_registration(row)
        _apply(row, norm)
        await session.flush()
        result = serialize_registration(row)
    await _log(company_id, user_id, "tax_registration_update", registration_id, {"before": before, "after": norm})
    return result


async def deactivate_registration(company_id: str, user_id: str, registration_id: str) -> Dict[str, Any]:
    async with session_scope() as session:
        row = await session.get(TaxRegistration, registration_id)
        if row is None or row.company_id != company_id:
            raise LookupError("认定行不存在")
        row.status = "inactive"
        await session.flush()
        result = serialize_registration(row)
    await _log(company_id, user_id, "tax_registration_deactivate", registration_id, {})
    return result


async def import_registrations(
    company_id: str,
    user_id: str,
    rows: Iterable[Dict[str, Any]],
    *,
    mode: str = "merge",
    source: str = "paste",
) -> Dict[str, Any]:
    """批量导入（粘贴表格 / 截图识别结果）。

    mode=merge：只 upsert 本次给出的行，其余保持不动（推荐，适合补录）。
    mode=replace：本次没出现的 active 行置 inactive（适合整表同步）。
    """
    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index, raw in enumerate(rows, start=1):
        try:
            normalized.append(normalize_row(raw))
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    if errors:
        raise ValueError("；".join(errors[:5]) + ("等" if len(errors) > 5 else ""))
    if not normalized:
        raise ValueError("没有可导入的行")

    # 同一批里重复的行以后出现的为准
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for norm in normalized:
        deduped[_row_key(norm)] = norm

    created = updated = deactivated = 0
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(TaxRegistration).where(TaxRegistration.company_id == company_id)
            )
        ).scalars().all()
        index = {
            (r.levy_item, r.levy_category, r.levy_sub_item, r.filing_cycle): r for r in existing
        }
        seq = max((r.seq for r in existing), default=0)
        for norm in deduped.values():
            seq += 1
            row = index.get(_row_key(norm))
            if row is None:
                session.add(
                    TaxRegistration(id=new_id(), company_id=company_id, seq=seq, source=source, **norm)
                )
                created += 1
                continue
            _apply(row, norm)
            if row.seq != seq:
                row.seq = seq
            updated += 1
        if mode == "replace":
            keep = set(deduped)
            for key, row in index.items():
                if key in keep or row.status != "active":
                    continue
                row.status = "inactive"
                deactivated += 1
        await session.flush()

    summary = {"created": created, "updated": updated, "deactivated": deactivated, "total": len(deduped)}
    await _log(company_id, user_id, "tax_registration_import", None, {"mode": mode, "source": source, **summary})
    return summary


def build_template(entity_type: str = "ltd") -> List[Dict[str, Any]]:
    """按主体类型带出默认认定行（小规模纳税人口径）。

    默认带出的只是**产品默认值**，正式申报以税局认定表为准——页面上必须标明。
    文化事业建设费不默认带：只有广告/娱乐服务收入才核定，硬塞给用户会变成噪音。
    """
    rows: List[Dict[str, Any]] = [
        {
            "levyItem": "增值税",
            "levyCategory": "商业(3%)",
            "filingCycle": "季",
            "deadlineText": "期满之日起15日内",
            "levyRate": 0.03,
            "taxRate": 0.03,
            "mainFlag": "主税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "城市维护建设税",
            "levyCategory": "市区（增值税附征）",
            "filingCycle": "季",
            "deadlineText": "期满之日起15日内",
            "taxRate": 0.07,
            "mainFlag": "增值税附税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "教育费附加",
            "levyCategory": "增值税教育费附加",
            "filingCycle": "季",
            "deadlineText": "期满之日起15日内",
            "taxRate": 0.03,
            "mainFlag": "增值税附税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "地方教育附加",
            "levyCategory": "增值税地方教育附加",
            "filingCycle": "季",
            "deadlineText": "期满之日起15日内",
            "taxRate": 0.02,
            "mainFlag": "增值税附税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "个人所得税",
            "levyCategory": "工资薪金所得",
            "filingCycle": "月",
            "deadlineText": "期满之日起15日内",
            "mainFlag": "主税",
            "collectionMethod": "代扣代缴",
        },
        {
            "levyItem": "印花税",
            "levyCategory": "买卖合同",
            "filingCycle": "季",
            "deadlineText": "期满之日起15日内",
            "taxRate": 0.0003,
            "mainFlag": "主税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "印花税",
            "levyCategory": "营业账簿",
            "filingCycle": "年",
            "deadlineText": "期满之日起15日内",
            "taxRate": 0.00025,
            "mainFlag": "主税",
            "collectionMethod": "自行申报",
        },
        {
            "levyItem": "残疾人就业保障金",
            "levyCategory": "残疾人就业保障金",
            "levySubItem": "按比例",
            "filingCycle": "年",
            "deadlineText": "次年9月30号",
            "mainFlag": "主税",
            "collectionMethod": "自行申报",
        },
    ]
    if entity_type == "ltd":
        rows.insert(
            4,
            {
                "levyItem": "企业所得税",
                "levyCategory": "应纳税所得额",
                "filingCycle": "季",
                "deadlineText": "期满之日起15日内",
                "taxRate": 0.25,
                "mainFlag": "主税",
                "collectionMethod": "自行申报",
            },
        )
    else:
        rows.insert(
            4,
            {
                "levyItem": "个人所得税",
                "levyCategory": "经营所得",
                "filingCycle": "季",
                "deadlineText": "期满之日起15日内",
                "mainFlag": "主税",
                "collectionMethod": "自行申报",
            },
        )
    return [normalize_row({**row}) for row in rows]


def _period_sort_key(period: str) -> tuple:
    digits = re.findall(r"\d+", period)
    return tuple(int(part) for part in digits) if digits else (0,)


async def preview_schedule(company_id: str, year: int) -> Dict[str, Any]:
    """申报节奏预览：按申报表组 × 属期，附截止日与当前征期状态。"""
    schedule = await filing_deadline_service.expected_schedule(company_id, year)
    deadlines = await filing_deadline_service.list_deadlines(company_id)
    by_key = {
        (row["taxKind"], row["period"]): row for row in deadlines if row["period"].startswith(str(year))
    }

    items: List[Dict[str, Any]] = []
    for (group, period), due in schedule["items"].items():
        row = by_key.get((group, period))
        items.append(
            {
                "returnGroup": group,
                "name": GROUP_LABELS.get(group)
                or filing_deadline_service.TAX_KINDS.get(group, {}).get("name", group),
                "period": period,
                "cycle": "month" if re.fullmatch(r"\d{4}-\d{2}", period) else ("year" if period.isdigit() else "quarter"),
                "dueDate": due.isoformat(),
                "status": row["status"] if row else "not_materialized",
                "deadlineId": row["id"] if row else None,
                "daysLeft": row["daysLeft"] if row else (due - date.today()).days,
            }
        )
    items.sort(key=lambda item: (_period_sort_key(item["period"]), item["returnGroup"]))

    groups: List[Dict[str, Any]] = []
    seen_groups = {item["returnGroup"] for item in items}
    for group in filing_deadline_service.RETURN_GROUP_ORDER:
        if group not in seen_groups:
            continue
        group_items = [item for item in items if item["returnGroup"] == group]
        groups.append(
            {
                "returnGroup": group,
                "name": group_items[0]["name"],
                "registrations": schedule["rowsByGroup"].get(group, []),
                "periods": [
                    {**item, "returnGroup": None, "name": None} for item in group_items
                ],
            }
        )

    by_period: Dict[str, Dict[str, Any]] = {}
    by_due_month: Dict[str, Dict[str, Any]] = {}
    for item in items:
        period_bucket = by_period.setdefault(
            item["period"],
            {"period": item["period"], "dueDate": item["dueDate"], "items": []},
        )
        if item["dueDate"] < period_bucket["dueDate"]:
            period_bucket["dueDate"] = item["dueDate"]
        period_bucket["items"].append(
            {k: item[k] for k in ("returnGroup", "name", "dueDate", "status", "deadlineId", "daysLeft")}
        )
        month_key = item["dueDate"][:7]
        month_bucket = by_due_month.setdefault(month_key, {"month": month_key, "items": []})
        month_bucket["items"].append(
            {
                **{k: item[k] for k in ("returnGroup", "name", "period", "dueDate", "status", "deadlineId")},
                "daysLeft": item["daysLeft"],
            }
        )

    return {
        "year": year,
        "source": schedule["source"],
        "cycles": schedule["cycles"],
        "conflicts": [
            {"levyItem": c["levyItem"], "levyCategory": c["levyCategory"], "returnGroup": c["returnGroup"]}
            for c in schedule["conflicts"]
        ],
        "summary": {
            "itemCount": len(items),
            "groupCount": len(seen_groups),
            "periodCount": len(by_period),
        },
        "groups": groups,
        "byPeriod": sorted(by_period.values(), key=lambda b: _period_sort_key(b["period"])),
        "byDueMonth": [by_due_month[key] for key in sorted(by_due_month)],
    }


async def diff_against_deadlines(company_id: str, year: int) -> Dict[str, Any]:
    """认定表口径 vs 当前已物化征期：将要新增/豁免/恢复多少条。"""
    schedule = await filing_deadline_service.expected_schedule(company_id, year)
    deadlines = await filing_deadline_service.list_deadlines(company_id)
    year_rows = {(row["taxKind"], row["period"]): row for row in deadlines if row["period"].startswith(str(year))}
    # 建档前已截止的期间 refresh 不会物化，差异对比必须同步过滤，否则
    # 年中建档的公司会永远显示「将新增 N 条」，点「应用到征期」却清不掉。
    onboard = await filing_deadline_service.company_onboard_date(company_id)
    expected = {
        key: due for key, due in schedule["items"].items() if not (onboard and due < onboard)
    }

    will_create = [key for key in expected if key not in year_rows]
    will_waive = [
        key for key, row in year_rows.items() if key not in expected and row["status"] == "pending"
    ]
    will_restore = [
        key
        for key, row in year_rows.items()
        if key in expected
        and row["status"] == "waived"
        and (row.get("filedEvidence") or {}).get("reason") in filing_deadline_service.AUTO_WAIVE_REASONS
    ]

    def _brief(keys: List[tuple]) -> List[Dict[str, Any]]:
        out = []
        for group, period in sorted(keys, key=lambda k: (_period_sort_key(k[1]), k[0]))[:20]:
            out.append(
                {
                    "taxKind": group,
                    "name": GROUP_LABELS.get(group)
                    or filing_deadline_service.TAX_KINDS.get(group, {}).get("name", group),
                    "period": period,
                }
            )
        return out

    return {
        "year": year,
        "source": schedule["source"],
        "willCreateCount": len(will_create),
        "willWaiveCount": len(will_waive),
        "willRestoreCount": len(will_restore),
        "willCreate": _brief(will_create),
        "willWaive": _brief(will_waive),
        "willRestore": _brief(will_restore),
    }
