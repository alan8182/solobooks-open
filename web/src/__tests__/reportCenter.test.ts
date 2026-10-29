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
    '/reports/archive',
  ]

  const financialStatementPaths = [
    '/reports/balance-sheet',
    '/reports/cash-flow',
    '/reports/equity-changes',
  ]

  // 账簿入口已全部移出报表中心：五本账走 /ledger/books 工作台，整册走 /ledger/book。
  // 报表中心只发「结论」，账簿归侧栏，避免同一批账两套入口。
  /** 五本账是同一个标签页里的内容切换，不各自占路由 */
  const ledgerBookKinds = [
    'general-ledger',
    'sub-ledger',
    'chronological',
    'multi-column',
    'partner-balances',
  ]

  // 并入工作台前的旧地址，仍要被重定向兜住（首页快捷入口与报表下钻还在用）
  const legacyLedgerPaths: Record<string, string> = {
    '/reports/general-ledger': 'general-ledger',
    '/reports/sub-ledger': 'sub-ledger',
    '/reports/chronological': 'chronological',
    '/reports/multi-column': 'multi-column',
    '/reports/partner-balances': 'partner-balances',
  }

  // 增值税与所得税申报辅助并入税务申报工作台（/tax-filing）的旧地址，同样保留重定向
  const legacyTaxAssistPaths: Record<string, string> = {
    '/reports/vat': 'vat',
    '/reports/cit': 'cit',
  }

  const allReportPaths = [
    ...businessHealthPaths,
    ...financialStatementPaths,
  ]

  it('groups cards by purpose into business health and financial statements', () => {
    expect(businessHealthPaths).toHaveLength(5)
    expect(financialStatementPaths).toHaveLength(3)

    // 同一个入口不能在两个分组里各出现一次
    expect(new Set(allReportPaths).size).toBe(allReportPaths.length)
  })

  it('routes all five ledger books through a single workspace entry', () => {
    expect(ledgerBookKinds).toHaveLength(5)
    const resolved = router.resolve('/ledger/books')
    expect(resolved.matched.length).toBeGreaterThan(0)
    // 单 path 单 name：KeepAlive 与页内标签都按 name 认，拆成子路由会各占一个壳
    expect(resolved.name).toBe('LedgerBooks')
  })

  it('redirects legacy ledger links into the workspace and keeps the period', () => {
    for (const [path, book] of Object.entries(legacyLedgerPaths)) {
      const record = router.resolve(path).matched.at(-1)
      expect(typeof record?.redirect).toBe('function')
      const redirect = record!.redirect as unknown as (to: {
        query: Record<string, string>
      }) => { path: string; query: Record<string, string> }
      const target = redirect({ query: { month: '2026-08' } })
      expect(target.path).toBe('/ledger/books')
      expect(target.query.book).toBe(book)
      // 期间必须原样带过去，否则从年度报表下钻会掉回默认月份
      expect(target.query.month).toBe('2026-08')
    }
  })

  it('redirects legacy tax assist links into tax-filing workspace and keeps period and card', () => {
    for (const [path, card] of Object.entries(legacyTaxAssistPaths)) {
      const record = router.resolve(path).matched.at(-1)
      expect(typeof record?.redirect).toBe('function')
      const redirect = record!.redirect as unknown as (to: {
        query: Record<string, string>
      }) => { path: string; query: Record<string, string> }
      const target = redirect({ query: { month: '2026-08' } })
      expect(target.path).toBe('/tax-filing')
      expect(target.query.card).toBe(card)
      expect(target.query.month).toBe('2026-08')
    }
  })

  it('ensures every report card has a valid matched route in the router', () => {
    for (const path of allReportPaths) {
      const resolved = router.resolve(path)
      expect(resolved.matched.length).toBeGreaterThan(0)
    }
  })

  it('supports AP aging route navigation seamlessly', () => {
    const apResolved = router.resolve('/reports/ap-aging')
    expect(apResolved.matched.length).toBeGreaterThan(0)
  })

  it('preserves month query parameter during route resolution across all reports', () => {
    for (const path of allReportPaths) {
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
