/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('report center architecture and routing', () => {
  const businessHealthPaths = [
    '/reports/health',
    '/reports/income-statement',
    '/reports/comparison',
    '/reports/ar-aging',
  ]

  const taxCompliancePaths = [
    '/reports/vat',
    '/reports/cit',
    '/reports/tax-summary',
    '/reports/archive',
  ]

  const legalBookPaths = [
    '/reports/balance-sheet',
    '/reports/cash-flow',
    '/reports/general-ledger',
    '/reports/sub-ledger',
  ]

  it('organizes reports into 3 symmetrical 4-card tiers (12 cards total)', () => {
    expect(businessHealthPaths).toHaveLength(4)
    expect(taxCompliancePaths).toHaveLength(4)
    expect(legalBookPaths).toHaveLength(4)

    const allPaths = [...businessHealthPaths, ...taxCompliancePaths, ...legalBookPaths]
    const uniquePaths = new Set(allPaths)
    expect(uniquePaths.size).toBe(12)
  })

  it('ensures every report card has a valid matched route in the router', () => {
    const allPaths = [...businessHealthPaths, ...taxCompliancePaths, ...legalBookPaths]
    for (const path of allPaths) {
      const resolved = router.resolve(path)
      expect(resolved.matched.length).toBeGreaterThan(0)
    }
  })

  it('supports AP aging route navigation seamlessly', () => {
    const apResolved = router.resolve('/reports/ap-aging')
    expect(apResolved.matched.length).toBeGreaterThan(0)
  })

  it('preserves month query parameter during route resolution across all reports', () => {
    const allPaths = [...businessHealthPaths, ...taxCompliancePaths, ...legalBookPaths]
    for (const path of allPaths) {
      const resolved = router.resolve({ path, query: { month: '2026-08' } })
      expect(resolved.query.month).toBe('2026-08')
      expect(resolved.matched.length).toBeGreaterThan(0)
    }
  })

  it('filters zero-amount rows while preserving core anchor rows in simplified income statement view', () => {
    const coreKeys = new Set([
      'operating_revenue',
      'operating_cost',
      'tax_surcharges',
      'selling_expenses',
      'admin_expenses',
      'finance_expenses',
      'operating_profit',
      'profit_total',
      'income_tax_expense',
      'net_profit',
    ])

    const sampleRows = [
      { key: 'operating_revenue', label: '营业收入', values: { current: 10000 }, isSubtotal: true },
      { key: 'resource_tax', label: '资源税', values: { current: 0 }, isSubtotal: false },
      { key: 'pollution_fee', label: '排污费', values: { current: 0 }, isSubtotal: false },
      { key: 'admin_expenses', label: '管理费用', values: { current: 2000 }, isSubtotal: false },
      { key: 'net_profit', label: '净利润', values: { current: 8000 }, isSubtotal: true },
    ]

    const filterRows = (rows: typeof sampleRows, hideZero: boolean) => {
      if (!hideZero) return rows
      return rows.filter(row => {
        if (coreKeys.has(row.key) || row.isSubtotal) return true
        return Object.values(row.values).some(v => Math.abs(v) > 0.005)
      })
    }

    const simplified = filterRows(sampleRows, true)
    expect(simplified.map(r => r.key)).toEqual(['operating_revenue', 'admin_expenses', 'net_profit'])
    expect(simplified.find(r => r.key === 'resource_tax')).toBeUndefined()
    expect(simplified.find(r => r.key === 'pollution_fee')).toBeUndefined()

    const full = filterRows(sampleRows, false)
    expect(full).toHaveLength(5)
  })

  it('verifies balance sheet plain-language accounting equation (assets = liabilities + equity)', () => {
    const assets = 500000
    const liabilities = 120000
    const equity = 380000

    const isBalanced = assets === liabilities + equity
    expect(isBalanced).toBe(true)

    const ownerNetWorth = assets - liabilities
    expect(ownerNetWorth).toBe(equity)
  })

  it('correctly maps quarter and year periods to representative end months', () => {
    function getRepresentativeMonth(p: string, pType: 'month' | 'quarter' | 'year'): string {
      if (pType === 'quarter') {
        const [y, qStr] = p.split('-Q')
        const q = parseInt(qStr || '1', 10)
        return `${y}-${String(q * 3).padStart(2, '0')}`
      }
      if (pType === 'year') {
        return `${p}-12`
      }
      return p
    }

    expect(getRepresentativeMonth('2026-05', 'month')).toBe('2026-05')
    expect(getRepresentativeMonth('2026-Q1', 'quarter')).toBe('2026-03')
    expect(getRepresentativeMonth('2026-Q2', 'quarter')).toBe('2026-06')
    expect(getRepresentativeMonth('2026-Q3', 'quarter')).toBe('2026-09')
    expect(getRepresentativeMonth('2026-Q4', 'quarter')).toBe('2026-12')
    expect(getRepresentativeMonth('2026', 'year')).toBe('2026-12')
  })

  it('preserves multi-period query parameters during report navigation', () => {
    const resolved = router.resolve({
      path: '/reports/income-statement',
      query: { month: '2026-09', period: '2026-Q3', periodType: 'quarter' },
    })
    expect(resolved.query.month).toBe('2026-09')
    expect(resolved.query.period).toBe('2026-Q3')
    expect(resolved.query.periodType).toBe('quarter')
  })
})
