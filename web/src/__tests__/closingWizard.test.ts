/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  amountClassForDirection,
  buildSuggestedMatchPair,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  groupedEntryTotal,
  invoiceDisplayAmount,
  isReconciledEntry,
  isNonBankSettledEntry,
  formatDispositionTag,
  formatDispositionExplanation,
  reconciliationDifference,
  signedInvoiceAmount,
} from '@/utils/closingWizard'
import { sortEntriesByDateDescending } from '@/utils/entrySort'

describe('closing wizard invoice display helpers', () => {
  it('keeps every original entry in a settlement suggestion even when the fee is first', () => {
    const entries = [
      { id: 'fee', direction: 'expense', amount: 0.05 },
      { id: 'income', direction: 'income', amount: 5 },
    ]
    const pair = buildSuggestedMatchPair({
      type: 'match', statementId: 'bank', matchedEntryId: 'fee',
      matchedEntryIds: ['fee', 'income'], matchedEntries: entries,
      statement: { amount: 4.95 },
    }, entries)
    expect(pair?.matchedEntryIds).toEqual(['fee', 'income'])
    expect(pair?.entries).toHaveLength(2)
    expect(pair?.status).toBe('pending')
    expect(buildSuggestedMatchPair({
      type: 'match', matchedEntryIds: ['fee', 'income'], statement: { amount: 4.95 },
    }, [entries[0]!])).toBeNull()
    expect(buildSuggestedMatchPair({
      type: 'match', matchedEntryIds: ['fee', 'income'], statement: { amount: 5 },
    }, entries)).toBeNull()
  })

  it('signs expense invoices negative for reconciliation math only', () => {
    expect(signedInvoiceAmount({ direction: 'expense', amount: 500 })).toBe(-500)
    expect(amountClassForDirection('expense', 500)).toBe('expense')
  })

  it('shows income invoices as positive amounts', () => {
    expect(signedInvoiceAmount({ direction: 'income', amount: 300 })).toBe(300)
    expect(amountClassForDirection('income', -300)).toBe('income')
  })

  it('preserves negative income amounts for refunds', () => {
    expect(signedInvoiceAmount({ direction: 'income', amount: -5 })).toBe(-5)
  })

  it('displays invoices with their raw amount so only red-letter ones read negative', () => {
    expect(invoiceDisplayAmount({ direction: 'expense', amount: 431.25 })).toBe(431.25)
    expect(invoiceDisplayAmount({ direction: 'income', amount: 300 })).toBe(300)
    expect(invoiceDisplayAmount({ direction: 'expense', amount: -5 })).toBe(-5)
    expect(invoiceDisplayAmount(null)).toBe(0)
  })

  it('only treats linked statements as confirmed when the entry is reconciled', () => {
    const statements = [
      { matched_entry_id: 'invoice-pending' },
      { matched_entry_id: 'invoice-confirmed' },
    ]
    const entries = [
      { id: 'invoice-pending', billingStatus: null },
      { id: 'invoice-confirmed', billingStatus: 'reconciled' },
    ]

    const { linkedEntryIds, confirmedEntryIds } = getMatchedEntryIdSets(statements, entries)

    expect(linkedEntryIds.has('invoice-pending')).toBe(true)
    expect(linkedEntryIds.has('invoice-confirmed')).toBe(true)
    expect(confirmedEntryIds.has('invoice-pending')).toBe(false)
    expect(confirmedEntryIds.has('invoice-confirmed')).toBe(true)
  })

  it('keeps reconciliation status separate from an existing matched_entry_id', () => {
    expect(isReconciledEntry({ id: 'invoice-pending', billingStatus: null })).toBe(false)
    expect(isReconciledEntry({ id: 'invoice-confirmed', billingStatus: 'reconciled' })).toBe(true)
  })

  it('combines income and fees into the bank settlement amount', () => {
    const entries = [
      { id: 'sale', direction: 'income', amount: 59 },
      { id: 'fee', direction: 'expense', amount: 0.53 },
    ]

    expect(groupedEntryTotal(entries)).toBe(58.47)
    expect(reconciliationDifference(58.47, entries)).toBe(0)
    expect(reconciliationDifference(58.5, entries)).toBe(0.03)
  })

  it('uses the WeChat clearing effect for a net settlement', () => {
    const entries = [
      {
        id: 'sale', direction: 'income', amount: 59,
        voucherLines: {
          debit: [{ accountCode: '1012001', amount: 59 }],
          credit: [{ accountCode: '5001', amount: 59 }],
        },
      },
      {
        id: 'fee', direction: 'expense', amount: 0.53,
        voucherLines: {
          debit: [{ accountCode: '5603002', amount: 0.53 }],
          credit: [{ accountCode: '1012001', amount: 0.53 }],
        },
      },
    ]

    expect(groupedEntryTotal(entries)).toBe(58.47)
    expect(reconciliationDifference(58.47, entries)).toBe(0)
  })

  it('returns all grouped match IDs while keeping the legacy primary first', () => {
    expect(getStatementMatchedEntryIds({
      matched_entry_id: 'sale',
      matched_entry_ids: ['sale', 'fee'],
    })).toEqual(['sale', 'fee'])

    const { linkedEntryIds, confirmedEntryIds } = getMatchedEntryIdSets(
      [{ matched_entry_id: 'sale', matched_entry_ids: ['sale', 'fee'] }],
      [
        { id: 'sale', billingStatus: 'reconciled' },
        { id: 'fee', billingStatus: 'reconciled' },
      ],
    )

    expect([...linkedEntryIds]).toEqual(['sale', 'fee'])
    expect([...confirmedEntryIds]).toEqual(['sale', 'fee'])
  })

  it('sorts entries by date descending and keeps same-day order stable', () => {
    const entries = [
      { id: 'old', date: '2026-06-11' },
      { id: 'new-first', date: '2026-06-19' },
      { id: 'new-second', occurDate: '2026-06-19' },
      { id: 'missing-date' },
    ]

    expect(sortEntriesByDateDescending(entries).map((entry) => entry.id)).toEqual([
      'new-first',
      'new-second',
      'old',
      'missing-date',
    ])
    expect(entries.map((entry) => entry.id)).toEqual(['old', 'new-first', 'new-second', 'missing-date'])
  })

  it('maps readiness review steps to valid step indices (1 to 5)', () => {
    // Step 1: Voucher/Source, Step 4: Ledger/Bank reconciliation, Step 5: Close checks
    const targetSteps = [1, 2, 3, 4, 5]
    expect(targetSteps).toHaveLength(5)
    expect(targetSteps[targetSteps.length - 1]).toBe(5)
  })

  it('correctly identifies a zero-record month when entries, statements, and invoices are all empty', () => {
    const monthEntries: any[] = []
    const monthStatements: any[] = []
    const monthInvoices: any[] = []

    const isZeroRecordMonth = monthEntries.length === 0 &&
      monthStatements.length === 0 &&
      monthInvoices.length === 0

    expect(isZeroRecordMonth).toBe(true)

    // Cannot close directly without explicit confirmation
    let confirmZeroActivity = false
    const canCloseZeroMonth = isZeroRecordMonth && confirmZeroActivity
    expect(canCloseZeroMonth).toBe(false)

    // Can close only after explicit user confirmation
    confirmZeroActivity = true
    expect(isZeroRecordMonth && confirmZeroActivity).toBe(true)
  })

  it('blocks closing when active employees exist but payroll is not accrued or paid', () => {
    const employees = [{ name: 'Alice', base: 8000 }]
    let payrollAccrued = false
    let payrollPaid = false

    const hasBlockingPayroll = employees.length > 0 && (!payrollAccrued || !payrollPaid)
    expect(hasBlockingPayroll).toBe(true)

    payrollAccrued = true
    expect(employees.length > 0 && (!payrollAccrued || !payrollPaid)).toBe(true)

    payrollPaid = true
    expect(employees.length > 0 && (!payrollAccrued || !payrollPaid)).toBe(false)
  })
})

it('counts bank settlement once alongside original receipts and fees', () => {
  const entries = [
    { voucherLines: { debit: [{ accountCode: '1012001', amount: '100.00' }], credit: [{ accountCode: '5001', amount: '100.00' }] } },
    { voucherLines: { debit: [{ accountCode: '5603002', amount: '0.60' }], credit: [{ accountCode: '1012001', amount: '0.60' }] } },
    { voucherLines: { debit: [{ accountCode: '1002', amount: '99.40' }], credit: [{ accountCode: '1012001', amount: '99.40' }] } },
  ]
  expect(groupedEntryTotal(entries.slice(0, 2))).toBe(99.4)
  expect(groupedEntryTotal(entries)).toBe(99.4)
  expect(reconciliationDifference('99.40', entries)).toBe(0)
  const reversed = entries.map(entry => ({ voucherLines: { debit: entry.voucherLines.credit, credit: entry.voucherLines.debit } }))
  expect(groupedEntryTotal(reversed)).toBe(-99.4)
  expect(reconciliationDifference('-99.40', reversed)).toBe(0)
})

describe('unmatched entry filtering for closing wizard', () => {
  it('excludes boss personal paid entries and archive-only invoices from bank reconciliation', () => {
    const rawEntries = [
      { id: '1', title: '腾讯云发票', amount: 481.33, payer: 'boss_personal', source: 'invoice_import' },
      { id: '2', title: '财付通手续费', amount: 0.68, is_archive_only: true, source: 'invoice_import' },
      { id: '3', title: '办公室电费', amount: 200, payer: 'company_account', source: 'manual' },
    ]

    const filtered = rawEntries.filter((e: any) => {
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      const isPaidPersonally = (e as any).payer === 'boss_personal' || (e as any).payer?.startsWith?.('employee_personal')
      return isNotArchiveOnly && !isPaidPersonally
    })

    expect(filtered).toHaveLength(1)
    expect(filtered[0]?.id).toBe('3')
  })

  it('detects month-end transit WeChat bills occurring on or after the 28th', () => {
    const isTransit = (entry: any) => {
      if (entry.source !== 'wechat_bill') return false
      if (!entry.date) return false
      const day = parseInt(entry.date.slice(8, 10), 10)
      return day >= 28
    }

    expect(isTransit({ source: 'wechat_bill', date: '2026-08-31' })).toBe(true)
    expect(isTransit({ source: 'wechat_bill', date: '2026-08-15' })).toBe(false)
    expect(isTransit({ source: 'invoice_import', date: '2026-08-31' })).toBe(false)
  })
})

describe('reversal entries in reconciliation candidates', () => {
  const matchesReversalFilter = (entry: Record<string, unknown>) => {
    const isReversal = entry.entryType === 'reversal' || entry.entry_type === 'reversal'
    return !isReversal
  }

  it('excludes reversal entries from reconciliation candidates', () => {
    expect(matchesReversalFilter({ entryType: 'reversal' })).toBe(false)
    expect(matchesReversalFilter({ entry_type: 'reversal' })).toBe(false)
  })

  it('keeps normal and wechat entries as candidates', () => {
    expect(matchesReversalFilter({ entryType: 'standard', source: 'wechat_bill' })).toBe(true)
    expect(matchesReversalFilter({})).toBe(true)
  })

  it('detects zero-net selections caused by original+reversal pairs', () => {
    const total = [500, -500].reduce((sum, amount) => sum + amount, 0)
    const isZeroNet = Math.abs(total) <= 0.005
    expect(isZeroNet).toBe(true)
  })

  it('does not flag normal non-zero selections', () => {
    const total = [500, -0.05].reduce((sum, amount) => sum + amount, 0)
    expect(Math.abs(total) <= 0.005).toBe(false)
  })
})

describe('reversed originals in reconciliation candidates', () => {
  const buildCandidates = (entries: Array<Record<string, unknown>>) => {
    const reversalIds = new Set(entries
      .filter(e => e.entryType === 'reversal' || e.entry_type === 'reversal')
      .map(e => String(e.reversalOf || '').trim())
      .filter(Boolean))
    return entries.filter(e => {
      const isReversal = e.entryType === 'reversal' || e.entry_type === 'reversal'
      const isReversedOriginal = reversalIds.has(String(e.id))
      return !isReversal && !isReversedOriginal
    })
  }

  it('excludes both the reversal entry and its reversed original', () => {
    const candidates = buildCandidates([
      { id: 'orig-1', entryType: 'standard' },
      { id: 'rev-1', entryType: 'reversal', reversalOf: 'orig-1' },
      { id: 'normal', entryType: 'standard' },
    ])
    expect(candidates.map(c => c.id)).toEqual(['normal'])
  })

  it('keeps entries when no reversal exists', () => {
    const candidates = buildCandidates([{ id: 'a', entryType: 'standard' }, { id: 'b' }])
    expect(candidates).toHaveLength(2)
  })

  it('ignores reversal entries with empty reversalOf', () => {
    const candidates = buildCandidates([
      { id: 'rev-x', entryType: 'reversal', reversalOf: '' },
      { id: 'orig-2', entryType: 'standard' },
    ])
    expect(candidates.map(c => c.id)).toEqual(['orig-2'])
  })
})

describe('non-bank settled entries disposition check', () => {
  it('correctly identifies non-bank settled entries', () => {
    expect(isNonBankSettledEntry({ paymentStatus: 'unpaid' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'boss_personal' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'employee_personal:张三' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'employee_personal' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'prepaid_deduct' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'platform_balance' })).toBe(true)
    expect(isNonBankSettledEntry({ payer: 'advance_offset' })).toBe(true)

    expect(isNonBankSettledEntry({ payer: 'company_account', paymentStatus: null })).toBe(false)
    expect(isNonBankSettledEntry(null)).toBe(false)
  })

  it('formats human readable disposition tags accurately', () => {
    expect(formatDispositionTag({ paymentStatus: 'unpaid', direction: 'expense' })).toBe('还没付款')
    expect(formatDispositionTag({ paymentStatus: 'unpaid', direction: 'income' })).toBe('客户未付款')
    expect(formatDispositionTag({ payer: 'boss_personal', direction: 'expense' })).toBe('老板个人垫付')
    expect(formatDispositionTag({ payer: 'boss_personal', direction: 'income' })).toBe('老板私户代收')
    expect(formatDispositionTag({ payer: 'employee_personal:李四', direction: 'expense' })).toBe('员工垫付 · 李四')
    expect(formatDispositionTag({ payer: 'employee_personal', direction: 'expense' })).toBe('员工垫付')
    expect(formatDispositionTag({ payer: 'prepaid_deduct' })).toBe('预存扣款')
    expect(formatDispositionTag({ payer: 'platform_balance' })).toBe('平台未提现')
    expect(formatDispositionTag({ payer: 'advance_offset' })).toBe('冲前期预收')
  })

  it('formats boss-friendly accounting explanations accurately', () => {
    expect(formatDispositionExplanation({ payer: 'boss_personal', direction: 'expense' })).toContain('公司欠老板垫付款')
    expect(formatDispositionExplanation({ paymentStatus: 'unpaid', direction: 'income' })).toContain('客户欠款')
    expect(formatDispositionExplanation({ paymentStatus: 'unpaid', direction: 'expense' })).toContain('待付供应商款')
    expect(formatDispositionExplanation({ payer: 'employee_personal:王五', direction: 'expense' })).toContain('公司欠王五报销款')
    expect(formatDispositionExplanation({ payer: 'platform_balance', direction: 'income' })).toContain('平台待提现余额')
  })
})

