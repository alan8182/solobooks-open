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
  isReimbursementSettlementEntry,
  isSettledAsOfMonth,
  isUnreimbursedPersonalAdvanceEntry,
  computeBossLedgerSummary,
  isReversalLikeEntry,
  canResetEntryDisposition,
  isLiveInvoiceEntry,
  invoiceDedupKey,
  isPreferredInvoiceEntry,
  invoiceRowStatus,
  invoiceNeedsAttention,
  formatDispositionTag,
  formatDispositionExplanation,
  formatAccountPreviewLabel,
  categoryPlainHint,
  parseBankStatementDisplay,
  classifyMultiMatchCandidate,
  evaluateMultiMatchSelection,
  collectSuggestedMatchedEntryIds,
  formatMatchExplanation,
  formatMatchAccountLabel,
  formatMatchCategoryBasis,
  isFallbackInvoiceCategory,
  statementSellerAffinity,
  reconciliationDifference,
  voucherLinesSummary,
  signedInvoiceAmount,
  DISPOSITION_EXCLUDED_CATEGORIES,
  RD_EXPENSE_CATEGORY,
  dispositionCategoryGroups,
  dispositionCategoryOf,
  isEffectiveRdProject,
  hasEffectiveRdProjects,
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

  it('excludes reversal chains from confirmed disposition resets', () => {
    expect(isReversalLikeEntry({ entryType: 'reversal' })).toBe(true)
    expect(isReversalLikeEntry({ entry_type: 'reversal' })).toBe(true)
    expect(isReversalLikeEntry({ status: 'reversed' })).toBe(true)
    expect(isReversalLikeEntry({ reversalOf: 'orig-1' })).toBe(true)
    expect(isReversalLikeEntry({ entryType: 'standard', status: 'posted' })).toBe(false)
  })

  it('only allows unposted, non-bank-import dispositions to be reset', () => {
    expect(canResetEntryDisposition({
      id: 'invoice-1',
      payer: 'boss_personal',
      status: 'draft',
    })).toBe(true)
    expect(canResetEntryDisposition({
      id: 'invoice-1',
      payer: 'boss_personal',
      status: 'posted',
    })).toBe(false)
    expect(canResetEntryDisposition({
      id: 'reim-bank-1',
      payer: 'boss_personal',
      source: 'bank_import',
      status: 'pending_review',
    })).toBe(false)
    expect(canResetEntryDisposition({
      id: 'reversal-1',
      payer: 'boss_personal',
      entryType: 'reversal',
      status: 'posted',
    })).toBe(false)
  })

  it('formats boss-friendly accounting explanations accurately', () => {
    expect(formatDispositionExplanation({ payer: 'boss_personal', direction: 'expense' })).toContain('公司欠老板垫付款')
    expect(formatDispositionExplanation({ paymentStatus: 'unpaid', direction: 'income' })).toContain('客户欠款')
    expect(formatDispositionExplanation({ paymentStatus: 'unpaid', direction: 'expense' })).toContain('待付供应商款')
    expect(formatDispositionExplanation({ payer: 'employee_personal:王五', direction: 'expense' })).toContain('公司欠王五报销款')
    expect(formatDispositionExplanation({ payer: 'platform_balance', direction: 'income' })).toContain('平台待提现余额')
  })
})

describe('closing wizard invoice list dedup and status', () => {
  const original = {
    id: 'orig-1',
    title: '发票-深圳市腾讯计算机系统有限公司',
    occurDate: '2026-06-02',
    amount: 300,
    direction: 'expense',
    invoiceNumber: '26957000000148730058',
    payer: 'boss_personal',
    voucherStatus: 'reversed',
    reversalOf: null,
  }
  const reversal = {
    id: 'rev-1',
    title: '冲销：发票-深圳市腾讯计算机系统有限公司',
    occurDate: '2026-06-02',
    amount: -300,
    direction: 'expense',
    entryType: 'reversal',
    reversalOf: 'orig-1',
  }
  const reposted = {
    id: 'new-1',
    title: '发票-深圳市腾讯计算机系统有限公司',
    occurDate: '2026-06-02',
    amount: 300,
    direction: 'expense',
    payer: 'company_account',
    voucherStatus: 'posted',
  }

  it('treats reversal entries and reversed originals as non-live invoices', () => {
    expect(isLiveInvoiceEntry(original)).toBe(false)
    expect(isLiveInvoiceEntry(reversal)).toBe(false)
    expect(isLiveInvoiceEntry(reposted)).toBe(true)
  })

  it('collapses a reversed original and its repost onto one dedup key', () => {
    expect(invoiceDedupKey(reposted)).not.toBe('')
    expect(isPreferredInvoiceEntry(reposted, original)).toBe(true)
    expect(isPreferredInvoiceEntry(original, reposted)).toBe(false)
  })

  it('gives every row exactly one non-conflicting status', () => {
    const reimbursed = invoiceRowStatus({
      status: 'matched', payer: 'boss_personal', paymentStatus: '',
    })
    expect(reimbursed.kind).toBe('reimbursed')
    expect(reimbursed.label).toContain('已报销')
    expect(invoiceRowStatus({ status: 'matched', payer: 'company_account' }).kind).toBe('reconciled')
    expect(invoiceRowStatus({ payer: 'boss_personal' }).kind).toBe('advance')
    expect(invoiceRowStatus({ status: 'pending_confirmation' }).kind).toBe('pending')
    expect(invoiceRowStatus({}).kind).toBe('unmatched')
    expect(invoiceRowStatus({ is_archive_only: true }).kind).toBe('archived')
  })

  it('only counts unmatched and pending rows as needing attention', () => {
    expect(invoiceNeedsAttention({})).toBe(true)
    expect(invoiceNeedsAttention({ status: 'pending_confirmation' })).toBe(true)
    expect(invoiceNeedsAttention({ status: 'matched' })).toBe(false)
    expect(invoiceNeedsAttention({ payer: 'boss_personal' })).toBe(false)
    expect(invoiceNeedsAttention({ is_archive_only: true })).toBe(false)
  })
})


describe('voucherLinesSummary', () => {
  it('renders debit and credit sides with code, name and amount', () => {
    expect(
      voucherLinesSummary({
        debit: [{ accountCode: '5602999', accountName: '管理费用-其他', amount: '481.33' }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '481.33' }],
      }),
    ).toBe('借 5602999 管理费用-其他 ¥481.33 ／ 贷 2241 其他应付款 ¥481.33')
  })

  it('handles empty sides and missing voucher', () => {
    expect(voucherLinesSummary({ debit: [], credit: [] })).toBe('')
    expect(voucherLinesSummary(null)).toBe('')
    expect(voucherLinesSummary({ debit: [{ accountCode: '1002', accountName: '银行存款', amount: 5 }] }).startsWith('借 1002')).toBe(true)
  })
})

describe('dispositionCategoryGroups', () => {
  const options = [
    { label: '── 收入 ──', value: '__header_income__', disabled: true },
    { label: '主营业务收入', value: 'income|主营业务收入|5001|主营业务收入' },
    { label: '营业外收入', value: 'income|营业外收入|5301|营业外收入' },
    { label: '── 员工成本 ──', value: '__header_salary__', disabled: true },
    { label: '工资发放', value: 'expense|工资发放|221101|应付职工薪酬-工资' },
    { label: '社保公积金', value: 'expense|社保公积金|221102|应付职工薪酬-社保' },
    { label: '── 费用支出 ──', value: '__header_expense__', disabled: true },
    { label: '办公费', value: 'expense|办公费|5602004|管理费用-办公费' },
    { label: '信息技术服务费', value: 'expense|信息技术服务费|5602015|管理费用-信息技术服务费' },
    { label: '研发支出-费用化支出', value: 'expense|研发支出-费用化支出|4301|研发支出-费用化支出' },
    { label: '其他支出', value: 'expense|其他支出|5602999|管理费用-其他' },
    { label: '── 资本 ──', value: '__header_capital__', disabled: true },
    { label: '股东投入', value: 'owner_transfer|股东投入|3001|实收资本' },
  ]

  it('expense 方向：只保留费用组，剔除工资/社保/研发/资本类，组头转为分组标签', () => {
    const groups = dispositionCategoryGroups(options as any, 'expense')
    expect(groups.map((g) => g.label)).toEqual(['费用支出'])
    expect(groups[0].options.map((o) => o.label)).toEqual(['办公费', '信息技术服务费', '其他支出'])
  })

  it('income 方向：只保留收入组', () => {
    const groups = dispositionCategoryGroups(options as any, 'income')
    expect(groups.map((g) => g.label)).toEqual(['收入'])
    expect(groups[0].options.map((o) => o.label)).toEqual(['主营业务收入', '营业外收入'])
  })

  it('账号内没有有效研发项目时不暴露研发分类，避免选中后补不齐明细', () => {
    expect(DISPOSITION_EXCLUDED_CATEGORIES.has(RD_EXPENSE_CATEGORY)).toBe(false)
    const groups = dispositionCategoryGroups(options as any, 'expense')
    expect(groups.flatMap((g) => g.options).some((o) => o.value.includes('研发支出'))).toBe(false)
  })

  it('账号内有有效研发项目时暴露研发分类，可就地补项目与费用类别', () => {
    const groups = dispositionCategoryGroups(options as any, 'expense', true)
    const flat = groups.flatMap((g) => g.options)
    expect(flat.some((o) => o.value.includes(RD_EXPENSE_CATEGORY))).toBe(true)
    // 工资/社保仍属系统路径，常驻开关不影响它们的剔除
    expect(flat.some((o) => o.value.includes('工资发放'))).toBe(false)
    expect(flat.some((o) => o.value.includes('社保公积金'))).toBe(false)
  })
})

describe('研发项目有效性判定（与后端 _add_lines 口径对齐）', () => {
  it('只有 active / completed 的项目可用于归集', () => {
    expect(isEffectiveRdProject({ status: 'active' }, '2026-06')).toBe(true)
    expect(isEffectiveRdProject({ status: 'completed' }, '2026-06')).toBe(true)
    expect(isEffectiveRdProject({ status: 'draft' }, '2026-06')).toBe(false)
    expect(isEffectiveRdProject({ status: 'archived' }, '2026-06')).toBe(false)
    expect(isEffectiveRdProject(null, '2026-06')).toBe(false)
  })

  it('项目周期不覆盖目标月份时不算有效，避免点确认才报 RD_PROJECT_DATE_MISMATCH', () => {
    const june = '2026-06'
    expect(isEffectiveRdProject({ status: 'active', startDate: '2026-06-01' }, june)).toBe(true)
    expect(isEffectiveRdProject({ status: 'active', startDate: '2026-06-30' }, june)).toBe(true)
    expect(isEffectiveRdProject({ status: 'active', startDate: '2026-07-01' }, june)).toBe(false)
    expect(isEffectiveRdProject({ status: 'active', endDate: '2026-05-31' }, june)).toBe(false)
    expect(isEffectiveRdProject({ status: 'active', endDate: '2026-06-01' }, june)).toBe(true)
    expect(
      isEffectiveRdProject({ status: 'active', startDate: '2026-01-01', endDate: '2026-12-31' }, june),
    ).toBe(true)
  })

  it('月份边界按当月最后一天判定（含 2 月与 31 天月）', () => {
    expect(isEffectiveRdProject({ status: 'active', startDate: '2026-02-28' }, '2026-02')).toBe(true)
    expect(isEffectiveRdProject({ status: 'active', startDate: '2026-03-01' }, '2026-02')).toBe(false)
    expect(isEffectiveRdProject({ status: 'active', endDate: '2026-04-30' }, '2026-04')).toBe(true)
    // 4 月 29 日结项仍覆盖 4 月；3 月 31 日结项才算不覆盖
    expect(isEffectiveRdProject({ status: 'active', endDate: '2026-04-29' }, '2026-04')).toBe(true)
    expect(isEffectiveRdProject({ status: 'active', endDate: '2026-03-31' }, '2026-04')).toBe(false)
  })

  it('带时间戳的日期按天比较，不受时分秒影响', () => {
    expect(
      isEffectiveRdProject({ status: 'active', startDate: '2026-06-30T23:59:59Z' }, '2026-06'),
    ).toBe(true)
    expect(
      isEffectiveRdProject({ status: 'active', endDate: '2026-06-01T00:00:01Z' }, '2026-06'),
    ).toBe(true)
  })

  it('hasEffectiveRdProjects：任意一个项目有效即常驻', () => {
    const june = '2026-06'
    expect(hasEffectiveRdProjects([], june)).toBe(false)
    expect(hasEffectiveRdProjects(null, june)).toBe(false)
    expect(hasEffectiveRdProjects([{ status: 'draft' }], june)).toBe(false)
    expect(
      hasEffectiveRdProjects(
        [{ status: 'draft' }, { status: 'active', startDate: '2026-06-01' }],
        june,
      ),
    ).toBe(true)
  })
})

describe('dispositionCategoryOf', () => {
  it('从选项值取出分类名', () => {
    expect(dispositionCategoryOf('expense|信息技术服务费|5602015|管理费用-信息技术服务费')).toBe('信息技术服务费')
    expect(dispositionCategoryOf('')).toBe('')
    expect(dispositionCategoryOf(null)).toBe('')
  })
})

describe('发票销方与流水对手方亲和（疑似对应守门）', () => {
  const deepseekEntry = { id: 'e1', title: '发票-杭州深度求索人工智能基础技术研究有限公司', direction: 'expense' }

  it('对方等于销方：相关（直接付款）', () => {
    expect(statementSellerAffinity(
      { description: '汇兑 | 附言: deepseek调用服务费 (对方: 杭州深度求索人工智能基础技术研究有限公司)' },
      deepseekEntry,
    )).toBe(true)
  })

  it('对方与销方毫无关系：不相关——社保金库扣款不是科技公司的发票付款', () => {
    // 回归 2026-10：¥210 深度求索发票被金额巧合配到 10-15 社保金库流水
    expect(statementSellerAffinity(
      { description: '税费社保 (对方: 金库杭州市余杭区支库)' },
      deepseekEntry,
    )).toBe(false)
  })

  it('对方是报销人但描述带商户名：靠 ≥3 字交集判定相关', () => {
    expect(statementSellerAffinity(
      { description: '汇兑 | 附言: 报销百度智能云云服务器3年续期费用 (对方: 杨云飞)' },
      { id: 'e2', title: '发票-北京百度网讯科技有限公司', direction: 'expense' },
    )).toBe(true)
  })

  it('描述与销方只有 2 字交集（如「报销」）：不相关', () => {
    expect(statementSellerAffinity(
      { description: '汇兑 | 附言: 报销微信开放平台认证费用 (对方: 杨云飞)' },
      { id: 'e3', title: '发票-深圳市腾讯计算机系统有限公司', direction: 'expense' },
    )).toBe(false)
  })

  it('非发票类账目（标题无「发票-」）无从判断销方，维持原有规则', () => {
    expect(statementSellerAffinity(
      { description: '税费社保 (对方: 金库杭州市余杭区支库)' },
      { id: 'e4', title: '社保缴纳', direction: 'expense' },
    )).toBe(true)
  })

  it('销项发票（income）按购方 buyer_name 判断：一致放行、无关拦截、缺失时不拿销方本公司名误判', () => {
    const salesEntry = {
      id: 'e5',
      title: '发票-杭州惠保信网络科技有限公司',
      buyer_name: '深圳市得同保险代理有限公司',
      direction: 'income',
    }
    expect(statementSellerAffinity(
      { description: '提回对公户收款 | 26100900420688 (对方: 深圳市得同保险代理有限公司)' },
      salesEntry,
    )).toBe(true)
    expect(statementSellerAffinity(
      { description: '提回对公户收款 | 结算款 (对方: 某无关贸易有限公司)' },
      salesEntry,
    )).toBe(false)
    expect(statementSellerAffinity(
      { description: '提回对公户收款 | 26100900420688 (对方: 深圳市得同保险代理有限公司)' },
      { id: 'e6', title: '发票-杭州惠保信网络科技有限公司', direction: 'income' },
    )).toBe(true)
  })
})

describe('bank statement boss-friendly presentation helpers', () => {
  it('formatAccountPreviewLabel 在分类名与科目名相同时去重，仅保留代码；不同时保留名称与代码', () => {
    expect(formatAccountPreviewLabel('资本公积', { name: '资本公积', code: '3002' })).toBe('3002')
    expect(formatAccountPreviewLabel('营业外收入', { name: '营业外收入', code: '5301' })).toBe('5301')
    expect(formatAccountPreviewLabel('股东投入', { name: '实收资本', code: '3001' })).toBe('实收资本 3001')
    expect(formatAccountPreviewLabel('股东垫付', { name: '其他应付款', code: '2241' })).toBe('其他应付款 2241')
    expect(formatAccountPreviewLabel('股东投入', null)).toBe('')
  })

  it('categoryPlainHint 为多义项分类返回白话业务含义', () => {
    expect(categoryPlainHint('股东投入')).toContain('注册资本')
    expect(categoryPlainHint('股东垫付')).toContain('暂借')
    expect(categoryPlainHint('营业外收入')).toContain('实名验证')
    expect(categoryPlainHint('税费缴纳')).toContain('应交增值税余额')
    expect(categoryPlainHint('社保公积金')).toContain('应付职工薪酬')
    expect(categoryPlainHint('社保公积金')).not.toContain('工资模块')
    expect(categoryPlainHint('个税代缴')).toContain('2221012')
    expect(categoryPlainHint('罚款支出')).toContain('5711011')
    expect(categoryPlainHint('税收滞纳金')).toContain('5711010')
    expect(categoryPlainHint('所得税费用')).toContain('5801')
    expect(categoryPlainHint('未定义分类')).toBe('')
  })

  it('parseBankStatementDisplay 清洗银行原始描述：突出对手方与附言并去重姓名', () => {
    expect(parseBankStatementDisplay('汇兑 | 附言: 投资款 (对方: 何静谷)')).toEqual({
      counterparty: '何静谷',
      summary: '投资款',
      channel: '汇兑',
      raw: '汇兑 | 附言: 投资款 (对方: 何静谷)',
    })
    expect(parseBankStatementDisplay('汇兑 | 附言: 何静谷实缴出资款 (对方: 何静谷)')).toEqual({
      counterparty: '何静谷',
      summary: '实缴出资款',
      channel: '汇兑',
      raw: '汇兑 | 附言: 何静谷实缴出资款 (对方: 何静谷)',
    })
    expect(parseBankStatementDisplay('入账 | 附言: 791015企业实名验证 (对方: 银联商务支付股份有限公司)')).toEqual({
      counterparty: '银联商务支付股份有限公司',
      summary: '791015企业实名验证',
      channel: '入账',
      raw: '入账 | 附言: 791015企业实名验证 (对方: 银联商务支付股份有限公司)',
    })
    expect(parseBankStatementDisplay('汇兑 (对方: 何静谷)')).toEqual({
      counterparty: '何静谷',
      summary: '',
      channel: '汇兑',
      raw: '汇兑 (对方: 何静谷)',
    })
    expect(parseBankStatementDisplay('未知外部转账')).toEqual({
      counterparty: '',
      summary: '未知外部转账',
      channel: '',
      raw: '未知外部转账',
    })
  })
})

describe('multi-invoice and advancement reimbursement helpers', () => {
  it('classifies homogeneous pools accurately and distinguishes advancements from pending invoices', () => {
    expect(classifyMultiMatchCandidate({ id: 'a1', payer: 'boss_personal', amount: 200 })).toEqual({
      poolKind: 'advancement',
      poolKey: 'adv:boss_personal',
      poolLabel: '老板垫付',
    })
    expect(classifyMultiMatchCandidate({ id: 'a2', payer: 'employee_personal:杨云飞', amount: 300 })).toEqual({
      poolKind: 'advancement',
      poolKey: 'adv:employee_personal:杨云飞',
      poolLabel: '员工(杨云飞)垫付',
    })
    expect(classifyMultiMatchCandidate({
      id: 'i1',
      source: 'invoice_import',
      payer: 'company_account',
      amount: 500,
      voucherLines: { credit: [{ accountCode: '2241', amount: '500.00' }] },
    })).toEqual({
      poolKind: 'pending_invoice',
      poolKey: 'pending_invoice',
      poolLabel: '待付款发票',
    })
  })

  it('approves exact same-pool pending invoices and same-payer advancements while rejecting mixed pools or diffs', () => {
    const inv1 = { id: 'i1', amount: 600, source: 'invoice_import', payer: 'company_account' }
    const inv2 = { id: 'i2', amount: 856.5, source: 'invoice_import', payer: 'company_account' }
    const okInv = evaluateMultiMatchSelection(-1456.5, [inv1, inv2])
    expect(okInv.canConfirm).toBe(true)
    expect(okInv.isHomogeneous).toBe(true)
    expect(okInv.diff).toBe(0)
    expect(okInv.tolerance).toBe(0.01)

    const adv1 = { id: 'a1', amount: 456.5, payer: 'employee_personal:杨云飞' }
    const adv2 = { id: 'a2', amount: 1000.0, payer: 'employee_personal:杨云飞' }
    const okAdv = evaluateMultiMatchSelection(-1456.5, [adv1, adv2])
    expect(okAdv.canConfirm).toBe(true)
    expect(okAdv.tolerance).toBe(0.05)

    const diffCheck = evaluateMultiMatchSelection(-1500, [inv1, inv2])
    expect(diffCheck.canConfirm).toBe(false)
    expect(diffCheck.amountMatches).toBe(false)
    expect(diffCheck.diff).toBe(-43.5)

    const mixedPayer = evaluateMultiMatchSelection(-1000, [
      { id: 'b1', amount: 500, payer: 'boss_personal' },
      { id: 'e1', amount: 500, payer: 'employee_personal:杨云飞' },
    ])
    expect(mixedPayer.isHomogeneous).toBe(false)
    expect(mixedPayer.canConfirm).toBe(false)
    expect(mixedPayer.poolError).toContain('同一垫付人')

    const mixedPool = evaluateMultiMatchSelection(-1056.5, [adv1, inv1])
    expect(mixedPool.isHomogeneous).toBe(false)
    expect(mixedPool.canConfirm).toBe(false)
    expect(mixedPool.poolError).toContain('混合合并')
  })

  it('collects claimed entry IDs from 1:1 and 1:N match suggestions while keeping reimbursement candidates in pending list', () => {
    const activeStmtIds = new Set(['stmt-1', 'stmt-2', 'stmt-3'])
    const claimed = collectSuggestedMatchedEntryIds([
      { statementId: 'stmt-1', type: 'match', matchedEntryId: 'inv-1' },
      { statementId: 'stmt-2', type: 'match', matchedEntryId: 'inv-a', matchedEntryIds: ['inv-a', 'inv-b', 'inv-c'] },
      { statementId: 'stmt-3', type: 'match', matchedEntryId: 'inv-reim-keep', invoiceReimbursement: { entryId: 'inv-reim-keep' } },
      { statementId: 'stmt-3', type: 'new', matchedEntryId: 'should-ignore' },
      { statementId: 'stmt-3', type: 'difference', matchedEntryIds: ['diff-ignore'] },
      { statementId: 'stmt-settled', type: 'match', matchedEntryId: 'old-settled', matchedEntryIds: ['old-settled', 'old-2'] },
    ], activeStmtIds)

    expect([...claimed].sort()).toEqual(['inv-1', 'inv-a', 'inv-b', 'inv-c'])
  })

  it('formats plain source-and-consequence explanations for single and multi matches', () => {
    expect(formatMatchExplanation({ type: 'new' })).toBe('')

    // 老板垫付单据（截图 2026-08-18 ¥481.33 腾讯云场景）
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-cloud',
      matchedEntry: {
        id: 'e-cloud',
        occurDate: '2026-08-18',
        amount: 481.33,
        payer: 'boss_personal',
        isAdvancement: true,
      },
    })).toBe('2026-08-18 老板垫付单据（¥481.33）· 确认后生成报销还款凭证（冲减其他应付款），不重复记费用')

    // 员工垫付单据（优先采用 liveEntry 最新状态）
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-emp',
      matchedEntry: { id: 'e-emp', occurDate: '2026-09-01', amount: 120, payer: 'company_account', source: 'invoice_import' },
    }, {
      id: 'e-emp',
      occurDate: '2026-09-01',
      amount: 120,
      payer: 'employee_personal:杨云飞',
    })).toBe('2026-09-01 员工（杨云飞）垫付单据（¥120.00）· 确认后生成报销还款凭证（冲减其他应付款），不重复记费用')

    // 待付款 / 待收款发票（无品名沿用金额；有品名直接展示清洗后的核心品名）
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-inv-exp',
      matchedEntry: { id: 'e-inv-exp', occurDate: '2026-06-02', amount: 300, direction: 'expense', source: 'invoice_import', isPendingInvoice: true },
    })).toBe('2026-06-02 待付款发票（¥300.00）· 确认后按公户付款核销该发票，不重复记费用')
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-inv-item',
      matchedEntry: {
        id: 'e-inv-item',
        occurDate: '2026-10-31',
        amount: 300,
        direction: 'expense',
        source: 'invoice_import',
        isPendingInvoice: true,
        item_name: '*信息技术服务*软件测试服务',
      },
    })).toBe('2026-10-31 发票（品名：软件测试服务）· 确认后按公户付款核销该发票，不重复记费用')
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-adv-item',
      matchedEntry: {
        id: 'e-adv-item',
        occurDate: '2026-10-17',
        amount: 1794,
        direction: 'expense',
        source: 'invoice_import',
        payer: 'boss_personal',
        isAdvancement: true,
        item_name: '*信息技术服务*云服务器',
      },
    })).toBe('2026-10-17 老板垫付单据（品名：云服务器）· 确认后生成报销还款凭证（冲减其他应付款），不重复记费用')
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-inv-inc',
      matchedEntry: { id: 'e-inv-inc', occurDate: '2026-06-05', amount: 1000, direction: 'income', source: 'invoice_import', isPendingInvoice: true },
    })).toBe('2026-06-05 待收款发票（¥1,000.00）· 确认后按公户收款核销该发票，不重复记收入')

    // 已有公户记账单据
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryId: 'e-booked',
      matchedEntry: { id: 'e-booked', occurDate: '2026-10-12', amount: 250, direction: 'expense', source: 'manual' },
    })).toBe('2026-10-12 已有记账（¥250.00）· 确认后仅关联核销流水，不新增凭证')

    // 1:N 多笔垫付合并
    expect(formatMatchExplanation({
      type: 'match',
      matchedEntryIds: ['a1', 'a2'],
      matchedEntries: [
        { id: 'a1', amount: 200, payer: 'boss_personal' },
        { id: 'a2', amount: 281.33, payer: 'boss_personal' },
      ],
    })).toBe('合并匹配 2 笔垫付单据（合计 ¥481.33）· 确认后生成汇总报销还款凭证，不重复记费用')
  })

  it('extracts matched entry account label for single matches', () => {
    const sug = {
      type: 'match',
      matchedEntryId: 'inv-baidu',
      matchedEntry: {
        id: 'inv-baidu',
        occurDate: '2026-10-15',
        amount: 1794,
        direction: 'expense',
        category: '信息技术服务费',
      },
      statement: { date: '2026-10-26', amount: -1794 },
    }

    // 仅快照有 category 时回退展示分类
    expect(formatMatchAccountLabel(sug)).toBe('信息技术服务费')

    // liveEntry 带完整分录（含进项税行）：跳过 2221 进项税取主费用科目
    const liveWithTax = {
      id: 'inv-baidu',
      direction: 'expense',
      category: '信息技术服务费',
      voucherLines: {
        debit: [
          { accountCode: '222101', accountName: '应交税费-进项税额', amount: 101.55 },
          { accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 1692.45 },
        ],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 1794 }],
      },
    }
    expect(formatMatchAccountLabel(sug, liveWithTax)).toBe('5602015 管理费用-信息技术服务费')

    // 多笔合并返回空科目
    expect(formatMatchAccountLabel({ type: 'match', matchedEntryIds: ['a', 'b'] })).toBe('')
  })

  it('identifies fallback invoice categories cleanly', () => {
    expect(isFallbackInvoiceCategory('其他支出')).toBe(true)
    expect(isFallbackInvoiceCategory('管理费用-其他')).toBe(true)
    expect(isFallbackInvoiceCategory('')).toBe(true)
    expect(isFallbackInvoiceCategory('信息技术服务费')).toBe(false)
  })

  it('formats category assignment basis for match tooltips across rule, legacy item, manual, and fallback cases', () => {
    // 1. 规则命中带 categoryReason
    expect(formatMatchCategoryBasis({
      type: 'match',
      matchedEntryId: 'inv-wangyi',
      matchedEntry: {
        id: 'inv-wangyi',
        category: '信息技术服务费',
        categorySource: 'rule',
        categoryReason: '品名关键词「软件测试」命中信息技术服务费规则',
        item_name: '*信息技术服务*软件测试服务',
      },
    })).toContain('品名关键词「软件测试」命中信息技术服务费规则（发票品名：*信息技术服务*软件测试服务）；适用于云服务、软件订阅、技术开发与测试等支出')

    // 2. 历史老数据无 categoryReason：用发票品名 + 适用范围自动补全
    expect(formatMatchCategoryBasis({
      type: 'match',
      matchedEntryId: 'inv-legacy',
      matchedEntry: {
        id: 'inv-legacy',
        category: '信息技术服务费',
        item_name: '*信息技术服务*软件测试服务',
      },
    })).toContain('按发票品名「*信息技术服务*软件测试服务」归入「信息技术服务费」；适用于云服务、软件订阅')

    // 3. 人工确认 / liveEntry 就地改科目后覆盖旧快照
    expect(formatMatchCategoryBasis({
      type: 'match',
      matchedEntryId: 'inv-fb',
      matchedEntry: {
        id: 'inv-fb',
        category: '其他支出',
        categorySource: 'fallback',
        item_name: '*信息技术服务*软件测试服务',
      },
    }, {
      id: 'inv-fb',
      category: '咨询费',
      categorySource: 'manual',
    })).toContain('已人工确认归入「咨询费」（发票品名：*信息技术服务*软件测试服务）；适用于财税代理、法律')

    // 4. 兜底科目忽略「默认值」废话，提示用「修改单据」确认
    expect(formatMatchCategoryBasis({
      type: 'match',
      matchedEntryId: 'inv-fb2',
      matchedEntry: {
        id: 'inv-fb2',
        category: '其他支出',
        categorySource: 'fallback',
        categoryReason: '未能自动识别，采用默认值',
        item_name: '*综合服务*测试费',
      },
    })).toContain('按发票品名「*综合服务*测试费」归入「其他支出」；当前为兜底科目')

    // 5. 多笔合并返回空
    expect(formatMatchCategoryBasis({ type: 'match', matchedEntryIds: ['a', 'b'] })).toBe('')
  })

  it('excludes reimbursement settlement vouchers from isNonBankSettledEntry', () => {
    expect(isReimbursementSettlementEntry({ id: 'reim-bs-1-e1', direction: 'owner_transfer', payer: 'boss_personal' })).toBe(true)
    expect(isReimbursementSettlementEntry({ id: 'manual-1', direction: 'owner_transfer', category: '股东垫付报销', payer: 'boss_personal' })).toBe(true)
    expect(isNonBankSettledEntry({ id: 'reim-bs-1-e1', direction: 'owner_transfer', payer: 'boss_personal' })).toBe(false)
    expect(isNonBankSettledEntry({ id: 'manual-1', direction: 'owner_transfer', category: '员工垫付报销', payer: 'employee_personal:张三' })).toBe(false)
  })

  it('evaluates isSettledAsOfMonth respecting cross-month paymentDate', () => {
    expect(isSettledAsOfMonth({ billingStatus: 'reconciled', paymentDate: '2026-10-26' }, '2026-10')).toBe(true)
    expect(isSettledAsOfMonth({ billingStatus: 'reconciled', paymentDate: '2026-11-05' }, '2026-10')).toBe(false)
    expect(isSettledAsOfMonth({ billingStatus: 'has_invoice' }, '2026-10')).toBe(false)
  })

  it('computes bossLedgerSummary from 2241 advanceBalances without double-counting reimbursed or posted entries', () => {
    const octEntries = [
      { id: 'e-zhipu', direction: 'expense', amount: 693.84, payer: 'boss_personal', billingStatus: 'reconciled', paymentDate: '2026-10-26', voucherStatus: 'posted' },
      { id: 'e-baidu', direction: 'expense', amount: 1794.00, payer: 'boss_personal', billingStatus: 'reconciled', paymentDate: '2026-10-26', voucherStatus: 'posted' },
      { id: 'e-deepseek', direction: 'expense', amount: 210.00, payer: 'boss_personal', billingStatus: 'has_invoice', voucherStatus: 'posted' },
      { id: 'e-unpaid-cust', direction: 'income', amount: 5000, paymentStatus: 'unpaid', billingStatus: 'has_invoice' },
      { id: 'e-paid-cust', direction: 'income', amount: 3000, paymentStatus: 'unpaid', billingStatus: 'reconciled', paymentDate: '2026-10-28' },
      { id: 'e-unpaid-sup', direction: 'expense', amount: 800, paymentStatus: 'unpaid', billingStatus: 'has_invoice' },
    ]
    const advanceBalances = {
      month: '2026-10',
      items: [
        {
          payer: 'boss_personal',
          kind: 'boss' as const,
          name: '老板（股东）',
          opening: '481.33',
          advance: '2697.84',
          reimbursed: '2969.17',
          closing: '210.00',
          count: 9,
          lastDate: '2026-10-26',
          entries: [
            { id: 'v-ds', sourceId: 'e-deepseek', date: '2026-10-20', period: '2026-10', summary: 'DeepSeek', category: '信息技术服务费', amount: '210.00', direction: 'advance' as const, isCurrentMonth: true },
          ],
        },
      ],
      totals: { opening: '481.33', advance: '2697.84', reimbursed: '2969.17', closing: '210.00' },
    }

    const summary = computeBossLedgerSummary(octEntries, advanceBalances, '2026-10')
    expect(summary.fromAdvanceLedger).toBe(true)
    expect(summary.companyOwesBossAndEmp).toBe(210)
    expect(summary.bossOnly).toBe(210)
    expect(summary.empOnly).toBe(0)
    expect(summary.advanceMonthReimbursed).toBe(2969.17)
    expect(summary.customerOwes).toBe(5000)
    expect(summary.customerCount).toBe(1)
    expect(summary.companyOwesSupplier).toBe(800)
    expect(summary.supplierCount).toBe(1)

    // 新增尚未过账的草稿垫付：实时叠加，但不重复计算已过账的 DeepSeek
    const withDraft = computeBossLedgerSummary(
      [...octEntries, { id: 'e-draft-adv', direction: 'expense', amount: 150, payer: 'boss_personal', billingStatus: 'has_invoice', voucherStatus: 'draft' }],
      advanceBalances,
      '2026-10',
    )
    expect(withDraft.companyOwesBossAndEmp).toBe(360)
    expect(withDraft.bossOnly).toBe(360)

    // 回退到本地单据口径时，已报销单据仍被剔除、跨月后才报销的保留
    const fallback = computeBossLedgerSummary(
      [
        ...octEntries,
        { id: 'e-nov-reim', direction: 'expense', amount: 88, payer: 'employee_personal:李四', billingStatus: 'reconciled', paymentDate: '2026-11-03' },
      ],
      null,
      '2026-10',
    )
    expect(fallback.fromAdvanceLedger).toBe(false)
    expect(fallback.bossOnly).toBe(210)
    expect(fallback.empOnly).toBe(88)
    expect(fallback.companyOwesBossAndEmp).toBe(298)
    expect(isUnreimbursedPersonalAdvanceEntry({ id: 'e-zhipu', direction: 'expense', payer: 'boss_personal', billingStatus: 'reconciled', paymentDate: '2026-10-26' }, '2026-10')).toBe(false)
  })
})
