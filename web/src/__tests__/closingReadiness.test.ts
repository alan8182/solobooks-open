/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  firstActionableReadinessItem,
  getClosingReadiness,
  canBatchPostDrafts,
  deriveClosingTasks,
  summarizeFiveChecks,
  countOtherPendingReadiness,
  summarizeClosedIncomeStatement,
  formatClosingNoticeTitle,
  hasClosingNoticeActions,
  noticeAlertType,
  noticeExportScope,
  type ClosingReadinessItem,
  type PeriodCloseChecks,
} from '@/utils/closingReadiness'

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

  it('surfaces a preceding unclosed month as a blocking task that switches month', () => {
    const checks = {
      checks: [{
        code: 'PERIOD_ORDER', name: '关账顺序', status: 'blocking', uiStatus: 'blocked',
        earlierOpenCount: 1, earliestOpenMonth: '2026-08',
      }],
    }
    const [item] = getClosingReadiness(checks as any)
    expect(item.detail).toBe('2026-08 还没结账，请先结完 2026-08 再结本月。')
    expect(item.status).toBe('blocked')

    const [task] = deriveClosingTasks(checks as any)
    expect(task.id).toBe('period-order')
    expect(task.severity).toBe('blocked')
    expect(task.actions[0]).toEqual({ label: '去结 2026-08', kind: 'detail', month: '2026-08' })
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
    expect(items[0]?.detail).toContain('2 笔草稿凭证将在关账时自动编号过账')
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

  it('does not block on clean drafts that will auto-post at close, while keeping review tasks', () => {
    const cleanTasks = deriveClosingTasks({
      checks: [
        baseCheck('VOUCHER_TO_SOURCE', {
          draftCount: 2,
          autoPostAtCloseCount: 2,
          pendingReviewCount: 0,
          records: [
            { entryId: 'e1', reasons: ['pending_post'] },
            { entryId: 'e2', reasons: ['pending_post'] },
          ],
        }),
        baseCheck('LEDGER_TO_LEDGER', { blockedByPendingCount: 2, status: 'needs_review' }),
      ],
    })
    expect(cleanTasks).toEqual([])

    const reviewTasks = deriveClosingTasks({
      checks: [
        baseCheck('VOUCHER_TO_SOURCE', {
          draftCount: 2,
          autoPostAtCloseCount: 2,
          pendingReviewCount: 1,
        }),
      ],
    })
    expect(reviewTasks).toHaveLength(1)
    expect(reviewTasks[0].id).toBe('review')
  })

  it('distinguishes projected balanced drafts from real bank discrepancies in canBatchPostDrafts', () => {
    const base = {
      checks: [
        {
          code: 'VOUCHER_TO_SOURCE',
          name: '账证核对',
          status: 'blocking',
          draftCount: 1,
          autoPostAtCloseCount: 1,
          pendingReviewCount: 0,
          orphanVoucherCount: 0,
          records: [{ entryId: 'e1', reasons: ['pending_post'] }],
        },
      ],
    }
    const balanced = {
      checks: [
        ...base.checks,
        {
          code: 'BOOK_TO_ACTUAL',
          name: '账实核对',
          status: 'blocking',
          unreconciledBankCount: 0,
          issues: [{ type: 'bank_balance_mismatch', difference: '100.00' }],
          balanceReconciliation: { projected_book_difference: 0 },
        },
      ],
    }
    expect(canBatchPostDrafts(balanced as any)).toBe(true)

    const realMismatch = {
      checks: [
        ...base.checks,
        {
          code: 'BOOK_TO_ACTUAL',
          name: '账实核对',
          status: 'blocking',
          unreconciledBankCount: 0,
          issues: [{ type: 'bank_balance_mismatch', difference: '100.00' }],
          balanceReconciliation: { projected_book_difference: 50 },
        },
      ],
    }
    expect(canBatchPostDrafts(realMismatch as any)).toBe(false)
    const tasks = deriveClosingTasks(realMismatch as any)
    expect(tasks.some(t => t.id === 'balance')).toBe(true)
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

  it('folds missing sources into the match task instead of showing a second card', () => {
    const tasks = deriveClosingTasks({
      checks: [
        baseCheck('BOOK_TO_ACTUAL', {
          issues: [
            { type: 'book_only', entryId: 'e-1', reason: '银行存款分录未关联银行流水' },
            { type: 'missing_source', statementId: 'bs-1', reason: '银行流水缺少有效权威来源记录' },
          ],
        }),
      ],
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0].id).toBe('match')
    expect(tasks[0].title).toBe('有 1 张凭证的分录还没找到银行流水')
    expect(tasks[0].detail).toContain('配对后会自动补建 1 条流水的来源记录')
  })

  it('keeps a standalone task when only source records are missing', () => {
    const tasks = deriveClosingTasks({
      checks: [
        baseCheck('BOOK_TO_ACTUAL', {
          issues: [{ type: 'missing_source', statementId: 'bs-1', reason: '银行流水缺少有效权威来源记录' }],
        }),
      ],
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0].id).toBe('missing-source')
    expect(tasks[0].title).toContain('1 笔流水缺少来源记录')
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

  it('names the platform of duplicate income and stays neutral when mixed', () => {
    const duplicate = (platform: string | undefined, entryId: string) => ({
      type: 'possible_duplicate_income', entryId, platform, reason: '疑似重复',
    })
    const titleFor = (issues: ReturnType<typeof duplicate>[]) => deriveClosingTasks({
      checks: [baseCheck('BOOK_TO_ACTUAL', { issues })],
    }).find(t => t.id === 'duplicate')?.title

    expect(titleFor([duplicate('alipay', 'e-1')])).toBe('有 1 笔到账疑似与支付宝收入重复')
    expect(titleFor([duplicate('wechat', 'e-1'), duplicate('wechat', 'e-2')])).toBe('有 2 笔到账疑似与微信收入重复')
    // 微信、支付宝混在一起时不能把支付宝到账说成微信
    expect(titleFor([duplicate('wechat', 'e-1'), duplicate('alipay', 'e-2')])).toBe('有 2 笔到账疑似与平台收入重复')
    // 旧后端不返回 platform 时退回平台中性文案
    expect(titleFor([duplicate(undefined, 'e-1')])).toBe('有 1 笔到账疑似与平台收入重复')
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

describe('closing notices（跨期与补记提示）', () => {
  const passedChecks = [
    { code: 'VOUCHER_TO_SOURCE', name: '账证核对', status: 'passed' },
    { code: 'LEDGER_TO_LEDGER', name: '账账核对', status: 'passed' },
    { code: 'BOOK_TO_ACTUAL', name: '账实核对', status: 'passed', unreconciledBankCount: 0 },
    { code: 'LEDGER_TO_REPORT', name: '账表核对', status: 'passed' },
    { code: 'REPORT_TO_TAX', name: '申报校验', status: 'passed' },
  ]

  it('提示项不进入五项核对，也不产生阻断任务', () => {
    const checks = {
      canClose: true,
      checks: passedChecks,
      notices: [
        {
          code: 'CROSS_PERIOD_PENDING',
          name: '跨期未处理单据',
          severity: 'warning' as const,
          count: 2,
          amount: '1200.00',
          records: [{ businessDate: '2026-08-20', businessPeriod: '2026-08', description: '8 月流水', amount: '1200.00' }],
        },
      ],
    }

    expect(getClosingReadiness(checks)).toHaveLength(5)
    expect(deriveClosingTasks(checks)).toHaveLength(0)
    expect(checks.canClose).toBe(true)
  })

  it('将小微免税预告映射为 success 利好色调，去掉无意义的「1 笔」并禁用补记导出', () => {
    const vatNotice = {
      code: 'VAT_EXEMPTION_PREVIEW',
      name: '增值税小微免税结转预告',
      severity: 'info' as const,
      count: 1,
      amount: '0.77',
    }

    expect(noticeAlertType(vatNotice)).toBe('success')
    expect(formatClosingNoticeTitle(vatNotice)).toBe('小微免税预告 · 预计减免增值税 ¥0.77')
    expect(formatClosingNoticeTitle(vatNotice)).not.toContain('1 笔')
    expect(noticeExportScope(vatNotice)).toBeNull()
    expect(hasClosingNoticeActions(vatNotice)).toBe(false)
    expect(formatClosingNoticeTitle({ ...vatNotice, amount: undefined })).toBe('小微免税预告')
  })

  it('仅对补记与跨期白名单开放导出清单，暂估提示不挂补记导出', () => {
    const accrualPending = {
      code: 'ACCRUAL_PENDING_REVERSAL',
      name: '未冲回暂估',
      severity: 'warning' as const,
      count: 2,
      amount: '500.00',
    }
    const accrualInPeriod = {
      code: 'ACCRUAL_IN_PERIOD',
      name: '本期暂估',
      severity: 'warning' as const,
      count: 1,
      amount: '300.00',
    }
    for (const notice of [accrualPending, accrualInPeriod]) {
      expect(noticeAlertType(notice)).toBe('warning')
      expect(noticeExportScope(notice)).toBeNull()
      expect(hasClosingNoticeActions(notice)).toBe(false)
    }

    const crossPeriod = { code: 'CROSS_PERIOD_PENDING', name: '跨期未处理单据', severity: 'warning' as const, count: 2, amount: '1200.00' }
    const backfill = { code: 'BACKFILL_IN_PERIOD', name: '本期补记', severity: 'warning' as const, count: 1, amount: '200.00' }
    const vatMismatch = { code: 'VAT_PERIOD_MISMATCH', name: '增值税申报所属期差异', severity: 'warning' as const, count: 1, amount: '15.00' }
    expect(noticeExportScope(crossPeriod)).toBe('cross-period')
    expect(noticeExportScope(backfill)).toBe('backfill')
    expect(noticeExportScope(vatMismatch)).toBe('vat-period')
    expect(hasClosingNoticeActions(crossPeriod)).toBe(true)
    expect(hasClosingNoticeActions(backfill)).toBe(true)
    expect(hasClosingNoticeActions(vatMismatch)).toBe(true)

    const preBook = { code: 'PRE_BOOK_START_ACTIVITY', name: '启用前业务未纳入本账套', severity: 'warning' as const, count: 3 }
    expect(formatClosingNoticeTitle(preBook)).toBe('启用前业务未纳入本账套')
    expect(noticeExportScope(preBook)).toBeNull()
    expect(hasClosingNoticeActions(preBook)).toBe(true)

    // 工资超期未发放：提示卡片必须带「去工资表」入口，落到最早欠发月份。
    const salaryOverdue = {
      code: 'SALARY_PAYABLE_OUTSTANDING',
      name: '工资超期未发放',
      severity: 'warning' as const,
      count: 1,
      amount: '10986.57',
      suggestedMonth: '2026-10',
    }
    expect(noticeAlertType(salaryOverdue)).toBe('warning')
    expect(noticeExportScope(salaryOverdue)).toBeNull()
    expect(hasClosingNoticeActions(salaryOverdue)).toBe(true)
  })
})

describe('five check summary', () => {
  const readiness = (code: string, status: ClosingReadinessItem['status']): ClosingReadinessItem => ({
    code,
    title: code,
    status,
    detail: `${code} detail`,
    actionLabel: '查看',
    step: 4,
  })

  it('keeps the five checks in canonical order and maps their statuses', () => {
    const summary = summarizeFiveChecks([
      readiness('REPORT_TO_TAX', 'blocked'),
      readiness('VOUCHER_TO_SOURCE', 'complete'),
      readiness('BOOK_TO_ACTUAL', 'needs_review'),
      readiness('LEDGER_TO_LEDGER', 'complete'),
      readiness('LEDGER_TO_REPORT', 'complete'),
    ])

    expect(summary.map(row => row.code)).toEqual([
      'VOUCHER_TO_SOURCE',
      'LEDGER_TO_LEDGER',
      'BOOK_TO_ACTUAL',
      'LEDGER_TO_REPORT',
      'REPORT_TO_TAX',
    ])
    expect(summary.map(row => row.label)).toEqual(['凭证与来源', '账账核对', '银行流水核对', '账表核对', '申报校验'])
    expect(summary.map(row => row.status)).toEqual(['complete', 'complete', 'needs_review', 'complete', 'blocked'])
  })

  it('falls back to incomplete when a check is missing', () => {
    const [first] = summarizeFiveChecks([])

    expect(first).toEqual({
      code: 'VOUCHER_TO_SOURCE',
      label: '凭证与来源',
      status: 'incomplete',
      detail: '本期未返回该项核验结果。',
    })
  })

  it('counts only non-five readiness items that still need work', () => {
    expect(countOtherPendingReadiness([
      readiness('VOUCHER_TO_SOURCE', 'blocked'),
      readiness('PAYROLL_ACCRUAL', 'blocked'),
      readiness('FIXED_ASSET_DEPRECIATION', 'incomplete'),
      readiness('INTANGIBLE_AMORTIZATION', 'complete'),
      readiness('PERIOD_ORDER', 'not_required'),
    ])).toBe(2)
  })

  it('marks downstream checks and readiness items as waitingForPosting when blocked only by unposted vouchers', () => {
    const items = getClosingReadiness({
      checks: [
        { code: 'VOUCHER_TO_SOURCE', name: '凭证与来源', status: 'blocking', draftCount: 5, pendingReviewCount: 2 },
        { code: 'LEDGER_TO_LEDGER', name: '账账核对', status: 'needs_review', uiStatus: 'incomplete', blockedByPendingCount: 7 },
        {
          code: 'BOOK_TO_ACTUAL',
          name: '银行核对',
          status: 'blocking',
          unreconciledBankCount: 0,
          issues: [
            { type: 'unposted_match', entryId: 'e-1' },
            { type: 'bank_balance_mismatch', difference: '-500.00' },
          ],
        },
        { code: 'FIXED_ASSET_DEPRECIATION', name: '固定资产折旧', status: 'needs_review', uiStatus: 'incomplete', blockedByPendingCount: 7 },
      ],
    } as any)

    const ledger = items.find(i => i.code === 'LEDGER_TO_LEDGER')
    const bank = items.find(i => i.code === 'BOOK_TO_ACTUAL')
    const depreciation = items.find(i => i.code === 'FIXED_ASSET_DEPRECIATION')
    expect(ledger?.waitingForPosting).toBe(true)
    expect(bank?.status).toBe('incomplete')
    expect(bank?.waitingForPosting).toBe(true)
    expect(depreciation?.waitingForPosting).toBe(true)

    const summary = summarizeFiveChecks(items)
    expect(summary.find(s => s.code === 'BOOK_TO_ACTUAL')?.waitingForPosting).toBe(true)
    expect(countOtherPendingReadiness(items)).toBe(0)
  })
})

describe('summarizeClosedIncomeStatement（结账完成页指标口径）', () => {
  it('将银行利息收入（财务费用负数）归入收入侧，防止成本费用被倒推成负数', () => {
    const result = summarizeClosedIncomeStatement({
      rows: [
        { key: 'operating_revenue', values: { current: 0 } },
        { key: 'nonoperating_income', values: { current: 2.29 } },
        { key: 'finance_expenses', values: { current: -12.7 } },
      ],
      totals: { netProfit: { current: 14.99 } },
    })

    expect(result).toEqual({
      monthlyIncome: 14.99,
      monthlyExpense: 0,
      netProfit: 14.99,
      interestIncome: 12.7,
    })
  })

  it('同时存在正常成本与负财务费用或投资亏损时保持收支减法恒等', () => {
    expect(summarizeClosedIncomeStatement({
      rows: [
        { key: 'operating_revenue', values: { current: 100 } },
        { key: 'operating_cost', values: { current: 40 } },
        { key: 'finance_expenses', values: { current: -5 } },
      ],
      totals: { netProfit: { current: 65 } },
    })).toEqual({
      monthlyIncome: 105,
      monthlyExpense: 40,
      netProfit: 65,
      interestIncome: 5,
    })

    expect(summarizeClosedIncomeStatement({
      rows: [
        { key: 'operating_revenue', values: { current: 100 } },
        { key: 'investment_income', values: { current: -10 } },
      ],
      totals: { netProfit: { current: 90 } },
    })).toEqual({
      monthlyIncome: 100,
      monthlyExpense: 10,
      netProfit: 90,
      interestIncome: 0,
    })
  })
})

describe('PLATFORM_CLEARING_BALANCE（平台待结算负数检查）', () => {
  it('blocking 时禁止一键结账，并在任务清单里引导去第 1 步导入平台账单', () => {
    const payload: PeriodCloseChecks = {
      checks: [
        {
          code: 'VOUCHER_TO_SOURCE',
          name: '账证核对',
          status: 'blocking',
          draftCount: 1,
          autoPostAtCloseCount: 1,
          pendingReviewCount: 0,
          orphanVoucherCount: 0,
          records: [{ entryId: 'd-1', reasons: ['pending_post'] }],
        },
        {
          code: 'PLATFORM_CLEARING_BALANCE',
          name: '平台待结算核对',
          status: 'blocking',
          uiStatus: 'blocked',
          count: 1,
          totalAmount: '84.19',
          accounts: [{ accountCode: '1012001', accountName: '微信支付待结算', balance: '-84.19' }],
          message: '微信支付待结算（-84.19 元）期末余额为负，可能漏导本月微信支付月度交易账单（或期初余额未录入）',
        },
      ],
    }

    expect(canBatchPostDrafts(payload)).toBe(false)
    const tasks = deriveClosingTasks(payload)
    const clearingTask = tasks.find(t => t.id === 'platform-clearing-negative')
    expect(clearingTask).toEqual(expect.objectContaining({
      id: 'platform-clearing-negative',
      title: '微信支付待结算余额为负（¥-84.19）',
      affects: ['平台待结算核对'],
      actions: [{ label: '去导入平台账单', kind: 'import-bill' }],
      severity: 'blocked',
    }))
    const item = getClosingReadiness(payload).find(i => i.code === 'PLATFORM_CLEARING_BALANCE')
    expect(item?.status).toBe('blocked')
    expect(item?.step).toBe(1)
  })

  it('needs_review 且草稿可补平负数时，不阻断批量过账结账', () => {
    const payload: PeriodCloseChecks = {
      checks: [
        {
          code: 'VOUCHER_TO_SOURCE',
          name: '账证核对',
          status: 'blocking',
          draftCount: 1,
          autoPostAtCloseCount: 1,
          pendingReviewCount: 0,
          orphanVoucherCount: 0,
          records: [{ entryId: 'd-wx', reasons: ['pending_post'] }],
        },
        {
          code: 'PLATFORM_CLEARING_BALANCE',
          name: '平台待结算核对',
          status: 'needs_review',
          uiStatus: 'incomplete',
          blockedByPendingCount: 1,
          accounts: [{ accountCode: '1012001', accountName: '微信支付待结算', balance: '-84.19' }],
          message: '微信支付待结算（当前 -84.19 元）待过账凭证入账后余额将恢复正常',
        },
      ],
    }

    expect(canBatchPostDrafts(payload)).toBe(true)
    expect(deriveClosingTasks(payload).find(t => t.id === 'platform-clearing-negative')).toBeUndefined()
  })
})
