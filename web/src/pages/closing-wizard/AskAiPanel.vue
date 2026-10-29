<!--
  Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
  Licensed under the SoloBooks License v2.0.
-->
<script setup lang="ts">
/**
 * 行内问 AI 弹窗：分类模糊时与 AI 结账助手对话，仅分析建议、不代操作。
 * 从 closing-wizard/index.vue 拆出：流式对话/思考态/快捷提问/结论提取全部自包含；
 * 上下文（流水事实、候选科目）由父页算好传入，accept 事件由父页落地入账。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'
import { chatAgentStream, type AgentStreamEvent } from '@/api/agent'
import { renderMarkdown } from '@/utils/markdown'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  stmt: any | null
  /** 流水行的展示标题（父页 cleanStatementTitle 的结果） */
  stmtTitle: string
  /** 首条随问题发给 AI 的上下文（父页 buildAskAiContext 的结果） */
  prompt: string
  /** 候选分类与其对应科目（父页 ambiguityGroupOf + altAccountText 的结果） */
  candidateLines: { category: string; account: string }[]
  /** AI 未给结论时的兜底分类（当前建议），无则空串 */
  fallbackCategory: string
  /** 「按此入账」请求进行中（父页 quickSubmittingId === stmt.id） */
  confirming: boolean
}>()
const emit = defineEmits<{ accept: [category: string] }>()
const show = defineModel<boolean>('show', { default: false })

const message = useMessage()

// 首条为随问题发给 AI 的上下文（模板渲染成卡片，不显示为气泡）
const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const streaming = ref(false)
const toolHint = ref('')
const input = ref('')
const threadRef = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

const QUICK_QUESTIONS = [
  '这笔是溢价增资吗？该怎么判断？',
  '结合公司账目，直接给结论和理由',
  '这几类如果记错，各有什么后果？',
]

const chatMessages = computed(() => messages.value.slice(1))

// streaming 期间只要没有工具提示，就持续显示「思考中」——
// 覆盖 token 间隙与 tool_result 后、下一 token 前的等待盲区（否则界面像卡死）
const thinking = computed(() => streaming.value && !toolHint.value)

// 从 AI 回答末尾提取「【结论】分类名」，供「按此入账」一键落地
const conclusion = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.role !== 'assistant' || !m.content) continue
    const hit = m.content.match(/【结论】\s*([^\s【】]+)/)
    if (hit) return hit[1].trim()
  }
  return ''
})

function scrollBottom() {
  nextTick(() => {
    if (threadRef.value) threadRef.value.scrollTop = threadRef.value.scrollHeight
  })
}

function onEvent(ev: AgentStreamEvent) {
  switch (ev.type) {
    case 'token': {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant') {
        last.content += ev.content || ''
        scrollBottom()
      }
      break
    }
    case 'tool_call':
      toolHint.value = `AI 正在查账：${ev.name || '…'}`
      break
    case 'tool_result':
      toolHint.value = ''
      break
    case 'final':
      toolHint.value = ''
      // 个别响应只发 final 不发 token，占位气泡需要兜底填充
      if (ev.content) {
        const last = messages.value[messages.value.length - 1]
        if (last && last.role === 'assistant' && !last.content) last.content = ev.content
      }
      break
    case 'error':
      messages.value.push({ role: 'assistant', content: `⚠ ${ev.message || 'AI 处理失败'}` })
      break
  }
}

async function runStream() {
  if (!props.prompt) return
  controller?.abort()
  controller = new AbortController()
  streaming.value = true
  toolHint.value = ''
  scrollBottom()
  // 后端要求最后一条是 user；占位的空 assistant 消息只在本地承接流式 token
  const history = messages.value.map((m) => ({ role: m.role, content: m.content }))
  messages.value.push({ role: 'assistant', content: '' })
  try {
    await chatAgentStream(history, onEvent, controller.signal, undefined, false, 'accounting')
  } catch (err) {
    if ((err as Error)?.name !== 'AbortError') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && !last.content) {
        last.content = '⚠ 连接中断，请稍后重试。'
      }
    }
  } finally {
    streaming.value = false
    controller = null
    scrollBottom()
  }
}

watch(show, (visible) => {
  if (!visible) return
  messages.value = [{ role: 'user', content: props.prompt }]
  input.value = ''
  runStream()
})

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || streaming.value) return
  if (!text) input.value = ''
  messages.value.push({ role: 'user', content })
  await runStream()
}

function stop() {
  controller?.abort()
}

function close() {
  controller?.abort()
  show.value = false
}

function accept() {
  const category = conclusion.value || props.fallbackCategory
  if (!category) {
    message.warning('AI 未给出明确分类，请手动选择')
    return
  }
  emit('accept', category)
}
</script>

<template>
  <NModal
    v-model:show="show"
    :mask-closable="!streaming"
    style="width: 560px; max-width: 96vw;"
    :auto-focus="false"
    @after-leave="close"
  >
    <div class="ask-ai-dialog">
      <div class="ask-ai-header">
        <div class="ask-ai-header-main">
          <div class="ask-ai-title"><i class="ri-chat-sm-2-line"></i> 问 AI · 这笔流水该怎么记</div>
          <div v-if="stmt" class="ask-ai-subtitle">
            {{ stmt.transaction_date }} · {{ formatMoney(stmt.amount) }} · {{ stmtTitle }}
          </div>
        </div>
        <button type="button" class="ask-ai-close" :disabled="streaming" @click="show = false">
          <i class="ri-close-line"></i>
        </button>
      </div>

      <div ref="threadRef" class="ask-ai-thread">
        <!-- 上下文卡片：随首条问题一起发给 AI 的流水事实与候选，只读展示 -->
        <div v-if="stmt" class="ask-ai-context">
          <div class="ask-ai-context-row">
            <span class="ask-ai-context-label">流水</span>
            <span class="ask-ai-context-desc" :title="stmt.description">{{ stmt.description }}</span>
          </div>
          <div class="ask-ai-context-row">
            <span class="ask-ai-context-label">金额</span>
            <span class="ask-ai-context-amount" :class="stmt.amount >= 0 ? 'income' : 'expense'">
              {{ stmt.amount >= 0 ? '+' : '' }}{{ formatMoney(stmt.amount) }}
              <em>（{{ stmt.amount >= 0 ? '收款' : '付款' }} · {{ stmt.transaction_date }}）</em>
            </span>
          </div>
          <div class="ask-ai-context-row">
            <span class="ask-ai-context-label">候选</span>
            <span class="ask-ai-context-cands">
              <span v-for="line in candidateLines" :key="line.category" class="ask-ai-context-cand">
                {{ line.category }}<i class="ri-arrow-right-line"></i>{{ line.account }}
              </span>
            </span>
          </div>
        </div>

        <div
          v-for="(msg, idx) in chatMessages"
          :key="idx"
          class="ask-ai-msg"
          :class="msg.role"
        >
          <div v-if="msg.role === 'user' && msg.content" class="ask-ai-bubble">{{ msg.content }}</div>
          <div
            v-else-if="msg.role === 'assistant' && msg.content"
            class="ask-ai-bubble ask-ai-md"
            v-html="renderMarkdown(msg.content)"
          ></div>
        </div>

        <div v-if="toolHint" class="ask-ai-hint">
          <i class="ri-search-line"></i> {{ toolHint }}<span class="ask-ai-dots"><i></i><i></i><i></i></span>
        </div>
        <div v-else-if="thinking" class="ask-ai-hint">
          <i class="ri-sparkling-2-line"></i> AI 正在思考<span class="ask-ai-dots"><i></i><i></i><i></i></span>
        </div>
      </div>

      <div class="ask-ai-footer">
        <div v-if="!streaming && chatMessages.length <= 1" class="ask-ai-quick">
          <button
            v-for="q in QUICK_QUESTIONS"
            :key="q"
            type="button"
            class="ask-ai-quick-chip"
            @click="send(q)"
          >
            {{ q }}
          </button>
        </div>
        <div class="ask-ai-input-row">
          <NInput
            v-model:value="input"
            type="textarea"
            :rows="2"
            placeholder="补充事实或追问，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="send()"
          />
          <NButton v-if="!streaming" type="primary" :disabled="!input.trim()" @click="send()">
            <template #icon><i class="ri-send-plane-fill" /></template>
            发送
          </NButton>
          <NButton v-else type="warning" secondary @click="stop()">
            <template #icon><i class="ri-stop-fill" /></template>
            停止
          </NButton>
        </div>
        <div v-if="!streaming && chatMessages.length >= 1" class="ask-ai-confirm-row">
          <div class="ask-ai-confirm-conclusion">
            <i class="ri-sparkling-2-fill" style="color: var(--solo-primary); font-size: 15px;"></i>
            <span class="ask-ai-confirm-label">AI 结论</span>
            <span class="ask-ai-confirm-value">{{ conclusion || '未明确，按当前建议' }}</span>
          </div>
          <NButton
            type="primary"
            size="medium"
            :loading="confirming"
            @click="accept"
          >
            <template #icon><i class="ri-check-line" /></template>
            按此入账
          </NButton>
        </div>
        <div v-else class="ask-ai-note">AI 仅分析建议，入账以你在流水行点选的分类为准</div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.ask-ai-dialog {
  /* 裸 NModal 不带卡片样式，必须自带底色，否则页面内容透出 */
  background: var(--solo-bg-card);
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ask-ai-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.ask-ai-header-main {
  min-width: 0;
}

.ask-ai-close {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--solo-text-muted);
  font-size: 16px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.ask-ai-close:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.04);
  color: var(--solo-text-primary);
}

.ask-ai-close:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ask-ai-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.ask-ai-subtitle {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 460px;
}

/* 上下文卡片：随首条问题发给 AI 的流水事实，只读结构化展示 */
.ask-ai-context {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
  background: var(--solo-bg-soft, rgba(0, 0, 0, 0.015));
}

.ask-ai-context-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  min-width: 0;
}

.ask-ai-context-label {
  flex-shrink: 0;
  color: var(--solo-text-muted);
  font-size: 11px;
  width: 28px;
}

.ask-ai-context-desc {
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ask-ai-context-amount {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ask-ai-context-amount em {
  font-style: normal;
  font-weight: 400;
  color: var(--solo-text-muted);
  font-size: 11px;
}

.ask-ai-context-cands {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.ask-ai-context-cand {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border-radius: 10px;
  border: 1px solid var(--solo-border-light);
  background: #fff;
  color: var(--solo-text-secondary);
  font-size: 11px;
}

.ask-ai-context-cand i {
  font-size: 10px;
  color: var(--solo-text-muted);
}

.ask-ai-thread {
  min-height: 160px;
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px;
}

.ask-ai-msg {
  display: flex;
}

.ask-ai-msg.user {
  justify-content: flex-end;
}

.ask-ai-msg.assistant {
  justify-content: flex-start;
}

.ask-ai-bubble {
  max-width: 88%;
  padding: 7px 11px;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.ask-ai-msg.user .ask-ai-bubble {
  background: rgba(23, 71, 176, 0.08);
  color: var(--solo-text-primary);
}

.ask-ai-msg.assistant .ask-ai-bubble {
  background: rgba(0, 0, 0, 0.03);
  color: var(--solo-text-primary);
}

/* AI 回复是 markdown 块级排版，取消 pre-wrap */
.ask-ai-bubble.ask-ai-md {
  white-space: normal;
}

.ask-ai-md :deep(p) {
  margin: 0 0 6px;
}

.ask-ai-md :deep(p:last-child) {
  margin-bottom: 0;
}

.ask-ai-md :deep(ul),
.ask-ai-md :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.ask-ai-md :deep(li) {
  margin: 2px 0;
}

.ask-ai-md :deep(h1),
.ask-ai-md :deep(h2),
.ask-ai-md :deep(h3),
.ask-ai-md :deep(h4) {
  font-size: 13px;
  margin: 8px 0 4px;
}

.ask-ai-md :deep(table) {
  border-collapse: collapse;
  font-size: 11.5px;
  margin: 6px 0;
}

.ask-ai-md :deep(th),
.ask-ai-md :deep(td) {
  border: 1px solid var(--solo-border-light);
  padding: 3px 8px;
}

.ask-ai-md :deep(code) {
  background: rgba(0, 0, 0, 0.05);
  border-radius: 3px;
  padding: 0 4px;
  font-size: 11.5px;
}

.ask-ai-md :deep(blockquote) {
  margin: 4px 0;
  padding-left: 10px;
  border-left: 3px solid var(--solo-border-light);
  color: var(--solo-text-secondary);
}

.ask-ai-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
}

.ask-ai-dots {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.ask-ai-dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--solo-text-muted);
  animation: ask-ai-bounce 1.2s infinite ease-in-out;
}

.ask-ai-dots i:nth-child(2) { animation-delay: 0.2s; }
.ask-ai-dots i:nth-child(3) { animation-delay: 0.4s; }

@keyframes ask-ai-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-3px); opacity: 1; }
}

.ask-ai-footer {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ask-ai-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.ask-ai-quick-chip {
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px dashed var(--solo-border-light);
  background: transparent;
  color: var(--solo-text-secondary);
  font-size: 11px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.ask-ai-quick-chip:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.ask-ai-input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.ask-ai-input-row > :first-child {
  flex: 1;
}

.ask-ai-note {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.ask-ai-confirm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  margin-top: 2px;
  border-radius: 10px;
  background: var(--solo-info-bg);
  border: 1px solid rgba(23, 71, 176, 0.18);
}

.ask-ai-confirm-conclusion {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.ask-ai-confirm-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--solo-primary);
  flex-shrink: 0;
}

.ask-ai-confirm-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  padding: 2px 8px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid rgba(23, 71, 176, 0.22);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
