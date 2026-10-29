<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * Solo Audit Share 老板端：协审链接管理 + CPA 批注回流处理。
 *
 * 生成 → 复制链接私信发给 CPA → CPA 免注册批注 → 这里逐条处理（已知悉/已解决/忽略）。
 * 责任边界：批注是咨询参考；"查看凭证"只做跳转，采纳建议后的调整仍走草稿-确认流程。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NEmpty, NModal, NSelect, NSwitch, NTag, useMessage,
} from 'naive-ui'
import {
  createAuditShare, getAuditShare, listAuditShares, previewAuditShare,
  revokeAuditShare, updateShareAnnotationStatus,
  type OwnerShareOverview, type SharePreview,
} from '@/api/auditShare'
import { getCurrentMonth } from '@/utils/format'
import { SoloMonthPicker } from '@/components/ui'

const router = useRouter()
const message = useMessage()
const origin = window.location.origin

const shares = ref<OwnerShareOverview[]>([])
const loading = ref(false)

// 生成弹窗
const createVisible = ref(false)
const createMonth = ref<string>(getCurrentMonth())
const createAnonymized = ref(true)
const createExpiry = ref(30)
const creating = ref(false)
const preview = ref<SharePreview | null>(null)
const previewing = ref(false)
const createdShare = ref<OwnerShareOverview | null>(null)

const expiryOptions = [
  { label: '30 天有效（推荐）', value: 30 },
  { label: '7 天有效', value: 7 },
  { label: '90 天有效', value: 90 },
  { label: '永久有效（不推荐）', value: 0 },
]

const monthText = computed(() => createMonth.value || getCurrentMonth())

// 批注收件箱
const inboxShare = ref<OwnerShareOverview | null>(null)
const inboxVisible = ref(false)
const inboxLoading = ref(false)
const resolutionNotes = ref<Record<string, string>>({})

const stateTag: Record<string, { type: 'success' | 'error' | 'warning'; label: string }> = {
  active: { type: 'success', label: '生效中' },
  revoked: { type: 'error', label: '已撤销' },
  expired: { type: 'warning', label: '已过期' },
}

async function load() {
  loading.value = true
  try {
    shares.value = await listAuditShares()
  } catch {
    message.error('协审链接列表加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createdShare.value = null
  preview.value = null
  createVisible.value = true
}

async function runPreview() {
  previewing.value = true
  try {
    preview.value = await previewAuditShare({ month: monthText.value, anonymized: createAnonymized.value })
  } catch {
    message.error('生成预览失败，请稍后重试')
  } finally {
    previewing.value = false
  }
}

async function submitCreate() {
  if (creating.value) return
  creating.value = true
  try {
    createdShare.value = await createAuditShare({
      month: monthText.value,
      anonymized: createAnonymized.value,
      expiresInDays: createExpiry.value,
    })
    message.success('协审链接已生成')
    await load()
  } catch (error: unknown) {
    message.error((error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '生成失败')
  } finally {
    creating.value = false
  }
}

async function copyLink(share: OwnerShareOverview) {
  if (!share.token) return
  const url = `${window.location.origin}/share/${share.token}`
  try {
    await navigator.clipboard.writeText(url)
    message.success('链接已复制，请私信发送给会计师')
  } catch {
    message.info(`请手动复制：${url}`)
  }
}

async function revoke(share: OwnerShareOverview) {
  try {
    await revokeAuditShare(share.id)
    message.success('链接已撤销，立即失效')
    await load()
  } catch {
    message.error('撤销失败')
  }
}

async function openInbox(share: OwnerShareOverview) {
  inboxVisible.value = true
  inboxLoading.value = true
  try {
    inboxShare.value = await getAuditShare(share.id)
  } catch {
    message.error('批注加载失败')
  } finally {
    inboxLoading.value = false
  }
}

async function handleAnnotation(annotationId: string, status: string) {
  if (!inboxShare.value) return
  try {
    await updateShareAnnotationStatus(inboxShare.value.id, annotationId, {
      status,
      resolutionNote: resolutionNotes.value[annotationId] || undefined,
    })
    const row = inboxShare.value.annotations?.find((a) => a.id === annotationId)
    if (row) {
      row.status = status
      row.resolutionNote = resolutionNotes.value[annotationId] || null
    }
    message.success('已处理')
  } catch {
    message.error('处理失败，请重试')
  }
}

function gotoEntry(anchorId?: string | null) {
  if (anchorId) router.push(`/ledger/${anchorId}`)
}

onMounted(load)
</script>

<template>
  <div class="audit-share-page">
    <div class="page-header">
      <div>
        <h1>专家协审（Audit Share）</h1>
        <p class="muted">
          生成免注册只读链接，会计师打开即可查看当月账套体检报告并批注；批注意见回流到这里处理。
        </p>
      </div>
      <NButton type="primary" @click="openCreate">生成协审链接</NButton>
    </div>

    <NCard :bordered="false" class="mb-4">
      <div v-if="loading" class="muted">加载中…</div>
      <NEmpty v-else-if="!shares.length" description="还没有协审链接。完成一次月结后，生成链接发给你的会计师做账套体检。" />
      <div v-else class="share-list">
        <div v-for="share in shares" :key="share.id" class="share-row">
          <div class="share-row-main">
            <span class="share-month">{{ share.month }}</span>
            <NTag size="small" :bordered="false" :type="stateTag[share.state]?.type || 'default'">
              {{ stateTag[share.state]?.label || share.state }}
            </NTag>
            <NTag v-if="share.anonymized" size="tiny" :bordered="false">已脱敏</NTag>
            <span class="muted">查看 {{ share.viewCount }} 次 · 批注 {{ share.annotationCount }} 条</span>
          </div>
          <div class="share-row-meta muted">
            生成于 {{ share.createdAt?.slice(0, 10) || '—' }}
            <template v-if="share.expiresAt">；有效期至 {{ share.expiresAt.slice(0, 10) }}</template>
          </div>
          <div class="share-row-actions">
            <NButton size="small" secondary type="primary" @click="openInbox(share)">批注处理（{{ share.annotationCount }}）</NButton>
            <NButton v-if="share.state === 'active'" size="small" quaternary type="error" @click="revoke(share)">撤销</NButton>
          </div>
        </div>
      </div>
    </NCard>

    <!-- 生成弹窗 -->
    <NModal v-model:show="createVisible" preset="card" title="生成协审链接" style="max-width: 560px">
      <template v-if="!createdShare">
        <div class="form-row">
          <span class="form-label">体检月份</span>
          <SoloMonthPicker v-model:value="createMonth" clearable :is-date-disabled="(ts: number) => ts > Date.now()" style="width: 160px" />
        </div>
        <div class="form-row">
          <span class="form-label">脱敏身份信息</span>
          <div class="form-control">
            <NSwitch v-model:value="createAnonymized" size="small" />
            <span class="muted">{{ createAnonymized ? '往来单位/人名/手机号银行卡号自动打码，金额保留' : '不脱敏（仅建议对方是签了保密义务的会计师时关闭）' }}</span>
          </div>
        </div>
        <div class="form-row">
          <span class="form-label">有效期</span>
          <NSelect v-model:value="createExpiry" :options="expiryOptions" style="width: 220px" />
        </div>
        <div class="preview-block">
          <NButton size="small" :loading="previewing" @click="runPreview">预览脱敏效果</NButton>
          <div v-if="preview" class="preview-body">
            <div class="preview-stats">
              五项检查 {{ preview.checkCount }} 项（阻塞 {{ preview.blockingCount }}）·
              异常 {{ preview.anomalyCount }} 条 · 收入 {{ preview.incomeSummary.revenue }} /
              费用 {{ preview.incomeSummary.expense }} / 利润 {{ preview.incomeSummary.profit }}
            </div>
            <ul class="preview-list">
              <li v-for="(row, i) in preview.sampleAnomalies" :key="i">
                <NTag size="tiny" :bordered="false" :type="row.level === 'high' ? 'error' : 'warning'">{{ row.level }}</NTag>
                {{ row.title }}
              </li>
            </ul>
          </div>
        </div>
        <div class="create-actions">
          <NButton type="primary" :loading="creating" @click="submitCreate">生成链接</NButton>
        </div>
      </template>
      <template v-else>
        <div class="created-body">
          <p class="created-warn">
            链接包含脱敏后的账套数据，请通过微信/短信私信发送，不要发到群里。此链接只显示这一次，丢失后需重新生成。
          </p>
          <code class="created-link">{{ `${origin}/share/${createdShare.token}` }}</code>
          <div class="created-actions">
            <NButton type="primary" @click="copyLink(createdShare)">复制链接</NButton>
            <NButton quaternary @click="createVisible = false">完成</NButton>
          </div>
        </div>
      </template>
    </NModal>

    <!-- 批注收件箱 -->
    <NModal v-model:show="inboxVisible" preset="card" :title="`批注处理 · ${inboxShare?.month || ''}`" style="max-width: 640px">
      <div v-if="inboxLoading" class="muted">加载中…</div>
      <NEmpty v-else-if="!inboxShare?.annotations?.length" description="会计师还没有提交批注。" />
      <div v-else class="inbox-list">
        <div v-for="ann in inboxShare.annotations" :key="ann.id" class="inbox-row" :data-status="ann.status">
          <div class="inbox-head">
            <strong>{{ ann.authorName }}</strong>
            <span class="muted">{{ ann.createdAt?.replace('T', ' ').slice(0, 16) }}</span>
            <NTag size="tiny" :bordered="false" :type="ann.status === 'open' ? 'warning' : 'success'">
              {{ ann.status === 'open' ? '待处理' : ann.status === 'resolved' ? '已解决' : ann.status === 'acknowledged' ? '已知悉' : '已忽略' }}
            </NTag>
            <NButton v-if="ann.anchorType === 'entry' && ann.anchorId" size="tiny" quaternary type="primary" @click="gotoEntry(ann.anchorId)">
              查看凭证
            </NButton>
          </div>
          <p class="inbox-body">{{ ann.body }}</p>
          <template v-if="ann.status === 'open'">
            <input v-model="resolutionNotes[ann.id]" class="note-input" placeholder="处理说明（可选）" maxlength="200" />
            <div class="inbox-actions">
              <NButton size="tiny" type="primary" @click="handleAnnotation(ann.id, 'resolved')">已解决</NButton>
              <NButton size="tiny" secondary @click="handleAnnotation(ann.id, 'acknowledged')">已知悉</NButton>
              <NButton size="tiny" quaternary @click="handleAnnotation(ann.id, 'dismissed')">忽略</NButton>
            </div>
          </template>
          <p v-else-if="ann.resolutionNote" class="muted inbox-note">处理说明：{{ ann.resolutionNote }}</p>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.audit-share-page { max-width: 860px; margin: 0 auto; padding: 20px 16px 48px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
.page-header h1 { font-size: 20px; margin: 0 0 6px; }
.share-row { border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }
.share-row-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.share-month { font-weight: 600; }
.share-row-meta { margin-top: 4px; font-size: 12px; }
.share-row-actions { margin-top: 8px; display: flex; gap: 8px; justify-content: flex-end; }
.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.form-label { width: 84px; flex-shrink: 0; font-size: 14px; }
.form-control { display: flex; align-items: center; gap: 8px; flex: 1; }
.preview-block { margin: 6px 0 14px; }
.preview-body { margin-top: 10px; border: 1px dashed var(--border-2, #ddd); border-radius: 8px; padding: 10px 12px; }
.preview-stats { font-size: 13px; margin-bottom: 6px; }
.preview-list { margin: 0; padding-left: 18px; font-size: 13px; }
.preview-list li { margin: 4px 0; }
.create-actions { text-align: right; }
.created-warn { font-size: 13px; color: var(--warning-5, #9a6134); background: var(--warning-1, #fdf6ec); padding: 8px 10px; border-radius: 6px; }
.created-link { display: block; margin: 12px 0; padding: 10px; background: var(--fill-1, #f5f5f5); border-radius: 6px; font-size: 12px; word-break: break-all; }
.created-actions { display: flex; gap: 8px; justify-content: flex-end; }
.inbox-row { border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.inbox-row[data-status='open'] { border-left: 3px solid var(--warning-5, #c2872e); }
.inbox-head { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.inbox-body { margin: 8px 0; font-size: 14px; white-space: pre-wrap; }
.note-input { width: 100%; border: 1px solid var(--border-2, #ddd); border-radius: 6px; padding: 6px 10px; font-size: 13px; margin-bottom: 8px; }
.inbox-actions { display: flex; gap: 8px; justify-content: flex-end; }
.inbox-note { margin: 6px 0 0; }
.muted { color: var(--text-3, #888); font-size: 13px; }
.mb-4 { margin-bottom: 16px; }
</style>
