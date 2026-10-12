<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  NButton, NCard, NSelect, NSpin, NSwitch, NTag, useMessage,
} from 'naive-ui'
import apiClient from '@/api/client'
import {
  calculateDeadlineSummary,
  calculateExemptionGauge,
  formatCopyAmount,
  generateTaxCopilotInsight,
} from '@/utils/taxFiling'

const route = useRoute()
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
  }
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
  preferences: { code: string; title: string; detail: string; action: string }[]
  checklist: ChecklistItem[]
  summary: {
    readyToFile: boolean
    blockingIssues: string[]
    warnings: string[]
    vatPayable: number
    citPayable?: number
    overdue: string[]
  }
}

const now = new Date()
const defaultQuarter = `${now.getFullYear()}-Q${Math.floor(now.getMonth() / 3) + 1 || 4}`

const periodOptions = Array.from({ length: 6 }).map((_, i) => {
  const qIndex = Math.floor(now.getMonth() / 3) - i
  const year = now.getFullYear() + Math.floor(qIndex / 4)
  const q = ((qIndex % 4) + 4) % 4 + 1
  return { label: `${year} 年第 ${q} 季度`, value: `${year}-Q${q}` }
})

const queryPeriod = route.query.period ? String(route.query.period).trim() : ''
if (queryPeriod && !periodOptions.some(o => o.value === queryPeriod)) {
  periodOptions.unshift({ label: `${queryPeriod} 季度`, value: queryPeriod })
}

const period = ref(queryPeriod || defaultQuarter)
const loading = ref(false)
const pkg = ref<Package | null>(null)
const filingBusy = ref('')
const hideZeroFields = ref(true)

async function load() {
  loading.value = true
  try {
    const data = await apiClient.get(`/api/tax-filing/packages/${period.value}`)
    pkg.value = data as unknown as Package
  } catch (err) {
    message.error(err instanceof Error ? err.message : '申报作业包加载失败')
  } finally {
    loading.value = false
  }
}

async function markFiled(deadlineId: string | null, kindName: string, zero = false) {
  if (!deadlineId) {
    message.warning('该事项没有关联征期记录')
    return
  }
  filingBusy.value = deadlineId
  try {
    await apiClient.post(`/api/tax-filing/items/${deadlineId}/file`, {
      evidence: { via: 'tax-filing-page', zeroFiling: zero, filedAt: new Date().toISOString() },
    })
    message.success(`${kindName} 已标记完成`)
    await load()
  } catch (err) {
    message.error(err instanceof Error ? err.message : '操作失败')
  } finally {
    filingBusy.value = ''
  }
}

// 导出官方财务报表 XLS 模板
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
    message.success('财务报表 XLS 模板已成功导出')
  } catch (err) {
    let detail = err instanceof Error ? err.message : '导出失败'
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

// 财务报表事项单独突出显示
const finStatementCheckItem = computed(() => {
  return (pkg.value?.checklist ?? []).find(item => item.taxKind === 'fin_statements')
})

const otherChecklistItems = computed(() => {
  return (pkg.value?.checklist ?? []).filter(item => item.taxKind !== 'fin_statements')
})

// 折叠开关：三方核验底稿 / 增值税与所得税完整栏次（模板同名）
const showCrossDiff = ref(false)
const showFullVatTable = ref(false)
const showFullCitTable = ref(false)

const totalEstimatedTax = computed(() => {
  if (!pkg.value) return 0
  return (pkg.value.summary?.vatPayable || 0) + (pkg.value.summary?.citPayable || 0)
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
  if (!pkg.value) return { total: 0, filed: 0, pending: 0, overdue: 0 }
  const total = 2 + (finStatementCheckItem.value ? 1 : 0) + otherChecklistItems.value.length
  let filed = 0
  if (pkg.value.vat?.deadline?.status === 'filed') filed++
  if (pkg.value.cit?.deadline?.status === 'filed') filed++
  if (finStatementCheckItem.value?.status === 'filed') filed++
  for (const item of otherChecklistItems.value) {
    if (item.status === 'filed') filed++
  }
  return {
    total,
    filed,
    pending: total - filed,
    overdue: deadlineSummary.value.overdueCount,
  }
})

function statusTag(status: string) {
  if (status === 'filed') return { text: '已完成', type: 'success' as const }
  if (status === 'untracked') return { text: '未跟踪', type: 'default' as const }
  return { text: '待申报', type: 'warning' as const }
}

function deadlineText(d: DeadlineInfo | ChecklistItem): string {
  if (!d.dueDate) return ''
  const days = d.daysLeft
  if (days == null) return `截止 ${d.dueDate}`
  if (days < 0) return `已逾期 ${-days} 天（截止 ${d.dueDate}）`
  if (days === 0) return `今日截止（${d.dueDate}）`
  return `剩 ${days} 天（截止 ${d.dueDate}）`
}

onMounted(load)
</script>

<template>
  <div class="tax-filing-page">
    <!-- 页头 -->
    <div class="page-header">
      <div class="header-left">
        <div class="title-row">
          <h2>税报 · 申报作业包</h2>
          <NTag size="small" type="primary" round :bordered="false" class="mode-tag">小规模纳税人</NTag>
        </div>
        <span class="header-sub">账实核对 · 免税监控 · 电子税务局极速抄报作业台</span>
      </div>
      <div class="header-right">
        <NSelect
          v-model:value="period"
          :options="periodOptions"
          style="width: 170px"
          size="small"
          @update:value="load"
        />
        <NButton
          type="primary"
          secondary
          size="small"
          :loading="exporting"
          @click="exportFinancialXls"
        >
          📥 导出官方 XLS 模板
        </NButton>
        <NButton size="small" secondary @click="load" :loading="loading">
          刷新数据
        </NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="pkg">
        <!-- 1. 全局定心丸看板 (Overview Cockpit) -->
        <section class="overview-cockpit">
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
                + 所得税 {{ (pkg.summary.citPayable || 0) > 0 ? `¥ ${money(pkg.summary.citPayable)}` : '¥0' }}
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
                    :style="{ width: `${(taskStats.filed / Math.max(1, taskStats.total)) * 100}%` }"
                  />
                </div>
                <span class="progress-text">{{ taskStats.filed }} / {{ taskStats.total }} 已办结</span>
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

          <!-- 智能税情简报条（轻量紧凑） -->
          <div class="smart-insight-bar" v-if="copilotInsight">
            <span class="insight-badge">🤖 AI 税情简评</span>
            <span class="insight-headline">{{ copilotInsight.headline }}</span>
            <span class="insight-advice">建议：{{ copilotInsight.pathAdvice }}</span>
          </div>

          <!-- 前置体检：账·票·流水三方核验条 (支持就地展开差异) -->
          <div class="cross-check-bar" :class="{ 'is-diff': !pkg.crossCheck.consistent }">
            <div class="cross-bar-left">
              <span class="cross-check-icon">{{ pkg.crossCheck.consistent ? '✓' : '⚠' }}</span>
              <span class="cross-check-title">
                {{ pkg.crossCheck.consistent ? '账·票·流水三方核验一致，无跨期隐患' : `账·票·流水检测到 ${pkg.crossCheck.differences.length} 项差异` }}
              </span>
              <span class="cross-check-counts" v-if="pkg.crossCheck.consistent">
                (账载收入 ¥{{ money(pkg.crossCheck.bookSales) }} / 发票 ¥{{ money(pkg.crossCheck.invoiceTotal) }} / 流水 ¥{{ money(pkg.crossCheck.bankInflow) }})
              </span>
            </div>
            <NButton size="tiny" text @click="showCrossDiff = !showCrossDiff">
              {{ showCrossDiff ? '收起核验底稿' : '查看三方核验底稿 ▾' }}
            </NButton>
          </div>

          <!-- 展开的三方核验底稿与差异详情 -->
          <div v-show="showCrossDiff" class="cross-detail-panel">
            <div class="cross-three-columns">
              <div class="column-card">
                <div class="col-head">
                  <span class="col-name">1. 账载销售额</span>
                  <span class="col-count" v-if="pkg.crossCheck.bookCount != null">{{ pkg.crossCheck.bookCount }} 笔入账</span>
                </div>
                <div class="col-amount">¥ {{ money(pkg.crossCheck.bookSales) }}</div>
                <div class="col-desc">梭罗记账已确认的经营收入合计</div>
              </div>
              <div class="column-card">
                <div class="col-head">
                  <span class="col-name">2. 发票开具合计</span>
                  <span class="col-count" v-if="pkg.crossCheck.invoiceCount != null">{{ pkg.crossCheck.invoiceCount }} 张发票</span>
                </div>
                <div class="col-amount">¥ {{ money(pkg.crossCheck.invoiceTotal) }}</div>
                <div class="col-desc">当期已开具的普票/专票总额</div>
              </div>
              <div class="column-card">
                <div class="col-head">
                  <span class="col-name">3. 银行/微信入账</span>
                  <span class="col-count" v-if="pkg.crossCheck.bankInflowCount != null">{{ pkg.crossCheck.bankInflowCount }} 笔流水</span>
                </div>
                <div class="col-amount">¥ {{ money(pkg.crossCheck.bankInflow) }}</div>
                <div class="col-desc">对公账户与经营钱包实际到账资金</div>
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
        </section>

        <!-- 2. 核心申报任务流 (Task Cards Flow) -->
        <section class="task-cards-flow">
          <div class="flow-header">
            <h3>申报作业清单（共 3 项）</h3>
            <span class="flow-sub">按顺序核对关键栏次抄报至电子税务局，完成后点击标记办结即可</span>
          </div>

          <!-- 任务 1：增值税小规模申报 (主表) -->
          <NCard :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">1</span>
                <div>
                  <h4 class="task-name">增值税小规模纳税人申报（主表）</h4>
                  <span class="task-desc">
                    {{ pkg.vat.isBelowThreshold ? '季度未超 30 万元，享受小规模免税政策' : '已超免税限额，按 1% 征收率申报' }}
                  </span>
                </div>
              </div>
              <div class="task-status-box">
                <NTag size="small" :type="statusTag(pkg.vat.deadline.status).type" :bordered="false">
                  {{ statusTag(pkg.vat.deadline.status).text }}
                </NTag>
                <span class="task-deadline-text">{{ deadlineText(pkg.vat.deadline) }}</span>
              </div>
            </div>

            <!-- 抄报核心手卡 (Quick Copy Hand-Card) -->
            <div class="quick-copy-handcard">
              <div class="handcard-header">
                <span class="handcard-title">👉 电子税务局快速抄报手卡</span>
                <span class="handcard-tip">（在电子税务局申报表重点填报以下关键数值）</span>
              </div>
              <div class="handcard-grid">
                <div class="handcard-item">
                  <div class="handcard-item-top">
                    <span class="item-label">{{ pkg.vat.isBelowThreshold ? '免税销售额（主表第 10 行）' : '应税销售额（开票销售额）' }}</span>
                    <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.vat.totalSales, '销售额')">
                      复制
                    </NButton>
                  </div>
                  <div class="item-amount-val">
                    ¥ {{ money(pkg.vat.totalSales) }}
                  </div>
                </div>

                <div class="handcard-item">
                  <div class="handcard-item-top">
                    <span class="item-label">本期应纳增值税额</span>
                    <NButton size="tiny" quaternary type="primary" @click="copyAmount(pkg.summary.vatPayable, '应纳税额')">
                      复制
                    </NButton>
                  </div>
                  <div class="item-amount-val" :class="{ 'is-zero-val': pkg.summary.vatPayable === 0 }">
                    {{ pkg.summary.vatPayable > 0 ? `¥ ${money(pkg.summary.vatPayable)}` : '免征 ¥0.00' }}
                  </div>
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
                  <span class="tools-desc">完整栏次逐项核对底稿：</span>
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

            <!-- 卡片底栏：办结动作 -->
            <div class="task-card-footer" v-if="pkg.vat.deadline.deadlineId">
              <div class="footer-note">
                填报完成后，点击右侧标记已申报，系统将自动核销对应征期待办
              </div>
              <div class="footer-actions">
                <NButton
                  v-if="pkg.vat.deadline.status !== 'filed'"
                  type="primary"
                  :loading="filingBusy === pkg.vat.deadline.deadlineId"
                  @click="markFiled(pkg.vat.deadline.deadlineId, '增值税申报', pkg.summary.vatPayable === 0)"
                >
                  已在电子税务局申报，标记办结
                </NButton>
                <span v-else class="task-done-badge">✓ 本期增值税申报已办结</span>
              </div>
            </div>
          </NCard>

          <!-- 任务 2：企业所得税预缴 (A200000) -->
          <NCard :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">2</span>
                <div>
                  <h4 class="task-name">企业所得税季度预缴（A200000）</h4>
                  <span class="task-desc">
                    {{ pkg.cit.note || '享受小型微利企业优惠政策（实际税负 5%）' }}
                  </span>
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
                <span class="handcard-tip">（A200000 报表核心行次）</span>
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

            <!-- 卡片底栏：办结动作 -->
            <div class="task-card-footer" v-if="pkg.cit.deadline.deadlineId">
              <div class="footer-note">
                填报完成后，点击右侧标记已申报，系统将自动核销企业所得税征期待办
              </div>
              <div class="footer-actions">
                <NButton
                  v-if="pkg.cit.deadline.status !== 'filed'"
                  type="primary"
                  :loading="filingBusy === pkg.cit.deadline.deadlineId"
                  @click="markFiled(pkg.cit.deadline.deadlineId, '企业所得税预缴')"
                >
                  已在电子税务局申报，标记办结
                </NButton>
                <span v-else class="task-done-badge">✓ 本期企业所得税预缴已办结</span>
              </div>
            </div>
          </NCard>

          <!-- 任务 3：财务报表报送与附税事项 -->
          <NCard :bordered="false" class="task-card">
            <div class="task-card-header">
              <div class="task-title-group">
                <span class="task-step-badge">3</span>
                <div>
                  <h4 class="task-name">财务报表报送与附税事项</h4>
                  <span class="task-desc">
                    小企业会计准则月/季报表；支持直接下载税局标准格式 XLS 导入
                  </span>
                </div>
              </div>
              <div class="task-status-box" v-if="finStatementCheckItem">
                <NTag size="small" :type="statusTag(finStatementCheckItem.status).type" :bordered="false">
                  {{ statusTag(finStatementCheckItem.status).text }}
                </NTag>
                <span class="task-deadline-text">{{ deadlineText(finStatementCheckItem) }}</span>
              </div>
            </div>

            <!-- 核心行动：官方 XLS 下载与报送确认 -->
            <div class="statement-action-card">
              <div class="statement-left">
                <strong class="statement-title">📊 财务报表报送（资产负债表 · 利润表 · 现金流量表）</strong>
                <p class="statement-desc">
                  已自动生成符合国家税务总局标准格式的小企业会计报表。点击右侧直接下载，并在电子税务局「财务报表报送」模块上传导入。
                </p>
              </div>
              <div class="statement-right">
                <NButton
                  type="primary"
                  secondary
                  size="small"
                  :loading="exporting"
                  @click="exportFinancialXls"
                >
                  📥 导出官方 XLS 模板
                </NButton>
                <NButton
                  v-if="finStatementCheckItem && finStatementCheckItem.status !== 'filed'"
                  size="small"
                  :loading="filingBusy === finStatementCheckItem.deadlineId"
                  @click="markFiled(finStatementCheckItem.deadlineId, '财务报表报送')"
                >
                  标记已报送完成
                </NButton>
                <span v-else-if="finStatementCheckItem" class="task-done-badge">
                  已报送完成 ✓
                </span>
              </div>
            </div>

            <!-- 其他税费清单（印花税等） -->
            <div class="other-taxes-sublist" v-if="otherChecklistItems.length">
              <span class="sublist-title">其他税费与合规申报项：</span>
              <div class="other-items-grid">
                <div v-for="item in otherChecklistItems" :key="item.taxKind" class="other-item-row">
                  <div class="other-item-info">
                    <span class="other-name">{{ item.name }}</span>
                    <NTag size="tiny" :type="statusTag(item.status).type" :bordered="false">
                      {{ statusTag(item.status).text }}
                    </NTag>
                    <span class="other-deadline" v-if="item.dueDate">{{ deadlineText(item) }}</span>
                    <span class="other-hint">{{ item.hint }}</span>
                  </div>
                  <div class="other-item-action">
                    <NButton
                      v-if="item.status !== 'filed'"
                      size="tiny"
                      secondary
                      :loading="filingBusy === item.deadlineId"
                      @click="markFiled(item.deadlineId, item.name, item.taxKind === 'stamp_duty')"
                    >
                      {{ item.taxKind === 'stamp_duty' ? '确认无税源 / 办结' : '标记已申报' }}
                    </NButton>
                    <span v-else class="other-done-text">已办结 ✓</span>
                  </div>
                </div>
              </div>
            </div>
          </NCard>
        </section>

        <!-- 底部免责声明 -->
        <div class="footer-disclaimer">
          <p>
            提示：作业包数据基于梭罗记账的账载凭证实时测算，仅供申报辅助参考；最终申报口径与扣缴凭证以国家税务总局电子税务局为准。
          </p>
        </div>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.tax-filing-page {
  max-width: 1240px;
  margin: 0 auto;
  padding-bottom: 32px;
  width: 100%;
  box-sizing: border-box;
}

/* 页头 */
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
  color: var(--solo-text-primary, #1d2129);
}
.mode-tag {
  font-weight: 500;
}
.header-sub {
  font-size: 13px;
  color: var(--solo-text-muted, #86909c);
  margin-top: 4px;
  display: block;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 1. 全局定心丸看板 */
.overview-cockpit {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}
.overview-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
.overview-card {
  background: var(--solo-bg-card, #ffffff);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 16px 18px;
  border: 1px solid var(--solo-border, #f2f3f5);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.primary-tax-card {
  background: linear-gradient(145deg, rgba(24, 160, 88, 0.04) 0%, var(--solo-bg-card, #ffffff) 100%);
  border-color: rgba(24, 160, 88, 0.2);
}
.overview-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.overview-label {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
}
.overview-amount {
  font-size: 24px;
  font-weight: 750;
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #1d2129);
}
.overview-amount.is-zero {
  color: #18a058;
}
.overview-value {
  font-size: 20px;
  font-weight: 700;
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #1d2129);
}
.gauge-val {
  color: #18a058;
}
.overview-sub-detail {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
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
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #18a058;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.progress-text {
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
  flex-shrink: 0;
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
}
.insight-badge {
  font-weight: 600;
  color: var(--solo-ai, #165dff);
  flex-shrink: 0;
}
.insight-headline {
  font-weight: 600;
  color: var(--solo-text-primary, #1d2129);
}
.insight-advice {
  color: var(--solo-text-secondary, #4e5969);
  margin-left: auto;
  font-size: 12px;
}

/* 前置体检：三方核验条 */
.cross-check-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: rgba(24, 160, 88, 0.06);
  border: 1px solid rgba(24, 160, 88, 0.2);
  border-radius: var(--solo-radius, 8px);
  font-size: 13px;
  color: #18a058;
}
.cross-check-bar.is-diff {
  background: rgba(240, 160, 32, 0.06);
  border-color: rgba(240, 160, 32, 0.25);
  color: #d48806;
}
.cross-bar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cross-check-icon {
  font-weight: bold;
}
.cross-check-title {
  font-weight: 500;
}
.cross-check-counts {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
}

/* 三方核验展开面板 */
.cross-detail-panel {
  background: var(--solo-bg-page, #f7f8fa);
  border: 1px solid var(--solo-border, #f2f3f5);
  border-radius: var(--solo-radius, 8px);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cross-three-columns {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.column-card {
  background: var(--solo-bg-card, #ffffff);
  border-radius: 8px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border, #f2f3f5);
}
.col-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.col-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #4e5969);
}
.col-count {
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
}
.col-amount {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  margin: 6px 0 4px;
  color: var(--solo-text-primary, #1d2129);
}
.col-desc {
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
}
.diff-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.diff-row {
  border: 1px solid rgba(240, 160, 32, 0.3);
  background: rgba(240, 160, 32, 0.03);
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
  color: #f0a020;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.diff-hint-text {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-secondary, #4e5969);
}

/* 2. 申报作业清单 */
.task-cards-flow {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.flow-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary, #1d2129);
}
.flow-sub {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
  margin-top: 2px;
  display: block;
}

.task-card {
  border-radius: var(--solo-radius-lg, 12px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  border: 1px solid var(--solo-border, #f2f3f5);
}
.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
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
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--solo-primary, #165dff);
  color: #fff;
  font-size: 13px;
  font-weight: bold;
  flex-shrink: 0;
  margin-top: 2px;
}
.task-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary, #1d2129);
}
.task-desc {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
  margin-top: 3px;
  display: block;
}
.task-status-box {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.task-deadline-text {
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
}

/* 抄报核心手卡 (Quick Copy Hand-Card) */
.quick-copy-handcard {
  background: var(--solo-bg-page, #f7f8fa);
  border: 1px solid var(--solo-border, #f2f3f5);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 14px;
}
.handcard-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.handcard-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary, #1d2129);
}
.handcard-tip {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
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
  border: 1px solid var(--solo-border, #f2f3f5);
  border-radius: 8px;
  padding: 10px 14px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.handcard-item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.item-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #4e5969);
  font-weight: 500;
}
.item-amount-val {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary, #1d2129);
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}
.item-amount-val.is-zero-val {
  color: #18a058;
}

/* 折叠展开底稿栏次 */
.expand-table-section {
  margin-bottom: 12px;
}
.expand-toggle-btn {
  background: none;
  border: none;
  padding: 6px 0;
  font-size: 12px;
  color: var(--solo-primary, #165dff);
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
  border-top: 1px dashed var(--solo-border, #f2f3f5);
}
.table-tools-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.tools-desc {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
}
.tools-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--solo-text-secondary, #4e5969);
}

/* 栏次表 */
.form-sections-wrapper {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form-section-group {
  background: var(--solo-bg-page, #f7f8fa);
  border-radius: 8px;
  padding: 10px 12px;
}
.section-title-bar {
  margin-bottom: 8px;
}
.section-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #4e5969);
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
  border-radius: 6px;
  font-size: 12px;
}
.field-row.is-hidden {
  display: none;
}
.field-info {
  display: flex;
  align-items: center;
  gap: 8px;
}
.line-badge {
  font-size: 11px;
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--solo-text-secondary, #4e5969);
}
.field-label-text {
  color: var(--solo-text-primary, #1d2129);
}
.field-ops {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-amount-val {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #1d2129);
}
.field-amount-val.highlight {
  color: var(--solo-primary, #165dff);
}

/* 卡片底栏：办结动作 */
.task-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border, #f2f3f5);
}
.footer-note {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
}
.footer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.task-done-badge {
  color: #18a058;
  font-weight: 600;
  font-size: 13px;
}

/* 财务报表行动盒 */
.statement-action-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  background: var(--solo-bg-page, #f7f8fa);
  border: 1px solid var(--solo-border, #f2f3f5);
  border-radius: 10px;
  margin-bottom: 14px;
}
.statement-left {
  flex: 1;
}
.statement-title {
  font-size: 13px;
  color: var(--solo-text-primary, #1d2129);
}
.statement-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
  line-height: 1.4;
}
.statement-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

/* 其他附税列表 */
.other-taxes-sublist {
  padding-top: 12px;
  border-top: 1px solid var(--solo-border, #f2f3f5);
}
.sublist-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #4e5969);
  display: block;
  margin-bottom: 8px;
}
.other-items-grid {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.other-item-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--solo-bg-page, #f7f8fa);
  border-radius: 8px;
  font-size: 12px;
}
.other-item-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.other-name {
  font-weight: 500;
  color: var(--solo-text-primary, #1d2129);
}
.other-deadline {
  color: var(--solo-text-muted, #86909c);
}
.other-hint {
  color: var(--solo-text-muted, #86909c);
  margin-left: 8px;
}
.other-done-text {
  color: #18a058;
  font-weight: 500;
  font-size: 12px;
}

/* 底部免责声明 */
.footer-disclaimer {
  margin-top: 20px;
  text-align: center;
}
.footer-disclaimer p {
  margin: 0;
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
  line-height: 1.6;
}

@media (max-width: 900px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .header-right {
    width: 100%;
    flex-wrap: wrap;
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
  .statement-action-card {
    flex-direction: column;
    align-items: flex-start;
  }
  .task-card-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
}
</style>
