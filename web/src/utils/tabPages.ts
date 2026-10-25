/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { NAV_ITEMS, type NavItem } from './constants'

/**
 * 页内多标签：开标签的「工作类页面」白名单。
 *
 * 只收干活用的页面。设置、引导、认定表这类低频配置页沿用原路由，
 * 不占标签位——否则标签栏会被「会计科目 / 期初设置 / 审计日志」灌满。
 * 详情页（/vouchers/:id、/assets/:id 等）也不单独开标签：它们归到
 * 最近的父级标签下活动，关闭父标签才算离开（见 resolveActiveTab）。
 */
export const TAB_PATHS = [
  // 日常
  '/',
  '/copilot',
  '/todos',
  // 账务
  '/ledger',
  '/invoices',
  '/bank',
  '/vouchers',
  '/ledger/books',
  '/ledger/book',
  // 周期
  '/closing-wizard',
  '/tax-filing',
  '/reports',
  // 专项核算（列表页，进得去就要能开着）
  '/assets',
  '/intangibles',
  '/rd-expenses',
  '/mine/salary',
  '/private-expenses',
  '/recurring',
  '/aggregation',
] as const

/** 首页标签：固定在最左、不可关闭，也不被「关闭其他 / 关闭右侧」带走 */
export const HOME_TAB_PATH = '/'
export const HOME_TAB_NAME = 'Home'

/** 同时打开的标签上限。到顶后打开新页面会挤掉最久没用过的非固定标签，并提示挤掉了谁 */
export const MAX_TABS = 10

interface TabTitleEntry {
  label: string
  bossLabel?: string
}

const TITLE_INDEX: Map<string, TabTitleEntry> = (() => {
  const index = new Map<string, TabTitleEntry>()
  const walk = (items: NavItem[]) => {
    for (const item of items) {
      if (item.path) index.set(item.path, { label: item.label, bossLabel: item.bossLabel })
      if (item.children?.length) {
        for (const child of item.children) {
          if (!child.path) continue
          const bossLabel = (child as { bossLabel?: string }).bossLabel
          index.set(child.path, { label: child.label, bossLabel })
        }
      }
    }
  }
  walk(NAV_ITEMS)
  return index
})()

/**
 * 该路由是否「自己占一个标签」。只有白名单里的路径才算，详情页返回 null。
 */
export function matchTabPath(path: string | null | undefined): string | null {
  if (!path) return null
  return (TAB_PATHS as readonly string[]).includes(path) ? path : null
}

/**
 * 当前路由应该高亮哪个标签：
 * 白名单页面命中自己；详情页命中最近的父级标签（最长前缀优先）。
 */
export function resolveActiveTab(path: string | null | undefined): string | null {
  if (!path) return null
  const exact = matchTabPath(path)
  if (exact) return exact
  const parents = (TAB_PATHS as readonly string[]).filter(
    (candidate) => candidate !== HOME_TAB_PATH && path.startsWith(`${candidate}/`),
  )
  if (!parents.length) return null
  return parents.sort((a, b) => b.length - a.length)[0]
}

/**
 * 标签文案。老板视图用 bossLabel（例如「经营报表」在老板那叫「经营」），
 * 和侧边栏保持同一套叫法，避免同一个页面在导航里叫一个名、标签里叫另一个名。
 */
export function tabTitle(path: string | null | undefined, boss: boolean): string {
  if (!path) return ''
  const entry = TITLE_INDEX.get(path)
  if (!entry) return path
  return boss && entry.bossLabel ? entry.bossLabel : entry.label
}
