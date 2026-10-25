/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  A105080Preview,
  AmortizationCatchUpResult,
  AmortizationRunResult,
  AmortizationScheduleRow,
  IntangibleAsset,
  IntangibleAssetChangeResult,
  IntangibleAssetCreateRequest,
  IntangibleAssetEntryPrefill,
  IntangibleAssetFromEntryRequest,
  IntangibleAssetFromEntryResponse,
  IntangibleAssetListResponse,
  IntangibleAssetUpdateRequest,
  IntangibleCategory,
  IntangibleDisposal,
  IntangibleDisposalCreateRequest,
  IntangibleImpairmentTest,
  IntangibleImpairmentTestCreateRequest,
} from './types'

export function getIntangibleCategories(): Promise<{ items: IntangibleCategory[] }> {
  return apiClient.get('/api/intangible/categories')
}

export function getIntangibleAssets(params?: {
  status?: string
  category?: string
  keyword?: string
  expiringInDays?: number
  page?: number
  pageSize?: number
}): Promise<IntangibleAssetListResponse> {
  return apiClient.get('/api/intangible/assets', { params })
}

export function getIntangibleAsset(assetId: string): Promise<IntangibleAsset> {
  return apiClient.get(`/api/intangible/assets/${assetId}`)
}

export function createIntangibleAsset(data: IntangibleAssetCreateRequest): Promise<IntangibleAsset> {
  return apiClient.post('/api/intangible/assets', data)
}

export function getIntangibleAssetEntryPrefill(entryId: string): Promise<IntangibleAssetEntryPrefill> {
  return apiClient.get(`/api/intangible/assets/from-entry/${entryId}`)
}

export function createIntangibleAssetFromEntry(
  data: IntangibleAssetFromEntryRequest
): Promise<IntangibleAssetFromEntryResponse> {
  return apiClient.post('/api/intangible/assets/from-entry', data)
}

export function runIntangibleAmortizationCatchUp(payload: {
  throughPeriod: string
  assetIds?: string[]
  dryRun?: boolean
}): Promise<AmortizationCatchUpResult> {
  return apiClient.post('/api/intangible/amortization/catch-up', payload)
}

export function updateIntangibleAsset(assetId: string, data: IntangibleAssetUpdateRequest): Promise<IntangibleAsset> {
  return apiClient.put(`/api/intangible/assets/${assetId}`, data)
}

export function deleteIntangibleAsset(assetId: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/intangible/assets/${assetId}`)
}

export function submitIntangibleAsset(
  assetId: string
): Promise<{ asset: IntangibleAsset; entryStatus: string; journalEntryId: string }> {
  return apiClient.post(`/api/intangible/assets/${assetId}/submit`)
}

export function approveIntangibleAsset(
  assetId: string
): Promise<{ asset: IntangibleAsset; entryStatus: string }> {
  return apiClient.post(`/api/intangible/assets/${assetId}/approve`)
}

export function rejectIntangibleAsset(
  assetId: string,
  reason: string
): Promise<{ asset: IntangibleAsset }> {
  return apiClient.post(`/api/intangible/assets/${assetId}/reject`, { reason })
}

export function getIntangibleSchedule(assetId: string): Promise<{ items: AmortizationScheduleRow[] }> {
  return apiClient.get(`/api/intangible/assets/${assetId}/schedule`)
}

export function runIntangibleAmortization(payload: {
  period: string
  assetIds?: string[]
  dryRun?: boolean
}): Promise<AmortizationRunResult> {
  return apiClient.post('/api/intangible/amortization/run', payload)
}

export function createIntangibleDisposal(data: IntangibleDisposalCreateRequest): Promise<IntangibleDisposal> {
  return apiClient.post('/api/intangible/disposals', data)
}

export function getIntangibleDisposals(params?: {
  assetId?: string
  status?: string
}): Promise<{ items: IntangibleDisposal[] }> {
  return apiClient.get('/api/intangible/disposals', { params })
}

export function submitIntangibleDisposal(disposalId: string): Promise<{ ok?: boolean }> {
  return apiClient.post(`/api/intangible/disposals/${disposalId}/submit`)
}

export function approveIntangibleDisposal(disposalId: string): Promise<{ ok?: boolean }> {
  return apiClient.post(`/api/intangible/disposals/${disposalId}/approve`)
}

export function cancelIntangibleDisposal(disposalId: string): Promise<{ ok?: boolean }> {
  return apiClient.post(`/api/intangible/disposals/${disposalId}/cancel`)
}

export function createIntangibleImpairmentTest(
  data: IntangibleImpairmentTestCreateRequest
): Promise<IntangibleImpairmentTest> {
  return apiClient.post('/api/intangible/impairment-tests', data)
}

export function getIntangibleImpairmentTests(params?: {
  assetId?: string
}): Promise<{ items: IntangibleImpairmentTest[]; unreviewedCount: number }> {
  return apiClient.get('/api/intangible/impairment-tests', { params })
}

export function reviewIntangibleImpairmentTest(testId: string): Promise<IntangibleImpairmentTest> {
  return apiClient.post(`/api/intangible/impairment-tests/${testId}/review`)
}

export function applyIntangibleLifeChange(
  assetId: string,
  data: { usefulLifeMonths: number; residualValue?: number; effectivePeriod?: string }
): Promise<IntangibleAssetChangeResult> {
  return apiClient.post(`/api/intangible/assets/${assetId}/changes`, data)
}

export function getExpiringIntangibles(days = 90): Promise<IntangibleAssetListResponse> {
  return apiClient.get('/api/intangible/reports/expiring', { params: { days } })
}

export function getA105080Preview(year: number): Promise<A105080Preview> {
  return apiClient.get('/api/intangible/reports/a105080-preview', { params: { year } })
}

export function exportIntangibleLedger(): Promise<Blob> {
  return apiClient.get('/api/intangible/export/ledger', { responseType: 'blob' })
}
