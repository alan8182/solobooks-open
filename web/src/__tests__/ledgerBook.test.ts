/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * 电子账簿账页渲染：三栏式要素、余额方向、0 行过滤、警告条。
 * 这些是"打印件能不能当账簿用"的判定点，改版面时最容易悄悄回退。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { LedgerBook } from '@/api/ledger'
import BookPage from '@/pages/ledger/book.vue'

const harness = vi.hoisted(() => ({
  book: null as LedgerBook | null,
  routeQuery: {} as Record<string, string>,
  issuedItems: [] as any[],
  getLedgerBook: vi.fn(),
  back: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  exportExcel: vi.fn(),
  getLedgerBookSnapshot: vi.fn(),
  diffLedgerBookSnapshots: vi.fn(),
}))

vi.mock('@/api/ledger', () => ({
  getLedgerBook: (...args: unknown[]) => harness.getLedgerBook(...args),
  listLedgerBookSnapshots: () => Promise.resolve({ items: harness.issuedItems }),
  getLedgerBookSnapshot: vi.fn(),
  issueLedgerBookSnapshot: vi.fn(),
  voidLedgerBookSnapshot: vi.fn(),
}))

vi.mock('@/utils/export', () => ({
  exportLedgerBookToExcel: (...args: unknown[]) => harness.exportExcel(...args),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: harness.routeQuery }),
  useRouter: () => ({ back: harness.back, push: vi.fn() }),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ user: { nickname: '张三' } }),
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => harness.message,
}))

function makeBook(overrides: Partial<LedgerBook> = {}): LedgerBook {
  return {
    meta: {
      companyName: '杭州芥子星辰智能科技有限公司',
      month: '2026-10',
      year: null,
      scope: 'month',
      periodStart: '2026-10-01',
      periodEnd: '2026-10-31',
      periodStatus: 'open',
      periodStatusLabel: '未结账',
      units: '元',
      accountingStandard: '小企业会计准则',
      entryCount: 2,
      journalBasis: 'occurred_on',
      generatedAt: null,
      generatedBy: null,
      snapshotId: null,
      bookNo: null,
      contentHash: null,
      pageCount: null,
    },
    balance: {
      totalRows: 3,
      items: [
        {
          accountCode: '1002',
          accountName: '银行存款',
          category: '资产类',
          openingBalance: '100000.44',
          openingAbs: '100000.44',
          openingDirection: 'debit',
          debitTotal: '82.20',
          creditTotal: '800.74',
          balance: '-718.54',
          closingBalance: '99281.90',
          closingAbs: '99281.90',
          closingDirection: 'debit',
          isParent: false,
          hasActivity: true,
          hasBalance: true,
        },
        {
          accountCode: '2211',
          accountName: '应付职工薪酬',
          category: '负债类',
          openingBalance: '0.00',
          openingAbs: '0.00',
          openingDirection: 'flat',
          debitTotal: '0.00',
          creditTotal: '21400.00',
          balance: '-21400.00',
          closingBalance: '-21400.00',
          closingAbs: '21400.00',
          closingDirection: 'credit',
          isParent: true,
          hasActivity: true,
          hasBalance: true,
        },
        {
          accountCode: '5301',
          accountName: '营业外收入',
          category: '损益-收入类',
          openingBalance: '0.00',
          openingAbs: '0.00',
          openingDirection: 'flat',
          debitTotal: '0.00',
          creditTotal: '0.00',
          balance: '0.00',
          closingBalance: '0.00',
          closingAbs: '0.00',
          closingDirection: 'flat',
          isParent: false,
          hasActivity: false,
          hasBalance: false,
        },
      ],
      totalDebit: '82.20',
      totalCredit: '800.74',
      isBalanced: false,
      openingDebitTotal: '100000.44',
      openingCreditTotal: '100000.44',
      closingDebitTotal: '99281.90',
      closingCreditTotal: '21400.00',
      isOpeningBalanced: true,
      isClosingBalanced: false,
      hiddenZeroCount: 1,
    },
    details: [
      {
        code: '1002',
        name: '银行存款',
        category: '资产类',
        opening: '100000.44',
        openingAbs: '100000.44',
        openingDirection: 'debit',
        lines: [
          {
            date: '2026-10-03',
            period: '2026-10-01',
            voucherRef: '记-0007',
            summary: '支付房租',
            debit: '0.00',
            credit: '800.74',
            balance: '99200.44',
            balanceAbs: '99200.44',
            balanceDirection: 'debit',
            counterparties: [{ code: '5602013', name: '管理费用-租赁费', amount: '800.74' }],
            backfill: false,
          },
          {
            date: '2026-10-08',
            period: '2026-10-01',
            voucherRef: '记-0008',
            summary: '利息收入',
            debit: '82.20',
            credit: '0.00',
            balance: '99281.90',
            balanceAbs: '99281.90',
            balanceDirection: 'debit',
            counterparties: [{ code: '5603005', name: '财务费用-利息收入', amount: '82.20' }],
            backfill: false,
          },
        ],
        periodTotals: { debit: '82.20', credit: '800.74' },
        yearTotals: { debit: '1082.20', credit: '1800.74' },
        closing: '99281.90',
        closingAbs: '99281.90',
        closingDirection: 'debit',
        lineCount: 2,
        hasQuantity: false,
        unit: null,
        quantityOpening: '0',
      },
      {
        code: '221101',
        name: '应付职工薪酬-工资',
        category: '负债类',
        opening: '0.00',
        openingAbs: '0.00',
        openingDirection: 'flat',
        lines: [
          {
            date: '2026-10-31',
            period: '2026-10-01',
            voucherRef: '记-0012',
            summary: '计提工资',
            debit: '0.00',
            credit: '14800.00',
            balance: '-14800.00',
            balanceAbs: '14800.00',
            balanceDirection: 'credit',
            counterparties: [{ code: '5602001', name: '管理人员职工薪酬', amount: '14800.00' }],
            backfill: false,
          },
        ],
        periodTotals: { debit: '0.00', credit: '14800.00' },
        yearTotals: { debit: '0.00', credit: '14800.00' },
        closing: '-14800.00',
        closingAbs: '14800.00',
        closingDirection: 'credit',
        lineCount: 1,
        hasQuantity: false,
        unit: null,
        quantityOpening: '0',
      },
    ],
    chronological: {
      items: [
        {
          entryId: 'e1',
          voucherRef: '记-0007',
          period: '2026-10-01',
          date: '2026-10-03',
          summary: '支付房租',
          accountCode: '5602013',
          accountName: '管理费用-租赁费',
          direction: 'debit',
          debit: '800.74',
          credit: '0.00',
          backfill: false,
        },
      ],
      voucherCount: 1,
      totalDebit: '800.74',
      totalCredit: '800.74',
      isBalanced: true,
    },
    journals: [
      {
        kind: 'cash',
        code: '1001',
        name: '现金日记账',
        accountName: '库存现金',
        opening: '0.00',
        openingAbs: '0.00',
        openingDirection: 'flat',
        rows: [],
        closing: '0.00',
        closingAbs: '0.00',
        closingDirection: 'flat',
        totalDebit: '0.00',
        totalCredit: '0.00',
      },
      {
        kind: 'bank',
        code: '1002',
        name: '银行存款日记账',
        accountName: '银行存款',
        opening: '100000.44',
        openingAbs: '100000.44',
        openingDirection: 'debit',
        rows: [
          {
            date: '2026-10-03',
            summary: '本日合计',
            debit: '0.00',
            credit: '800.74',
            balance: '99200.44',
            balanceAbs: '99200.44',
            balanceDirection: 'debit',
            voucherCount: 1,
            backfill: false,
          },
        ],
        closing: '99200.44',
        closingAbs: '99200.44',
        closingDirection: 'debit',
        totalDebit: '0.00',
        totalCredit: '800.74',
      },
    ],
    general: null,
    pagination: {
      sections: [
        { key: 'cover', kind: 'cover', title: '封面', pageNo: 1, pageCount: 1 },
        { key: 'toc', kind: 'toc', title: '目录', pageNo: 2, pageCount: 1 },
        { key: 'balance', kind: 'balance', title: '科目余额表', pageNo: 3, pageCount: 1 },
        { key: 'detail:1002', kind: 'detail', title: '1002 银行存款 明细账', pageNo: 4, pageCount: 1, code: '1002' },
        { key: 'detail:221101', kind: 'detail', title: '221101 应付职工薪酬-工资 明细账', pageNo: 5, pageCount: 1, code: '221101' },
        { key: 'journal:bank', kind: 'journal', title: '银行存款日记账', pageNo: 6, pageCount: 1, kind_code: 'bank' },
        { key: 'chronological', kind: 'chronological', title: '序时账', pageNo: 7, pageCount: 1 },
        { key: 'signoff', kind: 'signoff', title: '落款', pageNo: 8, pageCount: 1 },
      ],
      totalPages: 8,
      pageSize: { balance: 24, detail: 22, journal: 24, chronological: 24 },
    },
    warnings: [
      {
        code: 'PERIOD_NOT_CLOSED',
        message: '本期尚未结账，账簿会随后续补记变化，不能作为正式账簿归档',
      },
    ],
    ...overrides,
  }
}

async function mountBook(
  book: LedgerBook = makeBook(),
  query: Record<string, string> = {},
  issued: any[] = [],
) {
  harness.book = book
  harness.routeQuery = query
  harness.issuedItems = issued
  // 服务端按 show_zero_rows 决定余额表口径（页码与渲染页数必须一致，
  // 2026-10-20 真机打印抓到的就是前端二次过滤导致的页码跳号）
  harness.getLedgerBook.mockImplementation((params?: { showZeroRows?: boolean }) => {
    const rows = params?.showZeroRows
      ? book.balance.items
      : book.balance.items.filter((row) => row.hasActivity || row.hasBalance)
    return Promise.resolve({ ...book, balance: { ...book.balance, items: rows } })
  })
  const wrapper = mount(BookPage)
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.clearAllMocks()
})

/** 状态文案里也含「签发正式账簿」四个字，不能靠字符串包含判断按钮是否存在 */
function hasIssueButton(wrapper: ReturnType<typeof mount>): boolean {
  return wrapper.findAll('button').some((item) => item.text().trim() === '签发正式账簿')
}

function issuedItem(hash: string, bookNo = '2026-10-0001') {
  return {
    id: `snap-${bookNo}`,
    bookKind: 'monthly',
    period: '2026-10',
    bookNo,
    status: 'issued',
    contentHash: hash,
    pageCount: 8,
    lineCount: 3,
    anchor: {},
    generatedBy: '张三',
    generatedAt: '2026-10-20T13:00:00+00:00',
    voidReason: null,
    voidedBy: null,
    voidedAt: null,
  }
}

describe('电子账簿账页', () => {
  it('明细账具备三栏式要素：借或贷列、对方科目、本期合计、本年累计', async () => {
    const wrapper = await mountBook()
    const html = wrapper.html()

    expect(html).toContain('借或贷')
    expect(html).toContain('对方科目')
    expect(html).toContain('本期合计（2 笔）')
    expect(html).toContain('本年累计')
    expect(html).toContain('5602013 管理费用-租赁费')
  })

  it('贷余科目显示「贷」+ 正数，不出现负号', async () => {
    const wrapper = await mountBook()
    const html = wrapper.html()

    expect(html).toContain('贷')
    expect(html).toContain('14,800.00')
    expect(html).not.toContain('-14,800.00')
    expect(html).not.toContain('¥')
  })

  it('默认隐藏零发生额科目，开关打开后显示', async () => {
    const wrapper = await mountBook()

    expect(wrapper.html()).not.toContain('营业外收入')
    const toggle = wrapper.find('.zero-toggle input')
    expect(toggle.exists()).toBe(true)
    await toggle.setValue(true)
    await flushPromises()
    await flushPromises()
    // 开关要重新向服务端要整册，而不是本地过滤
    expect(
      harness.getLedgerBook.mock.calls.some(([params]) => params?.showZeroRows === true),
    ).toBe(true)
    expect(wrapper.html()).toContain('营业外收入')
  })

  it('展示未结账警告，并给出借贷不平提示', async () => {
    const wrapper = await mountBook()
    const html = wrapper.html()

    expect(html).toContain('不能作为正式账簿归档')
    expect(html).toContain('本期借方发生额与贷方发生额不相等')
  })

  it('日记账只渲染有数据的册，且日结存带方向', async () => {
    const wrapper = await mountBook()
    const html = wrapper.html()

    expect(html).toContain('银行存款日记账')
    expect(html).not.toContain('现金日记账')
    expect(html).toContain('99,200.44')
  })

  it('年度册渲染总账 12 个月与本年合计', async () => {
    const yearBook = makeBook({
      meta: { ...makeBook().meta, scope: 'year', month: null, year: 2026 },
      general: {
        year: 2026,
        items: [
          {
            accountCode: '5602001',
            accountName: '管理人员职工薪酬',
            category: '损益-费用类',
            opening: '0.00',
            openingAbs: '0.00',
            openingDirection: 'flat',
            months: Array.from({ length: 12 }, (_, index) => ({
              month: `2026-${String(index + 1).padStart(2, '0')}`,
              debit: index === 9 ? '20000.00' : '0.00',
              credit: '0.00',
              balance: index === 9 ? '20000.00' : '0.00',
              balanceAbs: index === 9 ? '20000.00' : '0.00',
              balanceDirection: index === 9 ? 'debit' : 'flat',
              hasActivity: index === 9,
            })),
            totalDebit: '20000.00',
            totalCredit: '0.00',
            closing: '20000.00',
            closingAbs: '20000.00',
            closingDirection: 'debit',
            isParent: false,
          },
        ],
      },
    })
    const wrapper = await mountBook(yearBook, { scope: 'year' })
    const html = wrapper.html()

    expect(html).toContain('总　账')
    expect(html).toContain('2026 年度')
    expect(html).toContain('本年合计')
    expect(html).toContain('2026-10')
    expect(html).toContain('20,000.00')
    // F6 年度册明细账按月分节：每物理页标题带该页首行月份
    expect(html).toContain('2026-10 · 1002 银行存款 明细账')
  })

  it('月度册明细账标题不带月份前缀（F6 仅年度册生效）', async () => {
    const wrapper = await mountBook()
    expect(wrapper.html()).not.toContain('2026-10 · 1002 银行存款 明细账')
  })

  it('已结账未签发时给出签发入口', async () => {
    const closed = makeBook({ meta: { ...makeBook().meta, periodStatus: 'closed', periodStatusLabel: '已结账' } })
    const wrapper = await mountBook(closed)
    expect(hasIssueButton(wrapper)).toBe(true)
  })

  it('未结账只标预览件，不给签发入口', async () => {
    const wrapper = await mountBook(makeBook({ meta: { ...makeBook().meta, periodStatus: 'open' } }))
    const html = wrapper.html()
    expect(html).toContain('预览件')
    expect(hasIssueButton(wrapper)).toBe(false)
  })

  it('已签发且内容一致时显示账簿编号，不再给签发入口', async () => {
    const book = makeBook({
      meta: {
        ...makeBook().meta,
        periodStatus: 'closed',
        contentHash: 'deadbeefdeadbeefdeadbeef',
        bookNo: '2026-10-0001',
      },
    })
    const wrapper = await mountBook(book, {}, [issuedItem('deadbeefdeadbeefdeadbeef')])
    const html = wrapper.html()
    expect(html).toContain('2026-10-0001')
    expect(html).toContain('已签发')
    expect(hasIssueButton(wrapper)).toBe(false)
  })

  it('已签发但内容变了：标记账簿已过期并允许重新签发', async () => {
    const book = makeBook({
      meta: { ...makeBook().meta, periodStatus: 'closed', contentHash: 'newhashnewhashnewhash' },
    })
    const wrapper = await mountBook(book, {}, [issuedItem('oldhasholdhasholdhash')])
    const html = wrapper.html()
    expect(html).toContain('账簿已过期')
    expect(hasIssueButton(wrapper)).toBe(true)
  })

  it('同期间有多册签发时，历史列表提供「与上一册比对」入口', async () => {
    const wrapper = await mountBook(
      makeBook(),
      {},
      [issuedItem('h2222222222222222', '2026-10-0002'), issuedItem('h1111111111111111', '2026-10-0001')],
    )
    // 比对逻辑本身由后端 tests/test_ledger_book_snapshot.py 覆盖，
    // 这里只保证入口在"存在上一册"时才出现（只有一册时不该有比对按钮）
    expect(wrapper.html()).toContain('与上一册比对')

    const single = await mountBook(makeBook(), {}, [issuedItem('h1111111111111111', '2026-10-0001')])
    expect(single.html()).not.toContain('与上一册比对')
  })

  it('导出前把编制人写进 payload，保证导出的和页面上的一致', async () => {
    const wrapper = await mountBook()
    const exportButton = wrapper.findAll('button').find((item) => item.text().includes('导出 Excel'))
    expect(exportButton).toBeTruthy()
    await exportButton!.trigger('click')

    expect(harness.exportExcel).toHaveBeenCalledTimes(1)
    const payload = harness.exportExcel.mock.calls[0][0] as LedgerBook
    expect(payload.meta.generatedBy).toBe('张三')
  })
})
