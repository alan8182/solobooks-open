/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient, { makeIdempotencyKey } from './client'
import type {
  RecordTextRequest,
  RecordTextResponse,
  RecordReceiptResponse,
  RecordBatchResponse,
  ConfirmRequest,
  ConfirmResponse,
  Entry,
  EntryUpdateRequest,
} from './types'

export function recordText(data: RecordTextRequest): Promise<RecordTextResponse> {
  return apiClient.post('/api/record/text', data)
}

export function recordReceipt(file: File): Promise<RecordReceiptResponse> {
  const formData = new FormData()
  formData.append('image', file)
  return apiClient.post('/api/record/receipt', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

export async function recordSpeech(file: Blob): Promise<{ text: string }> {
  const formData = new FormData()
  formData.append('audio', file, 'speech.webm')
  return (await apiClient.post('/api/record/speech', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })) as unknown as { text: string }
}

export function recordBatch(text: string): Promise<RecordBatchResponse> {
  return apiClient.post('/api/record/batch', { text })
}

export function recordConfirm(data: ConfirmRequest): Promise<ConfirmResponse> {
  return apiClient.post('/api/record/confirm', data, { headers: idempotencyHeaders() })
}

export function getEntries(): Promise<{ entries: Entry[] }> {
  return apiClient.get('/api/record/entries')
}

export function updateEntry(entryId: string, data: EntryUpdateRequest): Promise<{ entry: Entry }> {
  return apiClient.patch(`/api/record/entries/${entryId}`, data, { headers: idempotencyHeaders() })
}

export function deleteEntry(entryId: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/record/entries/${entryId}`, { headers: idempotencyHeaders() })
}

function idempotencyHeaders(): Record<string, string> {
  return { 'Idempotency-Key': makeIdempotencyKey() }
}
