<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { h, ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, NDataTable, NModal, useMessage,
  NSpin, NPagination, NSelect,
} from 'naive-ui'
import { SoloMonthPicker, SoloEmpty } from '@/components/ui'
import PrivateExpenseDrawer from './components/PrivateExpenseDrawer.vue'
import {
  getExpenses, deleteExpense, getExpenseSummary, getAllExpenses,
} from '@/api/private-expense'
import type {
  PrivateExpense, PrivateExpenseSummary, PrivateExpenseFilter,
} from '@/api/private-expense'
import { formatMoney } from '@/utils/format'
import apiClient from '@/api/client'
import * as XLSX from 'xlsx'
import { saveWorkbook } from '@/utils/export'

const route = useRoute()
const message = useMessage()

const drawerVisible = ref(false)
const editingExpenseId = ref<string | null>(null)

function openCreate() {
  editingExpenseId.value = null
  drawerVisible.value = true
}

function openEdit(id: string) {
  editingExpenseId.value = id
  drawerVisible.value = true
}

function handleDrawerSuccess() {
  loadData()
}

watch(
  () => [route.query.create, route.query.edit],
  ([create, edit]) => {
    if (create) {
      openCreate()
    } else if (edit) {
      openEdit(String(edit))
    }
  },
  { immediate: true },
)

const loading = ref(false)
const exporting = ref(false)
const expenses = ref<PrivateExpense[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// Filters
const ownerName = ref('')
const direction = ref<string>('')
const invoiceStatus = ref<string>('')
const selectedMonth = ref<string | null>(null)

const summary = ref<PrivateExpenseSummary>({
  count: 0,
  total_amount: 0,
  income_amount: 0,
  expense_amount: 0,
  deposit_amount: 0,
  available_income_amount: 0,
  offset_amount: 0,
  balance_amount: 0,
  owners: [],
})

function normalizeSummary(data: Partial<PrivateExpenseSummary>): PrivateExpenseSummary {
  const incomeAmount = Number(data.income_amount ?? 0)
  const expenseAmount = Number(data.expense_amount ?? data.total_amount ?? 0)
  const depositAmount = Number(data.deposit_amount ?? 0)
  const availableIncomeAmount = Number(data.available_income_amount ?? Math.max(incomeAmount - depositAmount, 0))
  const offsetAmount = Number(data.offset_amount ?? Math.min(availableIncomeAmount, expenseAmount))
  const balanceAmount = Number(data.balance_amount ?? availableIncomeAmount - expenseAmount)
  return {
    count: Number(data.count ?? 0),
    total_amount: Number(data.total_amount ?? expenseAmount),
    income_amount: incomeAmount,
    expense_amount: expenseAmount,
    deposit_amount: depositAmount,
    available_income_amount: availableIncomeAmount,
    offset_amount: offsetAmount,
    balance_amount: balanceAmount,
    owners: data.owners || [],
  }
}

const ownerOptions = ref<Array<{ label: string; value: string }>>([])
const deleteTarget = ref<string | null>(null)
const showDeleteModal = ref(false)

const payerLabels: Record<string, string> = {
  cash: '现金',
  wechat: '微信',
  alipay: '支付宝',
  card: '银行卡',
  other: '其他',
}

const directionOptions = [
  { label: '全部收支', value: '' },
  { label: '支出 (老板垫付)', value: 'expense' },
  { label: '收入 (私户代收)', value: 'income' },
]

const invoiceStatusOptions = [
  { label: '全部发票状态', value: '' },
  { label: '有发票', value: 'has_invoice' },
  { label: '无发票', value: 'no_invoice' },
  { label: '后续补票/冲抵', value: 'substitute' },
]

const hasActiveFilter = computed(() => {
  return !!(ownerName.value || direction.value || invoiceStatus.value || selectedMonth.value)
})

function resetFilters() {
  ownerName.value = ''
  direction.value = ''
  invoiceStatus.value = ''
  selectedMonth.value = null
  page.value = 1
  loadData()
}

function getMonthDateRange(monthStr: string | null): { startDate: string; endDate: string } {
  if (!monthStr || !/^\d{4}-\d{2}$/.test(monthStr)) {
    return { startDate: '', endDate: '' }
  }
  const [yearStr, mStr] = monthStr.split('-')
  const year = parseInt(yearStr, 10)
  const month = parseInt(mStr, 10)
  const lastDay = new Date(year, month, 0).getDate()
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    startDate: `${yearStr}-${mStr}-01`,
    endDate: `${yearStr}-${mStr}-${pad(lastDay)}`,
  }
}

function isRecoverableDeposit(row: PrivateExpense) {
  return row.direction !== 'income' && row.category === '押金保证金'
}

async function loadData() {
  loading.value = true
  try {
    const { startDate, endDate } = getMonthDateRange(selectedMonth.value)
    const filter: PrivateExpenseFilter = {
      direction: direction.value || undefined,
      invoiceStatus: invoiceStatus.value || undefined,
      startDate: startDate || undefined,
      endDate: endDate || undefined,
    }

    const [listRes, summaryRes] = await Promise.all([
      getExpenses(page.value, pageSize.value, ownerName.value, filter),
      getExpenseSummary(ownerName.value),
    ])
    expenses.value = listRes.items
    total.value = listRes.total
    summary.value = normalizeSummary(summaryRes)
    if (!ownerName.value) {
      ownerOptions.value = (summaryRes.owners || []).map(item => ({
        label: item.owner_name,
        value: item.owner_name,
      }))
    }
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  page.value = 1
  loadData()
}

watch([ownerName, direction, invoiceStatus, selectedMonth], () => {
  handleFilterChange()
})

function confirmDelete(id: string) {
  deleteTarget.value = id
  showDeleteModal.value = true
}

async function handleDelete() {
  if (!deleteTarget.value) return
  try {
    await deleteExpense(deleteTarget.value)
    message.success('已删除')
    showDeleteModal.value = false
    deleteTarget.value = null
    await loadData()
  } catch {
    message.error('删除失败')
  }
}

function handlePageChange(p: number) {
  page.value = p
  loadData()
}

const officialEntries = ref<any[]>([])

function getOfficialEntryName(id: string): string {
  const found = officialEntries.value.find(e => e.id === id)
  return found ? `${found.title} (¥${found.amount?.toFixed(2)})` : `正式账目 #${id}`
}

onMounted(async () => {
  try {
    const resp: any = await apiClient.get('/api/record/entries')
    officialEntries.value = resp.entries || []
  } catch {
    // silent
  }
  loadData()
})

// Summary cards presentation
const balanceAmount = computed(() => summary.value.balance_amount)
const isCompanyOwesOwner = computed(() => balanceAmount.value < 0)
const isOwnerOwesCompany = computed(() => balanceAmount.value > 0)

const balanceTitle = computed(() => {
  if (isCompanyOwesOwner.value) return '公司应偿付老板'
  if (isOwnerOwesCompany.value) return '老板应归还公司'
  return '收支完全对齐'
})

const balanceBadgeText = computed(() => {
  if (isCompanyOwesOwner.value) return '待报销冲抵'
  if (isOwnerOwesCompany.value) return '代收沉淀'
  return '已结平'
})

const balanceBadgeClass = computed(() => {
  if (isCompanyOwesOwner.value) return 'badge-warn'
  if (isOwnerOwesCompany.value) return 'badge-info'
  return 'badge-success'
})

const balanceCardClass = computed(() => {
  if (isCompanyOwesOwner.value) return 'is-warn'
  if (isOwnerOwesCompany.value) return 'is-info'
  return 'is-success'
})

const balanceFormatted = computed(() => formatMoney(Math.abs(balanceAmount.value)))

const balanceHintText = computed(() => {
  if (isCompanyOwesOwner.value) {
    return `冲抵可用代收后，公司应走公账偿还老板垫款`
  }
  if (isOwnerOwesCompany.value) {
    return `代收款超出垫付，沉淀资金建议转入公司公账`
  }
  return `私户代收与垫付开支刚好完全冲抵平衡`
})

// Columns
const columns = [
  { title: '日期', key: 'occur_date', width: 96 },
  {
    title: '类型', key: 'direction', width: 75,
    render: (row: PrivateExpense) => {
      const isIncome = row.direction === 'income'
      return h(NTag, { size: 'small', type: isIncome ? 'success' : 'error', bordered: false }, { default: () => isIncome ? '代收' : '垫付' })
    },
  },
  { title: '事由', key: 'title', width: 180, ellipsis: { tooltip: true } },
  {
    title: '归属 / 渠道', key: 'owner_name', width: 130,
    render: (row: PrivateExpense) => h('div', { class: 'owner-cell' }, [
      h('span', { class: 'owner-name' }, row.owner_name || '本人'),
      h('span', { class: 'account-name' }, row.private_account || payerLabels[row.payer] || '其他'),
    ]),
  },
  {
    title: '金额', key: 'amount', width: 120,
    render: (row: PrivateExpense) => {
      const isIncome = row.direction === 'income'
      return h('span', {
        style: {
          color: isIncome ? 'var(--solo-income)' : 'var(--solo-expense)',
          fontWeight: 600,
          fontVariantNumeric: 'tabular-nums',
        },
      }, `${isIncome ? '+' : '-'}${formatMoney(row.amount)}`)
    },
  },
  {
    title: '分类', key: 'category', width: 110,
    render: (row: PrivateExpense) => row.category ? h(NTag, { size: 'small', bordered: false }, { default: () => row.category }) : '-',
  },
  {
    title: '状态', key: 'invoice_status', width: 120,
    render: (row: PrivateExpense) => {
      if (isRecoverableDeposit(row)) {
        return h(NTag, { size: 'small', type: 'info', bordered: false }, { default: () => '可收回押金' })
      }
      if (row.direction === 'income') {
        const available = row.is_available === undefined ? true : Boolean(row.is_available)
        return h(NTag, { size: 'small', type: available ? 'success' : 'warning', bordered: false }, {
          default: () => available ? '可用代收' : '受限资金',
        })
      }
      if (row.invoice_status === 'has_invoice') {
        return h(NTag, { size: 'small', type: 'success', bordered: false }, { default: () => '有发票' })
      } else if (row.invoice_status === 'substitute') {
        const entryName = getOfficialEntryName(row.substitute_entry_id || '')
        return h(NSpace, { vertical: true, size: 2 }, {
          default: () => [
            h(NTag, { size: 'small', type: 'warning', bordered: false }, { default: () => '后续冲抵' }),
            row.substitute_entry_id ? h('span', { style: { fontSize: '11px', color: 'var(--solo-text-muted)', display: 'block', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, `关联: ${entryName}`) : null,
          ],
        })
      } else {
        return h(NTag, { size: 'small', type: 'error', bordered: false }, { default: () => '无发票' })
      }
    },
  },
  {
    title: '操作', key: 'actions', width: 88,
    render: (row: PrivateExpense) => h(NSpace, { size: 4, wrap: false }, {
      default: () => [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row.id) }, { default: () => '编辑' }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => confirmDelete(row.id) }, { default: () => '删除' }),
      ],
    }),
  },
]

// Export to Excel
async function handleExport() {
  exporting.value = true
  try {
    const { startDate, endDate } = getMonthDateRange(selectedMonth.value)
    const filter: PrivateExpenseFilter = {
      direction: direction.value || undefined,
      invoiceStatus: invoiceStatus.value || undefined,
      startDate: startDate || undefined,
      endDate: endDate || undefined,
    }
    const allItems = await getAllExpenses(ownerName.value, filter)
    if (allItems.length === 0) {
      message.warning('当前筛选条件下没有可导出的私账记录')
      return
    }

    const detailRows = allItems.map(item => {
      const isIncome = item.direction === 'income'
      const invoiceStatusText = item.invoice_status === 'has_invoice'
        ? '有发票'
        : item.invoice_status === 'substitute'
        ? '后续补票/冲抵'
        : '无发票'
      const entryName = item.substitute_entry_id ? getOfficialEntryName(item.substitute_entry_id) : ''
      return {
        '发生日期': item.occur_date || '',
        '收支类型': isIncome ? '私户代收' : '老板垫付',
        '事由': item.title,
        '归属人': item.owner_name || '本人',
        '收付渠道/账户': item.private_account || payerLabels[item.payer] || '其他',
        '金额': (isIncome ? 1 : -1) * Number(item.amount),
        '收支分类': item.category || '',
        '发票与冲销状态': invoiceStatusText,
        '关联公账凭证/说明': entryName || item.substitute_memo || '',
        '备注': item.memo || '',
      }
    })

    const summaryRows = [
      { '项目': '统计归属人', '数值/说明': ownerName.value ? `归属人：${ownerName.value}` : '全部归属人' },
      { '项目': '总记录数', '数值/说明': `${summary.value.count} 笔` },
      { '项目': '私账垫付总额（支出）', '数值/说明': summary.value.expense_amount },
      { '项目': '私户代收总额（收入）', '数值/说明': summary.value.income_amount },
      { '项目': '可收回押金保证金', '数值/说明': summary.value.deposit_amount || 0 },
      { '项目': '可用代收金额', '数值/说明': summary.value.available_income_amount || 0 },
      { '项目': '可冲销金额', '数值/说明': summary.value.offset_amount },
      { '项目': '对账清偿净额', '数值/说明': summary.value.balance_amount },
      { '项目': '清偿对账结论', '数值/说明': balanceTitle.value },
      { '项目': '建议操作', '数值/说明': balanceHintText.value },
    ]

    const wb = XLSX.utils.book_new()
    const wsDetail = XLSX.utils.json_to_sheet(detailRows)
    wsDetail['!cols'] = [
      { wch: 12 }, { wch: 10 }, { wch: 24 }, { wch: 10 }, { wch: 16 },
      { wch: 12 }, { wch: 14 }, { wch: 16 }, { wch: 24 }, { wch: 20 },
    ]
    XLSX.utils.book_append_sheet(wb, wsDetail, '私账收支明细')

    const wsSummary = XLSX.utils.json_to_sheet(summaryRows)
    wsSummary['!cols'] = [{ wch: 24 }, { wch: 36 }]
    XLSX.utils.book_append_sheet(wb, wsSummary, '对账清偿汇总')

    const dateStr = new Date().toISOString().slice(0, 10)
    const fileName = `老板私账对账单_${ownerName.value || '全部'}_${dateStr}.xlsx`
    saveWorkbook(wb, fileName)
    message.success('已导出对账单 Excel')
  } catch {
    message.error('导出失败，请重试')
  } finally {
    exporting.value = false
  }
}

// 静默重拉，保证切页时数据最新
usePageRefresh(loadData)
</script>

<template>
  <div class="pe-page">
    <div class="page-header">
      <div class="header-left">
        <div class="title-row">
          <h2>小私账</h2>
          <NTag size="small" :bordered="false" round type="info">老板个人对账</NTag>
        </div>
        <p class="subtitle">记录未入公账的老板垫付与私户代收，自动核算对账净额，支撑报销冲抵与合规平账</p>
      </div>
      <div class="header-actions">
        <NButton size="small" secondary :loading="exporting" @click="handleExport">
          <template #icon><i class="ri-download-2-line" /></template>
          导出对账单
        </NButton>
        <NButton size="small" type="primary" @click="openCreate">
          <template #icon><i class="ri-add-line" /></template>
          记一笔私账
        </NButton>
      </div>
    </div>

    <!-- 4-Stat Conclusion Cards -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">私账垫付</span>
          <span class="stat-badge expense">{{ summary.count }} 笔记录</span>
        </div>
        <div class="stat-value expense-text">{{ formatMoney(summary.expense_amount) }}</div>
        <div class="stat-desc">老板个人垫付的公司经营支出</div>
      </div>

      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">私户代收</span>
          <span class="stat-badge income">可用 {{ formatMoney(summary.available_income_amount || 0) }}</span>
        </div>
        <div class="stat-value income-text">{{ formatMoney(summary.income_amount) }}</div>
        <div class="stat-desc">个人账户代收公司业务款项</div>
      </div>

      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">可收回押金</span>
          <span class="stat-badge muted">受限资金</span>
        </div>
        <div class="stat-value">{{ formatMoney(summary.deposit_amount || 0) }}</div>
        <div class="stat-desc">平台或合同保证金，不参与日常冲抵</div>
      </div>

      <div class="stat-card highlight-card" :class="balanceCardClass">
        <div class="stat-header">
          <span class="stat-label">{{ balanceTitle }}</span>
          <span class="stat-badge" :class="balanceBadgeClass">{{ balanceBadgeText }}</span>
        </div>
        <div class="stat-value" :class="isCompanyOwesOwner ? 'warn-text' : isOwnerOwesCompany ? 'info-text' : ''">
          {{ balanceFormatted }}
        </div>
        <div class="stat-desc">{{ balanceHintText }}</div>
      </div>
    </div>

    <!-- Unified Filter Bar -->
    <div class="filter-bar">
      <div class="filter-left">
        <NSelect
          v-model:value="ownerName"
          :options="[{ label: '全部归属人', value: '' }, ...ownerOptions]"
          placeholder="资金归属人"
          size="small"
          style="width: 130px"
        />
        <NSelect
          v-model:value="direction"
          :options="directionOptions"
          placeholder="收支方向"
          size="small"
          style="width: 140px"
        />
        <NSelect
          v-model:value="invoiceStatus"
          :options="invoiceStatusOptions"
          placeholder="发票状态"
          size="small"
          style="width: 140px"
        />
        <SoloMonthPicker
          v-model:value="selectedMonth"
          placeholder="按月份筛选"
          clearable
          style="width: 130px"
        />
      </div>
      <div class="filter-right" v-if="hasActiveFilter">
        <NButton quaternary size="small" @click="resetFilters">
          <template #icon><i class="ri-refresh-line" /></template>
          重置筛选
        </NButton>
      </div>
    </div>

    <!-- Table or Empty -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <template v-if="!loading && expenses.length === 0">
          <SoloEmpty
            title="暂无私账记录"
            description="未查询到符合条件的记录，可调整筛选或点击右上角「记一笔私账」"
          />
        </template>
        <template v-else>
          <NDataTable
            :columns="columns"
            :data="expenses"
            :row-key="(row: PrivateExpense) => row.id"
            :bordered="false"
            striped
            size="small"
          />
          <div class="pagination-wrap" v-if="total > pageSize">
            <NPagination
              :page="page"
              :page-size="pageSize"
              :item-count="total"
              @update:page="handlePageChange"
            />
          </div>
        </template>
      </NCard>
    </NSpin>

    <!-- Delete confirm -->
    <NModal
      v-model:show="showDeleteModal"
      preset="dialog"
      title="确认删除"
      content="确定要删除这条私账记录吗？"
      positive-text="删除"
      negative-text="取消"
      @positive-click="handleDelete"
      @negative-click="showDeleteModal = false"
    />

    <!-- Private expense drawer -->
    <PrivateExpenseDrawer
      v-model:show="drawerVisible"
      :expense-id="editingExpenseId"
      @success="handleDrawerSuccess"
    />
  </div>
</template>

<style scoped>
.pe-page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
  width: 100%;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.header-left {
  flex: 1;
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
  width: 100%;
  box-sizing: border-box;
}

.stat-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 84px;
  min-width: 0;
  box-sizing: border-box;
  overflow: hidden;
  transition: all 0.2s ease;
}

.stat-card.highlight-card {
  background: color-mix(in oklch, var(--solo-bg-card) 92%, var(--solo-primary) 8%);
  border-color: color-mix(in oklch, var(--solo-border-light) 70%, var(--solo-primary) 30%);
}

.stat-card.highlight-card.is-warn {
  background: color-mix(in oklch, var(--solo-bg-card) 94%, #f59e0b 6%);
  border-color: color-mix(in oklch, var(--solo-border-light) 60%, #f59e0b 40%);
}

.stat-card.highlight-card.is-info {
  background: color-mix(in oklch, var(--solo-bg-card) 94%, #3b82f6 6%);
  border-color: color-mix(in oklch, var(--solo-border-light) 60%, #3b82f6 40%);
}

.stat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 4px;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.stat-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
}

.stat-badge.expense {
  background: color-mix(in oklch, var(--solo-expense) 12%, transparent);
  color: var(--solo-expense);
}

.stat-badge.income {
  background: color-mix(in oklch, var(--solo-income) 12%, transparent);
  color: var(--solo-income);
}

.stat-badge.muted {
  background: var(--solo-bg-hover);
  color: var(--solo-text-muted);
}

.stat-badge.badge-warn {
  background: rgba(245, 158, 11, 0.15);
  color: #d97706;
}

.stat-badge.badge-info {
  background: rgba(59, 130, 246, 0.15);
  color: #2563eb;
}

.stat-badge.badge-success {
  background: color-mix(in oklch, var(--solo-income) 15%, transparent);
  color: var(--solo-income);
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1.2;
  margin-bottom: 3px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-value.expense-text {
  color: var(--solo-expense);
}

.stat-value.income-text {
  color: var(--solo-income);
}

.stat-value.warn-text {
  color: #d97706;
}

.stat-value.info-text {
  color: #2563eb;
}

.stat-desc {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  box-sizing: border-box;
}

.filter-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.filter-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.owner-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.35;
}

.owner-name {
  color: var(--solo-text-primary);
  font-weight: 500;
}

.account-name {
  overflow: hidden;
  color: var(--solo-text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-card {
  border-radius: var(--solo-radius-lg);
}

.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 480px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .page-header {
    flex-direction: column;
    align-items: stretch;
  }
  .header-actions {
    justify-content: flex-end;
  }
}
</style>
