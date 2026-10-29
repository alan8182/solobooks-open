/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAdminStore } from '@/stores/admin'
import { formatAdminDateTime, formatAdminNumber, useAdminTable } from '@/composables/useAdminTable'

describe('admin framework formatting & store', () => {
  it('formats dates safely with fallback for null/empty/invalid values', () => {
    expect(formatAdminDateTime(null)).toBe('—')
    expect(formatAdminDateTime('')).toBe('—')
    expect(formatAdminDateTime(undefined)).toBe('—')
    expect(formatAdminDateTime('invalid-date')).toBe('invalid-date')

    const formatted = formatAdminDateTime('2026-10-29T10:15:00Z')
    expect(formatted).toMatch(/^2026-10-29 \d{2}:15$/)
  })

  it('formats numbers with locale separation', () => {
    expect(formatAdminNumber(null)).toBe('—')
    expect(formatAdminNumber(undefined)).toBe('—')
    expect(formatAdminNumber(1000)).toBe('1,000')
    expect(formatAdminNumber(1234567)).toBe('1,234,567')
  })

  it('initializes admin store with default idle state and tracks refresh signals', () => {
    setActivePinia(createPinia())
    const store = useAdminStore()

    expect(store.state).toBe('idle')
    expect(store.overview).toBeNull()
    expect(store.isPlatformAdmin).toBe(false)
    expect(store.refreshSignal).toBe(0)

    store.triggerRefresh()
    expect(store.refreshSignal).toBe(1)

    store.reset()
    expect(store.state).toBe('idle')
    expect(store.overview).toBeNull()
  })

  it('manages table pagination, search query and data loading via useAdminTable', async () => {
    setActivePinia(createPinia())
    const mockFetcher = vi.fn().mockResolvedValue({
      items: [{ id: 1, name: 'Alpha' }, { id: 2, name: 'Beta' }],
      total: 2,
    })

    const table = useAdminTable<{ id: number; name: string }>({
      immediate: false,
      defaultPageSize: 10,
      loadData: mockFetcher,
    })

    expect(table.page.value).toBe(1)
    expect(table.pageSize.value).toBe(10)
    expect(table.items.value).toEqual([])

    await table.load()
    expect(mockFetcher).toHaveBeenCalledWith({ page: 1, pageSize: 10, query: '' })
    expect(table.items.value.length).toBe(2)
    expect(table.total.value).toBe(2)

    table.query.value = 'Beta'
    table.onSearch()
    expect(mockFetcher).toHaveBeenCalledWith({ page: 1, pageSize: 10, query: 'Beta' })

    table.onPageChange(2)
    expect(table.page.value).toBe(2)
    expect(mockFetcher).toHaveBeenCalledWith({ page: 2, pageSize: 10, query: 'Beta' })
  })
})
