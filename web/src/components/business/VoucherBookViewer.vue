<!-- Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 凭证册「仿真册」阅读器：深石板桌面舞台、实体三孔装订线与牛皮封面/象牙内页区分、
 * 动态左右书叠厚度、右侧索引标签贴、左装订约束的真实书本卷页（正交镜像纸背、
 * 圆柱脊高光、折痕投影）、右下/右上角悬停微翘与 2D 跟手拖拽翻页。
 * 页码与总页数一律取 payload.pagination 真值（与 PDF 签发件同源），不用 DOM 页数反推。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { darkTheme, NButton, NConfigProvider, NInput, NSelect, NSwitch, type GlobalThemeOverrides } from 'naive-ui'
import type { VoucherBook } from '@/api/voucherBook'
import {
  AUTO_FLIP_INTERVAL_MS,
  BOOK_CANVAS_HEIGHT,
  BOOK_CANVAS_WIDTH,
  FLIP_DURATION_MS,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  firstPageIndexOfSection,
  flattenVoucherBookPages,
  voucherBookTotalPages,
} from '@/utils/voucherBook'
import {
  arcPointForProgress,
  computePageCurl,
  computePageCurlFromTip,
  computeStackThickness,
  constrainCurlTip,
  curlEase,
  decideDragOutcome,
  type CurlCorner,
} from '@/utils/voucherBookCurl'
import VoucherBookSheet from '@/components/business/VoucherBookSheet.vue'

interface FlipTip {
  x: number
  y: number
}

interface FlipSession {
  from: number
  to: number
  dir: 'next' | 'prev'
  corner: CurlCorner
  progress: number
  dragging: boolean
  commitTarget: number
  tip: FlipTip | null
}

const props = defineProps<{
  show: boolean
  payload: VoucherBook | null
  signed?: boolean
  bookNo?: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const SPINE_X = 28
const BINDING_POSITIONS = [22, 50, 78]
const SECTION_OPTIONS = [
  { label: '封面', value: 'cover' },
  { label: '记账凭证', value: 'vouchers' },
  { label: '原件目录', value: 'originals' },
  { label: '装订签章', value: 'signoff' },
]
const TAB_LABELS: Record<string, string> = {
  cover: '封面',
  vouchers: '凭证',
  originals: '目录',
  signoff: '签章',
}
const KIND_TO_SECTION: Record<string, string> = {
  cover: 'cover',
  voucher: 'vouchers',
  invoice: 'vouchers',
  attachment: 'vouchers',
  originals: 'originals',
  signoff: 'signoff',
}

/** 覆盖 App.vue 浅色全局主题，避免深色阅读器顶栏与工具条文字变黑 */
const darkViewerThemeOverrides: GlobalThemeOverrides = {
  common: {
    textColorBase: 'rgba(255, 255, 255, 0.90)',
    textColor1: 'rgba(255, 255, 255, 0.90)',
    textColor2: 'rgba(255, 255, 255, 0.86)',
    textColor3: 'rgba(255, 255, 255, 0.56)',
    placeholderColor: 'rgba(255, 255, 255, 0.45)',
    textColorDisabled: 'rgba(255, 255, 255, 0.28)',
    inputColor: 'rgba(255, 255, 255, 0.08)',
    inputColorDisabled: 'rgba(255, 255, 255, 0.04)',
    borderColor: 'rgba(255, 255, 255, 0.18)',
    popoverColor: 'rgba(28, 34, 46, 0.98)',
    hoverColor: 'rgba(255, 255, 255, 0.10)',
    pressedColor: 'rgba(255, 255, 255, 0.16)',
  },
  Button: {
    textColor: 'rgba(255, 255, 255, 0.88)',
    textColorTertiary: 'rgba(255, 255, 255, 0.88)',
    textColorHover: 'rgba(255, 255, 255, 1)',
    textColorPressed: 'rgba(255, 255, 255, 1)',
    textColorFocus: 'rgba(255, 255, 255, 1)',
    textColorDisabled: 'rgba(255, 255, 255, 0.28)',
    textColorText: 'rgba(255, 255, 255, 0.88)',
    textColorTextHover: 'rgba(255, 255, 255, 1)',
    textColorTextPressed: 'rgba(255, 255, 255, 1)',
    textColorTextFocus: 'rgba(255, 255, 255, 1)',
    textColorTextDisabled: 'rgba(255, 255, 255, 0.28)',
    textColorGhost: 'rgba(255, 255, 255, 0.88)',
    colorQuaternaryHover: 'rgba(255, 255, 255, 0.12)',
    colorQuaternaryPressed: 'rgba(255, 255, 255, 0.18)',
  },
  Input: {
    color: 'rgba(255, 255, 255, 0.08)',
    colorFocus: 'rgba(255, 255, 255, 0.12)',
    textColor: 'rgba(255, 255, 255, 0.92)',
    placeholderColor: 'rgba(255, 255, 255, 0.45)',
    border: '1px solid rgba(255, 255, 255, 0.18)',
    borderHover: '1px solid rgba(255, 255, 255, 0.34)',
    borderFocus: '1px solid rgba(43, 102, 226, 0.85)',
  },
  Select: {
    peers: {
      InternalSelection: {
        color: 'rgba(255, 255, 255, 0.08)',
        colorActive: 'rgba(255, 255, 255, 0.12)',
        textColor: 'rgba(255, 255, 255, 0.92)',
        placeholderColor: 'rgba(255, 255, 255, 0.45)',
        arrowColor: 'rgba(255, 255, 255, 0.72)',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        borderHover: '1px solid rgba(255, 255, 255, 0.34)',
        borderActive: '1px solid rgba(43, 102, 226, 0.85)',
        borderFocus: '1px solid rgba(43, 102, 226, 0.85)',
      },
      InternalSelectMenu: {
        color: 'rgba(28, 34, 46, 0.98)',
        optionTextColor: 'rgba(255, 255, 255, 0.88)',
        optionTextColorActive: 'rgba(255, 255, 255, 1)',
        optionColorPending: 'rgba(255, 255, 255, 0.12)',
        optionColorActive: 'rgba(43, 102, 226, 0.32)',
      },
    },
  },
}

const pageIndex = ref(0)
const flip = ref<FlipSession | null>(null)
const zoom = ref(1)
const autoFlip = ref(false)
const soundEnabled = ref(false)
const jumpValue = ref<string>('')
const sectionKey = ref<string>('cover')
const internalHidden = ref(false)
const hovering = ref(false)
const hoverCorner = ref(false)
const baseWidth = ref(1120)
const shellRef = ref<HTMLElement | null>(null)

const pages = computed(() => (props.payload ? flattenVoucherBookPages(props.payload) : []))
const totalPages = computed(() => (props.payload ? voucherBookTotalPages(props.payload) : 0))
const isPreview = computed(() => !props.signed)
const availableSections = computed(() => new Set(pages.value.map((page) => KIND_TO_SECTION[page.kind])))
const sectionSelectOptions = computed(() =>
  SECTION_OPTIONS.map((opt) => ({
    ...opt,
    disabled: !availableSections.value.has(opt.value),
  })),
)

/** 非拖拽进行中时，取最终落点页码用于边界置灰，保证连击流畅不误锁 */
const effectivePageIndex = computed(() =>
  flip.value && !flip.value.dragging ? flip.value.commitTarget : pageIndex.value,
)

/** 翻页动画期间：底层露出的页；静止时即当前页 */
const basePage = computed(() => {
  const index = flip.value ? (flip.value.dir === 'next' ? flip.value.to : flip.value.from) : pageIndex.value
  return pages.value[index] || null
})
/** 翻页动画期间：正在绕左装订轴卷折的页片 */
const sheetPage = computed(() => {
  if (!flip.value) return null
  const index = flip.value.dir === 'next' ? flip.value.from : flip.value.to
  return pages.value[index] || null
})
/** 右下角悬停微翘露出的下一页预览 */
const nextPreviewPage = computed(() => {
  if (flip.value || pageIndex.value >= totalPages.value - 1) return null
  return pages.value[pageIndex.value + 1] || null
})

const pageBox = computed(() => {
  const width = Math.round(baseWidth.value * zoom.value)
  const height = Math.round((width * BOOK_CANVAS_HEIGHT) / BOOK_CANVAS_WIDTH)
  return { width, height }
})
const paperScale = computed(() => pageBox.value.width / BOOK_CANVAS_WIDTH)
const canvasStyle = computed(() => ({
  width: `${BOOK_CANVAS_WIDTH}px`,
  height: `${BOOK_CANVAS_HEIGHT}px`,
  transform: `scale(${paperScale.value})`,
}))

const pageStyle = computed(() => ({
  width: `${pageBox.value.width}px`,
  height: `${pageBox.value.height}px`,
}))

const curlGeom = computed(() => {
  if (!flip.value) return null
  const { width, height } = pageBox.value
  if (flip.value.tip) {
    return computePageCurlFromTip(
      flip.value.tip.x,
      flip.value.tip.y,
      width,
      height,
      SPINE_X,
      flip.value.corner,
    )
  }
  return computePageCurl(flip.value.progress, width, height, SPINE_X, flip.value.corner)
})

const stack = computed(() => {
  const activeIdx = flip.value
    ? Math.round(flip.value.from + (flip.value.to - flip.value.from) * flip.value.progress)
    : pageIndex.value
  return computeStackThickness(activeIdx, totalPages.value)
})

let autoTimer: number | null = null
let flipTimer: number | null = null
let flipRaf: number | null = null
let audioCtx: AudioContext | null = null
let justDragged = false

function clearFlipTimer() {
  if (flipTimer !== null) {
    window.clearTimeout(flipTimer)
    flipTimer = null
  }
}

function clearFlipRaf() {
  if (flipRaf !== null) {
    window.cancelAnimationFrame(flipRaf)
    flipRaf = null
  }
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** 轻量纸张摩擦翻页声（Web Audio 合成，默认关闭且严格容错） */
function playFlipAudio() {
  if (!soundEnabled.value || prefersReducedMotion() || typeof window === 'undefined') return
  try {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return
    if (!audioCtx) audioCtx = new Ctor()
    if (audioCtx.state === 'suspended') void audioCtx.resume()

    const duration = 0.12
    const sampleCount = Math.floor(audioCtx.sampleRate * duration)
    const buffer = audioCtx.createBuffer(1, sampleCount, audioCtx.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < sampleCount; i += 1) {
      const env = Math.sin((i / sampleCount) * Math.PI)
      channel[i] = (Math.random() * 2 - 1) * env
    }

    const source = audioCtx.createBufferSource()
    source.buffer = buffer
    const filter = audioCtx.createBiquadFilter()
    filter.type = 'bandpass'
    filter.frequency.setValueAtTime(1800, audioCtx.currentTime)
    filter.frequency.exponentialRampToValueAtTime(850, audioCtx.currentTime + duration)
    filter.Q.value = 1.4

    const gain = audioCtx.createGain()
    gain.gain.setValueAtTime(0.001, audioCtx.currentTime)
    gain.gain.linearRampToValueAtTime(0.055, audioCtx.currentTime + 0.025)
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration)

    source.connect(filter)
    filter.connect(gain)
    gain.connect(audioCtx.destination)
    source.start()
  } catch {
    // 浏览器限制或无音频上下文时静默忽略
  }
}

function commitFlip(target: number) {
  if (!flip.value || flip.value.commitTarget !== target) return
  clearFlipRaf()
  clearFlipTimer()
  flip.value = null
  pageIndex.value = target
  scheduleAutoTimer()
}

function flushActiveFlip() {
  if (flip.value && !flip.value.dragging) {
    commitFlip(flip.value.commitTarget)
  }
}

/** 驱动翻页进度从 fromProgress 平滑过渡到 toProgress（支持拖拽松手起点 tip 连续衔接） */
function animateFlipProgress(
  from: number,
  to: number,
  dir: 'next' | 'prev',
  corner: CurlCorner,
  fromProgress: number,
  toProgress: number,
  commitTarget: number,
  releaseTip: FlipTip | null = null,
) {
  const delta = Math.abs(toProgress - fromProgress)
  if (delta < 1e-3 || prefersReducedMotion()) {
    flip.value = null
    pageIndex.value = commitTarget
    scheduleAutoTimer()
    return
  }

  const durationMs = Math.max(140, Math.round(FLIP_DURATION_MS * delta))
  const { width, height } = pageBox.value
  const endTip = releaseTip ? arcPointForProgress(toProgress, width, height, SPINE_X, corner) : null

  flip.value = { from, to, dir, corner, progress: fromProgress, dragging: false, commitTarget, tip: releaseTip }
  clearFlipRaf()
  clearFlipTimer()

  const startTime = performance.now()
  const step = () => {
    if (!flip.value || flip.value.commitTarget !== commitTarget || flip.value.dragging) return
    const t = Math.min(1, (performance.now() - startTime) / durationMs)
    const eased = curlEase(t)
    const nextProgress = fromProgress + (toProgress - fromProgress) * eased
    let nextTip: FlipTip | null = null
    if (releaseTip && endTip) {
      nextTip = constrainCurlTip(
        releaseTip.x + (endTip.x - releaseTip.x) * eased,
        releaseTip.y + (endTip.y - releaseTip.y) * eased,
        width,
        height,
        SPINE_X,
        corner,
      )
    }
    flip.value = {
      ...flip.value,
      progress: nextProgress,
      tip: nextTip,
    }
    if (t < 1) {
      flipRaf = requestAnimationFrame(step)
    } else {
      flipRaf = null
    }
  }
  flipRaf = requestAnimationFrame(step)
  flipTimer = window.setTimeout(() => commitFlip(commitTarget), durationMs)
}

/** 翻页入口：相邻页走装订约束真实卷页（连击自动提交前页），跨多页/降低动效直接切换 */
function navigateTo(target: number, corner: CurlCorner = 'br') {
  if (!props.show || totalPages.value === 0 || flip.value?.dragging) return
  flushActiveFlip()
  const clamped = Math.min(Math.max(0, target), totalPages.value - 1)
  if (clamped === pageIndex.value) return
  hoverCorner.value = false
  if (Math.abs(clamped - pageIndex.value) !== 1 || prefersReducedMotion()) {
    pageIndex.value = clamped
    scheduleAutoTimer()
    return
  }
  const dir: 'next' | 'prev' = clamped > pageIndex.value ? 'next' : 'prev'
  playFlipAudio()
  animateFlipProgress(
    pageIndex.value,
    clamped,
    dir,
    corner,
    dir === 'next' ? 0 : 1,
    dir === 'next' ? 1 : 0,
    clamped,
  )
}

function clearAutoTimer() {
  if (autoTimer !== null) {
    window.clearInterval(autoTimer)
    autoTimer = null
  }
}

function scheduleAutoTimer() {
  clearAutoTimer()
  if (!autoFlip.value || !props.show) return
  autoTimer = window.setInterval(() => {
    if (hovering.value || flip.value) return
    if (pageIndex.value < totalPages.value - 1) {
      navigateTo(pageIndex.value + 1)
    } else {
      autoFlip.value = false
      clearAutoTimer()
    }
  }, AUTO_FLIP_INTERVAL_MS)
}

/* ── 指针边缘/页角 2D 拖拽跟手翻页（中间文本区保持可选中，不劫持复制） ── */
interface DragSession {
  pointerId: number
  dir: 'next' | 'prev'
  corner: CurlCorner
  startX: number
  startY: number
  lastX: number
  lastTime: number
  velocityX: number
  rect: DOMRect
  active: boolean
}

let dragSession: DragSession | null = null

function cleanupDragListeners() {
  window.removeEventListener('pointermove', onWindowPointerMove)
  window.removeEventListener('pointerup', onWindowPointerEnd)
  window.removeEventListener('pointercancel', onWindowPointerEnd)
  dragSession = null
}

function onShellPointerDown(event: PointerEvent) {
  if (event.button !== 0 || flip.value || totalPages.value <= 1 || prefersReducedMotion()) return
  const targetEl = event.target as HTMLElement | null
  if (targetEl?.closest('.book-tabs')) return
  if (targetEl?.closest('button, a, input, select') && !targetEl.closest('.corner-peel-zone')) return

  const rect = shellRef.value?.getBoundingClientRect()
  if (!rect || rect.width <= 0) return

  const relX = (event.clientX - rect.left) / rect.width
  const relY = (event.clientY - rect.top) / rect.height
  let dir: 'next' | 'prev' | null = null
  if (pageIndex.value < totalPages.value - 1 && (relX >= 0.78 || (relX >= 0.68 && relY >= 0.72))) {
    dir = 'next'
  } else if (pageIndex.value > 0 && relX <= 0.16) {
    dir = 'prev'
  }
  if (!dir) return

  const corner: CurlCorner = relY < 0.35 ? 'tr' : 'br'
  dragSession = {
    pointerId: event.pointerId,
    dir,
    corner,
    startX: event.clientX,
    startY: event.clientY,
    lastX: event.clientX,
    lastTime: performance.now(),
    velocityX: 0,
    rect,
    active: false,
  }
  window.addEventListener('pointermove', onWindowPointerMove)
  window.addEventListener('pointerup', onWindowPointerEnd)
  window.addEventListener('pointercancel', onWindowPointerEnd)
}

function onWindowPointerMove(event: PointerEvent) {
  if (!dragSession || event.pointerId !== dragSession.pointerId) return
  const dx = event.clientX - dragSession.startX
  const dy = event.clientY - dragSession.startY
  const now = performance.now()

  if (!dragSession.active) {
    if (Math.hypot(dx, dy) < 6) return
    if (Math.abs(dy) > Math.abs(dx) * 1.4) {
      cleanupDragListeners()
      return
    }
    const dir = dragSession.dir
    const target = dir === 'next' ? pageIndex.value + 1 : pageIndex.value - 1
    if (target < 0 || target >= totalPages.value) {
      cleanupDragListeners()
      return
    }
    dragSession.active = true
    hoverCorner.value = false
    playFlipAudio()
    flip.value = {
      from: pageIndex.value,
      to: target,
      dir,
      corner: dragSession.corner,
      progress: dir === 'next' ? 0 : 1,
      dragging: true,
      commitTarget: target,
      tip: null,
    }
  }

  const dt = Math.max(1, now - dragSession.lastTime)
  dragSession.velocityX = (event.clientX - dragSession.lastX) / dt
  dragSession.lastX = event.clientX
  dragSession.lastTime = now

  if (!flip.value) return
  const { width, height } = pageBox.value
  if (dragSession.dir === 'next') {
    const rawX = event.clientX - dragSession.rect.left
    const rawY = event.clientY - dragSession.rect.top
    const geom = computePageCurlFromTip(rawX, rawY, width, height, SPINE_X, dragSession.corner)
    flip.value = {
      ...flip.value,
      progress: geom.progress,
      tip: { x: geom.tipX, y: geom.tipY },
    }
  } else {
    const span = Math.max(220, dragSession.rect.width * 0.65)
    const progress = Math.min(1, Math.max(0, 1 - dx / span))
    flip.value = { ...flip.value, progress, tip: null }
  }
}

function onWindowPointerEnd(event: PointerEvent) {
  if (!dragSession || event.pointerId !== dragSession.pointerId) return
  const wasActive = dragSession.active
  const velocityX = dragSession.velocityX
  const dir = dragSession.dir
  cleanupDragListeners()

  if (!wasActive || !flip.value) return
  justDragged = true
  window.setTimeout(() => {
    justDragged = false
  }, 50)

  const current = flip.value
  const outcome = decideDragOutcome(current.progress, velocityX, dir)
  const toProgress = dir === 'next' ? (outcome === 'commit' ? 1 : 0) : outcome === 'commit' ? 0 : 1
  const commitTarget = outcome === 'commit' ? current.to : current.from
  animateFlipProgress(
    current.from,
    current.to,
    dir,
    current.corner,
    current.progress,
    toProgress,
    commitTarget,
    current.tip,
  )
}

function onCornerClick() {
  if (justDragged) return
  goNext()
}

function resetView() {
  cleanupDragListeners()
  clearFlipTimer()
  clearFlipRaf()
  flip.value = null
  pageIndex.value = 0
  zoom.value = 1
  jumpValue.value = ''
  sectionKey.value = 'cover'
  autoFlip.value = false
  hoverCorner.value = false
  clearAutoTimer()
}

watch(
  () => props.show,
  (value) => {
    if (value) {
      resetView()
    } else {
      cleanupDragListeners()
      clearAutoTimer()
      clearFlipTimer()
      clearFlipRaf()
    }
  },
  { immediate: true },
)

watch(autoFlip, () => scheduleAutoTimer())

watch(pageIndex, (index) => {
  const page = pages.value[index]
  if (page) sectionKey.value = KIND_TO_SECTION[page.kind] || 'cover'
})

function goPrev() {
  if (flip.value?.dragging) return
  flushActiveFlip()
  navigateTo(pageIndex.value - 1)
}

function goNext() {
  if (flip.value?.dragging) return
  flushActiveFlip()
  navigateTo(pageIndex.value + 1)
}

function backToCover() {
  navigateTo(0)
}

function submitJump() {
  const value = Number.parseInt(jumpValue.value, 10)
  if (!Number.isFinite(value)) return
  const target = Math.min(Math.max(1, value), totalPages.value)
  navigateTo(target - 1)
  jumpValue.value = ''
  scheduleAutoTimer()
}

function jumpSection(key: string) {
  if (!props.payload || !availableSections.value.has(key)) return
  sectionKey.value = key
  navigateTo(firstPageIndexOfSection(pages.value, key))
}

function zoomBy(delta: number) {
  zoom.value = clampZoom(zoom.value + delta)
}

function close() {
  emit('update:show', false)
  cleanupDragListeners()
  clearAutoTimer()
  clearFlipTimer()
  clearFlipRaf()
}

function onKeydown(event: KeyboardEvent) {
  if (!props.show) return
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
  if (target?.isContentEditable) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goPrev()
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    goNext()
  }
}

function updateBaseWidth() {
  // 同时受视口宽高约束（给右侧纸叠与索引标签贴留白）：100% 缩放时整本册完整入框
  const byWidth = window.innerWidth - 220
  const byHeight = (window.innerHeight - 170) * (297 / 210)
  baseWidth.value = Math.min(1180, Math.max(640, Math.min(byWidth, byHeight)))
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', updateBaseWidth)
  updateBaseWidth()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', updateBaseWidth)
  cleanupDragListeners()
  clearAutoTimer()
  clearFlipTimer()
  clearFlipRaf()
  if (audioCtx) {
    void audioCtx.close().catch(() => {})
    audioCtx = null
  }
})

// Teleport 层不会随 KeepAlive 页面消失：失活时隐藏、激活时恢复（同 SoloDrawer 惯例）
onDeactivated(() => {
  internalHidden.value = true
  cleanupDragListeners()
  clearAutoTimer()
  clearFlipTimer()
  clearFlipRaf()
})

onActivated(() => {
  internalHidden.value = false
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show && !internalHidden && payload"
      class="book-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="凭证册仿真阅读器"
    >
      <NConfigProvider :theme="darkTheme" :theme-overrides="darkViewerThemeOverrides">
        <div class="viewer-topbar">
          <span class="viewer-title">凭证册 · {{ payload.meta.month }}<template v-if="bookNo"> · {{ bookNo }}</template></span>
          <div class="topbar-actions">
            <NButton
              quaternary
              circle
              :aria-label="soundEnabled ? '关闭翻页音效' : '开启翻页音效'"
              :title="soundEnabled ? '翻页音效：开' : '翻页音效：关'"
              @click="soundEnabled = !soundEnabled"
            >
              <template #icon>
                <i :class="soundEnabled ? 'ri-volume-up-line' : 'ri-volume-mute-line'" />
              </template>
            </NButton>
            <NButton quaternary circle aria-label="关闭" @click="close">
              <template #icon><i class="ri-close-line" /></template>
            </NButton>
          </div>
        </div>
      </NConfigProvider>

      <div class="viewer-stage" @mouseenter="hovering = true" @mouseleave="hovering = false">
        <button
          type="button"
          class="edge edge-prev"
          aria-label="上一页"
          :disabled="effectivePageIndex === 0 || !!flip?.dragging"
          @click="goPrev"
        >
          <i class="ri-arrow-left-s-line" />
        </button>

        <div class="paper-wrap" :style="pageStyle">
          <div ref="shellRef" class="book-shell" @pointerdown="onShellPointerDown">
            <!-- 动态书叠厚度（左已翻 / 右未翻 / 底边） -->
            <div v-if="stack.leftPx > 0" class="book-stack-left" :style="{ width: `${stack.leftPx}px` }" aria-hidden="true" />
            <div v-if="stack.rightPx > 0" class="book-stack-right" :style="{ width: `${stack.rightPx}px` }" aria-hidden="true" />
            <div class="book-stack-bottom" aria-hidden="true" />

            <!-- 实体档案侧边索引标签贴 -->
            <nav
              class="book-tabs"
              :style="{ left: `calc(100% + ${stack.rightPx}px)` }"
              aria-label="凭证册章节导航"
            >
              <button
                v-for="opt in SECTION_OPTIONS"
                :key="opt.value"
                type="button"
                class="book-tab"
                :class="{ 'is-active': sectionKey === opt.value }"
                :disabled="!availableSections.has(opt.value)"
                :data-testid="`book-tab-${opt.value}`"
                @click="jumpSection(opt.value)"
              >
                {{ TAB_LABELS[opt.value] || opt.label }}
              </button>
            </nav>

            <!-- 翻页舞台（无 overflow:hidden，保证卷起的纸角能自然悬出页框） -->
            <div class="flip-book" :class="{ 'is-flipping': !!flip }">
              <!-- 底层页 -->
              <div
                v-if="basePage"
                class="book-paper page-layer"
                :class="{ 'is-cover': basePage.kind === 'cover' }"
              >
                <div
                  class="paper-canvas"
                  :class="{ 'is-cover': basePage.kind === 'cover' }"
                  :style="canvasStyle"
                >
                  <div v-if="basePage.kind === 'cover'" class="cover-spine-strip" aria-hidden="true">
                    <span v-for="pos in BINDING_POSITIONS" :key="pos" class="spine-rivet" :style="{ top: `${pos}%` }" />
                  </div>
                  <div v-else class="inner-binding" aria-hidden="true">
                    <span class="binding-thread" />
                    <span v-for="pos in BINDING_POSITIONS" :key="pos" class="binding-hole" :style="{ top: `${pos}%` }" />
                  </div>

                  <VoucherBookSheet
                    :page="basePage"
                    :payload="payload"
                    :total-pages="totalPages"
                    :signed="Boolean(signed)"
                  />
                </div>
                <div v-if="isPreview" class="preview-watermark" aria-hidden="true">预览件 · 未签发</div>

                <!-- 翻页期间紧贴折痕线 L 投射在底层露出页上的动态阴影 -->
                <div
                  v-if="curlGeom && !curlGeom.isFlat"
                  class="curl-under-shadow-clip"
                  :style="{ clipPath: curlGeom.peeledClip }"
                  aria-hidden="true"
                >
                  <div
                    class="curl-under-shadow-band"
                    :style="{
                      left: `${curlGeom.foldMidX}px`,
                      top: `${curlGeom.foldMidY}px`,
                      transform: `rotate(${curlGeom.normalAngleDeg}deg)`,
                      opacity: String(curlGeom.shadowOpacity),
                    }"
                  />
                </div>

                <!-- 右下角悬停微翘交互区（紧贴角点，避免遮挡页脚页码） -->
                <button
                  v-if="!flip && pageIndex < totalPages - 1 && nextPreviewPage"
                  type="button"
                  class="corner-peel-zone"
                  :class="{ 'is-peeling': hoverCorner }"
                  aria-label="翻至下一页"
                  @mouseenter="hoverCorner = true"
                  @mouseleave="hoverCorner = false"
                  @click="onCornerClick"
                >
                  <span class="corner-under-peek" aria-hidden="true" />
                  <span class="corner-fold-flap" aria-hidden="true" />
                </button>
              </div>

              <!-- 真实书本卷页层：平铺裁剪页 + 折痕正交镜像卷起纸背 -->
              <div v-if="flip && sheetPage && curlGeom" class="flip-leaf">
                <div class="curl-flat-layer" :style="{ clipPath: curlGeom.flatClip }">
                  <div
                    class="book-paper"
                    :class="{ 'is-cover': sheetPage.kind === 'cover' }"
                  >
                    <div
                      class="paper-canvas"
                      :class="{ 'is-cover': sheetPage.kind === 'cover' }"
                      :style="canvasStyle"
                    >
                      <div v-if="sheetPage.kind === 'cover'" class="cover-spine-strip" aria-hidden="true">
                        <span v-for="pos in BINDING_POSITIONS" :key="pos" class="spine-rivet" :style="{ top: `${pos}%` }" />
                      </div>
                      <div v-else class="inner-binding" aria-hidden="true">
                        <span class="binding-thread" />
                        <span v-for="pos in BINDING_POSITIONS" :key="pos" class="binding-hole" :style="{ top: `${pos}%` }" />
                      </div>

                      <VoucherBookSheet
                        :page="sheetPage"
                        :payload="payload"
                        :total-pages="totalPages"
                        :signed="Boolean(signed)"
                      />
                    </div>
                    <div v-if="isPreview" class="preview-watermark" aria-hidden="true">预览件 · 未签发</div>

                    <div
                      v-if="!curlGeom.isFlat"
                      class="curl-inner-crease"
                      :style="{
                        left: `${curlGeom.foldMidX}px`,
                        top: `${curlGeom.foldMidY}px`,
                        transform: `rotate(${curlGeom.normalAngleDeg + 180}deg)`,
                        opacity: String(curlGeom.ridgeStrength),
                      }"
                      aria-hidden="true"
                    />
                  </div>
                </div>

                <div
                  v-if="!curlGeom.isFlat"
                  class="curl-back-wrap"
                  :style="{ opacity: String(curlGeom.flapOpacity) }"
                  aria-hidden="true"
                >
                  <div
                    class="curl-back-transform"
                    :style="{ transform: curlGeom.backMatrix }"
                  >
                    <div
                      class="curl-back-sheet"
                      :class="{ 'is-cover': sheetPage.kind === 'cover' }"
                      :style="{ clipPath: curlGeom.peeledClip }"
                    >
                      <div class="curl-back-bleed" :class="{ 'is-cover': sheetPage.kind === 'cover' }">
                        <div
                          class="paper-canvas"
                          :class="{ 'is-cover': sheetPage.kind === 'cover' }"
                          :style="canvasStyle"
                        >
                          <VoucherBookSheet
                            :page="sheetPage"
                            :payload="payload"
                            :total-pages="totalPages"
                            :signed="Boolean(signed)"
                          />
                        </div>
                      </div>
                      <div
                        class="curl-ridge-lighting"
                        :style="{
                          left: `${curlGeom.foldMidX}px`,
                          top: `${curlGeom.foldMidY}px`,
                          transform: `rotate(${curlGeom.normalAngleDeg}deg)`,
                          opacity: String(curlGeom.ridgeStrength),
                        }"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <button
          type="button"
          class="edge edge-next"
          aria-label="下一页"
          :disabled="effectivePageIndex >= totalPages - 1 || !!flip?.dragging"
          @click="goNext"
        >
          <i class="ri-arrow-right-s-line" />
        </button>
      </div>

      <NConfigProvider :theme="darkTheme" :theme-overrides="darkViewerThemeOverrides">
        <div class="viewer-toolbar">
          <div class="toolbar-group">
            <NButton
              size="small"
              quaternary
              :disabled="effectivePageIndex === 0 || !!flip?.dragging"
              data-testid="book-prev"
              @click="goPrev"
            >
              ‹ 上一页
            </NButton>
            <span class="page-indicator" data-testid="book-page-indicator">第 {{ pageIndex + 1 }} / {{ totalPages }} 页</span>
            <NButton
              size="small"
              quaternary
              :disabled="effectivePageIndex >= totalPages - 1 || !!flip?.dragging"
              data-testid="book-next"
              @click="goNext"
            >
              下一页 ›
            </NButton>
          </div>

          <div class="toolbar-group">
            <span class="toolbar-label">跳转到</span>
            <NInput
              v-model:value="jumpValue"
              size="small"
              placeholder="页"
              class="jump-input"
              data-testid="book-jump"
              @keyup.enter="submitJump"
            />
            <NButton size="small" quaternary @click="submitJump">页</NButton>
            <NSelect
              v-model:value="sectionKey"
              size="small"
              :options="sectionSelectOptions"
              class="section-select"
              data-testid="book-section"
              @update:value="jumpSection"
            />
          </div>

          <div class="toolbar-group">
            <NButton size="small" quaternary :disabled="zoom <= MIN_ZOOM" aria-label="缩小" @click="zoomBy(-0.1)">－</NButton>
            <span class="zoom-value">{{ Math.round(zoom * 100) }}%</span>
            <NButton size="small" quaternary :disabled="zoom >= MAX_ZOOM" aria-label="放大" @click="zoomBy(0.1)">＋</NButton>
          </div>

          <div class="toolbar-group">
            <span class="toolbar-label">自动翻页</span>
            <NSwitch v-model:value="autoFlip" size="small" data-testid="book-autoplay" />
          </div>

          <NButton size="small" quaternary data-testid="book-cover" @click="backToCover">← 返回封面</NButton>
        </div>
      </NConfigProvider>
    </div>
  </Teleport>
</template>

<style scoped>
.book-viewer {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(ellipse 68% 58% at 50% 44%, var(--solo-book-desk-light) 0%, var(--solo-book-desk) 74%);
}
.viewer-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 16px 0 22px;
  color: rgba(255, 255, 255, 0.9);
  font-size: 13px;
}
.viewer-title { font-weight: 600; letter-spacing: 0.3px; }
.topbar-actions { display: flex; align-items: center; gap: 4px; }
.viewer-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 6px 18px 14px;
  overflow: auto;
}
.edge {
  flex: 0 0 auto;
  width: 40px;
  height: 64px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
  font-size: 26px;
  cursor: pointer;
  display: grid;
  place-items: center;
  backdrop-filter: blur(8px);
  transition: background-color 0.15s ease, border-color 0.15s ease, opacity 0.15s ease;
}
.edge:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.28);
}
.edge:disabled { opacity: 0.24; cursor: default; }

.paper-wrap {
  flex: 0 0 auto;
  margin: auto;
  transition: width 0.2s cubic-bezier(0.16, 1, 0.3, 1), height 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.book-shell {
  position: relative;
  width: 100%;
  height: 100%;
  touch-action: pan-y;
}

/* ── 实体书叠边缘 ── */
.book-stack-left,
.book-stack-right {
  position: absolute;
  top: 4px;
  bottom: 3px;
  pointer-events: none;
  background-color: var(--solo-paper-edge);
  background-image: repeating-linear-gradient(
    90deg,
    rgba(63, 52, 42, 0.16) 0 1px,
    rgba(255, 255, 255, 0.28) 1px 2.5px
  );
  transition: width 0.24s ease;
}
.book-stack-left {
  right: 100%;
  border-radius: 3px 0 0 3px;
  box-shadow: -3px 4px 10px rgba(0, 0, 0, 0.35), inset -2px 0 4px rgba(0, 0, 0, 0.14);
}
.book-stack-right {
  left: 100%;
  border-radius: 0 3px 3px 0;
  box-shadow: 4px 4px 12px rgba(0, 0, 0, 0.32), inset 2px 0 4px rgba(0, 0, 0, 0.12);
}
.book-stack-bottom {
  position: absolute;
  left: 8px;
  right: 1px;
  top: 100%;
  height: 6px;
  border-radius: 0 0 3px 3px;
  pointer-events: none;
  background-color: var(--solo-paper-edge);
  background-image: repeating-linear-gradient(
    0deg,
    rgba(63, 52, 42, 0.18) 0 1px,
    rgba(255, 255, 255, 0.24) 1px 2.5px
  );
  box-shadow:
    0 20px 48px rgba(0, 0, 0, 0.52),
    0 8px 18px rgba(0, 0, 0, 0.34);
}

/* ── 侧边档案索引标签贴 ── */
.book-tabs {
  position: absolute;
  top: 13%;
  left: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 4;
  padding-left: 1px;
}
.book-tab {
  appearance: none;
  border: 1px solid rgba(63, 52, 42, 0.25);
  border-left: 0;
  border-radius: 0 6px 6px 0;
  padding: 9px 7px;
  background: var(--solo-paper-back);
  color: var(--solo-text-secondary);
  font-size: 11px;
  font-weight: 600;
  writing-mode: vertical-rl;
  letter-spacing: 2px;
  cursor: pointer;
  box-shadow: 3px 2px 6px rgba(0, 0, 0, 0.26);
  transition: transform 0.18s ease, background-color 0.18s ease, color 0.18s ease, opacity 0.18s ease;
}
.book-tab:hover:not(:disabled) {
  transform: translateX(3px);
  color: var(--solo-text-primary);
}
.book-tab:disabled {
  opacity: 0.42;
  cursor: default;
}
.book-tab.is-active {
  transform: translateX(5px);
  background: var(--solo-paper);
  color: var(--solo-doc-ink);
  border-right: 3px solid var(--solo-primary);
}

/* ── 翻页舞台与纸张材质（flip-book 不设 overflow:hidden，保留卷起页角悬出纵深） ── */
.flip-book {
  position: relative;
  width: 100%;
  height: 100%;
}
.page-layer {
  position: absolute;
  inset: 0;
}
.book-paper {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: 2px 4px 4px 2px;
  background-color: var(--solo-paper);
  background-image:
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='grain'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23grain)' opacity='0.036'/%3E%3C/svg%3E"),
    linear-gradient(125deg, rgba(255, 255, 255, 0.22) 0%, transparent 48%, rgba(63, 52, 42, 0.04) 100%);
  box-shadow:
    inset 0 0 0 1px rgba(63, 52, 42, 0.12),
    0 2px 6px rgba(0, 0, 0, 0.18);
}
.book-paper.is-cover {
  background-color: var(--solo-paper-cover);
  background-image:
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='grain-c'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.7' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23grain-c)' opacity='0.055'/%3E%3C/svg%3E"),
    linear-gradient(135deg, rgba(255, 255, 255, 0.16) 0%, transparent 55%, rgba(63, 52, 42, 0.08) 100%);
}
/* 固定 1120×792 设计画布等比缩放，保证任意屏宽与缩放倍率下版式比例锁定不重叠 */
.paper-canvas {
  position: relative;
  transform-origin: 0 0;
  padding: 24px 36px 20px 64px;
}
.paper-canvas.is-cover {
  padding-left: 66px;
}

/* 封面左侧深色包脊与装订铆钉 */
.cover-spine-strip {
  position: absolute;
  inset: 0 auto 0 0;
  width: 34px;
  pointer-events: none;
  background:
    linear-gradient(
      90deg,
      rgba(0, 0, 0, 0.28) 0%,
      rgba(255, 255, 255, 0.09) 24%,
      rgba(0, 0, 0, 0.04) 76%,
      rgba(0, 0, 0, 0.34) 100%
    ),
    var(--solo-paper-cover-spine);
  box-shadow: 2px 0 6px rgba(24, 34, 48, 0.22);
}
.cover-spine-strip::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 100%;
  width: 10px;
  background: linear-gradient(90deg, rgba(24, 34, 48, 0.16), transparent);
}
.spine-rivet {
  position: absolute;
  left: 50%;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: radial-gradient(circle at 35% 32%, rgba(255, 255, 255, 0.85), rgba(168, 152, 128, 0.9) 55%, rgba(63, 52, 42, 0.95));
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.55);
}

/* 内页左侧三孔装订线与书脊曲率光影 */
.inner-binding {
  position: absolute;
  inset: 0 auto 0 0;
  width: 44px;
  pointer-events: none;
  background: linear-gradient(
    90deg,
    rgba(24, 34, 48, 0.2) 0%,
    rgba(24, 34, 48, 0.06) 28%,
    rgba(255, 255, 255, 0.28) 54%,
    transparent 100%
  );
}
.binding-thread {
  position: absolute;
  left: 18px;
  top: 22%;
  bottom: 22%;
  width: 2px;
  transform: translateX(-50%);
  background: rgba(250, 246, 238, 0.88);
  box-shadow: 1px 0 1px rgba(63, 52, 42, 0.35);
}
.binding-hole {
  position: absolute;
  left: 18px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: var(--solo-book-desk);
  box-shadow:
    inset 0 1px 2px rgba(0, 0, 0, 0.72),
    0 0 0 1.5px rgba(63, 52, 42, 0.24);
}

.preview-watermark {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  transform: rotate(-28deg);
  font-size: 46px;
  font-weight: 700;
  letter-spacing: 12px;
  color: rgba(24, 34, 48, 0.07);
  pointer-events: none;
  user-select: none;
}

/* ── 真实书本卷页：折痕裁剪平铺页 + 正交镜像纸背 + 圆柱高光 + 折痕投影 ── */
.flip-leaf {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 6;
}
.curl-flat-layer {
  position: absolute;
  inset: 0;
  will-change: clip-path;
}
.curl-inner-crease {
  position: absolute;
  width: 38px;
  height: 4000px;
  margin-top: -2000px;
  transform-origin: 0 2000px;
  pointer-events: none;
  background: linear-gradient(90deg, rgba(24, 20, 16, 0.24) 0px, rgba(24, 20, 16, 0.08) 12px, transparent 36px);
}
.curl-back-wrap {
  position: absolute;
  inset: 0;
  pointer-events: none;
  filter: drop-shadow(-8px 10px 18px rgba(0, 0, 0, 0.34));
  will-change: opacity;
}
.curl-back-transform {
  position: absolute;
  inset: 0;
  transform-origin: 0 0;
  will-change: transform;
}
.curl-back-sheet {
  position: absolute;
  inset: 0;
  overflow: hidden;
  border-radius: 2px 4px 4px 2px;
  background-color: var(--solo-paper-back);
  background-image:
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='grain-b'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23grain-b)' opacity='0.036'/%3E%3C/svg%3E");
  box-shadow: inset 0 0 0 1px rgba(63, 52, 42, 0.16);
  will-change: clip-path;
}
.curl-back-sheet.is-cover {
  background-color: var(--solo-paper-cover);
}
.curl-back-bleed {
  position: absolute;
  inset: 0;
  opacity: 0.095;
  filter: blur(0.35px);
  pointer-events: none;
  user-select: none;
}
.curl-back-bleed.is-cover {
  opacity: 0.035;
}
.curl-ridge-lighting {
  position: absolute;
  width: 112px;
  height: 4000px;
  margin-top: -2000px;
  transform-origin: 0 2000px;
  pointer-events: none;
  background: linear-gradient(
    90deg,
    rgba(40, 32, 24, 0.24) 0px,
    rgba(255, 255, 255, 0.72) 11px,
    rgba(255, 255, 255, 0.26) 26px,
    rgba(63, 52, 42, 0.12) 54px,
    transparent 105px
  );
}
.curl-under-shadow-clip {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
  will-change: clip-path;
}
.curl-under-shadow-band {
  position: absolute;
  width: 120px;
  height: 4000px;
  margin-top: -2000px;
  transform-origin: 0 2000px;
  background: linear-gradient(
    90deg,
    rgba(10, 14, 22, 0.44) 0px,
    rgba(10, 14, 22, 0.16) 28px,
    rgba(10, 14, 22, 0.04) 68px,
    transparent 115px
  );
}

/* ── 右下角悬停微翘交互区 ── */
.corner-peel-zone {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 76px;
  height: 76px;
  border: 0;
  padding: 0;
  background: transparent;
  cursor: pointer;
  z-index: 5;
}
.corner-under-peek,
.corner-fold-flap {
  position: absolute;
  right: 0;
  bottom: 0;
  pointer-events: none;
  transition: width 0.2s ease, height 0.2s ease, opacity 0.2s ease;
}
.corner-under-peek {
  width: 0;
  height: 0;
  opacity: 0;
  background:
    linear-gradient(135deg, rgba(24, 34, 48, 0.22) 48%, rgba(24, 34, 48, 0.05) 66%, transparent 86%),
    var(--solo-paper);
  clip-path: polygon(100% 0, 100% 100%, 0 100%);
}
.corner-fold-flap {
  width: 0;
  height: 0;
  opacity: 0;
  background: linear-gradient(
    315deg,
    rgba(63, 52, 42, 0.28) 46%,
    rgba(255, 255, 255, 0.92) 52%,
    var(--solo-paper-back) 68%,
    var(--solo-paper) 100%
  );
  clip-path: polygon(0 100%, 100% 0, 0 0);
  filter: drop-shadow(-3px -3px 5px rgba(0, 0, 0, 0.26));
}
.corner-peel-zone.is-peeling .corner-under-peek,
.corner-peel-zone:focus-visible .corner-under-peek,
.corner-peel-zone.is-peeling .corner-fold-flap,
.corner-peel-zone:focus-visible .corner-fold-flap {
  width: 34px;
  height: 34px;
  opacity: 1;
}

/* ── 底部深色磨砂悬浮控制条 ── */
.viewer-toolbar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px 18px;
  margin: 0 auto 14px;
  padding: 8px 16px;
  border-radius: 999px;
  background: rgba(22, 27, 36, 0.84);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.36);
  font-size: 12px;
  color: rgba(255, 255, 255, 0.86);
}
.toolbar-group { display: flex; align-items: center; gap: 6px; }
.toolbar-label { color: rgba(255, 255, 255, 0.58); }
.page-indicator { min-width: 96px; text-align: center; font-variant-numeric: tabular-nums; }
.zoom-value { min-width: 46px; text-align: center; font-variant-numeric: tabular-nums; }
.jump-input { width: 66px; }
.section-select { width: 108px; }
</style>
