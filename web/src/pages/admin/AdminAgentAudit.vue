<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref, watch } from 'vue'
import {
  NButton,
  NCard,
  NPopconfirm,
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
import { useAdminTable, formatAdminDateTime } from '@/composables/useAdminTable'
import { SoloTable, SoloDrawer, SoloStatusBadge, SoloButton } from '@/components/ui'

const message = useMessage()
const status = ref<'all' | AgentActionStatus>('all')

const detailVisible = ref(false)
const detail = ref<AdminAgentActionDetail | null>(null)
const detailLoading = ref(false)
const activeTab = ref<'payload' | 'evidence' | 'result'>('payload')

const {
  loading,
  items,
  total,
  page,
  pageSize,
  load,
  onPageChange,
  onPageSizeChange,
} = useAdminTable<AdminAgentActionItem>({
  defaultPageSize: 20,
  loadData: async ({ page, pageSize }) => {
    return await fetchAdminAgentActions({
      status: status.value,
      page,
      pageSize,
    })
  },
})

// 切换状态筛选时重新触发加载
watch(status, () => {
  page.value = 1
  void load()
})

const STATUS_OPTIONS = [
  { label: '全部动作', value: 'all' },
  { label: '待确认', value: 'pending' },
  { label: '已执行', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '已过期', value: 'expired' },
]

const STATUS_MAP: Record<AgentActionStatus, { label: string; tone: 'warning' | 'success' | 'danger' | 'neutral' }> = {
  pending: { label: '待确认', tone: 'warning' },
  approved: { label: '已执行', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
  expired: { label: '已过期', tone: 'neutral' },
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

function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

async function openDetail(row: AdminAgentActionItem) {
  detailVisible.value = true
  detailLoading.value = true
  activeTab.value = 'payload'
  try {
    detail.value = await fetchAgentActionDetail(row.id)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '动作详情加载失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function mark(row: AdminAgentActionItem | AdminAgentActionDetail, target: Exclude<AgentActionStatus, 'pending'>) {
  try {
    await markAgentAction(row.id, target, '管理控制台手动标记')
    message.success('已更新动作状态')
    if (detail.value && detail.value.id === row.id) {
      detail.value.status = target
    }
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '标记失败')
  }
}

const columns: DataTableColumns<AdminAgentActionItem> = [
  { title: '时间', key: 'createdAt', width: 155, render: (row) => formatAdminDateTime(row.createdAt) },
  {
    title: '动作与类型',
    key: 'title',
    minWidth: 220,
    render: (row) =>
      h('div', { class: 'action-title-cell', onClick: () => void openDetail(row) }, [
        h('span', { class: 'action-title-text' }, row.title || '未命名动作'),
        h('span', { class: 'action-type-sub' }, actionLabel(row.actionType)),
      ]),
  },
  {
    title: '归属公司',
    key: 'companyId',
    minWidth: 150,
    render: (row) => h('span', { class: 'monospace-cell' }, row.companyId),
  },
  {
    title: '提交用户',
    key: 'userId',
    minWidth: 130,
    render: (row) => h('span', { class: 'monospace-cell' }, row.userId),
  },
  {
    title: '审核状态',
    key: 'status',
    width: 105,
    render: (row) => {
      const info = STATUS_MAP[row.status]
      return h(SoloStatusBadge, { tone: info.tone, size: 'small' }, { default: () => info.label })
    },
  },
  {
    title: '处理进度',
    key: 'resolvedAt',
    width: 175,
    render: (row) =>
      row.resolvedAt
        ? h('div', { class: 'resolved-cell' }, [
            h('span', null, formatAdminDateTime(row.resolvedAt)),
            h('span', { class: 'resolved-by' }, row.resolvedBy ? `操作人: ${row.resolvedBy}` : ''),
          ])
        : h('span', { style: 'color:var(--solo-text-muted)' }, '待处理'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => {
      const detailBtn = h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => void openDetail(row) },
        { default: () => '详情' },
      )
      if (row.status !== 'pending') return detailBtn
      const approveBtn = h(
        NPopconfirm,
        { onPositiveClick: () => mark(row, 'approved') },
        {
          trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'success' }, { default: () => '通过' }),
          default: () => '仅改变状态为「已执行」，不重放业务代码，确认？',
        },
      )
      const rejectBtn = h(
        NPopconfirm,
        { onPositiveClick: () => mark(row, 'rejected') },
        {
          trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { default: () => '驳回' }),
          default: () => '标记驳回该建议动作，确认？',
        },
      )
      return h('div', { class: 'action-btns' }, [detailBtn, approveBtn, rejectBtn])
    },
  },
]
</script>

<template>
  <div class="admin-agent-audit">
    <NCard :bordered="false" class="filter-card">
      <div class="toolbar">
        <div class="status-tabs">
          <SoloButton
            v-for="opt in STATUS_OPTIONS"
            :key="opt.value"
            size="small"
            :variant="status === opt.value ? 'primary' : 'secondary'"
            @click="status = opt.value as any"
          >
            {{ opt.label }}
          </SoloButton>
        </div>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          全链路留痕：追踪 Agent 推送的记账、对账与摊销建议流转状态
        </span>
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
        :scroll-x="1100"
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        empty-title="暂无对应状态的 Agent 动作"
        empty-description="切换筛选选项以查看更多历史动作流"
        @row-click="openDetail"
      />
    </NCard>

    <!-- 动作详情抽屉 -->
    <SoloDrawer
      v-model:show="detailVisible"
      :title="detail ? `动作详情 · ${detail.title || detail.id}` : '动作详情'"
      :width="640"
    >
      <div v-if="detailLoading" class="detail-loading-box">
        <i class="ri-loader-4-line spin-icon" />
        <span>正在读取完整动作证据链...</span>
      </div>
      <div v-else-if="detail" class="detail-drawer-body">
        <div class="action-hero-card">
          <div class="action-avatar-box">
            <i class="ri-robot-2-line" />
          </div>
          <div class="action-hero-meta">
            <h3>{{ detail.title || actionLabel(detail.actionType) }}</h3>
            <span class="action-id-text">ID: {{ detail.id }}</span>
          </div>
          <SoloStatusBadge :tone="STATUS_MAP[detail.status].tone">
            {{ STATUS_MAP[detail.status].label }}
          </SoloStatusBadge>
        </div>

        <div class="detail-section">
          <h4 class="section-title">流转信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">关联账套</span>
              <span class="info-val monospace">{{ detail.companyId }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">触发用户</span>
              <span class="info-val monospace">{{ detail.userId }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">动作分类</span>
              <span class="info-val">{{ actionLabel(detail.actionType) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">创建时间</span>
              <span class="info-val">{{ formatAdminDateTime(detail.createdAt) }}</span>
            </div>
            <div v-if="detail.resolvedAt" class="info-item" style="grid-column: span 2">
              <span class="info-label">处理结果</span>
              <span class="info-val">{{ formatAdminDateTime(detail.resolvedAt) }} · 由 {{ detail.resolvedBy || '系统' }} 处理</span>
            </div>
          </div>
        </div>

        <!-- 数据查看 Tabs -->
        <div class="detail-section">
          <div class="data-tabs-header">
            <button
              class="data-tab-btn"
              :class="{ active: activeTab === 'payload' }"
              @click="activeTab = 'payload'"
            >
              参数内容 (Payload)
            </button>
            <button
              class="data-tab-btn"
              :class="{ active: activeTab === 'evidence' }"
              @click="activeTab = 'evidence'"
            >
              决策证据 (Evidence)
            </button>
            <button
              class="data-tab-btn"
              :class="{ active: activeTab === 'result' }"
              @click="activeTab = 'result'"
            >
              执行结果 (Result)
            </button>
          </div>

          <div class="json-code-box">
            <pre>{{ prettyJson(detail[activeTab]) || '（该项数据为空）' }}</pre>
          </div>
        </div>

        <!-- 运维手动干预 -->
        <div v-if="detail.status === 'pending'" class="detail-section mark-section">
          <h4 class="section-title">运维干预（标记状态）</h4>
          <p class="section-desc">仅修改数据库中动作审核记录，不触发真实记账回滚或入账代码。</p>
          <div class="action-row">
            <NPopconfirm :on-positive-click="() => mark(detail!, 'approved')">
              <template #trigger>
                <SoloButton size="small" variant="primary">
                  <template #icon><i class="ri-check-line" /></template>
                  标记为已执行
                </SoloButton>
              </template>
              确认将该建议标记为已通过？
            </NPopconfirm>

            <NPopconfirm :on-positive-click="() => mark(detail!, 'rejected')">
              <template #trigger>
                <SoloButton size="small" variant="secondary">
                  <template #icon><i class="ri-close-line" /></template>
                  标记为已驳回
                </SoloButton>
              </template>
              确认将该建议标记为已驳回？
            </NPopconfirm>

            <NPopconfirm :on-positive-click="() => mark(detail!, 'expired')">
              <template #trigger>
                <SoloButton size="small" variant="secondary">
                  <template #icon><i class="ri-time-line" /></template>
                  标记为已过期
                </SoloButton>
              </template>
              确认将该动作设为已失效/过期？
            </NPopconfirm>
          </div>
        </div>
      </div>
    </SoloDrawer>
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
  gap: 12px;
  flex-wrap: wrap;
}

.status-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.action-title-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  cursor: pointer;
}

.action-title-text {
  font-weight: 600;
  color: var(--solo-text-primary);
}

.action-type-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.monospace-cell {
  font-family: monospace;
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.resolved-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
  font-size: 12.5px;
}

.resolved-by {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 抽屉样式 */
.detail-loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px 0;
  color: var(--solo-text-muted);
}

.spin-icon {
  font-size: 24px;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.detail-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0;
}

.action-hero-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.action-avatar-box {
  width: 44px;
  height: 44px;
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}

.action-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.action-hero-meta h3 {
  margin: 0;
  font-size: 15.5px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.action-id-text {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: monospace;
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

.section-desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
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

.data-tabs-header {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--solo-border);
  padding-bottom: 6px;
}

.data-tab-btn {
  background: transparent;
  border: none;
  font-size: 13px;
  padding: 6px 12px;
  color: var(--solo-text-muted);
  cursor: pointer;
  border-radius: var(--solo-radius-sm, 4px);
  transition: all 0.15s ease-in-out;
}

.data-tab-btn:hover {
  color: var(--solo-text-primary);
  background: var(--solo-bg-soft);
}

.data-tab-btn.active {
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft, #edf3ff);
  font-weight: 600;
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
  max-height: 280px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}
</style>
