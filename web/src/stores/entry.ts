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
import { errorSummary } from '@/utils/errorPresenter'

export const useEntryStore = defineStore('entry', () => {
  const entries = ref<Entry[]>([])
  const loading = ref(false)
  /**
   * 最近一次加载失败的原因。**空数组不等于失败**——这两个状态必须分开，
   * 否则首屏拉取失败会被渲染成「暂无记账记录 + 0 指标」，用户会以为账丢了。
   */
  const error = ref('')

  const activeEntries = computed(() => {
    const store = useAuthStore()
    if (!store.currentCompanyId) return entries.value
    return entries.value.filter((e) => e.company_id === store.currentCompanyId)
  })

  // 首页「本月」与账簿同口径：按记账期间归集（补记凭证算在本月）。
  // 想看业务归属请用利润表的「按业务日期」口径。
  const currentMonthKey = () => {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  }
  const entryMonthKey = (e: any): string =>
    String(e.period || '').slice(0, 7) || String(e.occurDate || '').slice(0, 7)

  const monthlyIncome = computed(() =>
    activeEntries.value
      .filter((e) => e.direction === 'income' && entryMonthKey(e) === currentMonthKey())
      .reduce((sum, e) => sum + e.amount, 0),
  )

  const monthlyExpense = computed(() =>
    activeEntries.value
      .filter((e) => e.direction === 'expense' && entryMonthKey(e) === currentMonthKey())
      .reduce((sum, e) => sum + e.amount, 0),
  )

  const netProfit = computed(() => monthlyIncome.value - monthlyExpense.value)

  async function fetchEntries() {
    loading.value = true
    try {
      const resp = await getEntries()
      entries.value = resp.entries ?? []
      error.value = ''
    } catch (err) {
      // 保留失败原因供页面渲染「加载失败 + 重试」，但继续向上抛：
      // 调用点原有的 catch / 补记确认语义不能因为这次改动被吞掉。
      error.value = errorSummary(err, '记账记录加载失败')
      throw err
    } finally {
      loading.value = false
    }
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
    loading,
    error,
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
