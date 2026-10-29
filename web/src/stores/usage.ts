/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchMyUsage, type MyUsageSummary } from '@/api/usage'
import { useAuthStore } from '@/stores/auth'

/**
 * 用户 AI 用量缓存：导航栏摘要与个人设置页共用一次请求。
 * 缓存按登录用户失效——切换账号后 ensureLoaded 会自动重取，不串数据。
 */
export const useUsageStore = defineStore('usage', () => {
  const summary = ref<MyUsageSummary | null>(null)
  const loading = ref(false)
  const loaded = ref(false)
  const loadedFor = ref<string | null>(null)
  const loadError = ref(false)

  async function ensureLoaded(force = false) {
    const authStore = useAuthStore()
    if (!authStore.token) return
    if (loading.value) return
    if (loaded.value && !force && loadedFor.value === authStore.userId) return
    loading.value = true
    loadError.value = false
    try {
      summary.value = await fetchMyUsage()
      loaded.value = true
      loadedFor.value = authStore.userId ?? null
    } catch {
      // 用量展示是附属信息，失败不打扰主流程；页面内可重试
      loadError.value = true
    } finally {
      loading.value = false
    }
  }

  function reset() {
    summary.value = null
    loaded.value = false
    loadedFor.value = null
    loadError.value = false
  }

  return { summary, loading, loaded, loadError, ensureLoaded, reset }
})
