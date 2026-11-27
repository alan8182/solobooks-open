/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 登录态真相唯一：路由守卫 / RootSwitch / apiClient 认 localStorage.auth_token，
// store 认 auth_state 快照。两者分裂时（快照缺失或过期）守卫判定已登录、
// store 判定未登录，AppLayout push('/login') 被守卫弹回 '/'，页面永远停在「加载中...」。
// 回归用例：store 必须以 auth_token 为准完成 hydrate。

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'

vi.mock('@/api/auth', () => ({
  getAuthMe: vi.fn(),
  switchCompany: vi.fn(),
}))

import { useAuthStore } from '@/stores/auth'

// pinia 插件只有 install 到 app 之后才会执行（pinia 3 把未安装的插件放在 toBeInstalled），
// 所以这里必须挂一个空 app，否则 persist 的 hydrate 根本不会跑，测不出真相分裂。
function useFreshPinia() {
  const pinia = createPinia()
  pinia.use(piniaPluginPersistedstate)
  createApp({ render: () => null }).use(pinia)
  setActivePinia(pinia)
  return useAuthStore()
}

function persistedState(token: string | null, userId = 'u_test') {
  return JSON.stringify({ token, userId })
}

beforeEach(() => {
  localStorage.clear()
})

describe('auth store · auth_token 单一真相', () => {
  it('只有 auth_token、没有 auth_state 时仍视为已登录', () => {
    localStorage.setItem('auth_token', 'tok_only')
    const store = useFreshPinia()
    expect(store.token).toBe('tok_only')
    expect(store.isAuthenticated).toBe(true)
  })

  it('auth_state 快照过期时以 auth_token 为准（守卫和请求实际用的凭证）', () => {
    localStorage.setItem('auth_token', 'tok_new')
    localStorage.setItem('auth_state', persistedState('tok_old'))
    const store = useFreshPinia()
    expect(store.token).toBe('tok_new')
    expect(store.isAuthenticated).toBe(true)
  })

  it('有 auth_state 但缺 auth_token 时回写 auth_token', () => {
    localStorage.setItem('auth_state', persistedState('tok_state'))
    const store = useFreshPinia()
    expect(store.token).toBe('tok_state')
    expect(localStorage.getItem('auth_token')).toBe('tok_state')
  })

  it('两者都不存在时保持未登录', () => {
    const store = useFreshPinia()
    expect(store.token).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })
})
