# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from pydantic import BaseModel as PydanticBaseModel, ConfigDict, Field
from typing import Optional, List, Literal


def d2(value) -> Decimal:
    """Round a numeric value to 2 decimal places, returning Decimal."""
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class BaseModel(PydanticBaseModel):
    # Pydantic v2 emits Decimal as JSON strings by default, preserving precision.
    model_config = ConfigDict()


class StrictRequestModel(BaseModel):
    """Public request contract: stale client-computed financial fields are rejected."""
    model_config = ConfigDict(extra="forbid")

# ── 核心记账 ──

class RecordTextRequest(BaseModel):
    text: str
    mode: Literal["voice", "text"] = "text"
    client_time: Optional[str] = None  # 手机端本地时间
    source: Optional[str] = "mobile"   # 来源标记


class SpeechTextResponse(BaseModel):
    """上传短音频后，通义 Paraformer 识别的纯文本结果。"""
    text: str


class CloudFileRequest(BaseModel):
    """小程序云存储临时 URL 请求体。"""
    fileUrl: str
    filename: Optional[str] = None
    contentType: Optional[str] = None


class RecordTextResponse(BaseModel):
    id: str
    direction: Literal["income", "expense", "owner_transfer"]
    amount: Decimal
    title: str
    category: str
    confidence: float
    explanation: str
    # V3 新增：会计科目
    accountCode: Optional[str] = None
    accountName: Optional[str] = None
    # V3 新增：税务
    vatRate: Optional[Decimal] = None
    vatAmount: Optional[Decimal] = None
    taxDeductible: Optional[bool] = None
    riskTags: Optional[List[str]] = None
    voucherHint: Optional[dict] = None
    # 追问
    askFollowUp: Optional[str] = None
    followUpOptions: Optional[List[str]] = None
    # V3.1 新增：支付方
    payer: Optional[Literal["company_account", "boss_personal"]] = "company_account"

class RecordReceiptResponse(BaseModel):
    id: str
    merchant: str
    amount: Decimal
    date: Optional[str] = None
    direction: Optional[str] = "expense"
    suggestedCategory: str
    confidence: float
    explanation: str
    rawText: Optional[str] = None
    # V3 新增：发票识别
    invoiceType: Optional[str] = None
    invoiceNumber: Optional[str] = None
    billingStatus: Optional[str] = None
    # V3 新增：会计科目
    accountCode: Optional[str] = None
    accountName: Optional[str] = None
    vatRate: Optional[Decimal] = None
    vatAmount: Optional[Decimal] = None
    taxDeductible: Optional[bool] = None
    riskTags: Optional[List[str]] = None
    # 追问
    askFollowUp: Optional[str] = None
    followUpOptions: Optional[List[str]] = None
    # V3.1 新增：支付方
    payer: Optional[Literal["company_account", "boss_personal"]] = "company_account"
    # V3.2 新增：购销方信息（用于前端抬头比对和开票/收票方向确认）
    buyerName: Optional[str] = None
    buyerTaxId: Optional[str] = None
    sellerName: Optional[str] = None
    sellerTaxId: Optional[str] = None

class BatchEntry(BaseModel):
    direction: Literal["income", "expense", "owner_transfer"]
    amount: Decimal
    title: str
    category: str
    accountCode: Optional[str] = None
    accountName: Optional[str] = None
    vatRate: Optional[Decimal] = None
    vatAmount: Optional[Decimal] = None
    taxDeductible: Optional[bool] = None
    riskTags: Optional[List[str]] = None
    payer: Optional[Literal["company_account", "boss_personal"]] = "company_account"

class RecordBatchRequest(BaseModel):
    text: str

class RecordBatchResponse(BaseModel):
    entries: List[BatchEntry]

class ConfirmRequest(BaseModel):
    id: Optional[str] = None
    direction: str
    amount: Decimal
    title: str
    category: str
    accountCode: Optional[str] = None
    accountName: Optional[str] = None
    billingStatus: Optional[str] = None
    invoiceType: Optional[str] = None
    invoiceNumber: Optional[str] = None
    buyerTaxId: Optional[str] = None
    sellerTaxId: Optional[str] = None
    merchant: Optional[str] = None
    occurDate: Optional[str] = None
    paymentDate: Optional[str] = None
    sourceType: Optional[str] = None
    dedupFingerprint: Optional[str] = None
    # 跨期补记：业务日期所属期间已关账时，前端展示「补记到 X 月」并征得同意后才带上。
    # 不带则后端维持 PERIOD_CLOSED，避免把凭证静默写进用户没指定的月份。
    allowBackfill: bool = False
    backfillReason: Optional[str] = None
    payer: Optional[str] = "company_account"
    companyId: Optional[str] = None
    company_id: Optional[str] = None
    # 辅助核算（P0 WP2）：只带 id，name 由服务端按主数据回填，不信客户端文案。
    # 挂到哪些科目由 core.chart_policy.auxiliary_kinds_for_account 决定。
    partner_id: Optional[str] = None
    partnerId: Optional[str] = None
    department_id: Optional[str] = None
    departmentId: Optional[str] = None
    # 数量金额式（P1 WP-D）：confirm 时由服务端挂到勾选了数量核算的科目行上；
    # 数量×单价与金额的一致性在 _normalize_voucher_lines 校验（容差 1 分）。
    quantity: Optional[Decimal] = None
    unitPrice: Optional[Decimal] = None
    unit: Optional[str] = None
    project_id: Optional[str] = None
    projectId: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdExpenseType: Optional[Literal[
        "personnel", "direct_input", "depreciation_amortization", "design", "testing", "outsourced", "other"
    ]] = None
    rdOriginalAmount: Optional[Decimal] = None
    rdAllocationMethod: Optional[Literal["direct", "timesheet", "usage", "area", "quantity", "other"]] = None
    rdAllocationBasis: Optional[str] = None
    rdNotes: Optional[str] = None

class AccrualCreateRequest(BaseModel):
    """月末暂估：票未到但业务已发生。借方必须是成本费用或存货类科目。"""
    amount: Decimal
    businessDate: Optional[str] = None
    vendor: str
    # accountCode 可省略：只给 category 时按记账同一套分类→科目映射推导
    accountCode: Optional[str] = None
    accountName: Optional[str] = ""
    category: Optional[str] = None
    expectedInvoiceDate: str
    basis: Optional[Literal["contract", "receipt", "estimate"]] = "estimate"
    notes: Optional[str] = ""
    dedupFingerprint: Optional[str] = None


class AccrualReverseRequest(BaseModel):
    reason: Optional[str] = None
    reversalDate: Optional[str] = None


class AccrualSettleRequest(BaseModel):
    """票到冲抵：冲回暂估 + 按发票金额正式入账（同一事务）。"""
    amount: Optional[Decimal] = None
    occurDate: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    invoiceNumber: Optional[str] = None
    reason: Optional[str] = None


class ConfirmResponse(BaseModel):
    id: str
    voucherStatus: str
    voucherRef: Optional[str] = None
    duplicateRisk: Optional[dict] = None
    entry: dict


class EntryUpdateRequest(BaseModel):
    title: Optional[str] = None
    amount: Optional[Decimal] = None
    direction: Optional[Literal["income", "expense", "owner_transfer"]] = None
    category: Optional[str] = None
    billingStatus: Optional[str] = None
    paymentStatus: Optional[Literal["paid", "unpaid"]] = None
    accountCode: Optional[str] = None
    accountName: Optional[str] = None
    meta: Optional[dict] = None
    payer: Optional[str] = None
    invoiceType: Optional[str] = None
    invoiceNumber: Optional[str] = None
    buyerTaxId: Optional[str] = None
    sellerTaxId: Optional[str] = None
    merchant: Optional[str] = None
    occurDate: Optional[str] = None
    paymentDate: Optional[str] = None
    taxDeductible: Optional[bool] = None
    riskTags: Optional[list] = None
    is_archive_only: Optional[bool] = None
    isArchiveOnly: Optional[bool] = None
    remark: Optional[str] = None
    businessContext: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdExpenseType: Optional[Literal[
        "personnel", "direct_input", "depreciation_amortization", "design", "testing", "outsourced", "other"
    ]] = None
    rdOriginalAmount: Optional[Decimal] = None
    rdAllocationMethod: Optional[Literal["direct", "timesheet", "usage", "area", "quantity", "other"]] = None
    rdAllocationBasis: Optional[str] = None
    rdNotes: Optional[str] = None
    # 已人工调整过分录时，改方向/分类/付款方式/金额会按规则重算分录（冲掉手改）。
    # 前端弹窗确认后置 True 才允许覆盖；默认 None 由后端拦下，避免静默丢失。
    confirmRegenerateVoucher: Optional[bool] = None
    autoReverseRepost: Optional[bool] = None


class VoucherLineAdjustItem(BaseModel):
    """人工调整分录的一行。方向与金额由服务端再校验，这里只做结构约束。"""

    direction: str
    accountCode: str
    amount: Decimal
    accountName: Optional[str] = None


class VoucherLinesAdjustRequest(BaseModel):
    """会计手工改写分录（未过账凭证）。resetToSystem=True 时忽略 lines。"""

    lines: Optional[list[VoucherLineAdjustItem]] = None
    reason: Optional[str] = None
    resetToSystem: bool = False


class JournalActionResponse(BaseModel):
    entry: dict


class JournalReverseRequest(BaseModel):
    reason: str
    reversalDate: Optional[str] = None


class CorrectCategoryRequest(BaseModel):
    category: str
    reason: str = ""


class BatchPostRequest(BaseModel):
    entryIds: Optional[List[str]] = None
    month: Optional[str] = None


class BatchPostFailedItem(BaseModel):
    entryId: str
    title: str
    code: str
    reason: str


class BatchPostResponse(BaseModel):
    total: int
    postedCount: int
    failedCount: int
    postedEntryIds: List[str]
    failedEntries: List[BatchPostFailedItem]


class PeriodCloseRequest(BaseModel):
    pass


class PeriodReopenRequest(BaseModel):
    reason: str
    stepUpToken: str


class StepUpRequest(BaseModel):
    action: Literal["reopen_period", "confirm_legacy_migration", "adjust_opening_balance"]
    phone: str
    code: str
    bizId: Optional[str] = None


class StepUpResponse(BaseModel):
    stepUpToken: str
    expiresIn: int = 300


class CompanyMemberCreate(BaseModel):
    userId: str
    role: Literal["accountant", "preparer", "viewer"]


class CompanyMemberUpdate(BaseModel):
    role: Optional[Literal["accountant", "preparer", "viewer"]] = None
    status: Optional[Literal["active", "disabled"]] = None


class TodoStateUpdate(BaseModel):
    done: Optional[bool] = None
    dismissed: Optional[bool] = None


# ── Auth ──

class WechatLoginRequest(BaseModel):
    code: str
    privacy_accepted_at: str
    tos_version: Optional[str] = "v1.0"
    privacy_version: Optional[str] = "v1.0"


class WechatLoginResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    company_id: str
    expires_in: str
    has_password: bool = False


class SwitchCompanyRequest(BaseModel):
    """切换当前 company（写入服务端并下发新 JWT）。"""

    company_id: Optional[str] = None
    companyId: Optional[str] = None


class PhoneSendCodeRequest(BaseModel):
    """手机号发送验证码请求。"""

    phone: str


class PhoneSendCodeResponse(BaseModel):
    """手机号发送验证码响应。"""

    biz_id: Optional[str] = None
    request_id: Optional[str] = None


class PhoneLoginRequest(BaseModel):
    """手机号验证码登录请求。"""

    phone: str
    code: str
    biz_id: Optional[str] = None
    bizId: Optional[str] = None


class PhoneBindRequest(BaseModel):
    """登录后绑定手机号请求。"""

    phone: str
    code: str
    biz_id: Optional[str] = None
    bizId: Optional[str] = None


class WechatBindRequest(BaseModel):
    """登录后绑定微信请求。"""

    code: str


class PasswordLoginRequest(BaseModel):
    """手机号 + 密码登录请求。"""

    phone: str
    password: str


class SetPasswordRequest(BaseModel):
    """设置/修改密码请求（登录态）。已设密码时必须携带 old_password。"""

    old_password: Optional[str] = None
    oldPassword: Optional[str] = None
    new_password: str
    newPassword: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    """忘记密码：短信验证码 + 新密码（无登录态）。"""

    phone: str
    code: str
    biz_id: Optional[str] = None
    bizId: Optional[str] = None
    new_password: str
    newPassword: Optional[str] = None


# ── Copilot ──

class CopilotChatRequest(BaseModel):
    messages: List[dict]
    context: Optional[dict] = None
    mode: Optional[Literal["agent", "tax_advisor", "secretary", "coach"]] = "agent"

class CopilotChatResponse(BaseModel):
    reply: str
    bookPayload: Optional[dict] = None
    disclaimer: Optional[str] = None


# ── 报表 ──

class MonthlyReportRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Optional[Literal["month", "quarter", "year"]] = "month"
    includeSummary: Optional[bool] = False
    includeDrafts: Optional[bool] = False

class ReportDiagnosisRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Optional[Literal["month", "quarter", "year"]] = "month"
    includeDrafts: Optional[bool] = False

class ReportDiagnosisResponse(BaseModel):
    summary: str

class MonthlyReportResponse(BaseModel):
    summary: Optional[str] = None
    monthIncome: Decimal
    monthExpense: Decimal
    netProfit: Decimal
    categoryBreakdown: dict
    taxSummary: Optional[dict] = None
    availableCash: Optional[Decimal] = None
    pendingReceivables: Optional[Decimal] = None
    pendingPayables: Optional[Decimal] = None
    # 资产采购（存货/设备/预付）不进当期费用，单独展示以免用户误以为漏记
    assetPurchase: Optional[Decimal] = None
    period: Optional[str] = None
    periodType: Optional[str] = None



class HealthScoreRequest(StrictRequestModel):
    month: Optional[str] = None
    includeDiagnosis: Optional[bool] = False


class DimensionDetail(BaseModel):
    code: str
    name: str
    score: int
    weight: int
    rawScore: Optional[int] = None
    status: str
    highlight: str
    tips: Optional[str] = None


class HealthPenaltyItem(BaseModel):
    id: str
    dimensionCode: str
    title: str
    description: str
    deduction: int
    severity: Literal["high", "medium", "low"]
    targetRoute: Optional[str] = None
    actionLabel: Optional[str] = None


class HealthScoreResponse(BaseModel):
    month: Optional[str] = None
    totalScore: int
    level: str
    levelCode: Optional[str] = None
    levelLabel: Optional[str] = None
    summary: Optional[str] = None
    dimensions: dict
    dimensionDetails: Optional[List[DimensionDetail]] = None
    penalties: Optional[List[HealthPenaltyItem]] = None
    suggestions: List[str]
    keyMetrics: Optional[List[dict]] = None
    taxRiskScore: Optional[int] = None
    taxRiskLevel: Optional[str] = None
    riskItems: Optional[List[str]] = None
    aiDiagnosis: Optional[dict] = None


class VatAssistRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Literal["month", "quarter"] = "quarter"
    taxpayerType: Optional[Literal["general", "small_scale"]] = "small_scale"

class VatAssistItem(BaseModel):
    entryId: str
    occurDate: str
    title: str
    salesCategory: Literal["goods_labor", "services"]
    invoiceType: str
    invoiceNumber: str
    salesAmount: Decimal

class VatFilingField(BaseModel):
    code: str
    section: str
    label: str
    amount: Decimal
    editable: bool

class VatAssistResponse(BaseModel):
    period: str
    periodType: Literal["month", "quarter"]
    periodStart: str
    periodEnd: str
    taxpayerType: str
    policyVersion: str
    threshold: Decimal
    isBelowThreshold: bool
    exemptionApplied: bool
    goodsLaborSales: Decimal
    serviceSales: Decimal
    totalSales: Decimal
    taxableBase: Decimal
    vatBeforeRelief: Decimal
    vatReduction: Decimal
    vatExemption: Decimal
    items: List[VatAssistItem]
    totalVatPayable: Decimal
    cityMaintenanceTax: Decimal
    educationSurcharge: Decimal
    localEducationSurcharge: Decimal
    surchargeTotal: Decimal
    filingFields: List[VatFilingField]
    warnings: List[str]
    blockingIssues: List[str]
    readyToFile: bool
    sourceEntryCount: int


class CitAssistRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Literal["quarter"] = "quarter"
    specificBusinessIncome: Decimal = Decimal("0")
    nonTaxableIncome: Decimal = Decimal("0")
    acceleratedDepreciationReduction: Decimal = Decimal("0")
    exemptDeduction: Decimal = Decimal("0")
    incomeExemption: Decimal = Decimal("0")
    priorYearLoss: Decimal = Decimal("0")
    taxCredit: Decimal = Decimal("0")
    prepaidTax: Decimal = Decimal("0")
    specialBusinessPrepaidTax: Decimal = Decimal("0")
    employeeCountStart: Optional[int] = None
    employeeCountEnd: Optional[int] = None
    assetTotalStart: Optional[Decimal] = None
    assetTotalEnd: Optional[Decimal] = None
    restrictedIndustry: Optional[bool] = None
    manualAdjustmentsConfirmed: bool = False
    operatingCostConfirmed: bool = False
    totalProfitConfirmed: bool = False
    assetLoss: Optional[bool] = None

class CitFilingField(BaseModel):
    line: str
    label: str
    amount: Decimal
    editable: bool
    source: str

class CitAssistResponse(BaseModel):
    period: str
    periodStart: str
    periodEnd: str
    cumulativeRevenue: Decimal
    cumulativeOperatingCost: Decimal
    cumulativeProfit: Decimal
    actualProfit: Decimal
    standardTax: Decimal
    taxReduction: Decimal
    estimatedTax: Decimal
    smallMicroEligibility: Literal["eligible", "ineligible", "undetermined"]
    # 现行口径：在职员工名册人数（公司制下限 1）；名册不可用时回退申报表季初/季末平均
    employeeAverage: Optional[Decimal] = None
    assetAverage: Optional[Decimal] = None
    filingFields: List[CitFilingField]
    blockingIssues: List[str]
    warnings: List[str]
    readyToFile: bool
    policyVersion: str
    assetTotalStart: Decimal
    assetTotalEnd: Decimal
    operatingCostConfirmed: bool
    totalProfitConfirmed: bool
    assetLoss: Optional[bool]
    # 现行口径：仅「是否限制/禁止行业」已确认；从业人数自动派生
    companyTaxProfileComplete: bool
    beginnerReady: bool
    # 纳税调增（系统测算默认值，可修改）：招待费 60%/5‰、广宣费 15%、罚款滞纳金、无票支出
    autoAdjustmentTotal: Decimal = Decimal("0.00")
    autoAdjustments: List["CitAdjustmentItem"] = []


class CitAdjustmentItem(BaseModel):
    code: str
    label: str
    amount: Decimal
    basis: str
    detail: List[dict] = []


class ArchiveChecklistRequest(StrictRequestModel):
    month: str

class ArchiveItem(BaseModel):
    id: Optional[str] = None
    voucherNo: Optional[str] = None
    date: str
    summary: str
    amount: Decimal
    direction: Optional[str] = None
    category: Optional[str] = None
    docType: str
    invoiceNumber: Optional[str] = None
    invoiceType: Optional[str] = None
    compliance: str
    complianceStatus: Optional[str] = None
    riskTag: Optional[str] = None
    attachmentCount: int = 0
    note: Optional[str] = None

class ArchiveChecklistResponse(BaseModel):
    month: str
    items: List[ArchiveItem]
    compliantCount: int
    missingCount: int
    totalCount: int
    compliantAmount: Decimal = Decimal("0")
    missingAmount: Decimal = Decimal("0")
    totalAmount: Decimal = Decimal("0")


# ── 凭证（后台）──

class VoucherEntry(BaseModel):
    accountCode: str
    accountName: str
    amount: Decimal

class VoucherDraft(BaseModel):
    voucherDate: str
    voucherNumber: Optional[str] = None
    summary: str
    debitEntries: List[VoucherEntry]
    creditEntries: List[VoucherEntry]
    attachmentCount: int = 0

class VoucherGenerateRequest(StrictRequestModel):
    entryIds: List[str]

class VoucherGenerateResponse(BaseModel):
    vouchers: List[VoucherDraft]

class TrialBalanceRequest(StrictRequestModel):
    month: str

class TrialBalanceItem(BaseModel):
    accountCode: str
    accountName: str
    debitTotal: Decimal
    creditTotal: Decimal
    balance: Decimal

class TrialBalanceResponse(BaseModel):
    month: str
    items: List[TrialBalanceItem]
    isBalanced: bool

# ── 资产负债表 ──

class BalanceSheetRequest(StrictRequestModel):
    month: str
    # 季报口径：报表日=month 所在季度末（如 month=2026-10 且 quarter → 2026-Q4/9-30）
    periodType: Literal["month", "quarter"] = "month"

class BalanceSheetItem(BaseModel):
    accountCode: str
    accountName: str
    currentAmount: Decimal
    previousAmount: Decimal = Decimal("0")

class StandardStatementRow(BaseModel):
    """会计视图（准则格式）单行：年初/期末两列金额；kind 区分节标题/小计/总计行。"""
    rowNo: int = 0
    name: str
    openingAmount: Decimal = Decimal("0.00")
    currentAmount: Decimal = Decimal("0.00")
    level: int = 0
    kind: Literal["item", "section", "subtotal", "total"] = "item"
    accountCode: Optional[str] = None

class BalanceSheetStandard(BaseModel):
    """会小企01表格式：左资产、右负债和所有者权益，两列期间均为年初+期末。"""
    assets: List[StandardStatementRow]
    rights: List[StandardStatementRow]
    balanced: bool = True

class BalanceSheetResponse(BaseModel):
    month: str
    periodType: str = "month"
    period: str = ""
    assets: List[BalanceSheetItem]
    liabilities: List[BalanceSheetItem]
    equity: List[BalanceSheetItem]
    totalAssets: Decimal
    totalLiabilities: Decimal
    totalEquity: Decimal
    isBalanced: bool
    standard: Optional[BalanceSheetStandard] = None

# ── 现金流量表 ──

class CashFlowRequest(StrictRequestModel):
    month: str
    periodType: Literal["month", "quarter"] = "month"

class CashFlowItem(BaseModel):
    category: str
    description: str
    inflow: Decimal
    outflow: Decimal
    netAmount: Decimal

class CashFlowResponse(BaseModel):
    month: str
    periodType: str = "month"
    period: str = ""
    operating: List[CashFlowItem]
    investing: List[CashFlowItem]
    financing: List[CashFlowItem]
    operatingNet: Decimal
    investingNet: Decimal
    financingNet: Decimal
    netIncrease: Decimal
    openingCash: Decimal
    endingCash: Decimal
    balanceSheetCash: Decimal
    classificationDifference: Decimal
    cashRollForwardDifference: Decimal
    balanceSheetCashDifference: Decimal
    classificationBalanced: bool
    cashRollForwardBalanced: bool
    balanceSheetCashBalanced: bool


# ── 所有者权益变动表 ──

class EquityChangeRequest(StrictRequestModel):
    year: int


# ── 利润表 ──

class IncomeStatementRequest(StrictRequestModel):
    month: str
    viewMode: Literal["summary", "quarterly"] = "summary"
    # 报表口径：posting=法定（按记账期间，与申报口径一致）；business=管理（按业务日期）。
    # 两者只在存在跨期补记时才不同。
    basis: Literal["posting", "business"] = "posting"


class IncomeStatementRow(BaseModel):
    key: str
    label: str
    level: int = 0
    values: dict
    isSubtotal: bool = False


class IncomeStatementResponse(BaseModel):
    month: str
    viewMode: Literal["summary", "quarterly"]
    companyName: Optional[str] = None
    columns: List[dict]
    rows: List[IncomeStatementRow]
    totals: dict
    # §10 三项常驻字段：不含税收入 / 已计提增值税 / 待纳税调增，
    # 让用户看见系统知道自己不知道什么（缺省 None 表示旧版兼容）。
    vatSummary: Optional[dict] = None
    # 报表口径与本期补记影响：count/amount/periods（按原期间分组）
    basis: Literal["posting", "business"] = "posting"
    backfillImpact: Optional[dict] = None

# ── 固定资产（P1 WP-B 现代化：卡片-凭证外键 + 状态机 + 折旧计划）─────────

class FixedAssetCategoryResponse(BaseModel):
    code: str
    name: str
    defaultLifeMonths: int
    lifeBasis: str
    lifeMinMonths: int
    lifeMaxMonths: int
    oneTimeAllowed: bool
    expenseAccount: dict


class FixedAssetCategoryListResponse(BaseModel):
    items: List[FixedAssetCategoryResponse]


class FixedAssetCreateRequest(StrictRequestModel):
    name: str
    category: str
    acquisitionType: Literal["purchase", "self_built", "investor_contribution", "donation"] = "purchase"
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    residualValue: Optional[Decimal] = None
    residualRate: Optional[Decimal] = None    # 录入辅助；residualValue 缺省时按 originalValue × rate 换算
    usefulLifeMonths: int
    depreciationMethod: Literal["straight_line", "one_time"] = "straight_line"
    departmentId: Optional[str] = None
    custodian: Optional[str] = None
    location: Optional[str] = None
    paymentMethod: Literal["company_account", "account_payable", "personal_advance"] = "company_account"
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    taxLifeMonths: Optional[int] = None


class FixedAssetFromEntryRequest(FixedAssetCreateRequest):
    sourceEntryId: str


class FixedAssetUpdateRequest(StrictRequestModel):
    name: Optional[str] = None
    category: Optional[str] = None
    depreciationMethod: Optional[Literal["straight_line", "one_time"]] = None
    usefulLifeMonths: Optional[int] = None
    residualValue: Optional[Decimal] = None
    departmentId: Optional[str] = None
    custodian: Optional[str] = None
    location: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    taxLifeMonths: Optional[int] = None


class FixedAssetResponse(BaseModel):
    id: str
    code: str
    name: str
    category: str
    categoryName: str
    acquisitionType: str
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    residualValue: Decimal
    usefulLifeMonths: int
    depreciationMethod: str
    monthlyDepreciation: Decimal
    accumulatedDepreciation: Decimal
    netValue: Decimal
    remainingMonths: int
    lastDepreciatedPeriod: Optional[str] = None
    depreciationEndPeriod: Optional[str] = None
    status: str
    sourceEntryId: Optional[str] = None
    departmentId: Optional[str] = None
    custodian: Optional[str] = None
    location: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    disposedOn: Optional[str] = None
    disposalType: Optional[str] = None
    createdAt: str
    updatedAt: str


class FixedAssetListResponse(BaseModel):
    items: List[FixedAssetResponse]
    total: int
    page: int
    pageSize: int


class FixedAssetEntryPrefillResponse(BaseModel):
    sourceEntryId: str
    existingAssetId: Optional[str] = None
    name: str
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    suggestedCategory: str
    voucherNo: Optional[str] = None
    summary: Optional[str] = None
    sourceAccount: Optional[str] = None


class FixedAssetDepreciationDuePeriod(BaseModel):
    period: str
    amount: Decimal
    count: int


class FixedAssetFromEntryResponse(BaseModel):
    asset: FixedAssetResponse
    duePeriods: List[FixedAssetDepreciationDuePeriod]
    dueTotalAmount: Decimal


class FixedAssetDisposalCreateRequest(StrictRequestModel):
    assetId: str
    disposalType: Literal["sale", "scrap", "donate", "transfer_out"]
    disposalDate: str
    proceedsAmount: Optional[Decimal] = Decimal("0")
    outputVatAmount: Optional[Decimal] = Decimal("0")
    reason: Optional[str] = None


class FixedAssetDisposalResponse(BaseModel):
    id: str
    assetId: str
    assetCode: str
    assetName: str
    disposalType: str
    disposalDate: str
    proceedsAmount: Decimal
    outputVatAmount: Decimal
    accumulatedDepreciationSnapshot: Decimal
    netBookValueSnapshot: Decimal
    gainLossAmount: Decimal
    status: str
    reason: Optional[str] = None
    journalEntryId: Optional[str] = None
    initiatedBy: str
    approvedBy: Optional[str] = None
    createdAt: str


class FixedAssetDisposalListResponse(BaseModel):
    items: List[FixedAssetDisposalResponse]


class FixedAssetRejectRequest(StrictRequestModel):
    reason: str


class FixedAssetReconciliationResponse(BaseModel):
    applicable: bool
    balanced: bool
    cardCostTotal: Decimal
    cardDepreciationTotal: Decimal
    ledger1601: Decimal
    ledger1602: Decimal
    schedulePostedTotal: Decimal
    costDiff: Decimal
    depreciationDiff: Decimal
    scheduleDiff: Decimal
    cardCount: int
    details: List[dict]

# ── 分类管理 ──

class CategoryCreate(BaseModel):
    name: str
    direction: Literal["income", "expense", "owner_transfer"]
    parent_id: Optional[str] = None
    sort_order: int = 0

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    direction: Optional[Literal["income", "expense", "owner_transfer"]] = None
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

class CategoryResponse(BaseModel):
    id: str
    company_id: str
    name: str
    direction: str
    parent_id: Optional[str] = None
    sort_order: int
    is_system: bool
    is_active: bool
    created_at: str
    updated_at: str


# ── 科目表 ──

class AccountCreate(BaseModel):
    code: str
    name: str
    parent_code: Optional[str] = None
    level: int = 1
    category: Optional[str] = None
    type: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    parent_code: Optional[str] = None
    level: Optional[int] = None
    category: Optional[str] = None
    type: Optional[str] = None
    is_active: Optional[bool] = None
    # 数量金额式核算（P1 WP-D）：勾选后凭证行可带数量/单价，账表出数量结存
    quantity_enabled: Optional[bool] = None
    unit: Optional[str] = None


class AccountNode(BaseModel):
    id: str
    company_id: str
    code: str
    name: str
    parent_code: Optional[str] = None
    level: int
    category: Optional[str] = None
    type: Optional[str] = None
    is_leaf: bool
    is_system: bool
    is_active: bool
    quantity_enabled: bool = False
    unit: Optional[str] = None
    created_at: str
    updated_at: str
    children: List["AccountNode"] = []

AccountNode.model_rebuild()


# ── 研发项目与研发费用归集 ──

class RdProjectCreate(BaseModel):
    code: str
    name: str
    manager: Optional[str] = None
    startDate: date
    endDate: Optional[date] = None
    status: Literal["draft", "active", "completed"] = "draft"
    initiationDocumentStatus: Literal["missing", "partial", "complete"] = "missing"
    notes: Optional[str] = None


class RdProjectUpdate(BaseModel):
    name: Optional[str] = None
    manager: Optional[str] = None
    startDate: Optional[date] = None
    endDate: Optional[date] = None
    status: Optional[Literal["draft", "active", "completed", "archived"]] = None
    initiationDocumentStatus: Optional[Literal["missing", "partial", "complete"]] = None
    notes: Optional[str] = None


class RdProjectResponse(BaseModel):
    id: str
    code: str
    name: str
    manager: Optional[str] = None
    startDate: date
    endDate: Optional[date] = None
    status: str
    initiationDocumentStatus: str
    notes: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime

# ── 通用归集（管理口径）───────────────────────────────────────────────────────
# 分配接口只收金额：RATE = Numeric(12,6)，按比例分摊必然尾差，精确相等校验
# 永远不成立。allocation_rate 由后端回填，仅作留痕。


class AggregationDimensionCreate(BaseModel):
    code: str
    name: str
    kind: Literal["project", "department", "customer", "supplier", "custom"] = "custom"


class AggregationDimensionUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None


class AggregationDimensionResponse(BaseModel):
    id: str
    code: str
    name: str
    kind: str
    status: str
    createdAt: datetime
    updatedAt: datetime


class AggregationMemberCreate(BaseModel):
    code: str
    name: str
    startDate: date
    endDate: Optional[date] = None
    status: Literal["draft", "active", "completed", "archived"] = "active"
    metadata: Optional[dict] = None


class AggregationMemberUpdate(BaseModel):
    name: Optional[str] = None
    endDate: Optional[date] = None
    status: Optional[Literal["draft", "active", "completed", "archived"]] = None


class AggregationMemberResponse(BaseModel):
    id: str
    dimensionId: str
    code: str
    name: str
    startDate: date
    endDate: Optional[date] = None
    status: str
    metadata: dict = Field(default_factory=dict)
    createdAt: datetime
    updatedAt: datetime


class AggregationAllocationInput(BaseModel):
    memberId: str
    amount: Decimal
    allocationMethod: Literal[
        "direct", "timesheet", "usage", "area", "quantity", "revenue", "headcount", "other"
    ] = "direct"
    allocationBasis: Optional[str] = None


class AggregationReplaceRequest(BaseModel):
    dimensionId: str
    allocations: List[AggregationAllocationInput] = Field(default_factory=list)
    complete: bool = False


class AggregationRuleInput(BaseModel):
    memberId: str
    priority: int = 100
    accountPrefix: Optional[str] = None
    direction: Optional[Literal["debit", "credit"]] = None
    keyword: Optional[str] = None
    counterparty: Optional[str] = None
    rate: Decimal = Decimal("1")
    method: Literal[
        "direct", "timesheet", "usage", "area", "quantity", "revenue", "headcount", "other"
    ] = "direct"
    basis: Optional[str] = None


class AggregationRulesReplaceRequest(BaseModel):
    rules: List[AggregationRuleInput] = Field(default_factory=list)


class AggregationApplyRulesRequest(BaseModel):
    dimensionId: str
    start: str
    end: str
    basis: Literal["period", "occurDate"] = "period"
    scopePrefixes: Optional[List[str]] = None


# ── 报表项映射规则（P2，双轨校验） ──

class ReportLinePatternInput(BaseModel):
    """单个声明式 pattern。没有表达式求值，只有 mode/value/sign 三元组。

    sign 取 1 或 -1，用于表达减项——例如研发费用 = 4301 − 4301002（资本化部分不进损益）。
    """

    mode: Literal["prefix", "exact", "range"]
    value: str
    sign: int = 1


class ReportLineRuleInput(BaseModel):
    lineCode: str
    lineName: Optional[str] = None
    patterns: List[ReportLinePatternInput] = Field(default_factory=list)
    sign: Literal["debit", "credit", "net"] = "net"
    status: Literal["active", "archived"] = "active"
    effectiveFrom: str
    effectiveTo: Optional[str] = None


class ReportLineRulesReplaceRequest(BaseModel):
    report: Literal["profit", "balance"]
    rules: List[ReportLineRuleInput] = Field(default_factory=list)


class ReportLineCompareRequest(BaseModel):
    report: Literal["profit", "balance"]
    period: str = Field(description="YYYY-MM（月报）或 YYYY-Qn（季报）")
    periodType: Literal["month", "quarter"] = "month"


# ── 同比/环比分析 ──

class ComparisonItem(BaseModel):
    category: str
    base_amount: Decimal
    compare_amount: Decimal
    change_percent: Optional[Decimal] = None  # percentage change

class ComparisonRequest(StrictRequestModel):
    base_month: str
    compare_mode: Literal["mom", "yoy"]

class ComparisonResponse(BaseModel):
    base_month: str
    compare_month: str
    base_income: Decimal
    base_expense: Decimal
    base_profit: Decimal
    compare_income: Decimal
    compare_expense: Decimal
    compare_profit: Decimal
    income_change_percent: Optional[Decimal] = None
    expense_change_percent: Optional[Decimal] = None
    profit_change_percent: Optional[Decimal] = None
    category_breakdown: List[ComparisonItem]


# ── 无形资产 ──

INTANGIBLE_STATUS_VALUES = ("draft", "pending_review", "active", "idle", "fully_amortized", "disposing", "disposed", "scrapped")


class IntangibleCategoryResponse(BaseModel):
    code: str
    name: str
    defaultLifeMonths: int
    lifeBasis: str
    expenseAccount: dict
    remindExpiry: bool
    certLabel: str
    lifeMinMonths: int
    lifeMaxMonths: int


class IntangibleCategoryListResponse(BaseModel):
    items: List[IntangibleCategoryResponse]


class IntangibleAssetCreateRequest(StrictRequestModel):
    name: str
    category: str
    acquisitionType: Literal["purchase", "self_developed", "investor_contribution", "donation"] = "purchase"
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    residualValue: Optional[Decimal] = None   # 默认 0（小企业会计准则残值视为零）
    residualRate: Optional[Decimal] = None    # 录入辅助；residualValue 缺省时按 originalValue × rate 换算
    usefulLifeMonths: int
    legalCertNo: Optional[str] = None
    grantOrg: Optional[str] = None
    expiryDate: Optional[str] = None
    departmentId: Optional[str] = None
    custodian: Optional[str] = None
    paymentMethod: Literal["company_account", "account_payable", "personal_advance"] = "company_account"
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    taxLifeMonths: Optional[int] = None


class IntangibleAssetFromEntryRequest(IntangibleAssetCreateRequest):
    sourceEntryId: str


class IntangibleAssetUpdateRequest(StrictRequestModel):
    name: Optional[str] = None
    usefulLifeMonths: Optional[int] = None
    residualValue: Optional[Decimal] = None
    legalCertNo: Optional[str] = None
    grantOrg: Optional[str] = None
    expiryDate: Optional[str] = None
    departmentId: Optional[str] = None
    custodian: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    taxLifeMonths: Optional[int] = None


class IntangibleAssetResponse(BaseModel):
    id: str
    code: str
    name: str
    category: str
    categoryName: str
    acquisitionType: str
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    residualValue: Decimal
    usefulLifeMonths: int
    amortizationMethod: str
    accumulatedAmortization: Decimal
    netValue: Decimal
    remainingMonths: int
    lastAmortizedPeriod: Optional[str] = None
    amortizationEndPeriod: Optional[str] = None
    status: str
    sourceEntryId: Optional[str] = None
    legalCertNo: Optional[str] = None
    grantOrg: Optional[str] = None
    expiryDate: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None
    disposedOn: Optional[str] = None
    disposalType: Optional[str] = None
    createdAt: str
    updatedAt: str


class IntangibleAssetListResponse(BaseModel):
    items: List[IntangibleAssetResponse]
    total: int
    page: int
    pageSize: int


class IntangibleAssetEntryPrefillResponse(BaseModel):
    sourceEntryId: str
    existingAssetId: Optional[str] = None
    name: str
    acquisitionDate: str
    availableForUseDate: str
    originalValue: Decimal
    suggestedCategory: str
    voucherNo: Optional[str] = None
    summary: Optional[str] = None
    sourceAccount: Optional[str] = None


class IntangibleAmortizationDuePeriod(BaseModel):
    period: str
    amount: Decimal
    count: int


class IntangibleAssetFromEntryResponse(BaseModel):
    asset: IntangibleAssetResponse
    duePeriods: List[IntangibleAmortizationDuePeriod]
    dueTotalAmount: Decimal


class AmortizationScheduleRowResponse(BaseModel):
    id: str
    assetId: str
    assetName: str
    periodStart: str
    sequenceNo: int
    amount: Decimal
    accumulatedBefore: Decimal
    accumulatedAfter: Decimal
    status: str
    journalEntryId: Optional[str] = None
    planVersion: int


class AmortizationScheduleResponse(BaseModel):
    items: List[AmortizationScheduleRowResponse]


class AmortizationRunRequest(StrictRequestModel):
    period: str
    assetIds: Optional[List[str]] = None
    dryRun: bool = False


class AmortizationRunItem(BaseModel):
    assetId: str
    assetName: str
    periodStart: str
    sequenceNo: int
    amount: Decimal
    status: str  # posted / pending_review / skipped
    journalEntryId: Optional[str] = None


class AmortizationRunResponse(BaseModel):
    period: str
    dryRun: bool
    totalAmount: Decimal
    processedCount: int
    skippedCount: int
    postedCount: int
    pendingReviewCount: int
    items: List[AmortizationRunItem]


class AmortizationCatchUpRequest(StrictRequestModel):
    throughPeriod: str
    assetIds: Optional[List[str]] = None
    dryRun: bool = False


class AmortizationCatchUpPeriod(BaseModel):
    period: str
    totalAmount: Decimal
    processedCount: int
    skippedCount: int
    postedCount: int
    pendingReviewCount: int


class AmortizationCatchUpResponse(BaseModel):
    throughPeriod: str
    dryRun: bool
    periods: List[AmortizationCatchUpPeriod]
    totalAmount: Decimal
    processedCount: int
    skippedCount: int
    postedCount: int
    pendingReviewCount: int
    items: List[AmortizationRunItem]


class DepreciationCatchUpRequest(StrictRequestModel):
    throughPeriod: str
    assetIds: Optional[List[str]] = None
    dryRun: bool = False


class DepreciationCatchUpPeriod(BaseModel):
    period: str
    totalAmount: Decimal
    processedCount: int
    skippedCount: int
    postedCount: int
    pendingReviewCount: int


class DepreciationCatchUpResponse(BaseModel):
    throughPeriod: str
    dryRun: bool
    periods: List[DepreciationCatchUpPeriod]
    totalAmount: Decimal
    processedCount: int
    skippedCount: int
    postedCount: int
    pendingReviewCount: int
    items: List[AmortizationRunItem]


class IntangibleDisposalCreateRequest(StrictRequestModel):
    assetId: str
    disposalType: Literal["sale", "scrap", "expire", "donate", "transfer_out"]
    disposalDate: str
    proceedsAmount: Optional[Decimal] = Decimal("0")
    outputVatAmount: Optional[Decimal] = Decimal("0")
    reason: Optional[str] = None


class IntangibleDisposalResponse(BaseModel):
    id: str
    assetId: str
    assetCode: str
    assetName: str
    disposalType: str
    disposalDate: str
    proceedsAmount: Decimal
    outputVatAmount: Decimal
    accumulatedAmortizationSnapshot: Decimal
    netBookValueSnapshot: Decimal
    gainLossAmount: Decimal
    status: str
    reason: Optional[str] = None
    journalEntryId: Optional[str] = None
    initiatedBy: str
    approvedBy: Optional[str] = None
    createdAt: str


class IntangibleDisposalListResponse(BaseModel):
    items: List[IntangibleDisposalResponse]


class AmortizationSummaryRequest(StrictRequestModel):
    periodFrom: str
    periodTo: str


class AmortizationSummaryRow(BaseModel):
    category: str
    categoryName: str
    originalValue: Decimal
    amortizationAmount: Decimal
    adminAmount: Decimal
    rdAmount: Decimal


class AmortizationSummaryResponse(BaseModel):
    periodFrom: str
    periodTo: str
    items: List[AmortizationSummaryRow]
    reconciliation: dict


class IntangibleRejectRequest(StrictRequestModel):
    reason: str


class IntangibleImpairmentTestCreateRequest(StrictRequestModel):
    assetId: str
    testPeriod: str
    testDate: str
    triggerType: Literal["tech_obsolete", "legal_invalid", "market_decline", "idle_long", "other"]
    indicatorDescription: str
    recoverableAmount: Decimal
    evidenceRef: Optional[str] = None


class IntangibleImpairmentTestResponse(BaseModel):
    id: str
    assetId: str
    assetCode: str
    assetName: str
    testPeriod: str
    testDate: str
    triggerType: str
    indicatorDescription: str
    carryingAmount: Decimal
    recoverableAmount: Decimal
    conclusion: str
    provisionAmount: Decimal
    evidenceRef: Optional[str] = None
    assessedBy: str
    reviewedBy: Optional[str] = None
    reviewedAt: Optional[str] = None
    createdAt: str


class IntangibleImpairmentTestListResponse(BaseModel):
    items: List[IntangibleImpairmentTestResponse]


class IntangibleAssetChangeRequest(StrictRequestModel):
    usefulLifeMonths: int
    residualValue: Optional[Decimal] = None
    effectivePeriod: Optional[str] = None  # YYYY-MM，缺省为下一期



# ── 上手引导（M1） ──

class SetupStepProgress(BaseModel):
    current: int
    target: int
    unit: str


class SetupStepResponse(BaseModel):
    key: str
    title: str
    hint: str
    status: Literal["todo", "doing", "done", "waived", "locked"]
    blockedBy: Optional[str] = None
    detail: Optional[str] = None
    deepLink: str
    informational: bool = False
    progress: Optional[SetupStepProgress] = None
    waivedReason: Optional[str] = None


class SetupReadinessResponse(BaseModel):
    companyId: str
    view: Literal["boss", "accounting"]
    fiscalYear: int
    completed: int
    total: int
    ready: bool
    generatedAt: str
    items: List[SetupStepResponse]


class DemoBookResponse(BaseModel):
    companyId: str
    name: str
    entries: int = 0
    statements: int = 0
    durationMs: int = 0
    reused: bool = False


class DemoBookDeleteResponse(BaseModel):
    deleted: bool
    companyId: str
    nextCompanyId: Optional[str] = None
