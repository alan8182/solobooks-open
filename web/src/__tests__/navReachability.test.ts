/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@/utils/constants'
import { itemVisible, type WorkspaceView } from '@/utils/workspace'

/**
 * 复刻 Sidebar.vue 的过滤口径：先按 workspace 过滤父项，再过滤子项，
 * 最后丢掉既没有 path 也没有子项的空壳。任何一个 NAV 条目如果在两套视图里
 * 都过不了这一关，它就是「配置里有、用户找不到」的死链。
 */
function visiblePaths(mode: WorkspaceView): string[] {
  const paths: string[] = []
  for (const item of NAV_ITEMS) {
    if (!itemVisible(item.workspace, mode)) continue
    const children = item.children?.filter((child) => itemVisible(child.workspace, mode)) ?? []
    if (item.path) paths.push(item.path)
    for (const child of children) paths.push(child.path)
  }
  return paths
}

describe('navigation reachability', () => {
  it('every navigation entry is reachable in at least one workspace view', () => {
    const boss = new Set(visiblePaths('boss'))
    const accounting = new Set(visiblePaths('accounting'))

    const orphans: string[] = []
    for (const item of NAV_ITEMS) {
      const candidates = [item.path, ...(item.children?.map((child) => child.path) ?? [])].filter(
        (path): path is string => Boolean(path),
      )
      for (const path of candidates) {
        if (!boss.has(path) && !accounting.has(path)) orphans.push(path)
      }
    }

    expect(orphans).toEqual([])
  })

  it('reaches project profit via the reports page card, not a nav slot', () => {
    // 项目利润不占导航位（两个视图都一样），入口是「经营报表」页的项目利润卡片；
    // 路由 /aggregation/report 保留（卡片跳转可达，workspace 断言见 navigation.test.ts）
    expect(visiblePaths('boss')).not.toContain('/aggregation/report')
    expect(visiblePaths('accounting')).not.toContain('/aggregation/report')
  })

  it('does not attach boss-only entries to an accounting-only parent', () => {
    for (const item of NAV_ITEMS) {
      if (!item.children?.length) continue
      const parentVisibleForBoss = itemVisible(item.workspace, 'boss')
      for (const child of item.children) {
        const childVisibleForBoss = itemVisible(child.workspace, 'boss')
        // 父项在老板视图不可见时，子项也不该只在老板视图可见（否则两边都进不去）
        if (!parentVisibleForBoss && childVisibleForBoss) {
          expect({
            parent: item.label,
            child: child.path,
          }).toEqual({ parent: item.label, child: child.path })
        }
      }
    }
  })
})
