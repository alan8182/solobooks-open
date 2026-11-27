/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

// api 层单测：mock 掉 axios 实例，只断言路径与参数组装
vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
  },
}))

import apiClient from '@/api/client'
import { createFirstMonthPromoOrder, createTokenTopupOrder, fetchPricing } from '@/api/billing'

describe('AI 点数充值 api', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockClear()
    vi.mocked(apiClient.post).mockClear()
  })

  it('只提交整数元与渠道，点数由后端按目录换算（前端不得自算 token 数）', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      orderNo: 'SB2611190001',
      orderType: 'token_topup',
      tokenAmount: 100_000,
      amount: '10.00',
    })

    const result = await createTokenTopupOrder(10, 'wechat')

    expect(apiClient.post).toHaveBeenCalledWith('/api/billing/token-topup', {
      amountYuan: 10,
      channel: 'wechat',
    })
    expect(result.tokenAmount).toBe(100_000)
    expect(result.orderType).toBe('token_topup')
  })

  it('默认走支付宝渠道', async () => {
    await createTokenTopupOrder(1)
    expect(apiClient.post).toHaveBeenCalledWith('/api/billing/token-topup', {
      amountYuan: 1,
      channel: 'alipay',
    })
  })

  it('定价目录从 /api/billing/pricing 读取（价格与换算的唯一真相）', async () => {
    await fetchPricing()
    expect(apiClient.get).toHaveBeenCalledWith('/api/billing/pricing')
  })

  it('新人首月体验包只传支付渠道，价格与 80 万点数由后端目录决定', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      orderNo: 'SB2611240001',
      orderType: 'first_month_promo',
      tokenAmount: 800_000,
      amount: '1.00',
    })

    const result = await createFirstMonthPromoOrder('wechat')
    expect(apiClient.post).toHaveBeenCalledWith('/api/billing/first-month-promo', {
      channel: 'wechat',
    })
    expect(result.tokenAmount).toBe(800_000)
    expect(result.orderType).toBe('first_month_promo')
  })
})
