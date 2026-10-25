/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// T05 · 归集报表 + 报表口径对比单测。
//
// 独立文件，避免与 T02/T03/T04 的测试文件冲突。
//
// 覆盖三处最容易出**财务口径事故**的地方：
//   ① validatePattern 的 prefix ≥ 4 位硬校验——历史上出过两次口径事故
//      （_sum_prefix(bal,"4") 吞掉所有 4xxx；_sum_codes(["2211"]) 漏掉 221102
//      让应付职工薪酬静默归零）。这是全模块存在的核心理由。
//   ② 保存前全量本地校验——PUT 是 delete+重建，派生行/重复 lineCode/空 patterns
//      都在循环内才抛错 → 整批回滚、用户输入丢失。
//   ③ compare 结果的金额格式与 skipped/clean 文案区分——
//      金额若没在 API 层 Number()，会拼出 ¥0120.00；skipped 若显示成「零差异通过」
//      则是在骗用户。

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  compareAlert,
  compareOutcome,
  deltaTagType,
  draftFromRule,
  draftToInput,
  emptyDraft,
  isDerived,
  validateDrafts,
} from '@/pages/aggregation/report-line-rules.vue'
import { nextKey } from '@/pages/aggregation/report-line-rules.vue'
import type { PatternDraft, ReportLineRuleDraft } from '@/pages/aggregation/report-line-rules.vue'
import { validatePattern } from '@/utils/aggregation'
import { formatMoney } from '@/utils/format'

// ===== ① prefix ≥ 4 位 =====

describe('validatePattern · prefix 下限 4 位', () => {
  it('prefix 短于 4 位必须报错（430 会吞掉所有 4xxx）', () => {
    expect(validatePattern({ mode: 'prefix', value: '430' })).toBeTruthy()
    expect(validatePattern({ mode: 'prefix', value: '4' })).toBeTruthy()
  })

  it('prefix 恰好 4 位通过（4301）', () => {
    expect(validatePattern({ mode: 'prefix', value: '4301' })).toBeNull()
  })

  it('prefix 多位通过（4301002）', () => {
    expect(validatePattern({ mode: 'prefix', value: '4301002' })).toBeNull()
  })

  it('报错文案点名会吞掉的一级科目，便于用户自查', () => {
    expect(validatePattern({ mode: 'prefix', value: '430' })).toContain('4xxx')
  })

  it('非数字前缀报错；空值报错', () => {
    expect(validatePattern({ mode: 'prefix', value: '43A1' })).toBeTruthy()
    expect(validatePattern({ mode: 'prefix', value: '   ' })).toBe('请填写科目值')
  })

  it('exact 与 range 的规则各自独立', () => {
    expect(validatePattern({ mode: 'exact', value: '2211,221102' })).toBeNull()
    expect(validatePattern({ mode: 'range', value: '560101-560199' })).toBeNull()
    expect(validatePattern({ mode: 'range', value: '560199-560101' })).toBeTruthy()
  })
})

// ===== ② 保存前全量本地校验 =====

/**
 * 构造带本地 key 的 pattern 草稿。
 * key 是纯 UI 用途（v-for / 行内定位），测试不该关心但类型要求存在，故在此统一补上——
 * 顺带避免了每处手写对象都漏 key 导致的类型错误。
 */
function pattern(overrides: Partial<PatternDraft> = {}): PatternDraft {
  return {
    key: `pat-${nextKey('test')}`,
    mode: 'prefix',
    value: '',
    sign: 1,
    ...overrides,
  }
}

function draft(overrides: Partial<ReportLineRuleDraft> = {}): ReportLineRuleDraft {
  return {
    ...emptyDraft(),
    lineCode: 'rd_expense',
    patterns: [pattern({ value: '4301' })],
    effectiveFrom: '2026-01',
    ...overrides,
  }
}

describe('validateDrafts · 派生行不可配置', () => {
  it('profit 的派生行被拦下', () => {
    const errors = validateDrafts('profit', [draft({ lineCode: 'net_profit' })])
    expect(errors.some((e) => e.includes('派生行'))).toBe(true)
  })

  it('balance 的派生行同样被拦下', () => {
    const errors = validateDrafts('balance', [draft({ lineCode: 'asset_total' })])
    expect(errors.some((e) => e.includes('派生行'))).toBe(true)
  })

  it('isDerived 与 utils 常量一致（前后端同一份事实）', () => {
    expect(isDerived('profit', 'operating_profit')).toBe(true)
    expect(isDerived('balance', 'liability_equity_total')).toBe(true)
    expect(isDerived('profit', 'revenue')).toBe(false)
    // 跨报表不串：net_profit 是利润表派生行，不是资产负债表的
    expect(isDerived('balance', 'net_profit')).toBe(false)
  })
})

describe('validateDrafts · 重复 lineCode 用 Set 先于提交拦截', () => {
  it('同一 lineCode 出现两次时报错，并指出与第几行重复', () => {
    const errors = validateDrafts('profit', [
      draft({ lineCode: 'rd_expense' }),
      draft({ lineCode: 'rd_expense' }),
    ])
    expect(errors.some((e) => e.includes('重复') && e.includes('第 1 行'))).toBe(true)
  })

  it('不同 lineCode 不误报', () => {
    expect(validateDrafts('profit', [draft({ lineCode: 'a' }), draft({ lineCode: 'b' })])).toEqual([])
  })

  it('空 lineCode 报错', () => {
    const errors = validateDrafts('profit', [draft({ lineCode: '   ' })])
    expect(errors.some((e) => e.includes('不能为空'))).toBe(true)
  })
})

describe('validateDrafts · patterns', () => {
  it('空 patterns 报错', () => {
    const errors = validateDrafts('profit', [draft({ patterns: [] })])
    expect(errors.some((e) => e.includes('至少配置一个科目匹配'))).toBe(true)
  })

  it('pattern 前缀过短报错，且带上行号定位', () => {
    const errors = validateDrafts('profit', [draft({ patterns: [pattern({ value: '43' })] })])
    expect(errors.some((e) => e.includes('第 1 行') && e.includes('第 1 个科目匹配'))).toBe(true)
  })

  it('正确的加减组合通过（4301 − 4301002）', () => {
    const errors = validateDrafts('profit', [
      draft({
        patterns: [
          pattern({ value: '4301' }),
          pattern({ value: '4301002', sign: -1 }),
        ],
      }),
    ])
    expect(errors).toEqual([])
  })
})

describe('validateDrafts · 生效期', () => {
  it('effectiveFrom 必填', () => {
    const errors = validateDrafts('profit', [draft({ effectiveFrom: '' })])
    expect(errors.some((e) => e.includes('effectiveFrom'))).toBe(true)
  })

  it('effectiveTo 早于 effectiveFrom 报错', () => {
    const errors = validateDrafts('profit', [
      draft({ effectiveFrom: '2026-03', effectiveTo: '2026-01' }),
    ])
    expect(errors.some((e) => e.includes('不得早于生效期'))).toBe(true)
  })

  it('effectiveTo 等于 effectiveFrom 或留空都通过', () => {
    expect(
      validateDrafts('profit', [draft({ effectiveFrom: '2026-03', effectiveTo: '2026-03' })]),
    ).toEqual([])
    expect(validateDrafts('profit', [draft({ effectiveFrom: '2026-03', effectiveTo: '' })])).toEqual([])
  })
})

describe('validateDrafts · 一次性汇总所有错误', () => {
  it('多条规则各自出错时全部列出，不逐个弹窗（风险 5 的直接对策）', () => {
    const errors = validateDrafts('profit', [
      draft({ lineCode: '' }),
      draft({ lineCode: 'net_profit', patterns: [] }),
      draft({ lineCode: 'a', effectiveFrom: '2026-05', effectiveTo: '2026-01' }),
    ])
    // 第 1 行 lineCode 空 + 第 2 行派生行 + 空 patterns + 第 3 行期间颠倒
    expect(errors.length).toBeGreaterThanOrEqual(4)
  })

  it('空草稿数组返回明确提示', () => {
    expect(validateDrafts('profit', [])).toEqual(['请至少配置一条规则'])
  })
})

// ===== 草稿 ↔ 提交体 =====

describe('draftToInput', () => {
  it('空的可选字段转 null，不把空串当有值提交', () => {
    const input = draftToInput(draft({ lineName: '', effectiveTo: '' }))
    expect(input.lineName).toBeNull()
    expect(input.effectiveTo).toBeNull()
    expect(input.effectiveFrom).toBe('2026-01')
  })

  it('sign 归一到合法枚举', () => {
    expect(draftToInput(draft({ sign: 'debit' })).sign).toBe('debit')
    expect(draftToInput(draft({ sign: 'net' })).sign).toBe('net')
  })

  it('pattern 的 sign 保留 ±1，空白被 trim', () => {
    const input = draftToInput(
      draft({ patterns: [pattern({ value: '  4301 ', sign: -1 })] }),
    )
    expect(input.patterns[0]).toEqual({ mode: 'prefix', value: '4301', sign: -1 })
  })
})

describe('draftFromRule', () => {
  it('后端 pattern 的 sign ±1 原样带回，非法值归一到 1', () => {
    expect(draftFromRule({ lineCode: 'a', patterns: [{ mode: 'prefix', value: '4301', sign: -1 }] }).patterns[0].sign).toBe(-1)
    expect(draftFromRule({ lineCode: 'a', patterns: [{ mode: 'prefix', value: '4301', sign: 0 }] }).patterns[0].sign).toBe(1)
  })

  it('未知 sign 归一到 net', () => {
    expect(draftFromRule({ lineCode: 'a', sign: 'whatever' }).sign).toBe('net')
  })

  it('缺 patterns 时给空数组，不留 undefined', () => {
    expect(draftFromRule({ lineCode: 'a' }).patterns).toEqual([])
  })

  it('回填后的草稿能通过全量校验（round-trip 不引入新错误）', () => {
    const restored = draftFromRule({
      lineCode: 'rd_expense',
      patterns: [pattern({ value: '4301' })],
      effectiveFrom: '2026-01',
    })
    expect(validateDrafts('profit', [restored])).toEqual([])
  })
})

// ===== ③ compare 三态与金额格式 =====

describe('compareAlert · skipped 与 clean 文案必须不同', () => {
  it('skipped（未配规则）不得显示成零差异通过', () => {
    const alert = compareAlert({ skipped: true, clean: true, compared: 0, ruleCount: undefined })
    expect(alert.type).toBe('info')
    expect(alert.text).toContain('未配置任何启用中的规则')
    expect(alert.text).not.toContain('逐行一致')
  })

  it('clean（真做了对比且零差异）显示 success', () => {
    const alert = compareAlert({ skipped: false, clean: true, diffs: [], compared: 12, ruleCount: 8 })
    expect(alert.type).toBe('success')
    expect(alert.text).toContain('12')
    expect(alert.text).toContain('规则数 8')
  })

  it('有差异时既不是 success 也不是 info', () => {
    const alert = compareAlert({
      skipped: false,
      clean: false,
      diffs: [{}, {}],
      compared: 12,
      ruleCount: 8,
    })
    expect(alert.type).toBe('warning')
    expect(alert.text).toContain('2 行口径差异')
  })

  it('ruleCount 缺失（skipped 早退分支）时文案不出现 "undefined"', () => {
    const alert = compareAlert({ skipped: false, clean: true, diffs: [], compared: 3 })
    expect(alert.text).not.toContain('undefined')
  })

  it('compareOutcome 三态判定：diffs 非空时优先判 diff，不被 clean 覆盖', () => {
    expect(compareOutcome({ skipped: true, clean: true })).toBe('skipped')
    expect(compareOutcome({ skipped: false, clean: true, diffs: [] })).toBe('clean')
    expect(compareOutcome({ skipped: false, clean: true, diffs: [{}] })).toBe('diff')
  })
})

describe('compare 金额展示格式', () => {
  it('API 层已 Number() 的值格式化为 ¥120.00，不是 ¥0120.00', () => {
    expect(formatMoney(120)).toBe('¥120.00')
    expect(formatMoney(120)).not.toContain('¥0120')
  })

  it('负差额保留负号（QA 用 -120.00 复验）', () => {
    expect(formatMoney(-120)).toBe('-¥120.00')
  })

  it('delta 的正负决定标签色：正=绿（配置口径更高）、负=红、零=中性', () => {
    expect(deltaTagType(5)).toBe('success')
    expect(deltaTagType(-5)).toBe('error')
    expect(deltaTagType(0)).toBe('default')
  })

  it('带符号拼接不会错位（正差额显式加 +）', () => {
    const text = `+${formatMoney(120.5)}`
    expect(text).toBe('+¥120.50')
  })
})

// ===== ④ 页面渲染断言（DOM 层）=====
//
// 取向说明（重要，勿改回源码断言）：
//   · **行为类断言读渲染后的 DOM**。下面的用例真的把组件挂载起来、查 wrapper.text()，
//     所以页面重构、注释增删、格式化都不会误报；文案改了才会红。
//   · **纪律类断言读源码**，因为它压根无法从 DOM 观察（见 ⑤）。
//   挂载需要 Pinia 与 Naive UI，故用 createPinia + 全局注册 naive。

import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import naive from 'naive-ui'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

// API 层全部 mock：组件 onMounted 会打 store.bootstrap() 与 list 端点。
// 金额一律给**已是 number** 的值——api/aggregation.ts 的归一化保证这一点，
// 若这里传字符串就等于在测一条不存在的契约。
const apiMock = vi.hoisted(() => ({
  listRules: vi.fn(),
  readiness: vi.fn(),
  compare: vi.fn(),
  replace: vi.fn(),
  archive: vi.fn(),
  getReport: vi.fn(),
  getMeta: vi.fn(),
  listDims: vi.fn(),
}))

vi.mock('@/api/aggregation', () => ({
  listReportLineRules: apiMock.listRules,
  getSwitchReadiness: apiMock.readiness,
  compareReportLineRules: apiMock.compare,
  replaceReportLineRules: apiMock.replace,
  archiveReportLineRule: apiMock.archive,
  getAggregationReport: apiMock.getReport,
  // store.bootstrap() 会拉这两个；给最小可用值即可，页面本身不依赖具体内容
  getAggregationMeta: apiMock.getMeta,
  listDimensions: apiMock.listDims,
}))

// 每个用例前重置 mock 并给默认实现，避免用例间串味
beforeEach(() => {
  apiMock.listRules.mockReset().mockResolvedValue([])
  apiMock.readiness.mockReset().mockResolvedValue(readinessFixture())
  apiMock.compare.mockReset()
  apiMock.replace.mockReset()
  apiMock.archive.mockReset()
  apiMock.getReport.mockReset().mockResolvedValue(null)
  apiMock.getMeta.mockReset().mockResolvedValue({ kinds: [], methods: [], memberStatuses: [] })
  apiMock.listDims.mockReset().mockResolvedValue([
    { id: 'd1', code: 'RD', name: '研发项目', kind: 'project', status: 'active' },
  ])
})

function readPageSource(name: string): string {
  return readFileSync(resolve(process.cwd(), 'src/pages/aggregation', name), 'utf-8')
}

/**
 * 挂载页面组件。
 * 两个页面都调useMessage() / useDialog()，naive-ui 要求外层有 provider，
 * 否则 setup 阶段直接抛错，所以必须包一层 NMessageProvider + NDialogProvider。
 */
function mountPage(component: unknown) {
  const pinia = createPinia()
  setActivePinia(pinia)
  // report.vue 按工作视图双表达：会计视图常驻口径说明与四态概览，
  // 老板视图收进折叠区并换成经营语言。测试默认钉住会计视图。
  useWorkspaceViewStore(pinia).setMode('accounting')
  const Host = defineComponent({
    setup() {
      return () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(component as never) }) })
    },
  })
  return mount(Host, {
    // ⚠️ 必须 attachTo：NModal teleport 到 document.body，wrapper 不挂上去就找不到弹层按钮
    attachTo: document.body,
    global: { plugins: [pinia, naive] },
  })
}

function readinessFixture(overrides = {}) {
  return {
    report: 'profit',
    ready: false,
    requiredCleanPeriods: 3,
    currentCleanStreak: 1,
    checkedRuns: 4,
    latest: null,
    ...overrides,
  }
}

describe('report-line-rules.vue 渲染', () => {
  it('顶部警示常驻：未达连续 3 期零差异前，对外报表口径不变', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    expect(wrapper.text()).toContain('未达连续 3 期零差异前，对外报表口径不变')
    wrapper.unmount()
  })

  it('页面上不存在任何「切换口径」按钮——按渲染后的按钮文本判定，而非源码字符串', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    // 遍历真实渲染出的所有按钮，确认没有一个含「切换口径」或裸的「切换」
    const labels = wrapper.findAll('button').map((button) => button.text())
    expect(labels.length).toBeGreaterThan(0)
    expect(labels.filter((label) => label.includes('切换'))).toEqual([])
    expect(wrapper.text()).not.toContain('切换口径')
    wrapper.unmount()
  })

  it('就绪度显示连续期数 / 门槛期数，而不是「已达标」这类误导文案', async () => {
    apiMock.listRules.mockResolvedValue([])
    apiMock.readiness.mockResolvedValue(readinessFixture({ currentCleanStreak: 1 }))
    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    expect(wrapper.text()).toContain('连续 1/3 期零差异')
    expect(wrapper.text()).toContain('未达标')
    wrapper.unmount()
  })

  it('空规则时给出引导文案，不是空白表格', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    expect(wrapper.text()).toContain('暂无启用中的口径规则')
    wrapper.unmount()
  })

  it('表格里的派生行带「派生」标记，且归档行文案是「归档」不是「删除」', async () => {
    apiMock.listRules.mockResolvedValue([
      {
        id: 'r1',
        report: 'profit',
        lineCode: 'revenue',
        lineName: '营业收入',
        patterns: [{ mode: 'prefix', value: '6001', sign: 1 }],
        sign: 'net',
        status: 'active',
        effectiveFrom: '2026-01',
        effectiveTo: null,
      },
      {
        id: 'r2',
        report: 'profit',
        lineCode: 'net_profit',
        lineName: '净利润',
        patterns: [],
        sign: 'net',
        status: 'active',
        effectiveFrom: '2026-01',
        effectiveTo: null,
      },
    ])
    apiMock.readiness.mockResolvedValue(readinessFixture())
    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    const text = wrapper.text()
    expect(text).toContain('派生')
    expect(text).toContain('归档')
    expect(text).not.toContain('删除规则')
    wrapper.unmount()
  })
})

describe('report.vue 渲染', () => {
  it('顶部常驻口径说明：报表只取 posted，清单含草稿', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    const text = wrapper.text()
    expect(text).toContain('归集报表只统计已过账（posted）凭证')
    expect(text).toContain('待归集清单还包含草稿凭证')
    wrapper.unmount()
  })

  it('start/end 必填：未选月份时查询按钮禁用，首屏不发 report 请求', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    const queryButton = wrapper.findAll('button').find((b) => b.text() === '查询')
    expect(queryButton?.attributes('disabled')).toBeDefined()
    expect(apiMock.getReport).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('无数据时给出空态文案，而不是空表格', async () => {
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    expect(wrapper.text()).toContain('暂无数据')
    wrapper.unmount()
  })

  it('选定月份并查询后：totals.balanced === false 时红字告警「行金额 ≠ 已归集 + 未归集」', async () => {
    apiMock.getReport.mockResolvedValue({
      dimensionId: 'd1',
      dimensionCode: 'RD',
      dimensionName: '研发项目',
      basis: 'period',
      start: '2026-01',
      end: '2026-03',
      scopePrefixes: ['5', '6'],
      items: [
        {
          memberId: 'm1',
          code: 'M1',
          name: '平台组',
          status: 'active',
          allocatedAmount: 120,
          allocatedLineCount: 2,
        },
      ],
      totals: {
        lineAmount: 1000,
        allocatedAmount: 120,
        unassignedAmount: 900,
        // 故意不平衡：120 + 900 = 1020 ≠ 1000
        balanced: false,
        allocatedLineCount: 1,
        partialLineCount: 1,
        unallocatedLineCount: 3,
        lineCount: 5,
      },
    })
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    // 页面不自动查询（start/end 必填），必须先选月份再点查询
    await selectMonthRange(wrapper, ['2026-01', '2026-03'])
    await flush()
    const queryButton = wrapper.findAll('button').find((b) => b.text() === '查询')
    expect(queryButton?.attributes('disabled')).toBeUndefined()
    await queryButton?.trigger('click')
    await flush()

    const text = wrapper.text()
    expect(text).toContain('行金额 ≠ 已归集 + 未归集，请检查数据')
    // 金额已是 number，直接格式化，不会出现 ¥0120.00 这类拼接错位
    expect(text).toContain('¥120.00')
    expect(text).not.toContain('¥0120')
    // 四态概览都要露出来
    expect(text).toContain('已归集行')
    expect(text).toContain('部分归集行')
    expect(text).toContain('未归集行')
    wrapper.unmount()
  })

  it('lineAmount 为 0 时占比列不出现 Infinity / NaN（除零保护）', async () => {
    apiMock.getReport.mockResolvedValue({
      dimensionId: 'd1',
      dimensionCode: 'RD',
      dimensionName: '研发项目',
      basis: 'period',
      start: '2026-01',
      end: '2026-01',
      scopePrefixes: ['5'],
      items: [
        { memberId: 'm1', code: 'M1', name: '平台组', status: 'active', allocatedAmount: 120, allocatedLineCount: 1 },
      ],
      totals: {
        lineAmount: 0,
        allocatedAmount: 120,
        unassignedAmount: 0,
        balanced: true,
        allocatedLineCount: 1,
        partialLineCount: 0,
        unallocatedLineCount: 0,
        lineCount: 1,
      },
    })
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    await selectMonthRange(wrapper, ['2026-01', '2026-01'])
    await flush()
    await wrapper.findAll('button').find((b) => b.text() === '查询')?.trigger('click')
    await flush()

    const text = wrapper.text()
    expect(text).not.toContain('Infinity')
    expect(text).not.toContain('NaN')
    wrapper.unmount()
  })

  it('查到但该期间无已过账凭证行时，空态文案说的是「无已过账的损益类凭证行」', async () => {
    apiMock.getReport.mockResolvedValue({
      dimensionId: 'd1',
      dimensionCode: 'RD',
      dimensionName: '研发项目',
      basis: 'period',
      start: '2026-01',
      end: '2026-01',
      scopePrefixes: ['5'],
      items: [],
      totals: {
        lineAmount: 0,
        allocatedAmount: 0,
        unassignedAmount: 0,
        balanced: true,
        allocatedLineCount: 0,
        partialLineCount: 0,
        unallocatedLineCount: 0,
        lineCount: 0,
      },
    })
    const wrapper = mountPage((await import('@/pages/aggregation/report.vue')).default)
    await flush()
    await selectMonthRange(wrapper, ['2026-01', '2026-01'])
    await flush()
    await wrapper.findAll('button').find((b) => b.text() === '查询')?.trigger('click')
    await flush()
    expect(wrapper.text()).toContain('该期间无已过账的损益类凭证行')
    wrapper.unmount()
  })
})

/**
 * 给 NDatePicker(type=monthrange) 喂月份区间。
 * 页面用 value-format='yyyy-MM'，运行期交出字符串，但 TS 只认 [number, number]，
 * 所以这里直接 emit update:value 绕过选择器交互（不去测 naive-ui 自己的行为）。
 */
async function selectMonthRange(wrapper: ReturnType<typeof mount>, months: [string, string]): Promise<void> {
  const picker = wrapper.findAllComponents({ name: 'DatePicker' })[0]
  await picker?.vm.$emit('update:value', months)
  await flush()
}

// ===== ④-b P0 防线：草稿必须取**全量**规则（含已归档）=====
//
// 这条防线的意义（QA 变异测试 MUT-1 曾让它存活，即此防线裸奔）：
// 后端 replace_rules 是 **delete + 重建**（report_line_rule_service.py:400-404）。
// 若 openEditor() 取visibleRules.value（= 过滤掉已归档行）作为草稿，
// 用户在关闭「含已归档」开关的状态下点一次保存，那条归档规则就**被永久删除**了——
// 而归档的语义是「不再参与计算但历史可追溯」，删除等于毁历史。

/** 三条规则：2 条启用 + 1 条已归档 */
function threeRules() {
  return [
    {
      id: 'r1', report: 'profit', lineCode: 'revenue', lineName: '营业收入',
      patterns: [{ mode: 'prefix', value: '6001', sign: 1 }],
      sign: 'net', status: 'active', effectiveFrom: '2026-01', effectiveTo: null,
    },
    {
      id: 'r2', report: 'profit', lineCode: 'rd_expense', lineName: '研发费用',
      patterns: [pattern({ value: '4301' })],
      sign: 'net', status: 'active', effectiveFrom: '2026-01', effectiveTo: null,
    },
    {
      id: 'r3', report: 'profit', lineCode: 'old_line', lineName: '历史科目',
      patterns: [{ mode: 'exact', value: '6002', sign: 1 }],
      sign: 'net', status: 'archived', effectiveFrom: '2025-01', effectiveTo: '2025-12',
    },
  ]
}

describe('P0 · 草稿取全量（含已归档），关闭开关保存也不丢归档规则', () => {
  it('表格只显示 2 条启用规则，但保存时提交体仍含那条已归档规则（长度 3）', async () => {
    apiMock.listRules.mockResolvedValue(threeRules())
    // PUT 返回裸数组（后端 replace_rules 末 return after）
    apiMock.replace.mockImplementation((_report: string, rules: unknown[]) => Promise.resolve(rules))

    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()

    // 默认「仅启用」，表格里看不到归档行——这是变异体最容易骗过断言的地方
    expect(wrapper.text()).toContain('共 3 条（含已归档 1 条）')
    expect(wrapper.text()).not.toContain('历史科目')

    await clickButton('编辑规则')
    await clickButton('保存规则')

    expect(apiMock.replace).toHaveBeenCalledTimes(1)
    const submitted = apiMock.replace.mock.calls[0][1] as Array<{ lineCode: string; status: string }>
    // ⚠️ 这是本用例的核心断言：草稿来自 rules（全量 3 条），不是 visibleRules（2 条）
    expect(submitted).toHaveLength(3)
    expect(submitted.map((rule) => rule.lineCode)).toContain('old_line')
    // 且归档状态原样回传，不能被悄悄改成 active
    expect(submitted.find((rule) => rule.lineCode === 'old_line')?.status).toBe('archived')
    wrapper.unmount()
  })

  it('打开「含已归档」开关后提交体同样是 3 条（开关只控展示，不控提交范围）', async () => {
    apiMock.listRules.mockResolvedValue(threeRules())
    apiMock.replace.mockImplementation((_report: string, rules: unknown[]) => Promise.resolve(rules))

    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()

    // 打开「含已归档」开关
    await wrapper.find('.n-switch').trigger('click')
    await flush()
    expect(wrapper.text()).toContain('历史科目')

    await clickButton('编辑规则')
    await clickButton('保存规则')

    const submitted = apiMock.replace.mock.calls[0][1] as unknown[]
    expect(submitted).toHaveLength(3)
    wrapper.unmount()
  })

  it('保存失败时保留本地草稿，用户输入不丢（整批 PUT 会回滚）', async () => {
    apiMock.listRules.mockResolvedValue(threeRules())
    apiMock.replace.mockRejectedValue({ response: { data: { detail: { code: 'INVALID_REPORT_LINE_RULE', message: '规则不合法' } } } })

    const wrapper = mountPage((await import('@/pages/aggregation/report-line-rules.vue')).default)
    await flush()
    await clickButton('编辑规则')
    await clickButton('保存规则')

    expect(apiMock.replace).toHaveBeenCalledTimes(1)
    // 失败时编辑弹层**不能关闭**——草稿还在，用户能改了重试。
    // （成功路径会 editing=false 关掉弹层，所以这里断言弹层仍在，正是失败路径的标志）
    const saveButtonVisible = [...document.body.querySelectorAll('button')].some(
      (button) => (button.textContent ?? '').trim() === '保存规则',
    )
    expect(saveButtonVisible).toBe(true)
    wrapper.unmount()
  })
})

// ===== ④-c 防退化：双 <script> 块不得重复 import 同一标识符 =====
//
// Vue SFC 会把普通 <script> 与 <script setup> **合并进同一个模块作用域**，
// 所以同一标识符在两个块各import 一次会直接触发 TS2300 Duplicate identifier。
// QA 在审查窗口内实测捕获到 AllocationDrawer.vue 因此报红（且正是该文件
// 自己注释里警告过的陷阱），故这里用测试把这条约定守住——
// 理由：它只能靠类型检查发现，而 vitest 按模块解析、不做 TS 语义检查，
// 类型检查又不一定每次都跑。

/** 取出单个 .vue 源码里两个 script 块的 import 语句标识符 */
function importedIdentifiersOf(source: string): { plain: string[]; setup: string[] } {
  const blocks = [...source.matchAll(/<script(?:\s+setup)?[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1])
  const collect = (code: string): string[] => {
    const names: string[] = []
    for (const match of code.matchAll(/import\s+(?:type\s+)?([\s\S]*?)\s+from\s+['"][^'"]+['"]/g)) {
      const clause = match[1]
      const braced = clause.match(/\{([\s\S]*?)\}/)
      if (braced) {
        for (const part of braced[1].split(',')) {
          const name = part.trim().split(/\s+as\s+/).pop()?.trim()
          if (name) names.push(name)
        }
      }
      const defaultOrNamespace = clause.replace(/\{[^}]*\}/, '').replace(/^\s*,|,\s*$/g, '').trim()
      if (defaultOrNamespace && !defaultOrNamespace.startsWith('*')) names.push(defaultOrNamespace)
    }
    return names
  }
  return { plain: collect(blocks[0] ?? ''), setup: collect(blocks[1] ?? '') }
}

describe('防退化 · 双 <script> 块不重复 import 同一标识符（TS2300）', () => {
  const VUE_FILES = [
    'report.vue',
    'report-line-rules.vue',
    '_components/AllocationDrawer.vue',
    '_components/DimensionPicker.vue',
    'pending.vue',
    'rules.vue',
    'dimensions.vue',
    'index.vue',
  ]

  it.each(VUE_FILES)('%s 的两个 script 块无重复 import', (name) => {
    const { plain, setup } = importedIdentifiersOf(readPageSource(name))
    // 只关心出现在**两个块里**的标识符；同块内重复由 vue-tsc 负责
    const duplicated = [...new Set(plain.filter((identifier) => setup.includes(identifier)))]
    expect(
      duplicated,
      `${name} 在普通 <script> 与 <script setup> 中重复 import：${duplicated.join(', ')}（会触发 TS2300）`,
    ).toEqual([])
  })
})

// ===== ⑤ 纪律类断言：读源码（刻意为之）=====
//
// 这条断言**无法**从 DOM 观察：`const { data } = await apiClient.get(...)` 是编译期
// 就写错、运行期才解构出 undefined 的写法，页面上看不出任何异常（TS 不报错、any 泄漏）。
// 所以这里**刻意**读源码字符串做纪律断言，而非渲染断言。
//
// 路径说明：不能用 `fileURLToPath(new URL(..., import.meta.url))`——vitest 环境下
// import.meta.url 不是 file: scheme，会抛「The URL must be of scheme file」。
// 用 process.cwd()（vitest 下即 web/）相对解析。

describe('纪律断言 · 读源码（刻意为之，见上方注释）', () => {
  it('两个页面都不存在 `const { data }` 解构（响应拦截器已 return response.data）', () => {
    for (const name of ['report.vue', 'report-line-rules.vue']) {
      expect(readPageSource(name)).not.toContain('const { data }')
    }
  })

  it('report-line-rules.vue 页面上不存在任何切换口径入口', () => {
    const source = readPageSource('report-line-rules.vue')
    // 顶部警示文案必须在（这条同时兜住「警示被误删」）
    expect(source).toContain('未达连续 3 期零差异前，对外报表口径不变')
    // 归档文案在，删除文案不在
    expect(source).toContain('归档后该行不再参与口径计算，历史可追溯')
    expect(source).not.toContain('切换口径')
  })

  it('report.vue 保留口径说明常量引用与三态概览字段', () => {
    const source = readPageSource('report.vue')
    expect(source).toContain('REPORT_SCOPE_NOTE')
    expect(source).toContain('行金额 ≠ 已归集 + 未归集')
    expect(source).toContain('该期间无已过账的损益类凭证行')
  })
})


/**
 * 点一个按钮，按可见文本匹配。
 *
 * ⚠️ 必须查 document.body 而不是 wrapper：NModal 默认 teleport 到 body，
 * 弹层里的「保存规则」按钮不在 wrapper 子树内，只在 wrapper 里找会永远返回 undefined
 * ——而 `?.trigger()` 是可选链，点空了就静默跳过，测试会以「mock 没被调用」的形式
 * 失败，排查起来很绕。
 */
async function clickButton(text: string): Promise<void> {
  const target = [...document.body.querySelectorAll('button')].find(
    (button) => (button.textContent ?? '').trim() === text,
  )
  expect(target, `页面上找不到按钮「${text}」`).toBeTruthy()
  target?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flush()
}

/** 等待 onMounted 里的 promise 链（bootstrap → load）落地 */
async function flush(): Promise<void> {
  await new Promise((resolveTick) => setTimeout(resolveTick, 0))
  await new Promise((resolveTick) => setTimeout(resolveTick, 0))
}
