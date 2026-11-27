/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export type AuxiliaryKind = 'partner' | 'department' | 'project' | 'employee'

export interface AuxiliaryItem {
  id: string
  companyId: string
  code: string
  name: string
  isActive: boolean
  createdAt: string
  updatedAt: string
  // partner 专属
  type?: 'customer' | 'supplier' | 'both' | 'person'
  taxId?: string
  contact?: string
  phone?: string
  address?: string
  mnemonic?: string
  // employee 专属
  department?: string
  isOwner?: boolean
}

export interface AuxiliaryListResponse {
  items: AuxiliaryItem[]
  total: number
  page: number
  pageSize: number
}

const PATH: Record<AuxiliaryKind, string> = {
  partner: '/api/partners',
  department: '/api/departments',
  project: '/api/projects',
  employee: '/api/employees',
}

export function listAuxiliary(
  kind: AuxiliaryKind,
  params: { keyword?: string; includeInactive?: boolean; page?: number; pageSize?: number } = {},
): Promise<AuxiliaryListResponse> {
  return apiClient.get(PATH[kind], { params })
}

export function createAuxiliary(kind: AuxiliaryKind, payload: Record<string, unknown>): Promise<AuxiliaryItem> {
  return apiClient.post(PATH[kind], payload)
}

export function updateAuxiliary(
  kind: AuxiliaryKind,
  id: string,
  payload: Record<string, unknown>,
): Promise<AuxiliaryItem> {
  return apiClient.put(`${PATH[kind]}/${id}`, payload)
}

export function deactivateAuxiliary(kind: AuxiliaryKind, id: string): Promise<{ ok: boolean }> {
  return apiClient.post(`${PATH[kind]}/${id}/deactivate`)
}

export function deleteAuxiliary(kind: AuxiliaryKind, id: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`${PATH[kind]}/${id}`)
}

export interface AuxiliaryOptions {
  partners: AuxiliaryItem[]
  departments: AuxiliaryItem[]
  projects: AuxiliaryItem[]
  employees: AuxiliaryItem[]
}

export function getAuxiliaryOptions(keyword = '', limit = 20): Promise<AuxiliaryOptions> {
  return apiClient.get('/api/auxiliary/options', { params: { keyword, limit } })
}

export function assignEntryAuxiliary(
  entryId: string,
  payload: {
    partner_id?: string | null
    department_id?: string | null
    project_id?: string | null
    employee_id?: string | null
  },
): Promise<{ ok: boolean; updatedLines: number }> {
  return apiClient.put(`/api/record/entries/${entryId}/auxiliary`, payload)
}
