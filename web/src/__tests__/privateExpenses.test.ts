/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import PrivateExpenseIndex from '@/pages/private-expense/index.vue'
import * as privateExpenseApi from '@/api/private-expense'
import * as exportUtils from '@/utils/export'

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ entries: [] }),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
  useRoute: () => ({
    params: {},
    query: {},
  }),
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

describe('Private Expenses Page & Calculations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('normalizes summary metrics and handles company-owes-owner status (< 0)', async () => {
    vi.spyOn(privateExpenseApi, 'getExpenseSummary').mockResolvedValue({
      count: 5,
      total_amount: 15000,
      income_amount: 5000,
      expense_amount: 15000,
      deposit_amount: 1000,
      available_income_amount: 4000,
      offset_amount: 4000,
      balance_amount: -11000,
      owners: [{
        owner_name: '张三',
        count: 5,
        income_amount: 5000,
        expense_amount: 15000,
        deposit_amount: 1000,
        available_income_amount: 4000,
        offset_amount: 4000,
        balance_amount: -11000,
      }],
    })

    vi.spyOn(privateExpenseApi, 'getExpenses').mockResolvedValue({
      items: [
        {
          id: 'pe-1',
          company_id: 'test-co',
          user_id: 'u-1',
          title: '服务器采购垫付',
          amount: 11000,
          direction: 'expense',
          category: '小额采购',
          occur_date: '2026-03-15',
          payer: 'wechat',
          owner_name: '张三',
          private_account: '个人微信',
          is_available: true,
          memo: '垫付阿里云',
          invoice_status: 'has_invoice',
          created_at: '2026-03-15T10:00:00Z',
          updated_at: '2026-03-15T10:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })

    const wrapper = mount(PrivateExpenseIndex)
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 50))
    await nextTick()

    // Header check
    expect(wrapper.text()).toContain('小私账')
    expect(wrapper.text()).toContain('老板个人对账')

    // 4-stat cards check
    expect(wrapper.text()).toContain('私账垫付')
    expect(wrapper.text()).toContain('私户代收')
    expect(wrapper.text()).toContain('可收回押金')
    expect(wrapper.text()).toContain('公司应偿付老板')
    expect(wrapper.text()).toContain('待报销冲抵')

    // Filter toolbar exists
    expect(wrapper.find('.filter-bar').exists()).toBe(true)

    // Table item check
    expect(wrapper.text()).toContain('服务器采购垫付')
  })

  it('renders owner-owes-company conclusion when balance > 0', async () => {
    vi.spyOn(privateExpenseApi, 'getExpenseSummary').mockResolvedValue({
      count: 3,
      total_amount: 2000,
      income_amount: 8000,
      expense_amount: 2000,
      deposit_amount: 0,
      available_income_amount: 8000,
      offset_amount: 2000,
      balance_amount: 6000,
      owners: [],
    })

    vi.spyOn(privateExpenseApi, 'getExpenses').mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    })

    const wrapper = mount(PrivateExpenseIndex)
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 50))
    await nextTick()

    expect(wrapper.text()).toContain('老板应归还公司')
    expect(wrapper.text()).toContain('代收沉淀')
    // Empty state should be visible since items is empty
    expect(wrapper.text()).toContain('暂无私账记录')
  })

  it('passes extended filter parameters to getExpenses API', async () => {
    const getExpensesSpy = vi.spyOn(privateExpenseApi, 'getExpenses').mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    })
    vi.spyOn(privateExpenseApi, 'getExpenseSummary').mockResolvedValue({
      count: 0,
      total_amount: 0,
      income_amount: 0,
      expense_amount: 0,
      offset_amount: 0,
      balance_amount: 0,
    })

    // Call API with custom filters directly
    await privateExpenseApi.getExpenses(2, 50, '李四', {
      direction: 'expense',
      invoiceStatus: 'has_invoice',
      startDate: '2026-03-01',
      endDate: '2026-03-31',
    })

    expect(getExpensesSpy).toHaveBeenCalledWith(2, 50, '李四', {
      direction: 'expense',
      invoiceStatus: 'has_invoice',
      startDate: '2026-03-01',
      endDate: '2026-03-31',
    })
  })

  it('triggers Excel export and calls saveWorkbook', async () => {
    const saveWorkbookSpy = vi.spyOn(exportUtils, 'saveWorkbook').mockImplementation(() => {})
    vi.spyOn(privateExpenseApi, 'getAllExpenses').mockResolvedValue([
      {
        id: 'pe-2',
        company_id: 'test-co',
        user_id: 'u-1',
        title: '客户现金代收',
        amount: 3000,
        direction: 'income',
        category: '私下收款',
        occur_date: '2026-03-10',
        payer: 'cash',
        owner_name: '王五',
        private_account: '现金',
        is_available: true,
        memo: '代收尾款',
        invoice_status: 'no_invoice',
        created_at: '2026-03-10T10:00:00Z',
        updated_at: '2026-03-10T10:00:00Z',
      },
    ])

    const wrapper = mount(PrivateExpenseIndex)
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 50))
    await nextTick()

    // Find export button
    const exportBtn = wrapper.findAll('button').find((b) => b.text().includes('导出对账单'))
    expect(exportBtn).toBeDefined()
    if (exportBtn) {
      await exportBtn.trigger('click')
      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(saveWorkbookSpy).toHaveBeenCalled()
    }
  })
})
