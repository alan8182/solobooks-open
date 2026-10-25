/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 明细账页（P7-S1 并入整册口径）。
 *
 * 数据源从旧 /api/ledger/sub-ledger 迁到 /api/ledger/book?account_code=：
 * 粒度从"按凭证聚合行"变为"按凭证行"（与电子账簿明细页一致），
 * 数量科目的单笔移动按借贷方向拆进收入/发出数量列。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SubLedgerPage from '@/pages/report/sub-ledger.vue'

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

function bookWithDetail() {
  return {
    meta: { month: '2026-10', scope: 'month' },
    balance: { items: [], totalDebit: '0.00', totalCredit: '0.00', isBalanced: true, hiddenZeroCount: 0, totalRows: 0 },
    details: [
      {
        code: '1402',
        name: '库存商品',
        category: '资产',
        opening: '1000.00',
        openingAbs: '1000.00',
        openingDirection: 'debit',
        lines: [
          {
            date: '2026-10-05',
            period: '2026-10-01',
            voucherRef: 'VOU-1',
            summary: '采购入库',
            debit: '300.00',
            credit: '0.00',
            balance: '1300.00',
            balanceAbs: '1300.00',
            balanceDirection: 'debit',
            counterparties: [{ code: '2202', name: '应付账款', amount: '300.00' }],
            backfill: false,
            quantity: '10.00',
            unitPrice: '30.00',
            unit: '件',
          },
          {
            date: '2026-10-06',
            period: '2026-10-01',
            voucherRef: 'VOU-2',
            summary: '销售出库',
            debit: '0.00',
            credit: '150.00',
            balance: '1150.00',
            balanceAbs: '1150.00',
            balanceDirection: 'debit',
            counterparties: [],
            backfill: false,
            quantity: '5.00',
            unitPrice: '30.00',
            unit: '件',
          },
          {
            date: '2026-10-08',
            period: '2026-10-01',
            voucherRef: 'VOU-3',
            summary: '期初结转（补记）',
            debit: '0.00',
            credit: '0.00',
            balance: '1150.00',
            balanceAbs: '1150.00',
            balanceDirection: 'debit',
            counterparties: [],
            backfill: true,
          },
        ],
        periodTotals: { debit: '300.00', credit: '150.00' },
        yearTotals: { debit: '300.00', credit: '150.00' },
        closing: '1150.00',
        closingAbs: '1150.00',
        closingDirection: 'debit',
        lineCount: 3,
        hasQuantity: true,
        unit: '件',
      },
    ],
    chronological: { items: [], voucherCount: 0, isBalanced: true },
    journals: [],
    general: null,
    pagination: { sections: [], totalPages: 1, pageSize: {} },
    warnings: [],
  }
}

beforeEach(() => {
  harness.get.mockReset()
  harness.get.mockImplementation((url: string) => {
    if (url === '/api/ledger/accounts') {
      return Promise.resolve({ accounts: [{ code: '1402', name: '库存商品' }] })
    }
    if (url === '/api/ledger/book') {
      return Promise.resolve(bookWithDetail())
    }
    return Promise.resolve({})
  })
})

async function mountPage() {
  const wrapper = mount(SubLedgerPage)
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('明细账页（P7-S1 整册口径迁移）', () => {
  it('走整册接口单科目模式，逐笔行含对方科目与补记标记', async () => {
    const wrapper = await mountPage()
    const html = wrapper.html()

    expect(harness.get).toHaveBeenCalledWith(
      '/api/ledger/book',
      expect.objectContaining({
        params: expect.objectContaining({ account_code: '1122', month: expect.any(String) }),
      }),
    )
    expect(html).toContain('采购入库')
    expect(html).toContain('期初结转（补记）')
    expect(html).toContain('2202 应付账款')
  })

  it('数量科目：借方向拆进收入数量、贷方向拆进发出数量，单位跟随', async () => {
    const wrapper = await mountPage()
    const rows = wrapper.findAll('tbody tr')
    expect(rows.length).toBeGreaterThanOrEqual(3)
    const texts = rows.map((row) => row.text())
    expect(texts[0]).toContain('10.00件')
    expect(texts[1]).toContain('5.00件')
    expect(texts[2]).not.toContain('10.00件')
  })

  it('汇总卡显示本期借贷发生与期末余额（来自账簿页合计）', async () => {
    const wrapper = await mountPage()
    const html = wrapper.html()
    expect(html).toContain('300.00')
    expect(html).toContain('150.00')
    expect(html).toContain('1,150.00')
  })
})
