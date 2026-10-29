<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref } from 'vue'
import { NButton, NCard, type DataTableColumns } from 'naive-ui'
import { fetchAdminAuditLogs, type AdminAuditLogItem } from '@/api/admin'
import { useAdminTable, formatAdminDateTime } from '@/composables/useAdminTable'
import { SoloTable, SoloDrawer, SoloStatusBadge } from '@/components/ui'

const activeLog = ref<AdminAuditLogItem | null>(null)
const drawerVisible = ref(false)

const {
  loading,
  items,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
} = useAdminTable<AdminAuditLogItem>({
  defaultPageSize: 50,
  loadData: async ({ page, pageSize }) => {
    return await fetchAdminAuditLogs({ page, pageSize })
  },
})

const ACTION_MAP: Record<string, { label: string; tone: 'warning' | 'info' | 'danger' | 'success' }> = {
  disable_user: { label: '禁用用户', tone: 'danger' },
  enable_user: { label: '启用用户', tone: 'success' },
  grant_admin: { label: '授予管理员', tone: 'info' },
  revoke_admin: { label: '撤销管理员', tone: 'warning' },
  cleanup: { label: '数据清理', tone: 'warning' },
}

function getActionInfo(action: string) {
  return ACTION_MAP[action] ?? { label: action, tone: 'neutral' }
}

function openDetail(row: AdminAuditLogItem) {
  activeLog.value = row
  drawerVisible.value = true
}

function prettyJson(detail: Record<string, unknown>): string {
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return ''
  }
}

const columns: DataTableColumns<AdminAuditLogItem> = [
  { title: '时间', key: 'createdAt', width: 165, render: (row) => formatAdminDateTime(row.createdAt) },
  {
    title: '操作类型',
    key: 'action',
    width: 130,
    render: (row) => {
      const info = getActionInfo(row.action)
      return h(SoloStatusBadge, { tone: info.tone, size: 'small' }, { default: () => info.label })
    },
  },
  {
    title: '受控目标',
    key: 'targetId',
    minWidth: 180,
    render: (row) =>
      h('div', { class: 'target-cell', onClick: () => openDetail(row) }, [
        h('span', { class: 'target-type' }, row.targetType === 'user' ? '用户账号' : row.targetType),
        h('span', { class: 'target-id' }, row.targetId),
      ]),
  },
  {
    title: '操作人',
    key: 'operatorId',
    minWidth: 150,
    render: (row) => h('span', { class: 'operator-cell' }, row.operatorId),
  },
  {
    title: '变更快照',
    key: 'detail',
    ellipsis: { tooltip: true },
    render: (row) =>
      h(
        'span',
        {
          class: 'detail-preview',
        },
        JSON.stringify(row.detail),
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => openDetail(row) },
        { default: () => '详情' },
      ),
  },
]
</script>

<template>
  <div class="admin-audit">
    <NCard :bordered="false" class="header-card">
      <div class="header-intro">
        <div class="intro-left">
          <h3>管理操作留痕审计</h3>
          <p>平台管理员关键动作（账号封禁、提权撤权、批量清理）不可篡改审计追踪</p>
        </div>
        <div class="intro-right">
          <span class="note-pill"><i class="ri-shield-check-line" /> 强合规审计流水</span>
        </div>
      </div>
    </NCard>

    <NCard :bordered="false" class="table-card">
      <SoloTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        clickable
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [20, 50, 100],
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        empty-title="暂无操作日志记录"
        empty-description="系统日常管理变更将自动在此记录留痕"
        @row-click="openDetail"
      />
    </NCard>

    <!-- 操作日志详情抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="activeLog ? `审计明细 · ${getActionInfo(activeLog.action).label}` : '审计明细'"
      :width="540"
    >
      <div v-if="activeLog" class="audit-drawer-body">
        <div class="audit-hero-card">
          <div class="audit-icon-box">
            <i class="ri-history-line" />
          </div>
          <div class="audit-hero-meta">
            <h3>{{ getActionInfo(activeLog.action).label }}</h3>
            <span class="audit-time">{{ formatAdminDateTime(activeLog.createdAt) }}</span>
          </div>
          <SoloStatusBadge :tone="getActionInfo(activeLog.action).tone">
            {{ activeLog.action }}
          </SoloStatusBadge>
        </div>

        <div class="detail-section">
          <h4 class="section-title">操作基本信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">操作人</span>
              <span class="info-val monospace">{{ activeLog.operatorId }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">目标类型</span>
              <span class="info-val">{{ activeLog.targetType }}</span>
            </div>
            <div class="info-item" style="grid-column: span 2">
              <span class="info-label">目标标识 ID</span>
              <span class="info-val monospace">{{ activeLog.targetId }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">变更数据快照 (JSON)</h4>
          <div class="json-code-box">
            <pre>{{ prettyJson(activeLog.detail) || '（无附加密切数据）' }}</pre>
          </div>
        </div>
      </div>
    </SoloDrawer>
  </div>
</template>

<style scoped>
.admin-audit {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.header-intro {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.intro-left h3 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.intro-left p {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.note-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: var(--solo-bg-soft);
  border-radius: 999px;
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.target-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
  cursor: pointer;
}

.target-type {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}

.target-id {
  font-weight: 550;
  color: var(--solo-text-primary);
  font-family: monospace;
}

.operator-cell {
  font-family: monospace;
  font-size: 12.5px;
  color: var(--solo-text-primary);
}

.detail-preview {
  font-family: monospace;
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* 抽屉样式 */
.audit-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0;
}

.audit-hero-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.audit-icon-box {
  width: 44px;
  height: 44px;
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.audit-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.audit-hero-meta h3 {
  margin: 0;
  font-size: 15.5px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.audit-time {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 14px 16px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.info-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.info-val {
  font-size: 13px;
  color: var(--solo-text-primary);
}

.monospace {
  font-family: monospace;
}

.json-code-box {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 12px 14px;
}

.json-code-box pre {
  margin: 0;
  font-family: monospace;
  font-size: 12px;
  color: var(--solo-text-primary);
  line-height: 1.45;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
