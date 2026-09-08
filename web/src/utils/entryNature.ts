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

const PRESENTATIONS: Record<EntryNature, Omit<EntryNaturePresentation, 'nature' | 'detailLabel'>> = {
  income: { label: '收入', color: '#10b981', background: '#ecfdf5' },
  expense: { label: '支出', color: '#ef4444', background: '#fef2f2' },
  current_account: { label: '往来', color: '#f59e0b', background: '#fffbeb' },
  capital: {
    label: '资本',
    color: 'oklch(52% 0.13 276)',
    background: 'oklch(96% 0.025 276)',
  },
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
