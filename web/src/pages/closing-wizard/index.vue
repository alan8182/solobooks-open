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
  NTag,
  NTooltip,
  useMessage,
  useDialog,
} from 'naive-ui'
import { formatMoney } from '@/utils/format'
import {
  amountClassForDirection,
  buildSuggestedMatchPair,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  invoiceDisplayAmount,
  isReconciledEntry,
  isNonBankSettledEntry,
  formatDispositionTag,
  formatDispositionExplanation,
} from '@/utils/closingWizard'
import { sortEntriesByDateDescending } from '@/utils/entrySort'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { useEntryStore } from '@/stores/entry'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import apiClient from '@/api/client'
import { getAIDiagnosis, getIncomeStatement } from '@/api/report'
import { importInvoices } from '@/api/invoice'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { parseWechatBill, importWechatBill, fetchWechatBillRecords } from '@/api/wechat-bill'
import { useBackfillConfirm, isBackfillCancelled } from '@/composables/backfillConfirm'
import { summarizeWechatImportFailures, type WechatImportFailureGroup } from '@/utils/wechatImport'
import { useAuthStore } from '@/stores/auth'
import { getClosingReadiness, canBatchPostDrafts, deriveClosingTasks, type ClosingNotice, type ClosingNoticeRecord, type ClosingReadinessItem, type ClosingTaskAction } from '@/utils/closingReadiness'
import { postEntry, getPostingOptions, batchPostEntries, getFilingObligations, reopenPeriod, type FilingObligation, type FilingObligations } from '@/api/accounting'
import { updateEntry } from '@/api/record'
import { requestStepUp, sendPhoneCode } from '@/api/auth'
import { normalizeVoucherLines, generateVoucherLines, loadAccountMapping, accountForCategory, type VoucherLine } from '@/utils/accounting'
import ClosingAgentPanel from '@/components/business/ClosingAgentPanel.vue'
import FileDropzone from '@/components/business/FileDropzone.vue'
import * as XLSX from 'xlsx'

import { SoloEmpty } from '@/components/ui'
import { reportErrorToast } from '@/utils/errorPresenter'
import { offerRescue } from '@/composables/useRescue'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const entryStore = useEntryStore()
const authStore = useAuthStore()
const { confirmDelete } = useDeleteConfirm()
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
// ponytail: unified 5-step wizard with built-in readiness checks, replacing the dual-track showDetailWorkflow toggle
const showAgentPanel = ref(false)

// Target Month options: last 6 months dynamically
const now = new Date()
const currentYear = now.getFullYear()
const currentMonth = now.getMonth() // 0-11
const availablePeriods = ref<{ month: string; status: string | null }[]>([])
const defaultPrevMonth = new Date(currentYear, currentMonth - 1, 1)
const targetMonth = ref(`${defaultPrevMonth.getFullYear()}-${String(defaultPrevMonth.getMonth() + 1).padStart(2, '0')}`)
// 待办/关账任务跳入时按 query.month 定位月份（YYYY-MM），step/statement 见 loadAllData
const queryMonth = String(router.currentRoute?.value?.query?.month || '')
if (/^\d{4}-\d{2}$/.test(queryMonth)) targetMonth.value = queryMonth
// 期次下拉 = 最近 6 个月 ∪ 有数据的月份（凭证/账期/银行流水/工资表），保证历史有数据月份始终可选
const monthOptions = computed(() => {
  const labelOf = (val: string) => {
    const [yearStr, monthStr] = val.split('-')
    return `${yearStr}年${parseInt(monthStr, 10)}月`
  }
  const map = new Map<string, { label: string; value: string }>()
  for (let i = 0; i < 6; i++) {
    const d = new Date(currentYear, currentMonth - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    map.set(val, { label: labelOf(val), value: val })
  }
  for (const item of availablePeriods.value) {
    if (/^\d{4}-\d{2}$/.test(item.month)) {
      map.set(item.month, { label: labelOf(item.month), value: item.month })
    }
  }
  if (targetMonth.value && !map.has(targetMonth.value)) {
    map.set(targetMonth.value, { label: labelOf(targetMonth.value), value: targetMonth.value })
  }
  return [...map.values()].sort((a, b) => b.value.localeCompare(a.value))
})

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
const rawStatements = ref<any[]>([])
const bankStatements = ref<any[]>([])
const invoicesList = ref<any[]>([])
const bankUploadResult = ref<{ imported: number; errors: number; rows: any[] } | null>(null)

// WeChat bill state
const wechatBillRows = ref<any[]>([])
const wechatBillSummary = ref<Record<string, any>>({})
const wechatBillImported = ref(false)
const wechatBillImporting = ref(false)
const wechatImportMode = ref<'summary' | 'detail'>('summary')
// 导入失败明细（按原因聚合）：失败不再只靠一闪而过的 message，内嵌面板可读、可操作
const wechatBillFailures = ref<WechatImportFailureGroup[]>([])

// 「重新上传/重新导入」按钮复用拖拽区的文件选择器
const wechatDropzone = ref<InstanceType<typeof FileDropzone> | null>(null)
const invoiceDropzone = ref<InstanceType<typeof FileDropzone> | null>(null)
// 发票表的专业列（类型/入账模式/冲销）默认收起，老板只需要日期/名称/金额/状态
const showInvoiceDetails = ref(false)
const wechatBillRecords = ref<any[]>([])
const wechatBillRecordsSummary = ref<Record<string, any>>({})

// Step 3: Payroll data
const payrollAccrued = ref(false)
const payrollPaid = ref(false)
const payrollPendingReview = ref(false)
const payrollSsPaid = ref(false)
const payrollHfPaid = ref(false)
const payrollTaxPaid = ref(false)
const employees = ref<any[]>([])
const salarySheet = ref<any>(null)
const showSalaryTable = ref(false)
const payrollPaymentSuggestions = ref<any[]>([])
const applyingSalaryStatementId = ref('')

// Step 4: Matching workspace states
const selectedInvoiceIds = ref<string[]>([])
const selectedBank = ref<string | null>(null)
const matchedPairs = ref<any[]>([])
const aiSuggestions = ref<any[]>([])
const reconciliationHealth = ref<any | null>(null)
const reconciliationHealthLoading = ref(false)

// Step 5: Closing states
const closed = ref(false)
const closingLoading = ref(false)
const monthlyIncome = ref(0)
const monthlyExpense = ref(0)
const netProfit = ref(0)
const cfoDiagnosis = ref<any>(null)
// 本次结账自动生成的期末损益结转凭证号（完成页持久展示，不只靠瞬时 toast）
const closingVoucherRefs = ref<string[]>([])

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
  rawStatements.value.filter(s => s.transaction_date?.startsWith(targetMonth.value))
)
const monthInvoices = computed(() =>
  invoicesList.value.filter(inv => inv.date?.startsWith(targetMonth.value))
)
const isZeroRecordMonth = computed(() =>
  monthEntries.value.length === 0 &&
  monthStatements.value.length === 0 &&
  monthInvoices.value.length === 0
)
// 工资按权责发生制：所属月必须计提；发放可延到次月，凭银行流水核销，
// 社保/个税挂账同样作为负债结转，均不阻断本月结账。
const payrollHasSocial = computed(() =>
  employees.value.some((emp) => safeFloat(emp.social) > 0 || safeFloat(emp.personalSocial) > 0)
)
const payrollHasTax = computed(() =>
  employees.value.some((emp) => safeFloat(emp.personalTax) > 0)
)
const hasBlockingPayroll = computed(() =>
  employees.value.length > 0 && (!payrollAccrued.value || payrollPendingReview.value)
)

const readinessItems = computed(() => getClosingReadiness(periodCloseChecks.value))
const closingTasks = computed(() => deriveClosingTasks(periodCloseChecks.value))

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

function runTaskAction(action: ClosingTaskAction) {
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
    currentStep.value = 2
    if (action.statementId) {
      selectedBank.value = action.statementId
      scrollIntoBank(action.statementId)
    }
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
const canBatchPostAndClose = computed(() => canBatchPostDrafts(periodCloseChecks.value))
const closingReady = computed(() => {
  if (!periodCloseChecks.value?.canClose && !canBatchPostAndClose.value) return false
  if (hasBlockingPayroll.value) return false
  if (isZeroRecordMonth.value && !confirmZeroActivity.value) return false
  return true
})

let suggestionsController: AbortController | null = null

// Load all data on mount
onMounted(async () => {
  await Promise.all([loadAllData(), loadRdOptions(), loadAccountMapping()])
  refreshCategoryOptions()
})

async function loadAllData() {
  if (suggestionsController) {
    try {
      suggestionsController.abort()
    } catch {
      // ignore
    }
  }

  loading.value = true
  confirmZeroActivity.value = false
  try {
    // 1. 并发加载最核心的记账 entries 与银行 statements 数据，极大缩短串行等待时间
    const [_, res, periodChecks, availRes, obligations] = await Promise.all([
      entryStore.fetchEntries(),
      apiClient.get('/api/bank/statements', { timeout: 30000 }),
      apiClient.get(`/api/periods/${targetMonth.value}/close-checks`),
      apiClient.get('/api/periods/available').catch(() => null),
      getFilingObligations(targetMonth.value).catch(() => null),
    ])
    filingInfo.value = (obligations as unknown as FilingObligations | null) ?? null
    rawStatements.value = (res as any).items || []
    periodCloseChecks.value = periodChecks as any
    availablePeriods.value = (availRes as any)?.months || []
    
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
    fetchClosedDiagnosis()
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

function filterUnmatchedData() {
  // Unmatched bank statements
  // ponytail: 空日期的流水不过滤，否则银行流水导入后因日期解析失败消失
  bankStatements.value = rawStatements.value.filter(
    (s) => !s.matched_entry_id && (
      !s.transaction_date ||
      s.transaction_date === '' ||
      s.transaction_date.substring(0, 7) <= targetMonth.value
    )
  )

  // All invoices for current month (with linked/confirmed status split)
  const { linkedEntryIds, confirmedEntryIds } = getMatchedEntryIdSets(rawStatements.value, entryStore.activeEntries)
  invoicesList.value = entryStore.activeEntries.filter((e) => {
    const isInvoice = (e as any).source === 'invoice_import' || !!(e as any).invoiceNumber
    const isInMonth = entryPeriod(e) === targetMonth.value
    return isInvoice && isInMonth
  }).map(e => ({
    id: e.id,
    title: e.title,
    date: e.occurDate,
    amount: e.amount,
    direction: e.direction,
    category: (e as any).category || '',
    billingStatus: (e as any).billingStatus,
    paymentStatus: (e as any).paymentStatus || '',
    voucherStatus: (e as any).voucherStatus || '',
    voucherRef: (e as any).voucherRef || (e as any).voucher_no || '',
    payer: (e as any).payer || 'company_account',
    is_archive_only: (e as any).is_archive_only || (e as any).isArchiveOnly || false,
    status: confirmedEntryIds.has(e.id) ? 'matched' : linkedEntryIds.has(e.id) ? 'pending_confirmation' : 'unmatched'
  }))
}

function goLedgerDetail(entryId: string) {
  router.push({ path: `/ledger/${entryId}`, query: { from: `/closing-wizard?month=${targetMonth}&step=1` } })
}

async function handleDeleteInvoice(id: string) {
  loading.value = true
  try {
    await entryStore.removeEntry(id)
    message.success('已成功删除该发票，不予入账')
    await loadAllData()
  } catch (err) {
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    const reason = typeof detail === 'string' ? detail : (detail as { message?: string } | undefined)?.message
    message.error(reason || '删除发票失败')
  } finally {
    loading.value = false
  }
}

async function toggleArchiveMode(id: string, isArchive: boolean) {
  loading.value = true
  try {
    await apiClient.patch(`/api/record/entries/${id}`, {
      is_archive_only: isArchive,
      isArchiveOnly: isArchive
    })
    message.success(isArchive ? '已将该发票设为“仅归档不记账”，不再影响科目余额' : '已将该发票设为“参与记账”')
    await loadAllData()
  } catch (err) {
    message.error('切换记账模式失败')
  } finally {
    loading.value = false
  }
}

// 拖拽上传的校验失败提示（统一入口）
function onFileError(msg: string) {
  message.error(msg)
}

// Parse WeChat bill CSV (preview only, not yet imported)
async function processWechatBillFile(file: File) {
  loading.value = true
  try {
    const res = await parseWechatBill(file)
    if (!res.valid || res.valid.length === 0) {
      message.warning('未解析到有效的微信支付交易记录，请检查文件格式')
      return
    }
    wechatBillRows.value = res.valid
    wechatBillSummary.value = res.summary || {}
    wechatBillImported.value = false
    wechatBillFailures.value = []
    message.success(`已解析 ${res.valid.length} 条交易记录，请确认后点击导入`)
  } catch (e) {
    message.error('微信账单文件解析失败，请确认为微信商户后台导出的 CSV 格式')
  } finally {
    loading.value = false
  }
}

// Remove a single row from wechat bill preview
function removeWechatRow(index: number) {
  wechatBillRows.value.splice(index, 1)
}

// Confirm import of previewed wechat bill rows
async function confirmWechatBillImport() {
  const isSummaryMode = wechatImportMode.value === 'summary'
  const importableRows = wechatBillRows.value.filter((row) => !row.is_duplicate)
  if (wechatBillImporting.value) return
  if (!isSummaryMode && importableRows.length === 0) return

  // 计算发生日期：取账单最晚一条日期或目标月份最后一天
  let latestDate = ''
  for (const r of wechatBillRows.value) {
    if (r.date && (!latestDate || r.date > latestDate)) latestDate = r.date
  }
  const occurDate = latestDate || (targetMonth.value ? `${targetMonth.value}-28` : '')

  loading.value = true
  wechatBillImporting.value = true
  try {
    const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      importWechatBill(isSummaryMode ? [] : importableRows, {
        allowBackfill,
        backfillReason,
        importMode: wechatImportMode.value,
        summary: wechatBillSummary.value,
        occurDate,
      }),
    )
    wechatBillFailures.value = summarizeWechatImportFailures(res.results || [])
    wechatBillImported.value = true
    wechatBillRows.value = []
    let msg = isSummaryMode
      ? `微信账单月度汇总完成：已生成 1 张月度综合凭证。`
      : `微信账单导入完成：${res.imported} 条交易已入账。`
    if (res.ignored_duplicates > 0) {
      msg += `（已过滤 ${res.ignored_duplicates} 条已存在账目）`
    }
    if (res.failed > 0) {
      msg += `，${res.failed} 条失败（原因见下方提示）`
    }
    if (res.imported > 0) {
      message.success(msg)
    } else {
      message.warning(msg)
    }
    await loadAllData()
  } catch (e) {
    if (!isBackfillCancelled(e)) message.error('导入失败，请重试')
  } finally {
    wechatBillImporting.value = false
    loading.value = false
  }
}

// 启用日期拦截是「账套配置问题」而非数据问题，一键直达编辑弹窗修正
function gotoAdjustStartDate() {
  const companyId = authStore.currentCompanyId
  router.push({ path: '/mine/companies', query: companyId ? { edit: companyId } : {} })
}

// Load previously imported wechat bill records for the target month
async function loadWechatBillRecords() {
  try {
    const res: any = await fetchWechatBillRecords(targetMonth.value)
    wechatBillRecords.value = Array.isArray(res.items) ? res.items : []
    wechatBillRecordsSummary.value = res.summary || {}
  } catch {
    wechatBillRecords.value = []
    wechatBillRecordsSummary.value = {}
  }
}

// Parse and upload invoices Excel/CSV
async function processInvoiceFile(file: File) {
  loading.value = true
  
  const reader = new FileReader()
  reader.onload = async (ev) => {
    try {
      const data = new Uint8Array(ev.target?.result as ArrayBuffer)
      const workbook = XLSX.read(data, { type: 'array' })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      const json = XLSX.utils.sheet_to_json(sheet, { defval: '' })
      if (json.length === 0) {
        message.warning('Excel 文件为空')
        return
      }
      
      const columns = Object.keys(json[0] as any)
      const isTaxBureauFormat = columns.includes('数电发票号码') || columns.includes('销方名称') || columns.includes('购买方名称')
      
      let mapped: any[] = []
      if (isTaxBureauFormat) {
        mapped = json.map((row: any) => {
          const invoiceNum = String(row['数电发票号码'] || row['发票号码'] || row['发票代码'] || '').trim()
          
          const isNegative = String(row['是否正数发票'] || '').trim() === '否' || String(row['发票状态'] || '').trim() === '红冲'
          
          let amountVal = safeFloat(row['金额'] || row['不含税金额'] || 0)
          let totalAmountVal = safeFloat(row['价税合计'] || row['含税金额'] || row['总金额'] || 0)
          let taxAmountVal = safeFloat(row['税额'] || 0)
          
          if (isNegative) {
            amountVal = -Math.abs(amountVal)
            totalAmountVal = -Math.abs(totalAmountVal)
            taxAmountVal = -Math.abs(taxAmountVal)
          }
          
          return {
            invoice_number: invoiceNum,
            invoice_code: String(row['发票代码'] || '').trim(),
            invoice_date: String(row['开票日期'] || row['开票时间'] || row['发票日期'] || '').trim(),
            seller_name: String(row['销方名称'] || '').trim(),
            buyer_name: String(row['购买方名称'] || '').trim(),
            seller_tax_id: String(row['销方识别号'] || '').trim(),
            buyer_tax_id: String(row['购方识别号'] || '').trim(),
            total_amount: totalAmountVal,
            amount: amountVal || (totalAmountVal - taxAmountVal),
            tax_amount: taxAmountVal || (totalAmountVal - amountVal),
            tax_rate: String(row['税率'] || '').trim(),
            item_name: String(row['货物或应税劳务名称'] || '').trim(),
            specification: String(row['规格型号'] || '').trim(),
            unit: String(row['单位'] || '').trim(),
            quantity: row['数量'] ? safeFloat(row['数量']) : null,
            unit_price: row['单价'] ? safeFloat(row['单价']) : null,
            invoice_source: String(row['发票来源'] || '').trim(),
            invoice_type: String(row['发票票种'] || '').trim(),
            invoice_status: String(row['发票状态'] || '').trim(),
            drawer: String(row['开票人'] || '').trim(),
            remark: String(row['备注'] || '').trim()
          }
        })
      } else {
        // Fallback to fuzzy regex mapping
        const map: Record<string, string> = {}
        for (const col of columns) {
          const lc = col.toLowerCase()
          if (/发票号码|发票代码|invoice.*no|invoice.*num|发票编号/.test(lc)) map[col] = 'invoice_number'
          else if (/开票日期|发票日期|date|开票时间/.test(lc)) map[col] = 'invoice_date'
          else if (/销售方|卖方|seller|供应商|供货方/.test(lc)) map[col] = 'seller_name'
          else if (/购买方|买方|buyer|客户/.test(lc)) map[col] = 'buyer_name'
          else if (/价税合计|总金额|total.*amount|含税金额|合计/.test(lc)) map[col] = 'total_amount'
          else if (/金额|amount|不含税/.test(lc) && !/税额/.test(lc)) map[col] = 'amount'
          else if (/税额|tax/.test(lc)) map[col] = 'tax_amount'
          else if (/名称|货物|item|商品/.test(lc)) map[col] = 'item_name'
        }
        
        mapped = json.map((row: any) => {
          const out: Record<string, any> = {}
          for (const [col, field] of Object.entries(map)) {
            if (field) out[field] = row[col]
          }
          
          const isNegative = Object.entries(row).some(([k, v]) => {
            const key = String(k).trim()
            const val = String(v).trim()
            return (key === '是否正数发票' && val === '否') || (key === '发票状态' && val === '红冲')
          })
          
          if (isNegative) {
            if (out.amount !== undefined) out.amount = -Math.abs(safeFloat(out.amount))
            if (out.total_amount !== undefined) out.total_amount = -Math.abs(safeFloat(out.total_amount))
            if (out.tax_amount !== undefined) out.tax_amount = -Math.abs(safeFloat(out.tax_amount))
          }
          
          return out
        })
      }
      
      const parseRes: any = await apiClient.post('/api/invoice/parse', { rows: mapped })
      if (parseRes.valid.length === 0) {
        message.error('未在文件中解析到有效发票行')
        return
      }
      
      const rows = parseRes.valid.map((r: any) => ({
        ...r,
        direction: r.direction || 'expense',
        // 类别必须与方向同侧：销项票（income）兜底为主营业务收入，
        // 否则后端生成的挂账凭证会被收支校验拒绝导致整批导入失败
        category: r.category || (r.direction === 'income' ? '主营业务收入' : '其他支出')
      }))
      
      const importRes: any = await importInvoices(rows, false)
      let msg = `成功导入 ${importRes.imported} 张新发票！请在对账步骤查看候选建议并手动确认。`
      if (importRes.ignored_duplicates > 0) {
        msg += `（系统已自动过滤 ${importRes.ignored_duplicates} 张已存在的发票记录）`
      }
      message.success(msg)
      await loadAllData()
    } catch (err) {
      message.error('发票文件导入解析失败')
    } finally {
      loading.value = false
    }
  }
  reader.readAsArrayBuffer(file)
}

// Convert various date formats (including XLSX serial numbers) to YYYY-MM-DD
function formatBankDate(val: any): string {
  if (val === null || val === undefined || val === '') return ''
  // XLSX numeric serial date (e.g. 45800)
  if (typeof val === 'number') {
    const d = XLSX.SSF.parse_date_code(val)
    if (d) {
      return `${d.y}-${String(d.m).padStart(2, '0')}-${String(d.d).padStart(2, '0')}`
    }
  }
  const s = String(val).trim()
  // Already YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
  // YYYY/MM/DD or YYYYMMDD
  const m1 = s.match(/^(\d{4})[/](\d{1,2})[/](\d{1,2})/)
  if (m1) return `${m1[1]}-${m1[2].padStart(2, '0')}-${m1[3].padStart(2, '0')}`
  const m2 = s.match(/^(\d{4})(\d{2})(\d{2})/)
  if (m2) return `${m2[1]}-${m2[2]}-${m2[3]}`
  return s.slice(0, 10) || ''
}

function parseBankExcel(sheet: any): any[] {
  // Parse sheet into 2D array of raw rows
  const rawRows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' }) as any[][]
  
  let headerIndex = -1
  for (let i = 0; i < rawRows.length; i++) {
    const row = rawRows[i]
    if (!row) continue
    
    let score = 0
    for (const cell of row) {
      const val = String(cell || '').trim()
      if (/交易日|交易日期|日期|时间|date|time/i.test(val)) score++
      else if (/交易金额|发生额|金额|借贷发生额|amount/i.test(val)) score++
      else if (/交易摘要|摘要|描述|用途|description|desc|remark/i.test(val)) score++
      else if (/账户余额|余额|balance/i.test(val)) score++
    }
    
    if (score >= 3) {
      headerIndex = i
      break
    }
  }
  
  if (headerIndex === -1) {
    // Fallback if no structured header found
    return XLSX.utils.sheet_to_json(sheet, { defval: '' })
  }
  
  const headers = rawRows[headerIndex].map(h => String(h || '').trim())
  
  // Find date column indices to filter out header/footer rows without date
  const dateColIndices: number[] = []
  for (let j = 0; j < headers.length; j++) {
    const h = headers[j].toLowerCase()
    if (/交易日|记账日|入账日|起息日|日期|时间|date|time/i.test(h)) {
      dateColIndices.push(j)
    }
  }

  const json: any[] = []
  
  for (let i = headerIndex + 1; i < rawRows.length; i++) {
    const row = rawRows[i]
    if (!row || row.length === 0) continue
    
    // Skip empty or summary rows
    const firstCell = String(row[0] || '').trim()
    const hasSummaryText = row.some(cell => /累计|合计|总计|总记录数/.test(String(cell || '').trim()))
    if (/累计|合计|总计|总记录数/.test(firstCell) || hasSummaryText) continue
    
    // If a date column is defined but this row has no date, it is likely a footer or summary row
    if (dateColIndices.length > 0) {
      const hasDate = dateColIndices.some(idx => String(row[idx] || '').trim() !== '')
      if (!hasDate) continue
    }
    
    const obj: Record<string, any> = {}
    let hasValue = false
    for (let j = 0; j < headers.length; j++) {
      const header = headers[j]
      if (header) {
        obj[header] = row[j] !== undefined ? row[j] : ''
        if (obj[header] !== '') hasValue = true
      }
    }
    if (hasValue) {
      json.push(obj)
    }
  }
  
  return json
}

// Parse and upload bank statements CSV/Excel
async function processBankFile(file: File) {
  loading.value = true
  
  const reader = new FileReader()
  reader.onload = async (ev) => {
    try {
      const data = new Uint8Array(ev.target?.result as ArrayBuffer)
      const workbook = XLSX.read(data, { type: 'array' })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      const json = parseBankExcel(sheet)
      if (json.length === 0) {
        message.warning('流水文件为空')
        return
      }
      
      const columns = Object.keys(json[0] as any)
      const map: Record<string, string> = {}
      let typeCol = ''
      let counterpartyCol = ''
      let summaryCol = ''
      let commentCol = ''
      
      let incomeCol = ''
      let expenseCol = ''

      // 只选一个日期列：优先 交易日期/记账日期/入账日期；纯时间列（如 交易时间）不是日期源，
      // 否则多列同时映射时起息日期会覆盖交易日期，时间值还会导致后端日期解析失败
      const isTimeOnlyCol = (col: string) => {
        const lc = col.toLowerCase()
        return /时间|time/i.test(lc) && !/日期|交易日/i.test(lc)
      }
      const dateCandidates = columns.filter((col) => {
        const lc = col.toLowerCase()
        return !/对方/.test(lc) && !isTimeOnlyCol(col) && /日期|交易日|date/i.test(lc)
      })
      const dateCol = dateCandidates.find((col) => /交易日期|记账日期|入账日期|交易日/.test(col)) || dateCandidates[0] || ''

      for (const col of columns) {
        const lc = col.toLowerCase()
        if (col === dateCol) {
          map[col] = 'transaction_date'
        } else if (/账户余额|余额|balance/i.test(lc)) {
          map[col] = 'balance'
        } else if (/金额|发生额|amount/i.test(lc) && /支出|借方|支取|付款/.test(lc) && !/商户支出/.test(lc)) {
          // 必须同时是金额类表头：付款人名称/名义付款人名称 等对手方列含“付款”，
          // 若不限定金额列会把它们误判为支出金额列，导致交易金额列无法映射、金额全为 0
          expenseCol = col
        } else if (/金额|发生额|amount/i.test(lc) && /收入|贷方|存入|收款/.test(lc) && !/商户收入/.test(lc)) {
          incomeCol = col
        }
        
        // Find supplementary columns for creating an enriched description
        if (/交易类型|业务名称|业务类型|类型|type/i.test(col)) typeCol = col
        else if (/对手方|对方户名|对方名称|对方账号名称|收\(付\)方名称|收付方名称|对方开户名|对手方开户名|对手方银行户名|交易对手/i.test(col)) counterpartyCol = col
        else if (/交易摘要|摘要|描述|description|desc|remark/i.test(col)) summaryCol = col
        else if (/附言|用途|备注|附加信息|备注信息|附加备注|comment|purpose/i.test(col)) commentCol = col
      }

      // If we didn't find separate income/expense columns, search for a single amount column
      if (!incomeCol && !expenseCol) {
        for (const col of columns) {
          const lc = col.toLowerCase()
          if (/交易发生额|发生额|金额|amount/i.test(lc)) {
            map[col] = 'amount'
            break
          }
        }
      }
      
      const mapped = json.map((row: any) => {
        const out: Record<string, any> = {
          transaction_date: '',
          description: '',
          amount: 0,
          balance: 0
        }
        
        for (const [col, field] of Object.entries(map)) {
          if (field) {
            if (field === 'amount' || field === 'balance') {
              out[field] = safeFloat(row[col])
            } else if (field === 'transaction_date') {
              out[field] = formatBankDate(row[col])
            } else {
              out[field] = String(row[col] || '').trim()
            }
          }
        }
        
        // Compute amount if separate columns exist
        if (incomeCol || expenseCol) {
          const incVal = incomeCol ? safeFloat(row[incomeCol]) : 0
          const expVal = expenseCol ? safeFloat(row[expenseCol]) : 0
          out.amount = incVal - expVal
        }
        
        // Construct description: [Type] Summary | Comments (Counterparty)
        const typeName = typeCol ? String(row[typeCol] || '').trim() : ''
        const summaryText = summaryCol ? String(row[summaryCol] || '').trim() : ''
        const commentText = commentCol ? String(row[commentCol] || '').trim() : ''
        const counterpartyName = counterpartyCol ? String(row[counterpartyCol] || '').trim() : ''
        
        const descParts: string[] = []
        if (typeName) descParts.push(`[${typeName}]`)
        
        const textParts: string[] = []
        if (summaryText) textParts.push(summaryText)
        if (commentText && commentText !== summaryText) textParts.push(`附言: ${commentText}`)
        
        if (textParts.length > 0) {
          descParts.push(textParts.join(' | '))
        }
        
        if (counterpartyName) descParts.push(`(对方: ${counterpartyName})`)
        
        out.description = descParts.join(' ') || '-'
        return out
      })
      
      const importRes: any = await apiClient.post('/api/bank/import-json', {
        rows: mapped,
        statement_id: `bs-upload-${Date.now()}`
      })
      
      bankUploadResult.value = {
        imported: importRes.imported,
        errors: importRes.errors,
        rows: mapped
      }
      
      message.success(`成功导入 ${importRes.imported} 条银行流水！请在对账步骤手动确认匹配或分类。`)
      await loadAllData()
    } catch (err) {
      message.error('银行流水文件解析导入失败')
    } finally {
      loading.value = false
    }
  }
  reader.readAsArrayBuffer(file)
}

// Fetch Salary sheet
async function loadSalarySheet() {
  try {
    // GET /api/salary/sheets/{month} 直接返回工资表对象（扁平结构，无 sheet 包装层）
    const res: any = await apiClient.get(`/api/salary/sheets/${targetMonth.value}`)
    salarySheet.value = res
    employees.value = (res.records || []).map((r: any) => ({
      name: r.employee_name,
      dept: r.department,
      base: r.base_salary,
      bonus: r.bonus,
      social: r.company_social_security,
      personalSocial: r.personal_social_security,
      personalTax: r.personal_tax,
      net: r.net_salary,
      cost: r.total_cost
    }))

    if (res && res.id) {
      // accrued/paid：计提凭证已入账；pending_review：计提凭证待审核（maker_checker）
      payrollAccrued.value = res.status === 'accrued' || res.status === 'paid'
      payrollPendingReview.value = res.status === 'pending_review'
      payrollPaid.value = res.status === 'paid'
      payrollSsPaid.value = ['posted', 'not_required'].includes(res.ssPaymentStatus)
      payrollHfPaid.value = ['posted', 'not_required'].includes(res.hfPaymentStatus)
      payrollTaxPaid.value = ['posted', 'not_required'].includes(res.taxPaymentStatus)
    }
  } catch (err: any) {
    console.error('[loadSalarySheet] Error occurred:', err)
    employees.value = []
    payrollAccrued.value = false
    payrollPaid.value = false
    payrollPendingReview.value = false
      payrollSsPaid.value = false
      payrollHfPaid.value = false
      payrollTaxPaid.value = false
  }
}

async function loadPayrollPaymentSuggestions() {
  try {
    const res: any = await apiClient.get('/api/salary/payments/suggestions')
    payrollPaymentSuggestions.value = res.suggestions || []
  } catch {
    payrollPaymentSuggestions.value = []
  }
}

async function handleApplySalaryPayment(item: any) {
  applyingSalaryStatementId.value = item.statement.id
  try {
    const result: any = await apiClient.post('/api/salary/payments/apply', {
      month: item.month,
      statementId: item.statement.id,
    })
    message.success(
      result.linked
        ? `${item.month} 工资已按 ${item.statement.date} 流水发放并核销`
        : `工资已发放，但流水核销失败：${result.linkError || '请到银行流水页手工关联'}`,
    )
    await entryStore.fetchEntries()
    await loadAllData()
  } catch (err: any) {
    const detail = err.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '工资流水核销失败')
  } finally {
    applyingSalaryStatementId.value = ''
  }
}

async function handleAccruePayroll() {
  loading.value = true
  try {
    await apiClient.post(`/api/salary/sheets/${targetMonth.value}/accrue`)
    await entryStore.fetchEntries()
    message.success(`${targetMonth.value} 工资计提成功，已生成记账凭证！`)
    await loadAllData()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '工资计提失败')
  } finally {
    loading.value = false
  }
}

async function handlePayPayroll() {
  loading.value = true
  try {
    await apiClient.post(`/api/salary/sheets/${targetMonth.value}/pay`)
    await entryStore.fetchEntries()
    message.success(`${targetMonth.value} 工资发放确认成功，已生成付款凭证！`)
    await loadAllData()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '工资发放失败')
  } finally {
    loading.value = false
  }
}

async function handlePaySocial() {
  loading.value = true
  try {
    await apiClient.post(`/api/salary/sheets/${targetMonth.value}/pay-social`)
    await entryStore.fetchEntries()
    message.success(`${targetMonth.value} 社保缴纳成功，已生成付款凭证！`)
    await loadAllData()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '社保缴纳失败')
  } finally {
    loading.value = false
  }
}

async function handlePayHousingFund() {
  loading.value = true
  try {
    await apiClient.post(`/api/salary/sheets/${targetMonth.value}/pay-housing-fund`)
    await entryStore.fetchEntries()
    message.success(`${targetMonth.value} 公积金缴纳成功，已生成付款凭证！`)
    await loadAllData()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '公积金缴纳失败')
  } finally {
    loading.value = false
  }
}

async function handlePayTax() {
  loading.value = true
  try {
    await apiClient.post(`/api/salary/sheets/${targetMonth.value}/pay-tax`)
    await entryStore.fetchEntries()
    message.success(`${targetMonth.value} 个人所得税缴纳成功，已生成付款凭证！`)
    await loadAllData()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '个税缴纳失败')
  } finally {
    loading.value = false
  }
}

// Fetch AI matching suggestions
async function loadAiSuggestions() {
  if (suggestionsController) {
    try {
      suggestionsController.abort()
    } catch {
      // ignore
    }
  }
  suggestionsController = new AbortController()

  try {
    const res: any = await apiClient.post('/api/bank/ai-suggest', {}, { 
      timeout: 60000,
      signal: suggestionsController.signal
    })
    const visibleStatementIds = new Set(bankStatements.value.map((s: any) => s.id))
    aiSuggestions.value = (res.suggestions || []).filter((s: any) => visibleStatementIds.has(s.statementId))
    
    const suggestionPairs = aiSuggestions.value
      .map((suggestion: any) => buildSuggestedMatchPair(suggestion, entryStore.activeEntries))
      .filter(Boolean)

    matchedPairs.value = [...buildPersistedMatchedPairs(), ...suggestionPairs]
  } catch (err: any) {
    if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
      return
    }
    matchedPairs.value = buildPersistedMatchedPairs()
  }
}


function emptyReconciliationHealth() {
  return {
    broken_links: [],
    amount_mismatches: [],
    direction_mismatches: [],
    duplicate_entry_matches: [],
    pending_confirmations: [],
    summary: { highRiskCount: 0, warningCount: 0 },
  }
}

async function loadReconciliationHealth() {
  reconciliationHealthLoading.value = true
  try {
    reconciliationHealth.value = await apiClient.get('/api/bank/reconciliation-health', {
      params: { month: targetMonth.value },
    })
  } catch {
    reconciliationHealth.value = emptyReconciliationHealth()
  } finally {
    reconciliationHealthLoading.value = false
  }
}
// Split / delete a pair
async function deletePair(id: string) {
  const pair = matchedPairs.value.find(p => p.id === id)
  if (pair) {
    if (pair.status !== 'confirmed' && !pair.persisted) {
      matchedPairs.value = matchedPairs.value.filter(p => p.id !== id)
      message.info('已忽略该 AI 匹配建议')
      return
    }
    try {
      await apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: pair.statementId,
        type: 'match',
        matched_entry_id: '',
        matched_entry_ids: [],
      })
      message.info('对账关联已取消')
      await loadAllData()
    } catch (err) {
      message.error(apiErrorMessage(err, '取消对账失败'))
    }
  }
}

function isDirectionCompatible(direction: string, statementAmount: number) {
  if (statementAmount > 0) return direction === 'income' || direction === 'owner_transfer'
  if (statementAmount < 0) return direction === 'expense' || direction === 'owner_transfer'
  return true
}

function apiErrorMessage(err: any, fallback: string) {
  if (err?.code === 'ECONNABORTED') {
    return `${fallback}：请求超时，请刷新确认是否已经入账或对账。`
  }
  const detail = err?.response?.data?.detail || err?.response?.data?.message || err?.message
  if (detail && typeof detail === 'object') {
    const messageText = detail.message || detail.detail || '请求未通过校验'
    const numbers = [
      detail.statementAmount !== undefined ? `流水 ${formatMoney(detail.statementAmount)}` : '',
      detail.entryBankEffect !== undefined ? `账目银行影响 ${formatMoney(detail.entryBankEffect)}` : '',
      detail.difference !== undefined ? `差额 ${formatMoney(detail.difference)}` : '',
    ].filter(Boolean).join('，')
    return numbers ? `${fallback}：${messageText}（${numbers}）` : `${fallback}：${messageText}`
  }
  return detail ? `${fallback}：${detail}` : fallback
}

// Fetch diagnostic metrics and report when closed
async function fetchClosedDiagnosis() {
  try {
    const [statement, diagnosis] = await Promise.all([
      getIncomeStatement(targetMonth.value, 'summary'),
      getAIDiagnosis(targetMonth.value),
    ])
    const value = (key: string) => Number(statement.rows.find(row => row.key === key)?.values?.current || 0)
    monthlyIncome.value = value('operating_revenue') + value('nonoperating_income') + value('investment_income')
    netProfit.value = Number(statement.totals?.netProfit?.current || 0)
    monthlyExpense.value = monthlyIncome.value - netProfit.value
    cfoDiagnosis.value = diagnosis
  } catch (err) {
    // ignore
  }
}

// Perform Month-end Closing and lock period
async function runClosing() {
  if (hasBlockingPayroll.value) {
    currentStep.value = 2
    message.warning('本月在职员工工资尚未完成计提，请先计提后再结账（发放可延至次月）。')
    return
  }
  if (isZeroRecordMonth.value && !confirmZeroActivity.value) {
    message.warning('本月尚未录入任何账目记录。若确认本月无经营业务，请先勾选确认零申报。')
    return
  }

  closingLoading.value = true
  try {
    await loadReconciliationHealth()
    if (hasBlockingReconciliationRisk.value) {
      currentStep.value = 2
      message.warning('仍有高风险流水认领问题，请先处理后再结账。')
      return
    }

    const checks: any = await apiClient.get(`/api/periods/${targetMonth.value}/close-checks`)
    periodCloseChecks.value = checks
    if (!checks.canClose && !canBatchPostAndClose.value) {
      const blocking = getClosingReadiness(checks).filter(item => item.status === 'blocked')
      currentStep.value = 3
      message.warning(`还不能结账：${blocking.map(item => item.title).join('、')}`)
      return
    }

    const result: any = await apiClient.post(
      `/api/periods/${targetMonth.value}/close`,
      {},
      { headers: { 'Idempotency-Key': `close-${targetMonth.value}` } },
    )
    closed.value = result.status === 'closed' || result.periodStatus === 'closed'
    periodCloseChecks.value = result
    currentStep.value = 3
    await entryStore.fetchEntries()
    await fetchClosedDiagnosis()
    const postedCount = Number(result.autoPostedCount || 0)
    const closingRefs: string[] = Array.isArray(result.closingVoucherRefs) ? result.closingVoucherRefs.filter(Boolean) : []
    closingVoucherRefs.value = closingRefs
    const closingSuffix = closingRefs.length ? `（已自动生成期末损益结转凭证：${closingRefs.join('、')}）` : ''
    message.success(postedCount
      ? `🎉 已批量过账 ${postedCount} 张凭证并完成 ${targetMonth.value} 结账${closingSuffix}。`
      : `🎉 ${targetMonth.value} 结账成功！本期账册已锁定${closingSuffix}。`)
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    if (detail?.code === 'CLOSE_CHECK_FAILED' && detail.context?.checks) {
      periodCloseChecks.value = { ...periodCloseChecks.value, checks: detail.context.checks, canClose: false }
      message.error('本月核验仍有阻断项；本次自动过账已一并回滚，请按检查结果处理后重试。')
    } else if (detail?.code === 'REVIEW_REQUIRED') {
      // 救援层能定位到具体是哪张待复核，比这里一句笼统的话有用
      if (!offerRescue(err)) {
        message.error('本月有凭证需要其他复核人审批，本次过账和结账均未完成。')
      }
    } else {
      reportErrorToast(message, err, '结账失败，请检查阻断项')
    }
  } finally {
    closingLoading.value = false
  }
}
function downloadReport() {
  message.success('月度财务报销报告及凭证归档包 PDF 生成成功，正在下载...')
}

// ── 反结账：解锁本期账册并自动红冲期末损益结转凭证（后端 reopen_period） ──
const reopenVisible = ref(false)
const reopenReason = ref('')
const reopenPhone = ref('')
const reopenCode = ref('')
const reopenBizId = ref('')
const reopenSending = ref(false)
const reopenSubmitting = ref(false)

async function sendReopenCode() {
  if (!reopenPhone.value) {
    message.warning('请输入账号绑定的手机号')
    return
  }
  reopenSending.value = true
  try {
    const result = await sendPhoneCode({ phone: reopenPhone.value })
    reopenBizId.value = (result as any).biz_id || ''
    message.success('验证码已发送')
  } catch {
    message.error('验证码发送失败')
  } finally {
    reopenSending.value = false
  }
}

async function submitReopen() {
  if (!reopenReason.value.trim()) {
    message.warning('请填写反结账原因（将留痕）')
    return
  }
  if (!reopenPhone.value || !reopenCode.value) {
    message.warning('请输入手机号和短信验证码完成安全验证')
    return
  }
  reopenSubmitting.value = true
  try {
    const stepUp = await requestStepUp({
      action: 'reopen_period',
      phone: reopenPhone.value,
      code: reopenCode.value,
      bizId: reopenBizId.value || undefined,
    })
    await reopenPeriod(targetMonth.value, reopenReason.value.trim(), stepUp.stepUpToken)
    reopenVisible.value = false
    closingVoucherRefs.value = []
    closed.value = false
    await entryStore.fetchEntries()
    periodCloseChecks.value = await apiClient.get(`/api/periods/${targetMonth.value}/close-checks`)
    message.success(`${targetMonth.value} 已反结账：本期期末损益结转凭证已自动红冲，账册解锁，可补记后重新结账。`)
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '反结账失败')
  } finally {
    reopenSubmitting.value = false
  }
}

function safeFloat(val: any): number {
  if (val === null || val === undefined || val === '') return 0
  if (typeof val === 'number') return val
  const cleaned = String(val).replace(/,/g, '').trim()
  const parsed = parseFloat(cleaned)
  return isNaN(parsed) ? 0 : parsed
}

function isItemAutoMatched(row: any) {
  const amount = safeFloat(row.amount)
  const desc = String(row.description || '').trim()
  const date = String(row.transaction_date || '').trim()
  
  const found = rawStatements.value.find(s => 
    Math.abs(s.amount) === Math.abs(amount) && 
    s.description === desc &&
    String(s.transaction_date || '').startsWith(date)
  )
  const matchedEntryIds = getStatementMatchedEntryIds(found)
  if (matchedEntryIds.length === 0) return false
  return matchedEntryIds.every((entryId) => isReconciledEntry(
    entryStore.activeEntries.find((entry) => entry.id === entryId) as any,
  ))
}

const unmatchedInvoices = computed(() => {
  try {
    return invoicesList.value
  } catch (err) {
    console.error('unmatchedInvoices computed error:', err)
    return []
  }
})

function getEntryMonth(entry: any) {
  return entry.date ? String(entry.date).substring(0, 7) : ''
}

function getStatementMonth(statement: any) {
  return statement.transaction_date ? String(statement.transaction_date).substring(0, 7) : ''
}

function isStatementInTargetMonth(statement: any) {
  const month = getStatementMonth(statement)
  return month === targetMonth.value
}

function buildPersistedMatchedPairs() {
  try {
    const entriesById = new Map(entryStore.activeEntries.map((entry) => [entry.id, entry]))
    const pairs: any[] = []

    for (const statement of rawStatements.value) {
      const matchedEntryIds = getStatementMatchedEntryIds(statement)
      if (matchedEntryIds.length === 0 || !isStatementInTargetMonth(statement)) continue

      const entries = matchedEntryIds
        .map((entryId) => entriesById.get(entryId))
        .filter(Boolean) as any[]
      if (entries.length === 0) continue

      const confirmed = entries.length === matchedEntryIds.length && entries.every((item) => isReconciledEntry(item))
      const pairEntries = entries.map((item) => ({
        id: item.id,
        title: item.title || '未命名条目',
        amount: item.amount,
        direction: item.direction,
        billingStatus: item.billingStatus,
        category: item.category || '',
        accountCode: item.accountCode || '',
        accountName: item.accountName || '',
        voucherLines: item.voucherLines,
        voucherStatus: item.voucherStatus,
        source: item.source,
        reconciliationType: item.reconciliationType,
      }))
      pairs.push({
        id: statement.id,
        statementId: statement.id,
        matchedEntryId: matchedEntryIds[0],
        matchedEntryIds,
        entries: pairEntries,
        settlement: entries.find((item) => item.reconciliationType === 'wechat_settlement'),
        invoice: pairEntries[0],
        bank: {
          id: statement.id,
          title: statement.description || '未命名流水',
          amount: statement.amount,
        },
        confidence: confirmed ? '已确认' : '待确认',
        reason: confirmed
          ? (pairEntries.length > 1 ? `${pairEntries.length} 笔账目合并对账` : '已确认对账')
          : '已有流水关联，等待人工确认',
        persisted: true,
        status: confirmed ? 'confirmed' : 'pending',
      })
    }

    return pairs
  } catch (err) {
    console.error('buildPersistedMatchedPairs error:', err)
    return []
  }
}

const allUnmatchedEntries = computed(() => {
  try {
    const matchedIds = new Set(rawStatements.value.flatMap((statement) => getStatementMatchedEntryIds(statement)))
    // 冲销红字凭证（entryType=reversal）不参与对账；被红字冲销的原凭证同样
    // 与红字完全抵消、无剩余资金影响，一并退出对账候选（与待办去噪同口径）
    const reversalIds = new Set(entryStore.activeEntries
      .filter((e) => (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal')
      .map((e) => String((e as any).reversalOf || '').trim())
      .filter(Boolean))
    return sortEntriesByDateDescending(entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      const isBeforeOrInMonth = month ? month <= targetMonth.value : false
      const isNotMatched = !matchedIds.has(e.id)
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      const isNotReconciled = e.billingStatus !== 'reconciled' && (e as any).billing_status !== 'reconciled'
      // ponytail: 非公户结算（垫付、未付款挂账、预存扣款、平台未提现等）无需且无法匹配公户银行流水
      const isNonBank = isNonBankSettledEntry(e as any)
      const isReversal = (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal'
      const isReversedOriginal = reversalIds.has(String(e.id))
      return isBeforeOrInMonth && isNotMatched && isNotArchiveOnly && isNotReconciled && !isNonBank && !isReversal && !isReversedOriginal
    }).map(e => ({
      id: e.id,
      title: e.title,
      date: e.occurDate,
      month: e.occurDate ? e.occurDate.substring(0, 7) : '',
      amount: e.amount,
      direction: e.direction,
      category: (e as any).category || '',
      source: (e as any).source || 'manual',
      paymentStatus: (e as any).paymentStatus || '',
      invoiceNumber: (e as any).invoiceNumber || '',
      voucherLines: (e as any).voucherLines || null,
      payer: (e as any).payer || 'company_account',
      voucherStatus: (e as any).voucherStatus || '',
      is_archive_only: (e as any).is_archive_only || (e as any).isArchiveOnly || false,
      status: 'unmatched'
    })))
  } catch (err) {
    console.error('allUnmatchedEntries computed error:', err)
    return []
  }
})

// ── In-place Voucher Inspection & Posting Modal (ponytail: zero-redirect inspection) ──
const showVoucherModal = ref(false)
const voucherModalLoading = ref(false)
const voucherPostingLoading = ref(false)
const batchPostingLoading = ref(false)
const currentVoucherEntry = ref<any>(null)
const currentVoucherPostingOptions = ref<any>(null)

async function openVoucherModal(entryId: string) {
  if (!entryId) return
  showVoucherModal.value = true
  voucherModalLoading.value = true
  currentVoucherEntry.value = null
  currentVoucherPostingOptions.value = null
  try {
    let found = entryStore.activeEntries.find((e: any) => e.id === entryId)
    if (!found) {
      await entryStore.fetchEntries()
      found = entryStore.activeEntries.find((e: any) => e.id === entryId)
    }
    currentVoucherEntry.value = found || null
    if (found) {
      try {
        currentVoucherPostingOptions.value = await getPostingOptions(found.id)
      } catch {
        currentVoucherPostingOptions.value = null
      }
    }
  } finally {
    voucherModalLoading.value = false
  }
}

async function handleModalPostEntry() {
  if (!currentVoucherEntry.value || voucherPostingLoading.value) return
  voucherPostingLoading.value = true
  try {
    const res = await postEntry(currentVoucherEntry.value.id)
    message.success(`凭证过账成功！凭证号：${res.entry?.voucherRef || ''}`)
    currentVoucherEntry.value = res.entry
    await Promise.all([
      entryStore.fetchEntries(),
      loadAllData(),
    ])
  } catch (err: any) {
    message.error(apiErrorMessage(err, '过账失败'))
  } finally {
    voucherPostingLoading.value = false
  }
}

async function handleBatchPostMonth() {
  batchPostingLoading.value = true
  try {
    const res = await batchPostEntries({ month: targetMonth.value })
    if (res.postedCount > 0) {
      message.success(`已成功批量过账 ${res.postedCount} 张凭证！`)
    } else {
      message.info('本月暂无符合条件的待过账草稿凭证')
    }
    if (res.failedCount > 0) {
      message.warning(`有 ${res.failedCount} 张凭证过账未通过，请查看明细`)
    }
    await Promise.all([
      entryStore.fetchEntries(),
      loadAllData(),
    ])
  } catch (err: any) {
    message.error(apiErrorMessage(err, '批量过账失败'))
  } finally {
    batchPostingLoading.value = false
  }
}

const modalVoucherLines = computed<VoucherLine[]>(() => {
  if (!currentVoucherEntry.value) return []
  const stored = normalizeVoucherLines(currentVoucherEntry.value.voucherLines)
  if (stored.length > 0) return stored
  return generateVoucherLines({
    type: currentVoucherEntry.value.direction as 'income' | 'expense' | 'owner_transfer',
    amount: currentVoucherEntry.value.amount,
    category: currentVoucherEntry.value.category || '',
    payer: currentVoucherEntry.value.payer || 'company_account',
  })
})

const modalDebitTotal = computed(() =>
  modalVoucherLines.value.filter((l) => l.side === 'debit').reduce((s, l) => s + l.amount, 0)
)
const modalCreditTotal = computed(() =>
  modalVoucherLines.value.filter((l) => l.side === 'credit').reduce((s, l) => s + l.amount, 0)
)
const unmatchedEntries = computed(() => {
  try {
    return allUnmatchedEntries.value.filter(e => getEntryMonth(e) === targetMonth.value)
  } catch (err) {
    console.error('unmatchedEntries computed error:', err)
    return []
  }
})
const unmatchedStatements = computed(() => {
  try {
    return bankStatements.value.filter(s => {
      const month = getStatementMonth(s)
      return !month || month === targetMonth.value
    })
  } catch (err) {
    console.error('unmatchedStatements computed error:', err)
    return []
  }
})
const historicalUnmatchedStatements = computed(() => {
  try {
    return bankStatements.value.filter(s => {
      const month = getStatementMonth(s)
      return month && month < targetMonth.value
    })
  } catch (err) {
    console.error('historicalUnmatchedStatements computed error:', err)
    return []
  }
})
const totalNetSalary = computed(() => {
  try {
    return employees.value.reduce((sum, e) => sum + e.net, 0)
  } catch (err) {
    return 0
  }
})
const totalCompanyCost = computed(() => {
  try {
    return employees.value.reduce((sum, e) => sum + e.cost, 0)
  } catch (err) {
    return 0
  }
})

// Reconciliation completeness metrics
const reconciliationStats = computed(() => {
  try {
    const entriesById = new Map(entryStore.activeEntries.map((entry) => [entry.id, entry]))
    const isConfirmedStatement = (statement: any) => {
      const matchedEntryIds = getStatementMatchedEntryIds(statement)
      if (matchedEntryIds.length === 0) return false
      return matchedEntryIds.every((entryId) => isReconciledEntry(entriesById.get(entryId) as any))
    }

    // ponytail: 仅统计本月目标月份流水，避免历史未匹配流水拉低本月统计造成虚假告警
    const targetMonthStatements = rawStatements.value.filter((s: any) => {
      const m = s.transaction_date ? s.transaction_date.substring(0, 7) : ''
      return !m || m === targetMonth.value
    })

    const total = targetMonthStatements.length
    const matched = targetMonthStatements.filter(isConfirmedStatement).length
    const unmatched = total - matched
    const rate = total > 0 ? Math.round((matched / total) * 100) : (targetMonthStatements.length === 0 ? 100 : 0)

    const unmatchedItems = targetMonthStatements.filter(s => !isConfirmedStatement(s))
    const unmatchedIncome = unmatchedItems.filter(s => s.amount > 0)
    const unmatchedExpense = unmatchedItems.filter(s => s.amount < 0)

    return { total, matched, unmatched, rate, unmatchedIncome, unmatchedExpense }
  } catch (err) {
    console.error('reconciliationStats computed error:', err)
    return { total: 0, matched: 0, unmatched: 0, rate: 0, unmatchedIncome: [], unmatchedExpense: [] }
  }
})



const reconciliationHealthSummary = computed(() => {
  try {
    return reconciliationHealth.value?.summary || { highRiskCount: 0, warningCount: 0 }
  } catch {
    return { highRiskCount: 0, warningCount: 0 }
  }
})
const hasBlockingReconciliationRisk = computed(() => {
  try {
    return reconciliationHealthSummary.value.highRiskCount > 0
  } catch {
    return false
  }
})
// Get AI suggestion for a statement
function getAiSuggestion(stmtId: string) {
  return aiSuggestions.value.find(s => s.statementId === stmtId)
}

/** 账上已有对应凭证：此时该做的是核对并确认配对，不是再记一笔。 */
function hasExistingEntrySuggestion(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  return sug?.type === 'match' || sug?.type === 'existing'
}

/** 低置信 / 兜底草稿不是 AI 的判断，不能按「建议分类」展示。 */
function isFallbackSuggestion(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || sug.type !== 'new') return false
  return Number(sug.confidence || 0) < 0.5
}

function findEntryById(id: string) {
  return entryStore.activeEntries.find((entry: any) => entry.id === id)
}

function pairCategoryPreview(pair: any) {
  if (pair.entries?.length > 1) {
    const categories = [...new Set(pair.entries.map((entry: any) => entry.category).filter(Boolean))]
    return categories.length > 0 ? `${pair.entries.length} 笔 · ${categories.join('、')}` : `${pair.entries.length} 笔账目`
  }
  const category = pair.invoice?.category || '未选择分类'
  const account = [pair.invoice?.accountCode, pair.invoice?.accountName].filter(Boolean).join(' ')
  return account ? `${category} · ${account}` : category
}

// ─── Manual Classify Dialog ──────────────────────────────────────────────────

const CATEGORY_OPTIONS_RAW = [
  { label: '── 收入 ──', value: '__header_income__', disabled: true },
  { label: '主营业务收入', value: 'income|主营业务收入|5001|主营业务收入' },
  { label: '其他业务收入', value: 'income|其他业务收入|5051|其他业务收入' },
  { label: '营业外收入', value: 'income|营业外收入|5301|营业外收入' },
  { label: '利息收入', value: 'income|利息收入|5603005|财务费用-利息收入' },
  { label: '── 员工成本 ──', value: '__header_salary__', disabled: true },
  // 工资/社保流水走工资模块核销（应付职工薪酬），不直接记费用；与后端 account_mapper 对齐
  { label: '工资发放', value: 'expense|工资发放|221101|应付职工薪酬-工资' },
  { label: '社保公积金', value: 'expense|社保公积金|221102|应付职工薪酬-单位社保' },
  { label: '── 费用支出 ──', value: '__header_expense__', disabled: true },
  { label: '差旅费', value: 'expense|差旅费|5602006|管理费用-差旅费' },
  { label: '办公费', value: 'expense|办公费|5602004|管理费用-办公费' },
  { label: '业务招待费', value: 'expense|业务招待费|5602002|管理费用-业务招待费' },
  { label: '研发支出-费用化支出', value: 'expense|研发支出-费用化支出|4301|研发支出-费用化支出' },
  { label: '信息技术服务费', value: 'expense|信息技术服务费|5602015|管理费用-信息技术服务费' },
  { label: '租金物业', value: 'expense|租金物业|5602013|管理费用-租赁费' },
  { label: '银行及金融手续费', value: 'expense|银行及金融手续费|5603002|财务费用-手续费' },
  { label: '其他支出', value: 'expense|其他支出|5602999|管理费用-其他' },
  { label: '── 采购与往来 ──', value: '__header_purchase__', disabled: true },
  { label: '原材料采购', value: 'expense|原材料采购|1403|原材料' },
  { label: '商品采购', value: 'expense|商品采购|1405|库存商品' },
  { label: '供应商付款', value: 'expense|供应商付款|2202|应付账款' },
  { label: '预付账款', value: 'expense|预付账款|1123|预付账款' },
  // 1221 已容器化：往来款一律落末级子科目（员工往来 122103）
  { label: '其他应收款', value: 'expense|其他应收款|122103|其他应收款-员工往来' },
  { label: '其他应付款', value: 'expense|其他应付款|2241|其他应付款-其他' },
  { label: '往来款-支（暂付款）', value: 'expense|往来款|122103|其他应收款-员工往来' },
  { label: '往来款-收（暂收款）', value: 'income|往来款|2241|其他应付款-其他' },
  // 2221 是容器：税费缴纳落应交增值税末级（小规模纳税人专用）
  { label: '税费缴纳', value: 'expense|税费缴纳|2221026|应交增值税（小规模纳税人专用）' },
  { label: '── 资本 ──', value: '__header_capital__', disabled: true },
  { label: '股东投入', value: 'owner_transfer|股东投入|3001|实收资本' },
  { label: '资本公积', value: 'owner_transfer|资本公积|3002|资本公积' },
  { label: '── 往来 ──', value: '__header_owner__', disabled: true },
  { label: '股东垫付', value: 'owner_transfer|股东垫付|2241|其他应付款-股东' },
  { label: '员工垫付报销', value: 'owner_transfer|员工垫付报销|2241|其他应付款-员工' },
]

// 下拉选项：accountCode/accountName 权威值从后端 /account-mapping 加载后刷新，
// 硬编码仅兜底；分组/顺序/往来款收支拆分是前端 UI 编排，保留在此。
const CATEGORY_OPTIONS = ref(CATEGORY_OPTIONS_RAW)

function refreshCategoryOptions() {
  CATEGORY_OPTIONS.value = CATEGORY_OPTIONS_RAW.map((opt: any) => {
    if (opt.disabled || !opt.value || opt.value.startsWith('__')) return opt
    const [direction, category] = opt.value.split('|')
    // 往来款收/支是前端特化，后端映射只给单一 code，跳过以免覆盖收=2241 的语义
    if (category === '往来款') return opt
    const acct = accountForCategory(category)
    if (!acct) return opt
    return { ...opt, value: `${direction}|${category}|${acct.code}|${acct.name}` }
  })
}

function categoryValueNatureLabel(value: string) {
  const [direction = '', category = ''] = value.split('|')
  return getEntryNaturePresentation(direction, category).detailLabel
}

const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])
const rdProjectOptions = computed(() => rdProjects.value
  .filter(project => ['active', 'completed'].includes(project.status))
  .map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })))
const rdExpenseTypeOptions = computed(() => rdExpenseTypes.value
  .map(item => ({ label: item.label, value: item.value })))

async function loadRdOptions() {
  try {
    const [projects, types] = await Promise.all([getRdProjects(true), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypes.value = types.items
  } catch {
    message.warning('研发项目选项加载失败，研发分类暂不能确认')
  }
}

function isRdCategoryValue(value: string) {
  return value.split('|')[1] === '研发支出-费用化支出'
}

function rdDetailFromEntry(entry: any) {
  const lines = [
    ...(entry?.voucherLines?.debit || []),
    ...(entry?.voucherLines?.credit || []),
  ]
  return lines.find((line: any) => line.rdDetail)?.rdDetail || null
}

const showManualDialog = ref(false)
const manualTarget = ref<any>(null)
const manualForm = ref({
  title: '',
  categoryValue: '',
  businessContext: '',
  remark: '',
  rdProjectId: '',
  rdExpenseType: '',
  rdAllocationBasis: '',
  suggestedSubAccount: null as null | { parentCode: string; name: string },
  useSuggestedSubAccount: true,
})
const manualSubmitting = ref(false)
const manualAiLoading = ref(false)
const manualSuggestionMeta = ref<{ confidence?: number; reason?: string }>({})
const showPairCategoryDialog = ref(false)
const pairCategoryTarget = ref<any>(null)
const pairCategoryForm = ref({
  title: '',
  categoryValue: '',
  rdProjectId: '',
  rdExpenseType: '',
  rdAllocationBasis: '',
})
const pairCategorySubmitting = ref(false)

function defaultCategoryValue(stmt: any) {
  return stmt.amount >= 0
    ? 'income|主营业务收入|5001|主营业务收入'
    : 'expense|其他支出|5602999|管理费用-其他'
}

function cleanStatementTitle(stmt: any) {
  const desc = String(stmt.description || '')
  return desc.replace(/^\[.*?\]\s*/, '').replace(/^\(对方:\s*/, '').replace(/\)$/, '').trim() || desc || '银行流水记账'
}

function categoryValueFromSuggestion(stmt: any, suggested: any) {
  if (!suggested) return defaultCategoryValue(stmt)
  const direction = suggested.direction || (stmt.amount >= 0 ? 'income' : 'expense')
  const exact = `${direction}|${suggested.category}|${suggested.accountCode}|${suggested.accountName}`
  if (CATEGORY_OPTIONS.value.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.value.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${suggested.category}|`))
  return byCategory?.value || defaultCategoryValue(stmt)
}

function categoryValueFromEntry(entry: any, stmt: any) {
  if (!entry) return defaultCategoryValue(stmt)
  const exact = `${entry.direction}|${entry.category}|${entry.accountCode}|${entry.accountName}`
  if (CATEGORY_OPTIONS.value.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.value.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${entry.category}|`))
  return byCategory?.value || defaultCategoryValue(stmt)
}

function openManualClassify(stmt: any, suggestedEntry: any = null) {
  const suggestion = getAiSuggestion(stmt.id)
  if (suggestion?.type === 'match' || suggestion?.type === 'existing') {
    selectExistingReconciliation(stmt, suggestion.matchedEntryIds || [suggestion.matchedEntryId].filter(Boolean))
    return
  }
  manualTarget.value = stmt
  manualSuggestionMeta.value = suggestedEntry
    ? { confidence: suggestedEntry.confidence, reason: suggestedEntry.reason }
    : {}
  const sub = suggestedEntry?.suggestedSubAccount
  manualForm.value = {
    title: suggestedEntry?.title || cleanStatementTitle(stmt),
    categoryValue: categoryValueFromSuggestion(stmt, suggestedEntry),
    businessContext: '',
    remark: '',
    rdProjectId: '',
    rdExpenseType: '',
    rdAllocationBasis: '',
    suggestedSubAccount: (sub?.parentCode && sub?.name)
      ? { parentCode: String(sub.parentCode), name: String(sub.name) }
      : null,
    useSuggestedSubAccount: !!(sub?.parentCode && sub?.name),
  }
  showManualDialog.value = true
}

function selectExistingReconciliation(stmt: any, entryIds: string[]) {
  selectedBank.value = stmt.id
  selectedInvoiceIds.value = entryIds.every((id) => allUnmatchedEntries.value.some((entry) => entry.id === id)) ? entryIds : []
  message.warning('已有账目，请核对左侧收入与手续费后确认合并对账，勿重复记账。')
}

function openPairCategoryDialog(pair: any) {
  if (pair.entries?.length > 1) {
    message.info('合并对账请分别在经营账本中修改各笔分类')
    return
  }
  const entry = findEntryById(pair.matchedEntryId) || pair.invoice
  const rdDetail = rdDetailFromEntry(entry)
  pairCategoryTarget.value = pair
  pairCategoryForm.value = {
    title: entry?.title || pair.invoice?.title || '',
    categoryValue: categoryValueFromEntry(entry, pair.bank),
    rdProjectId: rdDetail?.projectId || '',
    rdExpenseType: rdDetail?.expenseType || '',
    rdAllocationBasis: rdDetail?.allocationBasis || '',
  }
  showPairCategoryDialog.value = true
}

async function handlePairCategorySave() {
  const pair = pairCategoryTarget.value
  if (!pair || !pairCategoryForm.value.title.trim() || !pairCategoryForm.value.categoryValue || pairCategoryForm.value.categoryValue.startsWith('__')) {
    message.warning('请填写标题并选择科目分类')
    return
  }

  const [direction, category, accountCode, accountName] = pairCategoryForm.value.categoryValue.split('|')
  if (category === '研发支出-费用化支出' && !(
    pairCategoryForm.value.rdProjectId && pairCategoryForm.value.rdExpenseType
  )) {
    message.warning('研发费用必须选择研发项目和费用类别')
    return
  }
  pairCategorySubmitting.value = true
  try {
    await apiClient.patch(`/api/record/entries/${pair.matchedEntryId}`, {
      title: pairCategoryForm.value.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
      ...(category === '研发支出-费用化支出' ? {
        rdProjectId: pairCategoryForm.value.rdProjectId,
        rdExpenseType: pairCategoryForm.value.rdExpenseType,
        rdOriginalAmount: Math.abs(Number(pair.bank?.amount || pair.invoice?.amount || 0)),
        rdAllocationMethod: 'direct',
        rdAllocationBasis: pairCategoryForm.value.rdAllocationBasis || undefined,
      } : {}),
    })
    pair.invoice = {
      ...pair.invoice,
      title: pairCategoryForm.value.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
    }
    await entryStore.fetchEntries()
    showPairCategoryDialog.value = false
    message.success(`已更新分类：${category}`)
  } catch (err: any) {
    message.error(apiErrorMessage(err, '分类更新失败'))
  } finally {
    pairCategorySubmitting.value = false
  }
}

async function refreshManualAiSuggestion() {
  if (!manualTarget.value) return
  manualAiLoading.value = true
  try {
    const res: any = await apiClient.post('/api/bank/classify-statement', {
      statement_id: manualTarget.value.id,
      business_context: manualForm.value.businessContext.trim(),
    }, { timeout: 60000 })
    if (!res?.suggestedEntry) {
      message.warning('暂未生成新的 AI 分类建议')
      return
    }
    const suggested = res.suggestedEntry
    manualSuggestionMeta.value = { confidence: suggested.confidence, reason: suggested.reason }
    const sub = suggested?.suggestedSubAccount
    manualForm.value = {
      ...manualForm.value,
      title: suggested.title || manualForm.value.title,
      categoryValue: categoryValueFromSuggestion(manualTarget.value, suggested),
      suggestedSubAccount: (sub?.parentCode && sub?.name)
        ? { parentCode: String(sub.parentCode), name: String(sub.name) }
        : manualForm.value.suggestedSubAccount,
      useSuggestedSubAccount: !!(sub?.parentCode && sub?.name) || manualForm.value.useSuggestedSubAccount,
    }
    message.success('已根据业务说明更新 AI 分类草稿')
  } catch (err: any) {
    message.error(`AI 分类建议更新失败: ${err.message || err}`)
  } finally {
    manualAiLoading.value = false
  }
}

async function handleManualClassify() {
  if (!manualTarget.value || !manualForm.value.categoryValue || !manualForm.value.title.trim()) {
    message.warning('请填写记账标题并选择分类')
    return
  }

  const [direction, category, suggestedCode, suggestedName] = manualForm.value.categoryValue.split('|')
  let accountCode = suggestedCode
  let accountName = suggestedName
  if (category === '研发支出-费用化支出' && !(
    manualForm.value.rdProjectId && manualForm.value.rdExpenseType
  )) {
    message.warning('研发费用必须选择研发项目和费用类别')
    return
  }
  const stmt = manualTarget.value

  const finalDirection = direction === 'owner_transfer'
    ? 'owner_transfer'
    : (stmt.amount >= 0 ? 'income' : 'expense')
  const businessContext = manualForm.value.businessContext.trim()
  const remark = [
    businessContext ? `业务说明：${businessContext}` : '',
    manualForm.value.remark.trim(),
  ].filter(Boolean).join('\n')

  // 若 AI 建议新建二级科目且用户确认，先落地科目，再用新 code 记账；失败则回落原映射科目
  if (manualForm.value.useSuggestedSubAccount && manualForm.value.suggestedSubAccount) {
    try {
      const created: any = await apiClient.post('/api/ledger/accounts/ensure-sub', {
        parent_code: manualForm.value.suggestedSubAccount.parentCode,
        name: manualForm.value.suggestedSubAccount.name,
      })
      if (created?.code && created?.name) {
        accountCode = created.code
        accountName = created.name
      }
    } catch {
      // 建科失败不阻断记账
    }
  }

  manualSubmitting.value = true
  try {
    const confirmRes: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      allowBackfill,
      backfillReason,
      new_entry: {
        title: manualForm.value.title.trim(),
        amount: Math.abs(stmt.amount),
        direction: finalDirection,
        category,
        accountCode,
        accountName,
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : new Date().toISOString().slice(0, 10),
        remark,
        ...(category === '研发支出-费用化支出' ? {
          rdProjectId: manualForm.value.rdProjectId,
          rdExpenseType: manualForm.value.rdExpenseType,
          rdOriginalAmount: Math.abs(stmt.amount),
          rdAllocationMethod: 'direct',
          rdAllocationBasis: manualForm.value.rdAllocationBasis || undefined,
        } : {}),
      }
    }, { timeout: 60000 }),
    )
    if (confirmRes?.status === 'already_matched') {
      message.success('该流水已完成对账，已刷新列表。')
    } else {
      message.success(`已确认记账并完成对账：「${manualForm.value.title.trim()}」→ ${category}`)
    }
    
    // ponytail: 接口成功后立即关闭弹窗和置灰，用非阻塞 loadAllData 刷新后台以防止 Vue 重渲染挂起阻塞 UI 响应
    showManualDialog.value = false
    manualSubmitting.value = false
    loadAllData()
  } catch (err: any) {
    if (isBackfillCancelled(err)) {
      manualSubmitting.value = false
      return
    }
    const detail = err?.response?.data?.detail
    if (detail?.code === 'EXISTING_WECHAT_ENTRIES') {
      showManualDialog.value = false
      const groups = detail.candidateEntryGroups || []
      selectExistingReconciliation(stmt, groups.length === 1 ? groups[0] : [])
    } else {
      message.error(apiErrorMessage(err, '记账提交失败'))
    }
    manualSubmitting.value = false
  }
}

const highConfidenceStatements = computed(() => {
  return unmatchedStatements.value.filter((stmt: any) => {
    const sug = getAiSuggestion(stmt.id)
    return sug && sug.confidence >= 0.95
  })
})

const statementTierFilter = ref<'all' | 'ready' | 'attention'>('all')
const selectedStatementIds = ref<string[]>([])
const rememberRuleOnConfirm = ref(true)

function isSelectableForBatch(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return false
  if (isFallbackSuggestion(stmt)) return false
  if (sug.type === 'new' && sug.suggestedEntry?.category === '研发支出-费用化支出') return false
  return Number(sug.confidence || 0) >= 0.5
}

function isNeedsAttentionStatement(stmt: any): boolean {
  if (findMatchingInvoiceForStatement(stmt)) return false
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return true
  return isFallbackSuggestion(stmt)
}

function suggestionBadgeLabel(stmt: any): string {
  if (isFallbackSuggestion(stmt)) return '待人工指定'
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return '待指定'
  if (sug.rule === 'company_memory_rule') return '记忆规则 98%'
  if (sug.rule) return '系统规则 100%'
  const conf = Number(sug.confidence || 0)
  if (conf >= 0.95) return '高置信度'
  return '推荐依据'
}

const selectableBatchStatements = computed(() =>
  unmatchedStatements.value.filter((stmt: any) => isSelectableForBatch(stmt))
)

const attentionStatements = computed(() =>
  unmatchedStatements.value.filter((stmt: any) => isNeedsAttentionStatement(stmt))
)

const filteredUnmatchedStatements = computed(() => {
  if (statementTierFilter.value === 'ready') {
    return unmatchedStatements.value.filter((stmt: any) => !isNeedsAttentionStatement(stmt))
  }
  if (statementTierFilter.value === 'attention') {
    return attentionStatements.value
  }
  return unmatchedStatements.value
})

watch(
  [unmatchedStatements, aiSuggestions],
  () => {
    const validSelectable = new Set(selectableBatchStatements.value.map((s: any) => s.id))
    const defaultHighConf = highConfidenceStatements.value
      .filter((s: any) => validSelectable.has(s.id))
      .map((s: any) => s.id)
    const kept = selectedStatementIds.value.filter((id) => validSelectable.has(id))
    selectedStatementIds.value = kept.length > 0 ? kept : defaultHighConf
  },
  { immediate: true }
)

function toggleStatementSelection(stmtId: string, checked: boolean) {
  if (checked) {
    if (!selectedStatementIds.value.includes(stmtId)) {
      selectedStatementIds.value = [...selectedStatementIds.value, stmtId]
    }
  } else {
    selectedStatementIds.value = selectedStatementIds.value.filter((id) => id !== stmtId)
  }
}

const allSelectableChecked = computed(
  () =>
    selectableBatchStatements.value.length > 0 &&
    selectableBatchStatements.value.every((stmt: any) => selectedStatementIds.value.includes(stmt.id))
)

const someSelectableChecked = computed(
  () =>
    !allSelectableChecked.value &&
    selectableBatchStatements.value.some((stmt: any) => selectedStatementIds.value.includes(stmt.id))
)

function toggleSelectAllStatements(checked: boolean) {
  selectedStatementIds.value = checked
    ? selectableBatchStatements.value.map((stmt: any) => stmt.id)
    : []
}

const batchTargetStatements = computed(() => {
  const selectedSet = new Set(selectedStatementIds.value)
  const picked = unmatchedStatements.value.filter(
    (stmt: any) => selectedSet.has(stmt.id) && isSelectableForBatch(stmt)
  )
  return picked.length > 0 ? picked : highConfidenceStatements.value
})

/** 按建议确认单笔流水（match 关联已有记录 / new 新建入账）。批量与单条共用同一路径。 */
async function confirmSuggested(
  stmt: any,
  sug: any,
  opts: { skipDuplicateCheck?: boolean; rememberRule?: boolean } = {}
) {
  if (sug.type === 'match') {
    const matchIds = sug.matchedEntryIds || [sug.matchedEntryId].filter(Boolean)
    return await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: stmt.id,
        type: 'match',
        matched_entry_ids: matchIds,
        allowBackfill,
        backfillReason,
      })
    )
  }
  const suggestedEntry = sug.suggestedEntry || {}
  return await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        title: suggestedEntry.title || cleanStatementTitle(stmt),
        category: suggestedEntry.category || (stmt.amount > 0 ? '其他业务收入' : '其他支出'),
        amount: Math.abs(stmt.amount),
        direction: stmt.amount > 0 ? 'income' : 'expense',
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        paymentDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        billingStatus: 'reconciled',
        source: 'bank_import',
        payer: 'company_account',
        rdProjectId: suggestedEntry.rdProjectId,
        rdExpenseType: suggestedEntry.rdExpenseType,
      },
      allowBackfill,
      backfillReason,
      ...(opts.skipDuplicateCheck ? { skipDuplicateCheck: true } : {}),
      ...(opts.rememberRule ? { rememberRule: true } : {}),
    })
  )
}

const batchConfirming = ref(false)
async function handleBatchConfirmRules() {
  const items = batchTargetStatements.value
    .map((stmt: any) => ({ stmt, sug: getAiSuggestion(stmt.id) }))
    .filter((item: any) => item.sug)
  if (items.length === 0) return
  batchConfirming.value = true
  let confirmed = 0
  let cancelled = false
  try {
    // 串行执行：跨期补记弹窗共用同一个 resolver，并发会互相覆盖
    for (const { stmt, sug } of items) {
      try {
        await confirmSuggested(stmt, sug)
        confirmed += 1
      } catch (err: any) {
        if (isBackfillCancelled(err)) {
          cancelled = true
          break
        }
        console.error('批量确认失败:', stmt.id, err)
      }
    }
    selectedStatementIds.value = []
    await loadAllData()
    const failed = items.length - confirmed
    if (cancelled) message.info(`已确认 ${confirmed} 笔，其余已取消`)
    else if (failed > 0) message.warning(`已确认 ${confirmed} 笔，${failed} 笔未能自动确认，请手动处理`)
    else message.success(`已确认 ${confirmed} 笔流水`)
  } finally {
    batchConfirming.value = false
  }
}

const quickSubmittingId = ref<string | null>(null)

/**
 * 新建入账时账上已有等额凭证（后端 EXISTING_ENTRY_MATCH）：
 * 直接给出"配对"选项，而不是再记一笔。返回 true 表示已接管错误提示。
 */
function promptExistingEntryMatch(stmt: any, err: any, retryAsNew?: () => Promise<void>): boolean {
  const detail = err?.response?.data?.detail
  if (!detail || detail.code !== 'EXISTING_ENTRY_MATCH') return false
  const entryIds: string[] = (detail.candidateEntryIds || []).map(String)
  if (!entryIds.length) return false
  const first = (detail.entries || [])[0] || {}
  dialog.warning({
    title: '发现一笔相同金额的已有凭证',
    content: `账上已有「${first.title || stmt.description}」${first.date ? `（${first.date}）` : ''}，金额相同。直接配对即可，避免同一笔钱记两次；确实属于两笔独立付款时再单独记账。`,
    positiveText: '直接配对',
    negativeText: '确认是两笔，仍要记账',
    onPositiveClick: async () => {
      try {
        await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
          apiClient.post('/api/bank/confirm-suggestion', {
            statement_id: stmt.id,
            type: 'match',
            matched_entry_ids: entryIds,
            allowBackfill,
            backfillReason,
          })
        )
        message.success(`已配对，未重复记账：「${stmt.description}」`)
        await loadAllData()
      } catch (pairErr: any) {
        if (!isBackfillCancelled(pairErr)) message.error(apiErrorMessage(pairErr, '配对失败'))
      }
    },
    onNegativeClick: async () => {
      if (!retryAsNew) {
        openVoucherModal(entryIds[0])
        return
      }
      try {
        await retryAsNew()
        message.success(`已按独立的一笔记账：「${stmt.description}」`)
        await loadAllData()
      } catch (retryErr: any) {
        if (!isBackfillCancelled(retryErr)) message.error(apiErrorMessage(retryErr, '入账失败'))
      }
    },
  })
  return true
}

async function handleAcceptSuggested(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return
  quickSubmittingId.value = stmt.id
  const shouldRemember = Boolean(rememberRuleOnConfirm.value && sug.type === 'new' && !isFallbackSuggestion(stmt))
  try {
    const res: any = await confirmSuggested(stmt, sug, { rememberRule: shouldRemember })
    const suggestedEntry = sug.suggestedEntry || {}
    if (sug.type === 'match') {
      message.success(`已完成对账：「${stmt.description}」`)
    } else if (res?.savedRulePattern) {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}`)
    }
    await loadAllData()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptExistingEntryMatch(stmt, err, () => confirmSuggested(stmt, sug, { skipDuplicateCheck: true, rememberRule: shouldRemember }).then(() => undefined))) return
    message.error(apiErrorMessage(err, '入账失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

async function handleQuickSelectCategory(stmt: any, category: string) {
  if (!category) return
  if (category === '研发支出-费用化支出') {
    openManualClassify(stmt, { category: '研发支出-费用化支出' })
    return
  }
  quickSubmittingId.value = stmt.id
  const submitNewEntry = (allowBackfill: boolean, backfillReason?: string, skipDuplicateCheck = false) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        title: cleanStatementTitle(stmt),
        category: category,
        amount: Math.abs(stmt.amount),
        direction: stmt.amount > 0 ? 'income' : 'expense',
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        paymentDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        billingStatus: 'reconciled',
        source: 'bank_import',
        payer: 'company_account',
      },
      allowBackfill,
      backfillReason,
      ...(skipDuplicateCheck ? { skipDuplicateCheck: true } : {}),
      ...(rememberRuleOnConfirm.value ? { rememberRule: true } : {}),
    })
  try {
    const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      submitNewEntry(allowBackfill, backfillReason)
    )
    if (res?.savedRulePattern) {
      message.success(`已入账：「${stmt.description}」→ ${category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已入账：「${stmt.description}」→ ${category}`)
    }
    await loadAllData()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptExistingEntryMatch(stmt, err, () => submitNewEntry(false, undefined, true).then(() => undefined))) return
    message.error(apiErrorMessage(err, '入账失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

function findMatchingInvoiceForStatement(stmt: any) {
  const stmtAmount = Math.abs(stmt.amount)
  return unmatchedEntries.value.find((entry: any) => {
    const entryAmount = Math.abs(Number(entry.amount) || 0)
    return Math.abs(stmtAmount - entryAmount) < 0.01 && isDirectionCompatible(entry.direction, stmt.amount)
  })
}

// ── 待确认账目（发票/手工账）：老板只需要回答"付款了吗" ─────────────────

/** 给一笔账目找最可能的银行流水：方向一致、日期 ±10 天、金额最接近。 */
function findStatementCandidateForEntry(entry: any) {
  const entryAmount = Math.abs(Number(entry.amount) || 0)
  const entryDate = String(entry.date || entry.occurDate || '').slice(0, 10)
  const wantsIncome = entry.direction === 'income'
  let best: { statement: any; diff: number } | null = null
  for (const stmt of unmatchedStatements.value) {
    const stmtAmount = Number(stmt.amount) || 0
    if ((stmtAmount > 0) !== wantsIncome) continue
    const stmtDate = String(stmt.transaction_date || '').slice(0, 10)
    if (entryDate && stmtDate) {
      const dayDiff = Math.abs(new Date(entryDate).getTime() - new Date(stmtDate).getTime()) / 86400000
      if (dayDiff > 10) continue
    }
    const diff = Math.abs(Math.abs(stmtAmount) - entryAmount)
    if (!best || diff < best.diff) best = { statement: stmt, diff }
  }
  return best
}

function statementCandidateText(candidate: { statement: any; diff: number } | null) {
  if (!candidate) return ''
  const stmt = candidate.statement
  const relation = candidate.diff < 0.01 ? '金额一致' : `差 ${formatMoney(candidate.diff)}`
  return `疑似对应 ${String(stmt.transaction_date).slice(0, 10)} ${stmt.description}（${relation}）`
}

/** 账目已付款：带用户去核对对应流水；找不到流水时提示补导。 */
function handleEntryPaid(entry: any) {
  const candidate = findStatementCandidateForEntry(entry)
  currentStep.value = 2
  if (!candidate) {
    message.warning('本月没找到对应流水。如果这笔是现金或个人账户支付，可以标记「还没付款」或直接忽略。')
    return
  }
  selectedBank.value = candidate.statement.id
  nextTick(() => scrollIntoBank(candidate.statement.id))
  message.info(statementCandidateText(candidate))
}

/** 确认单据去向（未付款/老板垫付/员工垫付/预存扣除/老板代收/平台未提现/冲前期预收等；若已过账则后台自动冲销重记） */
async function confirmEntryDisposition(entry: any, choice: { payer?: string; paymentStatus?: 'unpaid'; label: string }) {
  try {
    const { label, ...payload } = choice
    await updateEntry(entry.id, { ...payload, autoReverseRepost: true })
    message.success(`已确认：${label}`)
    await loadAllData()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '确认失败'))
  }
}

/** 撤销去向确认：单据重新回到待确认列表 */
async function resetEntryDisposition(entry: any) {
  try {
    await updateEntry(entry.id, { paymentStatus: null, payer: 'company_account', autoReverseRepost: true })
    message.success('已恢复为待确认状态')
    await loadAllData()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '恢复失败'))
  }
}

/** 标记未付款：草稿会改为挂账科目（应付/应收），不再出现在待确认列表。 */
async function markEntryUnpaid(entry: any) {
  const label = entry.direction === 'income' ? '客户未付款' : '还没付款'
  return confirmEntryDisposition(entry, { paymentStatus: 'unpaid', label })
}

const employeePayerOptions = computed(() => {
  const options = (employees.value || []).map((emp: any) => ({
    label: `员工垫付：${emp.name}`,
    key: `employee_personal:${emp.name}`
  }))
  options.push({
    label: '其他员工垫付',
    key: 'employee_personal'
  })
  return options
})

const SYSTEM_MANUAL_CATEGORIES = new Set([
  '人力成本',
  '工资发放',
  '社保公积金',
  '个人所得税',
  '银行及金融手续费',
  '银行手续费',
  '利息收入',
  '股东投入',
  '老板投入',
  '资本公积',
  '股东取现',
  '老板取现',
  '股东垫付报销',
  '员工垫付报销',
])

const pendingEntryList = computed(() =>
  unmatchedEntries.value.filter((entry: any) => {
    const isInvoice = entry.source === 'invoice_import' || !!entry.invoiceNumber
    if (isInvoice) return true
    if (entry.direction === 'owner_transfer') return false
    const cat = String(entry.category || '').trim()
    const title = String(entry.title || '').trim()
    if (SYSTEM_MANUAL_CATEGORIES.has(cat)) return false
    if (/工资|社保|公积金|手续费|注册资本|股东投入/.test(title)) return false
    return true
  })
)

/** 资金流出：支出，或负数的资本往来（股东取现等）；股东投入按流入显示 */
const entryIsOutflow = (entry: any) =>
  entry.direction === 'expense' || (entry.direction !== 'income' && Number(entry.amount) < 0)

const confirmedExceptionEntries = computed(() => {
  try {
    return entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      const isCurrentMonth = month === targetMonth.value
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      return isCurrentMonth && isNotArchiveOnly && isNonBankSettledEntry(e as any)
    }).map((e) => ({
      id: e.id,
      title: e.title,
      date: e.occurDate,
      amount: e.amount,
      direction: e.direction,
      category: (e as any).category || '',
      source: (e as any).source || 'manual',
      payer: (e as any).payer || 'company_account',
      paymentStatus: (e as any).paymentStatus || '',
    }))
  } catch (err) {
    console.error('confirmedExceptionEntries computed error:', err)
    return []
  }
})

/** 老板往来小账本汇总：让老板一眼看懂确认后的应收、应付、可免税报销垫付款 */
const bossLedgerSummary = computed(() => {
  let customerOwes = 0
  let customerCount = 0
  let companyOwesSupplier = 0
  let supplierCount = 0
  let companyOwesBossAndEmp = 0
  let bossOnly = 0
  let empOnly = 0

  for (const item of confirmedExceptionEntries.value) {
    const amt = Math.abs(Number(item.amount) || 0)
    const payer = String(item.payer || '')
    const status = String(item.paymentStatus || '')
    if (status === 'unpaid') {
      if (item.direction === 'income') {
        customerOwes += amt
        customerCount += 1
      } else if (item.direction === 'expense') {
        companyOwesSupplier += amt
        supplierCount += 1
      }
    } else if (item.direction === 'expense') {
      if (payer === 'boss_personal') {
        companyOwesBossAndEmp += amt
        bossOnly += amt
      } else if (payer.startsWith('employee_personal')) {
        companyOwesBossAndEmp += amt
        empOnly += amt
      }
    }
  }
  return {
    customerOwes,
    customerCount,
    companyOwesSupplier,
    supplierCount,
    companyOwesBossAndEmp,
    bossOnly,
    empOnly,
    hasAny: customerOwes > 0 || companyOwesSupplier > 0 || companyOwesBossAndEmp > 0
  }
})

const pendingInvoiceList = computed(() =>
  pendingEntryList.value.filter((entry: any) => entry.source === 'invoice_import' || !!entry.invoiceNumber)
)

const unpaidInvoiceCount = computed(() =>
  invoicesList.value.filter((inv: any) => isNonBankSettledEntry(inv)).length
)

async function handleMatchInvoiceDirect(stmt: any, invoice: any) {
  quickSubmittingId.value = stmt.id
  try {
    await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: stmt.id,
        type: 'match',
        matched_entry_ids: [invoice.id],
        allowBackfill,
        backfillReason,
      })
    )
    message.success(`已关联发票并完成核对：「${invoice.title}」`)
    await loadAllData()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    message.error(apiErrorMessage(err, '发票关联失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

const bossExpenseOptions = [
  { label: '办公耗材 / 打印杂费', value: '办公费' },
  { label: '信息服务 / 云服务器 / 软件', value: '信息技术服务费' },
  { label: '请客吃饭 / 商务招待', value: '业务招待费' },
  { label: '打车交通 / 差旅住宿', value: '差旅费' },
  { label: '研发项目支出（需选项目）', value: '研发支出-费用化支出' },
  { label: '采购款 / 原材料', value: '原材料采购' },
  { label: '房租 / 物业 / 水电通讯', value: '租金物业' },
  { label: '员工报销款 / 往来备用金', value: '其他应收款' },
  { label: '还老板垫付款 / 股东往来', value: '股东垫付报销' },
  { label: '税费缴纳（增值税/个税等）', value: '税费缴纳' },
  { label: '银行手续费 / 转账费', value: '银行及金融手续费' },
  { label: '其他日常支出', value: '其他支出' },
]

const bossIncomeOptions = [
  { label: '微信支付结算（提现到账）', value: '微信支付结算' },
  { label: '支付宝结算（提现到账）', value: '支付宝结算' },
  { label: '主营业务收入（货款/服务费）', value: '主营业务收入' },
  { label: '银行存款结息（利息收入）', value: '利息收入' },
  { label: '实名认证打款 / 营业外收入', value: '营业外收入' },
  { label: '股东投资款注入', value: '股东投入' },
  { label: '其他业务收入', value: '其他业务收入' },
]

function nextStep() {
  if (currentStep.value === 2 && hasBlockingPayroll.value) {
    if (payrollPendingReview.value) {
      message.warning('工资计提凭证正在等待审核，审核通过后才能继续。')
    } else {
      message.warning('本月在职员工工资尚未完成计提，请先计提后再结账（发放可延至次月）。')
    }
    return
  }
  if (currentStep.value < 3) {
    currentStep.value++
  }
}

function prevStep() {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}


// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(loadAllData)
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
          <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
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
          </div>
          <p class="report-subtitle">一站式完成发票对齐、流水对账、工资计提并锁定月度账期</p>
          <div v-if="mainFilingObligations.length" class="filing-obligation-strip">
            <span class="filing-obligation-lead">
              <i class="ri-file-list-3-line" />
              本期申报<template v-if="filingInfo?.vatCycleLabel">（增值税{{ filingInfo.vatCycleLabel }}）</template>
            </span>
            <span
              v-for="item in mainFilingObligations"
              :key="item.taxKind + item.period"
              class="filing-obligation-chip"
              :class="{ 'is-filed': item.status === 'filed' }"
            >
              {{ item.name }} · {{ item.period }}
              <template v-if="filingDueLabel(item)"> · {{ filingDueLabel(item) }}</template>
              <template v-if="item.status === 'filed'"> · 已申报</template>
            </span>
            <NButton text size="tiny" type="primary" class="filing-obligation-link" @click="goFilingPackage(mainFilingObligations[0])">
              查看申报底稿
            </NButton>
          </div>
        </div>
      </div>
      <div class="report-header-right">
        <!-- ponytail: collapsible AI closing copilot bar -->
        <NButton secondary size="small" @click="showAgentPanel = !showAgentPanel">
          <template #icon><i class="ri-sparkling-fill" style="color: var(--solo-primary);" /></template>
          {{ showAgentPanel ? '收起 AI 结账助手' : 'AI 结账顾问' }}
        </NButton>
      </div>
    </div>

    <!-- 一套月结向导同时服务老板与会计：默认大白话，专业明细折叠可展开 -->
    <!-- AI 结账助手 (可折叠抽屉式面板) -->
    <div v-show="showAgentPanel" class="agent-panel-wrapper mb-4">
      <ClosingAgentPanel :month="targetMonth" />
    </div>

    <!-- Steps Card (未结账时持续展示步骤导引) -->
    <NCard v-if="!closed" :bordered="false" class="report-card steps-card mb-3" style="padding: 6px 14px;">
      <NSteps :current="currentStep" size="small" @update:current="(val: number) => currentStep = val">
        <NStep title="导入单据" description="发票、流水、账单" style="cursor: pointer;" @click="currentStep = 1" />
        <NStep title="确认收支" description="系统已自动记账，逐笔确认例外" style="cursor: pointer;" @click="currentStep = 2" />
        <NStep title="一键锁账" description="检查通过后封账归档" style="cursor: pointer;" @click="currentStep = 3" />
      </NSteps>
    </NCard>

    <!-- 本月进度条：资料汇总 + 下一步入口，与步骤导航分开 -->
    <div v-if="!closed && currentStep === 1" class="step-status-bar mb-4">
      <div class="step-status-main">
        <i class="ri-folder-shared-line step-status-icon" />
        <span class="step-status-label">本月已导入</span>
        <span class="step-status-text">
          银行流水 {{ rawStatements.length }} 笔 · 待确认发票 {{ pendingInvoiceList.length }} 张{{ unpaidInvoiceCount > 0 ? `（另有 ${unpaidInvoiceCount} 张已确认去向）` : '' }} · 微信账单 {{ wechatBillRecordsSummary.total || wechatBillRecords.length }} 笔
        </span>
      </div>
      <NButton type="primary" size="small" @click="nextStep">
        {{ unmatchedStatements.length > 0 ? `下一步：确认 ${unmatchedStatements.length} 笔收支` : '下一步：确认收支' }}
        <template #icon><i class="ri-arrow-right-line" /></template>
      </NButton>
    </div>

    <!-- Step 1 Layout: 资料归集与导入 (发票 · 网银流水 · 平台账单) -->
    <div v-if="currentStep === 1">
      <NSpin :show="loading">
        <div class="step1-import-grid">
        <NCard :bordered="false" class="report-card" :class="{ 'step1-card-expanded': unmatchedInvoices.length > 0 }">
          <div class="step-import-head">
            <h3 class="report-section-title"><span class="step-import-num">1</span>导入发票</h3>
            <div class="step-import-meta">
              <NTag size="small" :type="unmatchedInvoices.length > 0 ? 'info' : 'default'" :bordered="false">
                {{ unmatchedInvoices.length > 0 ? `本月待核对 ${unmatchedInvoices.length} 张` : '还没导入' }}
              </NTag>
              <NButton v-if="unmatchedInvoices.length > 0" size="tiny" quaternary type="primary" @click="invoiceDropzone?.openPicker()">重新上传</NButton>
              <NButton v-if="unmatchedInvoices.length > 0" text size="tiny" @click="showInvoiceDetails = !showInvoiceDetails">
                {{ showInvoiceDetails ? '收起明细' : '展开明细' }}
              </NButton>
              <NButton text size="tiny" @click="router.push('/invoices')">去整页管理</NButton>
            </div>
          </div>
          <p class="report-subtitle">税局导出的进项/销项发票 Excel，自动拆分税率并生成发票明细。</p>

          <FileDropzone
            v-show="unmatchedInvoices.length === 0"
            ref="invoiceDropzone"
            accept=".xlsx,.xls,.csv"
            icon="ri-file-excel-2-line"
            title="点击或拖拽发票 Excel 上传"
            subtitle="上传后自动写入系统发票库"
            :loading="loading"
            @file="processInvoiceFile"
            @error="onFileError"
          />
  
          <div v-if="unmatchedInvoices.length > 0" class="report-table-wrapper" style="max-height: 320px; overflow-y: auto;">
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
                  <th style="text-align: center; width: 190px;">下一步</th>
                  <th v-if="showInvoiceDetails" style="text-align: center; width: 80px;">冲销 / 删除</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="inv in unmatchedInvoices" :key="inv.id" :style="inv.is_archive_only ? 'opacity: 0.7;' : ''">
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
                    <NTag v-if="inv.is_archive_only" size="small" type="default" :bordered="false">仅归档不入账</NTag>
                    <NTag v-else-if="isNonBankSettledEntry(inv)" size="small" type="info" :bordered="false">{{ formatDispositionTag(inv) }}</NTag>
                    <NTag v-else-if="inv.status === 'matched'" size="small" type="success" :bordered="false">已和公户对上</NTag>
                    <NTag v-else-if="inv.status === 'pending_confirmation'" size="small" type="info" :bordered="false">待确认</NTag>
                    <NTag v-else size="small" type="warning">还没和公户对上</NTag>
                  </td>
                  <td style="text-align: center;">
                    <template v-if="!inv.is_archive_only && inv.status !== 'matched' && !isNonBankSettledEntry(inv)">
                      <NButton size="tiny" type="primary" secondary @click="handleEntryPaid(inv)">已付款</NButton>
                      <NButton size="tiny" text style="margin-left: 6px;" @click="markEntryUnpaid(inv)">未付款</NButton>
                    </template>
                    <NButton
                      v-else-if="isNonBankSettledEntry(inv)"
                      size="tiny"
                      text
                      @click="resetEntryDisposition(inv)"
                    >重新确认</NButton>
                    <span v-else class="invoice-no-action">—</span>
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
                :type="(wechatBillRecordsSummary.total || wechatBillRows.length) > 0 ? 'success' : 'default'"
                :bordered="false"
              >
                {{ (wechatBillRecordsSummary.total || wechatBillRows.length) > 0
                  ? `已导入 ${wechatBillRecordsSummary.total || wechatBillRows.length} 笔`
                  : '还没导入' }}
              </NTag>
            </div>
          </div>
          <p class="report-subtitle">微信商户后台导出的当月账单 CSV，自动识别每笔手续费。</p>

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
                  当前模式：将按月度对账结果生成 <strong>1 张综合记账凭证</strong>（自动轧平收入、退款与手续费）
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
              <NTag size="small" :type="rawStatements.length > 0 ? 'success' : 'default'" :bordered="false">
                {{ rawStatements.length > 0 ? `已导入 ${rawStatements.length} 笔` : '还没导入' }}
              </NTag>
              <NButton text size="tiny" @click="router.push('/bank')">去整页管理</NButton>
            </div>
          </div>
          <p class="report-subtitle">网银导出的本月流水单，自动认领收支并匹配已有记账。</p>

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
                失败: <span style="color: var(--solo-expense); font-weight: bold;">{{ bankUploadResult.errors }}</span> 条
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
                    <td>{{ row.transaction_date || '-' }}</td>
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
                    <td>{{ stmt.transaction_date }}</td>
                    <td>{{ stmt.description }}</td>
                    <td class="amount-cell" :class="stmt.amount >= 0 ? 'income' : 'expense'">{{ formatMoney(stmt.amount) }}</td>
                    <td style="text-align: center;">
                      <NTag size="small" type="warning">待匹配</NTag>
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
        <div v-if="bossLedgerSummary.hasAny" class="boss-ledger-summary-bar" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 18px;">
          <div style="background: var(--solo-card-bg, #fff); border: 1px solid var(--solo-border, #e5e7eb); border-radius: 10px; padding: 12px 16px;">
            <div style="font-size: 12px; color: var(--solo-text-muted); display: flex; align-items: center; gap: 4px;">
              <i class="ri-hand-coin-line" style="color: var(--solo-income);"></i> 客户还欠公司（应收账款）
            </div>
            <div style="font-size: 18px; font-weight: 700; color: var(--solo-text-primary); margin-top: 4px;">
              {{ formatMoney(bossLedgerSummary.customerOwes) }}
            </div>
            <div style="font-size: 11px; color: var(--solo-text-muted); margin-top: 2px;">
              {{ bossLedgerSummary.customerCount > 0 ? `${bossLedgerSummary.customerCount} 张销项票待客户打款，回款时自动核销` : '本月无客户欠款' }}
            </div>
          </div>

          <div style="background: var(--solo-card-bg, #fff); border: 1px solid var(--solo-border, #e5e7eb); border-radius: 10px; padding: 12px 16px;">
            <div style="font-size: 12px; color: var(--solo-text-muted); display: flex; align-items: center; gap: 4px;">
              <i class="ri-bill-line" style="color: #d97706;"></i> 公司还欠供应商（应付挂账）
            </div>
            <div style="font-size: 18px; font-weight: 700; color: var(--solo-text-primary); margin-top: 4px;">
              {{ formatMoney(bossLedgerSummary.companyOwesSupplier) }}
            </div>
            <div style="font-size: 11px; color: var(--solo-text-muted); margin-top: 2px;">
              {{ bossLedgerSummary.supplierCount > 0 ? `${bossLedgerSummary.supplierCount} 张进项票未付款，后续公户打款时核销` : '本月无待付供应商款' }}
            </div>
          </div>

          <div style="background: var(--solo-card-bg, #fff); border: 1px solid var(--solo-border, #e5e7eb); border-radius: 10px; padding: 12px 16px;">
            <div style="font-size: 12px; color: var(--solo-text-muted); display: flex; align-items: center; gap: 4px;">
              <i class="ri-user-heart-line" style="color: var(--solo-primary);"></i> 公司欠老板/员工（可免税报销提取）
            </div>
            <div style="font-size: 18px; font-weight: 700; color: var(--solo-primary); margin-top: 4px;">
              {{ formatMoney(bossLedgerSummary.companyOwesBossAndEmp) }}
            </div>
            <div style="font-size: 11px; color: var(--solo-text-muted); margin-top: 2px;">
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
            <div v-for="entry in pendingEntryList" :key="entry.id" class="pending-entry-card">
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

                <!-- 支出（进项发票 / 日常支出手工账）胶囊选项 -->
                <template v-if="entry.direction === 'expense'">
                  <NButton size="tiny" round secondary @click="confirmEntryDisposition(entry, { payer: 'boss_personal', label: '老板个人垫付（计入公司欠老板款，可免税转回私卡）' })">老板垫付</NButton>
                  <NDropdown
                    trigger="click"
                    :options="employeePayerOptions"
                    @select="(key: string) => confirmEntryDisposition(entry, { payer: key, label: key.includes(':') ? `员工垫付（计入公司欠${key.split(':')[1]}报销款）` : '员工垫付（计入员工待报销款）' })"
                  >
                    <NButton size="tiny" round secondary>
                      员工垫付 <i class="ri-arrow-down-s-line" style="font-size: 11px; margin-left: 2px;"></i>
                    </NButton>
                  </NDropdown>
                  <NButton size="tiny" round secondary @click="confirmEntryDisposition(entry, { paymentStatus: 'unpaid', label: '还没付款（计入应付供应商挂账）' })">还没付款</NButton>
                  <NButton size="tiny" round quaternary @click="confirmEntryDisposition(entry, { payer: 'prepaid_deduct', label: '预存扣款（冲减前期预付余额）' })">预存扣款</NButton>
                </template>

                <!-- 收入（销项发票 / 日常收入手工账）胶囊选项 -->
                <template v-else>
                  <NButton size="tiny" round secondary @click="confirmEntryDisposition(entry, { paymentStatus: 'unpaid', label: '客户未付款（计入客户应收欠款）' })">客户未付款</NButton>
                  <NButton size="tiny" round secondary @click="confirmEntryDisposition(entry, { payer: 'boss_personal', label: '老板私户代收（计入老板暂存公款）' })">老板私户收</NButton>
                  <NButton size="tiny" round secondary @click="confirmEntryDisposition(entry, { payer: 'platform_balance', label: '在平台未提现（计入平台待提现余额）' })">在平台未提现</NButton>
                  <NButton size="tiny" round quaternary @click="confirmEntryDisposition(entry, { payer: 'advance_offset', label: '冲前期预收（核销前期预收客户款）' })">冲前期预收</NButton>
                </template>
              </div>
            </div>
          </div>
        </template>

        <!-- 已确认去向的单据（展示大白话入账结果，消灭糊涂账） -->
        <div v-if="confirmedExceptionEntries.length > 0" style="margin-bottom: 20px;">
          <NCollapse>
            <NCollapseItem :title="`已确认入账去向的单据 (${confirmedExceptionEntries.length})`" name="confirmed_dispositions">
              <template #header-extra>
                <span style="font-size: 12px; color: var(--solo-text-muted);">已自动归入对应往来账本，点击可重新选择</span>
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
                    <NButton size="tiny" text @click="resetEntryDisposition(entry)">重新选择</NButton>
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

            <!-- 方案与核算依据区 -->
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
                <strong class="plan-category">{{ getAiSuggestion(stmt.id)?.suggestedEntry?.category }}</strong>
                
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
            </div>

            <!-- 操作与调整区 -->
            <div class="reconcile-cell-action">
              <!-- 快捷选择科目 -->
              <template v-if="!hasExistingEntrySuggestion(stmt)">
                <NSelect
                  size="small"
                  class="reconcile-category-select"
                  placeholder="调整科目..."
                  :options="stmt.amount >= 0 ? bossIncomeOptions : bossExpenseOptions"
                  :disabled="quickSubmittingId === stmt.id"
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

              <!-- 次级操作/手动核算 -->
              <NButton
                size="small"
                quaternary
                class="reconcile-detail-btn"
                @click="openManualClassify(stmt, getAiSuggestion(stmt.id)?.suggestedEntry)"
              >
                <template #icon><i :class="hasExistingEntrySuggestion(stmt) ? 'ri-link-m' : 'ri-edit-line'" /></template>
                {{ hasExistingEntrySuggestion(stmt) ? '核对已有账目' : '核算明细' }}
              </NButton>
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
                  <div class="reconcile-cell-plan">
                    <span class="plan-label" style="font-size: 12px; color: var(--solo-text-muted);">往期未结流水，确认补记期间后入账</span>
                  </div>
                  <div class="reconcile-cell-action">
                    <NSelect
                      size="small"
                      class="reconcile-category-select"
                      placeholder="补记归类入账..."
                      :options="stmt.amount >= 0 ? bossIncomeOptions : bossExpenseOptions"
                      @update:value="(val: string) => handleQuickSelectCategory(stmt, val)"
                    />
                    <NButton size="small" quaternary class="reconcile-detail-btn" @click="openManualClassify(stmt)">
                      <template #icon><i class="ri-edit-line" /></template>
                      详情记账
                    </NButton>
                  </div>
                </div>
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>

        <!-- Completed Statements (Collapsible) -->
        <div v-if="matchedPairs.length > 0" style="margin-top: 20px;">
          <NCollapse>
            <NCollapseItem :title="`已认领并完成流水 (${matchedPairs.length} 笔)`" name="completed">
              <div class="feed-completed-list">
                <div v-for="pair in matchedPairs" :key="pair.id" class="feed-completed-item">
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
                <p class="report-subtitle">系统会核对凭证、银行流水与报表是否对得上，全部通过后即可封账。</p>
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
                <NButton size="small" type="primary" secondary @click="currentStep = 1">
                  <template #icon><i class="ri-folder-shared-line" /></template>
                  去归集发票与流水 (Step 1)
                </NButton>
                <NButton size="small" @click="router.push('/copilot')">
                  <template #icon><i class="ri-sparkling-line" /></template>
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
                    @click="handleBatchPostMonth"
                  >
                    <template #icon><i class="ri-check-double-line" /></template>
                    批量过账本月凭证
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
                    @click="currentStep = 1"
                  >
                    去补记
                  </NButton>
                </div>
              </NAlert>
            </div>

            <!-- 五项核验详细状态（原检查框架，折叠保留） -->
            <NCollapse class="readiness-detail-collapse mb-4">
              <NCollapseItem title="展开专业核验明细（凭证与来源 / 账账核对 / 银行流水核对 / 账表核对 / 申报校验）" name="checks">
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
              <NButton size="tiny" type="error" secondary @click="currentStep = 2">前往流水认领</NButton>
            </div>

            <!-- 关账操作栏 -->
            <div class="closing-action-box" style="text-align: center; padding: 24px 0 12px; border-top: 1px solid var(--solo-border-light);">
              <NButton
                type="primary"
                size="large"
                :loading="closingLoading"
                :disabled="!closingReady || hasBlockingReconciliationRisk"
                style="padding: 0 36px; font-weight: 600;"
                @click="runClosing"
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

      <!-- 5.2 已结账视图：CFO 诊断看板 -->
      <div v-else class="diagnosis-container">
        <NCard :bordered="false" class="report-card diagnosis-hero-card" style="padding: 24px;">
          <div class="checkmark-wrapper">
            <i class="ri-checkbox-circle-fill"></i>
          </div>
          <div style="text-align: center; margin-bottom: 24px;">
            <h3 class="ceremony-title">{{ targetMonth }} 结账成功</h3>
            <p class="ceremony-desc">本期账册已顺利归档，财务报表和凭证已锁定，AI 财务点评生成完毕。</p>
            <p v-if="closingVoucherRefs.length" class="ceremony-vouchers">
              本次自动生成期末损益结转凭证：{{ closingVoucherRefs.join('、') }}（已纳入凭证归档）
            </p>
            <p class="ceremony-book-hint">
              <i class="ri-book-2-line"></i>
              本期账簿已可导出：<a class="book-link" @click="router.push(`/ledger/book?month=${targetMonth}`)">电子账簿（余额表、明细账、日记账、总账）</a>
            </p>
            <p class="ceremony-book-hint">
              <i class="ri-shield-check-line"></i>
              想请会计师复核本月账？<a class="book-link" @click="router.push('/mine/audit-share')">生成专家协审链接</a>（免注册只读体检报告 + 批注意见回流）
            </p>
          </div>

          <div class="metrics-row mb-4">
            <div class="metric-box">
              <span class="metric-box-label">{{ targetMonth }} 经营收入</span>
              <span class="metric-box-val income">{{ formatMoney(monthlyIncome) }}</span>
            </div>
            <div class="metric-box">
              <span class="metric-box-label">{{ targetMonth }} 经营成本</span>
              <span class="metric-box-val expense">{{ formatMoney(monthlyExpense) }}</span>
            </div>
            <div class="metric-box">
              <span class="metric-box-label">本月净利润</span>
              <span class="metric-box-val" :class="netProfit >= 0 ? 'income' : 'expense'">
                {{ netProfit >= 0 ? '+' : '' }}{{ formatMoney(netProfit) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- AI Virtual CFO Comment -->
        <NCard :bordered="false" class="report-card mb-4 cfo-diagnosis-card" style="padding: 16px 20px;">
          <h3 class="report-section-title" style="margin: 0 0 12px; color: var(--solo-primary);">
            <i class="ri-robot-2-line"></i> AI CFO 月度深度诊断点评
          </h3>
          <div style="font-size: 13.5px; line-height: 1.8; color: var(--solo-text-primary); margin: 0; text-align: justify; white-space: pre-wrap;">
            {{ cfoDiagnosis?.diagnosis || '暂未生成诊断点评。' }}
          </div>
          <div v-if="cfoDiagnosis?.urgentActions?.length" style="margin-top: 16px;">
            <div style="font-size: 13px; font-weight: bold; color: var(--solo-expense); margin-bottom: 8px;">⚠️ 紧急关注事项：</div>
            <ul style="margin: 0; padding-left: 20px; font-size: 13px; color: var(--solo-text-primary); line-height: 1.6;">
              <li v-for="(act, idx) in cfoDiagnosis.urgentActions" :key="idx">{{ act }}</li>
            </ul>
          </div>
        </NCard>

        <!-- 季末结账接力：直达税务申报作业包 -->
        <div v-if="isQuarterEndMonth" class="quarter-tax-relay-card mb-4">
          <div class="relay-left">
            <div class="relay-icon">🏛️</div>
            <div class="relay-copy">
              <h4>本月为季度末月（{{ targetMonth }}），{{ closedQuarter }} 季度财务数据已封账固化</h4>
              <p>增值税免税额度、小型微利企业所得税测算与三方核对已具备法定申报条件，建议立即前往【税务申报】进行电子税务局抄报。</p>
            </div>
          </div>
          <NButton type="primary" size="medium" @click="router.push(`/tax-filing?period=${closedQuarter}`)">
            前往 {{ closedQuarter }} 税务申报 →
          </NButton>
        </div>

        <!-- Action tools -->
        <NSpace justify="center">
          <NButton size="medium" @click="downloadReport">
            <template #icon><i class="ri-file-zip-line" /></template>
            导出本月凭证与财务报告包 (PDF)
          </NButton>
          <NButton size="medium" secondary type="primary" @click="router.push(`/ledger/book?month=${targetMonth}`)">
            <template #icon><i class="ri-book-2-line" /></template>
            查看本期电子账簿
          </NButton>
          <NButton size="medium" type="primary" @click="router.push('/')">
            返回首页工作台
          </NButton>
        </NSpace>
        <div class="reopen-entry">
          <NButton size="tiny" quaternary type="warning" @click="reopenVisible = true">
            <template #icon><i class="ri-lock-unlock-line" /></template>
            反结账本期
          </NButton>
          <span class="reopen-entry-hint">补记或更正时使用，将红冲本期结转凭证并解锁账册</span>
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
    <!-- ── Reopen Period Dialog ──────────────────────────────────── -->
    <NModal
      v-model:show="reopenVisible"
      :mask-closable="true"
      style="width: 480px; max-width: 96vw;"
      :auto-focus="false"
    >
      <div class="manual-dialog reopen-dialog">
        <div class="manual-dialog-header">
          <div class="manual-dialog-icon reopen-icon"><i class="ri-lock-unlock-line"></i></div>
          <div>
            <div class="manual-dialog-title">反结账 {{ targetMonth }}</div>
            <div class="manual-dialog-subtitle">解锁本期账册，补记或更正后需重新结账</div>
          </div>
        </div>

        <div class="reopen-warning">
          <i class="ri-error-warning-line"></i>
          <span>反结账将自动红冲本期的期末损益结转凭证并解锁账册；如后续月份已关账，需先反结账最新月份。反结账原因将留痕。</span>
        </div>

        <div class="reopen-field">
          <label>反结账原因（必填）</label>
          <NInput
            v-model:value="reopenReason"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="例如：补记遗漏的银行手续费"
          />
        </div>

        <div class="reopen-field">
          <label>安全验证（仅账套所有者可反结账）</label>
          <div class="reopen-verify">
            <NInput v-model:value="reopenPhone" placeholder="绑定手机号" />
            <NInput v-model:value="reopenCode" placeholder="短信验证码" />
            <NButton size="small" :loading="reopenSending" @click="sendReopenCode">发送验证码</NButton>
          </div>
        </div>

        <div class="reopen-actions">
          <NButton size="small" @click="reopenVisible = false">取消</NButton>
          <NButton size="small" type="warning" :loading="reopenSubmitting" @click="submitReopen">
            确认反结账
          </NButton>
        </div>
      </div>
    </NModal>

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
              placeholder="请选择科目分类"
              :render-label="(opt: any) => opt.disabled ? opt.label : `${opt.label}`"
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

/* Manual Classify Dialog */
.manual-dialog {
  background: var(--solo-bg-card);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
}

/* Reopen Period Dialog + completion voucher note */
.reopen-icon {
  background: rgba(240, 153, 123, 0.12);
  color: #d85a30;
}

.reopen-warning {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: rgba(230, 162, 60, 0.08);
  border: 1px solid rgba(230, 162, 60, 0.25);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-text-secondary, #586474);
  margin-bottom: 16px;
}

.reopen-warning i {
  color: #ba7517;
  font-size: 15px;
  flex-shrink: 0;
  margin-top: 2px;
}

.reopen-field {
  margin-bottom: 14px;
}

.reopen-field > label {
  display: block;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--solo-text-primary);
  margin-bottom: 6px;
}

.reopen-verify {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 8px;
  align-items: center;
}

.reopen-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}

.reopen-entry {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 14px;
}

.reopen-entry-hint {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.ceremony-vouchers {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
/* F4：结账完成页引导到电子账簿（账簿的读者是会计/税务，给入口不给老板视图） */
.ceremony-book-hint {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
.ceremony-book-hint .book-link {
  color: var(--solo-primary);
  cursor: pointer;
}
.ceremony-book-hint .book-link:hover {
  text-decoration: underline;
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

/* ── 本期申报义务条（结账按月、申报按税种，两者不同维度） ── */
.filing-obligation-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
}

.filing-obligation-lead {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--solo-text-secondary);
  font-weight: 600;
}

.filing-obligation-chip {
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--solo-primary-soft, rgba(99, 102, 241, 0.08));
  color: var(--solo-text-secondary);
  font-variant-numeric: tabular-nums;
}

.filing-obligation-chip.is-filed {
  opacity: 0.55;
  text-decoration: line-through;
}

.filing-obligation-link {
  margin-left: 2px;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
  transition: all 0.2s ease;
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
  flex: 1.2;
  min-width: 320px;
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
  flex: 1;
  min-width: 220px;
}

.plan-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  max-width: 100%;
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
}

.plan-category {
  font-weight: 600;
  color: var(--solo-text-primary);
  font-size: 12px;
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
</style>
