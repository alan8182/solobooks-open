/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 税费种认定页：只锁三条用户可见的契约
//  ① 没有认定行时给出「同步 / 带出」两条出路，不能是空白页；
//  ② 有认定行时明细表出现「归属申报表」，说明附加税被并进增值税申报表；
//  ③ 申报节奏预览按截止月份分桶，点税种跳税报页。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const harness = vi.hoisted(() => ({
  push: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  dialogInfo: vi.fn(),
  dialogWarning: vi.fn(),
  list: vi.fn(),
  preview: vi.fn(),
  diff: vi.fn(),
  importRows: vi.fn(),
  createRow: vi.fn(),
  updateRow: vi.fn(),
  deactivateRow: vi.fn(),
  template: vi.fn(),
  apply: vi.fn(),
  recognize: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: harness.push }) }))

vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useMessage: () => ({
    success: harness.success,
    error: harness.error,
    warning: harness.warning,
    info: vi.fn(),
  }),
  useDialog: () => ({
    info: harness.dialogInfo,
    warning: harness.dialogWarning,
    error: vi.fn(),
  }),
}))

vi.mock('@/api/tax-registration', () => ({
  listTaxRegistrations: harness.list,
  previewTaxSchedule: harness.preview,
  diffTaxSchedule: harness.diff,
  importTaxRegistrations: harness.importRows,
  createTaxRegistration: harness.createRow,
  updateTaxRegistration: harness.updateRow,
  deactivateTaxRegistration: harness.deactivateRow,
  buildTaxRegistrationTemplate: harness.template,
  applyTaxSchedule: harness.apply,
  recognizeTaxRegistration: harness.recognize,
}))

const Page = (await import('@/pages/tax-registration/index.vue')).default

function registrationRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'reg-1',
    seq: 1,
    levyItem: '增值税',
    levyCategory: '商业(3%)',
    levySubItem: '',
    filingCycle: 'quarter',
    filingCycleText: '季',
    deadlineText: '期满之日起15日内',
    deadlineRule: 'quarter_15',
    deadlineRuleLabel: '期满之日起 15 日内（季末次月 15 日）',
    dueMonthOffset: null,
    dueDay: null,
    levyRate: 0.03,
    taxRate: 0.13,
    rateUnit: '',
    mainFlag: 'main',
    collectionMethod: 'self',
    returnGroup: 'vat',
    returnGroupLabel: '增值税及附加税费申报表',
    status: 'active',
    source: 'paste',
    note: '',
    ...overrides,
  }
}

function emptyPreview() {
  return {
    year: new Date().getFullYear(),
    source: 'default' as const,
    cycles: { vat: 'quarter', business_income_iit_prepay: 'quarter' },
    conflicts: [],
    summary: { itemCount: 0, groupCount: 0, periodCount: 0 },
    groups: [],
    byPeriod: [],
    byDueMonth: [],
  }
}

function previewWith(period: string, dueDate: string, month: string) {
  return {
    ...emptyPreview(),
    source: 'registration' as const,
    summary: { itemCount: 2, groupCount: 2, periodCount: 1 },
    byPeriod: [
      {
        period,
        dueDate,
        items: [
          { returnGroup: 'vat', name: '增值税及附加税费申报表', dueDate, status: 'pending', deadlineId: 'd1', daysLeft: 3 },
          { returnGroup: 'stamp_duty', name: '财产和行为税纳税申报表', dueDate, status: 'filed', deadlineId: 'd2', daysLeft: 3 },
        ],
      },
    ],
    byDueMonth: [
      {
        month,
        items: [
          { returnGroup: 'vat', name: '增值税及附加税费申报表', period, cycle: 'quarter', dueDate, status: 'pending', deadlineId: 'd1', daysLeft: 3 },
          { returnGroup: 'stamp_duty', name: '财产和行为税纳税申报表', period, cycle: 'quarter', dueDate, status: 'filed', deadlineId: 'd2', daysLeft: 3 },
        ],
      },
    ],
  }
}

function emptyDiff() {
  return {
    year: new Date().getFullYear(),
    source: 'default' as const,
    willCreateCount: 0,
    willWaiveCount: 0,
    willRestoreCount: 0,
    willCreate: [],
    willWaive: [],
    willRestore: [],
  }
}

beforeEach(() => {
  Object.values(harness).forEach((fn) => fn.mockReset())
  harness.importRows.mockResolvedValue({ created: 2, updated: 0, deactivated: 0, total: 2, refresh: { created: 2, autoWaived: 0, source: 'registration' } })
  harness.diff.mockResolvedValue(emptyDiff())
  harness.apply.mockResolvedValue({ year: 2026, created: 1, autoWaived: 0, source: 'registration' })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('税费种认定页', () => {
  it('没有认定行时给出同步与带出两条出路', async () => {
    harness.list.mockResolvedValue({ items: [], stats: { total: 0, active: 0, groups: {} } })
    harness.preview.mockResolvedValue(emptyPreview())

    const wrapper = mount(Page, { attachTo: document.body })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('税费种认定')
    expect(text).toContain('还没有同步税局认定表')
    expect(text).toContain('去同步')
    expect(text).toContain('按主体类型带出')
  })

  it('有认定行时展示归属申报表，并把附加税并进增值税申报表', async () => {
    harness.list.mockResolvedValue({
      items: [
        registrationRow(),
        registrationRow({
          id: 'reg-2',
          seq: 2,
          levyItem: '城市维护建设税',
          levyCategory: '市区（增值税附征）',
          taxRate: 0.07,
          mainFlag: 'vat_attachment',
        }),
      ],
      stats: { total: 2, active: 2, groups: { vat: 2 } },
    })
    harness.preview.mockResolvedValue(previewWith('2026-Q3', '2026-10-22', '2026-10'))

    const wrapper = mount(Page, { attachTo: document.body })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('城市维护建设税')
    // 两行都归到同一张申报表，页面上应出现两次该标签
    expect(text.split('增值税及附加税费申报表').length - 1).toBeGreaterThanOrEqual(2)
  })

  it('申报节奏预览按截止月份分桶，点税种跳税报页', async () => {
    harness.list.mockResolvedValue({
      items: [registrationRow()],
      stats: { total: 1, active: 1, groups: { vat: 1 } },
    })
    harness.preview.mockResolvedValue(previewWith('2026-Q3', '2026-10-22', '2026-10'))

    const wrapper = mount(Page, { attachTo: document.body })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('申报节奏预览')
    expect(text).toContain('2026年10月')
    expect(text).toContain('截止 2026-10-22')

    const chip = wrapper.findAll('button.chip').at(0)
    expect(chip).toBeTruthy()
    await chip?.trigger('click')
    expect(harness.push).toHaveBeenCalledWith({ path: '/tax-filing', query: { period: '2026-Q3' } })
  })
})
