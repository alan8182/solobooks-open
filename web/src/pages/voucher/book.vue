<!-- Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 凭证册装订（Phase 2 单据整理）：按月把已过账凭证装订成带封面的 PDF。
 * 签发定版语义与电子账簿一致：关账后才能签发、册号递增、作废留痕、sha256 归档。
 * 屏幕预览只在有签发件时展示冻结 payload（与签发时逐字一致）；
 * 未签发月份用「下载预览件 PDF」看完整版式。
 */
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NInput, NModal, NTag, useMessage } from 'naive-ui'
import {
  downloadVoucherBookPdf,
  downloadVoucherBookSnapshotPdf,
  getVoucherBookPeriodStatus,
  getVoucherBookSnapshot,
  issueVoucherBookSnapshot,
  listVoucherBookSnapshots,
  getVoucherBookPreview,
  voidVoucherBookSnapshot,
  type VoucherBook,
  type VoucherBookSnapshotSummary,
} from '@/api/voucherBook'
import { saveBlobFile } from '@/utils/download'
import { accountingErrorText } from '@/utils/errors'
import { reportErrorToast } from '@/utils/errorPresenter'
import {
  BOOK_CANVAS_HEIGHT,
  BOOK_CANVAS_WIDTH,
  canIssueVoucherBook,
  flattenVoucherBookPages,
  voucherBookFileName,
  voucherBookTotalPages,
} from '@/utils/voucherBook'
import VoucherBookSheet from '@/components/business/VoucherBookSheet.vue'
import VoucherBookViewer from '@/components/business/VoucherBookViewer.vue'
import { SoloEmpty, SoloMonthPicker } from '@/components/ui'
import { getCurrentMonth } from '@/utils/format'

const route = useRoute()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const loading = ref(false)
const issuing = ref(false)
const downloadingPdf = ref(false)
const downloadingSnapshotId = ref('')

const periodStatus = ref<string>('open')
const issuedList = ref<VoucherBookSnapshotSummary[]>([])
/** 屏幕预览：最新已签发册的冻结 payload */
const previewPayload = ref<VoucherBook | null>(null)
const previewBookNo = ref('')
const previewStatus = ref<string>('issued')
const previewLoading = ref(false)

const latestIssued = computed(() => issuedList.value.find((item) => item.status === 'issued') || null)
const canIssue = computed(() => canIssueVoucherBook(periodStatus.value, latestIssued.value))
const latestAny = computed(() => issuedList.value[0] || null)
const summary = computed(() => {
  if (!previewPayload.value) return null
  return {
    voucherCount: previewPayload.value.meta.voucherCount,
    attachmentTotal: previewPayload.value.meta.attachmentTotal,
    xmlOriginalTotal: previewPayload.value.meta.xmlOriginalTotal,
    debitTotal: previewPayload.value.meta.debitTotal,
    missingCount: previewPayload.value.originalsMissing.length,
  }
})

async function load() {
  loading.value = true
  try {
    const [{ items }, status] = await Promise.all([
      listVoucherBookSnapshots(month.value),
      getVoucherBookPeriodStatus(month.value),
    ])
    issuedList.value = items
    periodStatus.value = status.periodStatus
    await loadPreview()
  } catch (error: unknown) {
    reportErrorToast(message, error, '加载凭证册失败')
  } finally {
    loading.value = false
  }
}

async function loadPreview() {
  previewPayload.value = null
  previewBookNo.value = ''
  previewStatus.value = 'issued'
  const target = latestIssued.value
  if (!target) return
  previewLoading.value = true
  try {
    const detail = await getVoucherBookSnapshot(target.id)
    previewPayload.value = detail.payload
    previewBookNo.value = detail.bookNo
    previewStatus.value = detail.status
  } catch (error: unknown) {
    reportErrorToast(message, error, '加载已签发凭证册失败')
  } finally {
    previewLoading.value = false
  }
}

async function downloadPreview() {
  downloadingPdf.value = true
  try {
    const blob = await downloadVoucherBookPdf(month.value)
    saveBlobFile(blob, voucherBookFileName(month.value, '预览件'))
  } catch (error: unknown) {
    message.error(accountingErrorText(error, '生成预览件失败'))
  } finally {
    downloadingPdf.value = false
  }
}

async function downloadSnapshot(item: VoucherBookSnapshotSummary) {
  downloadingSnapshotId.value = item.id
  try {
    const blob = await downloadVoucherBookSnapshotPdf(item.id)
    saveBlobFile(blob, voucherBookFileName(item.period, '已签发', item.bookNo))
  } catch (error: unknown) {
    message.error(accountingErrorText(error, '下载失败'))
  } finally {
    downloadingSnapshotId.value = ''
  }
}

async function issue() {
  issuing.value = true
  try {
    const result = await issueVoucherBookSnapshot(month.value)
    message.success(`已签发凭证册 ${result.bookNo}（${result.pageCount} 页）`)
    await load()
  } catch (error: unknown) {
    message.error(accountingErrorText(error, '签发失败'))
  } finally {
    issuing.value = false
  }
}

const voidTarget = ref<VoucherBookSnapshotSummary | null>(null)
const voidReason = ref('')
const showVoid = ref(false)
const voiding = ref(false)

function openVoid(item: VoucherBookSnapshotSummary) {
  voidTarget.value = item
  voidReason.value = ''
  showVoid.value = true
}

async function confirmVoid() {
  if (!voidTarget.value) return
  voiding.value = true
  try {
    await voidVoucherBookSnapshot(voidTarget.value.id, voidReason.value.trim())
    message.success('已作废（册与归档件保留，审计已留痕）')
    showVoid.value = false
    await load()
  } catch (error: unknown) {
    message.error(accountingErrorText(error, '作废失败'))
  } finally {
    voiding.value = false
  }
}

function shaShort(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : ''
}

/* ── 已签发册纸质预览（单页 + 翻页，等比缩放锁定 A4 横版比例） ── */
const previewPageIndex = ref(0)
const previewPages = computed(() => (previewPayload.value ? flattenVoucherBookPages(previewPayload.value) : []))
const previewTotalPages = computed(() => (previewPayload.value ? voucherBookTotalPages(previewPayload.value) : 0))
const inlinePaperRef = ref<HTMLElement | null>(null)
const inlineScale = ref(1)
const inlineCanvasStyle = computed(() => ({
  width: `${BOOK_CANVAS_WIDTH}px`,
  height: `${BOOK_CANVAS_HEIGHT}px`,
  transform: `scale(${inlineScale.value})`,
}))
let inlineObserver: ResizeObserver | null = null

function updateInlineScale() {
  const width = inlinePaperRef.value?.clientWidth
  if (width && width > 0) {
    inlineScale.value = width / BOOK_CANVAS_WIDTH
  }
}

watch(inlinePaperRef, (el) => {
  inlineObserver?.disconnect()
  inlineObserver = null
  if (!el) return
  updateInlineScale()
  if (typeof ResizeObserver !== 'undefined') {
    inlineObserver = new ResizeObserver(updateInlineScale)
    inlineObserver.observe(el)
  }
})

onBeforeUnmount(() => {
  inlineObserver?.disconnect()
  inlineObserver = null
})

watch(previewPayload, () => {
  previewPageIndex.value = 0
})

/* ── 仿真册阅读器 ── */
const showViewer = ref(false)
const viewerPayload = ref<VoucherBook | null>(null)
const viewerSigned = ref(false)
const viewerBookNo = ref('')
const viewerLoading = ref(false)

async function openViewer() {
  if (previewPayload.value) {
    viewerPayload.value = previewPayload.value
    viewerSigned.value = true
    viewerBookNo.value = previewBookNo.value
    showViewer.value = true
    return
  }
  viewerLoading.value = true
  try {
    const preview = await getVoucherBookPreview(month.value)
    viewerPayload.value = preview.payload
    viewerSigned.value = Boolean(preview.signed)
    viewerBookNo.value = preview.bookNo || ''
    showViewer.value = true
  } catch (error: unknown) {
    message.error(accountingErrorText(error, '加载仿真册失败'))
  } finally {
    viewerLoading.value = false
  }
}

watch(month, load)
usePageRefresh(load)
onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <div class="report-title-section">
          <h2>凭证册装订</h2>
          <p class="report-subtitle">按月把已过账凭证装订成带封面的 PDF：封面要素 + 连续凭证 + 原件归档目录，签发后定版可追溯</p>
        </div>
      </div>
      <div class="report-header-right">
        <SoloMonthPicker v-model:value="month" style="width: 140px;" />
        <NButton secondary :loading="downloadingPdf" @click="downloadPreview">下载预览件 PDF</NButton>
        <NButton type="primary" :disabled="!canIssue" :loading="issuing" @click="issue">签发正式凭证册</NButton>
      </div>
    </div>

    <!-- 签发状态条 -->
    <div class="issue-bar" :class="{ warning: periodStatus !== 'closed' }">
      <template v-if="latestAny">
        <NTag size="small" :type="latestAny.status === 'issued' ? 'success' : 'default'" :bordered="false">
          {{ latestAny.status === 'issued' ? '已签发' : '已作废' }}
        </NTag>
        <span class="issue-meta">最新册 {{ latestAny.bookNo }} · {{ latestAny.pageCount }} 页 · 校验码 {{ shaShort(latestAny.contentHash) }}</span>
      </template>
      <template v-else>
        <span class="issue-meta">本期间还没有签发过凭证册；预览件可随时生成，签发需要期间已结账。</span>
      </template>
      <span v-if="periodStatus !== 'closed'" class="issue-hint">期间未结账，签发不可用</span>
    </div>

    <!-- 已签发列表 -->
    <div v-if="issuedList.length > 0" class="history-card">
      <div class="history-title">本期间签发记录（{{ issuedList.length }}）</div>
      <div v-for="item in issuedList" :key="item.id" class="history-row">
        <div class="history-main">
          <span class="history-book-no">{{ item.bookNo }}</span>
          <NTag v-if="item.status === 'void'" size="tiny" :bordered="false">已作废</NTag>
          <span class="history-meta">{{ item.generatedAt?.slice(0, 19).replace('T', ' ') }} · {{ item.pageCount }} 页 · sha256 {{ shaShort(item.contentHash) }}</span>
          <span v-if="item.status === 'void'" class="history-void">作废原因：{{ item.voidReason }}</span>
        </div>
        <div class="history-actions">
          <NButton size="tiny" quaternary type="primary" :loading="downloadingSnapshotId === item.id" @click="downloadSnapshot(item)">下载 PDF</NButton>
          <NButton v-if="item.status === 'issued'" size="tiny" quaternary type="error" @click="openVoid(item)">作废</NButton>
        </div>
      </div>
    </div>

    <!-- 屏幕预览：最新已签发册（纸质仿真单页 + 翻页） -->
    <div v-if="previewPayload" class="preview-section">
      <div class="preview-title">已签发册预览（{{ previewBookNo }}）</div>
      <div class="preview-summary">
        <span>凭证 {{ summary?.voucherCount }} 张</span>
        <span>原件 {{ summary?.attachmentTotal }} 份（XML {{ summary?.xmlOriginalTotal }}）</span>
        <span>借贷合计 ¥{{ summary?.debitTotal }}</span>
        <span v-if="summary && summary.missingCount > 0" class="missing-hint">⚠ {{ summary.missingCount }} 张凭证暂无原件</span>
      </div>
      <div class="paper-embedded">
        <div
          ref="inlinePaperRef"
          class="paper-page"
          :class="{ 'is-cover': previewPages[previewPageIndex]?.kind === 'cover' }"
          role="button"
          tabindex="0"
          aria-label="点击进入全屏阅读"
          @click="openViewer"
          @keydown.enter.prevent="openViewer"
          @keydown.space.prevent="openViewer"
        >
          <div class="paper-fullscreen-hint" aria-hidden="true">
            <i class="ri-fullscreen-line" />
            <span>点击全屏阅读</span>
          </div>
          <div
            class="paper-canvas"
            :class="{ 'is-cover': previewPages[previewPageIndex]?.kind === 'cover' }"
            :style="inlineCanvasStyle"
          >
            <div
              v-if="previewPages[previewPageIndex]?.kind === 'cover'"
              class="paper-spine-strip"
              aria-hidden="true"
            />
            <div v-else class="paper-binding-crease" aria-hidden="true" />
            <VoucherBookSheet
              v-if="previewPages[previewPageIndex]"
              :page="previewPages[previewPageIndex]"
              :payload="previewPayload"
              :total-pages="previewTotalPages"
              :signed="previewStatus === 'issued'"
            />
          </div>
        </div>
        <div class="paper-nav">
          <div class="paper-nav-left">
            <NButton size="small" quaternary :disabled="previewPageIndex === 0" @click="previewPageIndex -= 1">‹ 上一页</NButton>
            <span class="paper-page-indicator">第 {{ previewPageIndex + 1 }} / {{ previewTotalPages }} 页</span>
            <NButton
              size="small"
              quaternary
              :disabled="previewPageIndex >= previewTotalPages - 1"
              @click="previewPageIndex += 1"
            >下一页 ›</NButton>
            <NButton size="small" quaternary :disabled="previewPageIndex === 0" @click="previewPageIndex = 0">← 返回封面</NButton>
          </div>
          <NButton
            size="small"
            secondary
            data-testid="open-book-viewer"
            :loading="viewerLoading"
            @click="openViewer"
          >
            <template #icon><i class="ri-fullscreen-line" /></template>
            全屏阅读
          </NButton>
        </div>
      </div>
    </div>
    <div v-else-if="!loading" class="preview-section">
      <SoloEmpty
        title="本期间还没有签发凭证册"
        description="先下载预览件 PDF 查看完整版式（封面 + 连续凭证 + 原件目录）；期间结账后可签发定版。"
        icon="ri-booklet-line"
      />
      <div class="empty-fullscreen-action">
        <NButton secondary :loading="viewerLoading" @click="openViewer">
          <template #icon><i class="ri-fullscreen-line" /></template>
          全屏仿真预览
        </NButton>
      </div>
    </div>

    <!-- 仿真册阅读器（全屏翻页） -->
    <VoucherBookViewer
      v-model:show="showViewer"
      :payload="viewerPayload"
      :signed="viewerSigned"
      :book-no="viewerBookNo"
    />

    <NModal v-model:show="showVoid" preset="dialog" title="作废凭证册" :show-icon="false">
      <div class="void-body">
        <p>确定作废 <strong>{{ voidTarget?.bookNo }}</strong> 吗？作废只改状态标记，册与归档件保留、审计留痕。</p>
        <NInput v-model:value="voidReason" type="textarea" placeholder="作废原因（必填）" :rows="2" />
      </div>
      <template #action>
        <NButton quaternary @click="showVoid = false">取消</NButton>
        <NButton type="error" :disabled="!voidReason.trim()" :loading="voiding" @click="confirmVoid">作废</NButton>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; align-items: center; }
.issue-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 10px 16px;
  background: var(--solo-surface);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.issue-bar.warning { background: var(--solo-warning-bg); }
.issue-meta { font-size: 13px; color: var(--solo-text-primary); }
.issue-hint { margin-left: auto; font-size: 12px; color: var(--solo-warning); }
.history-card {
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--solo-surface);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.history-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.history-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid var(--solo-border-light);
}
.history-main { display: flex; align-items: center; gap: 10px; min-width: 0; flex-wrap: wrap; }
.history-book-no { font-weight: 600; font-variant-numeric: tabular-nums; }
.history-meta { font-size: 12px; color: var(--solo-text-muted); font-variant-numeric: tabular-nums; }
.history-void { font-size: 12px; color: var(--solo-text-muted); }
.history-actions { display: flex; gap: 4px; flex: 0 0 auto; }
.preview-section {
  background: var(--solo-surface);
  border: 1px solid var(--solo-border);
  border-radius: 12px;
  padding: 16px;
}
.preview-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.paper-embedded { margin-top: 14px; }
.paper-page {
  position: relative;
  width: 100%;
  aspect-ratio: 297 / 210;
  background-color: var(--solo-paper);
  background-image:
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='grain-p'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23grain-p)' opacity='0.032'/%3E%3C/svg%3E");
  border-radius: 2px 4px 4px 2px;
  box-shadow:
    4px 4px 0 var(--solo-paper-edge),
    5px 5px 0 rgba(63, 52, 42, 0.14),
    var(--solo-shadow-lg),
    inset 0 0 0 1px rgba(63, 52, 42, 0.12);
  overflow: hidden;
  padding: 0;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.paper-page:hover,
.paper-page:focus-visible {
  transform: translateY(-2px);
  outline: none;
}
.paper-fullscreen-hint {
  position: absolute;
  top: 14px;
  right: 16px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(30, 36, 45, 0.78);
  color: rgba(255, 255, 255, 0.92);
  font-size: 12px;
  pointer-events: none;
  opacity: 0;
  transform: translateY(-2px);
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.paper-page:hover .paper-fullscreen-hint,
.paper-page:focus-visible .paper-fullscreen-hint {
  opacity: 1;
  transform: translateY(0);
}
.paper-page.is-cover {
  background-color: var(--solo-paper-cover);
}
.paper-canvas {
  position: relative;
  transform-origin: 0 0;
  padding: 24px 36px 20px 64px;
}
.paper-canvas.is-cover {
  padding-left: 66px;
}
.paper-spine-strip {
  position: absolute;
  inset: 0 auto 0 0;
  width: 30px;
  pointer-events: none;
  background:
    linear-gradient(90deg, rgba(0, 0, 0, 0.24), transparent 35%, rgba(0, 0, 0, 0.28) 100%),
    var(--solo-paper-cover-spine);
}
.paper-binding-crease {
  position: absolute;
  inset: 0 auto 0 0;
  width: 38px;
  pointer-events: none;
  background: linear-gradient(90deg, rgba(24, 34, 48, 0.16), rgba(24, 34, 48, 0.04) 35%, transparent 100%);
}
.paper-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 12px;
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.paper-nav-left { display: flex; align-items: center; gap: 14px; }
.empty-fullscreen-action { display: flex; justify-content: center; margin-top: 4px; }
.paper-page-indicator { min-width: 88px; text-align: center; font-variant-numeric: tabular-nums; }
.preview-summary {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: var(--solo-text-muted);
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.missing-hint { color: var(--solo-warning); }
.file-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: monospace; font-variant-numeric: tabular-nums; }
.missing-list { font-size: 12px; color: var(--solo-warning); margin-bottom: 12px; }
/* 方案 A：XML 原件复现卡（与 PDF 复现页同源同声明） */
.void-body { display: flex; flex-direction: column; gap: 10px; }
</style>
