# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.
"""3 分钟财税健康体检规则引擎与可选 AI 商业化润色（免存即焚）."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Dict, List, Optional

from config import LLM_API_KEY
from core.money import money

DIMENSION_DEFS = [
    ("capital", "公私资金安全"),
    ("voucher", "票据与成本合规"),
    ("credit", "报表与融资信用"),
    ("governance", "优惠与账务治理"),
]

COMPANY_TYPE_LABELS = {
    "OPC": "一人有限责任公司",
    "LTD": "多股东有限责任公司",
    "INDIVIDUAL": "个体工商户",
    "SOLE_PROPRIETOR": "个人独资企业",
    "UNKNOWN": "小微企业",
}

QUIZ_RULES: List[Dict[str, Any]] = [
    {
        "code": "Q-01",
        "name": "公转私与往来挂账",
        "key": "q1",
        "dimension": "capital",
        "route": "/mine/legacy-import",
        "cta": "规范公私往来账",
        "legalBasis": "《个人所得税法》及股东借款跨年未还视同分红口径（20% 个税）",
        "cases": {
            "B": (6, "MEDIUM", "公转私先借后平：跨年节点容易踩线",
                  "有时备注“借款/备用金”转个人卡，虽年底前会平账，但若跨年未清理完极易触发个税核查。",
                  "公户转个人卡优先走工资/报销/分红流程，避免跨年挂在“其他应收款”。"),
            "C": (22, "CRITICAL", "公转私长期挂账：小心 20% 个人所得税红线",
                  "公户资金转个人卡常备注“借款/往来款”且跨年未还，按税法口径可能被认定为变相分红，面临 20% 个税及滞纳金风险。",
                  "立即梳理历年挂账往来，通过合规费用报销、退回公户或依法分红完税清理平账。"),
        },
    },
    {
        "code": "Q-02",
        "name": "私卡垫付报销",
        "key": "q2",
        "dimension": "capital",
        "route": "/login",
        "cta": "开启垫资报销留痕",
        "legalBasis": "《企业所得税税前扣除凭证管理办法》及股东垫资偿还证据链",
        "cases": {
            "B": (8, "MEDIUM", "私卡垫付年底凑票：报销证据链薄弱",
                  "个人微信/支付宝垫付后年底集中凑票，资金流与发票流时间错位，核查时很难自证业务真实性。",
                  "垫付发生时同步上传小票/支付截图生成报销单，做到钱、票、事一一对应。"),
            "C": (16, "HIGH", "老板长期私卡垫资：钱白垫了还难合法转回",
                  "个人垫付公司开销没入账或长期挂账，会导致账面成本缺失、利润虚高多交税，日后公户有钱也难合规转回个人卡。",
                  "建立“个人垫资→待报销→公户合规回款”流水台账，把老板垫的每一分钱记清还清。"),
        },
    },
    {
        "code": "Q-03",
        "name": "无票支出与成本票",
        "key": "q3",
        "dimension": "voucher",
        "route": "/login",
        "cta": "规范无票与外包入账",
        "legalBasis": "《发票管理办法》及税前扣除合规性要求",
        "cases": {
            "B": (15, "HIGH", "无票支出挂账或漏记：糊涂账且白多交税",
                  "没有发票的支出直接挂往来不平或干脆不入账，要么往来滚成黑洞，要么成本少记导致企业所得税白白多交。",
                  "个人小额零星支出可按规定凭收款凭证/付款流水入账，大额个人劳务走税务代开或合规代扣。"),
            "C": (24, "CRITICAL", "用无关发票顶替成本：金税四期高危稽查雷区",
                  "找加油票、餐饮票等与真实业务无关的票据冲抵无票支出或外包费，极易被金税系统比对出费用结构异常，引发稽查与补税罚款。",
                  "立即停用无关票据冲账，改为自然人代开、灵活用工或规范代扣代缴渠道。"),
        },
    },
    {
        "code": "Q-04",
        "name": "账表透明与利润真实性",
        "key": "q4",
        "dimension": "credit",
        "route": "/mine/legacy-import",
        "cta": "一键导入重构干净账本",
        "legalBasis": "金税四期长期微利微亏预警模型 & 银行授信尽调三表要求",
        "cases": {
            "B": (14, "HIGH", "盲盒代账：只收报税截图，账本实情未知",
                  "平时从未看过自己的利润表和科目余额表，外包会计“只报税不理账”的隐患通常在注销、融资或抽查时集中爆发。",
                  "要求导出近期科目余额表与资产负债表核验，或迁移至全透明的老板直管账本。"),
            "C": (20, "CRITICAL", "长期零利润/微亏或无三表：税局盯上 + 银行拒贷",
                  "报表人为做成微亏/零申报，会触发税局“长期微利微亏”异常预警；同时申请企业经营贷时银行初筛会直接拒贷。",
                  "还原真实营收与成本结构，按小企业会计准则沉淀连续、可信的财务三张表。"),
        },
    },
    {
        "code": "Q-05",
        "name": "开票回款与往来对账",
        "key": "q5",
        "dimension": "voucher",
        "route": "/login",
        "cta": "开启银票自动勾稽",
        "legalBasis": "《增值税暂行条例》纳税义务发生时间与往来账龄管理",
        "cases": {
            "B": (10, "MEDIUM", "靠脑子记账：开票、回款与流水未月月核对",
                  "平时只看银行卡余额，容易出现已开票未回款提前垫税、漏开发票产生滞纳金或客户欠款遗忘。",
                  "每月做一次银行流水与发票、应收款的逐笔核对，及时预警逾期应收。"),
            "C": (16, "HIGH", "陈年应收应付一团乱：营运资金失真",
                  "账面往来长期对不上，无法判断客户到底欠多少、自己欠供应商多少，合伙分红或税务核查时根本说不清。",
                  "清理核销历史死账与重复挂账，重建客商往来明细台账。"),
        },
    },
    {
        "code": "Q-06",
        "name": "研发与政策红利利用",
        "key": "q6",
        "dimension": "governance",
        "route": "/login",
        "cta": "开启研发辅助账归集",
        "legalBasis": "科技型中小企业/小微企业研发费用 100% 加计扣除政策",
        "cases": {
            "B": (12, "MEDIUM", "有研发投入未建辅助账：白白多交冤枉税",
                  "软件开发、技术外包或算力等研发支出直接混进“管理费用”，缺少研发辅助账，导致无法享受加计扣除税收优惠。",
                  "为研发项目建立独立辅助账归集人员、外包与云资源支出，合规释放减税红利。"),
        },
    },
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
    ("R-01", "公私往来与股东借款", "capital", 16),
    ("R-02", "个人垫资与大额挂账", "capital", 10),
    ("R-03", "未分配利润异常做平", "credit", 10),
    ("R-04", "账本借贷试算平衡", "credit", 25),
    ("R-05", "研发费用辅助账归集", "governance", 10),
    ("R-06", "固定资产折旧计提", "voucher", 10),
    ("R-07", "库存现金过高风险", "capital", 10),
    ("R-08", "预收款未及时确认收入", "credit", 12),
    ("R-09", "实收资本实缴体现", "credit", 8),
    ("R-10", "存货负数（负库存）异常", "voucher", 14),
]


def _money(value: Any) -> Decimal:
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

        normalized.append({
            "code": code,
            "name": name,
            "debit": side(("debit_balance", "debit", "opening_debit")),
            "credit": side(("credit_balance", "credit", "opening_credit")),
        })
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


def _fmt_yuan(amount: Decimal) -> str:
    return f"¥{amount:,.2f}"


def _make_check(
    code: str, name: str, dimension: str, status: str, observed: str,
    missing: List[str], fix: str, source: str, deduction: int = 0,
    *, severity: str = "MEDIUM", pain_title: str = "", amount_text: str = "",
    exposure: str = "", legal_basis: str = "", cta: str = "立即处理", route: str = "/login",
) -> Dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "status": status,
        "observed": observed,
        "missing": missing,
        "fix": fix,
        "source": source,
        "dimension": dimension,
        "deduction": deduction if status == "RISK" else 0,
        "_meta": {
            "severity": severity,
            "painTitle": pain_title or name,
            "amount": amount_text,
            "estimatedExposure": exposure,
            "legalBasis": legal_basis,
            "ctaLabel": cta,
            "ctaRoute": route,
        },
    }


def evaluate_quiz_rules(answers: Dict[str, str]) -> Dict[str, Any]:
    answers = _answers(answers)
    checks = []
    for rule in QUIZ_RULES:
        key, code, name, dim = rule["key"], rule["code"], rule["name"], rule["dimension"]
        val = answers.get(key, "UNKNOWN")
        if val in ("UNKNOWN", None):
            c = _make_check(code, name, dim, "UNKNOWN", "未提供该项回答", [key], "补充该项信息后可进一步判断。", "经营快检问卷")
        elif val == "NA" or (key == "q6" and val == "C"):
            c = _make_check(code, name, dim, "NA", "当前业务不涉及此项", [], "如后续新增此类业务再行关注。", "经营快检问卷")
        elif val in rule["cases"]:
            ded, sev, pain_title, desc, fix = rule["cases"][val]
            c = _make_check(
                code, name, dim, "RISK", desc, [], fix, "经营快检问卷", ded,
                severity=sev, pain_title=pain_title, legal_basis=rule["legalBasis"],
                cta=rule["cta"], route=rule["route"],
            )
        else:
            c = _make_check(code, name, dim, "PASS", "做法规范，未触发风险信号", [], "继续保持当前账务处理习惯。", "经营快检问卷")
        checks.append(c)
    answered = sum(answers.get(f"q{i}") not in (None, "UNKNOWN", "NA") for i in range(1, 7))
    return {"mode": "quiz", "checks": checks, "metrics": {"answeredQuestions": answered}}


def _capital_accounts(rows: List[Dict[str, Any]], standard: Optional[str]) -> tuple[List[Dict[str, Any]], bool]:
    capital, ambiguous = [], False
    for row in rows:
        code, name = row["code"], row["name"]
        capital_name = bool(re.fullmatch(r"(?:实收资本|股本)(?:\s*[-－—/／]\s*.+)?", name))
        production_name = bool(re.fullmatch(r"生产成本(?:\s*[-－—/／]\s*.+)?", name))
        root = code[:4] if re.fullmatch(r"\d{4,}", code) else ""
        if root == "4001" and production_name and standard != "ENTERPRISE":
            continue
        if root in ("3001", "4001"):
            if capital_name or (not name and (
                (root == "3001" and standard in ("SMALL_ENTERPRISE", None, "UNKNOWN"))
                or (root == "4001" and standard == "ENTERPRISE")
            )):
                capital.append(row)
            else:
                ambiguous = True
        elif capital_name:
            if code:
                ambiguous = True
            else:
                capital.append(row)
    return capital, ambiguous


def evaluate_balance_rules(
    rows: List[Dict[str, Any]],
    answers: Optional[Dict[str, str]] = None,
    *,
    company_type: Optional[str] = None,
    accounting_standard: Optional[str] = None,
) -> Dict[str, Any]:
    rows = _normalize_rows(rows)
    answers = _answers(answers)
    total_debit = sum((r["debit"] for r in rows), Decimal(0))
    total_credit = sum((r["credit"] for r in rows), Decimal(0))
    nonzero = any(r["debit"] != 0 or r["credit"] != 0 for r in rows)

    def select(prefixes: tuple[str, ...], names: tuple[str, ...]) -> List[Dict[str, Any]]:
        return [r for r in rows if r["code"].startswith(prefixes) or any(n in r["name"] for n in names)]

    def net(items: List[Dict[str, Any]], side: str = "debit") -> Decimal:
        return sum(((r["debit"] - r["credit"]) if side == "debit" else (r["credit"] - r["debit"]) for r in items), Decimal(0))

    rec = select(("1221",), ("其他应收款", "备用金"))
    payable = select(("2241",), ("其他应付款",))
    retained = [r for r in rows if "未分配利润" in r["name"]]
    fixed = select(("1601",), ("固定资产",))
    depreciation = select(("1602",), ("累计折旧",))
    cash = select(("1001",), ("库存现金",))
    advance = select(("2203", "2205"), ("预收账款", "合同负债"))
    capital, capital_ambiguous = _capital_accounts(rows, accounting_standard)
    inventory = select(("1403", "1405", "1408"), ("原材料", "库存商品", "周转材料"))

    checks: List[Dict[str, Any]] = []

    # R-04: 借贷试算平衡
    diff = abs(total_debit - total_credit)
    if not nonzero:
        checks.append(_make_check("R-04", "账本借贷试算平衡", "credit", "UNKNOWN", "余额表金额全为零，无法核验平衡性", ["非零余额表"], "请导出含实际余额的科目余额表。", "科目余额表"))
    elif diff > 0:
        checks.append(_make_check(
            "R-04", "账本借贷试算平衡", "credit", "RISK",
            f"借贷总额相差 {_fmt_yuan(diff)}，账本底层未平账，银行授信和财税审计会直接质疑账套可靠性。",
            [], "核对漏记分录或父子科目重复合计问题，在梭罗记账中重构平衡账套。", "科目余额表", 25,
            severity="CRITICAL", pain_title="账本借贷不平：一眼假账硬伤",
            amount_text=_fmt_yuan(diff), legal_basis="《会计基础工作规范》有借必有贷、借贷必相等原则",
            cta="一键导入重整平账", route="/mine/legacy-import",
        ))
    else:
        checks.append(_make_check("R-04", "账本借贷试算平衡", "credit", "PASS", f"借贷总额均平衡（{_fmt_yuan(total_debit)}）", [], "继续保持记账平衡。", "科目余额表"))

    # R-01: 其他应收款（公转私/股东借款）
    rec_amt = sum((max(Decimal(0), r["debit"] - r["credit"]) for r in rec), Decimal(0))
    counterparty = answers.get("loanCounterparty", "UNKNOWN")
    repayment = answers.get("loanRepayment", "UNKNOWN")
    purpose = answers.get("loanPurpose", "UNKNOWN")
    r01_hit = nonzero and (
        rec_amt > Decimal("30000")
        or (rec_amt > Decimal("5000") and counterparty in ("SHAREHOLDER", "UNKNOWN", "NA"))
        or (rec_amt > Decimal("5000") and repayment in ("NO", "PARTIAL"))
    )
    if not nonzero:
        checks.append(_make_check("R-01", "公私往来与股东借款", "capital", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif r01_hit and not (counterparty == "EMPLOYEE" and repayment == "YES" and rec_amt <= Decimal("30000")):
        tax_est = money(rec_amt * Decimal("0.20"))
        is_shareholder_risk = counterparty == "SHAREHOLDER" or purpose == "PERSONAL" or rec_amt > Decimal("30000")
        sev = "CRITICAL" if (counterparty == "SHAREHOLDER" or rec_amt >= Decimal("50000")) else "HIGH"
        exposure = f"若属股东借款跨年未还，按 20% 股息红利个税口径测算潜在敞口约 {_fmt_yuan(tax_est)}" if is_shareholder_risk else ""
        checks.append(_make_check(
            "R-01", "公私往来与股东借款", "capital", "RISK",
            f"「其他应收款/备用金」期末借方挂账 {_fmt_yuan(rec_amt)}。若涉及股东或老板长期借款未还，极易被税局认定为变相分红。",
            [], "逐笔核对欠款对象与用途，通过合规费用报销冲销或转回公户清理挂账。", "科目余额表及补充说明", 16,
            severity=sev, pain_title="其他应收款大额挂账：警惕 20% 股东借款个税雷",
            amount_text=_fmt_yuan(rec_amt), exposure=exposure,
            legal_basis="《个人所得税法》及财税〔2003〕158号（股东借款纳税年度终了未归还视同分红）",
            cta="立即清理往来挂账", route="/mine/legacy-import",
        ))
    else:
        checks.append(_make_check("R-01", "公私往来与股东借款", "capital", "PASS", f"其他应收款余额 {_fmt_yuan(rec_amt)}，处于正常经营往来区间", [], "保持备用金定期报销冲平。", "科目余额表"))

    # R-02: 其他应付款（老板私卡垫资/外部拆借）
    pay_amt = sum((max(Decimal(0), r["credit"] - r["debit"]) for r in payable), Decimal(0))
    if not nonzero:
        checks.append(_make_check("R-02", "个人垫资与大额挂账", "capital", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif pay_amt > Decimal("30000"):
        checks.append(_make_check(
            "R-02", "个人垫资与大额挂账", "capital", "RISK",
            f"「其他应付款」挂账 {_fmt_yuan(pay_amt)}，通常是老板长期私卡垫资或个人拆借未规范入账，日后公转私还款若缺凭证容易被税局追问资金来源。",
            [], "补齐对应垫付单据与流水证明，后续在账套内规范走垫资还款流程。", "科目余额表", 10,
            severity="HIGH" if pay_amt >= Decimal("100000") else "MEDIUM",
            pain_title="大额其他应付款：公私资金混同、垫资凭证风险",
            amount_text=_fmt_yuan(pay_amt), legal_basis="资金流水与关联方往来合规性核查要求",
            cta="导入梳理垫资明细", route="/mine/legacy-import",
        ))
    else:
        checks.append(_make_check("R-02", "个人垫资与大额挂账", "capital", "PASS", f"其他应付款余额 {_fmt_yuan(pay_amt)}，未见大额异常挂账", [], "注意留存个人垫付凭证。", "科目余额表"))

    # R-03: 未分配利润异常做平
    if nonzero and retained:
        ret_amt = net(retained, "credit")
        if abs(ret_amt) <= Decimal("500"):
            checks.append(_make_check(
                "R-03", "未分配利润异常做平", "credit", "RISK",
                f"「未分配利润」净额仅 {_fmt_yuan(ret_amt)}，贴近零或微亏状态。长期如此易触发税务微利预警，且银行经营贷初筛易因无盈利沉淀被拒。",
                [], "如实核算经营利润并规范成本发票，让财务报表恢复真实的融资信用背书。", "科目余额表", 10,
                severity="MEDIUM", pain_title="未分配利润贴近零：银行授信拒贷 + 税务预警信号",
                amount_text=_fmt_yuan(ret_amt), legal_basis="金税四期长期微利微亏预警模型 & 银行尽调盈利要求",
                cta="开启透明盈利核算", route="/login",
            ))
        else:
            checks.append(_make_check("R-03", "未分配利润异常做平", "credit", "PASS", f"未分配利润余额 {_fmt_yuan(ret_amt)}，未见刻意做平至零痕迹", [], "定期关注真实利润留存。", "科目余额表"))
    elif nonzero:
        checks.append(_make_check("R-03", "未分配利润异常做平", "credit", "NA", "未匹配到单独列示的未分配利润科目", [], "核对期末利润结转情况。", "科目余额表"))
    else:
        checks.append(_make_check("R-03", "未分配利润异常做平", "credit", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))

    # R-07: 库存现金过高（白条抵库信号）
    cash_amt = net(cash)
    if not nonzero:
        checks.append(_make_check("R-07", "库存现金过高风险", "capital", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif cash_amt > Decimal("50000"):
        checks.append(_make_check(
            "R-07", "库存现金过高风险", "capital", "RISK",
            f"账面「库存现金」高达 {_fmt_yuan(cash_amt)}。实际很少有小微企业会在保险柜放这么多现金，多半是“无票支出/老板支用未平账”形成的假现金。",
            [], "盘点真实库存现金，对已支出未入账的“白条”补充合规凭据或清理往来。", "科目余额表", 10,
            severity="HIGH", pain_title="账面现金虚高（白条抵库信号）：疑似漏记支出或股东支用",
            amount_text=_fmt_yuan(cash_amt), legal_basis="现金盘点与账实相符基本规范",
            cta="盘点清理现金账", route="/mine/legacy-import",
        ))
    else:
        checks.append(_make_check("R-07", "库存现金过高风险", "capital", "PASS", f"库存现金余额 {_fmt_yuan(cash_amt)}，在合理区间内", [], "保持现金账实一致。", "科目余额表"))

    # R-10: 存货负余额
    neg_inv = [r for r in inventory if r["credit"] - r["debit"] > 0]
    if not nonzero:
        checks.append(_make_check("R-10", "存货负数（负库存）异常", "voucher", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif neg_inv:
        neg_amt = sum((r["credit"] - r["debit"] for r in neg_inv), Decimal(0))
        checks.append(_make_check(
            "R-10", "存货负数（负库存）异常", "voucher", "RISK",
            f"存货类科目出现 {_fmt_yuan(neg_amt)} 贷方余额（俗称“负库存”），通常是商品已开票销售但采购发票或入库单未入账，税务比对极易标红。",
            [], "补齐对应采购入库单与进项发票，防止账实倒挂引发税务异常。", "科目余额表", 14,
            severity="HIGH", pain_title="账面出现“负库存”：进销项倒挂高敏风险",
            amount_text=_fmt_yuan(neg_amt), legal_basis="增值税进销存比对与成本结转规范",
            cta="核对进销存账务", route="/mine/legacy-import",
        ))
    else:
        checks.append(_make_check("R-10", "存货负数（负库存）异常", "voucher", "PASS", "存货科目未见贷方负数余额", [], "继续保持先入库后出库结转。", "科目余额表"))

    # R-06: 固定资产未计提折旧
    fixed_amt = net(fixed)
    dep_amt = net(depreciation, "credit")
    used = answers.get("assetPutIntoUse", "UNKNOWN")
    if not nonzero:
        checks.append(_make_check("R-06", "固定资产折旧计提", "voucher", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif used == "NO":
        checks.append(_make_check("R-06", "固定资产折旧计提", "voucher", "NA", "固定资产尚未投入使用", [], "投用次月起开始计提折旧。", "科目余额表及补充说明"))
    elif fixed and fixed_amt > Decimal("20000") and (not depreciation or dep_amt <= 0):
        checks.append(_make_check(
            "R-06", "固定资产折旧计提", "voucher", "RISK",
            f"账面固定资产 {_fmt_yuan(fixed_amt)}，但累计折旧为 0。漏提折旧会导致成本费用少计，企业所得税白白多交。",
            [], "核实资产启用时间，在账套中开启自动按月计提折旧。", "科目余额表", 10,
            severity="MEDIUM", pain_title="大额固定资产零折旧：疑似漏提折旧多交所得税",
            amount_text=_fmt_yuan(fixed_amt), legal_basis="《企业所得税法实施条例》固定资产折旧税前扣除规定",
            cta="开启自动折旧", route="/login",
        ))
    else:
        checks.append(_make_check("R-06", "固定资产折旧计提", "voucher", "PASS", "固定资产与折旧计提状态正常", [], "按月持续计提折旧。", "科目余额表"))

    # R-08: 预收款未及时确认收入
    adv_amt = net(advance, "credit")
    perf = answers.get("advancePerformance", "UNKNOWN")
    if not nonzero:
        checks.append(_make_check("R-08", "预收款未及时确认收入", "credit", "UNKNOWN", "余额表全为零", ["非零余额表"], "补充非零余额表。", "科目余额表"))
    elif advance and adv_amt > Decimal("50000"):
        if perf in ("YES", "PARTIAL") or perf == "UNKNOWN":
            checks.append(_make_check(
                "R-08", "预收款未及时确认收入", "credit", "RISK",
                f"预收账款/合同负债挂账 {_fmt_yuan(adv_amt)}。若业务已交付或部分完成却一直不转收入，可能被认定为延迟确认收入、少计税款。",
                [], "对照合同交付进度，将已履约部分及时结转为营业收入并配比成本。", "科目余额表", 12,
                severity="HIGH" if perf in ("YES", "PARTIAL") else "MEDIUM",
                pain_title="大额预收款长期挂账：小心延迟确认收入涉税风险",
                amount_text=_fmt_yuan(adv_amt), legal_basis="《企业所得税法》及《小企业会计准则》收入确认时点规定",
                cta="梳理合同收入确认", route="/mine/legacy-import",
            ))
        else:
            checks.append(_make_check("R-08", "预收款未及时确认收入", "credit", "PASS", f"预收款 {_fmt_yuan(adv_amt)} 尚未履约，暂无需确认收入", [], "交付完成时及时结转收入。", "科目余额表及补充说明"))
    else:
        checks.append(_make_check("R-08", "预收款未及时确认收入", "credit", "PASS", "预收款余额处于常规水平", [], "按履约节点确认收入。", "科目余额表"))

    # R-09: 实收资本
    if company_type in ("INDIVIDUAL", "SOLE_PROPRIETOR"):
        checks.append(_make_check("R-09", "实收资本实缴体现", "credit", "NA", "个体户/个人独资企业不适用实收资本检查", [], "无需处理。", "科目余额表"))
    elif nonzero and capital and not capital_ambiguous:
        cap_amt = net(capital, "credit")
        if cap_amt <= 0:
            checks.append(_make_check(
                "R-09", "实收资本实缴体现", "credit", "RISK",
                "账面「实收资本/股本」余额为 0。注意新《公司法》5 年实缴期限要求，且零实收资本不利于银行和商业合作信用评估。",
                [], "核对公司章程实缴安排；如股东已打款入公户，检查是否误挂进了“其他应付款”。", "科目余额表", 8,
                severity="MEDIUM", pain_title="账面实收资本为零：影响五年实缴合规与授信评分",
                amount_text=_fmt_yuan(cap_amt), legal_basis="新《公司法》第四十七条五年实缴期限规定",
                cta="核对股东出资入账", route="/login",
            ))
        else:
            checks.append(_make_check("R-09", "实收资本实缴体现", "credit", "PASS", f"实收资本余额 {_fmt_yuan(cap_amt)}，账面已体现出资", [], "确保出资凭证与工商公示一致。", "科目余额表"))
    else:
        checks.append(_make_check("R-09", "实收资本实缴体现", "credit", "NA", "表内未明确区分实收资本明细", [], "可结合章程核对实缴情况。", "科目余额表"))

    # R-05: 研发费用辅助账（仅在有线索或用户答复时评估，否则标 NA 不阻断出分）
    rd_act = answers.get("rdActivities", "UNKNOWN")
    rd_ledger = answers.get("rdAuxiliaryLedger", "UNKNOWN")
    has_rd_row = bool(select((), ("研发支出", "研发费用")))
    if rd_act == "YES" and rd_ledger == "NO":
        checks.append(_make_check(
            "R-05", "研发费用辅助账归集", "governance", "RISK",
            "存在研发投入但未建研发辅助账，导致无法合规申报研发费用 100% 加计扣除优惠。",
            [], "按项目单独归集研发人工、外包与云算力支出，建立标准研发辅助账。", "科目余额表及补充说明", 10,
            severity="MEDIUM", pain_title="有研发投入无辅助账：白交企业所得税冤枉钱",
            legal_basis="科技型中小企业研发费用税前加计扣除管理办法",
            cta="建立研发辅助账", route="/login",
        ))
    elif rd_act == "YES" and rd_ledger == "YES":
        checks.append(_make_check("R-05", "研发费用辅助账归集", "governance", "PASS", "已建立研发费用辅助账", [], "持续留存研发立项与工时记录。", "科目余额表及补充说明"))
    elif has_rd_row:
        checks.append(_make_check("R-05", "研发费用辅助账归集", "governance", "PASS", "账内已单设研发费用科目归集", [], "核对是否同步留存辅助账备查资料。", "科目余额表"))
    else:
        checks.append(_make_check("R-05", "研发费用辅助账归集", "governance", "NA", "未提供研发活动信息，本项不涉及", [], "如有技术研发投入建议建辅助账节税。", "科目余额表"))

    return {
        "mode": "balance",
        "checks": checks,
        "metrics": {"totalDebit": str(total_debit), "totalCredit": str(total_credit), "rowCount": len(rows), "nonzero": nonzero},
    }


def _build_flags(checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    flags = []
    for c in checks:
        if c["status"] != "RISK":
            continue
        meta = c.get("_meta", {})
        flags.append({
            "code": c["code"],
            "name": c["name"],
            "painTitle": meta.get("painTitle") or c["name"],
            "severity": meta.get("severity") or ("HIGH" if c["deduction"] >= 15 else "MEDIUM"),
            "deduction": c["deduction"],
            "amount": meta.get("amount", ""),
            "estimatedExposure": meta.get("estimatedExposure", ""),
            "desc": c["observed"],
            "fix": c["fix"],
            "legalBasis": meta.get("legalBasis", ""),
            "ctaLabel": meta.get("ctaLabel", "立即处理"),
            "ctaRoute": meta.get("ctaRoute", "/login"),
        })
    flags.sort(key=lambda f: (sev_order.get(f["severity"], 9), -f["deduction"]))
    return flags


def _deterministic_perspectives(
    score: Optional[int], flags: List[Dict[str, Any]], mode: str, is_opening: bool,
) -> Dict[str, Any]:
    if score is None:
        return {
            "oneSentenceVerdict": "有效信息不足，暂时无法打出可靠健康分，请至少完成 3 题或上传非零余额表。",
            "taxBureauView": {"ruleId": "TAX-INSUFFICIENT", "riskLevel": "unknown", "details": "当前有效信息过少，暂无法评估涉税风险点。", "checkChain": ""},
            "bankFunderView": {"ruleId": "BANK-INSUFFICIENT", "creditLevel": "unknown", "details": "当前有效信息过少，暂无法评估融资尽调水平。", "checkChain": ""},
            "urgentAction": "补充回答关键问题或上传最近一期科目余额表。",
            "recommendedFix": "使用 90 秒快检或上传余额表获取针对性排雷建议。",
        }

    tax_codes = {"Q-01", "Q-03", "Q-06", "R-01", "R-03", "R-05", "R-06", "R-07", "R-08", "R-10"}
    bank_codes = {"Q-01", "Q-04", "Q-05", "R-01", "R-02", "R-03", "R-04", "R-09"}
    tax_hits = [f for f in flags if f["code"] in tax_codes]
    bank_hits = [f for f in flags if f["code"] in bank_codes]

    if not flags:
        verdict = "账务与风控习惯较规范，公私隔离与票据意识优于多数同规模企业，继续保持月度对平即可。"
    else:
        top = flags[0]
        verdict = f"检出 {len(flags)} 项重点隐患，首要关注「{top['painTitle']}」，建议尽快按指引清理避免积重难返。"
    if is_opening and mode == "balance":
        verdict = "（注：当前基于期初余额筛查）" + verdict

    if tax_hits:
        tax_level = "high" if any(f["severity"] in ("CRITICAL", "HIGH") for f in tax_hits) else "medium"
        tax_details = "重点命中" + "、".join(f"「{f['name']}」" for f in tax_hits[:3]) + "，建议核实原始单据与报税口径，防范稽查补税或错失税收优惠。"
    else:
        tax_level = "low"
        tax_details = "本次检查范围内未命中公转私挂账、找票顶账或负库存等高频涉税红线。"

    if bank_hits:
        bank_level = "low" if any(f["severity"] in ("CRITICAL", "HIGH") for f in bank_hits) else "medium"
        bank_details = "受" + "、".join(f"「{f['name']}」" for f in bank_hits[:3]) + "影响，若现阶段申请经营贷或机构尽调，报表可信度与通过率会受拖累。"
    else:
        bank_level = "high"
        bank_details = "试算、往来及利润表现处于可控区间，具备良好的财务信用基础。"

    urgent = flags[0]["fix"] if flags else "可直接在梭罗记账建立标准账本，持续保持自动化对账留痕。"
    return {
        "oneSentenceVerdict": verdict,
        "taxBureauView": {"ruleId": "TAX-PAIN-V2", "riskLevel": tax_level, "details": tax_details, "checkChain": "、".join(f["code"] for f in tax_hits)},
        "bankFunderView": {"ruleId": "BANK-PAIN-V2", "creditLevel": bank_level, "details": bank_details, "checkChain": "、".join(f["code"] for f in bank_hits)},
        "urgentAction": urgent,
        "recommendedFix": "使用梭罗记账「AI 一键洗账 / 规范记账」，逐项平账并沉淀可信财务三张表。",
    }


_SCAN_POLISH_PROMPT = """你是一位精通中国小微企业财税与银行风控的「首席 AI 财务官」。
请根据输入的【确定性体检结果（已脱敏，不含任何公司或个人隐私）】，用老板能一眼看懂的商业大白话润色报告结论，直戳经营痛点（如 20% 股东借款个税风险、找票顶账稽查风险、银行拒贷、白交冤枉税等）。

## 硬性约束
1. 严禁编造任何输入中不存在的金额、税额或法律结论；若输入中有 `estimatedExposure`，可自然引用其测算口径，不要夸大。
2. 严禁使用晦涩会计术语（如“借方”“贷方”“二级科目”），全部翻译为经营后果和可落地的自救动作。
3. 严格输出纯 JSON 对象，不要 Markdown 代码块，字段如下：
{
  "oneSentenceVerdict": "一句话毒舌又客观的总判词（不超过60字）",
  "taxBureauDetails": "税局稽查视角提示（1-2句话，不超过90字）",
  "bankFunderDetails": "银行/资方尽调视角提示（1-2句话，不超过90字）",
  "urgentAction": "现在最应该先做的第一步动作（不超过50字）",
  "flagNarratives": {
    "规则代号": {
      "painTitle": "不超过24字的痛点标题",
      "desc": "不超过80字的大白话原因与经营后果",
      "fix": "不超过60字的落地化解建议"
    }
  }
}"""


def _apply_llm_polish(
    *,
    mode: str,
    company_type: Optional[str],
    score: Optional[int],
    level: str,
    flags: List[Dict[str, Any]],
    perspectives: Dict[str, Any],
) -> str:
    """使用严格脱敏后的规则摘要调用 LLM 润色文案；失败自动回退到确定性文案。"""
    if not LLM_API_KEY or not flags or score is None:
        return "deterministic"

    from services.llm import safe_chat_text_json

    summary_input = {
        "mode": mode,
        "companyType": COMPANY_TYPE_LABELS.get(company_type or "UNKNOWN", "小微企业"),
        "totalScore": score,
        "healthLevel": level,
        "triggeredRisks": [
            {
                "code": f["code"],
                "painTitle": f["painTitle"],
                "severity": f["severity"],
                "amount": f["amount"],
                "estimatedExposure": f["estimatedExposure"],
                "factSummary": f["desc"],
            }
            for f in flags[:5]
        ],
    }
    polished = safe_chat_text_json(
        _SCAN_POLISH_PROMPT,
        json.dumps(summary_input, ensure_ascii=False),
        fallback={},
        scene="health_scan",
        max_retries=1,
        temperature=0.3,
        max_tokens=900,
    )
    if not isinstance(polished, dict) or not polished:
        return "deterministic-fallback"

    def pick_str(key: str, max_len: int) -> Optional[str]:
        val = polished.get(key)
        return val.strip()[:max_len] if isinstance(val, str) and val.strip() else None

    if v := pick_str("oneSentenceVerdict", 100):
        perspectives["oneSentenceVerdict"] = v
    if v := pick_str("taxBureauDetails", 180):
        perspectives["taxBureauView"]["details"] = v
    if v := pick_str("bankFunderDetails", 180):
        perspectives["bankFunderView"]["details"] = v
    if v := pick_str("urgentAction", 120):
        perspectives["urgentAction"] = v

    flag_map = polished.get("flagNarratives")
    if isinstance(flag_map, dict):
        valid_codes = {f["code"]: f for f in flags}
        for code, patch in flag_map.items():
            if code in valid_codes and isinstance(patch, dict):
                target = valid_codes[code]
                if isinstance(patch.get("painTitle"), str) and patch["painTitle"].strip():
                    target["painTitle"] = patch["painTitle"].strip()[:40]
                if isinstance(patch.get("desc"), str) and patch["desc"].strip():
                    target["desc"] = patch["desc"].strip()[:160]
                if isinstance(patch.get("fix"), str) and patch["fix"].strip():
                    target["fix"] = patch["fix"].strip()[:120]

    return "llm"


def _valid_period(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2]|Q[1-4]))?", value))


def run_health_scan(
    *,
    mode: str,
    rows: Optional[List[Dict[str, Any]]] = None,
    answers: Optional[Dict[str, str]] = None,
    profile: Optional[Dict[str, Any]] = None,
    balanceMeta: Optional[Dict[str, Any]] = None,
    use_ai: bool = False,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    del client_ip  # Ephemeral: never log or persist user inputs.
    if mode not in ("quiz", "balance"):
        raise ValueError("不支持的体检模式")
    answers = _answers(answers)

    if profile is not None:
        if not isinstance(profile, dict) or set(profile) - {"companyType", "period", "accountingStandard", "vatType"}:
            raise ValueError("企业画像格式无效")
        for key, allowed in {
            "companyType": {"OPC", "LTD", "INDIVIDUAL", "SOLE_PROPRIETOR", "UNKNOWN", ""},
            "accountingStandard": {"SMALL_ENTERPRISE", "ENTERPRISE", "OTHER", "UNKNOWN", ""},
            "vatType": {"GENERAL", "SMALL_SCALE", "UNKNOWN", ""},
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

    company_type = (profile or {}).get("companyType") or answers.get("companyType") or None
    standard = (profile or {}).get("accountingStandard") or None
    is_opening = bool(balanceMeta and balanceMeta.get("balanceType") == "opening")

    raw_result = (
        evaluate_quiz_rules(answers)
        if mode == "quiz"
        else evaluate_balance_rules(rows, answers, company_type=company_type, accounting_standard=standard)
    )
    checks = raw_result["checks"]
    flags = _build_flags(checks)

    applicable = sum(c["status"] != "NA" for c in checks)
    completed = sum(c["status"] in ("PASS", "RISK") for c in checks)
    percent = round(completed * 100 / applicable, 2) if applicable else None

    if mode == "quiz":
        can_score = applicable > 0 and (completed >= 3 or completed == applicable)
    else:
        can_score = bool(raw_result["metrics"].get("nonzero")) and completed >= 1

    # 按有效维度动态计算分数，不因某个维度「不涉及 (NA)」而导致整份报告拒出分。
    dimensions: List[Dict[str, Any]] = []
    scored_dims: List[int] = []
    for key, name in DIMENSION_DEFS:
        group = [c for c in checks if c["dimension"] == key and c["status"] in ("PASS", "RISK")]
        if not group:
            has_na = any(c["dimension"] == key and c["status"] == "NA" for c in checks)
            dimensions.append({
                "key": key,
                "name": name,
                "score": None,
                "status": "NA" if has_na else "UNKNOWN",
                "statusLabel": "本次不涉及" if has_na else "信息不足",
                "detail": "当前经营情况不涉及该维度，不计入总分扣减。" if has_na else "补充信息后可评估该维度。",
            })
            continue
        dim_ded = sum(c["deduction"] for c in group)
        dim_score = max(0, 100 - dim_ded)
        scored_dims.append(dim_score)
        dim_status = "CRITICAL" if dim_score < 50 else "WARNING" if dim_score < 85 else "HEALTHY"
        dim_label = {"CRITICAL": "存在高危雷区", "WARNING": "存在隐患待理", "HEALTHY": "状态良好"}[dim_status]
        risk_cnt = sum(1 for c in group if c["status"] == "RISK")
        dim_detail = f"检出 {risk_cnt} 项需要重点处理的隐患" if risk_cnt else "本维度暂未发现异常线索"
        dimensions.append({
            "key": key,
            "name": name,
            "score": dim_score,
            "status": dim_status,
            "statusLabel": dim_label,
            "detail": dim_detail,
        })

    if not can_score or not scored_dims:
        score = None
        level = "UNKNOWN"
        label = "余额全零，暂无法评估" if (mode == "balance" and not raw_result["metrics"].get("nonzero")) else "有效信息过少，暂不评分"
    else:
        score = round(sum(scored_dims) / len(scored_dims))
        if score >= 85:
            level, label = "HEALTHY", "账务较健康"
        elif score >= 65:
            level, label = "MEDIUM_RISK", "存在隐患待清理"
        else:
            level, label = "HIGH_RISK", "高危雷区待整改"

    perspectives = _deterministic_perspectives(score, flags, mode, is_opening)
    analysis_source = "deterministic"
    if use_ai:
        analysis_source = _apply_llm_polish(
            mode=mode,
            company_type=company_type,
            score=score,
            level=level,
            flags=flags,
            perspectives=perspectives,
        )

    # 剥离内部 _meta，保持 checks 输出简洁干净
    public_checks = [{k: v for k, v in c.items() if k != "_meta"} for c in checks]
    active_weight = round(1 / len(scored_dims), 4) if scored_dims else 0

    return {
        "totalScore": score,
        "healthLevel": level,
        "levelLabel": label,
        "oneSentenceVerdict": perspectives["oneSentenceVerdict"],
        "taxBureauView": perspectives["taxBureauView"],
        "bankFunderView": perspectives["bankFunderView"],
        "views": {
            "taxBureauView": perspectives["taxBureauView"],
            "bankFunderView": perspectives["bankFunderView"],
        },
        "dimensions": dimensions,
        "flags": flags,
        "checks": public_checks,
        "coverage": {"completed": completed, "applicable": applicable, "percent": percent},
        "scoreBreakdown": {
            "formula": "各适用维度=max(0,100-该维扣分之和)；总分=适用维度等权平均",
            "dimensionBase": 100,
            "weights": {d["key"]: (active_weight if d["score"] is not None else 0) for d in dimensions},
            "deductions": [{"code": c["code"], "dimension": c["dimension"], "deduction": c["deduction"]} for c in checks if c["deduction"]],
            "totalDeduction": sum(c["deduction"] for c in checks),
            "scored": score is not None,
        },
        "urgentAction": perspectives["urgentAction"],
        "recommendedFix": perspectives["recommendedFix"],
        "meta": {
            "mode": mode,
            "ruleCount": len(flags),
            "ephemeral": True,
            "baselineVersion": "PAIN-V2",
            "analysisSource": analysis_source,
            "source": "backend",
            "isOpeningBalance": is_opening,
        },
    }
