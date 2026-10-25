/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 税费种认定 API。
// 调用铁律：client.ts 的响应拦截器已 return response.data，所以是
// `const data = await apiClient.get(...)`；禁止 `const { data } =` 解构。

import apiClient from './client'

export type FilingCycle = 'month' | 'quarter' | 'year' | 'other'
export type MainFlag = 'main' | 'vat_attachment'
export type CollectionMethod = 'self' | 'withholding'

export interface TaxRegistrationRow {
  id: string | null
  seq: number
  levyItem: string
  levyCategory: string
  levySubItem: string
  filingCycle: FilingCycle
  filingCycleText: string
  deadlineText: string
  deadlineRule: string
  deadlineRuleLabel: string
  dueMonthOffset: number | null
  dueDay: number | null
  levyRate: number | null
  taxRate: number | null
  rateUnit: string
  mainFlag: MainFlag
  collectionMethod: CollectionMethod
  returnGroup: string
  returnGroupLabel: string
  status: 'active' | 'inactive'
  source: string
  note: string
}

export interface TaxRegistrationList {
  items: TaxRegistrationRow[]
  stats: { total: number; active: number; groups: Record<string, number> }
}

export interface ScheduleItem {
  returnGroup: string
  name: string
  period: string
  cycle: FilingCycle
  dueDate: string
  status: string
  deadlineId: string | null
  daysLeft: number
}

export interface SchedulePeriodBucket {
  period: string
  dueDate: string
  items: Array<Pick<ScheduleItem, 'returnGroup' | 'name' | 'dueDate' | 'status' | 'deadlineId' | 'daysLeft'>>
}

export interface ScheduleMonthBucket {
  month: string
  items: ScheduleItem[]
}

export interface SchedulePreview {
  year: number
  source: 'registration' | 'default'
  cycles: { vat: string; business_income_iit_prepay: string }
  conflicts: Array<{ levyItem: string; levyCategory: string; returnGroup: string }>
  summary: { itemCount: number; groupCount: number; periodCount: number }
  groups: Array<{
    returnGroup: string
    name: string
    registrations: TaxRegistrationRow[]
    periods: Array<Omit<ScheduleItem, 'returnGroup' | 'name'> & { returnGroup: null; name: null }>
  }>
  byPeriod: SchedulePeriodBucket[]
  byDueMonth: ScheduleMonthBucket[]
}

export interface ScheduleDiff {
  year: number
  source: 'registration' | 'default'
  willCreateCount: number
  willWaiveCount: number
  willRestoreCount: number
  willCreate: Array<{ taxKind: string; name: string; period: string }>
  willWaive: Array<{ taxKind: string; name: string; period: string }>
  willRestore: Array<{ taxKind: string; name: string; period: string }>
}

export interface ImportResult {
  created: number
  updated: number
  deactivated: number
  total: number
  refresh?: { created: number; autoWaived: number; source: string }
}

export interface ParseResult {
  items: TaxRegistrationRow[]
  errors: string[]
}

export interface TaxRegistrationInput {
  levyItem: string
  levyCategory?: string
  levySubItem?: string
  filingCycle: FilingCycle
  deadlineText?: string
  levyRate?: string | number | null
  taxRate?: string | number | null
  mainFlag?: MainFlag
  collectionMethod?: CollectionMethod
  note?: string
}

export async function listTaxRegistrations(includeInactive = false): Promise<TaxRegistrationList> {
  const data = await apiClient.get('/api/tax-registrations', { params: { include_inactive: includeInactive } })
  return data as unknown as TaxRegistrationList
}

export async function createTaxRegistration(payload: TaxRegistrationInput): Promise<TaxRegistrationRow> {
  const data = await apiClient.post('/api/tax-registrations', payload)
  return data as unknown as TaxRegistrationRow
}

export async function updateTaxRegistration(
  registrationId: string,
  payload: TaxRegistrationInput,
): Promise<TaxRegistrationRow> {
  const data = await apiClient.put(`/api/tax-registrations/${registrationId}`, payload)
  return data as unknown as TaxRegistrationRow
}

export async function deactivateTaxRegistration(registrationId: string): Promise<TaxRegistrationRow> {
  const data = await apiClient.post(`/api/tax-registrations/${registrationId}/deactivate`)
  return data as unknown as TaxRegistrationRow
}

/** 只解析不落库：给导入弹窗做确认前预览。 */
export async function parseTaxRegistrationRows(rows: Array<Record<string, unknown>>): Promise<ParseResult> {
  const data = await apiClient.post('/api/tax-registrations/parse', { rows })
  return data as unknown as ParseResult
}

export async function importTaxRegistrations(
  rows: Array<Record<string, unknown>>,
  mode: 'merge' | 'replace' = 'merge',
  source: 'paste' | 'ocr' | 'manual' | 'template' = 'paste',
): Promise<ImportResult> {
  const data = await apiClient.post('/api/tax-registrations/import', { rows, mode, source })
  return data as unknown as ImportResult
}

export async function buildTaxRegistrationTemplate(entityType?: string): Promise<{
  entityType: string
  notice: string
  items: TaxRegistrationRow[]
  rows: Array<Record<string, string>>
}> {
  const data = await apiClient.post('/api/tax-registrations/template', entityType ? { entityType } : {})
  return data as unknown as {
    entityType: string
    notice: string
    items: TaxRegistrationRow[]
    rows: Array<Record<string, string>>
  }
}

export async function previewTaxSchedule(year: number): Promise<SchedulePreview> {
  const data = await apiClient.get('/api/tax-registrations/preview', { params: { year } })
  return data as unknown as SchedulePreview
}

export async function diffTaxSchedule(year: number): Promise<ScheduleDiff> {
  const data = await apiClient.get('/api/tax-registrations/diff', { params: { year } })
  return data as unknown as ScheduleDiff
}

export interface ApplyResult {
  year: number
  created: number
  autoWaived: number
  source: 'registration' | 'default'
  cycles: { vat: string; business_income_iit_prepay: string }
}

export async function applyTaxSchedule(year: number): Promise<ApplyResult> {
  const data = await apiClient.post('/api/tax-registrations/apply', null, { params: { year } })
  return data as unknown as ApplyResult
}

/** 上传税局认定表截图，返回待确认的行（不落库）。 */
export async function recognizeTaxRegistration(file: File): Promise<ParseResult & { rawCount: number }> {
  const body = new FormData()
  body.append('file', file)
  const data = await apiClient.post('/api/tax-registrations/recognize', body, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return data as unknown as ParseResult & { rawCount: number }
}
