/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { VoucherGenerateResponse } from './types'

export function generateVoucher(entryIds: string[]): Promise<VoucherGenerateResponse> {
  return apiClient.post('/api/ledger/voucher', { entryIds })
}

export interface LedgerAccountOption {
  code: string
  name: string
  type: string
  parentCode: string | null
  level: number
  isLeaf: boolean
  quantityEnabled: boolean
  unit: string | null
}

/**
 * 权威科目表。账簿类页面一律从这里取下拉项——两份 55 项硬编码清单曾经让
 * 用户自建的科目在总账/明细账里根本选不到。
 */
export function getLedgerAccounts(): Promise<{ accounts: LedgerAccountOption[] }> {
  return apiClient.get('/api/ledger/accounts')
}

export interface ChronologicalResponse {
  month: string
  items: {
    entryId: string
    voucherRef: string | null
    period: string
    date: string
    summary: string
    accountCode: string
    accountName: string
    direction: 'debit' | 'credit'
    debit: number
    credit: number
  }[]
  voucherCount: number
  totalDebit: number
  totalCredit: number
  isBalanced: boolean
}

export interface MultiColumnResponse {
  accountCode: string
  accountName: string
  month: string
  direction: 'debit' | 'credit'
  columns: { code: string; name: string }[]
  items: {
    entryId: string
    voucherRef: string | null
    date: string
    summary: string
    columns: Record<string, number>
    debit: number
    credit: number
    net: number
  }[]
  columnTotals: Record<string, number>
  mainTotal: number
  debitTotal: number
  creditTotal: number
  hint?: string
}

export function getChronologicalLedger(month: string): Promise<ChronologicalResponse> {
  return apiClient.get('/api/ledger/chronological', { params: { month } })
}

export function getMultiColumnLedger(accountCode: string, month: string): Promise<MultiColumnResponse> {
  return apiClient.get('/api/ledger/multi-column', { params: { account_code: accountCode, month } })
}

/* ────────────────────────── 电子账簿整册 ──────────────────────────
 * 一册 = 一次请求。金额一律是字符串（后端 money_text），方向由后端判定，
 * 前端只负责显示——贷余科目在资产类里也存在，方向规则不该散到视图层。
 */

export type BookScope = 'month' | 'year'

export interface LedgerBookMeta {
  companyName: string
  month: string | null
  year: number | null
  scope: BookScope
  periodStart: string
  periodEnd: string
  periodStatus: string | null
  periodStatusLabel: string
  units: string
  accountingStandard: string
  entryCount: number
  journalBasis: string
  generatedAt: string | null
  generatedBy: string | null
  snapshotId: string | null
  bookNo: string | null
  contentHash: string | null
  pageCount: number | null
}

export interface LedgerBalanceRow {
  accountCode: string
  accountName: string
  category: string
  openingBalance: string
  openingAbs: string
  openingDirection: 'debit' | 'credit' | 'flat'
  debitTotal: string
  creditTotal: string
  balance: string
  closingBalance: string
  closingAbs: string
  closingDirection: 'debit' | 'credit' | 'flat'
  isParent: boolean
  hasActivity: boolean
  hasBalance: boolean
  hasQuantity?: boolean
  unit?: string | null
  quantityOpening?: string | null
  quantityIn?: string | null
  quantityOut?: string | null
  quantityClosing?: string | null
}

export interface LedgerBalance {
  items: LedgerBalanceRow[]
  /** 全量启用科目行数（含零发生额）；items 是按当前口径过滤后的结果 */
  totalRows: number
  totalDebit: string
  totalCredit: string
  isBalanced: boolean
  openingDebitTotal: string
  openingCreditTotal: string
  closingDebitTotal: string
  closingCreditTotal: string
  isOpeningBalanced: boolean
  isClosingBalanced: boolean
  hiddenZeroCount: number
}

export interface LedgerCounterparty {
  code: string
  name: string
  amount: string
}

export interface LedgerDetailLine {
  date: string
  period: string
  voucherRef: string | null
  summary: string
  debit: string
  credit: string
  balance: string
  balanceAbs: string
  balanceDirection: 'debit' | 'credit' | 'flat'
  counterparties: LedgerCounterparty[]
  backfill: boolean
  quantity?: string
  unitPrice?: string | null
  unit?: string | null
}

export interface LedgerDetailPage {
  code: string
  name: string
  category: string
  opening: string
  openingAbs: string
  openingDirection: 'debit' | 'credit' | 'flat'
  lines: LedgerDetailLine[]
  periodTotals: { debit: string; credit: string }
  yearTotals: { debit: string; credit: string }
  closing: string
  closingAbs: string
  closingDirection: 'debit' | 'credit' | 'flat'
  lineCount: number
  hasQuantity: boolean
  unit: string | null
  quantityOpening?: string | null
}

export interface LedgerChronologicalRow {
  entryId: string
  voucherRef: string | null
  period: string
  date: string
  summary: string
  accountCode: string
  accountName: string
  direction: 'debit' | 'credit'
  debit: string
  credit: string
  backfill: boolean
}

export interface LedgerChronological {
  items: LedgerChronologicalRow[]
  voucherCount: number
  totalDebit: string
  totalCredit: string
  isBalanced: boolean
}

export interface LedgerJournalDay {
  date: string
  summary: string
  debit: string
  credit: string
  balance: string
  balanceAbs: string
  balanceDirection: 'debit' | 'credit' | 'flat'
  voucherCount: number
  backfill: boolean
}

export interface LedgerJournalBook {
  kind: 'cash' | 'bank'
  code: string
  name: string
  accountName: string
  opening: string
  openingAbs: string
  openingDirection: 'debit' | 'credit' | 'flat'
  rows: LedgerJournalDay[]
  closing: string
  closingAbs: string
  closingDirection: 'debit' | 'credit' | 'flat'
  totalDebit: string
  totalCredit: string
}

export interface LedgerGeneralMonth {
  month: string
  debit: string
  credit: string
  balance: string
  balanceAbs: string
  balanceDirection: 'debit' | 'credit' | 'flat'
  hasActivity: boolean
}

export interface LedgerGeneralPage {
  accountCode: string
  accountName: string
  category: string
  opening: string
  openingAbs: string
  openingDirection: 'debit' | 'credit' | 'flat'
  months: LedgerGeneralMonth[]
  totalDebit: string
  totalCredit: string
  closing: string
  closingAbs: string
  closingDirection: 'debit' | 'credit' | 'flat'
  isParent: boolean
}

export interface LedgerGeneral {
  year: number
  items: LedgerGeneralPage[]
}

export interface LedgerWarning {
  code: string
  message: string
}

export interface LedgerPageSection {
  key: string
  kind: string
  title: string
  pageNo: number
  pageCount: number
  code?: string
  kind_code?: string
}

export interface LedgerPagination {
  sections: LedgerPageSection[]
  totalPages: number
  pageSize: Record<string, number>
}

export interface LedgerBook {
  meta: LedgerBookMeta
  balance: LedgerBalance
  details: LedgerDetailPage[]
  chronological: LedgerChronological
  journals: LedgerJournalBook[]
  general: LedgerGeneral | null
  warnings: LedgerWarning[]
  pagination: LedgerPagination
}

export function getLedgerBook(params: {
  month?: string
  year?: number
  scope?: BookScope
  showZeroRows?: boolean
  /** 单科目查询模式（仅月度册）：details 只含该科目一页（父科目前缀聚合） */
  accountCode?: string
}): Promise<LedgerBook> {
  // 余额表的"含零发生额科目"必须由服务端决定：固定分页的页数按返回行数算，
  // 前端自己过滤会让目录页码与实际渲染页数对不上。
  return apiClient.get('/api/ledger/book', {
    params: {
      ...(params.month ? { month: params.month } : {}),
      ...(params.year ? { year: params.year } : {}),
      ...(params.accountCode ? { account_code: params.accountCode } : {}),
      scope: params.scope || 'month',
      show_zero_rows: params.showZeroRows ? 1 : 0,
    },
  })
}

/* ────────────────────────── 正式账簿（签发留痕） ────────────────────────── */

export interface LedgerBookSnapshotSummary {
  id: string
  bookKind: 'monthly' | 'yearly'
  period: string
  bookNo: string
  status: 'issued' | 'void'
  contentHash: string
  pageCount: number
  lineCount: number
  anchor: Record<string, unknown>
  generatedBy: string | null
  generatedAt: string | null
  voidReason: string | null
  voidedBy: string | null
  voidedAt: string | null
}

export interface LedgerBookSnapshotDetail extends LedgerBookSnapshotSummary {
  payload: LedgerBook
}

export function issueLedgerBookSnapshot(body: {
  month?: string
  year?: number
  scope?: BookScope
}): Promise<LedgerBookSnapshotDetail> {
  return apiClient.post('/api/ledger/book/snapshots', body)
}

export function listLedgerBookSnapshots(period?: string): Promise<{ items: LedgerBookSnapshotSummary[] }> {
  return apiClient.get('/api/ledger/book/snapshots', { params: period ? { period } : {} })
}

export function getLedgerBookSnapshot(id: string): Promise<LedgerBookSnapshotDetail> {
  return apiClient.get(`/api/ledger/book/snapshots/${id}`)
}

export function voidLedgerBookSnapshot(id: string, reason: string): Promise<LedgerBookSnapshotSummary> {
  return apiClient.post(`/api/ledger/book/snapshots/${id}/void`, { reason })
}


export interface LedgerSnapshotDiffRow {
  accountCode: string
  accountName: string
  openingDelta: string
  debitDelta: string
  creditDelta: string
  closingDelta: string
}

export interface LedgerSnapshotSide {
  id: string
  bookNo: string
  contentHash: string
  generatedAt: string | null
}

/** 两册账簿的科目级差异：反结账补记重签后，回答"新册和旧册差在哪"。 */
export interface LedgerSnapshotDiff {
  period: string
  base: LedgerSnapshotSide
  target: LedgerSnapshotSide
  hashEqual: boolean
  pageCountDelta: number
  balance: LedgerSnapshotDiffRow[]
  vouchersAdded: string[]
  vouchersRemoved: string[]
}

export function diffLedgerBookSnapshots(id: string, against: string): Promise<LedgerSnapshotDiff> {
  return apiClient.get(`/api/ledger/book/snapshots/${id}/diff`, { params: { against } })
}
