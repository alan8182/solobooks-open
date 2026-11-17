/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

/** 一个月度聚合桶（本月/上月共用）。失败调用计次数不计 token。 */
export interface UsageBucket {
  calls: number
  promptTokens: number
  completionTokens: number
  totalTokens: number
  month?: string
}

export interface UsageSceneRow extends UsageBucket {
  scene: string
}

export interface UsageDailyRow {
  date: string
  calls: number
  totalTokens: number
}

/** 配额状态：limitTokens 为 null 表示套餐未设限（额度数值待定，当前恒为此状态）。 */
export interface QuotaState {
  planCode: string
  planName: string
  periodType: 'monthly' | 'annual'
  periodStart: string
  periodEnd: string
  limitTokens: number | null
  bonusTokens: number
  usedTokens: number
  remainingTokens: number | null
  /** 付费套餐到期日（ISO 日期串）；null/undefined = 不适用（trial 或管理端调整） */
  planExpiresAt?: string | null
  /** 最多可创建的账套数；null = 不限（开源自部署或未来代账机构版） */
  maxCompanies?: number | null
  /** 当前名下已创建的账套数 */
  companyCount?: number
}

export interface MyUsageSummary {
  month: string
  currentMonth: UsageBucket
  previousMonth: UsageBucket
  byScene: UsageSceneRow[]
  daily: UsageDailyRow[]
  quota: QuotaState | null
}

export function fetchMyUsage(days = 30): Promise<MyUsageSummary> {
  return apiClient.get('/api/usage/me', { params: { days } })
}
