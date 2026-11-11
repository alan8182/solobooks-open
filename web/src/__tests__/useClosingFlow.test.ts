/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useClosingFlow } from '@/pages/closing-wizard/composables/useClosingFlow'

const harness = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  incomeStatement: vi.fn(),
  getTodos: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: harness.get,
    post: harness.post,
  },
}))
vi.mock('naive-ui', () => ({
  useMessage: () => ({
    warning: harness.warning,
    error: harness.error,
    success: vi.fn(),
  }),
  useDialog: () => ({ warning: vi.fn() }),
}))
vi.mock('@/api/report', () => ({
  getIncomeStatement: harness.incomeStatement,
  getAIDiagnosis: vi.fn(),
}))
vi.mock('@/api/todo', () => ({
  getTodos: harness.getTodos,
}))
vi.mock('@/utils/errorPresenter', () => ({ reportErrorToast: vi.fn() }))
vi.mock('@/composables/useRescue', () => ({ offerRescue: () => false }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useClosingFlow snapshot authorization', () => {
  it('sends the preview hash and refreshes after a stale close response', async () => {
    const hash = 'a'.repeat(64)
    harness.incomeStatement.mockResolvedValue({
      rows: [{ key: 'operating_revenue', values: { current: 100 } }],
      totals: { netProfit: { current: 80 } },
    })
    harness.get
      .mockResolvedValueOnce({
        periodStatus: 'open',
        canClose: true,
        blockingCount: 0,
        snapshotHash: hash,
        checks: [],
      })
      .mockResolvedValueOnce({
        periodStatus: 'open',
        canClose: true,
        blockingCount: 0,
        snapshotHash: 'b'.repeat(64),
        checks: [],
      })
    harness.post.mockRejectedValue({
      response: {
        data: {
          detail: {
            code: 'CLOSING_SNAPSHOT_STALE',
            message: '账务数据已变化',
          },
        },
      },
    })

    const month = ref('2026-10')
    const currentStep = ref(3)
    const periodCloseChecks = ref<any>(null)
    const flow = useClosingFlow({
      month,
      currentStep,
      periodCloseChecks,
      hasBlockingPayroll: ref(false),
      isZeroRecordMonth: ref(false),
      confirmZeroActivity: ref(false),
      loadReconciliationHealth: vi.fn(async () => {}),
      hasBlockingReconciliationRisk: () => false,
      entryStore: { fetchEntries: vi.fn(async () => {}) } as any,
    })

    await flow.runClosing()

    expect(harness.post).toHaveBeenCalledWith(
      '/api/periods/2026-10/close',
      { expectedSnapshotHash: hash },
    )
    expect(harness.get).toHaveBeenCalledTimes(2)
    expect(periodCloseChecks.value.snapshotHash).toBe('b'.repeat(64))
    expect(harness.warning).toHaveBeenCalledWith(
      expect.stringContaining('本次没有结账'),
      expect.objectContaining({ duration: 7000 }),
    )
  })
})

describe('useClosingFlow closing todos', () => {
  it('fetches the post-closing todo summary after a successful close without delaying the success toast', async () => {
    harness.incomeStatement.mockResolvedValue({
      rows: [{ key: 'operating_revenue', values: { current: 100 } }],
      totals: { netProfit: { current: 80 } },
    })
    harness.get.mockResolvedValue({
      periodStatus: 'open',
      canClose: true,
      blockingCount: 0,
      snapshotHash: 'c'.repeat(64),
      checks: [],
    })
    harness.post.mockResolvedValue({ status: 'closed', periodStatus: 'closed', autoPostedCount: 0, closingVoucherRefs: [] })
    harness.getTodos.mockResolvedValue({
      todos: [
        {
          id: 'filing-1', key: 'filing-1', type: 'filing', category: 'filing',
          title: '征期临近 - 增值税（2026-10）', desc: '截止 2026-11-15（剩 10 天）',
          priority: 'medium', done: false, dismissed: false, meta: { ruleId: 'filing_deadline' },
        },
        {
          id: 't-1', key: 't-1', type: 'task', title: '招待费复核',
          priority: 'medium', done: true, dismissed: false, meta: { ruleId: 'entertainment_limit' },
        },
      ],
    })

    const month = ref('2026-10')
    const currentStep = ref(3)
    const periodCloseChecks = ref<any>(null)
    const flow = useClosingFlow({
      month,
      currentStep,
      periodCloseChecks,
      hasBlockingPayroll: ref(false),
      isZeroRecordMonth: ref(false),
      confirmZeroActivity: ref(false),
      loadReconciliationHealth: vi.fn(async () => {}),
      hasBlockingReconciliationRisk: () => false,
      entryStore: { fetchEntries: vi.fn(async () => {}) } as any,
    })

    await flow.runClosing()

    // 待办取数不阻塞成功 toast，只要求最终填充卡片数据
    await vi.waitFor(() => expect(harness.getTodos).toHaveBeenCalledTimes(1))
    await vi.waitFor(() =>
      expect(flow.closingTodos.value.pendingCount).toBe(1),
    )
    expect(flow.closingTodos.value.topItems[0].route).toBe('/tax-filing?period=2026-Q4')
  })

  it('keeps the card empty and silent when the todos endpoint fails', async () => {
    harness.incomeStatement.mockResolvedValue({
      rows: [{ key: 'operating_revenue', values: { current: 100 } }],
      totals: { netProfit: { current: 80 } },
    })
    harness.get.mockResolvedValue({
      periodStatus: 'open',
      canClose: true,
      blockingCount: 0,
      snapshotHash: 'd'.repeat(64),
      checks: [],
    })
    harness.post.mockResolvedValue({ status: 'closed', periodStatus: 'closed', autoPostedCount: 0, closingVoucherRefs: [] })
    harness.getTodos.mockRejectedValue(new Error('network down'))

    const month = ref('2026-10')
    const currentStep = ref(3)
    const periodCloseChecks = ref<any>(null)
    const flow = useClosingFlow({
      month,
      currentStep,
      periodCloseChecks,
      hasBlockingPayroll: ref(false),
      isZeroRecordMonth: ref(false),
      confirmZeroActivity: ref(false),
      loadReconciliationHealth: vi.fn(async () => {}),
      hasBlockingReconciliationRisk: () => false,
      entryStore: { fetchEntries: vi.fn(async () => {}) } as any,
    })

    await flow.runClosing()

    await vi.waitFor(() => expect(harness.getTodos).toHaveBeenCalledTimes(1))
    await vi.waitFor(() =>
      expect(flow.closingTodos.value).toEqual({ pendingCount: 0, topItems: [] }),
    )
    // 失败静默：不允许因待办接口异常弹出错误提示打断结账成功页
    expect(harness.error).not.toHaveBeenCalled()
    expect(harness.warning).not.toHaveBeenCalled()
  })
})
