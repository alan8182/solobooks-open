/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import ClosingWizard from '@/pages/closing-wizard/index.vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

const harness = vi.hoisted(() => ({
  store: null as any,
  route: { value: { query: {} as Record<string, string> } } as any,
  get: vi.fn(), post: vi.fn(), push: vi.fn(), replace: vi.fn(),
  warning: vi.fn(), error: vi.fn(), success: vi.fn(),
  dialog: { warning: vi.fn(), error: vi.fn(), success: vi.fn() },
}))
vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.store }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ currentCompanyId: 'c_test_company' }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: harness.push, replace: harness.replace, currentRoute: harness.route }) }))
vi.mock('@/api/client', () => ({
  default: { get: harness.get, post: harness.post }, makeIdempotencyKey: () => 'test-only',
}))
vi.mock('@/composables/useDeleteConfirm', () => ({ useDeleteConfirm: () => ({ confirmDelete: vi.fn() }) }))
vi.mock('@/api/rd', () => ({ getRdProjects: async () => [], getRdExpenseTypes: async () => ({ items: [] }) }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ warning: harness.warning, error: harness.error, success: harness.success, info: vi.fn() }),
  useDialog: () => harness.dialog,
}))

beforeEach(() => {
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
    const wrapper = mount(ClosingWizard, { global: { stubs: { ClosingAgentPanel: true } } })
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

    const wrapper = mount(ClosingWizard, { global: { stubs: { ClosingAgentPanel: true } } })
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('.feed-card')
    expect(cards[0].text()).toContain('核对已有账目')
    expect(cards[1].text()).toContain('核算明细')
    // 兜底草稿不能冒充「建议分类」
    expect(cards[2].text()).toContain('系统兜底分类')
    expect(cards[2].text()).toContain('按此入账')
    expect(cards[2].text()).not.toContain('建议分类')

    const bulkButton = wrapper.findAll('button').find(button => button.text().includes('已识别流水'))!
    expect(bulkButton.text()).toContain('2 笔')
    await bulkButton.trigger('click')
    await flushPromises()

    expect(confirmed).toHaveLength(2)
    expect(confirmed.map(item => item.type).sort()).toEqual(['match', 'new'])
    expect(harness.post.mock.calls.some(([url]) => url === '/api/bank/batch-confirm-rules')).toBe(false)
    expect(harness.success).toHaveBeenCalledWith('已确认 2 笔流水')
    // 兜底草稿置信度低，不参与一键确认，仍留在待处理列表里
    expect(wrapper.text()).toContain('待处理银行流水 (1)')

    wrapper.unmount()
  })
})

describe('closing wizard unified view', () => {
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

    const wrapper = mount(ClosingWizard, { global: { stubs: { ClosingAgentPanel: true } } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('导入发票')
    expect(text).toContain('导入微信/平台账单')
    expect(text).toContain('导入银行流水')
    expect(text).toContain('去整页管理')

    const headers = () => wrapper.findAll('.report-table th').map(th => th.text())
    expect(headers()).toEqual(['日期', '发票名称', '记账分类', '发票金额', '状态', '下一步'])

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

    const wrapper = mount(ClosingWizard, { global: { stubs: { ClosingAgentPanel: true } } })
    await flushPromises()
    await wrapper.findAll('.n-step')[1]!.trigger('click')
    await flushPromises()

    const accept = wrapper.findAll('button').find(button => button.text().includes('认可入账'))!
    await accept.trigger('click')
    await flushPromises()

    expect(harness.dialog.warning).toHaveBeenCalledTimes(1)
    const options = harness.dialog.warning.mock.calls[0][0]
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

    const wrapper = mount(ClosingWizard, { global: { stubs: { ClosingAgentPanel: true } } })
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
      expect.stringContaining('已沉淀规则：后续「招商银行股份有限公司深圳高新园科创支行」自动归入此科目')
    )

    wrapper.unmount()
  })
})
