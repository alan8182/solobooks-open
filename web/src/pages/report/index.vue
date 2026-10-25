<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { SoloEmpty } from '@/components/ui'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { getMonthlyReport, getReportDiagnosis, getBalanceSheet } from '@/api/report'
import { exportSmallBusinessMonthQuarterTaxReport } from '@/api/tax-report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { usePageRefresh } from '@/composables/usePageRefresh'
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
const viewStore = useWorkspaceViewStore()

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

// 新账套最常见的困惑：进来看到一张全 0 的报表，以为系统坏了。
// 空报表直接给「先记第一笔账」，比让用户盯着 0.00 强。
const isReportEmpty = computed(
  () =>
    !loading.value &&
    Boolean(report.value) &&
    !report.value?.monthIncome &&
    !report.value?.monthExpense &&
    Object.keys(report.value?.categoryBreakdown ?? {}).length === 0,
)
const balanceSheetData = ref<BalanceSheetResponse | null>(null)
const showLegalBooks = ref(false)

async function fetchDiagnosis() {
  aiLoading.value = true
  try {
    const res = await getReportDiagnosis(selectedPeriod.value, periodType.value, viewStore.isBoss)
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
  if (report.value?.pendingPayables !== undefined && report.value.pendingPayables !== null) {
    return report.value.pendingPayables
  }
  const ap = balanceSheetData.value?.liabilities.find(l => l.accountCode === '2202')?.currentAmount ?? 0
  const otherAp = balanceSheetData.value?.liabilities.find(l => l.accountCode === '2241')?.currentAmount ?? 0
  return ap + otherAp
})

// 老板个人垫资（未报销往来）
const ownerAdvanceUnreimbursed = computed(() => {
  const otherAp = balanceSheetData.value?.liabilities.find(l => l.accountCode === '2241')?.currentAmount
  if (otherAp !== undefined && otherAp > 0) {
    return otherAp
  }
  return entryStore.activeEntries
    .filter(e => (e as any).payer === 'boss_personal')
    .reduce((sum, e) => sum + Number(e.amount || 0), 0)
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
      itemStyle: { borderRadius: [0, 4, 4, 0], color: '#1747b0' },
    }],
  }
})

// ── 模块归类与实时结论透出（双视图自适应：老板人话决策，会计准则底册） ─────────────────
const businessHealthReports = computed(() => {
  if (viewStore.isBoss) {
    return [
      {
        path: '/reports/income-statement',
        label: '赚亏明细',
        desc: report.value
          ? `${periodScopeName.value}净赚 ${formatMoney(report.value.netProfit)} · 净利率 ${profitMargin.value !== null ? profitMargin.value + '%' : '0.0%'}`
          : '营业收入、采购成本与实际经营利润明细',
        icon: 'ri-funds-line',
      },
      {
        path: '/reports/ar-aging',
        label: '催款与应付清单',
        desc: `客户欠款 ${formatMoney(arAmount.value)} · 待付账单 ${formatMoney(apAmount.value)}`,
        icon: 'ri-line-chart-line',
        isWarning: arAmount.value > 0,
      },
      {
        path: '/aggregation/report',
        label: '项目利润',
        desc: '按客户、业务或独立项目查看具体盈利贡献',
        icon: 'ri-projector-line',
      },
      {
        path: '/reports/advance-balances',
        label: '垫付台账',
        desc: '你或员工替公司垫的钱，报了没有、还差多少',
        icon: 'ri-hand-coin-line',
      },
      {
        path: '/reports/health',
        label: '企业健康体检',
        desc: '企业综合健康评分，排查现金流与发票断档风险',
        icon: 'ri-heart-pulse-line',
      },
    ]
  }

  return [
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
      path: '/aggregation/report',
      label: '项目利润',
      desc: '按客户、业务或独立项目查看具体盈利贡献',
      icon: 'ri-projector-line',
    },
    {
      path: '/reports/health',
      label: '健康评分',
      desc: '企业综合健康度评测，监控现金与发票风险',
      icon: 'ri-heart-pulse-line',
    },
    {
      path: '/reports/advance-balances',
      label: '垫付台账',
      desc: '老板与员工垫付的其他应付款，按人拆到未报余额',
      icon: 'ri-hand-coin-line',
    },
    {
      path: '/reports/comparison',
      label: '同比/环比',
      desc: '对比历史同期，追踪营收与支出趋势',
      icon: 'ri-bar-chart-grouped-line',
    },
  ]
})

const taxComplianceReports = computed(() => {
  const vatDesc = periodType.value === 'quarter'
    ? `小规模季免税 30 万额度 · 预计本季税额 ${formatMoney(estimatedVat.value)}`
    : periodType.value === 'year'
      ? `全年增值税预估 · 预计年税额 ${formatMoney(estimatedVat.value)}`
      : `小规模季免税折算 · 预计本月税额 ${formatMoney(estimatedVat.value)}`

  if (viewStore.isBoss) {
    return [
      {
        path: '/reports/vat',
        label: '增值税额度与预估',
        desc: vatDesc,
        icon: 'ri-receipt-line',
      },
      {
        path: '/reports/cit',
        label: '企业所得税优惠',
        desc: `小微企业 5% 优惠税率 · 预计税额 ${formatMoney(estimatedCit.value)}`,
        icon: 'ri-government-line',
      },
      {
        path: '/tax-filing',
        label: '税务申报',
        desc: '增值税/所得税申报底稿、征期待办一站式办理',
        icon: 'ri-stamp-line',
      },
      {
        path: '/reports/archive',
        label: '单据合规检查',
        desc: '发票与付款单据合规性检查，追踪未归档凭证',
        icon: 'ri-archive-line',
      },
    ]
  }

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
      path: '/tax-filing',
      label: '税务申报',
      desc: '增值税/所得税申报底稿、征期待办一站式办理',
      icon: 'ri-stamp-line',
    },
    {
      path: '/reports/archive',
      label: '归档清单',
      desc: '单据合规性检查与未归档凭证追踪',
      icon: 'ri-archive-line',
    },
  ]
})

/** 法定财务报表（3 张）：期末结论 */
const financialStatementReports = computed(() => [
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
    path: '/reports/equity-changes',
    label: '权益变动表',
    desc: '实收资本、盈余公积与未分配利润的年度变动',
    icon: 'ri-pie-chart-2-line',
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

async function loadReport() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    const [reportRes, balanceRes] = await Promise.allSettled([
      getMonthlyReport(selectedPeriod.value, periodType.value, false, viewStore.isBoss),
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
// 切回标签时静默重算：别的标签里刚记的账不该漏在这张报表外
usePageRefresh(loadReport)

watch(() => viewStore.mode, () => {
  loadReport()
})
</script>

<template>
  <div class="report-page">
    <!-- 顶栏标题与月/季/年统计区间切换 -->
    <div class="page-header">
      <div class="header-titles">
        <h2>{{ viewStore.isBoss ? '经营概览' : '报表中心' }}</h2>
        <p class="subtitle">
          <template v-if="viewStore.isBoss">
            企业实时收支、手头现钱与往来账款
          </template>
          <template v-else>
            随时掌握企业真实收支、账上资金与税费状况
          </template>
        </p>
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
    <SoloEmpty
      v-if="isReportEmpty"
      class="report-empty"
      title="还没有可看的经营数据"
      description="报表由已过账的凭证汇总而来。先记第一笔账，这里就有数了。"
      icon="ri-bar-chart-box-line"
    >
      <template #action>
        <div class="empty-actions">
          <NButton type="primary" size="small" @click="router.push('/copilot')">
            记第一笔账
          </NButton>
          <NButton size="small" @click="router.push('/guide')">看演示账套</NButton>
        </div>
      </template>
    </SoloEmpty>

    <NSpin v-else :show="loading">
      <NCard :bordered="false" id="report-summary-card" class="summary-card mb-4">
        <!-- 核心经营数据 4 宫格 -->
        <div class="cockpit-grid">
          <div class="cockpit-item">
            <span class="cockpit-label">{{ periodScopeName }}{{ viewStore.isBoss ? '收入' : '营业收入' }}</span>
            <div class="cockpit-val income">{{ report ? formatMoney(report.monthIncome) : '—' }}</div>
            <span class="cockpit-sub">
              {{ viewStore.isBoss ? '客户打款、已开票与待确认入账' : '含已开票与经营收款' }}
            </span>
          </div>

          <div class="cockpit-item">
            <span class="cockpit-label">{{ periodScopeName }}{{ viewStore.isBoss ? '支出' : '总支出' }}</span>
            <div class="cockpit-val expense">{{ report ? formatMoney(report.monthExpense) : '—' }}</div>
            <span class="cockpit-sub">
              {{ viewStore.isBoss ? '采购付款、房租水电与日常开支' : '采购、房租、人工与费用' }}
            </span>
          </div>

          <div class="cockpit-item main-profit-item">
            <div class="label-with-tag">
              <span class="cockpit-label">{{ periodScopeName }}{{ viewStore.isBoss ? '净赚 (落袋利润)' : '净赚 (利润)' }}</span>
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
            <span class="cockpit-sub">
              {{ viewStore.isBoss ? '扣除全部支出与税费后剩余利润' : '扣除全部成本与税费后净收益' }}
            </span>
          </div>

          <div class="cockpit-item">
            <span class="cockpit-label">{{ viewStore.isBoss ? `预估${periodScopeName}应缴税` : `预计${periodScopeName}税负` }}</span>
            <div class="cockpit-val text-primary">{{ formatMoney(totalEstimatedTax) }}</div>
            <span class="cockpit-sub">
              增值税 {{ formatMoney(estimatedVat) }} · {{ viewStore.isBoss ? '企业所得税' : '所得税' }} {{ formatMoney(estimatedCit) }}
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
              <div class="position-label">{{ viewStore.isBoss ? '账上现钱' : '账上可用资金（货币资金）' }}</div>
              <div class="position-num text-primary">{{ formatMoney(cashAmount) }}</div>
              <div class="position-desc">{{ viewStore.isBoss ? '公户结余与随时可支配资金' : '公司银行账户与手头随时可支配现金' }}</div>
            </div>
          </div>

          <div class="position-divider" />

          <div class="position-item ar-item">
            <div class="position-icon-wrap amber">
              <i class="ri-hand-coin-line position-icon" />
            </div>
            <div class="position-info">
              <div class="position-label">{{ viewStore.isBoss ? '客户待回款' : '待收回客户欠款（应收账款）' }}</div>
              <div class="position-num" :class="arAmount > 0 ? 'text-warning' : 'text-primary'">
                {{ formatMoney(arAmount) }}
              </div>
              <div class="position-desc">{{ viewStore.isBoss ? '对方还没给我的钱，需关注回款' : '已记收入但钱还未到账，需关注催收回款' }}</div>
            </div>
          </div>

          <div class="position-divider" />

          <div class="position-item ap-item">
            <div class="position-icon-wrap">
              <i class="ri-bill-line position-icon" />
            </div>
            <div class="position-info">
              <div class="position-label">{{ viewStore.isBoss ? '待付支出' : '对外待付款项（应付账款）' }}</div>
              <div class="position-num text-secondary">{{ formatMoney(apAmount) }}</div>
              <div class="position-desc">{{ viewStore.isBoss ? '我还没付给供应商或账单的钱' : '已收到货物或发票但尚未付给供应商的款项' }}</div>
            </div>
          </div>

          <template v-if="viewStore.isBoss">
            <div class="position-divider" />

            <div class="position-item advance-item">
              <div class="position-icon-wrap indigo">
                <i class="ri-user-shared-line position-icon" />
              </div>
              <div class="position-info">
                <div class="position-label">老板个人垫资</div>
                <div class="position-num" :class="ownerAdvanceUnreimbursed > 0 ? 'text-indigo' : 'text-primary'">
                  {{ formatMoney(ownerAdvanceUnreimbursed) }}
                </div>
                <div class="position-desc">个人垫付公司开支尚未报销</div>
              </div>
            </div>
          </template>
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
          <div class="section-label">{{ viewStore.isBoss ? '钱花哪儿了（支出分类）' : '支出结构拆解' }}</div>
          <VChart :option="chartOption" autoresize style="height: 200px" />
        </div>
      </NCard>
    </NSpin>

    <!-- 报表三大板块分层 -->
    <div class="report-sections">
      <!-- 板块 1：经营分析（日常看赚亏与欠款） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-line-chart-line section-icon" />
            <h3>{{ viewStore.isBoss ? '经营分析' : '经营体检与盈利分析' }}</h3>
          </div>
          <span class="section-sub">{{ viewStore.isBoss ? '日常经营关切：赚亏明细、催款清单与项目毛利' : '日常经营关切：利润走势、客户欠款与健康度' }}</span>
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

      <!-- 板块 2：财税预警与合规（季末/月末申报） -->
      <section class="report-section">
        <div class="section-header">
          <div class="section-title-wrap">
            <i class="ri-file-shield-2-line section-icon" />
            <h3>{{ viewStore.isBoss ? '税务预警与合规' : '税务预警与申报合规' }}</h3>
          </div>
          <span class="section-sub">{{ viewStore.isBoss ? '申报关切：免税额度预警、优惠测算与单据完整度' : '季末与月末关切：增值税免税额度、所得税优惠与发票凭证' }}</span>
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

      <!-- 板块 3：法定财务报表（外部做账/审计底册，支持折叠收纳）。
           账簿不在这里：五本账统一走侧栏「会计账簿」工作台，避免同一批账两套入口。 -->
      <section v-if="!viewStore.isBoss" class="report-section legal-books">
        <div class="section-header toggle-header" @click="showLegalBooks = !showLegalBooks">
          <div class="section-title-wrap">
            <i class="ri-file-chart-line section-icon" />
            <h3>法定财务报表</h3>
            <span class="legal-badge">做账 / 审计 / 报税备查</span>
          </div>
          <button type="button" class="toggle-btn">
            <span>{{ showLegalBooks ? '收起报表' : '展开 3 张报表与税局导出' }}</span>
            <i :class="showLegalBooks ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          </button>
        </div>

        <div v-show="showLegalBooks" class="legal-content">
          <!-- 税务导出工具箱（从顶部下沉至此） -->
          <div class="tax-export-panel">
            <div class="export-panel-left">
              <div class="export-panel-title">
                <i class="ri-download-cloud-line" />
                <span>税局标准月季报与底册导出</span>
              </div>
              <p class="export-panel-desc">按《小企业会计准则》导出标准报税 XLS 表格，用于税务申报或交由会计师审计核查。</p>
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
                导出表格
              </NButton>
            </div>
          </div>

          <!-- 三张法定报表：期末口径，报税与审计直接引用 -->
          <div class="report-grid report-grid-3">
            <div
              v-for="card in financialStatementReports"
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

      <section v-if="viewStore.isBoss" class="boss-accounting-link">
        <div>
          <strong>需要查看法定报表、账簿或做账底稿？</strong>
          <p>这些属于会计工作区，切换后可以查看完整报表与凭证。</p>
        </div>
        <NButton size="small" secondary type="primary" @click="viewStore.setMode('accounting')">
          切换到会计视图
        </NButton>
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

.subtitle-basis {
  color: var(--solo-text-muted, #636e7c);
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
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
}

/* 驾驶舱看板卡片 */
.summary-card {
  border-radius: var(--solo-radius-lg, 12px);
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
  color: var(--solo-income, #0f7354);
}

.margin-tag.neg {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #bd3d3d);
}

.cockpit-label {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
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
  color: var(--solo-text-muted, #636e7c);
}

.income { color: var(--solo-income, #0f7354); }
.expense { color: var(--solo-expense, #bd3d3d); }
.text-primary { color: var(--solo-text-primary, #182230); }
.text-secondary { color: var(--solo-text-secondary, #586474); }
.text-warning { color: var(--solo-warning, #a05c10); }

/* 真金白银头寸速览条 */
.cash-position-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding: 12px 18px;
  background: var(--solo-surface-soft, #F1F4F8);
  border-radius: var(--solo-radius-md, 8px);
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
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.position-icon-wrap.amber {
  background: var(--solo-warning-soft, #FFF5E8);
  color: var(--solo-warning, #a05c10);
}

.position-icon-wrap.indigo {
  background: #edf3ff;
  color: #1747b0;
}

.text-indigo {
  color: #1747b0;
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
  color: var(--solo-text-muted, #636e7c);
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
  border-radius: var(--solo-radius-md, 8px);
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
  color: var(--solo-primary, #1747b0);
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

.boss-accounting-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-soft, #F1F4F8);
}

.boss-accounting-link strong {
  color: var(--solo-text-primary, #182230);
  font-size: 13px;
}

.boss-accounting-link p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary, #586474);
  font-size: 12px;
}

@media (max-width: 720px) {
  .boss-accounting-link {
    align-items: flex-start;
    flex-direction: column;
  }
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
  color: var(--solo-primary, #1747b0);
}

.section-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}

.section-sub {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.toggle-header {
  cursor: pointer;
  padding: 10px 14px;
  background: var(--solo-surface, #FFFFFF);
  border: 1px solid var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius-md, 8px);
  transition: all 0.15s ease;
}

.toggle-header:hover {
  border-color: var(--solo-primary, #1747b0);
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
  color: var(--solo-primary, #1747b0);
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
  border-radius: var(--solo-radius-md, 8px);
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
  color: var(--solo-text-muted, #636e7c);
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
  border-radius: var(--solo-radius-lg, 12px);
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: 0 1px 2px rgba(24, 34, 48, 0.04);
}

.boss-project-card {
  width: 100%;
  margin-top: 14px;
  text-align: left;
  font: inherit;
}

.report-card:hover {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 8px 24px rgba(24, 34, 48, 0.06);
  transform: translateY(-1px);
}

.card-icon {
  width: 42px;
  height: 42px;
  border-radius: var(--solo-radius-md, 8px);
  background: var(--solo-primary-soft, #EDF3FF);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
  transition: all 0.2s ease;
}

.card-icon.warning-icon {
  background: var(--solo-warning-soft, #FFF5E8);
  color: var(--solo-warning, #a05c10);
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
  color: var(--solo-text-muted, #636e7c);
  font-size: 18px;
  transition: transform 0.15s;
}

.report-card:hover .card-arrow {
  color: var(--solo-primary, #1747b0);
  transform: translateX(2px);
}

.legal-card {
  background: var(--solo-surface-soft, #F1F4F8);
}

/* 底册只剩「税局导出」和「三张法定报表」两块：账簿已移出去单独成工作台，
   这里再放一遍就等于同一批账两套入口。 */
.legal-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.report-grid-3 {
  grid-template-columns: repeat(3, 1fr);
}

/* 三列比两列窄，单行描述会把金额吃掉：放到两行封顶，信息不丢 */
.report-grid-3 .card-desc {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  line-height: 1.5;
  white-space: normal;
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
.report-empty {
  padding: 56px 20px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}

.empty-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
