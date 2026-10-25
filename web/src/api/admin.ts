/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface AdminOverviewData {
  operator: { userId: string }
  database: { ok: boolean; engine: string }
  counts: { users: number; companies: number }
  agent: { actionsToday: number; conversationsToday: number }
  llm: {
    circuitOpen: boolean
    errorCount7d: number
    topErrors: { type: string; count: number }[]
    recentErrors: {
      createdAt: string | null
      scene: string
      model: string
      errorType: string
      userId: string | null
    }[]
  }
  usage: {
    trackingEnabled: boolean
    tokensToday: number | null
    tokensThisMonth: number | null
  }
  version: string
}

/** 平台概览聚合（同时作为进入 /admin 的权限探针：403 即非管理员）。 */
export async function fetchAdminOverview(): Promise<AdminOverviewData> {
  return (await apiClient.get('/api/admin/overview')) as AdminOverviewData
}

// ── M1：Token 用量统计 ────────────────────────────────────────────

export type UsageGroupBy = 'user' | 'company' | 'day' | 'model'

export interface UsageTokensRow {
  key: string
  calls: number
  promptTokens: number
  completionTokens: number
  totalTokens: number
  costEstimate: number | null
}

export interface UsageTokensResponse {
  groupBy: UsageGroupBy
  from: string
  to: string
  pricingConfigured: boolean
  rows: UsageTokensRow[]
}

export interface UsageRecentItem {
  id: number
  createdAt: string | null
  userId: string | null
  companyId: string | null
  scene: string
  model: string
  promptTokens: number
  completionTokens: number
  totalTokens: number
  durationMs: number
  status: 'ok' | 'error' | 'circuit_open'
  errorType: string | null
}

export interface UsageRecentResponse {
  items: UsageRecentItem[]
}

export async function fetchUsageTokens(params: {
  group_by: UsageGroupBy
  from?: string
  to?: string
}): Promise<UsageTokensResponse> {
  return (await apiClient.get('/api/admin/usage/tokens', { params })) as UsageTokensResponse
}

export async function fetchUsageRecent(params: {
  limit?: number
  status?: 'ok' | 'error' | 'circuit_open'
}): Promise<UsageRecentResponse> {
  return (await apiClient.get('/api/admin/usage/recent', { params })) as UsageRecentResponse
}

// ── M2：用户与公司管理 + 操作留痕 ─────────────────────────────────

export interface AdminUserItem {
  userId: string
  nickname: string
  phone: string
  isPlatformAdmin: boolean
  isDisabled: boolean
  companyCount: number
  roles: string[]
  createdAt: string
  lastLoginAt: string
}

export interface AdminUsersResponse {
  items: AdminUserItem[]
  total: number
  page: number
  pageSize: number
}

export async function fetchAdminUsers(params: {
  query?: string
  page?: number
  pageSize?: number
}): Promise<AdminUsersResponse> {
  return (await apiClient.get('/api/admin/users', { params })) as AdminUsersResponse
}

export async function patchAdminUser(
  userId: string,
  payload: { isDisabled?: boolean; isPlatformAdmin?: boolean },
): Promise<{ user: AdminUserItem; changed: string[] }> {
  return (await apiClient.patch(`/api/admin/users/${userId}`, payload)) as {
    user: AdminUserItem
    changed: string[]
  }
}

export interface AdminCompanyItem {
  companyId: string
  name: string
  ownerUserId: string
  ownerName: string
  ownerPhone: string
  baseCurrency: string
  accountingStandard: string
  memberCount: number
  entryCount: number
  lastActivityAt: string | null
  createdAt: string
}

export interface AdminCompaniesResponse {
  items: AdminCompanyItem[]
  total: number
}

export async function fetchAdminCompanies(params: { query?: string }): Promise<AdminCompaniesResponse> {
  return (await apiClient.get('/api/admin/companies', { params })) as AdminCompaniesResponse
}

export interface AdminAuditLogItem {
  id: number
  operatorId: string
  action: string
  targetType: string
  targetId: string
  detail: Record<string, unknown>
  createdAt: string | null
}

export interface AdminAuditLogsResponse {
  items: AdminAuditLogItem[]
  total: number
  page: number
  pageSize: number
}

export async function fetchAdminAuditLogs(params: {
  page?: number
  pageSize?: number
}): Promise<AdminAuditLogsResponse> {
  return (await apiClient.get('/api/admin/audit-logs', { params })) as AdminAuditLogsResponse
}

// ── M3：Agent 审计 + 数据运维 ─────────────────────────────────────

export type AgentActionStatus = 'pending' | 'approved' | 'rejected' | 'expired'

export interface AdminAgentActionItem {
  id: string
  companyId: string
  userId: string
  conversationId: string | null
  actionType: string
  title: string
  status: AgentActionStatus
  createdAt: string | null
  resolvedAt: string | null
  resolvedBy: string | null
}

export interface AdminAgentActionDetail extends AdminAgentActionItem {
  payload: Record<string, unknown>
  evidence: Record<string, unknown>
  result: Record<string, unknown> | null
}

export interface AdminAgentActionsResponse {
  items: AdminAgentActionItem[]
  total: number
  page: number
  pageSize: number
}

export async function fetchAdminAgentActions(params: {
  status?: 'all' | AgentActionStatus
  companyId?: string
  page?: number
  pageSize?: number
}): Promise<AdminAgentActionsResponse> {
  return (await apiClient.get('/api/admin/agent-actions', { params })) as AdminAgentActionsResponse
}

export async function fetchAgentActionDetail(actionId: string): Promise<AdminAgentActionDetail> {
  return (await apiClient.get(`/api/admin/agent-actions/${actionId}`)) as AdminAgentActionDetail
}

export async function markAgentAction(
  actionId: string,
  status: Exclude<AgentActionStatus, 'pending'>,
  note?: string,
): Promise<{ id: string; status: string; resolvedBy: string }> {
  return (await apiClient.patch(`/api/admin/agent-actions/${actionId}`, { status, note })) as {
    id: string
    status: string
    resolvedBy: string
  }
}

export type CleanupTable = 'llm_usage' | 'agent_actions' | 'admin_audit_logs'

export interface CleanupResult {
  table: CleanupTable
  before: string
  dryRun: boolean
  matched: number
  deleted: number
}

export async function runCleanup(params: {
  table: CleanupTable
  before: string
  dryRun: boolean
}): Promise<CleanupResult> {
  return (await apiClient.post('/api/admin/cleanup', params)) as CleanupResult
}

export interface CleanupStatsItem {
  table: CleanupTable
  total: number
  oldest: string | null
}

export async function fetchCleanupStats(): Promise<{ items: CleanupStatsItem[] }> {
  return (await apiClient.get('/api/admin/cleanup/stats')) as { items: CleanupStatsItem[] }
}
