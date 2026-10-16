<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getIncomeStatement } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type { IncomeStatementResponse, IncomeStatementRow, IncomeStatementViewMode } from '@/api/types'

// ponytail: Boss-centric income statement: 3-step formula card, simplified view hiding zero-balance tax rows
const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const viewMode = ref<IncomeStatementViewMode>('summary')
const hideZeroRows = ref(true)
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

const primaryColKey = computed(() => result.value?.columns[0]?.key || 'current')
const primaryColLabel = computed(() => result.value?.columns[0]?.label || '本月数')

const revenueVal = computed(() => {
  const row = result.value?.rows.find(r => r.key === 'operating_revenue')
  return row ? (row.values[primaryColKey.value] || 0) : 0
})

const netProfitVal = computed(() => {
  return result.value?.totals.netProfit?.[primaryColKey.value] ?? 0
})

const costExpenseVal = computed(() => {
  return Math.max(0, revenueVal.value - netProfitVal.value)
})

const currentMargin = computed(() => {
  if (revenueVal.value <= 0) return null
  return ((netProfitVal.value / revenueVal.value) * 100).toFixed(1)
})

function rowLabel(row: IncomeStatementRow) {
  return row.label.trim()
}

function rowPadding(row: IncomeStatementRow) {
  return `${row.level * 18}px`
}

const ROW_ACCOUNT_MAP: Record<string, { code: string; name: string }> = {
  operating_revenue: { code: '5001', name: '主营业务收入' },
  operating_cost: { code: '5401', name: '主营业务成本' },
  tax_surcharges: { code: '5403', name: '税金及附加' },
  selling_expenses: { code: '5601', name: '销售费用' },
  advertising_expense: { code: '5601015', name: '广告费和宣传费' },
  admin_expenses: { code: '5602', name: '管理费用' },
  business_entertainment: { code: '5602002', name: '业务招待费' },
  research_expense: { code: '4301', name: '研发支出' },
  finance_expenses: { code: '5603', name: '财务费用' },
  interest_expense: { code: '5603001', name: '利息费用/手续费' },
  nonoperating_income: { code: '5301', name: '营业外收入' },
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

const visibleRows = computed(() => {
  if (!result.value) return []
  if (!hideZeroRows.value) return result.value.rows

  // 必须保留的主干节点
  const coreKeys = new Set([
    'operating_revenue',
    'operating_cost',
    'tax_surcharges',
    'selling_expenses',
    'admin_expenses',
    'finance_expenses',
    'operating_profit',
    'profit_total',
    'income_tax_expense',
    'net_profit',
  ])

  return result.value.rows.filter(row => {
    if (coreKeys.has(row.key) || row.isSubtotal) return true
    return result.value?.columns.some(col => Math.abs(row.values[col.key] || 0) > 0.005)
  })
})

const hiddenRowsCount = computed(() => {
  if (!result.value) return 0
  return result.value.rows.length - visibleRows.value.length
})

function handleMonthChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
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
        <NButton text class="report-back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>利润表</h2>
          <p class="report-subtitle">反映企业在特定期间的经营成果与利润形成结构</p>
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
          @update:value="handleMonthChange"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- 老板利润三步公式卡 -->
        <NCard :bordered="false" class="report-card boss-formula-card mb-4">
          <div class="formula-head">
            <div class="formula-title">
              <i class="ri-calculator-line" />
              <span>经营盈利速览（{{ displayPeriod }} · {{ primaryColLabel }}）</span>
            </div>
            <span class="formula-tip">去会计黑话：直接看开票进账、日常开销与真正净赚</span>
          </div>

          <div class="formula-steps">
            <div class="step-card income-step">
              <div class="step-label">1. 营业收入 (开票/收款)</div>
              <div class="step-num income">{{ formatMoney(revenueVal) }}</div>
              <div class="step-note">日常主营业务与销售总额</div>
            </div>

            <div class="step-operator">－</div>

            <div class="step-card expense-step">
              <div class="step-label">2. 综合成本与费用</div>
              <div class="step-num expense">{{ formatMoney(costExpenseVal) }}</div>
              <div class="step-note">采购成本、房租、人工与税金费</div>
            </div>

            <div class="step-operator">＝</div>

            <div class="step-card profit-step">
              <div class="step-label-wrap">
                <span class="step-label">3. 净利润 (真正净赚)</span>
                <span
                  v-if="currentMargin !== null"
                  class="margin-pill"
                  :class="Number(currentMargin) >= 0 ? 'pos' : 'neg'"
                >
                  净利率 {{ currentMargin }}%
                </span>
              </div>
              <div
                class="step-num"
                :class="netProfitVal >= 0 ? 'income' : 'expense'"
              >
                {{ (netProfitVal >= 0 ? '+' : '') + formatMoney(netProfitVal) }}
              </div>
              <div class="step-note">最终属于企业的纯利润</div>
            </div>
          </div>
        </NCard>

        <!-- 法定申报明细表（含精简/全量切换） -->
        <NCard :bordered="false" class="report-card">
          <div class="income-table-meta">
            <div class="meta-left">
              <h3 class="report-section-title mb-0">
                {{ viewMode === 'summary' ? '小企业会计准则利润表（总表）' : '小企业会计准则利润表（季报）' }}
              </h3>
              <div class="view-filter-bar">
                <NButton
                  size="tiny"
                  :type="hideZeroRows ? 'primary' : 'default'"
                  secondary
                  @click="hideZeroRows = !hideZeroRows"
                >
                  <template #icon>
                    <i :class="hideZeroRows ? 'ri-filter-3-line' : 'ri-list-check-2'" />
                  </template>
                  {{ hideZeroRows ? '精简视图（已隐藏零发生行）' : '税局全量 32 行视图' }}
                </NButton>
                <span v-if="hideZeroRows && hiddenRowsCount > 0" class="filter-tip">
                  已隐藏 {{ hiddenRowsCount }} 项零发生额法定行（如消费税、排污费等）· 审计/申报时可一键切换全量
                </span>
              </div>
            </div>
            <span class="income-unit">单位：元</span>
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
                  v-for="row in visibleRows"
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
.mb-0 { margin-bottom: 0 !important; }
.mb-4 { margin-bottom: 16px; }

.income-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* 老板利润三步公式卡 */
.boss-formula-card {
  padding: 18px 20px;
}

.formula-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--solo-border-light, #EDF0F4);
  padding-bottom: 10px;
  flex-wrap: wrap;
}

.formula-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
}

.formula-tip {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
}

.formula-steps {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.step-card {
  flex: 1;
  padding: 14px 16px;
  background: var(--solo-surface-soft, #F1F4F8);
  border-radius: var(--solo-radius-md, 10px);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.step-card.income-step {
  border-left: 3px solid var(--solo-income, #12805C);
}

.step-card.expense-step {
  border-left: 3px solid var(--solo-expense, #C74444);
}

.step-card.profit-step {
  border-left: 3px solid var(--solo-primary, #165DFF);
  background: var(--solo-primary-soft, #EDF3FF);
}

.step-label-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
}

.step-num {
  font-size: 22px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.step-note {
  font-size: 11px;
  color: var(--solo-text-muted, #8A95A3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-operator {
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-muted, #8A95A3);
  flex-shrink: 0;
}

.margin-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  line-height: 1.3;
}

.margin-pill.pos {
  background: var(--solo-income-soft, #EAF7F1);
  color: var(--solo-income, #12805C);
}

.margin-pill.neg {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #C74444);
}

/* 表格元数据与精简视图条 */
.income-table-meta {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.meta-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.view-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-tip {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
}

.income-unit {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
  white-space: nowrap;
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
  color: var(--solo-text-secondary, #586474);
  white-space: nowrap;
}

.subtotal-row td {
  font-weight: 750;
  color: var(--solo-text-primary, #182230);
  background: rgba(15, 23, 42, 0.025);
}

.income { color: var(--solo-income, #12805C); }
.expense { color: var(--solo-expense, #C74444); }

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
  color: var(--solo-primary, #165DFF);
  opacity: 0;
  transition: opacity 0.15s ease;
}

.clickable-row:hover .drill-tag {
  opacity: 1;
}

@media (max-width: 860px) {
  .formula-steps {
    flex-direction: column;
    align-items: stretch;
  }
  .step-operator {
    text-align: center;
  }
}

@media (max-width: 720px) {
  .income-header-actions {
    justify-content: flex-start;
    width: 100%;
  }
  .income-table-meta {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
