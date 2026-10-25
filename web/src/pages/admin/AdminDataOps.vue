<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NPopconfirm,
  NSelect,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  fetchCleanupStats,
  runCleanup,
  type CleanupResult,
  type CleanupStatsItem,
  type CleanupTable,
} from '@/api/admin'

const message = useMessage()
const loading = ref(false)
const cleaning = ref(false)
const lastResult = ref<CleanupResult | null>(null)
const stats = ref<CleanupStatsItem[]>([])

const TABLES = [
  { label: 'LLM 用量明细（llm_usage）', value: 'llm_usage' },
  { label: 'Agent 动作流水（agent_actions）', value: 'agent_actions' },
  { label: '管理操作留痕（admin_audit_logs）', value: 'admin_audit_logs' },
]

const table = ref<CleanupTable>('llm_usage')
const beforeTs = ref<number>(Date.now() - 90 * 86400_000)

function beforeStr(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const statsColumns: DataTableColumns<CleanupStatsItem> = [
  { title: '表', key: 'table', minWidth: 220, render: (row) => h('span', { style: 'font-family:monospace;font-size:12.5px' }, row.table) },
  { title: '总行数', key: 'total', width: 110, align: 'right', render: (row) => row.total.toLocaleString('zh-CN') },
  { title: '最早记录（UTC）', key: 'oldest', minWidth: 190, render: (row) => (row.oldest ? row.oldest.replace('T', ' ').slice(0, 19) : '—') },
]

async function loadStats() {
  loading.value = true
  try {
    stats.value = (await fetchCleanupStats()).items
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '统计加载失败')
  } finally {
    loading.value = false
  }
}

const preview = computed(() => (lastResult.value?.dryRun && lastResult.value.table === table.value ? lastResult.value : null))

async function doPreview() {
  cleaning.value = true
  try {
    lastResult.value = await runCleanup({ table: table.value, before: beforeStr(beforeTs.value), dryRun: true })
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '预览失败')
  } finally {
    cleaning.value = false
  }
}

async function doApply() {
  cleaning.value = true
  try {
    const result = await runCleanup({ table: table.value, before: beforeStr(beforeTs.value), dryRun: false })
    lastResult.value = result
    message.success(`已删除 ${result.deleted} 行（留痕已记录）`)
    await loadStats()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '清理失败')
  } finally {
    cleaning.value = false
  }
}

onMounted(loadStats)
</script>

<template>
  <div class="admin-data-ops">
    <NCard title="数据规模" :bordered="false">
      <NDataTable
        :columns="statsColumns"
        :data="stats"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
      />
    </NCard>

    <NCard title="按时间清理" :bordered="false">
      <template #header-extra>
        <NTag size="small" round :bordered="false">删除 UTC 日期零点之前的记录</NTag>
      </template>
      <div class="cleanup-form">
        <NSelect v-model:value="table" :options="TABLES" size="small" style="width: 320px" />
        <div class="date-item">
          <span class="date-label">清理截至</span>
          <NDatePicker v-model:value="beforeTs" type="date" size="small" :is-date-disabled="(ts: number) => ts > Date.now()" />
        </div>
        <NButton size="small" :loading="cleaning" @click="doPreview">预览将删除的行数</NButton>
        <NPopconfirm
          :disabled="!preview || preview.matched === 0"
          @positive-click="doApply"
        >
          <template #trigger>
            <NButton size="small" type="error" :disabled="!preview || preview.matched === 0" :loading="cleaning">
              执行清理
            </NButton>
          </template>
          确认删除 {{ preview?.matched }} 行？此操作不可恢复（将写入操作留痕）。
        </NPopconfirm>
      </div>
      <p v-if="lastResult" class="cleanup-result" :class="{ preview: lastResult.dryRun }">
        <i class="ri-information-line" />
        <template v-if="lastResult.dryRun">
          预览：{{ lastResult.table }} 在 {{ lastResult.before }} 之前有 {{ lastResult.matched }} 行将被删除。
        </template>
        <template v-else>
          已清理：{{ lastResult.table }} 删除 {{ lastResult.deleted }} 行（{{ lastResult.before }} 之前）。
        </template>
      </p>
      <p class="ops-note">
        <i class="ri-shield-check-line" />
        清理仅作用于统计/审计类表，业务账套数据不受影响；所有清理操作均写入操作日志。
      </p>
    </NCard>
  </div>
</template>

<style scoped>
.admin-data-ops {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.cleanup-form {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.date-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.date-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.cleanup-result {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 14px 0 0;
  padding: 10px 12px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-income-bg);
  color: var(--solo-income);
  font-size: 13px;
}

.cleanup-result.preview {
  background: var(--solo-info-bg);
  color: var(--solo-info);
}

.ops-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}
</style>
