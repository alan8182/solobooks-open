/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { TodoItem } from '@/api/types'
import { periodToQuarter } from '@/utils/taxFiling'

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

// 从征期待办标题（如「征期临近 - 增值税（2025-10）」）里提取期次
export function extractFilingPeriod(title: string): string {
  const match = title.match(/（([^）]+)）/)
  return match ? match[1] : '本期'
}

/** 单条待办的「去处理」跳转目标；/todos 页与结账成功页共用这一份路由判断 */
export function getActionRoute(todo: TodoItem): string | null {
  if (todo.meta?.ruleId === 'tax_registration_unsynced') {
    return '/tax-registration'
  }
  if (isFixedAssetTodo(todo)) {
    return fixedAssetTodoRoute(todo)
  }
  if (isIntangibleAssetTodo(todo)) {
    return intangibleAssetTodoRoute(todo)
  }
  if (todo.meta?.entryId) {
    return `/ledger/${todo.meta.entryId}`
  }
  if (todo.entryId) {
    return `/ledger/${todo.entryId}`
  }
  if (todo.type === 'bank' || todo.meta?.statementId) {
    // 直达结账向导第四步配对现场，并带上流水定位参数
    const month = String(todo.meta?.date || '').slice(0, 7)
    const query = new URLSearchParams()
    if (month) query.set('month', month)
    query.set('step', '4')
    if (todo.meta?.statementId) query.set('statement', String(todo.meta.statementId))
    return `/closing-wizard?${query.toString()}`
  }
  if (todo.meta?.action === 'confirm_asset' || todo.title.includes('固定资产')) {
    return '/assets'
  }
  if (todo.meta?.action === 'review_intangible' || todo.meta?.action === 'view_intangible') {
    return `/intangibles/${todo.meta?.assetId || ''}`
  }
  if (todo.title.includes('无形资产')) {
    return '/intangibles'
  }
  return null
}

export function getActionLabel(todo: TodoItem): string {
  if (todo.meta?.ruleId === 'tax_registration_unsynced') {
    return '去同步认定'
  }
  if (isFixedAssetTodo(todo)) {
    return '确认折旧方案'
  }
  if (isIntangibleAssetTodo(todo)) {
    return '确认摊销方案'
  }
  if (todo.meta?.entryId || todo.entryId) {
    return '查看关联交易'
  }
  if (todo.type === 'bank') {
    return '去流水对账'
  }
  if (todo.meta?.action === 'confirm_asset' || todo.title.includes('固定资产')) {
    return '设置折旧'
  }
  if (todo.meta?.action === 'review_intangible') {
    return '去复核'
  }
  if (todo.meta?.action === 'view_intangible') {
    return '查看资产'
  }
  return '去处理'
}

export function isFilingTodo(todo: TodoItem): boolean {
  return todo.type === 'filing' || todo.category === 'filing'
}

/** 结账成功页待办卡的跳转：征期直达申报作业台对应季度，其余无专页时兜底待办中心 */
export function closingTodoRoute(todo: TodoItem): string {
  if (isFilingTodo(todo)) {
    const period = extractFilingPeriod(todo.title)
    return /^\d{4}-/.test(period) ? `/tax-filing?period=${periodToQuarter(period)}` : '/tax-filing'
  }
  return getActionRoute(todo) ?? '/todos'
}

export function closingTodoActionLabel(todo: TodoItem): string {
  if (isFilingTodo(todo)) {
    return '去申报'
  }
  return getActionRoute(todo) ? getActionLabel(todo) : '去处理'
}
