/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { formatMoney } from './format'

export interface ClosingWizardEntryLike {
  id?: string
  direction?: string
  amount?: number | string | null
  billingStatus?: string | null
  paymentDate?: string | null
  category?: string | null
  voucherLines?: unknown
  payer?: string | null
  paymentStatus?: string | null
  source?: string | null
  status?: string | null
  voucherStatus?: string | null
  entryType?: string | null
  entry_type?: string | null
  reversalOf?: string | null
  invoiceNumber?: string | null
  title?: string | null
  occurDate?: string | null
  buyer_name?: string | null
  buyerName?: string | null
}

export interface AdvanceBalanceEntryLike {
  id: string
  sourceId?: string | null
  voucherNo?: string
  date: string
  period: string
  summary: string
  category: string
  amount: string
  direction: 'advance' | 'reimbursement'
  isCurrentMonth: boolean
}

export interface AdvanceBalancePersonLike {
  payer: string
  kind: 'boss' | 'employee'
  name: string
  opening: string | number
  advance: string | number
  reimbursed: string | number
  closing: string | number
  count: number
  lastDate: string
  entries?: AdvanceBalanceEntryLike[]
}

export interface AdvanceBalancesSnapshot {
  month?: string
  items: AdvanceBalancePersonLike[]
  totals?: {
    opening: string | number
    advance: string | number
    reimbursed: string | number
    closing: string | number
  }
}

export interface BossLedgerSummary {
  customerOwes: number
  customerCount: number
  companyOwesSupplier: number
  supplierCount: number
  companyOwesBossAndEmp: number
  bossOnly: number
  empOnly: number
  advanceOpening: number
  advanceMonthOccurred: number
  advanceMonthReimbursed: number
  fromAdvanceLedger: boolean
  hasAny: boolean
}

export interface ClosingWizardStatementLike {
  matched_entry_id?: string | null
  matched_entry_ids?: string[] | null
  description?: string | null
  transaction_date?: string | null
}

/** 研发支出分类名：结账第二步据此决定是否就地展开研发归集。 */
export const RD_EXPENSE_CATEGORY = '研发支出-费用化支出'

/** 研发项目参与归集的最小形状（与后端 rd_projects 的字段保持一致）。 */
export interface RdProjectLike {
  status?: string | null
  startDate?: string | null
  endDate?: string | null
}

/** 与后端 _add_lines 的校验口径一致：只有 active/completed 的项目可以归集费用。 */
export const RD_PROJECT_ACTIVE_STATUSES = ['active', 'completed']

/**
 * 研发项目在指定月份是否可用于归集。

 * 后端 _add_lines 会校验记账日期落在 [start_date, end_date] 内，否则报
 * RD_PROJECT_DATE_MISMATCH。前端提前按月过滤，避免常驻入口点下去才报错；
 * 口径比后端更严的那部分（日期）必须收敛在这里，防止两处漂移。
 */
export function isEffectiveRdProject(
  project?: RdProjectLike | null,
  month?: string | null,
): boolean {
  if (!project) return false
  if (!RD_PROJECT_ACTIVE_STATUSES.includes(String(project.status || ''))) return false
  if (!month) return true
  const [yearText, monthText] = String(month).split('-')
  const year = Number(yearText)
  const monthIndex = Number(monthText)
  if (!year || !monthIndex) return true
  const monthStart = `${yearText}-${String(monthIndex).padStart(2, '0')}-01`
  const lastDay = new Date(Date.UTC(year, monthIndex, 0)).getUTCDate()
  const monthEnd = `${yearText}-${String(monthIndex).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`
  const startDate = project.startDate ? String(project.startDate).slice(0, 10) : ''
  const endDate = project.endDate ? String(project.endDate).slice(0, 10) : ''
  if (startDate && startDate > monthEnd) return false
  if (endDate && endDate < monthStart) return false
  return true
}

/** 账号内有任意可用于该月份的研发项目时，结账第二步常驻研发归集入口。 */
export function hasEffectiveRdProjects(
  projects?: RdProjectLike[] | null,
  month?: string | null,
): boolean {
  return (projects || []).some((project) => isEffectiveRdProject(project, month))
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

// ── 发票销方 ↔ 流水对手方亲和 ────────────────────────────────────────────────
// 与后端 services/bank_service.py 的 seller_affinity 同口径：金额+日期之外的
// 第三道配对证据。「疑似对应」提示不能只靠金额巧合（社保金库 vs 科技公司发票）。

function affinityClean(text: string): string {
  return String(text || '').replace(/[^\w\u4e00-\u9fa5]/g, '')
}

function hanSet(text: string): Set<string> {
  return new Set(String(text || '').match(/[\u4e00-\u9fa5]/g) || [])
}

/** 流水描述里的结构化对手方：「… (对方: xxx)」中的 xxx；无则空串。 */
export function statementCounterpartyOf(description?: string | null): string {
  const match = String(description || '').match(/[(（]对方[:：]\s*([^)）]+)[)）]/)
  return (match?.[1] || '').trim()
}

/** 发票类账目的销方名：标题「发票-」前缀之后的部分（导入即以此生成标题）。 */
export function entrySellerName(entry?: ClosingWizardEntryLike | null): string {
  const title = String(entry?.title || '')
  return title.includes('发票-') ? title.split('发票-').pop()!.trim() : ''
}

/** 发票类账目的对手方名：销项发票（income）取购方，进项发票（expense）取销方。 */
function entryInvoiceCounterpartyName(entry?: ClosingWizardEntryLike | null): string {
  if (entry?.direction === 'income') {
    return String(entry.buyer_name || entry.buyerName || '').trim()
  }
  return entrySellerName(entry)
}

/**
 * 发票对手方（进项取销方、销项取购方）与流水是否相关。任一满足即相关：
 * - 流水「对方」与发票对手方互相包含（≥2 字）
 * - 去掉对方段后的描述与发票对手方有足够汉字交集：报销场景（描述含「报销/垫付」）
 *   商户名常只有 2 字品牌（百度/智谱），阈值放宽到 2；非报销场景保持 ≥3
 * - 清洗后整段互相包含（≥6 字）
 * 非发票类账目（标题无「发票-」）或缺失购方名的销项发票无从判断对手方，返回 true 走原有规则。
 */
export function statementSellerAffinity(
  statement?: ClosingWizardStatementLike | null,
  entry?: ClosingWizardEntryLike | null,
): boolean {
  const seller = affinityClean(entryInvoiceCounterpartyName(entry))
  if (!seller) return true
  const desc = String(statement?.description || '')
  const counterparty = affinityClean(statementCounterpartyOf(desc))
  if (counterparty.length >= 2 && (
    counterparty === seller || counterparty.includes(seller) || seller.includes(counterparty)
  )) return true
  const descBody = affinityClean(desc.replace(/[(（]对方[:：][^)）]+[)）]/g, ''))
  const isReimbursement = descBody.includes('报销') || descBody.includes('垫付')
  const threshold = isReimbursement ? 2 : 3
  const common = [...hanSet(descBody)].filter((ch) => hanSet(seller).has(ch)).length
  if (common >= threshold) return true
  return descBody.length >= 6 && (descBody.includes(seller) || seller.includes(descBody))
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

/** 判断是否为银行核销生成的垫付报销还款凭证（属于已发生的还款动作，非未报销垫付单据）。 */
export function isReimbursementSettlementEntry(entry?: ClosingWizardEntryLike | null): boolean {
  if (!entry) return false
  if (String(entry.id || '').startsWith('reim-')) return true
  const category = String(entry.category || '').trim()
  return entry.direction === 'owner_transfer' && (category === '股东垫付报销' || category === '员工垫付报销')
}

/**
 * 判断单据是否已确认为非公户银行存款结算（包括个人垫付、未付款挂账、预存扣除、平台未提现等）。
 * 此类单据不通过公司银行公户进出，无需且无法匹配公户银行流水。
 */
export function isNonBankSettledEntry(entry?: ClosingWizardEntryLike | null): boolean {
  if (!entry || isReimbursementSettlementEntry(entry)) return false
  const payer = String((entry as any).payer || '')
  const paymentStatus = String((entry as any).paymentStatus || '')
  if (paymentStatus === 'unpaid') return true
  if (payer === 'boss_personal' || payer.startsWith('employee_personal')) return true
  if (['prepaid_deduct', 'platform_balance', 'advance_offset'].includes(payer)) return true
  return false
}

/** 截至目标月份是否已通过公户收付/报销核销结清；若在更晚月份才核销，回看目标月份时仍算未结清。 */
export function isSettledAsOfMonth(
  entry?: ClosingWizardEntryLike | null,
  targetMonth?: string | null,
): boolean {
  if (!entry) return false
  if (!isReconciledEntry(entry) && (entry as any).billing_status !== 'reconciled') return false
  const payMonth = String(entry.paymentDate || (entry as any).payment_date || '').slice(0, 7)
  if (payMonth && targetMonth && payMonth > targetMonth) return false
  return true
}

/** 截至目标月份仍未报销的个人垫付支出单据。 */
export function isUnreimbursedPersonalAdvanceEntry(
  entry?: ClosingWizardEntryLike | null,
  targetMonth?: string | null,
): boolean {
  if (!entry) return false
  if ((entry as any).is_archive_only || (entry as any).isArchiveOnly) return false
  if (isReversalLikeEntry(entry) || isReimbursementSettlementEntry(entry)) return false
  if (entry.direction !== 'expense') return false
  if (String(entry.paymentStatus || '') === 'unpaid') return false
  const payer = String(entry.payer || '')
  if (payer !== 'boss_personal' && !payer.startsWith('employee_personal')) return false
  return !isSettledAsOfMonth(entry, targetMonth)
}

/**
 * 老板往来小账本汇总（方案 A）：
 * - 客户欠款 / 待付供应商款：从当月已确认挂账且截至当期未核销的单据汇总
 * - 公司欠老板/员工垫付款：优先以 2241 垫付台账接口期末未报余额为权威真值，并叠加尚未过账的当期垫付草稿
 */
export function computeBossLedgerSummary(
  confirmedEntries: ClosingWizardEntryLike[],
  advanceBalances: AdvanceBalancesSnapshot | null,
  targetMonth?: string | null,
): BossLedgerSummary {
  let customerOwesCents = 0
  let customerCount = 0
  let supplierOwesCents = 0
  let supplierCount = 0
  let bossCents = 0
  let empCents = 0

  for (const item of confirmedEntries) {
    const amtCents = Math.round(Math.abs(Number(item.amount) || 0) * 100)
    const status = String(item.paymentStatus || '')
    if (status === 'unpaid' && !isSettledAsOfMonth(item, targetMonth)) {
      if (item.direction === 'income') {
        customerOwesCents += amtCents
        customerCount += 1
      } else if (item.direction === 'expense') {
        supplierOwesCents += amtCents
        supplierCount += 1
      }
    }
  }

  let advanceOpening = 0
  let advanceMonthOccurred = 0
  let advanceMonthReimbursed = 0
  const fromAdvanceLedger = Boolean(advanceBalances && Array.isArray(advanceBalances.items))

  if (fromAdvanceLedger && advanceBalances) {
    const postedIds = new Set<string>()
    for (const person of advanceBalances.items) {
      const closingCents = Math.max(0, Math.round((Number(person.closing) || 0) * 100))
      if (person.kind === 'boss') {
        bossCents += closingCents
      } else {
        empCents += closingCents
      }
      for (const e of person.entries || []) {
        const eid = String(e.id || '').trim()
        const sid = String(e.sourceId || '').trim()
        if (eid) postedIds.add(eid)
        if (sid) postedIds.add(sid)
      }
    }
    advanceOpening = Math.round((Number(advanceBalances.totals?.opening) || 0) * 100) / 100
    advanceMonthOccurred = Math.round((Number(advanceBalances.totals?.advance) || 0) * 100) / 100
    advanceMonthReimbursed = Math.round((Number(advanceBalances.totals?.reimbursed) || 0) * 100) / 100

    for (const item of confirmedEntries) {
      const voucherStatus = String(item.voucherStatus || item.status || '')
      const itemId = String(item.id || '').trim()
      if (
        voucherStatus !== 'posted'
        && (!itemId || !postedIds.has(itemId))
        && isUnreimbursedPersonalAdvanceEntry(item, targetMonth)
      ) {
        const amtCents = Math.round(Math.abs(Number(item.amount) || 0) * 100)
        if (String(item.payer || '') === 'boss_personal') {
          bossCents += amtCents
        } else {
          empCents += amtCents
        }
      }
    }
  } else {
    for (const item of confirmedEntries) {
      if (!isUnreimbursedPersonalAdvanceEntry(item, targetMonth)) continue
      const amtCents = Math.round(Math.abs(Number(item.amount) || 0) * 100)
      if (String(item.payer || '') === 'boss_personal') {
        bossCents += amtCents
      } else {
        empCents += amtCents
      }
    }
  }

  const customerOwes = customerOwesCents / 100
  const companyOwesSupplier = supplierOwesCents / 100
  const bossOnly = bossCents / 100
  const empOnly = empCents / 100
  const companyOwesBossAndEmp = (bossCents + empCents) / 100

  return {
    customerOwes,
    customerCount,
    companyOwesSupplier,
    supplierCount,
    companyOwesBossAndEmp,
    bossOnly,
    empOnly,
    advanceOpening,
    advanceMonthOccurred,
    advanceMonthReimbursed,
    fromAdvanceLedger,
    hasAny: customerOwes > 0 || companyOwesSupplier > 0 || companyOwesBossAndEmp > 0 || advanceMonthReimbursed > 0,
  }
}

/** 冲销凭证、被冲销原凭证，以及由系统冲销后重记的凭证都不再参与人工重新选择。 */
export function isReversalLikeEntry(entry?: ClosingWizardEntryLike | null): boolean {
  if (!entry) return false
  const entryType = String(entry.entryType || entry.entry_type || '')
  const status = String(entry.voucherStatus || entry.status || '')
  return (
    entryType === 'reversal'
    || status === 'reversed'
    || Boolean(String(entry.reversalOf || '').trim())
  )
}

/**
 * 只有未过账的普通确认记录才能恢复为待确认。
 * 已过账记录必须走流水解绑或凭证冲销；银行核销生成的报销垫付款也不能在这里重复重记。
 */
export function canResetEntryDisposition(entry?: ClosingWizardEntryLike | null): boolean {
  if (!entry || isReversalLikeEntry(entry) || !isNonBankSettledEntry(entry)) return false
  const entryStatus = String(entry.voucherStatus || entry.status || '')
  if (entryStatus === 'posted') return false
  const entryId = String(entry.id || '')
  if (entryId.startsWith('reim-') || entry.source === 'bank_import') return false
  return true
}

/** 冲销凭证与被冲销原凭证都不再是「活」的发票行，只在历史链路里展示。 */
export function isLiveInvoiceEntry(entry?: ClosingWizardEntryLike | null): boolean {
  return !isReversalLikeEntry(entry)
}

/**
 * 发票去重键：优先发票号；重记凭证会丢失发票号，退化为 日期+对方+金额+方向，
 * 保证同一张发票重记前后仍收敛成一行。
 */
export function invoiceDedupKey(entry?: ClosingWizardEntryLike | null): string {
  if (!entry) return ''
  const number = String(entry.invoiceNumber || '').trim()
  if (number) return `no:${number}`
  const title = String(entry.title || '').trim().replace(/^冲销：/, '')
  return [
    'fp',
    String(entry.occurDate || '').slice(0, 10),
    String(entry.direction || ''),
    title,
    String(Number(entry.amount) || 0),
  ].join('|')
}

/** 同一去重键下挑出最可信的活凭证：有发票号 > 已过账 > 非归档 > 日期更晚。 */
export function isPreferredInvoiceEntry(
  candidate?: ClosingWizardEntryLike | null,
  current?: ClosingWizardEntryLike | null,
): boolean {
  if (!current) return true
  if (!candidate) return false
  // 活凭证永远优先于冲销链里的任何记录：重记后原凭证已作废，
  // 哪怕它仍带着发票号，也不能盖过重记出来的新凭证。
  const candidateLive = isLiveInvoiceEntry(candidate)
  const currentLive = isLiveInvoiceEntry(current)
  if (candidateLive !== currentLive) return candidateLive
  const score = (entry: ClosingWizardEntryLike) => {
    let value = 0
    if (String(entry.invoiceNumber || '').trim()) value += 8
    const status = String(entry.voucherStatus || entry.status || '')
    if (status === 'posted') value += 4
    if (!(entry as any).is_archive_only && !(entry as any).isArchiveOnly) value += 2
    return value
  }
  const candidateScore = score(candidate)
  const currentScore = score(current)
  if (candidateScore !== currentScore) return candidateScore > currentScore
  return String(candidate.occurDate || '') >= String(current.occurDate || '')
}

export interface InvoiceRowStatus {
  kind: 'archived' | 'reconciled' | 'reimbursed' | 'advance' | 'pending' | 'unmatched'
  label: string
  tagType: 'default' | 'success' | 'info' | 'warning'
}

/**
 * 发票行状态。口径互斥，按「归档 → 已对公 → 垫付报销 → 垫付待报 → 待确认 → 待核对」判定，
 * 避免同一行同时挂“老板垫付”和“已和公户对上”两个互相矛盾的状态。
 */
export function invoiceRowStatus(row?: Record<string, any> | null): InvoiceRowStatus {
  if (!row) return { kind: 'unmatched', label: '还没和公户对上', tagType: 'warning' }
  if (row.is_archive_only || row.isArchiveOnly) {
    return { kind: 'archived', label: '仅归档不入账', tagType: 'default' }
  }
  const payer = String(row.payer || '')
  const paymentStatus = String(row.paymentStatus || '')
  const isAdvance = (
    paymentStatus !== 'unpaid'
    && (
      payer === 'boss_personal'
      || payer.startsWith('employee_personal')
      || ['prepaid_deduct', 'platform_balance', 'advance_offset'].includes(payer)
    )
  )
  if (row.status === 'matched') {
    return isAdvance
      ? { kind: 'reimbursed', label: `${formatDispositionTag(row)} · 已报销`, tagType: 'success' }
      : { kind: 'reconciled', label: '已和公户对上', tagType: 'success' }
  }
  if (isAdvance) {
    return { kind: 'advance', label: formatDispositionTag(row), tagType: 'info' }
  }
  if (row.status === 'pending_confirmation') {
    return { kind: 'pending', label: '待确认', tagType: 'info' }
  }
  return { kind: 'unmatched', label: '还没和公户对上', tagType: 'warning' }
}

/** 需要用户动手的发票行：未对上公户、或已关联待确认。 */
export function invoiceNeedsAttention(row?: Record<string, any> | null): boolean {
  const status = invoiceRowStatus(row)
  return status.kind === 'unmatched' || status.kind === 'pending'
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

/** 凭证行摘要（toast 用）：「借 5602999 管理费用-其他 ¥481.33 ／ 贷 2241 其他应付款 ¥481.33」 */
export function voucherLinesSummary(
  voucherLines: { debit?: any[]; credit?: any[] } | null | undefined,
): string {
  if (!voucherLines) return ''
  const fmt = (lines: any[]) =>
    (lines || [])
      .map((l: any) => `${l.accountCode} ${l.accountName || ''} ¥${l.amount}`)
      .join(' + ')
  const debit = fmt(voucherLines.debit || [])
  const credit = fmt(voucherLines.credit || [])
  if (!debit && !credit) return ''
  return `借 ${debit || '-'} ／ 贷 ${credit || '-'}`
}

/**
 * 去向确认第二步不出现的分类：工资/社保/平台结算是系统路径，不是费用科目选择项。
 * 研发支出不在此列——账号内有生效项目时可在卡片内就地补项目+类别，
 * 由 dispositionCategoryGroups 的 rdCategoryEnabled 开关控制。
 */
export const DISPOSITION_EXCLUDED_CATEGORIES = new Set([
  '工资发放',
  '社保公积金',
  '个税代缴',
  '微信支付结算',
  '支付宝结算',
  '平台提现结算',
])

/**
 * 去向确认第二步的科目分组：按流水方向过滤、剔除系统路径分类、
 * 保留 CATEGORY_OPTIONS 的语义组头（"── 费用支出 ──"等）。
 * 财务小白只看到"这笔算什么支出/收入"的语义选项，不见底层方向轴。

 * ``rdCategoryEnabled`` 为 false（账号内没有覆盖本月的有效研发项目）时，
 * 研发分类仍然隐藏——避免用户选中后卡在「请先建项目」。
 */
export function dispositionCategoryGroups(
  options: { label: string; value: string; disabled?: boolean }[],
  direction: 'income' | 'expense',
  rdCategoryEnabled = false,
): { label: string; options: { label: string; value: string }[] }[] {
  const groups: { label: string; options: { label: string; value: string }[] }[] = []
  let current: { label: string; options: { label: string; value: string }[] } | null = null
  for (const opt of options) {
    if (opt.disabled || !opt.value || String(opt.value).startsWith('__')) {
      current = opt.label && opt.label.includes('──') ? { label: opt.label.replace(/──/g, '').trim(), options: [] } : null
      if (current) groups.push(current)
      continue
    }
    if (!String(opt.value).startsWith(`${direction}|`)) continue
    const category = String(opt.value).split('|')[1]
    if (DISPOSITION_EXCLUDED_CATEGORIES.has(category)) continue
    if (category === RD_EXPENSE_CATEGORY && !rdCategoryEnabled) continue
    if (!current) current = { label: '其他', options: [] }
    current.options.push({ label: opt.label, value: String(opt.value) })
  }
  return groups.filter((group) => group.options.length > 0)
}

/** 第二步分类选项值 → 分类名（"expense|业务招待费|5602002|..."） */
export function dispositionCategoryOf(value?: string | null): string {
  const parts = String(value || '').split('|')
  return parts.length > 1 ? parts[1] : ''
}

/**
 * 轻视觉科目副标签：分类名与会计科目名完全相同时去重（如「资本公积」→「3002」），
 * 不同时保留「科目名 + 科目代码」（如「股东投入」→「实收资本 3001」），
 * 兼顾老板可读性与伴随式财务专业感。
 */
export function formatAccountPreviewLabel(
  category?: string | null,
  account?: { name?: string | null; code?: string | null } | null,
): string {
  if (!account) return ''
  const cat = String(category || '').trim()
  const name = String(account.name || '').trim()
  const code = String(account.code || '').trim()
  if (name && name === cat) return code
  return [name, code].filter(Boolean).join(' ')
}

/** 多义项分类的白话经营含义（面向非财务老板，按业务后果区分） */
export const AMBIGUITY_CATEGORY_HINTS: Record<string, string> = {
  股东投入: '计入公司实缴注册资本（工商登记出资，不能随意原路退回）',
  资本公积: '超过注册资本的溢价投入（归属公司权益，不改注册资本）',
  股东垫付: '算股东暂借给公司，日后公司有钱时可免税原路还回个人',
  主营业务收入: '公司日常主营业务收款（计入营业收入与应税所得）',
  其他业务收入: '非主营但与经营相关的附带收入（如转租、材料转让等）',
  营业外收入: '偶发进账（如实名验证打款、政府补助、违约金等）',
  往来款: '暂付/暂收押金、保证金或验证退款，不计入利润或费用',
  其他支出: '无法归入专项的零星日常费用（直接计入当期管理费用）',
  税费缴纳: '正常缴纳税款（冲减应交增值税余额；不含罚款滞纳金）。社保/公积金、代扣个税请选下面各自的选项，按应付职工薪酬核销',
  社保公积金: '社保/公积金缴纳（企+个一并冲减已计提的应付职工薪酬；建账前的遗留会冲销期初应付）',
  个税代缴: '代扣代缴员工个税（冲减应交个人所得税 2221012，不计入公司所得税费用）',
  罚款支出: '税务或行政罚款（计入营业外支出 5711011，企业所得税不得税前扣除）',
  税收滞纳金: '逾期缴税滞纳金（计入营业外支出 5711010，企业所得税不得税前扣除）',
  所得税费用: '缴纳企业所得税（计入所得税费用 5801）',
}

export function categoryPlainHint(category?: string | null): string {
  return AMBIGUITY_CATEGORY_HINTS[String(category || '').trim()] || ''
}

const BANK_CHANNEL_WORDS = new Set([
  '汇兑',
  '入账',
  '转账',
  '网银转账',
  '跨行汇款',
  '网上银行',
  '代付',
  '代收',
  '银联',
  '收款',
  '付款',
])

export interface ParsedBankStatementDisplay {
  counterparty: string
  summary: string
  channel: string
  raw: string
}

/**
 * 清洗银行流水原始描述（如「汇兑 | 附言: 投资款 (对方: 何静谷)」）用于卡片展示：
 * 把对手方和附言前置，通道词（汇兑/入账）退为次要元信息；原始描述保留在 raw 供悬停查看。
 */
export function parseBankStatementDisplay(description?: string | null): ParsedBankStatementDisplay {
  const raw = String(description || '').trim()
  if (!raw) return { counterparty: '', summary: '', channel: '', raw: '' }

  let body = raw
  let counterparty = ''
  const partyMatch = body.match(/(?:\(|（)对方\s*[:：]\s*(.+?)(?:\)|）)\s*$/)
  if (partyMatch) {
    counterparty = partyMatch[1].trim()
    body = body.slice(0, partyMatch.index).trim()
  }

  const rawParts = body
    .split('|')
    .map((part) => part.trim())
    .filter(Boolean)

  let channel = ''
  if (rawParts.length > 1 && BANK_CHANNEL_WORDS.has(rawParts[0])) {
    channel = rawParts.shift()!
  }

  let summary = rawParts
    .map((part) => part.replace(/^附言\s*[:：]\s*/, '').trim())
    .filter(Boolean)
    .join(' · ')

  // 避免「何静谷 · 何静谷实缴出资款」重复出现对手方姓名
  if (counterparty && summary.startsWith(counterparty) && summary.length > counterparty.length) {
    const trimmed = summary.slice(counterparty.length).replace(/^[\s·\-—:：]+/, '').trim()
    if (trimmed) summary = trimmed
  }

  // 若只剩通道词且有对手方，把通道词移入 channel（如「汇兑 (对方: 何静谷)」）
  if (!channel && counterparty && BANK_CHANNEL_WORDS.has(summary)) {
    channel = summary
    summary = ''
  }

  return { counterparty, summary, channel, raw }
}

export interface MultiMatchPoolInfo {
  poolKind: 'advancement' | 'pending_invoice' | 'other'
  poolKey: string
  poolLabel: string
}

export interface MultiMatchEvaluation {
  selectedCount: number
  selectedTotal: number
  statementAbs: number
  diff: number
  isHomogeneous: boolean
  poolError: string
  amountMatches: boolean
  canConfirm: boolean
  tolerance: number
}

/**
 * 归类合并报销/多票核销候选项所属资金池，与后端 `confirm_suggestion_flow` /
 * `_try_multi_invoice_match` 保持同一口径：个人垫付不可与非垫付混选，不同垫付人不可混选。
 */
export function classifyMultiMatchCandidate(entry?: ClosingWizardEntryLike | null): MultiMatchPoolInfo | null {
  if (!entry) return null
  const payer = String(entry.payer || '').trim()
  const isPersonal = payer === 'boss_personal'
    || payer === 'employee_personal'
    || payer.startsWith('employee_personal:')
  if (isPersonal) {
    const bankEffect = entry.voucherLines ? voucherAccountEffect(entry, '1002') : 0
    if (Math.abs(bankEffect) >= 0.01) {
      return { poolKind: 'other', poolKey: 'company_bank', poolLabel: '公户账目' }
    }
    let poolLabel = '员工垫付'
    if (payer === 'boss_personal') {
      poolLabel = '老板垫付'
    } else if (payer.startsWith('employee_personal:')) {
      const empName = payer.slice('employee_personal:'.length).trim()
      poolLabel = empName ? `员工(${empName})垫付` : '员工垫付'
    }
    return {
      poolKind: 'advancement',
      poolKey: `adv:${payer}`,
      poolLabel,
    }
  }
  const voucher = entry.voucherLines as {
    debit?: Array<{ accountCode?: string }>
    credit?: Array<{ accountCode?: string }>
  } | null | undefined
  const isPendingInvoice = Boolean(voucher) && (
    (Array.isArray(voucher?.credit) && voucher.credit.some((l) => String(l.accountCode) === '2241'))
    || (Array.isArray(voucher?.debit) && voucher.debit.some((l) => String(l.accountCode) === '1122'))
  )
  if (isPendingInvoice || String(entry.source || '') === 'invoice_import') {
    return {
      poolKind: 'pending_invoice',
      poolKey: 'pending_invoice',
      poolLabel: '待付款发票',
    }
  }
  return { poolKind: 'other', poolKey: 'company_bank', poolLabel: '公户账目' }
}

/** 实时计算多票合并核销勾选的合计数、资金池一致性与差额可提交状态。 */
export function evaluateMultiMatchSelection(
  statementAmount: number | string | null | undefined,
  selectedEntries: ClosingWizardEntryLike[],
): MultiMatchEvaluation {
  const statementCents = Math.round(Math.abs(Number(statementAmount) || 0) * 100)
  const selectedCents = selectedEntries.reduce(
    (sum, item) => sum + Math.round(Math.abs(Number(item?.amount) || 0) * 100),
    0,
  )
  const diffCents = selectedCents - statementCents
  const selectedCount = selectedEntries.length
  const classified = selectedEntries
    .map((item) => classifyMultiMatchCandidate(item))
    .filter((item): item is MultiMatchPoolInfo => item !== null)
  const hasAdv = classified.some((item) => item.poolKind === 'advancement')
  const hasNonAdv = classified.some((item) => item.poolKind !== 'advancement')
  const advKeys = new Set(
    classified.filter((item) => item.poolKind === 'advancement').map((item) => item.poolKey),
  )

  let isHomogeneous = true
  let poolError = ''
  if (hasAdv && hasNonAdv) {
    isHomogeneous = false
    poolError = '个人垫付报销暂不支持与待付款发票或公户账目混合合并，请分开核销'
  } else if (advKeys.size > 1) {
    isHomogeneous = false
    poolError = '多笔垫付合并报销仅支持同一垫付人，请按垫付人分别核销'
  }

  const tolerance = hasAdv && !hasNonAdv ? 0.05 : 0.01
  const toleranceCents = Math.round(tolerance * 100)
  const amountMatches = selectedCount > 0 && Math.abs(diffCents) <= toleranceCents
  return {
    selectedCount,
    selectedTotal: selectedCents / 100,
    statementAbs: statementCents / 100,
    diff: diffCents / 100,
    isHomogeneous,
    poolError,
    amountMatches,
    canConfirm: selectedCount > 0 && isHomogeneous && amountMatches,
    tolerance,
  }
}

export interface MatchSuggestionLike {
  statementId?: string | null
  type?: string | null
  matchedEntryId?: string | null
  matchedEntryIds?: string[] | null
  invoiceReimbursement?: unknown
}

/**
 * 收集已被银行流水 `match` 建议认领的单据 ID（同时兼容 1:1 matchedEntryId 与 1:N matchedEntryIds）。
 * 供「待确认款项的单据」列表剔除重复展示，避免同一张发票在上方流水与兜底列表同时出现、诱导误操作。
 * 注意：带有 `invoiceReimbursement`（疑似个人垫付报销、尚未确认垫付人与科目）的建议不屏蔽下方发票，
 * 让老板先在下方按全站统一流程确认垫付人与用途分类，再回到上方确认报销配对。
 */
export function collectSuggestedMatchedEntryIds(
  suggestions: MatchSuggestionLike[],
  activeStatementIds?: Set<string>,
): Set<string> {
  const claimed = new Set<string>()
  for (const sug of suggestions) {
    if (!sug || sug.type !== 'match') continue
    if (sug.invoiceReimbursement) continue
    if (activeStatementIds && sug.statementId != null && !activeStatementIds.has(String(sug.statementId))) {
      continue
    }
    const ids = Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0
      ? sug.matchedEntryIds
      : [sug.matchedEntryId]
    for (const rawId of ids) {
      const id = String(rawId || '').trim()
      if (id) claimed.add(id)
    }
  }
  return claimed
}

/** 清洗发票品名：去掉「*信息技术服务*」这类税目星号前缀，超长时截断保留核心商品/服务名。 */
function cleanInvoiceItemName(raw?: string | null): string {
  const text = String(raw || '').trim()
  if (!text) return ''
  const stripped = text.replace(/(?:^|(?<=[,，;；、\s]))\*[^*]+\*\s*/g, '').trim()
  const core = (stripped || text.replace(/\*/g, '').trim())
  if (core.length <= 20) return core
  return `${core.slice(0, 20)}…`
}

/**
 * 为第二步「匹配单据（match）」卡片生成「来源 + 确认后果」白话说明，
 * 让用户在点「确认配对」前看懂原单日期、性质/品名以及点击后是否会新增报销凭证。
 */
export function formatMatchExplanation(sug: any, liveEntry?: any): string {
  if (!sug || sug.type !== 'match') return ''

  const ids = Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0
    ? sug.matchedEntryIds
    : [sug.matchedEntryId].filter(Boolean)

  if (ids.length > 1) {
    const entries = Array.isArray(sug.matchedEntries) ? sug.matchedEntries : []
    const total = entries.length > 0
      ? entries.reduce((sum: number, item: any) => sum + Math.abs(Number(item?.amount) || 0), 0)
      : Math.abs(Number(sug.statement?.amount) || 0)
    const allAdv = entries.length > 0 && entries.every((item: any) => {
      const p = String(item?.payer || '').trim()
      return Boolean(item?.isAdvancement) || p === 'boss_personal' || p === 'employee_personal' || p.startsWith('employee_personal:')
    })
    const totalText = formatMoney(total)
    return allAdv
      ? `合并匹配 ${ids.length} 笔垫付单据（合计 ${totalText}）· 确认后生成汇总报销还款凭证，不重复记费用`
      : `合并匹配 ${ids.length} 笔单据（合计 ${totalText}）· 确认后统一核销对应垫付/待付单据`
  }

  const snapshot = sug.matchedEntry || {}
  const entry = liveEntry ? { ...snapshot, ...liveEntry } : snapshot
  if (!entry || (!entry.id && entry.amount == null && !entry.occurDate && !entry.payer && !entry.source)) {
    return ''
  }

  const dateText = String(entry.occurDate || '').slice(0, 10)
  const datePrefix = dateText ? `${dateText} ` : ''
  const amountText = formatMoney(Math.abs(Number(entry.amount ?? sug.statement?.amount) || 0))
  const cleanItem = cleanInvoiceItemName(entry.item_name ?? entry.itemName)
  const payer = String(entry.payer || '').trim()
  const isPersonal = Boolean(entry.isAdvancement)
    || payer === 'boss_personal'
    || payer === 'employee_personal'
    || payer.startsWith('employee_personal:')

  if (isPersonal) {
    let label = '个人垫付单据'
    if (payer === 'boss_personal') {
      label = '老板垫付单据'
    } else if (payer.startsWith('employee_personal:')) {
      const empName = payer.slice('employee_personal:'.length).trim()
      label = empName ? `员工（${empName}）垫付单据` : '员工垫付单据'
    }
    const detailText = cleanItem ? `（品名：${cleanItem}）` : `（${amountText}）`
    return `${datePrefix}${label}${detailText}· 确认后生成报销还款凭证（冲减其他应付款），不重复记费用`
  }

  if (entry.isPendingInvoice || String(entry.source || '') === 'invoice_import') {
    const isIncome = String(entry.direction || '') === 'income'
    const label = cleanItem ? '发票' : (isIncome ? '待收款发票' : '待付款发票')
    const detailText = cleanItem ? `（品名：${cleanItem}）` : `（${amountText}）`
    const consequence = isIncome
      ? '确认后按公户收款核销该发票，不重复记收入'
      : '确认后按公户付款核销该发票，不重复记费用'
    return `${datePrefix}${label}${detailText}· ${consequence}`
  }

  return `${datePrefix}已有记账（${amountText}）· 确认后仅关联核销流水，不新增凭证`
}

const MATCH_CATEGORY_SCOPE_HINTS: Record<string, string> = {
  信息技术服务费: '适用于云服务、软件订阅、技术开发与测试等支出；如属在研研发项目，可点「修改单据」改挂研发支出',
  办公费: '适用于日常办公用品、平台认证年费、打印耗材等支出',
  咨询费: '适用于财税代理、法律、管理及专项咨询服务',
  '广告费/推广费': '适用于投放推广、宣传物料与品牌营销支出',
  差旅费: '适用于因公出差的交通、住宿与差旅补贴',
  业务招待费: '适用于商务宴请、客户礼品等招待支出',
  房租物业费: '适用于办公场地租金、物业费与基础水电支出',
  物流快递费: '适用于快递邮寄与货物运输费用',
  银行及金融手续费: '适用于银行转账手续费、支付通道服务费等',
  '研发支出-费用化支出': '用于已立项研发项目的研发阶段费用归集',
  其他支出: '当前为兜底科目，建议点「修改单据」按真实业务用途确认具体科目',
  主营业务收入: '日常主营业务开票收入，计入营业收入',
}

/**
 * 为第二步「匹配单据（match）」卡片的 [i] 提示生成简明「科目归类依据」，
 * 说清该单据为什么落在当前科目（人工确认 / 规则或 AI 依据 / 发票品名）以及该科目的适用范围。
 */
export function formatMatchCategoryBasis(sug: any, liveEntry?: any): string {
  if (!sug || sug.type !== 'match') return ''
  const ids = Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0
    ? sug.matchedEntryIds
    : [sug.matchedEntryId].filter(Boolean)
  if (ids.length > 1) return ''

  const snapshot = sug.matchedEntry || {}
  const entry = liveEntry ? { ...snapshot, ...liveEntry } : snapshot
  if (!entry || typeof entry !== 'object') return ''

  const cat = String(entry.category || '').trim()
  const rawItem = String(entry.item_name ?? entry.itemName ?? '').trim()
  const cleanItem = cleanInvoiceItemName(rawItem)
  const source = String(entry.categorySource || '').trim()
  const reason = String(entry.categoryReason || '').trim()
  if (!cat && !rawItem && !reason) return ''

  const snapshotCat = String(snapshot.category || '').trim()
  const categoryChangedLive = Boolean(liveEntry?.category && snapshotCat && String(liveEntry.category).trim() !== snapshotCat)
  const isFallbackReason = reason.includes('默认值') || reason.includes('主营业务收入兜底')

  let basis = ''
  if (source === 'manual' || categoryChangedLive) {
    basis = `已人工确认归入「${cat || '当前科目'}」${rawItem ? `（发票品名：${rawItem}）` : ''}`
  } else if (reason && !isFallbackReason) {
    const hasItemInReason = Boolean(rawItem && (reason.includes(rawItem) || (cleanItem && reason.includes(cleanItem))))
    basis = rawItem && !hasItemInReason ? `${reason}（发票品名：${rawItem}）` : reason
  } else if (rawItem) {
    basis = cat ? `按发票品名「${rawItem}」归入「${cat}」` : `发票品名「${rawItem}」`
  } else {
    basis = `当前归入「${cat}」`
  }

  const scopeHint = cat ? MATCH_CATEGORY_SCOPE_HINTS[cat] : ''
  return scopeHint ? `${basis.replace(/[；;。]+$/, '')}；${scopeHint}` : basis
}

/**
 * 提取单笔匹配单据当前的分类与会计科目文本（如「5602015 管理费用-信息技术服务费」），
 * 让用户在点「确认配对」前先一眼看到原单据将落进哪个科目。
 */
export function formatMatchAccountLabel(sug: any, liveEntry?: any): string {
  if (!sug || sug.type !== 'match') return ''
  const ids = Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0
    ? sug.matchedEntryIds
    : [sug.matchedEntryId].filter(Boolean)
  if (ids.length > 1) return ''

  const snapshot = sug.matchedEntry || {}
  const entry = liveEntry ? { ...snapshot, ...liveEntry } : snapshot
  if (!entry || typeof entry !== 'object') return ''

  const voucher = entry.voucherLines as { debit?: any[]; credit?: any[] } | undefined
  let code = String(entry.accountCode || '').trim()
  let name = String(entry.accountName || '').trim()

  if (voucher && (Array.isArray(voucher.debit) || Array.isArray(voucher.credit))) {
    const primarySide = String(entry.direction || '') === 'income' ? (voucher.credit || []) : (voucher.debit || [])
    const mainLine = primarySide.find((line) => !String(line?.accountCode || '').startsWith('2221')) || primarySide[0]
    if (mainLine) {
      code = String(mainLine.accountCode || code).trim()
      name = String(mainLine.accountName || name).trim()
    }
  }

  const cat = String(entry.category || '').trim()
  if (code && name) {
    return cat && !name.includes(cat) ? `${cat} · ${code} ${name}` : `${code} ${name}`
  }
  if (name) return name
  if (cat) return cat
  return ''
}

/** 发票支出类兜底科目：未识别出具体业务用途时落到其他支出，对账确认前需由用户拍板。 */
export function isFallbackInvoiceCategory(category?: string | null): boolean {
  const normalized = String(category || '').trim()
  return !normalized || normalized === '其他支出' || normalized === '管理费用-其他'
}
