<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NInput, NSpin, useMessage } from 'naive-ui'
import {
  chatAgentStream,
  listAgentActions,
  type AgentActionItem,
  type AgentStreamEvent,
} from '@/api/agent'
import { recordReceipt, recordSpeech } from '@/api/record'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import AgentActionCard from '@/components/business/AgentActionCard.vue'
import { renderMarkdown } from '@/utils/markdown'
import { copyToClipboard } from '@/utils/clipboard'
import CopilotEvidenceBoard from '@/components/business/CopilotEvidenceBoard.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
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
  preview_asset_actions: '检查资产处理事项',
  propose_asset_closure: '发起资产处理确认',
}

const messages = ref<ChatMessage[]>([])
const input = ref('')
const streaming = ref(false)
const enableThinking = ref(false)
const pendingActions = ref<AgentActionItem[]>([])
const showRightBoard = ref(true)
const lastReceiptImage = ref<{ url: string; name: string; receipt?: Record<string, unknown> } | null>(null)
const chatCardRef = ref<InstanceType<typeof NCard> | null>(null)
let abortController: AbortController | null = null
const conversationId = ref(`copilot-${Date.now().toString(36)}`)
const STORAGE_KEY = 'solobooks:copilot_last_chat'

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
        '帮我记几笔：1) 9月25日买电脑椅 499 元对公；2) 9月26日打车 32 元私账；3) 9月28日收到咨询服务费 3000 元',
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
    message.error(err instanceof Error ? err.message : '发票识别失败，请重试')
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
        message.error(err instanceof Error ? err.message : '语音识别失败')
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
})

function scrollBottom() {
  nextTick(() => {
    const el = chatCardRef.value?.$el as HTMLElement | undefined
    if (el) el.scrollTop = el.scrollHeight
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
      if (target.reasoning && target.reasoningCollapsed === undefined) {
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
      break
    }
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

async function send(imageUrl?: string) {
  const text = input.value.trim()
  if (!text || streaming.value) return

  messages.value.push({ role: 'user', content: text, imageUrl })
  input.value = ''
  streaming.value = true

  const assistant = reactive<ChatMessage>({ role: 'assistant', content: '', steps: [], streaming: true })
  messages.value.push(assistant)
  scrollBottom()

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
    scrollBottom()
  }
}

function handleQuickAsk(prompt: string) {
  input.value = prompt
  send()
}

function saveChatToStorage() {
  try {
    const serializable = messages.value.slice(-30).map((m) => ({
      role: m.role,
      content: m.content,
      imageUrl: m.imageUrl,
      actions: m.actions,
      reasoning: m.reasoning,
    }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify(serializable))
  } catch {
    /* 静默 */
  }
}

function loadChatFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length > 0) {
        messages.value = parsed
      }
    }
  } catch {
    /* 静默 */
  }
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
  await Promise.all([refreshPending(), entryStore.fetchEntries()])
}

function clearChat() {
  if (streaming.value) return
  messages.value = []
  lastReceiptImage.value = null
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 静默 */
  }
  conversationId.value = `copilot-${Date.now().toString(36)}`
}

async function consumeRouteQuery() {
  const mode = route.query.mode
  const q = route.query.q
  const autoSend = route.query.autoSend

  if (!mode && !q && !autoSend) return

  // 消费完后立即清除 URL 参数，避免刷新页面时重复提问或反复弹出附件选择器
  await router.replace({ path: route.path, query: {} })

  if (mode === 'receipt') {
    nextTick(() => {
      pickImage()
    })
  }

  if (typeof q === 'string' && q.trim()) {
    input.value = q.trim()
    if (autoSend === '1') {
      send()
    }
  }
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    showRightBoard.value = window.innerWidth >= 1024
  }
  loadChatFromStorage()
  await Promise.all([refreshPending(), entryStore.fetchEntries()])
  await consumeRouteQuery()
})

watch(
  () => route.query,
  async (newQuery) => {
    if (newQuery.q || newQuery.mode || newQuery.autoSend) {
      await consumeRouteQuery()
    }
  },
)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([refreshPending(), entryStore.fetchEntries()]))
</script>

<template>
  <div class="copilot-page">
    <div class="page-header">
      <div class="header-titles">
        <h2>AI 会计 · 交互工作台</h2>
        <span class="header-badge-tag">数字员工 · 安全确认</span>
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
              <div class="msg-avatar" :class="{ 'is-streaming': msg.streaming }" :title="msg.role === 'user' ? '我' : '梭罗 · AI 会计'">
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
                      <div v-if="msg.content" class="md-body" v-html="renderMarkdown(msg.content)"></div>
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
            <div class="empty-icon"><i class="ri-sparkling-2-fill"></i></div>
            <p>你好，我是梭罗，你的 AI 会计。可以试着问我：</p>
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
            <p class="empty-note">查账、记账、找发票、看风险，直接说人话就行。</p>
          </div>
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
            :title="recording ? '点击结束录音' : '语音记账：点击开始/结束'"
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
            placeholder="例如：记一笔 昨天打车 58 元；这个月支出多少？（支持语音/发票图片）"
            :disabled="streaming"
            @keydown.enter.prevent="(!($event as KeyboardEvent).shiftKey && send())"
          />
          <NButton v-if="streaming" type="warning" secondary @click="stopStream">停止</NButton>
          <NButton v-else type="primary" :disabled="!canSend" @click="() => send()">发送</NButton>
        </div>
        <p class="disclaimer">AI 生成内容仅供参考；所有入账以人工确认为准，申报以税务局口径为准。</p>
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
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--navbar-height) - 52px - var(--page-tabs-height));
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
  margin-bottom: 10px;
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
.disclaimer {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--solo-text-muted);
  text-align: center;
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
    height: calc(100vh - var(--navbar-height) - 24px);
  }
  .page-header h2 {
    font-size: 17px;
  }
  .msg-main {
    max-width: 90%;
  }
}
</style>
