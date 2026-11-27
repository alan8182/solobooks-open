/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export type PlatformBillPlatform = 'wechat' | 'alipay'

export interface PlatformBillParseResult {
  platform: PlatformBillPlatform
  valid: Record<string, any>[]
  errors: { row: number; reason: string }[]
  warnings?: { reason: string }[]
  summary: Record<string, any>
  total: number
}

export interface PlatformBillImportResult {
  imported: number
  failed: number
  ignored_duplicates: number
  results: { status: string; title: string; amount?: number; error?: string }[]
}

/** 上传平台账单，后端按文件内容自动识别微信 / 支付宝。 */
export function parsePlatformBill(file: File): Promise<PlatformBillParseResult> {
  const form = new FormData()
  form.append('file', file)
  return apiClient.post('/api/platform-bill/parse', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function importPlatformBill(
  platform: PlatformBillPlatform,
  rows: Record<string, any>[],
  opts: {
    allowBackfill?: boolean
    backfillReason?: string
    importMode?: 'summary' | 'detail'
    summary?: Record<string, any>
    occurDate?: string
  } = {},
): Promise<PlatformBillImportResult> {
  return apiClient.post('/api/platform-bill/import', {
    platform,
    rows,
    importMode: opts.importMode || 'summary',
    summary: opts.summary,
    occurDate: opts.occurDate,
    allowBackfill: opts.allowBackfill || undefined,
    backfillReason: opts.backfillReason || undefined,
  })
}

/** 已导入记录；不传 platform 时合并微信与支付宝。 */
export function fetchPlatformBillRecords(
  month: string,
  platform?: PlatformBillPlatform,
): Promise<{
  month: string
  items: Record<string, any>[]
  summary: {
    total: number
    totalOrderAmount: number
    totalFee: number
    totalSettled: number
    totalSubsidy?: number
  }
}> {
  return apiClient.get('/api/platform-bill/records', { params: { month, platform } })
}
