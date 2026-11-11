/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { NSelect } from 'naive-ui'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ClosingWizard from '@/pages/closing-wizard/index.vue'
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
    get: vi.fn(), post: vi.fn(), patch: vi.fn(), push: vi.fn(), replace: vi.fn(),
    warning: vi.fn(), error: vi.fn(), success: vi.fn(),
    dialog: { warning: vi.fn(autoConfirmImportGapDialog), error: vi.fn(), success: vi.fn() },
    rdProjects: [] as any[],
    rdExpenseTypes: [] as any[],
  }
})
vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.store }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => harness.auth }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: harness.push, replace: harness.replace, currentRoute: harness.route }) }))
vi.mock('@/api/client', () => ({
  default: { get: harness.get, post: harness.post, patch: harness.patch }, makeIdempotencyKey: () => 'test-only',
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

    const confirmBtn = wrapper.findAll('button').find(button => button.text().includes('确认配对'))!
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(harness.error).not.toHaveBeenCalled()
    expect(harness.post.mock.calls.filter(([url]) => url === '/api/bank/confirm-suggestion')).toHaveLength(1)
    expect(wrapper.text()).toContain('已认领并完成流水')

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
    expect(bar.text()).toContain('还未上传：发票、微信账单')

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
    expect(cards[0].text()).toContain('核对已有账目')
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

    const aiEntry = wrapper.findAll('button').find(button => button.text().includes('AI CFO'))
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
    expect(text).toContain('导入微信/平台账单')
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

    // 已计提：计提按钮禁用并打勾，发放按钮可点
    expect(wrapper.text()).toContain('✓ 已经计提记账')
    expect(wrapper.text()).toContain('确认发放记账')
    // 有社保/个税挂账：缴纳按钮出现
    expect(wrapper.text()).toContain('确认缴纳社保')
    expect(wrapper.text()).toContain('确认缴纳个税')
    // 已计提但未发放：发放和社保/个税作为负债结转，不阻断进入下一步
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
    expect(wrapper.text()).not.toContain('确认缴纳社保')

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

    expect(wrapper.text()).toContain('✓ 已经确认发放')
    expect(wrapper.text()).toContain('✓ 社保已缴纳')
    expect(wrapper.text()).toContain('✓ 公积金已缴纳')
    expect(wrapper.text()).toContain('✓ 个税已缴纳')

    const nextButton = wrapper.findAll('button').find(button => button.text().includes('下一步'))!
    await nextButton.trigger('click')
    expect(harness.warning).not.toHaveBeenCalled()

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
        billingStatus: 'reconciled',
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

    // 抽屉底部提供直达「老板私账」台账按钮
    const targetBtn = wrapper.findAll('button').find(b => b.text().includes('前往老板私账登记还款'))!
    expect(targetBtn).toBeDefined()
    await targetBtn.trigger('click')
    await flushPromises()

    expect(harness.push).toHaveBeenCalledWith({
      path: '/private-expenses',
      query: undefined,
    })

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
    expect(wrapper.find('.pending-entry-plan').exists()).toBe(true)
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
    expect(harness.patch).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('「详细记账」打开修改弹窗并预置付款人', async () => {
    setupPendingEntry()
    const wrapper = mount(ClosingWizard)
    await flushPromises()

    await wrapper.find('.pending-entry-card').findAll('button').find(button => button.text() === '老板垫付')!.trigger('click')
    await flushPromises()
    await wrapper.find('.pending-entry-plan').findAll('button').find(button => button.text() === '详细记账')!.trigger('click')
    await flushPromises()

    // NModal 走 teleport，内容挂在 document.body 上
    expect(document.body.textContent).toContain('详细记账')
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
