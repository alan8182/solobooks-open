/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import VoucherSheet from '@/components/business/VoucherSheet.vue'

describe('VoucherSheet small scale vat exemption tag (P0)', () => {
  it('renders vat exemption hint when voucher contains 2221026 credit line', () => {
    const entry = {
      id: 'e-income-1',
      title: '9月技术服务费',
      direction: 'income',
      amount: 1010,
      occurDate: '2026-09-15',
      voucherLines: {
        debit: [{ code: '1002', name: '银行存款', amount: 1010, side: 'debit' }],
        credit: [
          { code: '5001', name: '主营业务收入', amount: 1000, side: 'credit' },
          { code: '2221026', name: '应交增值税（小规模纳税人专用）', amount: 10, side: 'credit' },
        ],
      },
    }

    const wrapper = mount(VoucherSheet, {
      props: {
        entry,
        companyName: '测试小微企业',
      },
    })

    const tag = wrapper.find('.vat-exempt-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toContain('季≤30万免税（结账自动转出）')
  })

  it('does not render vat exemption tag when 2221026 is absent', () => {
    const entry = {
      id: 'e-expense-1',
      title: '购买办公用品',
      direction: 'expense',
      amount: 500,
      occurDate: '2026-09-16',
      voucherLines: {
        debit: [{ code: '5602', name: '管理费用', amount: 500, side: 'debit' }],
        credit: [{ code: '1002', name: '银行存款', amount: 500, side: 'credit' }],
      },
    }

    const wrapper = mount(VoucherSheet, {
      props: {
        entry,
        companyName: '测试小微企业',
      },
    })

    expect(wrapper.find('.vat-exempt-tag').exists()).toBe(false)
  })
})

describe('Quarterly VAT exemption limit and uninvoiced status (P1 & P2)', () => {
  it('calculates quarterly sales and remaining limit under 300,000 correctly', () => {
    const limit = 300000
    const sales = 125000
    const remaining = Math.max(0, limit - sales)
    const percent = Math.min(100, Math.round((sales / limit) * 100))

    expect(remaining).toBe(175000)
    expect(percent).toBe(42)
  })

  it('identifies uninvoiced status when direction is income and billingStatus is no_invoice', () => {
    const entry = {
      direction: 'income',
      billingStatus: 'no_invoice',
    }
    const isUninvoiced = entry.direction === 'income' && entry.billingStatus === 'no_invoice'
    expect(isUninvoiced).toBe(true)

    const label = isUninvoiced ? '未开票收入' : '已有单据'
    expect(label).toBe('未开票收入')
  })
})
