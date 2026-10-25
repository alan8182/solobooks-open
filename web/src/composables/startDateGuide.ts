/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { ref } from 'vue'
import { accountingErrorDetail } from '@/utils/errors'

export interface StartDateGuideInfo {
  occurDate: string
  startDate: string
  message: string
}

// 模块级单例状态：引导弹窗挂载在 App.vue，任何记账入口的 catch 里
// 调 guideStartDate(err) 即可唤起，无需每处各自维护弹窗。
export const startDateGuideVisible = ref(false)
export const startDateGuideInfo = ref<StartDateGuideInfo | null>(null)

/** 记账提交的 catch 里调用；命中启用日期校验时弹引导并返回 true（已处理，调用方不必再 toast）。 */
export function guideStartDate(err: unknown): boolean {
  const detail = accountingErrorDetail(err)
  if (!detail || detail.code !== 'ENTRY_BEFORE_START_DATE') return false
  const ctx = detail.context || {}
  startDateGuideInfo.value = {
    occurDate: String(ctx.occurDate || ''),
    startDate: String(ctx.startDate || ''),
    message: detail.message,
  }
  startDateGuideVisible.value = true
  return true
}
