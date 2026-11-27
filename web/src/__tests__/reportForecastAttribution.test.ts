/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ReportPage from '@/pages/report/index.vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

const harness = vi.hoisted(() => ({
  store: null as any,
  route: { value: { query: {} as Record<string, string> } } as any,
  push: vi.fn(),
  replace: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  getCashForecast: vi.fn(),
  getCashForecastConfig: vi.fn(),
  updateCashForecastConfig: vi.fn(),
  getBusinessAttribution: vi.fn(),
}))

vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.store }))
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: harness.push,
    replace: harness.replace,
    currentRoute: harness.route,
  }),
}))

vi.mock('vue-echarts', () => ({
  default: {
    name: 'VChart',
    template: '<div class="v-chart-mock"></div>',
  },
}))

vi.mock('echarts/core', () => ({
  use: vi.fn(),
}))

vi.mock('@/api/report', () => ({
  getMonthlyReport: vi.fn(async () => ({
    period: '2026-10',
    monthIncome: 120000,
    monthExpense: 40000,
    netProfit: 80000,
    availableCash: 250000,
    pendingReceivables: 60000,
    pendingPayables: 20000,
    taxSummary: { estimatedVat: 0, estimatedCit: 2000 },
    categoryBreakdown: { '办公租赁': 15000, '云服务费': 25000 },
  })),
  getReportDiagnosis: vi.fn(async () => ({ summary: '经营状况稳健' })),
  getBalanceSheet: vi.fn(async () => ({
    totalAssets: 310000,
    totalLiabilities: 20000,
    totalEquity: 290000,
    assets: [
      { accountCode: '1002', accountName: '银行存款', currentAmount: 250000 },
      { accountCode: '1122', accountName: '应收账款', currentAmount: 60000 },
    ],
    liabilities: [
      { accountCode: '2202', accountName: '应付账款', currentAmount: 20000 },
    ],
  })),
  getCashForecast: harness.getCashForecast,
  getCashForecastConfig: harness.getCashForecastConfig,
  updateCashForecastConfig: harness.updateCashForecastConfig,
  getBusinessAttribution: harness.getBusinessAttribution,
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({
    warning: harness.warning,
    error: harness.error,
    success: harness.success,
    info: vi.fn(),
  }),
}))

describe('ReportPage Cash Forecast & Variance Attribution UI', () => {
  beforeEach(() => {
    const pinia = createPinia()
    setActivePinia(pinia)
    harness.store = reactive({
      activeEntries: [],
      fetchEntries: vi.fn(async () => {}),
    })

    harness.getCashForecast.mockResolvedValue({
      asOf: '2026-10-31',
      horizonDays: 60,
      scenario: 'neutral',
      currentCash: 250000,
      endingCash: 275000,
      minBalance: 210000,
      minBalanceDate: '2026-11-20',
      safetyThreshold: 100000,
      runwayMonths: 6.5,
      isDeficitProjected: false,
      deficitDate: null,
      dailyPoints: [
        { date: '2026-10-31', balance: 250000, net: 0, inflow: 0, outflow: 0 },
        { date: '2026-11-01', balance: 245000, net: -5000, inflow: 10000, outflow: 15000 },
      ],
      overdueReceivables: [
        { partnerId: 'p1', partnerName: '腾讯云计算', amount: 35000, daysOverdue: 12, oldestDate: '2026-10-01' },
      ],
      monthlyBurnRate: 30000,
      scenarioSummary: '预计未来60天资金链稳健，最低资金 21.00 万元',
    })

    harness.getCashForecastConfig.mockResolvedValue({
      defaultArDays: 30,
      defaultApDays: 30,
      partnerCreditDays: {},
      safetyCashThreshold: 100000,
    })

    harness.updateCashForecastConfig.mockResolvedValue({ success: true })

    harness.getBusinessAttribution.mockResolvedValue({
      baseMonth: '2026-10',
      compareMonth: '2026-09',
      baseProfit: 80000,
      compareProfit: 50000,
      profitDelta: 30000,
      topIncomeDrivers: [
        { key: 'c1', name: '阿里巴巴大客户', baseAmount: 70000, compareAmount: 40000, deltaAmount: 30000, changePercent: 75.0, tag: '增量' },
        { key: 'c2', name: '新签约字节客户', baseAmount: 20000, compareAmount: 0, deltaAmount: 20000, changePercent: null, tag: '新客户' },
      ],
      topExpenseDrivers: [
        { key: 'e1', name: '研发外包费', baseAmount: 15000, compareAmount: 5000, deltaAmount: 10000, changePercent: 200.0, tag: '激增' },
      ],
      anomalies: [
        { category: '研发费用', title: '外包支出激增', amount: 15000, historyAvgAmount: 5000, growthRate: 2.0, reason: '较前3个月均值激增 200%' },
      ],
      netProfit: 80000,
      netCashFlow: 45000,
      profitToCashDiscrepancy: -35000,
      bridgeSteps: [
        { name: '账面净利润', amount: 80000, type: 'profit', note: '权责发生制' },
        { name: '非付现折旧与摊销', amount: 5000, type: 'add', note: '无需动用现金' },
        { name: '客户欠款变动 (应收占用)', amount: -30000, type: 'sub', note: '未回款占用现钱' },
        { name: '供应商货款变动 (应付暂缓)', amount: 10000, type: 'add', note: '尚未对外付现' },
        { name: '资本性支出 (CapEx)', amount: -20000, type: 'sub', note: '设备购置' },
        { name: '实际货币资金净变动', amount: 45000, type: 'cash', note: '真金白银进出' },
      ],
      plainSummary: '本期净利润 8.00 万元，但受客户应收暂未回款及设备采购影响，实际现金净流入为 4.50 万元。',
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    harness.route.value.query = {}
  })

  it('renders Cash Forecast card with correct metrics and scenario summary without "AI CFO"', async () => {
    const viewStore = useWorkspaceViewStore()
    viewStore.setMode('boss')

    const wrapper = mount(ReportPage, {
      global: {
        stubs: {
          VChart: true,
          SoloDrawer: {
            props: ['show', 'title'],
            template: '<div v-if="show" class="stub-solo-drawer"><div class="drawer-title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
        },
      },
    })
    await flushPromises()

    const text = wrapper.text()

    // 验证“AI 经营诊断”保留且不存在旧“AI CFO”
    expect(text).toContain('AI 经营诊断')
    expect(text).not.toContain('AI CFO')

    // 验证轻量罗盘卡片指标与无双重货币符号 ¥¥
    expect(text).toContain('现金流预测')
    expect(text).toContain('可支撑月数')
    expect(text).toContain('6.5 个月')
    expect(text).toContain('健康：资金充裕')
    expect(text).not.toContain('¥¥')

    // 点击进入资金推演抽屉
    const forecastCard = wrapper.findAll('.compass-card').find(c => c.text().includes('现金流预测'))
    expect(forecastCard).toBeDefined()
    await forecastCard!.trigger('click')
    await flushPromises()

    const drawerText = wrapper.text()
    expect(drawerText).not.toContain('¥¥')
    expect(drawerText).toContain('当前可用资金')
    expect(drawerText).toContain('250,000.00')
    expect(drawerText).toContain('预测最低余额')
    expect(drawerText).toContain('210,000.00')

    // 验证逾期催收客户
    expect(drawerText).toContain('待催收客户账款')
    expect(drawerText).toContain('腾讯云计算')
    expect(drawerText).toContain('已逾期 12 天')
    expect(drawerText).toContain('35,000.00')
  })

  it('renders Variance Attribution card and Profit-to-Cash Bridge steps', async () => {
    const viewStore = useWorkspaceViewStore()
    viewStore.setMode('boss')

    const wrapper = mount(ReportPage, {
      global: {
        stubs: {
          VChart: true,
          SoloDrawer: {
            props: ['show', 'title'],
            template: '<div v-if="show" class="stub-solo-drawer"><div class="drawer-title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
        },
      },
    })
    await flushPromises()

    const text = wrapper.text()

    // 验证罗盘看板卡片与无双重货币符号
    expect(text).toContain('利润与现金对齐')
    expect(text).toContain('部分利润未变现')
    expect(text).not.toContain('¥¥')

    // 点击进入利现归因抽屉
    const attributionCard = wrapper.findAll('.compass-card').find(c => c.text().includes('利润与现金对齐'))
    expect(attributionCard).toBeDefined()
    await attributionCard!.trigger('click')
    await flushPromises()

    const drawerText = wrapper.text()
    expect(drawerText).not.toContain('¥¥')

    // 验证利现桥与归因抽屉内容
    expect(drawerText).toContain('利润与现金调节桥')
    expect(drawerText).toContain('账面净利润')
    expect(drawerText).toContain('非付现折旧与摊销')
    expect(drawerText).toContain('客户欠款变动 (应收占用)')
    expect(drawerText).toContain('供应商货款变动 (应付暂缓)')
    expect(drawerText).toContain('资本性支出 (CapEx)')
    expect(drawerText).toContain('实际货币资金净变动')

    // 验证收入驱动因子与标签
    expect(drawerText).toContain('主要收入驱动分析')
    expect(drawerText).toContain('阿里巴巴大客户')
    expect(drawerText).toContain('增量')
    expect(drawerText).toContain('新签约字节客户')
    expect(drawerText).toContain('新客户')

    // 验证费用异常激增检测
    expect(drawerText).toContain('支出异常激增检测')
    expect(drawerText).toContain('外包支出激增')
    expect(drawerText).toContain('较前3个月均值激增 200%')
  })

  it('opens forecast config drawer and saves updated rules', async () => {
    const viewStore = useWorkspaceViewStore()
    viewStore.setMode('boss')

    const wrapper = mount(ReportPage, {
      global: {
        stubs: {
          VChart: true,
          SoloDrawer: {
            props: ['show', 'title'],
            template: '<div v-if="show" class="stub-solo-drawer"><div class="drawer-title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
        },
      },
    })
    await flushPromises()

    // 点击进入资金推演抽屉
    const forecastCard = wrapper.findAll('.compass-card').find(c => c.text().includes('现金流预测'))
    expect(forecastCard).toBeDefined()
    await forecastCard!.trigger('click')
    await flushPromises()

    // 点击“预测规则”按钮
    const configBtn = wrapper.findAll('button').find(b => b.text().includes('预测规则'))
    expect(configBtn).toBeDefined()
    await configBtn!.trigger('click')
    await flushPromises()

    expect(harness.getCashForecastConfig).toHaveBeenCalled()
    expect(wrapper.text()).toContain('现金流预测规则配置')
    expect(wrapper.text()).toContain('默认客户收款账期 (天)')
    expect(wrapper.text()).toContain('安全现金储备警戒线 (元)')

    // 点击“保存规则”
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('保存规则'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(harness.updateCashForecastConfig).toHaveBeenCalled()
    expect(harness.success).toHaveBeenCalledWith('现金流预测规则已保存')
  })
})

