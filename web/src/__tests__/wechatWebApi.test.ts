/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 微信网站应用 API 层的契约归一化。
 *
 * 后端实际返回 `authorize_url` 与 `phone_required`（snake_case），
 * 页面契约是 `{ url }` 与 `{ phoneRequired }`。归一化只在这一层做；
 * 漏掉会让登录页拿不到扫码地址、或把 phone_required 当成登录成功而写入空 token。
 */

const harness = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/api/client', () => ({
  default: {
    get: harness.get,
    post: harness.post,
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  makeIdempotencyKey: () => 'test-key',
}))

import {
  bindWechatWebPhone,
  exchangeWechatWebTicket,
  getWechatWebAuthorizeUrl,
} from '@/api/auth'

beforeEach(() => {
  harness.get.mockReset()
  harness.post.mockReset()
})

describe('微信网站应用 API 契约', () => {
  it('authorize-url 把后端的 authorize_url 归一化成 url', async () => {
    harness.get.mockResolvedValue({
      authorize_url: 'https://open.weixin.qq.com/connect/qrconnect?state=x',
      expires_in: 600,
    })

    const result = await getWechatWebAuthorizeUrl({ redirect: '/bank' })

    expect(result).toEqual({ url: 'https://open.weixin.qq.com/connect/qrconnect?state=x' })
    const [path, config] = harness.get.mock.calls[0]
    expect(path).toBe('/api/auth/wechat/web/authorize-url')
    expect(config.params).toMatchObject({ redirect: '/bank' })
    // pending / state 都是 HttpOnly cookie，跨域 staging 也要带上
    expect(config.withCredentials).toBe(true)
  })

  it('exchange 把 phone_required 归一化成 phoneRequired，且不泄漏 token 字段', async () => {
    harness.post.mockResolvedValue({ phone_required: true })

    await expect(exchangeWechatWebTicket()).resolves.toEqual({ phoneRequired: true })
    const [path, body, config] = harness.post.mock.calls[0]
    expect(path).toBe('/api/auth/wechat/web/exchange')
    expect(body).toBeUndefined()
    expect(config.withCredentials).toBe(true)
  })

  it('exchange 成功时原样返回登录态（expires_in 允许字符串）', async () => {
    const payload = {
      access_token: 'jwt',
      token_type: 'Bearer',
      user_id: 'u_1',
      company_id: 'c_1',
      expires_in: '86400',
      has_password: false,
    }
    harness.post.mockResolvedValue(payload)

    await expect(exchangeWechatWebTicket()).resolves.toEqual(payload)
  })

  it('bind-phone 透传手机号 / 验证码 / biz_id', async () => {
    const payload = {
      access_token: 'jwt',
      token_type: 'Bearer',
      user_id: 'u_1',
      company_id: 'c_1',
      expires_in: '86400',
    }
    harness.post.mockResolvedValue(payload)

    await expect(
      bindWechatWebPhone({ phone: '13800138000', code: '123456', biz_id: 'biz_1' }),
    ).resolves.toEqual(payload)

    const [path, body, config] = harness.post.mock.calls[0]
    expect(path).toBe('/api/auth/wechat/web/bind-phone')
    expect(body).toEqual({ phone: '13800138000', code: '123456', biz_id: 'biz_1' })
    expect(config.withCredentials).toBe(true)
  })
})
