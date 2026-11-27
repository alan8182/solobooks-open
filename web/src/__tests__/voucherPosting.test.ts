/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { NInput, NSelect } from 'naive-ui'
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

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => vi.clearAllMocks())

describe('voucher posting workflow', () => {
  it('hides manual post action for single-user draft and keeps review workflow actions', async () => {
    const draftEntry = { id: 'draft', title: '测试收入', amount: 5, direction: 'income', category: '主营业务收入', occurDate: '2026-07-14', voucherStatus: 'draft', voucherLines: { debit: [{ accountCode: '1012001', accountName: '微信待结算', amount: 5 }], credit: [{ accountCode: '5001', accountName: '主营业务收入', amount: 5 }] } }
    h.store = { activeEntries: [draftEntry], fetchEntries: vi.fn(async () => {}) }
    h.options.mockResolvedValue({ canPost: true, canSubmit: false, canApprove: false })
    const draftWrapper = mount(EntryDetail)
    await flushPromises()
    expect(draftWrapper.text()).toContain('待关账封存')
    expect(draftWrapper.findAll('button').find(b => b.text().trim().startsWith('确认过账'))).toBeUndefined()
    draftWrapper.unmount()

    for (const [status, options, label, action] of [
      ['pending_review', { canApprove: true }, '复核并过账', 'approve'],
      ['draft', { canSubmit: true, canPost: false }, '提交复核', 'submit'],
    ] as const) {
      const entry = { ...draftEntry, voucherStatus: status }
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
      vi.clearAllMocks()
    }
  })

  it('shows drafts as pending close seal and navigates to closing-wizard instead of manual batch posting', async () => {
    const entries = Array.from({ length: 15 }, (_, i) => ({ id: `july-${i}`, title: `七月收入-${i}`, amount: 5, direction: 'income', occurDate: '2026-07-14', voucherStatus: 'draft' }))
    h.store = { activeEntries: [...entries, { id: 'old', title: '六月收入', occurDate: '2026-06-14', voucherStatus: 'draft' }], fetchEntries: vi.fn(async () => {}) }
    h.checks.mockResolvedValue({ checks: [{ code: 'VOUCHER_TO_SOURCE', records: entries.map(e => ({ entryId: e.id, title: e.title, date: e.occurDate, reasons: ['pending_post'] })) }] })

    const wrapper = mount(VoucherList)
    await flushPromises()
    expect(h.checks).toHaveBeenCalledWith('2026-07')
    expect(wrapper.text()).toContain('七月收入-14')
    expect(wrapper.text()).not.toContain('六月收入')
    expect(wrapper.text()).toContain('待关账封存')
    expect(wrapper.findAll('button').find(button => button.text().includes('一键过账全部'))).toBeUndefined()
    expect(wrapper.findAll('button').find(button => button.text().includes('批量过账所选'))).toBeUndefined()

    const goCloseBtn = wrapper.findAll('button').find(button => button.text().includes('前往月度结账'))
    expect(goCloseBtn).toBeDefined()
    expect(goCloseBtn!.text()).toContain('15 笔待封存')
    await goCloseBtn!.trigger('click')
    expect(h.push).toHaveBeenCalledWith({ path: '/closing-wizard', query: { month: '2026-07' } })

    wrapper.unmount()
  })

  it('filters the table by status and keyword', async () => {
    const entries = [
      { id: 'd-1', title: '草稿凭证', amount: 10, occurDate: '2026-07-01', voucherStatus: 'draft' },
      { id: 'p-1', title: '已过账凭证', amount: 20, occurDate: '2026-07-02', voucherStatus: 'posted', voucherRef: 'VOU-202607-000001' },
      { id: 'r-1', title: '已冲销凭证', amount: 30, occurDate: '2026-07-03', voucherStatus: 'reversed', voucherRef: 'VOU-202607-000002' },
    ]
    h.store = { activeEntries: entries, fetchEntries: vi.fn(async () => {}) }
    h.checks.mockResolvedValue({ checks: [] })

    const wrapper = mount(VoucherList)
    await flushPromises()
    const visibleRows = () => wrapper.findAll('tbody .n-data-table-tr').length
    expect(visibleRows()).toBe(3)

    // 状态筛选：已过账只剩一行
    const statusSelect = wrapper.findComponent(NSelect)
    await statusSelect.vm.$emit('update:value', 'posted')
    await flushPromises()
    expect(visibleRows()).toBe(1)
    expect(wrapper.text()).toContain('已过账凭证')
    expect(wrapper.text()).not.toContain('草稿凭证')

    // 关键词搜索：按凭证号命中（页面里有多个 NInput，须按 placeholder 定位搜索框）
    const searchInput = wrapper.findAllComponents(NInput)
      .find(c => c.props('placeholder') === '搜摘要 / 分类 / 凭证号')
    expect(searchInput).toBeDefined()
    await searchInput!.vm.$emit('update:value', 'VOU-202607-000002')
    await flushPromises()
    expect(visibleRows()).toBe(0)

    await statusSelect.vm.$emit('update:value', '')
    await flushPromises()
    expect(visibleRows()).toBe(1)
    expect(wrapper.text()).toContain('已冲销凭证')

    wrapper.unmount()
  })

  it('hides accounting voucher table before transaction is posted, and shows it after posting', async () => {
    const unpostedEntry = {
      id: 'draft',
      title: '银行流水待处理',
      amount: 100,
      direction: 'expense',
      category: '办公费',
      occurDate: '2026-07-15',
      voucherStatus: 'draft',
      voucherLines: {
        debit: [{ accountCode: '5602', accountName: '管理费用', amount: 100 }],
        credit: [{ accountCode: '1002', accountName: '银行存款', amount: 100 }],
      },
    }
    h.store = { activeEntries: [unpostedEntry], fetchEntries: vi.fn(async () => {}) }
    h.options.mockResolvedValue({ canPost: true })

    const wrapper = mount(EntryDetail)
    await flushPromises()

    // 未入账态：不渲染正式会计分录卡片与表格，只显示待入账引导区
    expect(wrapper.find('.voucher-card').exists()).toBe(false)
    expect(wrapper.find('.voucher-table').exists()).toBe(false)
    expect(wrapper.find('.unposted-guide-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('该交易尚未正式入账')

    // 模拟已过账数据
    const postedEntry = {
      ...unpostedEntry,
      voucherStatus: 'posted',
      voucherRef: 'VOU-202607-000008',
    }
    h.store = { activeEntries: [postedEntry], fetchEntries: vi.fn(async () => {}) }
    const postedWrapper = mount(EntryDetail)
    await flushPromises()

    // 已过账态：渲染正式会计分录表格及凭证号
    expect(postedWrapper.find('.unposted-guide-card').exists()).toBe(false)
    expect(postedWrapper.find('.voucher-card').exists()).toBe(true)
    expect(postedWrapper.find('.voucher-table').exists()).toBe(true)
    expect(postedWrapper.text()).toContain('VOU-202607-000008')
    expect(postedWrapper.find('.edit-section').exists()).toBe(false)

    wrapper.unmount()
    postedWrapper.unmount()
  })

  it('hides edit form and reverse button on posted reversal entries', async () => {
    const reversalEntry = {
      id: 'draft',
      title: '冲销：企业网银服务费',
      amount: -25,
      direction: 'expense',
      category: '银行及金融手续费',
      occurDate: '2026-10-02',
      voucherStatus: 'posted',
      voucherRef: 'VOU-202610-000030',
      entryType: 'reversal',
      isReversal: true,
      reversalOf: 'orig-1',
      voucherLines: {
        debit: [{ accountCode: '1002', accountName: '银行存款', amount: 25 }],
        credit: [{ accountCode: '5603002', accountName: '财务费用-手续费', amount: 25 }],
      },
    }
    h.store = { activeEntries: [reversalEntry], fetchEntries: vi.fn(async () => {}) }
    h.options.mockResolvedValue({ canPost: false, canReverse: true })

    const wrapper = mount(EntryDetail)
    await flushPromises()

    expect(wrapper.find('.edit-section').exists()).toBe(false)
    expect(wrapper.text()).toContain('冲销凭证')
    const reverseBtn = wrapper.find('.header-right').findAll('button').find(b => b.text().trim() === '冲销')
    expect(reverseBtn).toBeUndefined()

    wrapper.unmount()
  })
})
