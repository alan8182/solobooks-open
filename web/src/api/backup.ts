/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import { saveBlobFile } from '@/utils/download'

export interface BackupRecordItem {
  id: string
  fileName: string
  fileFormat?: 'xlsx' | 'pdf'
  scope: string
  periodFrom: string
  periodTo: string
  sizeBytes: number
  voucherCount: number
  fingerprint: string
  createdBy: string
  createdAt: string
  exists: boolean
}

export function getBackupRecords(limit = 50): Promise<{ items: BackupRecordItem[] }> {
  return apiClient.get('/api/backup/records', { params: { limit } })
}

export async function createArchive(year: number, months?: string): Promise<Blob> {
  // 响应拦截器已把 blob 直通返回（不带 headers），文件名由调用方按期间生成
  return apiClient.post('/api/backup/archive', null, {
    params: { year, months: months || undefined },
    responseType: 'blob',
    timeout: 120000,
  }) as unknown as Promise<Blob>
}

export async function createLedgerPdfArchive(params: {
  year: number
  month?: string
  scope?: 'month' | 'year'
  snapshotId?: string
}): Promise<Blob> {
  return apiClient.post('/api/backup/ledger-pdf', null, {
    params: {
      year: params.year,
      ...(params.month ? { month: params.month } : {}),
      scope: params.scope || 'year',
      ...(params.snapshotId ? { snapshot_id: params.snapshotId } : {}),
    },
    responseType: 'blob',
    timeout: 120000,
  }) as unknown as Promise<Blob>
}

export async function downloadBackupRecord(id: string, fileName: string): Promise<void> {
  const blob = await apiClient.get(`/api/backup/records/${id}/download`, {
    responseType: 'blob',
    timeout: 120000,
  })
  saveBlobFile(blob as unknown as Blob, fileName)
}
