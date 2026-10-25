<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    accept: string
    title: string
    subtitle?: string
    ariaLabel?: string
    icon?: string
    iconColor?: string
    loading?: boolean
    disabled?: boolean
    maxSizeMb?: number
    dropTitle?: string
  }>(),
  {
    subtitle: '',
    ariaLabel: '',
    icon: 'ri-upload-cloud-2-line',
    iconColor: '',
    loading: false,
    disabled: false,
    maxSizeMb: 20,
    dropTitle: '松开即可上传',
  },
)

const emit = defineEmits<{
  file: [file: File]
  error: [message: string]
}>()

const inputRef = ref<HTMLInputElement | null>(null)
const dragDepth = ref(0)
const isDragging = ref(false)

const locked = computed(() => props.loading || props.disabled)
const allowedExtensions = computed(() =>
  props.accept
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.startsWith('.')),
)

function openPicker() {
  if (locked.value) return
  inputRef.value?.click()
}

function validate(file: File): string | null {
  const name = file.name.toLowerCase()
  if (allowedExtensions.value.length && !allowedExtensions.value.some((ext) => name.endsWith(ext))) {
    return `仅支持 ${allowedExtensions.value.join(' / ')} 文件`
  }
  if (file.size === 0) return '文件为空，请重新选择'
  if (file.size > props.maxSizeMb * 1024 * 1024) return `文件不能超过 ${props.maxSizeMb}MB`
  return null
}

function acceptFile(file: File) {
  const error = validate(file)
  if (error) {
    emit('error', error)
    return
  }
  emit('file', file)
}

function onChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) acceptFile(file)
  input.value = ''
}

function onDragEnter(event: DragEvent) {
  if (locked.value) return
  event.preventDefault()
  dragDepth.value += 1
  isDragging.value = true
}

function onDragOver(event: DragEvent) {
  if (locked.value) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function onDragLeave(event: DragEvent) {
  if (locked.value) return
  event.preventDefault()
  dragDepth.value = Math.max(0, dragDepth.value - 1)
  if (dragDepth.value === 0) isDragging.value = false
}

function onDrop(event: DragEvent) {
  if (locked.value) return
  event.preventDefault()
  dragDepth.value = 0
  isDragging.value = false
  const files = event.dataTransfer?.files
  if (!files || files.length === 0) return
  if (files.length > 1) emit('error', '一次只能上传一个文件，已使用第一个')
  acceptFile(files[0])
}

// 拖到区域外时阻止浏览器把文件当作页面打开，避免丢失当前向导状态
function preventDocumentDrop(event: DragEvent) {
  event.preventDefault()
}

onMounted(() => {
  document.addEventListener('dragover', preventDocumentDrop)
  document.addEventListener('drop', preventDocumentDrop)
})

onBeforeUnmount(() => {
  document.removeEventListener('dragover', preventDocumentDrop)
  document.removeEventListener('drop', preventDocumentDrop)
})

// 供「重新上传」这类外部按钮复用同一个文件选择器
defineExpose({ openPicker })
</script>

<template>
  <div
    class="file-dropzone"
    :class="{ 'is-locked': locked }"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <button
      type="button"
      class="uploader-zone"
      :class="{ active: isDragging }"
      :disabled="locked"
      :aria-label="ariaLabel || title"
      @click="openPicker"
    >
      <i
        :class="[icon, 'upload-icon']"
        :style="iconColor ? { color: iconColor } : undefined"
        aria-hidden="true"
      ></i>
      <span class="upload-title">{{ isDragging ? dropTitle : title }}</span>
      <span class="upload-subtitle">{{ isDragging ? '松开鼠标即可开始处理' : subtitle }}</span>
    </button>
    <input
      ref="inputRef"
      type="file"
      class="dropzone-input"
      :accept="accept"
      @change="onChange"
    />
  </div>
</template>

<style scoped>
.file-dropzone {
  width: 100%;
}
.dropzone-input {
  display: none;
}
.uploader-zone:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
