/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'

// api 层单测：mock 掉 axios 实例，只断言路径与参数组装
vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
    post: vi.fn().mockResolvedValue({}),
  },
}))

import apiClient from '@/api/client'
import {
  fetchAdminOrder,
  fetchAdminOrders,
  queryAdminOrder,
  refundAdminOrder,
} from '@/api/admin'
import { routes } from '@/router'
import router from '@/router'

describe('admin orders route registration', () => {
  it('registers /console/orders under the console layout (no new top-level segment, nginx untouched)', () => {
    const record = routes
      .find((r) => r.path === '/console')
      ?.children?.find((r) => r.path === 'orders')
    expect(record).toBeDefined()
    expect(record?.name).toBe('AdminOrders')
    expect(record?.component).toBeDefined()
  })

  it('resolves the route via the public router as well', () => {
    expect(router.hasRoute('AdminOrders')).toBe(true)
    expect(router.resolve('/console/orders').name).toBe('AdminOrders')
  })
})

describe('admin orders api', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockClear()
    vi.mocked(apiClient.post).mockClear()
  })

  it('fetchAdminOrders passes search/status/pagination params', async () => {
    await fetchAdminOrders({ query: 'SB2611', status: 'activated', page: 2, pageSize: 50 })
    expect(apiClient.get).toHaveBeenCalledWith('/api/admin/billing/orders', {
      params: { query: 'SB2611', status: 'activated', page: 2, pageSize: 50 },
    })
  })

  it('refundAdminOrder posts the required reason', async () => {
    await refundAdminOrder('SB261118001', '客户误购，协商退款')
    expect(apiClient.post).toHaveBeenCalledWith('/api/admin/billing/orders/SB261118001/refund', {
      reason: '客户误购，协商退款',
    })
  })

  it('fetchAdminOrder and queryAdminOrder target the order detail/query endpoints', async () => {
    await fetchAdminOrder('SB261118001')
    expect(apiClient.get).toHaveBeenCalledWith('/api/admin/billing/orders/SB261118001')

    await queryAdminOrder('SB261118001')
    expect(apiClient.post).toHaveBeenCalledWith('/api/admin/billing/orders/SB261118001/query')
  })
})
