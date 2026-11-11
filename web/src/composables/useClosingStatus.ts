/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { computed, onMounted, ref, watch } from 'vue'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export interface ClosingStatusState {
  targetMonth: string
  isClosed: boolean
  loading: boolean
  badgeText: string
  badgeTitle: string
  badgeType: 'warning' | 'success'
}

function computeDefaultPrevMonth(): string {
  const d = new Date()
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function formatMonthShort(monthStr: string): string {
  const parts = monthStr.split('-')
  if (parts.length === 2) {
    return `${parseInt(parts[1], 10)}月`
  }
  return ''
}

function formatMonthFull(monthStr: string): string {
  const [year, month] = monthStr.split('-')
  return year && month ? `${year}年${parseInt(month, 10)}月` : monthStr
}

export function useClosingStatus() {
  const authStore = useAuthStore()
  const targetMonth = ref<string>(computeDefaultPrevMonth())
  const pendingMonth = ref<string | null>(null)
  const isClosed = ref<boolean>(false)
  const loading = ref<boolean>(false)

  async function fetchStatus() {
    if (!authStore.isAuthenticated) return
    loading.value = true
    try {
      // 侧栏始终对应当前结账期（上一自然月），与结账页默认期间保持一致。
      const cycleMonth = computeDefaultPrevMonth()
      targetMonth.value = cycleMonth
      pendingMonth.value = null
      const availRes = (await apiClient.get('/api/periods/available').catch(() => null)) as {
        months?: { month: string; status: string }[]
      } | null
      const months = availRes?.months || []

      if (months.length > 0) {
        const currentPeriod = months.find((item) => item.month === cycleMonth)
        isClosed.value = currentPeriod?.status === 'closed'
        pendingMonth.value = months
          .filter((item) => item.month < cycleMonth && item.status !== 'closed')
          .sort((a, b) => b.month.localeCompare(a.month))[0]?.month ?? null
      } else {
        const checks = (await apiClient
          .get(`/api/periods/${cycleMonth}/close-checks`)
          .catch(() => null)) as { periodStatus?: string } | null
        if (checks) {
          isClosed.value = checks.periodStatus === 'closed'
        }
      }
    } catch {
      // 降级：异常时不影响侧边栏主流程
    } finally {
      loading.value = false
    }
  }

  const shortMonth = computed(() => formatMonthShort(targetMonth.value))

  const badgeText = computed(() => {
    if (!shortMonth.value) return isClosed.value ? '已结' : '待结'
    return isClosed.value ? `${shortMonth.value}已结` : `${shortMonth.value}待结`
  })

  const badgeTitle = computed(() => {
    const current = `${formatMonthFull(targetMonth.value)}${isClosed.value ? '已结账' : '待结账'}`
    return pendingMonth.value
      ? `${current}；另有 ${formatMonthFull(pendingMonth.value)} 待结账`
      : current
  })

  const badgeType = computed<'warning' | 'success'>(() => {
    return isClosed.value ? 'success' : 'warning'
  })

  onMounted(() => {
    fetchStatus()
  })

  watch(
    () => authStore.currentCompanyId,
    (id) => {
      if (id) fetchStatus()
    },
  )

  return {
    targetMonth,
    isClosed,
    loading,
    badgeText,
    badgeTitle,
    badgeType,
    refresh: fetchStatus,
  }
}
