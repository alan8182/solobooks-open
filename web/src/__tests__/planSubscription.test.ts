/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
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

import PlanPage from '@/pages/mine/plan.vue'

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
    expect(text).toContain('云端免费试用版')
    expect(text).toContain('云端标准版')
    expect(text).toContain('598')
    expect(text).toContain('人机双签版')
    expect(text).toContain('2,980')
    expect(text).toContain('1 对 1 历史旧账导入与期初建账')
    expect(text).toContain('每年 500 万 AI Token 额度')
    expect(text).toContain('最多支持 3 个公司账套')
    expect(text).toContain('中级注册财务会计师每季度审账复核')
    expect(text).toContain('SB261113000001')
  })
})
