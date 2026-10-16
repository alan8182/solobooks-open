<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NModal,
  NPopconfirm,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  fetchAdminAgentActions,
  fetchAgentActionDetail,
  markAgentAction,
  type AdminAgentActionDetail,
  type AdminAgentActionItem,
  type AgentActionStatus,
} from '@/api/admin'

const message = useMessage()
const loading = ref(false)
const status = ref<'all' | AgentActionStatus>('all')
const items = ref<AdminAgentActionItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const detailVisible = ref(false)
const detail = ref<AdminAgentActionDetail | null>(null)
const detailLoading = ref(false)

const STATUS_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '待确认', value: 'pending' },
  { label: '已执行', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '已过期', value: 'expired' },
]

const STATUS_TAGS: Record<AgentActionStatus, { label: string; type: 'warning' | 'success' | 'error' | 'default' }> = {
  pending: { label: '待确认', type: 'warning' },
  approved: { label: '已执行', type: 'success' },
  rejected: { label: '已驳回', type: 'error' },
  expired: { label: '已过期', type: 'default' },
}

const ACTION_LABELS: Record<string, string> = {
  propose_voucher: '记账建议',
  propose_vouchers: '批量记账',
  propose_match: '对账建议',
  propose_accrual: '计提建议',
}

function actionLabel(type: string): string {
  return ACTION_LABELS[type] ?? type
}

function fmtDateTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return ''
  }
}

async function load() {
  loading.value = true
  try {
    const body = await fetchAdminAgentActions({
      status: status.value,
      page: page.value,
      pageSize: pageSize.value,
    })
    items.value = body.items
    total.value = body.total
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Agent 动作加载失败')
  } finally {
    loading.value = false
  }
}

async function openDetail(row: AdminAgentActionItem) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await fetchAgentActionDetail(row.id)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '详情加载失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function mark(row: AdminAgentActionItem, target: Exclude<AgentActionStatus, 'pending'>) {
  try {
    await markAgentAction(row.id, target, '管理后台手动标记')
    message.success('已标记')
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '标记失败')
  }
}

const columns: DataTableColumns<AdminAgentActionItem> = [
  { title: '时间', key: 'createdAt', width: 150, render: (row) => fmtDateTime(row.createdAt) },
  {
    title: '动作',
    key: 'title',
    minWidth: 200,
    render: (row) =>
      h('div', { style: 'display:flex;flex-direction:column;line-height:1.5' }, [
        h('span', { style: 'font-weight:600;color:var(--solo-text-primary)' }, row.title || '—'),
        h('span', { style: 'font-size:12px;color:var(--solo-text-muted)' }, actionLabel(row.actionType)),
      ]),
  },
  { title: '公司', key: 'companyId', minWidth: 150, ellipsis: { tooltip: true } },
  { title: '用户', key: 'userId', minWidth: 120, ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) => {
      const tag = STATUS_TAGS[row.status]
      return h(NTag, { type: tag.type, size: 'small', round: true, bordered: false }, { default: () => tag.label })
    },
  },
  { title: '处理人 / 时间', key: 'resolvedAt', width: 170, render: (row) => (row.resolvedAt ? `${fmtDateTime(row.resolvedAt)} · ${row.resolvedBy || '—'}` : '—') },
  {
    title: '操作',
    key: 'actions',
    width: 250,
    render: (row) => {
      const detailBtn = h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => void openDetail(row) },
        { default: () => '详情' },
      )
      if (row.status !== 'pending') return detailBtn
      const markBtn = (target: 'approved' | 'rejected' | 'expired', label: string) =>
        h(
          NPopconfirm,
          { onPositiveClick: () => mark(row, target) },
          {
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'primary' }, { default: () => label }),
            default: () => `仅改变状态为「${STATUS_TAGS[target].label}」，不执行任何业务逻辑，确认？`,
          },
        )
      return h('div', { style: 'display:flex;gap:2px' }, [detailBtn, markBtn('approved', '标记已执行'), markBtn('rejected', '标记驳回'), markBtn('expired', '标记过期')])
    },
  },
]

onMounted(load)
</script>

<template>
  <div class="admin-agent-audit">
    <NCard :bordered="false">
      <div class="toolbar">
        <NButton
          v-for="opt in STATUS_OPTIONS"
          :key="opt.value"
          size="small"
          :type="status === opt.value ? 'primary' : 'default'"
          :quaternary="status !== opt.value"
          @click="status = opt.value as any; page = 1; void load()"
        >
          {{ opt.label }}
        </NButton>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          手动标记仅改变状态（记录处理人），不执行业务逻辑
        </span>
      </div>
    </NCard>

    <NCard :bordered="false">
      <NDataTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        remote
        :scroll-x="1100"
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: (p: number) => { page = p; void load() },
          onUpdatePageSize: (s: number) => { pageSize = s; page = 1; void load() },
        }"
      />
    </NCard>

    <NModal v-model:show="detailVisible" preset="card" title="动作详情" style="max-width: 720px">
      <div v-if="detailLoading" class="detail-loading">加载中…</div>
      <div v-else-if="detail" class="detail-body">
        <div class="detail-meta">
          <span>{{ detail.title || detail.actionType }}</span>
          <NTag size="small" round :bordered="false" :type="STATUS_TAGS[detail.status].type">
            {{ STATUS_TAGS[detail.status].label }}
          </NTag>
        </div>
        <p class="detail-hint">{{ detail.companyId }} · {{ detail.userId }} · {{ fmtDateTime(detail.createdAt) }}</p>
        <div v-for="section in (['payload', 'evidence', 'result'] as const)" :key="section" class="detail-section">
          <h4>{{ section }}</h4>
          <pre>{{ prettyJson((detail as any)[section]) || '（空）' }}</pre>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.admin-agent-audit {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.detail-loading {
  padding: 48px;
  text-align: center;
  color: var(--solo-text-muted);
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.detail-hint {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  margin: 6px 0 14px;
}

.detail-section h4 {
  margin: 12px 0 6px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.detail-section pre {
  margin: 0;
  padding: 10px 12px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius-sm);
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
