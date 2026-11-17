<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import {
  detectBalanceFollowups,
  parseBalanceFileWithPreview,
  submitHealthScan,
  type BalanceFilePreview,
  type HealthScanReport,
} from '@/api/healthScan'
import { saveWorkpaperImage } from '@/utils/workpaperImage'

const router = useRouter()
const message = useMessage()

const currentMode = ref<'quiz' | 'balance'>('quiz')
const companyType = ref<string>('UNKNOWN')
const useAi = ref(true)
const loading = ref(false)
const report = ref<HealthScanReport | null>(null)
const reportError = ref('')
const workpaperEl = ref<HTMLElement | null>(null)
const savingImage = ref(false)
const reportSerial = ref('')

const companyTypes = [
  { val: 'OPC', label: '一人有限责任公司' },
  { val: 'LTD', label: '多股东公司' },
  { val: 'INDIVIDUAL', label: '个体工商户' },
  { val: 'UNKNOWN', label: '暂不确定' },
]

interface QuizQuestion {
  key: string
  topic: string
  title: string
  why: string
  options: { val: string; label: string; hint?: string }[]
}

const quizQuestions: QuizQuestion[] = [
  {
    key: 'q1',
    topic: '公私资金隔离',
    title: '公司公户往老板或个人卡转钱，平时通常怎么走账？',
    why: '公转私长期挂“借款”跨年未还，税局可按 20% 税率视同分红追缴个税。',
    options: [
      { val: 'A', label: '依法代扣个税走工资/分红，或极少公转私' },
      { val: 'B', label: '先备注“借款/备用金”转出，年底前会平掉账' },
      { val: 'C', label: '常备注“借款/往来款”转个人卡，跨年也没归还' },
    ],
  },
  {
    key: 'q2',
    topic: '个人垫资报销',
    title: '用自己微信/支付宝垫付公司花销后，账上怎么处理？',
    why: '私卡垫付不留痕，不仅利润虚高多交税，以后公户有钱也难合规转回个人。',
    options: [
      { val: 'A', label: '全公对公付款，或垫付后按单据及时报销入账' },
      { val: 'B', label: '平时直接付，年底再集中找票/凑数冲一笔' },
      { val: 'C', label: '经常私卡垫付，账上根本没记或长期挂在往来里' },
    ],
  },
  {
    key: 'q3',
    topic: '无票与成本票',
    title: '遇到没有发票的支出或个人外包，一般怎么入账？',
    why: '找加油/餐饮等无关票顶账是金税四期高危红线，直接漏记又会多交企业所得税。',
    options: [
      { val: 'A', label: '合规代开或按小额零星/代扣代缴规范处理' },
      { val: 'B', label: '挂在往来账里一直不平，或者干脆不算成本' },
      { val: 'C', label: '找其他加油票、餐饮票等无关发票顶替冲成本' },
    ],
  },
  {
    key: 'q4',
    topic: '账表与利润透明度',
    title: '现在能随时看清自己公司的账本和真实利润吗？',
    why: '长期做成“零利润/微亏”不仅触发税务预警，银行经营贷初筛也会直接拒贷。',
    options: [
      { val: 'A', label: '随时能看到真实盈亏，也能导出标准财务三表' },
      { val: 'B', label: '只收到过报税截图，从没见过自己的账本和利润表' },
      { val: 'C', label: '报表长期做成零利润或微亏，或者拿不出完整三表' },
    ],
  },
  {
    key: 'q5',
    topic: '开票与往来对账',
    title: '开出去的发票、客户回款和银行流水，平时对得上吗？',
    why: '银票不对账，容易出现没收到钱先交税、漏开发票滞纳金或坏账忘催。',
    options: [
      { val: 'A', label: '开票、收款与银行流水月月对平，往来清楚' },
      { val: 'B', label: '平时只看银行卡余额，开票回款全靠脑子记' },
      { val: 'C', label: '账面应收应付乱成一团，陈年往来一直对不上' },
    ],
  },
  {
    key: 'q6',
    topic: '研发与减税红利',
    title: '公司有软件开发、技术外包或算力等研发投入吗？',
    why: '有研发投入却没单独建辅助账，会白白错失 100% 研发费用加计扣除节税。',
    options: [
      { val: 'A', label: '有研发投入，已按规范建立研发辅助账享受优惠' },
      { val: 'B', label: '有技术/研发支出，但全混在管理费用里没建辅助账' },
      { val: 'C', label: '目前没有研发投入，暂不涉及此项' },
    ],
  },
]

const answers = ref<Record<string, string>>({})
const currentStep = ref(0)
const activeQuestion = computed(() => quizQuestions[currentStep.value]!)
const answeredCount = computed(() => quizQuestions.filter(q => answers.value[q.key]).length)

function selectQuizOption(key: string, val: string) {
  answers.value = { ...answers.value, [key]: val }
  if (currentStep.value < quizQuestions.length - 1) {
    currentStep.value += 1
  }
}

// ── 余额表模式状态 ──
const uploadedFileName = ref('')
const parsedRows = ref<Record<string, unknown>[]>([])
const preview = ref<BalanceFilePreview | null>(null)
const selectedBalanceType = ref<'ending' | 'opening'>('ending')
const balanceAnswers = ref<Record<string, string>>({})

const followupHints = computed(() => detectBalanceFollowups(parsedRows.value))
const isBalanced = computed(() => {
  if (!preview.value) return true
  return Math.abs(Number(preview.value.debitTotal) - Number(preview.value.creditTotal)) < 0.01
})
const balanceDiffText = computed(() => {
  if (!preview.value) return '0.00'
  const diff = Math.abs(Number(preview.value.debitTotal) - Number(preview.value.creditTotal))
  return diff.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
})

const disclaimer = '本报告基于您提供的数据或回答按现行公开财税规则初筛，仅供经营自查与风险梳理参考，不构成法定审计、税务鉴证或贷款授信结论。'

function fmtYuan(val: number): string {
  return `¥${val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

async function handleFileUpload(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  uploadedFileName.value = ''
  parsedRows.value = []
  preview.value = null
  balanceAnswers.value = {}
  loading.value = true
  try {
    const result = await parseBalanceFileWithPreview(file)
    parsedRows.value = result.rows
    preview.value = result.preview
    uploadedFileName.value = file.name
    selectedBalanceType.value = result.preview.balanceType === 'opening' ? 'opening' : 'ending'
    message.success(`已解析 ${result.rows.length} 条科目明细，可立即开始体检`)
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : '文件解析失败，请检查是否为标准科目余额表')
  } finally {
    loading.value = false
    target.value = ''
  }
}

function canSubmit(): boolean {
  if (currentMode.value === 'quiz') {
    const meaningful = quizQuestions.filter(q => answers.value[q.key] && answers.value[q.key] !== 'UNKNOWN').length
    return meaningful >= 3
  }
  return parsedRows.value.length > 0
}

async function startScan() {
  if (currentMode.value === 'quiz' && !canSubmit()) {
    message.warning('请至少完成 3 道题目，以便为您生成准确的健康评分')
    return
  }
  if (currentMode.value === 'balance' && !parsedRows.value.length) {
    message.warning('请先上传科目余额表（支持 .xlsx / .xls / .csv）')
    return
  }

  loading.value = true
  reportError.value = ''
  try {
    const payload = currentMode.value === 'quiz'
      ? {
          mode: 'quiz' as const,
          answers: answers.value,
          profile: { companyType: companyType.value },
          useAi: useAi.value,
        }
      : {
          mode: 'balance' as const,
          rows: parsedRows.value,
          answers: balanceAnswers.value,
          profile: { companyType: companyType.value },
          balanceMeta: {
            balanceType: preview.value?.balanceType === 'opening' ? 'opening' : selectedBalanceType.value,
            currencyUnit: preview.value?.unit || 'yuan',
            confirmed: true,
            complete: true,
          },
          useAi: useAi.value,
        }
    report.value = await submitHealthScan(payload)
    reportSerial.value = `SCAN-${Date.now().toString().slice(-6)}`
    message.success('体检报告已生成')
  } catch (err: unknown) {
    const msg = (err && typeof err === 'object' && 'response' in err)
      ? String((err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail || '')
      : ''
    reportError.value = msg || (err instanceof Error ? err.message : '体检分析失败，请稍后重试')
    message.error(reportError.value)
  } finally {
    loading.value = false
  }
}

function severityLabel(sev: string): string {
  return ({ CRITICAL: '高危红线', HIGH: '重点隐患', MEDIUM: '需关注', LOW: '轻度提醒' } as Record<string, string>)[sev] || '提醒'
}

function checkStatusLabel(status: string): string {
  return ({ PASS: '已检查', RISK: '有隐患', UNKNOWN: '待补充', NA: '不涉及', ERROR: '异常' } as Record<string, string>)[status] || status
}

const analysisSourceLabel = computed(() => {
  const src = report.value?.meta?.analysisSource
  if (src === 'llm') return '规则引擎 + AI 财务官润色'
  if (src === 'deterministic-fallback') return '确定性规则引擎（AI 降级）'
  return '确定性规则引擎'
})

function reportSummary(): string {
  if (!report.value) return ''
  const scoreText = report.value.totalScore == null ? '暂未评分' : `${report.value.totalScore} 分（${report.value.levelLabel}）`
  const flagLines = (report.value.flags || []).map((f, i) => `${i + 1}. [${severityLabel(f.severity)}] ${f.painTitle || f.name}：${f.desc} 建议：${f.fix}`)
  return [
    `梭罗记账 · 3分钟财税健康体检（${reportSerial.value}）`,
    `综合评分：${scoreText}`,
    `总诊断：${report.value.oneSentenceVerdict}`,
    `税局视角：${report.value.taxBureauView?.details || ''}`,
    `银行视角：${report.value.bankFunderView?.details || ''}`,
    ...(flagLines.length ? ['重点隐患清单：', ...flagLines] : ['未检出高频红线隐患']),
    `来源：${analysisSourceLabel.value}`,
    disclaimer,
  ].join('\n')
}

async function copyReportSummary() {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(reportSummary())
    message.success('体检摘要已复制')
  } catch {
    message.error('复制失败，请手动复制报告内容')
  }
}

async function saveReportImage() {
  if (!workpaperEl.value || savingImage.value) return
  savingImage.value = true
  try {
    await saveWorkpaperImage(workpaperEl.value, reportSerial.value || 'solobooks-scan')
    message.success('体检长图已保存')
  } catch {
    message.error('图片生成失败，请重试')
  } finally {
    savingImage.value = false
  }
}

function switchToBalanceCheck() {
  report.value = null
  currentMode.value = 'balance'
}

function restartScan() {
  report.value = null
  reportError.value = ''
}

function goAction(route?: string) {
  router.push(route || '/login')
}
</script>

<template>
  <div class="audit-scan-page">
    <header class="scan-header">
      <div class="header-inner">
        <button class="brand-wrap" @click="router.push('/')">
          <img src="/solo-logo.png" alt="SoloBooks" class="brand-logo" />
          <span>梭罗记账 · 3分钟财务健康体检</span>
        </button>
        <div class="header-actions">
          <NButton quaternary size="small" @click="router.push('/')">返回官网</NButton>
          <NButton type="primary" size="small" @click="router.push('/login')">免费建账</NButton>
        </div>
      </div>
    </header>

    <main class="scan-main">
      <!-- 顶部 Hero -->
      <section class="scan-hero">
        <p class="eyebrow">免登录 · 免存即焚 · 3 分钟自查</p>
        <h1 v-if="!report">别等税局预警、银行拒贷，才发现账本早就埋了雷</h1>
        <h1 v-else>你的企业财税健康体检简报</h1>
        <p v-if="!report">不需要懂会计术语。做 6 道经营常识题（90 秒），或传一张科目余额表（2 分钟），立刻揪出公私混同、20% 借款个税、找票顶账等隐形雷区。</p>

        <div v-if="!report" class="mode-tabs">
          <button
            class="mode-tab-btn"
            :class="{ active: currentMode === 'quiz' }"
            @click="currentMode = 'quiz'"
          >
            <i class="ri-flashlight-line" />
            90秒经营快检（无账本也能测）
          </button>
          <button
            class="mode-tab-btn"
            :class="{ active: currentMode === 'balance' }"
            @click="currentMode = 'balance'"
          >
            <i class="ri-file-excel-2-line" />
            2分钟传表深扫（上传科目余额表）
          </button>
        </div>
      </section>

      <!-- 输入区 -->
      <section v-if="!report" class="scan-card">
        <!-- 1. 极简主体选择（1次点击，不强制） -->
        <div class="company-strip">
          <span class="strip-label">企业形态（选填）：</span>
          <div class="pill-group">
            <button
              v-for="item in companyTypes"
              :key="item.val"
              class="pill-btn"
              :class="{ active: companyType === item.val }"
              @click="companyType = item.val"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <!-- 2A. 90秒经营快检（分步单卡） -->
        <div v-if="currentMode === 'quiz'" class="quiz-wizard">
          <div class="wizard-progress">
            <div class="progress-text">
              <span>第 {{ currentStep + 1 }} / {{ quizQuestions.length }} 题 · {{ activeQuestion.topic }}</span>
              <span>已完成 {{ answeredCount }} / {{ quizQuestions.length }}</span>
            </div>
            <div class="progress-track">
              <div
                class="progress-fill"
                :style="{ width: `${((currentStep + 1) / quizQuestions.length) * 100}%` }"
              />
            </div>
            <div class="step-dots">
              <button
                v-for="(q, idx) in quizQuestions"
                :key="q.key"
                class="step-dot"
                :class="{ current: idx === currentStep, done: !!answers[q.key] }"
                :aria-label="`第 ${idx + 1} 题`"
                @click="currentStep = idx"
              >
                {{ idx + 1 }}
              </button>
            </div>
          </div>

          <div class="question-stage">
            <h2>{{ activeQuestion.title }}</h2>
            <p class="question-why"><i class="ri-information-line" /> {{ activeQuestion.why }}</p>

            <div class="option-cards">
              <button
                v-for="opt in activeQuestion.options"
                :key="opt.val"
                class="option-card"
                :class="{ selected: answers[activeQuestion.key] === opt.val }"
                @click="selectQuizOption(activeQuestion.key, opt.val)"
              >
                <span class="opt-badge">{{ opt.val }}</span>
                <span class="opt-text">{{ opt.label }}</span>
                <i v-if="answers[activeQuestion.key] === opt.val" class="ri-checkbox-circle-fill opt-check" />
              </button>
            </div>

            <div class="wizard-nav">
              <button
                class="nav-link"
                :disabled="currentStep === 0"
                @click="currentStep = Math.max(0, currentStep - 1)"
              >
                ← 上一题
              </button>
              <button
                class="nav-skip"
                @click="selectQuizOption(activeQuestion.key, 'UNKNOWN')"
              >
                不清楚，跳过这题
              </button>
              <button
                v-if="currentStep < quizQuestions.length - 1"
                class="nav-link"
                @click="currentStep += 1"
              >
                下一题 →
              </button>
            </div>
          </div>
        </div>

        <!-- 2B. 2分钟传表深扫 -->
        <div v-else class="balance-wizard">
          <label class="upload-dropzone">
            <input type="file" accept=".xlsx,.xls,.csv" @change="handleFileUpload" />
            <i class="ri-upload-cloud-2-line upload-icon" />
            <strong>{{ uploadedFileName || '点击选择科目余额表（.xlsx / .xls / .csv）' }}</strong>
            <span>浏览器本地解析并自动脱敏客户与人名，不上传原始文件，不落库留存</span>
          </label>

          <div v-if="preview" class="balance-summary-card">
            <div class="summary-stats">
              <div class="stat-item">
                <span>已识别科目</span>
                <strong>{{ parsedRows.length }} 项</strong>
              </div>
              <div class="stat-item">
                <span>试算平衡</span>
                <strong :class="isBalanced ? 'text-income' : 'text-expense'">
                  {{ isBalanced ? '借贷平衡' : `差额 ¥${balanceDiffText}` }}
                </strong>
              </div>
              <div class="stat-item">
                <span>表内异常线索</span>
                <strong :class="(followupHints.hasLoanClue || followupHints.hasAdvanceClue) ? 'text-warning' : 'text-income'">
                  {{ followupHints.hasLoanClue ? `其他应收 ${fmtYuan(followupHints.loanAmount)}` : followupHints.hasAdvanceClue ? `预收款 ${fmtYuan(followupHints.advanceAmount)}` : '初筛未见大额挂账' }}
                </strong>
              </div>
            </div>

            <div v-if="preview.balanceType === 'unknown'" class="inline-balance-type">
              <span>未自动识别期别，这张表是：</span>
              <button
                class="pill-btn"
                :class="{ active: selectedBalanceType === 'ending' }"
                @click="selectedBalanceType = 'ending'"
              >
                期末余额（推荐）
              </button>
              <button
                class="pill-btn"
                :class="{ active: selectedBalanceType === 'opening' }"
                @click="selectedBalanceType = 'opening'"
              >
                期初余额
              </button>
            </div>
            <p v-else-if="preview.balanceType === 'opening'" class="opening-tip">
              <i class="ri-error-warning-line" /> 检测到当前为期初余额表，报告将基于期初数据进行线索提示。
            </p>

            <!-- 仅按表内真实线索弹出 0~2 个追问 -->
            <div v-if="followupHints.hasLoanClue" class="smart-followup">
              <p class="followup-title">补充确认 1：表内「其他应收款 / 备用金」有 {{ fmtYuan(followupHints.loanAmount) }}，主要是？</p>
              <div class="pill-group">
                <button
                  v-for="opt in [
                    { val: 'SHAREHOLDER', label: '股东/老板个人借用' },
                    { val: 'EMPLOYEE', label: '员工业务备用金' },
                    { val: 'UNKNOWN', label: '不清楚' },
                  ]"
                  :key="opt.val"
                  class="pill-btn"
                  :class="{ active: balanceAnswers.loanCounterparty === opt.val }"
                  @click="balanceAnswers = { ...balanceAnswers, loanCounterparty: opt.val }"
                >
                  {{ opt.label }}
                </button>
              </div>
            </div>

            <div v-if="followupHints.hasAdvanceClue" class="smart-followup">
              <p class="followup-title">补充确认：表内「预收账款 / 合同负债」有 {{ fmtYuan(followupHints.advanceAmount) }}，对应服务或货物交付了吗？</p>
              <div class="pill-group">
                <button
                  v-for="opt in [
                    { val: 'YES', label: '已交付或部分交付' },
                    { val: 'NO', label: '尚未交付' },
                    { val: 'UNKNOWN', label: '不清楚' },
                  ]"
                  :key="opt.val"
                  class="pill-btn"
                  :class="{ active: balanceAnswers.advancePerformance === opt.val }"
                  @click="balanceAnswers = { ...balanceAnswers, advancePerformance: opt.val }"
                >
                  {{ opt.label }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部提交区 -->
        <div class="submit-footer">
          <label class="ai-toggle">
            <input v-model="useAi" type="checkbox" />
            <span>开启 AI 财务官大白话解读（仅向模型发送脱敏后的风险规则代号）</span>
          </label>
          <p v-if="reportError" class="error-banner" role="alert">{{ reportError }}</p>
          <button
            class="scan-cta-btn"
            :disabled="loading || !canSubmit()"
            @click="startScan"
          >
            {{ loading ? '正在扫描分析中…' : '立即生成我的体检报告' }}
          </button>
        </div>
      </section>

      <!-- 报告区 -->
      <section v-else class="report-section">
        <div class="report-actions">
          <button @click="copyReportSummary"><i class="ri-file-copy-line" /> 复制报告摘要</button>
          <button :disabled="savingImage" @click="saveReportImage">
            <i class="ri-image-line" /> {{ savingImage ? '正在生成长图…' : '保存体检长图' }}
          </button>
          <button v-if="currentMode === 'quiz'" @click="switchToBalanceCheck">
            <i class="ri-file-excel-2-line" /> 再传余额表复核
          </button>
          <button @click="restartScan"><i class="ri-refresh-line" /> 重新体检</button>
        </div>

        <article ref="workpaperEl" class="audit-workpaper">
          <!-- 顶部评分 + 一句话诊断 -->
          <div class="verdict-hero" :class="`level-${report.healthLevel}`">
            <div class="score-ring">
              <span class="score-num">{{ report.totalScore ?? '—' }}</span>
              <span class="score-unit">{{ report.totalScore != null ? '分 / 100' : '信息不足' }}</span>
            </div>
            <div class="verdict-content">
              <div class="verdict-top">
                <span class="level-pill">{{ report.levelLabel }}</span>
                <span class="serial-tag">{{ reportSerial }} · {{ analysisSourceLabel }}</span>
              </div>
              <p class="verdict-headline">{{ report.oneSentenceVerdict }}</p>
              <div class="urgent-strip">
                <strong>优先动作：</strong>{{ report.urgentAction }}
              </div>
              <div class="hero-cta-row">
                <button class="primary-convert-btn" @click="goAction(report.flags?.[0]?.ctaRoute || '/login')">
                  {{ report.flags?.[0]?.ctaLabel || '免费开启干净账本' }} →
                </button>
              </div>
            </div>
          </div>

          <!-- 双视角仪表盘 -->
          <div class="perspectives-grid">
            <div class="perspective-card tax-card">
              <div class="p-head">
                <span class="p-title"><i class="ri-alarm-warning-fill" /> 税局稽查视角</span>
                <span class="p-badge" :class="`risk-${report.taxBureauView?.riskLevel || 'unknown'}`">
                  {{ report.taxBureauView?.riskLevel === 'high' ? '重点预警' : report.taxBureauView?.riskLevel === 'medium' ? '需核查' : report.taxBureauView?.riskLevel === 'low' ? '风险较低' : '待评估' }}
                </span>
              </div>
              <p>{{ report.taxBureauView?.details }}</p>
            </div>
            <div class="perspective-card bank-card">
              <div class="p-head">
                <span class="p-title"><i class="ri-bank-fill" /> 银行 / 资方授信视角</span>
                <span class="p-badge" :class="`credit-${report.bankFunderView?.creditLevel || 'unknown'}`">
                  {{ report.bankFunderView?.creditLevel === 'high' ? '信用良好' : report.bankFunderView?.creditLevel === 'medium' ? '存在扣分' : report.bankFunderView?.creditLevel === 'low' ? '易被拒贷' : '待评估' }}
                </span>
              </div>
              <p>{{ report.bankFunderView?.details }}</p>
            </div>
          </div>

          <!-- 四维健康矩阵 -->
          <div v-if="report.dimensions?.length" class="dimensions-strip">
            <div v-for="dim in report.dimensions" :key="dim.key" class="dim-tile">
              <div class="dim-tile-head">
                <span>{{ dim.name }}</span>
                <strong :class="`dim-${dim.status}`">{{ dim.score != null ? `${dim.score}分` : dim.statusLabel }}</strong>
              </div>
              <p>{{ dim.detail }}</p>
            </div>
          </div>

          <!-- 最痛的雷区清单 -->
          <div class="flags-section">
            <h3>{{ report.flags?.length ? `最先要拆掉的 ${report.flags.length} 颗雷` : '风险排雷结论' }}</h3>
            <div v-if="report.flags?.length" class="flags-list">
              <div v-for="flag in report.flags" :key="flag.code" class="flag-card" :class="`sev-${flag.severity}`">
                <div class="flag-head">
                  <span class="sev-badge">{{ severityLabel(flag.severity) }}</span>
                  <h4>{{ flag.painTitle || flag.name }}</h4>
                  <span v-if="flag.amount" class="flag-amount">涉及金额 {{ flag.amount }}</span>
                </div>
                <p v-if="flag.estimatedExposure" class="exposure-callout">
                  <i class="ri-money-cny-circle-line" /> {{ flag.estimatedExposure }}
                </p>
                <p class="flag-desc">{{ flag.desc }}</p>
                <p class="flag-fix"><strong>化解建议：</strong>{{ flag.fix }}</p>
                <div class="flag-foot">
                  <span v-if="flag.legalBasis" class="legal-basis">依据：{{ flag.legalBasis }}</span>
                  <button class="flag-cta" @click="goAction(flag.ctaRoute)">{{ flag.ctaLabel || '去处理' }} →</button>
                </div>
              </div>
            </div>
            <div v-else class="clean-pass-card">
              <i class="ri-shield-check-fill" />
              <div>
                <strong>未触发高频财税红线</strong>
                <p>当前作答或表内科目未见明显公私混同、找票顶账或试算失衡隐患。建议接入梭罗记账保持自动对账留痕。</p>
              </div>
            </div>
          </div>

          <!-- 可折叠的专业核查明细 -->
          <details class="tech-details">
            <summary>查看专业规则核查明细（覆盖率 {{ report.coverage?.percent ?? 0 }}%）</summary>
            <div class="checks-list">
              <div v-for="check in report.checks" :key="check.code" class="check-row">
                <span class="check-code">{{ check.code }}</span>
                <span class="check-name">{{ check.name }}</span>
                <span class="check-status" :class="`st-${check.status}`">{{ checkStatusLabel(check.status) }}</span>
                <span class="check-obs">{{ check.observed }}</span>
              </div>
            </div>
          </details>

          <p class="disclaimer">{{ disclaimer }}</p>
        </article>
      </section>
    </main>
  </div>
</template>

<style scoped>
.audit-scan-page { min-height: 100vh; background: #f8fafc; color: #0f172a; font-family: system-ui, -apple-system, sans-serif; }
.scan-header { background: #fff; border-bottom: 1px solid #e2e8f0; }
.header-inner { max-width: 960px; margin: auto; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.brand-wrap { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; border: 0; background: transparent; color: inherit; cursor: pointer; }
.brand-logo { width: 28px; height: 28px; object-fit: contain; }
.header-actions { display: flex; align-items: center; gap: 8px; }
.scan-main { max-width: 960px; margin: auto; padding: 32px 24px 72px; }

.scan-hero { margin-bottom: 24px; }
.eyebrow { color: var(--solo-income); font-size: 13px; font-weight: 700; letter-spacing: .04em; margin: 0 0 8px; }
.scan-hero h1 { font-size: clamp(24px, 3.6vw, 34px); line-height: 1.25; margin: 0 0 10px; color: #0f172a; }
.scan-hero p { color: #475569; line-height: 1.65; margin: 0; }

.mode-tabs { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }
.mode-tab-btn { display: inline-flex; align-items: center; gap: 6px; background: #fff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 16px; cursor: pointer; color: #334155; font-size: 14px; font-weight: 600; transition: all .15s; }
.mode-tab-btn.active { background: var(--solo-income-bg); border-color: var(--solo-income); color: var(--solo-income); }

.scan-card, .audit-workpaper { background: #fff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 28px; box-shadow: 0 4px 20px rgba(15, 23, 42, 0.04); }

.company-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 20px; border-bottom: 1px solid #f1f5f9; margin-bottom: 24px; }
.strip-label { font-size: 13px; color: #64748b; font-weight: 600; }
.pill-group { display: flex; flex-wrap: wrap; gap: 8px; }
.pill-btn { border: 1px solid #cbd5e1; background: #fff; color: #334155; border-radius: 999px; padding: 6px 14px; font-size: 13px; cursor: pointer; transition: all .15s; }
.pill-btn.active { background: #0f172a; border-color: #0f172a; color: #fff; font-weight: 600; }

/* 问卷分步向导 */
.wizard-progress { margin-bottom: 20px; }
.progress-text { display: flex; justify-content: space-between; font-size: 13px; color: #475569; font-weight: 600; margin-bottom: 8px; }
.progress-track { height: 6px; background: #e2e8f0; border-radius: 999px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--solo-income); transition: width .2s ease; }
.step-dots { display: flex; gap: 8px; margin-top: 12px; }
.step-dot { width: 28px; height: 28px; border-radius: 999px; border: 1px solid #cbd5e1; background: #fff; color: #64748b; font-size: 12px; font-weight: 600; cursor: pointer; }
.step-dot.done { background: var(--solo-income-bg); border-color: var(--solo-income); color: var(--solo-income); }
.step-dot.current { background: #0f172a; border-color: #0f172a; color: #fff; }

.question-stage h2 { font-size: 20px; margin: 0 0 8px; color: #0f172a; line-height: 1.4; }
.question-why { display: flex; align-items: flex-start; gap: 6px; font-size: 13px; color: #64748b; background: #f8fafc; padding: 10px 12px; border-radius: 8px; margin: 0 0 18px; }
.option-cards { display: grid; gap: 10px; }
.option-card { display: flex; align-items: center; gap: 12px; text-align: left; padding: 14px 16px; border: 1px solid #cbd5e1; border-radius: 10px; background: #fff; cursor: pointer; transition: all .15s; color: #0f172a; }
.option-card:hover { border-color: var(--solo-income); background: #f8fafc; }
.option-card.selected { border-color: var(--solo-income); background: var(--solo-income-bg); }
.opt-badge { width: 26px; height: 26px; border-radius: 6px; background: #f1f5f9; color: #334155; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; flex-shrink: 0; }
.option-card.selected .opt-badge { background: var(--solo-income); color: #fff; }
.opt-text { flex: 1; font-size: 15px; line-height: 1.45; font-weight: 500; }
.opt-check { color: var(--solo-income); font-size: 18px; }

.wizard-nav { display: flex; justify-content: space-between; align-items: center; margin-top: 18px; padding-top: 12px; border-top: 1px solid #f1f5f9; }
.nav-link, .nav-skip { background: transparent; border: 0; color: #475569; font-size: 13px; cursor: pointer; padding: 6px 4px; }
.nav-link:disabled { opacity: .4; cursor: not-allowed; }
.nav-skip { color: #64748b; text-decoration: underline; }

/* 余额表上传区 */
.upload-dropzone { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; border: 2px dashed #cbd5e1; border-radius: 12px; padding: 32px 20px; background: #f8fafc; cursor: pointer; text-align: center; transition: border-color .15s; }
.upload-dropzone:hover { border-color: var(--solo-income); }
.upload-dropzone input { display: none; }
.upload-icon { font-size: 32px; color: var(--solo-income); }
.upload-dropzone strong { font-size: 15px; color: #0f172a; }
.upload-dropzone span { font-size: 13px; color: #64748b; }

.balance-summary-card { margin-top: 18px; padding: 18px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; }
.summary-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.stat-item { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; display: grid; gap: 4px; }
.stat-item span { font-size: 12px; color: #64748b; }
.stat-item strong { font-size: 16px; color: #0f172a; }
.text-income { color: var(--solo-income) !important; }
.text-expense { color: var(--solo-expense) !important; }
.text-warning { color: var(--solo-warning) !important; }

.inline-balance-type { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; font-size: 13px; color: #475569; }
.opening-tip { margin: 12px 0 0; font-size: 13px; color: var(--solo-warning); display: flex; align-items: center; gap: 6px; }
.smart-followup { margin-top: 16px; padding-top: 14px; border-top: 1px solid #e2e8f0; }
.followup-title { font-size: 14px; font-weight: 600; color: #0f172a; margin: 0 0 10px; }

.submit-footer { margin-top: 24px; padding-top: 20px; border-top: 1px solid #f1f5f9; display: flex; flex-direction: column; gap: 14px; }
.ai-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #475569; cursor: pointer; }
.error-banner { color: var(--solo-expense); background: var(--solo-expense-bg); padding: 10px 12px; border-radius: 8px; font-size: 13px; margin: 0; }
.scan-cta-btn { border: 0; background: var(--solo-income); color: #fff; padding: 14px 24px; border-radius: 10px; font-size: 15px; font-weight: 700; cursor: pointer; }
.scan-cta-btn:disabled { opacity: .5; cursor: not-allowed; }

/* 报告视图 */
.report-actions { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.report-actions button { display: inline-flex; align-items: center; gap: 6px; background: #fff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 8px 14px; font-size: 13px; color: #334155; cursor: pointer; }

.verdict-hero { display: flex; gap: 24px; align-items: center; padding: 22px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; }
.verdict-hero.level-HIGH_RISK { background: var(--solo-expense-bg); border-color: var(--solo-expense); }
.verdict-hero.level-MEDIUM_RISK { background: var(--solo-warning-bg); border-color: var(--solo-warning); }
.verdict-hero.level-HEALTHY { background: var(--solo-income-bg); border-color: var(--solo-income); }

.score-ring { width: 104px; height: 104px; border-radius: 999px; background: #fff; border: 4px solid currentColor; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; }
.level-HIGH_RISK .score-ring { color: var(--solo-expense); }
.level-MEDIUM_RISK .score-ring { color: var(--solo-warning); }
.level-HEALTHY .score-ring { color: var(--solo-income); }
.level-UNKNOWN .score-ring { color: #64748b; }
.score-num { font-size: 32px; font-weight: 800; line-height: 1; }
.score-unit { font-size: 11px; color: #64748b; margin-top: 4px; }

.verdict-content { flex: 1; min-width: 0; }
.verdict-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.level-pill { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px; background: #0f172a; color: #fff; }
.serial-tag { font-size: 12px; color: #64748b; }
.verdict-headline { font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 10px; line-height: 1.4; }
.urgent-strip { font-size: 13px; color: #334155; background: rgba(255, 255, 255, 0.78); padding: 8px 12px; border-radius: 8px; margin-bottom: 12px; }
.primary-convert-btn { border: 0; background: #0f172a; color: #fff; padding: 10px 18px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; }

.perspectives-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 20px; }
.perspective-card { border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; background: #fff; }
.p-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.p-title { font-weight: 700; font-size: 14px; display: inline-flex; align-items: center; gap: 6px; }
.tax-card .p-title i { color: var(--solo-expense); }
.bank-card .p-title i { color: var(--solo-primary); }
.p-badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; background: #f1f5f9; color: #475569; }
.p-badge.risk-high, .p-badge.credit-low { background: var(--solo-expense-bg); color: var(--solo-expense); }
.p-badge.risk-medium, .p-badge.credit-medium { background: var(--solo-warning-bg); color: var(--solo-warning); }
.p-badge.risk-low, .p-badge.credit-high { background: var(--solo-income-bg); color: var(--solo-income); }
.perspective-card p { margin: 0; font-size: 13px; line-height: 1.6; color: #334155; }

.dimensions-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.dim-tile { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; background: #f8fafc; }
.dim-tile-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #475569; margin-bottom: 6px; }
.dim-tile-head strong { font-size: 14px; }
.dim-CRITICAL { color: var(--solo-expense); }
.dim-WARNING { color: var(--solo-warning); }
.dim-HEALTHY { color: var(--solo-income); }
.dim-NA, .dim-UNKNOWN { color: #64748b; }
.dim-tile p { margin: 0; font-size: 12px; color: #64748b; line-height: 1.45; }

.flags-section { margin-top: 24px; }
.flags-section h3 { font-size: 17px; margin: 0 0 12px; color: #0f172a; }
.flags-list { display: grid; gap: 12px; }
.flag-card { border: 1px solid #e2e8f0; border-left: 4px solid var(--solo-warning); border-radius: 10px; padding: 16px; background: #fff; }
.flag-card.sev-CRITICAL { border-left-color: var(--solo-expense); }
.flag-card.sev-HIGH { border-left-color: var(--solo-warning); }
.flag-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.sev-badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 6px; background: var(--solo-warning-bg); color: var(--solo-warning); }
.sev-CRITICAL .sev-badge { background: var(--solo-expense-bg); color: var(--solo-expense); }
.flag-head h4 { margin: 0; font-size: 15px; color: #0f172a; flex: 1; }
.flag-amount { font-size: 13px; font-weight: 700; color: #0f172a; }
.exposure-callout { margin: 0 0 8px; font-size: 13px; font-weight: 600; color: var(--solo-expense); background: var(--solo-expense-bg); padding: 6px 10px; border-radius: 6px; display: flex; align-items: center; gap: 6px; }
.flag-desc { margin: 0 0 8px; font-size: 13px; color: #334155; line-height: 1.6; }
.flag-fix { margin: 0 0 10px; font-size: 13px; color: #0f172a; line-height: 1.55; }
.flag-foot { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; padding-top: 8px; border-top: 1px solid #f1f5f9; }
.legal-basis { font-size: 12px; color: #64748b; }
.flag-cta { border: 0; background: transparent; color: var(--solo-primary); font-size: 13px; font-weight: 700; cursor: pointer; padding: 0; }

.clean-pass-card { display: flex; align-items: center; gap: 14px; padding: 18px; border-radius: 12px; background: var(--solo-income-bg); color: var(--solo-income); }
.clean-pass-card i { font-size: 28px; }
.clean-pass-card p { margin: 4px 0 0; font-size: 13px; color: #334155; }

.tech-details { margin-top: 22px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 16px; background: #f8fafc; }
.tech-details summary { font-size: 13px; font-weight: 600; color: #475569; cursor: pointer; }
.checks-list { margin-top: 12px; display: grid; gap: 8px; }
.check-row { display: grid; grid-template-columns: 56px 150px 72px 1fr; gap: 8px; font-size: 12px; padding: 6px 0; border-top: 1px solid #e2e8f0; align-items: baseline; }
.check-code { font-weight: 700; color: #475569; }
.check-name { font-weight: 600; color: #0f172a; }
.check-status { font-weight: 700; }
.st-PASS { color: var(--solo-income); }
.st-RISK { color: var(--solo-expense); }
.st-NA, .st-UNKNOWN { color: #64748b; }
.check-obs { color: #475569; }

.disclaimer { margin: 20px 0 0; padding: 12px 14px; background: #f8fafc; border-radius: 8px; font-size: 12px; color: #64748b; line-height: 1.6; }

@media (max-width: 720px) {
  .scan-main { padding: 20px 14px 56px; }
  .scan-card, .audit-workpaper { padding: 18px; }
  .summary-stats, .perspectives-grid, .dimensions-strip { grid-template-columns: 1fr; }
  .verdict-hero { flex-direction: column; align-items: flex-start; }
  .check-row { grid-template-columns: 52px 1fr 64px; }
  .check-obs { grid-column: 1 / -1; }
}
</style>
