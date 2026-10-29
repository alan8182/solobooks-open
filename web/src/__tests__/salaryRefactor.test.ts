/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 薪酬页重构守卫：入口组件拆分后，KPI 汇总、分组表头、三阶段进度、草稿编辑重算必须保持正确。
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import SalarySheetTab from '@/pages/mine/salary/components/SalarySheetTab.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ query: {} }) }))
vi.mock('naive-ui', async (importOriginal) => {
  const actual: any = await importOriginal()
  return { ...actual, useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }) }
})

const record = (over: Record<string, unknown> = {}) => ({
  id: 'r1', sheet_id: 's1', employee_id: 'e1', employee_name: '陈默',
  base_salary: 10000, bonus: 1000, allowance: 0, deduction: 0,
  personal_social_security: 925, personal_tax: 75, company_social_security: 2000,
  net_salary: 10000, total_cost: 13000, ...over,
})

function mountTab(sheetStatus: string, extra: Record<string, unknown> = {}) {
  return mount(SalarySheetTab, {
    global: { plugins: [createPinia()] },
    props: {
      selectedMonth: '2026-10',
      sheet: { id: 's1', status: sheetStatus, records: [], paymentStatus: 'unpaid' } as any,
      records: [record()] as any,
      employees: [{ id: 'e1', name: '陈默', department: '行政部' }] as any,
      departments: [{ name: '行政部', isRd: false }] as any,
      rdAllocations: [], paymentSuggestions: [], liabilitySuggestions: [],
      payrollRates: null, housingFund: { personal: 0, company: 0 },
      ...extra,
    },
  })
}

describe('salary refactor', () => {
  it('emits totals (net / cost / withheld) for the KPI cards', () => {
    const wrapper = mountTab('draft')
    const [payload] = wrapper.emitted('totals')!.at(-1)! as any[]
    expect(payload).toEqual({ count: 1, net: 10000, cost: 13000, withheld: 1000 })
  })

  it('renders grouped headers and the three-step progress', () => {
    const text = mountTab('draft').text()
    for (const label of ['应发项目', '代扣代缴', '实发与成本', '① 成本计提', '② 工资发放']) {
      expect(text).toContain(label)
    }
  })

  it('shows accrue button in draft and payment button once accrued', () => {
    expect(mountTab('draft').text()).toContain('计提记账')
    const accrued = mountTab('accrued', {
      sheet: { id: 's1', status: 'accrued', records: [], paymentStatus: 'unpaid', payment_voucher_id: null },
    })
    expect(accrued.text()).toContain('发放工资记账')
  })

  it('collapses bank suggestions behind a toggle', () => {
    const wrapper = mountTab('draft', {
      liabilitySuggestions: [{
        sheetId: 's1', month: '2026-10', kind: 'social_security', label: '社保', amount: '1420.00',
        payDate: '2026-10-15', confidence: 'high', reason: '',
        statement: { id: 'st1', date: '2026-10-15', description: '社保', amount: '1420.00' },
      }],
    })
    expect(wrapper.text()).toContain('银行流水发现 1 笔')
    expect(wrapper.text()).not.toContain('确认缴纳并核销')
  })
})
