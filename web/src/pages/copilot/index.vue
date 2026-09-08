<script setup lang="ts">
import { ref } from 'vue'
import { NCard, NButton, NInput, NSpin, useMessage, NSelect } from 'naive-ui'
import { copilotChat } from '@/api/copilot'

const message = useMessage()

interface Message {
  role: 'user' | 'assistant'
  content: string
}

const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const mode = ref<'agent' | 'tax_advisor' | 'secretary' | 'coach'>('agent')

const modeOptions = [
  { label: 'Agent', value: 'agent' as const },
  { label: '税务顾问', value: 'tax_advisor' as const },
  { label: '秘书', value: 'secretary' as const },
  { label: '教练', value: 'coach' as const },
]

const modeDescriptions: Record<string, string> = {
  agent: '通用智能助手，回答财税问题',
  tax_advisor: '专业税务顾问，提供税务建议',
  secretary: '行政秘书，处理日常事务',
  coach: '经营教练，提供管理建议',
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true

  try {
    const resp = await copilotChat({
      messages: messages.value.map((m) => ({ role: m.role, content: m.content })),
      mode: mode.value,
    })
    messages.value.push({ role: 'assistant', content: resp.reply })
    if (resp.disclaimer) {
      messages.value.push({ role: 'assistant', content: `> ${resp.disclaimer}` })
    }
  } catch {
    message.error('发送失败')
  } finally {
    loading.value = false
  }
}

function clearChat() {
  messages.value = []
}
</script>

<template>
  <div class="copilot-page">
    <div class="page-header">
      <h2>AI 助手</h2>
      <NButton size="tiny" text @click="clearChat" v-if="messages.length > 0">清空对话</NButton>
    </div>

    <!-- Mode selector -->
    <div class="mode-bar mb-4">
      <NSelect
        v-model:value="mode"
        :options="modeOptions"
        size="small"
        style="width: 160px;"
      />
      <span class="mode-desc">{{ modeDescriptions[mode] }}</span>
    </div>

    <!-- Chat messages -->
    <NCard :bordered="false" class="chat-card">
      <div class="chat-messages" v-if="messages.length > 0">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message"
          :class="msg.role"
        >
          <div class="msg-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
          <div class="msg-bubble">{{ msg.content }}</div>
        </div>
      </div>

      <div v-else class="chat-empty">
        <div class="empty-icon">🤖</div>
        <p>你好！我是司理 AI 助手，可以帮你：</p>
        <ul class="hint-list">
          <li>回答财税问题</li>
          <li>分析经营数据</li>
          <li>提供税务建议</li>
          <li>解答记账疑问</li>
        </ul>
      </div>

      <div v-if="loading" class="loading-indicator">
        <NSpin size="small" />
        <span>AI 思考中...</span>
      </div>
    </NCard>

    <!-- Input -->
    <div class="input-bar">
      <NInput
        v-model:value="input"
        type="textarea"
        :rows="2"
        placeholder="输入您的问题..."
        :disabled="loading"
        @keydown.enter.prevent="send"
      />
      <NButton type="primary" :loading="loading" @click="send">发送</NButton>
    </div>
  </div>
</template>

<style scoped>
.copilot-page {
  max-width: 640px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--navbar-height) - 48px);
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 12px; }
.mode-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.mode-desc {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* Chat */
.chat-card {
  border-radius: var(--solo-radius-lg);
  flex: 1;
  overflow-y: auto;
  margin-bottom: 12px;
}
.chat-messages {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.message {
  display: flex;
  gap: 10px;
}
.message.assistant {
  flex-direction: row;
}
.message.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.message.user .msg-avatar {
  background: var(--solo-primary);
  color: #fff;
}
.message.assistant .msg-avatar {
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
}
.msg-bubble {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
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

/* Empty */
.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 20px;
  color: var(--solo-text-secondary);
}
.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
}
.hint-list {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 2;
}

/* Loading */
.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  font-size: 13px;
  color: var(--solo-text-muted);
}

/* Input */
.input-bar {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.input-bar .n-input {
  flex: 1;
}
</style>
