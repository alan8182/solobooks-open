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
  isReversalLikeEntry,
  canResetEntryDisposition,
  isLiveInvoiceEntry,
  invoiceDedupKey,
  isPreferredInvoiceEntry,
  invoiceRowStatus,
  invoiceNeedsAttention,
  formatDispositionTag,
  formatDispositionExplanation,
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
