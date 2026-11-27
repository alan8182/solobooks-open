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

/** AI 点数充值目录：价格与换算由后端唯一真相（core/plans.py）给定 */
export interface TokenTopupConfig {
  /** 是否开放充值（自部署/自有模型环境不开放） */
  enabled: boolean
  /** 1 元 = 多少点（1 点 = 1 token） */
  tokensPerYuan: number
  minYuan: number
  maxYuan: number
}

/** 新人首月 AI 体验包目录：价格与有效期由后端唯一真相（core/plans.py）给定 */
export interface FirstMonthPromoConfig {
  enabled: boolean
  eligible: boolean
  purchased: boolean
  code: string
  name: string
  priceYuan: string
  originalPriceYuan: string
  tokens: number
  validDays: number
}

export interface PricingCatalog {
  alipayEnabled: boolean
  /** 微信支付（Native 扫码）渠道是否开通；渠道可用性一律以后端为准，前端不得自判 */
  wechatEnabled?: boolean
  items: PricingItem[]
  /** AI 点数充值（与套餐独立，余额永久有效）；缺省视为未开放 */
  tokenTopup?: TokenTopupConfig
  /** 新人首月 AI 体验包（¥1 买 80 万 Token，30 天有效，限购 1 次） */
  firstMonthPromo?: FirstMonthPromoConfig
}

/** 支付渠道：支付宝收银台 / 微信 Native 扫码 */
export type PaymentChannel = 'alipay' | 'wechat'

/** 订单状态：created 待支付 → paid 已到账 → activated 权益生效；closed/failed 为终态 */
export type BillingOrderStatus = 'created' | 'paid' | 'activated' | 'closed' | 'failed'

/** 订单类型：plan 套餐订阅 / token_topup AI 点数充值 / first_month_promo 新人首月体验包 */
export type BillingOrderType = 'plan' | 'token_topup' | 'first_month_promo'

export interface BillingOrder {
  orderNo: string
  planCode: string
  orderType?: BillingOrderType
  /** 充值到账点数（1 点 = 1 token）；套餐订单为 null */
  tokenAmount?: number | null
  subject: string
  amount: string
  status: BillingOrderStatus
  /** 支付渠道（存量数据为 alipay） */
  channel?: PaymentChannel
  tradeNo?: string | null
  paidAt?: string | null
  activatedAt?: string | null
  expiresAt?: string | null
  createdAt?: string | null
}

/** 创建订单返回：支付宝携带 payUrl（跳收银台），微信携带 codeUrl（渲染二维码） */
export interface CreateOrderResult {
  orderNo: string
  planCode: string
  orderType?: BillingOrderType
  tokenAmount?: number | null
  planName: string
  amount: string
  channel: PaymentChannel
  /** 支付宝收银台跳转地址（channel=alipay） */
  payUrl?: string
  /** 微信 Native 支付二维码内容（channel=wechat） */
  codeUrl?: string
  expiresAt: string
}

export function fetchPricing(): Promise<PricingCatalog> {
  return apiClient.get('/api/billing/pricing')
}

export function createBillingOrder(
  planCode: string,
  channel: PaymentChannel = 'alipay',
): Promise<CreateOrderResult> {
  return apiClient.post('/api/billing/orders', { planCode, channel })
}

/** 创建 AI 点数充值订单：只提交整数元，到账点数由后端按目录换算 */
export function createTokenTopupOrder(
  amountYuan: number,
  channel: PaymentChannel = 'alipay',
): Promise<CreateOrderResult> {
  return apiClient.post('/api/billing/token-topup', { amountYuan, channel })
}

/** 创建「新人首月 AI 体验包」订单（¥1 买 80 万 Token，30 天有效，限购 1 次） */
export function createFirstMonthPromoOrder(
  channel: PaymentChannel = 'alipay',
): Promise<CreateOrderResult> {
  return apiClient.post('/api/billing/first-month-promo', { channel })
}

export function fetchBillingOrder(orderNo: string): Promise<BillingOrder> {
  return apiClient.get(`/api/billing/orders/${encodeURIComponent(orderNo)}`)
}

export function fetchBillingOrders(limit = 20): Promise<{ items: BillingOrder[] }> {
  return apiClient.get('/api/billing/orders', { params: { limit } })
}
