<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'
import type { SchedulePreview } from '@/api/tax-registration'

const props = defineProps<{
  loading: boolean
  stats: { total: number; active: number; groups: Record<string, number> }
  preview: SchedulePreview | null
}>()

const emit = defineEmits<{
  refresh: []
  openImport: []
}>()

const isSynced = computed(() => props.preview?.source === 'registration')
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
      </div>
      <div class="header-actions">
        <NButton size="small" secondary :loading="loading" class="action-btn" @click="emit('refresh')">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新
        </NButton>
        <template v-if="stats.active > 0">
          <NButton size="small" type="primary" class="action-btn action-btn-primary" @click="emit('openImport')">
            <template #icon><i class="ri-download-cloud-2-line" /></template>
            从电子税务局同步
          </NButton>
        </template>
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
  align-items: center;
  justify-content: space-between;
  gap: 20px;
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
@media (max-width: 992px) {
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
