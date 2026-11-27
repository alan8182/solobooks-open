/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { NCheckbox, NSelect } from 'naive-ui'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ClosingWizard from '@/pages/closing-wizard/index.vue'
import FileDropzone from '@/components/business/FileDropzone.vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

const harness = vi.hoisted(() => {
  // 模拟用户在「本月还有单据未上传」确认弹窗里点「继续结账」：放行步骤跳转并还原弹窗生命周期
  const autoConfirmImportGapDialog = (opts: any) => {
    if (String(opts?.title || '') !== '本月还有单据未上传') return
    opts?.onPositiveClick?.()
    opts?.onAfterLeave?.()
  }
  return {
    autoConfirmImportGapDialog,
    store: null as any,
    auth: null as any,
    route: { value: { query: {} as Record<string, string> } } as any,
    get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn(), push: vi.fn(), replace: vi.fn(),
    warning: vi.fn(), error: vi.fn(), success: vi.fn(),
    dialog: { warning: vi.fn(autoConfirmImportGapDialog), error: vi.fn(), success: vi.fn() },
    rdProjects: [] as any[],
    rdExpenseTypes: [] as any[],
  }
})
vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.store }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => harness.auth }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: harness.push, replace: harness.replace, currentRoute: harness.route }),
  RouterLink: { name: 'RouterLink', props: ['to'], template: '<a class="router-link-stub"><slot /></a>' },
}))
vi.mock('@/api/client', () => ({
  default: { get: harness.get, post: harness.post, patch: harness.patch, delete: harness.del }, makeIdempotencyKey: () => 'test-only',
}))
vi.mock('@/composables/useDeleteConfirm', () => ({ useDeleteConfirm: () => ({ confirmDelete: vi.fn() }) }))
vi.mock('@/api/rd', () => ({
  getRdProjects: async () => harness.rdProjects,
  getRdExpenseTypes: async () => ({ items: harness.rdExpenseTypes }),
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ warning: harness.warning, error: harness.error, success: harness.success, info: vi.fn() }),
  useDialog: () => harness.dialog,
}))

beforeEach(() => {
  // 账套资料未加载（startDate 缺失）时向导回落到上月；需要其它默认月的用例自己覆盖 harness.auth
  harness.auth = { currentCompanyId: 'c_test_company', companies: [] }
  harness.rdProjects = []
  harness.rdExpenseTypes = []
  const pinia = createPinia()
  setActivePinia(pinia)
  useWorkspaceViewStore().setMode('accounting')
})

afterEach(() => {
  vi.clearAllMocks()
  harness.route.value.query = {}
})

describe('closing wizard settlement interaction', () => {
  it('confirms both original entries and removes them from the rendered unmatched column', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      { id: 'fee', title: '微信手续费', direction: 'expense', amount: 0.05, occurDate: `${month}-14`, category: '银行及金融手续费', source: 'wechat_bill', billingStatus: '' },
      { id: 'income', title: '微信原始收入', direction: 'income', amount: 5, occurDate: `${month}-14`, category: '主营业务收入', source: 'wechat_bill', billingStatus: '' },
    ])
    const statement = reactive({ id: 'bank', amount: 4.95, description: '财付通结算', transaction_date: `${month}-15`, matched_entry_id: '', matched_entry_ids: [] as string[] })
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        if (statement.matched_entry_id) throw new Error('AI service unavailable after confirmation')
        return { suggestions: [{
        type: 'match', statementId: 'bank', matchedEntryId: 'fee', matchedEntryIds: ['fee', 'income'],
        matchedEntries: entries, statement: { amount: 4.95, description: '财付通结算' }, confidence: 0.95,
      }] }
      }
      if (url === '/api/bank/confirm-suggestion') {
        expect(payload.type).toBe('match')
        expect(payload.matched_entry_ids).toEqual(['fee', 'income'])
        statement.matched_entry_id = 'fee'
        statement.matched_entry_ids = ['fee', 'income', 'settlement']
        entries.forEach(entry => { entry.billingStatus = 'reconciled' })
        entries.push({ id: 'settlement', title: '微信净额结算', amount: 4.95,
          direction: 'owner_transfer', occurDate: `${month}-15`, category: '微信支付结算',
          billingStatus: 'reconciled', reconciliationType: 'wechat_settlement', voucherStatus: 'draft',
          voucherLines: { debit: [{ accountCode: '1002', amount: 4.95 }], credit: [{ accountCode: '1012001', amount: 4.95 }] },
        })
        return { status: 'matched', settlementEntryId: 'settlement', settlementStatus: 'draft' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })
    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第二步：银行流水智能对账与配平')
    expect(wrapper.text()).toContain('财付通结算')
    expect(wrapper.text()).toContain('确认配对')
    // 一笔流水合并多笔账目：必须保留调整勾选入口（否则只能盲点确认）
    expect(wrapper.text()).toContain('调整多票勾选')

    const confirmBtn = wrapper.findAll('button').find(button => button.text().includes('确认配对'))!
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(harness.error).not.toHaveBeenCalled()
    expect(harness.post.mock.calls.filter(([url]) => url === '/api/bank/confirm-suggestion')).toHaveLength(1)
    expect(wrapper.text()).toContain('已认领并完成流水')

    wrapper.unmount()
  })
})

describe('closing wizard undo bank-created entry', () => {
  it('撤销原入账先解链再红冲，避免流水指向已冲销凭证', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      { id: 'fee-bank', title: '微信支付结算', direction: 'expense', amount: 4.95,
        occurDate: `${month}-15`, category: '银行及金融手续费', source: 'bank_import',
        billingStatus: '', voucherStatus: 'posted' },
    ])
    const statement = reactive({ id: 'bank', amount: 4.95, description: '财付通结算',
      transaction_date: `${month}-15`, matched_entry_id: '', matched_entry_ids: [] as string[] })
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        if (statement.matched_entry_id) return { suggestions: [] }
        return { suggestions: [{
          type: 'match', statementId: 'bank', matchedEntryId: 'fee-bank', matchedEntryIds: ['fee-bank'],
          matchedEntries: entries, statement: { amount: 4.95, description: '财付通结算' }, confidence: 0.9,
        }] }
      }
      if (url === '/api/bank/confirm-suggestion') {
        expect(payload).toMatchObject({ statement_id: 'bank', type: 'match', matched_entry_ids: [] })
        return { status: 'unmatched', entryId: null, entryIds: [] }
      }
      if (url === '/api/record/entries/fee-bank/reverse') {
        entries.splice(0, entries.length)
        return { entry: { id: 'fee-bank', status: 'reversed' } }
      }
      throw new Error(`Unexpected write: ${url}`)
    })
    harness.dialog.warning.mockImplementation((opts: any) => {
      if (opts?.title === '撤销原入账') opts.onPositiveClick?.()
      if (String(opts?.title || '') === '本月还有单据未上传') {
        opts.onPositiveClick?.()
        opts.onAfterLeave?.()
      }
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const undoBtn = wrapper.findAll('button').find(button => button.text().includes('撤销原入账'))!
    expect(undoBtn).toBeTruthy()
    await undoBtn.trigger('click')
    await flushPromises()

    const calls = harness.post.mock.calls.map(([url]) => url)
    const unlinkAt = calls.indexOf('/api/bank/confirm-suggestion')
    const reverseAt = calls.indexOf('/api/record/entries/fee-bank/reverse')
    expect(unlinkAt).toBeGreaterThanOrEqual(0)
    expect(reverseAt).toBeGreaterThan(unlinkAt)
    expect(harness.success).toHaveBeenCalledWith('原入账已撤销，该流水已回到待处理，可重新选择分类入账')

    wrapper.unmount()
  })
})

describe('closing wizard import gap prompt', () => {
  it('第 1 步单据未齐：状态条警示并弹确认，确认后同月不再重复弹', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      { id: 'income', title: '服务收入', direction: 'income', amount: 100, occurDate: `${month}-08`, category: '主营业务收入', source: 'manual', billingStatus: '' },
    ])
    const statement = reactive({ id: 'bank', amount: 100, description: '公户收款', transaction_date: `${month}-08`, matched_entry_id: 'income', matched_entry_ids: ['income'] })
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    // 首次调用先不自动确认，验证弹窗真的拦住了跳步（后续调用恢复默认的自动确认）
    harness.dialog.warning.mockImplementationOnce(() => {})
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    // 状态条升为警告样式并列出缺失项；银行流水本月有 1 笔，已导入部分仍展示
    const bar = wrapper.find('.step-status-bar')
    expect(bar.classes()).toContain('step-status-bar--warning')
    expect(bar.text()).toContain('本月单据未齐')
    expect(bar.text()).toContain('已导入：银行流水 1 笔')
    expect(bar.text()).toContain('还未上传：发票、平台账单')

    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()
    expect(harness.dialog.warning).toHaveBeenCalledTimes(1)
    expect(harness.dialog.warning.mock.calls[0]?.[0]).toMatchObject({ title: '本月还有单据未上传' })
    expect(wrapper.text()).not.toContain('第二步：银行流水智能对账与配平')

    // 用户点「继续结账」→ 放行进第 2 步
    const dialogOptions = harness.dialog.warning.mock.calls[0]?.[0] as { onPositiveClick?: () => void }
    dialogOptions.onPositiveClick?.()
    await flushPromises()
    expect(wrapper.text()).toContain('第二步：银行流水智能对账与配平')

    // 回第 1 步再跳第 2 步：本月已确认过「继续」，不再重复弹
    await wrapper.findAll('.n-step')[0]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()
    expect(harness.dialog.warning).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })
})

describe('closing wizard bulk confirm', () => {
  it('confirms every recognized suggestion, including matches, instead of only rule statements', async () => {
    // 旧实现把整批交给 /api/bank/batch-confirm-rules，该接口只处理规则类新记账，
    // match 类建议全被跳过，却仍然提示「已自动配平并入账 N 笔」。
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-match', amount: -100, description: '转出-云服务费', transaction_date: `${month}-18`, matched_entry_id: '' },
      { id: 's-new', amount: -25, description: '扣除-账户管理费', transaction_date: `${month}-20`, matched_entry_id: '' },
      { id: 's-fallback', amount: 2400, description: '转入-技术服务预付款', transaction_date: `${month}-22`, matched_entry_id: '' },
      { id: 's-review', amount: -50000, description: '往来款（对方: 通用供应商）', transaction_date: `${month}-23`, matched_entry_id: '' },
    ])
    const confirmed: any[] = []
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => {
            if (s.id === 's-match') {
              return {
                type: 'match', statementId: s.id, matchedEntryId: 'e-cloud', matchedEntryIds: ['e-cloud'],
                matchedEntry: { id: 'e-cloud', title: '云服务费' }, confidence: 1,
                statement: { date: s.transaction_date, description: s.description, amount: s.amount },
              }
            }
            if (s.id === 's-fallback') {
              return {
                type: 'new', statementId: s.id, confidence: 0.3,
                reason: '无法自动分类，已按流水收支方向生成草稿',
                suggestedEntry: { title: '技术服务预付款', category: '其他业务收入' },
                statement: { date: s.transaction_date, description: s.description, amount: s.amount },
              }
            }
            if (s.id === 's-review') {
              return {
                type: 'new', statementId: s.id, confidence: 0.98, requiresReview: true,
                rule: 'company_memory_rule',
                reviewReasons: ['本次金额明显高于历史规则范围'],
                suggestedEntry: { title: '信息技术服务费', category: '信息技术服务费' },
                statement: { date: s.transaction_date, description: s.description, amount: s.amount },
              }
            }
            return {
              type: 'new', statementId: s.id, confidence: 1,
              suggestedEntry: { title: '账户管理费', category: '财务费用-手续费' },
              statement: { date: s.transaction_date, description: s.description, amount: s.amount },
            }
          }),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmed.push(payload)
        const statement = statements.find(s => s.id === payload.statement_id)!
        statement.matched_entry_id = (payload.matched_entry_ids || [])[0] || 'created-entry'
        return { status: 'matched' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('.feed-card')
    // 单笔匹配不再给「核对已有账目」：匹配单据标题已可点开凭证，旧按钮只弹无落点提示
    expect(cards[0].text()).not.toContain('核对已有账目')
    expect(cards[0].text()).not.toContain('调整多票勾选')
    expect(cards[1].text()).toContain('详细记账')
    // 兜底草稿不能冒充「建议分类」
    expect(cards[2].text()).toContain('系统兜底分类')
    expect(cards[2].text()).toContain('按此入账')
    expect(cards[2].text()).not.toContain('建议分类')
    expect(wrapper.text()).toContain('规则待复核')

    const bulkButton = wrapper.findAll('button').find(button => button.text().includes('已识别流水'))!
    expect(bulkButton.text()).toContain('2 笔')
    await bulkButton.trigger('click')
    await flushPromises()

    expect(confirmed).toHaveLength(2)
    expect(confirmed.map(item => item.type).sort()).toEqual(['match', 'new'])
    expect(harness.post.mock.calls.some(([url]) => url === '/api/bank/batch-confirm-rules')).toBe(false)
    expect(harness.success).toHaveBeenCalledWith('已确认 2 笔流水')
    // 兜底草稿置信度低，不参与一键确认，仍留在待处理列表里
    expect(wrapper.text()).toContain('待处理银行流水 (2)')

    wrapper.unmount()
  })
})

describe('closing wizard unified view', () => {
  it('opens the ordinary AI accountant with the selected closing month', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const aiEntry = wrapper.findAll('button').find(button => button.text().includes('AI 财务官'))
    expect(aiEntry).toBeTruthy()
    await aiEntry!.trigger('click')

    expect(harness.push).toHaveBeenCalledWith({
      path: '/copilot',
      query: {
        q: expect.stringContaining(`${month} 月度结账`),
      },
    })

    wrapper.unmount()
  })

  it('shows the three import steps in boss view and folds professional invoice columns', async () => {
    // 老板与会计共用同一套向导：导入入口必须在第一步，专业列默认收起
    useWorkspaceViewStore().setMode('boss')
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      { id: 'inv-1', title: '云服务发票', direction: 'expense', amount: 106, occurDate: `${month}-10`, source: 'invoice_import', billingStatus: '' },
    ])
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async () => ({ suggestions: [] }))

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('导入发票')
    expect(text).toContain('导入平台账单')
    expect(text).toContain('导入银行流水')
    expect(text).toContain('去整页管理')

    const headers = () => wrapper.findAll('.report-table th').map(th => th.text())
    expect(headers()).toEqual(['日期', '发票名称', '记账分类', '发票金额', '状态'])

    const toggle = wrapper.findAll('button').find(button => button.text().includes('展开明细'))!
    await toggle.trigger('click')
    expect(headers()).toContain('发票类型')
    expect(headers()).toContain('入账模式')
    expect(headers()).toContain('冲销 / 删除')

    // 第三步的专业核验默认折叠，老板只看到任务卡
    await wrapper.findAll('.n-step')[2]!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.readiness-row').exists()).toBe(false)

    wrapper.unmount()
    useWorkspaceViewStore().setMode('accounting')
  })
})

describe('closing wizard duplicate guard', () => {
  it('offers pairing instead of repeating the booking when the same amount exists', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-dup', amount: -431.25, description: '转出-云服务费', transaction_date: `${month}-18`, matched_entry_id: '' },
    ])
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'new', statementId: 's-dup', confidence: 1,
            suggestedEntry: { title: '腾讯云服务器费用', category: '信息技术服务费' },
            statement: { date: `${month}-18`, description: '转出-云服务费', amount: -431.25 },
          }],
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        const err: any = new Error('409')
        err.response = {
          data: {
            detail: {
              code: 'EXISTING_ENTRY_MATCH',
              candidateEntryIds: ['e-old'],
              entries: [{ id: 'e-old', title: '腾讯云服务器费用', date: `${month}-18` }],
            },
          },
        }
        throw err
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const accept = wrapper.findAll('button').find(button => button.text().includes('认可入账'))!
    await accept.trigger('click')
    await flushPromises()

    // 跳步前的「单据未上传」确认也会走 dialog.warning，这里只统计认可入账触发的配对确认
    const pairingCalls = harness.dialog.warning.mock.calls.filter(
      (call) => String((call[0] as { title?: string })?.title || '').includes('相同金额'),
    )
    expect(pairingCalls).toHaveLength(1)
    const options = pairingCalls[0][0]
    expect(options.title).toContain('相同金额')
    expect(options.positiveText).toBe('直接配对')
    expect(options.negativeText).toBe('确认是两笔，仍要记账')

    wrapper.unmount()
  })
})

describe('closing wizard payroll step', () => {
  function mountAtPayrollStep(sheet: any) {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    harness.route.value.query = { step: '2' }
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      if (url.includes('/api/salary/sheets/')) return { ...sheet, month }
      return { items: [], records: [], summary: {} }
    })
    return { month, wrapper: mount(ClosingWizard) }
  }

  const accruedSheet = {
    id: 'sheet-1', status: 'accrued',
    accrual_voucher_id: 'v-accrue', payment_voucher_id: null,
    ss_paid_voucher_id: null, tax_paid_voucher_id: null,
    records: [{
      employee_name: '张三', department: '技术部',
      base_salary: 20000, bonus: 0,
      company_social_security: 6600, personal_social_security: 3700, personal_tax: 1500,
      net_salary: 14800, total_cost: 26600,
    }],
  }

  it('consumes the flat salary sheet payload and allows closing once accrued', async () => {
    // GET /api/salary/sheets/{month} 返回扁平工资表对象（无 sheet 包装层）。
    // 旧代码读 res.sheet 恒为 undefined → 结账被永久阻断。
    const { wrapper } = mountAtPayrollStep(accruedSheet)
    await flushPromises()

    // 已计提：直接收敛为单行提示（不渲染冗余的手动登记发薪/社保待办按钮），发薪扣款交由银行流水自动核销
    expect(wrapper.text()).toContain('员工薪酬已计提入账')
    expect(wrapper.text()).toContain('不阻断本月关账')
    expect(wrapper.text()).toContain('发薪与社保扣款将随银行流水自动核销')
    expect(wrapper.text()).not.toContain('登记发薪/社保')
    expect(wrapper.text()).not.toContain('确认发放记账')
    expect(wrapper.text()).not.toContain('确认缴纳社保')

    // 可展开查看工资明细表，且已计提不阻断进入下一步
    const detailBtn = wrapper.findAll('button').find(button => button.text().trim() === '明细')!
    await detailBtn.trigger('click')
    expect(wrapper.text()).toContain('¥14,800.00')
    expect(wrapper.text()).toContain('¥26,600.00')

    const nextButton = wrapper.findAll('button').find(button => button.text().includes('下一步'))!
    await nextButton.trigger('click')
    expect(harness.warning).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('blocks closing when payroll has not been accrued', async () => {
    const { wrapper } = mountAtPayrollStep({
      ...accruedSheet,
      status: 'draft',
      accrual_voucher_id: null,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('一键计提记账')

    const nextButton = wrapper.findAll('button').find(button => button.text().includes('下一步'))!
    await nextButton.trigger('click')
    expect(harness.warning).toHaveBeenCalledWith('本月在职员工工资尚未完成计提，请先计提后再结账（发放可延至次月）。')

    wrapper.unmount()
  })

  it('blocks payroll actions while the accrual voucher is pending review', async () => {
    const { wrapper } = mountAtPayrollStep({ ...accruedSheet, status: 'pending_review' })
    await flushPromises()

    expect(wrapper.text()).toContain('工资计提凭证正在等待审核')
    expect(wrapper.text()).toContain('计提凭证待审核')
    expect(wrapper.text()).not.toContain('待计提（阻断关账）')
    expect(wrapper.text()).not.toContain('确认缴纳社保')
    expect(wrapper.text()).not.toContain('社保怎么申报？')

    const nextButton = wrapper.findAll('button').find(button => button.text().includes('下一步'))!
    await nextButton.trigger('click')
    expect(harness.warning).toHaveBeenCalledWith('工资计提凭证正在等待审核，审核通过后才能继续。')

    wrapper.unmount()
  })

  it('does not block closing once salary, social and tax are all settled', async () => {
    const { wrapper } = mountAtPayrollStep({
      ...accruedSheet,
      status: 'paid', payment_voucher_id: 'v-pay', paymentStatus: 'posted',
      ss_paid_voucher_id: 'v-ss', hf_paid_voucher_id: 'v-hf', tax_paid_voucher_id: 'v-tax',
      ssPaymentStatus: 'posted', hfPaymentStatus: 'posted', taxPaymentStatus: 'posted',
    })
    await flushPromises()

    expect(wrapper.text()).toContain('员工薪酬与社保已结清')
    expect(wrapper.text()).not.toContain('发薪与社保扣款将随银行流水自动核销')

    const nextButton = wrapper.findAll('button').find(button => button.text().includes('下一步'))!
    await nextButton.trigger('click')
    expect(harness.warning).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('shows one-click confirm CTA when salary sheet is identical to previous month', async () => {
    harness.post.mockResolvedValue({ ...accruedSheet, status: 'accrued' })
    const { month, wrapper } = mountAtPayrollStep({
      ...accruedSheet,
      status: 'draft',
      accrual_voucher_id: null,
      carryForward: {
        hasPreviousSheet: true,
        sourceMonth: '2026-09',
        isIdenticalToPrevious: true,
        changeReasons: [],
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('已自动带出 2026-09 工资与社保明细')
    expect(wrapper.text()).toContain('金额与上月一致')
    expect(wrapper.text()).toContain('有变化，去修改')
    const confirmBtn = wrapper.findAll('button').find(b => b.text().includes('与上月无变化，直接确认'))!
    expect(confirmBtn).toBeTruthy()
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(harness.post).toHaveBeenCalledWith(
      `/api/salary/sheets/${month}/accrue`,
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('displays carry-forward change reasons and confirms modified sheet', async () => {
    const { month, wrapper } = mountAtPayrollStep({
      ...accruedSheet,
      status: 'draft',
      accrual_voucher_id: null,
      carryForward: {
        hasPreviousSheet: true,
        sourceMonth: '2026-09',
        isIdenticalToPrevious: false,
        clearedBonusTotal: '1000.00',
        clearedDeductionTotal: '0.00',
        changeReasons: ['上月一次性奖金 ¥1,000.00 已自动清零', '累计预扣个税较上月 +¥30.00'],
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('已基于 2026-09 自动带出固定工资与社保')
    expect(wrapper.text()).toContain('上月一次性奖金 ¥1,000.00 已自动清零')
    expect(wrapper.text()).toContain('累计预扣个税较上月 +¥30.00')
    expect(wrapper.text()).toContain('有变化，去修改')
    expect(wrapper.text()).toContain('确认本月工资并计提')

    const editBtn = wrapper.findAll('button').find(b => b.text().includes('有变化，去修改'))!
    await editBtn.trigger('click')
    expect(harness.push).toHaveBeenCalledWith({
      path: '/mine/salary',
      query: { month },
    })

    wrapper.unmount()
  })
})

describe('closing wizard tiered matching and rule memory', () => {
  it('filters attention statements and passes rememberRule when confirming a suggestion', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-mem', amount: -0.74, description: '汇兑 | 附言: 小程序注册验证 (对方: 招商银行股份有限公司深圳高新园科创支行)', transaction_date: `${month}-04`, matched_entry_id: '' },
      { id: 's-doubt', amount: -880, description: '转出-未知外部转账', transaction_date: `${month}-08`, matched_entry_id: '' },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [
            {
              type: 'new',
              statementId: 's-mem',
              confidence: 0.98,
              rule: 'company_memory_rule',
              reason: '命中企业记忆规则：「招商银行股份有限公司深圳高新园科创支行」自动归入其他应收款',
              suggestedEntry: { title: '其他应收款-招商银行', category: '其他应收款' },
              statement: { date: `${month}-04`, description: statements[0].description, amount: -0.74 },
            },
            {
              type: 'new',
              statementId: 's-doubt',
              confidence: 0.25,
              reason: '无法自动分类',
              suggestedEntry: { title: '未知外部转账', category: '其他支出' },
              statement: { date: `${month}-08`, description: statements[1].description, amount: -880 },
            },
          ],
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        statements[0].matched_entry_id = 'bank-s-mem'
        return { status: 'created', entryId: 'bank-s-mem', savedRulePattern: '招商银行股份有限公司深圳高新园科创支行' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('记忆规则 98%')
    expect(wrapper.text()).toContain('疑义待定 (1)')

    // 点击「疑义待定」分流胶囊，仅展示低置信度待人工指定流水
    const attentionPill = wrapper.findAll('.tier-pill').find(btn => btn.text().includes('疑义待定'))!
    await attentionPill.trigger('click')
    expect(wrapper.findAll('.feed-card')).toHaveLength(1)
    expect(wrapper.findAll('.feed-card')[0]!.text()).toContain('未知外部转账')

    // 切回全部，点击第一条的「认可入账」，验证携带 rememberRule: true 并提示已沉淀规则
    const allPill = wrapper.findAll('.tier-pill').find(btn => btn.text().includes('全部'))!
    await allPill.trigger('click')
    expect(wrapper.findAll('.feed-card')).toHaveLength(2)

    const acceptBtn = wrapper.findAll('button').find(btn => btn.text().includes('认可入账'))!
    await acceptBtn.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(1)
    expect(confirmPayloads[0].rememberRule).toBe(true)
    expect(harness.success).toHaveBeenCalledWith(
      expect.stringContaining('已沉淀规则：后续「招商银行股份有限公司深圳高新园科创支行」自动归入此科目'),
    )

    wrapper.unmount()
  })

  it('确认入账后默认沉淀规则且展示「撤销此次确认」，点击撤销后恢复待处理状态', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-undo', amount: -500, description: '汇兑 | 附言: 云资源续费 (对方: 阿里云计算有限公司)', transaction_date: `${month}-12`, matched_entry_id: '' },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [] as any[], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => ({
            type: 'new',
            statementId: s.id,
            confidence: 0.96,
            reason: '信息技术服务费特征',
            suggestedEntry: { title: '阿里云云资源续费', category: '信息技术服务费' },
            statement: { date: s.transaction_date, description: s.description, amount: s.amount },
          })),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        if (payload.type === 'new') {
          statements[0].matched_entry_id = 'bank-s-undo'
          return { status: 'created', entryId: 'bank-s-undo' }
        }
        if (payload.type === 'match' && Array.isArray(payload.matched_entry_ids) && payload.matched_entry_ids.length === 0) {
          statements[0].matched_entry_id = ''
          return { status: 'unmatched' }
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })
    harness.del.mockResolvedValue({ status: 'deleted' })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.row-remember-pill').exists()).toBe(false)

    const acceptBtn = wrapper.findAll('button').find(btn => btn.text().includes('认可入账'))!
    await acceptBtn.trigger('click')
    await flushPromises()

    expect(confirmPayloads[0].rememberRule).toBe(true)
    expect(wrapper.findAll('.feed-card')).toHaveLength(0)

    const undoBar = wrapper.find('.recent-confirm-undo-bar')
    expect(undoBar.exists()).toBe(true)
    expect(undoBar.text()).toContain('撤销此次确认')
    await undoBar.findAll('button').find(btn => btn.text().includes('撤销此次确认'))!.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(2)
    expect(confirmPayloads[1]).toMatchObject({ statement_id: 's-undo', type: 'match', matched_entry_ids: [] })
    expect(harness.del).toHaveBeenCalledWith('/api/record/entries/bank-s-undo')
    expect(wrapper.find('.recent-confirm-undo-bar').exists()).toBe(false)
    expect(wrapper.findAll('.feed-card')).toHaveLength(1)

    wrapper.unmount()
  })

  it('一付一退对冲流水显示「一并冲抵入账」，点一次同时确认两笔并支持成对撤销', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-pay', amount: -0.15, description: '汇兑 | 附言: 微信开放平台注册验证 (对方: 腾讯)', transaction_date: `${month}-30`, matched_entry_id: '' },
      { id: 's-ref', amount: 0.15, description: '汇兑 | 附言: 微信公众号注册退款 (对方: 腾讯)', transaction_date: `${month}-30`, matched_entry_id: '' },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [] as any[], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => (
            s.id === 's-pay'
              ? {
                  type: 'new', statementId: 's-pay', pairedStatementId: 's-ref', confidence: 0.9, rule: 'refund_pair',
                  suggestedEntry: { title: '往来暂付款', category: '往来款' },
                  statement: { date: s.transaction_date, description: s.description, amount: s.amount },
                }
              : {
                  type: 'new', statementId: 's-ref', pairedStatementId: 's-pay', confidence: 0.9, rule: 'refund_pair',
                  suggestedEntry: { title: '往来退款对冲', category: '往来退款' },
                  statement: { date: s.transaction_date, description: s.description, amount: s.amount },
                }
          )),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        if (payload.type === 'new') {
          const target = statements.find(s => s.id === payload.statement_id)!
          const entryId = `entry-${target.id}`
          target.matched_entry_id = entryId
          return { status: 'created', entryId }
        }
        if (payload.type === 'match' && Array.isArray(payload.matched_entry_ids) && payload.matched_entry_ids.length === 0) {
          const target = statements.find(s => s.id === payload.statement_id)!
          target.matched_entry_id = ''
          return { status: 'unmatched' }
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })
    harness.del.mockResolvedValue({ status: 'deleted' })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('一付一退对冲（净额 ¥0.00）')
    const pairBtns = wrapper.findAll('button').filter(btn => btn.text().includes('一并冲抵入账'))
    expect(pairBtns).toHaveLength(2)

    await pairBtns[0]!.trigger('click')
    await flushPromises()

    // 点一次同时确认了付款与退款两笔，且不沉淀规则
    expect(confirmPayloads).toHaveLength(2)
    expect(confirmPayloads.map(p => p.statement_id)).toEqual(['s-pay', 's-ref'])
    expect(confirmPayloads.every(p => p.rememberRule === undefined)).toBe(true)
    expect(wrapper.findAll('.feed-card')).toHaveLength(0)

    // 撤销此次确认同时退回两笔并删除两张草稿凭证
    const undoBar = wrapper.find('.recent-confirm-undo-bar')
    expect(undoBar.exists()).toBe(true)
    await undoBar.findAll('button').find(btn => btn.text().includes('撤销此次确认'))!.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(4)
    expect(harness.del).toHaveBeenCalledWith('/api/record/entries/entry-s-pay')
    expect(harness.del).toHaveBeenCalledWith('/api/record/entries/entry-s-ref')
    expect(wrapper.findAll('.feed-card')).toHaveLength(2)

    wrapper.unmount()
  })

  it('「改分类」弹窗把账目分类放第一位，且切换分类自动同步凭证摘要（手改后不覆盖）', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statements = reactive<any[]>([
      { id: 's-cat', amount: -500, description: '汇兑 | 附言: 往来款 (对方: 深度求索)', transaction_date: `${month}-31`, matched_entry_id: '' },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [] as any[], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'new',
            statementId: 's-cat',
            confidence: 0.85,
            suggestedEntry: { title: `信息技术服务费-${month}-31`, category: '信息技术服务费' },
            statement: { date: `${month}-31`, description: statements[0].description, amount: -500 },
          }],
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        return { status: 'created', entryId: 'entry-s-cat' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const modifyBtn = wrapper.findAll('button').find(btn => btn.text().includes('改分类'))!
    await modifyBtn.trigger('click')
    await flushPromises()

    const vm = wrapper.vm as any
    // 1. 通用「分类-日期」预填标题自动清洗为「对手方 · 分类」
    expect(vm.manualForm.title).toBe('深度求索 · 信息技术服务费')

    // 2. 弹窗首项为「账目分类」，切换分类后自动同步凭证摘要
    const labels = Array.from(document.body.querySelectorAll('.manual-dialog .manual-form-label')).map(el => el.textContent?.trim())
    expect(labels[0]).toContain('账目分类')
    vm.handleManualCategoryChange('expense|办公费|5602001|管理费用-办公费')
    await flushPromises()
    expect(vm.manualForm.title).toBe('深度求索 · 办公费')

    // 3. 用户手动修改摘要后，再切换分类不会覆盖用户自定义标题
    vm.manualForm.title = '自定义采购摘要'
    vm.handleManualCategoryChange('expense|业务招待费|5602002|管理费用-业务招待费')
    await flushPromises()
    expect(vm.manualForm.title).toBe('自定义采购摘要')

    // 4. 清空摘要仍可提交，后端入账自动兜底生成「对方 · 分类」
    vm.manualForm.title = ''
    await vm.handleManualClassify()
    await flushPromises()
    expect(confirmPayloads).toHaveLength(1)
    expect(confirmPayloads[0].new_entry).toMatchObject({
      category: '业务招待费',
      title: '深度求索 · 业务招待费',
    })

    wrapper.unmount()
  })

  it('opens boss ledger detail drawer when clicking summary card and navigates to target ledger', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      {
        id: 'e-reimb-1',
        title: '快递费',
        direction: 'expense',
        amount: 140,
        occurDate: `${month}-15`,
        category: '办公费',
        source: 'manual',
        payer: 'employee_personal:张三',
        billingStatus: 'has_invoice',
      },
    ])
    harness.store = {
      entries,
      activeEntries: entries,
      loadAll: vi.fn(async () => entries),
      updateEntry: vi.fn(),
    }
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [], total: 0 }
      if (url === '/api/bank/reconciliation-stats') return { total: 0, matched: 0, suggested: 0, pending: 0, manual: 0, rate: 100 }
      if (url === '/api/bank/suggestions') return { suggestions: [] }
      if (url === '/api/closing/readiness') return { checks: [] }
      if (url === '/api/tax/filing-obligations') return { obligations: [] }
      if (url === '/api/employees') return [{ id: 'emp-1', name: '张三' }]
      if (url === '/api/ledger/advance-balances') {
        return {
          month,
          items: [
            {
              payer: 'employee_personal:张三',
              kind: 'employee',
              name: '张三',
              opening: '0.00',
              advance: '140.00',
              reimbursed: '0.00',
              closing: '140.00',
              count: 1,
              lastDate: `${month}-15`,
              entries: [
                {
                  id: 'v-reimb-1',
                  sourceId: 'e-reimb-1',
                  date: `${month}-15`,
                  period: month,
                  summary: '快递费',
                  category: '办公费',
                  amount: '140.00',
                  direction: 'advance',
                  isCurrentMonth: true,
                },
              ],
            },
          ],
          totals: { opening: '0.00', advance: '140.00', reimbursed: '0.00', closing: '140.00' },
        }
      }
      return {}
    })

    const wrapper = mount(ClosingWizard, {
      global: {
        stubs: {
          SoloDrawer: {
            props: ['show', 'title'],
            template: '<div v-if="show" class="stub-solo-drawer"><div class="drawer-title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
        },
      },
    })
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('.boss-ledger-card')
    expect(cards.length).toBe(3)

    // 第三张卡片为「公司欠老板/员工」，有 ¥140 垫付款，可点击
    const cardEmp = cards[2]!
    expect(cardEmp.text()).toContain('公司欠老板/员工')
    expect(cardEmp.text()).toContain('¥140.00')
    expect(cardEmp.classes()).toContain('clickable')

    // 点击卡片，展开抽屉
    await cardEmp.trigger('click')
    await flushPromises()

    expect(wrapper.find('.stub-solo-drawer').exists()).toBe(true)
    expect(wrapper.find('.drawer-title').text()).toContain('公司欠老板/员工垫付款明细')
    expect(wrapper.text()).toContain('快递费')
    expect(wrapper.text()).toContain('员工垫付 · 张三')

    // 抽屉底部提供直达「垫付未报销汇总」台账按钮
    const targetBtn = wrapper.findAll('button').find(b => b.text().includes('查看垫付未报销汇总'))!
    expect(targetBtn).toBeDefined()
    await targetBtn.trigger('click')
    await flushPromises()

    expect(harness.push).toHaveBeenCalledWith({
      path: '/reports/advance-balances',
      query: { month },
    })

    wrapper.unmount()
  })

  it('test_boss_ledger_card_excludes_reimbursed_advances_and_uses_2241_balance', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      {
        id: 'inv-zhipu',
        title: '发票-浙江智谱新篇科技有限公司',
        direction: 'expense',
        amount: 693.84,
        occurDate: `${month}-08`,
        category: '信息技术服务费',
        source: 'invoice_import',
        payer: 'boss_personal',
        billingStatus: 'reconciled',
        paymentDate: `${month}-26`,
        voucherStatus: 'posted',
      },
      {
        id: 'inv-baidu',
        title: '发票-北京百度网讯科技有限公司',
        direction: 'expense',
        amount: 1794.00,
        occurDate: `${month}-17`,
        category: '信息技术服务费',
        source: 'invoice_import',
        payer: 'boss_personal',
        billingStatus: 'reconciled',
        paymentDate: `${month}-26`,
        voucherStatus: 'posted',
      },
      {
        id: 'inv-deepseek',
        title: '发票-杭州深度求索人工智能基础技术研究有限公司',
        direction: 'expense',
        amount: 210.00,
        occurDate: `${month}-20`,
        category: '信息技术服务费',
        source: 'invoice_import',
        payer: 'boss_personal',
        billingStatus: 'has_invoice',
        voucherStatus: 'posted',
      },
      {
        id: 'reim-bs-1-inv-baidu',
        title: '报销垫付款-发票-北京百度网讯科技有限公司',
        direction: 'owner_transfer',
        amount: 1794.00,
        occurDate: `${month}-26`,
        category: '股东垫付报销',
        source: 'bank_import',
        payer: 'boss_personal',
        billingStatus: 'reconciled',
        paymentDate: `${month}-26`,
        voucherStatus: 'posted',
      },
    ])
    harness.store = {
      entries,
      activeEntries: entries,
      loadAll: vi.fn(async () => entries),
      fetchEntries: vi.fn(async () => entries),
      updateEntry: vi.fn(),
    }
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [], total: 0 }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      if (url === '/api/ledger/advance-balances') {
        return {
          month,
          items: [
            {
              payer: 'boss_personal',
              kind: 'boss',
              name: '老板（股东）',
              opening: '481.33',
              advance: '2697.84',
              reimbursed: '2969.17',
              closing: '210.00',
              count: 9,
              lastDate: `${month}-26`,
              entries: [
                {
                  id: 'v-deepseek',
                  sourceId: 'inv-deepseek',
                  date: `${month}-20`,
                  period: month,
                  summary: '发票-杭州深度求索人工智能基础技术研究有限公司',
                  category: '信息技术服务费',
                  amount: '210.00',
                  direction: 'advance',
                  isCurrentMonth: true,
                },
              ],
            },
          ],
          totals: {
            opening: '481.33',
            advance: '2697.84',
            reimbursed: '2969.17',
            closing: '210.00',
          },
        }
      }
      return {}
    })

    const wrapper = mount(ClosingWizard, {
      global: {
        stubs: {
          SoloDrawer: {
            props: ['show', 'title'],
            template: '<div v-if="show" class="stub-solo-drawer"><div class="drawer-title">{{ title }}</div><slot /><slot name="footer" /></div>',
          },
        },
      },
    })
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('.boss-ledger-card')
    const bossCard = cards[2]!
    expect(bossCard.text()).toContain('¥210.00')
    expect(bossCard.text()).not.toContain('¥2,697.84')
    expect(bossCard.text()).toContain('本月已报销 ¥2,969.17')

    // 已确认入账去向单据不含 reim-* 报销还款凭证自身
    const confirmedIds = ((wrapper.vm as any).confirmedExceptionEntries || []).map((e: any) => e.id)
    expect(confirmedIds).not.toContain('reim-bs-1-inv-baidu')

    // 抽屉明细只含未报销的 DeepSeek，不含已报销的智谱/百度云
    await bossCard.trigger('click')
    await flushPromises()
    const drawer = wrapper.find('.stub-solo-drawer')
    expect(drawer.exists()).toBe(true)
    expect(drawer.text()).toContain('杭州深度求索人工智能基础技术研究有限公司')
    expect(drawer.text()).not.toContain('浙江智谱新篇科技有限公司')
    expect(drawer.text()).not.toContain('北京百度网讯科技有限公司')

    wrapper.unmount()
  })

  it('批量过账确认清单正确识别股东投入为资金流入、去掉日期后缀并附带科目代码', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      {
        id: 'e-owner-in',
        title: '杨云飞 · 股东投入',
        direction: 'owner_transfer',
        amount: 50000,
        occurDate: `${month}-22`,
        category: '股东投入',
        accountCode: '3001',
        accountName: '实收资本',
        voucherStatus: 'draft',
      },
      {
        id: 'e-it-fee',
        title: `信息技术服务费-${month}-31`,
        direction: 'expense',
        amount: 500,
        occurDate: `${month}-31`,
        category: '信息技术服务费',
        voucherStatus: 'draft',
      },
    ])
    harness.store = reactive({
      activeEntries: entries,
      fetchEntries: vi.fn(async () => {}),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const list = (wrapper.vm as any).batchPostEntries
    expect(list).toHaveLength(2)
    expect(list[0]).toMatchObject({
      id: 'e-owner-in',
      isOutflow: false,
      displayTitle: '杨云飞 · 股东投入',
      subtitle: '股东投入 · 实收资本 3001',
      shortDate: `${month.slice(5, 7)}-22`,
    })
    expect(list[1]).toMatchObject({
      id: 'e-it-fee',
      isOutflow: true,
      displayTitle: '信息技术服务费',
      shortDate: `${month.slice(5, 7)}-31`,
    })
    expect(list[1].subtitle).toContain('5602015')

    wrapper.unmount()
  })
})

describe('closing wizard pending entry two-step flow', () => {
  function setupPendingEntry(overrides: Record<string, any> = {}) {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([{
      id: 'e-tencent',
      title: '腾讯云服务器',
      direction: 'expense',
      amount: 481.33,
      occurDate: `${month}-18`,
      category: '业务招待费',
      source: 'invoice_import',
      invoiceNumber: 'INV-TENCENT-1',
      billingStatus: '',
      voucherStatus: 'draft',
      ...overrides,
    }])
    harness.route.value.query = { step: '2' }
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoice/classify-entry/e-tencent') {
        return { category: '信息技术服务费', reason: '云服务器属于信息技术服务' }
      }
      if (url === '/api/record/entries/e-tencent/voucher-preview') {
        const category = payload?.category || '业务招待费'
        const isTech = category === '信息技术服务费'
        return {
          entryId: 'e-tencent', status: 'draft', direction: 'expense', category,
          payer: payload?.payer || 'company_account',
          paymentStatus: payload?.paymentStatus || '',
          amount: '481.33', source: 'rule', willReverse: false, warnings: [],
          debit: [{
            accountCode: isTech ? '5602015' : '5602002',
            accountName: isTech ? '管理费用-信息技术服务费' : '管理费用-业务招待费',
            amount: '481.33',
          }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '481.33', partnerName: '股东' }],
        }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })
    harness.patch.mockImplementation(async () => ({
      entry: { id: 'e-tencent', voucherLines: { debit: [], credit: [] } },
    }))
    return { month, entries }
  }

  it('第一步只选付款人：不发 PATCH，原地展开科目预览并采纳 AI 建议', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const card = wrapper.find('.pending-entry-card')
    expect(card.exists()).toBe(true)
    const bossBtn = card.findAll('button').find(button => button.text() === '老板垫付')!
    await bossBtn.trigger('click')
    await flushPromises()

    // 关键：第一步绝不落库，否则单据会立刻从待确认列表消失
    expect(harness.patch).not.toHaveBeenCalled()
    expect(card.find('.pending-entry-footer').exists()).toBe(false)
    expect(wrapper.find('.pending-entry-plan').exists()).toBe(true)
    expect(wrapper.find('.pending-entry-plan-lines .plan-line.is-compact').exists()).toBe(true)
    const planText = wrapper.find('.pending-entry-plan').text()
    expect(planText).toContain('信息技术服务费')
    expect(planText).toContain('5602015')
    expect(planText).toContain('2241')
    expect(planText).toContain('往来：股东')
    // AI 建议替换了进口时错分的「业务招待费」，并重新预览了科目
    const previewCalls = harness.post.mock.calls.filter(call => String(call[0]).endsWith('/voucher-preview'))
    expect(previewCalls.length).toBe(2)
    expect(previewCalls[0][1]).toMatchObject({ payer: 'boss_personal', category: '业务招待费' })
    expect(previewCalls[1][1]).toMatchObject({ payer: 'boss_personal', category: '信息技术服务费' })

    wrapper.unmount()
  })

  it('点「认可入账」才发 PATCH：付款人 + 确认后的分类 + 自动红冲重记', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    const accept = wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '认可入账')!
    await accept.trigger('click')
    await flushPromises()

    expect(harness.patch).toHaveBeenCalledTimes(1)
    const [url, payload] = harness.patch.mock.calls[0]
    expect(url).toBe('/api/record/entries/e-tencent')
    expect(payload).toMatchObject({
      payer: 'boss_personal',
      category: '信息技术服务费',
      autoReverseRepost: true,
    })

    wrapper.unmount()
  })

  it('「还没付款」只发 paymentStatus，不带 payer', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '还没付款')!.trigger('click')
    await flushPromises()
    await wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '认可入账')!.trigger('click')
    await flushPromises()

    const payload = harness.patch.mock.calls[0][1]
    expect(payload.paymentStatus).toBe('unpaid')
    expect(payload.payer).toBeUndefined()
    expect(payload.category).toBe('信息技术服务费')

    wrapper.unmount()
  })

  it('「更改付款人」把卡片退回第一步，账上没有任何变化', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '老板垫付')!.trigger('click')
    await flushPromises()
    await wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '更改付款人')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.pending-entry-plan').exists()).toBe(false)
    expect(wrapper.find('.pending-entry-card .pending-entry-footer').exists()).toBe(true)
    expect(harness.patch).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('「修改单据」打开修改弹窗并预置付款人', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '老板垫付')!.trigger('click')
    await flushPromises()
    await wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '修改单据')!.trigger('click')
    await flushPromises()

    // NModal 走 teleport，内容挂在 document.body 上
    expect(document.body.querySelector('.manual-dialog-title')?.textContent).toBe('修改单据')
    expect(document.body.textContent).toContain('老板个人垫付')
    expect(document.body.textContent).toContain('让 AI 根据说明重新建议')
    expect(document.body.textContent).toContain('5602015')

    wrapper.unmount()
  })

  it('已过账凭证在第二步提示将红冲重记', async () => {
    setupPendingEntry({ voucherStatus: 'posted' })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoice/classify-entry/e-tencent') {
        return { category: '信息技术服务费', reason: '云服务器' }
      }
      if (url === '/api/record/entries/e-tencent/voucher-preview') {
        return {
          entryId: 'e-tencent', status: 'posted', direction: 'expense', category: '信息技术服务费',
          payer: payload?.payer || '', paymentStatus: '', amount: '481.33',
          source: 'rule', willReverse: true, warnings: [],
          debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: '481.33' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '481.33', partnerName: '股东' }],
        }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.pending-entry-plan').text()).toContain('红冲原凭证')

    wrapper.unmount()
  })

  it('收款待确认「修改单据」使用「收款去向：」、plan-lines-stack 对齐分录并格式化两位小数金额', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    setupPendingEntry({
      id: 'e-alipay',
      title: '支付宝账单汇总-2026-11-19~2026-11-19',
      direction: 'income',
      amount: 5,
      occurDate: `${month}-19`,
      category: '主营业务收入',
      source: 'alipay_bill',
      invoiceNumber: '',
      voucherStatus: 'draft',
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/record/entries/e-alipay/voucher-preview') {
        return {
          entryId: 'e-alipay',
          status: 'draft',
          direction: 'income',
          category: payload?.category || '主营业务收入',
          payer: payload?.payer || 'platform_balance',
          paymentStatus: payload?.paymentStatus || '',
          amount: '5',
          source: 'rule',
          willReverse: false,
          warnings: [],
          debit: [{ accountCode: '1012099', accountName: '其他货币资金-其他平台待结算', amount: 5 }],
          credit: [
            { accountCode: '5001', accountName: '主营业务收入', amount: 4.95 },
            { accountCode: '2221026', accountName: '应交税费-应交增值税（小规模纳税人专用）', amount: 0.05 },
          ],
        }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '在平台未提现')!.trigger('click')
    await flushPromises()
    await wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '修改单据')!.trigger('click')
    await flushPromises()

    const bodyText = document.body.textContent || ''
    expect(bodyText).toContain('收款去向：在平台未提现（计入平台待提现余额）')
    expect(bodyText).not.toContain('付款人：在平台未提现')
    expect(bodyText).toContain('¥5.00')
    expect(bodyText).toContain('¥4.95')
    expect(bodyText).toContain('¥0.05')
    expect(document.body.querySelectorAll('.manual-form-item .pending-entry-plan-lines .plan-lines-stack .plan-line').length).toBe(3)

    wrapper.unmount()
  })
})

describe('closing wizard 研发项目费用归集快捷选项', () => {
  function setupRdPendingEntry(overrides: Record<string, any> = {}) {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    harness.route.value.query = { step: '2' }
    harness.store = reactive({
      activeEntries: [{
        id: 'e-rd',
        title: '发票-腾讯云计算（北京）有限责任公司',
        direction: 'expense',
        amount: 481.33,
        occurDate: `${month}-18`,
        category: '其他支出',
        source: 'invoice_import',
        invoiceNumber: 'INV-RD-1',
        billingStatus: '',
        voucherStatus: 'draft',
        ...overrides,
      }],
      fetchEntries: vi.fn(async () => {}),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoice/classify-entry/e-rd') {
        // AI 也判定为研发支出：第二步不应把用户已经看到的研发分类覆盖回普通科目
        return { category: '研发支出-费用化支出', reason: '云服务费属于研发直接投入' }
      }
      if (url === '/api/record/entries/e-rd/voucher-preview') {
        const category = payload?.category || '其他支出'
        const isRd = category === '研发支出-费用化支出'
        return {
          entryId: 'e-rd', status: 'draft', direction: 'expense', category,
          payer: payload?.payer || 'company_account',
          paymentStatus: payload?.paymentStatus || '',
          amount: '481.33', source: 'rule', willReverse: false, warnings: [],
          debit: [{
            accountCode: isRd ? '430100102' : '5602015',
            accountName: isRd ? '研发支出-费用化支出-直接投入' : '管理费用-信息技术服务费',
            amount: '481.33',
          }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '481.33', partnerName: '股东' }],
        }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })
    harness.patch.mockImplementation(async () => ({
      entry: { id: 'e-rd', voucherLines: { debit: [], credit: [] } },
    }))
    return { month }
  }

  it('分类为研发支出时常驻归集子表单，单项目账套自动预填项目', async () => {
    const { month } = setupRdPendingEntry({ category: '研发支出-费用化支出' })
    harness.rdProjects = [{
      id: 'p-1', code: 'RD-001', name: '智能记账引擎', status: 'active',
      startDate: `${month}-01`, endDate: '', initiationDocumentStatus: 'complete',
      createdAt: '', updatedAt: '',
    }]
    harness.rdExpenseTypes = [{ value: 'direct_input', label: '直接投入', accountCode: '430100102' }]

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.find('.pending-entry-card').findAll('button').find(b => b.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    const rdBox = wrapper.find('.pending-entry-rd')
    expect(rdBox.exists()).toBe(true)
    expect(rdBox.text()).toContain('研发项目归集')
    expect(rdBox.text()).toContain('按凭证金额 100% 直接归集')
    // 单项目账套默认带出项目，少一次点击
    expect(rdBox.text()).toContain('RD-001 · 智能记账引擎')

    // 明细未补齐时点「认可入账」只提示，不发 PATCH
    const accept = wrapper.find('.pending-entry-plan').findAll('button').find(b => b.text() === '认可入账')!
    await accept.trigger('click')
    await flushPromises()
    expect(harness.patch).not.toHaveBeenCalled()
    expect(harness.warning).toHaveBeenCalledWith('请先选择研发项目和费用类别')

    wrapper.unmount()
  })

  it('研发子表单填齐后「认可入账」带研发字段，落 4301 明细科目', async () => {
    const { month } = setupRdPendingEntry({ category: '研发支出-费用化支出' })
    harness.rdProjects = [{
      id: 'p-1', code: 'RD-001', name: '智能记账引擎', status: 'active',
      startDate: `${month}-01`, endDate: '', initiationDocumentStatus: 'complete',
      createdAt: '', updatedAt: '',
    }]
    harness.rdExpenseTypes = [{ value: 'direct_input', label: '直接投入', accountCode: '430100102' }]

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.find('.pending-entry-card').findAll('button').find(b => b.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    // 预填项目后仍需选费用类别：直接驱动子表单的更新事件（模拟用户在选择器里选中）
    const rdSelects = wrapper.find('.pending-entry-rd-fields').findAllComponents(NSelect)
    expect(rdSelects.length).toBe(2)
    rdSelects[1]!.vm.$emit('update:value', 'direct_input')
    await flushPromises()

    const accept = wrapper.find('.pending-entry-plan').findAll('button').find(b => b.text() === '认可入账')!
    await accept.trigger('click')
    await flushPromises()

    expect(harness.patch).toHaveBeenCalledTimes(1)
    const [url, payload] = harness.patch.mock.calls[0]
    expect(url).toBe('/api/record/entries/e-rd')
    expect(payload).toMatchObject({
      payer: 'boss_personal',
      category: '研发支出-费用化支出',
      rdProjectId: 'p-1',
      rdExpenseType: 'direct_input',
      rdAllocationMethod: 'direct',
      rdOriginalAmount: '481.33',
      autoReverseRepost: true,
    })

    wrapper.unmount()
  })

  it('账号内没有有效研发项目时不出现研发子表单，也没有研发分类', async () => {
    setupRdPendingEntry()
    harness.rdProjects = [{
      id: 'p-draft', code: 'RD-D', name: '草稿项目', status: 'draft',
      startDate: '2020-01-01', endDate: '', initiationDocumentStatus: 'missing',
      createdAt: '', updatedAt: '',
    }]
    harness.rdExpenseTypes = [{ value: 'direct_input', label: '直接投入', accountCode: '430100102' }]

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.find('.pending-entry-card').findAll('button').find(b => b.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.pending-entry-rd').exists()).toBe(false)
    expect(wrapper.find('.pending-entry-plan-category').text()).not.toContain('研发支出-费用化支出')
    expect(wrapper.find('.pending-entry-plan-controls').text()).not.toContain('研发支出-费用化支出')

    wrapper.unmount()
  })

  it('已过账凭证不开放内联研发归集（后端只允许冲销重记）', async () => {
    const { month } = setupRdPendingEntry({ voucherStatus: 'posted', status: 'posted' })
    harness.rdProjects = [{
      id: 'p-1', code: 'RD-001', name: '智能记账引擎', status: 'active',
      startDate: `${month}-01`, endDate: '', initiationDocumentStatus: 'complete',
      createdAt: '', updatedAt: '',
    }]
    harness.rdExpenseTypes = [{ value: 'direct_input', label: '直接投入', accountCode: '430100102' }]

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.find('.pending-entry-card').findAll('button').find(b => b.text() === '老板垫付')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.pending-entry-rd').exists()).toBe(false)

    wrapper.unmount()
  })
})

describe('closing wizard post-closing todo card', () => {
  function setupClosedMonth(options: { todos: any[] }) {
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url.includes('/close-checks')) return { periodStatus: 'closed', canClose: true, checks: [] }
      if (url === '/api/todos') return { todos: options.todos, autoResolved: [], autoResolvedCount: 0 }
      return { items: [], records: [], summary: {} }
    })
  }

  it('renders the pending todo summary on the closed-period ceremony view and routes each row', async () => {
    setupClosedMonth({
      todos: [
        { id: 'f-1', key: 'f-1', type: 'filing', category: 'filing', title: '征期临近 - 增值税（2026-10）', desc: '截止 2026-11-15（剩 10 天）', priority: 'medium', done: false, dismissed: false, meta: { ruleId: 'filing_deadline' } },
        { id: 't-1', key: 't-1', type: 'task', title: '办公用品采购 - 补充票据', desc: '缺少对应发票', priority: 'medium', done: false, dismissed: false, meta: { entryId: 'e-9', ruleId: 'missing_document' } },
        { id: 't-2', key: 't-2', type: 'task', title: '已办结事项', priority: 'medium', done: true, dismissed: false, meta: {} },
      ],
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('结账成功')
    expect(wrapper.text()).toContain('结账后待办')
    expect(wrapper.text()).toContain('2 件待处理')
    expect(wrapper.text()).toContain('征期临近 - 增值税（2026-10）')
    // 已办结条目不进卡片
    expect(wrapper.text()).not.toContain('已办结事项')

    const rows = wrapper.findAll('.closing-todo-row')
    expect(rows).toHaveLength(2)
    await rows[0].trigger('click')
    expect(harness.push).toHaveBeenCalledWith('/tax-filing?period=2026-Q4')
    await rows[1].trigger('click')
    expect(harness.push).toHaveBeenCalledWith('/ledger/e-9')

    const viewAll = wrapper.findAll('button').find(b => b.text().includes('查看全部'))!
    await viewAll.trigger('click')
    expect(harness.push).toHaveBeenCalledWith('/todos')

    wrapper.unmount()
  })

  it('hides the todo card entirely when nothing is pending', async () => {
    setupClosedMonth({ todos: [{ id: 't-1', key: 't-1', type: 'task', title: '全部办结项', priority: 'medium', done: true, dismissed: false, meta: {} }] })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('结账成功')
    expect(wrapper.find('.closing-todo-card').exists()).toBe(false)

    wrapper.unmount()
  })
})

describe('closing wizard default month', () => {
  it('启用月就是本月的公司默认落在启用月，不选启用前的上月', async () => {
    const now = new Date()
    const currentKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    const previousKey = (() => {
      const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      return `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, '0')}`
    })()
    harness.auth = {
      currentCompanyId: 'c_new_company',
      companies: [{ company_id: 'c_new_company', name: '本月新公司', startDate: `${currentKey}-01` }],
    }
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/periods/available') return { months: [] }
      if (url === '/api/bank/statements') return { items: [] }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const monthSelect = wrapper.findAllComponents(NSelect)[0]!
    expect(monthSelect.props('value')).toBe(currentKey)
    const optionValues = (monthSelect.props('options') as { value: string }[]).map(option => option.value)
    expect(optionValues).toContain(currentKey)
    expect(optionValues).not.toContain(previousKey)

    wrapper.unmount()
  })
})

describe('closing wizard company region entry', () => {
  it('links straight to the company province e-tax portal when 注册地 is set', async () => {
    harness.auth = {
      currentCompanyId: 'c_zj',
      companies: [{ company_id: 'c_zj', name: '杭州测试主体', province: '浙江省', city: '' }],
    }
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async () => ({ suggestions: [] }))

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    // 注册地已填：不再让用户去省份地图里再选一次
    const entry = wrapper.findAll('a.guide-entry-link')
      .find(link => link.text().includes('去电子税务局'))
    expect(entry, '发票卡片缺电子税务局入口').toBeTruthy()
    expect(entry!.attributes('href')).toBe('https://etax.zhejiang.chinatax.gov.cn')

    wrapper.unmount()
  })
})

describe('closing wizard platform bill multi-platform import', () => {
  it('keeps the upload area reachable after an import so the other platform can be imported too', async () => {
    // 回归：平台账单卡片曾是单文件状态机——导入微信后上传区被 v-show 收起，
    // 只剩「重新导入」，同月想再导支付宝账单无路可走（用户 2026-11 反馈）。
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/platform-bill/parse') {
        const second = harness.post.mock.calls.filter(([u]) => u === '/api/platform-bill/parse').length > 1
        return second
          ? {
            platform: 'alipay',
            valid: [{ date: `${month}-12`, product_name: '支付宝订单', status: 'SUCCESS', order_amount: 88, fee: 0.53, refund_amount: 0, settled_amount: 87.47 }],
            errors: [],
            summary: { 总交易单数: 1, 订单总金额: 88, 退款总金额: 0, 手续费总金额: 0.53, periodStart: `${month}-12`, periodEnd: `${month}-12` },
            total: 1,
          }
          : {
            platform: 'wechat',
            valid: [{ date: `${month}-10`, product_name: '微信订单', status: 'SUCCESS', order_amount: 100, fee: 0.6, refund_amount: 0, settled_amount: 99.4 }],
            errors: [],
            summary: { 总交易单数: 1, 订单总金额: 100, 退款总金额: 0, 手续费总金额: 0.6, periodStart: `${month}-10`, periodEnd: `${month}-10` },
            total: 1,
          }
      }
      if (url === '/api/platform-bill/import') {
        return { imported: 1, failed: 0, ignored_duplicates: 0, results: [{ status: 'success', title: '账单汇总' }] }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const platformDropzone = () => wrapper.findAllComponents(FileDropzone)
      .find(item => String(item.props('title') || '').includes('微信/支付宝账单'))!
    const dropzoneVisible = () => platformDropzone().element.style.display !== 'none'
    expect(dropzoneVisible()).toBe(true)
    // 回归：两个平台的导出指引必须在上传前就能看到（以前按已解析平台只显示一个，
    // 没导过的人等于没有说明书）
    expect(wrapper.text()).toContain('微信账单怎么导出？')
    expect(wrapper.text()).toContain('支付宝账单怎么导出？')
    // 发票卡片同样要在上传前给出「从哪导出」的入口，并可直接跳到税局
    expect(wrapper.text()).toContain('发票在哪下载？')
    const taxEntry = wrapper.findAll('a.guide-entry-link')
      .find(link => link.text().includes('去电子税务局'))
    expect(taxEntry, '发票卡片缺电子税务局直达入口').toBeTruthy()
    expect(taxEntry!.attributes('href')).toBe('https://12366.chinatax.gov.cn/bsfw/onlinetaxation/main')

    // 第一份：微信账单 → 预览态收起上传区
    platformDropzone().vm.$emit('file', new File(['wechat'], 'wechat.csv', { type: 'text/csv' }))
    await flushPromises()
    expect(wrapper.text()).toContain('微信订单')
    expect(dropzoneVisible()).toBe(false)

    await wrapper.findAll('button').find(button => button.text().includes('生成账单汇总凭证'))!.trigger('click')
    await flushPromises()
    expect(harness.success).toHaveBeenCalled()
    expect(wrapper.text()).toContain('微信支付账单已导入')
    // 关键断言：导入成功后上方上传区保持露出（避免再多出一份「继续导入其他账单」重复按钮）
    expect(dropzoneVisible()).toBe(true)
    expect(wrapper.text()).not.toContain('继续导入其他账单')

    // 第二份：支付宝账单直接拖入 → 退出上一份的「已导入」态并进入新预览
    platformDropzone().vm.$emit('file', new File(['alipay'], 'alipay.csv', { type: 'text/csv' }))
    await flushPromises()
    expect(wrapper.text()).toContain('支付宝订单')
    expect(wrapper.text()).not.toContain('微信支付账单已导入')
    expect(wrapper.text()).toContain('支付宝')

    wrapper.unmount()
  })
})

describe('closing wizard prior paid invoice link', () => {
  function setupPriorPaidEntry(candidates: any[], statements: any[] = []) {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([{
      id: 'e-deepseek',
      title: '发票-杭州深度求索人工智能基础技术研究有限公司',
      direction: 'expense',
      amount: 500,
      occurDate: `${month}-25`,
      category: '其他支出',
      source: 'invoice_import',
      invoiceNumber: '26332000004655508586',
      billingStatus: '',
      voucherStatus: 'draft',
    }])
    harness.route.value.query = { step: '2' }
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') {
        expect(payload.entryIds).toEqual(['e-deepseek'])
        return { candidates: { 'e-deepseek': candidates } }
      }
      if (url === '/api/invoices/prior-payment/link') {
        expect(payload).toMatchObject({ invoiceEntryId: 'e-deepseek', priorEntryId: 'prior-pay-1' })
        entries.splice(0, entries.length) // 后端已删除本月草稿
        return { invoiceIds: ['inv-1'], priorEntry: { id: 'prior-pay-1', summary: '信息技术服务费', occurredOn: '2026-05-31', status: 'posted' } }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })
    return { month, entries }
  }

  const counterpartyCandidate = {
    entryId: 'prior-pay-1',
    summary: '信息技术服务费-2026-05-31',
    occurredOn: '2026-05-31',
    status: 'posted',
    amount: '500.00',
    statementDescription: '汇兑 | 附言: 往来款 (对方: 杭州深度求索人工智能基础技术研究有限公司)',
    confidence: 'counterparty',
  }

  it('检测到前期已入账付款：卡片直接给挂接建议，确认后不再重复入账', async () => {
    setupPriorPaidEntry([counterpartyCandidate])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const suggestion = wrapper.find('.prior-paid-suggestion')
    expect(suggestion.exists()).toBe(true)
    expect(suggestion.text()).toContain('检测到该款项已于 2026-05-31')
    expect(suggestion.text()).toContain('信息技术服务费-2026-05-31')

    await suggestion.find('button')!.trigger('click')
    await flushPromises()
    // 确认弹窗说明后果， positive 才真正落库
    const dialogOptions = harness.dialog.warning.mock.calls
      .map((call) => call[0] as any)
      .find((opts) => opts?.title === '挂接到前期已入账凭证？')
    expect(dialogOptions).toBeTruthy()
    expect(dialogOptions.content).toContain('不再生成新费用凭证')
    dialogOptions.onPositiveClick()
    await flushPromises()

    expect(harness.post.mock.calls.some(([url]) => url === '/api/invoices/prior-payment/link')).toBe(true)
    expect(harness.success).toHaveBeenCalledWith(expect.stringContaining('已挂接'))
    expect(wrapper.find('.pending-entry-card').exists()).toBe(false)

    wrapper.unmount()
  })

  it('仅弱匹配候选：不自动建议，走「前期已付款」展开列表手动挂接', async () => {
    setupPriorPaidEntry([{ ...counterpartyCandidate, confidence: 'amount_only', statementDescription: '' }])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    expect(wrapper.find('.prior-paid-suggestion').exists()).toBe(false)
    const chip = wrapper.find('.pending-entry-card').findAll('button').find(b => b.text().includes('前期已付款'))!
    await chip.trigger('click')
    await flushPromises()

    const panel = wrapper.find('.prior-paid-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('信息技术服务费-2026-05-31')

    await panel.findAll('button').find(b => b.text() === '挂接')!.trigger('click')
    await flushPromises()
    const dialogOptions = harness.dialog.warning.mock.calls
      .map((call) => call[0] as any)
      .find((opts) => opts?.title === '挂接到前期已入账凭证？')
    dialogOptions.onPositiveClick()
    await flushPromises()

    expect(harness.post.mock.calls.some(([url]) => url === '/api/invoices/prior-payment/link')).toBe(true)
    expect(wrapper.find('.pending-entry-card').exists()).toBe(false)

    wrapper.unmount()
  })

  it('没有等额前期付款：展开后明确提示先查前期流水', async () => {
    setupPriorPaidEntry([])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    expect(wrapper.find('.prior-paid-suggestion').exists()).toBe(false)
    const chip = wrapper.find('.pending-entry-card').findAll('button').find(b => b.text().includes('前期已付款'))!
    await chip.trigger('click')
    await flushPromises()

    expect(wrapper.find('.prior-paid-panel').text()).toContain('未找到金额一致的前期已入账款项')
    expect(harness.post.mock.calls.some(([url]) => url === '/api/invoices/prior-payment/link')).toBe(false)

    wrapper.unmount()
  })

  it('当月已有疑似流水：显示关联疑似流水，不再叠加前期挂接建议条', async () => {
    // 回归 2026-10：王翼发票既有当月疑似流水、又收到无关的前期报销凭证建议，
    // 两条提示打架且前期那条是「报销」两字交集造成的误报。
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statement = {
      id: 'stmt-deepseek',
      amount: -500,
      description: '汇兑 | 附言: deepseek调用服务费 (对方: 杭州深度求索人工智能基础技术研究有限公司)',
      transaction_date: `${month}-31`,
      matched_entry_id: '',
      matched_entry_ids: [],
    }
    setupPriorPaidEntry([{
      entryId: 'prior-baoxiao-626',
      summary: '报销垫付款-发票-深圳市腾讯计算机系统有限公司',
      occurredOn: '2026-06-26',
      status: 'posted',
      amount: '500.00',
      statementDescription: '汇兑 | 附言: 报销微信开放平台认证费用 (对方: 杨云飞)',
      confidence: 'counterparty',
    }], [statement])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    expect(wrapper.find('.prior-paid-suggestion').exists()).toBe(false)
    const matchBanner = wrapper.find('.pending-entry-card .statement-match-suggestion')
    expect(matchBanner.exists()).toBe(true)
    expect(matchBanner.find('button').text()).toContain('关联疑似流水')
    const card = wrapper.find('.pending-entry-card')
    const actions = card.findAll('button').map(b => b.text())
    expect(actions.some(text => text.includes('关联疑似流水'))).toBe(true)
    expect(actions.some(text => text.includes('前期已付款'))).toBe(true)
    expect(card.text()).toContain('疑似对应')
    expect(card.find('.pending-entry-footer').text()).toContain('不是这笔？选其他去向：')

    wrapper.unmount()
  })

  it('点击「关联疑似流水」直接提交 match 配对并刷新', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statement = {
      id: 'stmt-deepseek',
      amount: -500,
      description: '汇兑 | 附言: deepseek调用服务费 (对方: 杭州深度求索人工智能基础技术研究有限公司)',
      transaction_date: `${month}-31`,
      matched_entry_id: '',
      matched_entry_ids: [],
    }
    setupPriorPaidEntry([], [statement])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const matchBtn = wrapper.find('.pending-entry-card .statement-match-suggestion button')
    expect(matchBtn.text()).toContain('关联疑似流水')
    await matchBtn.trigger('click')
    await flushPromises()

    const confirmCall = harness.post.mock.calls.find(([url]) => url === '/api/bank/confirm-suggestion')
    expect(confirmCall?.[1]).toMatchObject({
      statement_id: 'stmt-deepseek',
      type: 'match',
      matched_entry_ids: ['e-deepseek'],
    })
    expect(harness.success).toHaveBeenCalledWith(expect.stringContaining('已关联发票并完成核对'))
    expect(wrapper.find('.recent-confirm-undo-bar').text()).toContain('关联流水')

    wrapper.unmount()
  })

  it('金额有差额的流水不再暗示疑似对应：¥210 发票不认领 ¥200 流水', async () => {
    // 回归 2026-10：±10% 容差让 ¥210 与 ¥200 两张同销方发票同时暗示同一张
    // ¥200 流水（差 ¥10.00）。账目侧提示只推金额一致，差额配对走流水侧低置信建议。
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const seller = '杭州深度求索人工智能基础技术研究有限公司'
    const entries = reactive<any[]>([
      { id: 'inv-210', title: `发票-${seller}`, direction: 'expense', amount: 210, occurDate: `${month}-20`, source: 'invoice_import', invoiceNumber: 'NUM-210', billingStatus: '', voucherStatus: 'draft' },
      { id: 'inv-200', title: `发票-${seller}`, direction: 'expense', amount: 200, occurDate: `${month}-20`, source: 'invoice_import', invoiceNumber: 'NUM-200', billingStatus: '', voucherStatus: 'draft' },
    ])
    const statement = {
      id: 'stmt-deepseek',
      amount: -200,
      description: `汇兑 | 附言: deepseek调用服务费 (对方: ${seller})`,
      transaction_date: `${month}-20`,
      matched_entry_id: '',
      matched_entry_ids: [],
    }
    harness.route.value.query = { step: '2' }
    harness.store = reactive({ activeEntries: entries, fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      return {}
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const cards = wrapper.findAll('.pending-entry-card')
    const card210 = cards.find(c => c.text().includes('-¥210.00'))!
    const card200 = cards.find(c => c.text().includes('-¥200.00'))!
    expect(card210.exists()).toBe(true)
    expect(card200.exists()).toBe(true)

    // ¥210：金额不一致 → 无「疑似对应」暗示，也不给「关联疑似流水」按钮
    expect(card210.text()).not.toContain('疑似对应')
    expect(card210.findAll('button').some(b => b.text().includes('关联疑似流水'))).toBe(false)
    // ¥200：金额一致 + 对手方一致 → 正常提示
    expect(card200.text()).toContain('疑似对应')
    expect(card200.text()).toContain('金额一致')
    expect(card200.findAll('button').some(b => b.text().includes('关联疑似流水'))).toBe(true)

    wrapper.unmount()
  })

  it('流水对手方与发票销方无关：不显示「吻合发票」徽标（社保金库 ≠ 科技公司发票）', async () => {
    // 回归 2026-10：社保金库 -200 被金额巧合标成「吻合发票：发票-杭州深度求索」，
    // 「吻合」必须同时有销方↔对手方亲和，只有金额接近不算
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statement = {
      id: 'stmt-shebao',
      amount: -500,
      description: '税费社保 (对方: 金库杭州市余杭区支库)',
      transaction_date: `${month}-15`,
      matched_entry_id: '',
      matched_entry_ids: [],
    }
    setupPriorPaidEntry([], [statement])
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    expect(wrapper.text()).not.toContain('吻合发票')

    wrapper.unmount()
  })

  it('手动分类弹窗「根据说明更新 AI 建议」可将税费社保重判为罚款支出二级科目（5711011）', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statement = {
      id: 'stmt-tax-penalty',
      amount: -200,
      description: '税费社保 (对方: 金库杭州市余杭区支库)',
      transaction_date: `${month}-15`,
      matched_entry_id: '',
      matched_entry_ids: [],
    }
    setupPriorPaidEntry([], [statement])
    const basePostImpl = harness.post.getMockImplementation()!
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/bank/classify-statement') {
        expect(payload).toEqual({
          statement_id: 'stmt-tax-penalty',
          business_context: '这笔是未及时申报个税被税务局罚了200元',
        })
        return {
          suggestedEntry: {
            direction: 'expense',
            category: '罚款支出',
            accountCode: '5711011',
            accountName: '营业外支出-罚款支出',
            title: '未及时申报个税罚款',
            confidence: 0.96,
            reason: '未及时申报个税被税务局罚款属于税务行政处罚，应计入营业外支出-罚款支出',
          },
        }
      }
      return basePostImpl(url, payload)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const vm = wrapper.vm as any
    const values = new Set(vm.CATEGORY_OPTIONS.map((opt: any) => opt.value))
    expect(values.has('expense|罚款支出|5711011|营业外支出-罚款支出')).toBe(true)
    expect(values.has('expense|税收滞纳金|5711010|营业外支出-税收滞纳金')).toBe(true)
    expect(values.has('income|政府补助|5301002|营业外收入-政府补助')).toBe(true)
    expect(values.has('expense|个税代缴|2221012|应交税费-应交个人所得税')).toBe(true)
    expect(values.has('expense|广告费|5601015|销售费用-广告费')).toBe(true)
    expect(values.has('expense|利息费用|5603001|财务费用-利息费用')).toBe(true)

    vm.openManualClassify(statement, {
      direction: 'expense',
      category: '社保公积金',
      accountCode: '221102',
      accountName: '应付职工薪酬-单位社保',
    })
    await flushPromises()

    vm.manualForm.businessContext = '这笔是未及时申报个税被税务局罚了200元'
    await vm.refreshManualAiSuggestion()
    await flushPromises()

    expect(vm.manualForm.categoryValue).toBe('expense|罚款支出|5711011|营业外支出-罚款支出')
    expect(vm.manualForm.title).toBe('未及时申报个税罚款')
    expect(vm.manualSuggestionMeta.reason).toContain('罚款支出')
    expect(document.body.textContent).toContain('5711011 营业外支出-罚款支出')

    wrapper.unmount()
  })
})

describe('closing wizard opening payroll settlement', () => {
  it('开账首月发放上月工资：不自动勾选批量确认，按钮显示「冲销期初应付（221101）」并提交 settleOpeningPayroll', async () => {
    const month = '2026-10'
    harness.auth = {
      currentCompanyId: 'c_test_company',
      companies: [{ company_id: 'c_test_company', startDate: '2026-10-01' }],
    }
    harness.route.value.query = { month, step: '2' }
    const statements = reactive<any[]>([
      {
        id: 'stmt-opening-payroll',
        amount: -9250,
        description: '付款 · APP移动代发 · 26年9月工资',
        transaction_date: '2026-10-12',
        matched_entry_id: '',
        matched_entry_ids: [],
      },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => ({
            type: 'new',
            statementId: s.id,
            confidence: 0.96,
            reason: '建账前应付工资期初余额充足，可直接冲销期初应付职工薪酬-工资（221101）',
            suggestedEntry: {
              title: '26年9月工资',
              category: '工资发放',
              accountCode: '221101',
              accountName: '应付职工薪酬-工资',
              requiresSalarySettlement: true,
            },
            payrollSettlement: {
              mode: 'opening_balance',
              kind: 'salary',
              bookStartDate: '2026-10-01',
              accountCode: '221101',
              accountName: '应付职工薪酬-工资',
              availableOpeningCredit: '9250.00',
              requiredAmount: '9250.00',
            },
            statement: { date: s.transaction_date, description: s.description, amount: s.amount },
          })),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        statements[0].matched_entry_id = 'bank-s-opening'
        return { status: 'created', entryId: 'bank-s-opening' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-opening-payroll"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('冲销期初应付（221101）')
    expect(row.text()).not.toContain('认可入账')
    expect((wrapper.vm as any).selectedStatementIds).toEqual([])
    expect((wrapper.vm as any).isSelectableForBatch(statements[0])).toBe(false)
    const checkbox = row.findComponent(NCheckbox)
    expect(checkbox.exists()).toBe(true)
    expect(checkbox.props('disabled')).toBe(true)

    const settleBtn = row.findAll('button').find(btn => btn.text().includes('冲销期初应付（221101）'))!
    await settleBtn.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(1)
    expect(confirmPayloads[0]).toMatchObject({
      statement_id: 'stmt-opening-payroll',
      type: 'new',
      settleOpeningPayroll: true,
    })
    expect(harness.success).toHaveBeenCalledWith(expect.stringContaining('221101 应付职工薪酬-工资'))

    wrapper.unmount()
  })

  it('开账首月发放上月工资但期初 221101 余额不足：展示警告提示并引导前往「/mine/opening」补录', async () => {
    const month = '2026-10'
    harness.auth = {
      currentCompanyId: 'c_test_company',
      companies: [{ company_id: 'c_test_company', startDate: '2026-10-01' }],
    }
    harness.route.value.query = { month, step: '2' }
    const statements = reactive<any[]>([
      {
        id: 'stmt-opening-missing',
        amount: -9250,
        description: '付款 · APP移动代发 · 26年9月工资',
        transaction_date: '2026-10-12',
        matched_entry_id: '',
        matched_entry_ids: [],
      },
    ])
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'new',
            statementId: 'stmt-opening-missing',
            confidence: 0.96,
            reason: '建账日前（2026-10-01）上月工资需先在期初设置录入 221101 贷方余额',
            suggestedEntry: {
              title: '26年9月工资',
              category: '工资发放',
              accountCode: '221101',
              accountName: '应付职工薪酬-工资',
              requiresSalarySettlement: true,
            },
            payrollSettlement: {
              mode: 'opening_balance_missing',
              kind: 'salary',
              bookStartDate: '2026-10-01',
              accountCode: '221101',
              accountName: '应付职工薪酬-工资',
              availableOpeningCredit: '0.00',
              requiredAmount: '9250.00',
            },
            statement: { date: '2026-10-12', description: statements[0].description, amount: -9250 },
          }],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-opening-missing"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('期初 221101 余额不足')
    expect(row.text()).toContain('去期初设置补录')
    expect(row.text()).not.toContain('认可入账')

    const fixBtn = row.findAll('button').find(btn => btn.text().includes('去期初设置补录'))!
    await fixBtn.trigger('click')
    await flushPromises()

    expect(harness.push).toHaveBeenCalledWith('/mine/opening')

    wrapper.unmount()
  })

  it('开账首月合并冲销多期初子科目（情况 A 社保+工资留存）：按钮展示「冲销期初应付（221102+221101）」', async () => {
    const month = '2026-10'
    harness.auth = {
      currentCompanyId: 'c_test_company',
      companies: [{ company_id: 'c_test_company', startDate: '2026-10-01' }],
    }
    harness.route.value.query = { month, step: '2' }
    const statements = reactive<any[]>([
      {
        id: 'stmt-opening-multi',
        amount: -4250,
        description: '付款 · 社会保险费扣款（9月社保）',
        transaction_date: '2026-10-15',
        matched_entry_id: '',
        matched_entry_ids: [],
      },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => ({
            type: 'new',
            statementId: s.id,
            confidence: 0.96,
            reason: '建账启用日（2026-10-01）前遗留应付，将合并冲销期初贷方余额：「221102 应付职工薪酬-单位社保」¥3000.00 + 「221101 应付职工薪酬-工资」¥1250.00，不重复计入本月费用',
            suggestedEntry: {
              title: '社会保险费扣款（9月社保）',
              category: '社保公积金',
              accountCode: '221102',
              accountName: '应付职工薪酬-单位社保',
              requiresSalarySettlement: true,
            },
            payrollSettlement: {
              mode: 'opening_balance',
              kind: 'liability',
              bookStartDate: '2026-10-01',
              accountCode: '221102',
              accountName: '应付职工薪酬-单位社保',
              availableOpeningCredit: '5250.00',
              remainingOpeningCredit: '1000.00',
              requiredAmount: '4250.00',
              allocations: [
                { accountCode: '221102', accountName: '应付职工薪酬-单位社保', amount: '3000.00' },
                { accountCode: '221101', accountName: '应付职工薪酬-工资', amount: '1250.00' },
              ],
            },
            statement: { date: s.transaction_date, description: s.description, amount: s.amount },
          })),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        statements[0].matched_entry_id = 'bank-stmt-opening-multi'
        return { status: 'created', entryId: 'bank-stmt-opening-multi', settledOpeningPayroll: true }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-opening-multi"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('冲销期初应付（221102+221101）')

    const settleBtn = row.findAll('button').find(btn => btn.text().includes('冲销期初应付（221102+221101）'))!
    await settleBtn.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(1)
    expect(confirmPayloads[0]).toMatchObject({
      statement_id: 'stmt-opening-multi',
      type: 'new',
      settleOpeningPayroll: true,
    })
    expect(harness.success).toHaveBeenCalledWith(expect.stringContaining('221102 应付职工薪酬-单位社保 ¥3000.00 + 221101 应付职工薪酬-工资 ¥1250.00'))

    wrapper.unmount()
  })
})

describe('closing wizard matched draft editing', () => {
  it('未过账的已配对草稿可在卡片上改付款来源，保存后按垫付报销口径重算', async () => {
    // 用户看到「确认配对」但付款来源不对（发票草稿挂了 2241 应付供应商，
    // 实际是杨云飞垫付）时，应能就地改，而不是只能确认或去别的页面改。
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const draft = reactive<any>({
      id: 'e-tencent',
      title: '报销微信开放平台认证费用',
      direction: 'expense',
      amount: 300,
      occurDate: `${month}-26`,
      category: '信息技术服务费',
      source: 'invoice_import',
      invoiceNumber: '26442000000000001234',
      billingStatus: '',
      payer: '',
      paymentStatus: '',
      voucherStatus: 'draft',
      voucherLines: {
        debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 300 }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 300 }],
      },
    })
    const statement = reactive({
      id: 'stmt-tencent',
      amount: -300,
      description: '转给杨云飞-报销微信开放平台认证费用',
      transaction_date: `${month}-26`,
      matched_entry_id: '',
    })
    harness.store = reactive({ activeEntries: [draft], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-tencent',
            matchedEntryId: 'e-tencent',
            matchedEntryIds: ['e-tencent'],
            matchedEntry: {
              id: 'e-tencent',
              title: '报销微信开放平台认证费用',
              amount: 300,
              direction: 'expense',
              category: '信息技术服务费',
              source: 'invoice_import',
            },
            confidence: 0.9,
            statement: { date: `${month}-26`, description: '转给杨云飞-报销微信开放平台认证费用', amount: -300 },
          }],
        }
      }
      if (url === '/api/record/entries/e-tencent/voucher-preview') {
        const partnerName = payload?.payer === 'boss_personal' ? '股东' : ''
        return {
          entryId: 'e-tencent',
          status: 'draft',
          direction: 'expense',
          category: '信息技术服务费',
          payer: payload?.payer || '',
          paymentStatus: payload?.paymentStatus || '',
          amount: '300.00',
          source: 'rule',
          willReverse: false,
          warnings: [],
          debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: '300.00' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '300.00', partnerName }],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })
    harness.patch.mockImplementation(async () => ({
      entry: { ...draft, payer: 'boss_personal' },
    }))

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const editBtn = wrapper.findAll('button').find(btn => btn.text() === '修改单据')
    expect(editBtn).toBeTruthy()
    await editBtn!.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.manual-dialog-title')?.textContent).toBe('修改单据')

    // 打开时按现有 2241 挂账反推为「还没付款」，不会把贷方误预览成 1002
    const previewCalls = () => harness.post.mock.calls
      .filter(([url]) => url === '/api/record/entries/e-tencent/voucher-preview')
      .map(([, payload]) => payload)
    expect(previewCalls().at(-1)?.paymentStatus).toBe('unpaid')

    // 改成老板垫付：预览立刻按 2241/股东往来重算
    const bossChip = Array.from(document.querySelectorAll<HTMLButtonElement>('.edit-voucher-payer-chips button'))
      .find(btn => btn.textContent?.includes('老板垫付'))!
    bossChip.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(previewCalls().at(-1)?.payer).toBe('boss_personal')
    expect(document.body.textContent).toContain('老板个人垫付')

    const saveBtn = Array.from(document.querySelectorAll<HTMLButtonElement>('.manual-dialog-actions button'))
      .find(btn => btn.textContent?.includes('保存修改'))!
    saveBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(harness.patch).toHaveBeenCalled()
    const [url, payload] = harness.patch.mock.calls.at(-1)!
    expect(url).toBe('/api/record/entries/e-tencent')
    expect(payload).toMatchObject({
      title: '报销微信开放平台认证费用',
      category: '信息技术服务费',
      payer: 'boss_personal',
    })
    expect(payload.paymentStatus).toBeUndefined()
    expect(payload.autoReverseRepost).toBeUndefined()

    // 未关账期内即使单据已过账，也保留「修改单据」（保存时红冲重记）；仅已冲销凭证不再允许修改
    draft.voucherStatus = 'posted'
    await flushPromises()
    expect(wrapper.findAll('button').some(btn => btn.text() === '修改单据')).toBe(true)

    draft.voucherStatus = 'reversed'
    await flushPromises()
    expect(wrapper.findAll('button').some(btn => btn.text() === '修改单据')).toBe(false)

    wrapper.unmount()
  })

  it('guides two-step personal reimbursement when invoice draft matches a personal reimbursement statement', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const draft = reactive({
      id: 'e-tencent-reim',
      title: '发票-深圳市腾讯计算机系统有限公司',
      amount: 300,
      direction: 'expense',
      category: '信息技术服务费',
      occurDate: `${month}-02`,
      source: 'invoice_import',
      invoiceNumber: '26957000000148730058',
      billingStatus: 'has_invoice',
      payer: 'company_account',
      voucherStatus: 'draft',
      voucherLines: {
        debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 300 }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 300 }],
      },
    })
    const statement = reactive({
      id: 'stmt-tencent-reim',
      amount: -300,
      description: '汇兑 | 附言: 报销微信开放平台认证费用 (对方: 杨云飞)',
      transaction_date: `${month}-26`,
      matched_entry_id: '',
    })
    harness.store = reactive({ activeEntries: [draft], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-tencent-reim',
            matchedEntryId: 'e-tencent-reim',
            matchedEntryIds: ['e-tencent-reim'],
            matchedEntry: {
              id: 'e-tencent-reim',
              title: '发票-深圳市腾讯计算机系统有限公司',
              amount: 300,
              direction: 'expense',
              category: '信息技术服务费',
              source: 'invoice_import',
              payer: 'company_account',
            },
            confidence: 0.6,
            counterpartyMismatch: true,
            requiresReview: true,
            invoiceReimbursement: {
              counterparty: '杨云飞',
              sellerName: '深圳市腾讯计算机系统有限公司',
              suggestedPayer: 'boss_personal',
              payerLabel: '老板垫付',
              entryId: 'e-tencent-reim',
            },
            statement: { date: `${month}-26`, description: statement.description, amount: -300 },
          }],
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        return { status: 'matched', entryId: 'reim-stmt-tencent-reim-e-tencent-reim' }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('需要你拍板')
    expect(wrapper.text()).toContain('付款对象是 杨云飞，发票销方是 深圳市腾讯计算机系统有限公司')
    expect(wrapper.text()).toContain('请先在下方「待确认单据」确认垫付人与用途分类')
    expect(wrapper.text()).toContain('5602015 管理费用-信息技术服务费')
    expect(wrapper.text()).toContain('去下方确认发票')
    expect(wrapper.text()).toContain('按公户直付')
    // requiresReview=true 时行不会被默认勾选进批量（复选框保持未选中或禁用）
    const checkedBoxes = wrapper.findAll('.reconcile-feed-row .n-checkbox--checked')
    expect(checkedBoxes).toHaveLength(0)

    // 关键闭环：发票同时保留在下方「待确认款项的单据」区，走统一「老板垫付 / 员工垫付」流程，且不被误推成「关联疑似流水」
    const pendingCard = wrapper.find('[data-pending-entry-id="e-tencent-reim"]')
    expect(pendingCard.exists()).toBe(true)
    expect(pendingCard.text()).toContain('老板垫付')
    expect(pendingCard.text()).toContain('员工垫付')
    expect(pendingCard.text()).not.toContain('关联疑似流水')

    wrapper.unmount()
  })

  it('renders plain source-and-consequence explanation on matched advancement cards', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const statement = reactive({
      id: 'stmt-cloud-reim',
      amount: -481.33,
      description: '汇兑 | 报销 (对方: 杨云飞)',
      transaction_date: `${month}-12`,
      matched_entry_id: '',
    })
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-cloud-reim',
            matchedEntryId: 'e-cloud-reim',
            matchedEntryIds: ['e-cloud-reim'],
            matchedEntry: {
              id: 'e-cloud-reim',
              title: '发票-腾讯云计算（北京）有限责任公司',
              amount: 481.33,
              direction: 'expense',
              occurDate: '2026-08-18',
              payer: 'boss_personal',
              isAdvancement: true,
            },
            confidence: 0.85,
            reason: '金额完全匹配，日期相差55天',
            statement: { date: `${month}-12`, description: statement.description, amount: -481.33 },
          }],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('2026-08-18 老板垫付单据（¥481.33）· 确认后生成报销还款凭证（冲减其他应付款），不重复记费用')
    expect(wrapper.find('[aria-label="科目与匹配依据"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('intercepts fallback-categorized corporate invoice matches into needs-attention and clears once category is updated', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const seller = '杭州深度求索人工智能基础技术研究有限公司'
    const draft = reactive({
      id: 'e-deepseek-fallback',
      title: `发票-${seller}`,
      amount: 200,
      direction: 'expense',
      category: '其他支出',
      categorySource: 'fallback',
      occurDate: `${month}-20`,
      source: 'invoice_import',
      invoiceNumber: '26332000004655508599',
      billingStatus: 'has_invoice',
      payer: 'company_account',
      voucherStatus: 'draft',
      voucherLines: {
        debit: [{ accountCode: '5602999', accountName: '管理费用-其他', amount: 200 }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 200 }],
      },
    })
    const statement = reactive({
      id: 'stmt-deepseek-fallback',
      amount: -200,
      description: `汇兑 | deepseek调用服务费 (对方: ${seller})`,
      transaction_date: `${month}-20`,
      matched_entry_id: '',
    })
    harness.store = reactive({ activeEntries: [draft], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-deepseek-fallback',
            matchedEntryId: 'e-deepseek-fallback',
            matchedEntryIds: ['e-deepseek-fallback'],
            matchedEntry: {
              id: 'e-deepseek-fallback',
              title: `发票-${seller}`,
              amount: 200,
              direction: 'expense',
              category: '其他支出',
              categorySource: 'fallback',
              source: 'invoice_import',
              payer: 'company_account',
            },
            confidence: 0.99,
            fallbackCategory: true,
            requiresReview: true,
            statement: { date: `${month}-20`, description: statement.description, amount: -200 },
          }],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-deepseek-fallback"]')
    expect(row.exists()).toBe(true)
    expect(wrapper.find('.feed-group-divider.attention').exists()).toBe(true)
    expect(wrapper.find('.feed-group-divider.ready').exists()).toBe(false)
    expect(row.text()).toContain('待确认科目')
    expect(row.text()).toContain('这张发票目前归在兜底科目「其他支出」')
    expect(row.findComponent(NCheckbox).props('disabled')).toBe(true)
    expect(row.findComponent(NCheckbox).props('checked')).toBe(false)

    // 修改单据改为具体科目后，提醒与「待确认科目」徽章立即消失，行恢复可勾选并回到「系统有把握」
    draft.category = '信息技术服务费'
    draft.categorySource = 'manual'
    draft.voucherLines = {
      debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 200 }],
      credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 200 }],
    }
    await flushPromises()

    const updatedRow = wrapper.find('[data-statement-id="stmt-deepseek-fallback"]')
    expect(wrapper.find('.feed-group-divider.ready').exists()).toBe(true)
    expect(wrapper.find('.feed-group-divider.attention').exists()).toBe(false)
    expect(updatedRow.text()).not.toContain('待确认科目')
    expect(updatedRow.text()).not.toContain('这张发票目前归在兜底科目「其他支出」')
    expect(updatedRow.text()).toContain('5602015 管理费用-信息技术服务费')
    expect(updatedRow.findComponent(NCheckbox).props('disabled')).toBe(false)

    wrapper.unmount()
  })

  it('shows cleaned invoice item name on matched card and exposes category basis tooltip even without statement match reason', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const draft = reactive({
      id: 'e-wangyi',
      title: '发票-王翼',
      amount: 300,
      direction: 'expense',
      category: '信息技术服务费',
      categorySource: 'rule',
      categoryReason: '品名关键词「软件测试」命中信息技术服务费规则',
      item_name: '*信息技术服务*软件测试服务',
      occurDate: `${month}-31`,
      source: 'invoice_import',
      invoiceNumber: '26332000004655509001',
      billingStatus: 'has_invoice',
      payer: 'company_account',
      voucherStatus: 'draft',
      voucherLines: {
        debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 300 }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 300 }],
      },
    })
    const statement = reactive({
      id: 'stmt-wangyi',
      amount: -300,
      description: '汇兑 | 软件测试费用 (对方: 王翼)',
      transaction_date: `${month}-31`,
      matched_entry_id: '',
    })
    harness.store = reactive({ activeEntries: [draft], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-wangyi',
            matchedEntryId: 'e-wangyi',
            matchedEntryIds: ['e-wangyi'],
            matchedEntry: {
              id: 'e-wangyi',
              title: '发票-王翼',
              amount: 300,
              direction: 'expense',
              category: '信息技术服务费',
              categorySource: 'rule',
              categoryReason: '品名关键词「软件测试」命中信息技术服务费规则',
              item_name: '*信息技术服务*软件测试服务',
              source: 'invoice_import',
              payer: 'company_account',
            },
            confidence: 0.99,
            statement: { date: `${month}-31`, description: statement.description, amount: -300 },
          }],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-wangyi"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain(`${month}-31 发票（品名：软件测试服务）· 确认后按公户付款核销该发票，不重复记费用`)
    expect(row.find('[aria-label="科目与匹配依据"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('renders AI re-suggestion result persistently inside the edit voucher dialog instead of an auto-dismissing toast', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const draft = reactive({
      id: 'e-wangyi-ai',
      title: '发票-王翼',
      amount: 300,
      direction: 'expense',
      category: '信息技术服务费',
      item_name: '*生产生活服务*软件测试服务',
      occurDate: `${month}-31`,
      source: 'invoice_import',
      billingStatus: 'has_invoice',
      payer: 'company_account',
      voucherStatus: 'draft',
    })
    const statement = reactive({
      id: 'stmt-wangyi-ai',
      amount: -300,
      description: '汇兑 | 软件测试费用 (对方: 王翼)',
      transaction_date: `${month}-31`,
      matched_entry_id: '',
    })
    const aiReason = '发票项目为*生产生活服务*软件测试服务，虽描述为兼职体验测试工具，实质为专业化软件测试服务，符合信息技术服务费口径。'
    harness.store = reactive({ activeEntries: [draft], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [statement] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-wangyi-ai',
            matchedEntryId: 'e-wangyi-ai',
            matchedEntryIds: ['e-wangyi-ai'],
            matchedEntry: { ...draft },
            confidence: 0.99,
            statement: { date: `${month}-31`, description: statement.description, amount: -300 },
          }],
        }
      }
      if (url === '/api/record/entries/e-wangyi-ai/voucher-preview') {
        return {
          entryId: 'e-wangyi-ai',
          status: 'draft',
          direction: 'expense',
          category: '信息技术服务费',
          payer: 'company_account',
          paymentStatus: 'unpaid',
          amount: '300.00',
          source: 'rule',
          willReverse: false,
          warnings: [],
          debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: '300.00' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '300.00' }],
        }
      }
      if (url === '/api/invoice/classify-entry/e-wangyi-ai') {
        return { direction: 'expense', category: '信息技术服务费', reason: aiReason }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    await wrapper.find('[data-statement-id="stmt-wangyi-ai"]')
      .findAll('button')
      .find(btn => btn.text() === '修改单据')!
      .trigger('click')
    await flushPromises()

    const textarea = document.body.querySelector<HTMLTextAreaElement>('.manual-dialog textarea')!
    expect(textarea).toBeTruthy()
    textarea.value = '这是请的兼职来帮我们体验测试工具的，是应该算到咨询服务还是信息技术服务？'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    const aiBtn = Array.from(document.querySelectorAll<HTMLButtonElement>('.manual-dialog button'))
      .find(btn => btn.textContent?.includes('让 AI 根据说明重新建议'))!
    aiBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    const inlineBox = document.body.querySelector('.edit-voucher-ai-suggestion')
    expect(inlineBox?.textContent).toContain('AI 建议：信息技术服务费')
    expect(inlineBox?.textContent).toContain(aiReason)
    expect(harness.success).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('实时缴税通道流水切换点选「社保公积金」候选后，切换为「冲销期初应付（221102+221101）」并按社保提交冲销', async () => {
    const month = '2026-10'
    harness.auth = {
      currentCompanyId: 'c_test_company',
      companies: [{ company_id: 'c_test_company', startDate: '2026-10-01' }],
    }
    harness.route.value.query = { month, step: '2' }
    const statements = reactive<any[]>([
      {
        id: 'stmt-tax-ch-ss',
        amount: -3670,
        description: '暂收款·税款 · 00TX:000990:20261010:000YST26101017G7U3ZA:实时缴税:税单号:433016261013026301',
        transaction_date: '2026-10-10',
        matched_entry_id: '',
        matched_entry_ids: [],
      },
    ])
    const confirmPayloads: any[] = []
    harness.store = reactive({ activeEntries: [], fetchEntries: vi.fn(async () => {}) })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/categories/account-mapping') {
        return {
          items: [
            { category: '税费缴纳', direction: 'expense', accountCode: '2221026', accountName: '应交增值税（小规模纳税人专用）' },
            { category: '社保公积金', direction: 'expense', accountCode: '221102', accountName: '应付职工薪酬-单位社保' },
            { category: '个税代缴', direction: 'expense', accountCode: '2221012', accountName: '应交税费-应交个人所得税' },
            { category: '罚款支出', direction: 'expense', accountCode: '5711011', accountName: '营业外支出-罚款支出' },
            { category: '税收滞纳金', direction: 'expense', accountCode: '5711010', accountName: '营业外支出-税收滞纳金' },
            { category: '所得税费用', direction: 'expense', accountCode: '5801', accountName: '所得税费用' },
          ],
          ambiguityGroups: {
            税费缴纳: ['税费缴纳', '社保公积金', '个税代缴', '罚款支出', '税收滞纳金', '所得税费用'],
          },
        }
      }
      if (url === '/api/bank/statements') return { items: statements.filter(s => !s.matched_entry_id) }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: statements.filter(s => !s.matched_entry_id).map(s => ({
            type: 'new',
            statementId: s.id,
            confidence: 0.5,
            requiresReview: true,
            reason: '摘要走财税库行通用扣款通道（税费/社保/罚款在流水上同形）',
            suggestedEntry: {
              title: '税费缴纳-2026-10-10',
              category: '税费缴纳',
              accountCode: '2221026',
              accountName: '应交增值税（小规模纳税人专用）',
              requiresConfirmation: true,
              requiresReview: true,
            },
            payrollLiabilitySettlement: {
              mode: 'opening_balance',
              kind: 'liability',
              bookStartDate: '2026-10-01',
              accountCode: '221102',
              accountName: '应付职工薪酬-单位社保',
              availableOpeningCredit: '4695.00',
              remainingOpeningCredit: '1025.00',
              requiredAmount: '3670.00',
              reason: '建账启用日（2026-10-01）前遗留应付，将合并冲销期初贷方余额：「221102 应付职工薪酬-单位社保」¥2445.00 + 「221101 应付职工薪酬-工资」¥1225.00，不重复计入本月费用',
              allocations: [
                { accountCode: '221102', accountName: '应付职工薪酬-单位社保', amount: '2445.00' },
                { accountCode: '221101', accountName: '应付职工薪酬-工资', amount: '1225.00' },
              ],
            },
            statement: { date: s.transaction_date, description: s.description, amount: s.amount },
          })),
        }
      }
      if (url === '/api/bank/confirm-suggestion') {
        confirmPayloads.push(payload)
        statements[0].matched_entry_id = 'bank-stmt-tax-ch-ss'
        return { status: 'created', entryId: 'bank-stmt-tax-ch-ss', settledOpeningPayroll: true }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-tax-ch-ss"]')
    expect(row.exists()).toBe(true)
    // 初始展示税费缴纳及「认可入账」，同时提供「社保公积金」选项
    const socialChip = row.findAll('.plan-alt-chip').find(btn => btn.text().includes('社保公积金'))
    expect(socialChip).toBeTruthy()
    expect(row.find('.reconcile-primary-btn').text()).toContain('认可入账')

    // 点选「社保公积金」后，瀑布分摊说明和按钮立即切换为「冲销期初应付（221102+221101）」
    await socialChip!.trigger('click')
    await flushPromises()

    expect(row.find('.plan-plain-hint').text()).toContain('「221102 应付职工薪酬-单位社保」¥2445.00 + 「221101 应付职工薪酬-工资」¥1225.00')
    const settleBtn = row.find('.reconcile-primary-btn')
    expect(settleBtn.text()).toContain('冲销期初应付（221102+221101）')

    await settleBtn.trigger('click')
    await flushPromises()

    expect(confirmPayloads).toHaveLength(1)
    expect(confirmPayloads[0]).toMatchObject({
      statement_id: 'stmt-tax-ch-ss',
      type: 'new',
      settleOpeningPayroll: true,
      new_entry: expect.objectContaining({
        category: '社保公积金',
        amount: 3670,
      }),
    })

    wrapper.unmount()
  })

  it('test_disposition_prompts_prior_payment_conflict_and_can_switch_to_link', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const draft = reactive({
      id: 'inv-ds-602',
      title: '发票-杭州深度求索人工智能基础技术研究有限公司',
      direction: 'expense',
      amount: 500,
      occurDate: `${month}-02`,
      category: '信息技术服务费',
      source: 'invoice_import',
      invoiceNumber: '26332000004655508586',
      payer: 'company_account',
      billingStatus: 'has_invoice',
      voucherStatus: 'draft',
    })
    const entries = reactive<any[]>([draft])
    const linkPayloads: any[] = []
    harness.store = reactive({
      entries,
      activeEntries: entries,
      fetchEntries: vi.fn(async () => entries),
      updateEntry: vi.fn(),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: [] }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      if (url === '/api/invoices/prior-payment/candidates') {
        return {
          candidates: {
            'inv-ds-602': [
              {
                entryId: 'prior-pay-531',
                summary: '信息技术服务费-2026-05-31',
                occurredOn: '2026-05-31',
                status: 'posted',
                amount: '500.00',
                statementDescription: '汇兑 | 附言: 往来款 (对方: 杭州深度求索人工智能基础技术研究有限公司)',
                confidence: 'counterparty',
              },
            ],
          },
        }
      }
      if (url === '/api/invoices/prior-payment/link') {
        linkPayloads.push(payload)
        const idx = entries.findIndex((e) => e.id === payload.invoiceEntryId)
        if (idx >= 0) entries.splice(idx, 1)
        return { invoiceIds: ['inv-1'], priorEntry: { id: payload.priorEntryId } }
      }
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      if (url.includes('/preview-voucher')) {
        return {
          entryId: 'inv-ds-602',
          category: '信息技术服务费',
          debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: '500.00' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '500.00' }],
          warnings: [],
        }
      }
      throw new Error(`Unexpected write: ${url}`)
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const bossBtn = wrapper.findAll('button').find((b) => b.text().trim() === '老板垫付')!
    expect(bossBtn).toBeDefined()
    await bossBtn.trigger('click')
    await flushPromises()

    const conflictCall = harness.dialog.warning.mock.calls.find(
      ([opts]: any[]) => opts?.title === '检测到前期公户可能已付过这笔钱',
    )?.[0]
    expect(conflictCall).toBeDefined()
    expect(conflictCall.positiveText).toBe('改挂到前期付款（推荐）')
    expect(conflictCall.negativeText).toBe('确为另一笔，继续选择')

    // 选择「确为另一笔，继续选择」后展开第二步去向面板
    conflictCall.onNegativeClick()
    await flushPromises()
    expect((wrapper.vm as any).expandedDispositionEntryId).toBe('inv-ds-602')
    expect(harness.patch).not.toHaveBeenCalled()

    // 若选择「改挂到前期付款（推荐）」，直接调用挂接接口不落垫付
    await conflictCall.onPositiveClick()
    await flushPromises()
    expect(linkPayloads).toEqual([{ invoiceEntryId: 'inv-ds-602', priorEntryId: 'prior-pay-531' }])
    expect(harness.patch).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('allows editing category and undoing on posted matched bank rows, and shows pre-close review modal before closing', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const entries = reactive<any[]>([
      {
        id: 'bank-bs-1007',
        title: '微信支付提现到账-2026-10-07',
        direction: 'owner_transfer',
        amount: 4.95,
        occurDate: `${month}-07`,
        category: '微信支付结算',
        source: 'bank_import',
        billingStatus: 'reconciled',
        voucherStatus: 'posted',
        voucherRef: 'VOU-202610-000006',
        voucherLines: {
          debit: [{ accountCode: '1002', accountName: '银行存款', amount: 4.95 }],
          credit: [{ accountCode: '1012001', accountName: '微信支付待结算', amount: 4.95 }],
        },
      },
    ])
    const statements = reactive<any[]>([
      {
        id: 'bs-1007',
        amount: 4.95,
        description: '入账 | 附言: 1007_1113670810 (对方: 财付通支付科技有限公司)',
        transaction_date: `${month}-07`,
        matched_entry_id: 'bank-bs-1007',
        matched_entry_ids: ['bank-bs-1007'],
      },
    ])
    const patchPayloads: any[] = []
    const postCalls: Array<{ url: string; payload: any }> = []
    harness.store = reactive({
      entries,
      activeEntries: entries,
      fetchEntries: vi.fn(async () => entries),
      updateEntry: vi.fn(),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) {
        return {
          periodStatus: 'open',
          canClose: true,
          snapshotHash: 'snap-1',
          checks: [
            { code: 'VOUCHER_TO_SOURCE', name: '账证核对', status: 'passed', draftCount: 0, pendingReviewCount: 0, orphanVoucherCount: 0 },
            { code: 'LEDGER_TO_LEDGER', name: '账账核对', status: 'passed', debit: '4.95', credit: '4.95' },
            { code: 'BOOK_TO_ACTUAL', name: '账实核对', status: 'passed', unreconciledBankCount: 0, issues: [] },
            { code: 'LEDGER_TO_REPORT', name: '账表核对', status: 'passed', issues: [] },
            { code: 'REPORT_TO_TAX', name: '申报校验', status: 'passed', missingRuleVersionCount: 0 },
          ],
        }
      }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      postCalls.push({ url, payload })
      if (url === '/api/bank/ai-suggest') return { suggestions: [] }
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/bank/confirm-suggestion') return { status: 'unmatched' }
      if (url === '/api/report/income-statement') {
        return { rows: [{ key: 'operating_revenue', isSubtotal: false, values: { current: 4.95 } }] }
      }
      if (url.includes('/close')) return { status: 'closed', closingVoucherRefs: ['VOU-CLOSE-1'] }
      throw new Error(`Unexpected post: ${url}`)
    })
    harness.patch.mockImplementation(async (url: string, payload: any) => {
      patchPayloads.push({ url, payload })
      return { entry: { ...entries[0], id: 'corrected-1007', ...payload } }
    })
    harness.dialog.warning.mockImplementation((opts: any) => {
      if (String(opts?.title || '') === '本月还有单据未上传') {
        opts.onPositiveClick?.()
        opts.onAfterLeave?.()
        return
      }
      opts.onPositiveClick?.()
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    // 1. 已过账流水点「修改分类」-> 弹红冲提示并带 autoReverseRepost: true 保存
    const pair = (wrapper.vm as any).persistedMatchedPairs[0]
    expect(pair).toBeDefined()
    ;(wrapper.vm as any).openPairCategoryDialog(pair)
    await flushPromises()
    ;(wrapper.vm as any).pairCategoryForm.categoryValue = 'income|主营业务收入|5001|主营业务收入'
    await (wrapper.vm as any).handlePairCategorySave()
    await flushPromises()
    expect(patchPayloads).toHaveLength(1)
    expect(patchPayloads[0].payload.autoReverseRepost).toBe(true)

    // 2. 已过账流水点「撤销」-> 原子传 reversePostedBankEntries: true
    await (wrapper.vm as any).deletePair(pair.id)
    await flushPromises()
    const undoCall = postCalls.find((c) => c.url === '/api/bank/confirm-suggestion')
    expect(undoCall?.payload.reversePostedBankEntries).toBe(true)

    // 3. 第三步点击结账按钮先弹出「账目最终核对」确认框，点确认才调用 /close
    await wrapper.findAll('.n-step')[2]!.trigger('click')
    await flushPromises()
    const closeBtn = wrapper.findAll('button').find((b) => b.text().includes('核对账目并锁账'))
    expect(closeBtn).toBeDefined()
    await closeBtn!.trigger('click')
    await flushPromises()
    expect((wrapper.vm as any).batchPostConfirmVisible).toBe(true)
    expect(postCalls.some((c) => c.url.includes('/close'))).toBe(false)

    await (wrapper.vm as any).confirmReviewAndClose()
    await flushPromises()
    expect(postCalls.some((c) => c.url.includes('/close'))).toBe(true)

    wrapper.unmount()
  })

  it('offers 修改单据, 换单据, and 不配对，改分类 on single-match cards including posted matched invoices after undo', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const postedInvoice = reactive<any>({
      id: 'e-zhipu',
      title: '发票-浙江智谱新篇科技有限公司',
      amount: 693.84,
      direction: 'expense',
      category: '信息技术服务费',
      accountCode: '5602015',
      accountName: '管理费用-信息技术服务费',
      occurDate: `${month}-08`,
      source: 'invoice_import',
      payer: 'boss_personal',
      voucherStatus: 'posted',
      voucherLines: {
        debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: 693.84 }],
        credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 693.84, partnerName: '股东' }],
      },
    })
    const altInvoice = reactive<any>({
      id: 'e-alt-1',
      title: '发票-备选供应商',
      amount: 693.84,
      direction: 'expense',
      category: '信息技术服务费',
      accountCode: '5602015',
      accountName: '管理费用-信息技术服务费',
      occurDate: `${month}-12`,
      source: 'invoice_import',
      voucherStatus: 'draft',
    })
    const entries = reactive<any[]>([postedInvoice, altInvoice])
    const statements = reactive<any[]>([
      {
        id: 'stmt-zhipu',
        amount: -693.84,
        description: '付款 | 杨云飞·报销智谱codingplan年度订阅费',
        transaction_date: `${month}-26`,
        matched_entry_id: '',
        matched_entry_ids: [] as string[],
      },
    ])
    const postCalls: Array<{ url: string; payload: any }> = []
    const patchCalls: Array<{ url: string; payload: any }> = []

    harness.store = reactive({
      entries,
      activeEntries: entries,
      fetchEntries: vi.fn(async () => entries),
      updateEntry: vi.fn(),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') return { items: statements }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string, payload: any) => {
      postCalls.push({ url, payload })
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-zhipu',
            matchedEntryId: 'e-zhipu',
            matchedEntryIds: ['e-zhipu'],
            matchedEntry: { ...postedInvoice },
            confidence: 0.92,
            statement: { date: `${month}-26`, description: '付款 | 杨云飞·报销智谱codingplan年度订阅费', amount: -693.84 },
          }],
        }
      }
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url === '/api/record/entries/e-zhipu/voucher-preview') {
        return {
          entryId: 'e-zhipu',
          status: 'posted',
          direction: 'expense',
          category: '信息技术服务费',
          amount: '693.84',
          source: 'rule',
          willReverse: true,
          warnings: [],
          debit: [{ accountCode: '5602015', accountName: '管理费用-信息技术服务费', amount: '693.84' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: '693.84', partnerName: '股东' }],
        }
      }
      if (url === '/api/bank/confirm-suggestion') return { status: 'matched' }
      throw new Error(`Unexpected post: ${url}`)
    })
    harness.patch.mockImplementation(async (url: string, payload: any) => {
      patchCalls.push({ url, payload })
      return { entry: { ...postedInvoice, id: 'e-zhipu-repost' } }
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    // 单笔已过账匹配卡片除「确认配对」外，应同时提供「修改单据」「不配对，改分类」「换单据」
    const actionButtons = wrapper.find('.reconcile-cell-action').findAll('button').map(b => b.text().trim())
    expect(actionButtons).toContain('确认配对')
    expect(actionButtons).toContain('修改单据')
    expect(actionButtons).toContain('不配对，改分类')
    expect(actionButtons).toContain('换单据')

    // 1. 点「修改单据」并在弹窗保存 -> 带 autoReverseRepost: true 走红冲重记并全量刷新
    const editBtn = wrapper.find('.reconcile-cell-action').findAll('button').find(b => b.text().trim() === '修改单据')!
    await editBtn.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.manual-dialog-title')?.textContent).toBe('修改单据')
    const saveEditBtn = Array.from(document.querySelectorAll<HTMLButtonElement>('.manual-dialog-actions button'))
      .find(btn => btn.textContent?.includes('保存修改'))!
    saveEditBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(patchCalls).toHaveLength(1)
    expect(patchCalls[0].url).toBe('/api/record/entries/e-zhipu')
    expect(patchCalls[0].payload.autoReverseRepost).toBe(true)

    // 2. 点「不配对，改分类」-> 弹防重复提醒，确认后打开单独记账弹窗并按 type: 'new' 提交
    const bypassBtn = wrapper.find('.reconcile-cell-action').findAll('button').find(b => b.text().trim() === '不配对，改分类')!
    await bypassBtn.trigger('click')
    await flushPromises()
    expect(harness.dialog.warning).toHaveBeenCalledWith(expect.objectContaining({
      title: '确定不配对、改为单独记账？',
    }))
    expect((wrapper.vm as any).showManualDialog).toBe(true)
    expect(Array.from(document.body.querySelectorAll('.manual-dialog-title')).some(el => el.textContent === '修改分类与记账明细')).toBe(true)

    ;(wrapper.vm as any).manualForm.categoryValue = 'expense|办公费|5602001|管理费用-办公费'
    await (wrapper.vm as any).handleManualClassify()
    await flushPromises()
    const newConfirmCall = postCalls.find(c => c.url === '/api/bank/confirm-suggestion')
    expect(newConfirmCall?.payload).toMatchObject({
      statement_id: 'stmt-zhipu',
      type: 'new',
      new_entry: expect.objectContaining({
        category: '办公费',
        accountCode: '5602001',
      }),
    })

    wrapper.unmount()
  })

  it('multi-match fallback card shows fallback count and lets user open 修改待定单据', async () => {
    const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    const month = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
    const travelEntries = reactive<any[]>([
      {
        id: 'e-travel-1',
        title: '发票-北京我遥我控科技有限公司',
        amount: 567,
        direction: 'expense',
        category: '其他支出',
        accountCode: '5602999',
        accountName: '管理费用-其他',
        occurDate: `${month}-09`,
        source: 'invoice_import',
        voucherStatus: 'posted',
        voucherLines: {
          debit: [{ accountCode: '5602999', accountName: '管理费用-其他', amount: 567 }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 567 }],
        },
      },
      {
        id: 'e-travel-2',
        title: '发票-京沈铁路客运专线辽宁有限责任公司',
        amount: 98,
        direction: 'expense',
        category: '其他支出',
        accountCode: '5602999',
        accountName: '管理费用-其他',
        occurDate: `${month}-09`,
        source: 'invoice_import',
        voucherStatus: 'posted',
      },
    ])
    const patchCalls: Array<{ url: string; payload: any }> = []
    harness.store = reactive({
      entries: travelEntries,
      activeEntries: travelEntries,
      fetchEntries: vi.fn(async () => travelEntries),
      updateEntry: vi.fn(),
    })
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/bank/statements') {
        return {
          items: [{
            id: 'stmt-travel-multi',
            transaction_date: `${month}-12`,
            description: '移动支付 | 报销差旅交通费大连/丹东 (对方: 杨云飞)',
            amount: -665,
            matched_entry_id: '',
          }],
        }
      }
      if (url === '/api/bank/reconciliation-health') return { summary: { highRiskCount: 0, warningCount: 0 } }
      if (url.includes('/close-checks')) return { periodStatus: 'open', canClose: false }
      return { items: [], records: [], summary: {} }
    })
    harness.post.mockImplementation(async (url: string) => {
      if (url === '/api/bank/ai-suggest') {
        return {
          suggestions: [{
            type: 'match',
            statementId: 'stmt-travel-multi',
            matchedEntryId: 'e-travel-1',
            matchedEntryIds: ['e-travel-1', 'e-travel-2'],
            matchedEntry: travelEntries[0],
            matchedEntries: travelEntries,
            confidence: 0.95,
            rule: 'multi_invoice_subset_match',
            fallbackCategory: true,
            requiresReview: true,
            statement: { date: `${month}-12`, description: '移动支付 | 报销差旅交通费大连/丹东 (对方: 杨云飞)', amount: -665 },
          }],
        }
      }
      if (url === '/api/invoices/prior-payment/candidates') return { candidates: {} }
      if (url.includes('/voucher-preview')) {
        return {
          entryId: 'e-travel-1',
          status: 'posted',
          direction: 'expense',
          category: '其他支出',
          amount: '665.00',
          source: 'rule',
          willReverse: true,
          warnings: [],
          debit: [{ accountCode: '5602999', accountName: '管理费用-其他', amount: '567.00' }],
          credit: [{ accountCode: '2241', accountName: '其他应付款', amount: 567 }],
        }
      }
      throw new Error(`Unexpected post: ${url}`)
    })
    harness.patch.mockImplementation(async (url: string, payload: any) => {
      patchCalls.push({ url, payload })
      return { entry: { id: url.split('/').pop(), ...payload } }
    })

    const wrapper = mount(ClosingWizard)
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const row = wrapper.find('[data-statement-id="stmt-travel-multi"]')
    expect(row.text()).toContain('合并单据中有 2 笔仍归在兜底科目「其他支出」')
    const editPendingBtn = row.find('.reconcile-cell-action').findAll('button').find(b => b.text().includes('修改待定单据'))
    expect(editPendingBtn).toBeTruthy()

    await editPendingBtn!.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.manual-dialog-title')?.textContent).toBe('修改单据')
    expect(document.body.textContent).toContain('同时将所选分类应用到本组其余 1 笔待定单据')
    expect((wrapper.vm as any).editVoucherApplyToSiblings).toBe(true)

    const saveBatchBtn = Array.from(document.querySelectorAll<HTMLButtonElement>('.manual-dialog-actions button'))
      .find(btn => btn.textContent?.includes('批量保存 (2 笔)'))!
    expect(saveBatchBtn).toBeTruthy()

    ;(wrapper.vm as any).editVoucherForm.categoryValue = 'expense|差旅费|5602006|管理费用-差旅费'
    saveBatchBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(patchCalls).toHaveLength(2)
    expect(patchCalls[0]).toMatchObject({
      url: '/api/record/entries/e-travel-1',
      payload: { category: '差旅费', autoReverseRepost: true },
    })
    expect(patchCalls[1]).toMatchObject({
      url: '/api/record/entries/e-travel-2',
      payload: { category: '差旅费', autoReverseRepost: true },
    })

    wrapper.unmount()
  })
})
