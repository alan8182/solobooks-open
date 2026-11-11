/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import BankBalanceWidget from '@/components/widgets/BankBalanceWidget.vue'
import apiClient from '@/api/client'

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn(),
  },
}))

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div>home</div>' } },
      { path: '/bank', component: { template: '<div>bank</div>' } },
    ],
  })
  router.push('/')
  return router
}

async function mountWidget() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.isReady()

  const wrapper = mount(BankBalanceWidget, {
    global: { plugins: [router, pinia] },
  })
  return { wrapper, router }
}

describe('BankBalanceWidget business widget', () => {
  it('renders bank balance from the latest statement and shows reconciled status', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') {
        return {
          items: [
            { id: 's1', transaction_date: '2026-11-01', balance: 50000, amount: 10000 },
            { id: 's2', transaction_date: '2026-11-05', balance: 128450.5, amount: 78450.5 },
          ],
        }
      }
      if (url === '/api/bank/difference') {
        return {
          bank_only_count: 0,
          book_only_count: 0,
          balance_reconciliation: { fully_reconciled: true, bank_closing_balance: 128450.5 },
        }
      }
      return {}
    })

    const { wrapper, router } = await mountWidget()
    await flushPromises()

    expect(wrapper.text()).toContain('公司公户实时余额')
    expect(wrapper.text()).toContain('128,450.50')
    expect(wrapper.text()).toContain('账银已对齐')

    // 点击卡片直达 /bank
    await wrapper.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/bank')
  })

  it('shows warning status when there are unmatched statements', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') {
        return {
          items: [
            { id: 's1', transaction_date: '2026-11-01', balance: 90000, amount: 20000 },
          ],
        }
      }
      if (url === '/api/bank/difference') {
        return {
          bank_only_count: 2,
          book_only_count: 1,
          balance_reconciliation: { fully_reconciled: false, bank_closing_balance: 90000 },
        }
      }
      return {}
    })

    const { wrapper } = await mountWidget()
    await flushPromises()

    expect(wrapper.text()).toContain('90,000.00')
    expect(wrapper.text()).toContain('待核对 3 笔')
    expect(wrapper.find('.status-warning').exists()).toBe(true)
  })

  it('shows empty hint when no statements are imported yet', async () => {
    vi.mocked(apiClient.get).mockImplementation(async () => ({ items: [] }))

    const { wrapper } = await mountWidget()
    await flushPromises()

    expect(wrapper.text()).toContain('待导入')
    expect(wrapper.text()).toContain('0.00')
    expect(wrapper.text()).toContain('点击上传流水，自动对账入账')
  })
})
