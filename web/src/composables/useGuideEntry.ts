/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { computed } from 'vue'
import { getActivePinia } from 'pinia'
import { getGuide, type Guide, type GuideId } from '@/content/guides'
import { useAuthStore } from '@/stores/auth'
import { etaxPortalUrl, etaxRegionLabel } from '@/utils/etaxPortal'

export type GuideEntry = NonNullable<Guide['entry']>

interface CompanyRegion {
  province?: string
  city?: string
}

/**
 * 把指引里的静态入口解析成当前主体的实际入口。
 *
 * `companyRegion` 的入口（电子税务局）没有全国统一地址，必须按注册地拼出省局/计划
 * 单列市站点；主体没填注册地时回落到 entry.url（税局省份地图），用户只需选一次。
 */
export function resolveGuideEntry(
  entry: GuideEntry | undefined,
  company?: CompanyRegion | null,
): GuideEntry | null {
  if (!entry) return null
  if (!entry.companyRegion) return entry
  const url = etaxPortalUrl(company?.province, company?.city)
  if (!url) return entry
  const label = etaxRegionLabel(company?.province, company?.city)
  return { ...entry, url, hint: label ? `${label}电子税务局` : entry.hint }
}

/** 当前主体的入口链接；主体信息变化（新建/编辑注册地）后自动跟着变。 */
export function useGuideEntry(guideId: GuideId) {
  // 组件可能在没有 Pinia 的上下文里被挂载（单测、独立预览）：没有会话时
  // 只回落到静态入口，不要为了取注册地把整个抽屉炸掉
  const auth = getActivePinia() ? useAuthStore() : null
  const company = computed<CompanyRegion | null>(() => {
    if (!auth) return null
    const id = auth.currentCompanyId
    return auth.companies.find((item) => item.company_id === id) ?? null
  })
  const entry = computed(() => resolveGuideEntry(getGuide(guideId)?.entry, company.value))
  return { entry }
}
