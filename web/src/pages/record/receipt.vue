<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, useMessage, NAlert, NUpload, NProgress } from 'naive-ui'
import { recordReceipt } from '@/api/record'
import { useEntryStore } from '@/stores/entry'
import AIClassificationCard from '@/components/business/AIClassificationCard.vue'
import type { RecordReceiptResponse } from '@/api/types'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const result = ref<RecordReceiptResponse | null>(null)
const uploading = ref(false)
const confirming = ref(false)
const error = ref('')
const uploadProgress = ref(0)

async function handleUpload(file: File) {
  if (file.size > 8 * 1024 * 1024) {
    message.error('文件大小不能超过 8MB')
    return false
  }
  const allowed = ['image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/webp']
  if (!allowed.includes(file.type)) {
    message.error('仅支持 PNG/JPG/JPEG/BMP 格式')
    return false
  }
  error.value = ''
  result.value = null
  uploading.value = true
  try {
    const resp = await recordReceipt(file)
    result.value = resp
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    error.value = resp?.response?.data?.detail || '识别失败，请换一张更清晰的照片'
  } finally {
    uploading.value = false
    uploadProgress.value = 0
  }
  return true
}

async function handleConfirm(data: Record<string, unknown>) {
  confirming.value = true
  try {
    const entry = await entryStore.confirmEntry({
      ...data,
      merchant: result.value?.merchant || '',
      invoiceType: result.value?.invoiceType,
      invoiceNumber: result.value?.invoiceNumber,
      billingStatus: result.value?.billingStatus,
      buyerTaxId: result.value?.buyerTaxId,
      sellerTaxId: result.value?.sellerTaxId,
    } as Parameters<typeof entryStore.confirmEntry>[0])
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
}
</script>

<template>
  <div class="receipt-page">
    <div class="page-header">
      <h2>拍照记账</h2>
      <p class="text-muted">上传单据照片，AI 自动识别并分类</p>
    </div>

    <NAlert v-if="error" type="error" closable class="mb-4" @close="error = ''">
      {{ error }}
    </NAlert>

    <!-- Upload area -->
    <NCard :bordered="false" class="upload-card" v-if="!result">
      <NUpload
        :max="1"
        accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp"
        :show-download-button="false"
        :show-retry-button="false"
        :show-cancel-button="false"
        :show-file-list="false"
        :custom-request="({ file }) => {
          if (file.file) handleUpload(file.file as File)
        }"
        :disabled="uploading"
      >
        <div class="upload-zone">
          <template v-if="uploading">
            <div class="uploading-state">
              <div class="spinner" />
              <span>正在识别...</span>
              <NProgress type="line" :percentage="uploadProgress" :indicator-placement="'inside'" />
            </div>
          </template>
          <template v-else>
            <div class="upload-icon">📷</div>
            <p class="upload-title">点击上传单据照片</p>
            <p class="upload-hint">支持 JPG/PNG/BMP，单张不超过 8MB</p>
          </template>
        </div>
      </NUpload>
    </NCard>

    <!-- AI result -->
    <div v-if="result" class="result-section">
      <div class="result-header">
        <h3>识别结果</h3>
        <NButton text @click="result = null" size="small">重新上传</NButton>
      </div>

      <AIClassificationCard
        :result="{
          id: result.id,
          direction: (result.direction as 'income' | 'expense' | 'owner_transfer') || 'expense',
          amount: result.amount,
          title: `${result.merchant || ''} ${result.suggestedCategory}`.trim(),
          category: result.suggestedCategory,
          confidence: result.confidence,
          explanation: result.explanation,
          accountCode: result.accountCode,
          accountName: result.accountName,
          vatRate: result.vatRate,
          vatAmount: result.vatAmount,
          taxDeductible: result.taxDeductible,
          riskTags: result.riskTags || [],
          payer: result.payer as 'company_account' | 'boss_personal' | undefined,
          askFollowUp: result.askFollowUp,
          followUpOptions: result.followUpOptions,
        }"
        :loading="uploading"
        @confirm="handleConfirm"
        @cancel="handleCancel"
        @retry="handleRetry"
      />
    </div>
  </div>
</template>

<style scoped>
.receipt-page {
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
.upload-card {
  border-radius: var(--solo-radius-lg);
}
.upload-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  cursor: pointer;
  border: 2px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
  transition: all 0.2s;
  min-height: 200px;
}
.upload-zone:hover {
  border-color: var(--solo-primary);
  background: rgba(31, 95, 234, 0.03);
}
.upload-icon {
  font-size: 48px;
  margin-bottom: 12px;
}
.upload-title {
  font-size: 16px;
  font-weight: 500;
  color: var(--solo-text-primary);
  margin-bottom: 4px;
}
.upload-hint {
  font-size: 13px;
  color: var(--solo-text-muted);
}
.uploading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 300px;
}
.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e2e8f0;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
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
