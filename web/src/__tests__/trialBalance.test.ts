/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  filterTrialBalanceRows,
  normalizeTrialBalanceRows,
  trialBalanceAccountDepth,
  trialBalanceTotals,
  type TrialBalanceRow,
} from '@/utils/trialBalance'

function row(overrides: Partial<TrialBalanceRow>): TrialBalanceRow {
  return {
    row_no: 1,
    original_code: '',
    original_name: '',
    partner_name: '',
    year_start_debit: null,
    year_start_credit: null,
    ytd_debit: null,
    ytd_credit: null,
    ending_debit: null,
    ending_credit: null,
    target_code: '',
    target_name: '',
    status: 'matched',
    note: '',
    ...overrides,
  }
}

describe('trial balance snapshot helpers', () => {
  it('normalizes metadata strings and keeps missing amounts as null', () => {
    const rows = normalizeTrialBalanceRows([
      {
        row_no: '2',
        original_code: '1002',
        original_name: '银行存款',
        year_start_debit: '18377.71',
        year_start_credit: null,
        ending_debit: '',
        ending_credit: '0',
        status: 'matched',
      },
    ])

    expect(rows[0].row_no).toBe(2)
    expect(rows[0].year_start_debit).toBe(18377.71)
    expect(rows[0].year_start_credit).toBeNull()
    expect(rows[0].ending_debit).toBeNull()
    expect(rows[0].ending_credit).toBe(0)
  })

  it('uses only the original total row and never invents totals from detail rows', () => {
    const withTotal = trialBalanceTotals([
      row({ row_no: 1, ending_debit: 100, ending_credit: 0 }),
      row({ row_no: 2, status: 'skipped_total', ending_debit: 100, ending_credit: 100 }),
    ])
    expect(withTotal.hasTotalRow).toBe(true)
    expect(withTotal.debit.ending).toBe(100)
    expect(withTotal.credit.ending).toBe(100)
    // 合计行没有年初/累计列时保持 null，不伪造 0
    expect(withTotal.debit.yearStart).toBeNull()

    const withoutTotal = trialBalanceTotals([
      row({ row_no: 1, ending_debit: 100, ending_credit: 0 }),
      row({ row_no: 2, ending_debit: 0, ending_credit: 100 }),
    ])
    expect(withoutTotal.hasTotalRow).toBe(false)
    // 没有合计行时不出数：上下级重复行相加会虚增
    expect(withoutTotal.debit.ending).toBeNull()
    expect(withoutTotal.credit.ending).toBeNull()
  })

  it('computes account depth and filters zero/status/query rows consistently', () => {
    expect(trialBalanceAccountDepth('1002')).toBe(0)
    expect(trialBalanceAccountDepth('2211001')).toBe(1)
    expect(trialBalanceAccountDepth('2221001005')).toBe(2)

    const sample = [
      row({ row_no: 1, original_code: '1001', original_name: '库存现金', status: 'matched' }),
      row({ row_no: 2, original_code: '1122', original_name: '应收账款', status: 'skipped_rollup' }),
      row({ row_no: 3, original_code: '1122002', original_name: '应收账款-创信保险', partner_name: '创信保险', status: 'skipped_zero' }),
      row({ row_no: 4, original_code: '2211001', original_name: '应付职工薪酬-职工薪酬', status: 'unmatched' }),
    ]

    expect(filterTrialBalanceRows(sample, { hideZero: true }).map(r => r.row_no)).toEqual([1, 2, 4])
    expect(filterTrialBalanceRows(sample, { statusFilter: 'zero', hideZero: true }).map(r => r.row_no)).toEqual([3])
    // 搜索时即使开启了隐藏零余额，也应命中零余额行，不让会计搜不到往来户
    expect(filterTrialBalanceRows(sample, { hideZero: true, query: '创信' }).map(r => r.row_no)).toEqual([3])
    expect(filterTrialBalanceRows(sample, { statusFilter: 'unmatched' }).map(r => r.row_no)).toEqual([4])
  })
})
