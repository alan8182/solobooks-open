<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { listAgentActions, type AgentActionItem } from '@/api/agent'
import { recordReceipt } from '@/api/record'
import { getPeriodCloseChecks } from '@/api/accounting'
import { firstActionableReadinessItem, getClosingReadiness, canBatchPostDrafts } from '@/utils/closingReadiness'
import AgentActionCard from '@/components/business/AgentActionCard.vue'
import StatsWidget from '@/components/widgets/StatsWidget.vue'
import RecentEntriesWidget from '@/components/widgets/RecentEntriesWidget.vue'
import TodoPreviewWidget from '@/components/widgets/TodoPreviewWidget.vue'

// ponytail: streamlined home into an Agent-centered cockpit: Omni Input + ActionQueue + Financial Pulse + Streamlined Grid
const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const entryStore = useEntryStore()

// ── Agent 待办草稿池 ─────────────────────────────────────────
const pendingActions = ref<AgentActionItem[]>([])
const loadingActions = ref(false)

async function loadPendingActions() {
  loadingActions.value = true
  try {
    pendingActions.value = await listAgentActions('pending')
  } catch {
    pendingActions.value = []
  } finally {
    loadingActions.value = false
  }
}

async function onActionResolved() {
  await Promise.all([
    loadPendingActions(),
    entryStore.fetchEntries(),
  ])
}

// ── 月度结账检查 ─────────────────────────────────────────────
const closingChecks = ref<any | null>(null)
const closingLoading = ref(true)
const closingTargetMonth = computed(() => {
  const date = new Date()
  date.setMonth(date.getMonth() - 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
})
const closingReadiness = computed(() => getClosingReadiness(closingChecks.value))
const closingNextTask = computed(() => firstActionableReadinessItem(closingReadiness.value))
const closingReady = computed(() => Boolean(closingChecks.value?.canClose || canBatchPostDrafts(closingChecks.value)))

// ── 税务征期监控 ─────────────────────────────────────────────
interface TaxDeadlineItem {
  id?: string
  taxKind: string
  taxName: string
  period: string
  dueDate: string
  daysLeft: number
  status: string
}

const taxDeadlines = ref<TaxDeadlineItem[]>([])
const taxLoading = ref(true)

async function loadTaxDeadlines() {
  taxLoading.value = true
  try {
    const res: any = await apiClient.get('/api/tax-filing/deadlines?upcoming_days=30')
    taxDeadlines.value = res.deadlines || []
  } catch {
    taxDeadlines.value = []
  } finally {
    taxLoading.value = false
  }
}

const primaryTaxDeadline = computed(() => {
  if (!taxDeadlines.value.length) return null
  const pending = taxDeadlines.value.filter(d => d.status !== 'filed')
  if (pending.length) {
    return [...pending].sort((a, b) => (a.daysLeft ?? 999) - (b.daysLeft ?? 999))[0]
  }
  return taxDeadlines.value[0]
})

const taxDeadlineCardStatus = computed(() => {
  const d = primaryTaxDeadline.value
  if (!d) return 'is-ready'
  if (d.status === 'filed') return 'is-ready'
  if (d.daysLeft != null && d.daysLeft < 0) return 'is-overdue'
  if (d.daysLeft != null && d.daysLeft <= 3) return 'is-urgent'
  return ''
})

// ── 全能输入与快速交代 ───────────────────────────────────────
const omniText = ref('')
const attaching = ref(false)
const imageInput = ref<HTMLInputElement | null>(null)

const QUICK_PROMPTS = [
  '这个月赚了多少钱？',
  '帮我记一笔：今天买办公用品 120 元微信付',
  '当前有哪些待办事项与税务风险？',
]

function pickImage() {
  if (attaching.value) return
  imageInput.value?.click()
}

async function onImagePicked(event: Event) {
  const picker = event.target as HTMLInputElement
  const file = picker.files?.[0]
  picker.value = ''
  if (!file) return
  attaching.value = true
  try {
    const receipt = (await recordReceipt(file)) as unknown as Record<string, unknown>
    const lines = ['记一笔（来自发票图片识别）：']
    if (receipt.sellerName || receipt.merchant) lines.push(`- 商户/销售方：${receipt.sellerName || receipt.merchant}`)
    if (receipt.amount != null) lines.push(`- 金额：${receipt.amount}`)
    if (receipt.date) lines.push(`- 日期：${receipt.date}`)
    if (receipt.suggestedCategory) lines.push(`- 建议分类：${receipt.suggestedCategory}`)
    if (receipt.invoiceType) lines.push(`- 发票类型：${receipt.invoiceType}`)
    router.push({
      path: '/copilot',
      query: { q: lines.join('\n'), autoSend: '1' },
    })
  } catch (err) {
    message.error(err instanceof Error ? err.message : '发票识别失败，请重试')
  } finally {
    attaching.value = false
  }
}

function submitPrompt(promptText?: string) {
  const q = (promptText ?? omniText.value).trim()
  if (!q) return
  router.push({
    path: '/copilot',
    query: { q, autoSend: '1' },
  })
}

// ── 日期与问候 ───────────────────────────────────────────────
const formattedDate = computed(() => {
  return new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })
})

const greetingText = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了，注意休息'
  if (hour < 11) return '早上好'
  if (hour < 13) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

onMounted(async () => {
  loadPendingActions()
  loadTaxDeadlines()
  try {
    closingChecks.value = await getPeriodCloseChecks(closingTargetMonth.value)
  } finally {
    closingLoading.value = false
  }
})
</script>

<template>
  <div class="home-workbench">
    <!-- 1. 顶栏问候与数字员工就绪态 -->
    <header class="workbench-header">
      <div class="header-main">
        <span class="date-label">{{ formattedDate }}</span>
        <h1 class="greeting-title">
          {{ greetingText }}，{{ authStore.user?.nickname || '主理人' }}
        </h1>
        <p class="greeting-subtitle">
          数字员工已在线。交代一句话记账、拍照上传票据，或查阅当期经营与纳税情况。
        </p>
      </div>

      <div class="agent-badge" @click="router.push('/copilot')">
        <span class="live-dot" />
        <span class="badge-text">AI 会计在线</span>
        <i class="ri-arrow-right-s-line" />
      </div>
    </header>

    <!-- 2. 全能 AI 记账与交互条 (Omni Input Bar) -->
    <div class="omni-bar-card">
      <div class="omni-input-wrapper">
        <input
          ref="imageInput"
          type="file"
          accept="image/png,image/jpeg,image/jpg,image/bmp"
          style="display: none"
          @change="onImagePicked"
        />
        <button
          type="button"
          class="attach-btn"
          :class="{ loading: attaching }"
          :disabled="attaching"
          title="上传发票或收据图片直接交给 AI 识别"
          @click="pickImage"
        >
          <i :class="attaching ? 'ri-loader-4-line spin' : 'ri-attachment-2'" />
          <span class="attach-label">传票据</span>
        </button>

        <div class="input-divider" />

        <input
          v-model="omniText"
          type="text"
          class="omni-text-input"
          placeholder="交代一句话记账（如：打车 48 元微信付；收到咨询服务费 3 万元），或向 AI 会计提问..."
          @keydown.enter="submitPrompt()"
        />

        <button
          type="button"
          class="send-btn"
          :disabled="!omniText.trim()"
          @click="submitPrompt()"
        >
          <i class="ri-sparkling-fill" />
          <span>交给 AI</span>
        </button>
      </div>

      <div class="prompt-chips">
        <span class="chips-hint">试试这样问：</span>
        <button
          v-for="p in QUICK_PROMPTS"
          :key="p"
          type="button"
          class="chip-item"
          @click="submitPrompt(p)"
        >
          {{ p }}
        </button>
      </div>
    </div>

    <!-- 3. AI 会计待办草稿池 (ActionQueue) -->
    <section v-if="pendingActions.length > 0" class="pending-section">
      <div class="section-banner">
        <div class="banner-title-group">
          <div class="spark-icon">
            <i class="ri-sparkling-fill" />
          </div>
          <div>
            <h2 class="banner-title">待确认的记账与核算草稿（{{ pendingActions.length }} 笔）</h2>
            <p class="banner-desc">AI 已完成初步分类与科目推导，点击确认即可一键入账；结果真实可查可撤销。</p>
          </div>
        </div>
        <NButton text type="primary" size="small" @click="router.push('/copilot')">
          在 AI 对话中查看全部
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>

      <div class="pending-cards-grid">
        <AgentActionCard
          v-for="action in pendingActions"
          :key="action.id"
          :action="action"
          @resolved="onActionResolved"
        />
      </div>
    </section>

    <!-- 4. 周期结账与税务提醒条 -->
    <div class="periodic-alerts-grid">
      <!-- 月度结账检查卡 -->
      <div class="closing-wizard-alert" :class="{ 'is-ready': closingReady }">
        <div class="alert-content">
          <div class="alert-icon-wrapper">
            <i :class="closingReady ? 'ri-checkbox-circle-line' : 'ri-calendar-check-line'" />
          </div>
          <div class="alert-text">
            <span class="alert-title">{{ closingTargetMonth }} 月度结账</span>
            <span v-if="closingLoading" class="alert-desc">正在检查本期结账准备情况...</span>
            <span v-else-if="closingNextTask" class="alert-desc">{{ closingNextTask.title }}：{{ closingNextTask.detail }}</span>
            <span v-else-if="closingReady" class="alert-desc">本期票流账核对已通过，可以确认锁账。</span>
            <span v-else class="alert-desc">进入月度结账向导查看本期核对状态。</span>
          </div>
        </div>
        <NButton type="primary" size="small" class="wizard-go-btn" @click="router.push('/closing-wizard')">
          {{ closingNextTask ? closingNextTask.actionLabel : '查看结账' }}
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>

      <!-- 税局征期监控卡 -->
      <div class="tax-filing-alert" :class="taxDeadlineCardStatus">
        <div class="alert-content">
          <div class="alert-icon-wrapper">
            <i :class="taxDeadlineCardStatus === 'is-ready' ? 'ri-checkbox-circle-line' : 'ri-file-shield-2-line'" />
          </div>
          <div class="alert-text">
            <span class="alert-title">
              税局征期监控（{{ primaryTaxDeadline ? primaryTaxDeadline.period : '当期' }}）
            </span>
            <span v-if="taxLoading" class="alert-desc">正在读取税局申报日历...</span>
            <span v-else-if="!primaryTaxDeadline || primaryTaxDeadline.status === 'filed'" class="alert-desc">
              本期主要税种申报已全部办结 ✓
            </span>
            <span v-else-if="primaryTaxDeadline.daysLeft < 0" class="alert-desc overdue-text">
              ⚠️ {{ primaryTaxDeadline.taxName }} 已逾期 {{-primaryTaxDeadline.daysLeft}} 天，请尽快补报！
            </span>
            <span v-else-if="primaryTaxDeadline.daysLeft <= 3" class="alert-desc urgent-text">
              ⏰ {{ primaryTaxDeadline.taxName }} 剩 {{ primaryTaxDeadline.daysLeft }} 天截止（{{ primaryTaxDeadline.dueDate }}）
            </span>
            <span v-else class="alert-desc">
              {{ primaryTaxDeadline.taxName }} 进行中，截止 {{ primaryTaxDeadline.dueDate }}（剩 {{ primaryTaxDeadline.daysLeft }} 天）
            </span>
          </div>
        </div>
        <NButton
          size="small"
          :type="taxDeadlineCardStatus === 'is-overdue' || taxDeadlineCardStatus === 'is-urgent' ? 'warning' : 'primary'"
          secondary
          class="wizard-go-btn"
          @click="router.push(primaryTaxDeadline ? `/tax-filing?period=${primaryTaxDeadline.period}` : '/tax-filing')"
        >
          {{ primaryTaxDeadline && primaryTaxDeadline.status !== 'filed' ? '去申报' : '作业包' }}
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>
    </div>

    <!-- 5. 经营脉搏 (收支与利润指标) -->
    <StatsWidget />

    <!-- 6. 底部双栏（最近流水 + 待办风险） -->
    <div class="dashboard-grid">
      <div class="grid-main-column">
        <RecentEntriesWidget />
      </div>
      <div class="grid-side-column">
        <TodoPreviewWidget />
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-workbench {
  max-width: 1440px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

/* 顶部问候 */
.workbench-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  padding: 4px 0 2px;
}

.date-label {
  display: block;
  font-size: 12px;
  font-weight: 550;
  color: var(--solo-text-muted);
  margin-bottom: 6px;
}

.greeting-title {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.4px;
}

.greeting-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.agent-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.agent-badge:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
  transform: translateY(-1px);
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--solo-income);
  box-shadow: 0 0 0 2px rgba(18, 128, 92, 0.2);
}

.badge-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

/* 全能 AI 输入栏 */
.omni-bar-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-xl);
  padding: 14px 18px;
  box-shadow: var(--solo-shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.omni-bar-card:focus-within {
  border-color: var(--solo-primary);
  box-shadow: 0 0 0 3px var(--solo-primary-glow);
}

.omni-input-wrapper {
  display: flex;
  align-items: center;
  gap: 10px;
}

.attach-btn {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  height: 38px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--solo-text-secondary);
  font-size: 13px;
  font-weight: 550;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.attach-btn:hover:not(:disabled) {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.attach-label {
  font-size: 12px;
}

.spin {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.input-divider {
  width: 1px;
  height: 24px;
  background: var(--solo-border);
}

.omni-text-input {
  flex: 1;
  border: none;
  background: transparent;
  outline: none;
  font-size: 14px;
  color: var(--solo-text-primary);
  padding: 6px 0;
}

.omni-text-input::placeholder {
  color: var(--solo-text-muted);
  font-size: 13px;
}

.send-btn {
  background: var(--solo-primary);
  border: none;
  border-radius: 8px;
  height: 38px;
  padding: 0 16px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.15s;
  flex-shrink: 0;
}

.send-btn:hover:not(:disabled) {
  opacity: 0.92;
  transform: translateY(-1px);
}

.send-btn:disabled {
  background: var(--solo-border);
  color: var(--solo-text-muted);
  cursor: not-allowed;
}

.prompt-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.chips-hint {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.chip-item {
  background: var(--solo-bg-soft);
  border: 1px solid transparent;
  border-radius: 14px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--solo-text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.chip-item:hover {
  background: #fff;
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

/* 待确认动作草稿区 */
.pending-section {
  background: #fff;
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: var(--solo-radius-lg);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: linear-gradient(180deg, #fffbf2 0%, #ffffff 60px);
}

.section-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.banner-title-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.spark-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #fef3c7;
  color: #d97706;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.banner-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.banner-desc {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.pending-cards-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 周期结账与税务提醒条 */
.periodic-alerts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

@media (max-width: 900px) {
  .periodic-alerts-grid {
    grid-template-columns: 1fr;
  }
}

.closing-wizard-alert, .tax-filing-alert {
  min-width: 0;
  min-height: 60px;
  padding: 12px 16px;
  border: 1px solid rgba(22, 93, 255, 0.15);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-blue-soft);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.closing-wizard-alert.is-ready, .tax-filing-alert.is-ready {
  border-color: rgba(18, 128, 92, 0.22);
  background: #f0fdf4;
}

.closing-wizard-alert.is-ready .alert-icon-wrapper,
.tax-filing-alert.is-ready .alert-icon-wrapper {
  color: var(--solo-income);
}

.tax-filing-alert.is-urgent {
  border-color: rgba(245, 158, 11, 0.35);
  background: #fffbeb;
}
.tax-filing-alert.is-urgent .alert-icon-wrapper {
  color: #f59e0b;
}

.tax-filing-alert.is-overdue {
  border-color: rgba(239, 68, 68, 0.35);
  background: #fef2f2;
}
.tax-filing-alert.is-overdue .alert-icon-wrapper {
  color: #ef4444;
}

.overdue-text {
  color: #ef4444 !important;
  font-weight: 600;
}
.urgent-text {
  color: #d97706 !important;
  font-weight: 600;
}

.alert-content {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.alert-icon-wrapper {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #fff;
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.alert-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.alert-title {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 650;
}

.alert-desc {
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.wizard-go-btn {
  flex-shrink: 0;
  border-radius: 8px;
}

/* 底部双栏 */
.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);
  gap: 22px;
  align-items: start;
}

.grid-main-column,
.grid-side-column {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

@media (max-width: 992px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  .workbench-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .omni-input-wrapper {
    flex-wrap: wrap;
  }
}
</style>
