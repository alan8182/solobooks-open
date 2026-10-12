<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getIncomeStatement } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type { IncomeStatementResponse, IncomeStatementRow, IncomeStatementViewMode } from '@/api/types'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const month = ref(getCurrentMonth())
const viewMode = ref<IncomeStatementViewMode>('summary')
const loading = ref(false)
const result = ref<IncomeStatementResponse | null>(null)

const modeOptions = [
  { label: '总表', value: 'summary' },
  { label: '季报', value: 'quarterly' },
]

const monthOptions = computed(() => {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    const label = `${d.getFullYear()}年${d.getMonth() + 1}月`
    options.push({ label, value: val })
  }
  return options
})

const displayPeriod = computed(() => {
  const [year, monthValue] = month.value.split('-')
  return `${year}年${Number(monthValue)}月`
})

const netProfitCells = computed(() => {
  if (!result.value) return []
  return result.value.columns.map((column) => ({
    key: column.key,
    label: column.label,
    amount: result.value?.totals.netProfit?.[column.key] ?? 0,
  }))
})

function rowLabel(row: IncomeStatementRow) {
  return row.label.trim()
}

function rowPadding(row: IncomeStatementRow) {
  return `${row.level * 18}px`
}

const ROW_ACCOUNT_MAP: Record<string, { code: string; name: string }> = {
  operating_revenue: { code: '6001', name: '主营业务收入' },
  operating_cost: { code: '5001', name: '主营业务成本' },
  tax_surcharges: { code: '2221', name: '应交税费/税金附加' },
  selling_expenses: { code: '6601', name: '销售费用-推广费' },
  advertising_expense: { code: '6601', name: '广告费和宣传费' },
  admin_expenses: { code: '5699', name: '管理费用' },
  business_entertainment: { code: '5603', name: '业务招待费' },
  research_expense: { code: '5301', name: '研发支出' },
  finance_expenses: { code: '6602', name: '财务费用' },
  interest_expense: { code: '6603', name: '利息费用/手续费' },
  nonoperating_income: { code: '6301', name: '营业外收入' },
}

function getDrillDownAccount(row: IncomeStatementRow) {
  return ROW_ACCOUNT_MAP[row.key]
}

function drillDown(row: IncomeStatementRow) {
  const acc = getDrillDownAccount(row)
  if (!acc) return
  router.push({
    path: '/reports/sub-ledger',
    query: {
      account_code: acc.code,
      account_name: acc.name,
      month: month.value,
      from: 'income-statement',
    },
  })
}

function amountClass(value: number, row?: IncomeStatementRow) {
  if (value < 0) return 'expense'
  if (row?.isSubtotal || row?.key === 'operating_revenue') return 'income'
  return ''
}

async function load() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    result.value = await getIncomeStatement(
      month.value,
      viewMode.value,
    )
  } catch {
    message.error('加载利润表失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>利润表</h2>
          <p class="report-subtitle">按小企业会计准则展示总表和季度利润结构</p>
        </div>
      </div>
      <div class="report-header-right income-header-actions">
        <NRadioGroup
          v-model:value="viewMode"
          size="small"
          @update:value="load"
        >
          <NRadioButton
            v-for="opt in modeOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </NRadioButton>
        </NRadioGroup>
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="load"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <NCard :bordered="false" class="report-card mb-4">
          <div class="income-summary-head">
            <div>
              <h3 class="report-section-title">净利润</h3>
              <p class="income-period">{{ displayPeriod }} · {{ viewMode === 'summary' ? '总表' : '季报' }}</p>
            </div>
            <span class="income-unit">单位：元</span>
          </div>
          <div class="report-summary-row income-summary-row">
            <div v-for="cell in netProfitCells" :key="cell.key" class="report-summary-item">
              <span class="report-summary-label">{{ cell.label }}</span>
              <span class="report-summary-val" :class="amountClass(cell.amount)">
                {{ formatMoney(cell.amount) }}
              </span>
            </div>
          </div>
        </NCard>

        <NCard :bordered="false" class="report-card">
          <div class="income-table-meta">
            <h3 class="report-section-title">{{ viewMode === 'summary' ? '利润表总表' : '利润表季报' }}</h3>
            <span>单位：元</span>
          </div>
          <div class="report-table-wrapper income-table-wrapper">
            <table class="report-table income-table">
              <thead>
                <tr>
                  <th class="item-col">项目</th>
                  <th
                    v-for="column in result.columns"
                    :key="column.key"
                    class="amount-head"
                  >
                    {{ column.label }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in result.rows"
                  :key="row.key"
                  :class="{ 'subtotal-row': row.isSubtotal, 'clickable-row': Boolean(getDrillDownAccount(row)) }"
                  @click="getDrillDownAccount(row) ? drillDown(row) : undefined"
                >
                  <td class="item-cell" :style="{ paddingLeft: rowPadding(row) }">
                    <span :class="{ 'account-link': Boolean(getDrillDownAccount(row)) }">{{ rowLabel(row) }}</span>
                    <span v-if="getDrillDownAccount(row)" class="drill-tag">明细账 ↗</span>
                  </td>
                  <td
                    v-for="column in result.columns"
                    :key="column.key"
                    class="amount-cell"
                    :class="amountClass(row.values[column.key] || 0, row)"
                  >
                    {{ formatMoney(row.values[column.key] || 0) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.income-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.income-summary-head,
.income-table-meta {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}
.income-period,
.income-unit,
.income-table-meta span {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
  white-space: nowrap;
}
.income-summary-row {
  border-bottom: 0;
  padding-bottom: 0;
  flex-wrap: wrap;
}
.income-table-wrapper {
  overflow-x: auto;
}
.income-table {
  min-width: 680px;
}
.item-col {
  min-width: 280px;
}
.amount-head {
  text-align: right;
  min-width: 128px;
}
.item-cell {
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.subtotal-row td {
  font-weight: 750;
  color: var(--solo-text-primary);
  background: rgba(15, 23, 42, 0.025);
}
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }

.clickable-row {
  cursor: pointer;
  transition: background-color 0.15s ease;
}
.clickable-row:hover {
  background: rgba(22, 93, 255, 0.05);
}
.account-link {
  font-weight: 500;
}
.drill-tag {
  margin-left: 8px;
  font-size: 11px;
  color: var(--solo-primary, #165dff);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.clickable-row:hover .drill-tag {
  opacity: 1;
}

@media (max-width: 720px) {
  .income-header-actions {
    justify-content: flex-start;
    width: 100%;
  }
  .income-summary-head,
  .income-table-meta {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
