/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'
import {
  parseOpeningRows,
  parseFixedAssetRows,
  parseOpeningWorkbook,
} from '@/utils/openingBalanceImport'

describe('opening balance Excel import and matching', () => {
  const mockExistingAccounts = [
    { account_code: '1001', account_name: '库存现金' },
    { account_code: '1002', account_name: '银行存款' },
    { account_code: '1122', account_name: '应收账款' },
    { account_code: '2202', account_name: '应付账款' },
    { account_code: '3001', account_name: '实收资本' },
    { account_code: '4104', account_name: '利润分配' },
  ]

  it('matches standard columns with code and separate debit/credit', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初借方': '50000.00', '期初贷方': '' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初借方': '', '期初贷方': '50000.00' },
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

    const capitalAcc = result.items.find(i => i.account_code === '3001')
    expect(capitalAcc?.credit_balance).toBe(50000)
  })

  it('resolves accounts by name when code has formatting difference (e.g. 1002.01 or missing code)', () => {
    const rows = [
      { '科目编码': '1002.01', '科目名称': '银行存款', '借方余额': '30000' },
      { '科目编码': '', '科目名称': '库存现金', '借方余额': '5000' },
      { '科目编码': '3001', '科目名称': '实收资本', '贷方余额': '35000' },
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
      { '代码': '3001', '名称': '实收资本', '方向': '贷', '余额': '20000' },
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
      { '科目代码': '3001', '科目名称': '实收资本', '贷方': '100' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.unmatchedCount).toBe(1)
    expect(result.warnings.length).toBeGreaterThan(0)
    expect(result.warnings[0]).toContain('未知特定科目')
  })

  it('extracts partner name and splits mid-year expense/revenue YTD items', () => {
    const extendedAccounts = [
      ...mockExistingAccounts,
      { account_code: '5001', account_name: '主营业务收入' },
      { account_code: '5602999', account_name: '管理费用-其他' },
    ]
    const rows = [
      { '科目代码': '1122', '科目名称': '应收账款', '借方': '10000', '客商': '华为技术有限公司' },
      { '科目代码': '2202', '科目名称': '应付账款', '贷方': '10000', '往来单位': '阿里云计算有限公司' },
      // 损益科目
      { '科目代码': '5001', '科目名称': '主营业务收入', '本年累计贷方': '60000' },
      { '科目代码': '5602999', '科目名称': '管理费用-其他', '本年累计借方': '25000' },
    ]

    const result = parseOpeningRows(rows, extendedAccounts)
    expect(result.isBalanced).toBe(true)
    expect(result.totalDebit).toBe(10000)
    expect(result.totalCredit).toBe(10000)

    const ar = result.items.find(i => i.account_code === '1122')
    expect(ar?.partner_name).toBe('华为技术有限公司')

    const ap = result.items.find(i => i.account_code === '2202')
    expect(ap?.partner_name).toBe('阿里云计算有限公司')

    expect(result.ytdItems.length).toBeGreaterThanOrEqual(2)
    const rev = result.ytdItems.find(i => i.account_code === '5001')
    expect(rev?.credit_balance).toBe(60000)
    const exp = result.ytdItems.find(i => i.account_code === '5602999')
    expect(exp?.debit_balance).toBe(25000)
  })

  it('parses fixed assets rows and multi-sheet workbooks', () => {
    const faRows = [
      { '资产名称': 'MacBook Pro', '资产类别': '电子设备', '购入日期': '2025-06-01', '原值': '15000', '累计折旧': '5000', '预计使用月数': '36', '残值率': '5%' },
      { '资产名称': '办公椅', '资产类别': '家具用具', '原值': '2000', '累计折旧': '400' },
    ]
    const assets = parseFixedAssetRows(faRows)
    expect(assets.length).toBe(2)
    expect(assets[0].name).toBe('MacBook Pro')
    expect(assets[0].cost).toBe(15000)
    expect(assets[0].accumulated_depreciation).toBe(5000)
    expect(assets[0].salvage_rate).toBe(0.05)
    expect(assets[1].cost).toBe(2000)

    // 测试多 Sheet 工作簿
    const wb = XLSX.utils.book_new()
    const ws1 = XLSX.utils.json_to_sheet([
      { '科目代码': '1002', '科目名称': '银行存款', '期初借方': '10000' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初贷方': '10000' },
    ])
    XLSX.utils.book_append_sheet(wb, ws1, '资产负债期初余额')

    const ws2 = XLSX.utils.json_to_sheet(faRows)
    XLSX.utils.book_append_sheet(wb, ws2, '固定资产期初卡片')

    const res = parseOpeningWorkbook(wb, mockExistingAccounts)
    expect(res.isBalanced).toBe(true)
    expect(res.totalDebit).toBe(10000)
    expect(res.fixedAssets.length).toBe(2)
  })

  it('supports custom mapping overrides and captures unmatched rows', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '特殊往来户', '期初借方': '12000' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初贷方': '12000' },
    ]

    // 未提供 mapping 时，9999 无法匹配
    const unmapped = parseOpeningRows(rows, mockExistingAccounts)
    expect(unmapped.unmatchedCount).toBe(1)
    expect(unmapped.unmatchedRows?.length).toBe(1)
    expect(unmapped.unmatchedRows?.[0].originalCode).toBe('9999')
    expect(unmapped.isBalanced).toBe(false)

    // 提供 mapping 后，9999 映射到 1122 应收账款
    const mapped = parseOpeningRows(rows, mockExistingAccounts, { '9999': '1122' })
    expect(mapped.unmatchedCount).toBe(0)
    expect(mapped.isBalanced).toBe(true)
    const matchedReceivable = mapped.items.find(i => i.account_code === '1122')
    expect(matchedReceivable?.debit_balance).toBe(12000)
  })
})
