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

// 从征期待办标题（如「征期临近 - 增值税（2025-10）」「企业所得税预缴（A 类）（2026-Q3）」）里提取期次
export function extractFilingPeriod(title: string): string {
  const periodMatch = String(title || '').match(/[（(](\d{4}-(?:Q[1-4]|\d{2}))[)）]/)
  if (periodMatch) return periodMatch[1]
  const allMatches = [...String(title || '').matchAll(/（([^）]+)）/g)]
  return allMatches.length > 0 ? allMatches[allMatches.length - 1][1] : '本期'
}

export interface FilingDeadlineInfo {
  dueDate: string
  statusLabel: string
  isOverdue: boolean
  days?: number
}

/** 从待申报描述与标题提取截止日期和逾期/剩余天数，供聚合卡与单卡头部直观展示。 */
export function extractFilingDeadlineInfo(todo: TodoItem): FilingDeadlineInfo {
  const desc = String(todo.desc || todo.description || '')
  const title = String(todo.title || '')
  const dueMatch = desc.match(/截止\s*(\d{4}-\d{2}-\d{2})/)
  const dueDate = dueMatch ? dueMatch[1] : ''
  const overdueMatch = desc.match(/已逾期\s*(\d+)\s*天/)
  if (overdueMatch) {
    const days = Number(overdueMatch[1])
    return { dueDate, statusLabel: `已逾期 ${days} 天`, isOverdue: true, days }
  }
  const remainMatch = desc.match(/剩\s*(\d+)\s*天/)
  if (remainMatch) {
    const days = Number(remainMatch[1])
    return { dueDate, statusLabel: `剩 ${days} 天`, isOverdue: false, days }
  }
  if (/^逾期未申报/.test(title) || desc.includes('逾期')) {
    return { dueDate, statusLabel: '已逾期', isOverdue: true }
  }
  return { dueDate, statusLabel: '', isOverdue: false }
}

export interface TaxPeriodBundle {
  period: string
  items: TodoItem[]
  urgentCount: number
  dueDate: string
  statusLabel: string
  isOverdue: boolean
}

/** 聚合卡上的税种简称：去掉「逾期未申报 - 」前缀与「（2026-Q3）」期次后缀。 */
export function extractTaxName(title: string): string {
  return String(title || '')
    .replace(/^.+?-\s*/, '')
    .replace(/（\d{4}[^）]*）\s*$/, '')
    .trim()
}

/** 把同一征期的申报待办聚合成一张综合卡；同一征期不足两项时仍按单卡展示。 */
export function getPeriodBundles(items: TodoItem[]): { bundles: TaxPeriodBundle[]; singles: TodoItem[] } {
  const periodMap = new Map<string, TodoItem[]>()
  const singles: TodoItem[] = []

  for (const item of items) {
    if (item.meta?.ruleId === 'filing_deadline') {
      const period = extractFilingPeriod(item.title)
      if (!periodMap.has(period)) periodMap.set(period, [])
      periodMap.get(period)!.push(item)
    } else {
      singles.push(item)
    }
  }

  const bundles: TaxPeriodBundle[] = []
  for (const [period, pItems] of periodMap.entries()) {
    if (pItems.length >= 2) {
      const infos = pItems.map((i) => extractFilingDeadlineInfo(i))
      const dueDate = infos
        .map((i) => i.dueDate)
        .filter(Boolean)
        .sort()[0] || ''
      const overdueInfos = infos.filter((i) => i.isOverdue)
      const isOverdue = overdueInfos.length > 0
      const urgentCount = pItems.filter((i) => i.priority === 'high').length
      let statusLabel = ''
      if (isOverdue) {
        const worst = overdueInfos.sort((a, b) => (b.days || 0) - (a.days || 0))[0]
        statusLabel = worst?.statusLabel || '已逾期'
      } else {
        const soonest = infos
          .filter((i) => i.days !== undefined)
          .sort((a, b) => (a.days ?? 999) - (b.days ?? 999))[0]
        statusLabel = soonest?.statusLabel || (urgentCount > 0 ? '紧急临期' : '')
      }
      bundles.push({
        period,
        items: pItems,
        urgentCount,
        dueDate,
        statusLabel,
        isOverdue,
      })
    } else {
      singles.push(...pItems)
    }
  }

  return { bundles, singles }
}

/**
 * 待办列表的渲染行：聚合卡本身作为一行，排在自己的明细前面。
 *
 * 展开时明细就地跟在卡片下方；若像以前那样把明细留到单卡列表统一展开，
 * 它们会跑到该分组所有聚合卡之后，看起来像挂在下一张聚合卡下面。
 */
export function buildTodoDisplayRows(items: TodoItem[], expandedPeriods: Set<string>): TodoItem[] {
  const { bundles, singles } = getPeriodBundles(items)
  const rows: TodoItem[] = []
  for (const bundle of bundles) {
    const key = `bundle:${bundle.period}`
    rows.push({
      id: key,
      key,
      type: 'filing',
      title: '',
      priority: 'high',
      done: false,
      dismissed: false,
      // 聚合卡行只用于渲染卡片本身，字段挂在 meta.bundle 上
      meta: { bundle },
    })
    if (expandedPeriods.has(bundle.period)) rows.push(...bundle.items)
  }
  rows.push(...singles)
  return rows
}

/** 单条待办的「去处理」跳转目标；/todos 页与结账成功页共用这一份路由判断 */
export function getActionRoute(todo: TodoItem): string | null {
  if (todo.meta?.ruleId === 'opening_balance_required') {
    return '/mine/opening'
  }
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
  if (todo.meta?.ruleId === 'opening_balance_required') {
    return '去期初建账'
  }
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

/** 剥离待付款发票或征期申报待办的机械前后缀，直接突出销方或税种期次。 */
export function formatTodoCardTitle(todo: TodoItem): string {
  const raw = String(todo.title || '').trim()
  if (todo.meta?.ruleId === 'invoice_unpaid' || /^发票-.*\s[-—]\s*发票待付款\/报销$/.test(raw)) {
    const cleaned = raw
      .replace(/^发票-/, '')
      .replace(/\s*[-—]\s*发票待付款\/报销$/, '')
      .trim()
    return cleaned || raw
  }
  if (todo.meta?.ruleId === 'filing_deadline') {
    const cleaned = raw.replace(/^(?:逾期未申报|征期临近)\s*-\s*/, '').trim()
    return cleaned || raw
  }
  return raw
}

/** 剥离已在卡片头部展示的重复金额与征期截止前缀。 */
export function formatTodoCardDesc(todo: TodoItem): string {
  let text = String(todo.desc || todo.description || '').trim()
  if (!text) return ''
  if (todo.amount) {
    text = text.replace(/^[¥￥]\s*[\d,.]+\s*[，,]\s*/, '').trim()
  }
  if (todo.meta?.ruleId === 'filing_deadline') {
    const stripped = text.replace(/^截止\s*\d{4}-\d{2}-\d{2}(?:[（(][^）)]*[）)])?[。.]?\s*/, '').trim()
    if (stripped) text = stripped
  }
  return text
}

export interface TodoCardBadge {
  label: string
  tone: 'urgent' | 'medium' | 'match' | 'info'
}

/** 区分真正高危/逾期状态与常规资金去向、征期倒计时和前置配置。 */
export function todoCardBadge(todo: TodoItem): TodoCardBadge | null {
  if (todo.meta?.ruleId === 'invoice_unpaid') {
    return todo.meta?.candidateExpenseId
      ? { label: '可一键核销', tone: 'match' }
      : { label: '待确认付款', tone: 'info' }
  }
  if (todo.meta?.ruleId === 'filing_deadline') {
    const info = extractFilingDeadlineInfo(todo)
    if (info.isOverdue) {
      return { label: info.statusLabel || '已逾期', tone: 'urgent' }
    }
    if (info.statusLabel) {
      if (todo.priority === 'high') return { label: info.statusLabel, tone: 'urgent' }
      if (info.days !== undefined && info.days <= 7) return { label: info.statusLabel, tone: 'medium' }
      return { label: info.statusLabel, tone: 'info' }
    }
  }
  if (todo.meta?.ruleId === 'opening_balance_required') {
    return { label: '前置必办', tone: 'urgent' }
  }
  if (todo.meta?.ruleId === 'tax_registration_unsynced') {
    return { label: '前置配置', tone: 'info' }
  }
  if (todo.priority === 'high') return { label: '紧急', tone: 'urgent' }
  if (todo.priority === 'medium') return { label: '重要', tone: 'medium' }
  return null
}

/** 非批量未完成态下，需明确选择资金去向或走专页/申报API办结的待办不展示独立勾选框，避免跳过入账/申报写库。 */
export function showCardCheckbox(todo: TodoItem, isBatchMode: boolean): boolean {
  const ruleId = String(todo.meta?.ruleId || '')
  if (ruleId === 'opening_balance_required' || todo.meta?.nonDismissible) return false
  if (isBatchMode || todo.done) return true
  return !['invoice_unpaid', 'owner_transfer_nature', 'filing_deadline', 'tax_registration_unsynced'].includes(ruleId)
}
