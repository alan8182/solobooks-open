/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  deriveMissingClosingImports,
  filterStatementsInMonth,
  formatMissingImportLabels,
} from '@/utils/closingImportGaps'

function counts(overrides: Partial<{ invoiceCount: number; wechatBillCount: number; bankStatementCount: number }> = {}) {
  return { invoiceCount: 1, wechatBillCount: 1, bankStatementCount: 1, ...overrides }
}

describe('deriveMissingClosingImports', () => {
  it('returns all three categories when nothing was imported', () => {
    const gaps = deriveMissingClosingImports({ invoiceCount: 0, wechatBillCount: 0, bankStatementCount: 0 })
    expect(gaps.map((gap) => gap.key)).toEqual(['invoice', 'wechat', 'bank'])
    expect(gaps.map((gap) => gap.label)).toEqual(['发票', '微信账单', '银行流水'])
  })

  it('returns only the missing categories in fixed order', () => {
    // 微信账单已导入，发票与银行流水缺失 → 顺序仍按卡片编号
    const gaps = deriveMissingClosingImports({ invoiceCount: 0, wechatBillCount: 5, bankStatementCount: 0 })
    expect(gaps).toEqual([
      { key: 'invoice', label: '发票' },
      { key: 'bank', label: '银行流水' },
    ])
  })

  it('returns empty when every category has data', () => {
    expect(deriveMissingClosingImports(counts())).toEqual([])
  })

  it('treats negative counts as missing', () => {
    const gaps = deriveMissingClosingImports(counts({ bankStatementCount: -1 }))
    expect(gaps.map((gap) => gap.key)).toEqual(['bank'])
  })
})

describe('formatMissingImportLabels', () => {
  it('joins labels with the enumeration comma', () => {
    const gaps = deriveMissingClosingImports({ invoiceCount: 0, wechatBillCount: 0, bankStatementCount: 0 })
    expect(formatMissingImportLabels(gaps)).toBe('发票、微信账单、银行流水')
  })

  it('returns an empty string when nothing is missing', () => {
    expect(formatMissingImportLabels([])).toBe('')
  })
})

describe('filterStatementsInMonth', () => {
  const statements = [
    { transaction_date: '2026-10-05 09:30:00' },
    { transaction_date: '2026-11-01 12:00:00' },
    { transaction_date: '2026-11-30 23:59:59' },
    { transaction_date: '' },
    { transaction_date: null },
    {},
  ]

  it('keeps only statements whose date falls in the target month', () => {
    const result = filterStatementsInMonth(statements, '2026-11')
    expect(result).toHaveLength(2)
    expect(result.every((s) => s.transaction_date?.startsWith('2026-11'))).toBe(true)
  })

  it('excludes statements without a parseable date', () => {
    // 空日期流水不归属任何月份，避免被误算进「已导入 N 笔」
    const result = filterStatementsInMonth(statements, '2026-10')
    expect(result).toEqual([{ transaction_date: '2026-10-05 09:30:00' }])
  })
})
