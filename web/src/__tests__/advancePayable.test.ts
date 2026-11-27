/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 垫付报销建议：
 * - 后端带 advancePayable 时，卡片展示「可冲减其他应付款余额」；
 * - 认可入账必须保留 owner_transfer 方向（否则 2241 冲减会被记成费用）；
 * - 用户改选其他分类后不再展示冲减承诺。
 */
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('naive-ui', () => ({
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
  useDialog: () => ({ warning: vi.fn() }),
}))
vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn(async () => ({ items: [], ambiguityGroups: {} })),
    post: vi.fn(async () => ({ status: 'matched', entryId: 'bank-s1' })),
  },
}))
vi.mock('@/api/record', () => ({ updateEntry: vi.fn() }))
vi.mock('@/api/rd', () => ({
  getRdExpenseTypes: vi.fn(async () => []),
  getRdProjects: vi.fn(async () => []),
}))

import apiClient from '@/api/client'
import { useReconciliationWorkspace } from '@/pages/closing-wizard/composables/useReconciliationWorkspace'

function buildWorkspace() {
  return useReconciliationWorkspace({
    month: ref('2026-11'),
    currentStep: ref(2),
    entryPeriod: () => '2026-11',
    entryStore: { activeEntries: [] } as any,
    // 模拟无需补记：直接执行业务回调
    backfillConfirm: { withBackfill: async (fn: any) => fn(false, undefined) } as any,
    openVoucherModal: async () => {},
    scrollIntoBank: () => {},
    employees: ref([]),
    currentVoucherEntry: ref(null),
    reloadAll: async () => {},
    apiErrorMessage: (_e: unknown, fallback: string) => fallback,
  })
}

const stmt = {
  id: 's-adv-1',
  amount: -104.3,
  transaction_date: '2026-11-20 11:08:33',
  description: '移动支付 | 报销 (对方: 杨云飞)',
}

const advanceSuggestion = {
  statementId: stmt.id,
  type: 'new',
  confidence: 0.9,
  rule: 'advance_payable',
  requiresReview: true,
  reason: '「杨云飞」其他应付款已有垫付余额 ¥35096.72',
  advancePayable: {
    counterparty: '杨云飞',
    accountCode: '2241',
    accountName: '其他应付款',
    available: '35096.72',
    kind: 'opening',
    sources: [{
      entryId: 'opening-c-2026',
      voucherRef: 'VOU-202610-000001',
      date: '2026-10-01',
      entryType: 'opening',
      amount: '35096.72',
      label: '建账时带入',
    }],
    original: {
      direction: 'expense',
      category: '员工报销',
      accountCode: '122103',
      accountName: '其他应收款-员工往来',
      title: '报销顺丰快递费',
    },
  },
  suggestedEntry: {
    direction: 'owner_transfer',
    category: '员工垫付报销',
    accountCode: '2241',
    accountName: '其他应付款',
    title: '报销顺丰快递费',
  },
}

describe('advance payable suggestion', () => {
  it('展示可冲减的本人其他应付款余额与来源', () => {
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [advanceSuggestion]

    expect(ws.advancePayableOf(stmt)).toEqual(advanceSuggestion.advancePayable)
    expect(ws.advancePayableSourceText(stmt)).toBe('来源：建账时带入 VOU-202610-000001')
    expect(ws.suggestionBadgeLabel(stmt)).toBe('冲减垫付应付')
  })

  it('用户改选其他分类后不再展示冲减承诺', () => {
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [advanceSuggestion]
    ws.rowCategoryChoice.value[stmt.id] = '其他支出'

    expect(ws.advancePayableOf(stmt)).toBeNull()
  })

  it('选「新支出」时按 AI 原分类打开记账对话框', () => {
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [{
      ...advanceSuggestion,
      advancePayable: {
        ...advanceSuggestion.advancePayable,
        original: { ...advanceSuggestion.advancePayable.original, category: '办公费', accountCode: '5602004' },
      },
    }]

    ws.handleAdvanceAsNewExpense(stmt)

    expect(ws.showManualDialog.value).toBe(true)
    expect(ws.manualForm.value.categoryValue).toContain('|办公费|')
  })

  it('AI 原本也判成往来类时，不拿它预填新支出的分类', () => {
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [{
      ...advanceSuggestion,
      advancePayable: {
        ...advanceSuggestion.advancePayable,
        original: { ...advanceSuggestion.advancePayable.original, category: '员工垫付报销', direction: 'owner_transfer' },
      },
    }]

    ws.handleAdvanceAsNewExpense(stmt)

    expect(ws.showManualDialog.value).toBe(true)
    expect(ws.manualForm.value.categoryValue).not.toContain('员工垫付报销')
  })

  it('认可入账保留 owner_transfer，金额按流水绝对值提交', async () => {
    const ws = buildWorkspace()
    await ws.confirmSuggested(stmt, advanceSuggestion)

    const [, payload] = vi.mocked(apiClient.post).mock.calls.at(-1)!
    expect(payload).toMatchObject({
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        direction: 'owner_transfer',
        category: '员工垫付报销',
        amount: 104.3,
      },
    })
  })

  it('普通支出建议仍按流水方向落 expense', async () => {
    const ws = buildWorkspace()
    await ws.confirmSuggested(stmt, {
      type: 'new',
      suggestedEntry: { direction: '', category: '办公费' },
    })

    const [, payload] = vi.mocked(apiClient.post).mock.calls.at(-1)! as any[]
    expect(payload.new_entry.direction).toBe('expense')
    expect(payload.new_entry.category).toBe('办公费')
  })
})
