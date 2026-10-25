/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface ParseIssue {
  row: number
  reason: string
}

export interface MappingSuggestion {
  row: number
  sourceCode: string
  sourceName: string
  suggestedCode: string
}

export interface BalanceParseRow {
  row: number
  accountCode: string
  accountName: string
  debitBalance: string
  creditBalance: string
  ytdDebit: string
  ytdCredit: string
  partnerName: string
}

export interface BalanceParseResponse {
  valid: BalanceParseRow[]
  errors: ParseIssue[]
  warnings: ParseIssue[]
  suggestions: MappingSuggestion[]
  mapping: Record<string, string>
  totals: { debit: number; credit: number; balanced: boolean; count: number }
  total: number
}

export interface VoucherLineParse {
  accountCode: string
  accountName: string
  direction: 'debit' | 'credit'
  amount: string
  partnerName: string
  departmentName: string
  projectName: string
}

export interface VoucherParseRow {
  key: string
  voucherNo: string
  date: string
  summary: string
  lines: VoucherLineParse[]
  rows: number[]
  debitTotal: number
  creditTotal: number
}

export interface VoucherParseResponse {
  valid: VoucherParseRow[]
  errors: ParseIssue[]
  warnings: ParseIssue[]
  suggestions: MappingSuggestion[]
  mapping: Record<string, string>
  totals: { vouchers: number; lines: number; amount: number }
  total: number
}

export function parseLegacyBalance(
  rows: Record<string, unknown>[],
  mapping?: Record<string, string>,
): Promise<BalanceParseResponse> {
  return apiClient.post('/api/legacy-import/balance/parse', { rows, mapping })
}

export function confirmLegacyBalance(
  payload: {
    items: BalanceParseRow[]
    fiscal_year: number
    start_period?: string
    mapping?: Record<string, string>
  },
  stepUpToken: string,
): Promise<Record<string, unknown>> {
  return apiClient.post('/api/legacy-import/balance/confirm', payload, {
    headers: { 'Step-Up-Token': stepUpToken },
  })
}

export function parseLegacyVouchers(
  rows: Record<string, unknown>[],
  mapping?: Record<string, string>,
): Promise<VoucherParseResponse> {
  return apiClient.post('/api/legacy-import/vouchers/parse', { rows, mapping })
}

export function confirmLegacyVouchers(payload: {
  vouchers: VoucherParseRow[]
  batch_id?: string
  post?: boolean
  mapping?: Record<string, string>
}): Promise<{
  batchId: string
  imported: number
  posted: number
  skipped: { voucherNo: string; reason: string }[]
}> {
  return apiClient.post('/api/legacy-import/vouchers/confirm', payload)
}

export function linkOpeningPartners(): Promise<{ scanned: number; linked: number }> {
  return apiClient.post('/api/legacy-import/link-opening-partners')
}
