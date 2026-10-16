/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import ClosingWizard from '@/pages/closing-wizard/index.vue'

const harness = vi.hoisted(() => ({
  store: null as any,
  get: vi.fn(), post: vi.fn(), push: vi.fn(),
  warning: vi.fn(), error: vi.fn(), success: vi.fn(),
}))
vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.store }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: harness.push }) }))
vi.mock('@/api/client', () => ({
  default: { get: harness.get, post: harness.post }, makeIdempotencyKey: () => 'test-only',
}))
vi.mock('@/composables/useDeleteConfirm', () => ({ useDeleteConfirm: () => ({ confirmDelete: vi.fn() }) }))
vi.mock('@/api/rd', () => ({ getRdProjects: async () => [], getRdExpenseTypes: async () => ({ items: [] }) }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ warning: harness.warning, error: harness.error, success: harness.success, info: vi.fn() }),
}))

afterEach(() => vi.clearAllMocks())

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
    await wrapper.findAll('.n-step')[3]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('本月待处理账目与发票 (2)')
    const pair = wrapper.find('.matched-pair-card')
    expect(pair.text()).toContain('微信原始收入')
    expect(pair.text()).toContain('微信手续费')
    expect(pair.text()).toContain('待核对')
    await wrapper.findAll('button').find(button => button.text().includes('手动分类'))!.trigger('click')
    expect(harness.warning).toHaveBeenCalled()
    expect(wrapper.text()).toContain('组合核对')
    const confirm = pair.findAll('button').find(button => button.text().includes('确认匹配'))!
    await confirm.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('本月待处理账目与发票 (0)')
    expect(wrapper.find('.matched-pair-card').text()).toContain('已核对')
    expect(wrapper.find('.matched-pair-card').text()).toContain('结算凭证待过账')
    expect(wrapper.find('.matched-pair-card').text()).toContain('查看结算凭证')
    expect(wrapper.text()).not.toContain('组合核对')
    expect(harness.error).not.toHaveBeenCalled()
    expect(harness.post.mock.calls.filter(([url]) => url === '/api/bank/confirm-suggestion')).toHaveLength(1)

    // Verify in-place inspection: clicking 查看结算凭证 opens modal instead of navigating away
    const viewVoucherBtn = wrapper.find('.matched-pair-card').findAll('button').find(button => button.text().includes('查看结算凭证'))!
    await viewVoucherBtn.trigger('click')
    await flushPromises()
    expect(harness.push).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('微信结算凭证')
    expect(document.body.textContent).toContain('1002')

    wrapper.unmount()
  })
})
