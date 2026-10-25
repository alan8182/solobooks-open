/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export function parseWechatBill(file: File): Promise<{
  valid: Record<string, any>[]
  errors: { row: number; reason: string }[]
  summary: Record<string, any>
  total: number
}> {
  const form = new FormData()
  form.append('file', file)
  return apiClient.post('/api/wechat-bill/parse', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function importWechatBill(
  rows: Record<string, any>[],
  opts: {
    allowBackfill?: boolean
    backfillReason?: string
    importMode?: 'summary' | 'detail'
    summary?: Record<string, any>
    occurDate?: string
  } = {},
): Promise<{
  imported: number
  failed: number
  ignored_duplicates: number
  results: { status: string; title: string; amount?: number; error?: string }[]
}> {
  return apiClient.post('/api/wechat-bill/import', {
    rows,
    importMode: opts.importMode || 'summary',
    summary: opts.summary,
    occurDate: opts.occurDate,
    allowBackfill: opts.allowBackfill || undefined,
    backfillReason: opts.backfillReason || undefined,
  })
}

export function fetchWechatBillRecords(month: string): Promise<{
  month: string
  items: Record<string, any>[]
  summary: { total: number; totalOrderAmount: number; totalFee: number; totalSettled: number }
}> {
  return apiClient.get('/api/wechat-bill/records', { params: { month } })
}
