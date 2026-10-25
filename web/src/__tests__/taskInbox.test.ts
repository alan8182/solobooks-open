/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { buildTaskInbox, type TaxDeadlineLike } from '@/composables/useTaskInbox'
import type { TodoItem } from '@/api/types'
import type { AgentActionItem } from '@/api/agent'

const pendingAction: AgentActionItem = {
  id: 'act-1',
  actionType: 'propose_voucher',
  title: '办公费 200 元',
  payload: {},
  evidence: {},
  result: null,
  status: 'pending',
  createdAt: null,
  resolvedAt: null,
}

const overdueTax: TaxDeadlineLike = {
  id: 'tax-1',
  taxKind: 'vat',
  taxName: '增值税',
  period: '2026-09',
  dueDate: '2026-10-15',
  daysLeft: -4,
  status: 'pending',
}

const filedTax: TaxDeadlineLike = {
  id: 'tax-2',
  taxKind: 'cit',
  taxName: '企业所得税',
  period: '2026-Q3',
  dueDate: '2026-10-20',
  daysLeft: 1,
  status: 'filed',
}

const highTodo: TodoItem = {
  id: 'todo-1',
  key: 'todo-1',
  type: 'receipt',
  title: '补充办公费发票',
  priority: 'high',
  amount: 200,
  done: false,
  dismissed: false,
}

const lowTodo: TodoItem = {
  id: 'todo-2',
  key: 'todo-2',
  type: 'other',
  title: '整理合同归档',
  priority: 'low',
  done: false,
  dismissed: false,
  entryId: 'entry-9',
}

describe('task inbox aggregation', () => {
  it('ranks overdue and blocking work before regular todos', () => {
    const tasks = buildTaskInbox({
      closingChecks: {
        checks: [
          {
            code: 'VOUCHER_TO_SOURCE',
            name: '凭证与来源检查',
            status: 'blocking',
            draftCount: 2,
          },
        ],
      },
      taxDeadlines: [overdueTax, filedTax],
      pendingActions: [pendingAction],
      todos: [lowTodo, highTodo],
    })

    expect(tasks).toHaveLength(5)
    expect(tasks[0].source).toBe('closing')
    expect(tasks[0].severity).toBe('critical')
    expect(tasks[1].source).toBe('tax')
    expect(tasks[1].title).toContain('已逾期')
    expect(tasks.some((task) => task.title === '企业所得税')).toBe(false)
  })

  it('caps the inbox at five items', () => {
    const todos: TodoItem[] = Array.from({ length: 8 }, (_, index) => ({
      id: `todo-${index}`,
      key: `todo-${index}`,
      type: 'other',
      title: `待办 ${index}`,
      priority: 'high',
      done: false,
      dismissed: false,
    }))

    const tasks = buildTaskInbox({ todos })
    expect(tasks).toHaveLength(5)
  })

  it('drops completed and dismissed todos', () => {
    const tasks = buildTaskInbox({
      todos: [
        { ...highTodo, done: true },
        { ...lowTodo, dismissed: true },
      ],
    })

    expect(tasks).toEqual([])
  })

  it('routes a todo with an entry id to the ledger detail', () => {
    const tasks = buildTaskInbox({ todos: [lowTodo] })
    expect(tasks[0].actionPath).toBe('/ledger/entry-9')
  })

  it('groups multiple tax deadlines into one compact task', () => {
    const tasks = buildTaskInbox({
      taxDeadlines: [
        overdueTax,
        {
          ...overdueTax,
          id: 'tax-2',
          taxKind: 'stamp',
          taxName: '印花税',
          daysLeft: 2,
        },
        {
          ...overdueTax,
          id: 'tax-3',
          taxKind: 'cit',
          taxName: '企业所得税',
          daysLeft: 5,
        },
      ],
    })

    expect(tasks).toHaveLength(1)
    expect(tasks[0].source).toBe('tax')
    expect(tasks[0].title).toBe('税务申报有 3 项待处理')
    expect(tasks[0].detail).toContain('增值税')
    expect(tasks[0].detail).toContain('印花税')
    expect(tasks[0].severity).toBe('critical')
  })
})
