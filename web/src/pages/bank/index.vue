<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, h, onMounted, watch } from 'vue'
import { accountingErrorText } from '@/utils/errors'
import { useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage, NSpin,
  NInput, NModal, NDataTable, NSelect, NDatePicker, useDialog,
} from 'naive-ui'
import apiClient from '@/api/client'
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
} from '@/utils/bankImport'
import { SoloDrawer, SoloEmpty, SoloStatusBadge } from '@/components/ui'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
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


const statementColumns = [
  { type: 'selection' as const },
  {
    title: '日期', key: 'transaction_date', width: 110,
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
    // 这才是这张表真正需要眼睛停下来的地方。
    render: (row: any) => row.matched_entry_id
      ? h(SoloStatusBadge, { tone: 'neutral', plain: true }, () => '已匹配')
      : h(SoloStatusBadge, { tone: 'warning', plain: true }, () => '未匹配'),
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
]

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
    await loadDiff()
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
      message.info(remainingAfterRules.value > 0 ? '规则未找到匹配，可继续使用 AI 分析剩余流水' : '暂无未匹配流水')
    } else {
      message.success(`已生成 ${aiSuggestions.value.length} 条匹配候选，请逐条核对确认`)
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

async function confirmSuggestion(suggestion: any) {
  if (isRdSuggestion(suggestion)) {
    const selection = rdSelectionFor(suggestion.statementId)
    if (!selection.projectId || !selection.expenseType) {
      message.warning('研发费用必须选择研发项目和费用类别')
      return
    }
  }
  aiConfirming.value[suggestion.statementId] = true
  try {
    const payload: any = {
      statement_id: suggestion.statementId,
      type: suggestion.type,
    }
    if (suggestion.type === 'match') {
      payload.matched_entry_id = suggestion.matchedEntryId
      payload.matched_entry_ids = suggestion.matchedEntryIds || [suggestion.matchedEntryId]
      payload.auto_post = true
    } else {
      const rdSelection = rdSelectionFor(suggestion.statementId)
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
    message.success(result.settlementStatus && result.settlementStatus !== 'posted'
      ? '已关联原收款与手续费，结算凭证待过账，请前往凭证页处理' : '确认成功')
    aiSuggestions.value = aiSuggestions.value.filter(s => s.statementId !== suggestion.statementId)
    await loadStatements()
  } catch {
    message.error('确认失败')
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

function triggerFilePicker() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.xlsx,.xls,.csv'
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
      excelStep.value = 'preview'
    } catch {
      message.error('解析文件失败')
    }
  }
  reader.readAsArrayBuffer(file)
  input.value = '' // reset
}

async function handleExcelImport() {
  const { rows: importable, skippedInvalidDate } = buildBankRows(excelRows.value, columnMapping.value)
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
  showExcelImport.value = true
}

onMounted(() => Promise.all([loadStatements(), loadRdOptions()]))
// 切回标签时静默重拉流水：凭证那边冲销凭证会改动匹配状态
usePageRefresh(loadStatements)
</script>

<template>
  <div class="bank-page">
    <div class="page-header">
      <div class="header-left">
        <div class="title-section">
          <h2>银行对账</h2>
          <p class="subtitle">核对银行流水并匹配系统记账凭证，所有匹配都需手动确认</p>
        </div>
      </div>
      <div class="header-right">
        <NSpace>
          <NButton size="small" :loading="matching" @click="handleMatch">生成匹配候选</NButton>
          <NButton size="small" @click="openExcelImport">导入文件</NButton>
        </NSpace>
      </div>
    </div>

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
          :scroll-x="980"
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
            <template #icon><i class="ri-sparkling-2-line" /></template>
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
              <NButton size="small" type="primary" :loading="aiConfirming[s.statementId]" @click="confirmSuggestion(s)">确认</NButton>
              <NButton size="small" quaternary @click="dismissSuggestion(s.statementId)">忽略</NButton>
            </div>
          </div>
        </div>
      </NSpin>
    </SoloDrawer>

    <!-- Import Modal（CSV/Excel 统一走前端解析，CSV 弹窗已移除） -->
    <NModal v-model:show="showExcelImport" title="导入银行流水文件" preset="card" style="width: 700px;">
      <template v-if="excelStep === 'upload'">
        <div class="upload-area">
          <p>选择银行流水文件（.xlsx / .xls / .csv）</p>
          <NButton type="primary" @click="triggerFilePicker">选择文件</NButton>
        </div>
      </template>

      <template v-if="excelStep === 'preview'">
        <p class="excel-info">文件：{{ excelFileName }} · 共 {{ excelRows.length }} 行</p>

        <h4 class="section-title">列映射</h4>
        <p class="mapping-hint">附言、对手方户名等辅助列无需映射，导入时会自动并入交易描述，供收支识别参考。</p>
        <div class="mapping-grid">
          <div v-for="col in excelColumns" :key="col" class="mapping-row">
            <span class="mapping-col">{{ col }}</span>
            <span class="mapping-arrow">→</span>
            <!-- 描述增强列（附言/对手方户名等）不参与映射，自动并入描述；显示角色让用户所见即所得 -->
            <NTag
              v-if="columnMapping[col] === '' && bankColumnDescriptionRole(col)"
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

        <h4 class="section-title" style="margin-top: 16px;">预览（前 5 行）</h4>
        <NDataTable
          :columns="excelColumns.map(c => ({ title: c, key: c }))"
          :data="excelRows.slice(0, 5)"
          :bordered="false"
          :single-line="true"
          size="small"
        />
      </template>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showExcelImport = false">取消</NButton>
          <NButton v-if="excelStep === 'preview'" type="primary" :loading="excelImporting" @click="handleExcelImport">
            确认导入 ({{ excelRows.length }} 条)
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
  max-width: 1240px;
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
</style>
