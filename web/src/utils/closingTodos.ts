/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { TodoItem } from '@/api/types'
import { closingTodoActionLabel, closingTodoRoute, isFilingTodo } from '@/utils/todoAction'

/** 结账成功页待办卡的单条摘要 */
export interface ClosingTodoItem {
  key: string
  title: string
  desc: string
  level: string
  route: string
  routeLabel: string
}

export interface ClosingTodoSummary {
  pendingCount: number
  topItems: ClosingTodoItem[]
}

const TOP_LIMIT = 3

/**
 * 结账成功后待办摘要：只取未办结条目，紧要的排前（high 优先 → 同级内征期优先 → 后端原序），
 * 最多展示 3 条。纯展示派生，接口失败或为空时由调用方整卡隐藏，不挡结账主流程。
 */
export function buildClosingTodoSummary(todos: TodoItem[]): ClosingTodoSummary {
  const pending = (todos || []).filter((todo) => !todo.done && !todo.dismissed)
  const score = (todo: TodoItem) =>
    (todo.priority === 'high' ? 0 : 1) * 10 + (isFilingTodo(todo) ? 0 : 1)
  const sorted = [...pending].sort((a, b) => score(a) - score(b))
  const topItems: ClosingTodoItem[] = sorted.slice(0, TOP_LIMIT).map((todo) => ({
    key: todo.key || todo.id,
    title: todo.title,
    desc: todo.description || todo.desc || '',
    level: todo.priority || 'medium',
    route: closingTodoRoute(todo),
    routeLabel: closingTodoActionLabel(todo),
  }))
  return { pendingCount: pending.length, topItems }
}
