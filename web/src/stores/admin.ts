/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchAdminOverview, type AdminOverviewData } from '@/api/admin'

export type AdminProbeState = 'idle' | 'loading' | 'ok' | 'forbidden' | 'error'

export const useAdminStore = defineStore('admin', () => {
  const overview = ref<AdminOverviewData | null>(null)
  const state = ref<AdminProbeState>('idle')
  const errorMessage = ref<string>('')
  const lastFetchedAt = ref<number>(0)
  const refreshSignal = ref<number>(0)

  const isPlatformAdmin = computed(() => state.value === 'ok')
  const operator = computed(() => overview.value?.operator ?? null)
  const dbOk = computed(() => overview.value?.database.ok ?? false)
  const circuitOpen = computed(() => overview.value?.llm.circuitOpen ?? false)
  const appVersion = computed(() => overview.value?.version ?? '3.0.0')

  /**
   * 平台管理员身份探针与概览加载。
   * 结果不落 localStorage，避免提权/降权产生本地时滞。
   */
  async function probe(force = false): Promise<AdminOverviewData | null> {
    const now = Date.now()
    if (!force && overview.value && state.value === 'ok' && now - lastFetchedAt.value < 15_000) {
      return overview.value
    }

    state.value = 'loading'
    errorMessage.value = ''
    try {
      const data = await fetchAdminOverview()
      overview.value = data
      state.value = 'ok'
      lastFetchedAt.value = Date.now()
      return data
    } catch (err: any) {
      if (err?.response?.status === 403) {
        state.value = 'forbidden'
        errorMessage.value = '当前账号不是平台管理员'
      } else {
        state.value = 'error'
        errorMessage.value = err?.response?.data?.detail || '无法获取管理后台数据'
      }
      return null
    }
  }

  /**
   * 触发全局或当前页面的数据重新拉取。
   */
  async function triggerRefresh(): Promise<void> {
    refreshSignal.value += 1
    await probe(true)
  }

  function reset(): void {
    overview.value = null
    state.value = 'idle'
    errorMessage.value = ''
    lastFetchedAt.value = 0
  }

  return {
    overview,
    state,
    errorMessage,
    lastFetchedAt,
    refreshSignal,
    isPlatformAdmin,
    operator,
    dbOk,
    circuitOpen,
    appVersion,
    probe,
    triggerRefresh,
    reset,
  }
})
