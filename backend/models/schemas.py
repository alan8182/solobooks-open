# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from pydantic import BaseModel as PydanticBaseModel, ConfigDict
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
    payer: Optional[str] = "company_account"
    companyId: Optional[str] = None
    company_id: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdExpenseType: Optional[Literal[
        "personnel", "direct_input", "depreciation_amortization", "design", "testing", "outsourced", "other"
    ]] = None
    rdOriginalAmount: Optional[Decimal] = None
    rdAllocationMethod: Optional[Literal["direct", "timesheet", "usage", "area", "quantity", "other"]] = None
    rdAllocationBasis: Optional[str] = None
    rdNotes: Optional[str] = None

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


class JournalActionResponse(BaseModel):
    entry: dict


class JournalReverseRequest(BaseModel):
    reason: str
    reversalDate: Optional[str] = None


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

class ReportDiagnosisRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Optional[Literal["month", "quarter", "year"]] = "month"

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
    period: Optional[str] = None
    periodType: Optional[str] = None



class HealthScoreRequest(StrictRequestModel):
    month: Optional[str] = None

class HealthScoreResponse(BaseModel):
    totalScore: int
    level: str
    dimensions: dict
    suggestions: List[str]
    taxRiskScore: Optional[int] = None
    taxRiskLevel: Optional[str] = None
    riskItems: Optional[List[str]] = None


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
    companyTaxProfileComplete: bool
    beginnerReady: bool


class ArchiveChecklistRequest(StrictRequestModel):
    month: str

class ArchiveItem(BaseModel):
    date: str
    summary: str
    amount: Decimal
    docType: str
    compliance: str
    riskTag: Optional[str] = None
    note: Optional[str] = None

class ArchiveChecklistResponse(BaseModel):
    month: str
    items: List[ArchiveItem]
    compliantCount: int
    missingCount: int
    totalCount: int


class TaxSummaryRequest(StrictRequestModel):
    month: Optional[str] = None
    period: Optional[str] = None
    periodType: Literal["month", "quarter"] = "quarter"
    taxpayerType: Optional[str] = "small_scale"

class TaxDeadlineItem(BaseModel):
    taxType: str
    filingPeriod: str
    dueDate: str
    status: Literal["upcoming", "due_soon", "due_today", "overdue"]
    daysRemaining: int
    verified: bool

class TaxSummaryResponse(BaseModel):
    period: str
    periodType: Literal["month", "quarter"]
    periodStart: str
    periodEnd: str
    totalSales: Decimal
    accountingProfit: Decimal
    taxableIncomeEstimate: Decimal
    estimatedVat: Decimal
    estimatedCit: Decimal
    vatExemption: Decimal
    bookExpenses: Decimal
    totalDeductible: Decimal
    totalNonDeductible: Decimal
    pendingDeductibility: Decimal
    riskCount: int
    filingDeadlines: List[TaxDeadlineItem]
    blockingIssues: List[str]
    warnings: List[str]
    readyToFile: bool
    policyVersion: str
    reminders: List[str]


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

class BalanceSheetItem(BaseModel):
    accountCode: str
    accountName: str
    currentAmount: Decimal
    previousAmount: Decimal = Decimal("0")

class BalanceSheetResponse(BaseModel):
    month: str
    assets: List[BalanceSheetItem]
    liabilities: List[BalanceSheetItem]
    equity: List[BalanceSheetItem]
    totalAssets: Decimal
    totalLiabilities: Decimal
    totalEquity: Decimal
    isBalanced: bool

# ── 现金流量表 ──

class CashFlowRequest(StrictRequestModel):
    month: str

class CashFlowItem(BaseModel):
    category: str
    description: str
    inflow: Decimal
    outflow: Decimal
    netAmount: Decimal

class CashFlowResponse(BaseModel):
    month: str
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


# ── 利润表 ──

class IncomeStatementRequest(StrictRequestModel):
    month: str
    viewMode: Literal["summary", "quarterly"] = "summary"


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

# ── 固定资产 ──

class FixedAssetBase(BaseModel):
    name: str
    originalValue: Decimal
    purchaseDate: str
    category: str
    usefulLife: int
    residualValueRate: Decimal
    oneTimeDepreciation: bool = False
    sourceEntryId: Optional[str] = None
    companyId: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None

class FixedAssetCreate(FixedAssetBase):
    id: Optional[str] = None
    company_id: Optional[str] = None
    accumulatedDepreciation: Optional[Decimal] = Decimal("0.0")
    status: Optional[str] = "in_use"
    lastDepreciationDate: Optional[str] = None

class FixedAssetUpdate(BaseModel):
    status: Optional[str] = None
    accumulatedDepreciation: Optional[Decimal] = None
    lastDepreciationDate: Optional[str] = None
    rdProjectId: Optional[str] = None
    rdUseRate: Optional[Decimal] = None
    rdAllocationBasis: Optional[str] = None

class FixedAssetResponse(FixedAssetBase):
    id: str
    accumulatedDepreciation: Decimal
    status: str
    lastDepreciationDate: Optional[str] = None

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
