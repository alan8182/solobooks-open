import apiClient from './client'
import type { FixedAsset, FixedAssetCreateRequest, FixedAssetUpdateRequest } from './types'

export function getFixedAssets(): Promise<{ assets: FixedAsset[] }> {
  return apiClient.get('/api/asset/assets')
}

export function createFixedAsset(data: FixedAssetCreateRequest): Promise<FixedAsset> {
  return apiClient.post('/api/asset/assets', data)
}

export function updateFixedAsset(assetId: string, data: FixedAssetUpdateRequest): Promise<FixedAsset> {
  return apiClient.put(`/api/asset/assets/${assetId}`, data)
}

export function deleteFixedAsset(assetId: string): Promise<{ message: string }> {
  return apiClient.delete(`/api/asset/assets/${assetId}`)
}

export function runDepreciation(period: string): Promise<any> {
  return apiClient.post('/api/asset/depreciation/run', null, { params: { period } })
}

export function getDepreciationSchedule(assetId?: string): Promise<{ items: any[] }> {
  const params: any = {}
  if (assetId) params.asset_id = assetId
  return apiClient.get('/api/asset/depreciation/schedule', { params })
}
