/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import type { VoucherBook } from '@/api/voucherBook'
import { FLIP_DURATION_MS } from '@/utils/voucherBook'
import VoucherBookViewer from '@/components/business/VoucherBookViewer.vue'
import VoucherSheet from '@/components/business/VoucherSheet.vue'

const payload: VoucherBook = {
  meta: {
    companyName: '示例采购有限公司',
    month: '2026-10',
    periodStart: '2026-10-01',
    periodEnd: '2026-10-31',
    periodStatus: 'closed',
    voucherCount: 1,
    attachmentTotal: 0,
    xmlOriginalTotal: 0,
    debitTotal: '106.00',
    contentHash: 'a'.repeat(64),
  },
  pagination: {
    sections: [
      { key: 'cover', title: '凭证册封面', pages: 1 },
      { key: 'vouchers', title: '记账凭证', pages: 1 },
      { key: 'originals', title: '原件归档目录', pages: 1 },
      { key: 'signoff', title: '装订签章', pages: 1 },
    ],
    totalPages: 4,
    originalPages: 1,
  },
  vouchers: [
    {
      entryId: 'e-1',
      voucherNo: '记-0001',
      voucherSeq: '1',
      date: '2026-10-03',
      period: '2026-10-01',
      title: '支付房租',
      backfill: false,
      lines: [
        {
          summary: '支付房租',
          code: '5602013',
          name: '管理费用-租赁费',
          auxiliaryText: '',
          side: 'debit',
          amount: '106.00',
          quantity: '',
          unit: '',
          unitPrice: '',
        },
        {
          summary: '支付房租',
          code: '1002',
          name: '银行存款',
          auxiliaryText: '',
          side: 'credit',
          amount: '106.00',
          quantity: '',
          unit: '',
          unitPrice: '',
        },
      ],
      blankRows: 4,
      debitTotal: '106.00',
      creditTotal: '106.00',
      capitalAmount: '壹佰零陆圆整',
      attachmentCount: 0,
      invoiceNumbers: [],
      attachments: [],
    },
  ],
  originals: [],
  originalsMissing: [],
}

function mountViewer(props: Record<string, unknown> = {}) {
  return mount(VoucherBookViewer, {
    props: { show: true, payload, signed: true, bookNo: '2026-10-0001', ...props },
    attachTo: document.body,
    global: {
      stubs: {
        VoucherSheet: { template: '<div class="voucher-sheet-stub">记 账 凭 证</div>' },
        InvoiceReproSheet: { template: '<div class="invoice-stub" />' },
      },
    },
  })
}

function bodyText(): string {
  return document.body.textContent || ''
}

function indicator(): string {
  return document.body.querySelector('[data-testid="book-page-indicator"]')?.textContent?.trim() || ''
}

function clickTestId(testId: string) {
  ;(document.body.querySelector(`[data-testid="${testId}"]`) as HTMLElement | null)?.click()
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('VoucherBookViewer 仿真册阅读器', () => {
  it('打开即封面页，页码取分页真值', async () => {
    const wrapper = mountViewer()
    await nextTick()
    expect(bodyText()).toContain('凭　证　封　面')
    expect(bodyText()).toContain('示例采购有限公司')
    expect(indicator()).toContain('第 1 / 4 页')
    wrapper.unmount()
  })

  it('下一页/上一页按钮触发左装订轴 3D 翻页，动画结束后页码前进/后退', async () => {
    vi.useFakeTimers()
    const wrapper = mountViewer()
    await nextTick()
    clickTestId('book-next')
    await nextTick()
    // 真实卷页平铺裁剪层与镜像纸背同时渲染，页码尚未提交
    await vi.advanceTimersByTimeAsync(150)
    expect(document.body.querySelector('.flip-leaf')).not.toBeNull()
    expect(document.body.querySelector('.curl-flat-layer')).not.toBeNull()
    expect(document.body.querySelector('.curl-back-sheet')).not.toBeNull()
    await vi.advanceTimersByTimeAsync(FLIP_DURATION_MS)
    await nextTick()
    expect(indicator()).toContain('第 2 / 4 页')
    expect(document.body.querySelector('.flip-leaf')).toBeNull()
    clickTestId('book-prev')
    await nextTick()
    await vi.advanceTimersByTimeAsync(150)
    expect(document.body.querySelector('.flip-leaf')).not.toBeNull()
    expect(document.body.querySelector('.curl-back-sheet')).not.toBeNull()
    await vi.advanceTimersByTimeAsync(FLIP_DURATION_MS)
    await nextTick()
    expect(indicator()).toContain('第 1 / 4 页')
    wrapper.unmount()
  })

  it('封面页使用牛皮纸封面与装订包脊，内页使用象牙纸与三孔装订线', async () => {
    const wrapper = mountViewer()
    await nextTick()
    expect(document.body.querySelector('.page-layer.is-cover')).not.toBeNull()
    expect(document.body.querySelector('.cover-spine-strip')).not.toBeNull()
    expect(bodyText()).toContain('电子归档')

    clickTestId('book-tab-signoff')
    await nextTick()
    expect(indicator()).toContain('第 4 / 4 页')
    expect(document.body.querySelector('.page-layer.is-cover')).toBeNull()
    expect(document.body.querySelector('.inner-binding')).not.toBeNull()
    wrapper.unmount()
  })

  it('连击下一页不丢单：立即提交进行中的翻页并切入下一页', async () => {
    vi.useFakeTimers()
    const wrapper = mountViewer()
    await nextTick()
    clickTestId('book-next')
    await nextTick()
    await vi.advanceTimersByTimeAsync(80)
    // 在第一次翻页未结束时快速连击下一页
    clickTestId('book-next')
    await nextTick()
    expect(indicator()).toContain('第 2 / 4 页')
    await vi.advanceTimersByTimeAsync(FLIP_DURATION_MS + 50)
    await nextTick()
    expect(indicator()).toContain('第 3 / 4 页')
    wrapper.unmount()
  })

  it('页码跳转输入并回车定位到目标页（越界夹取）', async () => {
    const wrapper = mountViewer()
    await nextTick()
    const input = document.body.querySelector('[data-testid="book-jump"] input') as HTMLInputElement | null
    expect(input).not.toBeNull()
    input!.value = '9'
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    input!.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
    await nextTick()
    expect(indicator()).toContain('第 4 / 4 页')
    wrapper.unmount()
  })

  it('Escape 关闭并 emit update:show=false', async () => {
    const wrapper = mountViewer()
    await nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('update:show')?.[0]).toEqual([false])
    wrapper.unmount()
  })

  it('自动翻页按间隔前进，到末页自动停止', async () => {
    vi.useFakeTimers()
    const wrapper = mountViewer()
    await nextTick()
    const switchEl = document.body.querySelector('[role="switch"]') as HTMLElement | null
    expect(switchEl).not.toBeNull()
    switchEl!.click()
    await nextTick()
    expect(indicator()).toContain('第 1 / 4 页')
    await vi.advanceTimersByTimeAsync(5000 + 16 + FLIP_DURATION_MS)
    await nextTick()
    expect(indicator()).toContain('第 2 / 4 页')
    await vi.advanceTimersByTimeAsync((5000 + 16 + FLIP_DURATION_MS) * 3)
    await nextTick()
    expect(indicator()).toContain('第 4 / 4 页')
    wrapper.unmount()
  })

  it('未签发 payload 显示预览件水印', async () => {
    const wrapper = mountViewer({ signed: false, bookNo: '' })
    await nextTick()
    expect(bodyText()).toContain('预览件 · 未签发')
    expect(bodyText()).not.toContain('电子归档')
    wrapper.unmount()
  })

  it('无凭证月份时禁用凭证侧边索引标签', async () => {
    const emptyVoucherPayload: VoucherBook = {
      ...payload,
      vouchers: [],
      pagination: {
        sections: [
          { key: 'cover', title: '凭证册封面', pages: 1 },
          { key: 'originals', title: '原件归档目录', pages: 1 },
          { key: 'signoff', title: '装订签章', pages: 1 },
        ],
        totalPages: 3,
        originalPages: 1,
      },
    }
    const wrapper = mountViewer({ payload: emptyVoucherPayload })
    await nextTick()
    const voucherTab = document.body.querySelector('[data-testid="book-tab-vouchers"]') as HTMLButtonElement | null
    expect(voucherTab?.disabled).toBe(true)
    wrapper.unmount()
  })

  it('VoucherSheet bookMode 开启 .is-book 与完整空白行', () => {
    const wrapper = mount(VoucherSheet, {
      props: {
        entry: {
          title: '报销垫付款',
          voucherRef: '记-0001',
          occurDate: '2026-10-03',
          voucherLines: {
            debit: [{ code: '5602', name: '管理费用', amount: 300, side: 'debit' }],
            credit: [{ code: '1002', name: '银行存款', amount: 300, side: 'credit' }],
          },
        },
        companyName: '示例采购有限公司',
        bookMode: true,
      },
    })
    expect(wrapper.find('.voucher-sheet.is-book').exists()).toBe(true)
    expect(wrapper.findAll('.blank-row')).toHaveLength(4)
    wrapper.unmount()
  })
})
