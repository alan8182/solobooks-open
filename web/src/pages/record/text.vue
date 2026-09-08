<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, NCard, useMessage, NSpace, NAlert, NTag } from 'naive-ui'
import { recordText } from '@/api/record'
import { useEntryStore } from '@/stores/entry'
import AIClassificationCard from '@/components/business/AIClassificationCard.vue'
import type { RecordTextResponse } from '@/api/types'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const text = ref('')
const classifying = ref(false)
const result = ref<RecordTextResponse | null>(null)
const confirming = ref(false)
const error = ref('')

const suggestions = [
  '公司团建采购零食水果 280 元，取得普通发票',
  '收到客户杭州信诺科技有限公司服务费 50000 元',
  '支付六月份办公室租金 8000 元，取得增值税专用发票',
  '购买打印纸与办公文具 350 元，取得电子普通发票',
  '报销研发部李四出差深圳的高铁票 520 元',
]

async function handleClassify() {
  if (!text.value.trim()) {
    message.warning('请输入记账内容')
    return
  }
  error.value = ''
  result.value = null
  classifying.value = true
  try {
    const resp = await recordText({
      text: text.value,
      source: 'web',
    })
    result.value = resp
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    error.value = resp?.response?.data?.detail || '识别失败，请重试'
  } finally {
    classifying.value = false
  }
}

async function handleConfirm(data: Record<string, unknown>) {
  confirming.value = true
  try {
    const entry = await entryStore.confirmEntry(data as unknown as Parameters<typeof entryStore.confirmEntry>[0])
    message.success('记账成功')
    router.push(`/ledger/${entry.id}`)
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '确认失败')
  } finally {
    confirming.value = false
  }
}

function handleCancel() {
  result.value = null
}

function handleRetry() {
  result.value = null
  handleClassify()
}
</script>

<template>
  <div class="record-text-page">
    <div class="page-header">
      <h2>记一笔</h2>
      <p class="text-muted">用文字描述交易，AI 自动分类记账</p>
    </div>

    <NAlert v-if="error" type="error" closable class="mb-4" @close="error = ''">
      {{ error }}
    </NAlert>

    <!-- Input section -->
    <NCard :bordered="false" class="input-card" v-if="!result">
      <div class="suggestions" v-if="!text">
        <span class="hint">试试这样说：</span>
        <div class="suggestion-chips">
          <NTag
            v-for="s in suggestions"
            :key="s"
            size="small"
            :bordered="false"
            class="suggestion-chip"
            @click="text = s"
          >
            {{ s }}
          </NTag>
        </div>
      </div>

      <NInput
        v-model:value="text"
        type="textarea"
        :rows="5"
        placeholder="描述您的交易，例如：&#10;公司团建采购零食水果 280 元，取得普通发票&#10;收到客户杭州信诺科技有限公司服务费 50000 元&#10;支付六月份办公室租金 8000 元，取得增值税专用发票"
        :disabled="classifying"
        class="text-input"
      />

      <NSpace justify="end" class="mt-4">
        <NButton @click="router.push('/')">取消</NButton>
        <NButton
          type="primary"
          :loading="classifying"
          @click="handleClassify"
          size="large"
        >
          AI 识别
        </NButton>
      </NSpace>
    </NCard>

    <!-- AI result -->
    <div v-if="result" class="result-section">
      <div class="result-header">
        <h3>AI 识别结果</h3>
        <NButton text @click="result = null" size="small">修改描述</NButton>
      </div>

      <AIClassificationCard
        :result="result"
        :loading="classifying"
        @confirm="handleConfirm"
        @cancel="handleCancel"
        @retry="handleRetry"
      />

      <NSpace justify="center" class="mt-4">
        <NButton @click="() => { result = null; text = '' }">继续记账</NButton>
      </NSpace>
    </div>
  </div>
</template>

<style scoped>
.record-text-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.mb-4 {
  margin-bottom: 16px;
}
.mt-4 {
  margin-top: 16px;
}
.input-card {
  border-radius: var(--solo-radius-lg);
}
.suggestions {
  margin-bottom: 16px;
}
.hint {
  font-size: 13px;
  color: var(--solo-text-secondary);
  display: block;
  margin-bottom: 8px;
}
.suggestion-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.suggestion-chip {
  cursor: pointer;
  transition: all 0.15s;
}
.suggestion-chip:hover {
  opacity: 0.8;
}
.text-input {
  font-size: 15px;
}
.result-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.result-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
</style>
