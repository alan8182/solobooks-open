/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { TodoItem, TodoStateUpdate } from './types'

export function getTodos(): Promise<{ todos: TodoItem[] }> {
  return apiClient.get('/api/todos')
}

export function updateTodoState(todoKey: string, data: TodoStateUpdate): Promise<{ message: string }> {
  return apiClient.patch(`/api/todos/${todoKey}`, data)
}
