<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { accountingErrorText } from '@/utils/errors'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NInput, NSpin, NTooltip, useMessage } from 'naive-ui'
import {
  chatAgentStream,
  getAgentAction,
  listAgentActions,
  type AgentActionItem,
  type AgentStreamEvent,
  type ClosingLink,
} from '@/api/agent'
import { recordReceipt, recordSpeech } from '@/api/record'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { clearCopilotChat, loadCopilotChat, saveCopilotChat } from '@/utils/copilotChat'
import AgentActionCard from '@/components/business/AgentActionCard.vue'
import { renderMarkdown } from '@/utils/markdown'
import { linkifyClosingWizardRoutes, localClosingWizardPath } from '@/utils/assistantLinks'
import { copyToClipboard } from '@/utils/clipboard'
import type { CompanyOnboardingType } from '@/utils/companyOnboarding'
import CopilotEvidenceBoard from '@/components/business/CopilotEvidenceBoard.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const authStore = useAuthStore()
const viewStore = useWorkspaceViewStore()

interface ToolStep {
  name: string
  label: string
  argsSummary: string
  status: 'running' | 'ok' | 'fail'
  summary?: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  steps?: ToolStep[]
  actions?: AgentActionItem[]
  reasoning?: string
  reasoningCollapsed?: boolean
  streaming?: boolean
  failed?: boolean
  imageUrl?: string
  closingLink?: ClosingLink
}

const TOOL_LABELS: Record<string, string> = {
  get_month_summary: '查询月度汇总',
  list_entries: '查询账目',
  list_invoice_entries: '查询发票',
  list_bank_statements: '查询银行流水',
  get_aging: '查询应收应付账龄',
  list_todos: '查询待办事项',
  search_books: '全局搜索',
  get_company_profile: '读取公司画像',
  propose_voucher: '创建记账草稿',
  propose_vouchers: '批量创建记账草稿',
  propose_statement_vouchers: '按银行流水创建待确认记账',
  propose_company_setup: '创建主体建账确认卡',
  preview_asset_actions: '检查资产处理事项',
  propose_asset_closure: '发起资产处理确认',
  run_close_checks: '运行结账五项检查',
  list_unmatched: '查找票流账差异',
  preview_accruals: '查看计提状态',
  propose_match: '发起对账确认',
  propose_accrual: '发起计提确认',
  generate_close_report: '生成结账检查报告',
  get_entry_detail: '查看条目详情',
  get_filing_deadlines: '查询申报期限',
  generate_filing_package: '生成申报作业包',
  search_tax_policy: '检索税务政策',
  list_private_expenses: '查询小私账',
  record_private_expense: '记入小私账',
}

const messages = ref<ChatMessage[]>([])
const input = ref('')
const streaming = ref(false)
const enableThinking = ref(false)
const pendingActions = ref<AgentActionItem[]>([])
const showRightBoard = ref(true)
const lastReceiptImage = ref<{ url: string; name: string; receipt?: Record<string, unknown> } | null>(null)
const chatCardRef = ref<InstanceType<typeof NCard> | null>(null)
// ── 贴底跟随：流式输出时只有用户停在底部附近才自动滚底 ──
// 用户上翻（scrollTop 减小）即暂停跟随，滚回底部附近 / 点「回到底部」/ 发新消息恢复。
// 不用「距底距离」判上翻：内容增长会拉大距离，会把没上翻的用户误判成上翻。
const followBottom = ref(true)
const SCROLL_FOLLOW_THRESHOLD = 80
let lastScrollTop = 0
let lastScrollHeight = 0
let boundScrollEl: HTMLElement | null = null
let abortController: AbortController | null = null
const conversationId = ref(`copilot-${Date.now().toString(36)}`)
// 聊天记录按账套隔离，键名派生与读写统一在 utils/copilotChat.ts

const copiedMessageIndex = ref<number | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | null = null

async function copyContent(content: string, index: number) {
  const ok = await copyToClipboard(content)
  if (ok) {
    copiedMessageIndex.value = index
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copiedMessageIndex.value = null
    }, 2000)
  }
}


const canSend = computed(() => !streaming.value && input.value.trim().length > 0)

// 已在当前对话中展示过的动作不再重复进"待确认"条，避免两处实例状态不同步
const stripActions = computed(() => {
  const inConversation = new Set(
    messages.value.flatMap((m) => m.actions?.map((a) => a.id) ?? []),
  )
  return pendingActions.value.filter((a) => !inConversation.has(a.id))
})

const examplePrompts = computed(() => (
  viewStore.isBoss
    ? [
        '这个月赚了多少？钱主要花在哪？',
        '我现在还欠别人多少钱？别人还欠我多少？',
        '这个月要交多少税？什么时候截止？',
        '公司买台电脑，这笔钱能抵税吗？',
      ]
    : [
        '查一下本月未过账凭证和异常分录',
        '帮我核对银行流水与凭证的差异',
        '生成本月结账检查摘要，列出阻断项',
        '这个月的进项和销项发票是否匹配？',
      ]
))

// ── 图片附件（发票/单据识别 → 交给 agent 起草）─────────────────────
const imageInput = ref<HTMLInputElement | null>(null)

const attaching = ref(false)

function pickImage() {
  if (streaming.value || attaching.value) return
  imageInput.value?.click()
}

function receiptMessage(r: Record<string, unknown>): string {
  const lines = ['记一笔（来自发票图片识别）：']
  if (r.sellerName || r.merchant) lines.push(`- 商户/销售方：${r.sellerName || r.merchant}`)
  if (r.amount != null) lines.push(`- 金额：${r.amount}`)
  if (r.date) lines.push(`- 日期：${r.date}`)
  if (r.suggestedCategory) lines.push(`- 建议分类：${r.suggestedCategory}`)
  if (r.invoiceType) lines.push(`- 发票类型：${r.invoiceType}`)
  return lines.join('\n')
}

async function onImagePicked(event: Event) {
  const picker = event.target as HTMLInputElement
  const file = picker.files?.[0]
  picker.value = ''
  if (!file || streaming.value) return
  attaching.value = true
  const previewUrl = URL.createObjectURL(file)
  lastReceiptImage.value = { url: previewUrl, name: file.name }
  try {
    const receipt = (await recordReceipt(file)) as unknown as Record<string, unknown>
    lastReceiptImage.value.receipt = receipt
    input.value = receiptMessage(receipt)
    await send(previewUrl)
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    attaching.value = false
  }
}

// ── 语音输入（ASR 转写后自动发送）──────────────────────────────────
const recording = ref(false)
const transcribing = ref(false)
let mediaRecorder: MediaRecorder | null = null
let audioChunks: Blob[] = []

async function toggleRecording() {
  if (recording.value) {
    mediaRecorder?.stop()
    return
  }
  if (streaming.value || transcribing.value) return
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    audioChunks = []
    mediaRecorder = new MediaRecorder(stream)
    mediaRecorder.ondataavailable = (ev) => {
      if (ev.data.size > 0) audioChunks.push(ev.data)
    }
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      recording.value = false
      const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
      if (blob.size < 2000) return // 过短视为误触
      transcribing.value = true
      try {
        const { text } = await recordSpeech(blob)
        const cleaned = text.trim()
        if (!cleaned) {
          message.warning('没有听清，请再说一次')
          return
        }
        input.value = cleaned
        await send()
      } catch (err) {
        message.error(accountingErrorText(err))
      } finally {
        transcribing.value = false
      }
    }
    mediaRecorder.start()
    recording.value = true
  } catch {
    message.error('无法使用麦克风，请检查浏览器权限')
  }
}

onBeforeUnmount(() => {
  if (recording.value) mediaRecorder?.stop()
  boundScrollEl?.removeEventListener('scroll', onChatScroll)
  boundScrollEl = null
})

function chatScrollEl(): HTMLElement | undefined {
  return (chatCardRef.value?.$el as HTMLElement | undefined) ?? undefined
}

function distanceFromBottom(el: HTMLElement): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight
}

function markScrolled(el: HTMLElement) {
  lastScrollTop = el.scrollTop
  lastScrollHeight = el.scrollHeight
}

function onChatScroll() {
  const el = chatScrollEl()
  if (!el) return
  // scrollTop 减小 = 用户主动上翻（程序化钉底是增大；内容收缩的钳位伴随 scrollHeight 同减，排除）
  const shrunk = el.scrollHeight < lastScrollHeight - 1
  if (!shrunk && el.scrollTop < lastScrollTop - 1) {
    followBottom.value = false
  } else if (distanceFromBottom(el) <= SCROLL_FOLLOW_THRESHOLD) {
    followBottom.value = true
  }
  markScrolled(el)
}

// 流式事件驱动：用户已上翻阅读时不打扰
function scrollBottom() {
  if (!followBottom.value) return
  nextTick(() => {
    if (!followBottom.value) return
    const el = chatScrollEl()
    if (el) {
      el.scrollTop = el.scrollHeight
      markScrolled(el)
    }
  })
}

// 用户主动行为（发送消息、点「回到底部」、载入历史会话）：无条件回底并恢复跟随
function forceScrollBottom() {
  followBottom.value = true
  nextTick(() => {
    const el = chatScrollEl()
    if (el) {
      el.scrollTop = el.scrollHeight
      markScrolled(el)
    }
  })
}

async function refreshPending() {
  try {
    pendingActions.value = await listAgentActions('pending')
  } catch {
    /* 静默：待办加载失败不阻塞对话 */
  }
}

function argsSummary(args: Record<string, unknown>): string {
  const keys = ['month', 'keyword', 'query', 'status', 'kind']
  const parts: string[] = []
  for (const k of keys) {
    if (args[k] != null && args[k] !== '') parts.push(String(args[k]))
  }
  if (args.title) parts.push(String(args.title))
  return parts.join(' · ')
}

function handleEvent(ev: AgentStreamEvent, target: ChatMessage) {
  switch (ev.type) {
    case 'reasoning':
      target.reasoning = (target.reasoning || '') + (ev.content || '')
      scrollBottom()
      break
    case 'token':
      // 用户上翻阅读时不自动折叠思考框，避免高度骤缩把阅读位置顶跳
      if (target.reasoning && target.reasoningCollapsed === undefined && followBottom.value) {
        target.reasoningCollapsed = true
      }
      target.content += ev.content || ''
      scrollBottom()
      break
    case 'tool_call': {
      target.steps = target.steps || []
      target.steps.push({
        name: ev.name || '',
        label: TOOL_LABELS[ev.name || ''] || ev.name || '工具调用',
        argsSummary: argsSummary(ev.arguments || {}),
        status: 'running',
      })
      scrollBottom()
      break
    }
    case 'tool_result': {
      const step = target.steps?.[target.steps.length - 1]
      if (step && step.status === 'running') {
        step.status = ev.ok ? 'ok' : 'fail'
        step.summary = ev.summary || ''
      }
      scrollBottom()
      break
    }
    case 'closing_link':
      if (ev.closingLink) {
        target.closingLink = ev.closingLink
        scrollBottom()
      }
      break
    case 'action':
      if (ev.action) {
        target.actions = target.actions || []
        target.actions.push(ev.action)
        scrollBottom()
      }
      break
    case 'final':
      if (ev.content && !target.content) target.content = ev.content
      break
    case 'error':
      target.failed = true
      target.content += (target.content ? '\n\n' : '') + `⚠ ${ev.message || 'Agent 处理失败'}`
      break
  }
}

function renderCopilotMarkdown(raw: string): string {
  return renderMarkdown(linkifyClosingWizardRoutes(raw))
}

function openClosingLink(link?: ClosingLink) {
  const path = localClosingWizardPath(link?.url || '')
  if (!path) {
    message.error('结账入口已失效，请重新运行结账检查')
    return
  }
  router.push(path)
}

function handleMarkdownClick(event: MouseEvent) {
  const anchor = (event.target as HTMLElement | null)?.closest('a')
  if (!anchor) return
  const path = localClosingWizardPath(anchor.getAttribute('href') || '')
  if (!path) return
  event.preventDefault()
  router.push(path)
}

async function send(imageUrl?: string) {
  const text = input.value.trim()
  if (!text || streaming.value) return

  messages.value.push({ role: 'user', content: text, imageUrl })
  input.value = ''
  streaming.value = true

  const assistant = reactive<ChatMessage>({ role: 'assistant', content: '', steps: [], streaming: true })
  messages.value.push(assistant)
  forceScrollBottom()

  abortController = new AbortController()
  try {
    await chatAgentStream(
      messages.value
        .filter((m) => m.content)
        .map((m) => ({ role: m.role, content: m.content })),
      (ev) => handleEvent(ev, assistant),
      abortController.signal,
      conversationId.value,
      enableThinking.value,
      viewStore.mode,
    )
  } catch (err) {
    if ((err as Error)?.name !== 'AbortError') {
      assistant.failed = true
      assistant.content +=
        (assistant.content ? '\n\n' : '') + '⚠ 连接中断，请稍后重试。'
    }
  } finally {
    assistant.streaming = false
    streaming.value = false
    abortController = null
    saveChatToStorage()
    await refreshPending()
    // 生成结束时若用户已上翻阅读，这里保持条件跟随，不强行拽回底部
    scrollBottom()
  }
}

function handleQuickAsk(prompt: string) {
  input.value = prompt
  send()
}

/**
 * 建账引导直接开「新增主体」表单。
 * 空聊天里没有公司名/税号/启用日期可提取，走对话只会先被追问一两轮，
 * 比结构化表单更慢；对话建账仍可用自然语言触发。
 */
function startCompanySetup(onboardingType: CompanyOnboardingType) {
  router.push({ path: '/mine/companies', query: { create: onboardingType } })
}

function saveChatToStorage(companyId: string | null = authStore.currentCompanyId) {
  saveCopilotChat(
    companyId,
    messages.value.slice(-30).map((m) => ({
      role: m.role,
      content: m.content,
      imageUrl: m.imageUrl,
      actions: m.actions,
      reasoning: m.reasoning,
      closingLink: m.closingLink,
    })),
  )
}

function loadChatFromStorage() {
  const records = loadCopilotChat(authStore.currentCompanyId)
  if (records.length > 0) {
    messages.value = records
    void refreshActionStates(messages.value)
    // 恢复历史会话直接落在最新一条，并复位为跟随模式
    forceScrollBottom()
  }
}

async function refreshActionStates(source: ChatMessage[]) {
  const actionIds = [
    ...new Set(
      source.flatMap((message) => message.actions?.map((action) => action.id) ?? []),
    ),
  ]
  if (!actionIds.length) return
  const settled = await Promise.allSettled(actionIds.map((id) => getAgentAction(id)))
  const latest = new Map<string, AgentActionItem>()
  settled.forEach((result) => {
    if (result.status === 'fulfilled') latest.set(result.value.id, result.value)
  })
  if (!latest.size) return
  source.forEach((message) => {
    if (!message.actions?.length) return
    message.actions = message.actions.map((action) => latest.get(action.id) || action)
  })
}

function retryLast() {
  if (streaming.value) return
  const lastUserMsg = [...messages.value].reverse().find((m) => m.role === 'user')
  if (lastUserMsg) {
    if (messages.value.length > 0 && messages.value[messages.value.length - 1].role === 'assistant') {
      messages.value.pop()
    }
    input.value = lastUserMsg.content
    send(lastUserMsg.imageUrl)
  }
}

function stopStream() {
  abortController?.abort()
}

async function onPendingResolved() {
  await Promise.all([
    refreshPending(),
    entryStore.fetchEntries(),
    refreshActionStates(messages.value),
  ])
  saveChatToStorage()
}

function clearChat() {
  if (streaming.value) return
  messages.value = []
  lastReceiptImage.value = null
  clearCopilotChat(authStore.currentCompanyId)
  conversationId.value = `copilot-${Date.now().toString(36)}`
  followBottom.value = true
}

let consumingRouteQuery = false

async function consumeRouteQuery() {
  if (consumingRouteQuery) return

  // router.replace 会立刻改变响应式 route.query，先快照本次跳转参数再消费。
  const mode = route.query.mode
  const scene = route.query.scene
  const q = route.query.q
  const autoSend = route.query.autoSend

  if (!mode && !scene && !q && !autoSend) return

  consumingRouteQuery = true
  try {
    if (scene === 'closing') {
      const month = String(route.query.month || '')
      await router.replace({
        path: '/closing-wizard',
        query: {
          ...( /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? { month } : {}),
          step: 3,
        },
      })
      return
    }

    // 消费完后立即清除 URL 参数，避免刷新页面时重复提问或反复弹出附件选择器
    await router.replace({ path: route.path, query: {} })

    if (mode === 'receipt') {
      nextTick(() => {
        pickImage()
      })
      return
    }

    // 旧的 ?scene=new|migration 深链同样直达建账表单：对话里没有主体信息可提取，
    // 多轮追问比一次填表慢；AI 引导仍保留 ?scene=onboarding。
    if (scene === 'new' || scene === 'migration') {
      await router.replace({ path: '/mine/companies', query: { create: scene } })
      return
    }
    if (scene === 'onboarding') {
      handleQuickAsk('你好，我准备开始在梭罗记账，请引导我选择「新公司从零建账」还是「从其他代账转入」，并帮我完成初始化。')
      return
    }

    if (typeof q === 'string' && q.trim()) {
      input.value = q.trim()
      if (autoSend === '1') {
        await send()
      }
    }
  } finally {
    consumingRouteQuery = false
  }
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    showRightBoard.value = window.innerWidth >= 1024
  }
  const el = chatCardRef.value?.$el as HTMLElement | null | undefined
  if (el) {
    boundScrollEl = el
    el.addEventListener('scroll', onChatScroll, { passive: true })
  }
  loadChatFromStorage()
  await Promise.all([refreshPending(), entryStore.fetchEntries()])
  await consumeRouteQuery()
})

watch(
  () => route.query,
  async (newQuery) => {
    if (newQuery.q || newQuery.mode || newQuery.scene || newQuery.autoSend) {
      await consumeRouteQuery()
    }
  },
)

// 本页被 KeepAlive 缓存，账套切换不会重新 mount，必须监听 currentCompanyId
// 重载对应账套的聊天记录；整页刷新时公司 ID 从 null 异步水合也走同一条路径
watch(
  () => authStore.currentCompanyId,
  (next, prev) => {
    if (next === prev) return
    if (streaming.value) {
      // 先把被打断的半截回复留在原账套名下，再切走
      stopStream()
      saveChatToStorage(prev)
    }
    lastReceiptImage.value = null
    conversationId.value = `copilot-${Date.now().toString(36)}`
    loadChatFromStorage()
  },
)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([refreshPending(), entryStore.fetchEntries()]))
</script>

<template>
  <div class="copilot-page">
    <div class="page-header">
      <div class="header-titles">
        <h2>AI CFO · 交互工作台</h2>
        <NTooltip trigger="hover" placement="bottom-start" :style="{ maxWidth: '340px' }">
          <template #trigger>
            <span class="header-badge-tag">
              数字员工 · 安全确认
              <i class="ri-information-line badge-info-icon" />
            </span>
          </template>
          AI 生成内容仅供参考；所有入账以人工确认为准，申报以税务局口径为准。
        </NTooltip>
      </div>
      <div class="header-actions">
        <NButton
          size="tiny"
          quaternary
          class="toggle-board-btn"
          :title="showRightBoard ? '收起右侧工作板' : '展开业务工作板'"
          @click="showRightBoard = !showRightBoard"
        >
          <i :class="showRightBoard ? 'ri-layout-right-line' : 'ri-layout-right-fill'" />
          <span>{{ showRightBoard ? '收起工作板' : '业务工作板' }}</span>
          <span v-if="stripActions.length > 0" class="header-badge">{{ stripActions.length }}</span>
        </NButton>
        <NButton size="tiny" text @click="clearChat" v-if="messages.length > 0 && !streaming">
          <i class="ri-delete-bin-line" /> 清空
        </NButton>
      </div>
    </div>

    <div class="workbench-grid" :class="{ 'board-hidden': !showRightBoard }">
      <!-- 左栏：AI 会话与交互工作流 -->
      <section class="workbench-main">
        <!-- 对话区 -->
        <NCard :bordered="false" class="chat-card" ref="chatCardRef">
          <div class="chat-messages" v-if="messages.length > 0">
            <div v-for="(msg, i) in messages" :key="i" class="message" :class="[msg.role, { streaming: msg.streaming }]">
              <div class="msg-avatar" :class="{ 'is-streaming': msg.streaming }" :title="msg.role === 'user' ? '我' : '梭罗 · AI CFO'">
                <i v-if="msg.role === 'user'" class="ri-user-3-fill" />
                <img v-else src="/solo-logo.png" alt="梭罗" class="avatar-logo" />
              </div>
              <div class="msg-main">
                <!-- 单据图片缩略图 -->
                <div v-if="msg.imageUrl" class="msg-image-thumb">
                  <img :src="msg.imageUrl" alt="单据图片" />
                </div>

                <!-- 工具调用过程 -->
                <div class="tool-steps" v-if="msg.steps && msg.steps.length > 0">
                  <div class="tool-step" v-for="(step, j) in msg.steps" :key="j">
                    <NSpin v-if="step.status === 'running'" :size="10" />
                    <span class="step-icon" :class="step.status" v-else>{{ step.status === 'ok' ? '✓' : '✕' }}</span>
                    <span class="step-label">{{ step.label }}</span>
                    <span class="step-args" v-if="step.argsSummary">{{ step.argsSummary }}</span>
                  </div>
                </div>

                <!-- 深度思考过程 -->
                <div class="reasoning-box" v-if="msg.reasoning">
                  <div
                    class="reasoning-header"
                    @click="msg.reasoningCollapsed = !msg.reasoningCollapsed"
                  >
                    <div class="reasoning-title">
                      <NSpin v-if="msg.streaming && !msg.content" :size="12" />
                      <i v-else class="ri-brain-line" />
                      <span>{{ msg.streaming && !msg.content ? '深度思考中…' : '已完成深度思考' }}</span>
                    </div>
                    <span class="reasoning-toggle">
                      {{ msg.reasoningCollapsed ? '展开' : '收起' }}
                      <i :class="msg.reasoningCollapsed ? 'ri-arrow-down-s-line' : 'ri-arrow-up-s-line'" />
                    </span>
                  </div>
                  <div class="reasoning-body" v-show="!msg.reasoningCollapsed">
                    <pre>{{ msg.reasoning }}</pre>
                  </div>
                </div>

                <div class="msg-bubble-wrap" v-if="msg.content || msg.role === 'user' || msg.streaming || !msg.reasoning">
                  <div class="msg-bubble" :class="{ 'is-error': msg.failed }">
                    <template v-if="msg.role === 'user'">{{ msg.content }}</template>
                    <template v-else>
                      <!-- 等待首字时的流体打字波 -->
                      <div class="typing-wave" v-if="msg.streaming && !msg.content && !msg.reasoning && (!msg.steps || !msg.steps.length)">
                        <span></span>
                        <span></span>
                        <span></span>
                      </div>
                      <div
                        v-if="msg.content"
                        class="md-body"
                        v-html="renderCopilotMarkdown(msg.content)"
                        @click="handleMarkdownClick"
                      ></div>
                      <span class="cursor-caret" v-if="msg.streaming && msg.content" />
                    </template>
                    <div v-if="msg.failed && !streaming" class="retry-hint">
                      <button type="button" class="retry-inline-btn" @click="retryLast">
                        <i class="ri-refresh-line" /> 点击重试
                      </button>
                    </div>
                  </div>
                  <button
                    v-if="msg.content && !msg.streaming"
                    type="button"
                    class="bubble-copy-btn"
                    :class="{ copied: copiedMessageIndex === i }"
                    :title="copiedMessageIndex === i ? '已复制' : '复制内容'"
                    @click="copyContent(msg.content, i)"
                  >
                    <i :class="copiedMessageIndex === i ? 'ri-check-line' : 'ri-file-copy-line'" />
                  </button>
                </div>

                <div
                  v-if="msg.closingLink && !msg.streaming"
                  class="closing-link-card"
                  :class="`is-${msg.closingLink.status}`"
                >
                  <div class="closing-link-copy">
                    <span class="closing-link-icon"><i class="ri-calendar-check-line" /></span>
                    <div>
                      <strong>{{ msg.closingLink.label }}</strong>
                      <p>{{ msg.closingLink.message }}</p>
                    </div>
                  </div>
                  <NButton
                    type="primary"
                    size="small"
                    class="closing-link-button"
                    @click="openClosingLink(msg.closingLink)"
                  >
                    <template #icon><i class="ri-arrow-right-line" /></template>
                    打开结账向导
                  </NButton>
                </div>

                <!-- 待确认动作卡片 -->
                <div class="msg-actions" v-if="msg.actions && msg.actions.length > 0">
                  <AgentActionCard
                    v-for="action in msg.actions"
                    :key="action.id"
                    :action="action"
                    @resolved="onPendingResolved"
                  />
                </div>
              </div>
            </div>
          </div>

          <div v-else class="chat-empty">
            <div class="empty-icon"><i class="ri-chat-3-line"></i></div>
            <p>你好，我是梭罗，你的 AI CFO。刚开始用？可以先选你的建账情况：</p>
            <div class="onboarding-entry-cards">
              <button
                type="button"
                class="onboarding-entry-card is-new"
                @click="startCompanySetup('new')"
              >
                <div class="onboarding-card-head">
                  <i class="ri-seedling-line" />
                  <strong>我是新成立的公司 · 从零记账</strong>
                </div>
                <span>刚拿营业执照、之前没记过账，0 期初快速建账</span>
              </button>
              <button
                type="button"
                class="onboarding-entry-card is-migration"
                @click="startCompanySetup('migration')"
              >
                <div class="onboarding-card-head">
                  <i class="ri-exchange-box-line" />
                  <strong>已记账公司 · 从其他代账/软件转入</strong>
                </div>
                <span>填一次主体信息，到期初建账页导入原代账的科目余额表 Excel</span>
              </button>
            </div>
            <p class="empty-sub-label">或者直接问我公司的财税问题：</p>
            <div class="prompt-list">
              <button
                v-for="prompt in examplePrompts"
                :key="prompt"
                class="prompt-chip"
                @click="input = prompt"
              >
                {{ prompt }}
              </button>
            </div>
            <p class="empty-note">不用记会计术语，照平时的说法问就行。</p>
          </div>

          <!-- 上翻阅读时的回底按钮：sticky 吸在滚动容器底部，恢复跟随用 -->
          <transition name="fade-up">
            <button
              v-if="!followBottom && messages.length > 0"
              type="button"
              class="scroll-bottom-btn"
              title="回到最新消息"
              @click="forceScrollBottom"
            >
              <i class="ri-arrow-down-line" />
              回到底部
            </button>
          </transition>
        </NCard>

        <!-- 输入区 -->
        <div class="input-bar">
          <input
            ref="imageInput"
            type="file"
            accept="image/png,image/jpeg,image/jpg,image/bmp"
            style="display: none"
            @change="onImagePicked"
          />
          <NButton
            size="small"
            quaternary
            title="拍发票/单据给 AI 识别记账"
            :disabled="streaming || attaching"
            :loading="attaching"
            @click="pickImage"
          >
            <i class="ri-attachment-2" />
          </NButton>
          <NButton
            size="small"
            quaternary
            :type="recording ? 'error' : 'default'"
            :title="recording ? '点击结束录音' : '语音提问：点击开始/结束'"
            :disabled="streaming || transcribing"
            :loading="transcribing"
            @click="toggleRecording"
          >
            <i :class="recording ? 'ri-stop-circle-fill' : 'ri-mic-line'" />
            <span v-if="recording" class="recording-text">录音中</span>
          </NButton>
          <NButton
            size="small"
            :type="enableThinking ? 'primary' : 'default'"
            :secondary="enableThinking"
            :quaternary="!enableThinking"
            class="thinking-toggle-btn"
            :class="{ active: enableThinking }"
            title="深度思考：默认关闭保证日常记账秒开；复杂税务筹划建议开启"
            :disabled="streaming"
            @click="enableThinking = !enableThinking"
          >
            <i class="ri-brain-line" />
            <span class="thinking-label">{{ enableThinking ? '思考已开' : '深度思考' }}</span>
          </NButton>
          <NInput
            v-model:value="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            placeholder="例如：这个月赚了多少？这笔收入要交哪些税？"
            :disabled="streaming"
            @keydown.enter.prevent="(!($event as KeyboardEvent).shiftKey && send())"
          />
          <NButton v-if="streaming" type="warning" secondary @click="stopStream">停止</NButton>
          <NButton v-else type="primary" :disabled="!canSend" @click="() => send()">发送</NButton>
        </div>
      </section>

      <!-- 右栏：业务工作板（单据原件核对 + 待确认草稿池 + 财务脉搏） -->
      <aside class="workbench-aside" v-show="showRightBoard">
        <CopilotEvidenceBoard
          :actions="stripActions"
          :active-receipt="lastReceiptImage"
          :monthly-income="entryStore.monthlyIncome"
          :monthly-expense="entryStore.monthlyExpense"
          :net-profit="entryStore.netProfit"
          @resolved="onPendingResolved"
          @quick-ask="handleQuickAsk"
          @close="showRightBoard = false"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.copilot-page {
  max-width: var(--page-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  /* ponytail: 紧贴视口底部，覆盖全局 .content > * 的 56px 底部留白，消除工作台悬空空隙 */
  height: calc(100vh - var(--navbar-height) - var(--page-tabs-height));
  padding: 16px 0 16px !important;
  box-sizing: border-box;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  height: 32px;
  flex-shrink: 0;
}
.header-titles {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header-titles h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  line-height: 1;
}
.header-badge-tag {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  background: var(--solo-bg-soft, #f1f4f8);
  border: 1px solid var(--solo-border, #dde3ea);
  padding: 1px 6px;
  border-radius: 4px;
  line-height: 16px;
  cursor: help;
}
.badge-info-icon {
  margin-left: 2px;
  font-size: 12px;
  vertical-align: -2px;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.toggle-board-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
}
.header-badge {
  background: var(--solo-primary, #1747b0);
  color: #fff;
  font-size: 10.5px;
  padding: 0 5px;
  border-radius: 8px;
  font-weight: 600;
  line-height: 16px;
  height: 16px;
}

/* 工作台栅格 */
.workbench-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(360px, 1fr);
  gap: 16px;
  flex: 1;
  min-height: 0;
}
.workbench-grid.board-hidden {
  grid-template-columns: 1fr;
}
.workbench-main {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.workbench-aside {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* 对话 */
.chat-card {
  border-radius: var(--solo-radius-lg, 12px);
  border: 1px solid var(--solo-border, #dde3ea);
  flex: 1;
  overflow-y: auto;
  margin-bottom: 16px;
  background: var(--solo-bg-card, #ffffff);
}
.msg-image-thumb {
  margin-bottom: 6px;
  border-radius: 4px;
  overflow: hidden;
  max-width: 200px;
  max-height: 140px;
  background: rgba(0, 0, 0, 0.05);
}
.msg-image-thumb img {
  width: 100%;
  height: auto;
  display: block;
}
.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 2px 20px;
}
/* 上翻阅读时吸底的「回到底部」按钮：sticky 在滚动容器（.chat-card）内 */
.scroll-bottom-btn {
  position: sticky;
  bottom: 16px;
  z-index: 6;
  display: flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  margin: 0 auto;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font-size: 12px;
  cursor: pointer;
  box-shadow: var(--solo-shadow-lg);
}
.scroll-bottom-btn:hover {
  color: var(--solo-primary);
  border-color: var(--solo-primary);
}
.fade-up-enter-active,
.fade-up-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
.message {
  display: flex;
  gap: 10px;
}
.message.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  overflow: hidden;
}
.message.user .msg-avatar {
  background: var(--solo-primary, #1747b0);
  color: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 6px rgba(22, 93, 255, 0.25);
}
.message.assistant .msg-avatar {
  background: #ffffff;
  border: 1px solid rgba(22, 93, 255, 0.16);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  padding: 3px;
}
.avatar-logo {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
/* 呼吸流光态：生成中/思考中 */
.message.assistant .msg-avatar.is-streaming {
  border-color: rgba(22, 93, 255, 0.45);
  animation: avatar-breathe 2s infinite ease-in-out;
}
@keyframes avatar-breathe {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(22, 93, 255, 0.25), 0 1px 4px rgba(0, 0, 0, 0.04);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(22, 93, 255, 0.15), 0 2px 10px rgba(22, 93, 255, 0.22);
  }
}
.msg-main {
  max-width: 82%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.msg-bubble {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.message.user .msg-bubble {
  background: var(--solo-primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.message.assistant .msg-bubble {
  background: var(--solo-bg-page);
  color: var(--solo-text-primary);
  border-bottom-left-radius: 4px;
}
/* markdown 渲染内容：取消 pre-wrap，按块级排版 */
.message.assistant .msg-bubble :deep(.md-body) {
  white-space: normal;
}
.message.assistant .msg-bubble :deep(.md-body > *:first-child) {
  margin-top: 0;
}
.message.assistant .msg-bubble :deep(.md-body > *:last-child) {
  margin-bottom: 0;
}
.message.assistant .msg-bubble :deep(.md-body h1),
.message.assistant .msg-bubble :deep(.md-body h2),
.message.assistant .msg-bubble :deep(.md-body h3) {
  font-size: 14px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.message.assistant .msg-bubble :deep(.md-body p) {
  margin: 6px 0;
}
.message.assistant .msg-bubble :deep(.md-body ul),
.message.assistant .msg-bubble :deep(.md-body ol) {
  margin: 6px 0;
  padding-left: 20px;
}
.message.assistant .msg-bubble :deep(.md-body li) {
  margin: 2px 0;
}
.message.assistant .msg-bubble :deep(.md-body a) {
  color: var(--solo-primary);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.message.assistant .msg-bubble :deep(.md-body code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  font-size: 13px;
}
.message.assistant .msg-bubble :deep(.md-body table) {
  border-collapse: collapse;
  margin: 8px 0;
}
.message.assistant .msg-bubble :deep(.md-body th),
.message.assistant .msg-bubble :deep(.md-body td) {
  border: 1px solid rgba(0, 0, 0, 0.1);
  padding: 4px 8px;
}

.closing-link-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid rgba(23, 71, 176, 0.18);
  border-radius: 10px;
  background: var(--solo-bg-page);
}
.closing-link-card.is-action_required {
  border-color: rgba(214, 137, 16, 0.28);
}
.closing-link-copy {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.closing-link-copy strong {
  display: block;
  color: var(--solo-text-primary);
  font-size: 13px;
  line-height: 1.4;
}
.closing-link-copy p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.45;
}
.closing-link-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  border-radius: 8px;
  color: var(--solo-primary);
  background: rgba(23, 71, 176, 0.08);
  font-size: 17px;
}
.closing-link-button {
  flex: 0 0 auto;
}

/* 流光打字光标 */
.cursor-caret {
  display: inline-block;
  width: 2px;
  height: 14px;
  margin-left: 2px;
  vertical-align: -1.5px;
  background: var(--solo-primary, #1747b0);
  border-radius: 1px;
  box-shadow: 0 0 6px rgba(22, 93, 255, 0.5);
  animation: caret-pulse 0.9s infinite ease-in-out;
}
@keyframes caret-pulse {
  0%, 100% { opacity: 0.2; }
  50% { opacity: 1; }
}

/* 气泡内三点流体打字波（Typing Wave） */
.typing-wave {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 6px;
  min-height: 22px;
}
.typing-wave span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--solo-primary, #1747b0);
  opacity: 0.35;
  animation: typing-bounce 1.4s infinite ease-in-out both;
}
.typing-wave span:nth-child(1) {
  animation-delay: -0.32s;
}
.typing-wave span:nth-child(2) {
  animation-delay: -0.16s;
}
@keyframes typing-bounce {
  0%, 80%, 100% {
    transform: translateY(0);
    opacity: 0.35;
  }
  40% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

/* 工具步骤 */
.tool-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tool-step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.step-icon {
  width: 14px;
  text-align: center;
}
.step-icon.ok {
  color: #0f7354;
}
.step-icon.fail {
  color: #bd3d3d;
}
.step-args {
  color: var(--solo-text-muted);
  opacity: 0.8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 深度思考过程 */
.reasoning-box {
  background: rgba(0, 0, 0, 0.03);
  border: 1px dashed rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  padding: 6px 10px;
  margin: 2px 0 6px;
  max-width: 100%;
}
.reasoning-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.reasoning-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}
.reasoning-title i {
  font-size: 14px;
}
.reasoning-toggle {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
}
.reasoning-body {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  max-height: 220px;
  overflow-y: auto;
}
.reasoning-body pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  color: var(--solo-text-secondary, #586474);
  font-family: inherit;
}

.msg-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.msg-bubble-wrap {
  position: relative;
  display: inline-flex;
  max-width: 100%;
}
.message.user .msg-bubble-wrap {
  align-self: flex-end;
}
.message.assistant .msg-bubble-wrap {
  align-self: flex-start;
}
.bubble-copy-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 22px;
  height: 22px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: var(--solo-text-muted, #636e7c);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.15s ease;
}
.message.assistant .bubble-copy-btn {
  right: -26px;
}
.message.user .bubble-copy-btn {
  left: -26px;
}
.msg-bubble-wrap:hover .bubble-copy-btn {
  opacity: 0.8;
}
.bubble-copy-btn:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.05);
  color: var(--solo-text-primary, #182230);
}
.bubble-copy-btn.copied {
  opacity: 1;
  color: var(--solo-income, #0f7354) !important;
}

.msg-bubble.is-error {
  background: #fff5f5 !important;
  color: var(--solo-expense, #bd3d3d) !important;
  border: 1px solid rgba(199, 68, 68, 0.2);
}
.retry-hint {
  margin-top: 6px;
  padding-top: 4px;
  border-top: 1px dashed rgba(199, 68, 68, 0.2);
}
.retry-inline-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--solo-primary, #1747b0);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
}
.retry-inline-btn:hover {
  text-decoration: underline;
}

/* 空态 */
/* naive 的内容层要撑满卡片，空态才能垂直居中；
   否则内容贴着卡片顶端，下面留出一整屏死白 */
.chat-card :deep(.n-card-content) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.chat-empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  color: var(--solo-text-secondary);
}
.empty-icon {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  background: radial-gradient(circle, var(--solo-ai-glow) 0%, transparent 75%);
  box-shadow: inset 0 0 0 1px rgba(22, 93, 255, 0.15);
}
.empty-icon i {
  font-size: 36px;
  color: var(--solo-ai);
}
.prompt-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
  width: 100%;
  max-width: 360px;
}
.prompt-chip {
  border: 1px solid var(--solo-border, #dde3ea);
  background: #fff;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--solo-text-primary);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.2s;
}
.prompt-chip:hover {
  border-color: var(--solo-ai, var(--solo-primary));
  color: var(--solo-ai, var(--solo-primary));
}
.empty-note {
  margin-top: 14px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.empty-sub-label {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.onboarding-entry-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  width: 100%;
  max-width: 520px;
  margin-top: 10px;
}
.onboarding-entry-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--solo-border, #dde3ea);
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: all 0.16s ease;
}
.onboarding-entry-card:hover {
  border-color: var(--solo-ai, var(--solo-primary));
  background: #f5f8ff;
  transform: translateY(-1px);
}
.onboarding-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--solo-text-primary);
}
.onboarding-card-head i {
  font-size: 16px;
  color: var(--solo-ai, var(--solo-primary));
}
.onboarding-entry-card span {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  line-height: 1.45;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  font-size: 13px;
  color: var(--solo-text-muted);
}

/* 输入 */
.input-bar {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.input-bar .n-input {
  flex: 1;
}
.input-bar .n-button i {
  font-size: 16px;
  line-height: 1;
}
.recording-text {
  margin-left: 4px;
  font-size: 12px;
  animation: blink 1.2s ease-in-out infinite;
}
.thinking-toggle-btn {
  border-radius: 12px;
  padding: 0 10px;
  font-size: 12px;
  transition: all 0.2s ease;
}
.thinking-toggle-btn.active {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  border-color: rgba(22, 93, 255, 0.3);
}
.thinking-label {
  margin-left: 4px;
  font-size: 12px;
}

@media (max-width: 1099px) {
  .workbench-grid {
    grid-template-columns: 1fr;
    position: relative;
  }
  .workbench-aside {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    width: 380px;
    max-width: 90vw;
    z-index: 10;
    box-shadow: -4px 0 16px rgba(0, 0, 0, 0.08);
  }
}
@media (max-width: 768px) {
  .copilot-page {
    height: calc(100vh - var(--navbar-height) - var(--page-tabs-height));
    padding: 12px 0 12px !important;
  }
  .page-header h2 {
    font-size: 17px;
  }
  .msg-main {
    max-width: 90%;
  }
}
</style>
