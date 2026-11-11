/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  closingTodoActionLabel,
  closingTodoRoute,
  fixedAssetTodoRoute,
  getActionLabel,
  getActionRoute,
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

describe('getActionRoute shared by the todos page and the closing card', () => {
  it('routes the tax registration sync todo to the registration page', () => {
    const item = todo({
      title: '同步税费种认定表',
      priority: 'medium',
      meta: { ruleId: 'tax_registration_unsynced' },
    })
    expect(getActionRoute(item)).toBe('/tax-registration')
    expect(getActionLabel(item)).toBe('去同步认定')
  })

  it('routes a ledger-entry todo to its voucher detail page', () => {
    const item = todo({
      title: '办公用品采购 - 补充票据',
      priority: 'medium',
      meta: { entryId: 'entry-7', ruleId: 'missing_document' },
    })
    expect(getActionRoute(item)).toBe('/ledger/entry-7')
    expect(getActionLabel(item)).toBe('查看关联交易')
  })

  it('routes a bank statement todo to closing wizard step 4 with locator params', () => {
    const item = todo({
      type: 'bank',
      title: '流水待处理 - 招行转账 3000 元',
      priority: 'medium',
      meta: { statementId: 'st-9', date: '2026-10-08' },
    })
    expect(getActionRoute(item)).toBe('/closing-wizard?month=2026-10&step=4&statement=st-9')
    expect(getActionLabel(item)).toBe('去流水对账')
  })

  it('returns no dedicated route for todos handled inline on the todos page', () => {
    const item = todo({
      title: '业务招待费超标复核',
      priority: 'medium',
      meta: { ruleId: 'entertainment_limit' },
    })
    expect(getActionRoute(item)).toBeNull()
    expect(getActionLabel(item)).toBe('去处理')
  })
})

describe('closingTodoRoute for the post-closing todo card', () => {
  it('routes a filing todo to the tax filing workbench of its quarter', () => {
    const item = todo({
      type: 'filing',
      category: 'filing',
      title: '征期临近 - 增值税（2026-10）',
      meta: { ruleId: 'filing_deadline' },
    })
    expect(closingTodoRoute(item)).toBe('/tax-filing?period=2026-Q4')
    expect(closingTodoActionLabel(item)).toBe('去申报')
  })

  it('keeps the bare tax-filing link when the period cannot be parsed', () => {
    const item = todo({
      type: 'filing',
      category: 'filing',
      title: '征期临近 - 印花税（本期）',
      meta: { ruleId: 'filing_deadline' },
    })
    expect(closingTodoRoute(item)).toBe('/tax-filing')
  })

  it('falls back to the todo center when a non-filing todo has no dedicated page', () => {
    const item = todo({
      title: '业务招待费超标复核',
      priority: 'medium',
      meta: { ruleId: 'entertainment_limit' },
    })
    expect(closingTodoRoute(item)).toBe('/todos')
    expect(closingTodoActionLabel(item)).toBe('去处理')
  })

  it('keeps dedicated routes and labels for non-filing todos', () => {
    const item = todo({
      title: '同步税费种认定表',
      priority: 'medium',
      meta: { ruleId: 'tax_registration_unsynced' },
    })
    expect(closingTodoRoute(item)).toBe('/tax-registration')
    expect(closingTodoActionLabel(item)).toBe('去同步认定')
  })
})
