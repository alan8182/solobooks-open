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
import { nextTick, ref } from 'vue'

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
            { category: '往来款', accountCode: '122199', accountName: '其他应收款-待收款项' },
            { category: '营业外收入', accountCode: '5301', accountName: '营业外收入' },
            { category: '税费缴纳', accountCode: '2221026', accountName: '应交增值税（小规模纳税人专用）' },
            { category: '社保公积金', accountCode: '221102', accountName: '应付职工薪酬-单位社保' },
            { category: '个税代缴', accountCode: '2221012', accountName: '应交税费-应交个人所得税' },
            { category: '罚款支出', accountCode: '5711011', accountName: '营业外支出-罚款支出' },
            { category: '税收滞纳金', accountCode: '5711010', accountName: '营业外支出-税收滞纳金' },
            { category: '所得税费用', accountCode: '5801', accountName: '所得税费用' },
          ],
          ambiguityGroups: {
            股东投入: ['股东投入', '资本公积', '股东垫付'],
            资本公积: ['股东投入', '资本公积', '股东垫付'],
            股东垫付: ['股东投入', '资本公积', '股东垫付'],
            营业外收入: ['主营业务收入', '其他业务收入', '营业外收入'],
            往来款: ['往来款', '主营业务收入', '营业外收入', '其他支出'],
            税费缴纳: ['税费缴纳', '社保公积金', '个税代缴', '罚款支出', '税收滞纳金', '所得税费用'],
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

import apiClient from '@/api/client'
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

  it('高置信度但仍展示多义项候选的行，默认不自动预勾选进批量确认', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    const vague = { ...vagueStmt, id: 's-vague' }
    const decisive = { ...decisiveStmt, id: 's-decisive' }
    ws.bankStatements.value = [vague, decisive]
    ws.aiSuggestions.value = [
      { ...ownerTransferSuggestion(0.97, '投资款'), statementId: vague.id },
      { ...ownerTransferSuggestion(0.97, '何静谷实缴出资款'), statementId: decisive.id },
    ]
    await nextTick()

    // 决断词单义行自动预勾选；含糊多义项行允许手动勾选，但默认不预勾选
    expect(ws.isSelectableForBatch(vague)).toBe(true)
    expect(ws.selectedStatementIds.value).toEqual([decisive.id])
    expect(ws.batchTargetStatements.value.map((s: any) => s.id)).toEqual([decisive.id])

    // 口径同源：未点选候选前归入「需要你拍板」，点选后转入「系统有把握」
    expect(ws.isNeedsAttentionStatement(vague)).toBe(true)
    expect(ws.isNeedsAttentionStatement(decisive)).toBe(false)
    expect(ws.readyStatements.value.map((s: any) => s.id)).toEqual([decisive.id])
    expect(ws.attentionStatements.value.map((s: any) => s.id)).toEqual([vague.id])

    ws.rowCategoryChoice.value[vague.id] = '资本公积'
    expect(ws.isNeedsAttentionStatement(vague)).toBe(false)
    expect(ws.readyStatements.value.map((s: any) => s.id)).toEqual([vague.id, decisive.id])
    expect(ws.attentionStatements.value).toEqual([])
  })

  it('refund_pair 与 micro_auth 确定性规则不展开模糊候选，自动归入系统有把握并默认预勾选', async () => {
    await loadAccountMapping()
    const ws = buildWorkspace()
    const pay = { id: 's-pay', amount: -0.15, description: '汇兑 | 附言: 微信开放平台注册验证 (对方: 腾讯)', transaction_date: '2026-05-30', matched_entry_id: '' }
    const ref = { id: 's-ref', amount: 0.15, description: '汇兑 | 附言: 微信公众号注册退款 (对方: 腾讯)', transaction_date: '2026-05-30', matched_entry_id: '' }
    const micro = { id: 's-micro', amount: 0.44, description: '入账 | 附言: 791015企业实名验证 (对方: 银联商务)', transaction_date: '2026-05-31', matched_entry_id: '' }
    ws.bankStatements.value = [pay, micro, ref]
    ws.aiSuggestions.value = [
      { statementId: pay.id, pairedStatementId: ref.id, type: 'new', confidence: 0.9, rule: 'refund_pair', suggestedEntry: { category: '往来款', direction: 'expense' } },
      { statementId: micro.id, type: 'new', confidence: 1.0, rule: 'micro_auth', suggestedEntry: { category: '营业外收入', direction: 'income' } },
      { statementId: ref.id, pairedStatementId: pay.id, type: 'new', confidence: 0.9, rule: 'refund_pair', suggestedEntry: { category: '往来退款', direction: 'income' } },
    ]
    await nextTick()

    expect(ws.ambiguityGroupOf(pay)).toEqual([])
    expect(ws.ambiguityGroupOf(micro)).toEqual([])
    expect(ws.attentionStatements.value).toEqual([])
    // 一付一退在 readyStatements 中紧邻聚类
    expect(ws.readyStatements.value.map((s: any) => s.id)).toEqual([pay.id, ref.id, micro.id])
    expect(ws.selectedStatementIds.value.sort()).toEqual([micro.id, pay.id, ref.id])
    expect(ws.suggestionBadgeLabel(pay)).toBe('一付一退对冲（净额 ¥0.00）')
    expect(ws.suggestionBadgeLabel(micro)).toBe('小额实名验证')

    // 取消勾选一侧对冲，另一侧同步取消
    ws.toggleStatementSelection(pay.id, false)
    expect(ws.selectedStatementIds.value).toEqual([micro.id])
  })

  it('税费缴纳金库扣款展开 6 候选且不预勾选，点选罚款支出后按所选科目入账且不沉淀自动规则', async () => {
    await loadAccountMapping()
    vi.mocked(apiClient.post).mockClear()
    const ws = useReconciliationWorkspace({
      month: ref('2026-10'),
      currentStep: ref(2),
      entryPeriod: () => '2026-10',
      entryStore: { activeEntries: [] } as any,
      backfillConfirm: { withBackfill: (fn: any) => fn(false, undefined) } as any,
      openVoucherModal: async () => {},
      scrollIntoBank: () => {},
      employees: ref([]),
      currentVoucherEntry: ref(null),
      reloadAll: async () => {},
      apiErrorMessage: (_e: unknown, fallback: string) => fallback,
    })

    const taxStmt = {
      id: 'stmt-tax-treasury',
      amount: -200,
      description: '税费社保 (对方: 金库杭州市余杭区支库)',
      transaction_date: '2026-10-15',
      matched_entry_id: '',
    }
    ws.bankStatements.value = [taxStmt]
    ws.aiSuggestions.value = [
      {
        statementId: taxStmt.id,
        type: 'new',
        confidence: 0.85,
        suggestedEntry: {
          category: '税费缴纳',
          direction: 'expense',
          accountCode: '2221026',
          accountName: '应交增值税（小规模纳税人专用）',
          title: '税费缴纳',
        },
      },
    ]
    await nextTick()

    expect(ws.ambiguityGroupOf(taxStmt)).toEqual([
      '税费缴纳', '社保公积金', '个税代缴', '罚款支出', '税收滞纳金', '所得税费用',
    ])
    expect(ws.isNeedsAttentionStatement(taxStmt)).toBe(true)
    expect(ws.isAmbiguityActive(taxStmt)).toBe(true)
    expect(ws.selectedStatementIds.value).toEqual([])
    expect(ws.attentionStatements.value.map((s: any) => s.id)).toEqual([taxStmt.id])

    // 用户点选「罚款支出」候选 chip
    ws.rowCategoryChoice.value[taxStmt.id] = '罚款支出'
    expect(ws.effectiveSuggestion(taxStmt)?.suggestedEntry).toMatchObject({
      category: '罚款支出',
      accountCode: '5711011',
      accountName: '营业外支出-罚款支出',
    })
    expect(ws.isAmbiguityActive(taxStmt)).toBe(false)
    expect(ws.isNeedsAttentionStatement(taxStmt)).toBe(false)
    expect(ws.readyStatements.value.map((s: any) => s.id)).toEqual([taxStmt.id])

    // 批量确认与单笔确认都应提交用户所选「罚款支出」且不附带 rememberRule
    await ws.handleBatchConfirmRules()
    expect(apiClient.post).toHaveBeenCalledTimes(1)
    const batchPayload = vi.mocked(apiClient.post).mock.calls[0][1] as any
    expect(batchPayload.new_entry.category).toBe('罚款支出')
    expect(batchPayload.rememberRule).toBeUndefined()

    vi.mocked(apiClient.post).mockClear()
    await ws.handleAcceptSuggested(taxStmt)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
    const singlePayload = vi.mocked(apiClient.post).mock.calls[0][1] as any
    expect(singlePayload.new_entry.category).toBe('罚款支出')
    expect(singlePayload.rememberRule).toBeUndefined()
  })

  it('点选「社保公积金」候选后该行不得进批量确认，单笔按所选分类提交走岗位拦截', async () => {
    await loadAccountMapping()
    vi.mocked(apiClient.post).mockClear()
    const ws = useReconciliationWorkspace({
      month: ref('2026-10'),
      currentStep: ref(2),
      entryPeriod: () => '2026-10',
      entryStore: { activeEntries: [] } as any,
      backfillConfirm: { withBackfill: (fn: any) => fn(false, undefined) } as any,
      openVoucherModal: async () => {},
      scrollIntoBank: () => {},
      employees: ref([]),
      currentVoucherEntry: ref(null),
      reloadAll: async () => {},
      apiErrorMessage: (_e: unknown, fallback: string) => fallback,
    })

    const taxStmt = {
      id: 'stmt-tax-ss-channel',
      amount: -3670,
      description: '税款 | 00TX:000990:20261010:000YST:实时缴税:税单号:433016261013026301',
      transaction_date: '2026-10-10',
      matched_entry_id: '',
    }
    ws.bankStatements.value = [taxStmt]
    ws.aiSuggestions.value = [
      {
        statementId: taxStmt.id,
        type: 'new',
        confidence: 0.85,
        suggestedEntry: {
          category: '税费缴纳',
          direction: 'expense',
          accountCode: '2221026',
          accountName: '应交增值税（小规模纳税人专用）',
          title: '税费缴纳',
        },
      },
    ]
    await nextTick()

    // 未点选前属于模糊待决断行，本来就不能批量
    expect(ws.isSelectableForBatch(taxStmt)).toBe(false)

    // 点选社保后仍不能批量：批量入口会绕过 409 期初冲销弹窗，导致整批静默失败
    ws.rowCategoryChoice.value[taxStmt.id] = '社保公积金'
    expect(ws.effectiveSuggestion(taxStmt)?.suggestedEntry).toMatchObject({
      category: '社保公积金',
      accountCode: '221102',
      accountName: '应付职工薪酬-单位社保',
    })
    expect(ws.isSelectableForBatch(taxStmt)).toBe(false)

    vi.mocked(apiClient.post).mockClear()
    await ws.handleAcceptSuggested(taxStmt)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(apiClient.post).mock.calls[0][1] as any
    expect(payload.new_entry.category).toBe('社保公积金')
    // 模糊组分类不沉淀规则
    expect(payload.rememberRule).toBeUndefined()
  })
})
