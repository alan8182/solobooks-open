import { describe, expect, it } from 'vitest'
import { firstActionableReadinessItem, getClosingReadiness, deriveClosingTasks } from '@/utils/closingReadiness'

describe('closing readiness', () => {
  it('turns backend blocking checks into an actionable business task', () => {
    const items = getClosingReadiness({
      checks: [{ code: 'BOOK_TO_ACTUAL', name: '账实核对', status: 'blocking', unreconciledBankCount: 2 }],
    })

    expect(items).toEqual([expect.objectContaining({
      title: '银行流水与账目配对（银行核对）',
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

  it('separates posting, review and missing sources and opens the voucher list', () => {
    const [item] = getClosingReadiness({ checks: [{ code: 'VOUCHER_TO_SOURCE', name: '账证核对', status: 'blocking', draftCount: 12, pendingReviewCount: 3, orphanVoucherCount: 1 }] })
    expect(item?.detail).toBe('12 笔待过账；3 笔待复核；1 笔缺少有效来源。')
    expect(item?.route).toBe('/vouchers')
  })

  it('does not label draft-excluded checks as complete', () => {
    const [item] = getClosingReadiness({ checks: [{ code: 'LEDGER_TO_LEDGER', name: '账账核对', status: 'needs_review', uiStatus: 'incomplete', blockedByPendingCount: 15 }] })
    expect(item?.status).toBe('incomplete')
    expect(item?.detail).toContain('15 笔凭证未过账')
  })

  it('marks vouchers and dependent checks as complete when drafts can be batch-posted at close', () => {
    const checksData = {
      checks: [
        {
          code: 'VOUCHER_TO_SOURCE',
          name: '账证核对',
          status: 'blocking',
          draftCount: 2,
          autoPostAtCloseCount: 2,
          pendingReviewCount: 0,
          orphanVoucherCount: 0,
          records: [
            { entryId: 'e-1', reasons: ['pending_post'] },
            { entryId: 'e-2', reasons: ['pending_post'] },
          ],
        },
        {
          code: 'LEDGER_TO_LEDGER',
          name: '账账核对',
          status: 'needs_review',
          blockedByPendingCount: 2,
        },
        {
          code: 'BOOK_TO_ACTUAL',
          name: '账实核对',
          status: 'blocking',
          unreconciledBankCount: 0,
          issues: [{ type: 'unposted_match', entryId: 'e-1' }],
        },
      ],
    }

    const items = getClosingReadiness(checksData as any)
    expect(items[0]?.status).toBe('complete')
    expect(items[0]?.detail).toContain('2 笔凭证将在确认结账时自动批量过账')
    expect(items[1]?.status).toBe('complete')
    expect(items[2]?.status).toBe('complete')
    expect(firstActionableReadinessItem(items)).toBeUndefined()
  })

  it('detects when drafts cannot be batch-posted due to review requirements or missing source', () => {
    const blockedData = {
      checks: [
        {
          code: 'VOUCHER_TO_SOURCE',
          name: '账证核对',
          status: 'blocking',
          draftCount: 2,
          autoPostAtCloseCount: 2,
          pendingReviewCount: 1, // needs review
          orphanVoucherCount: 0,
        },
      ],
    }

    const items = getClosingReadiness(blockedData as any)
    expect(items[0]?.status).toBe('blocked')
    expect(firstActionableReadinessItem(items)).toBeDefined()
  })
})

describe('deriveClosingTasks', () => {
  const baseCheck = (code: string, overrides: Record<string, unknown> = {}) => ({
    code, name: code, status: 'blocking', blockedByPendingCount: 0, ...overrides,
  })

  it('aggregates draft vouchers into one post task with batch-post action', () => {
    const tasks = deriveClosingTasks({
      checks: [
        baseCheck('VOUCHER_TO_SOURCE', {
          draftCount: 2,
          autoPostAtCloseCount: 2,
          records: [
            { entryId: 'e1', reasons: ['pending_post'] },
            { entryId: 'e2', reasons: ['pending_post'] },
          ],
        }),
        baseCheck('LEDGER_TO_LEDGER', { blockedByPendingCount: 2, status: 'needs_review' }),
      ],
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0].id).toBe('post')
    expect(tasks[0].title).toContain('2 张凭证还没过账')
    expect(tasks[0].actions.some(a => a.kind === 'run-closing')).toBe(true)
    expect(tasks[0].affects).toContain('账目内部一致（账账核对）')
  })

  it('merges unreconciled statements and book_only entries into one match task', () => {
    const tasks = deriveClosingTasks({
      checks: [
        baseCheck('BOOK_TO_ACTUAL', {
          status: 'blocking',
          issues: [
            { type: 'unreconciled', statementId: 'bs-1', date: '2026-09-01', description: '入账 | 附言', reason: '流水尚未关联有效核销凭证' },
            { type: 'book_only', entryId: 'e-1', reason: '银行存款分录未关联银行流水' },
            { type: 'unposted_match', entryId: 'e-2', reason: '核销凭证尚未过账' },
          ],
        }),
      ],
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0].id).toBe('match')
    expect(tasks[0].title).toContain('1 笔银行流水和 1 张账目凭证')
    expect(tasks[0].matchDetails).toHaveLength(1)
    expect(tasks[0].matchDetails![0].statementId).toBe('bs-1')
  })

  it('creates dedicated tasks for duplicate income and balance mismatch', () => {
    const tasks = deriveClosingTasks({
      checks: [
        baseCheck('BOOK_TO_ACTUAL', {
          issues: [
            { type: 'possible_duplicate_income', entryId: 'e-1', reason: '疑似重复' },
            { type: 'bank_balance_mismatch', reason: '余额不一致', difference: '12.70' },
          ],
        }),
      ],
    })
    expect(tasks.map(t => t.id)).toEqual(['duplicate', 'balance'])
    expect(tasks[1].detail).toContain('12.70')
  })

  it('returns empty when everything passes', () => {
    expect(deriveClosingTasks({
      checks: [baseCheck('VOUCHER_TO_SOURCE', { status: 'passed' }), baseCheck('LEDGER_TO_LEDGER', { status: 'passed' })],
    })).toEqual([])
  })

  it('returns empty for missing checks payload', () => {
    expect(deriveClosingTasks(null)).toEqual([])
    expect(deriveClosingTasks({})).toEqual([])
  })
})
