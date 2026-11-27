/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 增值税抄报手卡「确认式 / 填表式」偏好。
 *
 * 新版电子税务局会按业务复杂度自动匹配入口，前端无法预判，只能让用户在
 * 申报页看一眼后自己选。选择按公司持久化；没选过时由页面按本期数据推荐
 * （有未开票收入默认填表式），因此这里的 mode 允许为 null。
 */
import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import type { VatFilingMode } from '@/utils/taxFiling'

const STORAGE_PREFIX = 'solo:vat-guide-mode:'
const mode = ref<VatFilingMode | null>(null)
let touched = false
let hydratedKey: string | null = null

function storageKey(companyId: string | null): string {
  return `${STORAGE_PREFIX}${companyId || 'default'}`
}

function readStored(companyId: string | null): VatFilingMode | null {
  try {
    const value = localStorage.getItem(storageKey(companyId))
    return value === 'confirm' || value === 'form' ? value : null
  } catch {
    return null
  }
}

export function useVatFilingGuideMode() {
  const authStore = useAuthStore()
  if (!touched && hydratedKey !== authStore.currentCompanyId) {
    mode.value = readStored(authStore.currentCompanyId)
    hydratedKey = authStore.currentCompanyId
  }

  watch(() => authStore.currentCompanyId, (next, prev) => {
    // 整页刷新时公司 ID 从 null 异步水合；用户若已抢先切换，尊重其选择
    if (prev === null && touched) return
    mode.value = readStored(next)
    hydratedKey = next
  })

  function setMode(next: VatFilingMode) {
    mode.value = next
    touched = true
    hydratedKey = authStore.currentCompanyId
    try {
      localStorage.setItem(storageKey(authStore.currentCompanyId), next)
    } catch {
      // 隐私模式等存储失败不阻断切换
    }
  }

  return { mode, setMode }
}
