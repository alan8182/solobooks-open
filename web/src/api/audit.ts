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
