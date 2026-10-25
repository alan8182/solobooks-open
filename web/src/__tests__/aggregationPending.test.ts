/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// T04 · 待归集清单 + 归集抽屉单测。
//
// 独立文件，避免与 T02/T03 的 aggregationPage.test.ts 冲突。
//
// 覆盖的是本页**最重、最容易出数据事故**的两处：
//   ① toDraftRows 回填 —— PUT /lines/{id}/allocations 是整行替换语义，
//      不回填就会把用户已有的分配**静默抹掉**且无 undo。这是头号红线。
//   ② drawerState 四态 —— 「完成归集」按钮的 disabled 判据若与后端不一致，
//      用户会点一下吃 422，甚至出现「保存了但没完成」的困惑。

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AllocationDrawer, {
  drawerState,
  hasBackfillAnomaly,
  toDraftRows,
} from '@/pages/aggregation/_components/AllocationDrawer.vue'
import type { AllocationDraftRow } from '@/utils/aggregation'
import type { AggregationAllocation, AggregationPendingItem } from '@/api/types'

function allocation(overrides: Partial<AggregationAllocation> = {}): AggregationAllocation {
  return {
    id: 'alloc-1',
    journalLineId: 'line-1',
    dimensionId: 'dim-1',
    memberId: 'member-1',
    amount: 300,
    allocationRate: 0.3,
    allocationMethod: 'direct',
    allocationBasis: '按工时',
    source: 'manual',
    ruleId: null,
    ...overrides,
  }
}

function pendingItem(overrides: Partial<AggregationPendingItem> = {}): AggregationPendingItem {
  return {
    entryId: 'entry-1',
    lineId: 'line-1',
    voucherNo: '记-0001',
    summary: '研发费用报销',
    status: 'posted',
    occurredOn: '2026-03-28',
    periodStart: '2026-04-01',
    accountCode: '5301',
    accountName: '研发费用',
    direction: 'debit',
    amount: 1000,
    allocatedAmount: 300,
    remainingAmount: 700,
    state: 'partial',
    allocations: [],
    ...overrides,
  }
}

function row(overrides: Partial<AllocationDraftRow> = {}): AllocationDraftRow {
  return {
    memberId: 'member-1',
    amount: 0,
    allocationMethod: 'direct',
    allocationBasis: '',
    ...overrides,
  }
}

// ===== ① 回填：防「静默抹掉已有分配」的核心断言 =====

describe('toDraftRows 回填已有分配', () => {
  it('item 带 2 条已有 allocations 时，回填后 rows 长度为 2（不是 0）', () => {
    const item = pendingItem({
      allocations: [
        allocation({ id: 'a1', memberId: 'member-1', amount: 300, allocationBasis: '按工时' }),
        allocation({ id: 'a2', memberId: 'member-2', amount: 700, allocationBasis: '按面积' }),
      ],
    })

    const rows = toDraftRows(item)

    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.memberId)).toEqual(['member-1', 'member-2'])
    expect(rows.map((r) => r.amount)).toEqual([300, 700])
    expect(rows.map((r) => r.allocationBasis)).toEqual(['按工时', '按面积'])
    expect(rows.every((r) => r.allocationMethod === 'direct')).toBe(true)
  })

  it('回填的金额保持 number，不做 Number() 兜底、不引入字符串', () => {
    const rows = toDraftRows(pendingItem({ allocations: [allocation({ amount: 0.1 })] }))
    expect(typeof rows[0].amount).toBe('number')
    expect(rows[0].amount).toBe(0.1)
  })

  it('allocationBasis 为 null 时回填为空串而不是 null（避免输入框拿到 null）', () => {
    const rows = toDraftRows(
      pendingItem({ allocations: [allocation({ allocationBasis: null })] }),
    )
    expect(rows[0].allocationBasis).toBe('')
  })

  it('无已有分配时返回空数组（新建归集场景）', () => {
    expect(toDraftRows(pendingItem())).toEqual([])
    expect(toDraftRows(null)).toEqual([])
    expect(toDraftRows(undefined)).toEqual([])
  })

  it('回填后不得与原 allocations 共享对象引用（改草稿不会污染缓存的行对象）', () => {
    const item = pendingItem({ allocations: [allocation()] })
    const rows = toDraftRows(item)
    expect(rows[0]).not.toBe(item.allocations[0])
    rows[0].amount = 999
    expect(item.allocations[0].amount).toBe(300)
  })
})

describe('hasBackfillAnomaly 回填守卫', () => {
  it('有已有分配但回填为空 → 判为异常（必须 warning + 阻止保存）', () => {
    // 模拟「后端返回了 allocations 但前端解析不出草稿行」的场景
    const broken = pendingItem({ allocations: [allocation()] })
    const rows: AllocationDraftRow[] = []
    expect(hasBackfillAnomaly(broken, rows)).toBe(true)
  })

  it('无已有分配且回填为空 → 不是异常', () => {
    expect(hasBackfillAnomaly(pendingItem(), [])).toBe(false)
    expect(hasBackfillAnomaly(null, [])).toBe(false)
  })

  it('回填成功 → 不是异常', () => {
    const item = pendingItem({ allocations: [allocation()] })
    expect(hasBackfillAnomaly(item, toDraftRows(item))).toBe(false)
  })
})

// ===== ② 抽屉状态机四态 =====

describe('drawerState 四态：按钮 disabled 与差额文案', () => {
  const LINE = 1000

  it('空态：rows=[] → 完成归集禁用，保存草稿可用，提示行金额', () => {
    const state = drawerState([], LINE)
    expect(state.phase).toBe('empty')
    expect(state.canComplete).toBe(false)
    expect(state.canSaveDraft).toBe(true)
    expect(state.diffText).toBe('尚未分配 · 行金额 ¥1000.00')
  })

  it('空态：rows 有但合计为 0 也算空态', () => {
    const state = drawerState([row({ amount: 0 })], LINE)
    expect(state.phase).toBe('empty')
    expect(state.canComplete).toBe(false)
  })

  it('部分归集：合计 500 < 行金额 1000 → 完成禁用、草稿可用、提示还差多少', () => {
    const state = drawerState([row({ amount: 500 })], LINE)
    expect(state.phase).toBe('partial')
    expect(state.canComplete).toBe(false)
    expect(state.canSaveDraft).toBe(true)
    expect(state.diff).toBeCloseTo(-500, 6)
    expect(state.diffText).toBe('还差 ¥500.00（未分配 50%）')
    expect(state.diffTone).toBe('error')
  })

  it('超额：合计 1200 > 行金额 1000 → 完成禁用、**保存草稿也禁用**（后端 rate_total>1 必 422）', () => {
    const state = drawerState([row({ amount: 1200 })], LINE)
    expect(state.phase).toBe('over')
    expect(state.canComplete).toBe(false)
    expect(state.canSaveDraft).toBe(false)
    expect(state.diffText).toBe('超出 ¥200.00，请调减')
    expect(state.diffTone).toBe('error')
  })

  it('完成：合计 == 行金额 → 两个按钮都可用，绿色提示', () => {
    const state = drawerState([row({ amount: 1000 })], LINE)
    expect(state.phase).toBe('complete')
    expect(state.canComplete).toBe(true)
    expect(state.canSaveDraft).toBe(true)
    expect(state.diffText).toBe('已全额分配 ✓')
    expect(state.diffTone).toBe('success')
  })

  it('完成：多行拆分后合计精确等于行金额也算完成（三份 333.33+333.33+333.34 = 1000）', () => {
    const state = drawerState([row({ amount: 333.33 }), row({ amount: 333.33, memberId: 'm2' }), row({ amount: 333.34, memberId: 'm3' })], LINE)
    expect(state.canComplete).toBe(true)
    expect(state.diff).toBe(0)
  })

  it('浮点累加噪声不影响判等：0.1+0.2 判为已平（量化到分得 30）', () => {
    const state = drawerState([row({ amount: 0.1 }), row({ amount: 0.2, memberId: 'm2' })], 0.3)
    expect(state.canComplete).toBe(true)
    expect(state.diff).toBe(0)
    expect(state.total).toBe(0.3)
  })

  // ===== QA-7 回归：金额容差必须按分比较，不能用裸浮点 < 0.005 =====

  it('QA-7 回归：1000.005 vs 1000 → 不可完成（量化到 1000.01，差 1 分）', () => {
    // 旧实现 Math.abs(1000.005 - 1000) = 0.0049999999999954525 < 0.005 → 误判为可完成，
    // 放行 PUT 后被后端 Decimal(20,2) 判ALLOCATION_TOTAL_MISMATCH 回 422
    const state = drawerState([row({ amount: 1000.005 })], LINE)
    expect(state.canComplete).toBe(false)
    expect(state.total).toBe(1000.01)
    expect(state.diff).toBeCloseTo(0.01, 6)
    expect(state.phase).toBe('over')
  })

  it('1000.004 vs 1000 → 可完成：量化到 1000.00（与后端 Decimal(20,2) 同口径，不会 422）', () => {
    const state = drawerState([row({ amount: 1000.004 })], LINE)
    expect(state.canComplete).toBe(true)
    expect(state.total).toBe(1000)
    expect(state.phase).toBe('complete')
  })

  it('1000.006 vs 1000 → 不可完成（量化到 1000.01，差 1 分）', () => {
    expect(drawerState([row({ amount: 1000.006 })], LINE).canComplete).toBe(false)
  })

  it('恰好相等 1000 vs 1000 → 可完成', () => {
    const state = drawerState([row({ amount: 1000 })], LINE)
    expect(state.canComplete).toBe(true)
    expect(state.diff).toBe(0)
  })

  it('量化后的 expected / total / diff 一定是两位小数，不会漏出浮点噪声', () => {
    const state = drawerState([row({ amount: 0.1 }), row({ amount: 0.2, memberId: 'm2' })], 0.3)
    expect(state.expected).toBe(0.3)
    expect(state.total).toBe(0.3)
    expect(state.diff).toBe(0)
    // 展示文案里不会出现 0.30000000000000004
    expect(state.diffText).toBe('已全额分配 ✓')
  })

  it('贷方行金额为负时按绝对值比较（后端 _signed() 使 amount 带符号）', () => {
    const state = drawerState([row({ amount: 1000 })], -1000)
    expect(state.expected).toBe(1000)
    expect(state.canComplete).toBe(true)
    expect(state.phase).toBe('complete')
  })

  it('贷方负金额同样走按分量化：-1000.005 vs -1000 → 不可完成', () => {
    const state = drawerState([row({ amount: 1000.005 })], -1000)
    expect(state.canComplete).toBe(false)
    expect(state.phase).toBe('over')
  })

  it('行金额为 0 的极端行：空态提示不产生 NaN 或除零错误', () => {
    const state = drawerState([], 0)
    expect(state.phase).toBe('empty')
    expect(state.diffText).toBe('尚未分配 · 行金额 ¥0.00')
    expect(Number.isNaN(state.diff)).toBe(false)
  })
})

// ===== ③ 组件级：多条分配缺 basis 时阻止提交（后端 ALLOCATION_BASIS_REQUIRED 镜像）=====

const harness = vi.hoisted(() => ({
  replace: vi.fn(async () => ({})),
  clear: vi.fn(async () => ({})),
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/aggregation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/aggregation')>()),
  replaceLineAllocations: harness.replace,
  clearLineAllocations: harness.clear,
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useMessage: () => harness.message,
}))

/** NDrawer 用 teleport 渲染，内容落在 document.body 上，不能用 wrapper.find */
function queryAll(selector: string): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>(selector))
}

function clickEl(el: HTMLElement | null | undefined): void {
  if (!el) throw new Error(`元素未找到，无法点击`)
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

/** 模拟在 NInput 里输入（Naive UI 监听原生 input 事件做 v-model 同步） */
function typeInto(el: HTMLElement | null | undefined, value: string): void {
  const input = el?.querySelector('input') ?? (el as HTMLInputElement | null)
  if (!input) throw new Error('输入框未找到')
  ;(input as HTMLInputElement).value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('AllocationDrawer 提交校验', () => {
  afterEach(() => {
    harness.replace.mockClear()
    harness.clear.mockClear()
    harness.message.warning.mockClear()
    document.body.innerHTML = ''
  })

  it('回填两条已有分配；其中一条缺 allocationBasis 时阻止 PUT 并提示', async () => {
    const item = pendingItem({
      allocations: [
        allocation({ id: 'a1', memberId: 'member-1', amount: 400, allocationBasis: '按工时' }),
        allocation({ id: 'a2', memberId: 'member-2', amount: 600, allocationBasis: null }),
      ],
    })

    const wrapper = mount(AllocationDrawer, {
      props: {
        show: true,
        item,
        dimensionId: 'dim-1',
        members: [],
        methodOptions: [{ label: '直接归集', value: 'direct' }],
      },
      attachTo: document.body,
    })
    await flushPromises()

    // 顶部常驻「整行替换」告警 + 回填出两条已有分配
    expect(document.body.querySelector('[data-testid="overwrite-alert"]')?.textContent).toContain(
      '保存将覆盖本行在当前维度下的全部分配（2 条）',
    )
    expect(queryAll('.alloc-row')).toHaveLength(2)

    // 合计 1000 == 行金额 1000 → 「完成归集」可用
    const completeBtn = document.body.querySelector('[data-testid="btn-complete"]')
    expect(completeBtn?.className).not.toContain('n-button--disabled')

    // 第二条缺 basis → 点保存草稿必须被前端拦下（后端会吃 422 ALLOCATION_BASIS_REQUIRED）
    clickEl(document.body.querySelector('[data-testid="btn-draft"]'))
    await flushPromises()

    expect(harness.replace).not.toHaveBeenCalled()
    expect(harness.message.warning).toHaveBeenCalledWith('拆给多个归集对象时，每条都必须填写分摊依据')
    expect(document.body.querySelector('[data-testid="validation-error"]')?.textContent).toContain(
      '分摊依据',
    )

    // 补上 basis 后即可提交，且带上 dimensionId 与两条分配
    typeInto(queryAll('.alloc-row')[1].querySelector('input[placeholder*="分摊依据"]'), '按面积')
    await flushPromises()
    clickEl(document.body.querySelector('[data-testid="btn-draft"]'))
    await flushPromises()

    expect(harness.replace).toHaveBeenCalledTimes(1)
    // vi.mock 的 mock.calls 类型是 any[][]，直接断言成元组会被 TS 判重叠不足（TS2352），
    // 故经 unknown 收敛——与 aggregationApi.test.ts:82 的既有写法一致，不用 any。
    const [lineId, body] = harness.replace.mock.calls[0] as unknown as [
      string,
      Record<string, unknown>,
    ]
    expect(lineId).toBe('line-1')
    expect(body.dimensionId).toBe('dim-1')
    expect(body.complete).toBe(false)
    expect(body.allocations).toHaveLength(2)

    wrapper.unmount()
  })

  it('超额时「保存草稿」与「完成归集」双双禁用（后端 rate_total>1 必 422）', async () => {
    const item = pendingItem({
      amount: 1000,
      allocations: [
        allocation({ id: 'a1', memberId: 'member-1', amount: 600, allocationBasis: '按工时' }),
        allocation({ id: 'a2', memberId: 'member-2', amount: 600, allocationBasis: '按面积' }),
      ],
    })

    const wrapper = mount(AllocationDrawer, {
      props: {
        show: true,
        item,
        dimensionId: 'dim-1',
        members: [],
        methodOptions: [{ label: '直接归集', value: 'direct' }],
      },
      attachTo: document.body,
    })
    await flushPromises()

    expect(document.body.querySelector('[data-testid="diff-text"]')?.textContent).toBe('超出 ¥200.00，请调减')
    expect(document.body.querySelector('[data-testid="btn-draft"]')?.className).toContain(
      'n-button--disabled',
    )
    expect(document.body.querySelector('[data-testid="btn-complete"]')?.className).toContain(
      'n-button--disabled',
    )

    // 被禁用的按钮点了也不会发请求
    clickEl(document.body.querySelector('[data-testid="btn-draft"]'))
    await flushPromises()
    expect(harness.replace).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('「清空」按钮仅在该行确有归集记录时出现，避免 DELETE 吃到 404', async () => {
    const wrapper = mount(AllocationDrawer, {
      props: {
        show: true,
        item: pendingItem({ allocations: [] }),
        dimensionId: 'dim-1',
        members: [],
        methodOptions: [{ label: '直接归集', value: 'direct' }],
      },
      attachTo: document.body,
    })
    await flushPromises()

    expect(document.body.querySelector('[data-testid="btn-clear"]')).toBeNull()
    wrapper.unmount()
  })
})