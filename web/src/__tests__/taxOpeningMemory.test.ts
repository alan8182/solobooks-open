/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  detectPrepaidCitHint,
  loadTaxOpeningMemory,
  saveTaxOpeningMemory,
  taxOpeningMemoryStorageKey,
  type TaxOpeningMemory,
} from '@/utils/taxOpeningMemory'

const memory = {
  priorYearLoss: 12000,
  prepaidCitTax: 800,
  migrationNote: '2026年6月由金蝶云迁入',
}

describe('tax opening memory storage helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('derives a per-company, per-year storage key', () => {
    expect(taxOpeningMemoryStorageKey('c1', 2026)).toBe('solo:tax-opening-memory:c1:2026')
    expect(taxOpeningMemoryStorageKey(null, 2026)).toBe('solo:tax-opening-memory:default:2026')
  })

  // 根因回归：旧实现 solo_tax_opening_memory_${year} 不带账套维度，
  // 同账号切换账套会把 A 账套的亏损/预缴税额带进 B 账套表单
  it('isolates tax memory per company', () => {
    saveTaxOpeningMemory('c1', 2026, memory)
    saveTaxOpeningMemory('c2', 2026, { ...memory, priorYearLoss: 0, migrationNote: '' })

    expect(loadTaxOpeningMemory('c1', 2026)?.priorYearLoss).toBe(12000)
    expect(loadTaxOpeningMemory('c2', 2026)?.priorYearLoss).toBe(0)
  })

  it('isolates tax memory per fiscal year', () => {
    saveTaxOpeningMemory('c1', 2025, memory)
    expect(loadTaxOpeningMemory('c1', 2026)).toBeNull()
  })

  it('round-trips every field and returns null when nothing stored', () => {
    expect(loadTaxOpeningMemory('c1', 2026)).toBeNull()
    saveTaxOpeningMemory('c1', 2026, memory)
    expect(loadTaxOpeningMemory('c1', 2026)).toEqual(memory)

    const withCarryforward: TaxOpeningMemory = {
      ...memory,
      carryforwardAdvertising: 15000,
      carryforwardEducation: 3200,
      carryforwardDonations: [{ year: 2024, amount: 5000 }],
    }
    saveTaxOpeningMemory('c1', 2026, withCarryforward)
    expect(loadTaxOpeningMemory('c1', 2026)).toEqual(withCarryforward)
  })

  it('normalizes malformed values instead of crashing', () => {
    localStorage.setItem(
      taxOpeningMemoryStorageKey('c1', 2026),
      JSON.stringify({ priorYearLoss: 'abc', migrationNote: 42 }),
    )
    expect(loadTaxOpeningMemory('c1', 2026)).toEqual({
      priorYearLoss: 0,
      prepaidCitTax: 0,
      migrationNote: '42',
    })
  })

  it('returns null for corrupted json', () => {
    localStorage.setItem(taxOpeningMemoryStorageKey('c1', 2026), '{broken')
    expect(loadTaxOpeningMemory('c1', 2026)).toBeNull()
  })

  it('does not resurrect the legacy global key (deliberately not migrated)', () => {
    localStorage.setItem('solo_tax_opening_memory_2026', JSON.stringify(memory))
    expect(loadTaxOpeningMemory('c1', 2026)).toBeNull()
    // 旧键留作孤儿，不被读取也不被消费
    expect(localStorage.getItem('solo_tax_opening_memory_2026')).not.toBeNull()
  })
})

describe('detectPrepaidCitHint', () => {
  it('detects 2221006 YTD debit even when the quarter balance ended at 0 (skipped_zero)', () => {
    const hint = detectPrepaidCitHint([
      { original_code: '2221', original_name: '应交税费', ytd_debit: 6745.52, status: 'skipped_rollup' },
      { original_code: '2221006', original_name: '应交税费-应交企业所得税', ytd_debit: 209.21, status: 'skipped_zero' },
      { original_code: '2221008', original_name: '应交税费-应交城市维护建设税', ytd_debit: 112.41, status: 'matched' },
    ])
    expect(hint).toEqual({
      amount: 209.21,
      sourceLabel: '原表 2221006 应交企业所得税（本年累计借方）',
    })
  })

  it('ignores non-CIT taxes, rollup/total rows, and zero/null YTD debits', () => {
    expect(
      detectPrepaidCitHint([
        { original_code: '2221', original_name: '应交税费', ytd_debit: 500, status: 'skipped_total' },
        { original_code: '2221006', original_name: '应交税费-应交企业所得税', ytd_debit: 500, status: 'skipped_rollup' },
        { original_code: '2221026', original_name: '应交增值税（小规模纳税人专用）', ytd_debit: 300, status: 'matched' },
      ]),
    ).toBeNull()

    expect(
      detectPrepaidCitHint([
        { original_code: '2221006', original_name: '应交税费-应交企业所得税', ytd_debit: null, status: 'skipped_zero' },
      ]),
    ).toBeNull()
  })
})
