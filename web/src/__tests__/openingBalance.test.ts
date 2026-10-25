/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { deriveOpeningBalance, isCreditIncreasing } from '@/utils/accounting'

describe('isCreditIncreasing', () => {
  it('负债/权益/收入为贷方增加', () => {
    expect(isCreditIncreasing('2203')).toBe(true)
    expect(isCreditIncreasing('3001')).toBe(true)
    expect(isCreditIncreasing('5001')).toBe(true)
    expect(isCreditIncreasing('5051')).toBe(true)
    expect(isCreditIncreasing('5301')).toBe(true)
  })

  it('资产/成本/费用为借方增加', () => {
    expect(isCreditIncreasing('1002')).toBe(false)
    expect(isCreditIncreasing('4301')).toBe(false)
    expect(isCreditIncreasing('5401001')).toBe(false)
    expect(isCreditIncreasing('5602002')).toBe(false)
    expect(isCreditIncreasing('5801')).toBe(false)
  })
})

describe('deriveOpeningBalance', () => {
  it('账页余额为「借余为正、贷余为负」：期初 = 余额 - 借 + 贷', () => {
    // 演示账套 2026-06 银行存款首行：贷 2000，余额 187473
    expect(deriveOpeningBalance({ debit: 0, credit: 2000, balance: 187473 })).toBe(189473)
  })

  it('贷方科目（预收账款）：贷余记为负数，期初 = 余额 - 借 + 贷', () => {
    // 期初 0，本期贷方 1000 → 贷余 1000 在账页记为 -1000
    expect(deriveOpeningBalance({ debit: 0, credit: 1000, balance: -1000 })).toBe(0)
  })

  it('真实回归（5001 主营业务收入 2026-09）：首行贷 5 余额 -253 → 期初 -248（贷余 248）', () => {
    expect(deriveOpeningBalance({ debit: 0, credit: 5, balance: -253 })).toBe(-248)
  })
})
