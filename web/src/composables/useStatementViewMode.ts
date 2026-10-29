/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 报表页「老板视图 / 会计视图」切换偏好。
 * 模块级共享 ref：同一页面的切换控件与页面逻辑看到同一状态；
 * 按公司持久化到 localStorage，四张法定报表页共用同一份偏好。
 */
import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'

export type StatementViewMode = 'boss' | 'accounting'

const STORAGE_PREFIX = 'solo:report-view:'
const mode = ref<StatementViewMode | null>(null)
/** 用户在本会话是否已手动切换过：公司 ID 异步水合时不得覆盖用户的抢先切换 */
let touched = false

function storageKey(companyId: string | null): string {
  return `${STORAGE_PREFIX}${companyId || 'default'}`
}

function readStored(companyId: string | null): StatementViewMode {
  try {
    return localStorage.getItem(storageKey(companyId)) === 'accounting' ? 'accounting' : 'boss'
  } catch {
    return 'boss'
  }
}

export function useStatementViewMode() {
  const authStore = useAuthStore()
  if (mode.value === null) {
    mode.value = readStored(authStore.currentCompanyId)
  }

  watch(() => authStore.currentCompanyId, (next, prev) => {
    // 整页刷新时公司 ID 从 null 异步水合为真实值；若用户在水合前已切换视图，尊重其选择
    if (prev === null && touched) {
      return
    }
    mode.value = readStored(next)
  })

  function setMode(next: StatementViewMode) {
    mode.value = next
    touched = true
    try {
      localStorage.setItem(storageKey(authStore.currentCompanyId), next)
    } catch {
      // 隐私模式等存储失败不阻断切换
    }
  }

  return { mode, setMode }
}
