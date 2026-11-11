# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.
"""Evidence-limited, ephemeral health scan. No financial data leaves this process."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional

from core.money import money

DIMENSION_DEFS = [
    ("capital", "资金与公私独立性"), ("voucher", "成本票据与凭证链"),
    ("credit", "报表勾稽与信贷准入"), ("governance", "财税日常治理与申报"),
]
QUIZ_RULES = [
    ("Q-01", "个人垫资处理", "q1", "capital", {"C": (15, "个人垫资未规范报销")}),
    ("Q-02", "无票支出处理", "q2", "voucher", {"B": (20, "使用与业务不相关的票据"), "C": (16, "无票支出长期挂往来")}),
    ("Q-03", "公转私款项", "q3", "capital", {"B": (20, "借款跨年未还")}),
    ("Q-04", "研发费用辅助账", "q4", "governance", {"A": (10, "存在研发支出但未建立研发辅助账")}),
    ("Q-05", "利润报表了解情况", "q5", "credit", {"B": (10, "自述利润长期接近零或微亏"), "C": (10, "自述未查看报表")}),
    ("Q-06", "账表取得能力", "q6", "credit", {"B": (10, "自述无法提供完整账表"), "C": (10, "自述不清楚所需账表")}),
]
FOLLOWUP_OPTIONS = {
    "loanCounterparty": {"SHAREHOLDER", "EMPLOYEE", "OTHER", "UNKNOWN", "NA"},
    "loanOccurrence": {"YES", "NO", "UNKNOWN", "NA"},
    "loanRepayment": {"YES", "NO", "PARTIAL", "UNKNOWN", "NA"},
    "loanPurpose": {"BUSINESS", "PERSONAL", "OTHER", "UNKNOWN", "NA"},
    "rdActivities": {"YES", "NO", "UNKNOWN", "NA"},
    "rdAuxiliaryLedger": {"YES", "NO", "UNKNOWN", "NA"},
    "assetExists": {"YES", "NO", "UNKNOWN", "NA"},
    "assetPutIntoUse": {"YES", "NO", "UNKNOWN", "NA"},
    "advanceReceived": {"YES", "NO", "UNKNOWN", "NA"},
    "advancePerformance": {"YES", "NO", "PARTIAL", "UNKNOWN", "NA"},
}
R_DEFS = [
    ("R-01", "其他应收款核查", "capital", 8),
    ("R-02", "其他应付款核查", "capital", 10),
    ("R-03", "未分配利润核查", "credit", 8),
    ("R-04", "借贷试算平衡", "credit", 25),
    ("R-05", "研发辅助账核查", "governance", 8),
    ("R-06", "固定资产折旧核查", "voucher", 10),
    ("R-07", "库存现金核查", "capital", 8),
    ("R-08", "预收款履约核查", "credit", 10),
    ("R-09", "实收资本核查", "credit", 8),
    ("R-10", "存货贷方余额核查", "voucher", 12),
]


def _money(value: Any) -> Decimal:
    # No implicit zero, locale guessing, exponent notation, or silent rounding.
    if value is None or isinstance(value, bool):
        raise ValueError("余额必须是有效的两位小数金额")
    s = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:\d{1,15})(?:\.\d{1,2})?", s):
        raise ValueError("余额必须是有效的两位小数金额")
    try:
        amount = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError("余额必须是有效的两位小数金额") from exc
    if not amount.is_finite():
        raise ValueError("余额必须是有限金额")
    return money(amount)


def _normalize_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise ValueError("余额表必须包含 1 至 10000 行")
    normalized = []
    codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("余额表行格式无效")
        code = row.get("account_code", row.get("code", ""))
        name = row.get("account_name", row.get("name", ""))
        if not isinstance(code, str) or not isinstance(name, str) or len(code) > 100 or len(name) > 200:
            raise ValueError("科目编码或名称无效")
        code, name = code.strip(), name.strip()
        if not code and not name:
            raise ValueError("科目编码与名称不能同时为空")
        if code and code in codes:
            raise ValueError("科目编码重复，请先核对明细")
        if code:
            codes.add(code)
        def side(keys: tuple[str, ...]) -> Decimal:
            present = [key for key in keys if key in row]
            if len(present) != 1:
                raise ValueError("每行必须且只能提供一组借贷余额")
            return _money(row[present[0]])
        normalized.append({"code": code, "name": name,
                           "debit": side(("debit_balance", "debit", "opening_debit")),
                           "credit": side(("credit_balance", "credit", "opening_credit"))})
    numeric = sorted(c for c in codes if re.fullmatch(r"\d{4,}", c))
    if any(b.startswith(a) for a, b in zip(numeric, numeric[1:])):
        raise ValueError("余额表同时包含父级和明细科目，请先核对，避免重复计算")
    return normalized


def _answers(answers: Any) -> Dict[str, str]:
    if answers is None:
        return {}
    if not isinstance(answers, dict):
        raise ValueError("问答格式无效")
    result = {}
    options = {**{f"q{i}": {"A", "B", "C", "UNKNOWN", "NA"} for i in range(1, 7)}, **FOLLOWUP_OPTIONS}
    for key, value in answers.items():
        if key in options:
            if not isinstance(value, str) or value not in options[key]:
                raise ValueError(f"{key} 选项无效")
            result[key] = value
        elif key in ("companyType", "revenue"):
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError("画像字段无效")
            result[key] = value
        else:
            raise ValueError("存在未知问答字段")
    return result


def _check(code: str, name: str, dimension: str, status: str, observed: str, missing: List[str], fix: str, source: str, deduction: int = 0) -> Dict[str, Any]:
    return {"code": code, "name": name, "status": status, "observed": observed,
            "missing": missing, "fix": fix, "source": source, "dimension": dimension,
            "deduction": deduction if status == "RISK" else 0}


def evaluate_quiz_rules(answers: Dict[str, str]) -> Dict[str, Any]:
    answers = _answers(answers)
    checks = []
    for code, name, key, dimension, risks in QUIZ_RULES:
        value = answers.get(key, "UNKNOWN")
        if value == "UNKNOWN":
            check = _check(code, name, dimension, "UNKNOWN", "未提供答案", [key], "补充该问题的答案。", "问卷")
        elif value == "NA":
            check = _check(code, name, dimension, "UNKNOWN", "尚未说明不适用的依据", [key], "请补充情况以确认是否适用。", "问卷")
        elif key == "q4" and value == "C":
            check = _check(code, name, dimension, "NA", "自述无相关研发支出", [], "如实际存在研发支出，请重新确认。", "问卷")
        elif value in risks:
            deduction, observed = risks[value]
            check = _check(code, name, dimension, "RISK", observed, [], "结合原始资料核实并由专业人员判断。", "问卷", deduction)
        else:
            check = _check(code, name, dimension, "PASS", "该答案未触发本项线索", [], "保留相关资料备查。", "问卷")
        checks.append(check)
    return _result("quiz", checks, {"answeredQuestions": sum(answers.get(f"q{i}") not in (None, "UNKNOWN", "NA") for i in range(1, 7))})


def _result(mode: str, checks: List[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    flags = [{"code": c["code"], "name": c["name"], "severity": "HIGH" if c["deduction"] >= 15 else "MEDIUM",
              "deduction": c["deduction"], "desc": c["observed"], "fix": c["fix"]}
             for c in checks if c["status"] == "RISK" or c.get("hasClue")]
    return {"mode": mode, "checks": checks, "flags": flags, "metrics": metrics}


def _capital_accounts(rows: List[Dict[str, Any]], standard: Optional[str]) -> tuple[List[Dict[str, Any]], bool]:
    """Identify capital conservatively; 4001 also occurs in legacy mixed charts."""
    capital = []
    ambiguous = False
    for row in rows:
        code, name = row["code"], row["name"]
        capital_name = bool(re.fullmatch(r"(?:实收资本|股本)(?:\s*[-－—/／]\s*.+)?", name))
        production_name = bool(re.fullmatch(r"生产成本(?:\s*[-－—/／]\s*.+)?", name))
        root = code[:4] if re.fullmatch(r"\d{4,}", code) else ""
        if root == "4001" and production_name and standard != "ENTERPRISE":
            continue
        if root in ("3001", "4001"):
            if capital_name:
                capital.append(row)
            elif not name and ((root == "3001" and standard == "SMALL_ENTERPRISE")
                               or (root == "4001" and standard == "ENTERPRISE")):
                capital.append(row)
            else:
                ambiguous = True
        elif capital_name:
            if code:
                # Custom or conflicting codes cannot be reconciled from the name alone.
                ambiguous = True
            else:
                capital.append(row)
    return capital, ambiguous


def evaluate_balance_rules(rows: List[Dict[str, Any]], answers: Optional[Dict[str, str]] = None, *, complete: bool = False, company_type: Optional[str] = None, accounting_standard: Optional[str] = None) -> Dict[str, Any]:
    rows = _normalize_rows(rows)
    answers = _answers(answers)
    total_debit = sum((r["debit"] for r in rows), Decimal(0))
    total_credit = sum((r["credit"] for r in rows), Decimal(0))
    nonzero = any(r["debit"] != 0 or r["credit"] != 0 for r in rows)
    reliable = complete and nonzero
    def select(prefixes: tuple[str, ...], names: tuple[str, ...]) -> List[Dict[str, Any]]:
        return [r for r in rows if r["code"].startswith(prefixes) or any(n in r["name"] for n in names)]
    def net(items: List[Dict[str, Any]], side: str = "debit") -> Decimal:
        return sum(((r["debit"] - r["credit"]) if side == "debit" else (r["credit"] - r["debit"]) for r in items), Decimal(0))
    rec = select(("1221",), ("其他应收款", "备用金"))
    payable = select(("2241",), ("其他应付款",))
    # 4104/3104 may be a parent profit distribution account, not itself undistributed profit.
    retained = [r for r in rows if "未分配利润" in r["name"]]
    fixed = select(("1601",), ("固定资产",))
    depreciation = select(("1602",), ("累计折旧",))
    cash = select(("1001",), ("库存现金",))
    advance = select(("2203", "2205"), ("预收账款", "合同负债"))
    capital, capital_ambiguous = _capital_accounts(rows, accounting_standard)
    inventory = select(("1403", "1405", "1408"), ("原材料", "库存商品", "周转材料"))
    checks = []
    for code, name, dim, ded in R_DEFS:
        status, obs, missing, fix = "UNKNOWN", "资料不足，无法判断", [], "补充相关资料后核对。"
        has_clue = False
        if code == "R-04":
            diff = abs(total_debit - total_credit)
            obs = f"所提供科目借贷差额 {diff:.2f} 元"
            if reliable:
                status = "RISK" if diff != 0 else "PASS"
            else:
                missing = ["已确认完整且非全零的余额表"]
        elif code in ("R-01", "R-02", "R-07", "R-10"):
            items, amount, threshold = {
                "R-01": (rec, sum((max(Decimal(0), r["debit"] - r["credit"]) for r in rec), Decimal(0)), Decimal("30000")),
                "R-02": (payable, sum((max(Decimal(0), r["credit"] - r["debit"]) for r in payable), Decimal(0)), Decimal("30000")),
                "R-07": (cash, net(cash), Decimal("50000")),
                "R-10": (inventory, net(inventory, "credit"), Decimal("0")),
            }[code]
            if items:
                obs = f"相关科目净余额 {amount:.2f} 元；仅为核查线索"
                hit = amount > threshold
                if code == "R-01" and amount > Decimal("5000") and answers.get("loanCounterparty") in ("SHAREHOLDER", "UNKNOWN", "NA", None):
                    hit = True
                # Individual inventory accounts cannot be offset by other inventory accounts.
                if code == "R-10":
                    hit = any(r["credit"] - r["debit"] > 0 for r in items)
                    obs = "存在存货科目贷方净余额，需逐项核对" if hit else "所提供存货科目未见贷方净余额"
                if hit:
                    status = "RISK"
                    has_clue = True
                    if code == "R-01":
                        fields = ("loanCounterparty", "loanOccurrence", "loanRepayment", "loanPurpose")
                        if answers.get("loanOccurrence") == "NO":
                            fields = ("loanOccurrence",)
                        missing = [k for k in fields if answers.get(k, "UNKNOWN") in ("UNKNOWN", "NA")]
                        obs += "；往来性质、期间发生、偿还和用途需结合资料核实"
                        if missing:
                            status = "UNKNOWN"
                        else:
                            obs += "；自述：" + "、".join(f"{k}={answers[k]}" for k in fields)
                elif reliable:
                    status = "PASS"
                else:
                    missing = ["已确认完整的余额表"]
            elif reliable:
                status, obs = "PASS", "完整余额表中未见相关科目，不代表不存在相关业务"
            else:
                missing = ["相关科目或已确认完整的余额表"]
        elif code == "R-03":
            if retained:
                amount = net(retained, "credit")
                obs = f"未分配利润净余额 {amount:.2f} 元；单期接近零不等于人为调节"
                status = "RISK" if abs(amount) <= Decimal("500") else ("PASS" if reliable else "UNKNOWN")
                if status == "UNKNOWN": missing = ["已确认完整的余额表"]
            else:
                missing = ["明确标识的未分配利润明细"]
        elif code == "R-05":
            activity, ledger = answers.get("rdActivities", "UNKNOWN"), answers.get("rdAuxiliaryLedger", "UNKNOWN")
            if activity == "NO":
                status, obs = "NA", "自述无研发活动"
            elif activity == "YES" and ledger in ("YES", "NO"):
                status = "PASS" if ledger == "YES" else "RISK"
                obs = "自述存在研发活动，" + ("已建立研发辅助账" if ledger == "YES" else "未建立研发辅助账")
                fix = "核实研发活动、费用归集和辅助账；优惠适用须另行确认。"
            else:
                missing = ["rdActivities"] if activity in ("UNKNOWN", "NA") else ["rdAuxiliaryLedger"]
        elif code == "R-06":
            amt = net(fixed)
            if fixed and amt > Decimal("20000"):
                has_clue = True
                obs = f"固定资产净借方余额 {amt:.2f} 元"
                used = answers.get("assetPutIntoUse", "UNKNOWN")
                if answers.get("assetExists") == "NO":
                    used = "UNKNOWN"
                    obs += "；与自述不存在资产不一致，请核实"
                if used == "NO":
                    status, obs = "NA", obs + "；自述尚未投入使用"
                elif used != "YES":
                    missing = ["assetPutIntoUse"]
                elif not depreciation:
                    missing = ["累计折旧科目及固定资产明细"]
                elif net(depreciation, "credit") <= 0:
                    status, obs = "RISK", obs + "；已提供累计折旧科目净额不为正，需核实资产明细及折旧政策"
                elif reliable:
                    status = "PASS"
                else:
                    missing = ["已确认完整的余额表"]
            elif reliable:
                status, obs = "PASS", "未触发固定资产余额筛查阈值"
            else:
                missing = ["固定资产科目或已确认完整的余额表"]
        elif code == "R-08":
            amt = net(advance, "credit")
            if advance and amt > Decimal("50000"):
                has_clue = True
                obs = f"预收款或合同负债净贷方余额 {amt:.2f} 元"
                performance = answers.get("advancePerformance", "UNKNOWN")
                if answers.get("advanceReceived") == "NO":
                    performance = "UNKNOWN"
                    obs += "；与自述未收预收款不一致，请核实"
                if performance in ("YES", "PARTIAL"):
                    status, obs = "RISK", obs + "；自述存在已履约部分，需核对合同及收入确认"
                elif performance == "NO":
                    status, obs = ("PASS" if reliable else "UNKNOWN"), obs + "；自述尚未履约"
                    if not reliable: missing = ["已确认完整的余额表"]
                else:
                    missing = ["advancePerformance"]
            elif reliable:
                status, obs = "PASS", "未触发预收款余额筛查阈值"
            else:
                missing = ["预收款科目或已确认完整的余额表"]
        elif code == "R-09":
            if company_type in ("INDIVIDUAL", "SOLE_PROPRIETOR"):
                status, obs = "NA", "该主体不是公司，实收资本检查不适用"
            elif company_type not in ("OPC", "LTD"):
                missing = ["companyType"]
            elif capital_ambiguous:
                obs = "资本科目编码与名称或准则不足以可靠区分，需核对科目明细"
                missing = ["明确的实收资本或股本科目编码、名称及会计准则"]
            elif capital:
                amt = net(capital, "credit")
                obs = f"实收资本或股本净贷方余额 {amt:.2f} 元；须结合章程和出资记录"
                status = "RISK" if amt <= 0 else ("PASS" if reliable else "UNKNOWN")
                if status == "UNKNOWN": missing = ["已确认完整的余额表"]
            else:
                missing = ["实收资本科目及出资记录"]
        check = _check(code, name, dim, status, obs, missing, fix, "余额表及用户自述" if code in ("R-01", "R-05", "R-06", "R-08") else "用户提供的余额表", ded)
        if has_clue and status == "UNKNOWN":
            check["hasClue"] = True
        checks.append(check)
    return _result("balance", checks, {"totalDebit": str(total_debit), "totalCredit": str(total_credit), "rowCount": len(rows), "nonzero": nonzero})


def _valid_period(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2]|Q[1-4]))?", value))


def _profile_complete(profile: Any, mode: str) -> bool:
    if not isinstance(profile, dict):
        return False
    if profile.get("companyType") not in ("OPC", "LTD", "INDIVIDUAL", "SOLE_PROPRIETOR"):
        return False
    return (_valid_period(profile.get("period"))
            and profile.get("accountingStandard") in ("SMALL_ENTERPRISE", "ENTERPRISE", "OTHER")
            and profile.get("vatType") in ("GENERAL", "SMALL_SCALE"))


def _balance_complete(meta: Any, profile: Any) -> bool:
    if not isinstance(meta, dict) or not isinstance(profile, dict):
        return False
    return (meta.get("confirmed") is True and meta.get("complete") is True
            and _valid_period(meta.get("period")) and meta.get("period") == profile.get("period")
            and meta.get("balanceType") in ("ending", "期末", "END", "ENDING")
            and meta.get("currencyUnit") in ("元", "CNY", "RMB", "yuan", "Yuan"))


def run_health_scan(*, mode: str, rows: Optional[List[Dict[str, Any]]] = None,
                    answers: Optional[Dict[str, str]] = None, profile: Optional[Dict[str, Any]] = None,
                    balanceMeta: Optional[Dict[str, Any]] = None, client_ip: Optional[str] = None) -> Dict[str, Any]:
    del client_ip  # Never use or retain input data for logging or persistence.
    if mode not in ("quiz", "balance"):
        raise ValueError("不支持的体检模式")
    answers = _answers(answers)
    if profile is not None:
        if not isinstance(profile, dict) or set(profile) - {"companyType", "period", "accountingStandard", "vatType"}:
            raise ValueError("企业画像格式无效")
        for key, allowed in {
            "companyType": {"OPC", "LTD", "INDIVIDUAL", "SOLE_PROPRIETOR", "UNKNOWN"},
            "accountingStandard": {"SMALL_ENTERPRISE", "ENTERPRISE", "OTHER", "UNKNOWN"},
            "vatType": {"GENERAL", "SMALL_SCALE", "UNKNOWN"},
        }.items():
            if key in profile and profile[key] not in allowed:
                raise ValueError("企业画像选项无效")
        if profile.get("period") not in (None, "", "UNKNOWN") and not _valid_period(profile["period"]):
            raise ValueError("企业画像期间无效")
    if balanceMeta is not None:
        if not isinstance(balanceMeta, dict) or set(balanceMeta) - {"period", "balanceType", "currencyUnit", "confirmed", "complete"}:
            raise ValueError("余额表元信息格式无效")
        if any(key in balanceMeta and not isinstance(balanceMeta[key], bool) for key in ("confirmed", "complete")):
            raise ValueError("余额表确认及完整标志必须为布尔值")
        if balanceMeta.get("period") not in (None, "", "UNKNOWN") and not _valid_period(balanceMeta["period"]):
            raise ValueError("余额表期间无效")
        if any(key in balanceMeta and not isinstance(balanceMeta[key], str) for key in ("balanceType", "currencyUnit")):
            raise ValueError("余额表类型或币种单位无效")
    profile_ok = _profile_complete(profile, mode)
    meta_ok = _balance_complete(balanceMeta, profile) if mode == "balance" else True
    result = (evaluate_quiz_rules(answers) if mode == "quiz" else
              evaluate_balance_rules(rows, answers, complete=profile_ok and meta_ok,
                                     company_type=profile.get("companyType") if profile else None,
                                     accounting_standard=profile.get("accountingStandard") if profile else None))
    checks, flags = result["checks"], result["flags"]
    if mode == "balance" and (not profile_ok or not meta_ok or not result["metrics"]["nonzero"]):
        for check in checks:
            if check["status"] == "PASS":
                check.update(status="UNKNOWN", deduction=0)
                check["missing"].append("已确认完整、期间一致且非全零的期末余额表及企业画像")
    applicable = sum(c["status"] != "NA" for c in checks)
    completed = sum(c["status"] in ("PASS", "RISK") for c in checks)
    percent = round(completed * 100 / applicable, 2) if applicable else None
    core_ok = profile_ok and meta_ok and (mode != "balance" or result["metrics"]["nonzero"])
    can_score = core_ok and applicable > 0 and completed * 5 >= applicable * 4
    deduction = sum(c["deduction"] for c in checks)
    dimensions = []
    empty_dimensions = [name for key, name in DIMENSION_DEFS if not any(c["dimension"] == key and c["status"] != "NA" for c in checks)]
    only_inapplicable_blocks_scoring = bool(empty_dimensions) and can_score and all(c["status"] not in ("UNKNOWN", "ERROR") for c in checks)
    for key, name in DIMENSION_DEFS:
        group = [c for c in checks if c["dimension"] == key and c["status"] != "NA"]
        dim_deduction = sum(c["deduction"] for c in group)
        if not can_score or not group or any(c["status"] in ("UNKNOWN", "ERROR") for c in group):
            dimensions.append({"key": key, "name": name, "score": None, "status": "UNKNOWN", "statusLabel": "未充分评估", "detail": "资料不足或本维度无适用检查，不代表安全。"})
        else:
            dim_score = max(0, 100 - dim_deduction)
            status = "CRITICAL" if dim_score < 50 else "WARNING" if dim_score < 85 else "HEALTHY"
            dimensions.append({"key": key, "name": name, "score": dim_score, "status": status,
                               "statusLabel": {"CRITICAL": "待重点核查", "WARNING": "待核查", "HEALTHY": "本范围线索较少"}[status],
                               "detail": "仅反映已提供资料及本次筛查范围，不构成合规结论。"})
    can_score = can_score and all(d["score"] is not None for d in dimensions)
    if not can_score:
        # Partial dimensions must not appear as independently certified scores.
        for d in dimensions:
            detail = ("该维度无适用检查，不代表安全。" if d["name"] in empty_dimensions
                      else "存在无适用检查的维度，整体暂不评分，不代表安全。" if only_inapplicable_blocks_scoring
                      else "资料不足或存在未评估维度，不代表安全。")
            d.update(score=None, status="UNKNOWN", statusLabel="未充分评估", detail=detail)
    score = sum(d["score"] for d in dimensions) / len(dimensions) if can_score else None
    if score is None:
        level = "UNKNOWN"
        label = "维度无适用检查，暂不评分" if only_inapplicable_blocks_scoring else "资料不足，暂不评分"
    elif score >= 85:
        level, label = "HEALTHY", "筛查线索较少"
    elif score >= 65:
        level, label = "MEDIUM_RISK", "存在待核查线索"
    else:
        level, label = "HIGH_RISK", "较多线索待核查"
    reason = []
    if not profile_ok: reason.append("企业画像不完整")
    if not meta_ok: reason.append("余额表元信息未确认、非期末人民币元或期间不一致")
    if mode == "balance" and not result["metrics"]["nonzero"]: reason.append("余额表全部为零，无法确认有效性")
    if not applicable or completed * 5 < applicable * 4: reason.append("适用检查覆盖率不足80%")
    if empty_dimensions: reason.append("以下维度无适用检查：" + "、".join(empty_dimensions))
    if not can_score: reason.append("评分还要求四个维度均有适用且完成的检查")
    view_details = "仅提供资料范围内的核查线索，不能推断税额、违法事实或银行授信结果。"
    tax_view = {"ruleId": "RULE-TAX-EVIDENCE-V1", "riskLevel": "unknown", "details": view_details, "checkChain": ""}
    bank_view = {"ruleId": "RULE-CREDIT-EVIDENCE-V1", "creditLevel": "unknown", "details": view_details, "checkChain": ""}
    return {
        "totalScore": score, "healthLevel": level, "levelLabel": label,
        "oneSentenceVerdict": ("试运行分数，权重未经专业验证；仅为筛查线索，不构成合规或信用评级。" if can_score
                               else "存在无适用检查的维度，暂不评分；已发现线索仍需核实。" if only_inapplicable_blocks_scoring
                               else "资料不足，暂不评分；已发现线索仍需核实。"),
        "dimensions": dimensions, "flags": flags, "checks": checks,
        "coverage": {"completed": completed, "applicable": applicable, "percent": percent},
        "scoreBreakdown": {"formula": "各维度=max(0,100-该维扣分之和)；总分=四维等权平均", "notice": "试运行权重未经专业验证，不构成合规或信用评级；应逐项核查线索。", "dimensionBase": 100,
                           "weights": {key: 0.25 for key, _ in DIMENSION_DEFS},
                           "deductions": [{"code": c["code"], "dimension": c["dimension"], "deduction": c["deduction"]} for c in checks if c["deduction"]],
                           "totalDeduction": deduction, "scored": can_score, "reason": reason},
        "taxBureauView": tax_view, "bankFunderView": bank_view,
        "views": {"taxBureauView": tax_view, "bankFunderView": bank_view},
        "urgentAction": flags[0]["fix"] if flags else "补充缺失资料并核对原始凭证。",
        "recommendedFix": "结合合同、凭证和实际业务，由专业人员逐项核实。",
        "meta": {"mode": mode, "ruleCount": len(flags), "ephemeral": True, "baselineVersion": "EVIDENCE-V1", "analysisSource": "deterministic", "source": "backend"},
    }
