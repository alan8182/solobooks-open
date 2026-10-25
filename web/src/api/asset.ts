/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  AmortizationRunResult,
  AmortizationScheduleRow,
  DepreciationCatchUpResult,
  FixedAsset,
  FixedAssetCategory,
  FixedAssetCreateRequest,
  FixedAssetDisposal,
  FixedAssetDisposalCreateRequest,
  FixedAssetEntryPrefill,
  FixedAssetFromEntryRequest,
  FixedAssetFromEntryResponse,
  FixedAssetListResponse,
  FixedAssetReconciliation,
  FixedAssetUpdateRequest,
} from './types'

export function getFixedAssetCategories(): Promise<{ items: FixedAssetCategory[] }> {
  return apiClient.get('/api/asset/categories')
}

export function getFixedAssets(params?: {
  status?: string
  category?: string
  keyword?: string
  page?: number
  pageSize?: number
}): Promise<FixedAssetListResponse> {
  return apiClient.get('/api/asset/assets', { params })
}

export function getFixedAsset(assetId: string): Promise<FixedAsset> {
  return apiClient.get(`/api/asset/assets/${assetId}`)
}

export function createFixedAsset(data: FixedAssetCreateRequest): Promise<FixedAsset> {
  return apiClient.post('/api/asset/assets', data)
}

export function getFixedAssetEntryPrefill(entryId: string): Promise<FixedAssetEntryPrefill> {
  return apiClient.get(`/api/asset/assets/from-entry/${entryId}`)
}

export function createFixedAssetFromEntry(
  data: FixedAssetFromEntryRequest
): Promise<FixedAssetFromEntryResponse> {
  return apiClient.post('/api/asset/assets/from-entry', data)
}

export function runFixedAssetDepreciationCatchUp(payload: {
  throughPeriod: string
  assetIds?: string[]
  dryRun?: boolean
}): Promise<DepreciationCatchUpResult> {
  return apiClient.post('/api/asset/depreciation/catch-up', payload)
}

export function updateFixedAsset(assetId: string, data: FixedAssetUpdateRequest): Promise<FixedAsset> {
  return apiClient.put(`/api/asset/assets/${assetId}`, data)
}

export function deleteFixedAsset(assetId: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/asset/assets/${assetId}`)
}

export function submitFixedAsset(
  assetId: string
): Promise<{ asset: FixedAsset; entryStatus: string; journalEntryId: string }> {
  return apiClient.post(`/api/asset/assets/${assetId}/submit`)
}

export function approveFixedAsset(assetId: string): Promise<{ asset: FixedAsset; entryStatus: string }> {
  return apiClient.post(`/api/asset/assets/${assetId}/approve`)
}

export function rejectFixedAsset(assetId: string, reason: string): Promise<{ asset: FixedAsset }> {
  return apiClient.post(`/api/asset/assets/${assetId}/reject`, { reason })
}

export function getFixedAssetSchedule(assetId: string): Promise<{ items: AmortizationScheduleRow[] }> {
  return apiClient.get(`/api/asset/assets/${assetId}/schedule`)
}

export function runFixedAssetDepreciation(payload: {
  period: string
  assetIds?: string[]
  dryRun?: boolean
}): Promise<AmortizationRunResult> {
  return apiClient.post('/api/asset/depreciation/run', payload)
}

export function getFixedAssetDepreciationRuns(period: string): Promise<{
  period: string
  totalAmount: number
  items: {
    assetId: string
    assetCode: string
    assetName: string
    periodStart: string
    sequenceNo: number
    amount: number
    status: string
    journalEntryId?: string
  }[]
}> {
  return apiClient.get('/api/asset/depreciation/runs', { params: { period } })
}

export function getFixedAssetReconciliation(month?: string): Promise<FixedAssetReconciliation> {
  return apiClient.get('/api/asset/reconciliation', { params: month ? { month } : {} })
}

export function runFixedAssetBackfill(): Promise<Record<string, unknown>> {
  return apiClient.get('/api/asset/backfill')
}

export function createFixedAssetDisposal(data: FixedAssetDisposalCreateRequest): Promise<FixedAssetDisposal> {
  return apiClient.post('/api/asset/disposals', data)
}

export function getFixedAssetDisposals(params?: {
  assetId?: string
  status?: string
}): Promise<{ items: FixedAssetDisposal[] }> {
  return apiClient.get('/api/asset/disposals', { params })
}

export function submitFixedAssetDisposal(disposalId: string): Promise<{ ok?: boolean; status: string }> {
  return apiClient.post(`/api/asset/disposals/${disposalId}/submit`)
}

export function approveFixedAssetDisposal(
  disposalId: string
): Promise<{ ok?: boolean; status: string; assetStatus: string; journalEntryId: string }> {
  return apiClient.post(`/api/asset/disposals/${disposalId}/approve`)
}

export function cancelFixedAssetDisposal(
  disposalId: string
): Promise<{ ok?: boolean; status: string; assetStatus: string }> {
  return apiClient.post(`/api/asset/disposals/${disposalId}/cancel`)
}

export function exportFixedAssetLedger(): Promise<Blob> {
  return apiClient.get('/api/asset/export/ledger', { responseType: 'blob' })
}
