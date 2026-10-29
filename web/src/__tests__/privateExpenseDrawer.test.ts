/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import PrivateExpenseDrawer from '@/pages/private-expense/components/PrivateExpenseDrawer.vue'
import * as privateExpenseApi from '@/api/private-expense'

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ entries: [] }),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual: any = await importOriginal()
  return {
    ...actual,
    useMessage: () => ({
      success: vi.fn(),
      warning: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
    }),
  }
})

describe('PrivateExpenseDrawer Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('renders correctly with default values for new expense', async () => {
    vi.spyOn(privateExpenseApi, 'getExpenseSummary').mockResolvedValue({
      count: 0,
      total_amount: 0,
      income_amount: 0,
      expense_amount: 0,
      offset_amount: 0,
      balance_amount: 0,
      owners: [{ owner_name: '张三', count: 1, income_amount: 0, expense_amount: 100, offset_amount: 0, balance_amount: -100, deposit_amount: 0, available_income_amount: 0 }],
    })

    const wrapper = mount(PrivateExpenseDrawer, {
      props: {
        show: true,
        expenseId: null,
      },
      global: {
        stubs: {
          SoloDrawer: {
            props: ['show', 'title', 'width'],
            template: '<div v-if="show" class="stub-drawer"><div class="title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
          NDatePicker: true,
          NInputNumber: true,
        },
      },
    })

    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(wrapper.text()).toContain('新增私账记录')
    expect(wrapper.text()).toContain('支出（个人垫付）')
    expect(wrapper.text()).toContain('收入（私户代收）')
    expect(wrapper.text()).toContain('GPT 订阅')
  })

  it('populates fields when editing an existing record', async () => {
    vi.spyOn(privateExpenseApi, 'getExpense').mockResolvedValue({
      id: 'pe-999',
      company_id: 'test-co',
      user_id: 'u-1',
      title: '购买设计资产包',
      amount: 299,
      direction: 'expense',
      category: '设计/技术服务',
      occur_date: '2026-04-01',
      payer: 'alipay',
      owner_name: '李四',
      private_account: '个人支付宝',
      is_available: true,
      memo: '用于主视觉改版',
      invoice_status: 'has_invoice',
      created_at: '2026-04-01T10:00:00Z',
      updated_at: '2026-04-01T10:00:00Z',
    })

    const wrapper = mount(PrivateExpenseDrawer, {
      props: {
        show: true,
        expenseId: 'pe-999',
      },
      global: {
        stubs: {
          SoloDrawer: {
            props: ['show', 'title', 'width'],
            template: '<div v-if="show" class="stub-drawer"><div class="title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
          NDatePicker: true,
          NInputNumber: true,
        },
      },
    })

    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(wrapper.text()).toContain('编辑私账记录')
    expect(privateExpenseApi.getExpense).toHaveBeenCalledWith('pe-999')
  })
})
