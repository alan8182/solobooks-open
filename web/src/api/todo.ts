/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { TodoItem, TodoStateUpdate } from './types'

export interface SilentResolvedItem {
  id: string
  entryId?: string
  title: string
  amount: number
  date: string
  reason: string
  category?: string
  categoryLabel?: string
}

export interface GetTodosResponse {
  todos: TodoItem[]
  autoResolved?: SilentResolvedItem[]
  autoResolvedCount?: number
}

export function getTodos(): Promise<GetTodosResponse> {
  return apiClient.get('/api/todos')
}

export function updateTodoState(todoKey: string, data: TodoStateUpdate): Promise<{ message: string }> {
  return apiClient.patch(`/api/todos/${todoKey}`, data)
}

export function requestTodoAiAdvice(): Promise<{ todos: TodoItem[] }> {
  return apiClient.post('/api/todos/ai-advise')
}

export function linkInvoiceToExpense(invoiceEntryId: string, expenseEntryId: string): Promise<{ success: boolean; result: any }> {
  return apiClient.post('/api/todos/link-invoice', { invoiceEntryId, expenseEntryId })
}
