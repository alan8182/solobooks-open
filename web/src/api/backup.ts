/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface BackupRecordItem {
  id: string
  fileName: string
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

export async function downloadBackupRecord(id: string, fileName: string): Promise<void> {
  const blob = await apiClient.get(`/api/backup/records/${id}/download`, {
    responseType: 'blob',
    timeout: 120000,
  })
  const url = URL.createObjectURL(blob as unknown as Blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.click()
  URL.revokeObjectURL(url)
}
