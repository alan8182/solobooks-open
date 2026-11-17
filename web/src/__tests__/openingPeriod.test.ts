// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import { describe, expect, it } from 'vitest'
import {
  companyInception,
  openingCutoffOptions,
  resolveOpeningPeriod,
} from '@/utils/openingPeriod'
import type { ExpectedPeriod } from '@/utils/openingBalanceImport'

describe('resolveOpeningPeriod 建账时间默认值', () => {
  it('该年度已保存的建账时间优先于公司启用日期', () => {
    expect(resolveOpeningPeriod('2026-03', '2025-10-01', 2026)).toEqual({ year: 2026, month: '03' })
  })

  // 回归：此前没保存过就一律落到 1 月，10 月启用的账套显示「1 月启用」
  it('建账当年没有保存过时跟公司信息里的启用日期对齐', () => {
    expect(resolveOpeningPeriod(null, '2026-10-01', 2026)).toEqual({ year: 2026, month: '10' })
    expect(resolveOpeningPeriod(undefined, '2027-01-15', 2027)).toEqual({ year: 2027, month: '01' })
  })

  it('别的年度的期初是结转，不回落到建账月份', () => {
    // 后端把 startPeriod 按账套存成一个值，跨年度查到时不能再拿来当月
    expect(resolveOpeningPeriod('2026-10', '2026-10-01', 2027)).toEqual({ year: 2026, month: '01' })
  })

  it('两者都没有时退回 1 月', () => {
    expect(resolveOpeningPeriod(null, null, 2026)).toEqual({ year: null, month: '01' })
    expect(resolveOpeningPeriod('', '', 2026)).toEqual({ year: null, month: '01' })
  })
})

describe('companyInception 公司成立年月', () => {
  it('解析合法日期并忽略非法值', () => {
    expect(companyInception('2026-03-15')).toEqual({ year: 2026, month: 3 })
    expect(companyInception('2026-03')).toEqual({ year: 2026, month: 3 })
    expect(companyInception('')).toBeNull()
    expect(companyInception(null)).toBeNull()
    expect(companyInception('2026-13-01')).toBeNull()
  })
})

describe('openingCutoffOptions 只列能通过校验的月份', () => {
  function expected(overrides: Partial<ExpectedPeriod>): ExpectedPeriod {
    return {
      startYear: 2026,
      startMonth: 1,
      endYear: 2026,
      endMonth: 5,
      requiresFullYear: true,
      zeroOpening: false,
      ...overrides,
    }
  }

  it('从成立月列到启用前一月', () => {
    expect(openingCutoffOptions(expected({ startMonth: 3, endMonth: 5 })))
      .toEqual(['2026-03', '2026-04', '2026-05'])
  })

  it('年初建账从上年成立月列到 12 月', () => {
    const options = openingCutoffOptions(expected({
      startYear: 2025,
      startMonth: 3,
      endYear: 2025,
      endMonth: 12,
      requiresFullYear: false,
    }))
    expect(options[0]).toBe('2025-03')
    expect(options[options.length - 1]).toBe('2025-12')
    expect(options).toHaveLength(10)
  })

  it('零期初时没有可选的截止月', () => {
    expect(openingCutoffOptions(expected({ zeroOpening: true }))).toEqual([])
  })
})
