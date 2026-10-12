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
