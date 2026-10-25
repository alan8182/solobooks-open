/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { onMounted, ref } from 'vue'
import apiClient from '@/api/client'
import { listAgentActions, type AgentActionItem } from '@/api/agent'
import { getTodos } from '@/api/todo'
import { getPeriodCloseChecks } from '@/api/accounting'
import { periodToQuarter } from '@/utils/taxFiling'
import { deriveClosingTasks, type PeriodCloseChecks } from '@/utils/closingReadiness'
import type { TodoItem } from '@/api/types'

export type TaskSource = 'ai' | 'closing' | 'tax' | 'todo'
export type TaskSeverity = 'critical' | 'warning' | 'info'

export interface TaskInboxItem {
  id: string
  source: TaskSource
  sourceLabel: string
  title: string
  detail?: string
  amount?: number
  severity: TaskSeverity
  dueDate?: string
  actionLabel: string
  actionPath: string
  sortScore: number
}

export interface TaxDeadlineLike {
  id?: string
  taxKind: string
  taxName: string
  period: string
  dueDate: string
  daysLeft: number
  status: string
}

export interface TaskInboxInput {
  pendingActions?: AgentActionItem[]
  closingChecks?: PeriodCloseChecks | null
  taxDeadlines?: TaxDeadlineLike[]
  todos?: TodoItem[]
}

const SOURCE_LABELS: Record<TaskSource, string> = {
  ai: 'AI 草稿',
  closing: '月度结账',
  tax: '税务',
  todo: '待办',
}

const TODO_PRIORITY_SCORE: Record<string, number> = {
  high: 48,
  medium: 32,
  low: 16,
}

function severityRank(severity: TaskSeverity): number {
  if (severity === 'critical') return 3
  if (severity === 'warning') return 2
  return 1
}

function todoActionPath(todo: TodoItem): string {
  const entryId = String(todo.meta?.entryId || todo.entryId || '')
  if (entryId) return `/ledger/${entryId}`
  return '/todos'
}

export function buildTaskInbox(input: TaskInboxInput): TaskInboxItem[] {
  const tasks: TaskInboxItem[] = []
  const seen = new Set<string>()

  const push = (task: TaskInboxItem) => {
    if (seen.has(task.id)) return
    seen.add(task.id)
    tasks.push(task)
  }

  const closingTasks = deriveClosingTasks(input.closingChecks)
  if (closingTasks.length > 0) {
    const hasBlocked = closingTasks.some((task) => task.severity === 'blocked')
    const titles = closingTasks.slice(0, 2).map((task) => task.title).join('；')
    const more = closingTasks.length > 2 ? ` 等 ${closingTasks.length} 项` : ''
    push({
      id: 'closing-summary',
      source: 'closing',
      sourceLabel: SOURCE_LABELS.closing,
      title: `月度结账还有 ${closingTasks.length} 项待处理`,
      detail: `${titles}${more}`,
      severity: hasBlocked ? 'critical' : 'warning',
      actionLabel: '去结账',
      actionPath: '/closing-wizard',
      sortScore: hasBlocked ? 100 : 70,
    })
  }

  const pendingTaxes = (input.taxDeadlines ?? []).filter((deadline) => (
    deadline.status !== 'filed' && deadline.status !== 'waived'
  ))

  if (pendingTaxes.length === 1) {
    const deadline = pendingTaxes[0]
    const overdue = deadline.daysLeft < 0
    const urgent = deadline.daysLeft <= 3
    push({
      id: `tax-${deadline.id || `${deadline.taxKind}-${deadline.period}`}`,
      source: 'tax',
      sourceLabel: SOURCE_LABELS.tax,
      title: `${deadline.taxName}${overdue ? '已逾期' : '待申报'}`,
      detail: overdue
        ? `已逾期 ${-deadline.daysLeft} 天，请尽快补报`
        : `截止 ${deadline.dueDate}，剩 ${deadline.daysLeft} 天`,
      severity: overdue ? 'critical' : urgent ? 'warning' : 'info',
      dueDate: deadline.dueDate,
      actionLabel: '去申报',
      actionPath: `/tax-filing?period=${periodToQuarter(deadline.period)}`,
      sortScore: overdue ? 95 : urgent ? 85 : 50,
    })
  } else if (pendingTaxes.length > 1) {
    const sorted = [...pendingTaxes].sort((a, b) => a.daysLeft - b.daysLeft)
    const earliest = sorted[0]
    const hasOverdue = sorted.some((deadline) => deadline.daysLeft < 0)
    const names = sorted.map((deadline) => deadline.taxName)
    const nameText = names.length > 3
      ? `${names.slice(0, 3).join('、')} 等 ${names.length} 项`
      : names.join('、')
    push({
      id: 'tax-group',
      source: 'tax',
      sourceLabel: SOURCE_LABELS.tax,
      title: `税务申报有 ${pendingTaxes.length} 项待处理`,
      detail: `${nameText} · 截止 ${earliest.dueDate}${earliest.daysLeft < 0
        ? `，已逾期 ${-earliest.daysLeft} 天`
        : `，剩 ${earliest.daysLeft} 天`}`,
      severity: hasOverdue ? 'critical' : earliest.daysLeft <= 3 ? 'warning' : 'info',
      dueDate: earliest.dueDate,
      actionLabel: '去申报',
      actionPath: `/tax-filing?period=${periodToQuarter(earliest.period)}`,
      sortScore: hasOverdue ? 95 : earliest.daysLeft <= 3 ? 85 : 50,
    })
  }

  const pendingActions = input.pendingActions ?? []
  if (pendingActions.length > 0) {
    push({
      id: 'ai-pending',
      source: 'ai',
      sourceLabel: SOURCE_LABELS.ai,
      title: `有 ${pendingActions.length} 笔 AI 记账草稿待确认`,
      detail: '确认后才会正式入账，可逐笔核对或撤销',
      severity: 'warning',
      actionLabel: '去确认',
      actionPath: '/copilot',
      sortScore: 60,
    })
  }

  for (const todo of input.todos ?? []) {
    if (todo.done || todo.dismissed) continue
    const priority = TODO_PRIORITY_SCORE[todo.priority] ?? TODO_PRIORITY_SCORE.low
    const detail = todo.description || todo.desc || ''
    push({
      id: `todo-${todo.key || todo.id}`,
      source: 'todo',
      sourceLabel: SOURCE_LABELS.todo,
      title: todo.title,
      detail,
      amount: todo.amount,
      severity: todo.priority === 'high' ? 'warning' : 'info',
      actionLabel: '去处理',
      actionPath: todoActionPath(todo),
      sortScore: priority,
    })
  }

  return tasks
    .sort((a, b) => {
      if (b.sortScore !== a.sortScore) return b.sortScore - a.sortScore
      return severityRank(b.severity) - severityRank(a.severity)
    })
    .slice(0, 5)
}

function previousMonth(): string {
  const date = new Date()
  date.setMonth(date.getMonth() - 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

export function useTaskInbox() {
  const tasks = ref<TaskInboxItem[]>([])
  const loading = ref(true)
  const partialFailure = ref(false)

  async function load() {
    loading.value = true
    partialFailure.value = false
    const month = previousMonth()
    const [actionsResult, closingResult, taxResult, todoResult] = await Promise.allSettled([
      listAgentActions('pending'),
      getPeriodCloseChecks(month),
      fetchTaxDeadlines(),
      getTodos(),
    ])

    if ([actionsResult, closingResult, taxResult, todoResult].some((result) => result.status === 'rejected')) {
      partialFailure.value = true
    }

    tasks.value = buildTaskInbox({
      pendingActions: actionsResult.status === 'fulfilled' ? actionsResult.value : [],
      closingChecks: closingResult.status === 'fulfilled'
        ? (closingResult.value as unknown as PeriodCloseChecks)
        : null,
      taxDeadlines: taxResult.status === 'fulfilled' ? taxResult.value : [],
      todos: todoResult.status === 'fulfilled' ? (todoResult.value.todos || []) : [],
    })
    loading.value = false
  }

  onMounted(load)

  return {
    tasks,
    loading,
    partialFailure,
    reload: load,
  }
}

async function fetchTaxDeadlines(): Promise<TaxDeadlineLike[]> {
  const body = await apiClient.get('/api/tax-filing/deadlines', {
    params: { upcoming_days: 30 },
  }) as unknown as { deadlines?: TaxDeadlineLike[] }
  return body.deadlines ?? []
}
