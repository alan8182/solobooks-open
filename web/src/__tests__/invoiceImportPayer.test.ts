/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { NMessageProvider, NSelect } from 'naive-ui'
import InvoiceImportDrawer from '@/pages/invoice/InvoiceImportDrawer.vue'
import {
  INVOICE_PAYER_OPTIONS,
  hasUnnamedEmployeePayer,
  initialInvoicePayer,
  invoiceRowPayer,
} from '@/utils/invoiceImport'
import * as invoiceApi from '@/api/invoice'

vi.mock('@/api/invoice', () => ({
  parseInvoiceRows: vi.fn(),
  importInvoices: vi.fn(),
  parseInvoiceColumns: vi.fn(),
}))

vi.mock('@/api/rd', () => ({
  getRdProjects: vi.fn().mockResolvedValue([]),
  getRdExpenseTypes: vi.fn().mockResolvedValue({ items: [] }),
}))

vi.mock('@/api/salary', () => ({
  getEmployees: vi.fn().mockResolvedValue([
    { id: 'e-1', name: '张三', department: '运营', is_active: 1, is_owner: 0 },
    { id: 'e-2', name: '老板', department: '', is_active: 1, is_owner: 1 },
  ]),
}))

// Excel 解析不是本测试的重点：固定返回一行腾讯云发票
vi.mock('xlsx', () => ({
  read: vi.fn(() => ({ SheetNames: ['Sheet1'], Sheets: { Sheet1: {} } })),
  utils: {
    sheet_to_json: vi.fn(() => [
      {
        发票号码: 'INV-TENCENT-1',
        开票日期: '2026-10-08',
        销售方名称: '腾讯云计算（北京）有限责任公司',
        价税合计: 431.25,
      },
    ]),
  },
}))

function withProvider() {
  return defineComponent({
    render() {
      return h(NMessageProvider, null, {
        default: () => h(InvoiceImportDrawer, { show: true }),
      })
    },
  })
}

async function flush() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 30))
  await nextTick()
}

async function mountAtPreview() {
  vi.mocked(invoiceApi.parseInvoiceRows).mockResolvedValue({
    valid: [
      {
        invoice_number: 'INV-TENCENT-1',
        invoice_date: '2026-10-08',
        seller_name: '腾讯云计算（北京）有限责任公司',
        amount: 431.25,
        direction: 'expense',
        category: '信息技术服务费',
      },
    ],
    errors: [],
    total: 1,
    valid_count: 1,
  })
  vi.mocked(invoiceApi.importInvoices).mockResolvedValue({
    imported: 1,
    total: 1,
    errors: [],
    entries: [{ id: 'entry-1', title: '发票-腾讯云', success: true }],
  })

  const wrapper = mount(withProvider(), {
    global: {
      stubs: {
        SoloDrawer: { template: '<div class="solo-drawer-stub"><slot /></div>' },
        FileDropzone: { template: '<div class="file-dropzone-stub" />' },
      },
    },
  })
  await flush()

  const drawer = wrapper.findComponent(InvoiceImportDrawer)
  await (drawer.vm as any).handleFile(new File(['xlsx'], 'invoice.xlsx'))
  await flush()
  await (drawer.vm as any).proceedToPreview()
  await flush()
  return { wrapper, drawer }
}

function payerSelect(drawer: any) {
  return drawer.findAllComponents(NSelect).find(
    (select: any) => select.props('options') === INVOICE_PAYER_OPTIONS,
  )
}

async function clickImport(wrapper: any) {
  const button = wrapper.findAll('button').find((item: any) => item.text().includes('确认导入'))
  expect(button).toBeTruthy()
  await button.trigger('click')
  await flush()
}

describe('invoice import payer helpers', () => {
  it('defaults to company account and only rewrites expense rows', () => {
    expect(initialInvoicePayer(undefined)).toEqual({ kind: 'company_account', employeeName: '' })
    expect(invoiceRowPayer('expense', initialInvoicePayer(undefined))).toBe('company_account')
    expect(invoiceRowPayer('expense', { kind: 'boss_personal', employeeName: '' })).toBe('boss_personal')
    expect(invoiceRowPayer('expense', { kind: 'employee_personal', employeeName: '张三' })).toBe(
      'employee_personal:张三',
    )
    // 销项票的结算方式由应收科目决定，付款方选择不参与
    expect(invoiceRowPayer('income', { kind: 'boss_personal', employeeName: '' })).toBe(
      'company_account',
    )
  })

  it('flags employee advances without a chosen person', () => {
    expect(
      hasUnnamedEmployeePayer([{ direction: 'expense', payer: { kind: 'employee_personal', employeeName: '' } }]),
    ).toBe(true)
    expect(
      hasUnnamedEmployeePayer([{ direction: 'expense', payer: { kind: 'employee_personal', employeeName: '张三' } }]),
    ).toBe(false)
  })
})

describe('InvoiceImportDrawer 付款方确认', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows a payer column defaulting to company account and imports boss advance', async () => {
    const { wrapper, drawer } = await mountAtPreview()

    expect(wrapper.text()).toContain('付款方')
    const select = payerSelect(drawer)
    expect(select).toBeTruthy()
    expect(select!.props('value')).toBe('company_account')

    select!.vm.$emit('update:value', 'boss_personal')
    await flush()
    expect((drawer.vm as any).classifiedRows[0]._payer.kind).toBe('boss_personal')

    await clickImport(wrapper)

    expect(invoiceApi.importInvoices).toHaveBeenCalledTimes(1)
    const [rows] = vi.mocked(invoiceApi.importInvoices).mock.calls[0]
    expect(rows[0].payer).toBe('boss_personal')
  })

  it('imports employee advance with the chosen employee name', async () => {
    const { wrapper, drawer } = await mountAtPreview()

    payerSelect(drawer)!.vm.$emit('update:value', 'employee_personal')
    await flush()

    const employeeSelect = drawer
      .findAllComponents(NSelect)
      .find((select: any) => select.props('placeholder') === '选择垫付员工')
    expect(employeeSelect).toBeTruthy()
    employeeSelect!.vm.$emit('update:value', '张三')
    await flush()

    await clickImport(wrapper)

    const [rows] = vi.mocked(invoiceApi.importInvoices).mock.calls[0]
    expect(rows[0].payer).toBe('employee_personal:张三')
  })

  it('blocks import when employee advance has no person selected', async () => {
    const { wrapper, drawer } = await mountAtPreview()

    payerSelect(drawer)!.vm.$emit('update:value', 'employee_personal')
    await flush()
    await clickImport(wrapper)

    expect(invoiceApi.importInvoices).not.toHaveBeenCalled()
  })
})
