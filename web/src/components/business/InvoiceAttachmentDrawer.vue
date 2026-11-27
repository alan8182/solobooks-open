<!--
  Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
-->
<script setup lang="ts">
/**
 * 发票电子原件抽屉：双模式。
 * - invoice 模式（传 invoice）：管理某张发票的附件——补传版式/XML、下载、作废、查验标记；
 * - parked 模式（invoice 传 null）：未匹配原件列表——一键生成凭证草稿、重新匹配、作废。
 *
 * 原件不可变：作废只置状态标记（后端留审计），下载走 sha256 校验（不符 409）。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NEmpty, NInput, NTag } from 'naive-ui'
import { useDialog, useMessage } from 'naive-ui'
import {
  downloadInvoiceAttachment,
  getInvoiceAttachments,
  getParkedAttachmentCount,
  relinkInvoiceAttachments,
  setInvoiceVerify,
  uploadInvoiceAttachments,
  voidInvoiceAttachment,
} from '@/api/invoices'
import type { InvoiceAttachmentItem, InvoiceImportRow, InvoiceLedgerItem } from '@/api/invoices'
import FileDropzone from '@/components/business/FileDropzone.vue'
import OpeningGateAlert from '@/components/business/OpeningGateAlert.vue'
import { SoloDrawer } from '@/components/ui'
import { useAuthStore } from '@/stores/auth'
import { saveBlobFile } from '@/utils/download'
import {
  INVOICE_ATTACHMENT_ACCEPT,
  INVOICE_SUPPLEMENT_ACCEPT,
  attachmentTypeLabel,
  formatAttachmentSize,
  shortSha256,
  summarizeUploadResults,
  verifyStatusLabel,
} from '@/utils/invoiceAttachments'

const props = defineProps<{
  show: boolean
  /** null = parked（未匹配原件）模式 */
  invoice: InvoiceLedgerItem | null
}>()

const emit = defineEmits<{
  'update:show': [val: boolean]
  /** 台账数据有变（挂接/作废/查验），调用方重拉列表 */
  changed: []
  /** parked 模式：一键生成凭证草稿 */
  generate: [rows: InvoiceImportRow[]]
}>()

const message = useMessage()
const dialog = useDialog()
const authStore = (() => {
  try {
    return useAuthStore()
  } catch {
    return null
  }
})()
const openingBlocked = computed(() => Boolean(authStore?.openingBalanceRequired))

const loading = ref(false)
const uploading = ref(false)
const relinking = ref(false)
const attachments = ref<InvoiceAttachmentItem[]>([])
const voidingId = ref('')
const voidReason = ref('')

const isParked = () => props.invoice === null

watch(() => props.show, (val) => {
  if (val) {
    voidReason.value = ''
    voidingId.value = ''
    load()
  }
})

async function load() {
  loading.value = true
  try {
    const result = await getInvoiceAttachments(isParked() ? { parked: true } : { invoice_id: props.invoice!.id })
    attachments.value = result.items
  } catch {
    message.error('加载原件列表失败')
  } finally {
    loading.value = false
  }
}

async function handleFile(file: File) {
  uploading.value = true
  try {
    const results = await uploadInvoiceAttachments([file], isParked() ? undefined : props.invoice!.id)
    const summary = summarizeUploadResults(results)
    if (summary) message.info(summary)
    await load()
    emit('changed')
  } catch (error: unknown) {
    message.error(extractError(error, '上传原件失败'))
  } finally {
    uploading.value = false
  }
}

function extractError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

async function handleDownload(item: InvoiceAttachmentItem) {
  try {
    const blob = await downloadInvoiceAttachment(item.id)
    saveBlobFile(blob, item.fileName)
  } catch (error: unknown) {
    message.error(extractError(error, '下载失败'))
  }
}

function confirmVoid(item: InvoiceAttachmentItem) {
  dialog.warning({
    title: '作废原件',
    content: `确定作废「${item.fileName}」吗？作废只改状态标记，文件保留、审计留痕，不会删除任何记录。`,
    positiveText: '作废',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await voidInvoiceAttachment(item.id, voidingId.value === item.id ? voidReason.value : '')
        message.success('已作废（文件保留，审计已留痕）')
        voidingId.value = ''
        voidReason.value = ''
        await load()
        emit('changed')
      } catch (error: unknown) {
        message.error(extractError(error, '作废失败'))
      }
    },
  })
}

async function toggleVerify() {
  if (!props.invoice) return
  const verified = props.invoice.verifyStatus !== 'verified'
  try {
    await setInvoiceVerify(props.invoice.id, verified)
    message.success(verified ? '已标记为「已在电子税务局查验」' : '已取消查验标记')
    emit('changed')
  } catch (error: unknown) {
    message.error(extractError(error, '标记失败'))
  }
}

function toggleVoiding(item: InvoiceAttachmentItem) {
  if (voidingId.value === item.id) {
    voidingId.value = ''
    voidReason.value = ''
  } else {
    voidingId.value = item.id
    voidReason.value = ''
  }
}

async function runRelink() {
  relinking.value = true
  try {
    const result = await relinkInvoiceAttachments()
    if (result.relinked > 0) {
      message.success(`已重新挂接 ${result.relinked} 份原件`)
      emit('changed')
    } else {
      message.info('没有可挂接的原件（台账里还没有对应发票号）')
    }
    await load()
  } catch (error: unknown) {
    message.error(extractError(error, '重新匹配失败'))
  } finally {
    relinking.value = false
  }
}

function generateDraft(item: InvoiceAttachmentItem) {
  const rows = item.importRows || []
  if (rows.length === 0) {
    message.warning('这份原件没有可用的导入行')
    return
  }
  emit('generate', rows)
  emit('update:show', false)
}

async function refreshParkedCount() {
  try {
    await getParkedAttachmentCount()
  } catch {
    // 仅刷新计数，失败不打扰
  }
}

watch(() => props.show, (val) => {
  if (!val && isParked()) {
    void refreshParkedCount()
  }
})
</script>

<template>
  <SoloDrawer
    :show="show"
    :title="isParked() ? '未匹配的发票原件' : '发票原件'"
    :width="600"
    @update:show="emit('update:show', $event)"
  >
    <div class="attachment-drawer">
      <!-- parked 模式引导 -->
      <div v-if="isParked()" class="mode-tip">
        这些数电票 XML 在台账里还没有对应发票。可以一键生成凭证草稿（走导入预览，确认后才入账），或先作废。
      </div>

      <!-- invoice 模式：查验标记 -->
      <div v-if="!isParked() && invoice" class="verify-bar">
        <span class="verify-text">
          电子税务局查验状态：
          <NTag size="small" :type="invoice.verifyStatus === 'verified' ? 'success' : 'default'" :bordered="false">
            {{ verifyStatusLabel(invoice.verifyStatus) }}
          </NTag>
        </span>
        <NButton size="tiny" secondary @click="toggleVerify">
          {{ invoice.verifyStatus === 'verified' ? '取消标记' : '标记已查验' }}
        </NButton>
      </div>

      <!-- 上传入口 -->
      <OpeningGateAlert v-if="openingBlocked" compact />
      <FileDropzone
        v-else
        :accept="isParked() ? INVOICE_ATTACHMENT_ACCEPT : INVOICE_SUPPLEMENT_ACCEPT"
        :title="isParked() ? '上传数电票 XML / ZIP / PDF / OFD' : '补传原件（XML / PDF / OFD）'"
        subtitle="电子税务局下载的原件自动解析并按发票号挂接；同一文件不可重复归档"
        :loading="uploading"
        @file="handleFile"
        @error="message.error($event)"
      />

      <!-- parked 模式工具条 -->
      <div v-if="isParked() && attachments.length > 0" class="toolbar">
        <NButton size="small" secondary :loading="relinking" @click="runRelink">重新匹配</NButton>
      </div>

      <!-- 列表 -->
      <div v-if="attachments.length > 0" class="attachment-list">
        <div v-for="item in attachments" :key="item.id" class="attachment-item" :class="{ void: item.status === 'void' }">
          <div class="item-main">
            <div class="item-title">
              <NTag size="tiny" :bordered="false" type="info">{{ attachmentTypeLabel(item.documentType) }}</NTag>
              <span v-if="item.documentType === 'einvoice_xml' && item.parsedSummary" class="sig-ok">已签名</span>
              <span class="file-name" :title="item.fileName">{{ item.fileName }}</span>
            </div>
            <div class="item-meta">
              {{ formatAttachmentSize(item.sizeBytes) }} · sha256 {{ shortSha256(item.contentSha256) }} · {{ item.createdAt.slice(0, 19).replace('T', ' ') }}
            </div>
            <div v-if="item.parsedSummary" class="item-meta">
              开票 {{ item.parsedSummary.issueTime || '—' }} · 价税合计 ¥{{ item.parsedSummary.amountIncl }}
              <template v-if="item.parsedSummary.withholdingNotice"> · 需代扣代缴个税</template>
            </div>
            <div v-for="warning in item.parsedSummary?.warnings || []" :key="warning" class="item-warning">{{ warning }}</div>
            <div v-if="item.status === 'void'" class="item-void-note">已作废{{ item.voidReason ? `：${item.voidReason}` : '' }}（文件保留）</div>
            <div v-if="voidingId === item.id" class="void-reason-row">
              <NInput v-model:value="voidReason" size="tiny" placeholder="作废原因（可留空）" />
            </div>
          </div>
          <div class="item-actions">
            <template v-if="item.status === 'active'">
              <NButton size="tiny" quaternary type="primary" @click="handleDownload(item)">下载</NButton>
              <NButton
                v-if="isParked() && item.importRows && item.importRows.length > 0"
                size="tiny"
                type="primary"
                @click="generateDraft(item)"
              >
                生成凭证草稿
              </NButton>
              <NButton size="tiny" quaternary type="error" @click="toggleVoiding(item)">
                作废
              </NButton>
              <NButton v-if="voidingId === item.id" size="tiny" type="error" @click="confirmVoid(item)">确认作废</NButton>
            </template>
            <NButton v-else size="tiny" quaternary @click="handleDownload(item)">下载留档件</NButton>
          </div>
        </div>
      </div>
      <NEmpty v-else-if="!loading" :description="isParked() ? '没有未匹配的原件，所有原件都已挂接到发票' : '还没有归档任何原件'" class="empty-state" />
    </div>
  </SoloDrawer>
</template>

<style scoped>
.attachment-drawer { display: flex; flex-direction: column; gap: 12px; }
.mode-tip {
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
  padding: 8px 12px;
  background: var(--solo-surface-soft);
  border-radius: 8px;
}
.verify-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.verify-text { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.toolbar { display: flex; justify-content: flex-end; }
.attachment-list { display: flex; flex-direction: column; gap: 8px; }
.attachment-item {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-surface);
}
.attachment-item.void { opacity: 0.55; }
.item-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.item-title { display: flex; align-items: center; gap: 6px; min-width: 0; }
.file-name {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sig-ok { font-size: 11px; color: var(--solo-income); }
.item-meta { font-size: 12px; color: var(--solo-text-muted); font-variant-numeric: tabular-nums; }
.item-warning { font-size: 12px; color: var(--solo-warning); }
.item-void-note { font-size: 12px; color: var(--solo-text-muted); }
.void-reason-row { margin-top: 4px; }
.item-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex: 0 0 auto; }
.empty-state { margin: 24px 0; }
</style>
