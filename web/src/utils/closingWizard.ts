/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface ClosingWizardEntryLike {
  id?: string
  direction?: string
  amount?: number | string | null
  billingStatus?: string | null
  voucherLines?: unknown
  payer?: string | null
  paymentStatus?: string | null
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

// 展示用：发票原始金额（正常发票为正，红字发票保留负号），负号只表示真实红冲，
// 与全站金额展示口径一致。配对与差额计算仍需用 signedInvoiceAmount 的对账符号。
export function invoiceDisplayAmount(invoice?: ClosingWizardEntryLike | null): number {
  return invoice ? (Number(invoice.amount) || 0) : 0
}

export function groupedEntryTotal(entries: ClosingWizardEntryLike[]): number {
  // Match the server: prefer the group's actual bank effect. Summing each
  // entry's bank-or-clearing effect counts the same receipt twice after settlement.
  const bankCents = entries.reduce((sum, entry) => sum + Math.round(100 * (
    entry.voucherLines ? voucherAccountEffect(entry, '1002') : signedInvoiceAmount(entry)
  )), 0)
  if (bankCents !== 0) return bankCents / 100
  return entries.reduce((sum, entry) => sum + Math.round(
    100 * (voucherAccountEffect(entry, '1012001') + voucherAccountEffect(entry, '1012')),
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
  const settlementEffect = voucherAccountEffect(entry, '1012001') + voucherAccountEffect(entry, '1012')
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

/**
 * 判断单据是否已确认为非公户银行存款结算（包括个人垫付、未付款挂账、预存扣除、平台未提现等）。
 * 此类单据不通过公司银行公户进出，无需且无法匹配公户银行流水。
 */
export function isNonBankSettledEntry(entry?: ClosingWizardEntryLike | null): boolean {
  if (!entry) return false
  const payer = String((entry as any).payer || '')
  const paymentStatus = String((entry as any).paymentStatus || '')
  if (paymentStatus === 'unpaid') return true
  if (payer === 'boss_personal' || payer.startsWith('employee_personal')) return true
  if (['prepaid_deduct', 'platform_balance', 'advance_offset'].includes(payer)) return true
  return false
}

/**
 * 格式化已确认去向单据的人性化状态标签
 */
export function formatDispositionTag(entry?: ClosingWizardEntryLike | null): string {
  if (!entry) return '已确认'
  const payer = String((entry as any).payer || '')
  const paymentStatus = String((entry as any).paymentStatus || '')
  const direction = (entry as any).direction
  if (paymentStatus === 'unpaid') {
    return direction === 'income' ? '客户未付款' : '还没付款'
  }
  if (payer === 'boss_personal') {
    return direction === 'income' ? '老板私户代收' : '老板个人垫付'
  }
  if (payer.startsWith('employee_personal')) {
    const name = payer.includes(':') ? payer.split(':')[1]?.trim() : ''
    return name ? `员工垫付 · ${name}` : '员工垫付'
  }
  if (payer === 'prepaid_deduct') return '预存扣款'
  if (payer === 'platform_balance') return '平台未提现'
  if (payer === 'advance_offset') return '冲前期预收'
  return '已确认'
}

/**
 * 老板视角的人话入账结果说明（消灭“点完不知道记到哪了”的糊涂账）
 */
export function formatDispositionExplanation(entry?: ClosingWizardEntryLike | null): string {
  if (!entry) return ''
  const payer = String((entry as any).payer || '')
  const paymentStatus = String((entry as any).paymentStatus || '')
  const direction = (entry as any).direction
  if (paymentStatus === 'unpaid') {
    return direction === 'income'
      ? '计入「客户欠款（应收账款）」，后续收到客户汇款时自动核销'
      : '计入「待付供应商款（应付账款）」，后续公户付款时自动核销'
  }
  if (payer === 'boss_personal') {
    return direction === 'income'
      ? '计入「老板暂存公款」，需转回公户或抵扣公司欠老板的垫付款'
      : '计入「公司欠老板垫付款」，后续公户有钱可随时免税转回老板个人卡'
  }
  if (payer.startsWith('employee_personal')) {
    const name = payer.includes(':') ? payer.split(':')[1]?.trim() : '员工'
    return `计入「公司欠${name}报销款」，后续公户打款报销时直接冲销`
  }
  if (payer === 'prepaid_deduct') return '直接冲减前期已充值的「预付账款」余额，无需再付款'
  if (payer === 'platform_balance') return '计入「微信/支付宝等平台待提现余额」，提现到公户时核销'
  if (payer === 'advance_offset') return '直接冲减前期已收到的「客户预收款」，无需客户再打款'
  return ''
}
