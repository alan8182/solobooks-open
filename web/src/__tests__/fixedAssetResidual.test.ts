/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { residualRateFromValue, residualValueFromRate } from '@/utils/fixedAssetResidual'

describe('fixed asset residual conversions', () => {
  it('converts a percentage to a two-decimal residual amount', () => {
    expect(residualValueFromRate(16800, 5)).toBe(840)
    expect(residualValueFromRate(16800, 3.33)).toBe(559.44)
  })

  it('converts a residual amount back to a percentage', () => {
    expect(residualRateFromValue(16800, 840)).toBe(5)
    expect(residualRateFromValue(16800, 559.44)).toBe(3.33)
  })

  it('treats zero or invalid originals as zero', () => {
    expect(residualValueFromRate(0, 5)).toBe(0)
    expect(residualRateFromValue(0, 100)).toBe(0)
    expect(residualRateFromValue(16800, 0)).toBe(0)
  })
})
