/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface AdminOverviewData {
  operator: { userId: string; nickname?: string; phone?: string }
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

export type PlanCode = 'trial' | 'standard' | 'pro' | 'plus'

export interface AdminUserItem {
  userId: string
  nickname: string
  phone: string
  isPlatformAdmin: boolean
  isDisabled: boolean
  planCode: PlanCode
  planName: string
  planAnchorAt: string
  quotaBonusTokens: number
  /** 充值购买的 AI 点数余额（1 点 = 1 token，永久有效）；存量用户可能缺省 */
  tokenBalance?: number
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
  payload: {
    isDisabled?: boolean
    isPlatformAdmin?: boolean
    planCode?: PlanCode
    planAnchorAt?: string | null
    quotaBonusTokens?: number
    tokenBalance?: number
  },
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

// ── 订单管理 + 全额退款（/console/orders）────────────────────────

export type AdminOrderStatus = 'created' | 'paid' | 'activated' | 'closed' | 'failed'

export interface AdminOrderRefund {
  outRequestNo: string
  status: 'submitted' | 'success' | 'failed'
  refundAmount: string
  reason: string
  operatorUserId: string
  gatewayCode: string | null
  gatewaySubCode: string | null
  gatewayMsg: string | null
  refundedAt: string | null
  createdAt: string | null
}

export interface AdminOrderItem {
  orderNo: string
  userId: string
  planCode: string
  /** 订单类型：plan 套餐订阅 / token_topup AI 点数充值 / first_month_promo 新人首月体验包 */
  orderType?: 'plan' | 'token_topup' | 'first_month_promo'
  /** 充值到账点数（1 点 = 1 token）；套餐订单为 null */
  tokenAmount?: number | null
  subject: string
  amount: string
  status: AdminOrderStatus
  /** 支付渠道（存量数据为 alipay） */
  channel?: 'alipay' | 'wechat'
  tradeNo: string | null
  paidAt: string | null
  activatedAt: string | null
  expiresAt: string | null
  createdAt: string | null
  /** 最新一条退款记录（无退款为 null；详细历史在详情接口） */
  refund: AdminOrderRefund | null
}

export interface AdminOrderListResponse {
  items: AdminOrderItem[]
  total: number
  page: number
  pageSize: number
}

export interface AdminNotifyLogItem {
  tradeStatus: string
  signVerified: boolean
  result: string
  createdAt: string | null
}

export interface AdminOrderDetail extends AdminOrderItem {
  buyerLogonId: string | null
  refunds: AdminOrderRefund[]
  notifyLogs: AdminNotifyLogItem[]
}

export interface AdminGatewayTrade {
  [key: string]: unknown
}

export async function fetchAdminOrders(params: {
  query?: string
  status?: string
  page: number
  pageSize: number
}): Promise<AdminOrderListResponse> {
  return (await apiClient.get('/api/admin/billing/orders', { params })) as AdminOrderListResponse
}

export async function fetchAdminOrder(orderNo: string): Promise<AdminOrderDetail> {
  return (await apiClient.get(`/api/admin/billing/orders/${orderNo}`)) as AdminOrderDetail
}

export async function refundAdminOrder(orderNo: string, reason: string): Promise<AdminOrderDetail> {
  return (await apiClient.post(`/api/admin/billing/orders/${orderNo}/refund`, {
    reason,
  })) as AdminOrderDetail
}

export async function queryAdminOrder(
  orderNo: string,
): Promise<{ orderNo: string; gatewayTrade: AdminGatewayTrade | null }> {
  return (await apiClient.post(`/api/admin/billing/orders/${orderNo}/query`)) as {
    orderNo: string
    gatewayTrade: AdminGatewayTrade | null
  }
}
