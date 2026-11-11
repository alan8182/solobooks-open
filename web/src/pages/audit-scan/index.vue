<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { parseBalanceFileWithPreview, submitHealthScan } from '@/api/healthScan'
import { saveWorkpaperImage } from '@/utils/workpaperImage'

const router = useRouter()
const message = useMessage()
const currentMode = ref<'quiz' | 'balance'>('quiz')
const loading = ref(false)
const report = ref<ScanReport | null>(null)
const workpaperEl = ref<HTMLElement | null>(null)
const savingImage = ref(false)
const reportSerial = ref('')

interface Check {
  code: string
  name: string
  status: string
  observed?: unknown
  missing?: unknown
  fix?: unknown
  source?: unknown
  dimension?: string
  deduction?: number | null
}
interface ScanReport {
  totalScore: number | null
  levelLabel?: string
  healthLevel?: string
  dimensions?: Array<{ key: string; name: string; score: number | null; detail?: string; statusLabel?: string }>
  checks?: Check[]
  flags?: Array<{ code: string; name: string; desc?: string; fix?: string; legalBasis?: string }>
  coverage?: { completed: number; applicable: number; percent: number | null }
  scoreBreakdown?: unknown
  views?: unknown
  meta?: Record<string, unknown>
}
interface BalancePreview {
  periodLabel: string | null
  balanceType: string | null
  unit: string | null
  debitTotal: number | string | null
  creditTotal: number | string | null
  warnings: string[]
  hasAmbiguity: boolean
  selectedColumns: unknown
}

type Option = { val: string; text: string }
type Question = { key: string; title: string; options: Option[]; when?: { key: string; value: string } }
const unknownOptions: Option[] = [{ val: 'UNKNOWN', text: '不清楚' }, { val: 'NA', text: '不适用' }]
const yesNo: Option[] = [{ val: 'YES', text: '是' }, { val: 'NO', text: '否' }, ...unknownOptions]
const questions: Question[] = [
  { key: 'q1', title: '个人替主体垫付支出后，通常如何处理？', options: [
    { val: 'A', text: '没有个人垫付' }, { val: 'B', text: '有垫付，按单据报销' }, { val: 'C', text: '有垫付，尚未规范处理' }, ...unknownOptions,
  ] },
  { key: 'q2', title: '无票支出通常如何处理？', options: [
    { val: 'A', text: '取得真实业务凭据并依法处理' }, { val: 'B', text: '用与业务无关的票据列支' }, { val: 'C', text: '挂往来账，尚未处理' }, ...unknownOptions,
  ] },
  { key: 'q3', title: '主体向个人转款通常如何记录？', options: [
    { val: 'A', text: '按工资、分红等实际业务记录' }, { val: 'B', text: '记为借款或往来款，跨年尚未归还' }, { val: 'C', text: '没有此类转款' }, ...unknownOptions,
  ] },
  { key: 'q4', title: '研发支出是否建立辅助账？', options: [
    { val: 'A', text: '有研发支出，未建立辅助账' }, { val: 'B', text: '有研发支出，已建立辅助账' }, { val: 'C', text: '无研发支出' }, ...unknownOptions,
  ] },
  { key: 'q5', title: '对财务报表中的利润情况了解多少？', options: [
    { val: 'A', text: '了解报表中的利润情况' }, { val: 'B', text: '利润长期接近零或微亏' }, { val: 'C', text: '从未查看报表' }, ...unknownOptions,
  ] },
  { key: 'q6', title: '能否取得主体的账簿和财务报表？', options: [
    { val: 'A', text: '可以取得' }, { val: 'B', text: '无法取得完整账表' }, { val: 'C', text: '不清楚需要哪些账表' }, ...unknownOptions,
  ] },
  { key: 'loanOccurrence', title: '本期间是否发生主体向个人的借款？', options: yesNo },
  { key: 'loanCounterparty', title: '借款对象是谁？', when: { key: 'loanOccurrence', value: 'YES' }, options: [
    { val: 'SHAREHOLDER', text: '股东' }, { val: 'EMPLOYEE', text: '员工' }, { val: 'OTHER', text: '其他个人' }, ...unknownOptions,
  ] },
  { key: 'loanRepayment', title: '该借款是否已归还？', when: { key: 'loanOccurrence', value: 'YES' }, options: [
    { val: 'YES', text: '全部归还' }, { val: 'NO', text: '尚未归还' }, { val: 'PARTIAL', text: '部分归还' }, ...unknownOptions,
  ] },
  { key: 'loanPurpose', title: '借款用途是什么？', when: { key: 'loanOccurrence', value: 'YES' }, options: [
    { val: 'BUSINESS', text: '经营用途' }, { val: 'PERSONAL', text: '个人用途' }, { val: 'OTHER', text: '其他用途' }, ...unknownOptions,
  ] },
  { key: 'rdActivities', title: '本期间是否开展研发活动？', options: yesNo },
  { key: 'rdAuxiliaryLedger', title: '研发支出是否建立辅助账？', when: { key: 'rdActivities', value: 'YES' }, options: yesNo },
  { key: 'assetExists', title: '是否存在需要核查的固定资产？', options: yesNo },
  { key: 'assetPutIntoUse', title: '该资产是否已投入使用？', when: { key: 'assetExists', value: 'YES' }, options: yesNo },
  { key: 'advanceReceived', title: '是否存在需要核查的预收款？', options: yesNo },
  { key: 'advancePerformance', title: '预收款对应的义务是否已履行？', when: { key: 'advanceReceived', value: 'YES' }, options: [
    { val: 'YES', text: '已全部履行' }, { val: 'NO', text: '尚未履行' }, { val: 'PARTIAL', text: '部分履行' }, ...unknownOptions,
  ] },
]
const answers = ref<Record<string, string>>({})
const visibleQuestions = computed(() => questions.filter(q => {
  const quizQuestion = /^q[1-6]$/.test(q.key)
  return (currentMode.value === 'quiz' ? quizQuestion : !quizQuestion) && (!q.when || answers.value[q.when.key] === q.when.value)
}))
const profile = ref({ companyType: '', period: '', accountingStandard: '', vatType: '' })
const uploadedFileName = ref('')
const parsedRows = ref<Record<string, unknown>[]>([])
const preview = ref<BalancePreview | null>(null)
const balanceMeta = ref({ period: '', balanceType: '', currencyUnit: '', confirmed: false, complete: false })
const disclaimer = '本报告仅依据所提供资料和回答进行有限规则检查，不构成审计、税务或法律意见；未覆盖事项不能视为无风险，请结合原始凭证并咨询专业人士。'
const reportError = ref('')
function normalizedPeriod(label: string | null): string {
  const match = label?.trim().match(/^(\d{4})[年\-/](\d{1,2})月?$/)
  if (!match || Number(match[2]) < 1 || Number(match[2]) > 12) return ''
  return `${match[1]}-${match[2].padStart(2, '0')}`
}
const sourceLabel = computed(() => {
  const source = report.value?.meta?.source
  if (source === 'backend') return '服务端分析'
  if (source === 'local-quiz') return '本地问卷规则（服务端不可用）'
  if (source === 'local-template') return '本地占位结果（未完成余额表分析）'
  return source ? String(source) : '来源未提供'
})
const reportScope = computed(() => currentMode.value === 'balance' ? '科目余额表及补充回答' : '问卷回答')
const coverageLabel = computed(() => {
  const coverage = report.value?.coverage
  if (!coverage) return '未提供'
  if (!coverage.applicable || coverage.percent == null) return '不适用'
  return `${coverage.percent}%`
})
function errorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { detail?: unknown } } }).response
    if (typeof response?.data?.detail === 'string') return response.data.detail
  }
  return err instanceof Error ? err.message : fallback
}
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  if (Array.isArray(value)) return value.length ? value.map(display).join('；') : '无'
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>).map(([key, val]) => `${key}: ${display(val)}`).join('；') || '未提供'
  return String(value)
}
function statusLabel(status: string): string {
  const labels: Record<string, string> = { PASS: '已检查', RISK: '需关注', ERROR: '检查失败（未评分）', FAIL: '需关注', WARNING: '需关注', UNKNOWN: '未评分', NA: '不适用', NOT_APPLICABLE: '不适用', INSUFFICIENT_DATA: '未评分', NOT_SCORED: '未评分' }
  return labels[status?.toUpperCase()] || status || '未提供'
}
async function handleFileUpload(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  uploadedFileName.value = ''
  parsedRows.value = []
  preview.value = null
  balanceMeta.value = { period: '', balanceType: '', currencyUnit: '', confirmed: false, complete: false }
  loading.value = true
  try {
    const result = await parseBalanceFileWithPreview(file)
    parsedRows.value = result.rows
    preview.value = result.preview as BalancePreview
    uploadedFileName.value = file.name
    balanceMeta.value.period = normalizedPeriod(result.preview.periodLabel)
    balanceMeta.value.balanceType = result.preview.balanceType || 'unknown'
    balanceMeta.value.currencyUnit = result.preview.unit || 'unknown'
    message.success(`已解析 ${result.rows.length} 行，请核对预览`)
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : '文件解析失败，请检查文件格式')
  } finally {
    loading.value = false
    target.value = ''
  }
}
async function startScan() {
  if (!profile.value.companyType || !profile.value.period || !profile.value.accountingStandard || !profile.value.vatType) {
    message.warning('请填写主体、期间、会计准则和纳税人类型')
    return
  }
  if (currentMode.value === 'balance' && (!parsedRows.value.length || !balanceMeta.value.confirmed || !balanceMeta.value.period || !balanceMeta.value.balanceType || !balanceMeta.value.currencyUnit)) {
    message.warning('请上传余额表并核对确认期间、余额类型及单位')
    return
  }
  const selectedAnswers = Object.fromEntries(visibleQuestions.value.filter(q => answers.value[q.key]).map(q => [q.key, answers.value[q.key]]))
  if (currentMode.value === 'quiz' && !Object.keys(selectedAnswers).length) {
    message.warning('请至少回答一个问题；不清楚可选择“不清楚”')
    return
  }
  loading.value = true
  reportError.value = ''
  try {
    const payload = {
      mode: currentMode.value,
      answers: selectedAnswers,
      rows: currentMode.value === 'balance' ? parsedRows.value : undefined,
      profile: { ...profile.value },
      balanceMeta: currentMode.value === 'balance' ? { ...balanceMeta.value, balanceType: preview.value?.balanceType === 'unknown' ? balanceMeta.value.balanceType : preview.value?.balanceType || 'unknown' } : undefined,
    }
    report.value = await submitHealthScan(payload) as unknown as ScanReport
    reportSerial.value = `SCAN-${Date.now()}`
    message.success('体检结果已生成')
  } catch (err: unknown) {
    reportError.value = errorMessage(err, '体检分析失败，请稍后重试')
    message.error(reportError.value)
  } finally {
    loading.value = false
  }
}
function reportSummary(): string {
  if (!report.value) return ''
  const coverage = report.value.coverage
  const lines = (report.value.checks || []).map(c => `${c.code} ${c.name}：${statusLabel(c.status)}；观察：${display(c.observed)}；缺失：${display(c.missing)}；依据：${display(c.source)}；建议：${display(c.fix)}`)
  return ['梭罗记账 · 财税健康体检', `范围：${reportScope.value}`, `来源：${sourceLabel.value}`, `主体：${profile.value.companyType}；期间：${profile.value.period}；准则：${profile.value.accountingStandard}；纳税人类型：${profile.value.vatType}`, `评分：${report.value.totalScore == null ? '未评分' : `${report.value.totalScore} / 100`} ${report.value.levelLabel || ''}`, `覆盖率：${coverage ? `${coverage.completed}/${coverage.applicable}（${coverageLabel.value}）` : '未提供'}`, ...lines, disclaimer].join('\n')
}
async function copyReportSummary() {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(reportSummary())
    message.success('报告摘要已复制')
  } catch {
    message.error('复制失败，请手动复制报告内容')
  }
}
async function saveReportImage() {
  if (!workpaperEl.value || savingImage.value) return
  savingImage.value = true
  try {
    await saveWorkpaperImage(workpaperEl.value, reportSerial.value)
    message.success('报告图片已保存')
  } catch {
    message.error('图片生成失败，请重试')
  } finally {
    savingImage.value = false
  }
}
</script>

<template>
  <div class="audit-scan-page">
    <header class="scan-header"><div class="header-inner">
      <button class="brand-wrap" @click="router.push('/landing-v2')"><img src="/solo-logo.png" alt="SoloBooks" class="brand-logo" /><span>梭罗记账 · 财税健康体检</span></button>
      <NButton quaternary size="small" @click="router.push('/landing-v2')">返回官网</NButton>
    </div></header>
    <main class="scan-main">
      <section class="scan-hero">
        <p class="eyebrow">财税健康体检</p>
        <h1>从已知信息出发，梳理需要关注的事项</h1>
        <p>选择问卷或上传科目余额表，结果取决于提供的资料及可执行的检查，不替代专业判断。</p>
        <div v-if="!report" class="mode-tabs">
          <button class="mode-tab-btn" :class="{ active: currentMode === 'quiz' }" @click="currentMode = 'quiz'">问卷体检</button>
          <button class="mode-tab-btn" :class="{ active: currentMode === 'balance' }" @click="currentMode = 'balance'">余额表体检</button>
        </div>
      </section>
      <section v-if="!report" class="scan-card-section">
        <h2>主体与期间</h2>
        <div class="profile-grid">
          <label>主体类型<select v-model="profile.companyType"><option value="" disabled>请选择</option><option value="OPC">一人有限责任公司</option><option value="LTD">多股东有限责任公司</option><option value="INDIVIDUAL">个体工商户</option><option value="SOLE_PROPRIETOR">个人独资企业</option><option value="UNKNOWN">不清楚</option></select></label>
          <label>体检期间<input v-model="profile.period" type="month" /></label>
          <label>会计准则<select v-model="profile.accountingStandard"><option value="" disabled>请选择</option><option value="SMALL_ENTERPRISE">小企业会计准则</option><option value="ENTERPRISE">企业会计准则</option><option value="OTHER">其他</option><option value="UNKNOWN">不清楚</option></select></label>
          <label>纳税人类型<select v-model="profile.vatType"><option value="" disabled>请选择</option><option value="GENERAL">一般纳税人</option><option value="SMALL_SCALE">小规模纳税人</option><option value="UNKNOWN">不清楚</option></select></label>
        </div>
        <div v-if="currentMode === 'balance'" class="upload-container">
          <h2>上传科目余额表</h2><p>支持 .xlsx、.xls、.csv；解析后的结构化数据将提交用于体检，请核对后再提交。</p>
          <label class="btn-file-pick">选择文件<input type="file" accept=".xlsx,.xls,.csv" @change="handleFileUpload" /></label>
          <div v-if="preview" class="preview-box">
            <h3>解析预览 · {{ uploadedFileName }}（{{ parsedRows.length }} 行）</h3>
            <p>识别期间：{{ display(preview.periodLabel) }}；余额类型：{{ preview.balanceType === 'ending' ? '期末余额' : preview.balanceType === 'opening' ? '期初余额' : '不清楚' }}；单位：{{ preview.unit === 'yuan' ? '元' : '不清楚' }}</p>
            <p>识别列（从 0 开始，-1 表示未识别）：{{ display(preview.selectedColumns) }}</p>
            <p>借方合计：{{ display(preview.debitTotal) }}；贷方合计：{{ display(preview.creditTotal) }}</p>
            <p v-if="preview.hasAmbiguity" class="warning">存在识别歧义，请仔细核对原表。</p>
            <ul v-if="preview.warnings?.length" class="warning"><li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li></ul>
            <div class="profile-grid">
              <label>余额表期间<input v-model="balanceMeta.period" type="month" @input="balanceMeta.confirmed = false" /></label>
              <label v-if="preview.balanceType === 'unknown'">确认余额类型<select v-model="balanceMeta.balanceType" @change="balanceMeta.confirmed = false"><option value="unknown">不清楚（暂不评分）</option><option value="ending">期末余额</option><option value="opening">期初余额（不作为期末余额评分）</option></select></label>
              <label v-else>识别余额类型<input :value="preview.balanceType === 'ending' ? '期末余额' : '期初余额（不作为期末余额评分）'" readonly /></label>
              <label>金额单位<select v-model="balanceMeta.currencyUnit" @change="balanceMeta.confirmed = false"><option value="unknown">不清楚</option><option value="yuan">元</option></select></label>
            </div>
            <label class="check-label"><input v-model="balanceMeta.complete" type="checkbox" /> 我确认余额表涵盖本次体检所需的完整科目范围</label>
            <label class="check-label"><input v-model="balanceMeta.confirmed" type="checkbox" /> 我已核对识别列、期间、余额类型、单位和合计</label>
          </div>
        </div>
        <div class="quiz-container">
          <h2>{{ currentMode === 'quiz' ? '经营情况问卷' : '补充经营情况' }}</h2>
          <p>所有问题均不预选；可以跳过，也可以选择“不清楚”或“不适用”。</p>
          <fieldset v-for="q in visibleQuestions" :key="q.key" class="quiz-item">
            <legend>{{ q.title }}</legend>
            <div class="quiz-options"><label v-for="opt in q.options" :key="opt.val" class="quiz-option-label" :class="{ selected: answers[q.key] === opt.val }"><input v-model="answers[q.key]" type="radio" :name="q.key" :value="opt.val" />{{ opt.text }}</label></div>
          </fieldset>
        </div>
        <p class="disclaimer">{{ disclaimer }} 提交后，问卷与解析出的余额数据会发送至体检服务；请勿在表中包含不必要的敏感信息。</p>
        <p v-if="reportError" class="warning" role="alert">{{ reportError }}</p>
        <button class="scan-cta-btn" :disabled="loading" @click="startScan">{{ loading ? '处理中…' : '生成体检结果' }}</button>
      </section>
      <section v-else class="report-section">
        <div class="report-actions"><button @click="copyReportSummary">复制报告摘要</button><button :disabled="savingImage" @click="saveReportImage">{{ savingImage ? '正在生成图片…' : '保存为图片' }}</button><button @click="report = null">重新体检</button></div>
        <article ref="workpaperEl" class="audit-workpaper">
          <header class="report-heading"><div><p class="eyebrow">{{ reportSerial }}</p><h2>财税健康体检结果</h2></div><span>范围：{{ reportScope }}</span></header>
          <div class="report-meta"><span>来源：{{ sourceLabel }}</span><span>主体：{{ profile.companyType }}</span><span>期间：{{ profile.period }}</span><span>准则：{{ profile.accountingStandard }}</span><span>纳税人类型：{{ profile.vatType }}</span></div>
          <p v-if="report.meta?.source === 'local-template'" class="warning">本结果为占位结果，未完成余额表分析，不应作为决策依据。</p>
          <div class="score-grid"><div><span>综合评分</span><strong>{{ report.totalScore == null ? '未评分' : `${report.totalScore} / 100` }}</strong><span>{{ report.levelLabel }}</span></div><div><span>检查覆盖率</span><strong>{{ coverageLabel }}</strong><span v-if="report.coverage">已完成 {{ report.coverage.completed }} / 适用 {{ report.coverage.applicable }}</span></div></div>
          <div v-if="report.dimensions?.length" class="report-block"><h3>维度结果</h3><ul><li v-for="dim in report.dimensions" :key="dim.key">{{ dim.name }}：{{ dim.score == null ? '未评分' : dim.score }} {{ dim.statusLabel }} {{ dim.detail }}</li></ul></div>
          <div class="report-block"><h3>检查明细</h3><p v-if="!report.checks?.length">未提供检查明细，不能据此认定无风险。</p><div v-for="check in report.checks" :key="check.code" class="check-card"><h4>{{ check.name }} <small>{{ check.code }} · {{ statusLabel(check.status) }}</small></h4><p>观察：{{ display(check.observed) }}</p><p>缺失：{{ display(check.missing) }}</p><p>依据 / 来源：{{ display(check.source) }}</p><p>建议：{{ display(check.fix) }}</p><p>维度：{{ display(check.dimension) }}；扣分：{{ check.deduction == null ? '未评分' : check.deduction }}</p></div></div>
          <div v-if="report.scoreBreakdown" class="report-block"><h3>评分明细</h3><p>{{ display(report.scoreBreakdown) }}</p></div>
          <p class="disclaimer">{{ disclaimer }}</p>
        </article>
      </section>
    </main>
  </div>
</template>

<style scoped>
.audit-scan-page { min-height: 100vh; background: #f4f6f5; color: #22322d; font-family: system-ui, sans-serif; }
.scan-header { background: #fff; border-bottom: 1px solid #e1e8e4; }
.header-inner { max-width: 1080px; margin: auto; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }
.brand-wrap { display: flex; align-items: center; gap: 10px; font-weight: 700; border: 0; background: transparent; color: inherit; cursor: pointer; }
.brand-logo { width: 30px; height: 30px; object-fit: contain; }
.scan-main { max-width: 1080px; margin: auto; padding: 40px 24px 80px; }
.scan-hero { margin-bottom: 24px; } .scan-hero h1 { font-size: clamp(28px, 4vw, 40px); margin: 8px 0 12px; } .scan-hero p { color: #63756b; line-height: 1.7; }
.eyebrow { color: #38866a !important; font-size: 13px; font-weight: 700; letter-spacing: .08em; }
.mode-tabs, .report-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 22px; }
.mode-tab-btn, .report-actions button { background: #fff; border: 1px solid #d8e2dc; border-radius: 8px; padding: 10px 16px; cursor: pointer; color: #31483b; }
.mode-tab-btn.active { background: #e7f4ed; border-color: #368364; color: var(--solo-income); font-weight: 700; }
.scan-card-section, .audit-workpaper { background: #fff; border: 1px solid #e0e8e3; border-radius: 14px; padding: 32px; }
h2 { font-size: 21px; margin: 0 0 20px; } h3 { font-size: 17px; } p { line-height: 1.65; }
.profile-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.profile-grid label { display: grid; gap: 8px; font-size: 14px; font-weight: 600; }
.profile-grid input, .profile-grid select { box-sizing: border-box; width: 100%; min-height: 40px; border: 1px solid #cfdad3; border-radius: 6px; padding: 8px; background: white; color: #22322d; }
.upload-container, .quiz-container { margin-top: 36px; } .upload-container > p, .quiz-container > p { color: #63756b; }
.btn-file-pick { display: inline-block; background: #edf6f1; padding: 10px 16px; border-radius: 6px; cursor: pointer; color: var(--solo-income); } .btn-file-pick input { display: block; margin-top: 8px; }
.preview-box { margin-top: 16px; background: #f7faf8; border: 1px solid #dce8df; padding: 18px; border-radius: 8px; overflow-wrap: anywhere; }
.warning { color: var(--solo-warning); } .check-label { display: block; margin-top: 15px; }
.quiz-item { border: 0; border-top: 1px solid #edf0ee; padding: 20px 0; margin: 0; min-width: 0; }.quiz-item legend { font-weight: 650; padding: 0 0 8px; }
.quiz-options { display: flex; flex-wrap: wrap; gap: 8px; }.quiz-option-label { border: 1px solid #dce5df; border-radius: 7px; padding: 9px 12px; cursor: pointer; font-size: 14px; }.quiz-option-label.selected { border-color: #368364; background: #eef8f2; }.quiz-option-label input { margin-right: 7px; }
.disclaimer { color: #65746c; background: #f5f7f6; border-radius: 7px; padding: 15px; font-size: 13px; }.scan-cta-btn { border: 0; background: var(--solo-income); color: white; padding: 13px 26px; border-radius: 7px; cursor: pointer; font-weight: 700; }.scan-cta-btn:disabled { opacity: .6; cursor: wait; }
.report-actions { justify-content: flex-end; margin: 0 0 12px; }.report-heading { display: flex; justify-content: space-between; gap: 16px; align-items: center; }.report-heading h2 { margin-bottom: 0; }.report-meta { display: flex; flex-wrap: wrap; gap: 12px 22px; color: #607268; border-block: 1px solid #e5ece7; margin: 22px 0; padding: 14px 0; font-size: 13px; }
.score-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.score-grid > div { background: #f2f8f4; padding: 20px; border-radius: 8px; display: grid; gap: 8px; }.score-grid strong { font-size: 30px; color: var(--solo-income); }.score-grid span { font-size: 14px; color: #52675a; }
.report-block { margin-top: 30px; }.check-card { border-top: 1px solid #e4ebe6; padding: 12px 0; }.check-card h4 { margin: 0 0 8px; }.check-card small { color: #66796c; font-weight: 400; }.check-card p { margin: 4px 0; font-size: 14px; overflow-wrap: anywhere; }
@media (max-width: 700px) { .scan-main { padding: 24px 14px 50px; }.scan-card-section, .audit-workpaper { padding: 20px; }.profile-grid { grid-template-columns: 1fr 1fr; }.report-heading { align-items: flex-start; flex-direction: column; } }
</style>
