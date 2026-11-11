// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import { describe, expect, it } from 'vitest'
import { resolveOpeningPeriod } from '@/utils/openingPeriod'

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
