<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NCard, NDataTable, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { fetchAdminAuditLogs, type AdminAuditLogItem } from '@/api/admin'

const message = useMessage()
const loading = ref(false)
const items = ref<AdminAuditLogItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)

const ACTION_LABELS: Record<string, string> = {
  disable_user: '禁用用户',
  enable_user: '启用用户',
  grant_admin: '授予管理员',
  revoke_admin: '撤销管理员',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

function fmtDateTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function detailText(detail: Record<string, unknown>): string {
  try {
    return JSON.stringify(detail)
  } catch {
    return ''
  }
}

const columns: DataTableColumns<AdminAuditLogItem> = [
  { title: '时间', key: 'createdAt', width: 165, render: (row) => fmtDateTime(row.createdAt) },
  {
    title: '操作',
    key: 'action',
    width: 130,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          round: true,
          bordered: false,
          type: row.action.startsWith('disable') || row.action.startsWith('revoke') ? 'warning' : 'info',
        },
        { default: () => actionLabel(row.action) },
      ),
  },
  {
    title: '目标',
    key: 'targetId',
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) => `${row.targetType === 'user' ? '用户' : row.targetType} · ${row.targetId}`,
  },
  { title: '操作人', key: 'operatorId', minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: '变更详情',
    key: 'detail',
    minWidth: 280,
    ellipsis: { tooltip: true },
    render: (row) => detailText(row.detail),
  },
]

async function load() {
  loading.value = true
  try {
    const body = await fetchAdminAuditLogs({ page: page.value, pageSize: pageSize.value })
    items.value = body.items
    total.value = body.total
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '操作日志加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-audit">
    <NCard title="管理操作留痕" :bordered="false">
      <template #header-extra>
        <span class="note"><i class="ri-information-line" /> 禁用/启用、管理员变更均自动留痕</span>
      </template>
      <NDataTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        remote
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          onChange: (p: number) => { page = p; void load() },
        }"
      />
    </NCard>
  </div>
</template>

<style scoped>
.admin-audit {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.note {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}
</style>
