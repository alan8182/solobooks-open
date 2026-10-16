/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Entry } from '@/api/types'
import { getEntries, recordConfirm, updateEntry as apiUpdateEntry, deleteEntry as apiDeleteEntry } from '@/api/record'
import type { ConfirmRequest, EntryUpdateRequest } from '@/api/types'
import { useAuthStore } from './auth'

export const useEntryStore = defineStore('entry', () => {
  const entries = ref<Entry[]>([])

  const activeEntries = computed(() => {
    const store = useAuthStore()
    if (!store.currentCompanyId) return entries.value
    return entries.value.filter((e) => e.company_id === store.currentCompanyId)
  })

  const monthlyIncome = computed(() => {
    const now = new Date()
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    return activeEntries.value
      .filter((e) => e.direction === 'income' && e.occurDate?.startsWith(month))
      .reduce((sum, e) => sum + e.amount, 0)
  })

  const monthlyExpense = computed(() => {
    const now = new Date()
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    return activeEntries.value
      .filter((e) => e.direction === 'expense' && e.occurDate?.startsWith(month))
      .reduce((sum, e) => sum + e.amount, 0)
  })

  const netProfit = computed(() => monthlyIncome.value - monthlyExpense.value)

  async function fetchEntries() {
    const resp = await getEntries()
    entries.value = resp.entries ?? []
  }

  async function confirmEntry(data: ConfirmRequest): Promise<Entry> {
    const resp = await recordConfirm(data)
    await fetchEntries()
    return resp.entry as unknown as Entry
  }

  async function updateEntryField(entryId: string, data: EntryUpdateRequest): Promise<Entry | undefined> {
    const resp = await apiUpdateEntry(entryId, data)
    await fetchEntries()
    return resp.entry
  }

  async function removeEntry(entryId: string) {
    await apiDeleteEntry(entryId)
    await fetchEntries()
  }

  return {
    entries,
    activeEntries,
    monthlyIncome,
    monthlyExpense,
    netProfit,
    fetchEntries,
    confirmEntry,
    updateEntryField,
    removeEntry,
  }
})
