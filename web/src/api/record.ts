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
  VoucherPreview,
  VoucherPreviewRequest,
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
  const filename = file.type.includes('wav') ? 'speech.wav' : 'speech.webm'
  formData.append('audio', file, filename)
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

/**
 * 结账向导第二步：预览这笔单子按当前付款方式/分类将落哪个科目。
 * 只读接口，与「认可入账」共用后端同一条规则路径。
 */
export function previewEntryVoucher(
  entryId: string,
  data: VoucherPreviewRequest,
): Promise<VoucherPreview> {
  return apiClient.post(`/api/record/entries/${entryId}/voucher-preview`, data, { timeout: 30000 })
}

export function deleteEntry(entryId: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/record/entries/${entryId}`, { headers: idempotencyHeaders() })
}

/** 前期已付款候选：金额一致、日期不晚于发票日、公户已入账的凭证（跨期允许）。 */
export interface PriorPaymentCandidate {
  entryId: string
  summary: string
  occurredOn: string
  status: string
  amount: string
  statementDescription: string
  confidence: 'counterparty' | 'text' | 'amount_only'
}

/**
 * 批量检测发票挂账凭证可挂接的前期付款凭证（只读）。
 * 返回按 entryId 分组的候选列表；非发票凭证返回空数组。
 */
export function fetchPriorPaymentCandidates(
  entryIds: string[],
): Promise<{ candidates: Record<string, PriorPaymentCandidate[]> }> {
  return apiClient.post('/api/invoices/prior-payment/candidates', { entryIds }, { timeout: 30000 })
}

/**
 * 确认挂接：发票主数据迁到前期凭证 + 删除本月挂账草稿（本月不再重复入账）。
 * 幂等键防重复点击；重复点击返回首次结果。
 */
export function linkPriorPayment(
  invoiceEntryId: string,
  priorEntryId: string,
): Promise<{ invoiceIds: string[]; priorEntry: { id: string; summary: string; occurredOn: string; status: string } }> {
  return apiClient.post(
    '/api/invoices/prior-payment/link',
    { invoiceEntryId, priorEntryId },
    { headers: idempotencyHeaders() },
  )
}

function idempotencyHeaders(): Record<string, string> {
  return { 'Idempotency-Key': makeIdempotencyKey() }
}
