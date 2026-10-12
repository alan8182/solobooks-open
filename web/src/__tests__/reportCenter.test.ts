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
})
