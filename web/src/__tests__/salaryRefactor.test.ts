/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 薪酬页重构守卫：入口组件拆分后，KPI 汇总、分组表头、三阶段进度、草稿编辑重算必须保持正确。
import { nextTick } from 'vue'
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { useEntryStore } from '@/stores/entry'
import SalarySheetTab from '@/pages/mine/salary/components/SalarySheetTab.vue'
import PayrollRatesModal from '@/pages/mine/salary/components/PayrollRatesModal.vue'
import EmployeeModal from '@/pages/mine/salary/components/EmployeeModal.vue'
import TaxCalcModal from '@/pages/mine/salary/components/TaxCalcModal.vue'
import RdAllocationModal from '@/pages/mine/salary/components/RdAllocationModal.vue'

const {
  mockGetPayrollRates,
  mockUpdatePayrollRates,
  mockAddEmployee,
  mockUpdateEmployee,
  mockPreviewSalaryTax,
  mockUpdateRdSalaryAllocations,
  mockUnaccrueSalarySheet,
  mockSyncSalarySheetFromPrevious,
} = vi.hoisted(() => ({
  mockGetPayrollRates: vi.fn(),
  mockUpdatePayrollRates: vi.fn(),
  mockAddEmployee: vi.fn(),
  mockUpdateEmployee: vi.fn(),
  mockPreviewSalaryTax: vi.fn(),
  mockUpdateRdSalaryAllocations: vi.fn(),
  mockUnaccrueSalarySheet: vi.fn(),
  mockSyncSalarySheetFromPrevious: vi.fn(),
}))

vi.mock('@/api/salary', async (importOriginal) => {
  const actual: any = await importOriginal()
  return {
    ...actual,
    getPayrollRates: mockGetPayrollRates,
    updatePayrollRates: mockUpdatePayrollRates,
    addEmployee: mockAddEmployee,
    updateEmployee: mockUpdateEmployee,
    previewSalaryTax: mockPreviewSalaryTax,
    updateRdSalaryAllocations: mockUpdateRdSalaryAllocations,
    unaccrueSalarySheet: mockUnaccrueSalarySheet,
    syncSalarySheetFromPrevious: mockSyncSalarySheetFromPrevious,
  }
})

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

function mountTab(sheetStatus: string, extra: Record<string, unknown> = {}, mode: 'boss' | 'accounting' = 'accounting') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useWorkspaceViewStore().setMode(mode)
  return mount(SalarySheetTab, {
    global: { plugins: [pinia] },
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

  it('renders compact columns, auto-save hint and no manual tax/save buttons', () => {
    const text = mountTab('draft').text()
    for (const label of ['个人五险一金', '企业总成本', '含单位', '实发到手', '个税', '① 成本计提', '② 工资发放', '修改后自动保存']) {
      expect(text).toContain(label)
    }
    for (const gone of ['申报实务建议', '一键测算', '保存草稿修改', '研发项目工时', '补助/津贴']) {
      expect(text).not.toContain(gone)
    }
  })

  it('shows the research column only when the book has research departments', () => {
    const rd = mountTab('draft', { departments: [{ name: '行政部', isRd: true }] })
    expect(rd.text()).toContain('研发项目工时')
  })

  it('hides research column when RD department exists in book but no RD employee is in current month sheet', () => {
    const wrapper = mountTab('draft', {
      departments: [
        { name: '行政部', isRd: false },
        { name: '研发部', isRd: true },
      ],
      rdProjects: [{ id: 'p1', code: 'RD01', name: '项目A', status: 'active' }],
    })
    expect(wrapper.text()).not.toContain('研发项目工时')
  })

  it('renders zero inputs and hides payDate picker when sheet is paid and all liabilities settled', () => {
    const wrapper = mountTab('paid', {
      sheet: {
        id: 's1',
        status: 'paid',
        records: [],
        paymentStatus: 'posted',
        ssPaymentStatus: 'posted',
        hfPaymentStatus: 'posted',
        taxPaymentStatus: 'posted',
      },
    })
    expect(wrapper.findAll('.salary-table input').length).toBe(0)
    expect(wrapper.text()).not.toContain('实际发薪/缴纳日')
    expect(wrapper.text()).toContain('本月工资、社保、公积金及个税均已完成记账')
  })

  it('uses boss wording and hides voucher buttons in the boss view', () => {
    expect(mountTab('draft', {}, 'boss').text()).toContain('确认本月工资表')
    const accrued = mountTab('accrued', {
      sheet: { id: 's1', status: 'accrued', records: [], paymentStatus: 'unpaid', ssPaymentStatus: 'unpaid', accrual_voucher_id: 'v1' },
    }, 'boss').text()
    expect(accrued).toContain('一键登记已发已缴')
    expect(accrued).not.toContain('查看计提凭证')
    expect(accrued).not.toContain('缴纳社保')
  })

  it('shows accrue button in draft and payment button once accrued (accounting view)', () => {
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

  it('renders smart OCR screenshot dropzone in rates modal', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    mount(PayrollRatesModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        show: true,
        selectedMonth: '2026-10',
        payrollRates: null,
      },
    })
    expect(document.body.textContent).toContain('智能提取：粘贴或上传税务局核定表截图')
    expect(document.body.textContent).toContain('直接按 Ctrl+V 粘贴')
  })

  it('PayrollRatesModal supports synced housing ratios by default and independent ratios when toggled off', async () => {
    document.body.innerHTML = ''
    mockGetPayrollRates.mockResolvedValue({
      rates: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.08',
        housingCompany: '0.08',
      },
      defaults: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.08',
        housingCompany: '0.08',
      },
      isCustomized: true,
      region: '浙江',
      regionDesc: '浙江省标准',
    })
    mockUpdatePayrollRates.mockResolvedValue({
      rates: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.10',
        housingCompany: '0.08',
      },
      defaults: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.08',
        housingCompany: '0.08',
      },
      isCustomized: true,
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PayrollRatesModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        show: false,
        selectedMonth: '2026-10',
        payrollRates: null,
      },
    })
    await wrapper.setProps({ show: true })
    await flushPromises()
    await nextTick()

    expect(document.body.textContent).toContain('单位与个人同比例')
    const vm = wrapper.vm as any
    // 联动开启时，改单位公积金比例同步更新个人公积金比例
    vm.onHousingCompanyChange(12)
    expect(vm.form.housingCompany).toBe(12)
    expect(vm.form.housingPersonal).toBe(12)

    // 关闭联动后，单位与个人公积金比例可各自独立填写并提交不同小数
    vm.onSyncHousingToggle(false)
    vm.onHousingCompanyChange(8)
    vm.onHousingPersonalChange(10)
    await vm.handleSave()
    expect(mockUpdatePayrollRates).toHaveBeenLastCalledWith(
      {
        socialPersonal: 0.105,
        socialCompany: 0.262,
        housingPersonal: 0.1,
        housingCompany: 0.08,
      },
      { month: '2026-10', recalculate: true },
    )
    wrapper.unmount()

    // 若后端已存有不一致比例（如单位 8% / 个人 10%），弹窗打开时自动解绑不覆盖
    document.body.innerHTML = ''
    mockGetPayrollRates.mockResolvedValueOnce({
      rates: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.10',
        housingCompany: '0.08',
      },
      defaults: {
        socialPersonal: '0.105',
        socialCompany: '0.262',
        housingPersonal: '0.08',
        housingCompany: '0.08',
      },
      isCustomized: true,
    })
    const wrapperDiff = mount(PayrollRatesModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: { show: false, selectedMonth: '2026-10', payrollRates: null },
    })
    await wrapperDiff.setProps({ show: true })
    await flushPromises()
    expect((wrapperDiff.vm as any).syncHousingRates).toBe(false)
    expect((wrapperDiff.vm as any).form.housingCompany).toBe(8)
    expect((wrapperDiff.vm as any).form.housingPersonal).toBe(10)
    wrapperDiff.unmount()
  })

  it('EmployeeModal defaults housing_fund_base to null (follow SS) and supports presets', async () => {
    document.body.innerHTML = ''
    mockAddEmployee.mockResolvedValue({ id: 'emp-new' })
    mockUpdateEmployee.mockResolvedValue({ id: 'emp-1' })

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(EmployeeModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        show: false,
        employee: null,
        departmentOptions: [{ label: '技术部', value: '技术部' }],
        selectedMonth: '2026-10',
        isDraftSheet: true,
      },
    })
    await wrapper.setProps({ show: true })
    await nextTick()

    const vm = wrapper.vm as any
    expect(vm.form.housing_fund_base).toBeNull()
    vm.setHfPreset(0)
    expect(vm.form.housing_fund_base).toBe(0)
    vm.setHfPreset(null)
    expect(vm.form.housing_fund_base).toBeNull()
    vm.form.name = '杨云飞'
    vm.setHfPreset(2490)
    await vm.handleSave()
    expect(mockAddEmployee).toHaveBeenLastCalledWith(
      expect.objectContaining({
        name: '杨云飞',
        social_security_base: 6000,
        housing_fund_base: 2490,
      }),
    )
    wrapper.unmount()

    // 编辑已有员工并修改公积金基数时，默认带出 month + recalculate=true 参数联动重算草稿
    document.body.innerHTML = ''
    const editWrapper = mount(EmployeeModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        show: false,
        employee: {
          id: 'emp-1',
          name: '杨云飞',
          department: '技术部',
          base_salary: 12000,
          social_security_base: 7460,
          housing_fund_base: 2690,
        },
        departmentOptions: [{ label: '技术部', value: '技术部' }],
        selectedMonth: '2026-10',
        isDraftSheet: true,
      },
    })
    await editWrapper.setProps({ show: true })
    await nextTick()
    expect((editWrapper.vm as any).form.housing_fund_base).toBe(2690)
    await (editWrapper.vm as any).handleSave()
    expect(mockUpdateEmployee).toHaveBeenLastCalledWith(
      'emp-1',
      expect.objectContaining({ housing_fund_base: 2690 }),
      { month: '2026-10', recalculate: true },
    )
    editWrapper.unmount()
  })

  it('TaxCalcModal hides apply button in readonly mode', async () => {
    document.body.innerHTML = ''
    mockPreviewSalaryTax.mockResolvedValue({
      employeeId: 'e1',
      employeeName: '陈默',
      month: '2026-10',
      months: 10,
      startMonth: '2026-01',
      hireDateMissing: false,
      currentGross: '11000.00',
      currentWithheld: '925.00',
      priorGross: '0.00',
      priorWithheld: '0.00',
      priorTax: '0.00',
      cumulativeIncome: '11000.00',
      cumulativeBasicDeduction: '5000.00',
      cumulativeSpecialDeduction: '925.00',
      cumulativeAdditionalDeduction: '0.00',
      cumulativeOtherDeduction: '0.00',
      taxableIncome: '5075.00',
      taxRate: '0.03',
      quickDeduction: '0.00',
      cumulativeTax: '152.25',
      prepaidTax: '0.00',
      taxDue: '152.25',
      monthlySpecialAdditional: '0.00',
    })

    const wrapper = mount(TaxCalcModal, {
      attachTo: document.body,
      props: {
        show: false,
        target: record() as any,
        selectedMonth: '2026-10',
        initialSpecialAdditional: 0,
        readonly: true,
      },
    })
    await wrapper.setProps({ show: true })
    await flushPromises()
    await nextTick()

    const text = document.body.textContent || ''
    expect(text).toContain('当月工资表已锁定，仅支持查看测算明细')
    expect(text).not.toContain('填入本期个税')
    expect(text).not.toContain('重新试算')
    wrapper.unmount()
  })

  it('EmployeeModal shows real-time total cost preview when payrollRates is provided', async () => {
    document.body.innerHTML = ''
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(EmployeeModal, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        show: false,
        employee: null,
        departmentOptions: [{ label: '行政部', value: '行政部' }],
        payrollRates: {
          rates: {
            socialPersonal: '0.1',
            socialCompany: '0.2',
            housingPersonal: '0.08',
            housingCompany: '0.08',
          },
          defaults: {
            socialPersonal: '0.1',
            socialCompany: '0.2',
            housingPersonal: '0.08',
            housingCompany: '0.08',
          },
          isCustomized: true,
        },
      },
    })
    await wrapper.setProps({ show: true })
    await nextTick()

    const text = document.body.textContent || ''
    expect(text).toContain('按当前比例预估')
    expect(text).toContain('¥1,080.00')
    expect(text).toContain('¥4,920.00')
    expect(text).toContain('¥7,680.00')
    wrapper.unmount()
  })

  it('RdAllocationModal accepts percentage input and submits decimal allocationRate', async () => {
    document.body.innerHTML = ''
    mockUpdateRdSalaryAllocations.mockResolvedValue({ allocations: [] })
    const wrapper = mount(RdAllocationModal, {
      attachTo: document.body,
      props: {
        show: false,
        employee: record() as any,
        selectedMonth: '2026-10',
        projects: [{ id: 'p1', code: 'RD01', name: '核心平台', status: 'active' }] as any,
        allocations: [],
      },
    })
    await wrapper.setProps({ show: true })
    await nextTick()

    const vm = wrapper.vm as any
    vm.draft[0].projectId = 'p1'
    vm.draft[0].allocationPercent = 60
    vm.draft[0].allocationBasis = '工时记录'
    await vm.handleSave()

    expect(mockUpdateRdSalaryAllocations).toHaveBeenLastCalledWith(
      '2026-10',
      [
        expect.objectContaining({
          employeeId: 'e1',
          projectId: 'p1',
          allocationRate: 0.6,
          allocationBasis: '工时记录',
        }),
      ],
    )
    wrapper.unmount()
  })

  it('shows unaccrue button in accrued state and calls unaccrueSalarySheet on confirm', async () => {
    document.body.innerHTML = ''
    mockUnaccrueSalarySheet.mockReset()
    mockUnaccrueSalarySheet.mockResolvedValue({
      id: 's1',
      status: 'draft',
      records: [],
      accrual_voucher_id: null,
      reversalVoucherId: 'rev-1',
    })

    const bossWrapper = mountTab(
      'accrued',
      {
        sheet: {
          id: 's1',
          status: 'accrued',
          records: [],
          paymentStatus: 'unpaid',
          ssPaymentStatus: 'unpaid',
          accrual_voucher_id: 'v1',
        },
      },
      'boss',
    )
    expect(bossWrapper.text()).toContain('撤销确认')
    expect(bossWrapper.text()).not.toContain('冲销计提')
    bossWrapper.unmount()

    const pinia = createPinia()
    setActivePinia(pinia)
    useWorkspaceViewStore().setMode('accounting')
    useEntryStore().fetchEntries = vi.fn().mockResolvedValue(undefined)

    const accWrapper = mount(SalarySheetTab, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        selectedMonth: '2026-10',
        sheet: {
          id: 's1',
          status: 'accrued',
          records: [],
          paymentStatus: 'unpaid',
          ssPaymentStatus: 'unpaid',
          accrual_voucher_id: 'v1',
        } as any,
        records: [record()] as any,
        employees: [{ id: 'e1', name: '陈默', department: '行政部' }] as any,
        departments: [{ name: '行政部', isRd: false }] as any,
        rdAllocations: [],
        paymentSuggestions: [],
        liabilitySuggestions: [],
        payrollRates: null,
        housingFund: { personal: 0, company: 0 },
      },
    })

    expect(accWrapper.text()).toContain('冲销计提')
    await accWrapper.find('.unaccrue-btn').trigger('click')
    await nextTick()
    expect(document.body.textContent).toContain('确认冲销并退回草稿')

    const confirmBtn = document.querySelector<HTMLButtonElement>('.confirm-unaccrue-btn')!
    confirmBtn.click()
    await flushPromises()

    expect(mockUnaccrueSalarySheet).toHaveBeenCalledWith('2026-10', '工资数据核对有误，撤销重算', false)
    expect(accWrapper.emitted('sheet-updated')?.[0]?.[0]).toMatchObject({ status: 'draft' })
    accWrapper.unmount()
  })

  it('allows cascade unaccrue in fully settled state with includePayments=true', async () => {
    document.body.innerHTML = ''
    mockUnaccrueSalarySheet.mockReset()
    mockUnaccrueSalarySheet.mockResolvedValue({
      id: 's1',
      status: 'draft',
      records: [],
      accrual_voucher_id: null,
      reversedVoucherIds: ['rev-acc', 'rev-pay', 'rev-ss'],
      unmatchedStatementCount: 1,
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    useWorkspaceViewStore().setMode('boss')
    useEntryStore().fetchEntries = vi.fn().mockResolvedValue(undefined)

    const wrapper = mount(SalarySheetTab, {
      global: { plugins: [pinia] },
      attachTo: document.body,
      props: {
        selectedMonth: '2026-10',
        sheet: {
          id: 's1',
          status: 'paid',
          records: [],
          paymentStatus: 'posted',
          ssPaymentStatus: 'posted',
          hfPaymentStatus: 'posted',
          taxPaymentStatus: 'not_required',
          accrual_voucher_id: 'v-acc',
          payment_voucher_id: 'v-pay',
          ss_paid_voucher_id: 'v-ss',
          hf_paid_voucher_id: 'v-hf',
        } as any,
        records: [record()] as any,
        employees: [{ id: 'e1', name: '陈默', department: '行政部' }] as any,
        departments: [{ name: '行政部', isRd: false }] as any,
        rdAllocations: [],
        paymentSuggestions: [],
        liabilitySuggestions: [],
        payrollRates: null,
        housingFund: { personal: 0, company: 0 },
      },
    })

    expect(wrapper.findAll('.salary-table input').length).toBe(0)
    expect(wrapper.text()).not.toContain('实际发薪/缴纳日')
    expect(wrapper.text()).toContain('撤销重算')

    await wrapper.find('.unaccrue-btn').trigger('click')
    await nextTick()
    expect(document.body.textContent).toContain('一并红字冲销')
    expect(document.body.textContent).toContain('确认全部冲销并退回草稿')

    const confirmBtn = document.querySelector<HTMLButtonElement>('.confirm-unaccrue-btn')!
    confirmBtn.click()
    await flushPromises()

    expect(mockUnaccrueSalarySheet).toHaveBeenCalledWith('2026-10', '工资数据核对有误，撤销重算', true)
    expect(wrapper.emitted('sheet-updated')?.[0]?.[0]).toMatchObject({ status: 'draft' })
    wrapper.unmount()
  })

  it('shows carry-forward banner and triggers syncSalarySheetFromPrevious on sync click', async () => {
    mockSyncSalarySheetFromPrevious.mockReset()
    mockSyncSalarySheetFromPrevious.mockResolvedValue({
      id: 's1',
      status: 'draft',
      records: [record({ bonus: 0 })],
      carryForward: {
        hasPreviousSheet: true,
        sourceMonth: '2026-09',
        isIdenticalToPrevious: false,
        changeReasons: ['上月一次性奖金 ¥1,000.00 已自动清零'],
      },
    })

    const wrapper = mountTab('draft', {
      sheet: {
        id: 's1',
        status: 'draft',
        records: [],
        paymentStatus: 'unpaid',
        carryForward: {
          hasPreviousSheet: true,
          sourceMonth: '2026-09',
          isIdenticalToPrevious: false,
          changeReasons: ['上月一次性奖金 ¥1,000.00 已自动清零'],
        },
      },
    })

    expect(wrapper.text()).toContain('已自动带出 2026-09 固定工资与社保')
    expect(wrapper.text()).toContain('上月一次性奖金 ¥1,000.00 已自动清零')

    await wrapper.find('.sync-previous-btn').trigger('click')
    await flushPromises()

    expect(mockSyncSalarySheetFromPrevious).toHaveBeenCalledWith('2026-10')
    expect(wrapper.emitted('sheet-updated')?.[0]?.[0]).toMatchObject({ status: 'draft' })
    expect(wrapper.emitted('reload-rd')).toHaveLength(1)
    wrapper.unmount()
  })
})
