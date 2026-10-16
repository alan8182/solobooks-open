<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getMonthlyReport, getReportDiagnosis, getBalanceSheet } from '@/api/report'
import { exportSmallBusinessMonthQuarterTaxReport } from '@/api/tax-report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { exportReportToPDF } from '@/utils/export'
import type { MonthlyReportResponse, BalanceSheetResponse } from '@/api/types'

import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

// ponytail: Boss-centric report hub: direct financial conclusions, cash/receivables visibility, restrained blues
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(router.currentRoute?.value.query.month || '')
const requestedPeriod = String(router.currentRoute?.value.query.period || '')
const requestedPeriodType = String(router.currentRoute?.value.query.periodType || '') as 'month' | 'quarter' | 'year'

const initialPeriodType: 'month' | 'quarter' | 'year' = ['month', 'quarter', 'year'].includes(requestedPeriodType)
  ? requestedPeriodType
  : /^\d{4}-Q[1-4]$/.test(requestedPeriod || requestedMonth)
    ? 'quarter'
    : /^\d{4}$/.test(requestedPeriod || requestedMonth)
      ? 'year'
      : 'month'

const periodType = ref<'month' | 'quarter' | 'year'>(initialPeriodType)

function generateMonthOptions() {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    const label = `${d.getFullYear()}年${d.getMonth() + 1}月`
    options.push({ label, value: val })
  }
  return options
}

function generateQuarterOptions() {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  const currentQuarterIndex = now.getFullYear() * 4 + Math.floor(now.getMonth() / 3)
  for (let i = 0; i < 8; i++) {
    const absoluteQuarter = currentQuarterIndex - i
    const year = Math.floor(absoluteQuarter / 4)
    const quarter = ((absoluteQuarter % 4) + 4) % 4 + 1
    options.push({ label: `${year}年第${quarter}季度`, value: `${year}-Q${quarter}` })
  }
  return options
}

function generateYearOptions() {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  const currentYear = now.getFullYear()
  for (let i = 0; i < 5; i++) {
    const year = currentYear - i
    options.push({ label: `${year}年度`, value: `${year}` })
  }
  return options
}

const periodOptions = computed(() => {
  if (periodType.value === 'quarter') return generateQuarterOptions()
  if (periodType.value === 'year') return generateYearOptions()
  return generateMonthOptions()
})

const defaultMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedPeriod || requestedMonth)
  ? (requestedPeriod || requestedMonth)
  : getCurrentMonth()

const defaultQuarter = /^\d{4}-Q[1-4]$/.test(requestedPeriod || requestedMonth)
  ? (requestedPeriod || requestedMonth)
  : generateQuarterOptions()[0].value

const defaultYear = /^\d{4}$/.test(requestedPeriod || requestedMonth)
  ? (requestedPeriod || requestedMonth)
  : generateYearOptions()[0].value

const selectedPeriod = ref<string>(
  initialPeriodType === 'quarter' ? defaultQuarter :
  initialPeriodType === 'year' ? defaultYear :
  defaultMonth
)

function getRepresentativeMonth(p: string, pType: 'month' | 'quarter' | 'year'): string {
  if (pType === 'quarter') {
    const [y, qStr] = p.split('-Q')
    const q = parseInt(qStr || '1', 10)
    return `${y}-${String(q * 3).padStart(2, '0')}`
  }
  if (pType === 'year') {
    return `${p}-12`
  }
  return p
}

const representativeMonth = computed(() => getRepresentativeMonth(selectedPeriod.value, periodType.value))
const taxPeriodType = ref<'month' | 'quarter'>('month')
const loading = ref(false)
const aiLoading = ref(false)
const aiSummary = ref<string>('')
const report = ref<MonthlyReportResponse | null>(null)
const balanceSheetData = ref<BalanceSheetResponse | null>(null)
const showLegalBooks = ref(false)

async function fetchDiagnosis() {
  aiLoading.value = true
  try {
    const res = await getReportDiagnosis(selectedPeriod.value, periodType.value)
    aiSummary.value = res.summary
    message.success('AI 经营诊断生成完成')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '生成诊断失败，请重试')
  } finally {
    aiLoading.value = false
  }
}

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
const taxPeriod = ref(defaultMonth)
const taxPeriodTypeOptions = [
  { label: '月报', value: 'month' },
  { label: '季报', value: 'quarter' },
]

function handleTaxPeriodTypeChange() {
  taxPeriod.value = completedTaxPeriods(taxPeriodType.value)[0].value
}

function handlePeriodTypeChange() {
  aiSummary.value = ''
  if (periodType.value === 'quarter') {
    selectedPeriod.value = generateQuarterOptions()[0].value
  } else if (periodType.value === 'year') {
    selectedPeriod.value = generateYearOptions()[0].value
  } else {
    selectedPeriod.value = generateMonthOptions()[0].value
  }
  router.replace({
    query: {
      period: selectedPeriod.value,
      periodType: periodType.value,
      month: representativeMonth.value,
    },
  })
  loadReport()
}

function handlePeriodChange() {
  aiSummary.value = ''
  router.replace({
    query: {
      period: selectedPeriod.value,
      periodType: periodType.value,
      month: representativeMonth.value,
    },
  })
  loadReport()
}

function navigateToReport(path: string) {
  router.push({
    path,
    query: {
      month: representativeMonth.value,
      period: selectedPeriod.value,
      periodType: periodType.value,
    },
  })
}

const periodScopeName = computed(() => {
  if (periodType.value === 'quarter') return '本季'
  if (periodType.value === 'year') return '全年'
  return '本月'
})

const periodTypeName = computed(() => {
  if (periodType.value === 'quarter') return '季度'
  if (periodType.value === 'year') return '年度'
  return '月份'
})

// ── 真金白银与经营关键指标 ──────────────────────────────────────────
const cashAmount = computed(() => {
  if (report.value?.availableCash !== undefined && report.value.availableCash !== null) {
    return report.value.availableCash
  }
  return balanceSheetData.value?.assets.find(a => a.accountCode === '1002')?.currentAmount ?? 0
})

const arAmount = computed(() => {
  if (report.value?.pendingReceivables !== undefined && report.value.pendingReceivables !== null) {
    return report.value.pendingReceivables
  }
  const ar = balanceSheetData.value?.assets.find(a => a.accountCode === '1122')?.currentAmount ?? 0
  const otherAr = balanceSheetData.value?.assets.find(a => a.accountCode === '1221')?.currentAmount ?? 0
  return ar + otherAr
})

const apAmount = computed(() => {
  const ap = balanceSheetData.value?.liabilities.find(l => l.accountCode === '2202')?.currentAmount ?? 0
  const otherAp = balanceSheetData.value?.liabilities.find(l => l.accountCode === '2241')?.currentAmount ?? 0
  return ap + otherAp
})

const estimatedVat = computed(() => Number(report.value?.taxSummary?.estimatedVat || 0))
const estimatedCit = computed(() => Number(report.value?.taxSummary?.estimatedCit || 0))
const totalEstimatedTax = computed(() => estimatedVat.value + estimatedCit.value)

const profitMargin = computed(() => {
  if (!report.value || !report.value.monthIncome || report.value.monthIncome <= 0) return null
  const margin = (report.value.netProfit / report.value.monthIncome) * 100
  return margin.toFixed(1)
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
      axisLabel: { fontSize: 12, color: '#586474' },
    },
    series: [{
      type: 'bar',
      data: entries.map(e => e.amount),
      barMaxWidth: 18,
      itemStyle: { borderRadius: [0, 4, 4, 0], color: '#165DFF' },
    }],
  }
})

// ── 模块归类与实时结论透出（沉静司理蓝收敛，保留专业核算能力） ─────────────────
const businessHealthReports = computed(() => [
  {
    path: '/reports/income-statement',
    label: '利润表',
    desc: report.value
      ? `${periodScopeName.value}净赚 ${formatMoney(report.value.netProfit)} · 净利率 ${profitMargin.value !== null ? profitMargin.value + '%' : '0.0%'}`
      : '收入、成本费用与经营净利结构拆解',
    icon: 'ri-funds-line',
  },
  {
    path: '/reports/ar-aging',
    label: '往来账龄',
    desc: `客户待收 ${formatMoney(arAmount.value)} · 供应商待付 ${formatMoney(apAmount.value)}`,
    icon: 'ri-line-chart-line',
    isWarning: arAmount.value > 0,
  },
  {
    path: '/reports/health',
    label: '健康评分',
    desc: '企业综合健康度评测，监控现金与发票风险',
    icon: 'ri-heart-pulse-line',
  },
  {
    path: '/reports/comparison',
    label: '同比/环比',
    desc: '对比历史同期，追踪营收与支出趋势',
    icon: 'ri-bar-chart-grouped-line',
  },
])

const taxComplianceReports = computed(() => {
  const vatDesc = periodType.value === 'quarter'
    ? `小规模季免税 30 万额度 · 预计本季税额 ${formatMoney(estimatedVat.value)}`
    : periodType.value === 'year'
      ? `全年增值税预估 · 预计年税额 ${formatMoney(estimatedVat.value)}`
      : `小规模季免税折算 · 预计本月税额 ${formatMoney(estimatedVat.value)}`

  return [
    {
      path: '/reports/vat',
      label: '增值税辅助',
      desc: vatDesc,
      icon: 'ri-receipt-line',
    },
    {
      path: '/reports/cit',
      label: '企业所得税',
      desc: `小微企业 5% 优惠税率 · 预计税额 ${formatMoney(estimatedCit.value)}`,
      icon: 'ri-government-line',
    },
    {
      path: '/reports/tax-summary',
      label: '税务汇总',
      desc: '全年税费缴纳台账与申报纳税日历',
      icon: 'ri-calendar-todo-line',
    },
    {
      path: '/reports/archive',
      label: '归档清单',
      desc: '单据合规性检查与未归档凭证追踪',
      icon: 'ri-archive-line',
    },
  ]
})

const legalBookReports = computed(() => [
  {
    path: '/reports/balance-sheet',
    label: '资产负债表',
    desc: balanceSheetData.value
      ? `总家底 ${formatMoney(balanceSheetData.value.totalAssets)} · 老板净资产 ${formatMoney(balanceSheetData.value.totalEquity)}`
      : '期末资产、对外负债与所有者权益状况',
    icon: 'ri-scales-3-line',
  },
  {
    path: '/reports/cash-flow',
    label: '现金流量表',
    desc: '经营、投资及筹资现金变动分析',
    icon: 'ri-exchange-dollar-line',
  },
  {
    path: '/reports/general-ledger',
    label: '总分类账',
    desc: '按会计科目钻取借贷余三栏底账',
    icon: 'ri-book-3-line',
  },
  {
    path: '/reports/sub-ledger',
    label: '明细账',
    desc: '明细分录与对应科目核对',
    icon: 'ri-book-read-line',
  },
])

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
    message.success('经营看板 PDF 导出成功')
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
    const [reportRes, balanceRes] = await Promise.allSettled([
      getMonthlyReport(selectedPeriod.value, periodType.value),
      getBalanceSheet(representativeMonth.value),
    ])
    if (reportRes.status === 'fulfilled') {
      report.value = reportRes.value
      if (reportRes.value.summary) {
        aiSummary.value = reportRes.value.summary
      }
    } else {
      const errDetail = (reportRes.reason as any)?.response?.data?.detail
      message.error(typeof errDetail === 'string' ? `加载经营报表失败: ${errDetail}` : '加载经营报表失败')
      console.error('加载经营报表失败:', reportRes.reason)
    }
    if (balanceRes.status === 'fulfilled') {
      balanceSheetData.value = balanceRes.value
    }
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
    <!-- 顶栏标题与月/季/年统计区间切换 -->
    <div class="page-header">
      <div class="header-titles">
        <h2>报表中心</h2>
        <p class="subtitle">随时掌握企业真实收支、账上资金与税费状况</p>
      </div>

      <div class="header-actions">
        <NRadioGroup
          v-model:value="periodType"
          size="small"
          @update:value="handlePeriodTypeChange"
        >
          <NRadioButton value="month">月度</NRadioButton>
          <NRadioButton value="quarter">季度</NRadioButton>
          <NRadioButton value="year">年度</NRadioButton>
        </NRadioGroup>

        <div class="select-group" :title="`切换${periodTypeName}统计区间`">
          <span class="select-label">统计区间</span>
          <NSelect
            v-model:value="selectedPeriod"
            :options="periodOptions"
            size="small"
            style="width: 150px;"
            @update:value="handlePeriodChange"
          />
        </div>
      </div>
    </div>

    <!-- 顶部老板经营驾驶舱 -->
    <NSpin :show="loading">
      <NCard :bordered="false" id="report-summary-card" class="summary-card mb-4">
        <!-- 核心经营数据 4 宫格 -->
        <div class="cockpit-grid">
          <div class="cockpit-item">
            <span class="cockpit-label">{{ periodScopeName }}营业收入</span>
            <div class="cockpit-val income">{{ report ? formatMoney(report.monthIncome) : '—' }}</div>
            <span class="cockpit-sub">含已开票与经营收款</span>
          </div>

          <div class="cockpit-item">
            <span class="cockpit-label">{{ periodScopeName }}总支出</span>
            <div class="cockpit-val expense">{{ report ? formatMoney(report.monthExpense) : '—' }}</div>
            <span class="cockpit-sub">采购、房租、人工与费用</span>
          </div>

          <div class="cockpit-item main-profit-item">
            <div class="label-with-tag">
              <span class="cockpit-label">{{ periodScopeName }}净赚 (利润)</span>
              <span v-if="profitMargin !== null" class="margin-tag" :class="Number(profitMargin) >= 0 ? 'pos' : 'neg'">
                净利率 {{ profitMargin }}%
              </span>
            </div>
            <div
              class="cockpit-val"
              :class="report && report.netProfit >= 0 ? 'income' : 'expense'"
            >
              {{ report ? (report.netProfit >= 0 ? '+' : '') + formatMoney(report.netProfit) : '—' }}
            </div>
            <span class="cockpit-sub">扣除全部成本与税费后净收益</span>
          </div>

          <div class="cockpit-item">
            <span class="cockpit-label">预计{{ periodScopeName }}税负</span>
            <div class="cockpit-val text-primary">{{ formatMoney(totalEstimatedTax) }}</div>
            <span class="cockpit-sub">
              增值税 {{ formatMoney(estimatedVat) }} · 所得税 {{ formatMoney(estimatedCit) }}
            </span>
          </div>
        </div>

        <!-- 真金白银头寸速览条（破解“账面有利润却没现钱”痛点） -->
        <div class="cash-position-strip">
          <div class="position-item cash-item">
            <div class="position-icon-wrap">
              <i class="ri-wallet-3-line position-icon" />
            </div>
            <div class="position-info">
              <div class="position-label">账上可用资金（货币资金）</div>
              <div class="position-num text-primary">{{ formatMoney(cashAmount) }}</div>
              <div class="position-desc">公司银行账户与手头随时可支配现金</div>
            </div>
          </div>

          <div class="position-divider" />

          <div class="position-item ar-item">
            <div class="position-icon-wrap amber">
              <i class="ri-hand-coin-line position-icon" />
            </div>
            <div class="position-info">
              <div class="position-label">待收回客户欠款（应收账款）</div>
              <div class="position-num" :class="arAmount > 0 ? 'text-warning' : 'text-primary'">
                {{ formatMoney(arAmount) }}
              </div>
              <div class="position-desc">已记收入但钱还未到账，需关注催收回款</div>
            </div>
          </div>

          <div class="position-divider" />

          <div class="position-item ap-item">
            <div class="position-icon-wrap">
              <i class="ri-bill-line position-icon" />
            </div>
            <div class="position-info">
              <div class="position-label">对外待付款项（应付账款）</div>
              <div class="position-num text-secondary">{{ formatMoney(apAmount) }}</div>
              <div class="position-desc">已收到货物或发票但尚未付给供应商的款项</div>
            </div>
          </div>
        </div>

        <!-- AI 会计经营简评（手动触发） -->
        <div class="ai-summary-box">
          <div class="ai-summary-header">
            <div class="summary-badge">
              <i class="ri-sparkling-fill" />
              <span>AI 经营诊断与建议</span>
            </div>
            <NButton
              size="tiny"
              secondary
              type="primary"
              :loading="aiLoading"
              @click="fetchDiagnosis"
            >
              <template #icon>
                <i :class="aiSummary ? 'ri-refresh-line' : 'ri-magic-line'" />
              </template>
              {{ aiSummary ? '重新诊断' : '生成诊断建议' }}
            </NButton>
          </div>
          <p v-if="aiSummary" class="summary-text">{{ aiSummary }}</p>
          <p v-else class="summary-hint">
            基于本期收支、净利润与支出结构，由 AI 会计分析经营健康度并给出改善建议。
          </p>
        </div>

        <!-- 分类支出占比 -->
        <div v-if="categoryEntries.length > 0" class="category-section">
          <div class="section-label">支出结构拆解</div>
          <VChart :option="chartOption" autoresize style="height: 200px" />
        </div>
      </NCard>
    </NSpin>

    <!-- 报表三大板块分层 -->
    <div class="report-sections">
      <!-- 板块 1：经营体检与管理分析（日常看赚亏与欠款） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-line-chart-line section-icon" />
            <h3>经营体检与盈利分析</h3>
          </div>
          <span class="section-sub">日常经营关切：利润走势、客户欠款与健康度</span>
        </div>
        <div class="report-grid">
          <div
            v-for="card in businessHealthReports"
            :key="card.path"
            class="report-card"
            @click="navigateToReport(card.path)"
          >
            <div class="card-icon" :class="{ 'warning-icon': card.isWarning }">
              <i :class="card.icon" />
            </div>
            <div class="card-body">
              <div class="card-title">{{ card.label }}</div>
              <div class="card-desc" :title="card.desc">{{ card.desc }}</div>
            </div>
            <i class="ri-arrow-right-s-line card-arrow" />
          </div>
        </div>
      </section>

      <!-- 板块 2：财税核算与预警（季末/月末申报） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-file-shield-2-line section-icon" />
            <h3>税务预警与申报合规</h3>
          </div>
          <span class="section-sub">季末与月末关切：增值税免税额度、所得税优惠与发票凭证</span>
        </div>
        <div class="report-grid">
          <div
            v-for="card in taxComplianceReports"
            :key="card.path"
            class="report-card"
            @click="navigateToReport(card.path)"
          >
            <div class="card-icon">
              <i :class="card.icon" />
            </div>
            <div class="card-body">
              <div class="card-title">{{ card.label }}</div>
              <div class="card-desc" :title="card.desc">{{ card.desc }}</div>
            </div>
            <i class="ri-arrow-right-s-line card-arrow" />
          </div>
        </div>
      </section>

      <!-- 板块 3：法定财务底账（外部做账/审计底册，支持折叠收纳） -->
      <section class="report-section legal-books">
        <div class="section-header toggle-header" @click="showLegalBooks = !showLegalBooks">
          <div class="section-title-wrap">
            <i class="ri-book-3-line section-icon" />
            <h3>法定报表与账簿底册</h3>
            <span class="legal-badge">做账 / 审计 / 报税备查</span>
          </div>
          <button type="button" class="toggle-btn">
            <span>{{ showLegalBooks ? '收起底册' : '展开 4 项法定报表与税局导出' }}</span>
            <i :class="showLegalBooks ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          </button>
        </div>

        <div v-show="showLegalBooks" class="legal-content mt-3">
          <!-- 税务导出工具箱（从顶部下沉至此） -->
          <div class="tax-export-panel">
            <div class="export-panel-left">
              <div class="export-panel-title">
                <i class="ri-download-cloud-line" />
                <span>税局标准月季报与底册导出</span>
              </div>
              <p class="export-panel-desc">按《小企业会计准则》导出标准报税 XLS 表格或报表简报 PDF，用于税务申报或交由会计师审计核查。</p>
            </div>
            <div class="export-panel-actions">
              <NSelect
                v-model:value="taxPeriodType"
                :options="taxPeriodTypeOptions"
                size="small"
                style="width: 80px;"
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

          <!-- 4 项法定报表卡片 -->
          <div class="report-grid mt-3">
            <div
              v-for="card in legalBookReports"
              :key="card.path"
              class="report-card legal-card"
              @click="navigateToReport(card.path)"
            >
              <div class="card-icon">
                <i :class="card.icon" />
              </div>
              <div class="card-body">
                <div class="card-title">{{ card.label }}</div>
                <div class="card-desc" :title="card.desc">{{ card.desc }}</div>
              </div>
              <i class="ri-arrow-right-s-line card-arrow" />
            </div>
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
  color: var(--solo-text-primary, #182230);
  letter-spacing: -0.4px;
}

.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
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
  gap: 8px;
}

.select-label {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
  white-space: nowrap;
}

/* 驾驶舱看板卡片 */
.summary-card {
  border-radius: var(--solo-radius-lg, 14px);
  border: 1px solid var(--solo-border, #DDE3EA);
  background: var(--solo-surface, #FFFFFF);
}

/* 核心经营 4 宫格 */
.cockpit-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--solo-border-light, #EDF0F4);
}

.cockpit-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cockpit-item.main-profit-item {
  position: relative;
}

.label-with-tag {
  display: flex;
  align-items: center;
  gap: 8px;
}

.margin-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: var(--solo-radius-pill, 999px);
  line-height: 1.4;
}

.margin-tag.pos {
  background: var(--solo-income-soft, #EAF7F1);
  color: var(--solo-income, #12805C);
}

.margin-tag.neg {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #C74444);
}

.cockpit-label {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
  font-weight: 500;
}

.cockpit-val {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.cockpit-sub {
  font-size: 11px;
  color: var(--solo-text-muted, #8A95A3);
}

.income { color: var(--solo-income, #12805C); }
.expense { color: var(--solo-expense, #C74444); }
.text-primary { color: var(--solo-text-primary, #182230); }
.text-secondary { color: var(--solo-text-secondary, #586474); }
.text-warning { color: var(--solo-warning, #B46B17); }

/* 真金白银头寸速览条 */
.cash-position-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding: 12px 18px;
  background: var(--solo-surface-soft, #F1F4F8);
  border-radius: var(--solo-radius-md, 10px);
  gap: 16px;
}

.position-item {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.position-icon-wrap {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--solo-primary-soft, #EDF3FF);
  color: var(--solo-primary, #165DFF);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.position-icon-wrap.amber {
  background: var(--solo-warning-soft, #FFF5E8);
  color: var(--solo-warning, #B46B17);
}

.position-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.position-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
}

.position-num {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.position-desc {
  font-size: 11px;
  color: var(--solo-text-muted, #8A95A3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.position-divider {
  width: 1px;
  height: 36px;
  background: var(--solo-border, #DDE3EA);
  flex-shrink: 0;
}

/* AI 简评 */
.ai-summary-box {
  margin-top: 14px;
  padding: 12px 16px;
  background: var(--solo-primary-soft, #EDF3FF);
  border-radius: var(--solo-radius-md, 10px);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ai-summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.summary-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 650;
  color: var(--solo-primary, #165DFF);
}

.summary-text {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-primary, #182230);
  line-height: 1.55;
}

.summary-hint {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.5;
}

.category-section {
  margin-top: 16px;
}

.section-label {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-secondary, #586474);
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
  color: var(--solo-primary, #165DFF);
}

.section-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}

.section-sub {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
}

.toggle-header {
  cursor: pointer;
  padding: 10px 14px;
  background: var(--solo-surface, #FFFFFF);
  border: 1px solid var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius-md, 10px);
  transition: all 0.15s ease;
}

.toggle-header:hover {
  border-color: var(--solo-primary, #165DFF);
  background: var(--solo-primary-soft, #EDF3FF);
}

.legal-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--solo-surface-soft, #F1F4F8);
  color: var(--solo-text-secondary, #586474);
  border-radius: var(--solo-radius-pill, 999px);
  font-weight: 500;
}

.toggle-btn {
  background: none;
  border: none;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-primary, #165DFF);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

/* 税务导出工作台 */
.tax-export-panel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 18px;
  background: var(--solo-surface, #FFFFFF);
  border: 1px dashed var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius-md, 10px);
  flex-wrap: wrap;
}

.export-panel-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.export-panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}

.export-panel-desc {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
}

.export-panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 网格排布（每组 4 项，双列对称） */
.report-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}

.report-card {
  background: var(--solo-surface, #FFFFFF);
  border: 1px solid var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius-lg, 14px);
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: 0 1px 2px rgba(24, 34, 48, 0.04);
}

.report-card:hover {
  border-color: var(--solo-primary, #165DFF);
  box-shadow: 0 8px 24px rgba(24, 34, 48, 0.06);
  transform: translateY(-1px);
}

.card-icon {
  width: 42px;
  height: 42px;
  border-radius: var(--solo-radius-md, 10px);
  background: var(--solo-primary-soft, #EDF3FF);
  color: var(--solo-primary, #165DFF);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
  transition: all 0.2s ease;
}

.card-icon.warning-icon {
  background: var(--solo-warning-soft, #FFF5E8);
  color: var(--solo-warning, #B46B17);
}

.card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.card-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}

.card-desc {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-arrow {
  color: var(--solo-text-muted, #8A95A3);
  font-size: 18px;
  transition: transform 0.15s;
}

.report-card:hover .card-arrow {
  color: var(--solo-primary, #165DFF);
  transform: translateX(2px);
}

.legal-card {
  background: var(--solo-surface-soft, #F1F4F8);
}

.mt-3 {
  margin-top: 12px;
}

@media (max-width: 960px) {
  .cockpit-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .cash-position-strip {
    flex-direction: column;
    align-items: stretch;
  }
  .position-divider {
    width: 100%;
    height: 1px;
  }
}

@media (max-width: 720px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
  }
  .cockpit-grid {
    grid-template-columns: 1fr;
  }
  .report-grid {
    grid-template-columns: 1fr;
  }
  .tax-export-panel {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
