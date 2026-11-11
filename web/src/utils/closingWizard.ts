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
  source?: string | null
  status?: string | null
  voucherStatus?: string | null
  entryType?: string | null
  entry_type?: string | null
  reversalOf?: string | null
  invoiceNumber?: string | null
  title?: string | null
  occurDate?: string | null
}

export interface ClosingWizardStatementLike {
  matched_entry_id?: string | null
  matched_entry_ids?: string[] | null
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
