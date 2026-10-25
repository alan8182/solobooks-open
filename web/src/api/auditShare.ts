/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * Solo Audit Share 协审链接 API。
 * 公开端点（/api/share/*）免注册：CPA 无登录态，apiClient 不带 Authorization 即可访问。
 */
import apiClient from '@/api/client'

// ---------------------------------------------------------------------------
// 类型（与 backend/services/audit_share_service.build_snapshot 对齐）
// ---------------------------------------------------------------------------

export interface ShareCheckItem {
  code: string
  name: string
  status: 'passed' | 'blocking' | string
  pendingCount?: number
  draftCount?: number
  pendingReviewCount?: number
  orphanVoucherCount?: number
  unreconciledBankCount?: number
  issueCount?: number
  records?: Array<{ entryId?: string; title?: string; date?: string; status?: string; reasons?: string[] }>
}

export interface TrialBalanceRow {
  accountCode: string
  accountName: string
  debit: string
  credit: string
  balance: string
}

export interface AnomalyItem {
  id: string
  type: string
  level: 'high' | 'medium' | 'low' | string
  title: string
  description?: string
  entryId?: string
  amount?: string | number
}

export interface UnmatchedReport {
  status?: string
  message?: string
  month?: string
  bank_only?: Array<{ date: string; description: string; amount: number }>
  book_only?: Array<{ date?: string; title?: string; amount?: number }>
  bank_only_count?: number
  book_only_count?: number
  matched_effect_issue_count?: number
  balance_reconciliation?: {
    has_data?: boolean
    flow_balanced?: boolean
    book_difference?: string | number
  }
}

export interface ShareSnapshot {
  generatedAt: string
  month: string
  disclaimer: string
  checks: {
    period?: string
    debit?: string
    credit?: string
    checks: ShareCheckItem[]
    accounts?: TrialBalanceRow[]
  }
  trialBalance: TrialBalanceRow[]
  anomalies: AnomalyItem[]
  unmatched: UnmatchedReport
  accruals: { estimateCount?: number | null }
  incomeSummary: { revenue: string; expense: string; profit: string }
}

export interface ShareView {
  share: {
    shareId: string
    month: string
    anonymized: boolean
    companyName: string
    expiresAt: string | null
    viewCount: number
    generatedAt: string
    disclaimer: string
  }
  snapshot: ShareSnapshot
}

export interface ShareAnnotation {
  id: string
  anchorType: 'overall' | 'entry' | 'check_item' | 'report_section' | string
  anchorId?: string | null
  body: string
  authorName: string
  status: 'open' | 'acknowledged' | 'resolved' | 'dismissed' | string
  resolutionNote?: string | null
  createdAt: string
}

export interface OwnerShareOverview {
  id: string
  month: string
  anonymized: boolean
  state: 'active' | 'revoked' | 'expired' | string
  expiresAt: string | null
  revokedAt: string | null
  viewCount: number
  lastViewedAt: string | null
  annotationCount: number
  createdAt: string | null
  url?: string
  token?: string
  stats?: {
    month: string
    checkCount: number
    blockingCount: number
    anomalyCount: number
    trialBalanceRows: number
    incomeSummary: { revenue: string; expense: string; profit: string }
  }
  annotations?: ShareAnnotation[]
}

export interface SharePreview {
  month: string
  checkCount: number
  blockingCount: number
  anomalyCount: number
  trialBalanceRows: number
  incomeSummary: { revenue: string; expense: string; profit: string }
  anonymized: boolean
  sampleAnomalies: AnomalyItem[]
}

// ---------------------------------------------------------------------------
// CPA 侧（免注册）
// ---------------------------------------------------------------------------

export async function fetchShareView(token: string): Promise<ShareView> {
  return apiClient.get(`/api/share/${encodeURIComponent(token)}`)
}

export async function fetchShareAnnotations(token: string): Promise<ShareAnnotation[]> {
  const resp = await apiClient.get(`/api/share/${encodeURIComponent(token)}/annotations`)
  return (resp as unknown as { annotations: ShareAnnotation[] }).annotations ?? []
}

export async function createShareAnnotation(
  token: string,
  payload: { authorName: string; anchorType: string; anchorId?: string | null; body: string },
): Promise<ShareAnnotation> {
  return apiClient.post(`/api/share/${encodeURIComponent(token)}/annotations`, payload)
}

// ---------------------------------------------------------------------------
// 老板侧（require_auth）
// ---------------------------------------------------------------------------

export async function createAuditShare(payload: {
  month: string
  anonymized: boolean
  expiresInDays: number
}): Promise<OwnerShareOverview> {
  return apiClient.post('/api/audit-shares', payload)
}

export async function previewAuditShare(payload: {
  month: string
  anonymized: boolean
}): Promise<SharePreview> {
  return apiClient.post('/api/audit-shares/preview', payload)
}

export async function listAuditShares(): Promise<OwnerShareOverview[]> {
  return apiClient.get('/api/audit-shares')
}

export async function getAuditShare(shareId: string): Promise<OwnerShareOverview> {
  return apiClient.get(`/api/audit-shares/${shareId}`)
}

export async function revokeAuditShare(shareId: string): Promise<OwnerShareOverview> {
  return apiClient.post(`/api/audit-shares/${shareId}/revoke`)
}

export async function updateShareAnnotationStatus(
  shareId: string,
  annotationId: string,
  payload: { status: string; resolutionNote?: string },
): Promise<{ id: string; status: string; resolutionNote?: string | null }> {
  return apiClient.post(`/api/audit-shares/${shareId}/annotations/${annotationId}/status`, payload)
}
