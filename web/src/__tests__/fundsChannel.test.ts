/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { fundsChannelFromVoucher, payerDisplayLabel } from '@/utils/fundsChannel'

const wechatRefund = {
  debit: [{ accountCode: '5001', amount: '5.00' }],
  credit: [{ accountCode: '1012001', amount: '5.00' }],
}

const bankPayment = {
  debit: [{ accountCode: '5603002', amount: '0.17' }],
  credit: [{ accountCode: '1002', amount: '0.17' }],
}

const wechatReceive = {
  debit: [{ accountCode: '1012', amount: '19.00' }],
  credit: [{ accountCode: '5001', amount: '19.00' }],
}

describe('fundsChannelFromVoucher', () => {
  it('detects wechat settlement when 1012 has net movement', () => {
    expect(fundsChannelFromVoucher(wechatRefund)).toBe('wechat_settlement')
    expect(fundsChannelFromVoucher(wechatReceive)).toBe('wechat_settlement')
  })

  it('detects company bank when only 1002 moves', () => {
    expect(fundsChannelFromVoucher(bankPayment)).toBe('company_bank')
  })

  it('returns empty for missing voucher or neither account', () => {
    expect(fundsChannelFromVoucher(null)).toBe('')
    expect(fundsChannelFromVoucher({ debit: [{ accountCode: '5603002', amount: '1' }], credit: [{ accountCode: '2241', amount: '1' }] })).toBe('')
  })
})

describe('payerDisplayLabel', () => {
  it('shows wechat settlement channel for 1012 refunds', () => {
    expect(payerDisplayLabel('', wechatRefund)).toBe('微信商户账户（待结算）')
    expect(payerDisplayLabel('company_account', wechatReceive)).toBe('微信商户账户（待结算）')
  })

  it('shows bank label for 1002 movements', () => {
    expect(payerDisplayLabel('company_account', bankPayment)).toBe('公司账户（银行）')
  })

  it('falls back to 公司账户 when voucher has no funds account', () => {
    expect(payerDisplayLabel('', null)).toBe('公司账户')
  })

  it('personal payers always win over channel', () => {
    expect(payerDisplayLabel('boss_personal', bankPayment)).toBe('股东个人')
    expect(payerDisplayLabel('employee_personal:1', wechatReceive)).toBe('员工垫付')
  })
})
