/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 总分类账页（P7-S2 命名对齐 + P7-S1 并入整册口径）。
 *
 * 曾经页面标题叫「总分类账」，默认视图是科目汇总（其实是余额表）、
 * 下钻是单科目逐笔流水（其实是明细账）——会计一眼看出概念错误。
 * 现在默认视图必须是真总账：每科目 12 个月汇总行 + 本年合计；
 * 余额表与明细流水视图也全部走 /api/ledger/book 同一条取数路径。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import GeneralLedgerPage from '@/pages/report/general-ledger.vue'

const harness = vi.hoisted(() => ({
  get: vi.fn(),
  push: vi.fn(),
  message: { info: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: harness.push }),
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => harness.message,
}))

vi.mock('@/api/client', () => ({
  default: { get: (...args: unknown[]) => harness.get(...args) },
}))

function generalPage(overrides: Record<string, unknown> = {}) {
  const months = Array.from({ length: 12 }, (_, i) => ({
    month: `2026-${String(i + 1).padStart(2, '0')}`,
    debit: i === 5 ? '500.00' : '0.00',
    credit: i === 5 ? '200.00' : '0.00',
    balance: i < 5 ? '0.00' : '300.00',
    balanceAbs: i < 5 ? '0.00' : '300.00',
    balanceDirection: i < 5 ? 'flat' : 'debit',
    hasActivity: i === 5,
  }))
  return {
    accountCode: '1002',
    accountName: '银行存款',
    category: '资产',
    opening: '0.00',
    openingAbs: '0.00',
    openingDirection: 'flat',
    months,
    totalDebit: '500.00',
    totalCredit: '200.00',
    closing: '300.00',
    closingAbs: '300.00',
    closingDirection: 'debit',
    isParent: false,
    ...overrides,
  }
}

function balanceRow(overrides: Record<string, unknown> = {}) {
  return {
    accountCode: '1002',
    accountName: '银行存款',
    category: '资产',
    openingBalance: '0.00',
    openingAbs: '0.00',
    openingDirection: 'flat',
    debitTotal: '500.00',
    creditTotal: '200.00',
    balance: '300.00',
    closingBalance: '300.00',
    closingAbs: '300.00',
    closingDirection: 'debit',
    isParent: false,
    hasActivity: true,
    hasBalance: true,
    ...overrides,
  }
}

function detailPage(code = '1002') {
  return {
    code,
    name: '银行存款',
    category: '资产',
    opening: '0.00',
    openingAbs: '0.00',
    openingDirection: 'flat',
    lines: [
      {
        date: '2026-10-08',
        period: '2026-10-01',
        voucherRef: 'VOU-1',
        summary: '收客户款',
        debit: '500.00',
        credit: '0.00',
        balance: '500.00',
        balanceAbs: '500.00',
        balanceDirection: 'debit',
        counterparties: [{ code: '1122', name: '应收账款', amount: '500.00' }],
        backfill: false,
      },
      {
        date: '2026-10-09',
        period: '2026-10-01',
        voucherRef: 'VOU-2',
        summary: '付房租',
        debit: '0.00',
        credit: '200.00',
        balance: '300.00',
        balanceAbs: '300.00',
        balanceDirection: 'debit',
        counterparties: [],
        backfill: false,
      },
    ],
    periodTotals: { debit: '500.00', credit: '200.00' },
    yearTotals: { debit: '500.00', credit: '200.00' },
    closing: '300.00',
    closingAbs: '300.00',
    closingDirection: 'debit',
    lineCount: 2,
    hasQuantity: false,
    unit: null,
  }
}

function mockBook(generalItems: unknown[], balanceItems: unknown[] = []) {
  return {
    meta: { year: 2026 },
    balance: {
      items: balanceItems,
      totalDebit: '500.00',
      totalCredit: '200.00',
      isBalanced: true,
      hiddenZeroCount: 0,
      totalRows: balanceItems.length,
    },
    details: [],
    chronological: { items: [], voucherCount: 0, isBalanced: true },
    journals: [],
    general: { year: 2026, items: generalItems },
    pagination: { sections: [], totalPages: 1, pageSize: {} },
    warnings: [],
  }
}

async function mountPage() {
  const wrapper = mount(GeneralLedgerPage)
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  harness.get.mockReset()
  harness.get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/api/ledger/accounts') {
      return Promise.resolve({ accounts: [{ code: '1002', name: '银行存款' }] })
    }
    if (url === '/api/ledger/book') {
      const params = config?.params || {}
      const book = mockBook([generalPage()])
      if (params.scope === 'year') return Promise.resolve(book)
      if (params.account_code) {
        const page = detailPage(String(params.account_code))
        return Promise.resolve({ ...book, details: [page] })
      }
      if (params.show_zero_rows) {
        return Promise.resolve({ ...book, balance: { ...book.balance, items: [balanceRow()] } })
      }
      return Promise.resolve(book)
    }
    return Promise.resolve({})
  })
})

describe('总分类账页（P7-S2 命名对齐 + P7-S1 整册口径）', () => {
  it('默认渲染真总账：每科目 12 个月汇总行 + 本年合计，数据来自账簿 general 段', async () => {
    const wrapper = await mountPage()

    expect(harness.get).toHaveBeenCalledWith(
      '/api/ledger/book',
      expect.objectContaining({ params: expect.objectContaining({ scope: 'year' }) }),
    )
    const html = wrapper.html()
    expect(html).toContain('1002 银行存款')
    expect(html).toContain('年初')
    expect(html).toContain('2026-06')
    expect(html).toContain('本年合计')

    // 一个科目组 = 科目行 + 12 个月行 + 本年合计行
    const groups = wrapper.findAll('.gl-group')
    expect(groups).toHaveLength(1)
    expect(groups[0].findAll('tr')).toHaveLength(14)
  })

  it('总账行是月度汇总而不是逐笔流水：借方显示月合计 500，不含凭证摘要', async () => {
    const wrapper = await mountPage()
    const html = wrapper.html()
    expect(html).toContain('500.00')
    // 逐笔流水才会出现的凭证摘要/凭证号列不应出现在总账视图
    expect(html).not.toContain('摘要说明')
    expect(html).not.toContain('发生日期')
  })

  it('科目余额表视图走整册接口（show_zero_rows=1），字段映射保持不变', async () => {
    const wrapper = await mountPage()
    const tab = wrapper.findAll('button').find((item) => item.text() === '科目余额表')
    expect(tab).toBeTruthy()
    await tab!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(harness.get).toHaveBeenCalledWith(
      '/api/ledger/book',
      expect.objectContaining({
        params: expect.objectContaining({ show_zero_rows: 1, month: expect.any(String) }),
      }),
    )
    const html = wrapper.html()
    expect(html).toContain('科目编码')
    expect(html).toContain('期初余额')
    expect(html).toContain('银行存款')
    // 汇总行来自 balance 段的 totalDebit/totalCredit
    expect(html).toContain('500.00')
  })

  it('余额表行钻取明细流水：走整册接口单科目模式，展示逐笔行与真实期初', async () => {
    const wrapper = await mountPage()
    const tab = wrapper.findAll('button').find((item) => item.text() === '科目余额表')
    await tab!.trigger('click')
    await flushPromises()
    const row = wrapper.find('.summary-row')
    await row.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(harness.get).toHaveBeenCalledWith(
      '/api/ledger/book',
      expect.objectContaining({
        params: expect.objectContaining({ account_code: '1002' }),
      }),
    )
    const html = wrapper.html()
    expect(html).toContain('明细流水')
    expect(html).toContain('收客户款')
    expect(html).toContain('500.00')
    // 期初来自 details[0].opening，不再从首行倒推
    expect(html).toContain('期初余额')
  })

  it('容器科目在总账里标注子级小计', async () => {
    harness.get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/api/ledger/accounts') {
        return Promise.resolve({
          accounts: [
            { code: '1002', name: '银行存款' },
            { code: '2211', name: '应付职工薪酬' },
          ],
        })
      }
      if (url === '/api/ledger/book') {
        const params = config?.params || {}
        const book = mockBook([generalPage(), generalPage({ accountCode: '2211', accountName: '应付职工薪酬', isParent: true })])
        if (params.scope === 'year') return Promise.resolve(book)
        if (params.account_code) {
          return Promise.resolve({ ...book, details: [detailPage(String(params.account_code))] })
        }
        return Promise.resolve(book)
      }
      return Promise.resolve({})
    })

    const wrapper = await mountPage()
    expect(wrapper.html()).toContain('子级小计')
  })

  it('年度无发生额时给出明确空态', async () => {
    harness.get.mockImplementation((url: string) => {
      if (url === '/api/ledger/accounts') {
        return Promise.resolve({ accounts: [] })
      }
      if (url === '/api/ledger/book') {
        return Promise.resolve(mockBook([]))
      }
      return Promise.resolve({})
    })
    const wrapper = await mountPage()
    expect(wrapper.html()).toContain('本年度无科目发生额')
  })
})
