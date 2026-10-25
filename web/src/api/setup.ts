/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  DemoBookDeleteResponse,
  DemoBookResponse,
  SetupReadiness,
  SetupView,
} from './types'

/**
 * 账套就绪度。`view` 只决定文案与顺序，判定逻辑全在后端。
 * 超时单独放宽：这个接口要跑 5~7 条聚合查询，跟着全局 30s 也够，但演示账套生成慢得多。
 */
export function getSetupReadiness(params: {
  view: SetupView
  waived?: string[]
}): Promise<SetupReadiness> {
  return apiClient.get('/api/setup/readiness', {
    params: {
      view: params.view,
      waived: (params.waived ?? []).join(','),
    },
    timeout: 20_000,
  })
}

/** 生成（或复用）演示账套。3 个月约 22~25 张凭证，同步执行 4~10 秒。 */
export function createDemoCompany(): Promise<DemoBookResponse> {
  return apiClient.post('/api/setup/demo', {}, { timeout: 120_000 })
}

export function deleteDemoCompany(companyId: string): Promise<DemoBookDeleteResponse> {
  return apiClient.delete(`/api/setup/demo/${companyId}`, { timeout: 60_000 })
}
