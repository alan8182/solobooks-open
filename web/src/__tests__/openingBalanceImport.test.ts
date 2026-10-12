import { describe, expect, it } from 'vitest'
import { parseOpeningRows } from '@/utils/openingBalanceImport'

describe('opening balance Excel import and matching', () => {
  const mockExistingAccounts = [
    { account_code: '1001', account_name: '库存现金' },
    { account_code: '1002', account_name: '银行存款' },
    { account_code: '1122', account_name: '应收账款' },
    { account_code: '2202', account_name: '应付账款' },
    { account_code: '4001', account_name: '实收资本' },
    { account_code: '4104', account_name: '利润分配' },
  ]

  it('matches standard columns with code and separate debit/credit', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初借方': '50000.00', '期初贷方': '' },
      { '科目代码': '4001', '科目名称': '实收资本', '期初借方': '', '期初贷方': '50000.00' },
      { '科目代码': '合计', '科目名称': '总计', '期初借方': '50000.00', '期初贷方': '50000.00' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(50000)
    expect(result.totalCredit).toBe(50000)
    expect(result.isBalanced).toBe(true)
    expect(result.difference).toBe(0)
    expect(result.matchedCount).toBe(2)

    const bankAcc = result.items.find(i => i.account_code === '1002')
    expect(bankAcc?.debit_balance).toBe(50000)
    expect(bankAcc?.credit_balance).toBe(0)

    const capitalAcc = result.items.find(i => i.account_code === '4001')
    expect(capitalAcc?.credit_balance).toBe(50000)
  })

  it('resolves accounts by name when code has formatting difference (e.g. 1002.01 or missing code)', () => {
    const rows = [
      { '科目编码': '1002.01', '科目名称': '银行存款', '借方余额': '30000' },
      { '科目编码': '', '科目名称': '库存现金', '借方余额': '5000' },
      { '科目编码': '4001', '科目名称': '实收资本', '贷方余额': '35000' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(35000)
    expect(result.totalCredit).toBe(35000)
    expect(result.isBalanced).toBe(true)

    const cashAcc = result.items.find(i => i.account_code === '1001')
    expect(cashAcc?.debit_balance).toBe(5000)
  })

  it('handles direction + single amount column pattern', () => {
    const rows = [
      { '代码': '1002', '名称': '银行存款', '方向': '借', '余额': '20000' },
      { '代码': '4001', '名称': '实收资本', '方向': '贷', '余额': '20000' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(20000)
    expect(result.totalCredit).toBe(20000)
    expect(result.isBalanced).toBe(true)
  })

  it('nets debit and credit when both are present on the same account to avoid conflict', () => {
    const rows = [
      { '科目代码': '1122', '科目名称': '应收账款', '借方': '1000', '贷方': '300' },
      { '科目代码': '2202', '科目名称': '应付账款', '借方': '', '贷方': '700' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const arAcc = result.items.find(i => i.account_code === '1122')
    expect(arAcc?.debit_balance).toBe(700)
    expect(arAcc?.credit_balance).toBe(0)
    expect(result.isBalanced).toBe(true)
  })

  it('warns about unmatched accounts and unbalance', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '未知特定科目', '借方': '888' },
      { '科目代码': '1002', '科目名称': '银行存款', '借方': '100' },
      { '科目代码': '4001', '科目名称': '实收资本', '贷方': '100' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.unmatchedCount).toBe(1)
    expect(result.warnings.length).toBeGreaterThan(0)
    expect(result.warnings[0]).toContain('未知特定科目')
  })
})
