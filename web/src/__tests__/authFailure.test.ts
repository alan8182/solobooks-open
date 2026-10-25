/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 401 语义判定：只有「真·登录失效」才允许清 session 并跳登录页。
// 回归背景：2026-10-20 新增税费种认定接口后，本地后端未重启，`/api/tax-registrations`
// 落到 mount("/") 的 MCP 兜底应用，返回 `{"jsonrpc":...}` 401，前端当成登录过期
// → 点一下页面就被踢回登录。这里把「接口不存在」和「登录失效」分开。

import { describe, expect, it } from 'vitest'
import { shouldClearSession } from '@/api/client'

describe('shouldClearSession', () => {
  it('普通接口 401 视为登录失效', () => {
    expect(shouldClearSession(401, { detail: '未登录或缺少 Authorization' })).toBe(true)
    expect(shouldClearSession(401, undefined)).toBe(true)
  })

  it('MCP 兜底返回的 JSON-RPC 401 不视为登录失效', () => {
    expect(
      shouldClearSession(401, {
        jsonrpc: '2.0',
        error: { code: -32001, message: '无效或已失效的访问令牌' },
        id: null,
      }),
    ).toBe(false)
  })

  it('非 401 一律不清 session', () => {
    expect(shouldClearSession(404, { detail: 'Not Found' })).toBe(false)
    expect(shouldClearSession(500, { detail: 'boom' })).toBe(false)
    expect(shouldClearSession(undefined, undefined)).toBe(false)
  })
})
