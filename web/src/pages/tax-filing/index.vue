<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { accountingErrorText } from '@/utils/errors'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NInputNumber, NPopconfirm, NSelect, NSpin, NSwitch, NTag, NTooltip, useMessage,
} from 'naive-ui'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import BossTaxCheatSheet from '@/components/business/BossTaxCheatSheet.vue'
import { SoloSkeleton } from '@/components/ui'
import {
  FILING_CARD_ORDER,
  calculateDeadlineSummary,
  calculateExemptionGauge,
  formatCopyAmount,
  generateTaxCopilotInsight,
  periodToQuarter,
  quarterLabel,
  resolveNextPendingCard,
  summarizeOtherTaxes,
  type FilingCardKey,
} from '@/utils/taxFiling'

const route = useRoute()
const router = useRouter()
const message = useMessage()

interface FilingField {
  code: string
  section: string
  label: string
  amount: number
  editable: boolean
}
interface DeadlineInfo {
  deadlineId: string | null
  status: string
  dueDate: string | null
  daysLeft: number | null
}
interface ChecklistItem {
  taxKind: string
  name: string
  guide: string
  hint: string
  canExport?: boolean
  deadlineId: string | null
  status: string
  dueDate: string | null
  daysLeft: number | null
  needsEmployeeSetup?: boolean
  actionPath?: string | null
  actionLabel?: string | null
  itemPeriod?: string
}
interface CrossDiff {
  code: string
  label: string
  left: number
  right: number
  difference: number
  hint: string
}
interface Package {
  period: string
  periodType: string
  taxRegistrations?: { synced: boolean; count: number; groups: string[] }
  filingCycles?: { vat?: 'month' | 'quarter' }
  vatCycleMismatch?: boolean
  vat: {
    totalSales?: number
    isBelowThreshold?: boolean
    totalVatPayable?: number
    surchargeTotal?: number
    filingFields: FilingField[]
    warnings: string[]
    blockingIssues: string[]
    deadline: DeadlineInfo
  }
  cit: {
    revenue?: number
    totalProfit?: number
    preferentialTax?: number
    note?: string
    fields?: { line: string; label: string; amount: number }[]
    deadline: DeadlineInfo
  } | null
  citNotice?: {
    entityType: string
    label: string
    citApplicable: boolean
    incomeTaxKind: string
    incomeTaxLabel: string
    notice: string
  }
  businessIncomeIit: {
    applicable: boolean
    period: string
    filingCycle: string
    entityType: string
    revenue: number
    expenses: number
    profitTotal: number
    taxableIncome: number
    tax: number
    halvingReduction: number
    halvingApplied: boolean
    suggestedPayable: number
    previouslyPaid: number
    notes: string[]
    deadline: DeadlineInfo
  } | null
  crossCheck: {
    bookSales: number
    bookCount?: number
    invoiceTotal: number
    invoiceCount?: number
    bankInflow: number
    bankInflowCount?: number
    differences: CrossDiff[]
    consistent: boolean
  }
  deductionMaterials: {
    bookExpenses: number
    totalDeductible: number
    totalNonDeductible: number
    pendingDeductibility: number
  }
  preferences: { code: string; title: string; detail: string; action: string }[]
  checklist: ChecklistItem[]
  summary: {
    readyToFile: boolean
    blockingIssues: string[]
    warnings: string[]
    vatPayable: number
    citPayable?: number
    iitPayable?: number
    overdue: string[]
  }
}

const now = new Date()
const authStore = useAuthStore()
const viewStore = useWorkspaceViewStore()
// 申报周期决定期次粒度：月报公司按月出包，季报公司按季出包。
// 结账始终按月，这里的「期次」只是申报汇总口径。
const vatCycle = computed<'month' | 'quarter'>(() => {
  const company = authStore.companies.find(c => c.company_id === authStore.currentCompanyId)
  return company?.vatFilingCycle === 'month' ? 'month' : 'quarter'
})

// 默认期次 = 最近一个已结束的申报期（本月还没到征期，10 月打开时默认 9 月 / Q3）
const defaultQuarter = (() => {
  const quarterStart = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1)
  const lastEnded = new Date(quarterStart.getFullYear(), quarterStart.getMonth(), 0)
  return `${lastEnded.getFullYear()}-Q${Math.floor(lastEnded.getMonth() / 3) + 1}`
})()
const defaultMonth = (() => {
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
})()

function periodLabel(value: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(value)
  return m ? `${m[1]} 年 ${parseInt(m[2], 10)} 月` : quarterLabel(value)
}

function buildPeriodOptions(cycle: 'month' | 'quarter') {
  if (cycle === 'month') {
    return Array.from({ length: 6 }).map((_, i) => {
      const d = new Date(now.getFullYear(), now.getMonth() - 1 - i, 1)
      const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      return { label: periodLabel(value), value }
    })
  }
  return Array.from({ length: 6 }).map((_, i) => {
    const qIndex = Math.floor(now.getMonth() / 3) - i
    const year = now.getFullYear() + Math.floor(qIndex / 4)
    const q = ((qIndex % 4) + 4) % 4 + 1
    return { label: `${year} 年第 ${q} 季度`, value: `${year}-Q${q}` }
  })
}

const periodOptions = ref(buildPeriodOptions(vatCycle.value))

const rawQueryPeriod = route.query.period
  ? String(route.query.period)
  : (route.query.month ? String(route.query.month) : '')
// 月报公司不能再把月度折算成季度，否则会拿季度包当月度报；
// 但允许显式看季度汇总（页头会提示口径不一致，仅作参考）。
const queryPeriod = /^\d{4}-Q[1-4]$/.test(rawQueryPeriod)
  ? rawQueryPeriod
  : vatCycle.value === 'month'
    ? (/^\d{4}-\d{2}$/.test(rawQueryPeriod) ? rawQueryPeriod : '')
    : periodToQuarter(rawQueryPeriod)
if (queryPeriod && !periodOptions.value.some(o => o.value === queryPeriod)) {
  periodOptions.value.unshift({ label: periodLabel(queryPeriod), value: queryPeriod })
}

const period = ref(queryPeriod || (vatCycle.value === 'month' ? defaultMonth : defaultQuarter))
const loading = ref(false)
const pkg = ref<Package | null>(null)
const filingBusy = ref('')
const hideZeroFields = ref(true)

// 当前激活的可切换卡片：第 1 张为「报前核对与总览」，后 4 张为独立申报事项卡片
// 支持通过 route.query.card（如 ?card=vat 或 ?card=cit）直接锚定对应卡片
const validCards: FilingCardKey[] = ['overview', 'vat', 'cit', 'fin_statements', 'other_taxes']
const queryCard = String(route.query.card || '')
const activeCard = ref<FilingCardKey>(
  validCards.includes(queryCard as FilingCardKey) ? (queryCard as FilingCardKey) : 'overview',
)

const cardTitleMap: Record<FilingCardKey, string> = {
  overview: '报前核对与总览',
  vat: '增值税申报',
  cit: '企业所得税预缴',
  fin_statements: '财务报表报送',
  other_taxes: '其他税费确认',
}

async function load() {
  loading.value = true
  try {
    const data = await apiClient.get(`/api/tax-filing/packages/${period.value}`)
    pkg.value = data as unknown as Package
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    loading.value = false
  }
}

async function handlePeriodChange() {
  activeCard.value = 'overview'
  await load()
}

function advanceAfterResolve(previousCard: FilingCardKey, actionText: string) {
  const nextCard = resolveNextPendingCard(pkg.value, previousCard)
  if (nextCard && nextCard !== previousCard) {
    activeCard.value = nextCard
    message.success(`${actionText}，已自动切换到「${cardTitleMap[nextCard]}」`)
  } else if (nextCard === previousCard) {
    message.success(`${actionText}，请继续确认本卡片剩余待办项`)
  } else {
    message.success(`🎉 ${actionText}！本期所有申报事项已全部办结`)
  }
}

async function markFiled(deadlineId: string | null, kindName: string, zero = false) {
  if (!deadlineId) {
    message.warning('该事项没有关联征期记录')
    return
  }
  const currentCard = activeCard.value
  filingBusy.value = deadlineId
  try {
    await apiClient.post(`/api/tax-filing/items/${deadlineId}/file`, {
      evidence: { via: 'tax-filing-page', zeroFiling: zero, filedAt: new Date().toISOString() },
    })
    await load()
    advanceAfterResolve(currentCard, `${kindName} 已标记完成`)
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    filingBusy.value = ''
  }
}

function handleBossMarkFiled(deadlineId: string | null) {
  if (!deadlineId) {
    message.warning('该事项没有关联征期记录，请刷新页面后重试')
    return
  }
  if (pkg.value?.vat?.deadline?.deadlineId === deadlineId) {
    markFiled(deadlineId, '增值税及附加税费', (pkg.value?.vat?.totalSales ?? 0) === 0)
    return
  }
  if (pkg.value?.cit?.deadline?.deadlineId === deadlineId) {
    markFiled(deadlineId, '企业所得税预缴')
    return
  }
  if (pkg.value?.businessIncomeIit?.deadline?.deadlineId === deadlineId) {
    markFiled(deadlineId, '经营所得个税预缴')
    return
  }
  const item = pkg.value?.checklist?.find((t: any) => t.deadlineId === deadlineId)
  if (item) {
    markFiled(deadlineId, item.name || '申报事项')
  }
}

// ── 经营所得 B 表年度汇算 ──
interface IitAnnualCard {
  applicable: boolean
  profitTotal?: number
  adjustments?: { total: number; ownerSalary: number; ownerSalarySource: string; undocumentedExpenses: number }
  deductions?: { total: number; statutory: number }
  taxableIncome?: number
  tax?: number
  halvingApplied?: boolean
  prepaidTotal?: number
  finalPayable?: number
  finalRefund?: number
  notes?: string[]
  deadline?: DeadlineInfo | null
}
const iitAnnual = ref<IitAnnualCard | null>(null)
const iitAnnualLoading = ref(false)
const iitAnnualYear = ref<number>(now.getFullYear() - 1)
const iitAnnualInput = ref<{
  ownerSalary: number | null
  statutoryDeduction: number | null
  specialAdditionalDeductions: number | null
  prepaidTotal: number | null
}>({ ownerSalary: null, statutoryDeduction: null, specialAdditionalDeductions: null, prepaidTotal: null })
const iitAnnualYearOptions = Array.from({ length: 3 }).map((_, i) => {
  const year = now.getFullYear() - i
  return { label: `${year} 年度`, value: year }
})

async function loadIitAnnual() {
  iitAnnualLoading.value = true
  try {
    const payload: Record<string, unknown> = { fiscalYear: iitAnnualYear.value }
    const input = iitAnnualInput.value
    if (input.ownerSalary != null) payload.ownerSalary = input.ownerSalary
    if (input.statutoryDeduction != null) payload.statutoryDeduction = input.statutoryDeduction
    if (input.specialAdditionalDeductions != null) payload.specialAdditionalDeductions = input.specialAdditionalDeductions
    if (input.prepaidTotal != null) payload.prepaidTotal = input.prepaidTotal
    const data = (await apiClient.post<IitAnnualCard>('/api/tax-filing/iit-annual-assist', payload)) as unknown as IitAnnualCard
    if (!data.applicable) {
      message.warning('当前主体不适用经营所得个税汇算')
      iitAnnual.value = null
      return
    }
    iitAnnual.value = data
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    iitAnnualLoading.value = false
  }
}

async function waiveDeadline(deadlineId: string | null, kindName: string) {
  if (!deadlineId) {
    message.warning('该事项没有关联征期记录')
    return
  }
  const currentCard = activeCard.value
  filingBusy.value = deadlineId
  try {
    await apiClient.post(`/api/tax-filing/items/${deadlineId}/waive`, { reason: 'manual' })
    await load()
    advanceAfterResolve(currentCard, `「${kindName}」已标记为本期不适用`)
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    filingBusy.value = ''
  }
}

// 导出财务报表报送 XLS（税局标准格式，已填入本期账载三表数据）
const exporting = ref(false)

async function exportFinancialXls() {
  exporting.value = true
  try {
    const blob = (await apiClient.get('/api/tax-report/xiaoqiye/month-quarter/export', {
      params: { period: period.value, period_type: 'quarter' },
      responseType: 'blob',
      timeout: 120000,
    })) as unknown as Blob
    if (!(blob instanceof Blob)) throw new Error('导出响应格式异常')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `财务报表报送（小企业会计准则）_${period.value}.xls`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    message.success('财务报表 XLS 已导出，请在电子税务局「财务报表报送」模块上传')
  } catch (err) {
    let detail = accountingErrorText(err, '导出失败')
    const respData = (err as { response?: { data?: unknown } }).response?.data
    if (respData instanceof Blob) {
      try {
        const parsed = JSON.parse(await respData.text())
        if (typeof parsed?.detail === 'string') detail = parsed.detail
      } catch { /* 保留原始错误信息 */ }
    }
    message.error(detail)
  } finally {
    exporting.value = false
  }
}

function money(v: unknown): string {
  const n = Number(v ?? 0)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function copyAmount(val: number | string | undefined | null, label: string) {
  const text = formatCopyAmount(val)
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const input = document.createElement('input')
      input.value = text
      document.body.appendChild(input)
      input.select()
      document.execCommand('copy')
      input.remove()
    }
    message.success(`已复制 ${label}: ¥${text}`)
  } catch {
    message.info(`金额: ${text}`)
  }
}

const exemptionGauge = computed(() => {
  return calculateExemptionGauge(
    pkg.value?.vat.totalSales ?? 0,
    period.value,
    pkg.value?.periodType ?? 'quarter',
  )
})

const copilotInsight = computed(() => {
  if (!pkg.value) return null
  return generateTaxCopilotInsight(pkg.value)
})

const vatFieldsBySection = computed(() => {
  const sections = new Map<string, FilingField[]>()
  for (const f of pkg.value?.vat.filingFields ?? []) {
    const list = sections.get(f.section) ?? []
    list.push(f)
    sections.set(f.section, list)
  }
  return [...sections.entries()]
})

// 电子税务局填报路径指引：按本期数据动态生成分步抄报步骤与防错提示
interface GuideAmountRow { label: string; amount: number; copyLabel: string }
interface GuideStep { title: string; detail: string; amounts?: GuideAmountRow[] }

const vatFilingGuide = computed(() => {
  const pkgData = pkg.value
  const vat = pkgData?.vat
  if (!pkgData || !vat) return null
  const fieldAmount = (code: string) => vat.filingFields.find(f => f.code === code)?.amount ?? 0
  const totalSales = vat.totalSales ?? 0
  const steps: GuideStep[] = []
  const warnings: string[] = []

  if (totalSales === 0) {
    steps.push(
      { title: '无需填报销售额', detail: '本期账载销售额为 0，申报页各销售额入口保持 0 即可。' },
      { title: '直接「下一步」完成零申报', detail: '核对「本期应补（退）税额」为 ¥0.00 后，点「下一步」提交申报。' },
    )
    return { steps, warnings }
  }

  if (vat.isBelowThreshold) {
    const dialogRows: GuideAmountRow[] = []
    const goodsExempt = fieldAmount('simple.goods.exempt')
    const serviceExempt = fieldAmount('simple.services.exempt')
    if (goodsExempt > 0) dialogRows.push({ label: '「货物及劳务」弹窗 · 1%征收率行', amount: goodsExempt, copyLabel: '本期销售额·货物及劳务' })
    if (serviceExempt > 0) dialogRows.push({ label: '「服务、不动产和无形资产」弹窗 · 1%征收率行', amount: serviceExempt, copyLabel: '本期销售额·服务、不动产和无形资产' })
    steps.push(
      {
        title: '「本期销售情况」→ 点各销售额入口右侧编辑',
        detail: `在弹窗「1%征收率」行填入下方金额（免税销售额含税=不含税，直接填账载金额），其余行保持 0；「免税」行只读不用管——${pkg.value?.periodType === 'month' ? '本月未超 10 万' : '本季未超 30 万'}时系统会自动归集为「免税销售额」。`,
        amounts: dialogRows,
      },
      {
        title: '「预览表单」核对归集结果',
        detail: '主表第 9/10 栏「免税销售额」应等于你填的金额（本年累计自动累加），第 1 栏「应征增值税不含税销售额」应保持 0.00。',
      },
      {
        title: '核对税额并申报',
        detail: `「本期应补（退）税额」应为 ¥${money(pkgData.summary.vatPayable)}，确认后点「下一步」完成申报；附加税费同步为 0，无需处理。`,
      },
    )
    warnings.push(
      '若预览表单中金额落在第 1 栏「应征增值税不含税销售额」（应税行）而非第 9/10 栏，点右上角「我要填表」进报表，直接改第 10 栏「其中：小微企业免税销售额」（第 9 栏自动合计，第 19/20 栏免税额自动带出）。',
    )
    if (pkgData.crossCheck.invoiceTotal === 0) {
      warnings.push('本期发票开具为 ¥0.00，税局「自动归集发票」预填会是 0.00——这不是数据没同步，按上述步骤手动填报即可，否则会漏报销售收入。')
    }
    return { steps, warnings }
  }

  // 超免税额度：引导式弹窗按 1% 征收率填报（系统已价税分离）
  const goodsRate1 = fieldAmount('simple.goods.rate_1')
  const serviceRate1 = fieldAmount('simple.services.rate_1')
  if (goodsRate1 > 0) {
    steps.push({
      title: '「本期销售情况」→ 点「货物及劳务销售额」右侧编辑',
      detail: '在弹窗「1%征收率」行填入下方金额（不含税，已按 1% 换算）；3%/5% 及其中项保持 0。',
      amounts: [{ label: '1%征收率行', amount: goodsRate1, copyLabel: '1%征收率·货物及劳务' }],
    })
  }
  if (serviceRate1 > 0) {
    steps.push({
      title: '→ 点「服务、不动产和无形资产销售额」右侧编辑',
      detail: '在弹窗「1%征收率」行填入下方金额（不含税，已按 1% 换算）；其余行保持 0。',
      amounts: [{ label: '1%征收率行', amount: serviceRate1, copyLabel: '1%征收率·服务、不动产和无形资产' }],
    })
  }
  steps.push({
    title: '核对税额并申报扣款',
    detail: `弹窗确定后「本期应补（退）税额」应为 ¥${money(pkgData.summary.vatPayable)}，点「下一步」完成申报并及时缴款；附加税费随征自动计算。`,
  })
  return { steps, warnings }
})

// 完整栏次表：给非零行标注电子税局里的实际填写位置
function fieldLocationHint(f: FilingField): string {
  if (f.section === '税费试算' || f.section === '附加税费') return '系统自动试算，无需填写'
  if (f.label === '免税销售额') {
    const col = f.code.includes('goods') ? '货物及劳务' : '服务、不动产和无形资产'
    return `「本期销售情况」弹窗·1%征收率行 → 自动归集主表第 9/10 栏 · ${col}`
  }
  return `「本期销售情况」${f.section}弹窗 · ${f.label}行`
}

// 财务报表事项单独提取为第 4 张卡片
const finStatementCheckItem = computed(() => {
  return (pkg.value?.checklist ?? []).find(item => item.taxKind === 'fin_statements')
})

// 其他附税事项（印花税、个税扣缴等）提取为第 5 张卡片
const otherChecklistItems = computed(() => {
  return (pkg.value?.checklist ?? []).filter(item => item.taxKind !== 'fin_statements')
})

const otherTaxesSummary = computed(() => {
  return summarizeOtherTaxes(pkg.value?.checklist)
})

// 折叠开关：增值税与所得税完整栏次
const showFullVatTable = ref(false)
const showFullCitTable = ref(false)

const totalEstimatedTax = computed(() => {
  if (!pkg.value) return 0
  return (pkg.value.summary?.vatPayable || 0)
    + (pkg.value.summary?.citPayable || 0)
    + (pkg.value.summary?.iitPayable || 0)
})

const allDeadlines = computed(() => {
  if (!pkg.value) return []
  const list: (DeadlineInfo | ChecklistItem)[] = []
  if (pkg.value.vat?.deadline) list.push(pkg.value.vat.deadline)
  if (pkg.value.cit?.deadline) list.push(pkg.value.cit.deadline)
  if (pkg.value.checklist) list.push(...pkg.value.checklist)
  return list
})

const deadlineSummary = computed(() => {
  return calculateDeadlineSummary(allDeadlines.value)
})

const nearestDaysLeft = computed(() => deadlineSummary.value.nearestDaysLeft)

const taskStats = computed(() => {
  if (!pkg.value) return { total: 0, resolved: 0, filed: 0, waived: 0, pending: 0, overdue: 0 }
  const slots = [
    pkg.value.vat?.deadline,
    pkg.value.cit?.deadline,
    ...(pkg.value.checklist ?? []),
  ].filter((s): s is DeadlineInfo | ChecklistItem => !!s && !!s.status)
  const filed = slots.filter(s => s.status === 'filed').length
  const waived = slots.filter(s => s.status === 'waived').length
  return {
    total: slots.length,
    resolved: filed + waived,
    filed,
    waived,
    pending: slots.length - filed - waived,
    overdue: deadlineSummary.value.overdueCount,
  }
})

function statusTag(status: string) {
  if (status === 'filed') return { text: '已完成', type: 'success' as const }
  if (status === 'waived') return { text: '不适用', type: 'default' as const }
  if (status === 'untracked') return { text: '未跟踪', type: 'default' as const }
  return { text: '待申报', type: 'warning' as const }
}

function deadlineText(d: DeadlineInfo | ChecklistItem): string {
  if (!d.dueDate) return ''
  if (d.status === 'waived') return `原截止 ${d.dueDate}（已豁免）`
  const days = d.daysLeft
  if (days == null) return `截止 ${d.dueDate}`
  if (days < 0) return `已逾期 ${-days} 天（截止 ${d.dueDate}）`
  if (days === 0) return `今日截止（${d.dueDate}）`
  return `剩 ${days} 天（截止 ${d.dueDate}）`
}

// 5 张可切换导航卡片展示元数据
const switcherCards = computed(() => {
  const p = pkg.value
  if (!p) return []

  const hasBlockers = (p.summary?.blockingIssues?.length ?? 0) > 0
  const overviewTag = hasBlockers
    ? { text: '存在阻断', type: 'error' as const }
    : (p.crossCheck.consistent
        ? { text: '核对一致', type: 'success' as const }
        : { text: `${p.crossCheck.differences.length} 项差额`, type: 'warning' as const })

  const citApplicable = p.citNotice ? p.citNotice.citApplicable : !!p.cit
  const citAmount = p.summary.citPayable ?? 0
  const finStatus = finStatementCheckItem.value?.status ?? 'untracked'
  const otherSummary = otherTaxesSummary.value
  const otherTag = otherSummary.status === 'pending'
    ? { text: `${otherSummary.pendingCount} 项待办`, type: 'warning' as const }
    : statusTag(otherSummary.status)

  return [
    {
      key: 'overview' as FilingCardKey,
      step: 1,
      title: '报前核对与总览',
      metric: totalEstimatedTax.value === 0 ? '全额免征 ¥0.00' : `预计 ¥${money(totalEstimatedTax.value)}`,
      sub: p.crossCheck.consistent ? '账·票·流水三方一致' : '建议先核对账票差额',
      tag: overviewTag,
      isDone: p.crossCheck.consistent && !hasBlockers,
    },
    {
      key: 'vat' as FilingCardKey,
      step: 2,
      title: '增值税申报',
      metric: p.summary.vatPayable > 0 ? `应缴 ¥${money(p.summary.vatPayable)}` : '免征 ¥0.00',
      sub: p.vat.isBelowThreshold ? '小规模未超 30 万免税' : '按 1% 征收率填报',
      tag: statusTag(p.vat.deadline.status),
      isDone: p.vat.deadline.status === 'filed' || p.vat.deadline.status === 'waived',
    },
    citApplicable && p.cit
      ? {
          key: 'cit' as FilingCardKey,
          step: 3,
          title: '企业所得税预缴',
          metric: citAmount > 0 ? `应缴 ¥${money(citAmount)}` : '应缴 ¥0.00',
          sub: 'A200000 季度预缴表',
          tag: statusTag(p.cit.deadline.status),
          isDone: p.cit.deadline.status === 'filed' || p.cit.deadline.status === 'waived',
        }
      : p.businessIncomeIit
        ? {
            key: 'cit' as FilingCardKey,
            step: 3,
            title: '经营所得个税预缴',
            metric: p.businessIncomeIit.suggestedPayable > 0
              ? `应缴 ¥${money(p.businessIncomeIit.suggestedPayable)}`
              : '应缴 ¥0.00',
            sub: p.businessIncomeIit.halvingApplied ? 'A 表 · 已含减半优惠' : 'A 表 · 累计预缴口径',
            tag: statusTag(p.businessIncomeIit.deadline.status),
            isDone: p.businessIncomeIit.deadline.status === 'filed' || p.businessIncomeIit.deadline.status === 'waived',
          }
        : {
            key: 'cit' as FilingCardKey,
            step: 3,
            title: '企业所得税预缴',
            metric: '不适用',
            sub: p.citNotice?.incomeTaxLabel ?? '经营所得个人所得税',
            tag: { text: '不适用', type: 'default' as const },
            isDone: true,
          },
    {
      key: 'fin_statements' as FilingCardKey,
      step: 4,
      title: '财务报表报送',
      metric: '标准三表 XLS',
      sub: '一键导出上传税局',
      tag: statusTag(finStatus),
      isDone: finStatus === 'filed' || finStatus === 'waived',
    },
    {
      key: 'other_taxes' as FilingCardKey,
      step: 5,
      title: '其他税费确认',
      metric: otherSummary.pendingCount > 0
        ? `待确认 ${otherSummary.pendingCount} 项`
        : `${otherSummary.resolvedCount}/${otherSummary.totalCount} 已确认`,
      sub: '印花税 · 个税扣缴等',
      tag: otherTag,
      isDone: otherSummary.status === 'filed' || otherSummary.status === 'waived',
    },
  ]
})

const nextPendingFromOverview = computed(() => {
  return resolveNextPendingCard(pkg.value, 'overview')
})

const prevCardKey = computed<FilingCardKey | null>(() => {
  const idx = FILING_CARD_ORDER.indexOf(activeCard.value)
  return idx > 0 ? FILING_CARD_ORDER[idx - 1] : null
})

const nextCardKey = computed<FilingCardKey | null>(() => {
  const idx = FILING_CARD_ORDER.indexOf(activeCard.value)
  return idx >= 0 && idx < FILING_CARD_ORDER.length - 1 ? FILING_CARD_ORDER[idx + 1] : null
})

function startFilingFromOverview() {
  activeCard.value = nextPendingFromOverview.value ?? 'vat'
}

onMounted(load)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="tax-filing-page">
    <!-- 页头：仅保留期次切换与刷新，入口干净无重复 -->
    <div class="page-header">
      <div class="header-left">
        <div class="title-row">
          <h2>税务申报</h2>
          <NTag size="small" type="primary" round :bordered="false" class="mode-tag">小规模纳税人</NTag>
        </div>
      </div>
      <div class="header-right">
        <NSelect
          v-model:value="period"
          :options="periodOptions"
          style="width: 170px"
          size="small"
          @update:value="handlePeriodChange"
        />
        <NButton size="small" secondary @click="load" :loading="loading">
          刷新数据
        </NButton>
      </div>
    </div>

    <!-- 税费种认定入口：让用户知道本期「该报哪些税」是按税局认定表来的，而不是系统猜的 -->
    <div class="taxreg-bar">
      <span class="taxreg-bar__dot" :class="{ 'taxreg-bar__dot--on': pkg?.taxRegistrations?.synced }" />
      <span class="taxreg-bar__text">
        <template v-if="pkg?.taxRegistrations?.synced">
          已按税费种认定表生成征期：{{ pkg.taxRegistrations.count }} 项认定，覆盖
          {{ pkg.taxRegistrations.groups.length }} 张申报表
        </template>
        <template v-else>尚未同步税费种认定表，当前按内置税种清单生成征期</template>
      </span>
      <NButton size="tiny" text type="primary" @click="router.push('/tax-registration')">
        {{ pkg?.taxRegistrations?.synced ? '维护认定' : '去同步认定' }}
      </NButton>
    </div>

    <!-- 本期申报进度条 -->
    <div v-if="pkg" class="filing-progress-strip">
      <div class="filing-progress-strip-head">
        <span class="filing-progress-strip-title">本期申报进度</span>
        <span class="filing-progress-strip-count">
          {{ taskStats.resolved }} / {{ taskStats.total }} 已办结
          <template v-if="taskStats.pending > 0"> · 还有 {{ taskStats.pending }} 项待办</template>
        </span>
      </div>
      <div class="progress-track">
        <div
          class="progress-fill"
          :style="{ width: `${(taskStats.resolved / Math.max(1, taskStats.total)) * 100}%` }"
        />
      </div>
    </div>

    <!-- 首次加载（还没有任何数据）走骨架：先摆出卡片结构，比整屏转圈的感知速度快一个量级。
         已有数据时的刷新仍然用 NSpin 遮罩，避免内容闪一下又回来。 -->
    <div v-if="!pkg && loading" class="tax-skeleton" aria-busy="true" aria-label="正在加载申报数据">
      <div class="sk-switcher">
        <SoloSkeleton v-for="n in 5" :key="n" variant="rect" height="58px" />
      </div>
      <SoloSkeleton variant="card" :rows="4" />
      <SoloSkeleton variant="card" :rows="3" />
    </div>

    <NSpin v-else :show="loading">
      <template v-if="pkg">
        <BossTaxCheatSheet
          v-if="viewStore.isBoss"
          :pkg="pkg"
          :period="period"
          :loading="loading"
          :exporting="exporting"
          @switch-mode="viewStore.setMode('accounting')"
          @refresh="load"
          @mark-filed="handleBossMarkFiled"
          @export-xls="exportFinancialXls"
        />

        <template v-else>
          <NAlert
            v-if="pkg.vatCycleMismatch"
            type="warning"
            :bordered="false"
            class="cycle-mismatch-alert"
            style="margin-bottom: 12px;"
          >
            你的增值税申报周期是<strong>{{ pkg.filingCycles?.vat === 'month' ? '月报' : '季报' }}</strong>，
            当前查看的是<strong>{{ pkg.periodType === 'month' ? '月度' : '季度' }}</strong>申报期。
            请切换到对应期次再申报，本页仅作汇总参考。
          </NAlert>
        <!-- 顶部 5 张可切换导航卡片栏 -->
        <nav class="switcher-grid" aria-label="税务申报步骤卡片切换">
          <button
            v-for="card in switcherCards"
            :key="card.key"
            type="button"
            class="switcher-card"
            :class="{
              'is-active': activeCard === card.key,
              'is-done': card.isDone && activeCard !== card.key,
            }"
            @click="activeCard = card.key"
          >
            <div class="switcher-card-top">
              <span class="switcher-step-num">{{ card.step }}</span>
              <span class="switcher-card-title">{{ card.title }}</span>
              <NTag size="tiny" :type="card.tag.type" :bordered="false" class="switcher-tag">
                {{ card.tag.text }}
              </NTag>
            </div>
            <div class="switcher-card-metric">{{ card.metric }}</div>
            <div class="switcher-card-sub">{{ card.sub }}</div>
          </button>
        </nav>

        <!-- 单屏聚焦工作区：每次仅展示当前选中的 1 张卡片 -->
        <section class="active-card-stage">
          <!-- ==================== 卡片 1：报前核对与税情总览 ==================== -->
          <NCard v-if="activeCard === 'overview'" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">1</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">报前核对与税情总览</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      申报前确认本期预计税额、免税额度及账·票·流水比对结果，确认无误即可开始抄报
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box">
                <NTag
                  size="small"
                  :type="taskStats.pending === 0 ? 'success' : (taskStats.overdue > 0 ? 'error' : 'info')"
                  :bordered="false"
                >
                  {{ taskStats.pending === 0 ? '本期全部办结' : `待办 ${taskStats.pending} 项 · 已办 ${taskStats.resolved}/${taskStats.total}` }}
                </NTag>
                <span class="task-deadline-text">
                  {{ nearestDaysLeft != null ? (nearestDaysLeft >= 0 ? `距离截止还剩 ${nearestDaysLeft} 天` : `已逾期 ${-nearestDaysLeft} 天`) : '所有征期事项已了结' }}
                </span>
              </div>
            </div>

            <!-- 3 格核心定心丸看板 -->
            <div class="overview-grid">
              <!-- 核心指标 1：本期预计总税额 -->
              <div class="overview-card primary-tax-card">
                <span class="overview-label">本期预计应纳税额</span>
                <div class="overview-amount" :class="{ 'is-zero': totalEstimatedTax === 0 }">
                  <template v-if="totalEstimatedTax === 0">
                    全额免征 ¥0.00
                  </template>
                  <template v-else>
                    ¥ {{ money(totalEstimatedTax) }}
                  </template>
                </div>
                <div class="overview-sub-detail">
                  增值税 {{ pkg.summary.vatPayable > 0 ? `¥ ${money(pkg.summary.vatPayable)}` : '免征 ¥0' }}
                  <template v-if="pkg.citNotice?.citApplicable !== false && pkg.cit">
                    + 企业所得税 {{ (pkg.summary.citPayable || 0) > 0 ? `¥ ${money(pkg.summary.citPayable)}` : '¥0' }}
                  </template>
                  <template v-else-if="pkg.summary.iitPayable">
                    + 经营所得个税 ¥ {{ money(pkg.summary.iitPayable) }}
                  </template>
                  <template v-else>
                    · {{ pkg.citNotice?.incomeTaxLabel || '经营所得个人所得税' }} 见卡片 3
                  </template>
                </div>
              </div>

              <!-- 核心指标 2：申报征期与待办进度 -->
              <div class="overview-card">
                <div class="overview-card-head">
                  <span class="overview-label">申报征期与进度</span>
                  <NTag
                    size="tiny"
                    :type="taskStats.pending === 0 ? 'success' : (taskStats.overdue > 0 ? 'error' : (nearestDaysLeft != null && nearestDaysLeft <= 3 ? 'warning' : 'info'))"
                    :bordered="false"
                  >
                    <template v-if="taskStats.pending === 0">全部办结</template>
                    <template v-else-if="taskStats.overdue > 0">
                      {{ taskStats.overdue }} 项逾期 · {{ taskStats.pending }} 项待办
                    </template>
                    <template v-else>{{ taskStats.pending }} 项待办</template>
                  </NTag>
                </div>
                <div class="overview-value">
                  {{ nearestDaysLeft != null ? (nearestDaysLeft >= 0 ? `剩 ${nearestDaysLeft} 天截止` : `逾期 ${-nearestDaysLeft} 天`) : '全部办结' }}
                </div>
                <div class="overview-progress-bar">
                  <div class="progress-track">
                    <div
                      class="progress-fill"
                      :style="{ width: `${(taskStats.resolved / Math.max(1, taskStats.total)) * 100}%` }"
                    />
                  </div>
                  <span class="progress-text">{{ taskStats.resolved }} / {{ taskStats.total }} 已了结<template v-if="taskStats.waived > 0">（含豁免 {{ taskStats.waived }} 项）</template></span>
                </div>
              </div>

              <!-- 核心指标 3：免税额度监控 -->
              <div class="overview-card">
                <div class="overview-card-head">
                  <span class="overview-label">增值税免税红线 ({{ pkg.periodType === 'month' ? '10万' : '30万' }})</span>
                  <NTag
                    size="tiny"
                    :type="exemptionGauge.status === 'safe' ? 'success' : (exemptionGauge.status === 'warning' ? 'warning' : 'error')"
                    :bordered="false"
                  >
                    {{ exemptionGauge.status === 'danger' ? '超免税额度' : `已用 ${exemptionGauge.percent}%` }}
                  </NTag>
                </div>
                <div class="overview-value gauge-val">
                  ¥ {{ money(exemptionGauge.totalSales) }}
                </div>
                <div class="overview-sub-detail">
                  限额 ¥ {{ money(exemptionGauge.threshold) }} · {{ exemptionGauge.isExempt ? '免征增值税' : '按 1% 征收' }}
                </div>
              </div>
            </div>

            <!-- 智能税情简报条 -->
            <div class="smart-insight-bar" v-if="copilotInsight">
              <span class="insight-badge">🤖 AI 税情简评</span>
              <span class="insight-headline">{{ copilotInsight.headline }}</span>
              <span class="insight-advice">建议：{{ copilotInsight.pathAdvice }}</span>
            </div>

            <!-- 账·票·流水三方核验底稿（在总览卡内直接清晰呈现） -->
            <div class="cross-detail-panel">
              <div class="cross-panel-header" :class="{ 'is-diff': !pkg.crossCheck.consistent }">
                <span class="cross-check-icon">{{ pkg.crossCheck.consistent ? '✓' : '⚠' }}</span>
                <span class="cross-check-title">
                  {{ pkg.crossCheck.consistent ? '账·票·流水三方核验一致，无跨期隐患' : `账·票·流水检测到 ${pkg.crossCheck.differences.length} 项差额提醒` }}
                </span>
              </div>

              <div class="cross-three-columns">
                <div class="column-card">
                  <div class="col-head">
                    <span class="col-name">
                      1. 账载销售额
                      <NTooltip trigger="hover">
                        <template #trigger><i class="ri-information-line sub-info-icon" /></template>
                        已确认的经营收入合计（含税口径，与回款对齐；申报表仍按不含税填报）
                      </NTooltip>
                    </span>
                    <span class="col-count" v-if="pkg.crossCheck.bookCount != null">{{ pkg.crossCheck.bookCount }} 笔入账</span>
                  </div>
                  <div class="col-amount">¥ {{ money(pkg.crossCheck.bookSales) }}</div>
                </div>
                <div class="column-card">
                  <div class="col-head">
                    <span class="col-name">
                      2. 发票开具合计
                      <NTooltip trigger="hover">
                        <template #trigger><i class="ri-information-line sub-info-icon" /></template>
                        当期已开具的普票/专票总额
                      </NTooltip>
                    </span>
                    <span class="col-count" v-if="pkg.crossCheck.invoiceCount != null">{{ pkg.crossCheck.invoiceCount }} 张发票</span>
                  </div>
                  <div class="col-amount">¥ {{ money(pkg.crossCheck.invoiceTotal) }}</div>
                </div>
                <div class="column-card">
                  <div class="col-head">
                    <span class="col-name">
                      3. 银行/微信入账
                      <NTooltip trigger="hover">
                        <template #trigger><i class="ri-information-line sub-info-icon" /></template>
                        销售回款到账（微信/平台打款与直接销售收款，已剔除利息、退款、注资等非销售进账）
                      </NTooltip>
                    </span>
                    <span class="col-count" v-if="pkg.crossCheck.bankInflowCount != null">{{ pkg.crossCheck.bankInflowCount }} 笔流水</span>
                  </div>
                  <div class="col-amount">¥ {{ money(pkg.crossCheck.bankInflow) }}</div>
                </div>
              </div>

              <div v-if="pkg.crossCheck.differences.length" class="diff-container">
                <div v-for="diff in pkg.crossCheck.differences" :key="diff.code" class="diff-row">
                  <div class="diff-title-line">
                    <strong class="diff-name">{{ diff.label }}</strong>
                    <span class="diff-tag">差额 ¥ {{ money(Math.abs(diff.difference)) }}</span>
                  </div>
                  <p class="diff-hint-text">{{ diff.hint }}</p>
                </div>
              </div>
            </div>

            <!-- 企业所得税税前扣除资料状态（原报表中心「税务汇总」并入） -->
            <div class="cross-detail-panel deduction-panel">
              <div class="cross-panel-header" :class="{ 'is-diff': pkg.deductionMaterials.pendingDeductibility > 0 }">
                <span class="cross-check-icon">{{ pkg.deductionMaterials.pendingDeductibility > 0 ? '⚠' : '✓' }}</span>
                <span class="cross-check-title">
                  {{
                    pkg.deductionMaterials.pendingDeductibility > 0
                      ? `有 ¥ ${money(pkg.deductionMaterials.pendingDeductibility)} 支出待确认扣除资料（发票/收据）`
                      : '支出扣除资料齐备，企业所得税税前扣除无忧'
                  }}
                </span>
                <NTooltip trigger="hover">
                  <template #trigger><i class="ri-information-line sub-info-icon" /></template>
                  小规模纳税人不抵扣进项增值税；这里展示的是企业所得税税前扣除资料状态，按发票/收据确认情况逐笔标记
                </NTooltip>
              </div>
              <div class="deduction-four-columns">
                <div class="column-card">
                  <div class="col-head"><span class="col-name">账面支出</span></div>
                  <div class="col-amount">¥ {{ money(pkg.deductionMaterials.bookExpenses) }}</div>
                </div>
                <div class="column-card">
                  <div class="col-head"><span class="col-name">已确认可扣除</span></div>
                  <div class="col-amount">¥ {{ money(pkg.deductionMaterials.totalDeductible) }}</div>
                </div>
                <div class="column-card">
                  <div class="col-head"><span class="col-name">待确认扣除资料</span></div>
                  <div class="col-amount" :class="{ 'is-warning': pkg.deductionMaterials.pendingDeductibility > 0 }">
                    ¥ {{ money(pkg.deductionMaterials.pendingDeductibility) }}
                  </div>
                </div>
                <div class="column-card">
                  <div class="col-head"><span class="col-name">已确认不可扣除</span></div>
                  <div class="col-amount">¥ {{ money(pkg.deductionMaterials.totalNonDeductible) }}</div>
                </div>
              </div>
            </div>

            <!-- 卡片 1 底栏：引导开始第一项待办申报 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns" />
              <div class="footer-actions">
                <NButton type="primary" @click="startFilingFromOverview">
                  {{ nextPendingFromOverview ? `开始申报：${cardTitleMap[nextPendingFromOverview]} →` : '查看增值税申报底稿 →' }}
                </NButton>
              </div>
            </div>
          </NCard>

          <!-- ==================== 卡片 2：增值税小规模申报 (主表) ==================== -->
          <NCard v-else-if="activeCard === 'vat'" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">2</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">增值税小规模纳税人申报（主表）</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      {{ pkg.vat.isBelowThreshold
                        ? (pkg.periodType === 'month' ? '本月未超 10 万元：全额免征增值税，销售额仍需照实申报' : '本季未超 30 万元：全额免征增值税，销售额仍需照实申报')
                        : '已超免税限额：在「本期销售情况」弹窗按 1% 征收率填报' }}
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box">
                <NTag size="small" :type="statusTag(pkg.vat.deadline.status).type" :bordered="false">
                  {{ statusTag(pkg.vat.deadline.status).text }}
                </NTag>
                <span class="task-deadline-text">{{ deadlineText(pkg.vat.deadline) }}</span>
              </div>
            </div>

            <!-- 抄报核心手卡 (Quick Copy Hand-Card)：分步填报指引 -->
            <div v-if="vatFilingGuide" class="quick-copy-handcard">
              <div class="handcard-header">
                <i class="ri-file-list-3-line handcard-icon" aria-hidden="true"></i>
                <span class="handcard-title">电子税务局快速抄报手卡</span>
              </div>
              <div class="guide-steps">
                <div v-for="(step, idx) in vatFilingGuide.steps" :key="idx" class="guide-step">
                  <span class="guide-step-num">{{ idx + 1 }}</span>
                  <div class="guide-step-body">
                    <span class="guide-step-title">{{ step.title }}</span>
                    <p class="guide-step-detail">{{ step.detail }}</p>
                    <div v-if="step.amounts && step.amounts.length" class="guide-amounts">
                      <div v-for="a in step.amounts" :key="a.label" class="guide-amount-row">
                        <span class="guide-amount-label">{{ a.label }}</span>
                        <span class="guide-amount-val">¥ {{ money(a.amount) }}</span>
                        <NButton size="tiny" quaternary type="primary" @click="copyAmount(a.amount, a.copyLabel)">
                          复制
                        </NButton>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="vatFilingGuide.warnings.length" class="guide-warnings">
                <div v-for="(w, i) in vatFilingGuide.warnings" :key="i" class="guide-warning-row">
                  <i class="ri-error-warning-line" aria-hidden="true"></i>
                  <span>{{ w }}</span>
                </div>
              </div>
            </div>

            <!-- 可折叠的全部申报栏次 -->
            <div class="expand-table-section">
              <button type="button" class="expand-toggle-btn" @click="showFullVatTable = !showFullVatTable">
                <span>{{ showFullVatTable ? '收起增值税申报表完整栏次' : `查看增值税申报表完整栏次 (${pkg.vat.filingFields.length} 项) ▾` }}</span>
              </button>

              <div v-show="showFullVatTable" class="expanded-table-content">
                <div class="table-tools-row">
                  <div />
                  <div class="tools-switch">
                    <span>仅看有金额行</span>
                    <NSwitch v-model:value="hideZeroFields" size="small" />
                  </div>
                </div>

                <div class="form-sections-wrapper">
                  <div
                    v-for="[section, fields] in vatFieldsBySection"
                    :key="section"
                    class="form-section-group"
                  >
                    <div class="section-title-bar">
                      <span class="section-name">{{ section }}</span>
                    </div>
                    <div class="fields-table">
                      <div
                        v-for="f in fields"
                        :key="f.code"
                        class="field-row"
                        :class="{ 'is-zero': !f.amount, 'is-hidden': hideZeroFields && !f.amount }"
                      >
                        <div class="field-info">
                          <span class="field-label-text">{{ f.label }}</span>
                          <span v-if="f.amount > 0" class="field-location-tag">{{ fieldLocationHint(f) }}</span>
                        </div>
                        <div class="field-ops">
                          <span class="field-amount-val" :class="{ highlight: f.amount > 0 }">
                            ¥ {{ money(f.amount) }}
                          </span>
                          <NButton
                            size="tiny"
                            quaternary
                            type="primary"
                            @click="copyAmount(f.amount, f.label)"
                            class="copy-btn"
                          >
                            复制
                          </NButton>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 卡片 2 底栏：卡片切换 + 办结动作 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns">
                <NButton v-if="prevCardKey" size="small" quaternary @click="activeCard = prevCardKey">
                  ← 上一步：{{ cardTitleMap[prevCardKey] }}
                </NButton>
                <NButton v-if="nextCardKey" size="small" quaternary @click="activeCard = nextCardKey">
                  下一步：{{ cardTitleMap[nextCardKey] }} →
                </NButton>
              </div>
              <div class="footer-actions" v-if="pkg.vat.deadline.deadlineId">
                <template v-if="pkg.vat.deadline.status === 'pending'">
                  <NPopconfirm @positive-click="waiveDeadline(pkg.vat.deadline.deadlineId, '增值税申报')">
                    <template #trigger>
                      <NButton quaternary size="small">本期不适用</NButton>
                    </template>
                    确认将本期增值税申报标记为「不适用」？适用于建档前期间等无需申报的场景。
                  </NPopconfirm>
                  <NButton
                    type="primary"
                    :loading="filingBusy === pkg.vat.deadline.deadlineId"
                    @click="markFiled(pkg.vat.deadline.deadlineId, '增值税申报', pkg.summary.vatPayable === 0)"
                  >
                    标记申报办结
                  </NButton>
                </template>
                <span v-else-if="pkg.vat.deadline.status === 'waived'" class="task-done-badge">— 本期不适用（已豁免）</span>
                <span v-else class="task-done-badge">✓ 本期增值税申报已办结</span>
              </div>
            </div>
          </NCard>

          <!-- ==================== 卡片 3（经营所得个税）：个人独资企业 / 个体工商户 ==================== -->
          <NCard v-else-if="activeCard === 'cit' && !pkg.cit" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">3</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">经营所得个人所得税预缴（A 表）</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      {{ pkg.citNotice?.label || '当前主体' }} · 查账征收 · {{ pkg.businessIncomeIit?.filingCycle === 'month' ? '月报' : '季报' }}（按税局核定）
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box">
                <template v-if="pkg.businessIncomeIit">
                  <NTag size="small" :type="statusTag(pkg.businessIncomeIit.deadline.status).type" :bordered="false">
                    {{ statusTag(pkg.businessIncomeIit.deadline.status).text }}
                  </NTag>
                  <span class="task-deadline-text">{{ deadlineText(pkg.businessIncomeIit.deadline) }}</span>
                </template>
                <NTag v-else size="small" type="default" :bordered="false">暂无数据</NTag>
              </div>
            </div>

            <template v-if="pkg.businessIncomeIit">
              <div class="quick-copy-handcard">
                <div class="handcard-header">
                  <span class="handcard-title">👉 A 表累计申报口径（{{ pkg.businessIncomeIit.filingCycle === 'month' ? '月报' : '季报' }}）</span>
                </div>
                <div class="handcard-grid cols-3">
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">累计收入总额（不含税）</span>
                      <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.businessIncomeIit.revenue, '累计收入总额')">
                        复制
                      </NButton>
                    </div>
                    <div class="item-amount-val">¥ {{ money(pkg.businessIncomeIit.revenue) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">累计成本费用</span>
                      <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.businessIncomeIit.expenses, '累计成本费用')">
                        复制
                      </NButton>
                    </div>
                    <div class="item-amount-val">¥ {{ money(pkg.businessIncomeIit.expenses) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">累计应纳税所得额</span>
                      <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.businessIncomeIit.taxableIncome, '累计应纳税所得额')">
                        复制
                      </NButton>
                    </div>
                    <div class="item-amount-val" :class="{ 'is-zero-val': !pkg.businessIncomeIit.taxableIncome }">
                      ¥ {{ money(pkg.businessIncomeIit.taxableIncome) }}
                    </div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">应纳税额</span>
                      <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.businessIncomeIit.tax, '应纳税额')">
                        复制
                      </NButton>
                    </div>
                    <div class="item-amount-val">¥ {{ money(pkg.businessIncomeIit.tax) }}</div>
                  </div>
                  <div class="handcard-item" v-if="pkg.businessIncomeIit.halvingApplied">
                    <div class="handcard-item-top">
                      <span class="item-label">减半减免（个体户 ≤200 万）</span>
                      <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.businessIncomeIit.halvingReduction, '减半减免')">
                        复制
                      </NButton>
                    </div>
                    <div class="item-amount-val">-¥ {{ money(pkg.businessIncomeIit.halvingReduction) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">本期待缴（已缴待核）</span>
                    </div>
                    <div class="item-amount-val" :class="{ 'is-zero-val': !pkg.businessIncomeIit.suggestedPayable }">
                      ¥ {{ money(pkg.businessIncomeIit.suggestedPayable) }}
                    </div>
                  </div>
                </div>
              </div>
              <ul class="iit-notes">
                <li v-for="(note, idx) in pkg.businessIncomeIit.notes" :key="idx">{{ note }}</li>
              </ul>
            </template>
            <div v-else class="cit-not-applicable">
              <p class="cit-na-title">经营所得个税测算暂不可用</p>
              <p class="cit-na-text">{{ pkg.citNotice?.notice }}</p>
            </div>

            <!-- B 表年度汇算（次年 1-3 月汇算窗口） -->
            <div class="iit-annual-section">
              <div class="handcard-header">
                <span class="handcard-title">🧾 年度汇算清缴（B 表）</span>
                <NTooltip trigger="hover">
                  <template #trigger>
                    <i class="ri-information-line sub-info-icon" />
                  </template>
                  次年 3 月 31 日前完成上一年度汇算；数据为系统测算底稿
                </NTooltip>
              </div>
              <div class="iit-annual-controls">
                <NSelect v-model:value="iitAnnualYear" size="small" style="width: 130px" :options="iitAnnualYearOptions" />
                <NButton size="small" type="primary" :loading="iitAnnualLoading" @click="loadIitAnnual">
                  测算汇算
                </NButton>
              </div>
              <template v-if="iitAnnual">
                <div class="handcard-grid cols-3">
                  <div class="handcard-item">
                    <div class="handcard-item-top"><span class="item-label">年度利润</span></div>
                    <div class="item-amount-val">¥ {{ money(iitAnnual.profitTotal) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top"><span class="item-label">纳税调增（业主工资+无票）</span></div>
                    <div class="item-amount-val">+¥ {{ money(iitAnnual.adjustments?.total) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top"><span class="item-label">合计扣除（含 6 万）</span></div>
                    <div class="item-amount-val">-¥ {{ money(iitAnnual.deductions?.total) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top"><span class="item-label">应纳税所得额</span></div>
                    <div class="item-amount-val">¥ {{ money(iitAnnual.taxableIncome) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top"><span class="item-label">应纳税额</span></div>
                    <div class="item-amount-val">¥ {{ money(iitAnnual.tax) }}</div>
                  </div>
                  <div class="handcard-item">
                    <div class="handcard-item-top">
                      <span class="item-label">{{ (iitAnnual.finalRefund || 0) > 0 ? '应退税额' : '应补税额' }}</span>
                    </div>
                    <div class="item-amount-val" :class="{ 'is-zero-val': !(iitAnnual.finalPayable || iitAnnual.finalRefund) }">
                      ¥ {{ money((iitAnnual.finalRefund || 0) > 0 ? iitAnnual.finalRefund : iitAnnual.finalPayable) }}
                    </div>
                  </div>
                </div>
                <div class="iit-annual-inputs">
                  <label>业主工资调增
                    <NInputNumber v-model:value="iitAnnualInput.ownerSalary" :min="0" :precision="2" size="small" style="width: 140px" placeholder="缺省取名册" />
                  </label>
                  <label>减除费用
                    <NInputNumber v-model:value="iitAnnualInput.statutoryDeduction" :min="0" :precision="2" size="small" style="width: 140px" placeholder="缺省 60000" />
                  </label>
                  <label>专项附加扣除
                    <NInputNumber v-model:value="iitAnnualInput.specialAdditionalDeductions" :min="0" :precision="2" size="small" style="width: 140px" />
                  </label>
                  <label>已预缴合计
                    <NInputNumber v-model:value="iitAnnualInput.prepaidTotal" :min="0" :precision="2" size="small" style="width: 140px" />
                  </label>
                  <NButton size="small" @click="loadIitAnnual">重新测算</NButton>
                </div>
                <ul class="iit-notes">
                  <li v-for="(note, idx) in iitAnnual.notes" :key="idx">{{ note }}</li>
                </ul>
              </template>
            </div>

            <!-- 卡片 3（经营所得个税）底栏：统一主动作与卡片切换 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns">
                <NButton v-if="prevCardKey" size="small" quaternary @click="activeCard = prevCardKey">
                  ← 上一步：{{ cardTitleMap[prevCardKey] }}
                </NButton>
                <NButton v-if="nextCardKey" size="small" quaternary @click="activeCard = nextCardKey">
                  下一步：{{ cardTitleMap[nextCardKey] }} →
                </NButton>
              </div>
              <div class="footer-actions" v-if="pkg.businessIncomeIit?.deadline?.deadlineId">
                <template v-if="pkg.businessIncomeIit.deadline.status === 'pending'">
                  <NPopconfirm @positive-click="waiveDeadline(pkg.businessIncomeIit.deadline.deadlineId!, '经营所得个税预缴')">
                    <template #trigger>
                      <NButton quaternary size="small">本期不适用</NButton>
                    </template>
                    确认将本期经营所得个税预缴标记为「不适用」？适用于建档前期间等无需申报的场景。
                  </NPopconfirm>
                  <NButton
                    type="primary"
                    :loading="filingBusy === pkg.businessIncomeIit.deadline.deadlineId"
                    @click="markFiled(pkg.businessIncomeIit.deadline.deadlineId!, '经营所得个税预缴', pkg.businessIncomeIit.suggestedPayable === 0)"
                  >
                    标记申报办结
                  </NButton>
                </template>
                <span v-else-if="pkg.businessIncomeIit.deadline.status === 'waived'" class="task-done-badge">— 本期不适用（已豁免）</span>
                <span v-else class="task-done-badge">✓ 本期经营所得个税预缴已办结</span>
              </div>
            </div>
          </NCard>

          <!-- ==================== 卡片 3：企业所得税预缴 (A200000) ==================== -->
          <NCard v-else-if="activeCard === 'cit' && pkg.cit" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">3</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">企业所得税季度预缴（A200000）</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      {{ pkg.cit.note || '享受小型微利企业优惠政策（实际税负 5%）' }}
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box">
                <NTag size="small" :type="statusTag(pkg.cit.deadline.status).type" :bordered="false">
                  {{ statusTag(pkg.cit.deadline.status).text }}
                </NTag>
                <span class="task-deadline-text">{{ deadlineText(pkg.cit.deadline) }}</span>
              </div>
            </div>

            <!-- 抄报核心手卡 -->
            <div class="quick-copy-handcard">
              <div class="handcard-header">
                <span class="handcard-title">👉 电子税务局快速抄报手卡</span>
              </div>
              <div class="handcard-grid cols-3">
                <div class="handcard-item">
                  <div class="handcard-item-top">
                    <span class="item-label">第 1 行 营业收入</span>
                    <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.cit.revenue, '第1行营业收入')">
                      复制
                    </NButton>
                  </div>
                  <div class="item-amount-val">
                    ¥ {{ money(pkg.cit.revenue) }}
                  </div>
                </div>

                <div class="handcard-item">
                  <div class="handcard-item-top">
                    <span class="item-label">第 3 行 利润总额</span>
                    <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.cit.totalProfit, '第3行利润总额')">
                      复制
                    </NButton>
                  </div>
                  <div class="item-amount-val">
                    ¥ {{ money(pkg.cit.totalProfit) }}
                  </div>
                </div>

                <div class="handcard-item">
                  <div class="handcard-item-top">
                    <span class="item-label">应纳所得税额 (5%微利)</span>
                    <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.summary.citPayable || pkg.cit.preferentialTax, '应纳税额')">
                      复制
                    </NButton>
                  </div>
                  <div class="item-amount-val" :class="{ 'is-zero-val': !(pkg.summary.citPayable || pkg.cit.preferentialTax) }">
                    ¥ {{ money(pkg.summary.citPayable || pkg.cit.preferentialTax) }}
                  </div>
                </div>
              </div>
            </div>

            <!-- 可折叠的全部 A200000 栏次 -->
            <div class="expand-table-section" v-if="pkg.cit.fields?.length">
              <button type="button" class="expand-toggle-btn" @click="showFullCitTable = !showFullCitTable">
                <span>{{ showFullCitTable ? '收起企业所得税完整栏次' : `查看企业所得税完整栏次 (${pkg.cit.fields.length} 行) ▾` }}</span>
              </button>

              <div v-show="showFullCitTable" class="expanded-table-content">
                <div class="cit-fields-table">
                  <div
                    v-for="f in pkg.cit.fields"
                    :key="f.line"
                    class="field-row cit-row"
                    :class="{ 'is-zero': !f.amount }"
                  >
                    <div class="field-info">
                      <span class="line-badge">第 {{ f.line }} 行</span>
                      <span class="field-label-text">{{ f.label }}</span>
                    </div>
                    <div class="field-ops">
                      <span class="field-amount-val" :class="{ highlight: f.amount > 0 }">
                        ¥ {{ money(f.amount) }}
                      </span>
                      <NButton
                        size="tiny"
                        quaternary
                        type="primary"
                        @click="copyAmount(f.amount, `第${f.line}行 ${f.label}`)"
                        class="copy-btn"
                      >
                        复制
                      </NButton>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 卡片 3 底栏：卡片切换 + 办结动作 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns">
                <NButton v-if="prevCardKey" size="small" quaternary @click="activeCard = prevCardKey">
                  ← 上一步：{{ cardTitleMap[prevCardKey] }}
                </NButton>
                <NButton v-if="nextCardKey" size="small" quaternary @click="activeCard = nextCardKey">
                  下一步：{{ cardTitleMap[nextCardKey] }} →
                </NButton>
              </div>
              <div class="footer-actions" v-if="pkg.cit.deadline.deadlineId">
                <template v-if="pkg.cit.deadline.status === 'pending'">
                  <NPopconfirm @positive-click="waiveDeadline(pkg.cit.deadline.deadlineId, '企业所得税预缴')">
                    <template #trigger>
                      <NButton quaternary size="small">本期不适用</NButton>
                    </template>
                    确认将本期企业所得税预缴标记为「不适用」？适用于建档前期间等无需申报的场景。
                  </NPopconfirm>
                  <NButton
                    type="primary"
                    :loading="filingBusy === pkg.cit.deadline.deadlineId"
                    @click="markFiled(pkg.cit.deadline.deadlineId, '企业所得税预缴')"
                  >
                    标记申报办结
                  </NButton>
                </template>
                <span v-else-if="pkg.cit.deadline.status === 'waived'" class="task-done-badge">— 本期不适用（已豁免）</span>
                <span v-else class="task-done-badge">✓ 本期企业所得税预缴已办结</span>
              </div>
            </div>
          </NCard>

          <!-- ==================== 卡片 4：财务报表报送（独立卡片） ==================== -->
          <NCard v-else-if="activeCard === 'fin_statements'" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">4</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">财务报表报送</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      小企业会计准则标准格式（资产负债表 · 利润表 · 现金流量表），无需手填，直接导出 XLS 上传税局
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box" v-if="finStatementCheckItem">
                <NTag size="small" :type="statusTag(finStatementCheckItem.status).type" :bordered="false">
                  {{ statusTag(finStatementCheckItem.status).text }}
                </NTag>
                <span class="task-deadline-text">{{ deadlineText(finStatementCheckItem) }}</span>
              </div>
            </div>

            <!-- 财务报表两步极简指引手卡 -->
            <div class="quick-copy-handcard">
              <div class="handcard-header">
                <span class="handcard-title">📊 电子税务局财务报表报送指引</span>
              </div>
              <div class="guide-steps">
                <div class="guide-step">
                  <span class="guide-step-num">1</span>
                  <div class="guide-step-body">
                    <span class="guide-step-title">下载本期标准三表 XLS</span>
                    <p class="guide-step-detail">
                      包含资产负债表、利润表、现金流量表，期初与期末/累计栏次已自动计算。
                    </p>
                    <div class="statement-download-row">
                      <NButton
                        type="primary"
                        secondary
                        size="small"
                        :loading="exporting"
                        @click="exportFinancialXls"
                      >
                        📥 下载财务报表 XLS（{{ period }}）
                      </NButton>
                    </div>
                  </div>
                </div>
                <div class="guide-step">
                  <span class="guide-step-num">2</span>
                  <div class="guide-step-body">
                    <span class="guide-step-title">在电子税务局导入提交</span>
                    <p class="guide-step-detail">
                      {{ finStatementCheckItem?.guide || '进入电子税务局「财务报表报送及更正」，导入下载的 XLS 文件核对提交。' }}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <!-- 卡片 4 底栏：卡片切换 + 办结动作 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns">
                <NButton v-if="prevCardKey" size="small" quaternary @click="activeCard = prevCardKey">
                  ← 上一步：{{ cardTitleMap[prevCardKey] }}
                </NButton>
                <NButton v-if="nextCardKey" size="small" quaternary @click="activeCard = nextCardKey">
                  下一步：{{ cardTitleMap[nextCardKey] }} →
                </NButton>
              </div>
              <div class="footer-actions" v-if="finStatementCheckItem">
                <template v-if="finStatementCheckItem.status === 'pending'">
                  <NPopconfirm @positive-click="waiveDeadline(finStatementCheckItem.deadlineId, '财务报表报送')">
                    <template #trigger>
                      <NButton quaternary size="small">本期不适用</NButton>
                    </template>
                    确认将本期财务报表报送标记为「不适用」？适用于建档前期间等无需申报的场景。
                  </NPopconfirm>
                  <NButton
                    type="primary"
                    :loading="filingBusy === finStatementCheckItem.deadlineId"
                    @click="markFiled(finStatementCheckItem.deadlineId, '财务报表报送')"
                  >
                    标记报送办结
                  </NButton>
                </template>
                <span v-else-if="finStatementCheckItem.status === 'waived'" class="task-done-badge">
                  — 本期不适用（已豁免）
                </span>
                <span v-else class="task-done-badge">
                  ✓ 本期财务报表报送已办结
                </span>
              </div>
            </div>
          </NCard>

          <!-- ==================== 卡片 5：其他税费与合规确认（独立卡片） ==================== -->
          <NCard v-else-if="activeCard === 'other_taxes'" :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">5</span>
                <div>
                  <div class="task-title-row">
                    <h4 class="task-name">其他税费与合规确认</h4>
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line task-info-icon" />
                      </template>
                      逐项确认印花税、个人所得税代扣代缴及当季特定申报事项，无税源事项可直接办结
                    </NTooltip>
                  </div>
                </div>
              </div>
              <div class="task-status-box">
                <NTag size="small" :type="statusTag(otherTaxesSummary.status).type" :bordered="false">
                  {{ otherTaxesSummary.status === 'pending' ? `待确认 ${otherTaxesSummary.pendingCount} 项` : statusTag(otherTaxesSummary.status).text }}
                </NTag>
                <span class="task-deadline-text">
                  已确认 {{ otherTaxesSummary.resolvedCount }} / {{ otherTaxesSummary.totalCount }} 项
                </span>
              </div>
            </div>

            <!-- 其他税费独立子卡片列表 -->
            <div class="other-items-grid" v-if="otherChecklistItems.length">
              <div
                v-for="(item, idx) in otherChecklistItems"
                :key="item.deadlineId || (item.taxKind + (item.itemPeriod || ''))"
                class="other-item-card"
                :class="{ 'is-done': item.status === 'filed' || item.status === 'waived' }"
              >
                <div class="other-item-main">
                  <div class="other-item-title-row">
                    <span class="other-item-index">{{ idx + 1 }}</span>
                    <strong class="other-name">{{ item.name }}</strong>
                    <NTag size="tiny" :type="statusTag(item.status).type" :bordered="false">
                      {{ statusTag(item.status).text }}
                    </NTag>
                    <NTooltip v-if="item.guide || item.hint" trigger="hover">
                      <template #trigger>
                        <i class="ri-information-line sub-info-icon" />
                      </template>
                      <div>
                        <div v-if="item.guide">{{ item.guide }}</div>
                        <div v-if="item.hint" :style="{ marginTop: item.guide ? '4px' : '0', opacity: 0.8 }">{{ item.hint }}</div>
                      </div>
                    </NTooltip>
                    <span class="other-deadline" v-if="item.dueDate">{{ deadlineText(item) }}</span>
                  </div>
                  <NButton
                    v-if="item.actionPath && item.status === 'pending'"
                    text
                    type="primary"
                    size="tiny"
                    class="other-hint-action"
                    @click="router.push(item.actionPath)"
                  >
                    <i class="ri-user-add-line" />&nbsp;{{ item.actionLabel || '去处理' }}
                  </NButton>
                </div>

                <div class="other-item-action">
                  <template v-if="item.status === 'pending' && item.deadlineId">
                    <NPopconfirm @positive-click="waiveDeadline(item.deadlineId, item.name)">
                      <template #trigger>
                        <NButton size="small" quaternary>本期不适用</NButton>
                      </template>
                      确认将「{{ item.name }}」标记为本期不适用？适用于建档前期间等无需申报的场景。
                    </NPopconfirm>
                    <NButton
                      size="small"
                      type="primary"
                      :loading="filingBusy === item.deadlineId"
                      @click="markFiled(item.deadlineId, item.name, item.taxKind === 'stamp_duty')"
                    >
                      标记办结
                    </NButton>
                  </template>
                  <span v-else-if="item.status === 'waived'" class="other-done-text">— 本期不适用（已豁免）</span>
                  <span v-else-if="item.status === 'filed'" class="other-done-text">✓ 已办结</span>
                </div>
              </div>
            </div>

            <!-- 卡片 5 底栏 -->
            <div class="task-card-footer">
              <div class="footer-nav-btns">
                <NButton v-if="prevCardKey" size="small" quaternary @click="activeCard = prevCardKey">
                  ← 上一步：{{ cardTitleMap[prevCardKey] }}
                </NButton>
                <NButton size="small" quaternary @click="activeCard = 'overview'">
                  ↺ 回到总览
                </NButton>
              </div>
              <div class="footer-actions">
                <span v-if="otherTaxesSummary.pendingCount === 0" class="task-done-badge">
                  ✓ 本期其他税费事项已全部确认完毕
                </span>
              </div>
            </div>
          </NCard>
        </section>

        <!-- 底部免责声明 -->
        <div class="footer-disclaimer">
          <p>
            提示：申报数据基于梭罗记账的账载凭证实时测算，仅供申报辅助参考；最终申报口径与扣缴凭证以国家税务总局电子税务局为准。
          </p>
        </div>
        </template>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
/* 首次加载骨架：先按真实布局摆出「一排步骤卡 + 两块内容卡」*/
.tax-skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tax-skeleton .sk-switcher {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}

.boss-tax-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 14px;
  padding: 14px 18px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
}

.boss-tax-main {
  min-width: 0;
}

.boss-tax-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--solo-text-muted);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 1.2px;
}

.boss-tax-main strong {
  color: var(--solo-text-primary);
  font-size: 15px;
}

.boss-tax-main p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

@media (max-width: 720px) {
  .boss-tax-summary {
    align-items: flex-start;
    flex-direction: column;
  }
}

.tax-filing-page {
  max-width: 1160px;
  margin: 0 auto;
  padding-bottom: 32px;
  width: 100%;
  box-sizing: border-box;
}

/* 页头 */
.taxreg-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-card);
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.taxreg-bar__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--solo-text-muted);
}
.taxreg-bar__dot--on {
  background: var(--solo-income);
}
.taxreg-bar__text {
  flex: 1;
  min-width: 0;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.header-left .title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-left h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.mode-tag {
  font-weight: 500;
}
.header-sub {
  font-size: 13px;
  color: var(--solo-text-muted, #636e7c);
  margin-top: 4px;
  display: block;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 顶部 5 张可切换导航卡片栏 */
.switcher-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.switcher-card {
  appearance: none;
  text-align: left;
  background: var(--solo-bg-card, #ffffff);
  border: 1.5px solid var(--solo-border, #dde3ea);
  border-radius: 12px;
  padding: 12px 14px;
  cursor: pointer;
  transition: all 0.18s ease;
  display: flex;
  flex-direction: column;
  gap: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.switcher-card:hover {
  border-color: rgba(23, 71, 176, 0.45);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.06);
}
.switcher-card.is-active {
  border-color: var(--solo-primary, #1747b0);
  background: linear-gradient(180deg, rgba(23, 71, 176, 0.04) 0%, var(--solo-bg-card, #ffffff) 100%);
  box-shadow: 0 6px 16px rgba(23, 71, 176, 0.1);
}
.switcher-card.is-done {
  border-color: rgba(24, 160, 88, 0.28);
  background: linear-gradient(180deg, rgba(24, 160, 88, 0.03) 0%, var(--solo-bg-card, #ffffff) 100%);
}
.switcher-card-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.switcher-step-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.06);
  color: var(--solo-text-secondary, #586474);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.switcher-card.is-active .switcher-step-num {
  background: var(--solo-primary, #1747b0);
  color: #ffffff;
}
.switcher-card.is-done .switcher-step-num {
  background: rgba(24, 160, 88, 0.15);
  color: #0f7354;
}
.switcher-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.switcher-tag {
  margin-left: auto;
  flex-shrink: 0;
}
.switcher-card-metric {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
  margin-top: 2px;
}
.switcher-card-sub {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 单卡聚焦舞台 */
.active-card-stage {
  display: flex;
  flex-direction: column;
}

/* 卡片 1 内部：3 格核心定心丸看板 */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}
.overview-card {
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: var(--solo-radius-lg, 8px);
  padding: 14px 16px;
  border: 1px solid var(--solo-border, #edf0f4);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.primary-tax-card {
  background: linear-gradient(145deg, rgba(24, 160, 88, 0.06) 0%, var(--solo-bg-page, #f6f7f9) 100%);
  border-color: rgba(24, 160, 88, 0.22);
}
.overview-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.overview-label {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.overview-amount {
  font-size: 22px;
  font-weight: 750;
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.overview-amount.is-zero {
  color: #0f7354;
}
.overview-value {
  font-size: 19px;
  font-weight: 700;
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.gauge-val {
  color: #0f7354;
}
.overview-sub-detail {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.overview-progress-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}
.progress-track {
  flex: 1;
  height: 6px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 4px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #0f7354;
  border-radius: 4px;
  transition: width 0.3s ease;
}
.progress-text {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  flex-shrink: 0;
}
.filing-progress-strip {
  background: #fff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
  padding: 10px 16px;
  margin-bottom: 12px;
}
.filing-progress-strip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.filing-progress-strip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.filing-progress-strip-count {
  font-size: 12px;
  color: var(--solo-text-secondary, #636e7c);
}

/* 智能税情简报条 */
.smart-insight-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: linear-gradient(135deg, rgba(22, 93, 255, 0.04) 0%, rgba(114, 46, 209, 0.04) 100%);
  border: 1px solid rgba(22, 93, 255, 0.15);
  border-radius: var(--solo-radius, 8px);
  font-size: 13px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.insight-badge {
  font-weight: 600;
  color: var(--solo-ai, #1747b0);
  flex-shrink: 0;
}
.insight-headline {
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.insight-advice {
  color: var(--solo-text-secondary, #586474);
  margin-left: auto;
  font-size: 12px;
}

/* 三方核验面板 */
.cross-detail-panel {
  background: var(--solo-bg-page, #f6f7f9);
  border: 1px solid var(--solo-border, #edf0f4);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cross-panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #0f7354;
}
.cross-panel-header.is-diff {
  color: #a05c10;
}
.cross-check-icon {
  font-weight: bold;
}
.cross-three-columns {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.deduction-panel {
  margin-top: 12px;
}
.deduction-four-columns {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.col-amount.is-warning {
  color: #a05c10;
}
.column-card {
  background: var(--solo-bg-card, #ffffff);
  border-radius: 8px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border, #edf0f4);
}
.col-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.col-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #586474);
}
.col-count {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}
.col-amount {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  margin: 6px 0 4px;
  color: var(--solo-text-primary, #182230);
}
.col-desc {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}
.diff-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.diff-row {
  border: 1px solid rgba(240, 160, 32, 0.3);
  background: rgba(240, 160, 32, 0.04);
  border-radius: 8px;
  padding: 10px 14px;
}
.diff-title-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
.diff-tag {
  color: #a05c10;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.diff-hint-text {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
}

/* 聚焦任务大卡片通用样式 */
.task-card {
  border-radius: var(--solo-radius-lg, 12px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid var(--solo-border, #edf0f4);
}
.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.task-title-group {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.task-step-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--solo-primary, #1747b0);
  color: #fff;
  font-size: 13px;
  font-weight: bold;
  flex-shrink: 0;
  margin-top: 2px;
}
.task-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.task-name {
  margin: 0;
  font-size: 17px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}
.task-info-icon {
  font-size: 16px;
  color: var(--solo-text-muted, #636e7c);
  cursor: pointer;
  transition: color 0.15s ease;
}
.task-info-icon:hover {
  color: var(--solo-primary, #1747b0);
}
.sub-info-icon {
  font-size: 13px;
  color: var(--solo-text-muted, #636e7c);
  cursor: pointer;
  vertical-align: middle;
  margin-left: 4px;
  transition: color 0.15s ease;
}
.sub-info-icon:hover {
  color: var(--solo-primary, #1747b0);
}
.task-status-box {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  flex-shrink: 0;
}
.task-deadline-text {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}

/* 抄报核心手卡 (Quick Copy Hand-Card) */
.quick-copy-handcard {
  background: var(--solo-bg-page, #f6f7f9);
  border: 1px solid var(--solo-border, #edf0f4);
  border-radius: 8px;
  padding: 16px 18px;
  margin-bottom: 14px;
}
.handcard-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.handcard-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.handcard-tip {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.handcard-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.handcard-grid.cols-3 {
  grid-template-columns: repeat(3, 1fr);
}
.handcard-item {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #edf0f4);
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.handcard-item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.item-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  font-weight: 500;
}
.item-amount-val {
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}
.item-amount-val.is-zero-val {
  color: #0f7354;
}

/* 抄报步骤指引（手卡） */
.handcard-icon {
  font-size: 15px;
  color: var(--solo-primary, #1747b0);
}
.guide-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.guide-step {
  display: flex;
  gap: 10px;
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #edf0f4);
  border-radius: 8px;
  padding: 12px 14px;
}
.guide-step-num {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--solo-primary, #1747b0);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.guide-step-body {
  flex: 1;
  min-width: 0;
}
.guide-step-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.guide-step-detail {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-text-secondary, #586474);
}
.guide-amounts {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.guide-amount-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: 4px;
}
.guide-amount-label {
  flex: 1;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
}
.guide-amount-val {
  font-size: 17px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.guide-warnings {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.guide-warning-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-warning, #a05c10);
  background: var(--solo-warning-bg, #fff5e8);
  border-radius: 4px;
  padding: 8px 10px;
}
.guide-warning-row i {
  font-size: 14px;
  margin-top: 2px;
  flex: none;
}

.statement-download-row {
  margin-top: 10px;
}

/* 栏次行：税局填写位置标注 */
.field-location-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  color: var(--solo-info, #1747b0);
  background: var(--solo-info-bg, #edf3ff);
  white-space: nowrap;
}

/* 折叠展开底稿栏次 */
.expand-table-section {
  margin-bottom: 12px;
}
.expand-toggle-btn {
  background: none;
  border: none;
  padding: 6px 0;
  font-size: 12.5px;
  color: var(--solo-primary, #1747b0);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}
.expand-toggle-btn:hover {
  text-decoration: underline;
}
.expanded-table-content {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border, #edf0f4);
}
.table-tools-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.tools-desc {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.tools-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
}

/* 栏次表 */
.form-sections-wrapper {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form-section-group {
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: 8px;
  padding: 10px 12px;
}
.section-title-bar {
  margin-bottom: 8px;
}
.section-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
}
.fields-table, .cit-fields-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  background: var(--solo-bg-card, #ffffff);
  border-radius: 4px;
  font-size: 12px;
}
.field-row.is-hidden {
  display: none;
}
.field-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.line-badge {
  font-size: 11px;
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--solo-text-secondary, #586474);
}
.field-label-text {
  color: var(--solo-text-primary, #182230);
}
.field-ops {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-amount-val {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.field-amount-val.highlight {
  color: var(--solo-primary, #1747b0);
}

/* 卡片 5：其他附税独立子项卡片 */
.other-items-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.other-item-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  background: var(--solo-bg-page, #f6f7f9);
  border: 1px solid var(--solo-border, #edf0f4);
  border-radius: 8px;
}
.other-item-card.is-done {
  background: rgba(24, 160, 88, 0.03);
  border-color: rgba(24, 160, 88, 0.2);
}
.other-item-main {
  flex: 1;
  min-width: 0;
}
.other-item-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.other-item-index {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(23, 71, 176, 0.1);
  color: var(--solo-primary, #1747b0);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.other-name {
  font-size: 14px;
  color: var(--solo-text-primary, #182230);
}
.other-deadline {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.other-guide {
  margin: 6px 0 2px;
  font-size: 12.5px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.5;
}
.other-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.5;
}
.other-hint-action {
  margin-top: 4px;
  padding-left: 0;
}
.other-item-action {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.other-done-text {
  color: #0f7354;
  font-weight: 600;
  font-size: 13px;
}

/* 卡片底栏：前后切换与办结动作 */
.task-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--solo-border, #edf0f4);
  gap: 12px;
}
.footer-nav-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}
.footer-note {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.footer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.task-done-badge {
  color: #0f7354;
  font-weight: 600;
  font-size: 13px;
}

/* 底部免责声明 */
.footer-disclaimer {
  margin-top: 20px;
  text-align: center;
}
.footer-disclaimer p {
  margin: 0;
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.6;
}

@media (max-width: 1024px) {
  .switcher-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .header-right {
    width: 100%;
    flex-wrap: wrap;
  }
  .switcher-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .overview-grid {
    grid-template-columns: 1fr;
  }
  .handcard-grid, .handcard-grid.cols-3 {
    grid-template-columns: 1fr;
  }
  .cross-three-columns {
    grid-template-columns: 1fr;
  }
  .deduction-four-columns {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .other-item-card {
    flex-direction: column;
    align-items: flex-start;
  }
  .task-card-footer {
    flex-direction: column;
    align-items: flex-start;
  }
  .footer-actions {
    margin-left: 0;
    width: 100%;
    justify-content: flex-end;
  }
}

/* 卡片 3（不适用）：个人独资企业 / 个体工商户不缴企业所得税 */
.iit-notes {
  margin: 14px 2px 6px;
  padding: 0;
  list-style: none;
  font-size: 12px;
  line-height: 1.7;
  opacity: 0.75;
}
.iit-notes li::before {
  content: '· ';
}
.iit-annual-section {
  margin-top: 20px;
  padding: 16px 18px;
  border-radius: 8px;
  border: 1px dashed var(--solo-border, #dde3ea);
  background: var(--solo-bg-muted, #f6f7f9);
}
.iit-annual-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0 14px;
}
.iit-annual-inputs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  margin-top: 14px;
  font-size: 12px;
}
.iit-annual-inputs label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.cit-not-applicable {
  margin: 18px 2px 6px;
  padding: 16px 18px;
  border-radius: 8px;
  background: var(--solo-bg-muted, #f6f7f9);
  border: 1px dashed var(--solo-border, #dde3ea);
}
.cit-na-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.cit-na-text {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--solo-text-secondary, #586474);
}
.cit-na-text:last-child {
  margin-bottom: 0;
  color: var(--solo-text-muted, #636e7c);
}
</style>
