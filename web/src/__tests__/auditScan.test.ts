/** Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AuditScanPage from '@/pages/audit-scan/index.vue'
import { detectBalanceFollowups, parseBalanceFileWithPreview, submitHealthScan } from '@/api/healthScan'
import { saveWorkpaperImage } from '@/utils/workpaperImage'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('naive-ui', async () => ({
  ...(await vi.importActual<object>('naive-ui')),
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}))
vi.mock('@/utils/workpaperImage', () => ({ saveWorkpaperImage: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/healthScan', async () => {
  const actual = await vi.importActual<typeof import('@/api/healthScan')>('@/api/healthScan')
  return {
    ...actual,
    parseBalanceFileWithPreview: vi.fn(),
    submitHealthScan: vi.fn(),
  }
})

const wrappers: ReturnType<typeof mount>[] = []
function page() {
  const wrapper = mount(AuditScanPage, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}

const sampleReport = {
  totalScore: 62,
  healthLevel: 'HIGH_RISK',
  levelLabel: '高危雷区待整改',
  oneSentenceVerdict: '公转私挂账跨年未还，面临 20% 个税风险。',
  taxBureauView: { ruleId: 'TAX-PAIN-V2', riskLevel: 'high', details: '重点命中公转私挂账，建议核实原始单据。', checkChain: 'Q-01' },
  bankFunderView: { ruleId: 'BANK-PAIN-V2', creditLevel: 'low', details: '往来挂账影响经营贷初筛通过率。', checkChain: 'Q-01' },
  dimensions: [
    { key: 'capital', name: '公私资金安全', score: 62, status: 'WARNING', statusLabel: '存在隐患待理', detail: '检出 1 项隐患' },
  ],
  flags: [
    {
      code: 'Q-01',
      name: '公转私与往来挂账',
      painTitle: '公转私长期挂账：小心 20% 个人所得税红线',
      severity: 'CRITICAL',
      deduction: 22,
      amount: '¥100,000.00',
      estimatedExposure: '按 20% 个税口径测算敞口约 ¥20,000.00',
      desc: '公户转个人卡常备注借款且跨年未还。',
      fix: '通过合规报销或退回公户清理平账。',
      legalBasis: '《个人所得税法》',
      ctaLabel: '规范公私往来账',
      ctaRoute: '/mine/legacy-import',
    },
  ],
  checks: [
    { code: 'Q-01', name: '公转私与往来挂账', status: 'RISK', observed: '跨年未还', missing: [], fix: '清理', source: '问卷', dimension: 'capital', deduction: 22 },
  ],
  coverage: { completed: 6, applicable: 6, percent: 100 },
  scoreBreakdown: { totalDeduction: 22 },
  urgentAction: '先清理其他应收款挂账。',
  recommendedFix: '导入梭罗记账重整。',
  meta: { mode: 'quiz', ruleCount: 1, ephemeral: true, baselineVersion: 'PAIN-V2', analysisSource: 'llm', source: 'backend' },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(submitHealthScan).mockResolvedValue(sampleReport as never)
  vi.mocked(parseBalanceFileWithPreview).mockResolvedValue({
    rows: [
      { account_code: '1221', account_name: '其他应收款', debit_balance: 80000, credit_balance: 0 },
      { account_code: '3001', account_name: '实收资本', debit_balance: 0, credit_balance: 80000 },
    ],
    preview: { periodLabel: '2026-10', balanceType: 'ending', unit: 'yuan', debitTotal: 80000, creditTotal: 80000, warnings: [], hasAmbiguity: false, selectedColumns: { code: 0, name: 1, direction: -1, debit: 2, credit: 3, balance: -1 } },
  } as never)
})

afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  document.body.innerHTML = ''
})

describe('3分钟轻量财税健康体检', () => {
  it('问卷模式支持无需填专业画像直接分步答题并自动跳下一题、出痛点报告', async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboard } })

    const wrapper = page()
    // 未填任何前置字段即可答题：第 1 题点选 C 自动前进到第 2 题
    expect(wrapper.text()).toContain('第 1 / 6 题')
    await wrapper.findAll('.option-card')[2].trigger('click')
    expect(wrapper.text()).toContain('第 2 / 6 题')
    await wrapper.findAll('.option-card')[0].trigger('click')
    expect(wrapper.text()).toContain('第 3 / 6 题')
    await wrapper.findAll('.option-card')[0].trigger('click')

    // 完成 3 题即可直接提交生成报告
    const cta = wrapper.find('.scan-cta-btn')
    expect((cta.element as HTMLButtonElement).disabled).toBe(false)
    await cta.trigger('click')
    await flushPromises()

    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'quiz',
      useAi: true,
      answers: { q1: 'C', q2: 'A', q3: 'A' },
    }))
    const paper = wrapper.find('.audit-workpaper')
    expect(paper.text()).toContain('62')
    expect(paper.text()).toContain('20% 个人所得税红线')
    expect(paper.text()).toContain('税局稽查视角')
    expect(paper.text()).toContain('银行 / 资方授信视角')
    expect(paper.text()).toContain('不构成法定审计')

    await wrapper.findAll('.report-actions button')[0].trigger('click')
    expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('20% 个人所得税红线'))
    await wrapper.findAll('.report-actions button')[1].trigger('click')
    expect(saveWorkpaperImage).toHaveBeenCalledWith(paper.element, expect.any(String))
  })

  it('传表模式无需勾选确认框与列号，仅在检测到高额往来时弹出对应追问', async () => {
    const wrapper = page()
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')

    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'balance.csv')] })
    await input.trigger('change')
    await flushPromises()

    // 3 项摘要直接呈现，没有“识别列从0开始”和双复选框
    expect(wrapper.text()).toContain('已识别科目')
    expect(wrapper.text()).toContain('借贷平衡')
    expect(wrapper.text()).not.toContain('识别列')
    expect(wrapper.findAll('input[type=checkbox]')).toHaveLength(1) // 仅剩 AI 润色开关

    // 表里 1221 有 80,000，仅弹出 1 道针对性追问，不问预收款/研发/固定资产
    expect(wrapper.text()).toContain('其他应收款')
    expect(wrapper.text()).not.toContain('预收账款')
    await wrapper.findAll('.smart-followup .pill-btn')[0].trigger('click')

    await wrapper.find('.scan-cta-btn').trigger('click')
    await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'balance',
      useAi: true,
      answers: { loanCounterparty: 'SHAREHOLDER' },
      balanceMeta: expect.objectContaining({ balanceType: 'ending', confirmed: true, complete: true }),
    }))
  })

  it('余额表无异常往来线索时实现零追问一键出报告', async () => {
    vi.mocked(parseBalanceFileWithPreview).mockResolvedValueOnce({
      rows: [
        { account_code: '1002', account_name: '银行存款', debit_balance: 50000, credit_balance: 0 },
        { account_code: '3001', account_name: '实收资本', debit_balance: 0, credit_balance: 50000 },
      ],
      preview: { periodLabel: '', balanceType: 'unknown', unit: 'unknown', debitTotal: 50000, creditTotal: 50000, warnings: [], hasAmbiguity: true, selectedColumns: { code: 0, name: 1, direction: -1, debit: 2, credit: 3, balance: -1 } },
    } as never)

    const wrapper = page()
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'clean.csv')] })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.findAll('.smart-followup')).toHaveLength(0)
    await wrapper.find('.scan-cta-btn').trigger('click')
    await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'balance',
      answers: {},
      balanceMeta: expect.objectContaining({ balanceType: 'ending' }),
    }))
  })

  it('上传新文件失败时清除旧数据，不误提交旧表', async () => {
    const wrapper = page()
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'first.csv')] })
    await input.trigger('change')
    await flushPromises()

    vi.mocked(parseBalanceFileWithPreview).mockRejectedValueOnce(new Error('解析失败'))
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'bad.csv')] })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('.balance-summary-card').exists()).toBe(false)
    expect((wrapper.find('.scan-cta-btn').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('detectBalanceFollowups 准确识别往来与预收触发阈值', () => {
    expect(detectBalanceFollowups([
      { account_code: '1221', account_name: '其他应收款', debit_balance: 6000, credit_balance: 0 },
      { account_code: '2203', account_name: '预收账款', debit_balance: 0, credit_balance: 10000 },
    ])).toEqual({
      loanAmount: 6000,
      advanceAmount: 10000,
      hasLoanClue: true,
      hasAdvanceClue: false,
    })
  })
})
