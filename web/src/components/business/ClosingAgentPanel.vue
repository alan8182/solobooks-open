<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { NButton, NCard, NInput, NSpin } from 'naive-ui'
import { chatAgentStream, type AgentActionItem, type AgentStreamEvent } from '@/api/agent'
import AgentActionCard from '@/components/business/AgentActionCard.vue'
import { renderMarkdown } from '@/utils/markdown'
import { copyToClipboard } from '@/utils/clipboard'

const props = defineProps<{ month: string }>()

interface ToolStep {
  name: string
  label: string
  argsSummary: string
  status: 'running' | 'ok' | 'fail'
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  steps?: ToolStep[]
  actions?: AgentActionItem[]
  streaming?: boolean
  failed?: boolean
}

const TOOL_LABELS: Record<string, string> = {
  run_close_checks: '结账五项检查',
  list_unmatched: '查找票流账差异',
  preview_accruals: '查看计提状态',
  preview_asset_actions: '检查资产处理事项',
  propose_asset_closure: '发起资产处理确认',
  propose_match: '发起对账建议',
  propose_accrual: '发起计提确认',
  generate_close_report: '生成结账检查报告',
  get_month_summary: '查询月度汇总',
  list_entries: '查询账目',
  list_bank_statements: '查询银行流水',
  get_entry_detail: '查看条目详情',
}

const messages = ref<ChatMessage[]>([])
const input = ref('')
const streaming = ref(false)
const chatBodyRef = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null
const conversationId = ref(`closing-${props.month}-${Date.now().toString(36)}`)

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

function resetConversation() {
  if (streaming.value) return
  messages.value = []
  conversationId.value = `closing-${props.month}-${Date.now().toString(36)}`
}


const canSend = computed(() => !streaming.value && input.value.trim().length > 0)

const QUICK_ACTIONS = [
  { label: '预检本月账', prompt: '帮我预检一下本月的账，看看能不能结账' },
  { label: '找票流账差异', prompt: '帮我找一下本月票、流水、账目之间的差异' },
  { label: '计提状态', prompt: '看看本月折旧和工资的计提状态' },
  { label: '生成结账报告', prompt: '帮我生成本月的结账检查报告' },
]

function scrollBottom() {
  nextTick(() => {
    if (chatBodyRef.value) chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
  })
}

function argsSummary(args: Record<string, unknown>): string {
  return String(args.month ?? args.query ?? args.kind ?? '')
}

function handleEvent(ev: AgentStreamEvent, target: ChatMessage) {
  switch (ev.type) {
    case 'token':
      target.content += ev.content || ''
      scrollBottom()
      break
    case 'tool_call':
      target.steps = target.steps || []
      target.steps.push({
        name: ev.name || '',
        label: TOOL_LABELS[ev.name || ''] || ev.name || '工具调用',
        argsSummary: argsSummary(ev.arguments || {}),
        status: 'running',
      })
      scrollBottom()
      break
    case 'tool_result': {
      const step = target.steps?.[target.steps.length - 1]
      if (step && step.status === 'running') step.status = ev.ok ? 'ok' : 'fail'
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

async function send(prompt?: string) {
  const text = (prompt ?? input.value).trim()
  if (!text || streaming.value) return
  input.value = ''

  messages.value.push({ role: 'user', content: text })
  const assistant: ChatMessage = { role: 'assistant', content: '', steps: [], streaming: true }
  messages.value.push(assistant)
  streaming.value = true
  scrollBottom()

  // ponytail: send full multi-turn conversation history, with month context anchored on the first user message
  const history: { role: 'user' | 'assistant'; content: string }[] = messages.value
    .filter((m) => m.content && !m.streaming)
    .map((m, idx) => {
      let content = m.content
      if (idx === 0 && m.role === 'user' && !content.includes(props.month)) {
        content = `当前结账月份是 ${props.month}。\n${content}`
      }
      return { role: m.role, content }
    })

  abortController = new AbortController()
  try {
    await chatAgentStream(
      history,
      (ev) => handleEvent(ev, assistant),
      abortController.signal,
      conversationId.value,
      false,
      'accounting',
    )
  } catch (err) {
    if ((err as Error)?.name !== 'AbortError') {
      assistant.failed = true
      assistant.content += (assistant.content ? '\n\n' : '') + '⚠ 连接中断，请稍后重试。'
    }
  } finally {
    assistant.streaming = false
    streaming.value = false
    abortController = null
    scrollBottom()
  }
}

function stopStream() {
  abortController?.abort()
}
</script>

<template>
  <NCard :bordered="false" class="agent-panel" size="small">
    <div class="panel-head">
      <div class="panel-head-left">
        <span class="panel-title"><i class="ri-sparkling-2-fill"></i> AI 结账助手</span>
        <span class="panel-sub">预检 / 找差异 / 计提 / 报告——锁账仍由你点击</span>
      </div>
      <NButton
        v-if="messages.length > 0"
        text
        size="tiny"
        class="reset-btn"
        :disabled="streaming"
        @click="resetConversation"
      >
        <template #icon><i class="ri-refresh-line" /></template>
        新对话
      </NButton>
    </div>

    <div class="quick-bar">
      <NButton
        v-for="qa in QUICK_ACTIONS"
        :key="qa.label"
        size="tiny"
        secondary
        :disabled="streaming"
        @click="send(qa.prompt)"
      >
        {{ qa.label }}
      </NButton>
    </div>

    <div ref="chatBodyRef" class="panel-body">
      <div v-if="messages.length === 0" class="panel-empty">
        点击上方快捷按钮或直接输入，比如"帮我结 {{ month }} 的账"。
      </div>
      <div v-for="(msg, i) in messages" :key="i" class="msg" :class="[msg.role, { streaming: msg.streaming }]">
        <div class="msg-avatar" :class="{ 'is-streaming': msg.streaming }">
          <i v-if="msg.role === 'user'" class="ri-user-3-fill" />
          <img v-else src="/solo-logo.png" alt="梭罗" class="avatar-logo" />
        </div>
        <div class="msg-main">
          <div class="tool-steps" v-if="msg.steps && msg.steps.length">
            <div class="tool-step" v-for="(step, j) in msg.steps" :key="j">
              <NSpin v-if="step.status === 'running'" :size="10" />
              <span v-else class="step-icon" :class="step.status">{{ step.status === 'ok' ? '✓' : '✕' }}</span>
              <span>{{ step.label }}</span>
              <span class="step-args" v-if="step.argsSummary">{{ step.argsSummary }}</span>
            </div>
          </div>
          <div class="msg-bubble" v-if="msg.content || msg.streaming || msg.streaming === false">
            <template v-if="msg.role === 'user'">{{ msg.content }}</template>
            <template v-else>
              <!-- 等待首字时的流体打字波 -->
              <div class="typing-wave" v-if="msg.streaming && !msg.content && (!msg.steps || !msg.steps.length)">
                <span></span>
                <span></span>
                <span></span>
              </div>
              <div v-if="msg.content" class="md-body" v-html="renderMarkdown(msg.content)"></div>
              <span class="cursor-caret" v-if="msg.streaming && msg.content" />
            </template>
            <div class="msg-tools" v-if="msg.content && !msg.streaming">
              <button
                type="button"
                class="copy-btn"
                :class="{ copied: copiedMessageIndex === i }"
                :title="copiedMessageIndex === i ? '已复制' : '复制内容'"
                @click="copyContent(msg.content, i)"
              >
                <i :class="copiedMessageIndex === i ? 'ri-check-line' : 'ri-file-copy-line'" />
                <span>{{ copiedMessageIndex === i ? '已复制' : '复制' }}</span>
              </button>
            </div>
          </div>
          <div class="msg-actions" v-if="msg.actions && msg.actions.length">
            <AgentActionCard
              v-for="action in msg.actions"
              :key="action.id"
              :action="action"
            />
          </div>
        </div>
      </div>
    </div>

    <div class="panel-input">
      <NInput
        v-model:value="input"
        type="text"
        size="small"
        :placeholder="`例如：帮我结 ${month} 的账`"
        :disabled="streaming"
        @keydown.enter.prevent="(!($event as KeyboardEvent).shiftKey && send())"
      />
      <NButton v-if="streaming" size="small" type="warning" secondary @click="stopStream">停止</NButton>
      <NButton v-else size="small" type="primary" :disabled="!canSend" @click="send()">发送</NButton>
    </div>
  </NCard>
</template>

<style scoped>
.agent-panel {
  border-radius: var(--solo-radius-lg);
  margin-bottom: 12px;
  border: 1px solid rgba(22, 93, 255, 0.25);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.panel-head-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.reset-btn {
  color: var(--solo-text-muted);
  font-size: 11px;
}
.reset-btn:hover {
  color: var(--solo-ai, #1747b0);
}
.panel-title {
  font-weight: 600;
  color: var(--solo-ai, #1747b0);
}
.panel-title i {
  margin-right: 4px;
}
.panel-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.quick-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.panel-body {
  max-height: 380px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px;
}
.panel-empty {
  font-size: 12px;
  color: var(--solo-text-muted);
  text-align: center;
  padding: 12px 0;
}
.msg {
  display: flex;
  gap: 8px;
}
.msg.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  flex-shrink: 0;
  transition: all 0.3s ease;
  overflow: hidden;
}
.msg.user .msg-avatar {
  background: var(--solo-primary);
  color: #fff;
  border-radius: 8px;
}
.msg.assistant .msg-avatar {
  background: #ffffff;
  border: 1px solid rgba(22, 93, 255, 0.16);
  padding: 2px;
}
.avatar-logo {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.msg.assistant .msg-avatar.is-streaming {
  border-color: rgba(22, 93, 255, 0.45);
  animation: avatar-breathe 2s infinite ease-in-out;
}
@keyframes avatar-breathe {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(22, 93, 255, 0.25);
  }
  50% {
    box-shadow: 0 0 0 3px rgba(22, 93, 255, 0.15), 0 2px 8px rgba(22, 93, 255, 0.2);
  }
}
.msg-main {
  max-width: 88%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.msg-bubble {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .msg-bubble {
  background: var(--solo-primary);
  color: #fff;
}
.msg.assistant .msg-bubble {
  background: var(--solo-bg-page);
  color: var(--solo-text-primary);
}
/* markdown 渲染内容：取消 pre-wrap，按块级排版 */
.msg.assistant .msg-bubble :deep(.md-body) {
  white-space: normal;
}
.msg.assistant .msg-bubble :deep(.md-body > *:first-child) {
  margin-top: 0;
}
.msg.assistant .msg-bubble :deep(.md-body > *:last-child) {
  margin-bottom: 0;
}
.msg.assistant .msg-bubble :deep(.md-body h1),
.msg.assistant .msg-bubble :deep(.md-body h2),
.msg.assistant .msg-bubble :deep(.md-body h3) {
  font-size: 13px;
  font-weight: 600;
  margin: 10px 0 5px;
}
.msg.assistant .msg-bubble :deep(.md-body p) {
  margin: 5px 0;
}
.msg.assistant .msg-bubble :deep(.md-body ul),
.msg.assistant .msg-bubble :deep(.md-body ol) {
  margin: 5px 0;
  padding-left: 18px;
}
.msg.assistant .msg-bubble :deep(.md-body li) {
  margin: 2px 0;
}
.msg.assistant .msg-bubble :deep(.md-body code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  font-size: 12px;
}
.msg.assistant .msg-bubble :deep(.md-body table) {
  border-collapse: collapse;
  margin: 6px 0;
}
.msg.assistant .msg-bubble :deep(.md-body th),
.msg.assistant .msg-bubble :deep(.md-body td) {
  border: 1px solid rgba(0, 0, 0, 0.1);
  padding: 3px 7px;
}
/* 流光打字光标 */
.cursor-caret {
  display: inline-block;
  width: 2px;
  height: 13px;
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
  gap: 4px;
  padding: 3px 4px;
  min-height: 20px;
}
.typing-wave span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background-color: var(--solo-primary, #1747b0);
  opacity: 0.35;
  animation: typing-bounce 1.4s infinite ease-in-out both;
}
.typing-wave span:nth-child(1) { animation-delay: -0.32s; }
.typing-wave span:nth-child(2) { animation-delay: -0.16s; }
@keyframes typing-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-4px); opacity: 1; }
}
.tool-steps {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.tool-step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.step-icon.ok { color: #0f7354; }
.step-icon.fail { color: #bd3d3d; }
.step-args { opacity: 0.8; }
.msg-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.msg-tools {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
  padding-top: 4px;
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
}
.msg.user .msg-tools {
  border-top-color: rgba(255, 255, 255, 0.2);
}
.copy-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.2s;
}
.msg.user .copy-btn {
  color: rgba(255, 255, 255, 0.85);
}
.copy-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--solo-text, #182230);
}
.msg.user .copy-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
}
.copy-btn.copied {
  color: var(--solo-success, #0f7354) !important;
}
.panel-input {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.panel-input .n-input {
  flex: 1;
}
</style>
