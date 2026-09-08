import apiClient from './client'
import type { TodoItem, TodoStateUpdate } from './types'

export function getTodos(): Promise<{ todos: TodoItem[] }> {
  return apiClient.get('/api/todos')
}

export function updateTodoState(todoKey: string, data: TodoStateUpdate): Promise<{ message: string }> {
  return apiClient.patch(`/api/todos/${todoKey}`, data)
}
