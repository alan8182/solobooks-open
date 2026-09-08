import { describe, it, expect } from 'vitest'
import { formatMoney, formatDate, formatDateTime, formatMonth, getMonthRange } from '@/utils/format'

describe('formatMoney', () => {
  it('formats positive amount with ¥ prefix', () => {
    expect(formatMoney(1234.56)).toBe('¥1,234.56')
  })

  it('formats zero amount', () => {
    expect(formatMoney(0)).toBe('¥0.00')
  })

  it('formats negative amount with negative sign and ¥ prefix', () => {
    expect(formatMoney(-500)).toBe('-¥500.00')
  })

  it('formats large numbers with commas', () => {
    expect(formatMoney(1000000)).toBe('¥1,000,000.00')
  })

  it('handles decimal rounding', () => {
    expect(formatMoney(99.999)).toBe('¥100.00')
  })
})

describe('formatDate', () => {
  it('formats date string with default format', () => {
    expect(formatDate('2026-06-08')).toBe('2026-06-08')
  })

  it('formats with custom format', () => {
    expect(formatDate('2026-06-08', 'YYYY年M月D日')).toBe('2026年6月8日')
  })

  it('handles Date object', () => {
    const d = new Date(2026, 0, 1)
    expect(formatDate(d)).toBe('2026-01-01')
  })
})

describe('formatDateTime', () => {
  it('formats date time string', () => {
    expect(formatDateTime('2026-06-08T14:30:00')).toBe('2026-06-08 14:30')
  })
})

describe('formatMonth', () => {
  it('formats month in Chinese style', () => {
    expect(formatMonth('2026-06-08')).toBe('2026年6月')
  })
})

describe('getMonthRange', () => {
  it('returns correct start and end for a month', () => {
    const range = getMonthRange('2026-06')
    expect(range.start).toBe('2026-06-01')
    expect(range.end).toBe('2026-06-30')
  })

  it('handles February in leap year', () => {
    const range = getMonthRange('2024-02')
    expect(range.start).toBe('2024-02-01')
    expect(range.end).toBe('2024-02-29')
  })

  it('handles February in non-leap year', () => {
    const range = getMonthRange('2025-02')
    expect(range.start).toBe('2025-02-01')
    expect(range.end).toBe('2025-02-28')
  })
})
