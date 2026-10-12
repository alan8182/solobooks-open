<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NDivider,
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
  useMessage,
} from 'naive-ui'
import { formatMoney } from '@/utils/format'
import {
  amountClassForDirection,
  buildSuggestedMatchPair,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  groupedEntryTotal,
  isReconciledEntry,
  reconciliationDifference,
  signedInvoiceAmount,
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
import { getClosingReadiness, canBatchPostDrafts, deriveClosingTasks, type ClosingReadinessItem, type ClosingTaskAction } from '@/utils/closingReadiness'
import { postEntry, getPostingOptions, batchPostEntries } from '@/api/accounting'
import { normalizeVoucherLines, generateVoucherLines, type VoucherLine } from '@/utils/accounting'
import ClosingAgentPanel from '@/components/business/ClosingAgentPanel.vue'
import * as XLSX from 'xlsx'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const { confirmDelete } = useDeleteConfirm()

const currentStep = ref(1)
const loading = ref(false)
const periodCloseChecks = ref<any | null>(null)
// ponytail: unified 5-step wizard with built-in readiness checks, replacing the dual-track showDetailWorkflow toggle
const showAgentPanel = ref(false)

// Target Month options: last 6 months dynamically
const now = new Date()
const currentYear = now.getFullYear()
const currentMonth = now.getMonth() // 0-11
const monthOptions = Array.from({ length: 6 }).map((_, i) => {
  const d = new Date(currentYear, currentMonth - i, 1)
  const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  return { label: `${d.getFullYear()}年${d.getMonth() + 1}月`, value: val }
})
const defaultPrevMonth = new Date(currentYear, currentMonth - 1, 1)
const targetMonth = ref(`${defaultPrevMonth.getFullYear()}-${String(defaultPrevMonth.getMonth() + 1).padStart(2, '0')}`)
// 待办/关账任务跳入时按 query.month 定位月份（YYYY-MM），step/statement 见 loadAllData
const queryMonth = String(router.currentRoute?.value?.query?.month || '')
if (/^\d{4}-\d{2}$/.test(queryMonth)) targetMonth.value = queryMonth

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

// Uploader input refs
const invoiceInput = ref<HTMLInputElement | null>(null)
const bankInput = ref<HTMLInputElement | null>(null)
const wechatBillInput = ref<HTMLInputElement | null>(null)

// WeChat bill state
const wechatBillRows = ref<any[]>([])
const wechatBillSummary = ref<Record<string, any>>({})
const wechatBillImported = ref(false)
const wechatBillImporting = ref(false)
const wechatBillRecords = ref<any[]>([])
const wechatBillRecordsSummary = ref<Record<string, any>>({})

// Step 3: Payroll data
const payrollAccrued = ref(false)
const payrollPaid = ref(false)
const employees = ref<any[]>([])
const salarySheet = ref<any>(null)

// Step 4: Matching workspace states
const selectedInvoiceIds = ref<string[]>([])
const selectedBank = ref<string | null>(null)
const matchedPairs = ref<any[]>([])
const aiSuggestions = ref<any[]>([])
const reconciliationHealth = ref<any | null>(null)
const reconciliationHealthLoading = ref(false)
const pendingMatchedPairCount = computed(() => matchedPairs.value.filter((pair) => pair.status !== 'confirmed').length)

// Step 5: Closing states
const closed = ref(false)
const closingLoading = ref(false)
const monthlyIncome = ref(0)
const monthlyExpense = ref(0)
const netProfit = ref(0)
const cfoDiagnosis = ref<any>(null)

// ponytail: explicit detection of zero-transaction periods to prevent false "ready to close" reports
const confirmZeroActivity = ref(false)

const monthEntries = computed(() =>
  entryStore.activeEntries.filter(e => e.occurDate?.startsWith(targetMonth.value))
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
const hasBlockingPayroll = computed(() =>
  employees.value.length > 0 && (!payrollAccrued.value || !payrollPaid.value)
)

const readinessItems = computed(() => getClosingReadiness(periodCloseChecks.value))
const closingTasks = computed(() => deriveClosingTasks(periodCloseChecks.value))

function runTaskAction(action: ClosingTaskAction) {
  if (action.kind === 'run-closing') {
    runClosing()
    return
  }
  if (action.kind === 'match') {
    currentStep.value = 4
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
    router.push({ path: action.route || '/vouchers', query: { month: targetMonth.value, from: `/closing-wizard?month=${targetMonth.value}&step=5` } })
    return
  }
  currentStep.value = 4
}

function scrollIntoBank(statementId: string) {
  void nextTick(() => {
    document.querySelector(`[data-statement-id="${statementId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}
function handleReadinessAction(item: ClosingReadinessItem) {
  if (item.code === 'VOUCHER_TO_SOURCE') {
    const voucherCheck = periodCloseChecks.value?.checks?.find((c: any) => c.code === 'VOUCHER_TO_SOURCE')
    const firstEntryId = voucherCheck?.records?.[0]?.entryId
    if (firstEntryId) {
      openVoucherModal(String(firstEntryId))
      return
    }
  }
  if (item.route) router.push({ path: item.route, query: { month: targetMonth.value, from: `/closing-wizard?month=${targetMonth.value}&step=5` } })
  else currentStep.value = item.step
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
  await Promise.all([loadAllData(), loadRdOptions()])
})

async function loadAllData() {
  if (suggestionsController) {
    try {
      suggestionsController.abort()
      console.log('[loadAllData] Previous pending AI suggestion safely aborted at startup')
    } catch {
      // ignore
    }
  }

  loading.value = true
  confirmZeroActivity.value = false
  console.log('[loadAllData] Started loading for month:', targetMonth.value)
  try {
    // 1. 并发加载最核心的记账 entries 与银行 statements 数据，极大缩短串行等待时间
    const [_, res, periodChecks] = await Promise.all([
      entryStore.fetchEntries(),
      apiClient.get('/api/bank/statements', { timeout: 30000 }),
      apiClient.get(`/api/periods/${targetMonth.value}/close-checks`),
    ])
    console.log('[loadAllData] Core APIs completed')
    rawStatements.value = (res as any).items || []
    periodCloseChecks.value = periodChecks as any
    
    // 2. 账期状态只以后端为准
    closed.value = (periodChecks as any).periodStatus === 'closed'
    console.log('[loadAllData] locked check completed, closed:', closed.value)
    if (closed.value) {
      currentStep.value = 5
    } else if (router.currentRoute?.value?.query?.step) {
      const s = parseInt(String(router.currentRoute.value.query.step), 10)
      if (s >= 1 && s <= 5) currentStep.value = s
    } else if (closingReady.value && currentStep.value === 1) {
      currentStep.value = 5
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
    console.log('[loadAllData] filterUnmatchedData completed')
  } catch (err: any) {
    console.error('[loadAllData] Core error occurred:', err)
    message.error(`加载核心账目数据失败: ${err.message || err}`)
  } finally {
    // 4. 核心数据已加载或报错后，立即关闭大 Loading，让 UI 保持流畅
    loading.value = false
    console.log('[loadAllData] finally block executed, loading set to false')
  }

  // 5. 辅助与 AI 接口转为后台异步并行加载，防止慢接口超时阻断整个 UI 正常渲染
  console.log('[loadAllData] Launching auxiliary tasks...')
  loadReconciliationHealth()
  loadSalarySheet()
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
    const isInMonth = e.occurDate?.startsWith(targetMonth.value)
    return isInvoice && isInMonth
  }).map(e => ({
    id: e.id,
    title: e.title,
    date: e.occurDate,
    amount: e.amount,
    direction: e.direction,
    billingStatus: (e as any).billingStatus,
    voucherStatus: (e as any).voucherStatus || '',
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

async function handleMarkBossPayer(id: string) {
  loading.value = true
  try {
    await apiClient.patch(`/api/record/entries/${id}`, { payer: 'boss_personal' })
    message.success('已转为【老板个人垫付】（贷方记入其他应付款，不影响银行账实核对）')
    await loadAllData()
  } catch (err) {
    message.error('转为老板垫付失败')
  } finally {
    loading.value = false
  }
}

function isMonthEndTransit(entry: any): boolean {
  if (entry.source !== 'wechat_bill') return false
  if (!entry.date) return false
  const day = parseInt(entry.date.slice(8, 10), 10)
  return day >= 28
}

// Invoices file select trigger
function triggerInvoiceSelect() {
  invoiceInput.value?.click()
}

// Bank statements file select trigger
function triggerBankSelect() {
  bankInput.value?.click()
}

// WeChat bill file select trigger
function triggerWechatBillSelect() {
  wechatBillInput.value?.click()
}

// Parse WeChat bill CSV (preview only, not yet imported)
async function handleWechatBillUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  const file = input.files[0]
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
    message.success(`已解析 ${res.valid.length} 条交易记录，请确认后点击导入`)
  } catch (e) {
    message.error('微信账单文件解析失败，请确认为微信商户后台导出的 CSV 格式')
  } finally {
    loading.value = false
    input.value = ''
  }
}

// Remove a single row from wechat bill preview
function removeWechatRow(index: number) {
  wechatBillRows.value.splice(index, 1)
}

// Confirm import of previewed wechat bill rows
async function confirmWechatBillImport() {
  const importableRows = wechatBillRows.value.filter((row) => !row.is_duplicate)
  if (wechatBillImporting.value || importableRows.length === 0) return
  loading.value = true
  wechatBillImporting.value = true
  try {
    const res: any = await importWechatBill(importableRows)
    wechatBillImported.value = true
    wechatBillRows.value = []
    let msg = `微信账单导入完成：${res.imported} 条交易已入账。`
    if (res.ignored_duplicates > 0) {
      msg += `（已过滤 ${res.ignored_duplicates} 条已存在交易）`
    }
    if (res.failed > 0) {
      msg += `，${res.failed} 条失败`
    }
    if (res.imported > 0) {
      message.success(msg)
    } else {
      message.warning(msg)
    }
    await loadAllData()
  } catch (e) {
    message.error('导入失败，请重试')
  } finally {
    wechatBillImporting.value = false
    loading.value = false
  }
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
async function handleInvoiceUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  const file = input.files[0]
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
        category: r.category || '其他支出'
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
  input.value = ''
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
async function handleBankUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  const file = input.files[0]
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
      
      for (const col of columns) {
        const lc = col.toLowerCase()
        // Match date but exclude time
        if (/交易日期|记账日期|入账日期|交易日|起息日期|日期|date/i.test(lc) || (/交易时间|时间/.test(lc) && !/对方/.test(lc))) {
          map[col] = 'transaction_date'
        } else if (/账户余额|余额|balance/i.test(lc)) {
          map[col] = 'balance'
        } else if (/支出金额|支出|借方|支取|付款/.test(lc) && !/商户支出/.test(lc)) {
          expenseCol = col
        } else if (/收入金额|收入|贷方|存入|收款/.test(lc) && !/商户收入/.test(lc)) {
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
  input.value = ''
}

// Fetch Salary sheet
async function loadSalarySheet() {
  console.log('[loadSalarySheet] Started for month:', targetMonth.value)
  try {
    const res: any = await apiClient.get(`/api/salary/sheets/${targetMonth.value}`)
    console.log('[loadSalarySheet] API returned successfully, records count:', res.records?.length)
    salarySheet.value = res.sheet
    employees.value = (res.records || []).map((r: any) => ({
      name: r.employee_name,
      dept: r.department,
      base: r.base_salary,
      bonus: r.bonus,
      social: r.company_social_security,
      net: r.net_salary,
      cost: r.total_cost
    }))
    console.log('[loadSalarySheet] records mapping completed')
    
    if (res.sheet) {
      payrollAccrued.value = res.sheet.status === 'accrued' || res.sheet.status === 'paid'
      payrollPaid.value = res.sheet.status === 'paid'
    }
  } catch (err: any) {
    console.error('[loadSalarySheet] Error occurred:', err)
    employees.value = []
    payrollAccrued.value = false
    payrollPaid.value = false
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
      console.log('[loadAiSuggestions] Request canceled safely')
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
function selectInvoiceCard(id: string) {
  selectedInvoiceIds.value = selectedInvoiceIds.value.includes(id)
    ? selectedInvoiceIds.value.filter((entryId) => entryId !== id)
    : [...selectedInvoiceIds.value, id]
}

function selectBankCard(id: string) {
  if (selectedBank.value === id) {
    selectedBank.value = null
  } else {
    selectedBank.value = id
  }
}

function clearManualSelection() {
  selectedInvoiceIds.value = []
  selectedBank.value = null
}

// Manual match trigger
async function handleManualMatch() {
  if (selectedInvoiceIds.value.length === 0 || !selectedBank.value) return
  const entries = selectedInvoiceIds.value
    .map((id) => allUnmatchedEntries.value.find((entry) => entry.id === id))
    .filter(Boolean) as any[]
  const b = bankStatements.value.find(s => s.id === selectedBank.value)
  
  if (entries.length === selectedInvoiceIds.value.length && b) {
    const entriesTotal = groupedEntryTotal(entries)
    const difference = reconciliationDifference(b.amount, entries)
    if (Math.abs(difference) > 0.01) {
      message.warning(`所选 ${entries.length} 笔账目净额为 ${formatMoney(entriesTotal)}，与流水相差 ${formatMoney(difference)}。`)
      return
    }
    if ((b.amount > 0 && entriesTotal < 0) || (b.amount < 0 && entriesTotal > 0)) {
      message.warning('所选账目净额方向与银行流水方向不一致，请核对收入、支出和手续费。')
      return
    }
    
    loading.value = true
    try {
      const result: any = await apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: b.id,
        type: 'match',
        matched_entry_ids: selectedInvoiceIds.value,
      })
      
      message.success(result?.settlementStatus && result.settlementStatus !== 'posted'
        ? '已关联原收款与手续费，结算凭证已生成（结账时将自动过账）'
        : entries.length > 1 ? `已将 ${entries.length} 笔账目合并匹配到该流水` : '手动对账匹配成功')
      clearManualSelection()
      await loadAllData()
    } catch (err: any) {
      message.error(apiErrorMessage(err, '手动对账失败'))
    } finally {
      loading.value = false
    }
  }
}

// Confirm AI Match pair
async function confirmPair(id: string) {
  const pair = matchedPairs.value.find(p => p.id === id)
  if (pair) {
    if (!pair.matchedEntryId) {
      message.warning('该建议缺少可匹配的账目记录')
      return
    }
    if (pairNeedsCategoryReview(pair)) {
      message.warning('请先确认并补齐该账目的科目分类')
      openPairCategoryDialog(pair)
      return
    }
    if (!pair.persisted && (pair.entries?.length || 1) === 1 && !isDirectionCompatible(pair.invoice?.direction, safeFloat(pair.bank?.amount))) {
      message.warning('该候选的流水方向与账目方向不一致，请拆开后重新分类或手动匹配')
      return
    }
    try {
      const res: any = await apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: pair.statementId,
        type: 'match',
        matched_entry_id: pair.matchedEntryId,
        matched_entry_ids: pair.matchedEntryIds || [],
      })
      pair.status = 'confirmed'
      message.success(res?.settlementStatus && res.settlementStatus !== 'posted'
        ? '已关联原收款与手续费，结算凭证已生成（结账时将自动过账）'
        : res?.status === 'already_matched' ? '该流水原已关联，已补充确认对账' : `已核对并确认：${pair.invoice.title}`)
      await loadAllData()
    } catch (err: any) {
      message.error(apiErrorMessage(err, '确认对账失败'))
    }
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
// AI suggest entry classification (without matched invoice)
async function handleAutoPostStatement(id: string) {
  const b = bankStatements.value.find(s => s.id === id)
  if (!b || aiDraftLoadingStatementId.value) return

  const sug = getAiSuggestion(id)
  if (sug?.type === 'match' || sug?.type === 'existing') {
    openManualClassify(b)
    return
  }
  if (sug?.suggestedEntry) {
    openManualClassify(b, sug.suggestedEntry)
    message.info('已生成 AI 分类草稿，请核对后再确认记账并对账。')
    return
  }

  aiDraftLoadingStatementId.value = id
  try {
    const res: any = await apiClient.post('/api/bank/classify-statement', { statement_id: id }, { timeout: 60000 })
    if (res?.suggestedEntry) {
      openManualClassify(b, res.suggestedEntry)
      message.success('已生成 AI 分类草稿，请补充说明或修改分类后确认。')
    } else {
      openManualClassify(b)
      message.warning('未能获取 AI 分类建议，请手动选择分类。')
    }
  } catch (err: any) {
    console.error('[AI Classify Error Details]:', err)
    openManualClassify(b)
    message.error(`获取 AI 分类建议失败: ${err.message || err}`)
  } finally {
    aiDraftLoadingStatementId.value = null
  }
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
    currentStep.value = 3
    message.warning('本月在职员工薪酬尚未完成计提或发放确认，请先处理后再结账。')
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
      currentStep.value = 4
      message.warning('仍有高风险对账问题，请先修复后再结账。')
      return
    }

    const checks: any = await apiClient.get(`/api/periods/${targetMonth.value}/close-checks`)
    periodCloseChecks.value = checks
    if (!checks.canClose && !canBatchPostAndClose.value) {
      const blocking = getClosingReadiness(checks).filter(item => item.status === 'blocked')
      currentStep.value = 5
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
    currentStep.value = 5
    await entryStore.fetchEntries()
    await fetchClosedDiagnosis()
    const postedCount = Number(result.autoPostedCount || 0)
    message.success(postedCount
      ? `🎉 已批量过账 ${postedCount} 张凭证并完成 ${targetMonth.value} 结账。`
      : `🎉 ${targetMonth.value} 结账成功！本期账册已锁定。`)
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    if (detail?.code === 'CLOSE_CHECK_FAILED' && detail.context?.checks) {
      periodCloseChecks.value = { ...periodCloseChecks.value, checks: detail.context.checks, canClose: false }
      message.error('本月核验仍有阻断项；本次自动过账已一并回滚，请按检查结果处理后重试。')
    } else if (detail?.code === 'REVIEW_REQUIRED') {
      message.error('本月有凭证需要其他复核人审批，本次过账和结账均未完成。')
    } else {
      message.error(typeof detail === 'string' ? detail : detail?.message || '结账失败，请检查阻断项')
    }
  } finally {
    closingLoading.value = false
  }
}
function downloadReport() {
  message.success('月度财务报销报告及凭证归档包 PDF 生成成功，正在下载...')
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
      // ponytail: 个人垫付（老板/员工）不通过公司对公账户扣款，无需且无法匹配银行对公流水
      const isPaidPersonally = (e as any).payer === 'boss_personal' || (e as any).payer?.startsWith?.('employee_personal')
      const isReversal = (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal'
      const isReversedOriginal = reversalIds.has(String(e.id))
      return isBeforeOrInMonth && isNotMatched && isNotArchiveOnly && isNotReconciled && !isPaidPersonally && !isReversal && !isReversedOriginal
    }).map(e => ({
      id: e.id,
      title: e.title,
      date: e.occurDate,
      month: e.occurDate ? e.occurDate.substring(0, 7) : '',
      amount: e.amount,
      direction: e.direction,
      source: (e as any).source || 'manual',
      invoiceNumber: (e as any).invoiceNumber || '',
      voucherLines: (e as any).voucherLines || null,
      payer: (e as any).payer || 'company_account',
      is_archive_only: (e as any).is_archive_only || (e as any).isArchiveOnly || false,
      status: 'unmatched'
    })))
  } catch (err) {
    console.error('allUnmatchedEntries computed error:', err)
    return []
  }
})
const selectedEntriesForMatch = computed(() => selectedInvoiceIds.value
  .map((id) => allUnmatchedEntries.value.find((entry) => entry.id === id))
  .filter(Boolean) as any[])
const selectedBankForMatch = computed(() => bankStatements.value.find((statement) => statement.id === selectedBank.value) || null)
const selectedEntriesTotal = computed(() => groupedEntryTotal(selectedEntriesForMatch.value))
const selectedMatchDifference = computed(() => selectedBankForMatch.value
  ? reconciliationDifference(selectedBankForMatch.value.amount, selectedEntriesForMatch.value)
  : 0)
// 全抵消组合（如原记录+冲销对）净额为 0，对账无意义，明确禁止
const isZeroNetSelection = computed(() => (
  selectedEntriesForMatch.value.length > 0
  && Math.abs(selectedEntriesTotal.value) <= 0.005
))
const canConfirmManualMatch = computed(() => (
  selectedEntriesForMatch.value.length > 0
  && !!selectedBankForMatch.value
  && !isZeroNetSelection.value
  && Math.abs(selectedMatchDifference.value) <= 0.01
))

// ponytail: highlight items that exactly reconcile with current selection to cut cognitive search time
function isCandidateEntry(entry: any): boolean {
  if (!selectedBankForMatch.value) return false
  if (selectedInvoiceIds.value.includes(entry.id)) return false
  if (selectedInvoiceIds.value.length === 0) {
    const amt = signedInvoiceAmount(entry)
    return Math.abs(amt - selectedBankForMatch.value.amount) < 0.01
  }
  const candidateList = [...selectedEntriesForMatch.value, entry]
  const diff = reconciliationDifference(selectedBankForMatch.value.amount, candidateList)
  return Math.abs(diff) < 0.01
}

function isCandidateBank(stmt: any): boolean {
  if (selectedBank.value === stmt.id) return false
  if (!selectedEntriesForMatch.value.length) return false
  const diff = reconciliationDifference(stmt.amount, selectedEntriesForMatch.value)
  return Math.abs(diff) < 0.01
}

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
// ponytail: 语义分组——发票条目的"配对"是付款状态确认（票等钱），与账目核销
// （钱对钱）语义不同，分组展示避免概念混淆
const unmatchedInvoiceEntries = computed(() =>
  unmatchedEntries.value.filter(e => (e as any).source === 'invoice_import'))
const unmatchedLedgerEntries = computed(() =>
  unmatchedEntries.value.filter(e => (e as any).source !== 'invoice_import'))
const historicalUnmatchedEntries = computed(() => {
  try {
    return allUnmatchedEntries.value.filter(e => {
      const month = getEntryMonth(e)
      return month && month < targetMonth.value
    })
  } catch (err) {
    console.error('historicalUnmatchedEntries computed error:', err)
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



function healthIssueCount(key: string) {
  try {
    return reconciliationHealth.value?.[key]?.length || 0
  } catch {
    return 0
  }
}

const differenceSuggestionCount = computed(() => {
  try {
    return aiSuggestions.value.filter((s: any) => s.type === 'difference').length
  } catch {
    return 0
  }
})
const reconciliationHealthSummary = computed(() => {
  try {
    return reconciliationHealth.value?.summary || { highRiskCount: 0, warningCount: 0 }
  } catch {
    return { highRiskCount: 0, warningCount: 0 }
  }
})
const reconciliationWarningCount = computed(() => {
  try {
    return reconciliationHealthSummary.value.warningCount + differenceSuggestionCount.value
  } catch {
    return 0
  }
})
const hasBlockingReconciliationRisk = computed(() => {
  try {
    return reconciliationHealthSummary.value.highRiskCount > 0
  } catch {
    return false
  }
})
const reconciliationRiskItems = computed(() => {
  try {
    return [
      { key: 'broken_links', label: '断链流水', count: healthIssueCount('broken_links'), type: 'error' },
      { key: 'amount_mismatches', label: '金额不一致', count: healthIssueCount('amount_mismatches'), type: 'error' },
      { key: 'direction_mismatches', label: '方向不一致', count: healthIssueCount('direction_mismatches'), type: 'error' },
      { key: 'duplicate_entry_matches', label: '重复引用', count: healthIssueCount('duplicate_entry_matches'), type: 'error' },
      { key: 'pending_confirmations', label: '待补确认', count: healthIssueCount('pending_confirmations'), type: 'warning' },
      { key: 'difference_suggestions', label: '差异候选', count: differenceSuggestionCount.value, type: 'warning' },
    ].filter(item => item.count > 0)
  } catch (err) {
    console.error('reconciliationRiskItems computed error:', err)
    return []
  }
})
// Get AI suggestion for a statement
function getAiSuggestion(stmtId: string) {
  return aiSuggestions.value.find(s => s.statementId === stmtId)
}

function findEntryById(id: string) {
  return entryStore.activeEntries.find((entry: any) => entry.id === id)
}

function pairConfidenceText(pair: any) {
  if (pair.entries?.length > 1) {
    return `合并对账 · ${pair.entries.length} 笔`
  }
  if (pair.persisted) {
    return pair.status === 'confirmed' ? '匹配状态: 已核对' : '匹配状态: 已关联待确认'
  }
  return `AI 置信度: ${pair.confidence}`
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

function pairNeedsCategoryReview(pair: any) {
  if (pair.entries?.length > 1) return false
  return !pair.invoice?.category
}

// ─── Manual Classify Dialog ──────────────────────────────────────────────────

const CATEGORY_OPTIONS = [
  { label: '── 收入 ──', value: '__header_income__', disabled: true },
  { label: '主营业务收入', value: 'income|主营业务收入|6001|主营业务收入' },
  { label: '其他业务收入', value: 'income|其他业务收入|6051|其他业务收入' },
  { label: '营业外收入', value: 'income|营业外收入|6301|营业外收入' },
  { label: '利息收入', value: 'income|利息收入|6603|财务费用-利息收入' },
  { label: '── 员工成本 ──', value: '__header_salary__', disabled: true },
  { label: '工资支出', value: 'expense|工资支出|5501|应付职工薪酬-工资' },
  { label: '社保公积金', value: 'expense|社保公积金|5502|应付职工薪酬-社保公积金' },
  { label: '── 费用支出 ──', value: '__header_expense__', disabled: true },
  { label: '差旅费', value: 'expense|差旅费|5601|管理费用-差旅费' },
  { label: '办公费', value: 'expense|办公费|5602|管理费用-办公费' },
  { label: '业务招待费', value: 'expense|业务招待费|5603|管理费用-业务招待费' },
  { label: '研发支出-费用化支出', value: 'expense|研发支出-费用化支出|5301|研发支出-费用化支出' },
  { label: '信息技术服务费', value: 'expense|信息技术服务费|5605|管理费用-信息技术服务费' },
  { label: '租金支出', value: 'expense|租金支出|5606|管理费用-租金' },
  { label: '银行及金融手续费', value: 'expense|银行及金融手续费|6602|财务费用-手续费' },
  { label: '员工报销', value: 'expense|员工报销|5699|管理费用-其他' },
  { label: '其他支出', value: 'expense|其他支出|5699|管理费用-其他' },
  { label: '── 采购与往来 ──', value: '__header_purchase__', disabled: true },
  { label: '原材料采购', value: 'expense|原材料采购|1403|原材料' },
  { label: '商品采购', value: 'expense|商品采购|1405|库存商品' },
  { label: '供应商付款', value: 'expense|供应商付款|2202|应付账款' },
  { label: '预付账款', value: 'expense|预付账款|1123|预付账款' },
  { label: '其他应收款', value: 'expense|其他应收款|1221|其他应收款-其他' },
  { label: '其他应付款', value: 'expense|其他应付款|2241|其他应付款-其他' },
  { label: '往来款-支（暂付款）', value: 'expense|往来款|1221|其他应收款-其他' },
  { label: '往来款-收（暂收款）', value: 'income|往来款|2241|其他应付款-其他' },
  { label: '税费支出', value: 'expense|税费支出|2221|应交税费' },
  { label: '── 资本 ──', value: '__header_capital__', disabled: true },
  { label: '股东投入', value: 'owner_transfer|股东投入|4001|实收资本' },
  { label: '资本公积', value: 'owner_transfer|资本公积|4002|资本公积-股本溢价' },
  { label: '── 往来 ──', value: '__header_owner__', disabled: true },
  { label: '股东垫付', value: 'owner_transfer|股东垫付|2241|其他应付款-股东' },
]

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
})
const manualSubmitting = ref(false)
const aiDraftLoadingStatementId = ref<string | null>(null)
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
    ? 'income|主营业务收入|6001|主营业务收入'
    : 'expense|其他支出|5699|管理费用-其他'
}

function cleanStatementTitle(stmt: any) {
  const desc = String(stmt.description || '')
  return desc.replace(/^\[.*?\]\s*/, '').replace(/^\(对方:\s*/, '').replace(/\)$/, '').trim() || desc || '银行流水记账'
}

function categoryValueFromSuggestion(stmt: any, suggested: any) {
  if (!suggested) return defaultCategoryValue(stmt)
  const direction = suggested.direction || (stmt.amount >= 0 ? 'income' : 'expense')
  const exact = `${direction}|${suggested.category}|${suggested.accountCode}|${suggested.accountName}`
  if (CATEGORY_OPTIONS.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${suggested.category}|`))
  return byCategory?.value || defaultCategoryValue(stmt)
}

function categoryValueFromEntry(entry: any, stmt: any) {
  if (!entry) return defaultCategoryValue(stmt)
  const exact = `${entry.direction}|${entry.category}|${entry.accountCode}|${entry.accountName}`
  if (CATEGORY_OPTIONS.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${entry.category}|`))
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
  manualForm.value = {
    title: suggestedEntry?.title || cleanStatementTitle(stmt),
    categoryValue: categoryValueFromSuggestion(stmt, suggestedEntry),
    businessContext: '',
    remark: '',
    rdProjectId: '',
    rdExpenseType: '',
    rdAllocationBasis: '',
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
    manualForm.value = {
      ...manualForm.value,
      title: suggested.title || manualForm.value.title,
      categoryValue: categoryValueFromSuggestion(manualTarget.value, suggested),
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

  const [direction, category, accountCode, accountName] = manualForm.value.categoryValue.split('|')
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

  manualSubmitting.value = true
  try {
    const confirmRes: any = await apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
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
    }, { timeout: 60000 })
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
function nextStep() {
  if (currentStep.value === 3 && employees.value.length > 0 && (!payrollAccrued.value || !payrollPaid.value)) {
    message.warning('请先完成本月的工资计提和发放确认。')
    return
  }
  if (currentStep.value < 5) {
    currentStep.value++
  }
}

function prevStep() {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}
</script>

<template>
  <div class="report-container closing-wizard-container" :class="{ 'closing-wizard--wide': currentStep === 4 || currentStep === 5 }">
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

    <!-- AI 结账助手 (可折叠抽屉式面板) -->
    <div v-show="showAgentPanel" class="agent-panel-wrapper mb-4">
      <ClosingAgentPanel :month="targetMonth" />
    </div>

    <!-- Steps Card (未结账时持续展示步骤导引) -->
    <NCard v-if="!closed" :bordered="false" class="report-card steps-card mb-4" style="padding: 14px 18px;">
      <NSteps :current="currentStep" size="small" @update:current="(val: number) => currentStep = val">
        <NStep title="凭证导入" description="发票与单据" style="cursor: pointer;" @click="currentStep = 1" />
        <NStep title="银行流水导入" description="网银明细" style="cursor: pointer;" @click="currentStep = 2" />
        <NStep title="工资发放确认" description="薪酬社保" style="cursor: pointer;" @click="currentStep = 3" />
        <NStep title="智能对账分类" description="流水配对" style="cursor: pointer;" @click="currentStep = 4" />
        <NStep title="合规检查与关账" description="体检与封账" style="cursor: pointer;" @click="currentStep = 5" />
      </NSteps>
    </NCard>

    <!-- Step 1 Layout: Invoices Upload -->
    <div v-if="currentStep === 1">
      <NSpin :show="loading">
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title"><i class="ri-file-text-line"></i> 第一步：批量导入销项/进项发票记录</h3>
          <p class="report-subtitle mb-4">请将当月所有的发票 Excel 汇总表格拖拽上传至下方，系统将自动分析金额、税率并生成待对账发票库。</p>
          
          <!-- Hidden File Input for Invoices -->
          <input
            type="file"
            ref="invoiceInput"
            style="display: none;"
            accept=".xlsx,.xls,.csv"
            @change="handleInvoiceUpload"
          />
          <button type="button" class="uploader-zone mb-4" aria-label="选择发票 Excel 文件上传" @click="triggerInvoiceSelect">
            <i class="ri-file-excel-2-line upload-icon"></i>
            <span class="upload-title">点击或选择发票 Excel 文件上传</span>
            <span class="upload-subtitle">支持从税局导出的发票明细单，上传后将自动写入系统发票库</span>
          </button>
  
          <div v-if="unmatchedInvoices.length > 0" class="report-table-wrapper" style="max-height: 320px; overflow-y: auto;">
            <table class="report-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>发票名称</th>
                  <th style="text-align: center; width: 100px;">发票类型</th>
                  <th style="text-align: right;">发票金额</th>
                  <th style="text-align: center; width: 130px;">入账模式</th>
                  <th style="text-align: center; width: 100px;">状态</th>
                  <th style="text-align: center; width: 80px;">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="inv in unmatchedInvoices" :key="inv.id" :style="inv.is_archive_only ? 'opacity: 0.7;' : ''">
                  <td>{{ inv.date }}</td>
                  <td>{{ inv.title }}</td>
                  <td style="text-align: center;">
                    <NTag v-if="inv.direction === 'income'" size="small" type="info" :bordered="false">销项发票</NTag>
                    <NTag v-else-if="inv.direction === 'expense'" size="small" type="warning" :bordered="false">进项发票</NTag>
                    <NTag v-else size="small" type="default" :bordered="false">其他发票</NTag>
                  </td>
                  <td class="amount-cell" :class="amountClassForDirection(inv.direction, inv.amount)">
                    {{ formatMoney(signedInvoiceAmount(inv)) }}
                  </td>
                  <td style="text-align: center;">
                    <NCheckbox 
                      :checked="!inv.is_archive_only" 
                      @update:checked="toggleArchiveMode(inv.id, !$event)"
                    >
                      {{ inv.is_archive_only ? '仅归档' : '参与记账' }}
                    </NCheckbox>
                  </td>
                  <td style="text-align: center;">
                    <NTag v-if="inv.is_archive_only" size="small" type="default" :bordered="false">仅归档不入账</NTag>
                    <NTag v-else-if="inv.status === 'matched'" size="small" type="success" :bordered="false">已对账</NTag>
                    <NTag v-else-if="inv.status === 'pending_confirmation'" size="small" type="info" :bordered="false">待确认</NTag>
                    <NTag v-else size="small" type="warning">待对账</NTag>
                  </td>
                  <td style="text-align: center;">
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
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title"><i class="ri-wechat-pay-line"></i> 微信支付账单导入</h3>
          <p class="report-subtitle mb-4">从微信商户后台导出当月账单 CSV，精确获取每笔手续费，无需估算。</p>

          <input
            type="file"
            ref="wechatBillInput"
            style="display: none;"
            accept=".csv"
            @change="handleWechatBillUpload"
          />

          <div v-if="!wechatBillImported && wechatBillRows.length === 0">
            <button type="button" class="uploader-zone mb-4" aria-label="选择微信支付账单 CSV 文件上传" @click="triggerWechatBillSelect">
              <i class="ri-wechat-pay-line upload-icon" style="color: #07c160;"></i>
              <span class="upload-title">点击上传微信支付账单 CSV</span>
              <span class="upload-subtitle">从微信商户后台「账单中心」导出，支持按月账单</span>
            </button>
          </div>

          <!-- Preview table -->
          <div v-if="wechatBillRows.length > 0">
            <div class="report-table-wrapper" style="max-height: 300px; overflow-y: auto;">
              <table class="report-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>商品名称</th>
                    <th style="text-align: center; width: 80px;">状态</th>
                    <th style="text-align: right;">订单金额</th>
                    <th style="text-align: right;">手续费</th>
                    <th style="text-align: right;">到账净额</th>
                    <th style="text-align: center; width: 60px;">删除</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in wechatBillRows" :key="idx" :style="row.is_duplicate ? 'opacity: 0.55; background-color: var(--card-background-hover);' : ''">
                    <td>{{ row.date }}</td>
                    <td>
                      {{ row.product_name }}
                      <NTag v-if="row.is_duplicate" size="tiny" type="error" :bordered="false" style="margin-left: 6px;">系统已存在</NTag>
                    </td>
                    <td style="text-align: center;">
                      <NTag v-if="row.status === 'SUCCESS'" size="small" type="success" :bordered="false">收款</NTag>
                      <NTag v-else size="small" type="warning" :bordered="false">退款</NTag>
                    </td>
                    <td style="text-align: right;" :style="row.status === 'REFUND' ? 'color:var(--solo-expense)' : ''">
                      {{ row.status === 'REFUND' ? '-' : '' }}{{ formatMoney(row.status === 'REFUND' ? row.refund_amount : row.order_amount) }}
                    </td>
                    <td style="text-align: right; color: var(--solo-expense);">{{ formatMoney(row.fee) }}</td>
                    <td style="text-align: right;">{{ formatMoney(row.settled_amount) }}</td>
                    <td style="text-align: center;">
                      <NButton size="tiny" type="error" ghost @click="removeWechatRow(idx)">删除</NButton>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px;">
              <span style="font-size: 13px; color: var(--text-secondary);">
                <template v-if="wechatBillRows.some(r => r.is_duplicate)">
                  将导入 {{ wechatBillRows.filter(r => !r.is_duplicate).length }} 条新记录 · 自动过滤 {{ wechatBillRows.filter(r => r.is_duplicate).length }} 条已存在记录
                </template>
                <template v-else>
                  共 {{ wechatBillRows.length }} 条 · 合计手续费 {{ formatMoney(wechatBillRows.reduce((s, r) => s + r.fee, 0)) }}
                </template>
              </span>
              <div style="display: flex; gap: 8px;">
                <NButton size="small" ghost @click="triggerWechatBillSelect">重新上传</NButton>
                <NButton
                  size="small"
                  type="primary"
                  @click="confirmWechatBillImport"
                  :loading="wechatBillImporting"
                  :disabled="wechatBillImporting || wechatBillRows.filter(r => !r.is_duplicate).length === 0"
                >
                  确认导入
                </NButton>
              </div>
            </div>
          </div>

          <!-- Imported success state -->
          <div v-if="wechatBillImported" style="text-align: center; padding: 24px 0;">
            <i class="ri-checkbox-circle-fill" style="font-size: 32px; color: #07c160;"></i>
            <p style="margin-top: 8px; color: var(--text-secondary);">微信账单已导入，收款与手续费已自动分录</p>
            <NButton size="small" ghost style="margin-top: 8px;" @click="() => { wechatBillImported = false; triggerWechatBillSelect() }">重新导入</NButton>
          </div>

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
                    <th>日期</th>
                    <th>商品名称</th>
                    <th style="text-align: center; width: 80px;">状态</th>
                    <th style="text-align: right;">订单金额</th>
                    <th style="text-align: right;">手续费</th>
                    <th style="text-align: right;">到账净额</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="rec in wechatBillRecords" :key="rec.orderNo">
                    <td>{{ rec.date }}</td>
                    <td>{{ rec.title }}</td>
                    <td style="text-align: center;">
                      <NTag :type="rec.status === 'SUCCESS' ? 'success' : 'warning'" size="small" :bordered="false">
                        {{ rec.status === 'SUCCESS' ? '收款' : '退款' }}
                      </NTag>
                    </td>
                    <td style="text-align: right;" :style="rec.orderAmount < 0 ? 'color: var(--solo-expense)' : ''">{{ formatMoney(rec.orderAmount) }}</td>
                    <td style="text-align: right;" :style="rec.fee < 0 ? 'color: var(--solo-income)' : 'color: var(--solo-expense)'">{{ formatMoney(rec.fee) }}</td>
                    <td style="text-align: right;" :style="rec.settledAmount < 0 ? 'color: var(--solo-expense)' : ''">{{ formatMoney(rec.settledAmount) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </NCard>
      </NSpin>
    </div>

    <!-- Step 2 Layout: Bank Statement Upload -->
    <div v-if="currentStep === 2">
      <NSpin :show="loading">
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title"><i class="ri-bank-card-line"></i> 第二步：导入银行交易流水单</h3>
          <p class="report-subtitle mb-4">请导入本月企业银行网银导出的交易流水对账单，用于智能对齐发票及资金账目流向。</p>
  
          <!-- Hidden File Input for Bank Statements -->
          <input
            type="file"
            ref="bankInput"
            style="display: none;"
            accept=".xlsx,.xls,.csv"
            @change="handleBankUpload"
          />
          <button type="button" class="uploader-zone mb-4" aria-label="选择网银流水文件上传" @click="triggerBankSelect">
            <i class="ri-download-cloud-2-line upload-icon"></i>
            <span class="upload-title">点击或选择网银流水文件上传</span>
            <span class="upload-subtitle">支持工行、招行、建行等各商业银行导出的 Excel/CSV 流水，上传后将自动加载</span>
          </button>
  
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
      </NSpin>
    </div>

    <!-- Step 3 Layout: Salary payroll verification -->
    <div v-if="currentStep === 3">
      <NSpin :show="loading">
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title"><i class="ri-money-cny-box-line"></i> 第三步：确认本月员工薪酬与社保发放</h3>
          <p class="report-subtitle mb-4">根据本月花名册基数生成的员工应发薪资明细，请确认无误后计提记账并核销发放。</p>

          <div v-if="employees.length === 0" style="padding: 40px; text-align: center; color: var(--solo-text-secondary); background: rgba(0,0,0,0.01); border-radius: 8px;">
            ⚠️ 本月无在职员工薪酬数据。请先在个人中心 -「薪酬社保」模块导入/添加在职员工名册。
          </div>

          <template v-else>
            <NGrid :cols="3" :x-gap="16" class="mb-4">
              <NGridItem>
                <div class="report-card" style="padding: 16px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                  <div style="font-size: 12px; color: var(--solo-text-muted);">本月应发人数</div>
                  <div style="font-size: 20px; font-weight: bold; color: var(--solo-text-primary); margin-top: 4px;">{{ employees.length }} 人</div>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="report-card" style="padding: 16px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                  <div style="font-size: 12px; color: var(--solo-text-muted);">本月应发薪酬</div>
                  <div style="font-size: 20px; font-weight: bold; color: var(--solo-income); margin-top: 4px;">{{ formatMoney(totalNetSalary) }}</div>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="report-card" style="padding: 16px; background: rgba(99, 102, 241, 0.02); text-align: center;">
                  <div style="font-size: 12px; color: var(--solo-text-muted);">企业总人工成本</div>
                  <div style="font-size: 20px; font-weight: bold; color: var(--solo-expense); margin-top: 4px;">{{ formatMoney(totalCompanyCost) }}</div>
                </div>
              </NGridItem>
            </NGrid>

            <div class="report-table-wrapper mb-4">
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

            <NSpace justify="end">
              <NButton type="warning" :disabled="payrollAccrued" @click="handleAccruePayroll">
                {{ payrollAccrued ? '✓ 已经计提记账' : '一键计提记账' }}
              </NButton>
              <NButton type="primary" :disabled="!payrollAccrued || payrollPaid" @click="handlePayPayroll">
                {{ payrollPaid ? '✓ 已经确认发放' : '确认发放记账' }}
              </NButton>
            </NSpace>
          </template>
        </NCard>
      </NSpin>
    </div>

    <div v-if="currentStep === 4">
      <NCard :bordered="false" class="report-card mb-4" style="padding: 16px 20px;">
        <div class="page-header" style="margin-bottom: 12px !important;">
          <div class="header-left">
            <h3 class="report-section-title" style="margin: 0;"><i class="ri-sparkling-fill" style="color: var(--solo-primary);"></i> 第四步：AI 辅助流水对账与分类</h3>
            <p class="report-subtitle">左右两栏分别展示未匹配的条目与银行流水。AI 只生成候选建议，确认后才会完成对账或记账。</p>
          </div>
          <div class="header-right">
            <NTag size="small" type="warning" :bordered="false">AI 建议需逐条确认</NTag>
          </div>
        </div>

        <!-- Reconciliation Completeness Summary Bar -->
        <div class="recon-summary-bar" style="margin-bottom: 16px; padding: 14px 16px; border-radius: 10px; background: rgba(99, 102, 241, 0.04); border: 1px solid rgba(99,102,241,0.12); display: flex; gap: 20px; align-items: center; flex-wrap: wrap;">
          <div style="display: flex; align-items: center; gap: 12px; flex: 1; min-width: 220px;">
            <div style="position: relative; width: 48px; height: 48px;">
              <svg width="48" height="48" viewBox="0 0 48 48" style="transform: rotate(-90deg);">
                <circle cx="24" cy="24" r="19" fill="none" stroke="rgba(99,102,241,0.12)" stroke-width="6"/>
                <circle cx="24" cy="24" r="19" fill="none" stroke="var(--solo-primary)" stroke-width="6"
                  :stroke-dasharray="`${reconciliationStats.rate * 1.194} 119.4`"
                  stroke-linecap="round" style="transition: stroke-dasharray 0.6s ease;"/>
              </svg>
              <div style="position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; color: var(--solo-primary);">{{ reconciliationStats.rate }}%</div>
            </div>
            <div>
              <div style="font-size: 13px; font-weight: 600; color: var(--solo-text-primary);">对账完整率</div>
              <div style="font-size: 12px; color: var(--solo-text-muted); margin-top: 2px;">{{ reconciliationStats.matched }}/{{ reconciliationStats.total }} 条流水已匹配</div>
            </div>
          </div>
          <div style="display: flex; gap: 16px; flex-wrap: wrap;">
            <div style="text-align: center; padding: 6px 14px; background: rgba(16,185,129,0.07); border-radius: 8px; border: 1px solid rgba(16,185,129,0.15);">
              <div style="font-size: 18px; font-weight: 700; color: var(--solo-income);">{{ reconciliationStats.unmatchedIncome.length }}</div>
              <div style="font-size: 11px; color: var(--solo-text-muted);">未匹配收款</div>
            </div>
            <div style="text-align: center; padding: 6px 14px; background: rgba(239,68,68,0.07); border-radius: 8px; border: 1px solid rgba(239,68,68,0.15);">
              <div style="font-size: 18px; font-weight: 700; color: var(--solo-expense);">{{ reconciliationStats.unmatchedExpense.length }}</div>
              <div style="font-size: 11px; color: var(--solo-text-muted);">未匹配付款</div>
            </div>
            <div style="text-align: center; padding: 6px 14px; background: rgba(99,102,241,0.07); border-radius: 8px; border: 1px solid rgba(99,102,241,0.15);">
              <div style="font-size: 18px; font-weight: 700; color: var(--solo-primary);">{{ pendingMatchedPairCount }}</div>
              <div style="font-size: 11px; color: var(--solo-text-muted);">待确认配对</div>
            </div>
          </div>
        </div>


        <div class="recon-health-panel" :class="{ danger: hasBlockingReconciliationRisk }">
          <div class="recon-health-lead">
            <i :class="hasBlockingReconciliationRisk ? 'ri-error-warning-line' : reconciliationWarningCount > 0 ? 'ri-alert-line' : 'ri-shield-check-line'"></i>
            <div>
              <div class="recon-health-title">
                {{ hasBlockingReconciliationRisk ? '发现高风险对账问题' : reconciliationWarningCount > 0 ? '存在待复核事项' : '对账健康检查通过' }}
              </div>
              <div class="recon-health-desc">
                高风险 {{ reconciliationHealthSummary.highRiskCount }} 项，警告 {{ reconciliationWarningCount }} 项
              </div>
            </div>
          </div>
          <div class="recon-health-tags">
            <NTag
              v-for="item in reconciliationRiskItems"
              :key="item.key"
              size="small"
              :type="item.type === 'error' ? 'error' : 'warning'"
              :bordered="false"
            >
              {{ item.label }} {{ item.count }}
            </NTag>
            <NTag v-if="reconciliationHealthLoading" size="small" type="info" :bordered="false">检查中</NTag>
            <NTag v-if="!reconciliationHealthLoading && reconciliationRiskItems.length === 0" size="small" type="success" :bordered="false">可进入结账</NTag>
          </div>
        </div>
        <!-- Matching canvas -->
        <div class="canvas-layout">
          <!-- Left: Unmatched Entries -->
          <div class="canvas-column">
            <div class="column-header">
              <i class="ri-file-text-line"></i>
              <span>本月待处理账目与发票 ({{ unmatchedEntries.length }})</span>
              <span class="column-header-hint">可多选</span>
            </div>
            <div v-if="unmatchedEntries.length === 0" style="padding: 28px 0; text-align: center; color: var(--solo-text-muted); font-size: 12px;">
              本月条目已对账完成
            </div>
            <!-- 待核销账目：与银行流水核销（钱对钱） -->
            <div v-if="unmatchedLedgerEntries.length > 0" class="entry-group-title">
              待核销账目（与流水核对）<span>({{ unmatchedLedgerEntries.length }})</span>
            </div>
            <div
              v-for="inv in unmatchedLedgerEntries"
              :key="inv.id"
              class="canvas-card"
              :class="{ selected: selectedInvoiceIds.includes(inv.id), 'candidate-match': isCandidateEntry(inv) }"
              role="checkbox"
              :aria-checked="selectedInvoiceIds.includes(inv.id)"
              tabindex="0"
              @click="selectInvoiceCard(inv.id)"
              @keydown.enter.prevent="selectInvoiceCard(inv.id)"
              @keydown.space.prevent="selectInvoiceCard(inv.id)"
            >
              <div class="card-header-row">
                <span class="card-selection-indicator" :class="{ active: selectedInvoiceIds.includes(inv.id) }">
                  <i v-if="selectedInvoiceIds.includes(inv.id)" class="ri-check-line"></i>
                </span>
                <span class="card-title-text">{{ inv.title }}</span>
                <NTag v-if="isCandidateEntry(inv)" size="tiny" type="success" :bordered="false" class="match-candidate-tag">金额吻合</NTag>
                <span class="card-amount" :class="amountClassForDirection(inv.direction, inv.amount)">
                  {{ formatMoney(signedInvoiceAmount(inv)) }}
                </span>
              </div>
              <div class="card-meta">
                <span>{{ inv.date }}</span>
                <NTag v-if="inv.source === 'invoice_import'" size="tiny" type="info" :bordered="false">发票</NTag>
                <NTag v-else-if="inv.source === 'wechat_bill'" size="tiny" type="success" :bordered="false">微信账单</NTag>
                <NTag v-else size="tiny" type="warning" :bordered="false">手动记账</NTag>
                <NTag v-if="isMonthEndTransit(inv)" size="tiny" type="default" :bordered="false" style="color: var(--solo-primary);">次月在途(D+1)</NTag>
              </div>
              <div v-if="inv.source === 'invoice_import' || inv.invoiceNumber" class="card-quick-actions" @click.stop style="display: flex; align-items: center; gap: 8px; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--solo-border-light); font-size: 11px;">
                <span style="color: var(--solo-text-muted);">无对公流水？</span>
                <a style="color: var(--solo-primary); cursor: pointer; text-decoration: underline;" @click="handleMarkBossPayer(inv.id)">转老板垫付</a>
                <span style="color: var(--solo-border-light);">|</span>
                <a style="color: var(--solo-text-secondary); cursor: pointer; text-decoration: underline;" @click="toggleArchiveMode(inv.id, true)">设为仅归档</a>
              </div>
            </div>

            <!-- 发票待付款：配对=付款状态确认（票等钱），与账目核销语义不同 -->
            <div v-if="unmatchedInvoiceEntries.length > 0" class="entry-group-title">
              发票待付款（确认付款状态）<span>({{ unmatchedInvoiceEntries.length }})</span>
            </div>
            <div
              v-for="inv in unmatchedInvoiceEntries"
              :key="inv.id"
              class="canvas-card"
              :class="{ selected: selectedInvoiceIds.includes(inv.id), 'candidate-match': isCandidateEntry(inv) }"
              role="checkbox"
              :aria-checked="selectedInvoiceIds.includes(inv.id)"
              tabindex="0"
              @click="selectInvoiceCard(inv.id)"
              @keydown.enter.prevent="selectInvoiceCard(inv.id)"
              @keydown.space.prevent="selectInvoiceCard(inv.id)"
            >
              <div class="card-header-row">
                <span class="card-selection-indicator" :class="{ active: selectedInvoiceIds.includes(inv.id) }">
                  <i v-if="selectedInvoiceIds.includes(inv.id)" class="ri-check-line"></i>
                </span>
                <span class="card-title-text">{{ inv.title }}</span>
                <NTag v-if="isCandidateEntry(inv)" size="tiny" type="success" :bordered="false" class="match-candidate-tag">金额吻合</NTag>
                <span class="card-amount" :class="amountClassForDirection(inv.direction, inv.amount)">
                  {{ formatMoney(signedInvoiceAmount(inv)) }}
                </span>
              </div>
              <div class="card-meta">
                <span>{{ inv.date }}</span>
                <NTag size="tiny" type="info" :bordered="false">发票</NTag>
              </div>
              <div class="card-quick-actions" @click.stop style="display: flex; align-items: center; gap: 8px; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--solo-border-light); font-size: 11px;">
                <span style="color: var(--solo-text-muted);">无对公流水？</span>
                <a style="color: var(--solo-primary); cursor: pointer; text-decoration: underline;" @click="handleMarkBossPayer(inv.id)">转老板垫付</a>
                <span style="color: var(--solo-border-light);">|</span>
                <a style="color: var(--solo-text-secondary); cursor: pointer; text-decoration: underline;" @click="toggleArchiveMode(inv.id, true)">设为仅归档</a>
              </div>
            </div>

            <div v-if="historicalUnmatchedEntries.length > 0" style="margin: 14px 0 8px; font-size: 11px; font-weight: 600; color: var(--solo-text-muted);">
              历史遗留未对账 ({{ historicalUnmatchedEntries.length }})
            </div>
            <div
              v-for="inv in historicalUnmatchedEntries"
              :key="`history-${inv.id}`"
              class="canvas-card"
              :class="{ selected: selectedInvoiceIds.includes(inv.id), 'candidate-match': isCandidateEntry(inv) }"
              role="checkbox"
              :aria-checked="selectedInvoiceIds.includes(inv.id)"
              tabindex="0"
              @click="selectInvoiceCard(inv.id)"
              @keydown.enter.prevent="selectInvoiceCard(inv.id)"
              @keydown.space.prevent="selectInvoiceCard(inv.id)"
            >
              <div class="card-header-row">
                <span class="card-selection-indicator" :class="{ active: selectedInvoiceIds.includes(inv.id) }">
                  <i v-if="selectedInvoiceIds.includes(inv.id)" class="ri-check-line"></i>
                </span>
                <span class="card-title-text">{{ inv.title }}</span>
                <NTag v-if="isCandidateEntry(inv)" size="tiny" type="success" :bordered="false" class="match-candidate-tag">金额吻合</NTag>
                <span class="card-amount" :class="amountClassForDirection(inv.direction, inv.amount)">
                  {{ formatMoney(signedInvoiceAmount(inv)) }}
                </span>
              </div>
              <div class="card-meta">
                <span>{{ inv.date }}</span>
                <NTag size="tiny" type="warning" :bordered="false">历史遗留</NTag>
                <NTag v-if="isMonthEndTransit(inv)" size="tiny" type="default" :bordered="false" style="color: var(--solo-primary);">次月在途(D+1)</NTag>
              </div>
              <div v-if="inv.source === 'invoice_import' || inv.invoiceNumber" class="card-quick-actions" @click.stop style="display: flex; align-items: center; gap: 8px; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--solo-border-light); font-size: 11px;">
                <span style="color: var(--solo-text-muted);">无对公流水？</span>
                <a style="color: var(--solo-primary); cursor: pointer; text-decoration: underline;" @click="handleMarkBossPayer(inv.id)">转老板垫付</a>
                <span style="color: var(--solo-border-light);">|</span>
                <a style="color: var(--solo-text-secondary); cursor: pointer; text-decoration: underline;" @click="toggleArchiveMode(inv.id, true)">设为仅归档</a>
              </div>
            </div>
          </div>

          <!-- Middle: Matched Pairs (Pending review/Confirmed) -->
          <div class="canvas-column" style="background: rgba(16, 185, 129, 0.02); border-color: rgba(16, 185, 129, 0.15);">
            <div class="column-header"><i class="ri-check-double-line"></i> 已配对账单 ({{ matchedPairs.length }})</div>
            
            <!-- Manual match box trigger -->
            <div v-if="selectedInvoiceIds.length > 0 || selectedBank" class="match-composer">
              <div class="match-composer-header">
                <div>
                  <div class="match-composer-title">组合核对</div>
                  <div class="match-composer-subtitle">收入与手续费可合并匹配一笔净额流水</div>
                </div>
                <NButton size="tiny" text @click="clearManualSelection">清空</NButton>
              </div>
              <div class="match-composer-math">
                <div class="match-math-row">
                  <span>已选账目（{{ selectedEntriesForMatch.length }}）</span>
                  <strong :class="selectedEntriesTotal >= 0 ? 'income' : 'expense'">{{ formatMoney(selectedEntriesTotal) }}</strong>
                </div>
                <div class="match-math-row">
                  <span>银行流水</span>
                  <strong v-if="selectedBankForMatch" :class="selectedBankForMatch.amount >= 0 ? 'income' : 'expense'">
                    {{ formatMoney(selectedBankForMatch.amount) }}
                  </strong>
                  <span v-else class="match-awaiting">请在右侧选择</span>
                </div>
                <div v-if="selectedBankForMatch" class="match-math-row match-difference" :class="canConfirmManualMatch ? 'exact' : 'mismatch'">
                  <span>差额</span>
                  <strong>{{ formatMoney(selectedMatchDifference) }}</strong>
                </div>
              </div>
              <div v-if="isZeroNetSelection" class="match-composer-hint zero-net">
                <i class="ri-error-warning-line"></i>
                <span>所选账目净额为 0（可能包含与冲销抵消的条目），对账无意义，请重新选择</span>
              </div>
              <div class="match-composer-hint" :class="{ ready: canConfirmManualMatch }">
                <i :class="canConfirmManualMatch ? 'ri-checkbox-circle-line' : 'ri-information-line'"></i>
                <span>{{ canConfirmManualMatch ? '金额一致，可以确认对账' : selectedBankForMatch ? '继续勾选或取消账目，直到差额为 0' : '选择一笔银行流水后核对净额' }}</span>
              </div>
              <NButton size="small" type="primary" block :disabled="!canConfirmManualMatch" @click="handleManualMatch">
                {{ selectedInvoiceIds.length > 1 ? `确认合并 ${selectedInvoiceIds.length} 笔并对账` : '确认对账' }}
              </NButton>
            </div>

            <div v-if="matchedPairs.length === 0" style="padding: 40px 0; text-align: center; color: var(--solo-text-muted); font-size: 12px;">
              暂无已配对账单
            </div>
            
            <!-- Pairs List -->
            <div v-for="pair in matchedPairs" :key="pair.id" class="matched-pair-card">
              <div class="pair-header">
                <span class="confidence-badge" :class="pair.persisted ? 'manual' : 'high'">
                  {{ pairConfidenceText(pair) }}
                </span>
                <NTag v-if="pair.status === 'confirmed'" size="small" type="success">已核对</NTag>
                <NTag v-else size="small" type="warning">待核对</NTag>
              </div>

              <div class="pair-content">
                <div v-for="entry in pair.entries || [pair.invoice]" :key="entry.id" class="pair-item">
                  <div class="pair-item-label">
                    <i class="ri-file-list-3-line"></i>
                    <span>{{ entry.title }}</span>
                  </div>
                  <span class="pair-item-val" :class="amountClassForDirection(entry.direction, entry.amount)">
                    {{ formatMoney(signedInvoiceAmount(entry)) }}
                  </span>
                </div>

                <div class="pair-account-review" :class="{ warning: pairNeedsCategoryReview(pair) }">
                  <div class="pair-account-label">
                    <i class="ri-price-tag-3-line"></i>
                    <span>分类/科目</span>
                  </div>
                  <span>{{ pairCategoryPreview(pair) }}</span>
                </div>
                
                <div class="pair-divider">
                  <div class="pair-divider-icon"><i class="ri-arrow-up-down-line"></i></div>
                </div>

                <div class="pair-item">
                  <div class="pair-item-label">
                    <i class="ri-bank-card-line"></i>
                    <span>{{ pair.bank.title }}</span>
                  </div>
                  <span class="pair-item-val" :class="pair.bank.amount >= 0 ? 'income' : 'expense'">{{ formatMoney(pair.bank.amount) }}</span>
                </div>
              </div>

              <div v-if="pair.settlement" class="pair-actions">
                <NTag size="small" :type="pair.settlement.voucherStatus === 'posted' ? 'success' : 'warning'">
                  {{ pair.settlement.voucherStatus === 'posted' ? '结算凭证已过账' : '结算凭证待过账' }}
                </NTag>
                <NButton size="tiny" text type="primary" @click="openVoucherModal(pair.settlement.id)">查看结算凭证</NButton>
              </div>
              <div class="pair-actions" v-if="pair.status !== 'confirmed'">
                <NButton size="tiny" tertiary @click="deletePair(pair.id)">拆开</NButton>
                <NButton v-if="!pair.entries || pair.entries.length === 1" size="tiny" secondary @click="openPairCategoryDialog(pair)">修改分类</NButton>
                <NButton size="tiny" type="primary" secondary @click="confirmPair(pair.id)">确认匹配并认可分类</NButton>
              </div>
              <div class="pair-actions" v-else>
                <NButton v-if="!pair.entries || pair.entries.length === 1" size="tiny" text @click="openPairCategoryDialog(pair)">修改分类</NButton>
                <NButton size="tiny" text type="error" @click="deletePair(pair.id)">取消匹配</NButton>
              </div>
            </div>
          </div>

          <!-- Right: Unmatched Bank Statements -->
          <div class="canvas-column">
            <div class="column-header"><i class="ri-bank-line"></i> 本月未匹配流水 ({{ unmatchedStatements.length }})</div>
            <div v-if="unmatchedStatements.length === 0" style="padding: 28px 0; text-align: center; color: var(--solo-text-muted); font-size: 12px;">
              本月银行流水已匹配完成
            </div>
            <div
              v-for="stmt in unmatchedStatements"
              :key="stmt.id"
              :data-statement-id="stmt.id"
              class="canvas-card"
              :class="{ selected: selectedBank === stmt.id, 'candidate-match': isCandidateBank(stmt) }"
              role="radio"
              :aria-checked="selectedBank === stmt.id"
              tabindex="0"
              @click="selectBankCard(stmt.id)"
              @keydown.enter.prevent="selectBankCard(stmt.id)"
              @keydown.space.prevent="selectBankCard(stmt.id)"
            >
              <div class="card-header-row">
                <span class="card-selection-indicator radio" :class="{ active: selectedBank === stmt.id }">
                  <i v-if="selectedBank === stmt.id" class="ri-check-line"></i>
                </span>
                <span class="card-title-text">{{ stmt.description }}</span>
                <NTag v-if="isCandidateBank(stmt)" size="tiny" type="success" :bordered="false" class="match-candidate-tag">金额吻合</NTag>
                <span class="card-amount" :class="stmt.amount >= 0 ? 'income' : 'expense'">{{ formatMoney(stmt.amount) }}</span>
              </div>
              <div class="card-meta">
                <span>{{ stmt.transaction_date?.slice(0, 10) || '日期缺失' }}</span>
                <NTag size="tiny" :type="stmt.amount >= 0 ? 'success' : 'error'" :bordered="false" style="font-size: 10px; padding: 0 5px;">
                  {{ stmt.amount >= 0 ? '收款' : '付款' }}
                </NTag>
              </div>
              <div v-if="getAiSuggestion(stmt.id)" style="margin-top: 6px; padding: 5px 8px; border-radius: 6px; background: rgba(99,102,241,0.05); border: 1px dashed rgba(99,102,241,0.2); font-size: 11px; color: var(--solo-text-secondary);">
                <i class="ri-sparkling-2-fill" style="color: var(--solo-primary); margin-right: 3px;"></i>
                <span>{{ getAiSuggestion(stmt.id)?.type === 'new' ? 'AI 分类建议' : '对账建议' }}：{{ getAiSuggestion(stmt.id)?.suggestedEntry?.category || getAiSuggestion(stmt.id)?.reason }}</span>
                <span v-if="getAiSuggestion(stmt.id)?.type === 'new'" style="margin-left: 4px; color: var(--solo-text-muted);">置信度 {{ Math.round((getAiSuggestion(stmt.id)?.confidence || 0) * 100) }}%</span>
              </div>
              <div v-if="aiDraftLoadingStatementId === stmt.id" class="ai-draft-loading-hint">
                <i class="ri-loader-4-line ai-draft-spinner"></i>
                <span>AI 正在生成分类草稿，通常需要几秒</span>
              </div>
              <div class="card-meta" style="margin-top: 6px; justify-content: flex-end; gap: 6px;">
                <NButton size="tiny" secondary :disabled="!!aiDraftLoadingStatementId" @click.stop="openManualClassify(stmt)">
                  <i class="ri-edit-line" style="margin-right: 2px;"></i> 手动分类
                </NButton>
                <NButton
                  size="tiny"
                  type="warning"
                  text
                  :loading="aiDraftLoadingStatementId === stmt.id"
                  :disabled="!!aiDraftLoadingStatementId && aiDraftLoadingStatementId !== stmt.id"
                  @click.stop="handleAutoPostStatement(stmt.id)"
                >
                  <i v-if="aiDraftLoadingStatementId !== stmt.id" class="ri-sparkling-fill" style="margin-right: 2px;"></i>
                  {{ aiDraftLoadingStatementId === stmt.id ? '生成中' : '生成 AI 草稿' }}
                </NButton>
              </div>
            </div>

            <div v-if="historicalUnmatchedStatements.length > 0" style="margin: 14px 0 8px; font-size: 11px; font-weight: 600; color: var(--solo-text-muted);">
              历史遗留未匹配流水 ({{ historicalUnmatchedStatements.length }})
            </div>
            <div
              v-for="stmt in historicalUnmatchedStatements"
              :key="`history-${stmt.id}`"
              class="canvas-card"
              :class="{ selected: selectedBank === stmt.id }"
              role="radio"
              :aria-checked="selectedBank === stmt.id"
              tabindex="0"
              @click="selectBankCard(stmt.id)"
              @keydown.enter.prevent="selectBankCard(stmt.id)"
              @keydown.space.prevent="selectBankCard(stmt.id)"
            >
              <div class="card-header-row">
                <span class="card-selection-indicator radio" :class="{ active: selectedBank === stmt.id }">
                  <i v-if="selectedBank === stmt.id" class="ri-check-line"></i>
                </span>
                <span class="card-title-text">{{ stmt.description }}</span>
                <span class="card-amount" :class="stmt.amount >= 0 ? 'income' : 'expense'">{{ formatMoney(stmt.amount) }}</span>
              </div>
              <div class="card-meta">
                <span>{{ stmt.transaction_date?.slice(0, 10) || '日期缺失' }}</span>
                <NTag size="tiny" type="warning" :bordered="false">历史遗留</NTag>
              </div>
              <div v-if="getAiSuggestion(stmt.id)" style="margin-top: 6px; padding: 5px 8px; border-radius: 6px; background: rgba(99,102,241,0.05); border: 1px dashed rgba(99,102,241,0.2); font-size: 11px; color: var(--solo-text-secondary);">
                <i class="ri-sparkling-2-fill" style="color: var(--solo-primary); margin-right: 3px;"></i>
                <span>{{ getAiSuggestion(stmt.id)?.type === 'new' ? 'AI 分类建议' : '对账建议' }}：{{ getAiSuggestion(stmt.id)?.suggestedEntry?.category || getAiSuggestion(stmt.id)?.reason }}</span>
              </div>
              <div v-if="aiDraftLoadingStatementId === stmt.id" class="ai-draft-loading-hint">
                <i class="ri-loader-4-line ai-draft-spinner"></i>
                <span>AI 正在生成分类草稿，通常需要几秒</span>
              </div>
              <div class="card-meta" style="margin-top: 6px; justify-content: flex-end; gap: 6px;">
                <NButton size="tiny" secondary :disabled="!!aiDraftLoadingStatementId" @click.stop="openManualClassify(stmt)">手动分类</NButton>
                <NButton
                  size="tiny"
                  type="warning"
                  text
                  :loading="aiDraftLoadingStatementId === stmt.id"
                  :disabled="!!aiDraftLoadingStatementId && aiDraftLoadingStatementId !== stmt.id"
                  @click.stop="handleAutoPostStatement(stmt.id)"
                >
                  {{ aiDraftLoadingStatementId === stmt.id ? '生成中' : '生成 AI 草稿' }}
                </NButton>
              </div>
            </div>
          </div>
        </div>
      </NCard>
    </div>

    <!-- Step 5 Layout: Closing audit & ceremony / Closed CFO diagnostic report -->
    <div v-if="currentStep === 5">
      <!-- 5.1 未结账视图：合规体检与关账确认 -->
      <div v-if="!closed" class="closing-step-container">
        <NCard :bordered="false" class="report-card mb-4 readiness-overview">
          <NSpin :show="loading || closingLoading">
            <div class="readiness-heading">
              <div>
                <p class="eyebrow">第五步：关账前合规检查</p>
                <h3>{{ targetMonth }} 结账体检清单</h3>
                <p class="report-subtitle">检查凭证状态、借贷平衡、银行核对及财务报表关系，全部通过后可封账。</p>
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
                  <template #icon><i class="ri-file-add-line" /></template>
                  去录入发票/单据 (Step 1)
                </NButton>
                <NButton size="small" secondary @click="currentStep = 2">
                  <template #icon><i class="ri-bank-card-line" /></template>
                  去导入银行流水 (Step 2)
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

            <!-- 五项核验详细状态（原检查框架，折叠保留） -->
            <NCollapse class="readiness-detail-collapse mb-4">
              <NCollapseItem title="查看检查项明细（凭证与来源 / 账账核对 / 银行流水核对 / 账表核对 / 申报校验）" name="checks">
            <div class="readiness-list" aria-label="结账准备清单">
              <!-- 员工薪酬发放阻断项 -->
              <div v-if="hasBlockingPayroll" class="readiness-row blocked">
                <span class="readiness-row-icon"><i class="ri-error-warning-line" /></span>
                <span class="readiness-row-copy">
                  <strong>员工薪酬与社保发放</strong>
                  <small>本月有 {{ employees.length }} 名在职员工薪酬尚未完成计提或发放确认。</small>
                </span>
                <span class="readiness-row-status">需要处理</span>
                <NButton size="tiny" secondary type="warning" @click="currentStep = 3">
                  去计提与发放 (Step 3)
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
              </div>

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
              <NButton size="tiny" type="error" secondary @click="currentStep = 4">前往对账修复</NButton>
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
                  尚有 {{ employees.length }} 名员工薪酬待确认发放，暂无法结账
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
                {{ canBatchPostAndClose ? `确认后将批量过账本月 ${closeDraftCount} 张待过账凭证，再完成关账；如有凭证需复核或其他核验未通过，整次操作会回滚。` : isZeroRecordMonth ? '以零业务结账后，本期资产负债表与利润表将以零收支锁定归档' : '结账后本期账册将锁定归档，并自动生成 AI 财务体检与经营诊断报告' }}
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
              <p>增值税免税额度、小型微利企业所得税测算与三方核对已具备法定申报条件，建议立即前往【税报 · 申报作业包】进行电子税务局抄报。</p>
            </div>
          </div>
          <NButton type="primary" size="medium" @click="router.push(`/tax-filing?period=${closedQuarter}`)">
            前往 {{ closedQuarter }} 税报作业包 →
          </NButton>
        </div>

        <!-- Action tools -->
        <NSpace justify="center">
          <NButton size="medium" @click="downloadReport">
            <template #icon><i class="ri-file-zip-line" /></template>
            导出本月凭证与财务报告包 (PDF)
          </NButton>
          <NButton size="medium" type="primary" @click="router.push('/')">
            返回首页工作台
          </NButton>
        </NSpace>
      </div>
    </div>

    <!-- Step Navigation Buttons -->
    <div v-if="!closed" class="no-print" style="display: flex; justify-content: space-between; align-items: center; margin-top: 24px;">
      <NButton :disabled="currentStep === 1" @click="prevStep">
        <template #icon><i class="ri-arrow-left-line" /></template>
        上一步
      </NButton>
      <div v-if="currentStep < 5" style="display: flex; gap: 8px;">
        <NButton type="primary" @click="nextStep">
          {{ currentStep === 4 ? '下一步：前往关账检查' : '下一步' }}
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>
    </div>

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
                  <td colspan="4" style="text-align: center; color: var(--solo-text-muted); padding: 16px;">
                    暂无借贷分录明细
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
  color: var(--solo-text-primary, #1d2129);
}
.relay-copy p {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #4e5969);
  line-height: 1.5;
}

/* Manual Classify Dialog */
.manual-dialog {
  background: var(--solo-bg-card);
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
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
  border-radius: 10px;
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
  border-radius: 6px;
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
  border-radius: 6px;
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
  border-radius: 10px;
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
  border-radius: 14px;
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
  border-radius: 14px;
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
  color: var(--solo-primary, #165dff);
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
</style>
