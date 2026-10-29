<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'
import type { SchedulePreview } from '@/api/tax-registration'

const props = defineProps<{
  loading: boolean
  stats: { total: number; active: number; groups: Record<string, number> }
  preview: SchedulePreview | null
  year: number
}>()

const emit = defineEmits<{
  refresh: []
  useTemplate: []
  openImport: []
}>()

const isSynced = computed(() => props.preview?.source === 'registration')
const vatCycleText = computed(() => (props.preview?.cycles?.vat === 'month' ? '月报' : '季报'))
</script>

<template>
  <div class="registration-header-section">
    <div class="page-header">
      <div class="header-left">
        <div class="title-row">
          <h2>税费种认定</h2>
          <NTag
            size="small"
            :type="isSynced ? 'success' : 'warning'"
            round
            :bordered="false"
            class="status-badge"
          >
            {{ isSynced ? '已按税局认定同步' : '未同步 · 内置推导中' }}
          </NTag>
        </div>
        <p class="header-sub">
          对接电子税务局「税费种认定信息」；征期日历与各税种申报清单均按此表自动生成，清晰掌握每月与每季的应报税费。
        </p>
      </div>
      <div class="header-actions">
        <NButton size="small" secondary :loading="loading" class="action-btn" @click="emit('refresh')">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新
        </NButton>
        <NButton size="small" class="action-btn" @click="emit('useTemplate')">
          <template #icon><i class="ri-magic-line" /></template>
          按主体类型带出
        </NButton>
        <NButton size="small" type="primary" class="action-btn action-btn-primary" @click="emit('openImport')">
          <template #icon><i class="ri-download-cloud-2-line" /></template>
          从电子税务局同步
        </NButton>
      </div>
    </div>

    <!-- 4 大核心指标卡 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-top">
          <span class="stat-label">认定税种项数</span>
          <i class="ri-government-line stat-icon" />
        </div>
        <div class="stat-body">
          <strong class="stat-value">{{ stats.active }}</strong>
          <span class="stat-unit">项</span>
        </div>
        <div class="stat-footer">
          <span>共 {{ stats.total }} 项（含已停用 {{ stats.total - stats.active }} 项）</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-top">
          <span class="stat-label">归属申报表</span>
          <i class="ri-file-list-3-line stat-icon" />
        </div>
        <div class="stat-body">
          <strong class="stat-value">{{ Object.keys(stats.groups).length }}</strong>
          <span class="stat-unit">张</span>
        </div>
        <div class="stat-footer">
          <span>增值税、印花税等多税种智能并表</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-top">
          <span class="stat-label">{{ year }} 年应报事项</span>
          <i class="ri-calendar-event-line stat-icon" />
        </div>
        <div class="stat-body">
          <strong class="stat-value">{{ preview?.summary.itemCount ?? 0 }}</strong>
          <span class="stat-unit">次</span>
        </div>
        <div class="stat-footer">
          <span>涵盖 {{ preview?.summary.periodCount ?? 0 }} 个属期</span>
          <span v-if="!isSynced" class="stat-source-tag">内置推导</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-top">
          <span class="stat-label">增值税申报周期</span>
          <i class="ri-time-line stat-icon" />
        </div>
        <div class="stat-body">
          <strong class="stat-value stat-value--text">{{ vatCycleText }}</strong>
        </div>
        <div class="stat-footer">
          <span>城建及附加税同频随征</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.registration-header-section {
  margin-bottom: 16px;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 16px;
}
.header-left {
  flex: 1;
  min-width: 0;
}
.header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
  padding-top: 2px;
}
.action-btn {
  font-weight: 500;
}
.action-btn-primary {
  font-weight: 600;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  color: var(--solo-text-primary);
  letter-spacing: -0.01em;
}
.status-badge {
  font-weight: 500;
  font-size: 12px;
}
.header-sub {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
  max-width: 760px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 14px 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  min-height: 98px;
}
.stat-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.stat-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
.stat-icon {
  font-size: 16px;
  color: var(--solo-text-muted);
}
.stat-body {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin: 4px 0;
}
.stat-value {
  font-family: Inter, var(--solo-font-mono, monospace);
  font-size: 24px;
  font-weight: 680;
  color: var(--solo-text-primary);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.stat-value--text {
  font-size: 20px;
  font-family: inherit;
}
.stat-unit {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.stat-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--solo-text-muted);
}
.stat-source-tag {
  background: var(--solo-bg-soft);
  color: var(--solo-warning);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10px;
}
@media (max-width: 992px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .header-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-start;
  }
}
</style>
