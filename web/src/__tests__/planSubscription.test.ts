/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import router from '@/router'

const routeState = { query: {} as Record<string, unknown> }
const replaceSpy = vi.fn()
const pushSpy = vi.fn()

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRoute: () => routeState,
    useRouter: () => ({ push: pushSpy, replace: replaceSpy }),
  }
})

const mockPricing = vi.fn()
const mockOrders = vi.fn()

vi.mock('@/api/billing', () => ({
  fetchPricing: (...args: unknown[]) => mockPricing(...args),
  fetchBillingOrders: (...args: unknown[]) => mockOrders(...args),
  fetchBillingOrder: vi.fn(),
  createBillingOrder: vi.fn(),
  createTokenTopupOrder: vi.fn(),
  createFirstMonthPromoOrder: vi.fn(),
}))

vi.mock('@/stores/usage', () => ({
  useUsageStore: () => ({
    summary: ref({
      quota: {
        planCode: 'trial',
        planName: '免费体验版',
        periodType: 'monthly',
        periodStart: '2026-11-01',
        periodEnd: '2026-11-30',
        limitTokens: 100000,
        bonusTokens: 0,
        usedTokens: 25000,
        remainingTokens: 75000,
      },
      currentMonth: { totalTokens: 25000 },
    }),
    loading: false,
    ensureLoaded: vi.fn(),
  }),
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
  }
})

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,FAKEQR') },
}))

import PlanPage from '@/pages/mine/plan.vue'

// 条款/渠道弹窗都 teleport 到 body：用例结束后必须清场，
// 否则上一条用例残留的 .channel-row 会污染下一条用例的 document 级查询
afterEach(() => {
  document.body.innerHTML = ''
})

describe('standalone plan subscription page (/mine/plan)', () => {
  it('resolves /mine/plan as an authenticated route shared across boss and accounting views', () => {
    const resolved = router.resolve('/mine/plan')
    expect(resolved.matched.length).toBeGreaterThan(0)
    expect(resolved.name).toBe('PlanSubscription')
    expect(resolved.meta.workspace).toBeUndefined()
  })

  it('renders backend pricing catalog, delivery features, and historical orders', async () => {
    mockPricing.mockResolvedValue({
      alipayEnabled: true,
      items: [
        { planCode: 'standard', planName: '云端标准版', period: 'annual', priceAnnual: '598.00' },
        { planCode: 'pro', planName: '人机双签版', period: 'annual', priceAnnual: '2980.00' },
      ],
    })
    mockOrders.mockResolvedValue({
      items: [
        {
          orderNo: 'SB261113000001',
          planCode: 'standard',
          subject: '梭罗记账 云端标准版（年付）',
          amount: '598.00',
          status: 'activated',
          createdAt: '2026-11-13T10:00:00',
          activatedAt: '2026-11-13T10:01:00',
        },
      ],
    })

    const wrapper = mount(PlanPage)
    await flushPromises()

    const text = wrapper.text()
    // 免费档名称与后端 planName 统一为「免费体验版」，不再出现第二个叫法
    expect(text).toContain('免费体验版')
    expect(text).toContain('云端标准版')
    expect(text).toContain('598')
    expect(text).toContain('人机双签版')
    expect(text).toContain('2,980')
    expect(text).toContain('1 对 1 历史旧账导入与期初建账')
    expect(text).toContain('每年 1000 万 AI Token 额度')
    expect(text).toContain('最多支持 3 个公司账套')
    expect(text).toContain('专业会计师每季度审账复核（内部管理把关，非事务所法定审计报告）')
    expect(text).toContain('服务协议（含付费订阅与退款规则）')

    // 历史订单已移入「我的订单」右侧抽屉：主页只保留入口，打开抽屉后才见订单列表
    const ordersBtn = wrapper.findAll('button').find((b) => b.text().includes('我的订单'))
    expect(ordersBtn).toBeDefined()
    await ordersBtn!.trigger('click')
    await flushPromises()
    // NDrawer teleport 到 body，从 document 级查询抽屉内容
    const drawerContent = document.querySelector('.n-drawer-content')
    expect(drawerContent).not.toBeNull()
    expect(drawerContent!.textContent).toContain('SB261113000001')
    expect(drawerContent!.textContent).toContain('梭罗记账 云端标准版（年付）')
  })

  it('立即开通默认优先微信扫码并在底部展示条款，支持弹窗内直接切换支付宝', async () => {
    const { createBillingOrder, fetchBillingOrder } = await import('@/api/billing')
    const mockCreate = vi.mocked(createBillingOrder)
    mockCreate.mockReset()
    mockPricing.mockResolvedValue({
      alipayEnabled: true,
      wechatEnabled: true,
      items: [
        { planCode: 'standard', planName: '云端标准版', period: 'annual', priceAnnual: '598.00' },
        { planCode: 'pro', planName: '人机双签版', period: 'annual', priceAnnual: '2980.00' },
      ],
    })
    mockOrders.mockResolvedValue({ items: [] })
    vi.mocked(fetchBillingOrder).mockResolvedValue({
      orderNo: 'SB261119WECHAT1',
      planCode: 'standard',
      subject: '云端标准版（年付）',
      amount: '598.00',
      status: 'created',
      channel: 'wechat',
    })
    mockCreate
      .mockResolvedValueOnce({
        orderNo: 'SB261119WECHAT1',
        planCode: 'standard',
        planName: '云端标准版',
        amount: '598.00',
        channel: 'wechat',
        codeUrl: 'weixin://wxpay/bizpayurl?pr=TEST01',
        expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
      })
      .mockResolvedValueOnce({
        orderNo: 'SB261118000002',
        planCode: 'standard',
        planName: '云端标准版',
        amount: '598.00',
        channel: 'alipay',
        payUrl: 'https://openapi.alipay.com/gateway.do?test=1',
        expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
      })

    const wrapper = mount(PlanPage, { attachTo: document.body })
    await flushPromises()

    const buyBtn = wrapper.findAll('button').find((b) => b.text().includes('立即开通 云端标准版'))
    expect(buyBtn).toBeDefined()
    await buyBtn!.trigger('click')
    await flushPromises()

    expect(mockCreate).toHaveBeenCalledWith('standard', 'wechat')
    expect(document.body.textContent).toContain('套餐服务条款')
    expect(document.body.textContent).toContain('7 日内未启动人工服务可全额退费')
    expect(document.querySelector('img.wxpay-qr')).not.toBeNull()

    const switchBtn = document.querySelector('.wxpay-switch-alipay') as HTMLElement | null
    expect(switchBtn).not.toBeNull()
    switchBtn!.click()
    await flushPromises()
    expect(mockCreate).toHaveBeenNthCalledWith(2, 'standard', 'alipay')
    wrapper.unmount()
  })

  it('仅开通支付宝（wechatEnabled=false）时点击立即开通直接走支付宝', async () => {
    const { createBillingOrder } = await import('@/api/billing')
    const mockCreate = vi.mocked(createBillingOrder)
    mockCreate.mockReset()
    mockPricing.mockResolvedValue({
      alipayEnabled: true,
      wechatEnabled: false,
      items: [
        { planCode: 'standard', planName: '云端标准版', period: 'annual', priceAnnual: '598.00' },
        { planCode: 'pro', planName: '人机双签版', period: 'annual', priceAnnual: '2980.00' },
      ],
    })
    mockOrders.mockResolvedValue({ items: [] })
    mockCreate.mockResolvedValueOnce({
      orderNo: 'SB261118000003',
      planCode: 'standard',
      planName: '云端标准版',
      amount: '598.00',
      channel: 'alipay',
      payUrl: 'https://openapi.alipay.com/gateway.do?test=2',
      expiresAt: '2026-11-18T18:30:00',
    })

    const wrapper = mount(PlanPage, { attachTo: document.body })
    await flushPromises()

    const buyBtn = wrapper.findAll('button').find((b) => b.text().includes('立即开通 云端标准版'))
    await buyBtn!.trigger('click')
    await flushPromises()

    expect(mockCreate).toHaveBeenCalledWith('standard', 'alipay')
    expect(document.querySelector('img.wxpay-qr')).toBeNull()
    wrapper.unmount()
  })
})
