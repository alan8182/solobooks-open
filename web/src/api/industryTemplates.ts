/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface IndustryAccountPreset {
  code: string
  name: string
  parentCode: string
  type: string
  category: string
  direction: '' | 'debit' | 'credit'
  isLeaf: boolean
}

export interface IndustryTemplateConfig {
  accountPresets: IndustryAccountPreset[]
  modules: string[]
  notes: string[]
}

export interface IndustryTemplateItem {
  id: string
  code: string
  name: string
  groupCode: string
  groupName: string
  description: string
  restrictedDefault: boolean
  hidden: boolean
  enabled: boolean
  sortOrder: number
  version: number
  config: IndustryTemplateConfig
  isSystem: boolean
  createdBy: string
  createdAt: string | null
  updatedAt: string | null
}

export interface IndustryTemplateListResponse {
  items: IndustryTemplateItem[]
  total: number
}

export async function getIndustryTemplates(params?: {
  q?: string
  includeHidden?: boolean
}): Promise<IndustryTemplateListResponse> {
  return (await apiClient.get('/api/industry-templates', {
    params: { includeHidden: true, ...params },
  })) as IndustryTemplateListResponse
}

export async function getAdminIndustryTemplates(params?: {
  q?: string
}): Promise<IndustryTemplateListResponse> {
  return (await apiClient.get('/api/admin/industry-templates', {
    params,
  })) as IndustryTemplateListResponse
}

export type IndustryTemplateUpsertPayload = Omit<
  IndustryTemplateItem,
  'id' | 'version' | 'isSystem' | 'createdBy' | 'createdAt' | 'updatedAt'
>

export async function createAdminIndustryTemplate(
  payload: IndustryTemplateUpsertPayload,
): Promise<IndustryTemplateItem> {
  return (await apiClient.post('/api/admin/industry-templates', payload)) as IndustryTemplateItem
}

export async function updateAdminIndustryTemplate(
  id: string,
  payload: IndustryTemplateUpsertPayload,
): Promise<IndustryTemplateItem> {
  return (await apiClient.put(
    `/api/admin/industry-templates/${encodeURIComponent(id)}`,
    payload,
  )) as IndustryTemplateItem
}
