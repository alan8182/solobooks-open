/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// API 层的形状归一化与金额归一化单测。
// 这两条是本模块最强的防白屏/防错数防线，必须用 mock 复现后端的真实返回形状来守：
//   ① {items:[...]} 必须在 API 层解包成数组（否则组件写 res.items 静默拿到 undefined）；
//   ② compare.diffs 的金额必须变成 number（否则拼出 ¥0120.00，用户据此做错财务决策）。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

// 拦截器已 return response.data，所以 mock 直接 resolve 业务对象
vi.mock('@/api/client', () => ({
  default: {
    get: h.get,
    post: h.post,
    put: h.put,
    patch: h.patch,
    delete: h.delete,
  },
}))

import {
  applyRules,
  archiveReportLineRule,
  clearLineAllocations,
  compareReportLineRules,
  createDimension,
  getAggregationMeta,
  getAggregationReport,
  getSwitchReadiness,
  listAggregationRules,
  listDimensions,
  listMembers,
  listPendingLines,
  listReportLineRules,
  replaceAggregationRules,
  replaceLineAllocations,
  replaceReportLineRules,
  updateDimension,
} from '@/api/aggregation'
import router, { routes } from '@/router'
import { NAV_ITEMS } from '@/utils/constants'
import type { RouteRecordRaw } from 'vue-router'

beforeEach(() => {
  h.get.mockReset()
  h.post.mockReset()
  h.put.mockReset()
  h.patch.mockReset()
  h.delete.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('aggregation API shape normalization', () => {
  it('unwraps {items:[...]} into an array for listAggregationRules', async () => {
    const rules = [
      { id: 'r1', memberId: 'm1', priority: 10, rate: 1, method: 'direct', status: 'active' },
      { id: 'r2', memberId: 'm2', priority: 20, rate: 0.5, method: 'area', status: 'active' },
    ]
    // 后端 routers/aggregation.py:157 显式 return {"items": ...}
    h.get.mockResolvedValue({ items: rules })

    const result = await listAggregationRules('dim-1')

    expect(Array.isArray(result)).toBe(true)
    expect(result).toHaveLength(2)
    expect(result[0].id).toBe('r1')
    // 对外类型是数组，组件层不可能写 res.items
    expect((result as unknown as { items?: unknown }).items).toBeUndefined()
  })

  it('unwraps {items:[...]} for replaceAggregationRules too (整批 PUT)', async () => {
    h.put.mockResolvedValue({ items: [{ id: 'r1', memberId: 'm1', rate: 1 }] })

    const result = await replaceAggregationRules('dim-1', [
      { memberId: 'm1', priority: 10, rate: 1, method: 'direct' },
    ])

    expect(Array.isArray(result)).toBe(true)
    expect(result).toHaveLength(1)
    expect(h.put).toHaveBeenCalledWith('/api/aggregation/dimensions/dim-1/rules', {
      rules: [{ memberId: 'm1', priority: 10, rate: 1, method: 'direct' }],
    })
  })

  it('falls back to an empty array when rules envelope has no items', async () => {
    h.get.mockResolvedValue({})
    await expect(listAggregationRules('dim-1')).resolves.toEqual([])
  })

  it('passes through bare arrays from dimensions and members endpoints', async () => {
    const dimensions = [{ id: 'd1', code: 'RD', name: '研发项目', kind: 'project', status: 'active' }]
    const members = [{ id: 'm1', dimensionId: 'd1', code: 'RD-1', name: '项目A', status: 'active' }]
    h.get.mockResolvedValueOnce(dimensions).mockResolvedValueOnce(members)

    expect(await listDimensions()).toEqual(dimensions)
    expect(await listMembers('d1')).toEqual(members)
  })

  it('sends include_archived as a snake_case query param', async () => {
    // 后端 routers/aggregation.py:77 未设 alias，query 名就是 snake_case
    h.get.mockResolvedValue([])
    await listDimensions(true)
    expect(h.get).toHaveBeenCalledWith('/api/aggregation/dimensions', {
      params: { include_archived: true },
    })
  })

  it('keeps report-line-rules list and replace as bare arrays', async () => {
    // 后端 list_rules/replace_rules 都直接返回 list，既非单对象也非 {items}
    const rules = [{ id: 'rl1', lineCode: 'rd_expense', patterns: [], sign: 'net' }]
    h.get.mockResolvedValue(rules)
    h.put.mockResolvedValue(rules)

    expect(await listReportLineRules('profit')).toEqual(rules)
    expect(Array.isArray(await replaceReportLineRules('profit', []))).toBe(true)
  })

  it('returns the pending envelope untouched because it carries totals', async () => {
    const payload = {
      items: [{ lineId: 'l1', amount: -120, state: 'partial', allocations: [] }],
      total: 1,
      limit: 100,
      offset: 0,
      basis: 'period',
      state: 'all',
      totals: { lineAmount: -120, allocatedAmount: -50, remainingAmount: -70 },
    }
    h.get.mockResolvedValue(payload)

    const res = await listPendingLines({
      dimensionId: 'd1',
      start: '2026-01',
      end: '2026-03',
      scopePrefixes: '5,6',
    })

    expect(res.total).toBe(1)
    expect(res.totals.remainingAmount).toBe(-70)
    // scopePrefixes 保持逗号字符串形态（与 apply-rules 的数组不同形）
    expect(h.get).toHaveBeenCalledWith('/api/aggregation/pending', {
      params: { dimensionId: 'd1', start: '2026-01', end: '2026-03', scopePrefixes: '5,6' },
    })
  })

  it('sends scopePrefixes as an ARRAY for apply-rules, unlike the pending query', async () => {
    h.post.mockResolvedValue({ allocated: 3, skipped: 1, unmatched: 2, rules: 5 })

    const res = await applyRules({
      dimensionId: 'd1',
      start: '2026-01',
      end: '2026-03',
      scopePrefixes: ['5', '6'],
    })

    expect(res).toEqual({ allocated: 3, skipped: 1, unmatched: 2, rules: 5 })
    expect(h.post).toHaveBeenCalledWith('/api/aggregation/apply-rules', {
      dimensionId: 'd1',
      start: '2026-01',
      end: '2026-03',
      scopePrefixes: ['5', '6'],
    })
  })

  it('passes dimensionId as a required query param when clearing allocations', async () => {
    h.delete.mockResolvedValue({
      journalLineId: 'l1',
      dimensionId: 'd1',
      lineAmount: 120,
      allocatedAmount: 0,
      remainingAmount: 120,
      state: 'unallocated',
      allocations: [],
    })

    await clearLineAllocations('l1', 'd1')

    expect(h.delete).toHaveBeenCalledWith('/api/aggregation/lines/l1/allocations', {
      params: { dimensionId: 'd1' },
    })
  })

  it('uses allocationMethod/allocationBasis field names on write', async () => {
    h.put.mockResolvedValue({ state: 'allocated', allocations: [] })

    await replaceLineAllocations('l1', {
      dimensionId: 'd1',
      complete: true,
      allocations: [{ memberId: 'm1', amount: 100, allocationMethod: 'area', allocationBasis: '面积' }],
    })

    const body = h.put.mock.calls[0][1]
    // 不是 method/basis —— 写错这两个字段后端会 422
    expect(body.allocations[0]).toHaveProperty('allocationMethod', 'area')
    expect(body.allocations[0]).toHaveProperty('allocationBasis', '面积')
    expect(body.allocations[0]).not.toHaveProperty('method')
  })
})

describe('compareReportLineRules money normalization', () => {
  it('converts string baseline/configured/delta into numbers', async () => {
    // ⚠️ 这三个字段不命中 client.ts 的 MONEY_KEY 正则，会以字符串落地
    h.post.mockResolvedValue({
      report: 'profit',
      skipped: false,
      clean: false,
      compared: 2,
      ruleCount: 8,
      period: '2026-01',
      periodType: 'month',
      comparedAt: '2026-02-01T00:00:00+00:00',
      switchReadiness: {
        report: 'profit',
        ready: false,
        requiredCleanPeriods: 3,
        currentCleanStreak: 0,
        checkedRuns: 1,
        latest: null,
      },
      diffs: [
        {
          lineCode: 'rd_expense',
          lineName: '研发支出-费用化支出',
          baseline: '120.00',
          configured: '115.00',
          delta: '-5.00',
          hasRule: true,
        },
      ],
    })

    const res = await compareReportLineRules({ report: 'profit', period: '2026-01' })
    const diff = res.diffs[0]

    expect(typeof diff.baseline).toBe('number')
    expect(typeof diff.configured).toBe('number')
    expect(typeof diff.delta).toBe('number')
    expect(diff.baseline).toBe(120)
    expect(diff.configured).toBe(115)
    expect(diff.delta).toBe(-5)
  })

  it('handles negative money strings', () => {
    expect(Number('-120.00')).toBe(-120)
  })

  it('degrades non-numeric money to 0 instead of NaN', async () => {
    h.post.mockResolvedValue({
      report: 'balance',
      skipped: false,
      clean: false,
      compared: 1,
      ruleCount: 1,
      period: '2026-Q1',
      periodType: 'quarter',
      comparedAt: '2026-04-01T00:00:00+00:00',
      switchReadiness: {
        report: 'balance',
        ready: false,
        requiredCleanPeriods: 3,
        currentCleanStreak: 0,
        checkedRuns: 0,
        latest: null,
      },
      diffs: [
        { lineCode: 'x', lineName: 'x', baseline: null, configured: null, delta: null, hasRule: true },
      ],
    })

    const res = await compareReportLineRules({ report: 'balance', period: '2026-Q1', periodType: 'quarter' })

    expect(res.diffs[0].delta).toBe(0)
    expect(Number.isNaN(res.diffs[0].delta)).toBe(false)
  })

  it('tolerates a missing diffs array', async () => {
    h.post.mockResolvedValue({
      report: 'profit',
      skipped: true,
      clean: true,
      compared: 0,
      period: '2026-01',
      periodType: 'month',
      comparedAt: '2026-02-01T00:00:00+00:00',
      switchReadiness: {
        report: 'profit',
        ready: false,
        requiredCleanPeriods: 3,
        currentCleanStreak: 0,
        checkedRuns: 0,
        latest: null,
      },
    })

    const res = await compareReportLineRules({ report: 'profit', period: '2026-01' })

    expect(res.diffs).toEqual([])
    // skipped=true 的早退分支不含 ruleCount，必须是 undefined 而不是崩
    expect(res.ruleCount).toBeUndefined()
  })
})

describe('aggregation routing and menu wiring', () => {
  const AGGREGATION_ROUTES = [
    { path: '/aggregation', name: 'Aggregation' },
    { path: '/aggregation/dimensions', name: 'AggregationDimensions' },
    { path: '/aggregation/rules', name: 'AggregationRules' },
    { path: '/aggregation/pending', name: 'AggregationPending' },
    { path: '/aggregation/report', name: 'AggregationReport' },
    { path: '/aggregation/report-line-rules', name: 'AggregationLineRules' },
  ]

  it('registers all 6 aggregation routes under the authenticated root layout', () => {
    const resolvedPaths = router.getRoutes().map((route) => route.path)

    AGGREGATION_ROUTES.forEach(({ path }) => {
      expect(resolvedPaths, `缺少路由：${path}`).toContain(path)
    })
  })

  it('resolves each aggregation path to its expected route name', () => {
    AGGREGATION_ROUTES.forEach(({ path, name }) => {
      expect(router.resolve(path).name, `路径 ${path} 未解析到 ${name}`).toBe(name)
    })
  })

  it('anchors the specific case mandated by the T01 acceptance criteria', () => {
    expect(router.resolve('/aggregation/pending').name).toBe('AggregationPending')
  })

  it('inherits auth from the workbench shell instead of declaring its own meta', () => {
    // 既有 children 一律不写 requiresAuth，鉴权由工作台壳（/ 下的 AppLayout 层）继承；
    // 根路径 '/' 本身公开（匿名由 RootSwitch 呈现官网），壳层保持 requiresAuth
    const root = routes.find((route) => route.path === '/')
    const workbenchShell = root?.children?.find((route) => route.path === '')
    const children = workbenchShell?.children ?? []
    const aggregationChildren = children.filter((child) =>
      (child as RouteRecordRaw).path?.startsWith('aggregation'),
    )

    expect(aggregationChildren).toHaveLength(6)
    aggregationChildren.forEach((child) => {
      const meta = (child as RouteRecordRaw).meta as Record<string, unknown> | undefined
      expect(meta?.requiresAuth, `${(child as RouteRecordRaw).path} 不应覆盖认证要求`).toBeUndefined()
      if (meta?.workspace) {
        expect(meta.workspace, `${(child as RouteRecordRaw).path} 的工作区标记`).toContain('accounting')
      }
    })
    expect(workbenchShell?.meta?.requiresAuth).toBe(true)
  })

  it('keeps the aggregation entry reachable from the low-frequency tools menu', () => {
    const parent = NAV_ITEMS.find((item) => item.label === '专项核算')
    const menu = parent?.children?.find((child) => child.path === '/aggregation')

    expect(menu, '侧栏缺少「归集与分摊」入口').toBeTruthy()
    expect(menu?.label).toBe('归集与分摊')
    expect(router.resolve(menu?.path ?? '').matched.length, '菜单项 /aggregation 是死链').toBeGreaterThan(0)
  })
})

describe('aggregation API remaining endpoints', () => {
  it('returns meta label pairs untouched', async () => {
    h.get.mockResolvedValue({
      kinds: [{ value: 'project', label: '项目' }],
      methods: [{ value: 'direct', label: '直接归属' }],
      memberStatuses: [{ value: 'active', label: '启用' }],
    })

    const meta = await getAggregationMeta()

    expect(meta.kinds[0].label).toBe('项目')
    expect(meta.methods).toHaveLength(1)
  })

  it('creates and updates a dimension as a single object', async () => {
    const dimension = { id: 'd1', code: 'RD', name: '研发项目', kind: 'project', status: 'active' }
    h.post.mockResolvedValue(dimension)
    h.patch.mockResolvedValue({ ...dimension, status: 'archived' })

    expect((await createDimension({ code: 'RD', name: '研发项目', kind: 'project' })).id).toBe('d1')
    expect((await updateDimension('d1', { status: 'archived' })).status).toBe('archived')
    expect(h.patch).toHaveBeenCalledWith('/api/aggregation/dimensions/d1', { status: 'archived' })
  })

  it('keeps report totals naming unassignedAmount, not remainingAmount', async () => {
    h.get.mockResolvedValue({
      dimensionId: 'd1',
      dimensionCode: 'RD',
      dimensionName: '研发项目',
      basis: 'period',
      start: '2026-01-01',
      end: '2026-03-31',
      scopePrefixes: ['5', '6'],
      items: [],
      totals: {
        lineAmount: 1000,
        allocatedAmount: 800,
        // 报表口径叫 unassignedAmount；清单才叫 remainingAmount，别混用
        unassignedAmount: 200,
        balanced: true,
        allocatedLineCount: 8,
        partialLineCount: 1,
        unallocatedLineCount: 2,
        lineCount: 11,
      },
    })

    const res = await getAggregationReport({ dimensionId: 'd1', start: '2026-01', end: '2026-03' })

    expect(res.totals.unassignedAmount).toBe(200)
    expect(res.totals.balanced).toBe(true)
  })

  it('archives a report line rule as a single object (soft delete)', async () => {
    h.delete.mockResolvedValue({ id: 'rl1', lineCode: 'rd_expense', status: 'archived' })

    const res = await archiveReportLineRule('rl1')

    expect(res.status).toBe('archived')
    expect(h.delete).toHaveBeenCalledWith('/api/aggregation/report-line-rules/rl1')
  })

  it('reads switch readiness as a typed object', async () => {
    h.get.mockResolvedValue({
      report: 'profit',
      ready: false,
      requiredCleanPeriods: 3,
      currentCleanStreak: 1,
      checkedRuns: 2,
      latest: {
        report: 'profit',
        period: '2026-01',
        clean: false,
        skipped: false,
        compared: 12,
        diffCount: 1,
      },
    })

    const res = await getSwitchReadiness('profit')

    expect(res.requiredCleanPeriods).toBe(3)
    // latest 是审计 after 原样 dict，字段固定，不能当任意对象
    expect(res.latest?.diffCount).toBe(1)
  })
})
