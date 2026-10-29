<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getIncomeStatement } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type {
  IncomeStatementBasis,
  IncomeStatementResponse,
  IncomeStatementRow,
  IncomeStatementViewMode,
} from '@/api/types'
import ReportScopeNote from '@/components/business/ReportScopeNote.vue'
import StatementViewToggle from '@/components/business/StatementViewToggle.vue'
import ProStatementHeader from '@/components/business/ProStatementHeader.vue'
import { useStatementViewMode } from '@/composables/useStatementViewMode'
import { formatAmountPlain } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

// ponytail: Boss-centric income statement: 3-step formula card, simplified view hiding zero-balance tax rows
// 会计视图：小企业会计准则利润表全量 32 行（本年累计/本月金额等列），与申报表同构
const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const { mode } = useStatementViewMode()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const viewMode = ref<IncomeStatementViewMode>('summary')
const basis = ref<IncomeStatementBasis>('posting')
const hideZeroRows = ref(true)
const loading = ref(false)
const result = ref<IncomeStatementResponse | null>(null)

const modeOptions = [
  { label: '总表', value: 'summary' },
  { label: '季报', value: 'quarterly' },
]

// 法定口径与账簿、申报表一致；管理口径按业务发生日期归集，只在有跨期补记时才不同
const basisOptions: { label: string; value: IncomeStatementBasis }[] = [
  { label: '法定口径', value: 'posting' },
  { label: '按业务日期', value: 'business' },
]

const backfillImpact = computed(() => result.value?.backfillImpact || null)

function toggleBasis() {
  basis.value = basis.value === 'posting' ? 'business' : 'posting'
  load()
}

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

// §10 三项常驻字段：让用户看见系统知道自己不知道什么
const vatSummary = computed(() => result.value?.vatSummary || null)

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

// 会计视图表头的期间文字（总表=月份；季报=所在季度）
const proPeriodText = computed(() => {
  const [year, monthValue] = month.value.split('-').map(Number)
  if (viewMode.value === 'quarterly') {
    return `${year}年第${Math.floor((monthValue - 1) / 3) + 1}季度`
  }
  return `${year}年${monthValue}月`
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
      basis.value,
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
    <ReportScopeNote
      :scope="basis === 'business'
        ? '管理口径：按业务发生日期归集，用于看清每月真实经营，不作为申报口径'
        : '本表由系统按已过账复式分录自动编制，用于内部经营分析与报税准备'"
    />
    <div
      v-if="backfillImpact && backfillImpact.count > 0"
      class="backfill-impact-strip"
      data-testid="backfill-impact-strip"
    >
      <i class="ri-information-line" />
      <span class="backfill-impact-copy">
        本期有 <strong>{{ backfillImpact.count }}</strong> 笔、金额
        <strong>{{ formatMoney(backfillImpact.amount) }}</strong> 的凭证补记自
        <template v-for="(p, i) in backfillImpact.periods" :key="p.originalPeriod">
          {{ i ? '、' : '' }}{{ p.originalPeriod }}（{{ p.count }} 笔）
        </template>
        ，原始单据日期仍保留在原月份。
      </span>
      <NButton text type="primary" size="tiny" @click="toggleBasis">
        {{ basis === 'posting' ? '按业务日期看看' : '回到法定口径' }}
      </NButton>
    </div>
    <div v-if="vatSummary" class="vat-summary-strip" data-testid="vat-summary-strip">
      <span class="vat-summary-item">
        不含税收入 <strong>{{ formatMoney(vatSummary.netSales) }}</strong>
      </span>
      <span class="vat-summary-divider" />
      <span class="vat-summary-item">
        已计提增值税 <strong>{{ formatMoney(vatSummary.vatAccrued) }}</strong>
      </span>
      <span class="vat-summary-divider" />
      <span class="vat-summary-item">
        待纳税调整
        <strong>{{ formatMoney(vatSummary.pendingCitAdjustment) }}</strong>
        <span v-if="vatSummary.pendingCitItems > 0" class="vat-summary-count">
          （{{ vatSummary.pendingCitItems }} 项，补票后可转回）
        </span>
      </span>
    </div>
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
        <StatementViewToggle />
        <NRadioGroup
          v-model:value="basis"
          size="small"
          @update:value="load"
        >
          <NRadioButton
            v-for="opt in basisOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </NRadioButton>
        </NRadioGroup>
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
        <template v-if="mode === 'boss'">
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
              <div class="step-header">
                <span class="step-badge income">
                  <i class="ri-arrow-up-circle-line" />
                  <span>营业收入</span>
                </span>
                <span class="step-alias">开票 / 收款</span>
              </div>
              <div
                class="step-num"
                :class="revenueVal > 0 ? 'income' : ''"
              >
                {{ formatMoney(revenueVal) }}
              </div>
              <div class="step-note">日常主营业务与销售总额</div>
            </div>

            <div class="step-operator">
              <span class="operator-badge">
                <i class="ri-subtract-line" />
              </span>
            </div>

            <div class="step-card expense-step">
              <div class="step-header">
                <span class="step-badge expense">
                  <i class="ri-arrow-down-circle-line" />
                  <span>成本与费用</span>
                </span>
                <span class="step-alias">采购 / 薪税 / 开销</span>
              </div>
              <div
                class="step-num"
                :class="costExpenseVal > 0 ? 'expense' : ''"
              >
                {{ formatMoney(costExpenseVal) }}
              </div>
              <div class="step-note">采购成本、房租、人工与税金费</div>
            </div>

            <div class="step-operator">
              <span class="operator-badge">
                <i class="ri-equal-line" />
              </span>
            </div>

            <div class="step-card profit-step" :class="{ 'is-loss': netProfitVal < 0 }">
              <div class="step-header">
                <span class="step-badge profit">
                  <i class="ri-funds-line" />
                  <span>落袋净利润</span>
                </span>
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
                :class="netProfitVal > 0 ? 'income' : (netProfitVal < 0 ? 'expense' : '')"
              >
                {{ (netProfitVal > 0 ? '+' : '') + formatMoney(netProfitVal) }}
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

        <!-- 会计视图：小企业会计准则利润表全量行次 -->
        <template v-else>
          <NCard :bordered="false" class="report-card">
            <ProStatementHeader title="利润表" :period-text="proPeriodText" />
            <div class="report-table-wrapper income-table-wrapper">
              <table class="report-table income-table pro-income-table">
                <thead>
                  <tr>
                    <th class="rowno-col">行次</th>
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
                    v-for="(row, idx) in result.rows"
                    :key="row.key"
                    :class="{ 'subtotal-row': row.isSubtotal, 'clickable-row': Boolean(getDrillDownAccount(row)) }"
                    @click="getDrillDownAccount(row) ? drillDown(row) : undefined"
                  >
                    <td class="rowno-cell">{{ idx + 1 }}</td>
                    <td class="item-cell" :style="{ paddingLeft: rowPadding(row) }">
                      <span :class="{ 'account-link': Boolean(getDrillDownAccount(row)) }">{{ rowLabel(row) }}</span>
                      <span v-if="getDrillDownAccount(row)" class="drill-tag">明细账 ↗</span>
                    </td>
                    <td
                      v-for="column in result.columns"
                      :key="column.key"
                      class="amount-cell"
                    >
                      {{ formatAmountPlain(row.values[column.key] || 0) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="pro-compiler-note">编制基础：小企业会计准则；金额单位：元。</p>
          </NCard>
        </template>
            </template>
      <SoloEmpty
        v-else-if="!loading"
        title="本期没有收入和费用"
        description="利润表暂时是空的。记几笔账再回来看。"
        icon="ri-file-chart-line"
      />

    </NSpin>
  </div>
</template>

<style scoped>
.mb-0 { margin-bottom: 0 !important; }
.mb-4 { margin-bottom: 16px; }

.vat-summary-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border: 1px dashed rgba(128, 128, 128, 0.35);
  border-radius: 8px;
  font-size: 12px;
}

.vat-summary-strip strong {
  font-weight: 600;
  margin-left: 2px;
}

.vat-summary-count {
  opacity: 0.7;
}

.backfill-impact-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border: 1px dashed var(--solo-warning, #a05c10);
  border-radius: 8px;
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.backfill-impact-strip strong {
  font-weight: 600;
  color: var(--solo-warning, #a05c10);
}

.backfill-impact-copy {
  flex: 1 1 260px;
}

.vat-summary-divider {
  width: 1px;
  height: 14px;
  background: rgba(128, 128, 128, 0.35);
}

.income-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* 老板利润三步公式卡 */
.boss-formula-card {
  padding: 18px 22px;
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: var(--solo-radius-lg, 12px);
  box-shadow: 0 1px 3px rgba(24, 34, 48, 0.03);
}

.formula-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
  padding-bottom: 10px;
  flex-wrap: wrap;
}

.formula-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
}

.formula-title i {
  color: var(--solo-primary, #1747b0);
  font-size: 16px;
}

.formula-tip {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.formula-steps {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 14px;
}

.step-card {
  flex: 1;
  padding: 14px 16px;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-md, 10px);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
  box-shadow: 0 1px 2px rgba(24, 34, 48, 0.02);
  transition: all 0.2s ease;
}

.step-card:hover {
  border-color: #c7d2fe;
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.05);
}

.step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.step-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
  line-height: 1.4;
}

.step-badge.income {
  background: var(--solo-income-bg, #eaf7f1);
  color: var(--solo-income, #0f7354);
}

.step-badge.expense {
  background: var(--solo-expense-bg, #fbeeee);
  color: var(--solo-expense, #bd3d3d);
}

.step-badge.profit {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}

.step-alias {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}

/* 步骤 3（净利润）：作为计算目标卡片轻度高亮 */
.step-card.profit-step {
  background: var(--solo-bg-blue-soft, #edf3ff);
  border: 1px solid var(--solo-border, #dde3ea);
}

.step-card.profit-step:hover {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.06);
}

.step-card.profit-step.is-loss {
  background: var(--solo-expense-bg, #fbeeee);
  border-color: var(--solo-expense, #bd3d3d);
}

.step-num {
  font-size: 24px;
  font-weight: 700;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
  letter-spacing: -0.5px;
  color: var(--solo-text-primary, #182230);
  margin: 2px 0;
}

.step-num.income {
  color: var(--solo-income, #0f7354);
}

.step-num.expense {
  color: var(--solo-expense, #bd3d3d);
}

.step-note {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 算式连接符：精致居中圆环徽章 */
.step-operator {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.operator-badge {
  width: 26px;
  height: 26px;
  border-radius: 999px;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  box-shadow: 0 1px 3px rgba(24, 34, 48, 0.04);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--solo-text-secondary, #586474);
  font-size: 13px;
}

.margin-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  line-height: 1.3;
}

.margin-pill.pos {
  background: var(--solo-income-bg, #EAF7F1);
  color: var(--solo-income, #0f7354);
}

.margin-pill.neg {
  background: var(--solo-expense-bg, #FBEEEE);
  color: var(--solo-expense, #bd3d3d);
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
  color: var(--solo-text-muted, #636e7c);
}

.income-unit {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
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

.income { color: var(--solo-income, #0f7354); }
.expense { color: var(--solo-expense, #bd3d3d); }

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
  color: var(--solo-primary, #1747b0);
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

/* ── 会计视图 ── */
.rowno-col,
.rowno-cell {
  width: 48px;
  text-align: center;
  color: var(--solo-text-muted, #636e7c);
  font-size: 12px;
}

.pro-income-table {
  min-width: 720px;
}

.pro-compiler-note {
  margin-top: 12px;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.6;
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
