/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect } from 'vitest'
import type { RouteRecordRaw } from 'vue-router'
import { NAV_ITEMS } from '@/utils/constants'
import { matchTabPath, resolveActiveTab } from '@/utils/tabPages'
import { routes } from '@/router'

function findRoute(records: RouteRecordRaw[], targetPath: string): RouteRecordRaw | undefined {
  for (const r of records) {
    if (r.path === targetPath) return r
    if (r.children) {
      const found = findRoute(r.children, targetPath)
      if (found) return found
    }
  }
  return undefined
}

describe('长期资产合并（固定资产 + 无形资产）', () => {
  it('侧边栏「专项核算」中包含合并后的「长期资产」(/assets)，且不再独立展示「无形资产」', () => {
    const specialGroup = NAV_ITEMS.find((item) => item.label === '专项核算')
    expect(specialGroup).toBeDefined()

    const longTermAssetItem = specialGroup?.children?.find((child) => child.path === '/assets')
    expect(longTermAssetItem).toBeDefined()
    expect(longTermAssetItem?.label).toBe('长期资产')
    expect(longTermAssetItem?.icon).toBe('ri-archive-line')

    const legacyIntangibleItem = specialGroup?.children?.find((child) => child.path === '/intangibles')
    expect(legacyIntangibleItem).toBeUndefined()
  })

  it('路由层将 /intangibles 重定向至 /assets?tab=intangible，保证历史外链可用', () => {
    const intangibleRoute = findRoute(routes, 'intangibles')
    expect(intangibleRoute).toBeDefined()
    expect(typeof intangibleRoute?.redirect).toBe('function')

    if (typeof intangibleRoute?.redirect === 'function') {
      const redirected = (intangibleRoute.redirect as any)({ query: { foo: 'bar' } })
      expect(redirected).toEqual({
        path: '/assets',
        query: { tab: 'intangible', foo: 'bar' },
      })
    }
  })

  it('页签系统将无形资产详情与新增路由自动关联至「长期资产」(/assets) 标签', () => {
    expect(matchTabPath('/assets')).toBe('/assets')
    expect(resolveActiveTab('/assets')).toBe('/assets')
    expect(resolveActiveTab('/assets/AST-202610-000001')).toBe('/assets')
    expect(resolveActiveTab('/intangibles/INT-202610-000001')).toBe('/assets')
    expect(resolveActiveTab('/intangibles/create')).toBe('/assets')
  })
})
