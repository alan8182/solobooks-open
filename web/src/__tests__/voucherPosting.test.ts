/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import EntryDetail from '@/pages/ledger/detail.vue'
import VoucherList from '@/pages/voucher/index.vue'

const h = vi.hoisted(() => ({
  store: null as any,
  post: vi.fn(),
  approve: vi.fn(),
  submit: vi.fn(),
  batchPost: vi.fn(),
  options: vi.fn(),
  checks: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  dialogInfo: vi.fn(),
  dialogWarning: vi.fn(),
  push: vi.fn(),
}))

vi.mock('@/stores/entry', () => ({ useEntryStore: () => h.store }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'draft' }, query: { month: '2026-07' } }),
  useRouter: () => ({ push: h.push, replace: vi.fn() }),
}))
vi.mock('@/api/accounting', () => ({
  postEntry: h.post,
  approveEntry: h.approve,
  submitEntryForReview: h.submit,
  batchPostEntries: h.batchPost,
  getPostingOptions: h.options,
  getPeriodCloseChecks: h.checks,
  reverseEntry: vi.fn(),
}))
vi.mock('@/api/rd', () => ({ getRdProjects: async () => [], getRdExpenseTypes: async () => ({ items: [] }) }))
vi.mock('naive-ui', async importOriginal => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  NModal: { props: ['show'], template: '<div v-if="show" class="test-modal"><slot /><slot name="footer" /></div>' },
  useMessage: () => ({ error: h.error, success: h.success, warning: h.warning }),
  useDialog: () => ({ info: h.dialogInfo, warning: h.dialogWarning, error: vi.fn() }),
}))

afterEach(() => vi.clearAllMocks())

describe('voucher posting workflow', () => {
  it.each([
    ['draft', { canPost: true }, '确认过账', 'post'],
    ['pending_review', { canApprove: true }, '复核并过账', 'approve'],
    ['draft', { canSubmit: true, canPost: false }, '提交复核', 'submit'],
  ] as const)('reviews %s through the permitted action %s', async (status, options, label, action) => {
    const entry = { id: 'draft', title: '测试收入', amount: 5, direction: 'income', category: '主营业务收入', occurDate: '2026-07-14', voucherStatus: status, voucherLines: { debit: [{ accountCode: '1012001', accountName: '微信待结算', amount: 5 }], credit: [{ accountCode: '5001', accountName: '主营业务收入', amount: 5 }] } }
    h.store = { activeEntries: [entry], fetchEntries: vi.fn(async () => {}) }
    h.options.mockResolvedValue(options)
    h[action].mockResolvedValue({ entry: { ...entry, voucherStatus: action === 'submit' ? 'pending_review' : 'posted', voucherRef: action === 'submit' ? '' : 'VOU-202607-000001' } })
    const wrapper = mount(EntryDetail)
    await flushPromises()
    const matchAction = (button: { text: () => string }) => button.text().trim().startsWith(label)
    const button = wrapper.findAll('button').find(matchAction)!
    await button.trigger('click')
    expect(h[action]).not.toHaveBeenCalled()
    const modal = wrapper.find('.test-modal')
    expect(modal.text()).toContain('1012001')
    expect(modal.text()).toContain('5001')
    await modal.findAll('button').find(matchAction)!.trigger('click')
    await flushPromises()
    expect(h[action]).toHaveBeenCalledExactlyOnceWith('draft')
    expect(h.error).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain(action === 'submit' ? '待复核' : '已过账')
    wrapper.unmount()
  })

  it('shows all month blockers and provides batch post all action', async () => {
    const entries = Array.from({ length: 15 }, (_, i) => ({ id: `july-${i}`, title: `七月收入-${i}`, amount: 5, direction: 'income', occurDate: '2026-07-14', voucherStatus: 'draft' }))
    h.store = { activeEntries: [...entries, { id: 'old', title: '六月收入', occurDate: '2026-06-14', voucherStatus: 'draft' }], fetchEntries: vi.fn(async () => {}) }
    h.checks.mockResolvedValue({ checks: [{ code: 'VOUCHER_TO_SOURCE', records: entries.map(e => ({ entryId: e.id, title: e.title, date: e.occurDate, reasons: ['pending_post'] })) }] })
    h.dialogInfo.mockImplementation((opts: any) => opts.onPositiveClick?.())
    h.batchPost.mockResolvedValue({ total: 15, postedCount: 15, failedCount: 0, postedEntryIds: entries.map(e => e.id), failedEntries: [] })

    const wrapper = mount(VoucherList)
    await flushPromises()
    expect(h.checks).toHaveBeenCalledWith('2026-07')
    expect(wrapper.text()).toContain('七月收入-14')
    expect(wrapper.text()).not.toContain('六月收入')

    // Verify batch post button is present and clickable
    const batchAllBtn = wrapper.findAll('button').find(button => button.text().includes('一键过账全部'))
    expect(batchAllBtn).toBeDefined()
    expect(batchAllBtn!.text()).toContain('15')

    await batchAllBtn!.trigger('click')
    await flushPromises()

    expect(h.dialogInfo).toHaveBeenCalled()
    expect(h.batchPost).toHaveBeenCalledWith({ entryIds: entries.map(e => e.id) })
    expect(h.success).toHaveBeenCalledWith(expect.stringContaining('成功批量过账 15 笔'))

    wrapper.unmount()
  })

  it('handles partial failures in batch post cleanly with warning', async () => {
    const entries = [
      { id: 'e-1', title: '正常凭证', amount: 10, occurDate: '2026-07-10', voucherStatus: 'draft' },
      { id: 'e-2', title: '不平衡凭证', amount: 20, occurDate: '2026-07-11', voucherStatus: 'draft' },
    ]
    h.store = { activeEntries: entries, fetchEntries: vi.fn(async () => {}) }
    h.checks.mockResolvedValue({ checks: [] })
    h.dialogInfo.mockImplementation((opts: any) => opts.onPositiveClick?.())
    h.batchPost.mockResolvedValue({
      total: 2,
      postedCount: 1,
      failedCount: 1,
      postedEntryIds: ['e-1'],
      failedEntries: [{ entryId: 'e-2', title: '不平衡凭证', code: 'UNBALANCED_ENTRY', reason: '借贷不平衡' }],
    })

    const wrapper = mount(VoucherList)
    await flushPromises()

    const batchAllBtn = wrapper.findAll('button').find(button => button.text().includes('一键过账全部'))!
    await batchAllBtn.trigger('click')
    await flushPromises()

    expect(h.warning).toHaveBeenCalledWith(expect.stringContaining('已过账 1 笔凭证；1 笔因需复核或异常未过账'))
    expect(h.dialogWarning).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('supports selecting individual entries and batch posting only selected', async () => {
    const entries = [
      { id: 'sel-1', title: '选中的凭证', amount: 10, occurDate: '2026-07-10', voucherStatus: 'draft' },
      { id: 'sel-2', title: '未选的凭证', amount: 20, occurDate: '2026-07-11', voucherStatus: 'draft' },
    ]
    h.store = { activeEntries: entries, fetchEntries: vi.fn(async () => {}) }
    h.checks.mockResolvedValue({ checks: [] })
    h.dialogInfo.mockImplementation((opts: any) => opts.onPositiveClick?.())
    h.batchPost.mockResolvedValue({
      total: 1,
      postedCount: 1,
      failedCount: 0,
      postedEntryIds: ['sel-1'],
      failedEntries: [],
    })

    const wrapper = mount(VoucherList)
    await flushPromises()

    const checkboxes = wrapper.findAll('.n-checkbox')
    expect(checkboxes.length).toBeGreaterThanOrEqual(3)
    await checkboxes[1].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已勾选 1 笔')
    const selectedBtn = wrapper.findAll('button').find(button => button.text().includes('批量过账所选 (1)'))
    expect(selectedBtn).toBeDefined()

    await selectedBtn!.trigger('click')
    await flushPromises()

    expect(h.batchPost).toHaveBeenCalledWith({ entryIds: ['sel-1'] })
    expect(h.success).toHaveBeenCalledWith(expect.stringContaining('成功批量过账 1 笔'))

    wrapper.unmount()
  })
})
