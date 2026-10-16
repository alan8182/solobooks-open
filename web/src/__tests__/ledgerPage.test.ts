/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import LedgerPage from '@/pages/ledger/index.vue'

const harness = vi.hoisted(() => ({
  store: null as any,
  push: vi.fn(),
  message: { info: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/stores/entry', () => ({
  useEntryStore: () => harness.store,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: harness.push }),
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => harness.message,
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('ledger page default time filter', () => {
  it('defaults to current year (当年) and displays current year entries', async () => {
    const currentYear = new Date().getFullYear()
    const entries = reactive([
      {
        id: '1',
        title: '年中技术服务费',
        direction: 'income',
        amount: 8000,
        occurDate: `${currentYear}-06-15`,
        category: '技术服务费',
      },
      {
        id: '2',
        title: '去年的旧记录',
        direction: 'income',
        amount: 5000,
        occurDate: `${currentYear - 1}-10-10`,
        category: '其他业务收入',
      },
    ])

    harness.store = reactive({
      activeEntries: entries,
      fetchEntries: vi.fn(async () => {}),
    })

    const wrapper = mount(LedgerPage)
    await flushPromises()

    // The active time filter chip should be '今年'
    const activeChip = wrapper.find('.filter-group[aria-label="时间范围"] .chip.active')
    expect(activeChip.exists()).toBe(true)
    expect(activeChip.text()).toBe('今年')

    // Year entries should be included in the summary and list
    expect(wrapper.text()).toContain('年中技术服务费')
    expect(wrapper.text()).not.toContain('去年的旧记录')
  })
})
