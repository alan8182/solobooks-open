/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import AdvanceBalances from '@/pages/report/advance-balances.vue'
import apiClient from '@/api/client'
import router from '@/router'

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn(),
  },
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual: any = await importOriginal()
  return {
    ...actual,
    useRouter: () => ({
      push: vi.fn(),
    }),
    useRoute: () => ({
      params: {},
      query: { month: '2026-10' },
    }),
  }
})

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

describe('Advance Balances Ledger (垫付台账)', () => {
  const mockAdvanceData = {
    accountCode: '2241',
    month: '2026-10',
    asOf: '2026-10-31',
    items: [
      {
        payer: 'boss_personal',
        kind: 'boss',
        name: '老板（股东）',
        opening: '1000.00',
        advance: '500.00',
        reimbursed: '300.00',
        closing: '1200.00',
        count: 3,
        lastDate: '2026-10-25',
        entries: [
          {
            id: 'entry-1',
            sourceId: 'src-1',
            voucherNo: '001',
            date: '2026-10-10',
            period: '2026-10',
            summary: '差旅打车费',
            category: '差旅费',
            amount: '200.00',
            direction: 'advance',
            isCurrentMonth: true,
          },
          {
            id: 'entry-2',
            sourceId: 'src-2',
            voucherNo: '002',
            date: '2026-10-18',
            period: '2026-10',
            summary: '客户商务接待餐饮',
            category: '业务招待费',
            amount: '300.00',
            direction: 'advance',
            isCurrentMonth: true,
          },
        ],
      },
      {
        payer: 'employee_personal:张三',
        kind: 'employee',
        name: '张三',
        opening: '200.00',
        advance: '800.00',
        reimbursed: '200.00',
        closing: '800.00',
        count: 2,
        lastDate: '2026-10-20',
        entries: [
          {
            id: 'entry-3',
            sourceId: 'src-3',
            voucherNo: '003',
            date: '2026-10-20',
            period: '2026-10',
            summary: '办公用品采购',
            category: '办公费',
            amount: '800.00',
            direction: 'advance',
            isCurrentMonth: true,
          },
        ],
      },
      {
        payer: 'employee_personal:李四',
        kind: 'employee',
        name: '李四',
        opening: '0.00',
        advance: '100.00',
        reimbursed: '100.00',
        closing: '0.00',
        count: 2,
        lastDate: '2026-10-15',
        entries: [],
      },
    ],
    totals: {
      opening: '1200.00',
      advance: '1400.00',
      reimbursed: '600.00',
      closing: '2000.00',
    },
    other: {
      count: 1,
      advance: '350.00',
      reimbursed: '0.00',
    },
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue(mockAdvanceData)
  })

  it('renders top KPI summary cards correctly', async () => {
    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    // 检查期末待报总额 (2,000.00)
    expect(wrapper.text()).toContain('2,000.00')
    // 检查老板个人垫付待还 (1,200.00)
    expect(wrapper.text()).toContain('1,200.00')
    // 检查员工待报销款 (张三 800 + 李四 0 = 800.00)
    expect(wrapper.text()).toContain('800.00')
    // 检查本期动态 (+1,400.00 / -600.00)
    expect(wrapper.text()).toContain('1,400.00')
    expect(wrapper.text()).toContain('600.00')
  })

  it('displays the list of advance payers in the table', async () => {
    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    expect(wrapper.text()).toContain('老板（股东）')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('李四')
  })

  it('filters data by unsettled status when toggle is enabled', async () => {
    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    // 初始包含已结清的李四 (closing = 0.00)
    expect(wrapper.text()).toContain('李四')

    // 切换「仅看未结清」
    const vm = wrapper.vm as any
    vm.onlyUnsettled = true
    await nextTick()

    const names = vm.filteredItems.map((r: any) => r.name)
    expect(names).toContain('老板（股东）')
    expect(names).toContain('张三')
    expect(names).not.toContain('李四')
  })

  it('filters data by kind (boss vs employee)', async () => {
    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    const vm = wrapper.vm as any
    // 仅看老板
    vm.kindFilter = 'boss'
    await nextTick()
    expect(vm.filteredItems.map((r: any) => r.name)).toEqual(['老板（股东）'])

    // 仅看员工
    vm.kindFilter = 'employee'
    await nextTick()
    expect(vm.filteredItems.map((r: any) => r.name)).toEqual(['张三', '李四'])
  })

  it('opens detail drawer when clicking a person row', async () => {
    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    const vm = wrapper.vm as any
    expect(vm.drawerVisible).toBe(false)

    // 打开老板明细
    const bossItem = vm.items[0]
    vm.openDetail(bossItem)
    await nextTick()

    expect(vm.drawerVisible).toBe(true)
    expect(vm.selectedPerson.name).toBe('老板（股东）')
    expect(document.body.textContent).toContain('差旅打车费')
    expect(document.body.textContent).toContain('客户商务接待餐饮')
  })

  it('flags negative closing balances as warning', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      ...mockAdvanceData,
      totals: {
        opening: '0.00',
        advance: '100.00',
        reimbursed: '300.00',
        closing: '-200.00',
      },
    })

    const wrapper = mount(AdvanceBalances)
    await nextTick()
    await nextTick()

    expect(wrapper.text()).toContain('存在超额报销，需核对')
  })
})
