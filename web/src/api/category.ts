/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface CategoryItem {
  id: string
  company_id: string
  name: string
  direction: 'income' | 'expense' | 'owner_transfer'
  parent_id: string | null
  sort_order: number
  is_system: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CategoryCreateRequest {
  name: string
  direction: 'income' | 'expense' | 'owner_transfer'
  parent_id?: string
  sort_order?: number
}

export function getCategories(): Promise<CategoryItem[]> {
  return apiClient.get('/api/categories')
}

export function createCategory(data: CategoryCreateRequest): Promise<CategoryItem> {
  return apiClient.post('/api/categories', data)
}

export function updateCategory(id: string, data: Partial<CategoryCreateRequest & { is_active: boolean }>): Promise<CategoryItem> {
  return apiClient.put(`/api/categories/${id}`, data)
}

export function deleteCategory(id: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/categories/${id}`)
}
