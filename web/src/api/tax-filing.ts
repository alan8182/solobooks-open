import apiClient from './client'

export function markDeadlineFiled(
  deadlineId: string,
  evidence?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/tax-filing/items/${deadlineId}/file`, { evidence }, {
    headers: { 'Content-Type': 'application/json' },
  })
}
