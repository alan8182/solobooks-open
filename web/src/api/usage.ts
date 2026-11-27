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
  /** 套餐本期剩余 + 充值点数余额（先套餐后点数，因此这就是可用总量） */
  remainingTokens: number | null
  /** 购买的 AI 点数余额（永久有效，不随套餐周期重置；1 点 = 1 token） */
  tokenBalance?: number
  /** 新人首月体验包剩余点数（30 天有效，先于永久余额扣减） */
  promoTokenBalance?: number
  /** 新人首月体验包到期日（ISO 日期串）；null/undefined = 无有效特惠包 */
  promoExpiresAt?: string | null
  /** 当前账号是否有资格购买「新人首月 AI 体验包」 */
  promoEligible?: boolean
  /** 体验额度已用 ≥80% 或已耗尽（用于触发柔和升级提醒横幅） */
  promoLowQuota?: boolean
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
