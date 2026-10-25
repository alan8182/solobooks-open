/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'
import { MAX_TABS, matchTabPath, resolveActiveTab, tabTitle } from '@/utils/tabPages'

function makeRoute(path: string, name: string, fullPath = path): RouteLocationNormalizedLoaded {
  return { path, name, fullPath } as unknown as RouteLocationNormalizedLoaded
}

function openPage(store: ReturnType<typeof useTabsStore>, path: string, name = path) {
  return store.open(makeRoute(path, name))
}

describe('tab page registry', () => {
  it('只在工作类页面上开标签，详情页与设置页不占位', () => {
    expect(matchTabPath('/vouchers')).toBe('/vouchers')
    expect(matchTabPath('/ledger/book')).toBe('/ledger/book')
    expect(matchTabPath('/vouchers/VOU-202610-000001')).toBeNull()
    expect(matchTabPath('/mine/accounts')).toBeNull()
  })

  it('详情页高亮最近的父级标签，且精确命中最优先', () => {
    expect(resolveActiveTab('/vouchers/VOU-202610-000001')).toBe('/vouchers')
    expect(resolveActiveTab('/ledger/book')).toBe('/ledger/book')
    expect(resolveActiveTab('/ledger')).toBe('/ledger')
    expect(resolveActiveTab('/mine/accounts')).toBeNull()
  })

  it('拿不到路由 path 时安静返回（单测里 useRoute 可能只有一个空壳）', () => {
    expect(resolveActiveTab(undefined)).toBeNull()
    expect(resolveActiveTab('')).toBeNull()
    expect(matchTabPath(undefined)).toBeNull()
    expect(tabTitle(undefined, false)).toBe('')
  })

  it('标签文案跟当前视图走：老板视图用 bossLabel', () => {
    expect(tabTitle('/reports', false)).toBe('经营报表')
    expect(tabTitle('/reports', true)).toBe('经营')
    expect(tabTitle('/vouchers', true)).toBe('记账凭证')
  })
})

describe('tabs store', () => {
  let store: ReturnType<typeof useTabsStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useTabsStore()
  })

  it('初始只有固定的首页标签', () => {
    expect(store.tabs).toHaveLength(1)
    expect(store.tabs[0]).toMatchObject({ path: '/', pinned: true })
    expect(store.activePath).toBe('/')
    expect(store.isClosable('/')).toBe(false)
  })

  it('打开工作页新增标签，重复打开同一个页面不会翻倍', () => {
    expect(openPage(store, '/vouchers', 'Vouchers')).toEqual({ status: 'opened' })
    expect(openPage(store, '/vouchers', 'Vouchers')).toEqual({ status: 'existing' })
    expect(store.tabs).toHaveLength(2)
    expect(store.activePath).toBe('/vouchers')
    expect(store.cachedViews).toContain('Vouchers')
  })

  it('详情页不新增标签，只把高亮挪到父级标签上', () => {
    openPage(store, '/vouchers', 'Vouchers')
    const outcome = store.open(makeRoute('/vouchers/VOU-1', 'VoucherDetail'))
    expect(outcome).toEqual({ status: 'ignored' })
    expect(store.tabs).toHaveLength(2)
    expect(store.activePath).toBe('/vouchers')
  })

  it('到上限后挤掉最久没用的非固定标签，并保留首页', () => {
    const pages = [
      '/copilot', '/todos', '/ledger', '/invoices', '/bank',
      '/vouchers', '/ledger/book', '/closing-wizard', '/tax-filing',
    ]
    pages.forEach((path) => openPage(store, path))
    expect(store.tabs).toHaveLength(MAX_TABS)
    expect(store.isFull).toBe(true)

    const outcome = openPage(store, '/reports')
    expect(outcome).toEqual({ status: 'replaced', evictedPath: '/copilot' })
    expect(store.tabs.map((tab) => tab.path)).not.toContain('/copilot')
    expect(store.tabs.map((tab) => tab.path)).toContain('/')
    expect(store.tabs).toHaveLength(MAX_TABS)
    // 被挤掉的页面同时退出 keep-alive 缓存
    expect(store.cachedViews).not.toContain('/copilot')
  })

  it('关闭当前标签后给出邻居地址，固定标签关不掉', () => {
    openPage(store, '/vouchers', 'Vouchers')
    openPage(store, '/bank', 'Bank')
    expect(store.close('/bank')).toBe('/vouchers')
    expect(store.tabs.map((tab) => tab.path)).toEqual(['/', '/vouchers'])
    expect(store.close('/')).toBeNull()
    expect(store.tabs).toHaveLength(2)
  })

  it('关闭其他只留下首页和指定标签', () => {
    openPage(store, '/vouchers', 'Vouchers')
    openPage(store, '/bank', 'Bank')
    openPage(store, '/reports', 'Reports')
    store.closeOthers('/bank')
    expect(store.tabs.map((tab) => tab.path)).toEqual(['/', '/bank'])
    expect(store.cachedViews).toEqual(['Home', 'Bank'])
  })

  it('关闭右侧只砍掉后面的标签', () => {
    openPage(store, '/vouchers', 'Vouchers')
    openPage(store, '/bank', 'Bank')
    openPage(store, '/reports', 'Reports')
    store.closeRight('/bank')
    expect(store.tabs.map((tab) => tab.path)).toEqual(['/', '/vouchers', '/bank'])
  })

  it('刷新会推进 reload token，让页面重新挂载', () => {
    openPage(store, '/vouchers', 'Vouchers')
    expect(store.reloadToken('/vouchers')).toBe(0)
    store.reload('/vouchers')
    expect(store.reloadToken('/vouchers')).toBe(1)
  })

  it('reset 清空成只剩首页', () => {
    openPage(store, '/vouchers', 'Vouchers')
    store.reset()
    expect(store.tabs.map((tab) => tab.path)).toEqual(['/'])
    expect(store.cachedViews).toEqual(['Home'])
    expect(store.activePath).toBe('/')
  })

  it('守卫登记与注销：有关闭拦截的标签能被查到', () => {
    openPage(store, '/vouchers', 'Vouchers')
    let dirty = true
    const off = store.registerGuard('/vouchers', { isDirty: () => dirty })
    expect(store.dirtyGuard('/vouchers')?.isDirty()).toBe(true)
    dirty = false
    expect(store.dirtyGuard('/vouchers')).toBeNull()
    off()
    expect(store.guardCount('/vouchers')).toBe(0)
  })

  it('同一个标签可以挂多个守卫（列表被缓存、详情在前台），任一脏就要拦', () => {
    openPage(store, '/vouchers', 'Vouchers')
    let listDirty = false
    let detailDirty = false
    const offList = store.registerGuard('/vouchers', { isDirty: () => listDirty })
    const offDetail = store.registerGuard('/vouchers', { isDirty: () => detailDirty })
    expect(store.guardCount('/vouchers')).toBe(2)
    expect(store.dirtyGuard('/vouchers')).toBeNull()

    detailDirty = true
    expect(store.dirtyGuard('/vouchers')).not.toBeNull()

    // 详情页卸载后，缓存着的列表守卫还在
    offDetail()
    expect(store.dirtyGuard('/vouchers')).toBeNull()
    listDirty = true
    expect(store.dirtyGuard('/vouchers')?.isDirty()).toBe(true)
    offList()
    expect(store.dirtyGuard('/vouchers')).toBeNull()
  })
})
