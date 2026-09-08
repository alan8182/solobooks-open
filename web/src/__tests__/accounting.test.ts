import { describe, expect, it } from 'vitest'
import { amountSignForDirection, generateVoucherLines } from '@/utils/accounting'
import { EXPENSE_CATEGORIES, OWNER_TRANSFER_CATEGORIES } from '@/utils/constants'

describe('generateVoucherLines', () => {
  it('maps bank interest income to financial expense interest income, not main revenue', () => {
    const lines = generateVoucherLines({
      type: 'income',
      category: '利息收入',
      amount: 4.15,
      payer: 'company_account',
    })

    expect(lines).toEqual([
      { code: '1002', name: '银行存款', side: 'debit', amount: 4.15 },
      { code: '6603', name: '财务费用-利息收入', side: 'credit', amount: 4.15 },
    ])
  })

  it('maps backend expense categories without falling back to generic other expense', () => {
    const rdLines = generateVoucherLines({
      type: 'expense',
      category: '研发支出-费用化支出',
      amount: 1200,
      payer: 'company_account',
    })
    const prepayLines = generateVoucherLines({
      type: 'expense',
      category: '预付账款',
      amount: 500,
      payer: 'company_account',
    })

    expect(rdLines[0]).toEqual({ code: '5301', name: '研发支出-费用化支出', side: 'debit', amount: 1200 })
    expect(prepayLines[0]).toEqual({ code: '1123', name: '预付账款', side: 'debit', amount: 500 })
  })

  it('credits the named employee payable when an employee paid the expense', () => {
    const lines = generateVoucherLines({
      type: 'expense',
      category: '信息技术服务费',
      amount: 300,
      payer: 'employee_personal:杨云飞',
    })

    expect(lines).toEqual([
      { code: '5605', name: '管理费用-信息技术服务费', side: 'debit', amount: 300 },
      { code: '2241', name: '其他应付款-员工往来(杨云飞)', side: 'credit', amount: 300 },
    ])
  })

  it('maps 股东投入 to bank debit and paid-in capital credit', () => {
    const lines = generateVoucherLines({
      type: 'owner_transfer',
      category: '股东投入',
      amount: 50000,
      payer: 'company_account',
    })

    expect(lines).toEqual([
      { code: '1002', name: '银行存款', side: 'debit', amount: 50000 },
      { code: '4001', name: '实收资本', side: 'credit', amount: 50000 },
    ])
  })

  it('keeps 老板投入 as a legacy alias of 股东投入', () => {
    const lines = generateVoucherLines({
      type: 'owner_transfer',
      category: '老板投入',
      amount: 10000,
    })

    expect(lines[1]).toEqual({ code: '4001', name: '实收资本', side: 'credit', amount: 10000 })
  })

  it('maps 股东取现 as a bank outflow receivable from shareholder', () => {
    const lines = generateVoucherLines({
      type: 'owner_transfer',
      category: '股东取现',
      amount: 2000,
    })

    expect(lines).toEqual([
      { code: '1221', name: '其他应收款-股东', side: 'debit', amount: 2000 },
      { code: '1002', name: '银行存款', side: 'credit', amount: 2000 },
    ])
  })
})

describe('amountSignForDirection', () => {
  it('shows 股东投入 as an inflow and 股东取现 as an outflow', () => {
    expect(amountSignForDirection('owner_transfer', '股东投入')).toBe('+')
    expect(amountSignForDirection('owner_transfer', '股东取现')).toBe('-')
  })

  it('shows reimbursement settlements as cash outflows', () => {
    expect(amountSignForDirection('owner_transfer', '股东垫付报销')).toBe('-')
    expect(amountSignForDirection('owner_transfer', '员工垫付报销')).toBe('-')
  })
})

describe('reimbursement category direction', () => {
  it('only offers reimbursement settlement categories for owner transfers', () => {
    expect(EXPENSE_CATEGORIES).not.toContain('股东垫付报销')
    expect(EXPENSE_CATEGORIES).not.toContain('员工垫付报销')
    expect(OWNER_TRANSFER_CATEGORIES).toContain('股东垫付报销')
    expect(OWNER_TRANSFER_CATEGORIES).toContain('员工垫付报销')
  })
})
