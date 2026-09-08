import { describe, expect, it } from 'vitest'
import { firstActionableReadinessItem, getClosingReadiness } from '@/utils/closingReadiness'

describe('closing readiness', () => {
  it('turns backend blocking checks into an actionable business task', () => {
    const items = getClosingReadiness({
      checks: [{ code: 'BOOK_TO_ACTUAL', name: '账实核对', status: 'blocking', unreconciledBankCount: 2 }],
    })

    expect(items).toEqual([expect.objectContaining({
      title: '银行流水核对',
      status: 'blocked',
      detail: '2 笔银行流水还未核对。',
      actionLabel: '去核对',
      step: 4,
    })])
  })

  it('does not treat an unknown backend status as complete', () => {
    const items = getClosingReadiness({
      checks: [{ code: 'VOUCHER_TO_SOURCE', name: '账证核对', status: 'pending' }],
    })

    expect(items[0].status).toBe('incomplete')
    expect(firstActionableReadinessItem(items)).toEqual(items[0])
  })
})
