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

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const month = ref(getCurrentMonth())
const taxPeriodType = ref<'month' | 'quarter'>('month')
const loading = ref(false)
const report = ref<MonthlyReportResponse | null>(null)

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
const taxPeriod = ref(completedTaxPeriods('month')[0].value)
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
      itemStyle: { borderRadius: [0, 4, 4, 0], color: '#1890ff' },
    }],
  }
})

const reportCards = [
  { path: '/reports/health', label: '健康评分', desc: '企业经营健康度分析', icon: 'ri-heart-pulse-line', color: '#10b981' },
  { path: '/reports/vat', label: '增值税辅助', desc: '小规模纳税人增值税计算', icon: 'ri-receipt-line', color: '#3b82f6' },
  { path: '/reports/cit', label: '企业所得税', desc: '企业所得税预缴计算', icon: 'ri-government-line', color: '#8b5cf6' },
  { path: '/reports/archive', label: '归档清单', desc: '单据合规性检查', icon: 'ri-archive-line', color: '#f59e0b' },
  { path: '/reports/tax-summary', label: '税务汇总', desc: '税务申报汇总与提醒', icon: 'ri-calendar-todo-line', color: '#ef4444' },
  { path: '/reports/balance-sheet', label: '资产负债表', desc: '资产负债与权益状况', icon: 'ri-scales-3-line', color: '#0891b2' },
  { path: '/reports/income-statement', label: '利润表', desc: '总表/季报利润结构', icon: 'ri-funds-line', color: '#16a34a' },
  { path: '/reports/cash-flow', label: '现金流量表', desc: '经营/投资/筹资现金流', icon: 'ri-exchange-dollar-line', color: '#7c3aed' },
  { path: '/reports/general-ledger', label: '总分类账', desc: '按科目钻取，借贷余三栏', icon: 'ri-book-3-line', color: '#0ea5e9' },
  { path: '/reports/sub-ledger', label: '明细账', desc: '明细账含对方科目', icon: 'ri-book-read-line', color: '#14b8a6' },
  { path: '/reports/ar-aging', label: '应收账龄', desc: '应收账款账龄分析', icon: 'ri-line-chart-line', color: '#f43f5e' },
  { path: '/reports/ap-aging', label: '应付账龄', desc: '应付账款账龄分析', icon: 'ri-line-chart-down-line', color: '#8b5cf6' },
  { path: '/reports/comparison', label: '同比/环比', desc: '收入支出同比环比分析', icon: 'ri-bar-chart-grouped-line', color: '#059669' },
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
    <div class="page-header">
      <h2>报表中心</h2>
      <div class="header-actions">
        <NSelect
          v-model:value="taxPeriodType"
          :options="taxPeriodTypeOptions"
          size="small"
          style="width: 88px;"
          @update:value="handleTaxPeriodTypeChange"
        />
        <NSelect
          v-model:value="taxPeriod"
          :options="taxPeriodOptions"
          size="small"
          style="width: 150px;"
        />
        <NButton size="tiny" type="primary" secondary @click="handleExportTaxXls">导出税务 XLS</NButton>
        <NButton size="tiny" quaternary @click="handleExportPDF">导出 PDF</NButton>
        <NSelect
        v-model:value="month"
        :options="monthOptions"
        size="small"
        style="width: 140px;"
        @update:value="loadReport"
      /></div>
    </div>

    <!-- Monthly Summary -->
    <NSpin :show="loading">
      <NCard :bordered="false" id="report-summary-card" class="summary-card mb-4">
        <div class="summary-row">
          <div class="summary-item">
            <span class="sum-label">月收入</span>
            <span class="sum-value income">{{ report ? formatMoney(report.monthIncome) : '—' }}</span>
          </div>
          <div class="summary-item">
            <span class="sum-label">月支出</span>
            <span class="sum-value expense">{{ report ? formatMoney(report.monthExpense) : '—' }}</span>
          </div>
          <div class="summary-item">
            <span class="sum-label">净利润</span>
            <span class="sum-value" :class="report && report.netProfit >= 0 ? 'income' : 'expense'">
              {{ report ? formatMoney(report.netProfit) : '—' }}
            </span>
          </div>
        </div>

        <!-- Category breakdown chart -->
        <div v-if="categoryEntries.length > 0" class="category-section">
          <div class="section-label">分类支出占比</div>
          <VChart :option="chartOption" autoresize style="height: 260px" />
        </div>

        <div v-if="report?.summary" class="summary-text">
          {{ report.summary }}
        </div>
      </NCard>
    </NSpin>

    <!-- Report Cards -->
    <div class="report-grid">
      <div
        v-for="card in reportCards"
        :key="card.path"
        class="report-card"
        @click="router.push(card.path)"
      >
        <div class="card-icon" :style="{ background: card.color + '15', color: card.color }">
          <i :class="card.icon"></i>
        </div>
        <div class="card-body">
          <div class="card-title">{{ card.label }}</div>
          <div class="card-desc">{{ card.desc }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.report-page {
  max-width: 720px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mb-4 { margin-bottom: 16px; }
.summary-card {
  border-radius: var(--solo-radius-lg);
}
.summary-row {
  display: flex;
  gap: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--solo-border-light);
}
.summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sum-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.sum-value {
  font-size: 20px;
  font-weight: 700;
}
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }

/* Category bars */
.category-section {
  margin-top: 16px;
}
.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  margin-bottom: 4px;
}
.summary-text {
  margin-top: 12px;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
}

/* Report cards grid */
.report-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.report-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: var(--solo-shadow-sm);
  height: 100%;
}
.report-card:hover {
  border-color: var(--solo-primary-light);
  box-shadow: var(--solo-shadow-lg);
  transform: translateY(-2px);
  background: rgba(255, 255, 255, 0.95);
}
.card-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}
.report-card:hover .card-icon {
  transform: scale(1.08);
}
.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.card-desc {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 4px;
  line-height: 1.4;
}
</style>
