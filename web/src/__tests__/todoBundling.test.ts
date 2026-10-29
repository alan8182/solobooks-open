// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
import { describe, it, expect } from 'vitest'

function extractFilingPeriod(title: string): string {
  const match = title.match(/（([^）]+)）/)
  return match ? match[1] : '本期'
}

function extractTaxName(title: string): string {
  return title
    .replace(/^.+?-\s*/, '')
    .replace(/（\d{4}[^）]*）\s*$/, '')
    .trim()
}

describe('Todo Page UI Enhancements', () => {
  it('correctly extracts tax period from todo titles', () => {
    expect(extractFilingPeriod('逾期未申报 - 增值税及附加税费（2026-Q3）')).toBe('2026-Q3')
    expect(extractFilingPeriod('征期临近 - 个税扣缴（2026-10）')).toBe('2026-10')
    expect(extractFilingPeriod('同步税费种认定表')).toBe('本期')
  })

  it('correctly extracts clean tax names for compact bundle tags', () => {
    expect(extractTaxName('逾期未申报 - 增值税及附加税费（2026-Q3）')).toBe('增值税及附加税费')
    expect(extractTaxName('逾期未申报 - 企业所得税预缴（A 类）（2026-Q3）')).toBe('企业所得税预缴（A 类）')
    expect(extractTaxName('征期临近 - 个税扣缴（2026-10）')).toBe('个税扣缴')
  })
})
