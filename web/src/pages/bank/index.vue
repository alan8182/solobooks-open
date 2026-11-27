<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, h, onBeforeUnmount, onMounted, watch } from 'vue'
import { accountingErrorText } from '@/utils/errors'
import { useRouter } from 'vue-router'
import {
  NAlert, NCard, NButton, NTag, NSpace, useMessage, NSpin,
  NInput, NModal, NDataTable, NSelect, NDatePicker, useDialog,
  NCollapse, NCollapseItem,
} from 'naive-ui'
import apiClient from '@/api/client'
import {
  BANK_RECEIPT_ACCEPT,
  bindBankReceipt,
  downloadAttachmentBlob,
  fetchAttachmentPreviewBlob,
  listBankReceipts,
  relinkBankReceipts,
  uploadBankReceipts,
  type BankReceiptUploadResult,
  type VoucherAttachmentItem,
} from '@/api/attachments'
import { saveBlobFile } from '@/utils/download'
import { applySalaryPayment, applyPayrollLiability } from '@/api/salary'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import { usePageRefresh } from '@/composables/usePageRefresh'
import {
  buildBankRows,
  detectBankColumnMapping,
  parseBankWorkbookData,
  bankColumnDescriptionRole,
  extractStatementCounterparty,
  isBankCounterpartySourceBlocked,
} from '@/utils/bankImport'
import { SoloDrawer, SoloEmpty, SoloStatusBadge } from '@/components/ui'
import FileDropzone from '@/components/business/FileDropzone.vue'
import GuideTrigger from '@/components/business/GuideTrigger.vue'
import MailInboxCard from '@/components/business/MailInboxCard.vue'
import OpeningGateAlert from '@/components/business/OpeningGateAlert.vue'
import BossTrackNav from '@/components/business/BossTrackNav.vue'
import BankCategoryRulesDrawer from './components/BankCategoryRulesDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const authStore = (() => {
  try {
    return useAuthStore()
  } catch {
    return null
  }
})()
const openingBlocked = computed(() => Boolean(authStore?.openingBalanceRequired))
const viewStore = useWorkspaceViewStore()
const { confirmDelete } = useDeleteConfirm()

const loading = ref(false)
const statements = ref<any[]>([])
const diffReport = ref<any>(null)
const month = ref(new Date().toISOString().slice(0, 7))
const showUnmatched = ref(false)
const matching = ref(false)
const searchKeyword = ref('')
const dateRange = ref<[number, number] | null>(null)
const amountDirection = ref<'all' | 'income' | 'expense'>('all')
const matchStatus = ref<'all' | 'matched' | 'unmatched'>('all')
const tablePage = ref(1)
const tablePageSize = ref(20)
const checkedRowKeys = ref<Array<string | number>>([])
const batchDeleting = ref(false)
const repairingEffects = ref<Record<string, boolean>>({})
const showCategoryRules = ref(false)

const bankReceiptUploading = ref(false)
const bankReceiptRelinking = ref(false)
const bankReceiptResult = ref<BankReceiptUploadResult | null>(null)
const bankReceipts = ref<VoucherAttachmentItem[]>([])

const receiptsByStatement = computed<Record<string, VoucherAttachmentItem[]>>(() => {
  const map: Record<string, VoucherAttachmentItem[]> = {}
  for (const item of bankReceipts.value) {
    if (!item.bankStatementId) continue
    ;(map[item.bankStatementId] ||= []).push(item)
  }
  return map
})

const parkedReceipts = computed(() => bankReceipts.value.filter(item => !item.bankStatementId))

async function loadBankReceipts() {
  try {
    const res = await listBankReceipts()
    bankReceipts.value = res.items || []
  } catch {
    // 加载回单索引失败不阻断流水对账主流程
  }
}

const showReceiptPreviewModal = ref(false)
const previewStatement = ref<any>(null)
const previewReceiptList = ref<VoucherAttachmentItem[]>([])
const previewActiveIndex = ref(0)
const receiptPreviewUrl = ref('')
const receiptPreviewLoading = ref(false)
const receiptPreviewFailed = ref(false)
const activePreviewReceipt = computed(() => previewReceiptList.value[previewActiveIndex.value] || null)

function revokeReceiptPreview() {
  if (receiptPreviewUrl.value && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(receiptPreviewUrl.value)
  }
  receiptPreviewUrl.value = ''
}

async function loadReceiptPreviewImage(item: VoucherAttachmentItem | null) {
  revokeReceiptPreview()
  receiptPreviewFailed.value = false
  if (!item || typeof URL.createObjectURL !== 'function') return
  receiptPreviewLoading.value = true
  try {
    const blob = await fetchAttachmentPreviewBlob(item.id)
    if (showReceiptPreviewModal.value && activePreviewReceipt.value?.id === item.id) {
      receiptPreviewUrl.value = URL.createObjectURL(blob)
    }
  } catch {
    receiptPreviewFailed.value = true
  } finally {
    receiptPreviewLoading.value = false
  }
}

function openReceiptPreview(row: any, receipts: VoucherAttachmentItem[]) {
  previewStatement.value = row
  previewReceiptList.value = receipts
  previewActiveIndex.value = 0
  showReceiptPreviewModal.value = true
  void loadReceiptPreviewImage(receipts[0] || null)
}

function selectPreviewReceipt(index: number) {
  previewActiveIndex.value = index
  void loadReceiptPreviewImage(previewReceiptList.value[index] || null)
}

async function handleDownloadReceipt(item: VoucherAttachmentItem) {
  try {
    const blob = await downloadAttachmentBlob(item.id)
    saveBlobFile(blob, item.fileName)
  } catch (err) {
    message.error(accountingErrorText(err, '下载回单失败'))
  }
}

onBeforeUnmount(revokeReceiptPreview)

const showBindReceiptModal = ref(false)
const bindTargetStatement = ref<any>(null)
const selectedBindReceiptId = ref<string | null>(null)
const bindingReceipt = ref(false)

const bindReceiptOptions = computed(() =>
  parkedReceipts.value.map((item) => {
    const meta = (item.parsedMeta || {}) as Record<string, unknown>
    const txDate = String(meta.txDate || '').trim() || '缺日期'
    const amount = String(meta.amount || '').trim() ? `¥${String(meta.amount)}` : '未识别金额'
    const party =
      meta.direction === 'expense'
        ? String(meta.payeeName || meta.summary || '')
        : String(meta.payerName || meta.summary || '')
    const desc = party || item.fileName
    const recNo = meta.receiptNo ? ` · #${String(meta.receiptNo)}` : ''
    return {
      label: `${txDate} · ${amount} · ${desc}${recNo}`,
      value: item.id,
    }
  }),
)

function openBindReceiptModal(row: any) {
  bindTargetStatement.value = row
  const stmtAbs = Math.abs(Number(row.amount || 0))
  const suggested = parkedReceipts.value.find((item) => {
    const amt = Number((item.parsedMeta as Record<string, unknown> | undefined)?.amount || 0)
    return stmtAbs > 0 && Math.abs(amt - stmtAbs) < 0.01
  })
  selectedBindReceiptId.value = suggested?.id || parkedReceipts.value[0]?.id || null
  showBindReceiptModal.value = true
}

async function confirmBindReceipt() {
  if (!bindTargetStatement.value || !selectedBindReceiptId.value) return
  bindingReceipt.value = true
  try {
    await bindBankReceipt(selectedBindReceiptId.value, String(bindTargetStatement.value.id))
    message.success('已绑定回单到当前流水')
    showBindReceiptModal.value = false
    await loadBankReceipts()
  } catch (err) {
    message.error(accountingErrorText(err, '绑定回单失败'))
  } finally {
    bindingReceipt.value = false
  }
}

async function uploadBankReceiptBatch(files: File[]) {
  if (!files.length) return false
  bankReceiptUploading.value = true
  try {
    const res = await uploadBankReceipts(files, true)
    bankReceiptResult.value = res
    const { receiptTotal, matchedCount, parkedCount, statementPdfCount, duplicateCount } = res.summary
    const parts: string[] = []
    if (receiptTotal > 0) parts.push(`拆出回单 ${receiptTotal} 笔（已挂流水 ${matchedCount}，待挂 ${parkedCount}）`)
    if (statementPdfCount > 0) parts.push(`对账单 PDF ${statementPdfCount} 份`)
    if (duplicateCount > 0) parts.push(`重复跳过 ${duplicateCount} 份`)
    message.success(parts.length ? parts.join(' · ') : '回单文件处理完成')
    await loadBankReceipts()
    return true
  } catch (err) {
    message.error(accountingErrorText(err, '上传银行回单失败'))
    return false
  } finally {
    bankReceiptUploading.value = false
  }
}

async function handleUnifiedBankUpload(files: File[]) {
  const tableFiles = files.filter((f) => /\.(xlsx|xls|csv)$/i.test(f.name))
  const receiptFiles = files.filter((f) => /\.(zip|pdf|png|jpe?g|webp)$/i.test(f.name))

  if (!tableFiles.length && !receiptFiles.length) {
    message.warning('暂不支持该文件格式，请上传 .xlsx / .xls / .csv 流水或 .zip / .pdf / 图片回单')
    return
  }

  if (receiptFiles.length > 0) {
    const ok = await uploadBankReceiptBatch(receiptFiles)
    if (ok && tableFiles.length === 0) {
      showExcelImport.value = false
    }
  }

  if (tableFiles.length > 0) {
    if (tableFiles.length > 1) {
      message.info('每次预览导入 1 份流水表格，已为你加载第一份')
    }
    handleFile(tableFiles[0])
  }
}

async function handleRelinkBankReceipts() {
  bankReceiptRelinking.value = true
  try {
    const res = await relinkBankReceipts()
    await loadBankReceipts()
    if (bankReceiptResult.value) {
      bankReceiptResult.value = {
        ...bankReceiptResult.value,
        summary: {
          ...bankReceiptResult.value.summary,
          matchedCount: bankReceiptResult.value.summary.matchedCount + res.relinked,
          parkedCount: parkedReceipts.value.length,
        },
      }
    }
    message.success(res.relinked > 0 ? `已自动挂接 ${res.relinked} 笔银行回单到流水` : '当前没有可新挂接的待匹配回单')
  } catch {
    message.error('重新匹配回单失败')
  } finally {
    bankReceiptRelinking.value = false
  }
}

const balanceSummary = computed(() => diffReport.value?.balance_reconciliation || null)
const matchedEffectIssues = computed<any[]>(() => diffReport.value?.matched_effect_issues || [])
const unmatchedCount = computed(() =>
  Number(diffReport.value?.bank_only_count || 0) + Number(diffReport.value?.book_only_count || 0),
)
const reconciliationStatusText = computed(() => {
  if (balanceSummary.value?.fully_reconciled) return '本期银行对账完成'
  if (!balanceSummary.value?.flow_balanced) return '流水余额不连续'
  if (matchedEffectIssues.value.length > 0) return `发现 ${matchedEffectIssues.value.length} 笔账银影响异常`
  if (Math.abs(Number(balanceSummary.value?.book_difference || 0)) > 0.01) return '银行与账本余额不一致'
  if (unmatchedCount.value > 0) return `仍有 ${unmatchedCount.value} 项未匹配`
  return '本期仍有待核对项目'
})
const monthOptions = computed(() => {
  const months = new Set(statements.value
    .map(item => String(item.transaction_date || '').slice(0, 7))
    .filter(Boolean))
  // 当前月永远可选：即使本月还没有流水，也能回到本月查看（选项不随数据为空而消失）
  months.add(new Date().toISOString().slice(0, 7))
  return [...months].sort().reverse().map(value => {
    const [year, monthNumber] = value.split('-')
    return { label: `${year}年${Number(monthNumber)}月`, value }
  })
})

function formatMoney(value: unknown) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

const directionOptions = [
  { label: '全部收支', value: 'all' },
  { label: '收入', value: 'income' },
  { label: '支出', value: 'expense' },
]

const matchStatusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '已匹配', value: 'matched' },
  { label: '未匹配', value: 'unmatched' },
]

function toDateKey(timestamp: number) {
  const date = new Date(timestamp)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

const filteredStatements = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  const startDate = dateRange.value ? toDateKey(dateRange.value[0]) : ''
  const endDate = dateRange.value ? toDateKey(dateRange.value[1]) : ''

  return statements.value.filter((row) => {
    const rowDate = String(row.transaction_date || '').slice(0, 10)
    if (startDate && rowDate < startDate) return false
    if (endDate && rowDate > endDate) return false
    if (amountDirection.value === 'income' && Number(row.amount) <= 0) return false
    if (amountDirection.value === 'expense' && Number(row.amount) >= 0) return false
    if (matchStatus.value === 'matched' && !row.matched_entry_id) return false
    if (matchStatus.value === 'unmatched' && row.matched_entry_id) return false
    if (!keyword) return true

    const counterparty = row.counterparty || extractStatementCounterparty(row.description)
    return [rowDate, row.description, counterparty, row.amount, row.balance]
      .some(value => String(value ?? '').toLowerCase().includes(keyword))
  })
})

const hasActiveFilters = computed(() => Boolean(
  searchKeyword.value.trim()
  || dateRange.value
  || amountDirection.value !== 'all'
  || matchStatus.value !== 'all'
))

const tablePagination = computed(() => ({
  page: tablePage.value,
  pageSize: tablePageSize.value,
  showSizePicker: true,
  pageSizes: [20, 50, 100],
  itemCount: filteredStatements.value.length,
}))

function resetStatementFilters() {
  searchKeyword.value = ''
  dateRange.value = null
  amountDirection.value = 'all'
  matchStatus.value = 'all'
}

watch([searchKeyword, dateRange, amountDirection, matchStatus], () => {
  tablePage.value = 1
  checkedRowKeys.value = []
})

// AI suggest
const showAiPanel = ref(false)
const aiLoading = ref(false)
const aiSuggestions = ref<any[]>([])
const aiConfirming = ref<Record<string, boolean>>({})
const ruleSuggestedIds = ref<string[]>([])
const remainingAfterRules = ref(0)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])
const rdSelections = ref<Record<string, { projectId: string; expenseType: string }>>({})

function isRdSuggestion(suggestion: any) {
  return suggestion?.type === 'new'
    && suggestion?.suggestedEntry?.category === '研发支出-费用化支出'
}

function rdSelectionFor(statementId: string) {
  if (!rdSelections.value[statementId]) {
    rdSelections.value[statementId] = { projectId: '', expenseType: '' }
  }
  return rdSelections.value[statementId]
}

async function loadRdOptions() {
  try {
    const [projects, expenseTypes] = await Promise.all([getRdProjects(), getRdExpenseTypes()])
    rdProjects.value = projects.filter(project => project.status === 'active')
    rdExpenseTypes.value = expenseTypes.items
  } catch {
    message.warning('研发项目选项加载失败，研发类建议暂不能确认')
  }
}

// Excel import
const showExcelImport = ref(false)
const excelStep = ref<'upload' | 'preview'>('upload')
const excelRows = ref<any[]>([])
const excelColumns = ref<string[]>([])
const excelFileName = ref('')
const excelImporting = ref(false)
const columnMapping = ref<Record<string, string>>({})
const advancedPanels = ref<string[]>([])

// 预览与真正提交的数据必须同源：解析一次，核对和导入都用它，改映射即时重算
const importPreview = computed(() => buildBankRows(excelRows.value, columnMapping.value))
const PREVIEW_ROW_COUNT = 8
const previewRows = computed(() => importPreview.value.rows.slice(0, PREVIEW_ROW_COUNT))

const DESCRIPTION_FIELDS = ['description', 'comment', 'counterparty_name', 'payee_name', 'payer_name']

interface KeyFieldRow {
  field: string
  label: string
  columns: string[]
  detected: boolean
  required: boolean
  sourceText: string
  /** 字段来自多列（摘要拼接、收入/支出分列）时不给单列下拉，交给下方全部列 */
  editable: boolean
  splitMode?: boolean
}

// 识别结果只反查真正需要人确认的五个字段，其余列一律视为自动处理
const keyFieldRows = computed<KeyFieldRow[]>(() => {
  const colsOf = (fields: string[]) =>
    excelColumns.value.filter(col => fields.includes(columnMapping.value[col] || ''))
  const join = (cols: string[]) => cols.join('、')
  const date = colsOf(['transaction_date'])
  const time = colsOf(['transaction_time'])
  const desc = colsOf(['description'])
  const descSupport = colsOf(DESCRIPTION_FIELDS.filter(f => f !== 'description'))
  const amount = colsOf(['amount'])
  const split = colsOf(['income_amount', 'expense_amount'])
  const balance = colsOf(['balance'])
  return [
    {
      field: 'transaction_date', label: '日期', columns: date, required: true,
      detected: date.length > 0, sourceText: date.length ? join(date) : '未识别',
      editable: date.length <= 1,
    },
    {
      field: 'transaction_time', label: '交易时间', columns: time, required: false,
      detected: time.length > 0, sourceText: time.length ? join(time) : '未使用',
      editable: time.length <= 1,
    },
    {
      field: 'description', label: '摘要', columns: desc, required: false,
      detected: desc.length > 0 || descSupport.length > 0,
      sourceText: desc.length
        ? join(desc)
        : descSupport.length ? `附言/户名自动拼接（${descSupport.length} 列）` : '未使用',
      editable: desc.length <= 1,
    },
    {
      field: 'amount', label: '金额', columns: amount, required: true,
      detected: amount.length > 0 || split.length > 0,
      sourceText: amount.length
        ? join(amount)
        : split.length ? `收入/支出两列（${join(split)}）` : '未识别',
      editable: amount.length <= 1 && split.length === 0,
      splitMode: split.length > 0,
    },
    {
      field: 'balance', label: '余额', columns: balance, required: false,
      detected: balance.length > 0, sourceText: balance.length ? join(balance) : '未识别',
      editable: balance.length <= 1,
    },
  ]
})

const unrecognizedRequired = computed(() => keyFieldRows.value.filter(row => row.required && !row.detected))

// 换来源列：清掉同字段旧列；金额的三种来源互斥，选了单金额列就撤掉收支拆分
function setFieldSource(field: string, column: string) {
  const targets = field === 'amount' ? ['amount', 'income_amount', 'expense_amount'] : [field]
  for (const col of excelColumns.value) {
    if (col !== column && targets.includes(columnMapping.value[col] || '')) columnMapping.value[col] = ''
  }
  if (column) columnMapping.value[column] = field
}

const importPreviewColumns = [
  {
    title: '日期 / 时间', key: 'transaction_date', width: 132,
    render: (row: any) => {
      const raw = String(row.transaction_date || '')
      return h('div', { style: 'line-height: 1.35;' }, [
        h('div', raw.slice(0, 10) || '-'),
        raw.length > 10
          ? h('div', { style: 'font-size: 11px; color: var(--solo-text-muted);' }, raw.slice(11))
          : null,
      ])
    },
  },
  { title: '摘要', key: 'description', ellipsis: { tooltip: true } },
  {
    title: '金额', key: 'amount', width: 96, align: 'right' as const,
    render: (row: any) => h('span', {
      style: {
        color: row.amount < 0
          ? 'var(--solo-expense, #bd3d3d)'
          : row.amount > 0 ? 'var(--solo-income)' : 'var(--solo-text-muted)',
        fontVariantNumeric: 'tabular-nums',
      },
    }, Number(row.amount || 0).toFixed(2)),
  },
  {
    title: '余额', key: 'balance', width: 100, align: 'right' as const,
    render: (row: any) => h('span', { style: 'font-variant-numeric: tabular-nums;' },
      Number(row.balance || 0).toFixed(2)),
  },
]


const statementColumns = computed(() => [
  { type: 'selection' as const },
  {
    title: '日期', key: 'transaction_date', width: 110,
    // 网银把日期与时间拆两列时前端会并成 'YYYY-MM-DD HH:mm:ss'；表格里日期为主、
    // 时间用次级颜色另起一行，同日多笔同额手续费靠时间分辨先后。
    render: (row: any) => {
      const raw = String(row.transaction_date || '')
      const day = raw.slice(0, 10)
      const time = raw.slice(11)
      return h('div', { style: 'line-height: 1.35;' }, [
        h('div', day || '-'),
        time
          ? h('div', { style: 'font-size: 11px; color: var(--solo-text-muted);' }, time)
          : null,
      ])
    },
    sorter: (a: any, b: any) => String(a.transaction_date || '').localeCompare(String(b.transaction_date || '')),
    defaultSortOrder: 'descend' as const,
  },
  {
    title: '对方户名', key: 'counterparty', width: 160,
    render: (row: any) => {
      const party = row.counterparty || extractStatementCounterparty(row.description)
      return party
        ? h('span', { class: 'statement-counterparty', style: 'font-weight: 500;' }, party)
        : h('span', { style: 'color: var(--solo-text-muted);' }, '-')
    },
  },
  { title: '摘要', key: 'description' },
  {
    title: '金额', key: 'amount', width: 120,
    sorter: (a: any, b: any) => Number(a.amount || 0) - Number(b.amount || 0),
    render: (row: any) => row.amount.toFixed(2),
  },
  {
    title: '余额', key: 'balance', width: 120,
    sorter: (a: any, b: any) => Number(a.balance || 0) - Number(b.balance || 0),
    render: (row: any) => row.balance.toFixed(2),
  },
  {
    title: '匹配状态', key: 'matched_entry_id', width: 100,
    // 38 行里通常 38 个「已匹配」。正常态只用灰点 + 灰字，把颜色留给「未匹配」——
    // 已匹配行可点击直达对应记账凭证。
    render: (row: any) => row.matched_entry_id
      ? h(NButton, {
          text: true,
          size: 'tiny',
          title: '点击查看关联记账凭证',
          onClick: () => router.push(`/vouchers/${row.matched_entry_id}`),
        }, () => h(SoloStatusBadge, { tone: 'neutral', plain: true }, () => '已匹配'))
      : h(SoloStatusBadge, { tone: 'warning', plain: true }, () => '未匹配'),
  },
  {
    title: '回单', key: 'receipts', width: 92, align: 'center' as const,
    render: (row: any) => {
      const matched = receiptsByStatement.value[String(row.id)] || []
      if (matched.length > 0) {
        return h(NButton, {
          size: 'tiny',
          text: true,
          type: 'primary',
          title: '点击查看银行回单原件',
          onClick: () => openReceiptPreview(row, matched),
        }, () => matched.length > 1 ? `回单 (${matched.length})` : '查看回单')
      }
      if (parkedReceipts.value.length > 0) {
        return h(NButton, {
          size: 'tiny',
          quaternary: true,
          class: 'row-bind-receipt',
          title: '从待挂回单中手动绑定到本条流水',
          onClick: () => openBindReceiptModal(row),
        }, () => '补绑')
      }
      return h('span', { style: 'color: var(--solo-text-muted);' }, '—')
    },
  },
  {
    title: '操作', key: 'action', width: 90, align: 'center' as const,
    // 删除是这张表里使用频率最低、后果最重的动作，不该是整页唯一的红色。
    // 默认灰字静默，鼠标停到这一行才转红——触屏没有 hover，所以不做成 opacity:0。
    render: (row: any) => h(NButton, {
      size: 'tiny',
      quaternary: true,
      class: 'row-delete',
      onClick: () => confirmDelete({
        content: `确定删除 ${String(row.transaction_date || '').slice(0, 10)} 的这条银行流水吗？此操作不可撤销。`,
        onConfirm: () => handleDeleteStatement(row.id),
      })
    }, () => '删除')
  }
])

async function loadStatements() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/bank/statements')
    statements.value = data.items || []
    const latestMonth = statements.value
      .map(item => String(item.transaction_date || '').slice(0, 7))
      .filter(Boolean)
      .sort()
      .at(-1)
    if (!diffReport.value && latestMonth) month.value = latestMonth
    await Promise.all([loadDiff(), loadBankReceipts()])
  } catch {
    message.error('加载流水失败')
  } finally {
    loading.value = false
  }
}

async function loadDiff() {
  loading.value = true
  try {
    diffReport.value = await apiClient.get('/api/bank/difference', {
      params: { month: month.value },
    })
  } catch {
    message.error('加载余额核对失败')
  } finally {
    loading.value = false
  }
}

function openIssueEntry(issue: any) {
  const entryId = issue.entryIds?.[0]
  if (entryId) router.push(`/ledger/${entryId}`)
}

function confirmEffectRepair(issue: any) {
  dialog.warning({
    title: '确认修复账银差异',
    content: `${issue.repairDescription}。修复后系统会重新核对银行存款影响，并保留审计记录。`,
    positiveText: issue.repairLabel || '确认修复',
    negativeText: '取消',
    onPositiveClick: async () => {
      repairingEffects.value[issue.statementId] = true
      try {
        const result: any = await apiClient.post('/api/bank/repair-effect', {
          statement_id: issue.statementId,
          auto_post: true,
        })
        message.success(result.settlementStatus && result.settlementStatus !== 'posted'
          ? '已生成正式体系的结算凭证草稿，请前往凭证页过账'
          : result.status === 'already_repaired' ? '该差异已经修复' : '修复完成，已重新核对余额')
        await loadDiff()
      } catch (error: any) {
        const detail = error?.response?.data?.detail
        message.error(typeof detail === 'string' ? detail : detail?.message || '修复失败，请检查账目凭证')
        throw error
      } finally {
        repairingEffects.value[issue.statementId] = false
      }
    },
  })
}

async function handleMatch() {
  matching.value = true
  showAiPanel.value = true
  aiSuggestions.value = []
  ruleSuggestedIds.value = []
  remainingAfterRules.value = 0
  try {
    const result: any = await apiClient.post('/api/bank/match')
    aiSuggestions.value = result.suggestions || []
    ruleSuggestedIds.value = aiSuggestions.value.map(item => item.statementId)
    remainingAfterRules.value = Number(result.unmatched || 0)
    if (aiSuggestions.value.length === 0) {
      message.info(remainingAfterRules.value > 0 ? '规则未找到匹配，可点击「AI 分析剩余流水」继续处理' : '暂无未匹配流水')
    } else {
      message.success(`已找到 ${aiSuggestions.value.length} 条可匹配流水，请逐条核对确认`)
    }
  } catch {
    message.error('生成匹配候选失败')
  } finally {
    matching.value = false
  }
}
async function handleAiSuggest() {
  aiLoading.value = true
  showAiPanel.value = true
  try {
    const result: any = await apiClient.post('/api/bank/ai-suggest', {}, { timeout: 60000 })
    const existingIds = new Set([
      ...ruleSuggestedIds.value,
      ...aiSuggestions.value.map(item => item.statementId),
    ])
    const newSuggestions = (result.suggestions || []).filter((item: any) => !existingIds.has(item.statementId))
    aiSuggestions.value = [...aiSuggestions.value, ...newSuggestions]
    remainingAfterRules.value = 0
    if (newSuggestions.length === 0) {
      message.info('暂无需要处理的 AI 建议')
    } else {
      message.success(`AI 已补充 ${newSuggestions.length} 条建议，请逐条核对确认`)
    }
  } catch {
    message.error('AI 建议生成失败')
  } finally {
    aiLoading.value = false
  }
}

async function confirmSuggestion(
  suggestion: any,
  forceSettleOpening = false,
  options?: { reimbursementPayer?: string }
) {
  const payroll = suggestion.payrollSettlement
  if (!forceSettleOpening && payroll?.mode === 'opening_balance_missing') {
    router.push('/mine/opening')
    return
  }
  if (!forceSettleOpening && payroll?.mode === 'salary_module_required') {
    router.push({
      path: '/mine/salary',
      query: payroll.suggestedMonth ? { month: payroll.suggestedMonth } : {},
    })
    return
  }
  if (isRdSuggestion(suggestion)) {
    const selection = rdSelectionFor(suggestion.statementId)
    if (!selection.projectId || !selection.expenseType) {
      message.warning('研发费用必须选择研发项目和费用类别')
      return
    }
  }
  aiConfirming.value[suggestion.statementId] = true
  try {
    if (payroll?.mode === 'salary_sheet') {
      const res: any = payroll.kind === 'salary'
        ? await applySalaryPayment(payroll.month, suggestion.statementId)
        : await applyPayrollLiability(payroll.month, payroll.kind, suggestion.statementId)
      message.success(res?.linked ? `${payroll.month} ${payroll.label || '工资'}已核销` : `${payroll.label || '工资'}已确认，请检查流水关联`)
      aiSuggestions.value = aiSuggestions.value.filter(s => s.statementId !== suggestion.statementId)
      await loadStatements()
      return
    }

    const payload: any = {
      statement_id: suggestion.statementId,
      type: suggestion.type,
    }
    if (suggestion.type === 'match') {
      payload.matched_entry_id = suggestion.matchedEntryId
      payload.matched_entry_ids = suggestion.matchedEntryIds || [suggestion.matchedEntryId]
      if (options?.reimbursementPayer) {
        payload.reimbursementPayer = options.reimbursementPayer
      } else {
        payload.auto_post = true
      }
    } else {
      const rdSelection = rdSelectionFor(suggestion.statementId)
      if (forceSettleOpening || payroll?.mode === 'opening_balance') {
        payload.settleOpeningPayroll = true
      }
      payload.new_entry = {
        ...suggestion.suggestedEntry,
        occurDate: suggestion.statement.date?.slice(0, 10),
        amount: Math.abs(suggestion.statement.amount),
        ...(isRdSuggestion(suggestion) ? {
          rdProjectId: rdSelection.projectId,
          rdExpenseType: rdSelection.expenseType,
          rdOriginalAmount: Math.abs(suggestion.statement.amount),
          rdAllocationMethod: 'direct',
        } : {}),
      }
    }
    const result: any = await apiClient.post('/api/bank/confirm-suggestion', payload)
    message.success(result.settledOpeningPayroll
      ? '已冲销期初应付余额并完成对账'
      : result.settlementStatus && result.settlementStatus !== 'posted'
        ? '已关联原收款与手续费，结算凭证待过账，请前往凭证页处理'
        : '确认成功')
    aiSuggestions.value = aiSuggestions.value.filter(s => s.statementId !== suggestion.statementId)
    await loadStatements()
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    const code = detail?.code
    if (code === 'SALARY_REQUIRES_PAYROLL' || code === 'PAYROLL_LIABILITY_REQUIRES_PAYROLL' || code === 'PAYROLL_TAX_REQUIRES_PAYROLL') {
      const opening = detail?.openingPayroll
      if (opening?.canSettle) {
        dialog.info({
          title: '可冲销开账前期初应付',
          content: `该笔流水属于建账日（${opening.bookStartDate}）前遗留应付，「${opening.accountCode} ${opening.accountName}」期初可用贷方余额 ¥${opening.availableOpeningCredit}，是否直接冲销期初负债并完成对账？`,
          positiveText: '立即冲销期初应付',
          negativeText: '取消',
          onPositiveClick: () => confirmSuggestion(suggestion, true),
        })
      } else if (opening?.eligible) {
        dialog.warning({
          title: '期初应付余额不足',
          content: detail?.message || '请先到「期初设置」补录应付职工薪酬贷方期初余额。',
          positiveText: '前往期初设置补录',
          negativeText: '关闭',
          onPositiveClick: () => { router.push('/mine/opening') },
        })
      } else {
        dialog.warning({
          title: '需通过工资模块核销',
          content: detail?.message || '工资及社保公积金扣款需在工资模块先计提并按流水核销。',
          positiveText: '前往工资模块',
          negativeText: '关闭',
          onPositiveClick: () => { router.push('/mine/salary') },
        })
      }
    } else {
      message.error(accountingErrorText(err) || '确认失败')
    }
  } finally {
    delete aiConfirming.value[suggestion.statementId]
  }
}

function dismissSuggestion(statementId: string) {
  aiSuggestions.value = aiSuggestions.value.filter(s => s.statementId !== statementId)
  delete rdSelections.value[statementId]
}

function getConfidenceType(conf: number): 'success' | 'warning' | 'error' {
  if (conf >= 0.8) return 'success'
  if (conf >= 0.5) return 'warning'
  return 'error'
}

function getConfidenceLabel(conf: number): string {
  if (conf >= 0.8) return '高'
  if (conf >= 0.5) return '中'
  return '低'
}

// 邮件收件箱「导入」：只接受流水类附件（卡片已按 kinds 过滤，这里是最后防线）
function handleInboxImport({ kind, file }: { kind: string; file: File }) {
  if (kind !== 'bank') {
    message.warning('这份附件不是银行流水，请在月度结账向导里导入')
    return
  }
  handleFile(file)
}

function handleFile(file: File) {
  excelFileName.value = file.name
  const reader = new FileReader()
  reader.onload = (ev) => {
    try {
      const data = new Uint8Array(ev.target?.result as ArrayBuffer)
      const json = parseBankWorkbookData(data)
      if (json.length === 0) {
        message.warning('流水文件为空')
        return
      }
      excelColumns.value = Object.keys(json[0] as any)
      excelRows.value = json as any[]
      columnMapping.value = detectBankColumnMapping(excelColumns.value)
      advancedPanels.value = []
      excelStep.value = 'preview'
    } catch {
      message.error('解析文件失败')
    }
  }
  reader.readAsArrayBuffer(file)
}

async function handleExcelImport() {
  const { rows: importable, skippedInvalidDate } = importPreview.value
  if (importable.length === 0) {
    message.error('未在文件中解析到带有效交易日期的流水行')
    return
  }

  excelImporting.value = true
  try {
    const result: any = await apiClient.post('/api/bank/import-json', {
      rows: importable,
      statement_id: excelFileName.value,
    })
    const parts = [`导入成功：${result.imported} 条`]
    if (result.skipped > 0) parts.push(`自动过滤 ${result.skipped} 条重复流水`)
    if (skippedInvalidDate > 0) parts.push(`跳过 ${skippedInvalidDate} 条无有效日期`)
    if (result.errors > 0) parts.push(`${result.errors} 条导入失败`)
    message.success(parts.join('，'))
    showExcelImport.value = false
    excelStep.value = 'upload'
    excelRows.value = []
    await loadStatements()
  } catch {
    message.error('导入失败')
  } finally {
    excelImporting.value = false
  }
}

async function handleDeleteStatement(id: string) {
  loading.value = true
  try {
    await apiClient.delete(`/api/bank/statements/${id}`)
    message.success('流水删除成功！')
    checkedRowKeys.value = checkedRowKeys.value.filter(key => key !== id)
    await loadStatements()
  } catch (err) {
    message.error(accountingErrorText(err, '删除流水失败，请重试'))
  } finally {
    loading.value = false
  }
}

function confirmBatchDeleteStatements() {
  const ids = checkedRowKeys.value.map(String)
  if (ids.length === 0) return
  confirmDelete({
    content: `确定删除选中的 ${ids.length} 条银行流水吗？已建立的匹配关系会一并解除，此操作不可撤销。`,
    onConfirm: () => handleBatchDeleteStatements(ids),
  })
}

async function handleBatchDeleteStatements(ids: string[]) {
  batchDeleting.value = true
  loading.value = true
  try {
    const result: any = await apiClient.post('/api/bank/statements/batch-delete', { ids })
    message.success(result.message || `已删除 ${result.deleted} 条流水`)
    checkedRowKeys.value = []
    await loadStatements()
  } catch (err) {
    message.error(accountingErrorText(err, '批量删除失败，请重试'))
  } finally {
    batchDeleting.value = false
    loading.value = false
  }
}

function openExcelImport() {
  excelStep.value = 'upload'
  excelRows.value = []
  excelColumns.value = []
  excelFileName.value = ''
  columnMapping.value = {}
  advancedPanels.value = []
  showExcelImport.value = true
}

onMounted(() => Promise.all([loadStatements(), loadRdOptions()]))
// 切回标签时静默重拉流水：凭证那边冲销凭证会改动匹配状态
usePageRefresh(loadStatements)
</script>

<template>
  <div class="bank-page">
    <BossTrackNav />
    <div class="page-header">
      <div class="header-left">
        <div class="title-section">
          <h2>{{ viewStore.isBoss ? '公户流水与对账' : '银行对账' }}</h2>
          <p class="subtitle">{{ viewStore.isBoss ? '告别传统代账“只抄发票不对流水”。把每一笔公户流水 100% 勾稽配平，你的报表才具备真实的授信与尽调含金量' : '核对银行流水并 100% 匹配系统记账凭证，确保账实相符、随时经得起审计与尽调核查' }}</p>
        </div>
      </div>
      <div class="header-right">
        <NSpace>
          <NButton size="small" @click="showCategoryRules = true">分类规则</NButton>
          <NButton size="small" :loading="matching" @click="handleMatch">智能对账</NButton>
          <NButton size="small" @click="openExcelImport">导入文件</NButton>
        </NSpace>
      </div>
    </div>

    <OpeningGateAlert v-if="openingBlocked" />
    <NAlert
      v-if="bankReceiptResult"
      :type="bankReceiptResult.summary.parkedCount > 0 ? 'warning' : 'success'"
      closable
      class="mb-4"
      @close="bankReceiptResult = null"
    >
      <div class="receipt-alert-row">
        <span>
          银行回单归档：拆出 {{ bankReceiptResult.summary.receiptTotal }} 笔（已挂流水
          {{ bankReceiptResult.summary.matchedCount }} · 待挂 {{ bankReceiptResult.summary.parkedCount }}）
          <template v-if="bankReceiptResult.summary.statementPdfCount > 0">
            · 标准对账单 {{ bankReceiptResult.summary.statementPdfCount }} 份
          </template>
          <template v-if="bankReceiptResult.summary.duplicateCount > 0">
            · 重复跳过 {{ bankReceiptResult.summary.duplicateCount }}
          </template>
        </span>
        <NButton
          v-if="bankReceiptResult.summary.parkedCount > 0"
          size="tiny"
          quaternary
          type="primary"
          :loading="bankReceiptRelinking"
          @click="handleRelinkBankReceipts"
        >
          重新匹配流水
        </NButton>
      </div>
    </NAlert>
    <NAlert
      v-else-if="parkedReceipts.length > 0"
      type="warning"
      class="mb-4"
    >
      <div class="receipt-alert-row">
        <span>
          当前账套有 <strong>{{ parkedReceipts.length }}</strong> 笔待挂银行回单（尚未导入对应月份流水，或可在下方列表点「补绑」手动挂接）
        </span>
        <NButton
          size="tiny"
          quaternary
          type="primary"
          :loading="bankReceiptRelinking"
          @click="handleRelinkBankReceipts"
        >
          重新匹配流水
        </NButton>
      </div>
    </NAlert>

    <NSpin :show="loading">
      <section class="reconciliation-summary mb-4" aria-labelledby="reconciliation-title">
        <div class="reconciliation-heading">
          <div>
            <h3 id="reconciliation-title">余额核对</h3>
            <p>核对银行流水连续性，并与账本中的银行存款余额比较</p>
          </div>
          <NSelect
            v-model:value="month"
            :options="monthOptions"
            size="small"
            class="month-select"
            aria-label="核对月份"
            @update:value="loadDiff"
          />
        </div>

        <template v-if="balanceSummary?.has_data">
          <div class="balance-equation">
            <div class="balance-term">
              <span>期初余额</span>
              <strong>{{ formatMoney(balanceSummary.opening_balance) }}</strong>
            </div>
            <span class="operator">+</span>
            <div class="balance-term income-term">
              <span>本期收入</span>
              <strong>{{ formatMoney(balanceSummary.income_total) }}</strong>
            </div>
            <span class="operator">−</span>
            <div class="balance-term expense-term">
              <span>本期支出</span>
              <strong>{{ formatMoney(balanceSummary.expense_total) }}</strong>
            </div>
            <span class="operator">=</span>
            <div class="balance-term closing-term">
              <span>计算期末余额</span>
              <strong>{{ formatMoney(balanceSummary.calculated_closing_balance) }}</strong>
            </div>
          </div>

          <div class="reconciliation-result">
            <div
              class="result-status"
              :class="balanceSummary.fully_reconciled ? 'is-complete' : 'needs-attention'"
            >
              <i :class="balanceSummary.fully_reconciled ? 'ri-checkbox-circle-line' : 'ri-information-line'" />
              <span>{{ reconciliationStatusText }}</span>
            </div>
            <div class="result-details">
              <span>银行期末余额 {{ formatMoney(balanceSummary.bank_closing_balance) }}</span>
              <span>流水连续差额 {{ formatMoney(balanceSummary.flow_difference) }}</span>
              <span>账本银行存款 {{ formatMoney(balanceSummary.book_bank_balance) }}</span>
              <span>账银差额 {{ formatMoney(balanceSummary.book_difference) }}</span>
            </div>
            <NButton
              size="small"
              :type="unmatchedCount > 0 ? 'warning' : 'default'"
              secondary
              @click="showUnmatched = !showUnmatched"
            >
              {{ showUnmatched ? '收起未匹配项' : `查看未匹配项 (${unmatchedCount})` }}
            </NButton>
          </div>
        </template>
        <SoloEmpty
          v-else
          title="这个月还没有银行流水"
          description="上传网银导出的 CSV 或 Excel，系统会自动配对已记账的收支。"
          icon="ri-bank-line"
        >
          <template #action>
            <div class="empty-actions">
              <NButton type="primary" size="small" @click="openExcelImport">导入文件</NButton>
              <GuideTrigger guide="bank-statement" label="不知道去哪下载？" />
            </div>
          </template>
        </SoloEmpty>
      </section>

      <section
        v-if="matchedEffectIssues.length > 0"
        class="effect-issues mb-4"
        aria-labelledby="effect-issues-title"
      >
        <div class="effect-issues-heading">
          <div class="effect-issues-title-wrap">
            <i class="ri-error-warning-line" />
            <div>
              <h3 id="effect-issues-title">已匹配，但银行存款影响不一致 ({{ matchedEffectIssues.length }})</h3>
              <p>这些流水已经关联账目，但凭证对 1002 银行存款的影响没有还原实际收付款。</p>
            </div>
          </div>
          <span class="effect-issues-total">
            合计差额 {{ formatMoney(matchedEffectIssues.reduce((sum, item) => sum + Number(item.difference || 0), 0)) }}
          </span>
        </div>
        <div class="effect-issues-table-wrap">
          <table class="effect-issues-table">
            <thead>
              <tr>
                <th>日期 / 流水</th>
                <th class="numeric">流水金额</th>
                <th class="numeric">银行存款影响</th>
                <th class="numeric">差额</th>
                <th>问题说明</th>
                <th class="action-column">处理</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="issue in matchedEffectIssues" :key="issue.statementId">
                <td>
                  <span class="issue-date">{{ issue.date }}</span>
                  <span class="issue-description">{{ issue.description }}</span>
                </td>
                <td class="numeric">{{ formatMoney(issue.statementAmount) }}</td>
                <td class="numeric">{{ formatMoney(issue.bankEffect) }}</td>
                <td class="numeric issue-difference">{{ formatMoney(issue.difference) }}</td>
                <td>
                  <span class="issue-reason">{{ issue.reason }}</span>
                  <span v-if="issue.entryTitles?.length" class="issue-entries">
                    已匹配：{{ issue.entryTitles.join('、') }}
                  </span>
                </td>
                <td class="action-column">
                  <NButton
                    v-if="issue.repairable"
                    size="tiny"
                    type="warning"
                    secondary
                    :loading="repairingEffects[issue.statementId]"
                    @click="confirmEffectRepair(issue)"
                  >{{ issue.repairLabel }}</NButton>
                  <NButton v-else size="tiny" quaternary @click="openIssueEntry(issue)">
                    查看账目
                  </NButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div v-if="showUnmatched" class="unmatched-section mb-4">
        <div class="unmatched-column">
          <h3 class="section-title">银行未入账 ({{ diffReport?.bank_only_count || 0 }})</h3>
          <table class="diff-table">
            <thead><tr><th>日期</th><th>摘要</th><th>金额</th></tr></thead>
            <tbody>
              <tr v-for="(item, idx) in diffReport?.bank_only || []" :key="idx">
                <td>{{ item.date }}</td><td>{{ item.description }}</td>
                <td class="col-amount">{{ formatMoney(item.amount) }}</td>
              </tr>
              <tr v-if="(diffReport?.bank_only?.length || 0) === 0">
                <td colspan="3" class="empty">没有未入账流水</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="unmatched-column">
          <h3 class="section-title">账本无流水 ({{ diffReport?.book_only_count || 0 }})</h3>
          <table class="diff-table">
            <thead><tr><th>日期</th><th>摘要</th><th>金额</th></tr></thead>
            <tbody>
              <tr v-for="(item, idx) in diffReport?.book_only || []" :key="idx">
                <td>{{ item.date }}</td><td>{{ item.title }}</td>
                <td class="col-amount">{{ formatMoney(item.amount) }}</td>
              </tr>
              <tr v-if="(diffReport?.book_only?.length || 0) === 0">
                <td colspan="3" class="empty">没有无流水账目</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <NCard :bordered="false" class="table-card">
        <div class="statement-toolbar">
          <NInput v-model:value="searchKeyword" clearable placeholder="搜索摘要、金额、余额">
            <template #prefix><i class="ri-search-line" /></template>
          </NInput>
          <NDatePicker
            v-model:value="dateRange"
            type="daterange"
            clearable
            start-placeholder="开始日期"
            end-placeholder="结束日期"
          />
          <NSelect v-model:value="amountDirection" :options="directionOptions" />
          <NSelect v-model:value="matchStatus" :options="matchStatusOptions" />
          <NButton v-if="hasActiveFilters" quaternary size="small" @click="resetStatementFilters">
            清除筛选
          </NButton>
          <!-- 批量删除只在真的选了行之后才出现：平时它是个红色按钮，却没有可作用的对象 -->
          <NButton
            v-if="checkedRowKeys.length > 0"
            size="small"
            type="error"
            ghost
            :loading="batchDeleting"
            @click="confirmBatchDeleteStatements"
          >
            删除选中 ({{ checkedRowKeys.length }})
          </NButton>
        </div>
        <div class="table-result-meta">
          共 {{ statements.length }} 条，当前显示 {{ filteredStatements.length }} 条<template v-if="checkedRowKeys.length">，已选 {{ checkedRowKeys.length }} 条</template>
        </div>
        <NDataTable
          :columns="statementColumns"
          :data="filteredStatements"
          :row-key="(row: any) => row.id"
          :checked-row-keys="checkedRowKeys"
          @update:checked-row-keys="(keys: Array<string | number>) => checkedRowKeys = keys"
          :bordered="false"
          :single-line="true"
          :pagination="tablePagination"
          :scroll-x="1060"
          size="small"
          @update:page="tablePage = $event"
          @update:page-size="tablePageSize = $event; tablePage = 1"
        >
          <template #empty>
            <SoloEmpty
              variant="hint"
              :title="statements.length === 0 ? '暂无流水' : '没有符合当前条件的流水'"
              :description="
                statements.length === 0
                  ? '导入后可按月核对；没有匹配的流水会出现在待办里。'
                  : ''
              "
            >
              <template v-if="statements.length === 0" #action>
                <NButton size="small" @click="openExcelImport">导入</NButton>
              </template>
            </SoloEmpty>
          </template>
        </NDataTable>
      </NCard>
    </NSpin>

    <!-- Matching suggestions panel -->
    <SoloDrawer v-model:show="showAiPanel" title="流水匹配与候选建议" :width="720">
      <NSpin :show="aiLoading">
        <div v-if="remainingAfterRules > 0" class="ai-next-step">
          <div>
            <strong>仍有 {{ remainingAfterRules }} 条流水未找到规则匹配</strong>
            <span>AI 将只用于分析剩余流水，并可能建议新建记账草稿。</span>
          </div>
          <NButton type="primary" secondary size="small" :loading="aiLoading" @click="handleAiSuggest">
            <template #icon><i class="ri-search-eye-line" /></template>
            AI 分析剩余流水
          </NButton>
        </div>
        <SoloEmpty
          v-if="aiSuggestions.length === 0 && !aiLoading"
          variant="hint"
          :title="remainingAfterRules > 0 ? '规则未找到候选' : '没有待确认的候选'"
          :description="
            remainingAfterRules > 0
              ? '可继续用 AI 分析剩余流水。'
              : '规则匹配已覆盖大部分流水，剩下的可让 AI 逐条分析。'
          "
        />
        <div v-else class="ai-suggestions">
          <div v-for="s in aiSuggestions" :key="s.statementId" class="ai-suggestion-card">
            <div class="suggestion-header">
              <div class="statement-info">
                <span class="stmt-date">{{ s.statement.date?.slice(0, 10) }}</span>
                <span class="stmt-desc">{{ s.statement.description }}</span>
                <span class="stmt-amount" :class="s.statement.amount >= 0 ? 'income' : 'expense'">
                  {{ s.statement.amount >= 0 ? '+' : '' }}{{ s.statement.amount.toFixed(2) }}
                </span>
              </div>
              <NTag :type="getConfidenceType(s.confidence)" size="small" :bordered="false">
                置信度：{{ getConfidenceLabel(s.confidence) }}
              </NTag>
            </div>
            <div class="suggestion-body">
              <div class="suggestion-arrow">
                <i class="ri-arrow-down-line"></i>
                <span>{{ s.type === 'match' ? '匹配已有账目' : '建议新建草稿' }}</span>
              </div>
              <div v-if="s.type === 'match' && s.matchedEntry" class="matched-entry">
                <span class="entry-title">{{ s.matchedEntry.title }}</span>
                <span class="entry-cat">{{ s.matchedEntry.category }}</span>
                <span class="entry-amount">¥{{ s.matchedEntry.amount?.toFixed(2) }}</span>
              </div>
              <div v-else-if="s.type === 'new' && s.suggestedEntry" class="new-entry">
                <span class="entry-title">{{ s.suggestedEntry.title }}</span>
                <span class="entry-cat">{{ s.suggestedEntry.category }} · {{ s.suggestedEntry.accountName }}</span>
              </div>
              <div v-if="isRdSuggestion(s)" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px;">
                <NSelect
                  v-model:value="rdSelectionFor(s.statementId).projectId"
                  :options="rdProjects.map(project => ({ label: `${project.code} · ${project.name}`, value: project.id }))"
                  placeholder="选择研发项目"
                  size="small"
                  filterable
                />
                <NSelect
                  v-model:value="rdSelectionFor(s.statementId).expenseType"
                  :options="rdExpenseTypes.map(item => ({ label: item.label, value: item.value }))"
                  placeholder="选择费用类别"
                  size="small"
                />
              </div>
              <div class="suggestion-reason">{{ s.reason }}</div>
            </div>
            <div class="suggestion-actions">
              <NButton
                v-if="s.invoiceReimbursement"
                size="small"
                type="primary"
                :loading="aiConfirming[s.statementId]"
                @click="confirmSuggestion(s, false, { reimbursementPayer: s.invoiceReimbursement.suggestedPayer })"
              >
                按垫付报销入账（两步）
              </NButton>
              <NButton
                v-if="s.invoiceReimbursement"
                size="small"
                tertiary
                :loading="aiConfirming[s.statementId]"
                @click="confirmSuggestion(s)"
              >
                按公户直付
              </NButton>
              <NButton v-else size="small" type="primary" :loading="aiConfirming[s.statementId]" @click="confirmSuggestion(s)">
                {{
                  s.payrollSettlement?.mode === 'salary_sheet'
                    ? '核销工资/社保'
                    : s.payrollSettlement?.mode === 'opening_balance'
                      ? '冲销期初应付'
                      : s.payrollSettlement?.mode === 'opening_balance_missing'
                        ? '去期初设置补录'
                        : s.payrollSettlement?.mode === 'salary_module_required'
                          ? '去工资表核对'
                          : '确认'
                }}
              </NButton>
              <NButton size="small" quaternary @click="dismissSuggestion(s.statementId)">忽略</NButton>
            </div>
          </div>
        </div>
      </NSpin>
    </SoloDrawer>

    <BankCategoryRulesDrawer v-model:show="showCategoryRules" />

    <!-- 统一导入弹窗（流水表格走预览导入；回单 ZIP/PDF/图片自动拆单挂流水） -->
    <NModal v-model:show="showExcelImport" title="导入银行流水 / 银行回单" preset="card" style="width: min(760px, 92vw);">
      <template v-if="excelStep === 'upload'">
        <OpeningGateAlert v-if="openingBlocked" />
        <template v-else>
          <MailInboxCard collapsible :kinds="['bank']" @import="handleInboxImport" @error="message.error($event)" />
          <FileDropzone
            :accept="`.xlsx,.xls,.csv,${BANK_RECEIPT_ACCEPT}`"
            :multiple="true"
            :loading="bankReceiptUploading"
            icon="ri-bank-line"
            title="点击或拖拽银行流水 / 回单文件上传"
            subtitle="支持 .xlsx / .xls / .csv（解析预览后导入流水）与 .zip / .pdf / 图片（自动拆单并挂接已有流水）"
            @files="handleUnifiedBankUpload"
            @error="message.error($event)"
          />
        </template>
        <div class="upload-tip">
          <h4>导入说明</h4>
          <ul>
            <li><strong>银行流水明细（.xlsx / .xls / .csv）：</strong>使用网银「交易明细 / 流水」导出，上传后先预览解析结果，确认后再写入流水，重复流水会自动跳过。</li>
            <li><strong>银行回单附件（.zip / .pdf / 图片）：</strong>上传网银导出的回单压缩包或单笔回单，系统自动拆单并匹配挂接到对应银行流水，不重复生成流水。</li>
          </ul>
          <GuideTrigger guide="bank-statement" label="不知道去哪下载？" hint="手机银行导出步骤" />
        </div>
      </template>

      <template v-if="excelStep === 'preview'">
        <p class="excel-info">文件：{{ excelFileName }} · 共 {{ excelRows.length }} 行</p>

        <div class="detect-row">
          <button
            v-for="row in keyFieldRows"
            :key="row.field"
            type="button"
            class="detect-chip"
            :class="{
              'is-missing': row.required && !row.detected,
              'is-optional': !row.required && !row.detected,
            }"
            :title="`来源列：${row.sourceText}`"
            @click="advancedPanels = ['mapping']"
          >
            <span class="detect-mark">{{ row.detected ? '✓' : row.required ? '!' : '–' }}</span>
            <span class="detect-label">{{ row.label }}</span>
            <span class="detect-source">{{ row.sourceText }}</span>
          </button>
        </div>

        <p v-if="unrecognizedRequired.length" class="preview-warning">
          没有识别到{{ unrecognizedRequired.map(r => r.label).join('、') }}，请展开下方「字段来源不对？」指定对应列。
        </p>
        <p v-else-if="importPreview.skippedInvalidDate > 0" class="preview-warning">
          {{ importPreview.skippedInvalidDate }} 行没有可识别的交易日，已跳过。
        </p>

        <template v-if="previewRows.length">
          <div class="preview-head">
            <h4 class="section-title">解析预览</h4>
            <span class="preview-count">
              前 {{ previewRows.length }} 行 · 共 {{ importPreview.rows.length }} 条可导入
            </span>
          </div>
          <NDataTable
            :columns="importPreviewColumns"
            :data="previewRows"
            :bordered="false"
            :single-line="true"
            size="small"
            :max-height="260"
          />
          <p class="preview-footnote">导入后每笔流水的摘要、金额、余额将与上表一致，重复流水会自动跳过。</p>
        </template>
        <SoloEmpty
          v-else
          title="没有解析出可导入的流水"
          :description="unrecognizedRequired.length
            ? '展开下方「字段来源不对？」指定缺失字段对应的列，这里会立即显示解析结果。'
            : '文件中的交易日无法识别，已全部跳过。可展开下方字段来源确认日期列。'"
          icon="ri-file-warning-line"
        />

        <NCollapse v-model:expanded-names="advancedPanels" class="advanced-mapping">
          <NCollapseItem name="mapping" title="字段来源不对？调整列映射">
            <div class="key-field-list">
              <div v-for="row in keyFieldRows" :key="row.field" class="key-field-row">
                <span class="key-field-label">
                  {{ row.label }}<em v-if="row.required">*</em>
                </span>
                <span class="mapping-arrow">←</span>
                <NSelect
                  v-if="row.editable"
                  size="small"
                  style="width: 260px;"
                  :value="row.columns[0] || ''"
                  :options="[
                    { label: '忽略', value: '' },
                    ...excelColumns.map(col => ({ label: col, value: col })),
                  ]"
                  @update:value="(v: string) => setFieldSource(row.field, v)"
                />
                <span v-else class="key-field-static">
                  {{ row.splitMode ? '收入 / 支出分两列，见下方全部列' : `${row.columns.length} 列拼接，见下方全部列` }}
                </span>
              </div>
            </div>

            <p class="mapping-hint">其余列无需处理：附言、对方户名会自动并入摘要；开户行、账号类列自动忽略。</p>

            <div class="mapping-grid">
              <div v-for="col in excelColumns" :key="col" class="mapping-row">
                <span class="mapping-col">{{ col }}</span>
                <span class="mapping-arrow">→</span>
                <!-- 描述增强列自动并入描述；开户行/账号类列锁死为忽略，防止误映射成交易对手 -->
                <NTag
                  v-if="isBankCounterpartySourceBlocked(col)"
                  size="small"
                  type="warning"
                  :bordered="false"
                  class="mapping-auto-tag"
                >
                  已忽略 · 账户/开户字段
                </NTag>
                <NTag
                  v-else-if="columnMapping[col] === '' && bankColumnDescriptionRole(col)"
                  size="small"
                  type="info"
                  :bordered="false"
                  class="mapping-auto-tag"
                >
                  自动并入描述 · {{ bankColumnDescriptionRole(col) }}
                </NTag>
                <NSelect
                  v-else
                  :value="columnMapping[col]"
                  @update:value="(v: string) => columnMapping[col] = v"
                  :options="[
                    { label: '忽略', value: '' },
                    { label: '日期', value: 'transaction_date' },
                    { label: '交易时间', value: 'transaction_time' },
                    { label: '交易流水号', value: 'transaction_reference' },
                    { label: '摘要/描述', value: 'description' },
                    { label: '对方户名', value: 'counterparty_name' },
                    { label: '收款人户名', value: 'payee_name' },
                    { label: '付款人户名', value: 'payer_name' },
                    { label: '附言/备注', value: 'comment' },
                    { label: '金额(收支单列)', value: 'amount' },
                    { label: '存入(收入)金额', value: 'income_amount' },
                    { label: '支取(支出)金额', value: 'expense_amount' },
                    { label: '余额', value: 'balance' },
                  ]"
                  style="width: 160px;"
                  size="small"
                />
              </div>
            </div>

            <h4 class="section-title" style="margin-top: 16px;">原始文件预览（前 5 行）</h4>
            <NDataTable
              :columns="excelColumns.map(c => ({ title: c, key: c }))"
              :data="excelRows.slice(0, 5)"
              :bordered="false"
              :single-line="true"
              size="small"
              :max-height="220"
            />
          </NCollapseItem>
        </NCollapse>
      </template>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showExcelImport = false">取消</NButton>
          <NButton
            v-if="excelStep === 'preview'"
            type="primary"
            :loading="excelImporting"
            :disabled="importPreview.rows.length === 0"
            @click="handleExcelImport"
          >
            确认导入 ({{ importPreview.rows.length }} 条)
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 银行回单原件预览弹窗 -->
    <NModal
      v-model:show="showReceiptPreviewModal"
      preset="card"
      title="银行回单"
      style="width: min(880px, 94vw);"
      @after-leave="revokeReceiptPreview"
    >
      <div v-if="previewReceiptList.length > 1" class="receipt-modal-tabs">
        <NButton
          v-for="(item, idx) in previewReceiptList"
          :key="item.id"
          size="tiny"
          :type="idx === previewActiveIndex ? 'primary' : 'default'"
          :secondary="idx !== previewActiveIndex"
          @click="selectPreviewReceipt(idx)"
        >
          回单 {{ idx + 1 }}
        </NButton>
      </div>

      <NSpin :show="receiptPreviewLoading">
        <div class="receipt-preview-stage">
          <img
            v-if="receiptPreviewUrl"
            :src="receiptPreviewUrl"
            :alt="activePreviewReceipt?.fileName || '银行回单'"
            class="receipt-preview-img"
          />
          <div v-else class="receipt-preview-empty">
            {{ receiptPreviewFailed ? '回单图片加载失败，可点击下方「下载原件」' : '正在加载回单原件…' }}
          </div>
        </div>
      </NSpin>

      <template #footer>
        <NSpace justify="space-between" align="center" style="width: 100%;">
          <NButton
            v-if="previewStatement?.matched_entry_id"
            size="small"
            quaternary
            type="primary"
            @click="showReceiptPreviewModal = false; router.push(`/vouchers/${previewStatement.matched_entry_id}`)"
          >
            查看关联凭证
          </NButton>
          <span v-else />
          <NSpace>
            <NButton
              v-if="activePreviewReceipt"
              size="small"
              secondary
              @click="handleDownloadReceipt(activePreviewReceipt)"
            >
              下载原件
            </NButton>
            <NButton size="small" @click="showReceiptPreviewModal = false">关闭</NButton>
          </NSpace>
        </NSpace>
      </template>
    </NModal>

    <!-- 手动补绑待挂回单弹窗 -->
    <NModal
      v-model:show="showBindReceiptModal"
      preset="card"
      title="手动补绑待挂银行回单"
      style="width: min(560px, 92vw);"
    >
      <div v-if="bindTargetStatement" class="bind-stmt-box">
        <div><strong>流水日期：</strong>{{ String(bindTargetStatement.transaction_date || '').slice(0, 19) }}</div>
        <div><strong>流水金额：</strong>{{ formatMoney(bindTargetStatement.amount) }}</div>
        <div><strong>流水摘要：</strong>{{ bindTargetStatement.description || '—' }}</div>
      </div>
      <div class="bind-select-label">选择要绑定到本条流水的待挂回单：</div>
      <NSelect
        v-model:value="selectedBindReceiptId"
        :options="bindReceiptOptions"
        filterable
        placeholder="按日期、金额、对方户名或回单号搜索"
      />
      <template #footer>
        <NSpace justify="end">
          <NButton size="small" @click="showBindReceiptModal = false">取消</NButton>
          <NButton
            size="small"
            type="primary"
            :disabled="!selectedBindReceiptId"
            :loading="bindingReceipt"
            @click="confirmBindReceipt"
          >
            确认绑定
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
/* 行内删除：默认灰、hover 该行才转红。NDataTable 的行由子组件渲染，要穿透才选中 */
:deep(tr:hover) .row-delete,
:deep(tr:focus-within) .row-delete {
  color: var(--solo-expense, #bd3d3d);
}

.bank-page {
  max-width: var(--page-width);
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  margin-bottom: 16px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.header-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }
.table-card {
  border-radius: var(--solo-radius-lg);
}
.reconciliation-summary {
  padding: 18px 20px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}
.reconciliation-heading,
.reconciliation-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.reconciliation-heading h3 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 16px;
  font-weight: 650;
}
.reconciliation-heading p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.month-select {
  width: 124px;
  flex: 0 0 auto;
}
.balance-equation {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr auto 1.15fr;
  align-items: end;
  gap: 12px;
  margin: 20px 0 16px;
}
.balance-term {
  min-width: 0;
}
.balance-term span {
  display: block;
  margin-bottom: 5px;
  color: var(--solo-text-muted);
  font-size: 12px;
}
.balance-term strong {
  display: block;
  color: var(--solo-text-primary);
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.income-term strong { color: var(--solo-income); }
.expense-term strong { color: var(--solo-expense); }
.closing-term {
  padding-left: 12px;
  border-left: 1px solid var(--solo-border);
}
.operator {
  padding-bottom: 2px;
  color: var(--solo-text-muted);
  font-size: 18px;
}
.reconciliation-result {
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
.result-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
.result-status.is-complete { color: var(--solo-income); }
.result-status.needs-attention { color: var(--solo-warning); }
.result-details {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px 16px;
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.reconciliation-empty {
  padding: 28px 0 12px;
  color: var(--solo-text-muted);
  text-align: center;
}
.effect-issues {
  overflow: hidden;
  border: 1px solid var(--solo-warning);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}
.effect-issues-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  background: var(--solo-warning-bg);
}
.effect-issues-title-wrap {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 10px;
}
.effect-issues-title-wrap > i {
  margin-top: 2px;
  color: var(--solo-warning);
  font-size: 18px;
}
.effect-issues-heading h3 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 14px;
  font-weight: 650;
}
.effect-issues-heading p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.effect-issues-total {
  flex: 0 0 auto;
  color: var(--solo-warning);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.effect-issues-table-wrap { overflow-x: auto; }
.effect-issues-table {
  width: 100%;
  min-width: 780px;
  border-collapse: collapse;
  font-size: 12px;
}
.effect-issues-table th,
.effect-issues-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  vertical-align: middle;
}
.effect-issues-table th {
  color: var(--solo-text-secondary);
  background: var(--solo-bg-card);
  font-weight: 600;
  white-space: nowrap;
}
.effect-issues-table tbody tr:last-child td { border-bottom: 0; }
.effect-issues-table .numeric {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.effect-issues-table .action-column {
  width: 116px;
  text-align: right;
}
.issue-date,
.issue-description,
.issue-reason,
.issue-entries { display: block; }
.issue-date,
.issue-entries {
  color: var(--solo-text-muted);
  font-size: 11px;
}
.issue-description {
  max-width: 230px;
  margin-top: 2px;
  color: var(--solo-text-primary);
}
.issue-reason { color: var(--solo-text-primary); }
.issue-entries {
  max-width: 220px;
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.issue-difference {
  color: var(--solo-expense);
  font-weight: 600;
}
.unmatched-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-border);
}
.unmatched-column {
  min-width: 0;
  padding: 16px;
  background: var(--solo-bg-card);
}
.statement-toolbar {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(230px, 1.2fr) 118px 118px auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.table-result-meta {
  margin-bottom: 8px;
  color: var(--solo-text-muted);
  font-size: 12px;
}
.table-empty {
  padding: 30px 16px;
  color: var(--solo-text-muted);
}
.section-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
}
.mapping-hint {
  margin: -6px 0 10px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.mapping-auto-tag {
  width: 160px;
  justify-content: center;
  font-size: 12px;
}
.upload-tip {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
}
.upload-tip h4 {
  margin: 0 0 6px;
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 600;
}
.upload-tip ul {
  margin: 0;
  padding-left: 18px;
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
.excel-info {
  margin: 0 0 10px;
  color: var(--solo-text-secondary);
  font-size: 13px;
}

/* 识别结果：一眼确认五个关键字段是否对齐，点任意 chip 展开映射 */
.detect-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.detect-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  max-width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}
.detect-chip:hover {
  border-color: var(--solo-primary-light);
}
.detect-mark {
  color: var(--solo-income);
  font-weight: 600;
}
.detect-chip.is-missing {
  border-color: var(--solo-expense);
  background: var(--solo-expense-bg);
}
.detect-chip.is-missing .detect-mark {
  color: var(--solo-expense);
}
.detect-chip.is-optional .detect-mark {
  color: var(--solo-text-muted);
}
.detect-label {
  font-weight: 600;
}
.detect-source {
  min-width: 0;
  max-width: 240px;
  overflow: hidden;
  color: var(--solo-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-warning {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-left: 3px solid var(--solo-warning);
  border-radius: var(--solo-radius-sm);
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  font-size: 12px;
}
.preview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.preview-head .section-title {
  margin: 0;
}
.preview-count {
  color: var(--solo-text-muted);
  font-size: 12px;
}
.preview-footnote {
  margin: 8px 0 0;
  color: var(--solo-text-muted);
  font-size: 12px;
}
.advanced-mapping {
  margin-top: 16px;
}
.key-field-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.key-field-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.key-field-label {
  min-width: 76px;
  font-size: 13px;
}
.key-field-label em {
  margin-left: 2px;
  color: var(--solo-expense);
  font-style: normal;
}
.key-field-static {
  color: var(--solo-text-muted);
  font-size: 12px;
}
.mapping-grid {
  display: flex;
  max-height: 220px;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  padding-right: 4px;
}
.mapping-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mapping-col {
  width: 220px;
  overflow: hidden;
  color: var(--solo-text-secondary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mapping-arrow {
  flex-shrink: 0;
  color: var(--solo-text-muted);
}
.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.diff-table th {
  text-align: left;
  padding: 6px 8px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  border-bottom: 1px solid var(--solo-border-light);
}
.diff-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--solo-border-light);
}
.col-amount {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.empty {
  text-align: center;
  color: var(--solo-text-muted);
  padding: 24px;
}
.empty-state {
  text-align: center;
  color: var(--solo-text-muted);
  padding: 48px;
}

/* AI Suggestions */
.ai-next-step {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
}

@media (max-width: 900px) {
  .balance-equation {
    grid-template-columns: 1fr 1fr;
    gap: 14px 20px;
  }
  .operator { display: none; }
  .closing-term {
    padding-left: 0;
    border-left: 0;
  }
  .reconciliation-result {
    align-items: flex-start;
    flex-direction: column;
  }
  .unmatched-section { grid-template-columns: 1fr; }
  .statement-toolbar { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 600px) {
  .reconciliation-heading { align-items: flex-start; }
  .reconciliation-heading p { display: none; }
  .balance-equation { grid-template-columns: 1fr 1fr; }
  .balance-term strong { font-size: 16px; }
  .statement-toolbar { grid-template-columns: 1fr; }
  .effect-issues-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .key-field-row {
    flex-wrap: wrap;
  }
  .detect-source {
    max-width: 150px;
  }
  .mapping-col {
    width: 130px;
  }
}
.ai-next-step div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.ai-next-step strong {
  color: var(--solo-text-primary);
  font-size: 13px;
}
.ai-next-step span {
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.ai-suggestions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 60vh;
  overflow-y: auto;
}
.ai-suggestion-card {
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius);
  padding: 14px;
  background: var(--solo-bg-card);
  transition: border-color 0.2s;
}
.ai-suggestion-card:hover {
  border-color: var(--solo-primary-light);
}
.suggestion-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.statement-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.stmt-date {
  font-size: 12px;
  color: var(--solo-text-muted);
  flex-shrink: 0;
}
.stmt-desc {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stmt-amount {
  font-size: 14px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.stmt-amount.income {
  color: var(--solo-income);
}
.stmt-amount.expense {
  color: var(--solo-expense);
}
.suggestion-body {
  padding: 8px 0;
}
.suggestion-arrow {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-primary);
  margin-bottom: 8px;
}
.matched-entry, .new-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--solo-bg-page);
  border-radius: var(--solo-radius-sm);
  font-size: 13px;
}
.entry-title {
  font-weight: 500;
  color: var(--solo-text-primary);
}
.entry-cat {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.entry-amount {
  margin-left: auto;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.suggestion-reason {
  font-size: 12px;
  color: var(--solo-text-secondary);
  margin-top: 6px;
}
.suggestion-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--solo-border-light);
}
@media (max-width: 900px) {
  .statement-toolbar {
    grid-template-columns: minmax(200px, 1fr) minmax(220px, 1fr);
  }
}
@media (max-width: 640px) {
  .statement-toolbar {
    grid-template-columns: 1fr;
  }
  .ai-next-step {
    align-items: flex-start;
    flex-direction: column;
  }
}
.empty-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.receipt-alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.row-bind-receipt {
  color: var(--solo-text-muted);
}
.row-bind-receipt:hover {
  color: var(--solo-primary);
}
.receipt-modal-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}
.receipt-preview-stage {
  min-height: 240px;
  max-height: 68vh;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-page);
  padding: 10px;
  overflow: auto;
}
.receipt-preview-img {
  max-width: 100%;
  max-height: 64vh;
  object-fit: contain;
  display: block;
}
.receipt-preview-empty {
  font-size: 13px;
  color: var(--solo-text-muted);
}
.bind-stmt-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-page);
  font-size: 13px;
  color: var(--solo-text-primary);
}
.bind-select-label {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  margin-bottom: 6px;
}
</style>
