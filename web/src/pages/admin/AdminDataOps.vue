<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NCard,
  NDatePicker,
  NPopconfirm,
  NSelect,
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
import { useAdminStore } from '@/stores/admin'
import { formatAdminNumber } from '@/composables/useAdminTable'
import { SoloTable, SoloButton, SoloStatusBadge } from '@/components/ui'

const message = useMessage()
const adminStore = useAdminStore()

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
  {
    title: '数据库物理表',
    key: 'table',
    minWidth: 220,
    render: (row) => h('span', { class: 'table-name-mono' }, row.table),
  },
  {
    title: '存量数据总行数',
    key: 'total',
    width: 140,
    align: 'right',
    render: (row) => formatAdminNumber(row.total),
  },
  {
    title: '最早记录时间（UTC）',
    key: 'oldest',
    minWidth: 200,
    render: (row) => (row.oldest ? row.oldest.replace('T', ' ').slice(0, 19) : '—'),
  },
]

async function loadStats() {
  loading.value = true
  try {
    stats.value = (await fetchCleanupStats()).items
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '数据统计加载失败')
  } finally {
    loading.value = false
  }
}

const preview = computed(() =>
  lastResult.value?.dryRun && lastResult.value.table === table.value ? lastResult.value : null,
)

async function doPreview() {
  cleaning.value = true
  try {
    lastResult.value = await runCleanup({ table: table.value, before: beforeStr(beforeTs.value), dryRun: true })
    message.info(`预估将清理 ${lastResult.value.deleted} 条记录`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '预演失败')
  } finally {
    cleaning.value = false
  }
}

async function doApply() {
  cleaning.value = true
  try {
    const result = await runCleanup({ table: table.value, before: beforeStr(beforeTs.value), dryRun: false })
    lastResult.value = result
    message.success(`成功执行清理：已安全物理删除 ${result.deleted} 行（留痕已写入审计表）`)
    await loadStats()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '清理失败')
  } finally {
    cleaning.value = false
  }
}

watch(
  () => adminStore.refreshSignal,
  () => {
    void loadStats()
  },
)

onMounted(loadStats)
</script>

<template>
  <div class="admin-data-ops">
    <NCard title="表数据规模" :bordered="false" class="stats-card">
      <template #header-extra>
        <SoloButton size="small" variant="secondary" @click="loadStats">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新规模
        </SoloButton>
      </template>
      <SoloTable
        :columns="statsColumns"
        :data="stats"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
        empty-title="暂无表统计信息"
      />
    </NCard>

    <NCard title="归档清理作业" :bordered="false" class="ops-card">
      <div class="cleanup-form">
        <div class="form-row">
          <label class="form-label">目标清理表</label>
          <NSelect v-model:value="table" :options="TABLES" style="width: 320px" size="small" />
        </div>

        <div class="form-row">
          <label class="form-label">清理截至时间</label>
          <NDatePicker v-model:value="beforeTs" type="date" size="small" style="width: 200px" />
          <span class="form-hint">早于该日期的记录将被清理</span>
        </div>

        <div class="form-actions">
          <SoloButton
            size="small"
            variant="secondary"
            :loading="cleaning"
            @click="doPreview"
          >
            <template #icon><i class="ri-eye-line" /></template>
            预演扫描 (Dry Run)
          </SoloButton>

          <NPopconfirm :on-positive-click="doApply">
            <template #trigger>
              <SoloButton
                size="small"
                variant="danger"
                :loading="cleaning"
              >
                <template #icon><i class="ri-delete-bin-line" /></template>
                执行物理清理
              </SoloButton>
            </template>
            确认对 {{ table }} 执行物理删除（早于 {{ beforeStr(beforeTs) }}）？该操作不可撤销！
          </NPopconfirm>
        </div>

        <!-- 预演结果反馈 -->
        <div v-if="preview" class="preview-box">
          <div class="preview-header">
            <SoloStatusBadge tone="brand">
              预演成功 · 模拟结果
            </SoloStatusBadge>
            <span class="preview-time">扫描目标：{{ preview.table }}</span>
          </div>
          <p class="preview-desc">
            若立即执行，系统将清理 <strong>{{ formatAdminNumber(preview.deleted) }}</strong> 行历史数据（早于 {{ preview.before }}）。
          </p>
        </div>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.admin-data-ops {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.table-name-mono {
  font-family: monospace;
  font-size: 13px;
  color: var(--solo-text-primary);
}

.cleanup-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 680px;
  padding: 8px 0;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.form-label {
  width: 90px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.form-hint {
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.form-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}

.preview-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.preview-time {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: monospace;
}

.preview-desc {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-primary);
}

.preview-desc strong {
  color: var(--solo-primary);
  font-size: 15px;
}
</style>
