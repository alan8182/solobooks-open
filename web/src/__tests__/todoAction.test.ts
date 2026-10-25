/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  fixedAssetTodoRoute,
  intangibleAssetTodoRoute,
  isFixedAssetTodo,
  isIntangibleAssetTodo,
} from '@/utils/todoAction'
import type { TodoItem } from '@/api/types'

function todo(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    id: 'todo-1',
    key: 'todo-1',
    type: 'task',
    title: '购办公电脑及显示器 - 固定资产需确认折旧方案',
    priority: 'high',
    done: false,
    dismissed: false,
    meta: {
      entryId: 'entry-1',
      ruleId: 'fixed_asset_depreciation',
      action: 'confirm_asset',
    },
    ...overrides,
  }
}

describe('fixed asset todo action', () => {
  it('routes the todo to the prefilled asset creation page instead of the ledger', () => {
    const item = todo()
    expect(isFixedAssetTodo(item)).toBe(true)
    expect(fixedAssetTodoRoute(item)).toBe('/assets/create?sourceEntryId=entry-1')
  })

  it('does not treat ordinary ledger entries as fixed asset todos', () => {
    const item = todo({
      title: '办公费报销',
      meta: { entryId: 'entry-2', ruleId: 'missing_document' },
    })
    expect(isFixedAssetTodo(item)).toBe(false)
  })

  it('routes an intangible asset todo to the prefilled intangible form', () => {
    const item = todo({
      title: '购管理软件 - 无形资产需确认摊销方案',
      meta: {
        entryId: 'entry-3',
        ruleId: 'intangible_asset_amortization',
        action: 'confirm_intangible',
      },
    })
    expect(isIntangibleAssetTodo(item)).toBe(true)
    expect(intangibleAssetTodoRoute(item)).toBe('/intangibles/create?sourceEntryId=entry-3')
    expect(isFixedAssetTodo(item)).toBe(false)
  })
})
