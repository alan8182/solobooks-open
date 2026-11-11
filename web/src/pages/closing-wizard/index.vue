<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NDivider,
  NDropdown,
  NGrid,
  NGridItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NStep,
  NSteps,
  NText,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import { formatMoney } from '@/utils/format'
import GuideTrigger from '@/components/business/GuideTrigger.vue'
import {
  amountClassForDirection,
  invoiceDisplayAmount,
  formatDispositionTag,
  invoiceRowStatus,
  formatDispositionExplanation,
} from '@/utils/closingWizard'
import {
  deriveMissingClosingImports,
  filterStatementsInMonth,
  formatMissingImportLabels,
} from '@/utils/closingImportGaps'
import { useEntryStore } from '@/stores/entry'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import apiClient from '@/api/client'
import { useBackfillConfirm } from '@/composables/backfillConfirm'
import { useAuthStore } from '@/stores/auth'
import { getClosingReadiness, canBatchPostDrafts, deriveClosingTasks, summarizeFiveChecks, countOtherPendingReadiness, type ClosingNotice, type ClosingNoticeRecord, type ClosingReadinessItem, type ClosingTaskAction } from '@/utils/closingReadiness'
import { getFilingObligations, type FilingObligation, type FilingObligations } from '@/api/accounting'
import { loadAccountMapping } from '@/utils/accounting'
import ReopenPeriodDialog from './ReopenPeriodDialog.vue'
import AskAiPanel from './AskAiPanel.vue'
import { usePayrollFlow } from './composables/usePayrollFlow'
import { useClosingImports } from './composables/useClosingImports'
import { useClosingFlow } from './composables/useClosingFlow'
import { useBatchPost } from './composables/useBatchPost'
import { useReconciliationWorkspace } from './composables/useReconciliationWorkspace'
import FileDropzone from '@/components/business/FileDropzone.vue'
import MailInboxCard from '@/components/business/MailInboxCard.vue'
import { SoloDrawer, SoloEmpty } from '@/components/ui'

// 「重新上传/重新导入」按钮复用拖拽区的文件选择器
const wechatDropzone = ref<InstanceType<typeof FileDropzone> | null>(null)
const invoiceDropzone = ref<InstanceType<typeof FileDropzone> | null>(null)
const showMailInbox = ref(false)
const showDiagnosisModal = ref(false)

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

function openClosingCopilot() {
  const prompt = [
    `请检查 ${targetMonth.value} 月度结账。`,
    '先运行 run_close_checks，只按工具返回的 snapshotHash、findingId、金额和权威 ID 说明阻断项；',
    '需要处理时只创建待确认的修复草稿，不要声称已经入账或结账。',
    '最终锁账由我回到月度结账向导确认。',
  ].join('')
  router.push({ path: '/copilot', query: { q: prompt } })
}

/* 行内分类菜单给足宽度，避免长分类名被省略；限高后由 virtual-list 滚动。 */
const CATEGORY_MENU_PROPS = { style: '--n-height: 220px; width: 320px; max-width: calc(100vw - 16px)' }

// 研发类科目要求先选「研发项目 + 费用类别」才能提交，项目没建好就会卡在弹窗里。
// 这里给一个直达研发项目页的出口。弹窗是 Teleport 到 body 的，页面被 KeepAlive
// 缓存后切走，弹窗 DOM 不会跟着走——所以必须先关掉再跳，否则新页面上会压着一个僵尸弹窗。
const goCreateRdProject = () => {
  showManualDialog.value = false
  showPairCategoryDialog.value = false
  showEditVoucherDialog.value = false
  router.push({ path: '/rd-expenses', query: { tab: 'projects' } })
}
const entryStore = useEntryStore()
const authStore = useAuthStore()
const { confirmDelete } = useDeleteConfirm()
const currentCompany = computed(
  () => authStore.companies?.find((company) => company.company_id === authStore.currentCompanyId) ?? null,
)
// 导入/对账遇到「所属期间已关账」时的补记确认（不静默换期）
const backfillConfirm = useBackfillConfirm()

const currentStep = ref(1)

// 步骤变化同步到 URL：否则任何 loadAllData 刷新都会按旧的 query.step 把用户弹回第一步
watch(currentStep, (step) => {
  const route = router.currentRoute.value
  if (String(route.query.step || '') === String(step)) return
  router.replace({ query: { ...route.query, month: targetMonth.value, step: String(step) } })
})
const loading = ref(false)
const periodCloseChecks = ref<any | null>(null)
// 结账按月、申报按税种：向导要告诉用户「这个月结完，本期该报什么」
const filingInfo = ref<FilingObligations | null>(null)
// Target Month options: every month from the company's book start month to the current month
const now = new Date()
const currentYear = now.getFullYear()
const currentMonth = now.getMonth() // 0-11
const availablePeriods = ref<{ month: string; status: string | null }[]>([])
// 账套启用月：结账月份不能早于它，后端会以 PERIOD_BEFORE_BOOK_START 拒绝
function bookStartMonthKey(): string | null {
  const match = String(currentCompany.value?.startDate || '').match(/^(\d{4})-(0[1-9]|1[0-2])/)
  return match ? `${match[1]}-${match[2]}` : null
}
const defaultPrevMonth = new Date(currentYear, currentMonth - 1, 1)
const defaultPrevMonthKey = `${defaultPrevMonth.getFullYear()}-${String(defaultPrevMonth.getMonth() + 1).padStart(2, '0')}`
// 启用月就是本月的公司：默认上月会落在启用前，直接从启用月开始
const defaultMonthKey = (() => {
  const startKey = bookStartMonthKey()
  return startKey && startKey > defaultPrevMonthKey ? startKey : defaultPrevMonthKey
})()
const targetMonth = ref(defaultMonthKey)
// 待办/关账任务跳入时按 query.month 定位月份（YYYY-MM），step/statement 见 loadAllData
const queryMonth = String(router.currentRoute?.value?.query?.month || '')
const hasQueryMonth = /^\d{4}-\d{2}$/.test(queryMonth)
if (hasQueryMonth) targetMonth.value = queryMonth
let initialPeriodResolved = false
// 期次下拉 = 账套启用月至今 ∪ 有数据的月份，保证启用后无业务的首月也能进入结账流程
const monthOptions = computed(() => {
  const labelOf = (val: string) => {
    const [yearStr, monthStr] = val.split('-')
    return `${yearStr}年${parseInt(monthStr, 10)}月`
  }
  const map = new Map<string, { label: string; value: string }>()
  const addMonth = (d: Date) => {
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    map.set(val, { label: labelOf(val), value: val })
  }

  const startKey = bookStartMonthKey()
  if (startKey) {
    const startYear = Number(startKey.slice(0, 4))
    const startMonth = Number(startKey.slice(5, 7)) - 1
    const endMonth = new Date(currentYear, currentMonth, 1)
    for (let d = new Date(startYear, startMonth, 1); d <= endMonth; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
      addMonth(d)
    }
  } else {
    // 账套资料尚未加载或未填写启用日期时，保留原有最近 6 个月的安全兜底
    for (let i = 0; i < 6; i++) {
      addMonth(new Date(currentYear, currentMonth - i, 1))
    }
  }
  for (const item of availablePeriods.value) {
    if (/^\d{4}-\d{2}$/.test(item.month)) {
      map.set(item.month, { label: labelOf(item.month), value: item.month })
    }
  }
  // 显式深链的月份照旧保留；但默认选中的上月若落在启用前，不能混进候选，
  // 否则 getEarliestOpenMonth 会把它当成「最早未结账月」选中。
  if (targetMonth.value && !map.has(targetMonth.value) && (!startKey || targetMonth.value >= startKey || hasQueryMonth)) {
    map.set(targetMonth.value, { label: labelOf(targetMonth.value), value: targetMonth.value })
  }
  return [...map.values()].sort((a, b) => b.value.localeCompare(a.value))
})

function getEarliestOpenMonth(): string {
  const currentKey = `${currentYear}-${String(currentMonth + 1).padStart(2, '0')}`
  const statuses = new Map(availablePeriods.value.map(({ month, status }) => [month, status]))
  const startKey = bookStartMonthKey()
  // 账套启用月还没读到（月份下拉只剩最近 6 个月的兜底）时回落到上月，
  // 否则会按兜底列表取到半年前，用户点开向导却停在老账期。
  if (!startKey) {
    return statuses.get(defaultPrevMonthKey) === 'closed' ? currentKey : defaultPrevMonthKey
  }
  const candidates = monthOptions.value
    .map(option => option.value)
    .filter(month => month >= startKey && month <= currentKey)
    .sort((a, b) => a.localeCompare(b))
  return candidates.find(month => statuses.get(month) !== 'closed') || currentKey
}

// 启用日期可能晚于页面挂载到达（AppLayout 挂载后才拉 profile）：
// 先停在启用前时补一次定位并重载，否则首屏会一直显示不可结账的月份。
watch(
  () => currentCompany.value?.startDate,
  () => {
    if (hasQueryMonth) return
    const startKey = bookStartMonthKey()
    if (!startKey || targetMonth.value >= startKey) return
    targetMonth.value = getEarliestOpenMonth()
    if (initialPeriodResolved) loadAllData()
  },
)

const closedQuarter = computed(() => {
  if (!targetMonth.value) return ''
  const [yearStr, monthStr] = targetMonth.value.split('-')
  const monthNum = parseInt(monthStr, 10)
  const q = Math.ceil(monthNum / 3)
  return `${yearStr}-Q${q}`
})

const isQuarterEndMonth = computed(() => {
  if (!targetMonth.value) return false
  const monthStr = targetMonth.value.split('-')[1]
  return ['03', '06', '09', '12'].includes(monthStr)
})

// Real Unmatched Data lists
// ponytail: explicit detection of zero-transaction periods to prevent false "ready to close" reports
const confirmZeroActivity = ref(false)

// 期间归集以记账期间为准：补记凭证的业务日期在上个月，按 occurDate 过滤会让它
// 在自己的记账期间里消失（与凭证列表同一处坑）。
const entryPeriod = (e: any): string =>
  String(e.period || '').slice(0, 7) || String(e.occurDate || '').slice(0, 7)

const monthEntries = computed(() =>
  entryStore.activeEntries.filter(e =>
    entryPeriod(e) === targetMonth.value &&
    (e as any).entryType !== 'closing' &&
    !(e as any).isClosingTransfer
  )
)
const monthStatements = computed(() =>
  filterStatementsInMonth(rawStatements.value, targetMonth.value)
)
const monthInvoices = computed(() =>
  invoicesList.value.filter(inv => inv.date?.startsWith(targetMonth.value))
)
const isZeroRecordMonth = computed(() =>
  monthEntries.value.length === 0 &&
  monthStatements.value.length === 0 &&
  monthInvoices.value.length === 0
)
// 拖拽上传的校验失败提示（统一入口）
function onFileError(msg: string) {
  message.error(msg)
}

// 启用日期拦截是「账套配置问题」而非数据问题，一键直达编辑弹窗修正
function gotoAdjustStartDate() {
  const companyId = authStore.currentCompanyId
  router.push({ path: '/mine/companies', query: companyId ? { edit: companyId } : {} })
}

function apiErrorMessage(err: any, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string' && msg.trim()) return msg
  }
  const raw = err as { message?: unknown } | null
  if (typeof raw?.message === 'string' && raw.message.trim()) return raw.message
  return fallback
}

// 工资 6 连发流程（状态/请求/口径已拆入 composables/usePayrollFlow）
const {
  payrollAccrued,
  payrollPaid,
  payrollPendingReview,
  payrollSsPaid,
  payrollHfPaid,
  payrollTaxPaid,
  employees,
  showSalaryTable,
  payrollPaymentSuggestions,
  applyingSalaryStatementId,
  payrollHasSocial,
  payrollHasTax,
  hasBlockingPayroll,
  totalNetSalary,
  totalCompanyCost,
  loadSalarySheet,
  loadPayrollPaymentSuggestions,
  handleApplySalaryPayment,
  handleAccruePayroll,
  handlePayPayroll,
  handlePaySocial,
  handlePayHousingFund,
  handlePayTax,
} = usePayrollFlow({
  month: targetMonth,
  loading,
  entryStore,
  reloadAll: loadAllData,
})

// 三类导入流程（微信/发票/银行，状态与请求已拆入 composables/useClosingImports）
const {
  wechatBillRows,
  wechatBillSummary,
  wechatBillImported,
  wechatBillImporting,
  wechatImportMode,
  wechatBillFailures,
  wechatBillRecords,
  wechatBillRecordsSummary,
  loadWechatBillRecords,
  processWechatBillFile,
  removeWechatRow,
  confirmWechatBillImport,
  processInvoiceFile,
  handleDeleteInvoice,
  toggleArchiveMode,
  bankUploadResult,
  processBankFile,
} = useClosingImports({
  month: targetMonth,
  loading,
  entryStore,
  reloadAll: loadAllData,
})

// 邮件收件箱「导入」：附件构造 File 后路由到对应解析向导（与手动上传同一条链路，
// 解析预览后仍由用户确认入账；重复防线在业务层去重）
const INBOX_IMPORT_ROUTES: Record<string, (file: File) => unknown> = {
  invoice: processInvoiceFile,
  bank: processBankFile,
  wechat_bill: processWechatBillFile,
}
function handleInboxImport({ kind, file }: { kind: string; file: File }) {
  const route = INBOX_IMPORT_ROUTES[kind]
  if (route) route(file)
}

// 批量过账域（状态/请求已拆入 composables/useBatchPost）
const {
  showVoucherModal,
  voucherModalLoading,
  voucherPostingLoading,
  batchPostingLoading,
  batchPostConfirmVisible,
  batchPostWalkthrough,
  batchPostWalkthroughTotal,
  currentVoucherEntry,
  batchPostEntries,
  openVoucherModal,
  handleModalPostEntry,
  openBatchPostConfirm,
  startWalkthrough,
  startWalkthroughFromConfirm,
  confirmBatchPost,
  modalVoucherLines,
  modalDebitTotal,
  modalCreditTotal,
} = useBatchPost({
  month: targetMonth,
  entryPeriod,
  entryStore,
  reloadAll: loadAllData,
  errorMessage: apiErrorMessage,
})

// 对账工作台（AI 建议/配对/批量确认/分类编辑/处置/速记）已整体拆入
// composables/useReconciliationWorkspace.ts，按名解构保持模板兼容。
const {
  rawStatements,
  bankStatements,
  invoicesList,
  monthInvoiceRows,
  showInvoiceDetails,
  selectedInvoiceIds,
  selectedBank,
  matchedPairs,
  persistedMatchedPairs,
  filterUnmatchedData,
  loadAiSuggestions,
  loadReconciliationHealth,
  deletePair,
  matchUndoableEntries,
  undoMatchEntry,
  safeFloat,
  isItemAutoMatched,
  unmatchedInvoices,
  buildPersistedMatchedPairs,
  allUnmatchedEntries,
  unmatchedStatements,
  historicalUnmatchedStatements,
  reconciliationStats,
  reconciliationHealthSummary,
  hasBlockingReconciliationRisk,
  getAiSuggestion,
  hasExistingEntrySuggestion,
  isFallbackSuggestion,
  pairCategoryPreview,
  CATEGORY_OPTIONS,
  refreshCategoryOptions,
  categoryValueNatureLabel,
  filterCategory,
  renderCategoryLabel,
  rdProjectOptions,
  rdExpenseTypeOptions,
  loadRdOptions,
  isRdCategoryValue,
  showManualDialog,
  manualTarget,
  manualForm,
  manualSubmitting,
  manualAiLoading,
  manualSuggestionMeta,
  showPairCategoryDialog,
  pairCategoryTarget,
  pairCategoryForm,
  pairCategorySubmitting,
  showEditVoucherDialog,
  editVoucherForm,
  editVoucherSaving,
  editVoucherTargetEntry,
  cleanStatementTitle,
  suggestionAccountPreview,
  rowCategoryChoice,
  ambiguityGroupOf,
  effectiveSuggestion,
  altAccountText,
  askAiVisible,
  askAiStmt,
  askAiPrompt,
  askAiCandidateLines,
  openAskAi,
  handleAskAiAccept,
  openManualClassify,
  openPairCategoryDialog,
  handlePairCategorySave,
  openEditVoucher,
  saveEditVoucher,
  refreshManualAiSuggestion,
  handleManualClassify,
  highConfidenceStatements,
  statementTierFilter,
  selectedStatementIds,
  rememberRuleOnConfirm,
  isSelectableForBatch,
  isNeedsAttentionStatement,
  suggestionBadgeLabel,
  selectableBatchStatements,
  attentionStatements,
  filteredUnmatchedStatements,
  toggleStatementSelection,
  allSelectableChecked,
  someSelectableChecked,
  toggleSelectAllStatements,
  batchTargetStatements,
  batchConfirming,
  handleBatchConfirmRules,
  quickSubmittingId,
  handleAcceptSuggested,
  handleQuickSelectCategory,
  findMatchingInvoiceForStatement,
  findStatementCandidateForEntry,
  statementCandidateText,
  handleEntryPaid,
  beginDisposition,
  acceptPendingDisposition,
  cancelDisposition,
  setDispositionCategory,
  dispositionRecommendationReason,
  dispositionPreviewOf,
  dispositionPreviewDebitLines,
  dispositionPreviewCreditLines,
  dispositionCategorySelectGroups,
  expandedDispositionEntryId,
  dispositionChoice,
  dispositionRecommendation,
  dispositionCategorySelected,
  dispositionPreview,
  dispositionSubmittingId,
  dispositionRdEnabled,
  rdChoiceOf,
  setDispositionRdField,
  effectiveRdProjectOptions,
  editVoucherBusinessContext,
  editVoucherAiLoading,
  editVoucherPreview,
  refreshEditVoucherAi,
  loadEditVoucherPreview,
  openPendingEntryEdit,
  resetEntryDisposition,
  canResetEntryDisposition,
  dispositionResettingId,
  employeePayerOptions,
  pendingEntryList,
  entryIsOutflow,
  confirmedExceptionEntries,
  bossLedgerSummary,
  pendingInvoiceList,
  unpaidInvoiceCount,
  handleMatchInvoiceDirect,
  bossExpenseOptions,
  bossIncomeOptions,
} = useReconciliationWorkspace({
  month: targetMonth,
  currentStep,
  entryPeriod,
  entryStore,
  backfillConfirm,
  openVoucherModal,
  scrollIntoBank,
  employees,
  currentVoucherEntry,
  reloadAll: loadAllData,
  apiErrorMessage,
})

// 「修改凭证」弹窗：从待确认卡片进入时带出第一步选的付款人；
// 已过账凭证只能红冲重记，标题与自定义分录在弹窗里锁住。
const editVoucherIsPosted = computed(() => {
  const entry = editVoucherTargetEntry.value
  return String(entry?.voucherStatus || entry?.status || '') === 'posted'
})
const editVoucherDispositionLabel = computed(() => {
  const id = String(editVoucherTargetEntry.value?.id || '')
  return id ? String(dispositionChoice.value[id]?.label || '') : ''
})

// 第 1 步「单据未上传」提示：三类单据统一按本月口径判定（银行流水不再是账套全量），
// 派生逻辑收敛在 utils/closingImportGaps，状态条、卡片标签与跳转确认共用同一份结论。
const missingImportGaps = computed(() =>
  deriveMissingClosingImports({
    invoiceCount: monthInvoiceRows.value.length,
    wechatBillCount: wechatBillRecordsSummary.value.total || wechatBillRecords.value.length,
    bankStatementCount: monthStatements.value.length,
  })
)
// 零账目月不催上传：第 3 步已有「零业务确认」横幅兜底，这里再催一遍是噪音
const showImportGapWarning = computed(() =>
  !isZeroRecordMonth.value && missingImportGaps.value.length > 0
)
const importGapWarningText = computed(() => {
  const wechatCount = wechatBillRecordsSummary.value.total || wechatBillRecords.value.length
  const imported: string[] = []
  if (monthInvoiceRows.value.length > 0) imported.push(`发票 ${monthInvoiceRows.value.length} 张`)
  if (wechatCount > 0) imported.push(`微信账单 ${wechatCount} 笔`)
  if (monthStatements.value.length > 0) imported.push(`银行流水 ${monthStatements.value.length} 笔`)
  const importedPart = imported.length > 0 ? `已导入：${imported.join(' · ')}；` : ''
  return `${importedPart}还未上传：${formatMissingImportLabels(missingImportGaps.value)} —— 上传后系统才能自动记账与对账`
})

// 卡片「还没导入」标签：整体有缺失提示时升为警告色，零账目月保持中性灰
function importCardTagType(
  hasData: boolean,
  filledType: 'info' | 'success' | 'default',
): 'info' | 'success' | 'warning' | 'default' {
  if (hasData) return filledType
  return showImportGapWarning.value ? 'warning' : 'default'
}

const readinessItems = computed(() => getClosingReadiness(periodCloseChecks.value))
const closingTasks = computed(() => deriveClosingTasks(periodCloseChecks.value))

// 五项核对常显条：把结账检查里最核心的五项从折叠明细提到顶部，
// 关账前一眼看到「账证 / 账账 / 账实 / 账表 / 申报」是否都通过。
// 其余准备项（折旧、摊销、工资、关账顺序）仍在下方清单里逐项展开。
const fiveCheckItems = computed(() => summarizeFiveChecks(readinessItems.value))
const otherReadinessPending = computed(() => countOtherPendingReadiness(readinessItems.value))

// 提示项：跨期未处理单据 / 本期补记。不阻断结账，只要求用户知情。
const closingNotices = computed<ClosingNotice[]>(() => periodCloseChecks.value?.notices ?? [])

// 向导是「月度」的，但申报是「分税种」的：只显示与本期结账真正相关的两类申报义务，
// 个税扣缴、印花税等留给申报作业台，避免向导变成税务清单。
const MAIN_FILING_KINDS = new Set(['vat', 'cit', 'business_income_iit_prepay'])
const mainFilingObligations = computed<FilingObligation[]>(() =>
  (filingInfo.value?.obligations || []).filter(item => MAIN_FILING_KINDS.has(item.taxKind)),
)

function filingDueLabel(item: FilingObligation): string {
  const text = String(item.dueDate || '').slice(5)
  if (!text) return ''
  const [month, day] = text.split('-')
  return `${Number(month)} 月 ${Number(day)} 日前`
}

function goFilingPackage(item?: FilingObligation) {
  const period = item?.period || filingInfo.value?.quarter
  if (!period) return
  router.push({ path: '/tax-filing', query: { period } })
}

// 往来款项明细穿透抽屉 (方案 B: 结账上下文穿透 + 外部业务台账直达)
type BossLedgerDetailType = 'customer_owes' | 'supplier_owes' | 'boss_and_emp' | null
const activeBossLedgerType = ref<BossLedgerDetailType>(null)
const showBossLedgerDrawer = ref(false)

function openBossLedgerDetail(type: 'customer_owes' | 'supplier_owes' | 'boss_and_emp') {
  activeBossLedgerType.value = type
  showBossLedgerDrawer.value = true
}

const currentBossLedgerDetail = computed(() => {
  if (!activeBossLedgerType.value) return null
  if (activeBossLedgerType.value === 'customer_owes') {
    const list = confirmedExceptionEntries.value.filter(
      (e) => e.paymentStatus === 'unpaid' && e.direction === 'income',
    )
    return {
      type: 'customer_owes' as const,
      title: '客户欠款明细（应收账款）',
      amount: bossLedgerSummary.value.customerOwes,
      count: bossLedgerSummary.value.customerCount,
      bossOnly: 0,
      empOnly: 0,
      tip: '本月销项发票或应收业务中，客户尚未完成公户支付的款项。等公户收到回款流水时，系统将自动对账核销。',
      targetRoute: '/invoices',
      targetQuery: { direction: 'income', month: targetMonth.value },
      targetLabel: '前往发票台账（销项） ↗',
      entries: list,
    }
  }
  if (activeBossLedgerType.value === 'supplier_owes') {
    const list = confirmedExceptionEntries.value.filter(
      (e) => e.paymentStatus === 'unpaid' && e.direction === 'expense',
    )
    return {
      type: 'supplier_owes' as const,
      title: '待付供应商款明细（应付挂账）',
      amount: bossLedgerSummary.value.companyOwesSupplier,
      count: bossLedgerSummary.value.supplierCount,
      bossOnly: 0,
      empOnly: 0,
      tip: '本月进项发票或应付业务中，公司尚未付款挂账的款项。后续从公司银行公户付款打款时，系统将自动核销。',
      targetRoute: '/invoices',
      targetQuery: { direction: 'expense', month: targetMonth.value },
      targetLabel: '前往发票台账（进项） ↗',
      entries: list,
    }
  }
  if (activeBossLedgerType.value === 'boss_and_emp') {
    const list = confirmedExceptionEntries.value.filter(
      (e) => e.direction === 'expense' && (e.payer === 'boss_personal' || String(e.payer || '').startsWith('employee_personal')),
    )
    return {
      type: 'boss_and_emp' as const,
      title: '公司欠老板/员工垫付款明细',
      amount: bossLedgerSummary.value.companyOwesBossAndEmp,
      count: list.length,
      bossOnly: bossLedgerSummary.value.bossOnly,
      empOnly: bossLedgerSummary.value.empOnly,
      tip: '属于公司真实经营支出，但实际由老板或员工个人先行垫付。公户资金充裕时，可随时通过公对私转账归还个人，属于往来款返还不涉及个税。',
      targetRoute: '/private-expenses',
      targetQuery: undefined,
      targetLabel: '前往老板私账登记还款 ↗',
      entries: list,
    }
  }
  return null
})

function handleGoToTarget(routePath: string, query?: Record<string, string>) {
  showBossLedgerDrawer.value = false
  router.push({ path: routePath, query })
}

function noticeTitle(notice: ClosingNotice): string {
  // 启用前的数据是「流水 N 条 + 工资表 N 份」，混算成「N 笔」会串味；
  // 具体构成由 message 说明，标题只报事项名。
  if (notice.code === 'PRE_BOOK_START_ACTIVITY') return notice.name
  const amount = notice.amount ? ` · ¥${notice.amount}` : ''
  return `${notice.name}（${notice.count} 笔${amount}）`
}

function noticeRecordTitle(record: ClosingNoticeRecord): string {
  return String(record.summary || record.description || '')
}

function noticeRecordMeta(record: ClosingNoticeRecord): string {
  if (record.businessFilingPeriod) {
    return `纳税义务 ${record.businessFilingPeriod} → 记账 ${record.postingFilingPeriod}`
  }
  if (record.originalPeriod) return `原期间 ${record.originalPeriod}`
  if (record.businessPeriod) return `所属 ${record.businessPeriod}`
  return ''
}

function noticeRecordAmount(record: ClosingNoticeRecord): string {
  return String(record.amount ?? record.vatAmount ?? '')
}

const NOTICE_EXPORT_SCOPE: Record<string, string> = {
  CROSS_PERIOD_PENDING: 'cross-period',
  VAT_PERIOD_MISMATCH: 'vat-period',
}

async function exportBackfillNotice(notice: ClosingNotice) {
  const scope = NOTICE_EXPORT_SCOPE[notice.code] || 'backfill'
  try {
    const blob = (await apiClient.get('/api/periods/backfills/export', {
      params: { scope, month: targetMonth.value },
      responseType: 'blob',
    })) as unknown as Blob
    if (!(blob instanceof Blob)) throw new Error('导出响应格式异常')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${notice.name}_${targetMonth.value}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    message.success(`${notice.name}已导出`)
  } catch {
    message.error('导出失败，请稍后重试')
  }
}

function normalizeStep(s: number): number {
  if (s === 4) return 2
  if (s === 5) return 3
  if (s >= 1 && s <= 3) return s
  return 1
}

async function runTaskAction(action: ClosingTaskAction) {
  if (action.month) {
    targetMonth.value = action.month
    loadAllData()
    return
  }
  if (action.kind === 'run-closing') {
    runClosing()
    return
  }
  if (action.kind === 'match') {
    // 分裂自愈：个别入账路径只写 matched 指针、漏了对账同步（第二步显示已认领、
    // 第三步银行核对却说未配对）。先尝试幂等重建核心链接；真未配对才跳第二步。
    if (action.statementId) {
      try {
        const res: any = await apiClient.post(`/api/bank/reconciliations/resync/${action.statementId}`)
        if (res?.resynced) {
          message.success('检测到对账记录缺失，已自动重建配对，可重新检查锁账')
          await loadAllData()
          return
        }
      } catch {
        /* 自愈失败则按原流程跳转第二步手工处理 */
      }
    }
    currentStep.value = 2
    if (action.statementId) {
      selectedBank.value = action.statementId
      scrollIntoBank(action.statementId)
    }
    return
  }
  if (action.kind === 'walkthrough') {
    startWalkthrough()
    return
  }
  if (action.kind === 'balance-detail') {
    showBankBalanceDetail.value = true
    return
  }
  if (action.kind === 'vouchers') {
    const voucherCheck = periodCloseChecks.value?.checks?.find((c: any) => c.code === 'VOUCHER_TO_SOURCE')
    const firstEntryId = voucherCheck?.records?.[0]?.entryId
    if (firstEntryId) {
      openVoucherModal(String(firstEntryId))
      return
    }
    router.push({ path: action.route || '/vouchers', query: { month: targetMonth.value, from: `/closing-wizard?month=${targetMonth.value}&step=3` } })
    return
  }
  currentStep.value = 2
}

function scrollIntoBank(statementId: string) {
  void nextTick(() => {
    document.querySelector(`[data-statement-id="${statementId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}
function handleReadinessAction(item: ClosingReadinessItem) {
  if (item.code === 'PERIOD_ORDER') {
    const orderCheck = periodCloseChecks.value?.checks?.find((c: any) => c.code === 'PERIOD_ORDER')
    const earliest = String(orderCheck?.earliestOpenMonth || '')
    if (earliest) {
      targetMonth.value = earliest
      loadAllData()
    }
    return
  }
  if (item.code === 'VOUCHER_TO_SOURCE') {
    const voucherCheck = periodCloseChecks.value?.checks?.find((c: any) => c.code === 'VOUCHER_TO_SOURCE')
    const firstEntryId = voucherCheck?.records?.[0]?.entryId
    if (firstEntryId) {
      openVoucherModal(String(firstEntryId))
      return
    }
  }
  if (item.route) router.push({ path: item.route, query: { month: targetMonth.value, from: `/closing-wizard?month=${targetMonth.value}&step=3` } })
  else currentStep.value = normalizeStep(item.step)
}
const closeDraftCount = computed(() => Number(periodCloseChecks.value?.checks?.find((check: any) => check.code === 'VOUCHER_TO_SOURCE')?.autoPostAtCloseCount || 0))
const showBankBalanceDetail = ref(false)
const bankBalanceRecon = computed(() => {
  const check = periodCloseChecks.value?.checks?.find((c: any) => c.code === 'BOOK_TO_ACTUAL')
  return check?.balanceReconciliation || null
})
const canBatchPostAndClose = computed(() => canBatchPostDrafts(periodCloseChecks.value))

// 结账域（状态/请求已拆入 composables/useClosingFlow；阻断判定由各域传入）
const {
  closed,
  closingLoading,
  monthlyIncome,
  monthlyExpense,
  netProfit,
  cfoDiagnosis,
  diagnosisLoading,
  closingVoucherRefs,
  vatExemptAmount,
  vatExemptVoucherRefs,
  closingTodos,
  fetchClosedMetrics,
  fetchClosingTodos,
  generateDiagnosis,
  runClosing,
  downloadReport,
} = useClosingFlow({
  month: targetMonth,
  currentStep,
  periodCloseChecks,
  hasBlockingPayroll,
  isZeroRecordMonth,
  confirmZeroActivity,
  loadReconciliationHealth,
  hasBlockingReconciliationRisk: () => hasBlockingReconciliationRisk.value,
  entryStore,
})

const closingReady = computed(() => {
  if (!periodCloseChecks.value?.canClose && !canBatchPostDrafts(periodCloseChecks.value)) return false
  if (hasBlockingPayroll.value) return false
  if (isZeroRecordMonth.value && !confirmZeroActivity.value) return false
  return true
})


// Load all data on mount
onMounted(async () => {
  await Promise.all([loadAllData(), loadRdOptions(), loadAccountMapping()])
  refreshCategoryOptions()
})

async function loadAllData() {
  loading.value = true
  confirmZeroActivity.value = false
  // 切月/重载后重新提示单据缺失（同一月份内确认过「继续」就不重复弹）
  importGapConfirmed.value = false
  try {
    // 1. 先加载月份无关数据与账期列表，首次直达时据此定位最早未结账月份
    const [_, res, availRes] = await Promise.all([
      entryStore.fetchEntries(),
      apiClient.get('/api/bank/statements', { timeout: 30000 }),
      apiClient.get('/api/periods/available').catch(() => null),
    ])
    rawStatements.value = (res as any).items || []
    availablePeriods.value = (availRes as any)?.months || []

    if (!initialPeriodResolved) {
      if (!hasQueryMonth) targetMonth.value = getEarliestOpenMonth()
      initialPeriodResolved = true
    }

    // 月份确定后再读取当期结账检查与申报信息，保证首屏内容和选择月份一致。
    const [periodChecks, obligations] = await Promise.all([
      apiClient.get(`/api/periods/${targetMonth.value}/close-checks`),
      getFilingObligations(targetMonth.value).catch(() => null),
    ])
    filingInfo.value = (obligations as unknown as FilingObligations | null) ?? null
    periodCloseChecks.value = periodChecks as any
    
    // 2. 账期状态只以后端为准
    closed.value = (periodChecks as any).periodStatus === 'closed'
    if (closed.value) {
      currentStep.value = 3
    } else if (router.currentRoute?.value?.query?.step) {
      const s = parseInt(String(router.currentRoute.value.query.step), 10)
      if (!isNaN(s)) currentStep.value = normalizeStep(s)
    } else if (closingReady.value && currentStep.value === 1) {
      currentStep.value = 3
    }

    // 待办跳入：预选指定银行流水并滚动定位（清掉 query 避免刷新后重复触发）
    const queryStatementId = String(router.currentRoute?.value?.query?.statement || '')
    if (queryStatementId) {
      const found = bankStatements.value.find((statement: any) => statement.id === queryStatementId)
      if (found) {
        selectedBank.value = queryStatementId
        scrollIntoBank(queryStatementId)
      }
      router.replace({ query: { month: targetMonth.value, step: String(currentStep.value) } })
    }
    
    // 3. 更新同步的待对账数据过滤
    filterUnmatchedData()
    matchedPairs.value = buildPersistedMatchedPairs()
    const selectableIds = new Set(allUnmatchedEntries.value.map((entry) => entry.id))
    selectedInvoiceIds.value = selectedInvoiceIds.value.filter((id) => selectableIds.has(id))
    if (!bankStatements.value.some((statement) => statement.id === selectedBank.value)) selectedBank.value = null
  } catch (err: any) {
    console.error('[loadAllData] Core error occurred:', err)
    message.error(`加载核心账目数据失败: ${err.message || err}`)
  } finally {
    // 4. 核心数据已加载或报错后，立即关闭大 Loading，让 UI 保持流畅
    loading.value = false
  }

  // 5. 辅助与 AI 接口转为后台异步并行加载，防止慢接口超时阻断整个 UI 正常渲染
  loadReconciliationHealth()
  loadSalarySheet()
  loadPayrollPaymentSuggestions()
  loadWechatBillRecords()
  
  if (closed.value) {
    fetchClosedMetrics()
    fetchClosingTodos()
  }
  loadAiSuggestions()
}



function readinessStatusLabel(status: string) {
  return {
    complete: '已完成',
    blocked: '需要处理',
    needs_review: '需要复核',
    incomplete: '尚未完成',
    not_required: '本期不需要',
  }[status] || '尚未完成'
}

function readinessStatusIcon(status: string) {
  return {
    complete: 'ri-checkbox-circle-fill',
    blocked: 'ri-error-warning-line',
    needs_review: 'ri-eye-line',
    incomplete: 'ri-time-line',
    not_required: 'ri-subtract-line',
  }[status] || 'ri-time-line'
}

function goLedgerDetail(entryId: string) {
  router.push({ path: `/ledger/${entryId}`, query: { from: `/closing-wizard?month=${targetMonth}&step=1` } })
}

// ── 反结账：状态与请求已拆入 ReopenPeriodDialog.vue，成功后由 handleReopened 刷新 ──
const reopenVisible = ref(false)

async function handleReopened() {
  closingVoucherRefs.value = []
  closed.value = false
  await entryStore.fetchEntries()
  periodCloseChecks.value = await apiClient.get(`/api/periods/${targetMonth.value}/close-checks`)
}

// 用户主动的步骤跳转统一走 goStep：离开第 1 步前若有单据缺失先弹确认（可继续，不硬拦）。
// 程序性赋值不走这里——待办跳入、结账流程、URL 恢复等场景不应被提示打断。
const importGapConfirmed = ref(false)
const pendingStepTarget = ref(1)
// NStep 的 @click 与 NSteps 的 update:current 在同一次点击里都会调 goStep（naive-ui 合并两个
// onClick），弹窗路径必须幂等，否则一击会叠出两个确认框。
let importGapDialogOpen = false

function goStep(target: number) {
  if (target < 1 || target > 3 || target === currentStep.value) return
  if (
    currentStep.value === 1 &&
    target > 1 &&
    showImportGapWarning.value &&
    !importGapConfirmed.value
  ) {
    pendingStepTarget.value = target
    if (importGapDialogOpen) return
    importGapDialogOpen = true
    dialog.warning({
      title: '本月还有单据未上传',
      content: `本月还未上传：${formatMissingImportLabels(missingImportGaps.value)}。如果本月确实没有这些单据，可以直接继续；否则建议先上传，系统才能自动记账与对账。`,
      positiveText: '继续结账',
      negativeText: '返回上传',
      maskClosable: false,
      onPositiveClick: () => {
        importGapConfirmed.value = true
        goStep(pendingStepTarget.value)
      },
      // 无论用户走哪个出口关闭弹窗都要复位，否则之后点步骤不会再弹确认
      onAfterLeave: () => {
        importGapDialogOpen = false
      },
    })
    return
  }
  if (target === 3 && currentStep.value === 2 && hasBlockingPayroll.value) {
    if (payrollPendingReview.value) {
      message.warning('工资计提凭证正在等待审核，审核通过后才能继续。')
    } else {
      message.warning('本月在职员工工资尚未完成计提，请先计提后再结账（发放可延至次月）。')
    }
    return
  }
  currentStep.value = target
}

function nextStep() {
  goStep(currentStep.value + 1)
}

function prevStep() {
  goStep(currentStep.value - 1)
}


// 研发项目选项跟账套绑定：切账套时必须重拉，否则会拿着上一家的项目做归集
watch(
  () => authStore.currentCompanyId,
  (next, prev) => {
    if (!next || next === prev) return
    void Promise.all([loadAllData(), loadRdOptions()])
  },
)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([loadAllData(), loadRdOptions()]))
</script>

<template>
  <div class="report-container closing-wizard-container" :class="{ 'closing-wizard--wide': currentStep === 1 || currentStep === 2 || currentStep === 3 }">
    <!-- Header -->
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回首页
        </NButton>
        <div class="report-title-section">
          <div class="closing-title-row">
            <h2>月度结账向导</h2>
            <NSelect
              v-model:value="targetMonth"
              :options="monthOptions"
              style="width: 140px;"
              size="small"
              @update:value="loadAllData"
            />
            <NTag
              :type="closed ? 'success' : (isZeroRecordMonth ? (confirmZeroActivity ? 'info' : 'default') : (closingReady ? 'info' : 'warning'))"
              size="small"
              :bordered="false"
            >
              {{ closed ? '已结账归档' : (isZeroRecordMonth ? (confirmZeroActivity ? '已确认零申报' : '本月无账目') : (closingReady ? '已就绪可结账' : '待处理结账')) }}
            </NTag>
            <NTooltip trigger="hover" placement="bottom" :style="{ maxWidth: '320px' }">
              <template #trigger>
                <button
                  type="button"
                  class="closing-ai-badge-btn"
                  aria-label="打开 AI CFO 检查本月结账"
                  @click="openClosingCopilot"
                >
                  <i class="ri-sparkling-2-line" aria-hidden="true" />
                  <span>AI CFO</span>
                </button>
              </template>
              检查本月结账、解释差异并创建待确认修复草稿；最终锁账仍由你在此页确认。
            </NTooltip>
          </div>
          <div v-if="mainFilingObligations.length" class="closing-filing-inline">
            <i class="ri-calendar-check-line" aria-hidden="true" />
            <span class="filing-inline-title">本期申报</span>
            <span class="filing-inline-text">
              {{ mainFilingObligations.map(item => `${item.name}${filingDueLabel(item) ? `（${filingDueLabel(item)}）` : ''}`).join(' · ') }}
            </span>
            <button type="button" class="filing-inline-link" @click="goFilingPackage(mainFilingObligations[0])">
              查看申报底稿
              <i class="ri-arrow-right-s-line" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 一套月结向导同时服务老板与会计：默认大白话，专业明细折叠可展开 -->
    <!-- Steps Card (未结账时持续展示步骤导引) -->
    <NCard v-if="!closed" :bordered="false" class="report-card steps-card mb-3" style="padding: 6px 14px;">
      <NSteps :current="currentStep" size="small" @update:current="(val: number) => goStep(val)">
        <NStep title="导入单据" description="发票、流水、账单" style="cursor: pointer;" @click="goStep(1)" />
        <NStep title="确认收支" description="系统已自动记账，逐笔确认例外" style="cursor: pointer;" @click="goStep(2)" />
        <NStep title="一键锁账" description="检查通过后封账归档" style="cursor: pointer;" @click="goStep(3)" />
      </NSteps>
    </NCard>

    <!-- 本月进度条：资料汇总 + 邮箱免传快捷入口 + 下一步入口，与步骤导航分开；单据未齐时整体升为警告样式 -->
    <div v-if="!closed && currentStep === 1" class="step-status-bar mb-3" :class="{ 'step-status-bar--warning': showImportGapWarning }">
      <div class="step-status-main">
        <i class="step-status-icon" :class="showImportGapWarning ? 'ri-error-warning-line' : 'ri-folder-shared-line'" />
        <span class="step-status-label">{{ showImportGapWarning ? '本月单据未齐' : '本月已导入' }}</span>
        <span class="step-status-text">
          <template v-if="showImportGapWarning">{{ importGapWarningText }}</template>
          <template v-else>
            银行流水 {{ monthStatements.length }} 笔 · 待确认发票 {{ pendingInvoiceList.length }} 张{{ unpaidInvoiceCount > 0 ? `（另有 ${unpaidInvoiceCount} 张已确认去向）` : '' }} · 微信账单 {{ wechatBillRecordsSummary.total || wechatBillRecords.length }} 笔
          </template>
        </span>
      </div>
      <div class="step-status-actions">
        <button
          type="button"
          class="step-status-inbox-btn"
          :class="{ 'is-active': showMailInbox }"
          @click="showMailInbox = !showMailInbox"
        >
          <i class="ri-mail-send-line" aria-hidden="true" />
          <span>邮箱免传收单</span>
          <i :class="showMailInbox ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" aria-hidden="true" />
        </button>
        <NButton type="primary" size="small" @click="nextStep">
          {{ unmatchedStatements.length > 0 ? `下一步：确认 ${unmatchedStatements.length} 笔收支` : '下一步：确认收支' }}
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>
    </div>

    <!-- Step 1 Layout: 资料归集与导入 (发票 · 网银流水 · 平台账单) -->
    <div v-if="currentStep === 1">
      <NSpin :show="loading">
        <MailInboxCard
          collapsible
          hide-trigger
          v-model:expanded="showMailInbox"
          @import="handleInboxImport"
          @error="onFileError"
        />
        <div class="step1-import-grid">
        <NCard :bordered="false" class="report-card" :class="{ 'step1-card-expanded': unmatchedInvoices.length > 0 }">
          <div class="step-import-head">
            <h3 class="report-section-title"><span class="step-import-num">1</span>导入发票</h3>
            <div class="step-import-meta">
              <NTag size="small" :type="importCardTagType(monthInvoiceRows.length > 0, unmatchedInvoices.length > 0 ? 'info' : 'default')" :bordered="false">
                {{ monthInvoiceRows.length === 0
                  ? '还没导入'
                  : unmatchedInvoices.length > 0
                    ? `本月待核对 ${unmatchedInvoices.length} 张 · 共 ${monthInvoiceRows.length} 张`
                    : `本月已核对 ${monthInvoiceRows.length} 张` }}
              </NTag>
              <NButton v-if="monthInvoiceRows.length > 0" size="tiny" quaternary type="primary" @click="invoiceDropzone?.openPicker()">重新上传</NButton>
              <NButton v-if="monthInvoiceRows.length > 0" text size="tiny" @click="showInvoiceDetails = !showInvoiceDetails">
                {{ showInvoiceDetails ? '收起明细' : '展开明细' }}
              </NButton>
              <NButton text size="tiny" @click="router.push('/invoices')">去整页管理</NButton>
            </div>
          </div>
          <p class="report-subtitle">税局导出的进项/销项发票 Excel，自动拆分税率并生成发票明细。</p>

          <FileDropzone
            v-show="monthInvoiceRows.length === 0"
            ref="invoiceDropzone"
            accept=".xlsx,.xls,.csv"
            icon="ri-file-excel-2-line"
            title="点击或拖拽发票 Excel 上传"
            subtitle="上传后自动写入系统发票库"
            :loading="loading"
            @file="processInvoiceFile"
            @error="onFileError"
          />
  
          <div v-if="monthInvoiceRows.length > 0" class="report-table-wrapper" style="max-height: 320px; overflow-y: auto;">
            <table class="report-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>发票名称</th>
                  <th>记账分类</th>
                  <th v-if="showInvoiceDetails" style="text-align: center; width: 100px;">发票类型</th>
                  <th style="text-align: right;">发票金额</th>
                  <th v-if="showInvoiceDetails" style="text-align: center; width: 130px;">入账模式</th>
                  <th style="text-align: center; width: 120px;">状态</th>
                  <th v-if="showInvoiceDetails" style="text-align: center; width: 80px;">冲销 / 删除</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="inv in monthInvoiceRows" :key="inv.id" :style="inv.is_archive_only ? 'opacity: 0.7;' : ''">
                  <td>{{ inv.date }}</td>
                  <td>{{ inv.title }}</td>
                  <td>
                    <span>{{ inv.category || '系统未分类' }}</span>
                    <small
                      v-if="findStatementCandidateForEntry(inv)"
                      class="invoice-candidate-hint"
                    >{{ statementCandidateText(findStatementCandidateForEntry(inv)) }}</small>
                  </td>
                  <td v-if="showInvoiceDetails" style="text-align: center;">
                    <NTag v-if="inv.direction === 'income'" size="small" type="info" :bordered="false">销项发票</NTag>
                    <NTag v-else-if="inv.direction === 'expense'" size="small" type="warning" :bordered="false">进项发票</NTag>
                    <NTag v-else size="small" type="default" :bordered="false">其他发票</NTag>
                  </td>
                  <td class="amount-cell" :class="amountClassForDirection(inv.direction, inv.amount)">
                    {{ formatMoney(invoiceDisplayAmount(inv)) }}
                  </td>
                  <td v-if="showInvoiceDetails" style="text-align: center;">
                    <NCheckbox
                      :checked="!inv.is_archive_only"
                      @update:checked="toggleArchiveMode(inv.id, !$event)"
                    >
                      {{ inv.is_archive_only ? '仅归档' : '参与记账' }}
                    </NCheckbox>
                  </td>
                  <td style="text-align: center;">
                    <NTag size="small" :type="invoiceRowStatus(inv).tagType" :bordered="false">
                      {{ invoiceRowStatus(inv).label }}
                    </NTag>
                  </td>
                  <td v-if="showInvoiceDetails" style="text-align: center;">
                    <NButton
                      v-if="inv.voucherStatus === 'posted'"
                      size="tiny"
                      type="warning"
                      ghost
                      @click="goLedgerDetail(inv.id)"
                    >
                      已入账·去冲销
                    </NButton>
                    <NButton
                      v-else
                      size="tiny"
                      type="error"
                      ghost
                      @click="confirmDelete({
                        content: `确定删除发票“${inv.title || '未命名发票'}”吗？此操作不可撤销。`,
                        onConfirm: () => handleDeleteInvoice(inv.id),
                      })"
                    >
                      删除
                    </NButton>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </NCard>

        <!-- WeChat Pay Bill Import Card -->
        <!-- 展开条件两态都要算：wechatBillRows=上传预览中的行；wechatBillRecords=服务端已导入记录。
             只判前者会导致「已导入 15 笔」的常驻状态依然收起，表格挤在 1/3 卡片里。 -->
        <NCard :bordered="false" class="report-card" :class="{ 'step1-card-expanded': wechatBillRows.length > 0 || wechatBillRecords.length > 0 }">
          <div class="step-import-head">
            <h3 class="report-section-title"><span class="step-import-num">2</span>导入微信/平台账单</h3>
            <div class="step-import-meta">
              <NTag
                size="small"
                :type="importCardTagType((wechatBillRecordsSummary.total || wechatBillRows.length) > 0, 'success')"
                :bordered="false"
              >
                {{ (wechatBillRecordsSummary.total || wechatBillRows.length) > 0
                  ? `已导入 ${wechatBillRecordsSummary.total || wechatBillRows.length} 笔`
                  : '还没导入' }}
              </NTag>
            </div>
          </div>
          <p class="report-subtitle">
            微信商户后台导出的当月账单 CSV，自动识别每笔手续费。
            <GuideTrigger guide="wechat-bill" label="账单怎么导出？" />
          </p>

          <FileDropzone
            v-show="!wechatBillImported && wechatBillRows.length === 0"
            ref="wechatDropzone"
            accept=".csv"
            icon="ri-wechat-pay-line"
            icon-color="#07c160"
            title="点击或拖拽微信账单 CSV 上传"
            subtitle="从微信「账单中心」按月导出"
            :loading="loading"
            @file="processWechatBillFile"
            @error="onFileError"
          />

          <!-- Preview and Import Configuration -->
          <div v-if="wechatBillRows.length > 0">
            <!-- Summary card for monthly statement -->
            <div style="background: var(--card-background-hover); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; border: 1px solid var(--border-color);">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <span style="font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px;">
                  <i class="ri-file-chart-line" style="color: var(--primary-color);" /> 月度汇总核对（共 {{ wechatBillSummary.总交易单数 || wechatBillRows.length }} 笔）
                </span>
                <!-- 记账模式切换 -->
                <div style="display: flex; align-items: center; gap: 12px;">
                  <span style="font-size: 13px; color: var(--text-secondary);">记账方式：</span>
                  <label style="font-size: 13px; cursor: pointer; display: flex; align-items: center; gap: 4px;">
                    <input type="radio" value="summary" v-model="wechatImportMode" />
                    <strong>月度汇总记账</strong>（推荐·生成1张凭证）
                  </label>
                  <label style="font-size: 13px; cursor: pointer; display: flex; align-items: center; gap: 4px; color: var(--text-secondary);">
                    <input type="radio" value="detail" v-model="wechatImportMode" />
                    逐笔明细记账（生成多张凭证）
                  </label>
                </div>
              </div>
              <div style="display: flex; gap: 24px; font-size: 13px; flex-wrap: wrap;">
                <div>订单总额：<strong>{{ formatMoney(wechatBillSummary.订单总金额 || wechatBillRows.filter(r => r.status === 'SUCCESS').reduce((s, r) => s + r.order_amount, 0)) }}</strong></div>
                <div v-if="Number(wechatBillSummary.退款总金额 || 0) > 0" style="color: var(--solo-expense);">
                  退款总额：-{{ formatMoney(wechatBillSummary.退款总金额) }}
                </div>
                <div style="color: var(--solo-expense);">
                  平台手续费：{{ formatMoney(wechatBillSummary.手续费总金额 || wechatBillRows.reduce((s, r) => s + r.fee, 0)) }}
                </div>
                <div>
                  预计待结算净额：<strong style="color: var(--primary-color);">
                    {{ formatMoney(
                      Number(wechatBillSummary.订单总金额 || wechatBillSummary.应结订单总金额 || 0) -
                      Number(wechatBillSummary.退款总金额 || 0) -
                      Number(wechatBillSummary.手续费总金额 || 0)
                    ) }}
                  </strong>
                </div>
              </div>
            </div>

            <!-- Detail preview table (collapsible or scrollable) -->
            <div class="report-table-wrapper" style="max-height: 260px; overflow-y: auto;">
              <table class="report-table">
                <thead>
                  <tr>
                    <th class="date-cell">日期</th>
                    <th>商品名称</th>
                    <th style="text-align: center; width: 80px;">状态</th>
                    <th class="amount-cell" style="text-align: right;">订单金额</th>
                    <th class="amount-cell" style="text-align: right;">手续费</th>
                    <th class="amount-cell" style="text-align: right;">到账净额</th>
                    <th style="text-align: center; width: 60px;">删除</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in wechatBillRows" :key="idx" :style="row.is_duplicate ? 'opacity: 0.55; background-color: var(--card-background-hover);' : ''">
                    <td class="date-cell">{{ row.date }}</td>
                    <td>
                      {{ row.product_name }}
                      <NTag v-if="row.is_duplicate" size="tiny" type="error" :bordered="false" style="margin-left: 6px;">系统已存在</NTag>
                    </td>
                    <td style="text-align: center;">
                      <NTag v-if="row.status === 'SUCCESS'" size="small" type="success" :bordered="false">收款</NTag>
                      <NTag v-else size="small" type="warning" :bordered="false">退款</NTag>
                    </td>
                    <td class="amount-cell" style="text-align: right;" :style="row.status === 'REFUND' ? 'color:var(--solo-expense)' : ''">
                      {{ row.status === 'REFUND' ? '-' : '' }}{{ formatMoney(row.status === 'REFUND' ? row.refund_amount : row.order_amount) }}
                    </td>
                    <td class="amount-cell" style="text-align: right; color: var(--solo-expense);">{{ formatMoney(row.fee) }}</td>
                    <td class="amount-cell" style="text-align: right;">{{ formatMoney(row.settled_amount) }}</td>
                    <td style="text-align: center;">
                      <NButton size="tiny" type="error" ghost @click="removeWechatRow(idx)">删除</NButton>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px;">
              <span style="font-size: 13px; color: var(--text-secondary);">
                <template v-if="wechatImportMode === 'summary'">
                  当前模式：将按月度对账结果生成 <strong>1 张综合记账凭证</strong>（自动轧平收入、退款与手续费，收入按征收率价税分离计提增值税）
                </template>
                <template v-else-if="wechatBillRows.some(r => r.is_duplicate)">
                  逐笔明细模式：将导入 {{ wechatBillRows.filter(r => !r.is_duplicate).length }} 条新记录 · 自动过滤 {{ wechatBillRows.filter(r => r.is_duplicate).length }} 条已存在记录
                </template>
                <template v-else>
                  逐笔明细模式：共 {{ wechatBillRows.length }} 条 · 合计手续费 {{ formatMoney(wechatBillRows.reduce((s, r) => s + r.fee, 0)) }}
                </template>
              </span>
              <div style="display: flex; gap: 8px;">
                <NButton size="small" ghost @click="wechatDropzone?.openPicker()">重新上传</NButton>
                <NButton
                  size="small"
                  type="primary"
                  @click="confirmWechatBillImport"
                  :loading="wechatBillImporting"
                  :disabled="wechatBillImporting || (wechatImportMode === 'detail' && wechatBillRows.filter(r => !r.is_duplicate).length === 0)"
                >
                  {{ wechatImportMode === 'summary' ? '生成月度汇总凭证' : '确认逐笔导入' }}
                </NButton>
              </div>
            </div>
          </div>

          <!-- Imported success state -->
          <div v-if="wechatBillImported" style="text-align: center; padding: 24px 0;">
            <i class="ri-checkbox-circle-fill" :style="{ fontSize: '32px', color: wechatBillFailures.length > 0 ? '#a05c10' : '#07c160' }"></i>
            <p style="margin-top: 8px; color: var(--text-secondary);">
              {{ wechatBillFailures.length > 0 ? `导入完成，${wechatBillFailures.reduce((n, f) => n + f.count, 0)} 条交易未能入账（原因见下）` : '微信账单已导入，收款与手续费已自动分录' }}
            </p>
            <NButton size="small" ghost style="margin-top: 8px;" @click="() => { wechatBillImported = false; wechatBillFailures = []; wechatDropzone?.openPicker() }">重新导入</NButton>
          </div>

          <!-- Import failure details: aggregated by cause; start-date issues link to the company editor -->
          <NAlert
            v-if="wechatBillFailures.length > 0"
            type="warning"
            title="部分或全部交易未能入账"
            style="margin-top: 12px;"
          >
            <ul style="margin: 0; padding-left: 18px;">
              <li v-for="(f, i) in wechatBillFailures" :key="i" style="font-size: 13px; line-height: 1.8;">
                {{ f.message }}
                <NTag v-if="f.count > 1" size="small" :bordered="false" style="margin-left: 6px;">× {{ f.count }}</NTag>
              </li>
            </ul>
            <div v-if="wechatBillFailures.some(f => f.isStartDateIssue)" style="margin-top: 8px;">
              <NButton size="small" type="primary" @click="gotoAdjustStartDate">
                <i class="ri-calendar-line" />&nbsp;去调整启用日期
              </NButton>
              <span style="margin-left: 8px; font-size: 12px; color: var(--text-tertiary);">把启用日期改到账单最早日期之前，再回来重新导入</span>
            </div>
          </NAlert>

          <!-- Previously imported records for the target month -->
          <div v-if="wechatBillRecords.length > 0" style="margin-top: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px; margin-bottom: 8px;">
              <h4 class="report-section-title" style="font-size: 14px;">
                <i class="ri-history-line"></i> 已导入记录（{{ wechatBillRecordsSummary.total || wechatBillRecords.length }} 笔订单）
              </h4>
              <span style="font-size: 13px; color: var(--text-secondary);">
                合计手续费 {{ formatMoney(wechatBillRecordsSummary.totalFee ?? 0) }} · 合计到账净额 {{ formatMoney(wechatBillRecordsSummary.totalSettled ?? 0) }}
              </span>
            </div>
            <div class="report-table-wrapper" style="max-height: 260px; overflow-y: auto;">
              <table class="report-table">
                <thead>
                  <tr>
                    <th class="date-cell">日期</th>
                    <th>商品名称</th>
                    <th style="text-align: center; width: 80px;">状态</th>
                    <th class="amount-cell" style="text-align: right;">订单金额</th>
                    <th class="amount-cell" style="text-align: right;">手续费</th>
                    <th class="amount-cell" style="text-align: right;">到账净额</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="rec in wechatBillRecords" :key="rec.orderNo">
                    <td class="date-cell">{{ rec.date }}</td>
                    <td>{{ rec.title }}</td>
                    <td style="text-align: center;">
                      <NTag :type="rec.status === 'SUCCESS' ? 'success' : rec.status === 'SUMMARY' ? 'info' : 'warning'" size="small" :bordered="false">
                        {{ rec.status === 'SUCCESS' ? '收款' : rec.status === 'SUMMARY' ? '月度汇总' : '退款' }}
                      </NTag>
                    </td>
                    <td class="amount-cell" style="text-align: right;" :style="rec.orderAmount < 0 ? 'color: var(--solo-expense)' : ''">{{ formatMoney(rec.orderAmount) }}</td>
                    <td class="amount-cell" style="text-align: right;" :style="rec.fee < 0 ? 'color: var(--solo-income)' : 'color: var(--solo-expense)'">{{ formatMoney(rec.fee) }}</td>
                    <td class="amount-cell" style="text-align: right;" :style="rec.settledAmount < 0 ? 'color: var(--solo-expense)' : ''">{{ formatMoney(rec.settledAmount) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </NCard>

        <!-- 网银流水 -->
        <NCard :bordered="false" class="report-card" :class="{ 'step1-card-expanded': bankUploadResult || unmatchedStatements.length > 0 }">
          <div class="step-import-head">
            <h3 class="report-section-title"><span class="step-import-num">3</span>导入银行流水</h3>
            <div class="step-import-meta">
              <NTag size="small" :type="importCardTagType(monthStatements.length > 0, 'success')" :bordered="false">
                {{ monthStatements.length > 0 ? `已导入 ${monthStatements.length} 笔` : '还没导入' }}
              </NTag>
              <NButton text size="tiny" @click="router.push('/bank')">去整页管理</NButton>
            </div>
          </div>
          <p class="report-subtitle">
            网银导出的本月流水单，自动认领收支并匹配已有记账。
            <GuideTrigger guide="bank-statement" label="流水怎么下载？" />
          </p>

          <FileDropzone
            accept=".xlsx,.xls,.csv"
            icon="ri-download-cloud-2-line"
            title="点击或拖拽网银流水上传"
            subtitle="支持工行、招行、建行等 Excel/CSV"
            :loading="loading"
            @file="processBankFile"
            @error="onFileError"
          />
  
          <!-- Upload Feedback Panel -->
          <div v-if="bankUploadResult" class="mb-4" style="background: rgba(99, 102, 241, 0.02); border: 1px solid var(--solo-border); border-radius: var(--solo-radius); padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
              <span style="font-weight: bold; color: var(--solo-text-primary); font-size: 14px;">
                <i class="ri-checkbox-circle-line" style="color: var(--solo-income); margin-right: 4px;"></i>
                本次文件解析与导入成功
              </span>
              <span style="font-size: 12px; color: var(--solo-text-muted);">
                已导入: <span style="color: var(--solo-income); font-weight: bold;">{{ bankUploadResult.imported }}</span> 条 |
                失败: <span style="color: var(--solo-expense); font-weight: bold;">{{ bankUploadResult.errors }}</span> 条<template v-if="bankUploadResult.skipped > 0"> |
                跳过: <span style="color: #A05C10; font-weight: bold;">{{ bankUploadResult.skipped }}</span> 条（无有效交易日期）</template>
              </span>
            </div>
            
            <div class="report-table-wrapper" style="max-height: 250px; overflow-y: auto;">
              <table class="report-table">
                <thead>
                  <tr>
                    <th style="width: 110px;">交易日期</th>
                    <th>交易内容 (类型/摘要/对方)</th>
                    <th style="text-align: right; width: 110px;">发生额</th>
                    <th style="text-align: right; width: 110px;">账户余额</th>
                    <th style="text-align: center; width: 115px;">对账状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in bankUploadResult.rows" :key="idx">
                    <td>{{ (row.transaction_date || '').slice(0, 10) || '-' }}</td>
                    <td>{{ row.description || '-' }}</td>
                    <td class="amount-cell" :class="safeFloat(row.amount) >= 0 ? 'income' : 'expense'">
                      {{ formatMoney(safeFloat(row.amount)) }}
                    </td>
                    <td style="text-align: right; color: var(--solo-text-secondary);">
                      {{ formatMoney(safeFloat(row.balance)) }}
                    </td>
                    <td style="text-align: center;">
                      <NTag size="small" :type="isItemAutoMatched(row) ? 'success' : 'warning'">
                        {{ isItemAutoMatched(row) ? '已确认对账' : '待手动对账' }}
                      </NTag>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
  
          <div v-if="unmatchedStatements.length > 0">
            <div style="font-size: 12px; font-weight: bold; color: var(--solo-text-secondary); margin-bottom: 8px;">
              <i class="ri-bank-line"></i> 本月待匹配流水明细列表：
            </div>
            <div class="report-table-wrapper" style="max-height: 320px; overflow-y: auto;">
              <table class="report-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>交易摘要</th>
                    <th style="text-align: right;">发生额</th>
                    <th style="text-align: center;">状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="stmt in unmatchedStatements" :key="stmt.id">
                    <td>{{ stmt.transaction_date?.slice(0, 10) }}</td>
                    <td>{{ stmt.description }}</td>
                    <td class="amount-cell" :class="stmt.amount >= 0 ? 'income' : 'expense'">{{ formatMoney(stmt.amount) }}</td>
                    <td style="text-align: center;">
                      <NTooltip v-if="getAiSuggestion(stmt.id)?.type === 'match'" trigger="hover">
                        <template #trigger>
                          <NTag size="small" type="success" :bordered="false">已找到对应账目</NTag>
                        </template>
                        {{ getAiSuggestion(stmt.id)?.reason || '系统已匹配待确认账目，去下一步认领即可' }}
                      </NTooltip>
                      <NTag v-else size="small" type="warning">待匹配</NTag>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </NCard>
        </div>
      </NSpin>
    </div>

    <!-- Step 2 Layout: 流水认领与薪酬 (员工薪酬核对 · 智能流水认领与配平) -->
    <div v-if="currentStep === 2">
      <!-- 2.1 员工薪酬核对卡片 (紧凑折叠式，适合不同规模企业) -->
      <NCard :bordered="false" class="report-card mb-4" style="padding: 16px 20px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <i class="ri-user-star-line" style="font-size: 20px; color: var(--solo-primary);"></i>
            <div>
              <span style="font-weight: 600; font-size: 15px; color: var(--solo-text-primary);">员工薪酬与社保发放</span>
              <span v-if="employees.length === 0" style="font-size: 12px; color: var(--solo-text-muted); margin-left: 10px;">
                💡 本月无在职员工薪酬数据（轻资产/一人企业模式无需计提薪酬）
              </span>
              <span v-else style="font-size: 12px; color: var(--solo-text-muted); margin-left: 10px;">
                应发 {{ employees.length }} 人 · 实发薪酬 {{ formatMoney(totalNetSalary) }} · 企业人工总成本 {{ formatMoney(totalCompanyCost) }}
              </span>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <NButton size="tiny" secondary @click="router.push({ path: '/mine/salary', query: targetMonth ? { month: targetMonth } : {} })">
              <template #icon><i class="ri-edit-line" /></template>
              去修改
            </NButton>
            <template v-if="employees.length > 0">
              <NButton size="tiny" secondary @click="showSalaryTable = !showSalaryTable">
                {{ showSalaryTable ? '收起明细' : '查看明细' }}
                <template #icon><i :class="showSalaryTable ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" /></template>
              </NButton>
              <NTag v-if="payrollPaid" size="small" type="success" :bordered="false">✓ 薪酬已发放</NTag>
              <NTag v-else-if="payrollAccrued" size="small" type="info" :bordered="false">已计提待发放</NTag>
              <NTag v-else size="small" type="warning" :bordered="false">待计提记账</NTag>
            </template>
          </div>
        </div>

        <template v-if="employees.length > 0">
          <NGrid :cols="3" :x-gap="16" class="mt-3 mb-3">
            <NGridItem>
              <div class="report-card" style="padding: 12px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                <div style="font-size: 12px; color: var(--solo-text-muted);">本月应发人数</div>
                <div style="font-size: 18px; font-weight: bold; color: var(--solo-text-primary); margin-top: 2px;">{{ employees.length }} 人</div>
              </div>
            </NGridItem>
            <NGridItem>
              <div class="report-card" style="padding: 12px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                <div style="font-size: 12px; color: var(--solo-text-muted);">本月实发薪酬</div>
                <div style="font-size: 18px; font-weight: bold; color: var(--solo-income); margin-top: 2px;">{{ formatMoney(totalNetSalary) }}</div>
              </div>
            </NGridItem>
            <NGridItem>
              <div class="report-card" style="padding: 12px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                <div style="font-size: 12px; color: var(--solo-text-muted);">企业总人工成本</div>
                <div style="font-size: 18px; font-weight: bold; color: var(--solo-expense); margin-top: 2px;">{{ formatMoney(totalCompanyCost) }}</div>
              </div>
            </NGridItem>
          </NGrid>

          <!-- 展开后的员工薪酬明细 -->
          <div v-show="showSalaryTable" class="report-table-wrapper mb-3" style="max-height: 260px; overflow-y: auto;">
            <table class="report-table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>部门</th>
                  <th>基本工资</th>
                  <th>绩效/奖金</th>
                  <th>五险一金单位缴存</th>
                  <th style="text-align: right;">实发工资</th>
                  <th style="text-align: right;">企业总成本</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="emp in employees" :key="emp.name">
                  <td>{{ emp.name }}</td>
                  <td>{{ emp.dept }}</td>
                  <td>{{ formatMoney(emp.base) }}</td>
                  <td>{{ formatMoney(emp.bonus) }}</td>
                  <td>{{ formatMoney(emp.social) }}</td>
                  <td class="amount-cell income">{{ formatMoney(emp.net) }}</td>
                  <td class="amount-cell expense">{{ formatMoney(emp.cost) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <NAlert
            v-if="payrollPaymentSuggestions.length"
            type="info"
            :show-icon="false"
            style="margin-bottom: 12px;"
          >
            <div style="font-weight: 600; margin-bottom: 6px;">银行流水发现待发工资</div>
            <div
              v-for="item in payrollPaymentSuggestions"
              :key="item.statement.id"
              style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px;"
            >
              <span>{{ item.month }} 工资 · 实发 ¥{{ item.netSalary }}</span>
              <span style="color: var(--solo-text-muted); font-size: 12px;">{{ item.statement.date }} {{ item.statement.description }}</span>
              <NTag size="tiny" :type="item.confidence === 'high' ? 'success' : 'warning'" :bordered="false">
                {{ item.confidence === 'high' ? '高置信' : '请确认' }}
              </NTag>
              <NButton
                size="tiny"
                type="primary"
                :loading="applyingSalaryStatementId === item.statement.id"
                @click="handleApplySalaryPayment(item)"
              >
                确认发放并核销
              </NButton>
            </div>
          </NAlert>

          <NAlert
            v-if="payrollPendingReview"
            type="warning"
            :show-icon="false"
            style="margin-bottom: 12px;"
          >
            工资计提凭证正在等待审核（复核模式下需他人过账）。审核通过后此处会自动更新为「已计提」，届时才能继续发放与缴纳。
          </NAlert>

          <NSpace justify="end">
            <NButton size="small" type="warning" :disabled="payrollAccrued || payrollPendingReview" @click="handleAccruePayroll">
              {{ payrollAccrued ? '✓ 已经计提记账' : (payrollPendingReview ? '计提凭证待审核' : '一键计提记账') }}
            </NButton>
            <NButton size="small" type="primary" :disabled="!payrollAccrued || payrollPaid || payrollPendingReview" @click="handlePayPayroll">
              {{ payrollPaid ? '✓ 已经确认发放' : '确认发放记账' }}
            </NButton>
            <NButton
              v-if="payrollHasSocial && !payrollPendingReview"
              size="small"
              type="info"
              :disabled="!payrollAccrued || payrollSsPaid"
              @click="handlePaySocial"
            >
              {{ payrollSsPaid ? '✓ 社保已缴纳' : '确认缴纳社保' }}
            </NButton>
            <NButton
              v-if="payrollHasSocial && !payrollPendingReview"
              size="small"
              type="info"
              :disabled="!payrollAccrued || payrollHfPaid"
              @click="handlePayHousingFund"
            >
              {{ payrollHfPaid ? '✓ 公积金已缴纳' : '确认缴纳公积金' }}
            </NButton>
            <NButton
              v-if="payrollHasTax && !payrollPendingReview"
              size="small"
              type="info"
              :disabled="!payrollAccrued || payrollTaxPaid"
              @click="handlePayTax"
            >
              {{ payrollTaxPaid ? '✓ 个税已缴纳' : '确认缴纳个税' }}
            </NButton>
          </NSpace>
        </template>
      </NCard>

      <!-- 2.2 智能流水认领与配平工作台 -->
      <NCard :bordered="false" class="report-card mb-4" style="padding: 16px 20px;">
        <div class="page-header" style="margin-bottom: 12px !important;">
          <div class="header-left">
            <h3 class="report-section-title" style="margin: 0;"><i class="ri-bank-card-line" style="color: var(--solo-primary);"></i> 第二步：银行流水智能对账与配平</h3>
            <p class="report-subtitle">系统已自动比对银行对账单与记账单据，请复核推荐的入账科目并确认。</p>
          </div>
          <div class="header-right">
            <NTag size="small" type="info" :bordered="false">流水对账核销</NTag>
          </div>
        </div>

        <!-- Progress and Hero Action Bar -->
        <div class="feed-summary-hero">
          <div class="feed-hero-left">
            <div class="feed-hero-stat-circle">
              <div class="stat-number">{{ reconciliationStats.total ? `${reconciliationStats.rate}%` : '—' }}</div>
              <div class="stat-label">对账完成率</div>
            </div>
            <div class="feed-hero-metrics">
              <div class="metric-pill">本月共 <strong>{{ reconciliationStats.total }}</strong> 笔流水</div>
              <div class="metric-pill success" v-if="highConfidenceStatements.length > 0">
                <i class="ri-flashlight-line"></i> 系统已自动配平 <strong>{{ highConfidenceStatements.length }}</strong> 笔
              </div>
              <div class="metric-pill warning" v-if="unmatchedStatements.length - highConfidenceStatements.length > 0">
                待确认用途 <strong>{{ unmatchedStatements.length - highConfidenceStatements.length }}</strong> 笔
              </div>
              <div class="metric-pill" v-if="unmatchedStatements.length === 0 && reconciliationStats.total > 0">
                <i class="ri-check-line" style="color: var(--solo-income);"></i> 本月流水已全部完成
              </div>
              <div class="metric-pill warning" v-if="reconciliationStats.linkedUnposted?.length > 0">
                <i class="ri-error-warning-line"></i> <strong>{{ reconciliationStats.linkedUnposted.length }}</strong> 笔核销凭证尚未过账，锁账前需过账
              </div>
            </div>
          </div>
          <div class="feed-hero-right">
            <NButton
              v-if="batchTargetStatements.length > 0"
              type="primary"
              size="medium"
              class="hero-action-btn"
              :loading="batchConfirming"
              @click="handleBatchConfirmRules"
            >
              <template #icon><i class="ri-check-double-line" /></template>
              批量确认已识别流水 ({{ batchTargetStatements.length }} 笔)
            </NButton>
            <div v-else-if="unmatchedStatements.length === 0 && reconciliationStats.total > 0" style="display: flex; align-items: center; gap: 6px; color: var(--solo-income); font-weight: 600;">
              <i class="ri-checkbox-circle-fill" style="font-size: 20px;"></i> 全部流水已认领平衡
            </div>
          </div>
        </div>

        <!-- 老板往来小账本汇总：让老板一眼看懂确认后的应收、应付、可免税报销垫付款 -->
        <div v-if="bossLedgerSummary.hasAny" class="boss-ledger-summary-bar">
          <!-- 卡片 1: 客户还欠公司（应收账款） -->
          <div
            class="boss-ledger-card"
            :class="{ clickable: bossLedgerSummary.customerOwes > 0 || bossLedgerSummary.customerCount > 0 }"
            @click="(bossLedgerSummary.customerOwes > 0 || bossLedgerSummary.customerCount > 0) && openBossLedgerDetail('customer_owes')"
          >
            <div class="boss-ledger-card-header">
              <span class="boss-ledger-card-title">
                <i class="ri-hand-coin-line" style="color: var(--solo-income);"></i> 客户还欠公司（应收账款）
              </span>
              <span v-if="bossLedgerSummary.customerOwes > 0 || bossLedgerSummary.customerCount > 0" class="boss-ledger-card-link">
                查看明细 <i class="ri-arrow-right-s-line"></i>
              </span>
            </div>
            <div class="boss-ledger-card-amount">
              {{ formatMoney(bossLedgerSummary.customerOwes) }}
            </div>
            <div class="boss-ledger-card-meta">
              {{ bossLedgerSummary.customerCount > 0 ? `${bossLedgerSummary.customerCount} 张销项票待客户打款，回款时自动核销` : '本月无客户欠款' }}
            </div>
          </div>

          <!-- 卡片 2: 公司还欠供应商（应付挂账） -->
          <div
            class="boss-ledger-card"
            :class="{ clickable: bossLedgerSummary.companyOwesSupplier > 0 || bossLedgerSummary.supplierCount > 0 }"
            @click="(bossLedgerSummary.companyOwesSupplier > 0 || bossLedgerSummary.supplierCount > 0) && openBossLedgerDetail('supplier_owes')"
          >
            <div class="boss-ledger-card-header">
              <span class="boss-ledger-card-title">
                <i class="ri-bill-line" style="color: #d97706;"></i> 公司还欠供应商（应付挂账）
              </span>
              <span v-if="bossLedgerSummary.companyOwesSupplier > 0 || bossLedgerSummary.supplierCount > 0" class="boss-ledger-card-link">
                查看明细 <i class="ri-arrow-right-s-line"></i>
              </span>
            </div>
            <div class="boss-ledger-card-amount">
              {{ formatMoney(bossLedgerSummary.companyOwesSupplier) }}
            </div>
            <div class="boss-ledger-card-meta">
              {{ bossLedgerSummary.supplierCount > 0 ? `${bossLedgerSummary.supplierCount} 张进项票未付款，后续公户打款时核销` : '本月无待付供应商款' }}
            </div>
          </div>

          <!-- 卡片 3: 公司欠老板/员工（可免税报销提取） -->
          <div
            class="boss-ledger-card"
            :class="{ clickable: bossLedgerSummary.companyOwesBossAndEmp > 0 }"
            @click="bossLedgerSummary.companyOwesBossAndEmp > 0 && openBossLedgerDetail('boss_and_emp')"
          >
            <div class="boss-ledger-card-header">
              <span class="boss-ledger-card-title">
                <i class="ri-user-heart-line" style="color: var(--solo-primary);"></i> 公司欠老板/员工（可免税报销提取）
              </span>
              <span v-if="bossLedgerSummary.companyOwesBossAndEmp > 0" class="boss-ledger-card-link">
                查看明细 <i class="ri-arrow-right-s-line"></i>
              </span>
            </div>
            <div class="boss-ledger-card-amount" style="color: var(--solo-primary);">
              {{ formatMoney(bossLedgerSummary.companyOwesBossAndEmp) }}
            </div>
            <div class="boss-ledger-card-meta">
              <template v-if="bossLedgerSummary.companyOwesBossAndEmp > 0">
                老板垫付 {{ formatMoney(bossLedgerSummary.bossOnly) }} · 员工垫付 {{ formatMoney(bossLedgerSummary.empOnly) }}（公户有钱可随时转回私卡）
              </template>
              <template v-else>本月无个人垫付报销款</template>
            </div>
          </div>
        </div>

        <!-- 待确认的发票与日常收支：仅列公户没找到流水的发票和日常收支手工账，排除工资/手续费/股东投入等纯会计分录 -->
        <template v-if="pendingEntryList.length > 0">
          <div class="feed-section-header">
            <div style="font-weight: 600; font-size: 14px; color: var(--solo-text-primary); display: flex; align-items: center; gap: 6px;">
              <i class="ri-file-list-3-line" style="color: var(--solo-primary);"></i>
              <span>待确认款项的单据 ({{ pendingEntryList.length }})</span>
            </div>
            <span style="font-size: 12px; color: var(--solo-text-muted);">以下发票或日常收支在本月公户未找到对应流水，点一下确认实际收付情况即可自动入账</span>
          </div>
          <NAlert v-if="monthStatements.length === 0" type="warning" :bordered="false" style="margin-bottom: 12px;">
            本月还没有导入公户流水，请先确认是否漏导银行流水；若确定是通过个人垫付或尚未收付款，请直接点选下方胶囊按钮。
          </NAlert>

          <div class="pending-entry-list">
            <div
              v-for="entry in pendingEntryList"
              :key="entry.id"
              class="pending-entry-card"
              :class="{ 'pending-entry-card--expanded': expandedDispositionEntryId === entry.id }"
            >
              <div class="pending-entry-main">
                <div class="pending-entry-title">
                  <NTag v-if="entry.source === 'invoice_import' || entry.invoiceNumber" size="small" :bordered="false" :type="entry.direction === 'income' ? 'success' : 'warning'">
                    {{ entry.direction === 'income' ? '销项发票' : '进项发票' }}
                  </NTag>
                  <NTag v-else size="small" :bordered="false" type="info">
                    {{ entry.direction === 'income' ? '日常收入' : '日常支出' }}
                  </NTag>
                  <span class="pending-entry-date">{{ entry.date }}</span>
                  <strong>{{ entry.title }}</strong>
                </div>
                <div class="pending-entry-meta">
                  <span>用途分类：{{ entry.category || '日常经营' }}</span>
                  <span v-if="findStatementCandidateForEntry(entry)" class="pending-entry-hint">
                    {{ statementCandidateText(findStatementCandidateForEntry(entry)) }}
                  </span>
                  <span v-else class="pending-entry-hint muted">
                    {{ entry.direction === 'income' ? '公户未查到该笔收款，请确认款项去向：' : '公户未查到该笔扣款，请确认谁付的钱：' }}
                  </span>
                </div>
              </div>
              <div class="pending-entry-amount" :class="entryIsOutflow(entry) ? 'expense' : 'income'">
                {{ entryIsOutflow(entry) ? '-' : '+' }}{{ formatMoney(Math.abs(Number(entry.amount) || 0)) }}
              </div>
              <div class="pending-entry-actions">
                <NButton
                  v-if="findStatementCandidateForEntry(entry)"
                  size="tiny"
                  round
                  type="primary"
                  secondary
                  @click="handleEntryPaid(entry)"
                >关联疑似流水</NButton>

                <!-- 手改分录入口：标题/分类/科目都可改，发票挂账草稿重算时保持贷 2241 挂账 -->
                <NButton size="tiny" round type="primary" @click="openPendingEntryEdit(entry)">修改</NButton>

                <!-- 第一步：只确认「谁付的钱」，不落库；第二步在下方原地展开确认科目 -->
                <template v-if="entry.direction === 'expense'">
                  <NButton
                    size="tiny"
                    round
                    secondary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.payer === 'boss_personal' }"
                    @click="beginDisposition(entry, { payer: 'boss_personal', label: '老板个人垫付（计入公司欠老板款，可免税转回私卡）' })"
                  >老板垫付</NButton>
                  <NDropdown
                    trigger="click"
                    :options="employeePayerOptions"
                    @select="(key: string) => beginDisposition(entry, { payer: key, label: key.includes(':') ? `员工垫付（计入公司欠${key.split(':')[1]}报销款）` : '员工垫付（计入员工待报销款）' })"
                  >
                    <NButton
                      size="tiny"
                      round
                      secondary
                      :class="{ 'disposition-chip--active': !!dispositionChoice[entry.id]?.payer?.startsWith('employee_personal') }"
                    >
                      员工垫付 <i class="ri-arrow-down-s-line" style="font-size: 11px; margin-left: 2px;"></i>
                    </NButton>
                  </NDropdown>
                  <NButton
                    size="tiny"
                    round
                    secondary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.paymentStatus === 'unpaid' }"
                    @click="beginDisposition(entry, { paymentStatus: 'unpaid', label: '还没付款（计入应付供应商挂账）' })"
                  >还没付款</NButton>
                  <NButton
                    size="tiny"
                    round
                    quaternary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.payer === 'prepaid_deduct' }"
                    @click="beginDisposition(entry, { payer: 'prepaid_deduct', label: '预存扣款（冲减前期预付余额）' })"
                  >预存扣款</NButton>
                </template>

                <!-- 收入（销项发票 / 日常收入手工账）胶囊选项 -->
                <template v-else>
                  <NButton
                    size="tiny"
                    round
                    secondary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.paymentStatus === 'unpaid' }"
                    @click="beginDisposition(entry, { paymentStatus: 'unpaid', label: '客户未付款（计入客户应收欠款）' })"
                  >客户未付款</NButton>
                  <NButton
                    size="tiny"
                    round
                    secondary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.payer === 'boss_personal' }"
                    @click="beginDisposition(entry, { payer: 'boss_personal', label: '老板私户代收（计入老板暂存公款）' })"
                  >老板私户收</NButton>
                  <NButton
                    size="tiny"
                    round
                    secondary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.payer === 'platform_balance' }"
                    @click="beginDisposition(entry, { payer: 'platform_balance', label: '在平台未提现（计入平台待提现余额）' })"
                  >在平台未提现</NButton>
                  <NButton
                    size="tiny"
                    round
                    quaternary
                    :class="{ 'disposition-chip--active': dispositionChoice[entry.id]?.payer === 'advance_offset' }"
                    @click="beginDisposition(entry, { payer: 'advance_offset', label: '冲前期预收（核销前期预收客户款）' })"
                  >冲前期预收</NButton>
                </template>
              </div>

              <!-- 第二步：像待处理银行流水一样，先看建议科目与借贷，再「认可入账」 -->
              <div v-if="expandedDispositionEntryId === entry.id" class="pending-entry-plan">
                <div class="pending-entry-plan-head">
                  <span class="pending-entry-plan-title">
                    第二步：这笔算什么{{ entry.direction === 'income' ? '收入' : '支出' }}
                  </span>
                  <NTag size="small" :bordered="false" type="info">
                    {{ dispositionChoice[entry.id]?.label || '已选付款方式' }}
                  </NTag>
                  <NButton text size="tiny" @click="cancelDisposition(entry)">更改付款人</NButton>
                </div>
                <div class="pending-entry-plan-category">
                  <span class="plan-label">建议分类</span>
                  <strong>{{ dispositionPreview[entry.id]?.category || entry.category || '日常经营' }}</strong>
                  <NTag
                    v-if="dispositionPreview[entry.id]?.preview?.source === 'fallback'"
                    size="tiny"
                    :bordered="false"
                    type="warning"
                  >兜底科目，建议换分类</NTag>
                  <NTag
                    v-else-if="dispositionRecommendationReason(entry)"
                    size="tiny"
                    :bordered="false"
                    type="success"
                  >
                    AI 建议：{{ dispositionRecommendationReason(entry) }}
                  </NTag>
                  <NTag v-else size="tiny" :bordered="false">当前分类</NTag>
                  <span v-if="dispositionRecommendation[entry.id] === 'loading'" class="plan-muted">AI 正在看这张票…</span>
                </div>
                <div class="pending-entry-plan-lines">
                  <NSpin v-if="dispositionPreview[entry.id]?.loading" size="small" />
                  <template v-else-if="dispositionPreviewOf(entry)">
                    <div
                      v-for="line in dispositionPreviewDebitLines(entry)"
                      :key="`d-${line.accountCode}`"
                      class="plan-line"
                    >
                      <span class="plan-side debit">借</span>
                      <span class="plan-code">{{ line.accountCode }}</span>
                      <span class="plan-name">{{ line.accountName }}</span>
                      <span v-if="line.partnerName" class="plan-partner">往来：{{ line.partnerName }}</span>
                      <span class="plan-amount">¥{{ line.amount }}</span>
                    </div>
                    <div
                      v-for="line in dispositionPreviewCreditLines(entry)"
                      :key="`c-${line.accountCode}`"
                      class="plan-line"
                    >
                      <span class="plan-side credit">贷</span>
                      <span class="plan-code">{{ line.accountCode }}</span>
                      <span class="plan-name">{{ line.accountName }}</span>
                      <span v-if="line.partnerName" class="plan-partner">往来：{{ line.partnerName }}</span>
                      <span class="plan-amount">¥{{ line.amount }}</span>
                    </div>
                  </template>
                  <div v-else class="plan-error">
                    {{ dispositionPreview[entry.id]?.error || '暂时无法预览科目' }}
                  </div>
                </div>
                <div v-if="dispositionPreview[entry.id]?.preview?.willReverse" class="plan-note">
                  该凭证已过账，确认后会红冲原凭证并按新科目重记。
                </div>
                <div
                  v-for="warning in dispositionPreview[entry.id]?.preview?.warnings || []"
                  :key="warning.code"
                  class="plan-note warning"
                >{{ warning.message }}</div>
                <div class="pending-entry-plan-controls">
                  <NSelect
                    :value="dispositionCategorySelected[entry.id]"
                    :options="dispositionCategorySelectGroups(entry)"
                    placeholder="换分类"
                    size="small"
                    style="width: 220px;"
                    :menu-props="CATEGORY_MENU_PROPS"
                    @update:value="(value: string) => setDispositionCategory(entry, value)"
                  />
                  <NButton
                    size="small"
                    type="primary"
                    :loading="dispositionSubmittingId === entry.id"
                    :disabled="!!dispositionPreview[entry.id]?.loading"
                    @click="acceptPendingDisposition(entry)"
                  >认可入账</NButton>
                  <NButton size="small" secondary @click="openPendingEntryEdit(entry)">详细记账</NButton>
                </div>
                <!-- 研发归集：账号内有覆盖本月的有效项目时常驻，选中分类即就地补明细 -->
                <div
                  v-if="dispositionRdEnabled(entry) && isRdCategoryValue(dispositionCategorySelected[entry.id] || '')"
                  class="pending-entry-rd"
                >
                  <div class="pending-entry-rd-head">
                    <span class="pending-entry-rd-title">
                      <i class="ri-flask-line" />研发项目归集
                    </span>
                    <span class="pending-entry-rd-hint">
                      这类费用可以直接计入研发项目，留存加计扣除底稿
                    </span>
                    <NButton v-if="effectiveRdProjectOptions.length === 0" text size="tiny" @click="goCreateRdProject">
                      去新建项目
                    </NButton>
                  </div>
                  <div class="pending-entry-rd-fields">
                    <NSelect
                      :value="rdChoiceOf(entry).projectId || null"
                      :options="effectiveRdProjectOptions"
                      placeholder="选择研发项目"
                      size="small"
                      filterable
                      style="min-width: 200px;"
                      @update:value="(value: string) => setDispositionRdField(entry, 'projectId', value)"
                    />
                    <NSelect
                      :value="rdChoiceOf(entry).expenseType || null"
                      :options="rdExpenseTypeOptions"
                      placeholder="选择费用类别"
                      size="small"
                      style="min-width: 180px;"
                      @update:value="(value: string) => setDispositionRdField(entry, 'expenseType', value)"
                    />
                    <NInput
                      :value="rdChoiceOf(entry).allocationBasis"
                      placeholder="分摊依据（直接归集可留空）"
                      size="small"
                      maxlength="200"
                      style="min-width: 220px; flex: 1;"
                      @update:value="(value: string) => setDispositionRdField(entry, 'allocationBasis', value)"
                    />
                  </div>
                  <div class="pending-entry-rd-note">
                    按凭证金额 100% 直接归集。多项目分摊请用「详细记账」拆分金额与分摊方法。
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 已确认去向的单据（展示大白话入账结果，消灭糊涂账） -->
        <div v-if="confirmedExceptionEntries.length > 0" style="margin-bottom: 20px;">
          <NCollapse>
            <NCollapseItem :title="`已确认入账去向的单据 (${confirmedExceptionEntries.length})`" name="confirmed_dispositions">
              <template #header-extra>
                <span style="font-size: 12px; color: var(--solo-text-muted);">已自动归入对应往来账本；未过账记录可重新选择</span>
              </template>
              <div class="pending-entry-list" style="margin-top: 8px;">
                <div v-for="entry in confirmedExceptionEntries" :key="entry.id" class="pending-entry-card" style="opacity: 0.92; background: var(--solo-card-bg, #fff);">
                  <div class="pending-entry-main">
                    <div class="pending-entry-title">
                      <NTag size="small" :bordered="false" type="info">{{ entry.source === 'invoice_import' ? '发票' : '账目' }}</NTag>
                      <span class="pending-entry-date">{{ entry.date }}</span>
                      <strong>{{ entry.title }}</strong>
                      <NTag size="small" type="success" :bordered="false" style="margin-left: 8px;">
                        {{ formatDispositionTag(entry) }}
                      </NTag>
                    </div>
                    <div class="pending-entry-meta">
                      <span>{{ entry.category || '日常经营' }}</span>
                      <span style="color: var(--solo-primary); font-weight: 500;">
                        · {{ formatDispositionExplanation(entry) }}
                      </span>
                    </div>
                  </div>
                  <div class="pending-entry-amount" :class="entryIsOutflow(entry) ? 'expense' : 'income'">
                    {{ entryIsOutflow(entry) ? '-' : '+' }}{{ formatMoney(Math.abs(Number(entry.amount) || 0)) }}
                  </div>
                  <div class="pending-entry-actions">
                    <NButton
                      v-if="canResetEntryDisposition(entry)"
                      size="tiny"
                      text
                      :loading="dispositionResettingId === entry.id"
                      :disabled="!!dispositionResettingId && dispositionResettingId !== entry.id"
                      @click="resetEntryDisposition(entry)"
                    >
                      重新选择
                    </NButton>
                  </div>
                </div>
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>

        <!-- Pending Statements Feed -->
        <div class="feed-section-header" style="flex-wrap: wrap; gap: 10px;">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
            <div style="font-weight: 600; font-size: 14px; color: var(--solo-text-primary); display: flex; align-items: center; gap: 6px;">
              <i class="ri-time-line" style="color: var(--solo-primary);"></i>
              <span>待处理银行流水 ({{ unmatchedStatements.length }})</span>
            </div>
            <div v-if="unmatchedStatements.length > 0" class="tier-filter-pills">
              <button
                type="button"
                class="tier-pill"
                :class="{ active: statementTierFilter === 'all' }"
                @click="statementTierFilter = 'all'"
              >
                全部 ({{ unmatchedStatements.length }})
              </button>
              <button
                type="button"
                class="tier-pill"
                :class="{ active: statementTierFilter === 'ready' }"
                @click="statementTierFilter = 'ready'"
              >
                已识别 ({{ unmatchedStatements.length - attentionStatements.length }})
              </button>
              <button
                v-if="attentionStatements.length > 0"
                type="button"
                class="tier-pill attention"
                :class="{ active: statementTierFilter === 'attention' }"
                @click="statementTierFilter = 'attention'"
              >
                疑义待定 ({{ attentionStatements.length }})
              </button>
            </div>
          </div>
          <div v-if="unmatchedStatements.length > 0" style="display: flex; align-items: center; gap: 14px; flex-wrap: wrap;">
            <NCheckbox
              v-if="selectableBatchStatements.length > 0"
              size="small"
              :checked="allSelectableChecked"
              :indeterminate="someSelectableChecked"
              @update:checked="toggleSelectAllStatements"
            >
              <span style="font-size: 12px; color: var(--solo-text-secondary);">全选可确认 ({{ selectableBatchStatements.length }})</span>
            </NCheckbox>
            <NTooltip trigger="hover" placement="top" :style="{ maxWidth: '320px' }">
              <template #trigger>
                <span>
                  <NCheckbox v-model:checked="rememberRuleOnConfirm" size="small">
                    <span style="font-size: 12px; color: var(--solo-text-secondary);">记住此规则（相同对方/摘要自动入账）</span>
                  </NCheckbox>
                </span>
              </template>
              勾选后，确认或调整某笔流水的入账科目时，系统会自动记住该对方户名或摘要特征；后续导入同类流水将直接按 98% 记忆规则自动归类。
            </NTooltip>
          </div>
        </div>

        <!-- Empty state -->
        <div v-if="unmatchedStatements.length === 0" class="feed-empty-box">
          <i class="ri-shield-check-line feed-empty-icon"></i>
          <div style="font-size: 15px; font-weight: 600; color: var(--solo-text-primary); margin-top: 8px;">
            本月所有银行流水已处理完成
          </div>
          <div style="font-size: 12px; color: var(--solo-text-muted); margin-top: 4px;">
            资金进出已 100% 对应入账，可直接进入下一步进行关账合规检查。
          </div>
        </div>

        <!-- Statement Cards List (Compact Reconcile Feed) -->
        <div v-else class="feed-card-list">
          <div
            v-for="stmt in filteredUnmatchedStatements"
            :key="stmt.id"
            class="feed-card reconcile-row"
            :class="{
              'high-conf': (getAiSuggestion(stmt.id)?.confidence || 0) >= 0.95 && !isNeedsAttentionStatement(stmt),
              'needs-attention': isNeedsAttentionStatement(stmt),
            }"
          >
            <!-- 事实区：批量勾选 + 方向Tag + 日期 + 交易对方与附言 + 金额 -->
            <div class="reconcile-cell-fact">
              <div class="fact-lead">
                <NCheckbox
                  size="small"
                  :checked="selectedStatementIds.includes(stmt.id)"
                  :disabled="!isSelectableForBatch(stmt)"
                  @update:checked="(val: boolean) => toggleStatementSelection(stmt.id, val)"
                />
                <NTag
                  size="small"
                  :type="stmt.amount >= 0 ? 'success' : 'error'"
                  :bordered="false"
                  style="font-weight: 600; flex-shrink: 0;"
                >
                  {{ stmt.amount >= 0 ? '收款' : '付款' }}
                </NTag>
                <span class="fact-date">{{ stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '未指定日期' }}</span>
                <span class="fact-desc" :title="stmt.description">{{ stmt.description }}</span>
              </div>
              <div class="fact-amount" :class="stmt.amount >= 0 ? 'income' : 'expense'">
                {{ stmt.amount >= 0 ? '+' : '' }}{{ formatMoney(stmt.amount) }}
              </div>
            </div>

            <!-- 第二行：方案与核算依据 + 操作调整 -->
            <div class="reconcile-row-detail">
              <div class="reconcile-cell-plan">
              <!-- Case 1: 匹配记账单据 -->
              <div v-if="getAiSuggestion(stmt.id)?.type === 'match'" class="plan-badge match">
                <i class="ri-link-m" style="font-size: 14px; color: var(--solo-primary);"></i>
                <span class="plan-label">匹配单据：</span>
                <NButton
                  text
                  size="tiny"
                  type="primary"
                  class="feed-rec-link"
                  @click="openVoucherModal(getAiSuggestion(stmt.id)?.matchedEntryId || getAiSuggestion(stmt.id)?.matchedEntryIds?.[0])"
                >
                  <strong>{{ getAiSuggestion(stmt.id)?.matchedEntry?.title || getAiSuggestion(stmt.id)?.reason }}</strong>
                </NButton>
              </div>

              <!-- Case 2: 匹配未关联发票 -->
              <div v-else-if="findMatchingInvoiceForStatement(stmt)" class="plan-badge match">
                <i class="ri-file-list-3-line" style="font-size: 14px; color: var(--solo-primary);"></i>
                <span class="plan-label">吻合发票：</span>
                <strong class="ellipsis" :title="findMatchingInvoiceForStatement(stmt)?.title" style="max-width: 180px;">
                  {{ findMatchingInvoiceForStatement(stmt)?.title }}
                </strong>
                <span style="font-size: 11px; color: var(--solo-text-muted);">({{ formatMoney(findMatchingInvoiceForStatement(stmt)?.amount || 0) }})</span>
              </div>

              <!-- Case 3: 建议分类（带 Tooltip 解释） -->
              <div
                v-else-if="getAiSuggestion(stmt.id)?.suggestedEntry?.category"
                class="plan-badge"
                :class="isFallbackSuggestion(stmt) ? 'fallback' : 'standard'"
              >
                <span class="plan-label">{{ isFallbackSuggestion(stmt) ? '系统兜底分类：' : '建议分类：' }}</span>
                <strong class="plan-category">{{ effectiveSuggestion(stmt)?.suggestedEntry?.category }}</strong>
                <span v-if="suggestionAccountPreview(stmt)" class="plan-account-preview">
                  <i class="ri-arrow-right-line" style="font-size: 11px;"></i>
                  {{ suggestionAccountPreview(stmt)!.name }} {{ suggestionAccountPreview(stmt)!.code }}
                </span>

                <NTooltip trigger="hover" placement="top" :style="{ maxWidth: '360px' }">
                  <template #trigger>
                    <span
                      class="plan-evidence-pill"
                      :class="
                        isFallbackSuggestion(stmt)
                          ? 'attention'
                          : getAiSuggestion(stmt.id)?.rule === 'company_memory_rule'
                            ? 'memory-rule'
                            : 'verified'
                      "
                    >
                      <i
                        :class="
                          isFallbackSuggestion(stmt)
                            ? 'ri-alert-line'
                            : getAiSuggestion(stmt.id)?.rule === 'company_memory_rule'
                              ? 'ri-bookmark-3-line'
                              : 'ri-shield-check-line'
                        "
                        style="font-size: 11px;"
                      ></i>
                      <span>{{ suggestionBadgeLabel(stmt) }}</span>
                    </span>
                  </template>
                  <div style="font-size: 12px; line-height: 1.5;">
                    <div style="font-weight: 600; margin-bottom: 3px; color: var(--solo-primary);">记账依据分析</div>
                    <div>{{ getAiSuggestion(stmt.id)?.reason || '系统根据往来特征与账目特征智能匹配' }}</div>
                  </div>
                </NTooltip>
              </div>

              <!-- Case 4: 无推荐分类 -->
              <div v-else class="plan-badge none">
                <i class="ri-question-line" style="font-size: 13px;"></i>
                <span class="plan-label">待指定核算科目</span>
              </div>

              <!-- 模糊分类候选：正确科目取决于账外事实，展开候选由用户点选；问 AI 仅分析不代操作。
                   必须放在 Case 链之后——中间插入 v-if 会截断 v-if/v-else 链，让「待指定核算科目」误显 -->
              <div v-if="ambiguityGroupOf(stmt).length" class="plan-alternatives">
                <button
                  v-for="altCat in ambiguityGroupOf(stmt)"
                  :key="altCat"
                  type="button"
                  class="plan-alt-chip"
                  :class="{ selected: effectiveSuggestion(stmt)?.suggestedEntry?.category === altCat }"
                  @click="rowCategoryChoice[stmt.id] = altCat"
                >
                  {{ altCat }}<span v-if="altAccountText(stmt, altCat)" class="plan-alt-account">{{ altAccountText(stmt, altCat) }}</span>
                </button>
                <button type="button" class="plan-ask-ai" @click="openAskAi(stmt)">
                  <i class="ri-chat-sm-2-line" style="font-size: 12px;"></i> 问 AI
                </button>
              </div>
              </div>

              <!-- 操作与调整区 -->
              <div class="reconcile-cell-action">
                <!-- 快捷选择科目 -->
                <template v-if="!hasExistingEntrySuggestion(stmt)">
                  <NSelect
                    size="small"
                    class="reconcile-category-select"
                    placeholder="换分类..."
                    :options="stmt.amount >= 0 ? bossIncomeOptions : bossExpenseOptions"
                    :disabled="quickSubmittingId === stmt.id"
                    :menu-props="CATEGORY_MENU_PROPS"
                    @update:value="(val: string) => handleQuickSelectCategory(stmt, val)"
                  />
                </template>

                <!-- 主确认按钮 -->
                <NButton
                  v-if="getAiSuggestion(stmt.id)?.type === 'match'"
                  size="small"
                  type="primary"
                  secondary
                  :loading="quickSubmittingId === stmt.id"
                  @click="handleAcceptSuggested(stmt)"
                >
                  确认配对
                </NButton>
                <NButton
                  v-else-if="findMatchingInvoiceForStatement(stmt)"
                  size="small"
                  type="primary"
                  secondary
                  :loading="quickSubmittingId === stmt.id"
                  @click="handleMatchInvoiceDirect(stmt, findMatchingInvoiceForStatement(stmt)!)"
                >
                  关联发票
                </NButton>
                <NButton
                  v-else-if="getAiSuggestion(stmt.id)?.suggestedEntry?.category"
                  size="small"
                  type="primary"
                  :loading="quickSubmittingId === stmt.id"
                  @click="handleAcceptSuggested(stmt)"
                >
                  <template #icon><i class="ri-check-line" /></template>
                  {{ isFallbackSuggestion(stmt) ? '按此入账' : '认可入账' }}
                </NButton>

                <!-- 详细记账：完整表单（改标题/研发明细/二级科目），非配对场景的次级入口 -->
                <NButton
                  v-if="!hasExistingEntrySuggestion(stmt)"
                  size="small"
                  quaternary
                  class="reconcile-detail-btn"
                  @click="openManualClassify(stmt, getAiSuggestion(stmt.id)?.suggestedEntry)"
                >
                  <template #icon><i class="ri-edit-line" /></template>
                  详细记账
                </NButton>

                <!-- 已有账目待配对：唯一入口（此时下拉已隐藏） -->
                <NButton
                  v-if="hasExistingEntrySuggestion(stmt)"
                  size="small"
                  quaternary
                  class="reconcile-detail-btn"
                  @click="openManualClassify(stmt, getAiSuggestion(stmt.id)?.suggestedEntry)"
                >
                  <template #icon><i class="ri-link-m" /></template>
                  核对已有账目
                </NButton>
                <NButton
                  v-if="matchUndoableEntries(stmt).length"
                  size="small"
                  quaternary
                  type="error"
                  @click="undoMatchEntry(stmt)"
                >
                  撤销原入账
                </NButton>
              </div>
            </div>
          </div>
        </div>

        <!-- Historical Unmatched (Collapsible) -->
        <div v-if="historicalUnmatchedStatements.length > 0" style="margin-top: 16px;">
          <NCollapse>
            <NCollapseItem :title="`历史月份遗留未认领流水 (${historicalUnmatchedStatements.length})`" name="history">
              <div class="feed-card-list">
                <div v-for="stmt in historicalUnmatchedStatements" :key="stmt.id" class="feed-card reconcile-row" style="opacity: 0.92;">
                  <div class="reconcile-cell-fact">
                    <div class="fact-lead">
                      <NTag size="small" type="warning" :bordered="false" style="flex-shrink: 0;">历史遗留</NTag>
                      <span class="fact-date">{{ stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '' }}</span>
                      <span class="fact-desc" :title="stmt.description">{{ stmt.description }}</span>
                    </div>
                    <div class="fact-amount" :class="stmt.amount >= 0 ? 'income' : 'expense'">
                      {{ stmt.amount >= 0 ? '+' : '' }}{{ formatMoney(stmt.amount) }}
                    </div>
                  </div>
                  <div class="reconcile-row-detail">
                    <div class="reconcile-cell-plan">
                      <span class="plan-label" style="font-size: 12px; color: var(--solo-text-muted);">往期未结流水，确认补记期间后入账</span>
                    </div>
                    <div class="reconcile-cell-action">
                      <NSelect
                        size="small"
                        class="reconcile-category-select"
                        placeholder="补记归类入账..."
                        :options="stmt.amount >= 0 ? bossIncomeOptions : bossExpenseOptions"
                        :menu-props="CATEGORY_MENU_PROPS"
                        @update:value="(val: string) => handleQuickSelectCategory(stmt, val)"
                      />
                      <NButton size="small" quaternary class="reconcile-detail-btn" @click="openManualClassify(stmt)">
                        <template #icon><i class="ri-edit-line" /></template>
                        详情记账
                      </NButton>
                    </div>
                  </div>
                </div>
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>

        <!-- Completed Statements (Collapsible) -->
        <!-- 只列持久化配对：建议对（待确认配对）属于待处理行，混进来会让撤销变成"刷新又回来" -->
        <div v-if="persistedMatchedPairs.length > 0" style="margin-top: 20px;">
          <NCollapse>
            <NCollapseItem :title="`已认领并完成流水 (${persistedMatchedPairs.length} 笔)`" name="completed">
              <div class="feed-completed-list">
                <div v-for="pair in persistedMatchedPairs" :key="pair.id" class="feed-completed-item">
                  <div style="display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;">
                    <i class="ri-checkbox-circle-fill" style="color: var(--solo-income); font-size: 16px;"></i>
                    <span style="font-size: 12px; color: var(--solo-text-muted);">{{ pair.bank?.date || pair.bank?.transaction_date?.slice(0, 10) }}</span>
                    <span style="font-weight: 500; font-size: 13px; color: var(--solo-text-primary);" class="ellipsis">{{ pair.bank?.title || pair.bank?.description }}</span>
                    <i class="ri-arrow-right-line" style="color: var(--solo-text-muted); font-size: 12px;"></i>
                    <NTag size="tiny" type="info" :bordered="false">{{ pairCategoryPreview(pair) || '已配平' }}</NTag>
                  </div>
                  <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-weight: 600; font-size: 13px;" :class="pair.bank?.amount >= 0 ? 'income' : 'expense'">
                      {{ formatMoney(pair.bank?.amount) }}
                    </span>
                    <NButton size="tiny" quaternary type="primary" @click="openPairCategoryDialog(pair)">修改分类</NButton>
                    <NButton size="tiny" quaternary type="error" @click="deletePair(pair.id)">撤销</NButton>
                  </div>
                </div>
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>
      </NCard>
    </div>

    <!-- Step 3 Layout: Closing audit & ceremony / Closed CFO diagnostic report -->
    <div v-if="currentStep === 3">
      <!-- 3.1 未结账视图：合规体检与关账确认 -->
      <div v-if="!closed" class="closing-step-container">
        <NCard :bordered="false" class="report-card mb-4 readiness-overview">
          <NSpin :show="loading || closingLoading">
            <div class="readiness-heading">
              <div>
                <p class="eyebrow">第三步：检查并锁账</p>
                <h3>{{ targetMonth }} 关账检查</h3>
                <p class="report-subtitle">系统自动核对凭证、银行流水与报表是否 100% 勾稽。每次规范封账，都在为公司积累经得起授信与融资尽调的数字资产。</p>
              </div>
              <div class="readiness-count" :class="{ ready: closingReady && !hasBlockingReconciliationRisk, zero: isZeroRecordMonth }">
                <template v-if="isZeroRecordMonth">
                  <strong style="color: var(--solo-text-muted);">0 笔</strong>
                  <span>本月暂无业务记账</span>
                </template>
                <template v-else>
                  <strong>{{ closingTasks.length === 0 ? '0' : closingTasks.length }}</strong>
                  <span>{{ closingTasks.length === 0 ? (canBatchPostAndClose ? `结账时将批量过账 ${closeDraftCount} 张` : '全部就绪，可以结账') : '步待办后可结账' }}</span>
                </template>
              </div>
            </div>

            <!-- 五项核对常显条：关账前先看这五项，细节仍可在下方折叠清单展开 -->
            <div class="five-check-strip" role="group" aria-label="五项核对">
              <div class="five-check-strip-head">
                <span>五项核对</span>
                <small v-if="!isZeroRecordMonth && otherReadinessPending > 0">另有 {{ otherReadinessPending }} 项准备项待处理</small>
                <small v-else>{{ isZeroRecordMonth ? '本月无业务记录' : '结账前自动核验' }}</small>
              </div>
              <div class="five-check-chips">
                <div
                  v-for="item in fiveCheckItems"
                  :key="item.code"
                  class="five-check-chip"
                  :class="item.status"
                  :title="item.detail"
                >
                  <span class="five-check-chip-icon"><i :class="readinessStatusIcon(item.status)" /></span>
                  <span class="five-check-chip-copy">
                    <strong>{{ item.label }}</strong>
                    <small>{{ isZeroRecordMonth ? '本月无记录' : readinessStatusLabel(item.status) }}</small>
                  </span>
                </div>
              </div>
            </div>

            <!-- 零业务/未记账提示横幅 -->
            <div v-if="isZeroRecordMonth" class="zero-record-card mb-4">
              <div class="zero-record-head">
                <div class="zero-record-icon"><i class="ri-alert-line" /></div>
                <div class="zero-record-text">
                  <h4>检测到 {{ targetMonth }} 暂未录入任何记账凭证、发票或银行流水</h4>
                  <p>
                    如果您本月有实际经营业务，请务必先录入单据或导入网银流水，避免直接结账导致财务报表与税表全为 0；若本月确实无经营业务（零申报），可勾选确认后进行零业务结账。
                  </p>
                </div>
              </div>
              <div class="zero-record-actions">
                <NButton size="small" type="primary" secondary @click="goStep(1)">
                  <template #icon><i class="ri-folder-shared-line" /></template>
                  去归集发票与流水 (Step 1)
                </NButton>
                <NButton size="small" @click="router.push('/copilot')">
                  <template #icon><i class="ri-edit-line" /></template>
                  先记一笔
                </NButton>
                <div class="zero-record-confirm">
                  <NCheckbox v-model:checked="confirmZeroActivity">
                    本月确无任何实际经营收支（确认按零申报/零业务结账）
                  </NCheckbox>
                </div>
              </div>
            </div>

            <!-- 待办任务队列：同一根因聚合为一个任务，完成即少一步 -->
            <div v-if="!isZeroRecordMonth && closingTasks.length > 0" class="closing-task-list mb-4" aria-label="结账待办任务">
              <div v-for="(task, taskIndex) in closingTasks" :key="task.id" class="closing-task-card" :class="task.severity">
                <div class="task-index" :class="task.severity">{{ taskIndex + 1 }}</div>
                <div class="task-body">
                  <h4>{{ task.title }}</h4>
                  <p v-if="task.detail">{{ task.detail }}</p>
                  <small v-if="task.affects.length">这会影响：{{ task.affects.join(' · ') }}</small>
                  <NCollapse v-if="task.matchDetails?.length" class="task-details-collapse">
                    <NCollapseItem :title="`查看 ${task.matchDetails.length} 笔明细`" name="details">
                      <div v-for="detailItem in task.matchDetails" :key="detailItem.statementId" class="task-detail-row">
                        <span class="task-detail-date">{{ detailItem.date }}</span>
                        <span class="task-detail-desc">{{ detailItem.description }}</span>
                        <NButton size="tiny" secondary type="primary" @click="runTaskAction({ label: '去配对', kind: 'match', statementId: detailItem.statementId })">去配对</NButton>
                      </div>
                    </NCollapseItem>
                  </NCollapse>
                </div>
                <div class="task-actions">
                  <NButton
                    v-if="task.id === 'post'"
                    size="small"
                    type="primary"
                    secondary
                    :loading="batchPostingLoading"
                    @click="openBatchPostConfirm"
                  >
                    <template #icon><i class="ri-check-double-line" /></template>
                    去批量过账
                  </NButton>
                  <NButton
                    v-for="action in task.actions"
                    :key="action.label"
                    size="small"
                    :type="action.kind === 'run-closing' ? 'primary' : 'default'"
                    @click="runTaskAction(action)"
                  >
                    {{ action.label }}
                    <template #icon><i class="ri-arrow-right-line" /></template>
                  </NButton>
                </div>
              </div>
            </div>

            <!-- 跨期与补记提示：不阻断结账，但必须让用户看见并知情 -->
            <div v-if="closingNotices.length > 0" class="closing-notice-list mb-4" aria-label="跨期与补记提示">
              <NAlert
                v-for="notice in closingNotices"
                :key="notice.code"
                type="warning"
                :title="noticeTitle(notice)"
                class="closing-notice"
              >
                <p class="notice-message">{{ notice.message }}</p>
                <NCollapse v-if="notice.records?.length" class="notice-records">
                  <NCollapseItem :title="`查看 ${notice.records.length} 条明细`" name="notice-records">
                    <div
                      v-for="(record, index) in notice.records"
                      :key="index"
                      class="notice-record-row"
                    >
                      <span class="notice-record-date">{{ record.businessDate }}</span>
                      <span class="notice-record-title">{{ noticeRecordTitle(record) }}</span>
                      <span class="notice-record-amount">¥{{ noticeRecordAmount(record) }}</span>
                      <small class="notice-record-meta">{{ noticeRecordMeta(record) }}</small>
                    </div>
                  </NCollapseItem>
                </NCollapse>
                <div class="notice-actions">
                  <!-- 启用前数据没有补记台账可导出（导出的会是空表，误导人） -->
                  <NButton
                    v-if="notice.code !== 'PRE_BOOK_START_ACTIVITY'"
                    size="tiny"
                    secondary
                    @click="exportBackfillNotice(notice)"
                  >
                    <template #icon><i class="ri-download-2-line" /></template>
                    导出清单
                  </NButton>
                  <NButton
                    v-if="notice.code === 'PRE_BOOK_START_ACTIVITY'"
                    size="tiny"
                    secondary
                    type="primary"
                    @click="router.push('/mine/companies')"
                  >
                    去改启用日期
                  </NButton>
                  <NButton
                    v-if="notice.code === 'CROSS_PERIOD_PENDING'"
                    size="tiny"
                    secondary
                    type="primary"
                    @click="goStep(1)"
                  >
                    去补记
                  </NButton>
                </div>
              </NAlert>
            </div>

            <!-- 全部核验明细（五项已在顶部常显，这里补充折旧、摊销、工资、关账顺序等准备项） -->
            <NCollapse class="readiness-detail-collapse mb-4">
              <NCollapseItem title="展开全部核验明细（含折旧、摊销、工资、关账顺序）" name="checks">
            <div class="readiness-list" aria-label="结账准备清单">
              <div
                v-for="item in readinessItems"
                :key="item.code"
                class="readiness-row"
                :class="item.status"
              >
                <span class="readiness-row-icon"><i :class="readinessStatusIcon(item.status)" /></span>
                <span class="readiness-row-copy">
                  <strong>{{ item.title }}</strong>
                  <small>{{ item.detail }}</small>
                  <small v-for="(issue, index) in (periodCloseChecks?.checks?.find((check: any) => check.code === item.code)?.issues || [])" :key="index">
                    {{ issue.date || '' }} {{ issue.description || '' }} {{ issue.reason || issue.message }}
                    <NButton v-if="issue.entryId || issue.entryIds?.[0]" text size="tiny" @click="openVoucherModal(issue.entryId || issue.entryIds[0])">查看凭证</NButton>
                  </small>
                </span>
                <span class="readiness-row-status">{{ isZeroRecordMonth ? '本月无记录' : readinessStatusLabel(item.status) }}</span>
                <NButton
                  v-if="item.status !== 'complete' && item.status !== 'not_required'"
                  size="tiny"
                  secondary
                  @click="handleReadinessAction(item)"
                >
                  {{ item.actionLabel }}
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
              </div>
            </div>
              </NCollapseItem>
            </NCollapse>

            <!-- 高风险阻断拦截提示 -->
            <div v-if="hasBlockingReconciliationRisk" class="closing-risk-lock mb-4" style="margin-top: 12px; padding: 12px 16px; background: rgba(239, 68, 68, 0.08); border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.2); display: flex; align-items: center; gap: 10px; color: var(--solo-expense);">
              <i class="ri-error-warning-fill" style="font-size: 18px;"></i>
              <span>检测到 {{ reconciliationHealthSummary.highRiskCount }} 项高风险对账问题（如金额不符或未核对），修复后方可关账。</span>
              <NButton size="tiny" type="error" secondary @click="goStep(2)">前往流水认领</NButton>
            </div>

            <!-- 关账操作栏 -->
            <div class="closing-action-box" style="text-align: center; padding: 24px 0 12px; border-top: 1px solid var(--solo-border-light);">
              <NButton
                type="primary"
                size="large"
                :loading="closingLoading"
                :disabled="!closingReady || hasBlockingReconciliationRisk"
                style="padding: 0 36px; font-weight: 600;"
                @click="runClosing()"
              >
                <template #icon><i class="ri-lock-line" /></template>
                <template v-if="hasBlockingPayroll">
                  尚有 {{ employees.length }} 名员工工资待计提，暂无法结账
                </template>
                <template v-else-if="isZeroRecordMonth && !confirmZeroActivity">
                  本月暂无记账，请先补录或确认零申报
                </template>
                <template v-else-if="isZeroRecordMonth && confirmZeroActivity">
                  确认按【零业务/零申报】结账
                </template>
                <template v-else-if="closingReady && !hasBlockingReconciliationRisk">
                  <template v-if="canBatchPostAndClose">确认过账 {{ closeDraftCount }} 张并结账</template>
                  <template v-else>确认一键完成本月结账</template>
                </template>
                <template v-else>
                  尚有阻断项待处理，暂无法结账
                </template>
              </NButton>
              <p style="font-size: 12px; color: var(--solo-text-muted); margin-top: 8px;">
                {{ canBatchPostAndClose ? `确认后将批量过账本月 ${closeDraftCount} 张待过账凭证，自动生成期末损益结转凭证后完成关账；如有凭证需复核或其他核验未通过，整次操作会回滚。` : isZeroRecordMonth ? '以零业务结账后，本期资产负债表与利润表将以零收支锁定归档' : '结账后本期账册将锁定归档，自动生成期末损益结转凭证（12 月含年度利润结转），并生成 AI 财务体检与经营诊断报告' }}
              </p>
            </div>
          </NSpin>
        </NCard>
      </div>

      <!-- 5.2 已结账视图：成果交代 + 3 大后续动作看板 -->
      <div v-else class="ceremony-dashboard">
        <!-- 结账成功大卡片：保留原版大气仪式感与大方块指标，做大做足成就感 -->
        <NCard :bordered="false" class="report-card ceremony-hero-card mb-4">
          <div class="checkmark-wrapper">
            <i class="ri-checkbox-circle-fill" />
          </div>

          <div class="ceremony-hero-content">
            <div class="ceremony-achievement-pill">
              <i class="ri-shield-star-line" />
              <span>独立掌握财务 · 账目主权归于自己 · 尽调就绪</span>
            </div>
            <h2 class="ceremony-title">{{ targetMonth }} 结账成功</h2>
            <p class="ceremony-desc">
              不依赖代账中介，本月流水全清、发票全齐、账目全平。你的公司又积累了一个月可随时交付银行授信、审计与投资人尽调的标准数字资产。
            </p>
            <div class="ceremony-meta-tags">
              <NTag size="small" type="success" :bordered="false">本期账册已锁定归档</NTag>
              <NTag size="small" :bordered="false">
                损益结转凭证：{{ closingVoucherRefs.join('、') || '已自动结转' }}
              </NTag>
              <NTag
                v-if="vatExemptAmount > 0"
                size="small"
                type="warning"
                :bordered="false"
                :title="vatExemptVoucherRefs.length ? `免税凭证号：${vatExemptVoucherRefs.join('、')}` : ''"
              >
                已享受小微免税 {{ formatMoney(vatExemptAmount) }}
              </NTag>
            </div>
          </div>

          <!-- 原样保留并放大的 3 列经营指标大方块（强化数据色彩） -->
          <div class="metrics-row ceremony-metrics-row">
            <div class="metric-box ceremony-metric-box is-income">
              <div class="metric-box-label">
                <i class="ri-arrow-right-up-line" />
                <span>{{ targetMonth }} 经营收入</span>
              </div>
              <span class="metric-box-val is-income">{{ formatMoney(monthlyIncome) }}</span>
            </div>
            <div class="metric-box ceremony-metric-box is-expense">
              <div class="metric-box-label">
                <i class="ri-arrow-right-down-line" />
                <span>{{ targetMonth }} 经营成本</span>
              </div>
              <span class="metric-box-val is-expense">{{ formatMoney(monthlyExpense) }}</span>
            </div>
            <div class="metric-box ceremony-metric-box is-profit" :class="netProfit >= 0 ? 'is-positive' : 'is-negative'">
              <div class="metric-box-label">
                <i :class="netProfit >= 0 ? 'ri-line-chart-line' : 'ri-funds-line'" />
                <span>本月净利润</span>
              </div>
              <span class="metric-box-val" :class="netProfit >= 0 ? 'is-income' : 'is-expense'">
                {{ netProfit >= 0 ? '+' : '' }}{{ formatMoney(netProfit) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 核心区：三大后续动作卡 (Bento 3 栏网格) -->
        <div class="ceremony-actions-grid mb-3">
          <!-- 动作 1: CFO 财务报告 -->
          <div class="ceremony-action-card">
            <div class="action-card-header">
              <div class="action-card-title-group">
                <div class="action-card-icon is-cfo"><i class="ri-file-chart-line" /></div>
                <div>
                  <h4 class="action-card-title">CFO 财务报告</h4>
                  <span class="action-card-sub">成果交付 · 经营复盘</span>
                </div>
              </div>
              <NTag size="tiny" :type="cfoDiagnosis ? 'success' : 'default'" :bordered="false">
                {{ cfoDiagnosis ? '已生成' : '待生成' }}
              </NTag>
            </div>
            <div class="action-card-body">
              <template v-if="diagnosisLoading">
                <div class="action-card-loading">
                  <NSpin size="small" />
                  <span>正在生成 AI 财务诊断…</span>
                </div>
              </template>
              <template v-else-if="cfoDiagnosis">
                <p class="action-diagnosis-snippet">
                  {{ cfoDiagnosis.diagnosis }}
                </p>
                <div v-if="cfoDiagnosis.urgentActions?.length" class="action-urgent-tag">
                  <i class="ri-error-warning-line" /> 关注：{{ cfoDiagnosis.urgentActions[0] }}
                </div>
              </template>
              <template v-else>
                <p class="action-card-desc">
                  按需调用 AI 深度分析本月利润走势、收支结构健康度，并生成经营改善建议与风险提示。
                </p>
              </template>
            </div>
            <div class="action-card-footer">
              <div class="action-card-buttons">
                <NButton
                  type="primary"
                  size="small"
                  :loading="diagnosisLoading"
                  @click="cfoDiagnosis ? (showDiagnosisModal = true) : generateDiagnosis()"
                >
                  <template #icon><i :class="cfoDiagnosis ? 'ri-eye-line' : 'ri-sparkling-line'" /></template>
                  {{ cfoDiagnosis ? '查看完整诊断' : '生成 AI 诊断' }}
                </NButton>
                <NButton size="small" quaternary @click="downloadReport">
                  <template #icon><i class="ri-download-2-line" /></template>
                  导出 PDF 包
                </NButton>
              </div>
            </div>
          </div>

          <!-- 动作 2: 按期申报税费 -->
          <div class="ceremony-action-card">
            <div class="action-card-header">
              <div class="action-card-title-group">
                <div class="action-card-icon is-tax"><i class="ri-government-line" /></div>
                <div>
                  <h4 class="action-card-title">按期申报税费</h4>
                  <span class="action-card-sub">法定履约 · 电子税局</span>
                </div>
              </div>
              <NTag size="tiny" type="info" :bordered="false">
                {{ isQuarterEndMonth ? `${closedQuarter} 季报` : '当期申报' }}
              </NTag>
            </div>
            <div class="action-card-body">
              <div class="action-tax-target">
                <strong>{{ isQuarterEndMonth ? `${closedQuarter} 季度财务数据已封账固化` : '本期账册已满足申报核算条件' }}</strong>
              </div>
              <p class="action-card-desc">
                {{ isQuarterEndMonth
                  ? '增值税免税额度、小微所得税测算已齐备，建议立即前往电子税局进行季度抄报。'
                  : '税费申报底稿已生成，包含增值税及附加税费、个税扣缴计算，支持核对后申报。' }}
              </p>
            </div>
            <div class="action-card-footer">
              <div class="action-card-buttons">
                <NButton
                  type="primary"
                  size="small"
                  @click="router.push(isQuarterEndMonth ? `/tax-filing?period=${closedQuarter}` : '/tax-filing')"
                >
                  前往税务申报
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
                <NButton
                  size="small"
                  text
                  type="primary"
                  @click="mainFilingObligations.length ? goFilingPackage(mainFilingObligations[0]) : router.push('/tax-filing')"
                >
                  查看申报底稿
                </NButton>
              </div>
            </div>
          </div>

          <!-- 动作 3: 其他结账待办（若无待办则展示已清空状态，保持 closing-todo-card 语义满足单测） -->
          <div v-if="closingTodos.topItems.length" class="ceremony-action-card closing-todo-card">
            <div class="action-card-header">
              <div class="action-card-title-group">
                <div class="action-card-icon is-todo"><i class="ri-task-line" /></div>
                <div>
                  <h4 class="action-card-title">结账后待办</h4>
                  <span class="action-card-sub">经营闭环 · 往来跟踪</span>
                </div>
              </div>
              <span class="closing-todo-count">{{ closingTodos.pendingCount }} 件待处理</span>
            </div>
            <div class="action-card-body">
              <ul class="closing-todo-list">
                <li
                  v-for="item in closingTodos.topItems.slice(0, 2)"
                  :key="item.key"
                  class="closing-todo-row"
                  @click="router.push(item.route)"
                >
                  <span class="closing-todo-dot" :class="item.level === 'high' ? 'is-high' : 'is-medium'" />
                  <div class="closing-todo-copy">
                    <strong>{{ item.title }}</strong>
                    <span v-if="item.desc">{{ item.desc }}</span>
                  </div>
                  <span class="closing-todo-cta">{{ item.routeLabel }} <i class="ri-arrow-right-s-line" /></span>
                </li>
              </ul>
            </div>
            <div class="action-card-footer">
              <div class="action-card-buttons">
                <NButton type="primary" secondary size="small" @click="router.push('/todos')">
                  查看全部
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
              </div>
            </div>
          </div>

          <!-- 动作 3 零待办态：当没有待办时，展示轻量账目健康态（不含 closing-todo-card class 满足单测） -->
          <div v-else class="ceremony-action-card is-todo-empty">
            <div class="action-card-header">
              <div class="action-card-title-group">
                <div class="action-card-icon is-done"><i class="ri-checkbox-circle-line" /></div>
                <div>
                  <h4 class="action-card-title">经营待办已清空</h4>
                  <span class="action-card-sub">状态健康 · 规范合规</span>
                </div>
              </div>
              <NTag size="tiny" type="success" :bordered="false">全部就绪</NTag>
            </div>
            <div class="action-card-body">
              <p class="action-card-desc">
                本月无滞留未办事务，往来款项与发票核对清晰，账目保持健康状态。
              </p>
            </div>
            <div class="action-card-footer">
              <div class="action-card-buttons">
                <NButton secondary size="small" @click="router.push('/todos')">
                  待办中心
                </NButton>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部轻量辅助工具栏 -->
        <div class="ceremony-footer-bar">
          <div class="ceremony-footer-links">
            <button type="button" class="footer-link-btn" @click="router.push(`/ledger/book?month=${targetMonth}`)">
              <i class="ri-book-2-line" /> 导出电子账簿（总账/明细账）
            </button>
            <span class="footer-link-divider">·</span>
            <button type="button" class="footer-link-btn" @click="router.push('/mine/audit-share')">
              <i class="ri-shield-check-line" /> 专家协审链接
            </button>
            <span class="footer-link-divider">·</span>
            <button type="button" class="footer-link-btn is-warn" @click="reopenVisible = true">
              <i class="ri-lock-unlock-line" /> 反结账本期
            </button>
          </div>
          <NButton type="primary" size="medium" @click="router.push('/')">
            返回首页工作台
          </NButton>
        </div>
      </div>
    </div>

    <!-- Step Navigation Buttons (第一步的下一步入口在步骤条汇总行，避免重复) -->
    <div v-if="!closed && currentStep > 1" class="no-print" style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px;">
      <NButton :disabled="currentStep === 1" @click="prevStep">
        <template #icon><i class="ri-arrow-left-line" /></template>
        上一步
      </NButton>
      <div v-if="currentStep < 3" style="display: flex; gap: 8px;">
        <NButton type="primary" @click="nextStep">
          {{ currentStep === 2 ? '下一步：前往关账检查' : '下一步：流水认领与薪酬' }}
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>
    </div>
    <!-- ── Reopen Period Dialog（已拆入 ReopenPeriodDialog.vue） ────── -->
    <ReopenPeriodDialog v-model:show="reopenVisible" :month="targetMonth" @reopened="handleReopened" />

    <!-- ── Manual Classify Dialog ─────────────────────────────────── -->
    <NModal
      v-model:show="showManualDialog"
      :mask-closable="true"
      style="width: 480px; max-width: 96vw;"
      :auto-focus="false"
    >
      <div class="manual-dialog">
        <!-- Header -->
        <div class="manual-dialog-header">
          <div class="manual-dialog-icon"><i class="ri-quill-pen-line"></i></div>
          <div>
            <div class="manual-dialog-title">分类记账草稿</div>
            <div class="manual-dialog-subtitle">AI 仅提供建议，确认后才会记账并对账</div>
          </div>
        </div>

        <!-- Statement Info -->
        <div class="manual-stmt-info">
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-calendar-line"></i> 交易日期</span>
            <span class="manual-stmt-val">{{ manualTarget?.transaction_date?.slice(0, 10) }}</span>
          </div>
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-bank-card-line"></i> 银行摘要</span>
            <span class="manual-stmt-val" style="font-size: 12px; color: var(--solo-text-secondary);">{{ manualTarget?.description }}</span>
          </div>
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-money-cny-circle-line"></i> 交易金额</span>
            <span class="manual-stmt-val" :class="(manualTarget?.amount ?? 0) >= 0 ? 'income' : 'expense'" style="font-size: 16px; font-weight: 700;">
              {{ formatMoney(manualTarget?.amount) }}
            </span>
          </div>
        </div>

        <NDivider style="margin: 12px 0;" />

        <!-- Form -->
        <div class="manual-form">
          <div class="manual-form-item">
            <label class="manual-form-label">记账标题 <span style="color: var(--solo-expense);">*</span></label>
            <NInput
              v-model:value="manualForm.title"
              placeholder="请输入记账标题，如：5月工资代发、住房公积金缴纳"
              maxlength="50"
              show-count
            />
          </div>

          <div class="manual-form-item">
            <label class="manual-form-label">业务说明（选填）</label>
            <NInput
              v-model:value="manualForm.businessContext"
              type="textarea"
              placeholder="例如：这笔技术服务费用于研发项目"
              :rows="2"
              maxlength="200"
            />
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 6px;">
              <span style="font-size: 11px; color: var(--solo-text-muted);">补充用途后，可让 AI 重新生成分类草稿</span>
              <NButton size="tiny" secondary :loading="manualAiLoading" @click="refreshManualAiSuggestion">
                根据说明更新 AI 建议
              </NButton>
            </div>
          </div>

          <div v-if="manualSuggestionMeta.reason" class="manual-account-preview">
            <span>AI 理由：{{ manualSuggestionMeta.reason }}</span>
            <span v-if="manualSuggestionMeta.confidence !== undefined" style="margin-left: 12px;">置信度：<b>{{ Math.round((manualSuggestionMeta.confidence || 0) * 100) }}%</b></span>
          </div>

          <div v-if="manualForm.suggestedSubAccount" class="manual-account-preview" style="margin-top: 12px;">
            <NCheckbox v-model:checked="manualForm.useSuggestedSubAccount">
              建议新建二级科目 <b>{{ manualForm.suggestedSubAccount.name }}</b>（父科目 {{ manualForm.suggestedSubAccount.parentCode }}）
            </NCheckbox>
          </div>

          <div class="manual-form-item">
            <label class="manual-form-label">账目分类 <span style="color: var(--solo-expense);">*</span></label>
            <NSelect
              v-model:value="manualForm.categoryValue"
              :options="CATEGORY_OPTIONS"
              placeholder="请选择科目分类"
              :render-label="(opt: any) => opt.disabled ? opt.label : `${opt.label}`"
            />
            <!-- Direction & Account preview -->
            <div v-if="manualForm.categoryValue && !manualForm.categoryValue.startsWith('__')" class="manual-account-preview">
              <span>科目代码：<b>{{ manualForm.categoryValue.split('|')[2] }}</b></span>
              <span style="margin-left: 12px;">科目名称：<b>{{ manualForm.categoryValue.split('|')[3] }}</b></span>
              <span style="margin-left: 12px;">方向：
                <NTag size="tiny" :type="manualForm.categoryValue.startsWith('income') ? 'success' : manualForm.categoryValue.startsWith('expense') ? 'error' : 'warning'" :bordered="false">
                  {{ categoryValueNatureLabel(manualForm.categoryValue) }}
                </NTag>
              </span>
            </div>
          </div>

          <div v-if="isRdCategoryValue(manualForm.categoryValue)" class="manual-form-item">
            <label class="manual-form-label">研发归集 <span style="color: var(--solo-expense);">*</span></label>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
              <NSelect
                v-model:value="manualForm.rdProjectId"
                :options="rdProjectOptions"
                placeholder="选择研发项目"
                filterable
              />
              <NSelect
                v-model:value="manualForm.rdExpenseType"
                :options="rdExpenseTypeOptions"
                placeholder="选择费用类别"
              />
            </div>
            <NInput
              v-model:value="manualForm.rdAllocationBasis"
              style="margin-top: 10px;"
              placeholder="分摊依据（直接归集可选，如合同或领料单编号）"
              maxlength="200"
            />
            <NText depth="3" style="display: block; margin-top: 8px; font-size: 12px;">
              找不到研发项目？
              <NButton text type="primary" size="tiny" @click="goCreateRdProject">去研发项目页新建</NButton>
            </NText>
          </div>

          <div class="manual-form-item">
            <label class="manual-form-label">备注（选填）</label>
            <NInput
              v-model:value="manualForm.remark"
              type="textarea"
              placeholder="可填写其他备注，如合同编号、付款对象等"
              :rows="2"
              maxlength="200"
            />
          </div>
        </div>

        <!-- Actions -->
        <div class="manual-dialog-actions">
          <NButton @click="showManualDialog = false">取消</NButton>
          <NButton
            type="primary"
            :loading="manualSubmitting"
            :disabled="!manualForm.title.trim() || !manualForm.categoryValue || manualForm.categoryValue.startsWith('__') || (isRdCategoryValue(manualForm.categoryValue) && (!manualForm.rdProjectId || !manualForm.rdExpenseType))"
            @click="handleManualClassify"
          >
            <template #icon><i class="ri-check-line" /></template>
            确认记账并对账
          </NButton>
        </div>
      </div>
    </NModal>

    <!-- ── Existing Pair Category Review Dialog ───────────────────── -->
    <NModal
      v-model:show="showPairCategoryDialog"
      :mask-closable="true"
      style="width: 460px; max-width: 96vw;"
      :auto-focus="false"
    >
      <div class="manual-dialog">
        <div class="manual-dialog-header">
          <div class="manual-dialog-icon"><i class="ri-price-tag-3-line"></i></div>
          <div>
            <div class="manual-dialog-title">确认账目分类</div>
            <div class="manual-dialog-subtitle">确认配对前，请核对该账目的分类与会计科目</div>
          </div>
        </div>

        <div class="manual-stmt-info">
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-file-list-3-line"></i> 账目记录</span>
            <span class="manual-stmt-val">{{ pairCategoryTarget?.invoice?.title }}</span>
          </div>
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-bank-card-line"></i> 银行流水</span>
            <span class="manual-stmt-val" style="font-size: 12px; color: var(--solo-text-secondary);">{{ pairCategoryTarget?.bank?.title }}</span>
          </div>
          <div class="manual-stmt-row">
            <span class="manual-stmt-label"><i class="ri-money-cny-circle-line"></i> 流水金额</span>
            <span class="manual-stmt-val" :class="(pairCategoryTarget?.bank?.amount ?? 0) >= 0 ? 'income' : 'expense'" style="font-size: 16px; font-weight: 700;">
              {{ formatMoney(pairCategoryTarget?.bank?.amount) }}
            </span>
          </div>
        </div>

        <NDivider style="margin: 12px 0;" />

        <div class="manual-form">
          <div class="manual-form-item">
            <label class="manual-form-label">账目标题 <span style="color: var(--solo-expense);">*</span></label>
            <NInput
              v-model:value="pairCategoryForm.title"
              placeholder="请输入账目标题"
              maxlength="50"
              show-count
            />
          </div>

          <div class="manual-form-item">
            <label class="manual-form-label">账目分类 <span style="color: var(--solo-expense);">*</span></label>
            <NSelect
              v-model:value="pairCategoryForm.categoryValue"
              :options="CATEGORY_OPTIONS"
              placeholder="输入关键字搜索分类 / 科目名 / 代码"
              filterable
              :filter="filterCategory"
              :render-label="renderCategoryLabel"
              :virtual-scroll="false"
            />
            <div v-if="pairCategoryForm.categoryValue && !pairCategoryForm.categoryValue.startsWith('__')" class="manual-account-preview">
              <span>科目代码：<b>{{ pairCategoryForm.categoryValue.split('|')[2] }}</b></span>
              <span style="margin-left: 12px;">科目名称：<b>{{ pairCategoryForm.categoryValue.split('|')[3] }}</b></span>
              <span style="margin-left: 12px;">方向：
                <NTag size="tiny" :type="pairCategoryForm.categoryValue.startsWith('income') ? 'success' : pairCategoryForm.categoryValue.startsWith('expense') ? 'error' : 'warning'" :bordered="false">
                  {{ categoryValueNatureLabel(pairCategoryForm.categoryValue) }}
                </NTag>
              </span>
            </div>
          </div>

          <div v-if="isRdCategoryValue(pairCategoryForm.categoryValue)" class="manual-form-item">
            <label class="manual-form-label">研发归集 <span style="color: var(--solo-expense);">*</span></label>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
              <NSelect
                v-model:value="pairCategoryForm.rdProjectId"
                :options="rdProjectOptions"
                placeholder="选择研发项目"
                filterable
              />
              <NSelect
                v-model:value="pairCategoryForm.rdExpenseType"
                :options="rdExpenseTypeOptions"
                placeholder="选择费用类别"
              />
            </div>
            <NInput
              v-model:value="pairCategoryForm.rdAllocationBasis"
              style="margin-top: 10px;"
              placeholder="分摊依据（直接归集可选）"
              maxlength="200"
            />
            <NText depth="3" style="display: block; margin-top: 8px; font-size: 12px;">
              找不到研发项目？
              <NButton text type="primary" size="tiny" @click="goCreateRdProject">去研发项目页新建</NButton>
            </NText>
          </div>
        </div>

        <div class="manual-dialog-actions">
          <NButton @click="showPairCategoryDialog = false">取消</NButton>
          <NButton
            type="primary"
            :loading="pairCategorySubmitting"
            :disabled="!pairCategoryForm.title.trim() || !pairCategoryForm.categoryValue || pairCategoryForm.categoryValue.startsWith('__') || (isRdCategoryValue(pairCategoryForm.categoryValue) && (!pairCategoryForm.rdProjectId || !pairCategoryForm.rdExpenseType))"
            @click="handlePairCategorySave"
          >
            <template #icon><i class="ri-check-line" /></template>
            保存分类
          </NButton>
        </div>
      </div>
    </NModal>

    <!-- 修改草稿凭证：过账前发现分类/标题不对可就地改 -->
    <NModal
      v-model:show="showEditVoucherDialog"
      :mask-closable="true"
      style="width: 460px; max-width: 96vw;"
      :auto-focus="false"
    >
      <div class="manual-dialog">
        <div class="manual-dialog-header">
          <div class="manual-dialog-icon"><i class="ri-edit-line"></i></div>
          <div>
            <div class="manual-dialog-title">详细记账</div>
            <div class="manual-dialog-subtitle">{{ editVoucherTargetEntry?.title || currentVoucherEntry?.voucherRef || currentVoucherEntry?.title || '凭证' }}</div>
          </div>
        </div>

        <div class="manual-form">
          <div v-if="editVoucherDispositionLabel" class="manual-account-preview" style="margin: 0 0 12px;">
            <span>付款人：<b>{{ editVoucherDispositionLabel }}</b></span>
          </div>
          <div v-if="editVoucherIsPosted" class="plan-note" style="margin: 0 0 12px;">
            该凭证已过账：保存会红冲原凭证并按新科目重记；摘要与自定义分录不能改。
          </div>
          <div class="manual-form-item">
            <label class="manual-form-label">账目标题 <span v-if="!editVoucherIsPosted" style="color: var(--solo-expense);">*</span></label>
            <NInput
              v-model:value="editVoucherForm.title"
              placeholder="请输入账目标题"
              maxlength="50"
              show-count
              :disabled="editVoucherIsPosted"
            />
          </div>
          <div class="manual-form-item">
            <label class="manual-form-label">业务说明（AI 会据此重新建议科目）</label>
            <NInput
              v-model:value="editVoucherBusinessContext"
              type="textarea"
              placeholder="例如：腾讯云服务器年费，用于公司官网和小程序运行"
              :autosize="{ minRows: 2, maxRows: 4 }"
              maxlength="200"
              show-count
            />
            <NButton
              size="tiny"
              secondary
              style="margin-top: 8px;"
              :loading="editVoucherAiLoading"
              @click="refreshEditVoucherAi"
            >
              <template #icon><i class="ri-file-edit-line" /></template>
              让 AI 根据说明重新建议
            </NButton>
          </div>
          <div class="manual-form-item">
            <label class="manual-form-label">账目分类 <span style="color: var(--solo-expense);">*</span></label>
            <NSelect
              v-model:value="editVoucherForm.categoryValue"
              :options="CATEGORY_OPTIONS"
              placeholder="请选择科目分类"
              :render-label="(opt: any) => opt.disabled ? opt.label : `${opt.label}`"
              @update:value="() => editVoucherTargetEntry && loadEditVoucherPreview(editVoucherTargetEntry)"
            />
            <div v-if="editVoucherForm.categoryValue && !editVoucherForm.categoryValue.startsWith('__')" class="manual-account-preview">
              <span>科目代码：<b>{{ editVoucherForm.categoryValue.split('|')[2] }}</b></span>
              <span style="margin-left: 12px;">科目名称：<b>{{ editVoucherForm.categoryValue.split('|')[3] }}</b></span>
            </div>
          </div>
          <div v-if="isRdCategoryValue(editVoucherForm.categoryValue)" class="manual-form-item">
            <label class="manual-form-label">研发归集 <span style="color: var(--solo-expense);">*</span></label>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
              <NSelect v-model:value="editVoucherForm.rdProjectId" :options="rdProjectOptions" placeholder="选择研发项目" filterable />
              <NSelect v-model:value="editVoucherForm.rdExpenseType" :options="rdExpenseTypeOptions" placeholder="选择费用类别" />
            </div>
            <NInput v-model:value="editVoucherForm.rdAllocationBasis" style="margin-top: 10px;" placeholder="分摊依据（直接归集可选）" maxlength="200" />
            <NText depth="3" style="display: block; margin-top: 8px; font-size: 12px;">
              找不到研发项目？
              <NButton text type="primary" size="tiny" @click="goCreateRdProject">去研发项目页新建</NButton>
            </NText>
          </div>
          <div class="manual-form-item">
            <label class="manual-form-label">将落账的分录</label>
            <div class="pending-entry-plan-lines" style="margin-top: 0;">
              <NSpin v-if="editVoucherPreview.loading" size="small" />
              <template v-else-if="editVoucherPreview.preview">
                <div
                  v-for="line in editVoucherPreview.preview.debit"
                  :key="`d-${line.accountCode}`"
                  class="plan-line"
                >
                  <span class="plan-side debit">借</span>
                  <span class="plan-code">{{ line.accountCode }}</span>
                  <span class="plan-name">{{ line.accountName }}</span>
                  <span v-if="line.partnerName" class="plan-partner">往来：{{ line.partnerName }}</span>
                  <span class="plan-amount">¥{{ line.amount }}</span>
                </div>
                <div
                  v-for="line in editVoucherPreview.preview.credit"
                  :key="`c-${line.accountCode}`"
                  class="plan-line"
                >
                  <span class="plan-side credit">贷</span>
                  <span class="plan-code">{{ line.accountCode }}</span>
                  <span class="plan-name">{{ line.accountName }}</span>
                  <span v-if="line.partnerName" class="plan-partner">往来：{{ line.partnerName }}</span>
                  <span class="plan-amount">¥{{ line.amount }}</span>
                </div>
                <div
                  v-for="warning in editVoucherPreview.preview.warnings"
                  :key="warning.code"
                  class="plan-note warning"
                >{{ warning.message }}</div>
              </template>
              <div v-else class="plan-error">
                {{ editVoucherPreview.error || '保存前会按所选分类生成分录' }}
              </div>
            </div>
          </div>
        </div>

        <div class="manual-dialog-actions">
          <NButton @click="showEditVoucherDialog = false">取消</NButton>
          <NButton
            type="primary"
            :loading="editVoucherSaving"
            :disabled="editVoucherPreview.loading || (!editVoucherIsPosted && !editVoucherForm.title.trim()) || !editVoucherForm.categoryValue || editVoucherForm.categoryValue.startsWith('__') || (isRdCategoryValue(editVoucherForm.categoryValue) && (!editVoucherForm.rdProjectId || !editVoucherForm.rdExpenseType))"
            @click="saveEditVoucher"
          >
            <template #icon><i class="ri-check-line" /></template>
            保存修改
          </NButton>
        </div>
      </div>
    </NModal>

    <!-- 行内问 AI（已拆入 AskAiPanel.vue）：上下文由父页组装，结论落地走 handleAskAiAccept -->
    <AskAiPanel
      v-model:show="askAiVisible"
      :stmt="askAiStmt"
      :stmt-title="askAiStmt ? cleanStatementTitle(askAiStmt) : ''"
      :prompt="askAiPrompt"
      :candidate-lines="askAiCandidateLines"
      :fallback-category="askAiStmt ? (effectiveSuggestion(askAiStmt)?.suggestedEntry?.category || '') : ''"
      :confirming="!!askAiStmt && quickSubmittingId === askAiStmt.id"
      @accept="handleAskAiAccept"
    />

    <!-- 批量过账确认：列凭证明细 + 说明后果 + 取消/确认 -->
    <NModal v-model:show="batchPostConfirmVisible" style="width: 540px; max-width: 96vw;">
      <div class="batch-post-confirm">
        <div class="batch-post-confirm-header">
          <i class="ri-check-double-line"></i>
          <h3>确认过账 {{ batchPostEntries.length }} 张凭证</h3>
        </div>
        <div class="batch-post-confirm-tip">
          过账 = 把这 {{ batchPostEntries.length }} 张草稿正式记入账簿。之后它们才会参与报表、无形资产摊销、固定资产折旧和银行配对；过账后不能单张撤销，要改只能整月反结账。
        </div>
        <div class="batch-post-confirm-list">
          <div v-for="e in batchPostEntries" :key="e.id" class="batch-post-confirm-row">
            <div class="batch-post-confirm-main">
              <span class="batch-post-confirm-title">{{ e.title }}</span>
              <span class="batch-post-confirm-cat">{{ e.category }}<em v-if="e.accountName"> · {{ e.accountName }}</em></span>
            </div>
            <span class="batch-post-confirm-amt" :class="e.direction === 'income' ? 'income' : 'expense'">
              {{ e.direction === 'income' ? '+' : '-' }}{{ formatMoney(e.amount) }}
            </span>
          </div>
        </div>
        <div class="batch-post-confirm-footer">
          <NButton size="small" quaternary @click="startWalkthroughFromConfirm">
            <template #icon><i class="ri-list-check-2"></i></template>
            逐笔过账
          </NButton>
          <div style="flex: 1;"></div>
          <NButton size="small" @click="batchPostConfirmVisible = false">取消</NButton>
          <NButton size="small" type="primary" :loading="batchPostingLoading" @click="confirmBatchPost">
            确认过账 {{ batchPostEntries.length }} 张
          </NButton>
        </div>
      </div>
    </NModal>

    <!-- 银行余额核对明细：让「查看核对明细」真正展示差额 -->
    <NModal v-model:show="showBankBalanceDetail" preset="card" title="银行余额核对明细" style="max-width: 540px;">
      <div v-if="bankBalanceRecon && bankBalanceRecon.has_data">
        <div class="bank-balance-line"><span>期初余额</span><b>{{ bankBalanceRecon.opening_balance }}</b></div>
        <div class="bank-balance-line"><span>＋ 流入（收到的钱）</span><b>{{ bankBalanceRecon.income_total }}</b></div>
        <div class="bank-balance-line"><span>－ 流出（花掉的钱）</span><b>{{ bankBalanceRecon.expense_total }}</b></div>
        <div class="bank-balance-line"><span>＝ 应该有的期末余额</span><b>{{ bankBalanceRecon.calculated_closing_balance }}</b></div>
        <div class="bank-balance-line"><span>银行流水上的期末余额</span><b>{{ bankBalanceRecon.bank_closing_balance }}</b></div>
        <div v-if="Number(bankBalanceRecon.flow_difference) !== 0" class="bank-balance-warn">
          ⚠ 差了 {{ bankBalanceRecon.flow_difference }}——流水可能漏导了几笔，或某笔的金额/余额列、期初余额填错了。
        </div>
        <div class="bank-balance-line" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--solo-border-light);">
          <span>账面上记录的银行存款</span><b>{{ bankBalanceRecon.book_bank_balance }}</b>
        </div>
        <div class="bank-balance-line"><span>银行实际期末余额</span><b>{{ bankBalanceRecon.bank_closing_balance }}</b></div>
        <div v-if="Number(bankBalanceRecon.book_difference) !== 0" class="bank-balance-warn">
          ⚠ 差了 {{ bankBalanceRecon.book_difference }}——账面和银行对不上；凭证还没过账会少算，过账后一般会平。
        </div>
      </div>
      <div v-else>暂无流水余额数据。</div>
    </NModal>

    <!-- ── In-place Voucher Details Modal (ponytail: zero-redirect inspection) ── -->
    <NModal
      v-model:show="showVoucherModal"
      :mask-closable="true"
      style="width: 620px; max-width: 96vw;"
      :auto-focus="false"
    >
      <div class="manual-dialog">
        <!-- Header -->
        <div class="manual-dialog-header">
          <div class="manual-dialog-icon"><i class="ri-file-text-line"></i></div>
          <div style="flex: 1; min-width: 0;">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
              <span class="manual-dialog-title">{{ currentVoucherEntry?.voucherRef || '记账凭证（草稿）' }}</span>
              <NTag size="small" :type="currentVoucherEntry?.voucherStatus === 'posted' ? 'success' : 'warning'" :bordered="false">
                {{ currentVoucherEntry?.voucherStatus === 'posted' ? '已过账' : '待过账草稿' }}
              </NTag>
              <NTag v-if="currentVoucherEntry?.reconciliationType === 'wechat_settlement'" size="small" type="info" :bordered="false">
                微信结算凭证
              </NTag>
            </div>
            <div class="manual-dialog-subtitle">{{ currentVoucherEntry?.title || '凭证分录与来源检查' }}</div>
          </div>
        </div>

        <div v-if="batchPostWalkthrough" class="walkthrough-progress">
          <i class="ri-guide-line"></i>
          逐笔过账中 · 第 {{ batchPostWalkthroughTotal - batchPostEntries.length + 1 }} / {{ batchPostWalkthroughTotal }} 笔
          <span class="walkthrough-progress-hint">过账后自动进入下一笔</span>
        </div>

        <NSpin :show="voucherModalLoading">
          <!-- Meta info -->
          <div class="voucher-meta-box">
            <div class="voucher-meta-item">
              <span>发生日期：</span>
              <strong>{{ currentVoucherEntry?.occurDate || '-' }}</strong>
            </div>
            <div class="voucher-meta-item">
              <span>业务金额：</span>
              <strong :class="currentVoucherEntry?.direction === 'income' ? 'income' : 'expense'">
                {{ formatMoney(currentVoucherEntry?.amount) }}
              </strong>
            </div>
            <div class="voucher-meta-item" v-if="currentVoucherEntry?.category">
              <span>业务分类：</span>
              <strong>{{ currentVoucherEntry?.category }}</strong>
            </div>
            <div class="voucher-meta-item" v-if="currentVoucherEntry?.reconciliationType">
              <span>对账类型：</span>
              <strong>{{ currentVoucherEntry?.reconciliationType === 'wechat_settlement' ? '微信净额结算' : currentVoucherEntry?.reconciliationType }}</strong>
            </div>
          </div>

          <!-- Voucher Lines Table -->
          <div class="voucher-table-wrapper">
            <table>
              <thead>
                <tr>
                  <th style="width: 54px;">方向</th>
                  <th>会计科目</th>
                  <th style="text-align: right; width: 110px;">借方金额</th>
                  <th style="text-align: right; width: 110px;">贷方金额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(line, idx) in modalVoucherLines" :key="idx">
                  <td>
                    <NTag size="tiny" :type="line.side === 'debit' ? 'info' : 'warning'" :bordered="false">
                      {{ line.side === 'debit' ? '借' : '贷' }}
                    </NTag>
                  </td>
                  <td>
                    <span style="font-family: monospace; font-weight: 700; margin-right: 6px;">{{ line.code }}</span>
                    <span>{{ line.name }}</span>
                  </td>
                  <td style="text-align: right; font-family: monospace;">
                    {{ line.side === 'debit' ? formatMoney(line.amount) : '-' }}
                  </td>
                  <td style="text-align: right; font-family: monospace;">
                    {{ line.side === 'credit' ? formatMoney(line.amount) : '-' }}
                  </td>
                </tr>
                <tr v-if="modalVoucherLines.length === 0">
                  <td colspan="4" style="text-align: center;">
                    <SoloEmpty
                      variant="hint"
                      title="暂无借贷分录"
                      description="该凭证还没有分录行。"
                    />
                  </td>
                </tr>
              </tbody>
              <tfoot v-if="modalVoucherLines.length > 0">
                <tr>
                  <td colspan="2">合计</td>
                  <td style="text-align: right; font-family: monospace;">{{ formatMoney(modalDebitTotal) }}</td>
                  <td style="text-align: right; font-family: monospace;">{{ formatMoney(modalCreditTotal) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          <!-- Actions -->
          <div class="voucher-modal-footer">
            <div>
              <NButton
                v-if="currentVoucherEntry?.id"
                text
                size="tiny"
                type="primary"
                @click="router.push({ path: `/ledger/${currentVoucherEntry.id}`, query: { from: `/closing-wizard?month=${targetMonth}&step=${currentStep}` } })"
              >
                在完整账目页打开 ↗
              </NButton>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <NButton size="small" @click="showVoucherModal = false">关闭</NButton>
              <NButton
                v-if="currentVoucherEntry && currentVoucherEntry.voucherStatus !== 'posted'"
                size="small"
                @click="openEditVoucher"
              >
                <template #icon><i class="ri-edit-line" /></template>
                修改
              </NButton>
              <NButton
                v-if="currentVoucherEntry && currentVoucherEntry.voucherStatus !== 'posted'"
                type="primary"
                size="small"
                :loading="voucherPostingLoading"
                @click="handleModalPostEntry"
              >
                <template #icon><i class="ri-check-line" /></template>
                立即过账
              </NButton>
            </div>
          </div>
        </NSpin>
      </div>
    </NModal>
    <NModal
      v-model:show="backfillConfirm.state.show"
      preset="card"
      title="这批业务要补记到下个月"
      style="max-width: 460px;"
      :mask-closable="false"
    >
      <div class="backfill-copy">
        <p v-if="backfillConfirm.state.businessPeriod">
          <strong>{{ backfillConfirm.state.businessPeriod }}</strong> 已经关账，这批业务将按
          <strong>{{ backfillConfirm.state.targetPeriod }}</strong> 记账，原始单据日期不变。
        </p>
        <p v-else>
          单据所属期间已关账，这批业务将补记到
          <strong>{{ backfillConfirm.state.targetPeriod }}</strong>，原始单据日期不变。
        </p>
        <p style="color: var(--solo-text-muted); font-size: 12px;">
          依据《小企业会计准则》第 88 条，差错在发现当期更正，不追溯调整已关账月份。
        </p>
        <p v-if="backfillConfirm.state.crossQuarter" class="backfill-warn">
          注意：这次补记跨越季度，会同时影响两个季度的所得税预缴；小规模纳税人的季度
          30 万免税判断也可能因此变化。
        </p>
      </div>
      <NInput
        v-model:value="backfillConfirm.state.reason"
        type="textarea"
        placeholder="补记原因（必填，例如：8 月单据 9 月才拿到）"
        :autosize="{ minRows: 2, maxRows: 4 }"
      />
      <template #footer>
        <div class="backfill-actions">
          <NButton size="small" quaternary @click="backfillConfirm.cancel">取消</NButton>
          <NButton size="small" type="primary" @click="backfillConfirm.submit">
            确认补记到 {{ backfillConfirm.state.targetPeriod }}
          </NButton>
        </div>
      </template>
    </NModal>

    <!-- 往来款项明细穿透抽屉 (方案 B: 结账上下文穿透 + 外部业务台账直达) -->
    <SoloDrawer
      v-model:show="showBossLedgerDrawer"
      :title="currentBossLedgerDetail?.title || '款项明细'"
      :width="600"
    >
      <div v-if="currentBossLedgerDetail" class="boss-ledger-drawer-content">
        <!-- 统计与说明 Banner -->
        <div class="boss-ledger-drawer-banner">
          <div class="boss-ledger-drawer-amount-row">
            <div>
              <div class="drawer-amount-label">本月汇总金额</div>
              <div class="drawer-amount-value" :style="{ color: currentBossLedgerDetail.type === 'boss_and_emp' ? 'var(--solo-primary)' : 'var(--solo-text-primary)' }">
                {{ formatMoney(currentBossLedgerDetail.amount) }}
              </div>
            </div>
            <NTag size="medium" :bordered="false" type="info">
              共 {{ currentBossLedgerDetail.entries.length }} 笔单据
            </NTag>
          </div>

          <div v-if="currentBossLedgerDetail.type === 'boss_and_emp' && currentBossLedgerDetail.amount > 0" class="drawer-breakdown-row">
            <span>老板垫付：<strong>{{ formatMoney(currentBossLedgerDetail.bossOnly) }}</strong></span>
            <span class="breakdown-divider">·</span>
            <span>员工垫付：<strong>{{ formatMoney(currentBossLedgerDetail.empOnly) }}</strong></span>
          </div>

          <div class="drawer-tip-box">
            <i class="ri-information-line"></i>
            <span>{{ currentBossLedgerDetail.tip }}</span>
          </div>
        </div>

        <!-- 单据列表 -->
        <div class="boss-ledger-drawer-list-header">
          <span>包含单据明细</span>
          <span style="font-size: 12px; color: var(--solo-text-muted);">可点击凭证查看借贷科目或重新指定去向</span>
        </div>

        <div v-if="currentBossLedgerDetail.entries.length === 0" style="padding: 30px 0;">
          <SoloEmpty description="暂无相关款项单据" />
        </div>

        <div v-else class="boss-ledger-drawer-entries">
          <div
            v-for="entry in currentBossLedgerDetail.entries"
            :key="entry.id"
            class="drawer-entry-card"
          >
            <div class="drawer-entry-main">
              <div class="drawer-entry-header-line">
                <NTag size="small" :bordered="false" :type="entry.direction === 'income' ? 'success' : 'warning'">
                  {{ entry.source === 'invoice_import' || entry.invoiceNumber ? (entry.direction === 'income' ? '销项发票' : '进项发票') : (entry.direction === 'income' ? '日常收入' : '日常支出') }}
                </NTag>
                <span class="drawer-entry-date">{{ entry.date }}</span>
                <strong class="drawer-entry-title" :title="entry.title">{{ entry.title }}</strong>
                <NTag size="small" type="success" :bordered="false" style="margin-left: 4px;">
                  {{ formatDispositionTag(entry) }}
                </NTag>
              </div>
              <div class="drawer-entry-meta-line">
                <span class="drawer-entry-category">{{ entry.category || '日常经营' }}</span>
                <span class="drawer-entry-explanation">
                  · {{ formatDispositionExplanation(entry) }}
                </span>
              </div>
            </div>
            <div class="drawer-entry-side">
              <div class="drawer-entry-amount" :class="entryIsOutflow(entry) ? 'expense' : 'income'">
                {{ entryIsOutflow(entry) ? '-' : '+' }}{{ formatMoney(Math.abs(Number(entry.amount) || 0)) }}
              </div>
              <div class="drawer-entry-actions">
                <NButton size="tiny" secondary type="primary" @click="openVoucherModal(entry.id)">
                  查看凭证
                </NButton>
                <NButton
                  v-if="canResetEntryDisposition(entry)"
                  size="tiny"
                  text
                  :loading="dispositionResettingId === entry.id"
                  :disabled="!!dispositionResettingId && dispositionResettingId !== entry.id"
                  @click="resetEntryDisposition(entry)"
                >
                  重新选择
                </NButton>
              </div>
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
          <NButton
            v-if="currentBossLedgerDetail?.targetRoute"
            type="primary"
            secondary
            @click="handleGoToTarget(currentBossLedgerDetail.targetRoute, currentBossLedgerDetail.targetQuery)"
          >
            {{ currentBossLedgerDetail.targetLabel }}
          </NButton>
          <div v-else></div>
          <NButton @click="showBossLedgerDrawer = false">关闭</NButton>
        </div>
      </template>
    </SoloDrawer>

    <!-- AI CFO 完整诊断弹窗 -->
    <NModal
      v-model:show="showDiagnosisModal"
      preset="card"
      title="AI CFO 月度深度诊断点评"
      style="width: min(640px, 94vw);"
    >
      <div v-if="cfoDiagnosis" class="diagnosis-modal-content">
        <div class="diagnosis-modal-body">
          {{ cfoDiagnosis.diagnosis }}
        </div>
        <div v-if="cfoDiagnosis.urgentActions?.length" class="diagnosis-urgent-section">
          <div class="urgent-title">⚠️ 紧急关注事项：</div>
          <ul>
            <li v-for="(act, idx) in cfoDiagnosis.urgentActions" :key="idx">{{ act }}</li>
          </ul>
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <NButton text size="tiny" :disabled="diagnosisLoading" @click="generateDiagnosis">
            {{ diagnosisLoading ? '重新生成中…' : '↻ 重新生成诊断' }}
          </NButton>
          <NButton type="primary" size="small" @click="showDiagnosisModal = false">我知道了</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }
.mt-6 { margin-top: 24px; }
.col-code { width: 100px; }
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }
.cost-color { color: var(--solo-expense); }
.money-color { color: var(--solo-income); }

/* 标题主行、AI CFO 入口与申报提示 */
.closing-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.closing-ai-badge-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.closing-ai-badge-btn:hover {
  background: var(--solo-primary);
  color: var(--solo-bg-card);
  border-color: var(--solo-primary);
}

.closing-filing-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-secondary);
  flex-wrap: wrap;
}

.closing-filing-inline i {
  color: var(--solo-primary);
  font-size: 13px;
  flex-shrink: 0;
}

.filing-inline-title {
  font-weight: 600;
  color: var(--solo-text-primary);
  flex-shrink: 0;
}

.filing-inline-text {
  color: var(--solo-text-muted);
}

.filing-inline-link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0 4px;
  border: none;
  background: none;
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.15s ease;
}

.filing-inline-link:hover {
  text-decoration: underline;
}

/* 进度条右侧操作区与邮箱免传收单按钮 */
.step-status-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.step-status-inbox-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  height: 28px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.step-status-inbox-btn:hover,
.step-status-inbox-btn.is-active {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.boss-closing-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
  padding: 14px 18px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
}

.boss-closing-main {
  min-width: 0;
}

.boss-closing-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--solo-text-muted);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 1.2px;
}

.boss-closing-main strong {
  color: var(--solo-text-primary);
  font-size: 15px;
}

.boss-closing-main p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.boss-closing-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

@media (max-width: 720px) {
  .boss-closing-summary {
    align-items: flex-start;
    flex-direction: column;
  }

  .boss-closing-meta {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }
}

.quarter-tax-relay-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 18px 22px;
  background: linear-gradient(135deg, rgba(24, 160, 88, 0.08) 0%, rgba(22, 93, 255, 0.08) 100%);
  border: 1px solid rgba(24, 160, 88, 0.3);
  border-radius: var(--solo-radius-lg, 12px);
}
.relay-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.relay-icon {
  font-size: 32px;
}
.relay-copy h4 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.relay-copy p {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.5;
}

/* 结账后待办卡：单卡分组行，不嵌套小卡 */
.closing-todo-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.closing-todo-count {
  margin-left: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.closing-todo-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.closing-todo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 4px;
  border-top: 1px solid var(--solo-border-light);
  cursor: pointer;
}
.closing-todo-row:hover {
  background: var(--solo-bg-blue-soft);
}
.closing-todo-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.closing-todo-dot.is-high {
  background: var(--solo-expense);
}
.closing-todo-dot.is-medium {
  background: var(--solo-warning);
}
.closing-todo-copy {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.closing-todo-copy strong {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.closing-todo-copy span {
  font-size: 12px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.closing-todo-cta {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  font-size: 12.5px;
  color: var(--solo-primary);
}

/* Manual Classify Dialog */
.manual-dialog {
  background: var(--solo-bg-card);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
}

/* completion voucher note */







/* ── 结账完成仪式看板与三大动作 (Bento Grid) ── */
.ceremony-dashboard {
  display: flex;
  flex-direction: column;
}

/* 结账成果大卡片：体量饱满，居中对齐，赋予老板掌舵财务的成就感 */
.ceremony-hero-card {
  padding: 36px 32px 32px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg, 12px);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  box-shadow: var(--solo-shadow-sm, 0 1px 3px rgba(0, 0, 0, 0.04));
}

.ceremony-hero-card .checkmark-wrapper {
  margin: 0 auto 20px;
}

.ceremony-hero-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 680px;
  margin: 0 auto 24px;
}

/* 成就勋章胶囊 */
.ceremony-achievement-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px;
  border-radius: 999px;
  background: var(--solo-income-soft);
  color: var(--solo-income);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 12px;
  border: 1px solid rgba(15, 115, 84, 0.2);
  letter-spacing: 0.2px;
}

.ceremony-hero-content h2.ceremony-title {
  margin: 0 0 10px;
  font-size: 24px;
  font-weight: 800;
  color: var(--solo-text-primary);
  line-height: 1.25;
}

.ceremony-hero-content .ceremony-desc {
  margin: 0 0 14px;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
}

.ceremony-meta-tags {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 指标大方块（做大一些、更有品质感、带有数据色彩倾向） */
.ceremony-metrics-row {
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.ceremony-metric-box {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  transition: all 0.2s ease;
  position: relative;
  overflow: hidden;
}

.ceremony-metric-box::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: transparent;
}

/* 经营收入：绿意温润 */
.ceremony-metric-box.is-income {
  background: linear-gradient(180deg, var(--solo-income-soft) 0%, var(--solo-bg-card) 60%);
  border-color: rgba(15, 115, 84, 0.25);
}
.ceremony-metric-box.is-income::before {
  background: var(--solo-income);
}
.ceremony-metric-box.is-income .metric-box-label {
  color: var(--solo-income);
}

/* 经营成本：暖红警示 */
.ceremony-metric-box.is-expense {
  background: linear-gradient(180deg, var(--solo-expense-soft) 0%, var(--solo-bg-card) 60%);
  border-color: rgba(189, 61, 61, 0.25);
}
.ceremony-metric-box.is-expense::before {
  background: var(--solo-expense);
}
.ceremony-metric-box.is-expense .metric-box-label {
  color: var(--solo-expense);
}

/* 净利润：根据盈亏动态着色 */
.ceremony-metric-box.is-profit.is-positive {
  background: linear-gradient(180deg, var(--solo-income-soft) 0%, var(--solo-bg-card) 60%);
  border-color: rgba(15, 115, 84, 0.25);
}
.ceremony-metric-box.is-profit.is-positive::before {
  background: var(--solo-income);
}
.ceremony-metric-box.is-profit.is-positive .metric-box-label {
  color: var(--solo-income);
}

.ceremony-metric-box.is-profit.is-negative {
  background: linear-gradient(180deg, var(--solo-expense-soft) 0%, var(--solo-bg-card) 60%);
  border-color: rgba(189, 61, 61, 0.25);
}
.ceremony-metric-box.is-profit.is-negative::before {
  background: var(--solo-expense);
}
.ceremony-metric-box.is-profit.is-negative .metric-box-label {
  color: var(--solo-expense);
}

.ceremony-metric-box:hover {
  box-shadow: var(--solo-shadow-sm, 0 2px 6px rgba(0, 0, 0, 0.05));
  transform: translateY(-1px);
}

.ceremony-metric-box .metric-box-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--solo-text-muted);
}

.ceremony-metric-box .metric-box-val {
  font-size: 24px;
  font-weight: 800;
  font-family: 'Outfit', sans-serif;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.ceremony-metric-box .metric-box-val.is-income,
.ceremony-metric-box .metric-box-val.income {
  color: var(--solo-income);
}

.ceremony-metric-box .metric-box-val.is-expense,
.ceremony-metric-box .metric-box-val.expense {
  color: var(--solo-expense);
}

@media (max-width: 768px) {
  .ceremony-hero-card {
    padding: 24px 16px 20px;
  }
  .ceremony-metrics-row {
    grid-template-columns: 1fr;
    gap: 12px;
  }
}

/* 三列动作卡网格 */
.ceremony-actions-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

@media (max-width: 960px) {
  .ceremony-actions-grid {
    grid-template-columns: 1fr;
  }
}

.ceremony-action-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg, 12px);
  min-height: 200px;
  transition: all 0.2s ease;
}

.ceremony-action-card:hover {
  border-color: var(--solo-primary);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.04);
}

.action-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.action-card-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.action-card-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  flex-shrink: 0;
}

.action-card-icon.is-cfo {
  background: var(--solo-primary-glow, rgba(23, 71, 176, 0.08));
  color: var(--solo-primary);
}

.action-card-icon.is-tax {
  background: var(--solo-income-soft);
  color: var(--solo-income);
}

.action-card-icon.is-todo {
  background: var(--solo-warning-soft);
  color: var(--solo-warning);
}

.action-card-icon.is-done {
  background: var(--solo-income-soft);
  color: var(--solo-income);
}

.action-card-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1.2;
}

.action-card-sub {
  display: block;
  font-size: 11px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}

.action-card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  margin-bottom: 14px;
  min-height: 60px;
}

.action-card-desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
}

.action-diagnosis-snippet {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-primary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.action-urgent-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-expense);
}

.action-tax-target {
  font-size: 13px;
  color: var(--solo-text-primary);
  margin-bottom: 4px;
}

.action-card-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.todo-empty-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--solo-income);
}

.action-card-footer {
  border-top: 1px solid var(--solo-border-light);
  padding-top: 10px;
}

.action-card-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 底部轻量辅助工具栏 */
.ceremony-footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 4px;
  flex-wrap: wrap;
  gap: 12px;
}

.ceremony-footer-links {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12.5px;
}

.footer-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  padding: 2px 4px;
  color: var(--solo-text-secondary);
  cursor: pointer;
  font-size: 12.5px;
  transition: color 0.15s ease;
}

.footer-link-btn:hover {
  color: var(--solo-primary);
  text-decoration: underline;
}

.footer-link-btn.is-warn:hover {
  color: var(--solo-warning);
}

.footer-link-divider {
  color: var(--solo-text-muted);
}

/* 诊断弹窗内容 */
.diagnosis-modal-content {
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--solo-text-primary);
}

.diagnosis-modal-body {
  white-space: pre-wrap;
  text-align: justify;
}

.diagnosis-urgent-section {
  margin-top: 16px;
  padding: 10px 14px;
  background: var(--solo-expense-soft, rgba(189, 61, 61, 0.08));
  border: 1px solid rgba(189, 61, 61, 0.2);
  border-radius: 8px;
}

.urgent-title {
  font-weight: 700;
  color: var(--solo-expense);
  margin-bottom: 6px;
}

.diagnosis-urgent-section ul {
  margin: 0;
  padding-left: 20px;
  font-size: 12.5px;
  color: var(--solo-text-primary);
  line-height: 1.6;
}
.manual-dialog-header {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}
.manual-dialog-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: rgba(99, 102, 241, 0.1);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}
.manual-dialog-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.manual-dialog-subtitle {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}
.manual-stmt-info {
  background: rgba(99, 102, 241, 0.03);
  border: 1px solid rgba(99, 102, 241, 0.1);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.manual-stmt-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.manual-stmt-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.manual-stmt-val {
  font-size: 13px;
  color: var(--solo-text-primary);
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}
.manual-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.manual-form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.manual-form-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.ai-draft-loading-hint {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--solo-text-muted);
}

.ai-draft-spinner {
  display: inline-block;
  animation: ai-draft-spin 0.9s linear infinite;
  color: var(--solo-primary);
}

@keyframes ai-draft-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.manual-account-preview {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  padding: 6px 10px;
  background: rgba(0,0,0,0.02);
  border-radius: 4px;
  border: 1px solid var(--solo-border);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.manual-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--solo-border);
}
.pair-account-review {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(16, 185, 129, 0.06);
  border: 1px solid rgba(16, 185, 129, 0.14);
  color: var(--solo-text-secondary);
  font-size: 11px;
  line-height: 1.35;
}
.pair-account-review.warning {
  background: rgba(245, 158, 11, 0.08);
  border-color: rgba(245, 158, 11, 0.24);
  color: var(--solo-warning);
}
.pair-account-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--solo-text-muted);
  flex-shrink: 0;
}
.pair-account-review > span:last-child {
  min-width: 0;
  text-align: right;
  word-break: break-word;
}
.pair-actions {
  flex-wrap: wrap;
}
.recon-health-panel {
  margin: 0 0 16px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid rgba(245, 158, 11, 0.22);
  background: rgba(245, 158, 11, 0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.recon-health-panel.danger {
  border-color: rgba(239, 68, 68, 0.22);
  background: rgba(239, 68, 68, 0.06);
}
.recon-health-lead {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 240px;
}
.recon-health-lead i {
  font-size: 20px;
  color: var(--solo-warning);
}
.recon-health-panel.danger .recon-health-lead i {
  color: var(--solo-expense);
}
.recon-health-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.recon-health-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.recon-health-tags {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: wrap;
}
.closing-risk-lock {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0 auto 14px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(239, 68, 68, 0.22);
  background: rgba(239, 68, 68, 0.06);
  color: var(--solo-expense);
  font-size: 12px;
  font-weight: 600;
}

.readiness-overview {
  padding: 24px;
}

.readiness-heading,
.next-task,
.readiness-row {
  display: flex;
  align-items: center;
}

.readiness-heading {
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--solo-text-muted);
  font-size: 12px;
  font-weight: 600;
}

.readiness-heading h3,
.next-task h4 {
  margin: 0;
  color: var(--solo-text-primary);
}

.readiness-heading h3 {
  font-size: 24px;
  line-height: 1.3;
}

.readiness-count {
  min-width: 116px;
  padding: 12px 16px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-soft);
  text-align: right;
}

.readiness-count strong,
.readiness-count span {
  display: block;
}

.readiness-count strong {
  color: var(--solo-text-primary);
  font-size: 20px;
  font-variant-numeric: tabular-nums;
}

.readiness-count span {
  margin-top: 2px;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.readiness-count.ready {
  border-color: rgba(18, 128, 92, 0.24);
  background: var(--solo-income-soft);
}

.next-task {
  gap: 16px;
  padding: 20px;
  border: 1px solid rgba(22, 93, 255, 0.24);
  border-radius: 12px;
  background: var(--solo-bg-blue-soft);
}

.next-task.ready-state {
  border-color: rgba(18, 128, 92, 0.24);
  background: var(--solo-income-soft);
}

.next-task-icon {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: rgba(22, 93, 255, 0.12);
  color: var(--solo-primary);
  font-size: 20px;
}

.next-task-icon.blocked {
  background: var(--solo-warning-soft);
  color: var(--solo-warning);
}

.next-task-icon.complete {
  background: var(--solo-income-soft);
  color: var(--solo-income);
}

.next-task-copy {
  min-width: 0;
  flex: 1;
}

.next-task-copy p {
  margin: 6px 0 0;
  color: var(--solo-text-secondary);
  font-size: 14px;
}

.five-check-strip {
  margin: 0 0 20px;
}

.five-check-strip-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.five-check-strip-head span {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.five-check-strip-head small {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.five-check-chips {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.five-check-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 10px;
  background: var(--solo-bg-soft);
  transition: background-color 180ms ease, border-color 180ms ease;
}

.five-check-chip-icon {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: var(--solo-bg-card);
  color: var(--solo-text-muted);
  font-size: 14px;
}

.five-check-chip-copy {
  min-width: 0;
}

.five-check-chip-copy strong,
.five-check-chip-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.five-check-chip-copy strong {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.five-check-chip-copy small {
  margin-top: 1px;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.five-check-chip.complete {
  background: var(--solo-income-soft);
}

.five-check-chip.complete .five-check-chip-icon {
  color: var(--solo-income);
}

.five-check-chip.complete .five-check-chip-copy small {
  color: var(--solo-income);
}

.five-check-chip.blocked,
.five-check-chip.needs_review {
  background: var(--solo-warning-soft);
}

.five-check-chip.blocked .five-check-chip-icon,
.five-check-chip.needs_review .five-check-chip-icon {
  color: var(--solo-warning);
}

.five-check-chip.blocked .five-check-chip-copy small,
.five-check-chip.needs_review .five-check-chip-copy small {
  color: var(--solo-warning);
}

@media (max-width: 900px) {
  .five-check-chips {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .five-check-chips {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .five-check-chip {
    transition: none;
  }
}

.readiness-list {
  margin-top: 20px;
  overflow: hidden;
  border: 1px solid var(--solo-border);
  border-radius: 12px;
}

.readiness-row {
  width: 100%;
  gap: 12px;
  padding: 14px 16px;
  border: 0;
  border-bottom: 1px solid var(--solo-border-light);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
}

.readiness-row:last-child {
  border-bottom: 0;
}

.readiness-row:not(:disabled) {
  cursor: pointer;
}

.readiness-row:not(:disabled):hover {
  background: var(--solo-bg-soft);
}

.readiness-row:focus-visible,
.detail-workflow-link:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}

.readiness-row:disabled {
  cursor: default;
}

.readiness-row-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
}

.readiness-row.complete .readiness-row-icon {
  background: var(--solo-income-soft);
  color: var(--solo-income);
}

.readiness-row.blocked .readiness-row-icon {
  background: var(--solo-warning-soft);
  color: var(--solo-warning);
}

.readiness-row-copy {
  min-width: 0;
  flex: 1;
}

.readiness-row-copy strong,
.readiness-row-copy small {
  display: block;
}

.readiness-row-copy small {
  margin-top: 3px;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.readiness-row-status {
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.readiness-row.complete .readiness-row-status {
  color: var(--solo-income);
}

.readiness-row.blocked .readiness-row-status {
  color: var(--solo-warning);
}

.detail-workflow-link {
  margin-top: 16px;
}

.readiness-empty {
  padding: 20px;
  color: var(--solo-text-secondary);
  text-align: center;
}

.cfo-diagnosis-card {
  border: 1px solid rgba(22, 93, 255, 0.18);
  background: var(--solo-bg-card);
}

@media (max-width: 720px) {
  .readiness-overview {
    padding: 16px;
  }

  .readiness-heading,
  .next-task {
    align-items: flex-start;
    flex-direction: column;
  }

  .readiness-count {
    width: 100%;
    text-align: left;
  }

  .next-task .n-button {
    width: 100%;
  }
}

/* Zero-record month banner */
.zero-record-card {
  padding: 16px 18px;
  background: var(--solo-bg-blue-soft, #f0f7ff);
  border: 1px dashed var(--solo-primary-light, #93c5fd);
  border-radius: var(--solo-radius, 8px);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.zero-record-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.zero-record-icon {
  font-size: 22px;
  color: var(--solo-primary, #1747b0);
  flex-shrink: 0;
  margin-top: 2px;
}
.zero-record-text h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.zero-record-text p {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}
.zero-record-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 10px;
  border-top: 1px dashed rgba(99, 102, 241, 0.15);
}
.zero-record-confirm {
  margin-left: auto;
}
.readiness-count.zero {
  border-color: var(--solo-border);
  background: var(--solo-bg-soft);
}
@media (max-width: 768px) {
  .zero-record-confirm {
    margin-left: 0;
    width: 100%;
  }
}

.backfill-copy {
  margin-bottom: 10px;
  font-size: 13px;
  line-height: 1.7;
}
.backfill-copy p {
  margin: 0 0 6px;
}
.backfill-warn {
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
  font-size: 12px;
}
.backfill-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}



/* Bank Feed Categorization Workbench (Step 4) */
.feed-summary-hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 16px 20px;
  border-radius: var(--solo-radius-lg, 12px);
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.05) 0%, rgba(16, 185, 129, 0.05) 100%);
  border: 1px solid rgba(99, 102, 241, 0.15);
  margin-bottom: 20px;
}

.feed-hero-left {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.feed-hero-stat-circle {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: var(--solo-bg-card);
  border: 2px solid var(--solo-primary);
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.15);
}

.feed-hero-stat-circle .stat-number {
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-primary);
  line-height: 1.1;
}

.feed-hero-stat-circle .stat-label {
  font-size: 9px;
  color: var(--solo-text-muted);
  transform: scale(0.9);
}

.feed-hero-metrics {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.metric-pill {
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.metric-pill.success {
  background: rgba(16, 185, 129, 0.08);
  border-color: rgba(16, 185, 129, 0.25);
  color: var(--solo-income);
}

.metric-pill.warning {
  background: rgba(245, 158, 11, 0.08);
  border-color: rgba(245, 158, 11, 0.25);
  color: #a05c10;
}

.feed-section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.feed-empty-box {
  padding: 48px 24px;
  text-align: center;
  background: rgba(16, 185, 129, 0.02);
  border: 1px dashed rgba(16, 185, 129, 0.2);
  border-radius: 12px;
}

.feed-empty-icon {
  font-size: 40px;
  color: var(--solo-income);
}

.feed-card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 商业化紧凑型对账行 (Compact Reconcile Row) */
.reconcile-row {
  /* 两行布局：第一行事实（勾选/摘要/金额），第二行决策（建议分类+操作） */
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 8px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
  transition: all 0.2s ease;
}

.reconcile-row-detail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  /* 与第一行摘要文本对齐（跳过勾选框宽度） */
  padding-left: 30px;
}

.reconcile-row:hover {
  border-color: rgba(99, 102, 241, 0.3);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.reconcile-row.high-conf {
  border-left: 3px solid var(--solo-primary);
}

.reconcile-row.needs-attention {
  border-left: 3px solid var(--solo-warning);
  background: var(--solo-warning-bg);
}

.tier-filter-pills {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tier-pill {
  padding: 2px 10px;
  font-size: 12px;
  border-radius: 999px;
  border: 1px solid var(--solo-border-light);
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.tier-pill.active {
  background: var(--solo-info-bg);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}

.tier-pill.attention {
  color: var(--solo-warning);
  background: var(--solo-warning-bg);
}

.tier-pill.attention.active {
  border-color: var(--solo-warning);
  font-weight: 600;
}

.reconcile-cell-fact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.fact-lead {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.fact-date {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.fact-desc {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fact-amount {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  min-width: 80px;
  text-align: right;
}

.reconcile-cell-plan {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  row-gap: 4px;
  flex: 1;
  min-width: 0;
}

.plan-badge {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  row-gap: 4px;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  max-width: 100%;
  min-width: 0;
}

.plan-badge.standard {
  background: rgba(99, 102, 241, 0.06);
  border: 1px solid rgba(99, 102, 241, 0.16);
  color: var(--solo-text-primary);
}

.plan-badge.match {
  background: rgba(16, 185, 129, 0.06);
  border: 1px solid rgba(16, 185, 129, 0.2);
  color: var(--solo-text-primary);
}

.plan-badge.fallback {
  background: rgba(0, 0, 0, 0.02);
  border: 1px dashed var(--solo-border-light);
  color: var(--solo-text-muted);
}

.plan-badge.none {
  background: rgba(0, 0, 0, 0.02);
  border: 1px dashed var(--solo-border-light);
  color: var(--solo-text-muted);
}

.plan-label {
  color: var(--solo-text-muted);
  font-size: 11px;
  flex-shrink: 0;
  white-space: nowrap;
}

.plan-category {
  font-weight: 600;
  color: var(--solo-text-primary);
  font-size: 12px;
  white-space: nowrap;
}

.plan-account-preview {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--solo-text-muted);
  font-size: 11px;
  flex-shrink: 0;
  white-space: nowrap;
}

.plan-alternatives {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
}

.plan-alt-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--solo-border-light);
  background: #fff;
  color: var(--solo-text-secondary);
  font-size: 11px;
  line-height: 18px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.plan-alt-chip:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.plan-alt-chip.selected {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  background: rgba(23, 71, 176, 0.06);
  font-weight: 600;
}

.plan-alt-account {
  color: var(--solo-text-muted);
  font-size: 10px;
  font-weight: 400;
}

.plan-ask-ai {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px dashed var(--solo-border-light);
  background: transparent;
  color: var(--solo-text-muted);
  font-size: 11px;
  line-height: 18px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.plan-ask-ai:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}


.batch-post-confirm {
  /* 裸 NModal 不带卡片样式，必须自带底色，否则页面内容透出 */
  background: var(--solo-bg-card);
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
}

.batch-post-confirm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.batch-post-confirm-header i {
  color: var(--solo-primary);
  font-size: 18px;
}

.batch-post-confirm-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary);
  margin: 0;
}

.batch-post-confirm-tip {
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
  background: var(--solo-info-bg);
  border: 1px solid rgba(23, 71, 176, 0.16);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}

.batch-post-confirm-list {
  max-height: 260px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.batch-post-confirm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
}

.batch-post-confirm-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.batch-post-confirm-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-post-confirm-cat {
  font-size: 11px;
  color: var(--solo-text-secondary);
}

.batch-post-confirm-cat em {
  font-style: normal;
}

.batch-post-confirm-amt {
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.batch-post-confirm-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.walkthrough-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--solo-info-bg);
  border: 1px solid rgba(23, 71, 176, 0.18);
  border-radius: 8px;
  font-size: 12px;
  color: var(--solo-primary);
  font-weight: 500;
  margin-bottom: 10px;
}

.walkthrough-progress-hint {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--solo-text-secondary);
}

.bank-balance-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  font-size: 13px;
}

.bank-balance-line span {
  color: var(--solo-text-secondary);
}

.bank-balance-line b {
  font-weight: 600;
  color: var(--solo-text-primary);
  font-family: monospace;
}

.bank-balance-warn {
  margin-top: 8px;
  padding: 8px 10px;
  background: rgba(250, 173, 20, 0.08);
  border: 1px solid rgba(250, 173, 20, 0.3);
  border-radius: 8px;
  font-size: 12px;
  color: var(--solo-warning);
  line-height: 1.5;
}

.plan-evidence-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  cursor: help;
  user-select: none;
  white-space: nowrap;
}

.plan-evidence-pill.verified {
  background: var(--solo-info-bg);
  color: var(--solo-primary);
}

.plan-evidence-pill.memory-rule {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}

.plan-evidence-pill.attention {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}

.plan-evidence-pill.fallback {
  background: rgba(0, 0, 0, 0.04);
  color: var(--solo-text-muted);
}

.reconcile-cell-action {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-shrink: 0;
}

.reconcile-category-select {
  width: 170px;
}

.reconcile-detail-btn {
  color: var(--solo-text-muted);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .reconcile-row-detail {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .reconcile-cell-plan {
    width: 100%;
  }

  .reconcile-cell-action {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .reconcile-category-select {
    flex: 1 1 180px;
    width: auto;
    max-width: 240px;
  }
}

@media (max-width: 640px) {
  .reconcile-row-detail {
    padding-left: 0;
  }

  .reconcile-category-select {
    flex-basis: 100%;
    max-width: none;
  }
}

/* 旧卡片样式向下兼容 */
.feed-card {
  padding: 14px 18px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
  transition: all 0.2s ease;
}

.feed-card:hover {
  border-color: rgba(99, 102, 241, 0.3);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.04);
}

.feed-card.high-conf {
  border-left: 4px solid var(--solo-primary);
}

.feed-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.feed-card-lead {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 240px;
}

.feed-card-date {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.feed-card-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--solo-text-primary);
}

.feed-card-amount {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.feed-card-recommendation {
  margin-top: 10px;
}

.feed-rec-badge {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.feed-rec-badge.ai {
  background: rgba(99, 102, 241, 0.05);
  border: 1px solid rgba(99, 102, 241, 0.18);
  color: var(--solo-text-primary);
}

/* 兜底草稿（AI 没判出来）：灰底虚线，不冒充建议 */
.feed-rec-badge.fallback {
  background: rgba(0, 0, 0, 0.02);
  border: 1px dashed var(--solo-border-light);
  color: var(--solo-text-muted);
}

.feed-rec-badge.match {
  background: rgba(16, 185, 129, 0.06);
  border: 1px solid rgba(16, 185, 129, 0.2);
  color: var(--solo-text-primary);
}

/* 匹配凭证标题：点击可打开凭证详情 */
.feed-rec-link {
  font-size: 12px;
  padding: 0;
  height: auto;
}
.feed-rec-link:hover strong {
  text-decoration: underline;
}

.feed-rec-badge.none {
  background: rgba(0, 0, 0, 0.02);
  border: 1px dashed var(--solo-border-light);
  color: var(--solo-text-muted);
}

.feed-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border-light);
  flex-wrap: wrap;
}

.feed-quick-select-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.feed-select-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.feed-completed-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.feed-completed-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.02);
  border-radius: 4px;
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 老板往来小账本汇总卡片交互 (方案 B) */
.boss-ledger-summary-bar {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 18px;
}

.boss-ledger-card {
  background: var(--solo-card-bg, #fff);
  border: 1px solid var(--solo-border, #e5e7eb);
  border-radius: 10px;
  padding: 12px 16px;
  transition: all 0.2s ease;
  position: relative;
}

.boss-ledger-card.clickable {
  cursor: pointer;
}

.boss-ledger-card.clickable:hover {
  border-color: var(--solo-primary, #1677ff);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.boss-ledger-card-header {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.boss-ledger-card-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.boss-ledger-card-link {
  font-size: 12px;
  color: var(--solo-primary, #1677ff);
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-weight: 500;
  opacity: 0.85;
}

.boss-ledger-card.clickable:hover .boss-ledger-card-link {
  opacity: 1;
}

.boss-ledger-card-amount {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
  margin-top: 4px;
}

.boss-ledger-card-meta {
  font-size: 11px;
  color: var(--solo-text-muted);
  margin-top: 2px;
  line-height: 1.4;
}

/* 往来账本抽屉样式 */
.boss-ledger-drawer-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.boss-ledger-drawer-banner {
  background: var(--solo-bg-subtle, #f9fafb);
  border: 1px solid var(--solo-border, #e5e7eb);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.boss-ledger-drawer-amount-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.drawer-amount-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.drawer-amount-value {
  font-size: 22px;
  font-weight: 700;
  margin-top: 2px;
}

.drawer-breakdown-row {
  font-size: 13px;
  color: var(--solo-text-secondary);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: rgba(0, 0, 0, 0.03);
  border-radius: 6px;
}

.breakdown-divider {
  color: var(--solo-text-muted);
}

.drawer-tip-box {
  font-size: 12px;
  color: var(--solo-text-muted);
  line-height: 1.5;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.drawer-tip-box i {
  font-size: 14px;
  color: var(--solo-primary);
  margin-top: 2px;
  flex-shrink: 0;
}

.boss-ledger-drawer-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  padding: 0 2px;
}

.boss-ledger-drawer-entries {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.drawer-entry-card {
  background: var(--solo-card-bg, #fff);
  border: 1px solid var(--solo-border, #e5e7eb);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  transition: border-color 0.15s ease;
}

.drawer-entry-card:hover {
  border-color: var(--solo-border-hover, #cbd5e1);
}

.drawer-entry-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.drawer-entry-header-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.drawer-entry-date {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.drawer-entry-title {
  font-size: 13px;
  color: var(--solo-text-primary);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-entry-meta-line {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
}

.drawer-entry-explanation {
  color: var(--solo-primary);
  font-weight: 500;
}

.drawer-entry-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  flex-shrink: 0;
}

.drawer-entry-amount {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.drawer-entry-amount.income {
  color: var(--solo-income, #16a34a);
}

.drawer-entry-amount.expense {
  color: var(--solo-text-primary, #111827);
}

.drawer-entry-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

</style>
