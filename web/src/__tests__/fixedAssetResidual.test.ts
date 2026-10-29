/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { amortizationPlan, depreciationPlan, residualRateFromValue, residualValueFromRate } from '@/utils/fixedAssetResidual'

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

describe('fixed asset depreciation plan', () => {
  it('starts the month after available-for-use and spans the useful life', () => {
    const plan = depreciationPlan({ originalValue: 8500, residualValue: 420, usefulLifeMonths: 36, oneTime: false, availableForUseDate: '2026-10-15' })
    expect(plan).toMatchObject({ base: 8080, monthly: 224.44, usePeriod: '2026-10', startPeriod: '2026-11', endPeriod: '2029-10' })
  })

  it('treats one-time deduction as a single period and crosses year boundaries', () => {
    const plan = depreciationPlan({ originalValue: 8500, residualValue: 0, usefulLifeMonths: 36, oneTime: true, availableForUseDate: '2026-12-01' })
    expect(plan).toMatchObject({ monthly: 8500, startPeriod: '2027-01', endPeriod: '2027-01' })
  })

  it('leaves periods empty without a date', () => {
    const plan = depreciationPlan({ originalValue: 1000, residualValue: 0, usefulLifeMonths: 12, oneTime: false, availableForUseDate: null })
    expect(plan.startPeriod).toBe('')
    expect(plan.endPeriod).toBe('')
  })
})

describe('intangible asset amortization plan', () => {
  it('starts the same month of available-for-use and amortizes evenly', () => {
    const plan = amortizationPlan({ originalValue: 60000, residualValue: 0, usefulLifeMonths: 60, availableForUseDate: '2026-10-15' })
    expect(plan).toMatchObject({
      base: 60000,
      monthly: 1000,
      usePeriod: '2026-10',
      startPeriod: '2026-10',
      endPeriod: '2031-09',
    })
  })

  it('supports non-zero residual value', () => {
    const plan = amortizationPlan({ originalValue: 10000, residualValue: 1000, usefulLifeMonths: 10, availableForUseDate: '2026-01-01' })
    expect(plan).toMatchObject({
      base: 9000,
      monthly: 900,
      startPeriod: '2026-01',
      endPeriod: '2026-10',
    })
  })

  it('handles empty date gracefully', () => {
    const plan = amortizationPlan({ originalValue: 10000, residualValue: 0, usefulLifeMonths: 12, availableForUseDate: null })
    expect(plan.startPeriod).toBe('')
    expect(plan.endPeriod).toBe('')
  })
})
