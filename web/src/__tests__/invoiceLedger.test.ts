/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import InvoiceLedger from '@/pages/invoice/ledger.vue'
import InvoiceImportDrawer from '@/pages/invoice/InvoiceImportDrawer.vue'
import router from '@/router'

vi.mock('@/api/invoices', () => ({
  getInvoices: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'inv-1',
        invoiceNumber: '24332000000012345678',
        invoiceDate: '2026-10-15',
        buyerName: '测试购买方',
        sellerName: '测试销售方',
        itemName: '技术服务费',
        amountExclTax: '1000.00',
        taxAmount: '60.00',
        amountInclTax: '1060.00',
        voucherRef: '记-001',
        direction: 'income',
        status: 'normal',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  }),
  getInvoiceSummary: vi.fn().mockResolvedValue({
    month_from: '2026-10',
    month_to: '2026-10',
    totals: {
      incomeAmount: '1060.00',
      incomeTax: '60.00',
      incomeCount: 1,
      expenseAmount: '0.00',
      expenseTax: '0.00',
      expenseCount: 0,
      netTax: '60.00',
    },
  }),
  backfillInvoices: vi.fn().mockResolvedValue({
    scanned: 10,
    created: 1,
  }),
}))

vi.mock('@/api/rd', () => ({
  getRdProjects: vi.fn().mockResolvedValue([]),
  getRdExpenseTypes: vi.fn().mockResolvedValue({ items: [] }),
}))

function withProvider(comp: any, props: any = {}) {
  return defineComponent({
    render() {
      return h(NMessageProvider, null, {
        default: () => h(comp, props),
      })
    },
  })
}

describe('InvoiceLedger and Import Merger', () => {
  it('redirects old /record/invoice-import route to /invoices?import=true', async () => {
    const routeDef = router.getRoutes().find(r => r.path === '/record/invoice-import')
    expect(routeDef?.redirect).toBe('/invoices?import=true')
  })

  it('renders invoice ledger table with summary values', async () => {
    setActivePinia(createPinia())
    const localRouter = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/invoices', component: InvoiceLedger },
        { path: '/vouchers/:id', component: { template: '<div>Voucher Detail</div>' } },
      ],
    })
    localRouter.push('/invoices')
    await localRouter.isReady()

    const wrapper = mount(withProvider(InvoiceLedger), {
      global: {
        plugins: [localRouter],
        stubs: {
          InvoiceImportDrawer: true,
          SoloSkeleton: true,
          SoloFieldHelp: true,
        },
      },
    })

    expect(wrapper.text()).toContain('发票台账')
    expect(wrapper.text()).toContain('销项价税合计')
    expect(wrapper.text()).toContain('导入发票')
  })

  it('mounts InvoiceImportDrawer in upload step initially', async () => {
    const wrapper = mount(withProvider(InvoiceImportDrawer, { show: true }), {
      global: {
        stubs: {
          SoloDrawer: {
            template: '<div class="solo-drawer-stub"><slot /></div>',
          },
          FileDropzone: {
            template: '<div class="file-dropzone-stub">Upload Here</div>',
          },
        },
      },
    })

    expect(wrapper.text()).toContain('Upload Here')
    expect(wrapper.text()).toContain('导入说明')
  })
})
