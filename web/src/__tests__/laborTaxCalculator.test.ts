/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司.
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { calcLaborTaxFromGross, calcLaborTaxFromNet } from '@/utils/laborTaxCalculator'

describe('laborTaxCalculator', () => {
  it('handles <= 800 gross under 2026 28号公告 (both VAT and IIT exempt)', () => {
    const res = calcLaborTaxFromGross(300)
    expect(res.exclVatAmount).toBe(297.03)
    expect(res.vatOverThreshold).toBe(false)
    expect(res.vatAmount).toBe(0)
    expect(res.surchargeAmount).toBe(0)
    expect(res.iitAmount).toBe(0)
    expect(res.netAmount).toBe(300)
    expect(res.actionSummary).toContain('零/免税申报')
  })

  it('handles 800-1000 gross (IIT withheld, VAT below 1000 threshold exempt)', () => {
    const res = calcLaborTaxFromGross(900)
    expect(res.vatOverThreshold).toBe(false)
    expect(res.vatAmount).toBe(0)
    expect(res.surchargeAmount).toBe(0)
    expect(res.iitAmount).toBe(20)
    expect(res.netAmount).toBe(880)
    expect(res.actionSummary).toContain('未达 1,000 元')
  })

  it('handles >= 1000 excl VAT gross (withholds 1% VAT + half surcharge + IIT)', () => {
    const res = calcLaborTaxFromGross(2020)
    expect(res.exclVatAmount).toBe(2000)
    expect(res.vatOverThreshold).toBe(true)
    expect(res.vatAmount).toBe(20)
    // 城建 7% + 教育 3% + 地方教育 2% = 12%，减半为 6% → 20 * 6% = 1.20
    expect(res.surchargeAmount).toBe(1.2)
    // 劳务报酬按不含税 2000 计：(2000 - 800) * 20% = 240
    expect(res.iitAmount).toBe(240)
    expect(res.totalTax).toBe(261.2)
    expect(res.netAmount).toBe(1758.8)
  })

  it('handles large gross > 4000 using 80% taxable income rule', () => {
    const res = calcLaborTaxFromGross(10100)
    expect(res.exclVatAmount).toBe(10000)
    expect(res.vatAmount).toBe(100)
    expect(res.surchargeAmount).toBe(6)
    expect(res.iitAmount).toBe(1600)
    expect(res.netAmount).toBe(8394)
  })

  it('exempts VAT withholding when not in the 7 service categories', () => {
    const res = calcLaborTaxFromGross(5000, { isSevenServices: false })
    expect(res.vatOverThreshold).toBe(false)
    expect(res.vatAmount).toBe(0)
    expect(res.surchargeAmount).toBe(0)
    expect(res.iitAmount).toBe(800)
    expect(res.netAmount).toBe(4200)
  })

  it('reverse-calculates gross from target net accurately including threshold boundary', () => {
    for (const target of [500, 960, 2000, 5000]) {
      const res = calcLaborTaxFromNet(target)
      expect(res.mode).toBe('net_to_gross')
      expect(res.netAmount).toBeGreaterThanOrEqual(target)
      expect(res.netAmount - target).toBeLessThanOrEqual(0.02)
      const forward = calcLaborTaxFromGross(res.grossAmount)
      expect(forward.netAmount).toBe(res.netAmount)
    }
  })
})
