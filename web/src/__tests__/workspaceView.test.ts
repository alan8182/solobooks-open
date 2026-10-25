/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import {
  readWorkspaceView,
  useWorkspaceViewStore,
  workspaceStorageKey,
  writeWorkspaceView,
} from '@/stores/workspaceView'
import { itemVisible, routeAllowsWorkspace } from '@/utils/workspace'

describe('workspace view helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to the boss view when no preference exists', () => {
    expect(readWorkspaceView(null)).toBe('boss')
    expect(readWorkspaceView('c1')).toBe('boss')
  })

  it('stores preferences per company', () => {
    writeWorkspaceView('c1', 'accounting')

    expect(localStorage.getItem(workspaceStorageKey('c1'))).toBe('accounting')
    expect(readWorkspaceView('c1')).toBe('accounting')
    expect(readWorkspaceView('c2')).toBe('boss')
  })

  it('falls back to boss for invalid stored values', () => {
    localStorage.setItem(workspaceStorageKey('c1'), 'unknown')
    expect(readWorkspaceView('c1')).toBe('boss')
  })

  it('treats missing workspace metadata as visible in both views', () => {
    expect(itemVisible(undefined, 'boss')).toBe(true)
    expect(itemVisible([], 'accounting')).toBe(true)
    expect(itemVisible(['accounting'], 'boss')).toBe(false)
    expect(itemVisible(['accounting'], 'accounting')).toBe(true)
  })

  it('only shows a deep-link notice when the route excludes the current view', () => {
    expect(routeAllowsWorkspace(undefined, 'boss')).toBe(true)
    expect(routeAllowsWorkspace(['accounting'], 'boss')).toBe(false)
    expect(routeAllowsWorkspace(['accounting'], 'accounting')).toBe(true)
  })
})

describe('workspace view store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  function setCompany(companyId: string) {
    const authStore = useAuthStore()
    authStore.user = {
      user_id: 'u1',
      nickname: '测试用户',
      avatar: '',
      phone: '',
      phone_bound: false,
      wechat_bound: false,
      companies: [
        { company_id: 'c1', name: '公司一', created_at: '' },
        { company_id: 'c2', name: '公司二', created_at: '' },
      ],
      current_company_id: companyId,
      server_time: '',
    }
  }

  it('defaults to boss and persists a manual switch', () => {
    setCompany('c1')
    const store = useWorkspaceViewStore()
    store.load()

    expect(store.isBoss).toBe(true)

    store.setMode('accounting')
    expect(store.isAccounting).toBe(true)
    expect(localStorage.getItem(workspaceStorageKey('c1'))).toBe('accounting')
  })

  it('restores the preference for the active company', async () => {
    writeWorkspaceView('c2', 'accounting')
    setCompany('c1')
    const store = useWorkspaceViewStore()
    store.load()
    expect(store.isBoss).toBe(true)

    const authStore = useAuthStore()
    authStore.user = {
      ...authStore.user!,
      current_company_id: 'c2',
    }
    await nextTick()

    expect(store.isAccounting).toBe(true)
  })
})
