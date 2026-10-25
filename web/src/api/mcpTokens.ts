/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface McpTokenItem {
  id: string
  name: string
  companyId: string
  scopes: string
  lastUsedAt: string | null
  expiresAt: string | null
  revokedAt: string | null
  createdAt: string | null
}

export interface McpTokenCreateRequest {
  name: string
  scopes: string
  company_id?: string
  expires_days?: number
}

export interface McpTokenCreateResponse {
  token: string
  item: Omit<McpTokenItem, 'lastUsedAt' | 'revokedAt'>
  notice: string
}

export function listMcpTokens(): Promise<{ tokens: McpTokenItem[] }> {
  return apiClient.get('/api/mcp/tokens')
}

export function createMcpToken(data: McpTokenCreateRequest): Promise<McpTokenCreateResponse> {
  return apiClient.post('/api/mcp/tokens', data)
}

export function revokeMcpToken(tokenId: string): Promise<{ revoked: boolean }> {
  return apiClient.delete(`/api/mcp/tokens/${tokenId}`)
}
