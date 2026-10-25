/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface DatedEntryLike {
  date?: string | null
  occurDate?: string | null
}

export function sortEntriesByDateDescending<T extends DatedEntryLike>(entries: T[]): T[] {
  const dateKey = (entry: DatedEntryLike) => String(entry.date || entry.occurDate || '').slice(0, 10)
  return [...entries].sort((left, right) => {
    const leftDate = dateKey(left)
    const rightDate = dateKey(right)
    if (!leftDate && !rightDate) return 0
    if (!leftDate) return 1
    if (!rightDate) return -1
    return rightDate.localeCompare(leftDate)
  })
}
