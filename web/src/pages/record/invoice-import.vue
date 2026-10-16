<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, h, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage, NSpin,
  NModal, NDataTable, NSelect, NInput, NCheckbox, NEmpty, NDatePicker
} from 'naive-ui'
import apiClient from '@/api/client'
import { importInvoices } from '@/api/invoice'
import { useEntryStore } from '@/stores/entry'
import * as XLSX from 'xlsx'
import { formatMoney } from '@/utils/format'
import { EXPENSE_CATEGORIES, INCOME_CATEGORIES } from '@/utils/constants'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const { confirmDelete } = useDeleteConfirm()

onMounted(async () => {
  await Promise.all([
    entryStore.activeEntries.length === 0 ? entryStore.fetchEntries() : Promise.resolve(),
    loadRdOptions(),
  ])
})

const importedInvoices = computed(() => {
  return entryStore.activeEntries.filter((e: any) => e.source === 'invoice_import' || !!e.invoiceNumber)
})

const invoiceSearch = ref('')
const invoiceDateRange = ref<[number, number] | null>(null)
const invoiceDirection = ref<'all' | 'income' | 'expense'>('all')
const invoiceMatchStatus = ref<'all' | 'reconciled' | 'pending' | 'archive'>('all')
const invoicePage = ref(1)
const invoicePageSize = ref(20)

const invoiceDirectionOptions = [
  { label: '全部收支', value: 'all' },
  { label: '销项（收入）', value: 'income' },
  { label: '进项（支出）', value: 'expense' },
]

const invoiceMatchStatusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '已对账', value: 'reconciled' },
  { label: '待对账', value: 'pending' },
  { label: '仅归档', value: 'archive' },
]

function invoiceDateKey(timestamp: number) {
  const date = new Date(timestamp)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function isArchiveInvoice(invoice: any) {
  return Boolean(invoice.is_archive_only || invoice.isArchiveOnly)
}

function isPostedInvoice(invoice: any) {
  return invoice.voucherStatus === 'posted'
}

function isReversedInvoice(invoice: any) {
  return invoice.voucherStatus === 'reversed'
}

const filteredInvoices = computed(() => {
  const keyword = invoiceSearch.value.trim().toLowerCase()
  const startDate = invoiceDateRange.value ? invoiceDateKey(invoiceDateRange.value[0]) : ''
  const endDate = invoiceDateRange.value ? invoiceDateKey(invoiceDateRange.value[1]) : ''

  return importedInvoices.value.filter((invoice: any) => {
    const date = String(invoice.occurDate || '').slice(0, 10)
    const archived = isArchiveInvoice(invoice)
    if (startDate && date < startDate) return false
    if (endDate && date > endDate) return false
    if (invoiceDirection.value !== 'all' && invoice.direction !== invoiceDirection.value) return false
    if (invoiceMatchStatus.value === 'reconciled' && (archived || invoice.billingStatus !== 'reconciled')) return false
    if (invoiceMatchStatus.value === 'pending' && (archived || invoice.billingStatus === 'reconciled')) return false
    if (invoiceMatchStatus.value === 'archive' && !archived) return false
    if (!keyword) return true

    return [invoice.title, invoice.invoiceNumber, invoice.category, invoice.payer, invoice.memo, date, invoice.amount]
      .some(value => String(value ?? '').toLowerCase().includes(keyword))
  })
})

const hasInvoiceFilters = computed(() => Boolean(
  invoiceSearch.value.trim()
  || invoiceDateRange.value
  || invoiceDirection.value !== 'all'
  || invoiceMatchStatus.value !== 'all'
))

const invoicePagination = computed(() => ({
  page: invoicePage.value,
  pageSize: invoicePageSize.value,
  showSizePicker: true,
  pageSizes: [20, 50, 100],
  itemCount: filteredInvoices.value.length,
}))

function resetInvoiceFilters() {
  invoiceSearch.value = ''
  invoiceDateRange.value = null
  invoiceDirection.value = 'all'
  invoiceMatchStatus.value = 'all'
}

watch([invoiceSearch, invoiceDateRange, invoiceDirection, invoiceMatchStatus], () => {
  invoicePage.value = 1
})

function goVoucherDetail(invoice: any) {
  router.push(`/ledger/${invoice.id}`)
}

async function handleDeleteInvoice(id: string) {
  loading.value = true
  try {
    await entryStore.removeEntry(id)
    message.success('发票已删除')
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
    message.success(isArchive ? '已变更为“仅归档不记账”，不影响科目余额' : '已变更为“参与记账”')
    await entryStore.fetchEntries()
  } catch (err) {
    message.error('修改入账模式失败')
  } finally {
    loading.value = false
  }
}

const step = ref<'upload' | 'mapping' | 'preview'>('upload')
const loading = ref(false)
const importing = ref(false)
const excelRows = ref<any[]>([])
const excelColumns = ref<string[]>([])
const columnMapping = ref<Record<string, string>>({})
const classifiedRows = ref<any[]>([])
const importResult = ref<any>(null)
const showResult = ref(false)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])

async function loadRdOptions() {
  try {
    const [projects, expenseTypes] = await Promise.all([getRdProjects(), getRdExpenseTypes()])
    rdProjects.value = projects.filter(project => project.status === 'active')
    rdExpenseTypes.value = expenseTypes.items
  } catch {
    message.warning('研发项目选项加载失败，研发类发票暂不能导入')
  }
}

const directionOptions = [
  { label: '进项 (支出)', value: 'expense' },
  { label: '销项 (收入)', value: 'income' },
  { label: '资本与往来', value: 'owner_transfer' },
]

async function triggerFilePicker() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.xlsx,.xls'
  input.style.display = 'none'
  input.addEventListener('change', handleFileUpload)
  document.body.appendChild(input)
  input.click()
  document.body.removeChild(input)
}

function handleFileUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

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
      excelColumns.value = Object.keys(json[0] as any)
      excelRows.value = json as any[]

      // 税务局全量发票查询导出标准列名（精确匹配）
      const TAX_BUREAU_MAP: Record<string, string> = {
        '序号': '',
        '发票代码': 'invoice_code',
        '发票号码': 'invoice_number',
        '数电发票号码': 'invoice_number',
        '销售方识别号': 'seller_tax_id',
        '销售方名称': 'seller_name',
        '购买方识别号': 'buyer_tax_id',
        '购买方名称': 'buyer_name',
        '开票日期': 'invoice_date',
        '税收分类编码': '',
        '特定业务类型': '',
        '货物或应税劳务名称': 'item_name',
        '规格型号': 'specification',
        '单位': 'unit',
        '数量': 'quantity',
        '单价': 'unit_price',
        '金额': 'amount',
        '税率': 'tax_rate',
        '税额': 'tax_amount',
        '价税合计': 'total_amount',
        '发票来源': 'invoice_source',
        '发票票种': 'invoice_type',
        '发票状态': 'invoice_status',
        '是否正数发票': '',
        '发票风险等级': '',
        '开票人': 'drawer',
        '备注': 'remark',
      }

      const map: Record<string, string> = {}
      for (const col of excelColumns.value) {
        // 先尝试精确匹配税务局标准列名
        if (TAX_BUREAU_MAP[col] !== undefined) {
          map[col] = TAX_BUREAU_MAP[col]
        } else {
          const lc = col.toLowerCase()
          if (/数电发票号码|数电号码|发票号码|发票代码|invoice.*no|invoice.*num|发票编号/.test(lc)) map[col] = 'invoice_number'
        else if (/开票日期|发票日期|date|开票时间/.test(lc)) map[col] = 'invoice_date'
        else if (/销.*识别号|销.*税号|销.*纳税人|seller.*tax/.test(lc)) map[col] = 'seller_tax_id'
        else if (/购.*识别号|购.*税号|购.*纳税人|buyer.*tax/.test(lc)) map[col] = 'buyer_tax_id'
        else if (/销售方|销方|卖方|seller|供应商|供货方/.test(lc)) map[col] = 'seller_name'
        else if (/购买方|购方|买方|buyer|客户/.test(lc)) map[col] = 'buyer_name'
        else if (/价税合计|总金额|total.*amount|含税金额|合计/.test(lc)) map[col] = 'total_amount'
        else if (/金额|amount|不含税/.test(lc) && !/税额/.test(lc)) map[col] = 'amount'
        else if (/税额|tax/.test(lc)) map[col] = 'tax_amount'
        else if (/税率/.test(lc)) map[col] = 'tax_rate'
        else if (/名称|货物|item|商品/.test(lc)) map[col] = 'item_name'
        else map[col] = ''
        }
      }
      columnMapping.value = map
      step.value = 'mapping'
    } catch {
      message.error('解析 Excel 文件失败')
    } finally {
      loading.value = false
    }
  }
  reader.readAsArrayBuffer(file)
  input.value = '' // reset
}

function safeFloat(val: any): number {
  if (val === null || val === undefined || val === '') return 0
  if (typeof val === 'number') return val
  const cleaned = String(val).replace(/,/g, '').trim()
  const parsed = parseFloat(cleaned)
  return isNaN(parsed) ? 0 : parsed
}

async function proceedToPreview() {
  // Build mapped rows
  const mapped = excelRows.value.map(row => {
    const out: Record<string, any> = {}
    for (const [col, field] of Object.entries(columnMapping.value)) {
      if (field) {
        const val = (row as any)[col]
        if (val !== undefined && val !== null && String(val).trim() !== '') {
          out[field] = val
        } else if (out[field] === undefined) {
          out[field] = val
        }
      }
    }
    
    const isNegative = Object.entries(row as any).some(([k, v]) => {
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

  // Send to backend for validation
  loading.value = true
  try {
    const result: any = await apiClient.post('/api/invoice/parse', { rows: mapped })
    classifiedRows.value = result.valid.map((r: any) => ({
      ...r,
      _direction: r.direction || 'expense',
      _category: r.category || '其他支出',
    }))
    if (result.errors.length > 0) {
      message.warning(`${result.errors.length} 行数据无效已跳过`)
    }
    if (result.valid.length === 0) {
      message.error('没有有效数据')
      return
    }
    step.value = 'preview'
  } catch {
    message.error('解析数据失败')
  } finally {
    loading.value = false
  }
}

async function handleImport() {
  const duplicatesCount = classifiedRows.value.filter(r => r.is_duplicate).length
  const totalCount = classifiedRows.value.length

  if (duplicatesCount === totalCount) {
    message.warning('预览中的发票已存在于发票库中，无需重复导入')
    return
  }

  const incompleteRdRow = classifiedRows.value.find(row =>
    !row.is_duplicate
    && row._category === '研发支出-费用化支出'
    && (!row._rdProjectId || !row._rdExpenseType),
  )
  if (incompleteRdRow) {
    message.warning(`发票 ${incompleteRdRow.invoice_number || ''} 的研发项目或费用类别未选择`)
    return
  }

  importing.value = true
  try {
    const filtered = classifiedRows.value.filter(r => !r.is_duplicate)
    const rows = filtered.map(r => ({
      ...r,
      direction: r._direction,
      category: r._category,
      rdProjectId: r._rdProjectId,
      rdExpenseType: r._rdExpenseType,
      rdOriginalAmount: r.amount,
      rdAllocationMethod: 'direct',
    }))
    const result = await importInvoices(rows, false)
    importResult.value = result
    showResult.value = true
    if (result.imported > 0) {
      if (duplicatesCount > 0) {
        message.success(`成功导入 ${result.imported} 条新记账条目，已自动为您剔除 ${duplicatesCount} 条重复发票！`)
      } else {
        message.success(`成功导入 ${result.imported} 条记账条目`)
      }
      await entryStore.fetchEntries()
    }
    if (result.imported < result.total) {
      message.warning(`${result.total - result.imported} 条导入失败`)
    }
  } catch {
    message.error('导入失败')
  } finally {
    importing.value = false
  }
}

function resetAll() {
  step.value = 'upload'
  excelRows.value = []
  excelColumns.value = []
  columnMapping.value = {}
  classifiedRows.value = []
  importResult.value = null
  showResult.value = false
  entryStore.fetchEntries()
}

function deletePreviewRow(index: number) {
  classifiedRows.value.splice(index, 1)
  if (classifiedRows.value.length === 0) {
    message.warning('预览列表已空，已返回上传页面')
    step.value = 'upload'
  } else {
    message.success('已剔除该发票行')
  }
}

// Bookkeeping Modal state and handlers
const showBookkeepingModal = ref(false)
const selectedInvoiceForBookkeeping = ref<any>(null)
const savingBookkeeping = ref(false)

// Invoice Detail Modal
const showDetailModal = ref(false)
const selectedInvoice = ref<any>(null)

function openDetailModal(inv: any) {
  selectedInvoice.value = inv
  showDetailModal.value = true
}
const bookkeepingForm = ref({
  direction: 'expense',
  category: '',
  payerType: 'company_account',
  employeeName: '',
  businessContext: '',
  remark: ''
})

const bookkeepingCategoryOptions = computed(() => {
  const categories = bookkeepingForm.value.direction === 'income' 
    ? INCOME_CATEGORIES 
    : EXPENSE_CATEGORIES
  return categories.map(cat => ({ label: cat, value: cat }))
})

const fetchingAiSuggestion = ref(false)
const aiSuggestedCategory = ref<string | null>(null)
const aiReason = ref<string | null>(null)

function openBookkeepingModal(inv: any) {
  selectedInvoiceForBookkeeping.value = inv
  const payer = inv.payer || 'company_account'
  let payerType = 'company_account'
  let employeeName = ''
  if (payer === 'boss_personal') {
    payerType = 'boss_personal'
  } else if (payer.startsWith('employee_personal:')) {
    payerType = 'employee_personal'
    employeeName = payer.substring('employee_personal:'.length)
  } else if (payer === 'employee_personal') {
    payerType = 'employee_personal'
  }
  
  bookkeepingForm.value = {
    direction: inv.direction || 'expense',
    category: inv.category || (inv.direction === 'income' ? '主营业务收入' : '其他支出'),
    payerType,
    employeeName,
    businessContext: inv.businessContext || inv.business_context || '',
    remark: inv.remark || ''
  }
  showBookkeepingModal.value = true

  // Reset AI suggestion state (do not auto-fetch)
  fetchingAiSuggestion.value = false
  aiSuggestedCategory.value = null
  aiReason.value = null
}

function fetchAiSuggestion() {
  if (!selectedInvoiceForBookkeeping.value) return
  fetchingAiSuggestion.value = true
  aiSuggestedCategory.value = null
  aiReason.value = null

  apiClient.post(`/api/invoice/classify-entry/${selectedInvoiceForBookkeeping.value.id}`, {
    business_context: bookkeepingForm.value.businessContext.trim()
  })
    .then((res: any) => {
      if (res && res.category) {
        aiSuggestedCategory.value = res.category
        aiReason.value = res.reason || '根据发票抬头及品名智能推断分类'
        message.success('AI 智能分类建议已生成')
      } else {
        message.warning('未能获取 AI 分类建议，请手动选择')
      }
    })
    .catch(err => {
      console.error('获取AI分类建议失败:', err)
      message.error('获取 AI 分类建议失败')
    })
    .finally(() => {
      fetchingAiSuggestion.value = false
    })
}

async function handleSaveBookkeeping() {
  if (!selectedInvoiceForBookkeeping.value) return
  
  let payer = bookkeepingForm.value.payerType
  if (payer === 'employee_personal') {
    const name = bookkeepingForm.value.employeeName.trim()
    if (!name) {
      message.error('请输入垫付员工的姓名')
      return
    }
    payer = `employee_personal:${name}`
  }
  
  savingBookkeeping.value = true
  try {
    await entryStore.updateEntryField(selectedInvoiceForBookkeeping.value.id, {
      category: bookkeepingForm.value.category,
      payer,
      direction: bookkeepingForm.value.direction,
      businessContext: bookkeepingForm.value.businessContext.trim(),
      remark: bookkeepingForm.value.remark.trim()
    })
    message.success('记账信息保存成功')
    showBookkeepingModal.value = false
    await entryStore.fetchEntries()
  } catch (err) {
    message.error('保存失败')
  } finally {
    savingBookkeeping.value = false
  }
}

const importedInvoiceColumns = [
  {
    title: '日期', key: 'occurDate', width: 130,
    sorter: (a: any, b: any) => String(a.occurDate || '').localeCompare(String(b.occurDate || '')),
    defaultSortOrder: 'descend' as const,
  },
  { title: '发票名称', key: 'title', minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: '类型', key: 'direction', width: 110, align: 'center' as const,
    render: (row: any) => h(NTag, {
      size: 'small',
      type: row.direction === 'income' ? 'info' : 'warning',
      bordered: false,
    }, () => row.direction === 'income' ? '销项发票' : '进项发票'),
  },
  {
    title: '发票金额', key: 'amount', width: 125, align: 'right' as const,
    sorter: (a: any, b: any) => Number(a.amount || 0) - Number(b.amount || 0),
    render: (row: any) => h('span', {
      style: {
        color: row.direction === 'income' ? 'var(--solo-income)' : 'var(--solo-expense)',
        fontWeight: 650,
      },
    }, formatMoney(Number(row.amount) || 0)),
  },
  {
    title: '入账模式', key: 'is_archive_only', width: 130, align: 'center' as const,
    render: (row: any) => h(NCheckbox, {
      checked: !isArchiveInvoice(row),
      disabled: isPostedInvoice(row) || isReversedInvoice(row) || row.billingStatus === 'reconciled',
      'onUpdate:checked': (checked: boolean) => toggleArchiveMode(row.id, !checked),
    }, () => isArchiveInvoice(row) ? '仅归档' : '参与记账'),
  },
  {
    title: '状态', key: 'billingStatus', width: 120, align: 'center' as const,
    render: (row: any) => {
      if (isReversedInvoice(row)) {
        return h(NTag, { size: 'small', type: 'default', bordered: false }, () => '已冲销')
      }
      if (isArchiveInvoice(row)) {
        return h(NTag, { size: 'small', type: 'default', bordered: false }, () => '仅归档不入账')
      }
      return row.billingStatus === 'reconciled'
        ? h(NTag, { size: 'small', type: 'success' }, () => '已对账')
        : h(NTag, { size: 'small', type: 'warning' }, () => '待对账')
    },
  },
  {
    title: '操作', key: 'actions', width: 190, align: 'center' as const,
    render: (row: any) => h(NSpace, { justify: 'center', size: 4, wrap: true }, () => [
      h(NButton, { size: 'tiny', secondary: true, onClick: () => openDetailModal(row) }, () => '明细'),
      // 操作按凭证生命周期收敛：已核销不可改删（保护核销关系）、已过账走冲销、
      // 已冲销仅留痕，只有草稿未对账发票才可编辑/删除
      isReversedInvoice(row) ? null
        : row.billingStatus === 'reconciled'
          ? h(NButton, { size: 'tiny', type: 'success', secondary: true, onClick: () => openDetailModal(row) }, () => '已对账·查看')
          : isPostedInvoice(row)
            ? h(NButton, { size: 'tiny', type: 'warning', secondary: true, onClick: () => goVoucherDetail(row) }, () => '已入账·去冲销')
            : !isArchiveInvoice(row) ? h(NButton, {
              size: 'tiny',
              type: 'primary',
              secondary: true,
              onClick: () => openBookkeepingModal(row),
            }, () => row.payer ? '编辑记账' : '去记账') : null,
      (!isPostedInvoice(row) && !isReversedInvoice(row) && row.billingStatus !== 'reconciled')
        ? h(NButton, {
          size: 'tiny', type: 'error', ghost: true,
          onClick: () => confirmDelete({
            content: `确定删除发票“${row.title || '未命名发票'}”吗？此操作不可撤销。`,
            onConfirm: () => handleDeleteInvoice(row.id),
          }),
        }, () => '删除')
        : null,
    ]),
  },
]

const previewColumns = [
  {
    title: '发票号码', key: 'invoice_number', width: 220,
    render: (row: any) => {
      const children = [h('span', row.invoice_number)]
      if (row.is_duplicate) {
        children.push(
          h(NTag, {
            size: 'small',
            type: 'warning',
            style: 'margin-left: 8px;'
          }, () => '已存在/重复')
        )
      }
      return h('div', { style: 'display: flex; align-items: center;' }, children)
    }
  },
  { title: '日期', key: 'invoice_date', width: 100 },
  { title: '销售方', key: 'seller_name' },
  {
    title: '金额', key: 'amount', width: 100,
    render: (row: any) => formatMoney(row.amount || 0),
  },
  {
    title: '发票类型', key: '_direction', width: 120,
    render: (row: any) => h(NSelect, {
      value: row._direction,
      'onUpdate:value': (v: string) => { row._direction = v },
      options: directionOptions,
      size: 'tiny',
    }),
  },
  {
    title: '分类', key: '_category', width: 140,
    render: (row: any) => h(NInput, {
      value: row._category,
      'onUpdate:value': (v: string) => { row._category = v },
      size: 'tiny',
    }),
  },
  {
    title: '研发项目', key: '_rdProjectId', width: 190,
    render: (row: any) => row._category === '研发支出-费用化支出'
      ? h(NSelect, {
          value: row._rdProjectId,
          'onUpdate:value': (value: string) => { row._rdProjectId = value },
          options: rdProjects.value.map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })),
          placeholder: '选择项目',
          filterable: true,
          size: 'tiny',
        })
      : h('span', { class: 'text-muted' }, '—'),
  },
  {
    title: '研发费用类别', key: '_rdExpenseType', width: 150,
    render: (row: any) => row._category === '研发支出-费用化支出'
      ? h(NSelect, {
          value: row._rdExpenseType,
          'onUpdate:value': (value: string) => { row._rdExpenseType = value },
          options: rdExpenseTypes.value.map(item => ({ label: item.label, value: item.value })),
          placeholder: '选择类别',
          size: 'tiny',
        })
      : h('span', { class: 'text-muted' }, '—'),
  },
  {
    title: '操作', key: 'action', width: 80, align: 'center' as const,
    render: (_: any, index: number) => h(NButton, {
      size: 'tiny',
      type: 'error',
      ghost: true,
      onClick: () => deletePreviewRow(index)
    }, () => '删除')
  }
]
</script>

<template>
  <div class="invoice-import-page">
    <div class="page-header">
      <div class="page-header-left">
        <NButton text class="back-btn" @click="router.push('/ledger')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回账本
        </NButton>
        <div class="title-section">
          <h2>发票导入</h2>
          <p class="subtitle">批量导入税务局全量发票 Excel 明细，自动识别进销项并生成记账草稿</p>
        </div>
      </div>
    </div>

    <!-- Step: Upload -->
    <NCard v-if="step === 'upload'" :bordered="false" class="step-card">
      <div class="upload-area">
        <p class="upload-hint">选择从税务局导出的发票 Excel 文件（.xlsx / .xls）</p>
        <NButton type="primary" :loading="loading" @click="triggerFilePicker">选择文件</NButton>
      </div>
    </NCard>

    <NCard v-if="step === 'upload'" title="已导入发票库" :bordered="false" style="margin-top: 16px;">
      <div v-if="importedInvoices.length === 0" style="padding: 40px 0; text-align: center; color: var(--solo-text-muted);">
        <NEmpty description="暂无已导入发票，请从上方上传发票进行批量解析导入" />
      </div>
      <div v-else>
        <div class="invoice-toolbar">
          <NInput v-model:value="invoiceSearch" clearable placeholder="搜索名称、号码、分类、金额">
            <template #prefix><i class="ri-search-line" /></template>
          </NInput>
          <NDatePicker
            v-model:value="invoiceDateRange"
            type="daterange"
            clearable
            start-placeholder="开始日期"
            end-placeholder="结束日期"
          />
          <NSelect v-model:value="invoiceDirection" :options="invoiceDirectionOptions" />
          <NSelect v-model:value="invoiceMatchStatus" :options="invoiceMatchStatusOptions" />
          <NButton v-if="hasInvoiceFilters" quaternary size="small" @click="resetInvoiceFilters">
            清除筛选
          </NButton>
        </div>
        <div class="invoice-result-meta">
          共 {{ importedInvoices.length }} 张，当前显示 {{ filteredInvoices.length }} 张
        </div>
        <NDataTable
          :columns="importedInvoiceColumns"
          :data="filteredInvoices"
          :row-key="(row: any) => row.id"
          :bordered="false"
          :single-line="true"
          :pagination="invoicePagination"
          :scroll-x="1020"
          size="small"
          @update:page="invoicePage = $event"
          @update:page-size="invoicePageSize = $event; invoicePage = 1"
        >
          <template #empty>
            <div class="invoice-table-empty">没有符合当前条件的发票</div>
          </template>
        </NDataTable>
      </div>
    </NCard>

    <!-- Step: Column Mapping -->
    <NCard v-if="step === 'mapping'" :bordered="false" class="step-card">
      <h3 class="step-title">列映射</h3>
      <p class="text-muted">请将 Excel 列映射到系统字段</p>
      <div class="mapping-grid">
        <div v-for="col in excelColumns" :key="col" class="mapping-row">
          <span class="mapping-col">{{ col }}</span>
          <span class="mapping-arrow">→</span>
          <NSelect
            :value="columnMapping[col]"
            @update:value="(v: string) => columnMapping[col] = v"
            :options="[
              { label: '忽略', value: '' },
              { label: '发票代码', value: 'invoice_code' },
              { label: '发票号码', value: 'invoice_number' },
              { label: '开票日期', value: 'invoice_date' },
              { label: '销售方', value: 'seller_name' },
              { label: '销售方税号', value: 'seller_tax_id' },
              { label: '购买方', value: 'buyer_name' },
              { label: '购买方税号', value: 'buyer_tax_id' },
              { label: '金额', value: 'amount' },
              { label: '税额', value: 'tax_amount' },
              { label: '税率', value: 'tax_rate' },
              { label: '价税合计', value: 'total_amount' },
              { label: '货物名称', value: 'item_name' },
              { label: '规格型号', value: 'specification' },
              { label: '单位', value: 'unit' },
              { label: '数量', value: 'quantity' },
              { label: '单价', value: 'unit_price' },
            ]"
            style="width: 160px;"
            size="small"
          />
        </div>
      </div>
      <div class="step-actions">
        <NButton @click="step = 'upload'">重新选择</NButton>
        <NButton type="primary" :loading="loading" @click="proceedToPreview">下一步</NButton>
      </div>
    </NCard>

    <!-- Step: Preview & Import -->
    <NCard v-if="step === 'preview'" :bordered="false" class="step-card">
      <h3 class="step-title">预览与分类</h3>
      <p class="text-muted">共 {{ classifiedRows.length }} 条记录。可编辑方向和分类。</p>

      <NSpin :show="loading">
        <NDataTable
          :columns="previewColumns"
          :data="classifiedRows"
          :bordered="false"
          :single-line="true"
          size="small"
        />
      </NSpin>

      <div class="step-actions">
        <NButton @click="resetAll">重新开始</NButton>
        <NButton type="primary" :loading="importing" @click="handleImport">
          确认导入 ({{ classifiedRows.length }} 条)
        </NButton>
      </div>
    </NCard>

    <!-- Result modal -->
    <NModal v-model:show="showResult" title="导入结果" preset="card" style="width: 460px;">
      <div v-if="importResult">
        <p>成功导入 <NTag type="success" :bordered="false">{{ importResult.imported }}</NTag> 条</p>
        <p>总处理 <NTag type="info" :bordered="false">{{ importResult.total }}</NTag> 条</p>
        <div v-if="importResult.errors?.length" class="error-list">
          <p class="text-muted">错误：</p>
          <div v-for="(err, idx) in importResult.errors" :key="idx" class="error-item">
            <NTag size="tiny" type="error" :bordered="false">第 {{ err.row + 1 }} 行</NTag>
            <span>{{ err.reason }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton secondary @click="resetAll">继续导入</NButton>
          <NButton secondary type="warning" @click="router.push('/closing-wizard')">去结账向导对账</NButton>
          <NButton type="primary" @click="router.push('/ledger')">查看日记账</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Bookkeeping Modal -->
    <NModal v-model:show="showBookkeepingModal" title="发票记账管理" preset="card" style="width: 520px; border-radius: 16px;">
      <div v-if="selectedInvoiceForBookkeeping">
        <NSpace vertical size="large">
          <div class="invoice-summary-details">
            <div class="summary-row">
              <span class="summary-label">发票名称:</span>
              <span class="summary-value">{{ selectedInvoiceForBookkeeping.title }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">开票日期:</span>
              <span class="summary-value">{{ selectedInvoiceForBookkeeping.occurDate }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">发票金额:</span>
              <span class="summary-value amount-text" :style="selectedInvoiceForBookkeeping.direction === 'income' ? 'color: var(--solo-income);' : 'color: var(--solo-expense);'">{{ formatMoney(Number(selectedInvoiceForBookkeeping.amount) || 0) }}</span>
            </div>
          </div>
          
          <div class="form-item">
            <span class="form-label">收支方向</span>
            <NSelect 
              v-model:value="bookkeepingForm.direction" 
              :options="[
                { label: '进项 (支出)', value: 'expense' },
                { label: '销项 (收入)', value: 'income' }
              ]"
              disabled
            />
          </div>

          <div class="form-item">
            <span class="form-label">业务说明（选填）</span>
            <NInput 
              v-model:value="bookkeepingForm.businessContext" 
              type="textarea" 
              placeholder="例如：这笔技术服务费用于研发项目" 
              :autosize="{ minRows: 2, maxRows: 4 }"
            />
          </div>

          <div class="ai-trigger-row">
            <span class="ai-trigger-tip">补充用途后，可让 AI 重新生成分类草稿</span>
            <NButton 
              size="tiny" 
              type="primary" 
              secondary 
              :loading="fetchingAiSuggestion"
              @click="fetchAiSuggestion"
            >
              {{ aiSuggestedCategory ? '根据说明更新 AI 建议' : '获取 AI 建议' }}
            </NButton>
          </div>

          <div v-if="fetchingAiSuggestion || aiSuggestedCategory" class="form-item">
            <div v-if="fetchingAiSuggestion" style="padding: 12px; font-size: 12px; color: var(--solo-text-secondary); display: flex; align-items: center; gap: 6px; background: var(--solo-bg-blue-soft, #eef6ff); border: 1px dashed var(--solo-primary, #3b82f6); border-radius: 8px;">
              <NSpin size="small" />
              <span>正在获取 AI 智能分类建议...</span>
            </div>
            <div v-else-if="aiSuggestedCategory" class="ai-suggestion-box">
              <div class="ai-suggestion-badge">
                <i class="ri-sparkles-line" style="font-size: 14px; margin-right: 2px;"></i>
                <span>AI 建议：{{ aiSuggestedCategory }}</span>
                <NButton 
                  size="tiny" 
                  type="primary" 
                  ghost 
                  style="margin-left: auto; height: 20px; padding: 0 6px; font-size: 11px;"
                  @click="bookkeepingForm.category = aiSuggestedCategory"
                >
                  采纳建议
                </NButton>
              </div>
              <p class="ai-suggestion-reason">分析原因：{{ aiReason }}</p>
            </div>
          </div>

          <div class="form-item">
            <span class="form-label">费用分类</span>
            <NSelect 
              v-model:value="bookkeepingForm.category" 
              :options="bookkeepingCategoryOptions" 
              filterable
              placeholder="请选择费用科目分类"
            />
          </div>

          <div class="form-item">
            <span class="form-label">支付方式</span>
            <NSelect 
              v-model:value="bookkeepingForm.payerType" 
              :options="[
                { label: '公司对公支付', value: 'company_account' },
                { label: '老板个人垫付', value: 'boss_personal' },
                { label: '员工个人垫付', value: 'employee_personal' }
              ]"
            />
          </div>

          <div v-if="bookkeepingForm.payerType === 'employee_personal'" class="form-item">
            <span class="form-label">员工姓名</span>
            <NInput 
              v-model:value="bookkeepingForm.employeeName" 
              placeholder="请输入垫付员工的姓名"
            />
          </div>

          <div class="form-item">
            <span class="form-label">备注（选填）</span>
            <NInput 
              v-model:value="bookkeepingForm.remark" 
              type="textarea" 
              placeholder="可填写其他备注，如合同编号、付款对象等" 
              :autosize="{ minRows: 2, maxRows: 4 }"
            />
          </div>
        </NSpace>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showBookkeepingModal = false">取消</NButton>
          <NButton type="primary" :loading="savingBookkeeping" @click="handleSaveBookkeeping">确认记账</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Invoice Detail Modal -->
    <NModal v-model:show="showDetailModal" title="发票明细" preset="card" style="width: 560px; border-radius: 16px;">
      <div v-if="selectedInvoice" class="invoice-detail-content">
        <div class="detail-section">
          <h4 class="detail-section-title">基本信息</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="detail-label">发票名称</span>
              <span class="detail-value">{{ selectedInvoice.title }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">发票号码</span>
              <span class="detail-value">{{ selectedInvoice.invoiceNumber || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">发票代码</span>
              <span class="detail-value">{{ selectedInvoice.invoice_code || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">开票日期</span>
              <span class="detail-value">{{ selectedInvoice.occurDate || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">发票类型</span>
              <span class="detail-value">{{ selectedInvoice.invoice_type || (selectedInvoice.direction === 'income' ? '销项发票' : '进项发票') }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">发票来源</span>
              <span class="detail-value">{{ selectedInvoice.invoice_source || selectedInvoice.source || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">发票状态</span>
              <span class="detail-value">{{ selectedInvoice.invoice_status || '-' }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="detail-section-title">交易方信息</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="detail-label">销售方</span>
              <span class="detail-value">{{ selectedInvoice.seller_name || selectedInvoice.merchant || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">销售方税号</span>
              <span class="detail-value">{{ selectedInvoice.seller_tax_id || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">购买方</span>
              <span class="detail-value">{{ selectedInvoice.buyer_name || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">购买方税号</span>
              <span class="detail-value">{{ selectedInvoice.buyer_tax_id || '-' }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="detail-section-title">货物/服务明细</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="detail-label">货物名称</span>
              <span class="detail-value">{{ selectedInvoice.item_name || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">规格型号</span>
              <span class="detail-value">{{ selectedInvoice.specification || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">单位</span>
              <span class="detail-value">{{ selectedInvoice.unit || '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">数量</span>
              <span class="detail-value">{{ selectedInvoice.quantity ?? '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">单价</span>
              <span class="detail-value">{{ selectedInvoice.unit_price != null ? formatMoney(selectedInvoice.unit_price) : '-' }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="detail-section-title">金额与税务</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="detail-label">不含税金额</span>
              <span class="detail-value">{{ formatMoney(selectedInvoice.amount || 0) }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">税率</span>
              <span class="detail-value">{{ selectedInvoice.tax_rate || selectedInvoice.vatRate ? ((selectedInvoice.vatRate ?? parseFloat(selectedInvoice.tax_rate)) * 100).toFixed(0) + '%' : '-' }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">税额</span>
              <span class="detail-value">{{ formatMoney(selectedInvoice.tax_amount || selectedInvoice.vatAmount || 0) }}</span>
            </div>
            <div class="detail-item">
              <span class="detail-label">价税合计</span>
              <span class="detail-value amount-text">{{ formatMoney((Number(selectedInvoice.amount) || 0) + (Number(selectedInvoice.tax_amount || selectedInvoice.vatAmount) || 0)) }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section" v-if="selectedInvoice.remark || selectedInvoice.drawer">
          <h4 class="detail-section-title">其他</h4>
          <div class="detail-grid">
            <div class="detail-item" v-if="selectedInvoice.drawer">
              <span class="detail-label">开票人</span>
              <span class="detail-value">{{ selectedInvoice.drawer }}</span>
            </div>
            <div class="detail-item" v-if="selectedInvoice.remark">
              <span class="detail-label">备注</span>
              <span class="detail-value">{{ selectedInvoice.remark }}</span>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDetailModal = false">关闭</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.invoice-import-page {
  max-width: 1240px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 8px 0 0;
  font-size: 20px;
  font-weight: 600;
}
.step-card {
  border-radius: var(--solo-radius-lg);
}
.step-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
  margin-top: 4px;
  margin-bottom: 16px;
}
.upload-area {
  text-align: center;
  padding: 48px 24px;
}
.upload-hint {
  color: var(--solo-text-secondary);
  margin-bottom: 16px;
}
.mapping-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}
.mapping-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mapping-col {
  min-width: 120px;
  font-size: 13px;
  color: var(--solo-text-primary);
  font-weight: 500;
}
.mapping-arrow {
  color: var(--solo-text-muted);
}
.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}
.error-list {
  margin-top: 12px;
}
.error-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.invoice-toolbar {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(230px, 1.2fr) 128px 118px auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.invoice-result-meta {
  margin-bottom: 8px;
  color: var(--solo-text-muted);
  font-size: 12px;
}
.invoice-table-empty {
  padding: 30px 16px;
  color: var(--solo-text-muted);
}

/* Bookkeeping form custom styles */
.invoice-summary-details {
  background: var(--solo-bg-blue-soft, #f4f7fc);
  padding: 12px 16px;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.summary-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.summary-label {
  color: var(--solo-text-secondary, #64748b);
}
.summary-value {
  font-weight: 500;
  color: var(--solo-text-primary, #1e293b);
}
.amount-text {
  color: var(--n-primary-color, #10b981);
  font-weight: bold;
  font-size: 15px;
}
.form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #64748b);
}

/* AI suggestion box styles */
.ai-suggestion-box {
  background: var(--solo-bg-blue-soft, #eef6ff);
  border: 1px dashed var(--solo-primary, #3b82f6);
  padding: 8px 12px;
  border-radius: 8px;
  margin-bottom: 4px;
  font-size: 12px;
}
.ai-suggestion-badge {
  display: flex;
  align-items: center;
  font-weight: 600;
  color: var(--solo-primary, #1f5fea);
  gap: 4px;
}
.ai-suggestion-reason {
  color: var(--solo-text-secondary, #5b6673);
  margin: 4px 0 0;
  line-height: 1.4;
}

/* AI Trigger Row */
.ai-trigger-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: -8px;
  margin-bottom: 4px;
}
.ai-trigger-tip {
  font-size: 12px;
  color: var(--solo-text-secondary, #64748b);
}

/* Invoice Detail Modal */
.invoice-detail-content {
  max-height: 60vh;
  overflow-y: auto;
}
.detail-section {
  margin-bottom: 20px;
}
.detail-section:last-child {
  margin-bottom: 0;
}
.detail-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #1e293b);
  margin: 0 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--solo-border-light, #e2e8f0);
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 24px;
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  font-size: 13px;
  padding: 4px 0;
}
.detail-label {
  color: var(--solo-text-secondary, #64748b);
  flex-shrink: 0;
  margin-right: 12px;
}
.detail-value {
  color: var(--solo-text-primary, #1e293b);
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}
@media (max-width: 900px) {
  .invoice-toolbar {
    grid-template-columns: minmax(200px, 1fr) minmax(220px, 1fr);
  }
}
@media (max-width: 640px) {
  .invoice-toolbar {
    grid-template-columns: 1fr;
  }
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
