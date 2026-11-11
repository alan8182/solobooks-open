/** Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AuditScanPage from '@/pages/audit-scan/index.vue'
import { parseBalanceFileWithPreview, submitHealthScan } from '@/api/healthScan'
import { saveWorkpaperImage } from '@/utils/workpaperImage'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('naive-ui', async () => ({
  ...(await vi.importActual<object>('naive-ui')),
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}))
vi.mock('@/utils/workpaperImage', () => ({ saveWorkpaperImage: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/healthScan', () => ({
  parseBalanceFileWithPreview: vi.fn(),
  submitHealthScan: vi.fn(),
}))

const wrappers: ReturnType<typeof mount>[] = []
function page() {
  const wrapper = mount(AuditScanPage, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}
async function fillProfile(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('select').setValue('OPC')
  await wrapper.find('input[type=month]').setValue('2026-10')
  await wrapper.findAll('select')[1].setValue('SMALL_ENTERPRISE')
  await wrapper.findAll('select')[2].setValue('SMALL_SCALE')
}
async function selectAnswer(wrapper: ReturnType<typeof mount>, name: string, value: string) {
  await wrapper.find(`input[name="${name}"][value="${value}"]`).setValue()
}
const report = {
  totalScore: null, healthLevel: 'UNSCORED', levelLabel: '资料不足', dimensions: [], flags: [], views: {},
  meta: { source: 'backend' }, coverage: { completed: 1, applicable: 4, percent: 25 }, scoreBreakdown: { deduction: 0 },
  checks: [{ code: 'R-01', name: '借款核查', status: 'UNKNOWN', observed: '存在往来余额', missing: ['借款用途'], source: '余额表与问卷', fix: '核实借款凭据', dimension: '资金', deduction: null }],
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(submitHealthScan).mockResolvedValue(report as never)
  vi.mocked(parseBalanceFileWithPreview).mockResolvedValue({
    rows: [{ account_code: '1221', debit_balance: 100 }],
    preview: { periodLabel: '2026-10', balanceType: 'ending', unit: 'yuan', debitTotal: 100, creditTotal: 0, warnings: ['请核对期末列'], hasAmbiguity: true, selectedColumns: { debit: 2, credit: 3 } },
  } as never)
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); document.body.innerHTML = '' })

describe('财税健康体检', () => {
  it('不预选答案，条件追问展开并在条件变更后不提交隐藏答案', async () => {
    const wrapper = page()
    expect(wrapper.findAll('input[type=radio]:checked')).toHaveLength(0)
    expect(wrapper.find('input[name=loanCounterparty]').exists()).toBe(false)
    await fillProfile(wrapper)
    await selectAnswer(wrapper, 'q1', 'UNKNOWN')
    await selectAnswer(wrapper, 'q4', 'NA')
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    await selectAnswer(wrapper, 'loanOccurrence', 'YES')
    await selectAnswer(wrapper, 'loanCounterparty', 'SHAREHOLDER')
    await selectAnswer(wrapper, 'loanRepayment', 'PARTIAL')
    await selectAnswer(wrapper, 'loanPurpose', 'BUSINESS')
    await selectAnswer(wrapper, 'rdActivities', 'YES')
    await selectAnswer(wrapper, 'rdAuxiliaryLedger', 'NA')
    await selectAnswer(wrapper, 'assetExists', 'YES')
    await selectAnswer(wrapper, 'assetPutIntoUse', 'UNKNOWN')
    await selectAnswer(wrapper, 'advanceReceived', 'YES')
    await selectAnswer(wrapper, 'advancePerformance', 'PARTIAL')
    await selectAnswer(wrapper, 'loanOccurrence', 'NO')
    expect(wrapper.find('input[name=loanCounterparty]').exists()).toBe(false)
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'balance.csv')] })
    await input.trigger('change'); await flushPromises()
    await wrapper.findAll('.preview-box input[type=checkbox]')[1].setValue(true)
    await wrapper.find('.scan-cta-btn').trigger('click')
    await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'balance', profile: { companyType: 'OPC', period: '2026-10', accountingStandard: 'SMALL_ENTERPRISE', vatType: 'SMALL_SCALE' },
      answers: { loanOccurrence: 'NO', rdActivities: 'YES', rdAuxiliaryLedger: 'NA', assetExists: 'YES', assetPutIntoUse: 'UNKNOWN', advanceReceived: 'YES', advancePerformance: 'PARTIAL' },
    }))
  })
  it('展示覆盖率、缺失、依据及未评分，并在复制和图片中保留范围来源及免责声明', async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboard } })
    const wrapper = page()
    await fillProfile(wrapper)
    await selectAnswer(wrapper, 'q1', 'NA')
    await wrapper.find('.scan-cta-btn').trigger('click')
    await flushPromises()
    const paper = wrapper.find('.audit-workpaper')
    expect(paper.text()).toContain('未评分')
    expect(paper.text()).toContain('25%')
    expect(paper.text()).toContain('借款用途')
    expect(paper.text()).toContain('余额表与问卷')
    expect(paper.text()).toContain('范围：问卷回答')
    expect(paper.text()).toContain('来源：服务端分析')
    expect(paper.text()).toContain('不构成审计')
    await wrapper.findAll('.report-actions button')[0].trigger('click')
    expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('范围：问卷回答'))
    expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('来源：服务端分析'))
    expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('不构成审计'))
    await wrapper.findAll('.report-actions button')[1].trigger('click')
    expect(saveWorkpaperImage).toHaveBeenCalledWith(paper.element, expect.any(String))
    expect(wrapper.text()).not.toContain('零个税')
    expect(wrapper.text()).not.toContain('银行直接拒贷')
  })
  it('余额表需预览确认，保留不完整标记并传递识别元数据', async () => {
    const wrapper = page()
    await fillProfile(wrapper)
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    const file = new File(['x'], 'balance.csv', { type: 'text/csv' })
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('请核对期末列')
    expect(wrapper.text()).toContain('存在识别歧义')
    expect(wrapper.text()).toContain('识别列')
    await wrapper.find('.scan-cta-btn').trigger('click')
    expect(submitHealthScan).not.toHaveBeenCalled()
    const checks = wrapper.findAll('.preview-box input[type=checkbox]')
    await checks[1].setValue(true)
    await wrapper.find('.scan-cta-btn').trigger('click')
    await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'balance', rows: [{ account_code: '1221', debit_balance: 100 }],
      balanceMeta: { period: '2026-10', balanceType: 'ending', currencyUnit: 'yuan', confirmed: true, complete: false },
    }))
  })
  it.each(['ending', 'opening', 'unknown'])('通用借贷表头允许明确选择 %s 并按选择提交', async (balanceType) => {
    vi.mocked(parseBalanceFileWithPreview).mockResolvedValueOnce({
      rows: [{ account_code: '1001', account_name: '库存现金', debit_balance: 1, credit_balance: 0 }],
      preview: { periodLabel: '2026-10', balanceType: 'unknown', unit: 'yuan', debitTotal: 1, creditTotal: 0, warnings: ['余额期初或期末无法确定，请确认。'], hasAmbiguity: true, selectedColumns: {} },
    } as never)
    const wrapper = page()
    await fillProfile(wrapper)
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'generic.csv')] })
    await input.trigger('change'); await flushPromises()
    const checks = wrapper.findAll('.preview-box input[type=checkbox]')
    await checks[0].setValue(true)
    await checks[1].setValue(true)
    if (balanceType !== 'unknown') {
      await wrapper.find('.preview-box select').setValue(balanceType)
      expect((checks[1].element as HTMLInputElement).checked).toBe(false)
      await wrapper.find('.scan-cta-btn').trigger('click')
      expect(submitHealthScan).not.toHaveBeenCalled()
      await checks[1].setValue(true)
    }
    await wrapper.find('.scan-cta-btn').trigger('click'); await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({ balanceMeta: expect.objectContaining({ balanceType, complete: true, confirmed: true }) }))
  })

  it('期初余额识别后不可修改为期末，按期初提交', async () => {
    vi.mocked(parseBalanceFileWithPreview).mockResolvedValueOnce({
      rows: [{ account_code: '1001', debit_balance: 1, credit_balance: 0 }],
      preview: { periodLabel: '2026-10', balanceType: 'opening', unit: 'yuan', debitTotal: 1, creditTotal: 0, warnings: [], hasAmbiguity: false, selectedColumns: {} },
    } as never)
    const wrapper = page()
    await fillProfile(wrapper)
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'opening.csv')] })
    await input.trigger('change'); await flushPromises()
    expect((wrapper.find('input[readonly]').element as HTMLInputElement).value).toBe('期初余额（不作为期末余额评分）')
    await wrapper.findAll('.preview-box input[type=checkbox]')[1].setValue(true)
    await wrapper.find('.scan-cta-btn').trigger('click'); await flushPromises()
    expect(submitHealthScan).toHaveBeenCalledWith(expect.objectContaining({ balanceMeta: expect.objectContaining({ balanceType: 'opening' }) }))
  })
  it('上传新文件失败时清除旧数据，不能误提交旧余额表', async () => {
    const wrapper = page()
    await fillProfile(wrapper)
    await wrapper.findAll('.mode-tab-btn')[1].trigger('click')
    const input = wrapper.find('input[type=file]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'first.csv')] })
    await input.trigger('change'); await flushPromises()
    await wrapper.findAll('.preview-box input[type=checkbox]')[1].setValue(true)
    vi.mocked(parseBalanceFileWithPreview).mockRejectedValueOnce(new Error('解析失败'))
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['x'], 'bad.csv')] })
    await input.trigger('change'); await flushPromises()
    expect(wrapper.find('.preview-box').exists()).toBe(false)
    await wrapper.find('.scan-cta-btn').trigger('click')
    expect(submitHealthScan).not.toHaveBeenCalled()
  })
})
