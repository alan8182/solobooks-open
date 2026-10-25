/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  PAYER_OPTIONS,
  fundsChannelFromVoucher,
  joinPayer,
  payerCardLabel,
  payerDisplayLabel,
  splitPayer,
} from '@/utils/fundsChannel'

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

describe('确认卡片的付款方三值', () => {
  it('老板与员工分开两个选项，老板不混进员工名单', () => {
    expect(PAYER_OPTIONS.map((o) => o.value)).toEqual([
      'company_account',
      'boss_personal',
      'employee_personal',
    ])
    expect(PAYER_OPTIONS.some((o) => o.label.includes('老板'))).toBe(true)
  })

  it('splitPayer 解出垫付人姓名，历史值 personal 视同老板垫付', () => {
    expect(splitPayer('employee_personal:张三')).toEqual({
      kind: 'employee_personal',
      employeeName: '张三',
    })
    expect(splitPayer('employee_personal')).toEqual({ kind: 'employee_personal', employeeName: '' })
    expect(splitPayer('personal')).toEqual({ kind: 'boss_personal', employeeName: '' })
    expect(splitPayer(undefined)).toEqual({ kind: 'company_account', employeeName: '' })
  })

  it('joinPayer 与 splitPayer 互为逆运算', () => {
    for (const payer of ['company_account', 'boss_personal', 'employee_personal:李四']) {
      const { kind, employeeName } = splitPayer(payer)
      expect(joinPayer(kind, employeeName)).toBe(payer)
    }
    // 选了员工垫付但还没选人：仍然给后端一个合法值，由卡片拦住提交
    expect(joinPayer('employee_personal', '')).toBe('employee_personal')
    expect(joinPayer('boss_personal', '张三')).toBe('boss_personal')
  })

  it('payerCardLabel 带出垫付人姓名', () => {
    expect(payerCardLabel('company_account')).toBe('公司对公')
    expect(payerCardLabel('boss_personal')).toBe('老板个人垫付')
    expect(payerCardLabel('employee_personal:张三')).toBe('员工垫付 · 张三')
    expect(payerCardLabel('employee_personal')).toBe('员工垫付')
  })
})
