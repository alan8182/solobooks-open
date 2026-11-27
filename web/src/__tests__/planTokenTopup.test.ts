/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks license v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const catalog = {
  alipayEnabled: true,
  wechatEnabled: true,
  items: [
    { planCode: 'standard', planName: '云端标准版', period: 'annual' as const, priceAnnual: '598.00' },
  ],
  tokenTopup: { enabled: true, tokensPerYuan: 10_000, minYuan: 1, maxYuan: 10_000 },
}

const quotaState = {
  planCode: 'trial',
  planName: '免费体验版',
  periodType: 'monthly' as const,
  periodStart: '2026-11-01',
  periodEnd: '2026-12-01',
  limitTokens: 100_000,
  bonusTokens: 0,
  usedTokens: 20_000,
  remainingTokens: 130_000,
  tokenBalance: 50_000,
  maxCompanies: 3,
  companyCount: 1,
}

const usageStore = {
  summary: { quota: quotaState, currentMonth: { totalTokens: 20_000 } },
  loading: false,
  ensureLoaded: vi.fn().mockResolvedValue(undefined),
}

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,FAKEQR') },
}))
vi.mock('@/stores/usage', () => ({ useUsageStore: () => usageStore }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))
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
}))

import { createTokenTopupOrder, fetchBillingOrder } from '@/api/billing'
import PlanPage from '@/pages/mine/plan.vue'

let mountedWrapper: ReturnType<typeof mount> | null = null

async function mountPlan() {
  setActivePinia(createPinia())
  // 支付弹窗由 naive-ui Teleport 到 body，必须挂到 document.body 才断言得到
  const wrapper = mount(PlanPage, { attachTo: document.body })
  await flushPromises()
  mountedWrapper = wrapper
  return wrapper
}

function bodyText() {
  return document.body.textContent ?? ''
}

beforeEach(() => {
  usageStore.summary = { quota: quotaState, currentMonth: { totalTokens: 20_000 } }
  catalog.alipayEnabled = true
  catalog.wechatEnabled = true
  catalog.tokenTopup.enabled = true
  vi.mocked(createTokenTopupOrder).mockReset()
  vi.mocked(fetchBillingOrder).mockResolvedValue({
    orderNo: 'SB_TOPUP_WX_1',
    planCode: 'token_topup',
    orderType: 'token_topup',
    subject: 'AI 点数充值 100000 点',
    amount: '10.00',
    status: 'created',
    channel: 'wechat',
  })
})

afterEach(() => {
  mountedWrapper?.unmount()
  mountedWrapper = null
  document.body.innerHTML = ''
})

describe('订阅页 · AI 点数充值', () => {
  it('按目录渲染充值卡片、单价与余额（单价/换算只来自后端）', async () => {
    const wrapper = await mountPlan()
    const text = wrapper.text()
    expect(text).toContain('AI 点数充值')
    expect(text).toContain('1 元 = 10,000 点')
    expect(text).toContain('当前点数余额')
    expect(text).toContain('5.0w')
    // 套餐剩余按「套餐额度」展示，不把充值余额混进本周期用量
    expect(text).toContain('本期剩余 8.0w tokens')
  })

  it('快捷金额与自定义金额联动到账点数预览', async () => {
    const wrapper = await mountPlan()
    const quickButtons = wrapper.findAll('.topup-quick-btn')
    expect(quickButtons.map((b) => b.text())).toEqual(['10 元', '30 元', '100 元', '300 元'])

    await quickButtons[2].trigger('click')
    expect(wrapper.find('.topup-preview').text()).toContain('100.0w')
  })

  it('目录关闭充值（自部署/自带 Key）时不渲染入口', async () => {
    catalog.tokenTopup.enabled = false
    const wrapper = await mountPlan()
    expect(wrapper.find('.topup-card').exists()).toBe(false)
  })

  it('点「立即充值」默认优先微信直出二维码，底部展示充值条款并可切换支付宝', async () => {
    vi.mocked(createTokenTopupOrder)
      .mockResolvedValueOnce({
        orderNo: 'SB_TOPUP_WX_1',
        planCode: 'token_topup',
        orderType: 'token_topup',
        tokenAmount: 100_000,
        planName: 'AI 点数充值 100000 点',
        amount: '10.00',
        channel: 'wechat',
        codeUrl: 'weixin://wxpay/bizpayurl?pr=TOPUP1',
        expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
      })
      .mockResolvedValueOnce({
        orderNo: 'SB_TOPUP_ALI_1',
        planCode: 'token_topup',
        orderType: 'token_topup',
        tokenAmount: 100_000,
        planName: 'AI 点数充值 100000 点',
        amount: '10.00',
        channel: 'alipay',
        payUrl: 'https://openapi.alipay.com/gateway.do?topup=1',
        expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
      })

    const wrapper = await mountPlan()
    await wrapper.find('.topup-submit').trigger('click')
    await flushPromises()

    expect(createTokenTopupOrder).toHaveBeenCalledWith(10, 'wechat')
    const text = bodyText()
    expect(text).toContain('微信扫码支付')
    expect(text).toContain('充值服务条款')
    expect(text).toContain('AI 点数为虚拟商品')
    expect(text).toContain('仅未消耗的点数部分可联系客服原路退回')
    expect(document.querySelector('img.wxpay-qr')).not.toBeNull()

    const switchBtn = document.querySelector('.wxpay-switch-alipay') as HTMLElement | null
    expect(switchBtn).not.toBeNull()
    switchBtn!.click()
    await flushPromises()

    expect(createTokenTopupOrder).toHaveBeenNthCalledWith(2, 10, 'alipay')
  })

  it('仅开通支付宝时点「立即充值」直接走支付宝下单', async () => {
    catalog.wechatEnabled = false
    vi.mocked(createTokenTopupOrder).mockResolvedValueOnce({
      orderNo: 'SB_TOPUP_ALI_ONLY',
      planCode: 'token_topup',
      orderType: 'token_topup',
      tokenAmount: 100_000,
      planName: 'AI 点数充值 100000 点',
      amount: '10.00',
      channel: 'alipay',
      payUrl: 'https://openapi.alipay.com/gateway.do?topup=only',
      expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })

    const wrapper = await mountPlan()
    await wrapper.find('.topup-submit').trigger('click')
    await flushPromises()

    expect(createTokenTopupOrder).toHaveBeenCalledWith(10, 'alipay')
    expect(document.querySelector('img.wxpay-qr')).toBeNull()
  })
})
