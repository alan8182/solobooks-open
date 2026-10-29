/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export function roundMoney(value: number): number {
  return Math.round((Number.isFinite(value) ? value : 0) * 100) / 100
}

export function residualValueFromRate(originalValue: number, ratePercent: number): number {
  const original = Number(originalValue) || 0
  const rate = Math.min(100, Math.max(0, Number(ratePercent) || 0))
  return roundMoney(original * rate / 100)
}

export function residualRateFromValue(originalValue: number, residualValue: number): number {
  const original = Number(originalValue) || 0
  const residual = Math.max(0, Number(residualValue) || 0)
  if (original <= 0 || residual <= 0) return 0
  return roundMoney(residual / original * 100)
}

export function addMonths(period: string, months: number): string {
  const y = Number(period.slice(0, 4))
  const m = Number(period.slice(5, 7))
  const total = y * 12 + (m - 1) + months
  return `${String(Math.floor(total / 12)).padStart(4, '0')}-${String((total % 12) + 1).padStart(2, '0')}`
}

/** 固定资产折旧计划：可供使用次月起提；一次性折旧视为 1 期。 */
export function depreciationPlan(input: {
  originalValue: number
  residualValue: number
  usefulLifeMonths: number
  oneTime: boolean
  availableForUseDate: string | null
}) {
  const base = Math.max(0, (input.originalValue || 0) - (input.residualValue || 0))
  const months = input.oneTime ? 1 : (input.usefulLifeMonths || 0)
  const usePeriod = input.availableForUseDate ? input.availableForUseDate.slice(0, 7) : ''
  const startPeriod = usePeriod ? addMonths(usePeriod, 1) : ''
  return {
    base,
    months,
    monthly: months > 0 ? roundMoney(base / months) : 0,
    usePeriod,
    startPeriod,
    endPeriod: startPeriod && months > 0 ? addMonths(startPeriod, months - 1) : '',
  }
}

/** 无形资产摊销计划：可供使用当月起摊；直线法平均分摊。 */
export function amortizationPlan(input: {
  originalValue: number
  residualValue: number
  usefulLifeMonths: number
  availableForUseDate: string | null
}) {
  const base = Math.max(0, (input.originalValue || 0) - (input.residualValue || 0))
  const months = input.usefulLifeMonths || 0
  const usePeriod = input.availableForUseDate ? input.availableForUseDate.slice(0, 7) : ''
  const startPeriod = usePeriod
  return {
    base,
    months,
    monthly: months > 0 ? roundMoney(base / months) : 0,
    usePeriod,
    startPeriod,
    endPeriod: startPeriod && months > 0 ? addMonths(startPeriod, months - 1) : '',
  }
}
