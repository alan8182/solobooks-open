/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { buildClosingTodoSummary } from '@/utils/closingTodos'
import type { TodoItem } from '@/api/types'

function todo(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    id: 'todo-1',
    key: 'todo-1',
    type: 'task',
    title: '办公用品采购 - 补充票据',
    desc: '缺少对应发票',
    priority: 'medium',
    done: false,
    dismissed: false,
    meta: { entryId: 'entry-1', ruleId: 'missing_document' },
    ...overrides,
  }
}

describe('buildClosingTodoSummary', () => {
  it('counts only pending todos and drops done and dismissed items', () => {
    const summary = buildClosingTodoSummary([
      todo(),
      todo({ id: 'todo-2', key: 'todo-2', done: true }),
      todo({ id: 'todo-3', key: 'todo-3', dismissed: true }),
    ])
    expect(summary.pendingCount).toBe(1)
    expect(summary.topItems).toHaveLength(1)
    expect(summary.topItems[0].key).toBe('todo-1')
  })

  it('puts high priority first and filing category ahead within the same level', () => {
    const summary = buildClosingTodoSummary([
      todo({ id: 't-1', key: 't-1', priority: 'medium' }),
      todo({
        id: 't-2', key: 't-2', priority: 'high', title: '招待费复核',
        meta: { ruleId: 'entertainment_limit' },
      }),
      todo({
        id: 't-3', key: 't-3', priority: 'medium', type: 'filing', category: 'filing',
        title: '征期临近 - 增值税（2026-10）', meta: { ruleId: 'filing_deadline' },
      }),
    ])
    expect(summary.topItems.map((item) => item.key)).toEqual(['t-2', 't-3', 't-1'])
    // 征期条目直达申报作业台对应季度
    expect(summary.topItems[1].route).toBe('/tax-filing?period=2026-Q4')
    expect(summary.topItems[1].routeLabel).toBe('去申报')
  })

  it('caps the card at three items while keeping the full pending count', () => {
    const summary = buildClosingTodoSummary([
      todo({ id: 't-1', key: 't-1' }),
      todo({ id: 't-2', key: 't-2' }),
      todo({ id: 't-3', key: 't-3' }),
      todo({ id: 't-4', key: 't-4' }),
      todo({ id: 't-5', key: 't-5' }),
    ])
    expect(summary.pendingCount).toBe(5)
    expect(summary.topItems).toHaveLength(3)
  })

  it('returns an empty summary so the card stays hidden when nothing is pending', () => {
    expect(buildClosingTodoSummary([])).toEqual({ pendingCount: 0, topItems: [] })
    expect(buildClosingTodoSummary([todo({ done: true })])).toEqual({ pendingCount: 0, topItems: [] })
  })
})
