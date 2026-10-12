<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getMonthlyReport } from '@/api/report'
import { exportSmallBusinessMonthQuarterTaxReport } from '@/api/tax-report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { exportReportToPDF } from '@/utils/export'
import type { MonthlyReportResponse } from '@/api/types'

import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

// ponytail: reorganized 13 flat cards into 3 structured layers: Business Health, Tax Compliance, Legal Books
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(router.currentRoute?.value.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const taxPeriodType = ref<'month' | 'quarter'>('month')
const loading = ref(false)
const report = ref<MonthlyReportResponse | null>(null)
const showLegalBooks = ref(false)

function completedTaxPeriods(type: 'month' | 'quarter') {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  if (type === 'month') {
    for (let i = 1; i <= 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
      const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      options.push({ label: `${d.getFullYear()}年${d.getMonth() + 1}月`, value })
    }
    return options
  }

  const currentQuarterIndex = now.getFullYear() * 4 + Math.floor(now.getMonth() / 3)
  for (let i = 1; i <= 8; i++) {
    const absoluteQuarter = currentQuarterIndex - i
    const year = Math.floor(absoluteQuarter / 4)
    const quarter = ((absoluteQuarter % 4) + 4) % 4 + 1
    options.push({ label: `${year}年第${quarter}季度`, value: `${year}-Q${quarter}` })
  }
  return options
}

const taxPeriodOptions = computed(() => completedTaxPeriods(taxPeriodType.value))
const taxPeriod = ref(hasRequestedMonth ? requestedMonth : completedTaxPeriods('month')[0].value)
const taxPeriodTypeOptions = [
  { label: '月报', value: 'month' },
  { label: '季报', value: 'quarter' },
]

function handleTaxPeriodTypeChange() {
  taxPeriod.value = completedTaxPeriods(taxPeriodType.value)[0].value
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

const categoryEntries = computed(() => {
  if (!report.value?.categoryBreakdown) return []
  return Object.entries(report.value.categoryBreakdown)
    .sort(([, a], [, b]) => b - a)
    .map(([name, amount]) => ({ name, amount }))
})

const chartOption = computed(() => {
  const entries = categoryEntries.value
  return {
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      formatter: (params: { value: number; axisValueLabel: string }[]) => {
        const p = params[0]
        return `${p.axisValueLabel}: ${formatMoney(p.value)}`
      },
    },
    grid: { left: 80, right: 20, top: 10, bottom: 10, containLabel: false },
    xAxis: {
      type: 'value' as const,
      axisLabel: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'category' as const,
      data: entries.map(e => e.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { fontSize: 12, color: '#666' },
    },
    series: [{
      type: 'bar',
      data: entries.map(e => e.amount),
      barMaxWidth: 20,
      itemStyle: { borderRadius: [0, 4, 4, 0], color: '#165dff' },
    }],
  }
})

// ── 模块归类：经营体检、财税预警、法定底账 ──────────────────────
const businessHealthReports = [
  { path: '/reports/health', label: '健康评分', desc: '企业经营健康度评测，监控现金与风险', icon: 'ri-heart-pulse-line', color: '#10b981' },
  { path: '/reports/income-statement', label: '利润表', desc: '收入、成本与利润结构拆解', icon: 'ri-funds-line', color: '#16a34a' },
  { path: '/reports/comparison', label: '同比/环比', desc: '对比历史同期，追踪经营效益趋势', icon: 'ri-bar-chart-grouped-line', color: '#059669' },
  { path: '/reports/ar-aging', label: '往来账龄', desc: '应收应付账龄分析，防范逾期欠款', icon: 'ri-line-chart-line', color: '#f43f5e' },
]

const taxComplianceReports = [
  { path: '/reports/vat', label: '增值税辅助', desc: '小规模纳税人免税额度监控与税额测算', icon: 'ri-receipt-line', color: '#3b82f6' },
  { path: '/reports/cit', label: '企业所得税', desc: '小型微利企业减免优惠与预缴测算', icon: 'ri-government-line', color: '#8b5cf6' },
  { path: '/reports/tax-summary', label: '税务汇总', desc: '全年税费缴纳汇总台账与申报日历', icon: 'ri-calendar-todo-line', color: '#ef4444' },
  { path: '/reports/archive', label: '归档清单', desc: '单据合规性检查与未归档凭证追踪', icon: 'ri-archive-line', color: '#f59e0b' },
]

const legalBookReports = [
  { path: '/reports/balance-sheet', label: '资产负债表', desc: '期末资产、负债与所有者权益状况', icon: 'ri-scales-3-line', color: '#0891b2' },
  { path: '/reports/cash-flow', label: '现金流量表', desc: '经营、投资及筹资现金变动分析', icon: 'ri-exchange-dollar-line', color: '#7c3aed' },
  { path: '/reports/general-ledger', label: '总分类账', desc: '按会计科目钻取借贷余三栏底账', icon: 'ri-book-3-line', color: '#0ea5e9' },
  { path: '/reports/sub-ledger', label: '明细账', desc: '明细分录与对应科目核对', icon: 'ri-book-read-line', color: '#14b8a6' },
]

async function handleExportTaxXls() {
  try {
    const blob = await exportSmallBusinessMonthQuarterTaxReport(taxPeriod.value, taxPeriodType.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'tax-report-xiaoqiye-month-quarter-' + taxPeriod.value + '.xls'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    message.success('税务标准报表已导出')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '导出税务标准报表失败')
  }
}

async function handleExportPDF() {
  try {
    await exportReportToPDF('report-summary-card')
    message.success('导出成功')
  } catch {
    message.error('导出 PDF 失败')
  }
}

async function loadReport() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    report.value = await getMonthlyReport(month.value)
  } catch {
    message.error('加载报表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadReport()
})
</script>

<template>
  <div class="report-page">
    <!-- 顶栏标题与紧凑操作区 -->
    <div class="page-header">
      <div class="header-titles">
        <h2>报表中心</h2>
        <p class="subtitle">经营体检与合规报表，随时掌握企业真实收支与税费状况</p>
      </div>

      <div class="header-actions">
        <div class="select-group" title="切换月报统计月份">
          <span class="select-label">所属月份</span>
          <NSelect
            v-model:value="month"
            :options="monthOptions"
            size="small"
            style="width: 130px;"
            @update:value="loadReport"
          />
        </div>

        <div class="export-box">
          <span class="select-label">税务导出</span>
          <NSelect
            v-model:value="taxPeriodType"
            :options="taxPeriodTypeOptions"
            size="small"
            style="width: 76px;"
            @update:value="handleTaxPeriodTypeChange"
          />
          <NSelect
            v-model:value="taxPeriod"
            :options="taxPeriodOptions"
            size="small"
            style="width: 140px;"
          />
          <NButton size="small" type="primary" secondary @click="handleExportTaxXls">
            <template #icon><i class="ri-file-excel-line" /></template>
            导出 XLS
          </NButton>
          <NButton size="small" quaternary @click="handleExportPDF">
            <template #icon><i class="ri-file-pdf-line" /></template>
            PDF
          </NButton>
        </div>
      </div>
    </div>

    <!-- 顶部月度简报与 AI 诊断 -->
    <NSpin :show="loading">
      <NCard :bordered="false" id="report-summary-card" class="summary-card mb-4">
        <div class="summary-row">
          <div class="summary-item">
            <span class="sum-label">{{ month }} 收入</span>
            <span class="sum-value income">{{ report ? formatMoney(report.monthIncome) : '—' }}</span>
          </div>
          <div class="summary-item">
            <span class="sum-label">{{ month }} 支出</span>
            <span class="sum-value expense">{{ report ? formatMoney(report.monthExpense) : '—' }}</span>
          </div>
          <div class="summary-item main-profit">
            <span class="sum-label">{{ month }} 净利润</span>
            <span class="sum-value" :class="report && report.netProfit >= 0 ? 'income' : 'expense'">
              {{ report ? (report.netProfit >= 0 ? '+' : '') + formatMoney(report.netProfit) : '—' }}
            </span>
          </div>
        </div>

        <!-- AI 会计经营简评 -->
        <div v-if="report?.summary" class="ai-summary-box">
          <div class="summary-badge">
            <i class="ri-sparkling-fill" />
            <span>AI 会计经营分析</span>
          </div>
          <p class="summary-text">{{ report.summary }}</p>
        </div>

        <!-- 分类支出占比 -->
        <div v-if="categoryEntries.length > 0" class="category-section">
          <div class="section-label">支出分类占比</div>
          <VChart :option="chartOption" autoresize style="height: 220px" />
        </div>
      </NCard>
    </NSpin>

    <!-- 报表三大板块分层 -->
    <div class="report-sections">
      <!-- 板块 1：经营体检与管理分析（老板随时关注） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-line-chart-line section-icon health" />
            <h3>经营体检与盈利分析</h3>
          </div>
          <span class="section-sub">实时监控现金流健康度、利润走势与客户欠款</span>
        </div>
        <div class="report-grid">
          <div
            v-for="card in businessHealthReports"
            :key="card.path"
            class="report-card"
            @click="router.push(card.path)"
          >
            <div class="card-icon" :style="{ background: card.color + '15', color: card.color }">
              <i :class="card.icon" />
            </div>
            <div class="card-body">
              <div class="card-title">{{ card.label }}</div>
              <div class="card-desc">{{ card.desc }}</div>
            </div>
            <i class="ri-arrow-right-s-line card-arrow" />
          </div>
        </div>
      </section>

      <!-- 板块 2：财税核算与预警（季末/月末申报） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-file-shield-2-line section-icon tax" />
            <h3>税务预警与申报合规</h3>
          </div>
          <span class="section-sub">小规模增值税免税监控、企业所得税优惠测算与发票归档</span>
        </div>
        <div class="report-grid">
          <div
            v-for="card in taxComplianceReports"
            :key="card.path"
            class="report-card"
            @click="router.push(card.path)"
          >
            <div class="card-icon" :style="{ background: card.color + '15', color: card.color }">
              <i :class="card.icon" />
            </div>
            <div class="card-body">
              <div class="card-title">{{ card.label }}</div>
              <div class="card-desc">{{ card.desc }}</div>
            </div>
            <i class="ri-arrow-right-s-line card-arrow" />
          </div>
        </div>
      </section>

      <!-- 板块 3：法定财务底账（外部做账/审计底册，支持折叠收纳） -->
      <section class="report-section legal-books">
        <div class="section-header toggle-header" @click="showLegalBooks = !showLegalBooks">
          <div class="section-title-wrap">
            <i class="ri-book-3-line section-icon legal" />
            <h3>法定报表与账簿底册</h3>
            <span class="legal-badge">做账 / 审计 / 报税备查</span>
          </div>
          <button type="button" class="toggle-btn">
            <span>{{ showLegalBooks ? '收起底册' : '展开 4 项法定报表' }}</span>
            <i :class="showLegalBooks ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          </button>
        </div>

        <div v-show="showLegalBooks" class="report-grid mt-3">
          <div
            v-for="card in legalBookReports"
            :key="card.path"
            class="report-card legal-card"
            @click="router.push(card.path)"
          >
            <div class="card-icon" :style="{ background: card.color + '15', color: card.color }">
              <i :class="card.icon" />
            </div>
            <div class="card-body">
              <div class="card-title">{{ card.label }}</div>
              <div class="card-desc">{{ card.desc }}</div>
            </div>
            <i class="ri-arrow-right-s-line card-arrow" />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.report-page {
  max-width: 1240px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 100%;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 4px;
}

.header-titles h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.4px;
}

.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.select-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.select-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  white-space: nowrap;
}

.export-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
}

.summary-card {
  border-radius: var(--solo-radius-lg);
  border: 1px solid var(--solo-border-glass);
}

.summary-row {
  display: flex;
  align-items: center;
  gap: 36px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--solo-border-light);
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-item.main-profit {
  padding-left: 20px;
  border-left: 1px solid var(--solo-border-light);
}

.sum-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.sum-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }

/* AI 简评 */
.ai-summary-box {
  margin-top: 14px;
  padding: 10px 14px;
  background: var(--solo-bg-blue-soft);
  border-radius: var(--solo-radius);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-primary);
}

.summary-text {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.category-section {
  margin-top: 16px;
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  margin-bottom: 4px;
}

/* 报表三大板块 */
.report-sections {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.report-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.section-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-icon {
  font-size: 18px;
}

.section-icon.health { color: #10b981; }
.section-icon.tax { color: #3b82f6; }
.section-icon.legal { color: #8b5cf6; }

.section-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.section-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.toggle-header {
  cursor: pointer;
  padding: 8px 12px;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius);
  transition: all 0.15s ease;
}

.toggle-header:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.legal-badge {
  font-size: 11px;
  padding: 1px 6px;
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
  border-radius: 4px;
}

.toggle-btn {
  background: none;
  border: none;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-primary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

/* 网格排布（每组 4 项，双列对称） */
.report-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}

.report-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: var(--solo-shadow-sm);
}

.report-card:hover {
  border-color: var(--solo-primary);
  box-shadow: var(--solo-shadow);
  transform: translateY(-1px);
}

.card-icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}

.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.card-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.card-desc {
  font-size: 12px;
  color: var(--solo-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-arrow {
  color: var(--solo-text-muted);
  font-size: 18px;
  transition: transform 0.15s;
}

.report-card:hover .card-arrow {
  color: var(--solo-primary);
  transform: translateX(2px);
}

.legal-card {
  background: #fafbfc;
}

@media (max-width: 900px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
  }
  .header-actions {
    flex-wrap: wrap;
  }
  .report-grid {
    grid-template-columns: 1fr;
  }
}
</style>
