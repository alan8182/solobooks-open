/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import {
  HOME_TAB_NAME,
  HOME_TAB_PATH,
  MAX_TABS,
  matchTabPath,
  resolveActiveTab,
} from '@/utils/tabPages'

export interface PageTab {
  /** 标签身份：路由 path（不含 query），同一个页面永远只有一个标签 */
  path: string
  /** 路由 name，同时充当 <KeepAlive :include> 的缓存名 */
  name: string
  /** 最近一次停留时的完整地址，切回标签时还原筛选条件 */
  fullPath: string
  /** 首页固定标签：不可关闭 */
  pinned: boolean
  /** 仅运行时使用：越久没碰越先被挤出上限 */
  lastActiveAt: number
}

/** 页面登记的「离开前确认」——有未保存内容时关闭标签必须先问一句 */
export interface TabGuard {
  isDirty: () => boolean
  save?: () => void | Promise<void>
}

export type OpenOutcome =
  | { status: 'opened' }
  | { status: 'existing' }
  /** 到上限了：挤掉了一个最久没用过的标签 */
  | { status: 'replaced'; evictedPath: string }
  /** 不是工作类页面（详情页 / 设置页），不占标签位 */
  | { status: 'ignored' }

export const useTabsStore = defineStore('tabs', () => {
  const tabs = ref<PageTab[]>([
    { path: HOME_TAB_PATH, name: HOME_TAB_NAME, fullPath: HOME_TAB_PATH, pinned: true, lastActiveAt: 0 },
  ])
  /** 传给 <KeepAlive :include>：只有开着的标签才留在内存里 */
  const cachedViews = ref<string[]>([HOME_TAB_NAME])
  /** 当前高亮的标签（可能是详情页，此时高亮它的父级标签） */
  const activePath = ref<string>(HOME_TAB_PATH)
  /** 手动「刷新本页」：值变化会让对应标签重新挂载 */
  const reloadTokens = ref<Record<string, number>>({})

  /**
   * 同一个标签可能同时挂着几个守卫：列表页被 KeepAlive 缓存在后台，详情页在前台，
   * 两边都可能有没保存的东西。所以按标签收一个集合，任何一个脏就要拦。
   */
  const guards = new Map<string, Set<TabGuard>>()

  const isFull = computed(() => tabs.value.length >= MAX_TABS)
  const activeTab = computed(() => tabs.value.find((tab) => tab.path === activePath.value) ?? null)

  function touch(path: string) {
    const tab = tabs.value.find((item) => item.path === path)
    if (tab) tab.lastActiveAt = Date.now()
  }

  function dropFromCache(name: string) {
    cachedViews.value = cachedViews.value.filter((item) => item !== name)
  }

  function removeTab(path: string) {
    const index = tabs.value.findIndex((item) => item.path === path)
    if (index < 0) return null
    const [removed] = tabs.value.splice(index, 1)
    dropFromCache(removed.name)
    delete reloadTokens.value[path]
    guards.delete(path)
    return removed
  }

  /** 只同步高亮位置与地址栏，不增删标签（详情页、设置页走这里） */
  function syncActive(route: RouteLocationNormalizedLoaded) {
    const path = resolveActiveTab(route.path)
    activePath.value = path ?? ''
    if (!path) return
    touch(path)
    const tab = tabs.value.find((item) => item.path === path)
    // 只有停在标签自己的地址上才记 fullPath，详情页的 query 不该覆盖列表筛选
    if (tab && tab.path === route.path) tab.fullPath = route.fullPath
  }

  function open(route: RouteLocationNormalizedLoaded): OpenOutcome {
    const path = matchTabPath(route.path)
    const name = String(route.name ?? '')
    if (!path || !name) {
      syncActive(route)
      return { status: 'ignored' }
    }

    const existing = tabs.value.find((item) => item.path === path)
    if (existing) {
      existing.fullPath = route.fullPath
      existing.lastActiveAt = Date.now()
      activePath.value = path
      return { status: 'existing' }
    }

    let evictedPath = ''
    if (isFull.value) {
      // 到上限：挤掉最久没碰过的非固定标签。首页永远留着。
      const victim = tabs.value
        .filter((item) => !item.pinned)
        .sort((a, b) => a.lastActiveAt - b.lastActiveAt)[0]
      if (victim) {
        evictedPath = victim.path
        removeTab(victim.path)
      }
    }

    tabs.value.push({
      path,
      name,
      fullPath: route.fullPath,
      pinned: false,
      lastActiveAt: Date.now(),
    })
    if (!cachedViews.value.includes(name)) cachedViews.value = [...cachedViews.value, name]
    activePath.value = path

    return evictedPath ? { status: 'replaced', evictedPath } : { status: 'opened' }
  }

  /**
   * 关闭一个标签。若关的是当前标签，返回接下来该去的地址（调用方负责 router.push）。
   */
  function close(path: string): string | null {
    const index = tabs.value.findIndex((item) => item.path === path)
    if (index < 0 || tabs.value[index].pinned) return null
    const wasActive = activePath.value === path
    removeTab(path)
    if (!wasActive) return null
    const next = tabs.value[Math.min(index, tabs.value.length - 1)]
    activePath.value = next?.path ?? ''
    return next ? next.fullPath : HOME_TAB_PATH
  }

  function closeOthers(path: string): string | null {
    const keep = tabs.value.filter((item) => item.pinned || item.path === path)
    for (const tab of tabs.value) {
      if (!keep.some((item) => item.path === tab.path)) {
        dropFromCache(tab.name)
        delete reloadTokens.value[tab.path]
        guards.delete(tab.path)
      }
    }
    tabs.value = keep
    const target = keep.find((item) => item.path === path)
    if (!target) return null
    if (activePath.value !== path) {
      activePath.value = path
      return target.fullPath
    }
    return null
  }

  function closeRight(path: string): string | null {
    const index = tabs.value.findIndex((item) => item.path === path)
    if (index < 0) return null
    const keep = tabs.value.filter((item, i) => i <= index || item.pinned)
    for (const tab of tabs.value) {
      if (!keep.some((item) => item.path === tab.path)) {
        dropFromCache(tab.name)
        delete reloadTokens.value[tab.path]
        guards.delete(tab.path)
      }
    }
    tabs.value = keep
    const target = keep.find((item) => item.path === path)
    if (target && activePath.value !== path) {
      activePath.value = path
      return target.fullPath
    }
    return null
  }

  /** 硬刷新：换 key 重新挂载，页面自己的 onMounted 会重新拉数据 */
  function reload(path: string) {
    reloadTokens.value = { ...reloadTokens.value, [path]: (reloadTokens.value[path] ?? 0) + 1 }
  }

  function reloadToken(path: string | null): number {
    return path ? reloadTokens.value[path] ?? 0 : 0
  }

  /** 可关闭：非固定，且没有登记「必须先确认」的守卫 */
  function isClosable(path: string): boolean {
    const tab = tabs.value.find((item) => item.path === path)
    return Boolean(tab && !tab.pinned)
  }

  function registerGuard(key: string, guard: TabGuard): () => void {
    const set = guards.get(key) ?? new Set<TabGuard>()
    set.add(guard)
    guards.set(key, set)
    return () => {
      const current = guards.get(key)
      if (!current) return
      current.delete(guard)
      if (current.size === 0) guards.delete(key)
    }
  }

  /** 有关闭拦截就返回第一个「确实脏了」的守卫（它带着 save），否则 null */
  function dirtyGuard(path: string): TabGuard | null {
    const set = guards.get(path)
    if (!set) return null
    for (const guard of set) {
      if (guard.isDirty()) return guard
    }
    return null
  }

  function guardCount(path: string): number {
    return guards.get(path)?.size ?? 0
  }

  /** 退出登录 / 切换公司时清空，避免把上一套账的标签带过去 */
  function reset() {
    tabs.value = [{ path: HOME_TAB_PATH, name: HOME_TAB_NAME, fullPath: HOME_TAB_PATH, pinned: true, lastActiveAt: Date.now() }]
    cachedViews.value = [HOME_TAB_NAME]
    activePath.value = HOME_TAB_PATH
    reloadTokens.value = {}
    guards.clear()
  }

  return {
    tabs,
    cachedViews,
    activePath,
    activeTab,
    reloadTokens,
    maxTabs: MAX_TABS,
    isFull,
    open,
    syncActive,
    close,
    closeOthers,
    closeRight,
    reload,
    reloadToken,
    isClosable,
    registerGuard,
    dirtyGuard,
    guardCount,
    reset,
  }
})
