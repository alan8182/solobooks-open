import apiClient from './client'
import type { Entry } from './types'

const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() })

export function postEntry(entryId: string): Promise<{ entry: Entry }> {
  return apiClient.post(`/api/record/entries/${entryId}/post`, {}, { headers: headers() })
}

export function approveEntry(entryId: string): Promise<{ entry: Entry }> {
  return apiClient.post(`/api/record/entries/${entryId}/approve`, {}, { headers: headers() })
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

export function getPeriodCloseChecks(month: string): Promise<Record<string, unknown>> {
  return apiClient.get(`/api/periods/${month}/close-checks`)
}

export function closePeriod(month: string): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/periods/${month}/close`, {}, { headers: headers() })
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
