<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 邮件收件箱卡片：专属收件地址展示 + 邮件收取附件的暂存列表。
 *
 * 边界：本组件不解析任何文件——「导入」仅把附件构造成 File 交给父页面的
 * 现有解析向导（emit import），确认入账的权力始终在用户手上。
 * 点击导入即标记 consumed（已处理），重复点击靠业务层去重兜底。
 */
import { computed, onMounted, ref } from 'vue'
import {
  deleteInboxDocument,
  fetchInboxDocuments,
  fetchMailbox,
  markInboxDocumentConsumed,
  resetMailboxAddress,
  type InboxDocumentItem,
  type InboxImportKind,
} from '@/api/mailbox'
import { importKindLabel, inboxDocumentToFile } from '@/utils/mailboxImport'

/**
 * kinds：入口上下文过滤。银行弹窗传 ['bank']、发票抽屉传 ['invoice']——
 * 只显示该类型（及待定类型的）附件，unknown 的「选类型」也只给本入口的类型，
 * 避免在银行弹窗里把文件喂给发票解析器；不传 = 结账向导全量视图。
 */
const props = withDefaults(
  defineProps<{
    kinds?: InboxImportKind[]
    /** 折叠模式：收件箱无附件时默认缩为轻量胶囊，点击展开查看地址；有附件时自动展开 */
    collapsible?: boolean
    /** 隐藏自带的折叠胶囊入口（由父容器自定义放置触发按钮） */
    hideTrigger?: boolean
  }>(),
  {
    collapsible: false,
    hideTrigger: false,
  },
)

const emit = defineEmits<{
  /** kind 已解析（unknown 时先让用户选类型），file 已构造完毕 */
  import: [payload: { kind: InboxImportKind; file: File; document: InboxDocumentItem }]
  error: [message: string]
}>()

const mailbox = ref<{ address: string; enabled: boolean } | null>(null)
const allItems = ref<InboxDocumentItem[]>([])
const loading = ref(false)
/** 后端不可用时的可见提示；不再整卡隐藏，否则环境差异表现为「这功能没了」 */
const loadError = ref('')
const importingId = ref('')
const copied = ref(false)
const chooseKindFor = ref<InboxDocumentItem | null>(null)
const expanded = defineModel<boolean>('expanded', { default: false })

const items = computed(() => {
  if (!props.kinds || props.kinds.length === 0) return allItems.value
  return allItems.value.filter(
    (item) => item.importKind === 'unknown' || props.kinds!.includes(item.importKind),
  )
})

const isCollapsed = computed(() => {
  if (!props.collapsible) return false
  if (loadError.value || items.value.length > 0) return false
  return !expanded.value
})

/** unknown 附件在限定入口下可选的类型（单一入口时只有一个选项） */
const kindChoices = computed<InboxImportKind[]>(() =>
  props.kinds && props.kinds.length > 0 ? props.kinds : ['invoice', 'bank', 'wechat_bill'],
)

async function reload(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    // 两个接口分开判定：地址可用而附件列表失败时，仍展示地址并给出可重试提示，
    // 不再静默隐藏——后端未迁移/中台故障都要让用户看得见（否则只有生产能用）
    const [info, docs] = await Promise.allSettled([fetchMailbox(), fetchInboxDocuments()])
    if (info.status === 'fulfilled') mailbox.value = info.value
    if (docs.status === 'fulfilled') allItems.value = docs.value.items || []
    loadError.value =
      info.status === 'rejected' || docs.status === 'rejected'
        ? '邮箱收单服务暂时不可用，请稍后重试'
        : ''
  } finally {
    if (showLoading) loading.value = false
  }
}

onMounted(reload)

async function copyAddress() {
  if (!mailbox.value?.address) return
  try {
    await navigator.clipboard.writeText(mailbox.value.address)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    emit('error', '复制失败，请手动选中复制')
  }
}

async function onResetAddress() {
  if (!window.confirm('重置后旧收件地址立即失效，已发出的邮件将无法入账。确定重置吗？')) return
  try {
    const info = await resetMailboxAddress()
    mailbox.value = info
  } catch {
    emit('error', '地址重置失败，请稍后重试')
  }
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)}KB`
  return `${bytes}B`
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  return iso.slice(5, 16).replace('T', ' ')
}

async function doImport(doc: InboxDocumentItem, kind: InboxImportKind) {
  chooseKindFor.value = null
  if (importingId.value) return
  importingId.value = doc.id
  try {
    const file = await inboxDocumentToFile(doc)
    emit('import', { kind, file, document: doc })
    if (doc.status === 'pending') {
      await markInboxDocumentConsumed(doc.id)
      doc.status = 'consumed'
    }
  } catch {
    emit('error', `附件「${doc.filename}」拉取失败，请稍后重试`)
  } finally {
    importingId.value = ''
  }
}

function onImportClick(doc: InboxDocumentItem) {
  if (doc.importKind === 'unknown') {
    chooseKindFor.value = doc
    return
  }
  doImport(doc, doc.importKind)
}

async function onDownload(doc: InboxDocumentItem) {
  try {
    const file = await inboxDocumentToFile(doc)
    const url = URL.createObjectURL(file)
    const link = document.createElement('a')
    link.href = url
    link.download = doc.filename
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    emit('error', `附件「${doc.filename}」下载失败`)
  }
}

async function onDelete(doc: InboxDocumentItem) {
  if (!window.confirm(`确定删除收件箱里的「${doc.filename}」吗？`)) return
  try {
    await deleteInboxDocument(doc.id)
    allItems.value = allItems.value.filter((item) => item.id !== doc.id)
  } catch {
    emit('error', '删除失败，请稍后重试')
  }
}

defineExpose({ reload, items, mailbox, isCollapsed, expanded })
</script>

<template>
  <div
    v-if="!hideTrigger || !isCollapsed"
    class="mail-inbox-card"
    :class="{
      'is-collapsible-expanded': collapsible && !isCollapsed,
      'is-collapsible-collapsed': isCollapsed,
    }"
  >
    <!-- 折叠态：仅展示轻量胶囊入口，不占首屏大白卡 -->
    <div v-if="isCollapsed && !hideTrigger" class="inbox-collapsed-bar">
      <button type="button" class="inbox-collapsed-trigger" @click="expanded = true">
        <i class="ri-mail-send-line" aria-hidden="true"></i>
        <span>邮箱免传收单</span>
        <span class="inbox-collapsed-sub">查看专属收件地址</span>
        <i class="ri-arrow-down-s-line" aria-hidden="true"></i>
      </button>
    </div>

    <template v-else>
      <!-- 单行紧凑版式：地址就是这张卡的全部内容，说明只在空态出现一次 -->
      <div class="inbox-row">
        <span class="inbox-head-icon"><i class="ri-mail-line" aria-hidden="true"></i></span>
        <div class="inbox-main">
          <div class="inbox-line">
            <span class="inbox-title">邮箱收单</span>
            <template v-if="mailbox">
              <code class="inbox-address-value">{{ mailbox.address }}</code>
              <button type="button" class="inbox-copy-btn" @click="copyAddress">
                <i :class="copied ? 'ri-check-line' : 'ri-file-copy-line'" aria-hidden="true"></i>
                {{ copied ? '已复制' : '复制' }}
              </button>
              <button type="button" class="inbox-link-btn" @click="onResetAddress">重置</button>
              <span v-if="!mailbox.enabled" class="inbox-hint" title="当前环境未配置服务端 IMAP 轮询环境变量">本地未配 IMAP</span>
              <span v-if="items.length > 0" class="inbox-status">收件箱 {{ items.length }} 份</span>
              <button
                v-if="collapsible && items.length === 0"
                type="button"
                class="inbox-link-btn inbox-collapse-btn"
                @click="expanded = false"
              >
                收起
              </button>
            </template>
            <span v-else-if="loadError" class="inbox-status is-error">服务不可用</span>
          </div>
          <p v-if="mailbox && !loadError && items.length === 0" class="inbox-tip">
            把税局 / 网银 / 微信导出的单据发到上面这个地址，附件会出现在这里，免下载上传。
          </p>
        </div>
      </div>

      <div v-if="loadError" class="inbox-error">
        <i class="ri-error-warning-line" aria-hidden="true"></i>
        <span class="inbox-error-text">{{ loadError }}</span>
        <button type="button" class="inbox-link-btn is-retry" @click="reload()">重试</button>
      </div>

      <ul v-if="items.length > 0" class="inbox-list">
        <li v-for="doc in items" :key="doc.id" class="inbox-item" :class="{ 'is-done': doc.status === 'consumed' }">
          <div class="inbox-item-main">
            <span class="inbox-item-name" :title="doc.filename">
              <i
                class="ri-file-excel-2-line"
                :class="{ 'is-other': !['xlsx', 'xls', 'csv'].includes(doc.fileExt) }"
                aria-hidden="true"
              ></i>
              {{ doc.filename }}
            </span>
            <span class="inbox-item-meta">
              {{ formatSize(doc.sizeBytes) }} · {{ importKindLabel(doc.importKind) }}
              <template v-if="doc.emailFrom"> · {{ doc.emailFrom }}</template>
              <template v-if="doc.receivedAt"> · {{ formatTime(doc.receivedAt) }}</template>
            </span>
            <span v-if="doc.status === 'consumed'" class="inbox-item-note">已处理</span>
            <span v-else-if="doc.note" class="inbox-item-note is-warn" :title="doc.note">{{ doc.note }}</span>
          </div>
          <div class="inbox-item-actions">
            <template v-if="doc.status !== 'unsupported'">
              <button
                type="button"
                class="inbox-link-btn is-primary"
                :disabled="importingId === doc.id"
                @click="onImportClick(doc)"
              >
                {{ importingId === doc.id ? '拉取中…' : '导入' }}
              </button>
            </template>
            <template v-if="chooseKindFor?.id === doc.id">
              <button
                v-for="kind in kindChoices"
                :key="kind"
                type="button"
                class="inbox-link-btn"
                @click="doImport(doc, kind)"
              >
                作为{{ importKindLabel(kind) }}
              </button>
            </template>
            <button type="button" class="inbox-link-btn" @click="onDownload(doc)">下载</button>
            <button type="button" class="inbox-link-btn is-danger" @click="onDelete(doc)">删除</button>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.mail-inbox-card {
  width: 100%;
}
.mail-inbox-card.is-collapsible-expanded {
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  margin-bottom: 12px;
}
.mail-inbox-card.is-collapsible-collapsed {
  margin-bottom: 10px;
}
.inbox-collapsed-bar {
  display: flex;
  justify-content: flex-end;
}
.inbox-collapsed-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.inbox-collapsed-trigger:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}
.inbox-collapsed-sub {
  color: var(--solo-text-muted);
  font-weight: 400;
}
.inbox-collapse-btn {
  margin-left: auto;
}

/* 紧凑单行：图标 + [标题 · 地址 · 复制 · 重置 · 状态]，说明只在空态占一行 */
.inbox-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.inbox-head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: var(--solo-radius-sm, 4px);
  background: var(--solo-primary-glow, rgba(23, 71, 176, 0.08));
  color: var(--solo-primary, #1747b0);
  font-size: 14px;
}
.inbox-main {
  min-width: 0;
  flex: 1;
}
.inbox-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 26px;
}
.inbox-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.inbox-address-value {
  font-family: var(--solo-font-mono, ui-monospace, monospace);
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-primary, #1747b0);
  user-select: all;
  word-break: break-all;
}
.inbox-hint {
  font-size: 12px;
  color: var(--solo-warning, #a05c10);
}
.inbox-status {
  padding: 1px 8px;
  border-radius: var(--solo-radius-pill, 999px);
  background: var(--solo-primary-glow, rgba(23, 71, 176, 0.08));
  color: var(--solo-primary, #1747b0);
  font-size: 12px;
  white-space: nowrap;
}
.inbox-status.is-error {
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
}
/* 空态说明：只在没有附件时出现，替代原先的标题副文案 + 空态卡两处重复 */
.inbox-tip {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--solo-text-muted, #636e7c);
}
.inbox-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border: none;
  border-radius: var(--solo-radius-sm, 4px);
  background: var(--solo-primary, #1747b0);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}
.inbox-copy-btn:hover {
  background: var(--solo-primary-dark, #0d3282);
}

/* 服务不可用：可见、可重试，不再整卡消失 */
.inbox-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(160, 92, 16, 0.22);
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
  font-size: 12.5px;
}
.inbox-error-text {
  min-width: 0;
}
.inbox-error .is-retry {
  margin-left: auto;
  color: var(--solo-warning, #a05c10);
  font-weight: 600;
}

/* 附件列表 */
.inbox-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.inbox-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-card, #fff);
  flex-wrap: wrap;
}
.inbox-item.is-done {
  opacity: 0.6;
}
.inbox-item-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.inbox-item-name {
  font-size: 13px;
  font-weight: 500;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inbox-item-name .is-other {
  opacity: 0.5;
}
.inbox-item-meta {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.inbox-item-note {
  font-size: 12px;
  color: var(--solo-income, #0f7354);
}
.inbox-item-note.is-warn {
  color: var(--solo-warning, #a05c10);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inbox-item-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.inbox-link-btn {
  border: none;
  background: none;
  padding: 4px 6px;
  font-size: 12.5px;
  color: var(--solo-text-secondary, #586474);
  cursor: pointer;
  border-radius: var(--solo-radius-sm, 4px);
  transition: background 0.15s ease, color 0.15s ease;
}
.inbox-link-btn:hover {
  background: rgba(127, 127, 127, 0.12);
  color: var(--solo-text-primary, #182230);
}
.inbox-link-btn.is-primary {
  color: var(--solo-primary, #1747b0);
  font-weight: 600;
}
.inbox-link-btn.is-danger {
  color: var(--solo-expense, #bd3d3d);
}
.inbox-link-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
