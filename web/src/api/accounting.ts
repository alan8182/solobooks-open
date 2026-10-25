/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { Entry } from './types'
import { makeIdempotencyKey } from './client'

const headers = () => ({ 'Idempotency-Key': makeIdempotencyKey() })

export function postEntry(entryId: string): Promise<{ entry: Entry }> {
  return apiClient.post(`/api/record/entries/${entryId}/post`, {}, { headers: headers() })
}

export function approveEntry(entryId: string): Promise<{ entry: Entry }> {
  return apiClient.post(`/api/record/entries/${entryId}/approve`, {}, { headers: headers() })
}

export interface BatchPostFailedItem {
  entryId: string
  title: string
  code: string
  reason: string
}

export interface BatchPostResult {
  total: number
  postedCount: number
  failedCount: number
  postedEntryIds: string[]
  failedEntries: BatchPostFailedItem[]
}

export function batchPostEntries(data: { entryIds?: string[]; month?: string }): Promise<BatchPostResult> {
  return apiClient.post('/api/record/entries/batch-post', data, { headers: headers() })
}

export interface PostingOptions { canPost: boolean; canApprove: boolean; canSubmit: boolean; postingPolicy: string }
export function getPostingOptions(entryId: string): Promise<PostingOptions> {
  return apiClient.get(`/api/record/entries/${entryId}/posting-options`)
}
export function submitEntryForReview(entryId: string): Promise<{ entry: Entry }> {
  return apiClient.post(`/api/record/entries/${entryId}/submit-review`, {}, { headers: headers() })
}

export function reverseEntry(
  entryId: string,
  reason: string,
  reversalDate?: string,
): Promise<{ entry: Entry }> {
  return apiClient.post(
    `/api/record/entries/${entryId}/reverse`,
    { reason, reversalDate },
    { headers: headers() },
  )
}

/** 人工调整分录的一行（会计纠错用）。 */
export interface VoucherLineAdjustment {
  direction: 'debit' | 'credit'
  accountCode: string
  amount: number | string
}

/**
 * 会计手工改写分录（科目/方向/金额/增删行）。
 *
 * 服务端会复核借贷平衡、「借方合计 = 交易金额」、末级可用科目三项；已过账
 * 凭证一律拒绝（只能冲销）。resetToSystem=true 时丢弃手改、按系统规则重算。
 */
export function adjustEntryVoucherLines(
  entryId: string,
  payload: { lines?: VoucherLineAdjustment[]; reason?: string; resetToSystem?: boolean },
): Promise<{ entry: Entry }> {
  return apiClient.patch(`/api/record/entries/${entryId}/voucher-lines`, payload, { headers: headers() })
}

export function getPeriodCloseChecks(month: string): Promise<Record<string, unknown>> {
  return apiClient.get(`/api/periods/${month}/close-checks`)
}

export interface BackfillTarget {
  businessDate: string
  businessPeriod?: string
  postingPeriod?: string
  needsBackfill: boolean
  targetPeriod: string | null
  crossQuarter?: boolean
  crossYear?: boolean
  canBackfill: boolean
  reason?: string
  message?: string
}

/**
 * 提交前预判：这笔业务会被记到哪个月。
 * 业务日期所属期间已关账时返回 needsBackfill，前端据此弹确认框。
 */
export function getBackfillTarget(businessDate: string): Promise<BackfillTarget> {
  return apiClient.get('/api/periods/backfill-target', { params: { businessDate } })
}

export function closePeriod(month: string): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/periods/${month}/close`, {}, { headers: headers() })
}

// ── 暂估入账（月末票未到） ──────────────────────────────────

export interface AccrualCreateInput {
  amount: number | string
  businessDate?: string
  vendor: string
  /** 可省略：只给 category 时后端按记账同一套分类→科目映射推导 */
  accountCode?: string
  accountName?: string
  category?: string
  expectedInvoiceDate: string
  basis?: 'contract' | 'receipt' | 'estimate'
  notes?: string
}

export interface AccrualRecord {
  entryId: string
  voucherRef: string
  summary: string
  vendor: string
  amount: string
  businessDate: string
  postingPeriod: string
  expectedInvoiceDate: string
  basis: string
  entryStatus: string
  overdueDays: number
}

export interface PendingAccruals {
  count: number
  amount: string
  records: AccrualRecord[]
}

export function createAccrual(data: AccrualCreateInput): Promise<Record<string, unknown>> {
  return apiClient.post('/api/accruals', data, { headers: headers() })
}

export function reverseAccrual(
  entryId: string,
  reason?: string,
  reversalDate?: string,
): Promise<Record<string, unknown>> {
  return apiClient.post(
    `/api/accruals/${entryId}/reverse`,
    { reason, reversalDate },
    { headers: headers() },
  )
}

export function settleAccrual(
  entryId: string,
  data: { amount: number | string; occurDate?: string; invoiceNumber?: string; title?: string },
): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/accruals/${entryId}/settle`, data, { headers: headers() })
}

export function getPendingAccruals(month?: string): Promise<PendingAccruals> {
  return apiClient.get('/api/accruals/pending', { params: month ? { month } : {} })
}

export function reopenPeriod(
  month: string,
  reason: string,
  stepUpToken: string,
): Promise<Record<string, unknown>> {
  return apiClient.post(
    `/api/periods/${month}/reopen`,
    { reason, stepUpToken },
    { headers: headers() },
  )
}

export interface FilingObligation {
  taxKind: string
  name: string
  period: string
  cycle: 'month' | 'quarter'
  dueDate: string
  daysLeft: number
  status: string
  deadlineId: string | null
}

export interface FilingObligations {
  month: string
  quarter: string
  cycles: { vat: 'month' | 'quarter'; businessIncomeIit: 'month' | 'quarter' }
  vatCycleLabel: string
  obligations: FilingObligation[]
}

/**
 * 结账月份 → 本期申报义务。
 * 结账按月、申报按税种（月/季），两者是不同维度，向导需要显式告诉用户本期要报什么。
 */
export function getFilingObligations(month: string): Promise<FilingObligations> {
  return apiClient.get('/api/tax-filing/obligations', { params: { month } })
}
