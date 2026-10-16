/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface PrivateExpense {
  id: string
  company_id: string
  user_id: string
  title: string
  amount: number
  direction?: 'income' | 'expense'
  category: string
  occur_date: string
  payer: string
  owner_name?: string
  private_account?: string
  is_available?: boolean | number
  memo: string
  invoice_status?: 'no_invoice' | 'has_invoice' | 'substitute'
  substitute_entry_id?: string
  substitute_memo?: string
  created_at: string
  updated_at: string
}

export interface PrivateExpenseCreateRequest {
  title: string
  amount: number
  direction?: 'income' | 'expense'
  category?: string
  occur_date?: string
  payer?: string
  owner_name?: string
  private_account?: string
  is_available?: boolean
  memo?: string
  invoice_status?: 'no_invoice' | 'has_invoice' | 'substitute'
  substitute_entry_id?: string
  substitute_memo?: string
}

export interface PrivateExpenseListResponse {
  items: PrivateExpense[]
  total: number
  page: number
  page_size: number
}

export interface PrivateExpenseSummary {
  count: number
  total_amount: number
  income_amount: number
  expense_amount: number
  deposit_amount?: number
  available_income_amount?: number
  offset_amount: number
  balance_amount: number
  owners?: PrivateExpenseOwnerSummary[]
}

export interface PrivateExpenseOwnerSummary {
  owner_name: string
  count: number
  income_amount: number
  expense_amount: number
  deposit_amount: number
  available_income_amount: number
  offset_amount: number
  balance_amount: number
}

export function getExpenses(page = 1, pageSize = 20, ownerName = ''): Promise<PrivateExpenseListResponse> {
  return apiClient.get('/api/private-expenses', { params: { page, page_size: pageSize, owner_name: ownerName } })
}

export async function getAllExpenses(ownerName = ''): Promise<PrivateExpense[]> {
  const pageSize = 200
  const items: PrivateExpense[] = []
  let page = 1

  while (true) {
    const response = await getExpenses(page, pageSize, ownerName)
    items.push(...response.items)
    if (items.length >= response.total || response.items.length === 0) break
    page += 1
  }

  return items
}

export function getExpenseSummary(ownerName = ''): Promise<PrivateExpenseSummary> {
  return apiClient.get('/api/private-expenses/summary', { params: { owner_name: ownerName } })
}

export function getExpense(id: string): Promise<PrivateExpense> {
  return apiClient.get(`/api/private-expenses/${id}`)
}

export function createExpense(data: PrivateExpenseCreateRequest): Promise<PrivateExpense> {
  return apiClient.post('/api/private-expenses', data)
}

export function updateExpense(id: string, data: Partial<PrivateExpenseCreateRequest>): Promise<PrivateExpense> {
  return apiClient.put(`/api/private-expenses/${id}`, data)
}

export function deleteExpense(id: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/private-expenses/${id}`)
}
