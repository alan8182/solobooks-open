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
    monthIncome: 88000,
    monthExpense: 32000,
    netProfit: 56000,
    availableCash: 150000,
    pendingReceivables: 24000,
    pendingPayables: 6000,
    taxSummary: {
      estimatedVat: 0,
      estimatedCit: 2800,
    },
    categoryBreakdown: {
      '办公租赁': 12000,
      '外包技术': 20000,
    },
  })),
  getReportDiagnosis: vi.fn(async () => ({ summary: '经营状况稳健，现金流充裕' })),
  getBalanceSheet: vi.fn(async () => ({
    totalAssets: 174000,
    totalLiabilities: 11000,
    totalEquity: 163000,
    assets: [
      { accountCode: '1002', accountName: '银行存款', currentAmount: 150000 },
      { accountCode: '1122', accountName: '应收账款', currentAmount: 24000 },
    ],
    liabilities: [
      { accountCode: '2202', accountName: '应付账款', currentAmount: 6000 },
      { accountCode: '2241', accountName: '其他应付款-股东垫资', currentAmount: 5000 },
    ],
  })),
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

beforeEach(() => {
  const pinia = createPinia()
  setActivePinia(pinia)
  harness.store = reactive({
    activeEntries: [
      { id: '1', title: '服务器续费', amount: 5000, payer: 'boss_personal' },
    ],
    fetchEntries: vi.fn(async () => {}),
  })
})

afterEach(() => {
  vi.clearAllMocks()
  harness.route.value.query = {}
})

describe('ReportPage Dual-View UX (Boss vs Accounting)', () => {
  it('renders boss-friendly language, 4 cash position metrics and management cards in Boss view', async () => {
    const viewStore = useWorkspaceViewStore()
    viewStore.setMode('boss')

    const wrapper = mount(ReportPage, {
      global: {
        stubs: {
          VChart: true,
        },
      },
    })
    await flushPromises()

    // 标题与副标题脱敏
    expect(wrapper.text()).toContain('经营概览')
    expect(wrapper.text()).toContain('企业实时收支、手头现钱与往来账款')

    // 4 宫格核心人话标题
    expect(wrapper.text()).toContain('收入')
    expect(wrapper.text()).toContain('支出')
    expect(wrapper.text()).toContain('净赚 (落袋利润)')
    expect(wrapper.text()).toContain('预估本月应缴税')

    // 真金白银头寸速览条：包含 4 项，去除会计专业括号
    expect(wrapper.text()).toContain('账上现钱')
    expect(wrapper.text()).toContain('客户待回款')
    expect(wrapper.text()).toContain('待付支出')
    expect(wrapper.text()).toContain('老板个人垫资')
    expect(wrapper.text()).toContain('可支撑约 4.7 个月开销')
    expect(wrapper.text()).not.toContain('货币资金')
    expect(wrapper.text()).not.toContain('应收账款')
    expect(wrapper.text()).not.toContain('应付账款')

    // 支出结构人话化
    expect(wrapper.text()).toContain('钱花哪儿了（支出分类）')

    // 老板经营核心卡片
    expect(wrapper.text()).toContain('赚亏明细')
    expect(wrapper.text()).toContain('催款与应付清单')
    expect(wrapper.text()).toContain('项目利润')
    expect(wrapper.text()).toContain('企业健康体检')
    expect(wrapper.text()).toContain('单据合规检查')
    expect(wrapper.text()).not.toContain('增值税额度与预估')
    expect(wrapper.text()).not.toContain('企业所得税优惠')

    // 老板视图底部应提示切换会计视图
    expect(wrapper.text()).toContain('需要查看法定报表、账簿或做账底稿？')
    expect(wrapper.find('.legal-books').exists()).toBe(false)
  })

  it('renders standard accounting terminology and legal books in Accounting view', async () => {
    const viewStore = useWorkspaceViewStore()
    viewStore.setMode('accounting')

    const wrapper = mount(ReportPage, {
      global: {
        stubs: {
          VChart: true,
        },
      },
    })
    await flushPromises()

    // 传统会计标题
    expect(wrapper.text()).toContain('报表中心')
    expect(wrapper.text()).toContain('随时掌握企业真实收支、账上资金与税费状况')

    // 4 宫格会计术语
    expect(wrapper.text()).toContain('营业收入')
    expect(wrapper.text()).toContain('总支出')
    expect(wrapper.text()).toContain('净赚 (利润)')
    expect(wrapper.text()).toContain('预计本月税负')

    // 头寸速览条保留准则科目全称，不渲染老板垫资
    expect(wrapper.text()).toContain('账上可用资金（货币资金）')
    expect(wrapper.text()).toContain('待收回客户欠款（应收账款）')
    expect(wrapper.text()).toContain('对外待付款项（应付账款）')
    expect(wrapper.text()).not.toContain('老板个人垫资')

    // 会计专业报表卡片
    expect(wrapper.text()).toContain('利润表')
    expect(wrapper.text()).toContain('往来账龄')
    // 项目利润收进经营报表：会计视图同样有卡片入口（原顶级导航位已移除）
    expect(wrapper.text()).toContain('项目利润')
    expect(wrapper.text()).toContain('健康评分')
    expect(wrapper.text()).toContain('同比/环比')

    // 单据合规与法定报表
    expect(wrapper.text()).toContain('单据合规检查')
    expect(wrapper.text()).not.toContain('增值税辅助')
    expect(wrapper.text()).not.toContain('企业所得税')
    expect(wrapper.find('.legal-books').exists()).toBe(true)
    expect(wrapper.text()).toContain('法定财务报表')
    expect(wrapper.text()).toContain('资产负债表已配平')
    expect(wrapper.text()).toContain('税局标准月季报与底册导出')
    // 账簿已移出报表中心：五本账统一走侧栏「会计账簿」工作台，不在这里重复发入口
    expect(wrapper.find('.ledger-book-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('多栏账')
  })
})
