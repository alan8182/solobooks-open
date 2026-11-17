/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

/** 售卖目录项：价格为后端唯一真相（core/plans.py），前端禁止自写价格 */
export interface PricingItem {
  planCode: string
  planName: string
  period: 'annual'
  priceAnnual: string
}

export interface PricingCatalog {
  alipayEnabled: boolean
  items: PricingItem[]
}

/** 订单状态：created 待支付 → paid 已到账 → activated 权益生效；closed/failed 为终态 */
export type BillingOrderStatus = 'created' | 'paid' | 'activated' | 'closed' | 'failed'

export interface BillingOrder {
  orderNo: string
  planCode: string
  subject: string
  amount: string
  status: BillingOrderStatus
  tradeNo?: string | null
  paidAt?: string | null
  activatedAt?: string | null
  expiresAt?: string | null
  createdAt?: string | null
}

/** 创建订单返回：携带收银台跳转地址 */
export interface CreateOrderResult {
  orderNo: string
  planCode: string
  planName: string
  amount: string
  payUrl: string
  expiresAt: string
}

export function fetchPricing(): Promise<PricingCatalog> {
  return apiClient.get('/api/billing/pricing')
}

export function createBillingOrder(planCode: string): Promise<CreateOrderResult> {
  return apiClient.post('/api/billing/orders', { planCode })
}

export function fetchBillingOrder(orderNo: string): Promise<BillingOrder> {
  return apiClient.get(`/api/billing/orders/${encodeURIComponent(orderNo)}`)
}

export function fetchBillingOrders(limit = 20): Promise<{ items: BillingOrder[] }> {
  return apiClient.get('/api/billing/orders', { params: { limit } })
}
