import { describe, expect, it } from 'vitest'
import {
  amountClassForDirection,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  groupedEntryTotal,
  isReconciledEntry,
  reconciliationDifference,
  signedInvoiceAmount,
} from '@/utils/closingWizard'
import { sortEntriesByDateDescending } from '@/utils/entrySort'

describe('closing wizard invoice display helpers', () => {
  it('shows expense invoices as negative amounts', () => {
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

  it('uses the WeChat 1012 clearing effect for a net settlement', () => {
    const entries = [
      {
        id: 'sale', direction: 'income', amount: 59,
        voucherLines: {
          debit: [{ accountCode: '1012', amount: 59 }],
          credit: [{ accountCode: '6001', amount: 59 }],
        },
      },
      {
        id: 'fee', direction: 'expense', amount: 0.53,
        voucherLines: {
          debit: [{ accountCode: '5608', amount: 0.53 }],
          credit: [{ accountCode: '1012', amount: 0.53 }],
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
})
