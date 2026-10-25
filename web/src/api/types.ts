/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// API transport uses fixed-point strings. The response interceptor converts
// them to numbers for display-only view models; authoritative calculations
// remain on the server.
export type Money = number
export type MoneyInput = string | number

// ===== Auth Types =====
export interface WechatLoginRequest {
  code: string
  privacy_accepted_at: string
  tos_version?: string
  privacy_version?: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user_id: string
  company_id: string
  expires_in: number
  has_password?: boolean
}

export interface PhoneSendCodeRequest {
  phone: string
}

export interface PhoneSendCodeResponse {
  biz_id?: string
  request_id?: string
}

export interface PhoneLoginRequest {
  phone: string
  code: string
  biz_id?: string
}

export interface PasswordLoginRequest {
  phone: string
  password: string
}

export interface SetPasswordRequest {
  old_password?: string
  new_password: string
}

export interface ResetPasswordRequest {
  phone: string
  code: string
  biz_id?: string
  new_password: string
}

export interface UserProfile {
  user_id: string
  nickname: string
  avatar: string
  phone: string
  phone_bound: boolean
  wechat_bound: boolean
  has_password?: boolean
  companies: CompanyInfo[]
  current_company_id: string
  server_time: string
}

export interface CompanyInfo {
  company_id: string
  name: string
  type?: string
  taxpayerType?: string
  industry?: string
  startDate?: string
  taxId?: string
  restrictedIndustry?: boolean
  /** 增值税申报周期（小规模可按税局核定选月报/季报） */
  vatFilingCycle?: string
  /** 经营所得 A 表预缴周期（个体户/个独按税局核定） */
  businessIncomeIitFilingCycle?: string
  /** 演示账套标记：由 onboarding 生成，可一键删除，永不与真实账套混用 */
  isDemo?: boolean
  created_at: string
}

export interface UpdateProfileRequest {
  nickname?: string
  avatar?: string
}

export interface CreateCompanyRequest {
  name: string
  type?: string
  taxpayerType?: string
  industry?: string
  startDate?: string
  taxId?: string
  restrictedIndustry?: boolean
  vatFilingCycle?: string
  businessIncomeIitFilingCycle?: string
}

export interface SwitchCompanyRequest {
  company_id?: string
}

// ===== Record Types =====
export interface RecordTextRequest {
  text: string
  mode?: 'voice' | 'text'
  client_time?: string
  source?: string
}

export interface RecordTextResponse {
  id: string
  direction: 'income' | 'expense' | 'owner_transfer'
  amount: Money
  title: string
  category: string
  confidence: number
  explanation: string
  accountCode?: string
  accountName?: string
  vatRate?: number
  vatAmount?: Money
  taxDeductible?: boolean
  riskTags?: string[]
  voucherHint?: Record<string, unknown>
  askFollowUp?: boolean
  followUpOptions?: string[]
  payer?: 'company_account' | 'boss_personal'
}

export interface RecordReceiptResponse {
  id: string
  merchant: string
  amount: Money
  date?: string
  direction?: string
  suggestedCategory: string
  confidence: number
  explanation: string
  rawText?: string
  invoiceType?: string
  invoiceNumber?: string
  billingStatus?: string
  accountCode?: string
  accountName?: string
  vatRate?: number
  vatAmount?: Money
  taxDeductible?: boolean
  riskTags?: string[]
  askFollowUp?: boolean
  followUpOptions?: string[]
  payer?: string
  buyerName?: string
  buyerTaxId?: string
  sellerName?: string
  sellerTaxId?: string
}

export interface BatchEntry {
  direction: string
  amount: Money
  title: string
  category: string
  accountCode?: string
  accountName?: string
  vatRate?: number
  vatAmount?: Money
  taxDeductible?: boolean
  riskTags?: string[]
  payer?: string
}

export interface RecordBatchResponse {
  entries: BatchEntry[]
}

export interface ConfirmRequest {
  id?: string
  direction: string
  amount: MoneyInput
  title: string
  category: string
  accountCode?: string
  accountName?: string
  billingStatus?: string
  invoiceType?: string
  invoiceNumber?: string
  buyerTaxId?: string
  sellerTaxId?: string
  merchant?: string
  occurDate?: string
  /** 跨期补记：业务日期所属期间已关账、用户确认补记到后续期间时为 true */
  allowBackfill?: boolean
  /** 补记原因（写进台账，结账检查与备查簿要用） */
  backfillReason?: string
  paymentDate?: string
  sourceType?: string
  dedupFingerprint?: string
  payer?: string
  rdProjectId?: string
  rdExpenseType?: RdExpenseType
  rdOriginalAmount?: MoneyInput
  rdAllocationMethod?: 'direct' | 'timesheet' | 'usage' | 'area' | 'quantity' | 'other'
  rdAllocationBasis?: string
  rdNotes?: string
}

export type RdExpenseType = 'personnel' | 'direct_input' | 'depreciation_amortization' | 'design' | 'testing' | 'outsourced' | 'other'

export interface RdProject {
  id: string
  code: string
  name: string
  manager?: string
  startDate: string
  endDate?: string
  status: 'draft' | 'active' | 'completed' | 'archived'
  initiationDocumentStatus: 'missing' | 'partial' | 'complete'
  notes?: string
  createdAt: string
  updatedAt: string
}

export interface RdExpenseTypeOption {
  value: RdExpenseType
  label: string
  accountCode: string
}

export interface ConfirmResponse {
  id: string
  voucherStatus: string
  voucherRef?: string
  duplicateRisk?: Record<string, unknown>
  entry: Record<string, unknown>
}

export interface Entry {
  id: string
  user_id: string
  company_id: string
  direction: string
  amount: Money
  title: string
  category: string
  accountCode?: string
  accountName?: string
  billingStatus?: string
  invoiceType?: string
  invoiceNumber?: string
  merchant?: string
  occurDate?: string
  paymentDate?: string
  payer?: string
  vatRate?: number
  vatAmount?: Money
  taxDeductible?: boolean
  riskTags?: string[]
  sourceType?: string
  voucherStatus?: string
  voucherRef?: string
  voucherLines?: unknown
  /** 分录已被会计人工调整（服务端 metadata 透出），改分类前需确认覆盖 */
  voucherManuallyAdjusted?: boolean
  voucherAdjustment?: {
    adjustedAt?: string
    adjustedBy?: string
    reason?: string
    previousLines?: { lineNo?: number; direction?: string; accountCode?: string; accountName?: string; amount?: string }[]
  }
  /** 凭证类型：closing = 期末损益结转（系统自动生成）；opening = 期初；reversal = 红字冲销 */
  entryType?: string
  /** 期末损益结转凭证标记：结账自动生成，反结账时自动红冲 */
  isClosingTransfer?: boolean
  /** 记账期间（YYYY-MM）。补记时可能与该凭证的业务日期所属月不同；法定报表按此口径 */
  period?: string
  /** 跨期补记：业务日期所属期间已关账，凭证改记到后续未关账期间 */
  isBackfill?: boolean
  backfillFromPeriod?: string
  backfillReason?: string
  /** 凭证署名（真实操作留痕）：制单人/审核人/记账人的显示名，空串表示未发生（如未复核） */
  createdByName?: string
  reviewedByName?: string
  postedByName?: string
  created_at?: string
  updated_at?: string
  remark?: string
  businessContext?: string
  business_context?: string
}

export interface EntryUpdateRequest {
  title?: string
  amount?: MoneyInput
  direction?: string
  category?: string
  billingStatus?: string
  paymentStatus?: 'paid' | 'unpaid' | null
  accountCode?: string
  accountName?: string
  payer?: string
  invoiceType?: string
  invoiceNumber?: string
  buyerTaxId?: string
  sellerTaxId?: string
  merchant?: string
  occurDate?: string
  paymentDate?: string | null
  taxDeductible?: boolean
  riskTags?: string[]
  remark?: string
  businessContext?: string
  rdProjectId?: string
  rdExpenseType?: RdExpenseType
  rdOriginalAmount?: MoneyInput
  rdAllocationMethod?: 'direct' | 'timesheet' | 'usage' | 'area' | 'quantity' | 'other'
  rdAllocationBasis?: string
  rdNotes?: string
  /** 分录已被人工调整过时，改方向/分类/付款方式/金额会重算分录；确认覆盖后置 true */
  confirmRegenerateVoucher?: boolean
  /** 已过账凭证一键改付款去向时，由后端自动红冲原凭证并按新去向重记过账 */
  autoReverseRepost?: boolean
}

// ===== Todo Types =====
export interface TodoItem {
  id: string
  key: string
  type: string
  /** 事项分类：filing/match/receipt/reimburse/billing/risk/other，后端派生；旧后端缺省时前端按 ruleId 兜底 */
  category?: string
  title: string
  description?: string
  desc?: string
  priority: 'high' | 'medium' | 'low'
  entryId?: string
  amount?: number
  done: boolean
  dismissed: boolean
  meta?: any
}

export interface TodoStateUpdate {
  done?: boolean
  dismissed?: boolean
}

// ===== Report Types =====
export interface MonthlyReportRequest {
  month: string
}

export interface MonthlyReportResponse {
  summary?: string
  monthIncome: Money
  monthExpense: Money
  netProfit: Money
  categoryBreakdown: Record<string, Money>
  taxSummary?: Record<string, unknown>
  availableCash?: Money
  pendingReceivables?: Money
  pendingPayables?: Money
  period?: string
  periodType?: 'month' | 'quarter' | 'year'
}

export interface ReportDiagnosisResponse {
  summary: string
}

export interface HealthScoreResponse {
  totalScore: number
  level: string
  dimensions: Record<string, number>
  suggestions: string[]
  taxRiskScore?: number
  taxRiskLevel?: string
  riskItems?: string[]
}

export interface VatAssistResponse {
  period: string
  periodType: 'month' | 'quarter'
  periodStart: string
  periodEnd: string
  taxpayerType: string
  policyVersion: string
  threshold: Money
  isBelowThreshold: boolean
  exemptionApplied: boolean
  goodsLaborSales: Money
  serviceSales: Money
  taxableBase: Money
  vatBeforeRelief: Money
  vatReduction: Money
  vatExemption: Money
  items: VatAssistItem[]
  totalVatPayable: Money
  totalSales: Money
  cityMaintenanceTax: Money
  educationSurcharge: Money
  localEducationSurcharge: Money
  surchargeTotal: Money
  filingFields: VatFilingField[]
  warnings: string[]
  blockingIssues: string[]
  readyToFile: boolean
  sourceEntryCount: number
}

export interface VatAssistItem {
  entryId: string
  occurDate: string
  title: string
  salesCategory: 'goods_labor' | 'services'
  invoiceType: string
  invoiceNumber: string
  salesAmount: Money
}

export interface VatFilingField {
  code: string
  section: string
  label: string
  amount: Money
  editable: boolean
}

export interface CitAssistResponse {
  period: string
  periodStart: string
  periodEnd: string
  cumulativeRevenue: Money
  cumulativeOperatingCost: Money
  cumulativeProfit: Money
  actualProfit: Money
  standardTax: Money
  taxReduction: Money
  estimatedTax: Money
  smallMicroEligibility: 'eligible' | 'ineligible' | 'undetermined'
  employeeAverage: Money | null
  assetAverage: Money | null
  filingFields: CitFilingField[]
  blockingIssues: string[]
  warnings: string[]
  readyToFile: boolean
  policyVersion: string
  assetTotalStart: Money
  assetTotalEnd: Money
  operatingCostConfirmed: boolean
  totalProfitConfirmed: boolean
  assetLoss: boolean | null
  companyTaxProfileComplete: boolean
  beginnerReady: boolean
}

export interface CitFilingField {
  line: string
  label: string
  amount: Money
  editable: boolean
  source: string
}

export interface ArchiveChecklistResponse {
  month: string
  items: ArchiveItem[]
  compliantCount: number
  missingCount: number
  totalCount: number
}

export interface ArchiveItem {
  date: string
  summary: string
  amount: Money
  docType: string
  compliance: string
  riskTag?: string
  note?: string
}

// ===== Balance Sheet Types =====
export interface BalanceSheetItem {
  accountCode: string
  accountName: string
  currentAmount: Money
  previousAmount?: Money
}

export interface BalanceSheetResponse {
  month: string
  periodType?: 'month' | 'quarter'
  period?: string
  assets: BalanceSheetItem[]
  liabilities: BalanceSheetItem[]
  equity: BalanceSheetItem[]
  totalAssets: Money
  totalLiabilities: Money
  totalEquity: Money
  isBalanced: boolean
}

// ===== Cash Flow Types =====
export interface CashFlowItem {
  category: string
  description: string
  inflow: Money
  outflow: Money
  netAmount: Money
}

export interface CashFlowResponse {
  month: string
  periodType?: 'month' | 'quarter'
  period?: string
  operating: CashFlowItem[]
  investing: CashFlowItem[]
  financing: CashFlowItem[]
  operatingNet: Money
  investingNet: Money
  financingNet: Money
  netIncrease: Money
  openingCash: Money
  endingCash: Money
  balanceSheetCash: Money
  classificationDifference: Money
  cashRollForwardDifference: Money
  balanceSheetCashDifference: Money
  classificationBalanced: boolean
  cashRollForwardBalanced: boolean
  balanceSheetCashBalanced: boolean
}

// ===== Income Statement Types =====
export type IncomeStatementViewMode = 'summary' | 'quarterly'

export interface IncomeStatementColumn {
  key: string
  label: string
}

export interface IncomeStatementRow {
  key: string
  label: string
  level: number
  values: Record<string, Money>
  isSubtotal: boolean
}

export interface IncomeStatementVatSummary {
  /** 不含税收入（凭证行 5001/5051 净额） */
  netSales: Money
  /** 已计提增值税（凭证行 2221026 净额） */
  vatAccrued: Money
  /** 系统测算的待纳税调增合计（补票后可转回） */
  pendingCitAdjustment: Money
  /** 待纳税调增事项笔数 */
  pendingCitItems: number
}

export type IncomeStatementBasis = 'posting' | 'business'

export interface IncomeStatementBackfillImpact {
  count: number
  amount: Money
  periods: Array<{ originalPeriod: string; count: number; amount: Money }>
}

export interface IncomeStatementResponse {
  month: string
  viewMode: IncomeStatementViewMode
  companyName?: string
  columns: IncomeStatementColumn[]
  rows: IncomeStatementRow[]
  totals: Record<string, Record<string, Money>>
  vatSummary?: IncomeStatementVatSummary
  /** posting=法定口径（按记账期间）；business=管理口径（按业务日期） */
  basis?: IncomeStatementBasis
  /** 本期补记：有多少笔业务的归属月份不是本期 */
  backfillImpact?: IncomeStatementBackfillImpact
}

// ===== Voucher Types =====
export interface VoucherEntry {
  accountCode: string
  accountName: string
  amount: Money
}

export interface VoucherDraft {
  voucherDate: string
  voucherNumber?: string
  summary: string
  debitEntries: VoucherEntry[]
  creditEntries: VoucherEntry[]
  attachmentCount: number
}

export interface VoucherGenerateResponse {
  vouchers: VoucherDraft[]
}

// ===== Fixed Asset Types（P1 WP-B 现代化：卡片-凭证外键 + 状态机）=====
export interface FixedAssetCategory {
  code: string
  name: string
  defaultLifeMonths: number
  lifeBasis: string
  lifeMinMonths: number
  lifeMaxMonths: number
  oneTimeAllowed: boolean
  expenseAccount: { code: string; name: string }
}

export interface FixedAsset {
  id: string
  code: string
  name: string
  category: string
  categoryName: string
  acquisitionType: 'purchase' | 'self_built' | 'investor_contribution' | 'donation'
  acquisitionDate: string
  availableForUseDate: string
  originalValue: Money
  residualValue: Money
  usefulLifeMonths: number
  depreciationMethod: 'straight_line' | 'one_time'
  monthlyDepreciation: Money
  accumulatedDepreciation: Money
  netValue: Money
  remainingMonths: number
  lastDepreciatedPeriod?: string
  depreciationEndPeriod?: string
  status: string
  sourceEntryId?: string
  departmentId?: string
  custodian?: string
  location?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
  disposedOn?: string
  disposalType?: string
  createdAt: string
  updatedAt: string
}

export interface FixedAssetListResponse {
  items: FixedAsset[]
  total: number
  page: number
  pageSize: number
}

export interface FixedAssetCreateRequest {
  name: string
  category: string
  acquisitionType?: 'purchase' | 'self_built' | 'investor_contribution' | 'donation'
  acquisitionDate: string
  availableForUseDate: string
  originalValue: number
  residualValue?: number
  residualRate?: number
  usefulLifeMonths: number
  depreciationMethod?: 'straight_line' | 'one_time'
  departmentId?: string
  custodian?: string
  location?: string
  paymentMethod?: 'company_account' | 'account_payable' | 'personal_advance'
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
}

export interface FixedAssetFromEntryRequest extends FixedAssetCreateRequest {
  sourceEntryId: string
}

export interface FixedAssetEntryPrefill {
  sourceEntryId: string
  existingAssetId?: string
  name: string
  acquisitionDate: string
  availableForUseDate: string
  originalValue: number
  suggestedCategory: string
}

export interface FixedAssetDepreciationDuePeriod {
  period: string
  amount: number
  count: number
}

export interface FixedAssetFromEntryResponse {
  asset: FixedAsset
  duePeriods: FixedAssetDepreciationDuePeriod[]
  dueTotalAmount: number
}

export interface FixedAssetUpdateRequest {
  name?: string
  category?: string
  depreciationMethod?: 'straight_line' | 'one_time'
  usefulLifeMonths?: number
  residualValue?: number
  departmentId?: string
  custodian?: string
  location?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
}

export interface FixedAssetDisposal {
  id: string
  assetId: string
  assetCode: string
  assetName: string
  disposalType: 'sale' | 'scrap' | 'donate' | 'transfer_out'
  disposalDate: string
  proceedsAmount: Money
  outputVatAmount: Money
  accumulatedDepreciationSnapshot: Money
  netBookValueSnapshot: Money
  gainLossAmount: Money
  status: string
  reason?: string
  journalEntryId?: string
  initiatedBy: string
  approvedBy?: string
  createdAt: string
}

export interface FixedAssetDisposalCreateRequest {
  assetId: string
  disposalType: 'sale' | 'scrap' | 'donate' | 'transfer_out'
  disposalDate: string
  proceedsAmount?: number
  outputVatAmount?: number
  reason?: string
}

export interface FixedAssetReconciliation {
  applicable: boolean
  balanced: boolean
  cardCostTotal: Money
  cardDepreciationTotal: Money
  ledger1601: Money
  ledger1602: Money
  schedulePostedTotal: Money
  costDiff: Money
  depreciationDiff: Money
  scheduleDiff: Money
  cardCount: number
  details: {
    assetId: string
    assetCode: string
    assetName: string
    status: string
    originalValue: Money
    accumulatedDepreciation: Money
    sourceEntryId?: string
  }[]
}

// ===== Copilot Types =====
export interface CopilotChatRequest {
  messages: { role: string; content: string }[]
  context?: Record<string, unknown>
  mode?: 'agent' | 'tax_advisor' | 'secretary' | 'coach'
}

export interface CopilotChatResponse {
  reply: string
  bookPayload?: Record<string, unknown>
  disclaimer?: string
}

// ===== Comparison Types =====
export interface ComparisonItem {
  category: string
  base_amount: Money
  compare_amount: Money
  change_percent: Money | null
}

export interface ComparisonResponse {
  base_month: string
  compare_month: string
  base_income: Money
  base_expense: Money
  base_profit: Money
  compare_income: Money
  compare_expense: Money
  compare_profit: Money
  income_change_percent: Money | null
  expense_change_percent: Money | null
  profit_change_percent: Money | null
  category_breakdown: ComparisonItem[]
}

// ===== Ledger Types =====

// ===== 通用归集（aggregation）=====
// 权威来源：backend/models/schemas.py:880-1007、backend/services/aggregation_service.py。
// 金额字段到达本层时已由 apiClient 响应拦截器（client.ts MONEY_KEY）转为 number；
// 唯独 report-line-rules compare 的 baseline/configured/delta 不命中该正则，
// 由 api/aggregation.ts::compareReportLineRules 集中 Number() 一次，组件层直接用。

/** 后端 AccountingError.detail() 的形状（core/errors.py:20-29） */
export interface AccountingErrorDetailShape {
  code: string
  message: string
  context?: Record<string, unknown>
  err_sig?: string
  trace_domain?: string
}

/**
 * 后端 GET /meta 下发的标签对（{value,label}[]），前端禁止硬编码这些中文。
 *
 * ⚠️ 教训记录（2026-03，曾加过 `[key: string]: unknown`，已移除）：
 * 当时以为「加索引签名就能让 `LabelOption[]` 直传 NSelect、免去页面侧到处
 * `.map(o => ({label, value}))`。实测（vue-tsc 3.2.8 / naive-ui 2.44）该假设**不成立**：
 * `SelectMixedOption = SelectBaseOption | SelectGroupOption | SelectIgnoredOption`，
 * `SelectIgnoredOption` 要求 `type: 'ignored' | 'render'`，而 `unknown` 与 `string` 不兼容，
 * 所以**签名加了照样报 TS2322**，页面侧仍必须做 `.map()` 收敛。
 *
 * 即便利为零，代价却是真的：宽索引签名会让本类型的**对象字面量多余属性检查失效**
 * ——`{ value, label, lable }` 这类拼写笔误不再被TS2353 拦住，且该类型会静默兼容
 * 任意形参类型。故按「不加无收益、只增风险」的原则移除，恢复严格检查。
 *
 * 页面侧统一用局部 `toSelectOptions(options: LabelOption[]): SelectOption[]` 收敛
 * （参考 `pages/aggregation/rules.vue` 的同名函数），不要把索引签名加回来。
 */
export interface LabelOption {
  value: string
  label: string
}

export interface AggregationMeta {
  kinds: LabelOption[]
  methods: LabelOption[]
  memberStatuses: LabelOption[]
}

/** 维度类型，对应 AggregationDimensionCreate.kind */
export type AggregationDimensionKind = 'project' | 'department' | 'customer' | 'supplier' | 'custom'
/** 归集对象状态，MEMBER_STATUSES 有 4 个值 */
export type AggregationMemberStatus = 'draft' | 'active' | 'completed' | 'archived'
/** 维度状态：维度只有 active/archived 两态（无 draft/completed） */
export type AggregationDimensionStatus = 'active' | 'archived'
/** 分摊方法，ALLOCATION_METHODS 有 8 个值 */
export type AllocationMethod =
  | 'direct' | 'timesheet' | 'usage' | 'area' | 'quantity' | 'revenue' | 'headcount' | 'other'
/** 凭证行方向，ck_journal_line_direction 约束为 debit/credit */
export type JournalDirection = 'debit' | 'credit'
/** 凭证状态：待归集清单含这三种（aggregation_service.PENDING_STATUSES） */
export type VoucherStatus = 'draft' | 'pending_review' | 'posted'
/** 归集三态，_line_state() 的返回值 */
export type AggregationLineState = 'unallocated' | 'partial' | 'allocated'
/** GET /pending 的 state 入参：all 之外与三态同名 */
export type AggregationPendingStateParam = 'all' | AggregationLineState
/** 归集口径：期间 / 业务日期 */
export type AggregationBasis = 'period' | 'occurDate'
/** 规则的方向过滤条件；空表示不限 */
export type AggregationRuleDirection = JournalDirection

/** GET/POST/PATCH /dimensions 的单对象 */
export interface AggregationDimension {
  id: string
  code: string
  name: string
  /** 后端 schema 声明为 str（Literal 仅在入参收紧），故此处用 string */
  kind: string
  status: string
  createdAt: string
  updatedAt: string
}

// ===== Intangible Asset Types =====
export interface IntangibleCategory {
  code: string
  name: string
  defaultLifeMonths: number
  lifeBasis: string
  expenseAccount: { code: string; name: string }
  remindExpiry: boolean
  certLabel: string
  lifeMinMonths: number
  lifeMaxMonths: number
}

export interface IntangibleAsset {
  id: string
  code: string
  name: string
  category: string
  categoryName: string
  acquisitionType: 'purchase' | 'self_developed' | 'investor_contribution' | 'donation'
  acquisitionDate: string
  availableForUseDate: string
  originalValue: Money
  residualValue: Money
  usefulLifeMonths: number
  amortizationMethod: string
  accumulatedAmortization: Money
  netValue: Money
  remainingMonths: number
  lastAmortizedPeriod?: string
  amortizationEndPeriod?: string
  status: string
  sourceEntryId?: string
  legalCertNo?: string
  grantOrg?: string
  expiryDate?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
  departmentId?: string
  custodian?: string
  disposedOn?: string
  disposalType?: string
  createdAt: string
  updatedAt: string
}

export interface AggregationDimensionInput {
  code: string
  name: string
  kind: AggregationDimensionKind
}

export interface AggregationDimensionUpdate {
  name?: string
  status?: AggregationDimensionStatus
}

/** GET/POST/PATCH /dimensions/{id}/members 的单对象 */
export interface AggregationMember {
  id: string
  dimensionId: string
  code: string
  name: string
  startDate: string
  endDate?: string | null
  status: string
  metadata: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

/** POST members 必填 startDate；status 默认 active */
export interface AggregationMemberInput {
  code: string
  name: string
  startDate: string
  endDate?: string | null
  status?: AggregationMemberStatus
  metadata?: Record<string, unknown>
}

export interface AggregationMemberUpdate {
  name?: string
  endDate?: string | null
  status?: AggregationMemberStatus
}

/** serialize_allocation() 的返回（金额恒为正：row.amount） */
export interface AggregationAllocation {
  id: string
  journalLineId: string
  dimensionId: string
  memberId: string
  amount: number
  allocationRate: number
  allocationMethod: string
  allocationBasis?: string | null
  /** manual=人工归集，rule=规则命中。apply_rules 绝不覆盖 manual */
  source: string
  ruleId?: string | null
}

/** PUT /lines/{id}/allocations 的入参。字段名是 allocationMethod/allocationBasis，不是 method/basis */
export interface AggregationAllocationInput {
  memberId: string
  amount: number
  allocationMethod: AllocationMethod
  allocationBasis?: string | null
}

/** serialize_rule() 的返回。注意有 status 字段（list_rules 只返回 active） */
export interface AggregationRule {
  id: string
  dimensionId: string
  memberId: string
  priority: number
  accountPrefix?: string | null
  direction?: string | null
  keyword?: string | null
  counterparty?: string | null
  rate: number
  method: string
  basis?: string | null
  status: string
}

/** AggregationRuleInput：入参**没有** status（后端强制写 active） */
export interface AggregationRuleInput {
  memberId: string
  priority?: number
  accountPrefix?: string | null
  direction?: AggregationRuleDirection | null
  keyword?: string | null
  counterparty?: string | null
  rate?: number | string
  method?: AllocationMethod
  basis?: string | null
}

/** 待归集清单的单行。amount/allocatedAmount/remainingAmount 是**带符号**的（贷行为负） */
export interface AggregationPendingItem {
  entryId: string
  lineId: string
  voucherNo?: string | null
  summary: string
  status: string
  occurredOn: string
  periodStart: string
  accountCode: string
  accountName: string
  direction: JournalDirection
  amount: number
  allocatedAmount: number
  remainingAmount: number
  state: AggregationLineState
  allocations: AggregationAllocation[]
}

/** 归集报表的成员维度行（aggregation_report 的 buckets） */
export interface AggregationReportItem {
  memberId: string
  code: string
  name: string
  status: string
  allocatedAmount: number
  allocatedLineCount: number
}

/** 报表口径规则的两种报表 */
export type ReportKind = 'profit' | 'balance'
/** 规则级方向语义（debit/credit/net），与 pattern.sign 的 ±1 不是一回事 */
export type ReportLineSign = 'debit' | 'credit' | 'net'
export type ReportLinePatternMode = 'prefix' | 'exact' | 'range'
export type ReportLineRuleStatus = 'active' | 'archived'

/** 声明式科目匹配。mode='prefix' 时 value 不得短于 4 位（后端强校验） */
export interface ReportLinePattern {
  mode: ReportLinePatternMode
  value: string
  sign: 1 | -1
}

export interface ReportLinePatternInput {
  mode: ReportLinePatternMode
  value: string
  sign?: 1 | -1
}

export interface ReportLineRule {
  id: string
  report: string
  lineCode: string
  lineName: string
  patterns: ReportLinePattern[]
  sign: string
  status: string
  effectiveFrom?: string | null
  effectiveTo?: string | null
  createdBy?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface ReportLineRuleInput {
  lineCode: string
  lineName?: string | null
  patterns: ReportLinePatternInput[]
  sign?: ReportLineSign
  status?: ReportLineRuleStatus
  /** 必填，后端 _parse_date(required=True) */
  effectiveFrom: string
  effectiveTo?: string | null
}

/**
 * 双轨对比的单行差异。
 * ⚠️ baseline/configured/delta 后端是 money_text() 字符串且**不命中** client.ts 的
 * MONEY_KEY 正则，api/aggregation.ts 已统一 Number()，此处声明为 number。
 */
export interface ReportLineDiff {
  lineCode: string
  lineName: string
  baseline: number
  configured: number
  delta: number
  hasRule: boolean
  /** true = 配置了但现有报表没有此行（仅 orphan 分支带该字段） */
  orphan?: boolean
}

export interface IntangibleAssetCreateRequest {
  name: string
  category: string
  acquisitionType: 'purchase' | 'self_developed' | 'investor_contribution' | 'donation'
  acquisitionDate: string
  availableForUseDate: string
  originalValue: number
  residualValue?: number
  residualRate?: number
  usefulLifeMonths: number
  legalCertNo?: string
  grantOrg?: string
  expiryDate?: string
  departmentId?: string
  custodian?: string
  paymentMethod?: 'company_account' | 'account_payable' | 'personal_advance'
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
  taxLifeMonths?: number
}

export interface IntangibleAssetFromEntryRequest extends IntangibleAssetCreateRequest {
  sourceEntryId: string
}

export interface IntangibleAssetEntryPrefill {
  sourceEntryId: string
  existingAssetId?: string
  name: string
  acquisitionDate: string
  availableForUseDate: string
  originalValue: number
  suggestedCategory: string
}

export interface IntangibleAmortizationDuePeriod {
  period: string
  amount: number
  count: number
}

export interface IntangibleAssetFromEntryResponse {
  asset: IntangibleAsset
  duePeriods: IntangibleAmortizationDuePeriod[]
  dueTotalAmount: number
}

export interface IntangibleAssetUpdateRequest {
  name?: string
  usefulLifeMonths?: number
  residualValue?: number
  legalCertNo?: string
  grantOrg?: string
  expiryDate?: string
  departmentId?: string
  custodian?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
  taxLifeMonths?: number
}

export interface IntangibleAssetListResponse {
  items: IntangibleAsset[]
  total: number
  page: number
  pageSize: number
}

export interface AmortizationScheduleRow {
  id: string
  assetId: string
  assetName: string
  periodStart: string
  sequenceNo: number
  amount: Money
  accumulatedBefore: Money
  accumulatedAfter: Money
  status: 'planned' | 'posted' | 'reversed' | 'skipped'
  journalEntryId?: string
  planVersion: number
}

export interface AmortizationRunResult {
  period: string
  dryRun: boolean
  totalAmount: Money
  processedCount: number
  skippedCount: number
  postedCount: number
  pendingReviewCount: number
  items: {
    assetId: string
    assetName: string
    periodStart: string
    sequenceNo: number
    amount: Money
    status: string
    journalEntryId?: string
  }[]
}

export interface DepreciationCatchUpResult {
  throughPeriod: string
  dryRun: boolean
  periods: {
    period: string
    totalAmount: Money
    processedCount: number
    skippedCount: number
    postedCount: number
    pendingReviewCount: number
  }[]
  totalAmount: Money
  processedCount: number
  skippedCount: number
  postedCount: number
  pendingReviewCount: number
  items: AmortizationRunResult['items']
}

export type AmortizationCatchUpResult = DepreciationCatchUpResult

export interface IntangibleDisposal {
  id: string
  assetId: string
  assetCode: string
  assetName: string
  disposalType: 'sale' | 'scrap' | 'expire' | 'donate' | 'transfer_out'
  disposalDate: string
  proceedsAmount: Money
  outputVatAmount: Money
  accumulatedAmortizationSnapshot: Money
  netBookValueSnapshot: Money
  gainLossAmount: Money
  status: string
  reason?: string
  journalEntryId?: string
  initiatedBy: string
  approvedBy?: string
  createdAt: string
}

export interface IntangibleDisposalCreateRequest {
  assetId: string
  disposalType: 'sale' | 'scrap' | 'expire' | 'donate' | 'transfer_out'
  disposalDate: string
  proceedsAmount?: number
  outputVatAmount?: number
  reason?: string
}

export interface IntangibleImpairmentTest {
  id: string
  assetId: string
  assetCode: string
  assetName: string
  testPeriod: string
  testDate: string
  triggerType: string
  triggerLabel: string
  indicatorDescription: string
  carryingAmount: Money
  recoverableAmount: Money
  conclusion: 'no_impairment' | 'impairment_noted'
  provisionAmount: Money
  evidenceRef?: string
  assessedBy: string
  reviewedBy?: string
  reviewedAt?: string
  createdAt: string
  suggestion?: string
}

export interface IntangibleImpairmentTestCreateRequest {
  assetId: string
  testPeriod: string
  testDate: string
  triggerType: 'tech_obsolete' | 'legal_invalid' | 'market_decline' | 'idle_long' | 'other'
  indicatorDescription: string
  recoverableAmount: number
  evidenceRef?: string
}

export interface IntangibleAssetChangeResult {
  asset: IntangibleAsset
  effectivePeriod: string
  planVersion: number
  remainingMonths: number
  monthlyAmount: string
}

export interface A105080PreviewItem {
  assetId: string
  assetCode: string
  assetName: string
  category: string
  originalValue: Money
  amortizationThisYear: Money
  amortizedMonthsThisYear: number
  accumulatedAmortization: Money
  netBookValue: Money
  taxLifeMonths: number
  taxAmortizationThisYear: Money
  adjustmentAmount: Money
}

export interface A105080Preview {
  year: number
  items: A105080PreviewItem[]
  totals: {
    amortizationThisYear: Money
    taxAmortizationThisYear: Money
    adjustmentAmount: Money
  }
  assumptions: string[]
}

// ===== Setup / Onboarding Types =====

export type SetupView = 'boss' | 'accounting'

/** 就绪度的五种状态：locked 表示被前置项挡住，不是「没做」 */
export type SetupStepStatus = 'todo' | 'doing' | 'done' | 'waived' | 'locked'

export interface SetupStepProgress {
  current: number
  target: number
  unit: string
}

export interface SetupStep {
  key: string
  title: string
  hint: string
  status: SetupStepStatus
  blockedBy: string | null
  detail: string | null
  deepLink: string
  /** 信息项（如系统预置的科目表）不进分母 */
  informational: boolean
  progress: SetupStepProgress | null
  waivedReason?: string | null
}

export interface SetupReadiness {
  companyId: string
  view: SetupView
  fiscalYear: number
  completed: number
  total: number
  ready: boolean
  generatedAt: string
  items: SetupStep[]
}

export interface DemoBookResponse {
  companyId: string
  name: string
  entries: number
  statements: number
  durationMs: number
  /** true 表示复用了已存在的演示账套，没有重新生成 */
  reused: boolean
}

export interface DemoBookDeleteResponse {
  deleted: boolean
  companyId: string
  /** 删除当前账套时后端会先切到这本，前端需要跟着换 token */
  nextCompanyId: string | null
}
