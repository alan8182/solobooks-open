/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@/utils/constants'
import { resolveAuthRedirect } from '@/router/guards'
import { routes } from '@/router'
import router from '@/router'

describe('sidebar navigation structure', () => {
  it('keeps frequent work at the first level and removes the duplicate record action', () => {
    const topLevelPaths = NAV_ITEMS.flatMap((item) => item.path ? [item.path] : [])

    expect(topLevelPaths).toEqual([
      '/',
      '/copilot',
      '/todos',
      '/private-expenses',
      '/ledger',
      '/invoices',
      '/bank',
      '/closing-wizard',
      '/tax-filing',
      '/reports',
      // 项目利润不再占导航位：入口收敛到「经营报表」页的项目利润卡片，
      // 路由 /aggregation/report 保留（卡片跳转与旧链接不受影响）。
    ])
    expect(topLevelPaths).not.toContain('/aggregation/report')
    expect(topLevelPaths).not.toContain('/record/text')
  })

  it('organizes navigation into four workflow groups', () => {
    const groups = Array.from(new Set(NAV_ITEMS.map((item) => item.group)))
    expect(groups).toEqual(['日常', '账务', '周期', '工具'])
  })

  it('groups vouchers and low-frequency tools into secondary menus', () => {
    const vouchers = NAV_ITEMS.find((item) => item.label === '凭证与账簿')
    const special = NAV_ITEMS.find((item) => item.label === '专项核算')
    const settings = NAV_ITEMS.find((item) => item.label === '设置')

    expect(vouchers?.children?.map((item) => item.path)).toEqual([
      '/vouchers',
      '/ledger/books',
      '/ledger/book',
      '/reports/archive',
    ])
    expect(special?.children?.map((item) => item.path)).toEqual([
      '/assets',
      '/rd-expenses',
      '/mine/salary',
      '/private-expenses',
      '/aggregation',
    ])
    expect(settings?.children?.map((item) => item.path)).toEqual([
      // 税费种认定：税局口径的配置
      '/tax-registration',
      '/mine/accounts',
      '/mine/contacts',
      '/mine/opening',
      '/mine/backup',
      '/mine/mcp',
      '/mine',
    ])
    expect(NAV_ITEMS.some((item) => item.label === '财务工具')).toBe(false)
  })

  it('keeps invoice ledger as a top-level direct navigation item in accounting group', () => {
    const invoiceItem = NAV_ITEMS.find((item) => item.path === '/invoices')
    expect(invoiceItem).toBeDefined()
    expect(invoiceItem?.label).toBe('发票台账')
    expect(invoiceItem?.group).toBe('账务')
    expect(invoiceItem?.children).toBeUndefined()
  })

  it('never exposes a navigation path that does not resolve to a route', () => {
    const paths = NAV_ITEMS.flatMap((item) => [
      item.path,
      ...(item.children?.map((child) => child.path) ?? []),
    ]).filter((path): path is string => Boolean(path))

    paths.forEach((path) => {
      expect(router.resolve(path).matched.length, `导航死链：${path}`).toBeGreaterThan(0)
    })
  })

  it('marks professional bookkeeping routes as accounting-only', () => {
    expect(router.resolve('/vouchers').meta.workspace).toEqual(['accounting'])
    expect(router.resolve('/ledger/book').meta.workspace).toEqual(['accounting'])
    expect(router.resolve('/reports/general-ledger').meta.workspace).toEqual(['accounting'])
    expect(router.resolve('/mine/accounts').meta.workspace).toEqual(['accounting'])
    expect(router.resolve('/rd-expenses').meta.workspace).toEqual(['accounting'])
  })

  it('keeps shared boss and accounting routes available in both views', () => {
    expect(router.resolve('/').meta.workspace).toBeUndefined()
    expect(router.resolve('/ledger').meta.workspace).toBeUndefined()
    expect(router.resolve('/todos').meta.workspace).toBeUndefined()
    expect(router.resolve('/reports').meta.workspace).toBeUndefined()
    expect(router.resolve('/closing-wizard').meta.workspace).toBeUndefined()
    expect(router.resolve('/tax-filing').meta.workspace).toBeUndefined()
    expect(router.resolve('/aggregation/report').meta.workspace).toBeUndefined()
  })
})

describe('landing page routing', () => {
  it('exposes /landing as a public route alongside login and agreement pages', () => {
    const publicPaths = routes
      .filter((route) => route.meta?.requiresAuth === false)
      .map((route) => route.path)
    expect(publicPaths).toContain('/landing')
    expect(publicPaths).toEqual(expect.arrayContaining(['/landing', '/login', '/agreement', '/privacy']))
  })

  it('keeps the workbench root protected while / itself is the public landing', () => {
    // 根路径按登录态分流：/ 本身公开（匿名呈现官网），内层工作台壳仍要求登录
    const root = routes.find((route) => route.path === '/')
    expect(root?.meta?.requiresAuth).toBe(false)
    const workbenchShell = root?.children?.find((route) => route.path === '')
    expect(workbenchShell?.meta?.requiresAuth).toBe(true)
  })
})

describe('platform console routing', () => {
  it('mounts the admin console at /console instead of the scannable /admin', () => {
    // 公开后台路径是爆破字典的高频词，改名只降低被扫概率；真正的门是后端 require_admin
    const consoleRoute = routes.find((route) => route.path === '/console')
    expect(consoleRoute?.name).toBeUndefined()
    expect(consoleRoute?.meta?.requiresAuth).toBe(true)
    expect(consoleRoute?.children?.some((child) => child.name === 'AdminOverview')).toBe(true)

    const legacy = routes.find((route) => route.path === '/admin')
    expect(legacy, '/admin 应已下线，避免旧路径继续被扫描命中').toBeUndefined()
  })
})

describe('auth redirect guard rules', () => {
  const protectedRoute = { path: '/ledger', meta: { requiresAuth: true } }
  const publicRoute = { path: '/landing', meta: { requiresAuth: false } }

  it('leaves unauthenticated root visits in place so RootSwitch renders the landing inline', () => {
    expect(resolveAuthRedirect({ path: '/', meta: {} }, null)).toBeNull()
  })

  it('directs unauthenticated protected visits to login', () => {
    expect(resolveAuthRedirect(protectedRoute, null)).toBe('/login')
    expect(resolveAuthRedirect(protectedRoute, '')).toBe('/login')
  })

  it('lets authenticated users past protected routes but bounces them away from login', () => {
    expect(resolveAuthRedirect(protectedRoute, 'token')).toBeNull()
    expect(resolveAuthRedirect({ path: '/login', meta: {} }, 'token')).toBe('/')
  })

  it('leaves public routes alone in both states', () => {
    expect(resolveAuthRedirect(publicRoute, null)).toBeNull()
    expect(resolveAuthRedirect(publicRoute, 'token')).toBeNull()
  })
})

describe('wide table and report container responsive width rules', () => {
  it('ensures report-container is expanded beyond the legacy 920px bottleneck', async () => {
    // ponytail: ensures multi-column financial tables have sufficient breathing room (1200px+)
    const fs = await import('node:fs')
    const path = await import('node:path')
    const reportsCss = fs.readFileSync(
      path.resolve(__dirname, '../assets/styles/reports.css'),
      'utf-8',
    )
    const closingWizardCss = fs.readFileSync(
      path.resolve(__dirname, '../assets/styles/closing_wizard.css'),
      'utf-8',
    )

    expect(reportsCss).toContain('max-width: 1240px;')
    expect(reportsCss).not.toMatch(/\.report-container\s*\{\s*max-width:\s*920px;/)
    expect(closingWizardCss).toContain('.closing-wizard--wide')
    expect(closingWizardCss).toContain('max-width: 1240px;')
  })
})
