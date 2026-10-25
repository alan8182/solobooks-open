/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export type EntryNature = 'income' | 'expense' | 'current_account' | 'capital'
export type EntryNatureFilter = 'all' | EntryNature

export interface EntryNatureSource {
  direction?: string
  category?: string
}

export interface EntryNaturePresentation {
  nature: EntryNature
  label: string
  detailLabel: string
  color: string
  background: string
}

const CAPITAL_CATEGORIES = new Set(['股东投入', '老板投入', '资本投入', '实收资本', '资本公积'])

/**
 * 收支配色。color 同时被用作**文字色**（首页金额、分类徽章字符），所以必须是
 * 能过 AA 的深色，不能拿 Tailwind 500 的那套（#10b981 / #ef4444 / #f59e0b
 * 白底只有 2.07–2.54:1，金额根本读不清）。background 仍是浅底，深浅配对用 token。
 */
const PRESENTATIONS: Record<EntryNature, Omit<EntryNaturePresentation, 'nature' | 'detailLabel'>> = {
  income: { label: '收入', color: 'var(--solo-income)', background: 'var(--solo-income-bg)' },
  expense: { label: '支出', color: 'var(--solo-expense)', background: 'var(--solo-expense-bg)' },
  current_account: { label: '往来', color: 'var(--solo-warning)', background: 'var(--solo-warning-bg)' },
  capital: { label: '资本', color: 'var(--solo-capital)', background: 'var(--solo-capital-bg)' },
}

export function getEntryNature(direction = '', category = ''): EntryNature {
  if (CAPITAL_CATEGORIES.has(category.trim())) return 'capital'
  if (direction === 'income') return 'income'
  if (direction === 'expense') return 'expense'
  return 'current_account'
}

export function getEntryNaturePresentation(direction = '', category = ''): EntryNaturePresentation {
  const nature = getEntryNature(direction, category)
  const base = PRESENTATIONS[nature]
  const detailLabel = nature === 'capital'
    ? (category.trim() === '资本公积' ? '资本公积' : '资本投入')
    : base.label
  return { nature, ...base, detailLabel }
}

export function matchesEntryNature(entry: EntryNatureSource, filter: EntryNatureFilter): boolean {
  return filter === 'all' || getEntryNature(entry.direction, entry.category) === filter
}
