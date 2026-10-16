/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface AuditLogItem {
  id: string
  company_id: string
  user_id: string
  action: string
  entity_type: string
  entity_id: string | null
  detail: string
  created_at: string
}

export interface AuditLogResponse {
  items: AuditLogItem[]
  total: number
  limit: number
  offset: number
}

export function getAuditLogs(limit = 50, offset = 0): Promise<AuditLogResponse> {
  return apiClient.get('/api/audit/logs', { params: { limit, offset } })
}
