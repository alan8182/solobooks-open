/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@/utils/constants'
import { resolveAuthRedirect } from '@/router/guards'
import { routes } from '@/router'

describe('sidebar navigation structure', () => {
  it('keeps frequent work at the first level and removes the duplicate record action', () => {
    const topLevelPaths = NAV_ITEMS.flatMap((item) => item.path ? [item.path] : [])

    expect(topLevelPaths).toEqual([
      '/',
      '/copilot',
      '/ledger',
      '/todos',
      '/closing-wizard',
      '/tax-filing',
      '/reports',
    ])
    expect(topLevelPaths).not.toContain('/record/text')
  })

  it('organizes navigation into 3 clear temporal/functional groups', () => {
    const groups = Array.from(new Set(NAV_ITEMS.map((item) => item.group)))
    expect(groups).toEqual(['日常', '周期', '管理'])
  })

  it('groups infrequent finance and settings destinations into secondary menus', () => {
    const financeTools = NAV_ITEMS.find((item) => item.label === '财务工具')
    const settings = NAV_ITEMS.find((item) => item.label === '系统设置')

    expect(financeTools?.children?.map((item) => item.path)).toEqual([
      '/record/invoice-import',
      '/bank',
      '/vouchers',
      '/recurring',
      '/assets',
      '/rd-expenses',
      '/mine/salary',
      '/private-expenses',
    ])
    expect(settings?.children?.map((item) => item.path)).toEqual([
      '/mine',
      '/mine/companies',
      '/mine/accounts',
      '/mine/opening',
      '/mine/mcp',
      '/mine/audit-log',
    ])
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

  it('keeps the workbench root protected', () => {
    const root = routes.find((route) => route.path === '/')
    expect(root?.meta?.requiresAuth).toBe(true)
  })
})

describe('auth redirect guard rules', () => {
  const protectedRoute = { path: '/ledger', meta: { requiresAuth: true } }
  const publicRoute = { path: '/landing', meta: { requiresAuth: false } }

  it('directs unauthenticated root visits to the landing page', () => {
    expect(resolveAuthRedirect({ path: '/', meta: {} }, null)).toBe('/landing')
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
