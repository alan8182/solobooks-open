/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 对账工作台模糊分类候选的判定逻辑：
 * - 命中模糊组恒展示候选 chips（资本公积等备选不得因 LLM 高置信度而时有时无）；
 * - 高置信度（≥0.95）视为 AI 已决断，不拦截批量确认；低置信度模糊行必须逐笔决断。
 */
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('naive-ui', () => ({
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
  useDialog: () => ({ warning: vi.fn() }),
}))
vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url.includes('account-mapping')) {
        return {
          items: [
            { category: '股东投入', accountCode: '3001', accountName: '实收资本' },
            { category: '资本公积', accountCode: '3002', accountName: '资本公积' },
            { category: '股东垫付', accountCode: '2241', accountName: '其他应付款-股东' },
          ],
          ambiguityGroups: {
            股东投入: ['股东投入', '资本公积', '股东垫付'],
            资本公积: ['股东投入', '资本公积', '股东垫付'],
            股东垫付: ['股东投入', '资本公积', '股东垫付'],
          },
        }
      }
      return {}
    }),
    post: vi.fn(async () => ({})),
  },
}))
vi.mock('@/api/record', () => ({ updateEntry: vi.fn() }))
vi.mock('@/api/rd', () => ({
  getRdExpenseTypes: vi.fn(async () => []),
  getRdProjects: vi.fn(async () => []),
}))

import { loadAccountMapping } from '@/utils/accounting'
import { useReconciliationWorkspace } from '@/pages/closing-wizard/composables/useReconciliationWorkspace'

function buildWorkspace() {
  return useReconciliationWorkspace({
    month: ref('2026-05'),
    currentStep: ref(2),
    entryPeriod: () => '2026-05',
    entryStore: { activeEntries: [] } as any,
    backfillConfirm: {} as any,
    openVoucherModal: async () => {},
    scrollIntoBank: () => {},
    employees: ref([]),
    currentVoucherEntry: ref(null),
    reloadAll: async () => {},
    apiErrorMessage: (_e: unknown, fallback: string) => fallback,
  })
}

// 收款流水 +¥30,000，AI 高置信度建议股东投入
const stmt = { id: 's1', amount: 30000, matched_entry_id: null, transaction_date: '2026-05-22' }
// 附言决定是否展开候选：含糊（投资款）→ 展开；决断词（实缴出资款）→ 不展开
const vagueStmt = { ...stmt, description: '汇兑 | 附言: 投资款 (对方: 何静谷)' }
const decisiveStmt = { ...stmt, description: '汇兑 | 附言: 何静谷实缴出资款 (对方: 何静谷)' }

function ownerTransferSuggestion(confidence: number, description = '') {
  return {
    statementId: stmt.id,
    type: 'new',
    confidence,
    suggestedEntry: { category: '股东投入', direction: 'income', title: description },
  }
}

describe('reconciliation ambiguity group', () => {
  it('附言含糊（投资款）时高置信度仍展示模糊候选（资本公积不得消失）', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [ownerTransferSuggestion(0.97)]
    ws.aiSuggestions.value = [{ ...ownerTransferSuggestion(0.97), statementId: vagueStmt.id }]

    expect(ws.ambiguityGroupOf(vagueStmt)).toEqual(['股东投入', '资本公积', '股东垫付'])
  })

  it('附言含决断词（实缴出资款）且高置信度时不展示候选', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [ownerTransferSuggestion(0.97, '何静谷实缴出资款')]

    expect(ws.ambiguityGroupOf(decisiveStmt)).toEqual([])
  })

  it('决断词只在高置信度时收起候选，低置信度仍保留安全网', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [ownerTransferSuggestion(0.7, '何静谷实缴出资款')]

    expect(ws.ambiguityGroupOf(decisiveStmt)).toEqual(['股东投入', '资本公积', '股东垫付'])
  })

  it('资本公积建议 + 含糊附言仍展开候选（股东投入等备选可见）', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [
      {
        statementId: vagueStmt.id,
        type: 'new',
        confidence: 0.97,
        suggestedEntry: { category: '资本公积', direction: 'income', title: '投资款' },
      },
    ]

    expect(ws.ambiguityGroupOf(vagueStmt)).toEqual(['股东投入', '资本公积', '股东垫付'])
  })

  it('高置信度不拦截批量确认，点选候选后低置信度恢复批量资格', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()

    ws.aiSuggestions.value = [ownerTransferSuggestion(0.97)]
    expect(ws.isAmbiguityActive(vagueStmt)).toBe(false)

    ws.aiSuggestions.value = [ownerTransferSuggestion(0.7)]
    expect(ws.isAmbiguityActive(vagueStmt)).toBe(true)
    ws.rowCategoryChoice.value[stmt.id] = '资本公积'
    expect(ws.isAmbiguityActive(vagueStmt)).toBe(false)
  })

  it('非模糊组分类不产生候选', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    ws.aiSuggestions.value = [
      {
        statementId: stmt.id,
        type: 'new',
        confidence: 0.97,
        suggestedEntry: { category: '主营业务收入', direction: 'income' },
      },
    ]

    expect(ws.ambiguityGroupOf(stmt)).toEqual([])
    expect(ws.isAmbiguityActive(stmt)).toBe(false)
  })
})
