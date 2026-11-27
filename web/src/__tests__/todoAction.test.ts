/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  closingTodoActionLabel,
  closingTodoRoute,
  extractFilingDeadlineInfo,
  extractFilingPeriod,
  fixedAssetTodoRoute,
  formatTodoCardDesc,
  formatTodoCardTitle,
  getActionLabel,
  getActionRoute,
  intangibleAssetTodoRoute,
  isFixedAssetTodo,
  isIntangibleAssetTodo,
  showCardCheckbox,
  todoCardBadge,
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

describe('todo card presentation helpers', () => {
  it('strips mechanical invoice prefix and suffix for invoice_unpaid titles', () => {
    const item = todo({
      title: '发票-杭州深度求索人工智能基础技术研究有限公司 - 发票待付款/报销',
      meta: { ruleId: 'invoice_unpaid' },
    })
    expect(formatTodoCardTitle(item)).toBe('杭州深度求索人工智能基础技术研究有限公司')
  })

  it('strips redundant leading amount from description when card shows amount separately', () => {
    const item = todo({
      amount: 481.33,
      desc: '¥481，此发票在流水中未找到对应支出。请确认是否已付款。',
      meta: { ruleId: 'invoice_unpaid' },
    })
    expect(formatTodoCardDesc(item)).toBe('此发票在流水中未找到对应支出。请确认是否已付款。')
  })

  it('assigns semantic badges for invoice_unpaid instead of flooding urgent tags', () => {
    const matched = todo({
      priority: 'high',
      meta: { ruleId: 'invoice_unpaid', candidateExpenseId: 'exp-1' },
    })
    const plain = todo({
      priority: 'high',
      meta: { ruleId: 'invoice_unpaid' },
    })
    const overdue = todo({
      priority: 'high',
      meta: { ruleId: 'filing_deadline' },
    })
    expect(todoCardBadge(matched)).toEqual({ label: '可一键核销', tone: 'match' })
    expect(todoCardBadge(plain)).toEqual({ label: '待确认付款', tone: 'info' })
    expect(todoCardBadge(overdue)).toEqual({ label: '紧急', tone: 'urgent' })
  })

  it('hides standalone checkbox on disposition-required todos unless batch mode or done', () => {
    const invoiceTodo = todo({ done: false, meta: { ruleId: 'invoice_unpaid' } })
    const docTodo = todo({ done: false, meta: { ruleId: 'missing_document' } })
    expect(showCardCheckbox(invoiceTodo, false)).toBe(false)
    expect(showCardCheckbox(invoiceTodo, true)).toBe(true)
    expect(showCardCheckbox({ ...invoiceTodo, done: true }, false)).toBe(true)
    expect(showCardCheckbox(docTodo, false)).toBe(true)
  })
})

describe('filing deadline period parsing and presentation helpers', () => {
  it('extracts trailing fiscal period when tax name itself contains parentheses', () => {
    const title = '逾期未申报 - 企业所得税预缴（A 类）（2026-Q3）'
    expect(extractFilingPeriod(title)).toBe('2026-Q3')
    expect(closingTodoRoute(todo({
      type: 'filing',
      category: 'filing',
      title,
      meta: { ruleId: 'filing_deadline' },
    }))).toBe('/tax-filing?period=2026-Q3')
  })

  it('parses deadline date, overdue days and remaining days cleanly', () => {
    const overdue = todo({
      title: '逾期未申报 - 个税扣缴（2026-09）',
      desc: '截止 2026-10-22（已逾期 31 天）。每月必须申报。',
      priority: 'high',
      meta: { ruleId: 'filing_deadline' },
    })
    const upcoming = todo({
      title: '征期临近 - 个税扣缴（2026-11）',
      desc: '截止 2026-12-15（剩 23 天）。每月必须申报。',
      priority: 'medium',
      meta: { ruleId: 'filing_deadline' },
    })

    expect(extractFilingDeadlineInfo(overdue)).toEqual({
      dueDate: '2026-10-22',
      statusLabel: '已逾期 31 天',
      isOverdue: true,
      days: 31,
    })
    expect(extractFilingDeadlineInfo(upcoming)).toEqual({
      dueDate: '2026-12-15',
      statusLabel: '剩 23 天',
      isOverdue: false,
      days: 23,
    })

    expect(formatTodoCardTitle(overdue)).toBe('个税扣缴（2026-09）')
    expect(formatTodoCardDesc(overdue)).toBe('每月必须申报。')
    expect(todoCardBadge(overdue)).toEqual({ label: '已逾期 31 天', tone: 'urgent' })
    expect(todoCardBadge(upcoming)).toEqual({ label: '剩 23 天', tone: 'info' })
    expect(showCardCheckbox(overdue, false)).toBe(false)
    expect(showCardCheckbox(overdue, true)).toBe(true)
  })

  it('marks tax_registration_unsynced as setup banner and hides checkbox outside batch mode', () => {
    const setupTodo = todo({
      title: '同步税费种认定表',
      priority: 'high',
      meta: { ruleId: 'tax_registration_unsynced' },
    })
    expect(todoCardBadge(setupTodo)).toEqual({ label: '前置配置', tone: 'info' })
    expect(showCardCheckbox(setupTodo, false)).toBe(false)
  })

  it('routes opening_balance_required to /mine/opening and blocks checkboxes in all modes', () => {
    const openingTodo = todo({
      id: 'opening-balance-required:c1',
      key: 'opening-balance-required:c1',
      type: 'setup',
      category: 'setup',
      title: '完成期初建账',
      priority: 'high',
      meta: { ruleId: 'opening_balance_required', nonDismissible: true },
    })
    expect(getActionRoute(openingTodo)).toBe('/mine/opening')
    expect(getActionLabel(openingTodo)).toBe('去期初建账')
    expect(closingTodoRoute(openingTodo)).toBe('/mine/opening')
    expect(closingTodoActionLabel(openingTodo)).toBe('去期初建账')
    expect(todoCardBadge(openingTodo)).toEqual({ label: '前置必办', tone: 'urgent' })
    expect(showCardCheckbox(openingTodo, false)).toBe(false)
    expect(showCardCheckbox(openingTodo, true)).toBe(false)
    expect(showCardCheckbox({ ...openingTodo, done: true }, true)).toBe(false)
  })
})
