<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NInput,
  NSelect,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
  type DataTableSortState,
} from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { formatDate, formatMoney } from '@/utils/format'
import { moneyPresentation } from '@/utils/accounting'
import { exportLedgerToExcel } from '@/utils/export'
import {
  getEntryNaturePresentation,
  matchesEntryNature,
  type EntryNatureFilter,
} from '@/utils/entryNature'
import { normalizedPostingStatus, postingStatusPresentation } from '@/utils/postingStatus'
import type { Entry } from '@/api/types'

type TimeFilter = 'week' | 'month' | 'quarter' | 'year' | 'all'

const router = useRouter()
const entryStore = useEntryStore()
const message = useMessage()

const keyword = ref('')
const timeFilter = ref<TimeFilter>('year')
const natureFilter = ref<EntryNatureFilter>('all')
const categoryFilter = ref<string | null>(null)
const billingStatusFilter = ref<string | null>(null)
const postingStatusFilter = ref<string | null>(null)
const sorter = ref<DataTableSortState | null>({
  columnKey: 'occurDate',
  order: 'descend',
  sorter: 'default',
})

const timeOptions = [
  { key: 'week' as const, label: '本周' },
  { key: 'month' as const, label: '本月' },
  { key: 'quarter' as const, label: '本季度' },
  { key: 'year' as const, label: '今年' },
  { key: 'all' as const, label: '全部' },
]

const natureOptions = [
  { key: 'all' as const, label: '全部' },
  { key: 'income' as const, label: '收入' },
  { key: 'expense' as const, label: '支出' },
  { key: 'current_account' as const, label: '往来/垫付' },
  { key: 'capital' as const, label: '股东投入' },
]

const billingStatusOptions = [
  { label: '全部单据状态', value: 'all' },
  { label: '待补发票/收据', value: 'pending' },
  { label: '已有发票/收据', value: 'complete' },
]

const postingStatusOptions = [
  { label: '已过账', value: 'posted' },
  { label: '草稿', value: 'draft' },
]

const pagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  onChange: (page: number) => {
    pagination.page = page
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.pageSize = pageSize
    pagination.page = 1
  },
  prefix: ({ itemCount }: { itemCount?: number }) => `共 ${itemCount ?? 0} 笔`,
})

function entryTimestamp(entry: Entry): number {
  const value = entry.occurDate || entry.created_at
  if (!value) return 0
  const timestamp = new Date(value).getTime()
  return Number.isNaN(timestamp) ? 0 : timestamp
}

function isInRange(entry: Entry, range: TimeFilter): boolean {
  if (range === 'all') return true
  const timestamp = entryTimestamp(entry)
  if (!timestamp) return true

  const now = new Date()
  if (range === 'year') {
    const start = new Date(now.getFullYear(), 0, 1, 0, 0, 0, 0).getTime()
    const end = new Date(now.getFullYear(), 11, 31, 23, 59, 59, 999).getTime()
    return timestamp >= start && timestamp <= end
  }

  const start = new Date(now)
  start.setHours(0, 0, 0, 0)
  const days = { week: 7, month: 30, quarter: 90 }[range]
  start.setDate(start.getDate() - days)
  const end = new Date(now)
  end.setHours(23, 59, 59, 999)
  return timestamp >= start.getTime() && timestamp <= end.getTime()
}

function hasDocument(entry: Entry): boolean {
  return Boolean(entry.billingStatus && entry.billingStatus !== 'no_document')
}

function matchesKeyword(entry: Entry, query: string): boolean {
  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  if (!normalizedQuery) return true
  return [
    entry.title,
    entry.merchant,
    entry.category,
    entry.accountName,
    entry.invoiceNumber,
    entry.remark,
  ].some((value) => value?.toLocaleLowerCase('zh-CN').includes(normalizedQuery))
}

const filteredEntries = computed(() => entryStore.activeEntries.filter((entry) => {
  if (!matchesKeyword(entry, keyword.value)) return false
  if (!isInRange(entry, timeFilter.value)) return false
  if (!matchesEntryNature(entry, natureFilter.value)) return false
  if (categoryFilter.value && entry.category !== categoryFilter.value) return false
  if (billingStatusFilter.value === 'pending' && hasDocument(entry)) return false
  if (billingStatusFilter.value === 'complete' && !hasDocument(entry)) return false
  if (postingStatusFilter.value && normalizedPostingStatus(entry.voucherStatus) !== postingStatusFilter.value) return false
  return true
}))

const totalIncome = computed(() => filteredEntries.value
  .filter((entry) => entry.direction === 'income')
  .reduce((sum, entry) => sum + entry.amount, 0))

const totalExpense = computed(() => filteredEntries.value
  .filter((entry) => entry.direction === 'expense')
  .reduce((sum, entry) => sum + entry.amount, 0))

const netProfit = computed(() => totalIncome.value - totalExpense.value)
const pendingCount = computed(() => filteredEntries.value.filter((entry) => !hasDocument(entry)).length)

const categoryOptions = computed(() => Array.from(new Set(
  entryStore.activeEntries.map((entry) => entry.category).filter(Boolean),
)).sort().map((category) => ({ label: category, value: category })))

const hasActiveFilters = computed(() => Boolean(
  keyword.value.trim()
  || timeFilter.value !== 'year'
  || natureFilter.value !== 'all'
  || categoryFilter.value
  || billingStatusFilter.value
  || postingStatusFilter.value,
))

function dateLabel(entry: Entry): string {
  const value = entry.occurDate || entry.created_at
  return value ? formatDate(value) : '日期待补'
}

function billingStatusLabel(entry: Entry): string {
  return hasDocument(entry) ? '已有单据' : '待补单据'
}

const columns = computed<DataTableColumns<Entry>>(() => [
  {
    title: '日期',
    key: 'occurDate',
    width: 112,
    sorter: (a, b) => entryTimestamp(a) - entryTimestamp(b),
    sortOrder: sorter.value?.columnKey === 'occurDate' ? sorter.value.order : false,
    render: (row) => h('span', { class: 'date-cell' }, dateLabel(row)),
  },
  {
    title: '摘要',
    key: 'title',
    minWidth: 210,
    ellipsis: { tooltip: true },
    sorter: (a, b) => a.title.localeCompare(b.title, 'zh-CN'),
    sortOrder: sorter.value?.columnKey === 'title' ? sorter.value.order : false,
    render: (row) => h('div', { class: 'summary-cell' }, [
      h('span', { class: 'entry-title' }, row.title || '未命名记录'),
      h('span', { class: 'entry-meta' }, row.merchant || row.accountName || '暂无交易方'),
    ]),
  },
  {
    title: '分类',
    key: 'category',
    width: 132,
    ellipsis: { tooltip: true },
    sorter: (a, b) => (a.category || '').localeCompare(b.category || '', 'zh-CN'),
    sortOrder: sorter.value?.columnKey === 'category' ? sorter.value.order : false,
    render: (row) => row.category || '未分类',
  },
  {
    title: '性质',
    key: 'nature',
    width: 86,
    render: (row) => {
      const presentation = getEntryNaturePresentation(row.direction, row.category)
      return h(NTag, {
        size: 'small',
        bordered: false,
        style: { color: presentation.color, backgroundColor: presentation.background },
      }, { default: () => presentation.detailLabel })
    },
  },
  {
    title: '单据',
    key: 'billingStatus',
    width: 100,
    render: (row) => h(NTag, {
      size: 'small',
      bordered: false,
      type: hasDocument(row) ? 'success' : 'warning',
    }, { default: () => billingStatusLabel(row) }),
  },
  {
    title: '状态',
    key: 'postingStatus',
    width: 84,
    render: (row) => {
      const presentation = postingStatusPresentation(row.voucherStatus)
      if (!presentation) return null
      return h(NTag, {
        size: 'small',
        bordered: false,
        type: presentation.tagType,
      }, { default: () => presentation.label })
    },
  },
  {
    title: '金额',
    key: 'amount',
    width: 136,
    align: 'right',
    sorter: (a, b) => a.amount - b.amount,
    sortOrder: sorter.value?.columnKey === 'amount' ? sorter.value.order : false,
    render: (row) => {
      const presentation = moneyPresentation(row.direction, row.category, row.amount)
      return h('span', {
        class: ['amount-cell', presentation.tone],
      }, presentation.text)
    },
  },
  {
    title: '',
    key: 'actions',
    width: 72,
    align: 'right',
    render: (row) => h(NButton, {
      text: true,
      type: 'primary',
      onClick: (event: MouseEvent) => {
        event.stopPropagation()
        goDetail(row)
      },
    }, { default: () => '查看' }),
  },
])

watch(
  [keyword, timeFilter, natureFilter, categoryFilter, billingStatusFilter, postingStatusFilter],
  () => { pagination.page = 1 },
)

watch(filteredEntries, (entries) => {
  const lastPage = Math.max(1, Math.ceil(entries.length / pagination.pageSize))
  if (pagination.page > lastPage) pagination.page = lastPage
})

function goDetail(entry: Entry) {
  router.push(`/ledger/${entry.id}`)
}

function handleSorterChange(nextSorter: DataTableSortState | DataTableSortState[] | null) {
  sorter.value = Array.isArray(nextSorter) ? (nextSorter[0] ?? null) : nextSorter
}

function resetFilters() {
  keyword.value = ''
  timeFilter.value = 'year'
  natureFilter.value = 'all'
  categoryFilter.value = null
  billingStatusFilter.value = null
  postingStatusFilter.value = null
}

function handleExportExcel() {
  try {
    exportLedgerToExcel(filteredEntries.value)
    message.success(`已导出 ${filteredEntries.value.length} 笔记录`)
  } catch {
    message.error('导出失败，请稍后重试')
  }
}
</script>

<template>
  <div class="ledger-page">
    <div class="page-header">
      <div>
        <h2>经营账本</h2>
        <p>流水清晰可查，所有账目均由 AI 会计实时分类归集</p>
      </div>
      <NSpace>
        <NButton :disabled="filteredEntries.length === 0" @click="handleExportExcel">
          <template #icon><i class="ri-download-2-line" /></template>
          导出 Excel
        </NButton>
        <NButton type="primary" @click="router.push('/copilot')">
          <template #icon><i class="ri-sparkling-fill" /></template>
          AI 记账
        </NButton>
      </NSpace>
    </div>

    <NCard :bordered="false" class="summary-card">
      <div class="summary-top">
        <span class="summary-label">当前结果净利润</span>
        <span class="summary-value" :class="netProfit >= 0 ? 'text-green' : 'text-red'">
          {{ netProfit >= 0 ? '+' : '' }}{{ formatMoney(netProfit) }}
        </span>
      </div>
      <div class="summary-ie">
        <div class="ie-item">
          <span class="ie-label">收入</span>
          <span class="ie-value income">{{ formatMoney(totalIncome) }}</span>
        </div>
        <div class="ie-item">
          <span class="ie-label">支出</span>
          <span class="ie-value expense">{{ formatMoney(totalExpense) }}</span>
        </div>
        <div class="summary-stats">
          <span>共 {{ filteredEntries.length }} 笔</span>
          <span v-if="pendingCount">{{ pendingCount }} 笔待补单据</span>
        </div>
      </div>
    </NCard>

    <section class="ledger-workspace" aria-label="账本查询结果">
      <div class="filter-toolbar">
        <NInput
          v-model:value="keyword"
          clearable
          class="search-input"
          placeholder="搜索摘要、交易方、分类或发票号"
          aria-label="搜索账本"
          :input-props="{ name: 'ledger-search' }"
        />
        <NSelect
          v-model:value="categoryFilter"
          clearable
          filterable
          class="filter-select"
          placeholder="全部分类"
          :input-props="{ name: 'ledger-category' }"
          :options="categoryOptions"
        />
        <NSelect
          v-model:value="billingStatusFilter"
          clearable
          class="filter-select status-select"
          placeholder="全部单据状态"
          :input-props="{ name: 'ledger-document-status' }"
          :options="billingStatusOptions.filter(option => option.value !== 'all')"
        />
        <NSelect
          v-model:value="postingStatusFilter"
          clearable
          class="filter-select posting-status-select"
          placeholder="全部过账状态"
          :input-props="{ name: 'ledger-posting-status' }"
          :options="postingStatusOptions"
        />
        <NButton v-if="hasActiveFilters" quaternary @click="resetFilters">重置</NButton>
      </div>

      <div class="quick-filters">
        <div class="filter-group" aria-label="时间范围">
          <span class="filter-label">时间</span>
          <button
            v-for="option in timeOptions"
            :key="option.key"
            class="chip"
            :class="{ active: timeFilter === option.key }"
            :aria-pressed="timeFilter === option.key"
            @click="timeFilter = option.key"
          >{{ option.label }}</button>
        </div>
        <div class="filter-group" aria-label="收支性质">
          <span class="filter-label">性质</span>
          <button
            v-for="option in natureOptions"
            :key="option.key"
            class="chip"
            :class="{ active: natureFilter === option.key }"
            :aria-pressed="natureFilter === option.key"
            @click="natureFilter = option.key"
          >{{ option.label }}</button>
        </div>
      </div>

      <NDataTable
        class="ledger-table"
        :bordered="false"
        :single-line="true"
        :columns="columns"
        :data="filteredEntries"
        :pagination="pagination"
        :row-key="(row: Entry) => row.id"
        :scroll-x="920"
        :row-props="(row: Entry) => ({
          tabindex: 0,
          role: 'link',
          'aria-label': `查看${row.title || '账本记录'}详情`,
          onClick: () => goDetail(row),
          onKeydown: (event: KeyboardEvent) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              goDetail(row)
            }
          },
        })"
        @update:sorter="handleSorterChange"
      >
        <template #empty>
          <NEmpty :description="hasActiveFilters ? '没有符合条件的记录' : '还没有账本记录'">
            <template #extra>
              <NButton v-if="hasActiveFilters" @click="resetFilters">清除筛选</NButton>
              <NButton v-else type="primary" @click="router.push('/copilot')">
                <template #icon><i class="ri-sparkling-fill" /></template>
                AI 记第一笔
              </NButton>
            </template>
          </NEmpty>
        </template>
      </NDataTable>
    </section>
  </div>
</template>

<style scoped>
.ledger-page {
  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.page-header p {
  margin: 4px 0 0;
  color: var(--solo-text-secondary);
  font-size: 13px;
}

.summary-card {
  margin-bottom: 16px;
  border-radius: var(--solo-radius-lg);
}

.summary-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.summary-label {
  color: var(--solo-text-secondary);
  font-size: 13px;
}

.summary-value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.text-green { color: var(--solo-income); }
.text-red { color: var(--solo-expense); }

.summary-ie {
  display: flex;
  align-items: flex-end;
  gap: 32px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}

.ie-item {
  display: flex;
  min-width: 112px;
  flex-direction: column;
  gap: 2px;
}

.ie-label {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.ie-value {
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.ie-value.income { color: var(--solo-income); }
.ie-value.expense { color: var(--solo-expense); }

.summary-stats {
  display: flex;
  flex: 1;
  justify-content: flex-end;
  gap: 16px;
  color: var(--solo-text-muted);
  font-size: 12px;
}

.ledger-workspace {
  overflow: hidden;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}

.filter-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
}

.search-input { width: min(360px, 100%); }
.filter-select { width: 168px; }
.status-select { width: 156px; }
.posting-status-select { width: 132px; }

.quick-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 24px;
  padding: 0 16px 14px;
  border-bottom: 1px solid var(--solo-border-light);
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.filter-label {
  margin-right: 2px;
  color: var(--solo-text-muted);
  font-size: 12px;
}

.chip {
  min-height: 28px;
  padding: 4px 11px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
  cursor: pointer;
  font-size: 12px;
  transition: background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out;
  white-space: nowrap;
}

.chip:hover {
  border-color: var(--solo-border);
  color: var(--solo-primary);
}

.chip.active {
  border-color: var(--solo-primary-soft);
  background: var(--solo-primary-soft);
  color: var(--solo-primary);
  font-weight: 500;
}

.ledger-table :deep(.n-data-table-tr) {
  cursor: pointer;
}

.ledger-table :deep(.n-data-table-tr:focus-visible) {
  outline: 2px solid rgba(22, 93, 255, 0.42);
  outline-offset: -2px;
}

.ledger-table :deep(.n-data-table-th) {
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.ledger-table :deep(.n-data-table-td) {
  padding-top: 13px;
  padding-bottom: 13px;
}

.date-cell,
.amount-cell {
  font-variant-numeric: tabular-nums;
}

.date-cell {
  color: var(--solo-text-secondary);
}

.summary-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.entry-title,
.entry-meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-title {
  color: var(--solo-text-primary);
  font-weight: 500;
}

.entry-meta {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.amount-cell {
  font-weight: 600;
}

.amount-cell.income { color: var(--solo-income); }
.amount-cell.expense { color: var(--solo-expense); }
.amount-cell.neutral { color: var(--solo-text-primary); }

@media (max-width: 760px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .page-header :deep(.n-space) {
    justify-content: flex-end;
  }

  .filter-toolbar {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .search-input {
    width: 100%;
  }

  .filter-select,
  .status-select {
    flex: 1;
    min-width: 140px;
  }

  .summary-ie {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 12px 24px;
  }

  .summary-stats {
    flex-basis: 100%;
    justify-content: flex-start;
  }
}
</style>
