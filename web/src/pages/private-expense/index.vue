<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { h, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, NDataTable, NModal, useMessage, NStatistic, NSpin, NPagination } from 'naive-ui'
import { getExpenses, deleteExpense, getExpenseSummary } from '@/api/private-expense'
import type { PrivateExpense, PrivateExpenseSummary } from '@/api/private-expense'
import { formatMoney } from '@/utils/format'
import apiClient from '@/api/client'

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const expenses = ref<PrivateExpense[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
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
const ownerName = ref('')
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

function isRecoverableDeposit(row: PrivateExpense) {
  return row.direction !== 'income' && row.category === '押金保证金'
}

async function loadData() {
  loading.value = true
  try {
    const [listRes, summaryRes] = await Promise.all([
      getExpenses(page.value, pageSize.value, ownerName.value),
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

function handleOwnerChange() {
  page.value = 1
  loadData()
}

function selectOwner(value: string) {
  if (ownerName.value === value) return
  ownerName.value = value
  handleOwnerChange()
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

const columns = [
  { title: '日期', key: 'occur_date', width: 96 },
  {
    title: '类型', key: 'direction', width: 70,
    render: (row: PrivateExpense) => {
      const isIncome = row.direction === 'income'
      return h(NTag, { size: 'small', type: isIncome ? 'success' : 'error', bordered: false }, { default: () => isIncome ? '收入' : '支出' })
    },
  },
  { title: '事由', key: 'title', width: 180, ellipsis: { tooltip: true } },
  {
    title: '归属 / 账户', key: 'owner_name', width: 130,
    render: (row: PrivateExpense) => h('div', { class: 'owner-cell' }, [
      h('span', { class: 'owner-name' }, row.owner_name || '本人'),
      h('span', { class: 'account-name' }, row.private_account || payerLabels[row.payer] || '其他'),
    ]),
  },
  {
    title: '金额', key: 'amount', width: 112,
    render: (row: PrivateExpense) => {
      const isIncome = row.direction === 'income'
      return h('span', {
        style: { color: isIncome ? 'var(--solo-income)' : 'var(--solo-expense)', fontWeight: 600 },
      }, `${isIncome ? '+' : '-'}${formatMoney(row.amount)}`)
    },
  },
  {
    title: '分类', key: 'category', width: 120,
    render: (row: PrivateExpense) => row.category ? h(NTag, { size: 'small', bordered: false }, { default: () => row.category }) : '-',
  },
  {
    title: '状态', key: 'invoice_status', width: 100,
    render: (row: PrivateExpense) => {
      if (isRecoverableDeposit(row)) {
        return h(NTag, { size: 'small', type: 'info', bordered: false }, { default: () => '可收回' })
      }
      if (row.direction === 'income') {
        const available = row.is_available === undefined ? true : Boolean(row.is_available)
        return h(NTag, { size: 'small', type: available ? 'success' : 'warning', bordered: false }, {
          default: () => available ? '可用' : '受限',
        })
      }
      if (row.invoice_status === 'has_invoice') {
        return h(NTag, { size: 'small', type: 'success', bordered: false }, { default: () => '有发票' })
      } else if (row.invoice_status === 'substitute') {
        const entryName = getOfficialEntryName(row.substitute_entry_id || '')
        return h(NSpace, { vertical: true, size: 2 }, {
          default: () => [
            h(NTag, { size: 'small', type: 'warning', bordered: false }, { default: () => '后续补票/冲抵' }),
            row.substitute_entry_id ? h('span', { style: { fontSize: '11px', color: 'var(--solo-text-muted)', display: 'block', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, `关联: ${entryName}`) : null
          ]
        })
      } else {
        return h(NTag, { size: 'small', type: 'error', bordered: false }, { default: () => '无发票' })
      }
    }
  },
  {
    title: '操作', key: 'actions', width: 88,
    render: (row: PrivateExpense) => h(NSpace, { size: 4, wrap: false }, {
      default: () => [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => router.push(`/private-expenses/${row.id}`) }, { default: () => '编辑' }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => confirmDelete(row.id) }, { default: () => '删除' }),
      ],
    }),
  },
]

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(loadData)
</script>

<template>
  <div class="pe-page">
    <div class="page-header">
      <h2>小私账</h2>
      <NButton size="small" type="primary" @click="router.push('/private-expenses/create')">新增记录</NButton>
    </div>

    <!-- Summary -->
    <div class="summary-bar">
      <NCard :bordered="false" class="stat-card">
        <NStatistic :value="summary.count" label="总笔数" />
      </NCard>
      <NCard :bordered="false" class="stat-card">
        <NStatistic :value="formatMoney(summary.income_amount)" label="私账收入" />
      </NCard>
      <NCard :bordered="false" class="stat-card">
        <NStatistic :value="formatMoney(summary.expense_amount)" label="私账支出" />
      </NCard>
      <NCard :bordered="false" class="stat-card">
        <NStatistic :value="formatMoney(summary.deposit_amount || 0)" label="可收回押金" />
      </NCard>
      <NCard :bordered="false" class="stat-card offset-card">
        <NStatistic :value="formatMoney(summary.offset_amount)" label="可冲销金额" />
        <p class="offset-note">可用收入：{{ formatMoney(summary.available_income_amount || 0) }}</p>
        <p class="offset-note">冲销后净额：{{ formatMoney(summary.balance_amount) }}</p>
      </NCard>
    </div>

    <div v-if="!ownerName && (summary.owners?.length || 0) > 1" class="owner-breakdown">
      <button
        v-for="owner in summary.owners"
        :key="owner.owner_name"
        type="button"
        class="owner-summary-row"
        @click="ownerName = owner.owner_name; handleOwnerChange()"
      >
        <strong>{{ owner.owner_name }}</strong>
        <span>可用收入 {{ formatMoney(owner.available_income_amount) }}</span>
        <span>经营支出 {{ formatMoney(owner.expense_amount) }}</span>
        <span>可冲销 {{ formatMoney(owner.offset_amount) }}</span>
        <span>净额 {{ formatMoney(owner.balance_amount) }}</span>
      </button>
    </div>

    <div class="list-toolbar">
      <span class="filter-label">归属人</span>
      <div class="owner-pills" role="group" aria-label="按资金归属人筛选">
        <button
          type="button"
          class="owner-pill"
          :class="{ active: ownerName === '' }"
          :aria-pressed="ownerName === ''"
          @click="selectOwner('')"
        >
          全部
        </button>
        <button
          v-for="option in ownerOptions"
          :key="option.value"
          type="button"
          class="owner-pill"
          :class="{ active: ownerName === option.value }"
          :aria-pressed="ownerName === option.value"
          @click="selectOwner(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <!-- Table -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
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
      </NCard>
    </NSpin>

    <!-- Delete confirm -->
    <NModal v-model:show="showDeleteModal" preset="dialog" title="确认删除"
      content="确定要删除这条私账记录吗？"
      positive-text="删除"
      negative-text="取消"
      @positive-click="handleDelete"
      @negative-click="showDeleteModal = false"
    />
  </div>
</template>

<style scoped>
.pe-page {
  max-width: 960px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.summary-bar {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.stat-card {
  flex: 1;
  border-radius: var(--solo-radius-lg);
}
.offset-note {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.owner-breakdown {
  margin: -2px 0 14px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  overflow: hidden;
  background: var(--solo-bg-card);
}
.owner-summary-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(100px, 1.3fr) repeat(4, minmax(92px, 1fr));
  gap: 12px;
  align-items: center;
  padding: 10px 14px;
  border: 0;
  border-bottom: 1px solid var(--solo-border-light);
  background: transparent;
  color: var(--solo-text-secondary);
  text-align: left;
  cursor: pointer;
}
.owner-summary-row:last-child {
  border-bottom: 0;
}
.owner-summary-row:hover,
.owner-summary-row:focus-visible {
  background: var(--solo-bg-page);
}
.owner-summary-row strong {
  color: var(--solo-text-primary);
}
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-bottom: 10px;
}
.filter-label {
  flex: none;
  color: var(--solo-text-secondary);
  font-size: 13px;
}
.owner-pills {
  display: flex;
  min-width: 0;
  gap: 6px;
  overflow-x: auto;
  padding: 2px;
  scrollbar-width: none;
}
.owner-pills::-webkit-scrollbar {
  display: none;
}
.owner-pill {
  flex: none;
  min-height: 30px;
  padding: 4px 14px;
  border: 1px solid var(--solo-border-light);
  border-radius: 999px;
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition: background-color 160ms ease-out, border-color 160ms ease-out, color 160ms ease-out;
}
.owner-pill:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}
.owner-pill:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 2px;
}
.owner-pill.active {
  border-color: var(--solo-primary);
  background: var(--solo-primary);
  color: color-mix(in oklch, var(--solo-bg-card) 96%, var(--solo-primary));
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
@media (max-width: 820px) {
  .summary-bar {
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .stat-card {
    min-width: 150px;
  }
  .owner-summary-row {
    grid-template-columns: 1fr 1fr;
  }
  .list-toolbar {
    justify-content: flex-start;
  }
}
</style>
