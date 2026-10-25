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
