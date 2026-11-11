/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import BankCategoryRulesDrawer from '@/pages/bank/components/BankCategoryRulesDrawer.vue'

const harness = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  getCategories: vi.fn(),
  message: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  dialog: { warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({
  default: {
    get: harness.get,
    patch: harness.patch,
    delete: harness.delete,
  },
}))
vi.mock('@/api/category', () => ({
  getCategories: harness.getCategories,
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => harness.message,
  useDialog: () => harness.dialog,
}))

function mountDrawer() {
  return mount(BankCategoryRulesDrawer, {
    props: { show: true },
    global: {
      stubs: {
        SoloDrawer: {
          props: ['show'],
          template: '<section><slot /></section>',
        },
        SoloEmpty: {
          props: ['title', 'description'],
          template: '<div>{{ title }} {{ description }}</div>',
        },
      },
    },
  })
}

describe('BankCategoryRulesDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    harness.getCategories.mockResolvedValue([
      { id: 'cat-1', name: '信息技术服务费', direction: 'expense', is_active: true },
      { id: 'cat-2', name: '办公费', direction: 'expense', is_active: true },
    ])
  })

  it('shows tenant rules with purpose, confidence and usage evidence', async () => {
    harness.get.mockResolvedValue({
      total: 1,
      items: [{
        id: 'rule-1',
        direction: 'expense',
        counterpartyKey: '通用服务有限公司',
        purposeKey: '信息技术服务',
        category: '信息技术服务费',
        accountCode: '5602015',
        confidence: 0.98,
        storedConfidence: 0.98,
        enabled: true,
        hitCount: 3,
        lastHitAt: '2026-06-01T08:30:00+00:00',
        overrideCount: 1,
        sourceStatementId: 'stmt-1',
      }],
    })

    const wrapper = mountDrawer()
    await flushPromises()

    expect(harness.get).toHaveBeenCalledWith('/api/bank/category-rules', expect.objectContaining({
      params: expect.objectContaining({ limit: 200 }),
    }))
    expect(wrapper.text()).toContain('通用服务有限公司 · 信息技术服务')
    expect(wrapper.text()).toContain('精确规则')
    expect(wrapper.text()).toContain('信息技术服务费')
    expect(wrapper.text()).toContain('5602015')
    expect(wrapper.text()).toContain('命中 3 次')
    expect(wrapper.text()).toContain('已覆盖修改 1 次')
    wrapper.unmount()
  })

  it('labels counterparty-only rules as requiring review', async () => {
    harness.get.mockResolvedValue({
      total: 1,
      items: [{
        id: 'rule-broad',
        direction: 'expense',
        counterpartyKey: '通用服务有限公司',
        purposeKey: '',
        category: '其他应收款',
        accountCode: '122199',
        confidence: 0.78,
        storedConfidence: 0.98,
        enabled: true,
        hitCount: 0,
      }],
    })

    const wrapper = mountDrawer()
    await flushPromises()

    expect(wrapper.text()).toContain('通用服务有限公司')
    expect(wrapper.text()).toContain('仅对方，需复核')
    expect(wrapper.text()).toContain('78%')
    wrapper.unmount()
  })
})
