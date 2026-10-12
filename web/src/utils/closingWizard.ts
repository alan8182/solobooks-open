export interface ClosingWizardEntryLike {
  id?: string
  direction?: string
  amount?: number | string | null
  billingStatus?: string | null
  voucherLines?: unknown
}

export interface ClosingWizardStatementLike {
  matched_entry_id?: string | null
  matched_entry_ids?: string[] | null
}

export function signedInvoiceAmount(invoice?: ClosingWizardEntryLike | null): number {
  if (!invoice) return 0
  const rawAmount = Number(invoice.amount) || 0
  if (rawAmount < 0) return rawAmount
  const amount = Math.abs(rawAmount)
  if (invoice.direction === 'expense') return -amount
  if (invoice.direction === 'income') return amount
  if (invoice.direction === 'owner_transfer')
    return Number(invoice.amount) || 0
  return Number(invoice.amount) || 0
}

export function groupedEntryTotal(entries: ClosingWizardEntryLike[]): number {
  // Match the server: prefer the group's actual bank effect. Summing each
  // entry's bank-or-clearing effect counts the same receipt twice after settlement.
  const bankCents = entries.reduce((sum, entry) => sum + Math.round(100 * (
    entry.voucherLines ? voucherAccountEffect(entry, '1002') : signedInvoiceAmount(entry)
  )), 0)
  if (bankCents !== 0) return bankCents / 100
  return entries.reduce((sum, entry) => sum + Math.round(
    100 * voucherAccountEffect(entry, '1012'),
  ), 0) / 100
}

function voucherAccountEffect(entry: ClosingWizardEntryLike, accountCode: string): number {
  const voucher = entry.voucherLines as {
    debit?: Array<{ accountCode?: string; amount?: number | string | null }>
    credit?: Array<{ accountCode?: string; amount?: number | string | null }>
  } | null | undefined
  if (!voucher || typeof voucher !== 'object') return 0
  const debitLines = Array.isArray(voucher.debit) ? voucher.debit : []
  const creditLines = Array.isArray(voucher.credit) ? voucher.credit : []
  const debit = debitLines.reduce((sum, line) => (
    line.accountCode === accountCode ? sum + (Number(line.amount) || 0) : sum
  ), 0)
  const credit = creditLines.reduce((sum, line) => (
    line.accountCode === accountCode ? sum + (Number(line.amount) || 0) : sum
  ), 0)
  return debit - credit
}

export function reconciliationEntryEffect(entry: ClosingWizardEntryLike): number {
  if (!entry.voucherLines) return signedInvoiceAmount(entry)
  const bankEffect = voucherAccountEffect(entry, '1002')
  if (Math.abs(bankEffect) > 1e-9) return bankEffect
  const settlementEffect = voucherAccountEffect(entry, '1012')
  if (Math.abs(settlementEffect) > 1e-9) return settlementEffect
  return bankEffect
}

export function reconciliationDifference(
  statementAmount: number | string | null | undefined,
  entries: ClosingWizardEntryLike[],
): number {
  return Number(((Number(statementAmount) || 0) - groupedEntryTotal(entries)).toFixed(2))
}

export function getStatementMatchedEntryIds(statement?: ClosingWizardStatementLike | null): string[] {
  if (!statement) return []
  const ids: string[] = []
  for (const rawId of statement.matched_entry_ids || []) {
    const entryId = String(rawId || '').trim()
    if (entryId && !ids.includes(entryId)) ids.push(entryId)
  }
  const legacyId = String(statement.matched_entry_id || '').trim()
  if (legacyId && !ids.includes(legacyId)) ids.unshift(legacyId)
  return ids
}

export function amountClassForDirection(direction?: string, amount?: number | string | null): 'income' | 'expense' {
  if (direction === 'income') return 'income'
  if (direction === 'expense') return 'expense'
  return (Number(amount) || 0) >= 0 ? 'income' : 'expense'
}

export function isReconciledEntry(entry?: ClosingWizardEntryLike | null): boolean {
  return entry?.billingStatus === 'reconciled'
}

export function getMatchedEntryIdSets(
  statements: ClosingWizardStatementLike[],
  entries: ClosingWizardEntryLike[],
): { linkedEntryIds: Set<string>; confirmedEntryIds: Set<string> } {
  const entriesById = new Map(entries.map((entry) => [String(entry.id || ''), entry]))
  const linkedEntryIds = new Set<string>()
  const confirmedEntryIds = new Set<string>()

  for (const statement of statements) {
    for (const entryId of getStatementMatchedEntryIds(statement)) {
      linkedEntryIds.add(entryId)
      if (isReconciledEntry(entriesById.get(entryId))) {
        confirmedEntryIds.add(entryId)
      }
    }
  }

  return { linkedEntryIds, confirmedEntryIds }
}

export function isDirectionCompatible(direction?: string | null, amount?: number | null): boolean {
  if (!direction) return true
  const amt = Number(amount) || 0
  const bankDir = amt >= 0 ? 'income' : 'expense'
  if (direction === 'owner_transfer') return true
  return direction === bankDir
}

export function buildSuggestedMatchPair(suggestion: any, entries: ClosingWizardEntryLike[]) {
  if (suggestion.type !== 'match') return null
  const ids = getStatementMatchedEntryIds({
    matched_entry_id: suggestion.matchedEntryId,
    matched_entry_ids: suggestion.matchedEntryIds,
  })
  if (ids.length === 0) return null
  const suggestedEntries = suggestion.matchedEntries || [suggestion.matchedEntry].filter(Boolean)
  const pairEntries = ids.map((id) => entries.find((entry) => entry.id === id)
    || suggestedEntries.find((entry: any) => entry.id === id))
  if (pairEntries.some((entry) => !entry)) return null
  const amount = Number(suggestion.statement?.amount) || 0
  if (ids.length > 1) {
    if (Math.abs(reconciliationDifference(amount, pairEntries)) > 0.01) return null
  } else if (!isDirectionCompatible(pairEntries[0].direction, amount)) {
    return null
  }
  return {
    id: suggestion.statementId,
    statementId: suggestion.statementId,
    matchedEntryId: ids[0],
    matchedEntryIds: ids,
    entries: pairEntries,
    invoice: pairEntries[0],
    bank: {
      id: suggestion.statementId,
      title: suggestion.statement?.description || '未命名流水',
      amount,
    },
    confidence: typeof suggestion.confidence === 'string' ? suggestion.confidence
      : `${Math.round((suggestion.confidence || 0) * 100)}%`,
    reason: suggestion.reason || '金额与日期匹配',
    persisted: false,
    status: 'pending',
  }
}
