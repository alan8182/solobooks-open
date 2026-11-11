<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import {
  NModal, NSpin, NForm, NFormItem, NInputNumber, NSwitch,
  NSpace, NButton, NTag, useMessage,
} from 'naive-ui'
import {
  getPayrollRates, updatePayrollRates, parsePayrollRatesScreenshot,
  type PayrollRates, type ParsedTaxRatesResult,
} from '@/api/salary'

const props = defineProps<{
  show: boolean
  selectedMonth: string
  payrollRates: PayrollRates | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved', rates: PayrollRates, recalculate: boolean): void
}>()

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const recognizing = ref(false)
const recognizedInfo = ref<ParsedTaxRatesResult | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragging = ref(false)
const localRates = ref<PayrollRates | null>(null)
const ratesRecalculate = ref(true)

const form = ref({
  socialPersonal: 10.5,
  socialCompany: 25,
  housingPersonal: 8,
  housingCompany: 8,
})

function toPercentNumber(rateValue: any): number {
  const n = Number(rateValue)
  if (Number.isNaN(n)) return 0
  return Number((n * 100).toFixed(4).replace(/\.?0+$/, ''))
}

function ratesPercent(value: number) {
  return `${Number((value * 100).toFixed(2))}%`
}

watch(() => props.show, async (visible) => {
  if (visible) {
    loading.value = true
    recognizedInfo.value = null
    try {
      const data: any = await getPayrollRates()
      localRates.value = data
      form.value = {
        socialPersonal: toPercentNumber(data.rates.socialPersonal),
        socialCompany: toPercentNumber(data.rates.socialCompany),
        housingPersonal: toPercentNumber(data.rates.housingPersonal),
        housingCompany: toPercentNumber(data.rates.housingCompany),
      }
    } catch {
      message.error('加载社保公积金比例失败')
    } finally {
      loading.value = false
    }
  }
})

function closeModal() {
  emit('update:show', false)
}

async function handleFileProcess(file: File) {
  if (!file.type.startsWith('image/')) {
    message.warning('请上传图片文件（PNG / JPG / WEBP）')
    return
  }
  recognizing.value = true
  recognizedInfo.value = null
  try {
    const res = await parsePayrollRatesScreenshot(file)
    recognizedInfo.value = res
    if (res.socialCompany > 0) {
      form.value.socialCompany = toPercentNumber(res.socialCompany)
    }
    if (res.socialPersonal > 0) {
      form.value.socialPersonal = toPercentNumber(res.socialPersonal)
    }
    if (res.housingCompany > 0) {
      form.value.housingCompany = toPercentNumber(res.housingCompany)
    }
    if (res.housingPersonal > 0) {
      form.value.housingPersonal = toPercentNumber(res.housingPersonal)
    }
    message.success(`已提取${res.province || ''}社保费率，已自动填入下方表单`)
  } catch (err: any) {
    message.error(err.response?.data?.detail || '截图识别失败，请手动填写比例')
  } finally {
    recognizing.value = false
  }
}

function triggerFileInput() {
  if (recognizing.value) return
  fileInputRef.value?.click()
}

function onFileInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    handleFileProcess(file)
  }
  input.value = ''
}

function onDrop(event: DragEvent) {
  isDragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    handleFileProcess(file)
  }
}

function handlePaste(e: ClipboardEvent) {
  if (!props.show || recognizing.value) return
  const items = e.clipboardData?.items
  if (!items) return
  for (let i = 0; i < items.length; i++) {
    if (items[i].type.startsWith('image/')) {
      const file = items[i].getAsFile()
      if (file) {
        e.preventDefault()
        handleFileProcess(file)
        return
      }
    }
  }
}

onMounted(() => {
  window.addEventListener('paste', handlePaste)
})

onUnmounted(() => {
  window.removeEventListener('paste', handlePaste)
})

async function handleSave() {
  const values = Object.values(form.value)
  if (values.some((v) => Number.isNaN(v) || v < 0 || v > 100)) {
    message.warning('比例需落在 0~100 之间（如 8% 请填 8）')
    return
  }

  saving.value = true
  try {
    const result: any = await updatePayrollRates(
      {
        socialPersonal: form.value.socialPersonal / 100,
        socialCompany: form.value.socialCompany / 100,
        housingPersonal: form.value.housingPersonal / 100,
        housingCompany: form.value.housingCompany / 100,
      },
      { month: props.selectedMonth, recalculate: ratesRecalculate.value },
    )
    localRates.value = result
    emit('saved', result, ratesRecalculate.value)
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存比例失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="社保公积金比例（本账套）"
    style="width: min(560px, 92vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <NSpin :show="loading">
      <!-- 智能截图提取区 -->
      <div
        class="ocr-dropzone"
        :class="{ 'is-dragging': isDragging, 'is-recognizing': recognizing }"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
        @drop.prevent="onDrop"
        @click="triggerFileInput"
      >
        <input
          ref="fileInputRef"
          type="file"
          accept="image/*"
          style="display: none"
          @change="onFileInputChange"
        />
        <NSpin :show="recognizing" size="small">
          <div class="dropzone-content">
            <div class="dropzone-title">
              <span class="icon">📷</span>
              <span class="text">智能提取：粘贴或上传税务局核定表截图</span>
            </div>
            <p class="dropzone-desc">
              在电子税务局截取社保核定明细后，在此<strong>直接按 Ctrl+V 粘贴</strong>，或点击上传图片，AI 自动对齐并计算比例。
            </p>
          </div>
        </NSpin>
      </div>

      <!-- 识别成功提示卡片 -->
      <div v-if="recognizedInfo" class="recognized-banner">
        <div class="recognized-header">
          <NTag type="success" size="small" round>已识别：{{ recognizedInfo.province }}标准</NTag>
          <span class="recognized-summary">{{ recognizedInfo.summary }}</span>
        </div>
      </div>

      <NForm label-placement="left" label-width="96" style="margin-top: 14px">
        <NFormItem label="个人社保">
          <NInputNumber v-model:value="form.socialPersonal" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="个人公积金">
          <NInputNumber v-model:value="form.housingPersonal" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="单位社保">
          <NInputNumber v-model:value="form.socialCompany" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="单位公积金">
          <NInputNumber v-model:value="form.housingCompany" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
      </NForm>
      <p v-if="localRates && !localRates.isCustomized && !recognizedInfo" class="modal-hint muted">
        已按企业注册地（{{ localRates.regionDesc || '系统默认基准' }}）自动预设：单位社保 {{ ratesPercent(Number(localRates.defaults.socialCompany)) }} · 个人社保 {{ ratesPercent(Number(localRates.defaults.socialPersonal)) }}
      </p>
      <div class="rates-recalc-row">
        <NSwitch v-model:value="ratesRecalculate" size="small" />
        <span class="recalc-hint">保存后按新比例重新计算 {{ selectedMonth }} 草稿工资表（已计提月份不受影响）</span>
      </div>
    </NSpin>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.ocr-dropzone {
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-md);
  background: var(--solo-bg-hover);
  padding: 12px 14px;
  cursor: pointer;
  transition: all 0.2s ease;
  user-select: none;
}
.ocr-dropzone:hover,
.ocr-dropzone.is-dragging {
  border-color: var(--solo-primary);
  background: var(--solo-primary-glow);
}
.dropzone-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dropzone-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
}
.dropzone-title .icon {
  font-size: 14px;
}
.dropzone-desc {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.45;
  margin: 0;
}
.dropzone-desc strong {
  color: var(--solo-primary);
  font-weight: 600;
}
.recognized-banner {
  margin-top: 10px;
  padding: 10px 12px;
  background: var(--solo-primary-glow);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-md);
}
.recognized-header {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
}
.recognized-summary {
  font-size: 12px;
  color: var(--solo-text-primary);
  line-height: 1.4;
}
.modal-hint {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
  margin: 0;
}
.modal-hint.muted {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  margin-top: 6px;
}
.rates-recalc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  padding: 10px 12px;
  background: var(--solo-bg-hover);
  border-radius: var(--solo-radius-md);
}
.recalc-hint {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}
</style>
