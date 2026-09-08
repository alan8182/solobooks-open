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

export interface UserProfile {
  user_id: string
  nickname: string
  avatar: string
  phone: string
  phone_bound: boolean
  wechat_bound: boolean
  companies: CompanyInfo[]
  current_company_id: string
  server_time: string
}

export interface CompanyInfo {
  company_id: string
  name: string
  type?: string
  taxpayerType?: string
  startDate?: string
  taxId?: string
  smallMicroEnterprise?: boolean
  restrictedIndustry?: boolean
  employeeCount?: number
  citPrepaymentMethod?: string
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
  startDate?: string
  taxId?: string
  smallMicroEnterprise?: boolean
  restrictedIndustry?: boolean
  employeeCount?: number
  citPrepaymentMethod?: string
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
}

// ===== Todo Types =====
export interface TodoItem {
  id: string
  key: string
  type: string
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
  summary: string
  monthIncome: Money
  monthExpense: Money
  netProfit: Money
  categoryBreakdown: Record<string, Money>
  taxSummary?: Record<string, unknown>
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

export interface TaxSummaryResponse {
  period: string
  periodType: 'month' | 'quarter'
  periodStart: string
  periodEnd: string
  totalSales: Money
  accountingProfit: Money
  taxableIncomeEstimate: Money
  estimatedVat: Money
  estimatedCit: Money
  vatExemption: Money
  bookExpenses: Money
  totalDeductible: Money
  totalNonDeductible: Money
  pendingDeductibility: Money
  riskCount: number
  filingDeadlines: TaxDeadlineItem[]
  blockingIssues: string[]
  warnings: string[]
  readyToFile: boolean
  policyVersion: string
  reminders: string[]
}

export interface TaxDeadlineItem {
  taxType: string
  filingPeriod: string
  dueDate: string
  status: 'upcoming' | 'due_soon' | 'due_today' | 'overdue'
  daysRemaining: number
  verified: boolean
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

export interface IncomeStatementResponse {
  month: string
  viewMode: IncomeStatementViewMode
  companyName?: string
  columns: IncomeStatementColumn[]
  rows: IncomeStatementRow[]
  totals: Record<string, Record<string, Money>>
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

// ===== Fixed Asset Types =====
export interface FixedAsset {
  id: string
  name: string
  originalValue: Money
  purchaseDate: string
  category: string
  usefulLife: number
  residualValueRate: number
  oneTimeDepreciation: boolean
  accumulatedDepreciation: number
  status: string
  lastDepreciationDate?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
  sourceEntryId?: string
}

export interface FixedAssetCreateRequest {
  name: string
  originalValue: number
  purchaseDate: string
  category: string
  usefulLife: number
  residualValueRate?: number
  oneTimeDepreciation?: boolean
  sourceEntryId?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
}

export interface FixedAssetUpdateRequest {
  status?: string
  accumulatedDepreciation?: Money
  lastDepreciationDate?: string
  rdProjectId?: string
  rdUseRate?: number
  rdAllocationBasis?: string
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
