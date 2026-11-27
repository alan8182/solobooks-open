<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton, NInputNumber, NTag, NSwitch, NModal, NInput, NDatePicker } from 'naive-ui'
import { SoloEmpty, SoloDrawer } from '@/components/ui'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import {
  getMonthlyReport,
  getReportDiagnosis,
  getBalanceSheet,
  getCashForecast,
  getCashForecastConfig,
  updateCashForecastConfig,
  getBusinessAttribution,
} from '@/api/report'
import { exportSmallBusinessMonthQuarterTaxReport } from '@/api/tax-report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { usePageRefresh } from '@/composables/usePageRefresh'
import type {
  MonthlyReportResponse,
  BalanceSheetResponse,
  CashForecastResponse,
  CashForecastConfig,
  ManualCashEvent,
  BusinessAttributionResponse,
} from '@/api/types'

import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent])

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
const showLegalBooks = ref(true)

const isBalanceSheetBalanced = computed(() => {
  if (!balanceSheetData.value) return true
  const { totalAssets = 0, totalLiabilities = 0, totalEquity = 0 } = balanceSheetData.value
  return Math.abs(totalAssets - (totalLiabilities + totalEquity)) < 0.01
})

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

// 资金安全跑道（Runway）：账上现钱按当期月均开支可维持月数
const cashRunwayMonths = computed(() => {
  const expense = report.value?.monthExpense || 0
  if (!cashAmount.value || cashAmount.value <= 0) return 0
  if (expense <= 0) return null
  const months = cashAmount.value / expense
  return Number(months.toFixed(1))
})

const categoryEntries = computed(() => {
  if (!report.value?.categoryBreakdown) return []
  return Object.entries(report.value.categoryBreakdown)
    .sort(([, a], [, b]) => b - a)
    .map(([name, amount]) => ({ name, amount }))
})

const categoryChartHeight = computed(() =>
  Math.min(180, Math.max(96, categoryEntries.value.length * 28 + 20)),
)

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
        desc: '从账实相符、公私边界、现金跑道评估合规与融资尽调就绪度',
        icon: 'ri-heart-pulse-line',
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
      path: '/reports/archive',
      label: '单据合规检查',
      desc: '单据合规性检查与未归档凭证追踪',
      icon: 'ri-archive-line',
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

// ── 现金流滚动预测与资金跑道 ──
const forecastHorizon = ref<number>(60)
const forecastScenario = ref<'neutral' | 'optimistic' | 'pessimistic'>('neutral')
const forecastLoading = ref(false)
const forecastData = ref<CashForecastResponse | null>(null)
const showForecastDrawer = ref(false)
const showAttributionDrawer = ref(false)
const showConfigDrawer = ref(false)
const configSaving = ref(false)
const forecastConfig = ref<CashForecastConfig>({
  defaultArDays: 30,
  defaultApDays: 30,
  partnerCreditDays: {},
  safetyCashThreshold: null,
  manualCashEvents: [],
})

const showAddEventModal = ref(false)
const newEventForm = ref<{
  name: string
  type: 'inflow' | 'outflow'
  amount: number
  date: string
  repeat: 'none' | 'monthly' | 'quarterly' | 'yearly'
  repeatDay: number | null
  confidence: 'high' | 'medium' | 'low'
}>({
  name: '',
  type: 'outflow',
  amount: 10000,
  date: '',
  repeat: 'none',
  repeatDay: 10,
  confidence: 'high',
})

async function loadForecast() {
  forecastLoading.value = true
  try {
    const res = await getCashForecast('', forecastHorizon.value, forecastScenario.value)
    forecastData.value = res
  } catch (err) {
    console.error('加载现金流预测失败:', err)
  } finally {
    forecastLoading.value = false
  }
}

async function openConfigDrawer() {
  showConfigDrawer.value = true
  try {
    const cfg = await getCashForecastConfig()
    forecastConfig.value = {
      defaultArDays: cfg.defaultArDays || 30,
      defaultApDays: cfg.defaultApDays || 30,
      partnerCreditDays: cfg.partnerCreditDays || {},
      safetyCashThreshold: cfg.safetyCashThreshold,
      manualCashEvents: cfg.manualCashEvents || [],
    }
  } catch (err) {
    console.error('加载现金流配置失败:', err)
  }
}

async function saveConfig() {
  configSaving.value = true
  try {
    await updateCashForecastConfig(forecastConfig.value)
    message.success('现金流预测规则已保存')
    showConfigDrawer.value = false
    await loadForecast()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '保存失败')
  } finally {
    configSaving.value = false
  }
}

function openAddEventModal() {
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10)
  newEventForm.value = {
    name: '',
    type: 'outflow',
    amount: 10000,
    date: tomorrow,
    repeat: 'none',
    repeatDay: 10,
    confidence: 'high',
  }
  showAddEventModal.value = true
}

async function confirmAddEvent() {
  if (!newEventForm.value.name.trim()) {
    message.warning('请输入事项名称')
    return
  }
  if (!newEventForm.value.amount || newEventForm.value.amount <= 0) {
    message.warning('请输入有效金额')
    return
  }

  if (!forecastConfig.value.manualCashEvents) {
    const cfg = await getCashForecastConfig()
    forecastConfig.value = {
      defaultArDays: cfg.defaultArDays || 30,
      defaultApDays: cfg.defaultApDays || 30,
      partnerCreditDays: cfg.partnerCreditDays || {},
      safetyCashThreshold: cfg.safetyCashThreshold,
      manualCashEvents: cfg.manualCashEvents || [],
    }
  }

  const newEv: ManualCashEvent = {
    id: `evt_${Date.now()}`,
    name: newEventForm.value.name.trim(),
    type: newEventForm.value.type,
    amount: newEventForm.value.amount,
    date: newEventForm.value.date,
    repeat: newEventForm.value.repeat,
    repeatDay: newEventForm.value.repeat === 'monthly' ? (newEventForm.value.repeatDay || 10) : null,
    confidence: newEventForm.value.confidence,
    enabled: true,
  }

  forecastConfig.value.manualCashEvents = [
    ...(forecastConfig.value.manualCashEvents || []),
    newEv,
  ]

  try {
    await updateCashForecastConfig(forecastConfig.value)
    message.success('已登记预期收支，现金流预测已重新计算')
    showAddEventModal.value = false
    await loadForecast()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '保存失败')
  }
}

async function toggleTimelineEvent(eventId: string) {
  const baseId = eventId.split('_').slice(0, 2).join('_')
  if (!forecastConfig.value.manualCashEvents?.length) {
    const cfg = await getCashForecastConfig()
    forecastConfig.value.manualCashEvents = cfg.manualCashEvents || []
  }

  const target = forecastConfig.value.manualCashEvents?.find(
    e => e.id === eventId || e.id === baseId
  )
  if (!target) return

  target.enabled = !target.enabled
  try {
    await updateCashForecastConfig(forecastConfig.value)
    message.info(target.enabled ? `已启用「${target.name}」` : `已在预测中停用「${target.name}」`)
    await loadForecast()
  } catch {
    message.error('更新失败')
  }
}

async function removeConfigEvent(id: string) {
  forecastConfig.value.manualCashEvents = (forecastConfig.value.manualCashEvents || []).filter(
    e => e.id !== id
  )
  try {
    await updateCashForecastConfig(forecastConfig.value)
    message.success('已删除收支规则')
    await loadForecast()
  } catch {
    message.error('删除失败')
  }
}

// ── 经营波动归因与利现差额桥 ──
const attributionMode = ref<'mom' | 'yoy'>('mom')
const attributionLoading = ref(false)
const attributionData = ref<BusinessAttributionResponse | null>(null)

async function loadAttribution() {
  attributionLoading.value = true
  try {
    const res = await getBusinessAttribution(representativeMonth.value, attributionMode.value)
    attributionData.value = res
  } catch (err) {
    console.error('加载经营归因失败:', err)
  } finally {
    attributionLoading.value = false
  }
}

const attributionCardSummary = computed(() => {
  if (!attributionData.value) return '对比净利润与现金变动，穿透应收、应付与开支占用'
  const raw = attributionData.value.plainSummary || ''
  // 剥离重复的【利现差额】前缀（数字已在上方大字展示），优先提取真正的归因原因
  const cleaned = raw.replace(/^【利现差额】[^。]*[。]\s*/, '').trim()
  if (cleaned) return cleaned
  if (Math.abs(attributionData.value.profitToCashDiscrepancy || 0) < 0.01) {
    return '当期账面利润与现金变动基本对齐，无显著往来款或开支占用'
  }
  return raw
})

const attributionStatus = computed(() => {
  if (!attributionData.value) return null
  const { netProfit, netCashFlow } = attributionData.value
  if (netProfit > 0 && netCashFlow < netProfit) {
    return { text: netCashFlow <= 0 ? '利润承压未变现' : '部分利润未变现', type: 'warning' as const }
  }
  if (netCashFlow >= netProfit && netCashFlow > 0) {
    return { text: '现金转化良好', type: 'success' as const }
  }
  return { text: '利现基本对齐', type: 'default' as const }
})

const forecastChartOption = computed(() => {
  if (!forecastData.value || !forecastData.value.dailyPoints?.length) return {}
  const points = forecastData.value.dailyPoints
  const xData = points.map(p => p.date.slice(5))
  const yData = points.map(p => p.balance)
  const safety = Number(forecastData.value.safetyThreshold || 0)

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const item = params[0]
        const idx = item.dataIndex
        const p = points[idx]
        return `
          <div style="font-size: 13px; line-height: 1.6;">
            <strong>${p.date}</strong><br/>
            预计余额: <b>${formatMoney(p.balance)}</b><br/>
            预计流入: +${formatMoney(p.inflow)}<br/>
            预计流出: -${formatMoney(p.outflow)}<br/>
            当日净额: ${p.net >= 0 ? '+' : ''}${formatMoney(p.net)}
          </div>
        `
      },
    },
    grid: { left: 70, right: 30, top: 20, bottom: 25 },
    xAxis: {
      type: 'category',
      data: xData,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#dde3ea' } },
      axisLabel: { color: '#586474', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { type: 'dashed', color: '#edf0f4' } },
      axisLabel: {
        color: '#586474',
        formatter: (val: number) => `¥${(val / 1000).toFixed(0)}k`,
      },
    },
    series: [
      {
        name: '预计可用现金',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: yData,
        lineStyle: { width: 3, color: '#1747b0' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(23, 71, 176, 0.25)' },
              { offset: 1, color: 'rgba(23, 71, 176, 0.01)' },
            ],
          },
        },
        markLine: safety > 0 ? {
          silent: true,
          symbol: 'none',
          data: [
            {
              yAxis: safety,
              label: {
                formatter: `安全警戒线 ${formatMoney(safety)}`,
                position: 'insideEndTop',
                color: '#bd3d3d',
                fontSize: 11,
              },
              lineStyle: { type: 'dashed', color: '#bd3d3d', width: 1.5 },
            },
          ],
        } : undefined,
      },
    ],
  }
})

onMounted(() => {
  loadReport()
  loadForecast()
  loadAttribution()
})
// 切回标签时静默重算：别的标签里刚记的账不该漏在这张报表外
usePageRefresh(() => {
  loadReport()
  loadForecast()
  loadAttribution()
})

watch(() => viewStore.mode, () => {
  loadReport()
})

watch([forecastHorizon, forecastScenario], () => {
  loadForecast()
})

watch([attributionMode, representativeMonth], () => {
  loadAttribution()
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
          </div>

          <div class="cockpit-item">
            <span class="cockpit-label">{{ periodScopeName }}{{ viewStore.isBoss ? '支出' : '总支出' }}</span>
            <div class="cockpit-val expense">{{ report ? formatMoney(report.monthExpense) : '—' }}</div>
          </div>

          <div class="cockpit-item main-profit-item">
            <div class="label-with-tag">
              <span class="cockpit-label">{{ periodScopeName }}{{ viewStore.isBoss ? '净赚' : '净利润' }}</span>
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
              <div class="position-desc">
                <template v-if="viewStore.isBoss">
                  {{ cashRunwayMonths !== null ? (cashRunwayMonths > 0 ? `公户结余 · 可支撑约 ${cashRunwayMonths} 个月开销` : '公户结余 · 现金流吃紧需关注') : '公户结余与随时可支配资金' }}
                </template>
                <template v-else>
                  公司银行账户与手头随时可支配现金
                </template>
              </div>
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

        <!-- AI 经营诊断（生成后展开正文；未生成时并入分类标题右侧或轻量单行触发） -->
        <div v-if="aiSummary" class="ai-summary-box">
          <div class="ai-summary-header">
            <div class="summary-badge">
              <i class="ri-line-chart-line" />
              <span>AI 经营诊断</span>
            </div>
            <NButton
              size="tiny"
              secondary
              type="primary"
              :loading="aiLoading"
              @click="fetchDiagnosis"
            >
              <template #icon>
                <i class="ri-refresh-line" />
              </template>
              重新诊断
            </NButton>
          </div>
          <p class="summary-text">{{ aiSummary }}</p>
        </div>

        <!-- 分类支出占比 -->
        <div v-if="categoryEntries.length > 0" class="category-section">
          <div class="category-section-head">
            <div class="section-label">{{ viewStore.isBoss ? '钱花哪儿了（支出分类）' : '支出结构拆解' }}</div>
            <NButton
              v-if="!aiSummary"
              size="tiny"
              secondary
              type="primary"
              :loading="aiLoading"
              @click="fetchDiagnosis"
            >
              <template #icon>
                <i class="ri-line-chart-line" />
              </template>
              AI 经营诊断
            </NButton>
          </div>
          <VChart :option="chartOption" autoresize :style="{ height: `${categoryChartHeight}px` }" />
        </div>
        <div v-else-if="!aiSummary" class="ai-inline-trigger">
          <NButton
            size="tiny"
            secondary
            type="primary"
            :loading="aiLoading"
            @click="fetchDiagnosis"
          >
            <template #icon>
              <i class="ri-line-chart-line" />
            </template>
            AI 经营诊断
          </NButton>
        </div>
      </NCard>
    </NSpin>

    <!-- AI 决策罗盘：资金跑道与利现差额轻量看板 -->
    <div v-if="viewStore.isBoss" class="decision-compass-strip">
      <!-- 资金跑道透视卡 -->
      <div
        class="compass-card"
        role="button"
        tabindex="0"
        @click="showForecastDrawer = true"
        @keydown.enter="showForecastDrawer = true"
      >
        <div class="compass-header">
          <div class="compass-title-wrap">
            <div class="compass-icon-wrap primary">
              <i class="ri-funds-box-line" />
            </div>
            <span class="compass-card-title">现金流预测</span>
          </div>
          <div class="compass-badge">
            <NTag v-if="forecastData?.isDeficitProjected" type="error" size="small">
              高危：资金赤字
            </NTag>
            <NTag v-else-if="forecastData && forecastData.minBalance < (forecastData.safetyThreshold || 0)" type="warning" size="small">
              预警：触及安全线
            </NTag>
            <NTag v-else-if="forecastData" type="success" size="small">
              健康：资金充裕
            </NTag>
          </div>
        </div>

        <div class="compass-stat-group">
          <div class="compass-stat-item">
            <span class="stat-label">可支撑月数</span>
            <span class="stat-num text-primary">
              {{
                forecastData?.runwayMonths !== null && forecastData?.runwayMonths !== undefined
                  ? (forecastData.runwayMonths >= 60 ? '60+ 个月' : `${forecastData.runwayMonths} 个月`)
                  : '测算中...'
              }}
            </span>
          </div>
          <div class="compass-stat-item">
            <span class="stat-label">预计最低资金</span>
            <span class="stat-num" :class="forecastData && forecastData.minBalance < (forecastData.safetyThreshold || 0) ? 'text-danger' : ''">
              {{ forecastData ? formatMoney(forecastData.minBalance) : '¥0.00' }}
            </span>
          </div>
        </div>

        <div class="compass-footer">
          <p class="compass-summary-line">
            <template v-if="forecastData?.minBalanceDate">
              预计 {{ forecastData.minBalanceDate }} 最低探底 · 安全线 {{ formatMoney(forecastData.safetyThreshold || 0) }}
            </template>
            <template v-else>
              已综合未结应收、应付支出与月度固定开销
            </template>
          </p>
          <span class="footer-link-text">
            走势推演 <i class="ri-arrow-right-line" />
          </span>
        </div>
      </div>

      <!-- 利现差额透视卡 -->
      <div
        class="compass-card"
        role="button"
        tabindex="0"
        @click="showAttributionDrawer = true"
        @keydown.enter="showAttributionDrawer = true"
      >
        <div class="compass-header">
          <div class="compass-title-wrap">
            <div class="compass-icon-wrap indigo">
              <i class="ri-bubble-chart-line" />
            </div>
            <span class="compass-card-title">利润与现金对齐</span>
          </div>
          <div class="compass-badge">
            <NTag v-if="attributionStatus" :type="attributionStatus.type" size="small">
              {{ attributionStatus.text }}
            </NTag>
          </div>
        </div>

        <div class="compass-stat-group">
          <div class="compass-stat-item">
            <span class="stat-label">实际现金变动</span>
            <span class="stat-num" :class="attributionData && attributionData.netCashFlow >= 0 ? 'text-primary' : 'text-danger'">
              {{ attributionData ? (attributionData.netCashFlow >= 0 ? `+${formatMoney(attributionData.netCashFlow)}` : formatMoney(attributionData.netCashFlow)) : '测算中...' }}
            </span>
          </div>
          <div class="compass-stat-item">
            <span class="stat-label">账面净利润</span>
            <span class="stat-num" :class="attributionData && attributionData.netProfit < 0 ? 'text-danger' : 'text-primary'">
              {{ attributionData ? (attributionData.netProfit >= 0 ? `+${formatMoney(attributionData.netProfit)}` : formatMoney(attributionData.netProfit)) : '¥0.00' }}
            </span>
          </div>
        </div>

        <div class="compass-footer">
          <p class="compass-summary-line text-truncate" :title="attributionCardSummary">
            {{ attributionCardSummary }}
          </p>
          <span class="footer-link-text">
            查看明细 <i class="ri-arrow-right-line" />
          </span>
        </div>
      </div>
    </div>

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

      <!-- 板块 2：法定财务报表（外部做账/审计底册，支持折叠收纳）。
           账簿不在这里：五本账统一走侧栏「会计账簿」工作台，避免同一批账两套入口。 -->
      <section v-if="!viewStore.isBoss" class="report-section legal-books">
        <div class="section-header toggle-header" @click="showLegalBooks = !showLegalBooks">
          <div class="section-title-wrap">
            <i class="ri-file-chart-line section-icon" />
            <h3>法定财务报表</h3>
            <span class="legal-badge">做账 / 审计 / 报税备查</span>
            <span class="balance-tag" :class="isBalanceSheetBalanced ? 'balanced' : 'unbalanced'">
              <i :class="isBalanceSheetBalanced ? 'ri-checkbox-circle-line' : 'ri-error-warning-line'" />
              {{ isBalanceSheetBalanced ? '资产负债表已配平' : '试算未配平' }}
            </span>
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
        <span>需要查看法定报表、账簿或做账底稿？</span>
        <NButton size="small" secondary type="primary" @click="viewStore.setMode('accounting')">
          切换到会计视图
        </NButton>
      </section>
    </div>

    <!-- 现金流预测抽屉 -->
    <SoloDrawer
      v-model:show="showForecastDrawer"
      title="现金流预测"
      :width="760"
    >
      <div class="drawer-controls-bar">
        <div class="control-item">
          <span class="control-label">周期：</span>
          <NRadioGroup v-model:value="forecastHorizon" size="small">
            <NRadioButton :value="30">30天</NRadioButton>
            <NRadioButton :value="60">60天</NRadioButton>
            <NRadioButton :value="90">90天</NRadioButton>
          </NRadioGroup>
        </div>
        <div class="control-item">
          <span class="control-label">情景：</span>
          <NRadioGroup v-model:value="forecastScenario" size="small">
            <NRadioButton value="neutral">中性</NRadioButton>
            <NRadioButton value="optimistic">乐观</NRadioButton>
            <NRadioButton value="pessimistic">悲观</NRadioButton>
          </NRadioGroup>
        </div>
        <NButton size="small" secondary @click="openConfigDrawer">
          <template #icon><i class="ri-settings-4-line" /></template>
          预测规则
        </NButton>
      </div>

      <NSpin :show="forecastLoading">
        <div v-if="forecastData" class="forecast-content">
          <!-- 核心指标摘要 -->
          <div class="forecast-metrics-grid">
            <div class="forecast-metric-item">
              <div class="metric-label">当前可用资金</div>
              <div class="metric-val text-primary">{{ formatMoney(forecastData.currentCash) }}</div>
              <div class="metric-sub">实时活期与现金总和</div>
            </div>
            <div class="forecast-metric-item">
              <div class="metric-label">预测最低余额</div>
              <div class="metric-val" :class="forecastData.minBalance < (forecastData.safetyThreshold || 0) ? 'text-danger' : 'text-primary'">
                {{ formatMoney(forecastData.minBalance) }}
              </div>
              <div class="metric-sub">
                {{ forecastData.minBalanceDate ? `预计 ${forecastData.minBalanceDate}` : '资金平稳' }}
              </div>
            </div>
            <div class="forecast-metric-item">
              <div class="metric-label">可支撑月数</div>
              <div class="metric-val text-indigo">
                {{
                  forecastData.runwayMonths !== null
                    ? (forecastData.runwayMonths >= 60 ? '60+ 个月' : `${forecastData.runwayMonths} 个月`)
                    : '60+ 个月'
                }}
              </div>
              <div class="metric-sub">按月均开支测算</div>
            </div>
            <div class="forecast-metric-item">
              <div class="metric-label">水位风险</div>
              <div class="metric-status">
                <NTag v-if="forecastData.isDeficitProjected" type="error" size="small">高危：预计赤字</NTag>
                <NTag v-else-if="forecastData.minBalance < (forecastData.safetyThreshold || 0)" type="warning" size="small">预警：触及安全线</NTag>
                <NTag v-else type="success" size="small">健康：资金充裕</NTag>
              </div>
              <div class="metric-sub">
                安全线 {{ formatMoney(forecastData.safetyThreshold || 0) }}
              </div>
            </div>
          </div>

          <!-- 预警与高危提示条（仅在触及安全线或赤字时高亮提醒） -->
          <div
            v-if="forecastData.scenarioSummary && (forecastData.isDeficitProjected || forecastData.minBalance < (forecastData.safetyThreshold || 0))"
            class="forecast-insights-box"
          >
            <div
              class="insight-item"
              :class="forecastData.isDeficitProjected ? 'insight-danger' : 'insight-warning'"
            >
              <i :class="forecastData.isDeficitProjected ? 'ri-alarm-warning-line' : 'ri-error-warning-line'" />
              <div class="insight-body">
                <span class="insight-title">{{ forecastData.scenario === 'pessimistic' ? '悲观推演预警' : (forecastData.scenario === 'optimistic' ? '乐观推演预警' : '资金水位预警') }}</span>
                <span class="insight-desc">{{ forecastData.scenarioSummary }}</span>
              </div>
            </div>
          </div>

          <!-- ECharts 折线图 -->
          <div class="forecast-chart-wrap">
            <div class="chart-caption">
              <span>资金水位走势（每日预计可用余额）</span>
            </div>
            <VChart :option="forecastChartOption" autoresize style="height: 220px; width: 100%;" />
          </div>

          <!-- 待收催收排期 -->
          <div class="forecast-overdue-box">
            <div class="schedule-head">
              <div class="schedule-head-title">
                <i class="ri-alarm-warning-line text-warning" />
                <span>待催收客户账款</span>
              </div>
              <span class="head-count">
                {{ forecastData.overdueReceivables?.length ? `共 ${forecastData.overdueReceivables.length} 笔（逾期优先）` : '暂无待催收账款' }}
              </span>
            </div>
            <div v-if="forecastData.overdueReceivables && forecastData.overdueReceivables.length > 0" class="schedule-list">
              <div
                v-for="(item, idx) in forecastData.overdueReceivables"
                :key="idx"
                class="schedule-row"
              >
                <div class="row-partner">
                  <span class="partner-name">{{ item.partnerName }}</span>
                  <NTag v-if="item.daysOverdue > 0" size="tiny" type="error">已逾期 {{ item.daysOverdue }} 天</NTag>
                  <NTag v-else size="tiny" type="default">最久单据 {{ item.oldestDate }}</NTag>
                </div>
                <div class="row-amount text-primary">+{{ formatMoney(item.amount) }}</div>
              </div>
            </div>
          </div>

          <!-- 未来关键资金日程与预期收支（沙盘推演） -->
          <div class="forecast-timeline-box">
            <div class="schedule-head">
              <div class="schedule-head-title">
                <i class="ri-calendar-event-line text-primary" />
                <span>未来资金日程与收支测算 (沙盘推演)</span>
              </div>
              <div class="schedule-head-actions">
                <span class="head-count">共 {{ forecastData.timelineEvents?.length || 0 }} 项节点</span>
                <NButton size="tiny" type="primary" secondary @click="openAddEventModal">
                  <template #icon><i class="ri-add-line" /></template>
                  添加预期收支
                </NButton>
              </div>
            </div>

            <div v-if="!forecastData.timelineEvents || forecastData.timelineEvents.length === 0" class="schedule-empty">
              暂无未来计划收支节点。可点击右上角添加预期回款或偶发大额支出。
            </div>
            <div v-else class="timeline-list">
              <div
                v-for="ev in forecastData.timelineEvents"
                :key="ev.id"
                class="timeline-row"
                :class="{ 'is-disabled': ev.enabled === false }"
              >
                <div class="timeline-date-badge">
                  <span class="timeline-day">{{ ev.date.slice(5) }}</span>
                </div>
                <div class="timeline-detail">
                  <div class="timeline-title-bar">
                    <span class="timeline-title">{{ ev.name }}</span>
                    <NTag v-if="ev.source === 'system_tax'" size="tiny" type="warning">税费征期</NTag>
                    <NTag v-else-if="ev.repeat === 'monthly'" size="tiny" type="info">每月固定</NTag>
                    <NTag v-else-if="ev.repeat === 'quarterly'" size="tiny" type="info">季度固定</NTag>
                    <NTag v-if="ev.confidence === 'medium'" size="tiny" type="default">口头约定</NTag>
                    <NTag v-else-if="ev.confidence === 'low'" size="tiny" type="default">初步意向</NTag>
                  </div>
                  <div class="timeline-sub">
                    <span>{{ ev.date }}</span>
                    <span v-if="ev.repeat && ev.repeat !== 'none'"> · 周期性</span>
                  </div>
                </div>
                <div class="timeline-amount-col">
                  <span
                    v-if="Number(ev.amount) > 0"
                    class="timeline-amount"
                    :class="ev.type === 'inflow' ? 'text-primary' : 'text-danger'"
                  >
                    {{ ev.type === 'inflow' ? '+' : '-' }}{{ formatMoney(Number(ev.amount)) }}
                  </span>
                  <span v-else class="timeline-amount text-muted">预警提醒</span>
                  <div v-if="ev.source === 'manual'" class="timeline-switch">
                    <NSwitch
                      :value="ev.enabled"
                      size="small"
                      @update:value="() => toggleTimelineEvent(ev.id)"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </NSpin>
    </SoloDrawer>

    <!-- 利润与现金对齐抽屉 -->
    <SoloDrawer
      v-model:show="showAttributionDrawer"
      title="利润与现金对齐"
      :width="760"
    >
      <div class="drawer-controls-bar">
        <div class="control-item">
          <span class="control-label">对比口径：</span>
          <NRadioGroup v-model:value="attributionMode" size="small">
            <NRadioButton value="mom">环比（上月）</NRadioButton>
            <NRadioButton value="yoy">同比（去年同月）</NRadioButton>
          </NRadioGroup>
        </div>
      </div>

      <NSpin :show="attributionLoading">
        <div v-if="attributionData" class="attribution-content">
          <!-- 左右双栏：左侧利现差额桥，右侧驱动因子分解 -->
          <div class="attribution-grid">
            <!-- 左侧：利现差额桥 -->
            <div class="bridge-panel">
              <div class="panel-title">
                <i class="ri-git-commit-line" />
                <span>利润与现金调节桥</span>
              </div>
              <p class="bridge-desc">{{ attributionData.plainSummary }}</p>

              <div class="bridge-steps-list">
                <div
                  v-for="(step, idx) in attributionData.bridgeSteps"
                  :key="idx"
                  class="bridge-step-row"
                  :class="{
                    'bridge-base': step.type === 'profit',
                    'bridge-total': step.type === 'cash',
                  }"
                >
                  <div class="step-label">
                    <span
                      v-if="step.type === 'add'"
                      class="step-op plus"
                    >+</span>
                    <span
                      v-else-if="step.type === 'sub'"
                      class="step-op minus"
                    >-</span>
                    <div class="step-label-text">
                      <span class="step-name">{{ step.name }}</span>
                      <span v-if="step.note" class="step-note">{{ step.note }}</span>
                    </div>
                  </div>
                  <div
                    class="step-val"
                    :class="step.amount >= 0 ? (step.type === 'cash' ? 'text-primary font-bold' : 'text-primary') : 'text-danger'"
                  >
                    {{ step.type === 'add' && step.amount > 0 ? '+' : '' }}{{ formatMoney(step.amount) }}
                  </div>
                </div>
              </div>
            </div>

            <!-- 右侧：收入与成本驱动因素 -->
            <div class="drivers-panel">
              <!-- 收入因子 -->
              <div class="driver-section">
                <div class="driver-header">
                  <div class="driver-title">
                    <i class="ri-arrow-up-circle-line text-primary" />
                    <span>主要收入驱动分析</span>
                  </div>
                  <span class="driver-stat">
                    利润波动: {{ attributionData.profitDelta >= 0 ? '+' : '' }}{{ formatMoney(attributionData.profitDelta) }}
                  </span>
                </div>

                <!-- 客户贡献标签清单 -->
                <div v-if="attributionData.topIncomeDrivers?.length" class="driver-items-list">
                  <div
                    v-for="(cust, idx) in attributionData.topIncomeDrivers.slice(0, 5)"
                    :key="idx"
                    class="driver-item"
                  >
                    <div class="driver-name-tag">
                      <span class="driver-name">{{ cust.name }}</span>
                      <NTag v-if="cust.tag === '新客户' || cust.tag === 'new'" type="success" size="tiny">新客户</NTag>
                      <NTag v-else-if="cust.tag === '流失' || cust.tag === 'churned'" type="error" size="tiny">流失</NTag>
                      <NTag v-else-if="cust.tag === '增量' || cust.tag === 'expansion'" type="info" size="tiny">增量</NTag>
                      <NTag v-else size="tiny">{{ cust.tag || '波动' }}</NTag>
                    </div>
                    <div class="driver-val" :class="cust.deltaAmount >= 0 ? 'text-primary' : 'text-danger'">
                      {{ cust.deltaAmount >= 0 ? '+' : '' }}{{ formatMoney(cust.deltaAmount) }}
                    </div>
                  </div>
                </div>
                <div v-else class="driver-empty-neutral">
                  <span>暂无收入主要波动因子</span>
                </div>
              </div>

              <!-- 费用异常与激增项 -->
              <div class="driver-section">
                <div class="driver-header">
                  <div class="driver-title">
                    <i class="ri-alert-line text-warning" />
                    <span>支出异常激增检测</span>
                  </div>
                </div>

                <!-- 异常检测提醒 -->
                <div v-if="attributionData.anomalies?.length" class="anomalies-list">
                  <div
                    v-for="(anomaly, idx) in attributionData.anomalies"
                    :key="idx"
                    class="anomaly-card"
                  >
                    <div class="anomaly-top">
                      <span class="anomaly-name">{{ anomaly.title || anomaly.category }}</span>
                      <NTag v-if="anomaly.growthRate !== null" type="error" size="tiny">激增 +{{ (anomaly.growthRate * 100).toFixed(0) }}%</NTag>
                    </div>
                    <div class="anomaly-desc">{{ anomaly.reason }}</div>
                  </div>
                </div>
                <div v-else class="anomalies-empty">
                  <i class="ri-shield-check-line" />
                  <span>未发现支出异常突增项目，开支结构平稳</span>
                </div>

                <!-- 费用主要变动项 -->
                <div v-if="attributionData.topExpenseDrivers?.length" class="driver-items-list" style="margin-top: 8px;">
                  <div
                    v-for="(exp, idx) in attributionData.topExpenseDrivers.slice(0, 3)"
                    :key="idx"
                    class="driver-item"
                  >
                    <div class="driver-name-tag">
                      <span class="driver-name">{{ exp.name }}</span>
                      <span class="caption-tip">较上期变动</span>
                    </div>
                    <div class="driver-val" :class="exp.deltaAmount > 0 ? 'text-danger' : 'text-primary'">
                      {{ exp.deltaAmount > 0 ? '+' : '' }}{{ formatMoney(exp.deltaAmount) }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </NSpin>
    </SoloDrawer>

    <!-- 现金流预测规则配置抽屉 -->
    <SoloDrawer
      v-model:show="showConfigDrawer"
      title="现金流预测规则配置"
      :width="600"
    >
      <div class="config-drawer-content">
        <p class="config-intro">
          设置企业默认账期与周期性固定收支项。AI 财务官将根据本配置在滚动推演中自动计算未到期资金流动。
        </p>

        <!-- 基础账期与水位线 -->
        <div class="config-group">
          <div class="config-group-title">基础账期与警戒线</div>
          <div class="config-fields-grid">
            <div class="config-field">
              <label>默认客户收款账期 (天)</label>
              <NInputNumber
                v-model:value="forecastConfig.defaultArDays"
                :min="1"
                :max="365"
                placeholder="如 30"
              />
              <span class="field-hint">未设置特定约定时按此天数推算客户应收回款日</span>
            </div>
            <div class="config-field">
              <label>默认供应商付款账期 (天)</label>
              <NInputNumber
                v-model:value="forecastConfig.defaultApDays"
                :min="1"
                :max="365"
                placeholder="如 30"
              />
              <span class="field-hint">未设置特定约定时按此天数推算供应商应付款到期日</span>
            </div>
            <div class="config-field full-width">
              <label>安全现金储备警戒线 (元)</label>
              <NInputNumber
                v-model:value="forecastConfig.safetyCashThreshold"
                :min="0"
                :step="10000"
                placeholder="如 50000 (留空为 0)"
              />
              <span class="field-hint">账面可用现钱低于此水位时系统将高亮预警并提示催收</span>
            </div>
          </div>
        </div>

        <!-- 预期与周期性收支日程列表 -->
        <div class="config-group">
          <div class="config-group-head">
            <div class="config-group-title">预期与周期性收支规则 (已配置 {{ forecastConfig.manualCashEvents?.length || 0 }} 条)</div>
            <NButton size="tiny" secondary type="primary" @click="openAddEventModal">
              <template #icon><i class="ri-add-line" /></template>
              新增规则
            </NButton>
          </div>
          <div v-if="!forecastConfig.manualCashEvents || forecastConfig.manualCashEvents.length === 0" class="config-events-empty">
            暂无自定义收支规则。添加如固定发薪日、办公室租金、大额设备采购等可更准确测算资金水位。
          </div>
          <div v-else class="config-events-list">
            <div
              v-for="item in forecastConfig.manualCashEvents"
              :key="item.id"
              class="config-event-item"
            >
              <div class="event-item-info">
                <div class="event-item-name">
                  <span class="event-title">{{ item.name }}</span>
                  <NTag :type="item.type === 'inflow' ? 'success' : 'error'" size="tiny">
                    {{ item.type === 'inflow' ? '流入' : '支出' }}
                  </NTag>
                  <NTag v-if="item.repeat === 'monthly'" size="tiny" type="info">每月 {{ item.repeatDay }} 号</NTag>
                  <NTag v-else-if="item.repeat === 'quarterly'" size="tiny" type="info">季度循环</NTag>
                  <NTag v-else size="tiny">{{ item.date || '单次' }}</NTag>
                  <NTag v-if="item.confidence === 'medium'" size="tiny">口头承诺</NTag>
                  <NTag v-else-if="item.confidence === 'low'" size="tiny">初步意向</NTag>
                </div>
                <div class="event-item-amount" :class="item.type === 'inflow' ? 'text-primary' : 'text-danger'">
                  {{ item.type === 'inflow' ? '+' : '-' }}{{ formatMoney(Number(item.amount)) }}
                </div>
              </div>
              <div class="event-item-ops">
                <NSwitch v-model:value="item.enabled" size="small" />
                <NButton text size="tiny" type="error" @click="removeConfigEvent(item.id)">
                  <template #icon><i class="ri-delete-bin-line" /></template>
                </NButton>
              </div>
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <div class="drawer-actions">
          <NButton secondary @click="showConfigDrawer = false">取消</NButton>
          <NButton type="primary" :loading="configSaving" @click="saveConfig">保存规则</NButton>
        </div>
      </template>
    </SoloDrawer>

    <!-- 添加预期收支模态框 -->
    <NModal
      v-model:show="showAddEventModal"
      preset="card"
      title="添加预期收支（纳入现金流测算）"
      style="width: min(520px, 94vw);"
    >
      <div class="add-event-form">
        <div class="form-row">
          <label class="form-label">事项名称 *</label>
          <NInput v-model:value="newEventForm.name" placeholder="如：办公室季度租金 / 客户尾款" />
        </div>
        <div class="form-row-grid">
          <div class="form-row">
            <label class="form-label">资金流向 *</label>
            <NRadioGroup v-model:value="newEventForm.type" size="small">
              <NRadioButton value="outflow">流出 (支出)</NRadioButton>
              <NRadioButton value="inflow">流入 (回款)</NRadioButton>
            </NRadioGroup>
          </div>
          <div class="form-row">
            <label class="form-label">金额 (元) *</label>
            <NInputNumber v-model:value="newEventForm.amount" :min="1" :step="1000" placeholder="如 15000" />
          </div>
        </div>
        <div class="form-row-grid">
          <div class="form-row">
            <label class="form-label">周期规则</label>
            <NSelect
              v-model:value="newEventForm.repeat"
              :options="[
                { label: '单次支出/收款', value: 'none' },
                { label: '每月固定', value: 'monthly' },
                { label: '每季度固定', value: 'quarterly' },
                { label: '每年固定', value: 'yearly' },
              ]"
              size="small"
            />
          </div>
          <div v-if="newEventForm.repeat === 'none'" class="form-row">
            <label class="form-label">预计发生日期 *</label>
            <NDatePicker
              v-model:formatted-value="newEventForm.date"
              value-format="yyyy-MM-dd"
              type="date"
              size="small"
              placeholder="选择发生日期"
            />
          </div>
          <div v-else class="form-row">
            <label class="form-label">每月几号</label>
            <NInputNumber v-model:value="newEventForm.repeatDay" :min="1" :max="31" placeholder="如 10" size="small" />
          </div>
        </div>
        <div class="form-row">
          <label class="form-label">置信度</label>
          <NRadioGroup v-model:value="newEventForm.confidence" size="small">
            <NRadioButton value="high">高 (已定约定)</NRadioButton>
            <NRadioButton value="medium">中 (口头承诺)</NRadioButton>
            <NRadioButton value="low">低 (初步意向)</NRadioButton>
          </NRadioGroup>
          <span class="field-hint">悲观压力测试将自动剔除低/中置信度的回款流入</span>
        </div>
      </div>
      <template #footer>
        <div class="drawer-actions">
          <NButton secondary @click="showAddEventModal = false">取消</NButton>
          <NButton type="primary" @click="confirmAddEvent">确定添加</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.report-page {
  max-width: var(--page-width);
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
  min-width: 0;
}

.cockpit-item.main-profit-item {
  position: relative;
}

.label-with-tag {
  display: flex;
  align-items: center;
  gap: 6px;
}

.margin-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: var(--solo-radius-pill, 999px);
  line-height: 1.4;
  white-space: nowrap;
  flex-shrink: 0;
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
  white-space: nowrap;
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
  padding: 12px 16px;
  background: var(--solo-surface-soft, #F1F4F8);
  border-radius: var(--solo-radius-md, 8px);
  gap: 12px;
}

.position-item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
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
  flex: 1;
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
  margin-top: 14px;
}

.category-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}

.ai-inline-trigger {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.section-label {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-secondary, #586474);
}

/* 报表三大板块 */
.report-sections {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.report-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.boss-accounting-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border: 1px solid var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-soft, #F1F4F8);
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
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
  padding: 10px 14px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 12px;
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
  width: 36px;
  height: 36px;
  border-radius: var(--solo-radius-md, 8px);
  background: var(--solo-primary-soft, #EDF3FF);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
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
.balance-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--solo-radius-pill, 999px);
  font-weight: 600;
}

.balance-tag.balanced {
  background: var(--solo-income-soft, #EAF7F1);
  color: var(--solo-income, #0f7354);
}

.balance-tag.unbalanced {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #bd3d3d);
}

.report-empty {
  padding: 36px 20px;
  border: 1px dashed var(--solo-border, #DDE3EA);
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-surface, #FFFFFF);
  margin-bottom: 8px;
}

.empty-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── 决策罗盘看板卡条（轻量渐进式设计） ── */
.decision-compass-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

@media (max-width: 960px) {
  .decision-compass-strip {
    grid-template-columns: 1fr;
  }
}

.compass-card {
  background: var(--solo-surface, #ffffff);
  border: 1px solid var(--solo-border-color, #dde3ea);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  min-width: 0;
}

.compass-card:hover {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 6px 18px rgba(23, 71, 176, 0.08);
  transform: translateY(-1px);
}

.compass-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.compass-title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.compass-icon-wrap {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.compass-icon-wrap.primary {
  background: var(--solo-primary-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}

.compass-icon-wrap.indigo {
  background: var(--solo-capital-soft, #f1eefe);
  color: var(--solo-capital, #4c3fa8);
}

.compass-title-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.compass-card-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
  line-height: 1.3;
}

.compass-card-subtitle {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.compass-micro-tag {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--solo-surface-soft, #f1f4f8);
  color: var(--solo-text-secondary, #586474);
  font-weight: 500;
}

.compass-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.compass-stat-group {
  display: flex;
  align-items: baseline;
  gap: 28px;
}

.compass-stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
}

.stat-num {
  font-size: 22px;
  font-weight: 700;
  font-family: var(--font-numeric, var(--solo-font-numeric));
  color: var(--solo-text-primary, #182230);
  line-height: 1.2;
}

.compass-summary-line {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.4;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.compass-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 2px;
}

.footer-link-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-primary, #1747b0);
  flex-shrink: 0;
}

.compass-card:hover .footer-link-text {
  color: var(--solo-primary-hover, #2b66e2);
}

/* 抽屉内控制条与说明 */
.drawer-section-intro {
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.5;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
}

.drawer-controls-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--solo-surface-soft, #f1f4f8);
  border-radius: 8px;
}

.control-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.control-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  font-weight: 500;
  white-space: nowrap;
}

/* 预测卡片内容 */
.forecast-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.forecast-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

@media (max-width: 900px) {
  .forecast-metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.forecast-metric-item {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.metric-val {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.metric-sub {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.forecast-insights-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.insight-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--solo-radius-sm);
  font-size: 13px;
}

.insight-danger {
  background: var(--solo-expense-bg);
  border: 1px solid var(--solo-border);
  color: var(--solo-expense);
}

.insight-warning {
  background: var(--solo-warning-bg);
  border: 1px solid var(--solo-border);
  color: var(--solo-warning);
}

.insight-info {
  background: var(--solo-income-bg);
  border: 1px solid var(--solo-border);
  color: var(--solo-income);
}

.insight-body {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.insight-title {
  font-weight: 600;
}

.insight-desc {
  color: inherit;
}

.insight-action {
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}

.forecast-chart-wrap {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 14px 16px 8px;
}

.chart-caption {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--solo-text-secondary);
  font-weight: 500;
  margin-bottom: 8px;
}

.caption-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.forecast-schedules-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

@media (max-width: 768px) {
  .forecast-schedules-grid {
    grid-template-columns: 1fr;
  }
}

.schedule-column {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 12px 14px;
}

.schedule-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  margin-bottom: 10px;
}

.head-count {
  font-size: 11px;
  color: var(--solo-text-muted);
  font-weight: normal;
}

.schedule-empty {
  font-size: 12px;
  color: var(--solo-text-muted);
  text-align: center;
  padding: 16px 0;
}

.schedule-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.schedule-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
}

.row-partner {
  display: flex;
  align-items: center;
  gap: 6px;
}

.partner-name {
  max-width: 150px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-amount {
  font-weight: 600;
}

/* 归因与利现桥卡片样式 */
.attribution-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.attribution-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 860px) {
  .attribution-grid {
    grid-template-columns: 1fr;
  }
}

.bridge-panel,
.drivers-panel {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.bridge-desc {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.bridge-steps-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bridge-step-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 9px 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  font-size: 13px;
}

.bridge-base {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-border);
  font-weight: 600;
}

.bridge-total {
  background: var(--solo-bg-soft);
  border-color: var(--solo-border);
  border-top: 2px solid var(--solo-border);
  margin-top: 4px;
}

.step-label {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: var(--solo-text-primary);
  flex: 1;
  min-width: 0;
}

.step-label-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.step-name {
  line-height: 1.35;
}

.step-note {
  font-size: 11px;
  font-weight: 400;
  color: var(--solo-text-muted);
  line-height: 1.35;
}

.step-op {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-top: 1px;
  flex-shrink: 0;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
}

.step-op.plus {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}

.step-op.minus {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
}

.step-val {
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
}

.drivers-panel {
  gap: 14px;
}

.driver-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.driver-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}

.driver-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.driver-stat {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.driver-summary {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.driver-items-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.driver-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
}

.driver-name-tag {
  display: flex;
  align-items: center;
  gap: 6px;
}

.anomalies-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.anomaly-card {
  padding: 8px 10px;
  background: var(--solo-expense-bg);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
}

.anomaly-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  color: var(--solo-expense);
  margin-bottom: 2px;
}

.anomaly-desc {
  color: var(--solo-text-secondary);
  font-size: 11px;
}

.anomalies-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px;
  background: var(--solo-income-bg);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
  color: var(--solo-income);
}

.driver-empty-neutral {
  padding: 10px;
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* 抽屉样式 */
.config-drawer-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.config-intro {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.config-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-group-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.config-group-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.group-sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.config-fields-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.config-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-field.full-width {
  grid-column: span 2;
}

.config-field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}

.field-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 时间轴与沙盘推演收支日程 */
.forecast-timeline-box {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.schedule-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  transition: opacity 0.2s ease;
}

.timeline-row.is-disabled {
  opacity: 0.45;
  background: var(--solo-bg-soft);
}

.timeline-date-badge {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  padding: 4px 6px;
  background: var(--solo-bg-blue-soft);
  border-radius: var(--solo-radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--solo-primary);
  font-family: var(--font-numeric, var(--solo-font-numeric));
}

.timeline-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.timeline-title-bar {
  display: flex;
  align-items: center;
  gap: 6px;
}

.timeline-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.timeline-sub {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.timeline-amount-col {
  display: flex;
  align-items: center;
  gap: 12px;
}

.timeline-amount {
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-numeric, var(--solo-font-numeric));
}

.config-group-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.config-events-empty {
  font-size: 12px;
  color: var(--solo-text-muted);
  background: var(--solo-bg-soft);
  padding: 12px;
  border-radius: var(--solo-radius-sm);
  text-align: center;
}

.config-events-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.config-event-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
}

.event-item-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.event-item-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.event-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
}

.event-item-amount {
  font-size: 13px;
  font-weight: 600;
  font-family: var(--font-numeric, var(--solo-font-numeric));
}

.event-item-ops {
  display: flex;
  align-items: center;
  gap: 8px;
}

.add-event-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-row-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.form-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
</style>
