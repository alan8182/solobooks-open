<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NInput, NSelect, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { backfillInvoices, getInvoices, getInvoiceSummary } from '@/api/invoices'
import type { InvoiceLedgerItem, InvoiceSummaryResponse } from '@/api/invoices'
import { SoloFieldHelp, SoloMonthPicker, SoloTable } from '@/components/ui'
import { formatDate, formatMoney, getCurrentMonth } from '@/utils/format'
import InvoiceImportDrawer from './InvoiceImportDrawer.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const initialMonth = typeof route.query.month === 'string' && route.query.month ? route.query.month : getCurrentMonth()
const monthFrom = ref(initialMonth)
const monthTo = ref(initialMonth)
const direction = ref<string>(typeof route.query.direction === 'string' ? route.query.direction : '')
const posting = ref<string>('')
const keyword = ref('')
const loading = ref(false)
const items = ref<InvoiceLedgerItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const summary = ref<InvoiceSummaryResponse | null>(null)
const backfilling = ref(false)
const showImport = ref(false)

function openImport() {
  showImport.value = true
}

const directionOptions = [
  { label: '全部', value: '' },
  { label: '销项', value: 'income' },
  { label: '进项', value: 'expense' },
]

const postingOptions = [
  { label: '全部', value: '' },
  { label: '待过账', value: 'pending' },
  { label: '已过账', value: 'posted' },
]

const totals = computed(() => summary.value?.totals)

async function load() {
  loading.value = true
  try {
    const [list, summaryData] = await Promise.all([
      getInvoices({
        month_from: monthFrom.value || undefined,
        month_to: monthTo.value || undefined,
        direction: direction.value || undefined,
        posting: posting.value || undefined,
        keyword: keyword.value.trim() || undefined,
        page: page.value,
        page_size: pageSize.value,
      }),
      getInvoiceSummary({
        month_from: monthFrom.value || undefined,
        month_to: monthTo.value || undefined,
      }),
    ])
    items.value = list.items
    total.value = list.total
    summary.value = summaryData
  } catch {
    message.error('加载发票台账失败')
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function openEntry(row: InvoiceLedgerItem) {
  if (!row.entryId) {
    message.warning('该发票没有关联凭证（可能是历史回填数据）')
    return
  }
  router.push(`/vouchers/${row.entryId}`)
}

async function runBackfill() {
  backfilling.value = true
  try {
    const result = await backfillInvoices()
    message.success(`已扫描 ${result.scanned} 条历史记录，新增 ${result.created} 张发票`)
    await load()
  } catch {
    message.error('回填失败，请稍后重试')
  } finally {
    backfilling.value = false
  }
}

const columns: DataTableColumns<InvoiceLedgerItem> = [
  {
    title: '类型',
    key: 'direction',
    width: 70,
    render(row) {
      return h(
        NTag,
        {
          size: 'small',
          type: row.direction === 'income' ? 'success' : 'info',
          bordered: false,
        },
        { default: () => (row.direction === 'income' ? '销项' : '进项') },
      )
    },
  },
  {
    title: '开票日期',
    key: 'invoiceDate',
    width: 110,
    render(row) {
      return row.invoiceDate ? formatDate(row.invoiceDate) : '—'
    },
  },
  {
    title: '发票号码',
    key: 'invoiceNumber',
    width: 170,
    className: 'mono',
    render(row) {
      return row.invoiceNumber || '—'
    },
  },
  {
    title: '对方单位',
    key: 'counterparty',
    minWidth: 160,
    ellipsis: { tooltip: true },
    render(row) {
      return (row.direction === 'income' ? row.buyerName : row.sellerName) || '—'
    },
  },
  {
    title: '货物/服务',
    key: 'itemName',
    width: 160,
    ellipsis: { tooltip: true },
    render(row) {
      return row.itemName || '—'
    },
  },
  {
    title: '不含税',
    key: 'amountExclTax',
    width: 120,
    align: 'right',
    className: 'amount-cell',
    render(row) {
      return formatMoney(Number(row.amountExclTax))
    },
  },
  {
    title: '税额',
    key: 'taxAmount',
    width: 100,
    align: 'right',
    className: 'amount-cell',
    render(row) {
      return formatMoney(Number(row.taxAmount))
    },
  },
  {
    title: '价税合计',
    key: 'amountInclTax',
    width: 120,
    align: 'right',
    className: 'amount-cell balance',
    render(row) {
      return formatMoney(Number(row.amountInclTax))
    },
  },
  {
    title: '凭证号',
    key: 'voucherRef',
    width: 120,
    render(row) {
      return row.voucherRef || '待过账'
    },
  },
]

const pagination = computed(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  pageSizes: [20, 50, 100],
  showSizePicker: true,
  onChange: (p: number) => {
    page.value = p
    load()
  },
  onUpdatePageSize: (s: number) => {
    pageSize.value = s
    page.value = 1
    load()
  },
}))

onMounted(() => {
  load()
  if (route.query.import === 'true' || route.query.import === '1') {
    showImport.value = true
  }
})

watch(() => route.query.import, (val) => {
  if (val === 'true' || val === '1') {
    showImport.value = true
  }
})

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <div class="report-title-section">
          <h2>发票台账</h2>
          <p class="report-subtitle">进项/销项发票统一台账：按期间查询、按方向筛选，行点击直达对应凭证</p>
        </div>
      </div>
      <div class="report-header-right">
        <NButton secondary :loading="backfilling" @click="runBackfill">回填历史发票</NButton>
        <NButton type="primary" @click="openImport">导入发票</NButton>
      </div>
    </div>

    <div class="summary-cards">
      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">
          销项价税合计
          <SoloFieldHelp term="销项" />
        </span>
        <span class="summary-value income">{{ formatMoney(Number(totals?.incomeAmount || 0)) }}</span>
        <span class="summary-sub">{{ totals?.incomeCount || 0 }} 张 · 税额 {{ formatMoney(Number(totals?.incomeTax || 0)) }}</span>
      </NCard>
      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">
          进项价税合计
          <SoloFieldHelp term="进项" />
        </span>
        <span class="summary-value expense">{{ formatMoney(Number(totals?.expenseAmount || 0)) }}</span>
        <span class="summary-sub">{{ totals?.expenseCount || 0 }} 张 · 税额 {{ formatMoney(Number(totals?.expenseTax || 0)) }}</span>
      </NCard>
      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">税额净额（销项-进项）</span>
        <span class="summary-value">{{ formatMoney(Number(totals?.netTax || 0)) }}</span>
        <span class="summary-sub">仅作参考，申报口径以税务作业包为准</span>
      </NCard>
    </div>

    <div class="filter-bar mb-4">
      <NFormItem label="起始月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="monthFrom" style="width: 130px;" clearable placeholder="起始月份" />
      </NFormItem>
      <NFormItem label="截止月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="monthTo" style="width: 130px;" clearable placeholder="截止月份" />
      </NFormItem>
      <NFormItem label="方向" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="direction" :options="directionOptions" style="width: 110px;" />
      </NFormItem>
      <NFormItem label="过账状态" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="posting" :options="postingOptions" style="width: 110px;" />
      </NFormItem>
      <NFormItem label="关键字" label-placement="left" :show-feedback="false">
        <NInput v-model:value="keyword" placeholder="发票号 / 对方 / 项目" style="width: 220px;" clearable @keyup.enter="search" />
      </NFormItem>
      <NButton type="primary" @click="search">查询</NButton>
    </div>

    <NCard :bordered="false" class="report-card">
      <SoloTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :scroll-x="1050"
        :pagination="pagination"
        clickable
        empty-title="本期没有发票"
        empty-description="该期间没有发票记录；可批量导入或回填历史发票。"
        @row-click="openEntry"
      >
        <template #empty-action>
          <NButton size="small" @click="openImport">
            导入发票
          </NButton>
        </template>
      </SoloTable>
    </NCard>

    <!-- 发票导入抽屉：合并自原 /record/invoice-import 页面 -->
    <InvoiceImportDrawer v-model:show="showImport" @success="load" />
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }
.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
/* naive-ui 的内容层类名是 .n-card-content（无下划线）。纵向布局必须打在这一层：
   打在 .summary-card 根节点上时 flex 只有一个子元素，三个 span 仍是行内流，
   22px 的金额会压住 12px 的副文案。 */
.summary-card :deep(.n-card-content) { display: flex; flex-direction: column; gap: 6px; }
.summary-label { font-size: 12px; color: var(--solo-text-muted); }
.summary-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
/* 收支配色只允许来自 token。此前兜底 hex 写反（income→红、expense→绿）且与 token 不同源，
   变量一旦失效整页财务语义反转 —— 不要在这里加 hex 兜底。 */
.summary-value.income { color: var(--solo-income); }
.summary-value.expense { color: var(--solo-expense); }
.summary-sub { font-size: 12px; color: var(--solo-text-muted); }
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 10px 16px;
  background: #ffffff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
:deep(.balance) { font-weight: 700; color: var(--solo-primary); }
</style>
