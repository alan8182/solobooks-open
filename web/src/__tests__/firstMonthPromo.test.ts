/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const routeState = { path: '/dashboard', query: {} as Record<string, unknown> }
const pushSpy = vi.fn()
const replaceSpy = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: pushSpy, replace: replaceSpy }),
}))

const promoCatalog = {
  enabled: true,
  eligible: true,
  purchased: false,
  code: 'first_month_promo',
  name: '新人首月 AI 体验包',
  priceYuan: '1.00',
  originalPriceYuan: '80.00',
  tokens: 800_000,
  validDays: 30,
}

const catalog = {
  alipayEnabled: true,
  wechatEnabled: true,
  items: [
    { planCode: 'standard', planName: '云端标准版', period: 'annual' as const, priceAnnual: '598.00' },
  ],
  tokenTopup: { enabled: true, tokensPerYuan: 10_000, minYuan: 1, maxYuan: 10_000 },
  firstMonthPromo: { ...promoCatalog },
}

const quotaState = {
  planCode: 'trial',
  planName: '免费体验版',
  periodType: 'monthly' as const,
  periodStart: '2026-11-01',
  periodEnd: '2026-12-01',
  limitTokens: 200_000,
  bonusTokens: 0,
  usedTokens: 170_000,
  remainingTokens: 30_000,
  tokenBalance: 0,
  promoTokenBalance: 0,
  promoExpiresAt: null as string | null,
  promoEligible: true,
  promoLowQuota: true,
  maxCompanies: 3,
  companyCount: 1,
}

const usageStore = {
  summary: { quota: { ...quotaState }, currentMonth: { totalTokens: 170_000 } },
  loading: false,
  ensureLoaded: vi.fn().mockResolvedValue(undefined),
}

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,FAKEQR') },
}))
vi.mock('@/stores/usage', () => ({ useUsageStore: () => usageStore }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
}))
vi.mock('@/api/billing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/billing')>()),
  fetchPricing: vi.fn(async () => catalog),
  fetchBillingOrders: vi.fn(async () => ({ items: [] })),
  fetchBillingOrder: vi.fn(),
  createBillingOrder: vi.fn(),
  createTokenTopupOrder: vi.fn(),
  createFirstMonthPromoOrder: vi.fn(),
}))

import { createFirstMonthPromoOrder, fetchBillingOrder } from '@/api/billing'
import PlanPage from '@/pages/mine/plan.vue'
import QuotaLowBanner from '@/pages/layout/QuotaLowBanner.vue'

let mountedWrapper: ReturnType<typeof mount> | null = null

beforeEach(() => {
  sessionStorage.clear()
  pushSpy.mockReset()
  replaceSpy.mockReset()
  routeState.path = '/mine/plan'
  routeState.query = {}
  catalog.firstMonthPromo = { ...promoCatalog }
  usageStore.summary = { quota: { ...quotaState }, currentMonth: { totalTokens: 170_000 } }
  vi.mocked(createFirstMonthPromoOrder).mockReset()
})

afterEach(() => {
  mountedWrapper?.unmount()
  mountedWrapper = null
  document.body.innerHTML = ''
})

describe('新人首月 AI 体验包前端流转', () => {
  it('有资格时渲染特惠卡片，点抢购默认直接拉起微信扫码并在底部展示 30 天条款', async () => {
    setActivePinia(createPinia())
    vi.mocked(fetchBillingOrder).mockResolvedValue({
      orderNo: 'SB261124PROMO1',
      planCode: 'first_month_promo',
      orderType: 'first_month_promo',
      subject: '新人首月 AI 体验包',
      amount: '1.00',
      status: 'created',
      channel: 'wechat',
    })
    vi.mocked(createFirstMonthPromoOrder).mockResolvedValueOnce({
      orderNo: 'SB261124PROMO1',
      planCode: 'first_month_promo',
      orderType: 'first_month_promo',
      tokenAmount: 800_000,
      planName: '新人首月 AI 体验包',
      amount: '1.00',
      channel: 'wechat',
      codeUrl: 'weixin://wxpay/bizpayurl?pr=promo',
      expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })

    const wrapper = mount(PlanPage, { attachTo: document.body })
    mountedWrapper = wrapper
    await flushPromises()

    expect(wrapper.find('.promo-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('新人首月 AI 体验包')
    expect(wrapper.text()).toContain('原价 ¥80')

    await wrapper.find('.promo-submit').trigger('click')
    await flushPromises()

    expect(createFirstMonthPromoOrder).toHaveBeenCalledWith('wechat')
    expect(document.body.textContent).toContain('新人体验包服务条款')
    expect(document.body.textContent).toContain('有效期 30 天')
    expect(document.querySelector('img.wxpay-qr')).not.toBeNull()
  })

  it('无资格时不展示新人首月体验包卡片', async () => {
    setActivePinia(createPinia())
    catalog.firstMonthPromo.eligible = false

    const wrapper = mount(PlanPage, { attachTo: document.body })
    mountedWrapper = wrapper
    await flushPromises()

    expect(wrapper.find('.promo-card').exists()).toBe(false)
  })

  it('QuotaLowBanner 在非订阅页且额度 ≥80% 时展示，在订阅页隐藏，关闭后本会话不再弹出', async () => {
    routeState.path = '/dashboard'
    const wrapper = mount(QuotaLowBanner)
    mountedWrapper = wrapper
    expect(wrapper.find('.quota-low-banner').exists()).toBe(true)
    expect(wrapper.text()).toContain('¥1 解锁 80 万')
    expect(wrapper.text()).toContain('升级标准版 ¥598/年')

    await wrapper.find('.banner-close').trigger('click')
    expect(wrapper.find('.quota-low-banner').exists()).toBe(false)
    expect(sessionStorage.getItem('solo:promo-banner-dismissed')).toBe('1')
    wrapper.unmount()

    sessionStorage.clear()
    routeState.path = '/mine/plan'
    const onPlan = mount(QuotaLowBanner)
    mountedWrapper = onPlan
    expect(onPlan.find('.quota-low-banner').exists()).toBe(false)
  })
})
