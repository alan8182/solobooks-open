/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { TodoItem } from '@/api/types'

export function isFixedAssetTodo(todo: TodoItem): boolean {
  return todo.meta?.action === 'confirm_asset'
    || todo.meta?.ruleId === 'fixed_asset_depreciation'
    || todo.title.includes('固定资产')
}

export function fixedAssetTodoRoute(todo: TodoItem): string {
  const entryId = todo.meta?.entryId || todo.entryId
  return entryId
    ? `/assets/create?sourceEntryId=${encodeURIComponent(String(entryId))}`
    : '/assets'
}

export function isIntangibleAssetTodo(todo: TodoItem): boolean {
  return todo.meta?.action === 'confirm_intangible'
    || todo.meta?.ruleId === 'intangible_asset_amortization'
    || (todo.title.includes('无形资产') && todo.title.includes('摊销方案'))
}

export function intangibleAssetTodoRoute(todo: TodoItem): string {
  const entryId = todo.meta?.entryId || todo.entryId
  return entryId
    ? `/intangibles/create?sourceEntryId=${encodeURIComponent(String(entryId))}`
    : '/intangibles'
}
