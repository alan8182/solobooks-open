<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NDatePicker, NFormItem, NInput, NPagination, NSelect, NSpin, NTag, useMessage } from 'naive-ui'
import { backfillInvoices, getInvoices, getInvoiceSummary } from '@/api/invoices'
import type { InvoiceLedgerItem, InvoiceSummaryResponse } from '@/api/invoices'
import { SoloEmpty, SoloFieldHelp, SoloSkeleton } from '@/components/ui'
import { formatDate, formatMoney, getCurrentMonth } from '@/utils/format'

const router = useRouter()
const message = useMessage()

const monthFrom = ref(getCurrentMonth())
const monthTo = ref(getCurrentMonth())
const direction = ref<string>('')
const keyword = ref('')
const loading = ref(false)
const items = ref<InvoiceLedgerItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const summary = ref<InvoiceSummaryResponse | null>(null)
const backfilling = ref(false)

const directionOptions = [
  { label: '全部', value: '' },
  { label: '销项', value: 'income' },
  { label: '进项', value: 'expense' },
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

onMounted(load)

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
        <NButton type="primary" @click="router.push('/record/invoice-import')">导入发票</NButton>
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
        <NDatePicker v-model:formatted-value="monthFrom" type="month" value-format="yyyy-MM" format="yyyy-MM" style="width: 130px;" clearable />
      </NFormItem>
      <NFormItem label="截止月份" label-placement="left" :show-feedback="false">
        <NDatePicker v-model:formatted-value="monthTo" type="month" value-format="yyyy-MM" format="yyyy-MM" style="width: 130px;" clearable />
      </NFormItem>
      <NFormItem label="方向" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="direction" :options="directionOptions" style="width: 110px;" />
      </NFormItem>
      <NFormItem label="关键字" label-placement="left" :show-feedback="false">
        <NInput v-model:value="keyword" placeholder="发票号 / 对方 / 项目" style="width: 220px;" clearable @keyup.enter="search" />
      </NFormItem>
      <NButton type="primary" @click="search">查询</NButton>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 70px;">类型</th>
                <th style="width: 110px;">开票日期</th>
                <th style="width: 150px;">发票号码</th>
                <th>对方单位</th>
                <th style="width: 160px;">货物/服务</th>
                <th style="width: 120px; text-align: right;">不含税</th>
                <th style="width: 100px; text-align: right;">税额</th>
                <th style="width: 120px; text-align: right;">价税合计</th>
                <th style="width: 120px;">凭证号</th>
              </tr>
            </thead>
            <tbody>
              <!-- 加载态用骨架行，不用转圈：表格的列宽和行高都在，感知上"马上就来" -->
              <template v-if="loading && items.length === 0">
                <tr v-for="n in 8" :key="'sk-' + n" class="skeleton-row">
                  <td><SoloSkeleton variant="rect" height="12px" width="36px" /></td>
                  <td><SoloSkeleton variant="rect" height="12px" width="72px" /></td>
                  <td><SoloSkeleton variant="rect" height="12px" width="104px" /></td>
                  <td><SoloSkeleton variant="rect" height="12px" width="62%" /></td>
                  <td><SoloSkeleton variant="rect" height="12px" width="70%" /></td>
                  <td class="amount-cell"><SoloSkeleton variant="rect" height="12px" width="70px" /></td>
                  <td class="amount-cell"><SoloSkeleton variant="rect" height="12px" width="54px" /></td>
                  <td class="amount-cell"><SoloSkeleton variant="rect" height="12px" width="74px" /></td>
                  <td><SoloSkeleton variant="rect" height="12px" width="44px" /></td>
                </tr>
              </template>
              <tr v-else-if="items.length === 0">
                <td colspan="9" class="report-empty-cell is-component">
                  <SoloEmpty
                    variant="hint"
                    title="本期没有发票"
                    description="该期间没有发票记录；可批量导入或回填历史发票。"
                  >
                    <template #action>
                      <NButton size="small" @click="router.push('/record/invoice-import')">
                        导入发票
                      </NButton>
                    </template>
                  </SoloEmpty>
                </td>
              </tr>
              <template v-else>
                <tr v-for="row in items" :key="row.id" class="clickable" @click="openEntry(row)">
                  <td>
                    <NTag size="small" :type="row.direction === 'income' ? 'success' : 'info'" :bordered="false">
                      {{ row.direction === 'income' ? '销项' : '进项' }}
                    </NTag>
                  </td>
                  <td>{{ row.invoiceDate ? formatDate(row.invoiceDate) : '—' }}</td>
                  <td class="mono">{{ row.invoiceNumber || '—' }}</td>
                  <td>{{ row.direction === 'income' ? row.buyerName : row.sellerName }}</td>
                  <td class="ellipsis" :title="row.itemName">{{ row.itemName || '—' }}</td>
                  <td class="amount-cell">{{ formatMoney(Number(row.amountExclTax)) }}</td>
                  <td class="amount-cell">{{ formatMoney(Number(row.taxAmount)) }}</td>
                  <td class="amount-cell balance">{{ formatMoney(Number(row.amountInclTax)) }}</td>
                  <td>{{ row.voucherRef || '待过账' }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <NPagination
            v-model:page="page"
            v-model:page-size="pageSize"
            :item-count="total"
            :page-sizes="[20, 50, 100]"
            show-size-picker
            @update:page="load"
            @update:page-size="() => { page = 1; load() }"
          />
        </div>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
/* 空态单元格里放的是 SoloEmpty 组件，内边距与斜体交给组件自己表达 */
.report-empty-cell.is-component {
  padding: 0;
  font-style: normal;
}
/* 骨架行与真实行同高，加载完不会跳一下 */
.skeleton-row td { padding-top: 13px; padding-bottom: 13px; }
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
.report-table-wrapper { overflow-x: auto; }
.clickable { cursor: pointer; }
.clickable:hover td { background: var(--solo-primary-50); }
.mono { font-variant-numeric: tabular-nums; }
.ellipsis { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
