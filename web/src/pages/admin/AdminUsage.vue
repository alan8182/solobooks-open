<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NSelect,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import {
  fetchUsageRecent,
  fetchUsageTokens,
  type UsageGroupBy,
  type UsageRecentItem,
  type UsageTokensResponse,
} from '@/api/admin'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const message = useMessage()
const loading = ref(false)
const recentLoading = ref(false)

const groupBy = ref<UsageGroupBy>('model')
const groupOptions = [
  { label: '按模型', value: 'model' },
  { label: '按用户', value: 'user' },
  { label: '按公司', value: 'company' },
  { label: '按天', value: 'day' },
]

const defaultRange = (): [number, number] => {
  const now = Date.now()
  return [now - 29 * 86400_000, now]
}
const range = ref<[number, number]>(defaultRange())

const usage = ref<UsageTokensResponse | null>(null)
const recent = ref<UsageRecentItem[]>([])

const SCENE_LABELS: Record<string, string> = {
  agent_chat: 'Agent 对话',
  copilot: '经营助手',
  invoice_ocr: '单据识别',
  classify: '智能分类',
  batch_split: '连续日志拆分',
  report: '报表洞察',
  bank_statement: '银行流水',
  invoice: '发票处理',
  asr: '语音转写',
  other: '其他',
}

function sceneLabel(scene: string): string {
  return SCENE_LABELS[scene] ?? scene
}

function fmtDate(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function fmtDuration(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}min`
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

function fmtNum(v: number | null | undefined): string {
  return typeof v === 'number' ? v.toLocaleString('zh-CN') : '—'
}

const dimensionLabel = computed(
  () => ({ model: '模型', user: '用户', company: '公司', day: '日期' })[groupBy.value],
)

const hasPricing = computed(
  () => usage.value?.pricingConfigured && usage.value.rows.some((r) => r.costEstimate !== null),
)

const groupColumns = computed<DataTableColumns>(() => {
  const cols: DataTableColumns = [
    { title: dimensionLabel.value, key: 'key', minWidth: 140, ellipsis: { tooltip: true } },
    { title: '调用次数', key: 'calls', width: 90, align: 'right' },
    { title: 'Prompt', key: 'promptTokens', width: 110, align: 'right', render: (row: any) => fmtNum(row.promptTokens) },
    { title: 'Completion', key: 'completionTokens', width: 110, align: 'right', render: (row: any) => fmtNum(row.completionTokens) },
    { title: '总 tokens', key: 'totalTokens', width: 110, align: 'right', render: (row: any) => fmtNum(row.totalTokens) },
  ]
  if (hasPricing.value) {
    cols.push({
      title: '估算成本 (¥)',
      key: 'costEstimate',
      width: 110,
      align: 'right',
      render: (row: any) => (row.costEstimate === null ? '—' : row.costEstimate.toFixed(4)),
    })
  }
  return cols
})

const recentColumns: DataTableColumns = [
  { title: '时间', key: 'createdAt', width: 130, render: (row: any) => fmtDateTime(row.createdAt) },
  { title: '场景', key: 'scene', width: 100, render: (row: any) => sceneLabel(row.scene) },
  { title: '模型', key: 'model', width: 150, ellipsis: { tooltip: true } },
  { title: '用户', key: 'userId', width: 120, ellipsis: { tooltip: true }, render: (row: any) => row.userId || '—' },
  { title: '公司', key: 'companyId', width: 120, ellipsis: { tooltip: true }, render: (row: any) => row.companyId || '—' },
  { title: 'tokens', key: 'totalTokens', width: 90, align: 'right', render: (row: any) => fmtNum(row.totalTokens) },
  { title: '耗时', key: 'durationMs', width: 80, align: 'right', render: (row: any) => fmtDuration(row.durationMs) },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row: any) =>
      row.status === 'ok'
        ? 'ok'
        : row.status === 'circuit_open'
          ? '已熔断'
          : row.errorType || 'error',
  },
]

const chartOption = computed(() => {
  if (!usage.value) return {}
  const rows = [...usage.value.rows].sort((a, b) => a.key.localeCompare(b.key))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['总 tokens', '调用次数'], top: 0 },
    grid: { left: 56, right: 48, top: 32, bottom: 28 },
    xAxis: { type: 'category', data: rows.map((r) => r.key) },
    yAxis: [
      { type: 'value', name: 'tokens' },
      { type: 'value', name: '次数' },
    ],
    series: [
      {
        name: '总 tokens',
        type: 'line',
        smooth: true,
        data: rows.map((r) => r.totalTokens),
        itemStyle: { color: '#1747b0' },
        areaStyle: { color: 'rgba(22, 93, 255, 0.08)' },
      },
      {
        name: '调用次数',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        data: rows.map((r) => r.calls),
        itemStyle: { color: '#0f7354' },
      },
    ],
  }
})

async function loadUsage() {
  loading.value = true
  try {
    usage.value = await fetchUsageTokens({
      group_by: groupBy.value,
      from: fmtDate(range.value[0]),
      to: fmtDate(range.value[1]),
    })
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '用量统计加载失败')
  } finally {
    loading.value = false
  }
}

async function loadRecent() {
  recentLoading.value = true
  try {
    recent.value = (await fetchUsageRecent({ limit: 50 })).items
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '调用明细加载失败')
  } finally {
    recentLoading.value = false
  }
}

function onGroupChange() {
  void loadUsage()
}

function onRangeChange(value: [number, number] | null) {
  if (value) {
    range.value = value
    void loadUsage()
  }
}

function refreshAll() {
  void loadUsage()
  void loadRecent()
}

onMounted(refreshAll)
</script>

<template>
  <div class="admin-usage">
    <NCard :bordered="false" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-item">
          <span class="toolbar-label">统计维度</span>
          <NSelect v-model:value="groupBy" :options="groupOptions" size="small" style="width: 120px" @update:value="onGroupChange" />
        </div>
        <div class="toolbar-item">
          <span class="toolbar-label">日期范围</span>
          <NDatePicker
            :value="range"
            type="daterange"
            size="small"
            clearable
            @update:value="onRangeChange"
          />
        </div>
        <NButton size="small" quaternary @click="refreshAll">
          <i class="ri-refresh-line" />
          刷新
        </NButton>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          按 UTC 日界统计；成本估算需配置 LLM_PRICE_TABLE
          <template v-if="!hasPricing">（当前未配置，仅显示 token）</template>
        </span>
      </div>
    </NCard>

    <NCard title="每日用量趋势（统计范围内）" :bordered="false" class="chart-card">
      <div v-if="loading" class="chart-loading"><NSpin size="medium" /></div>
      <VChart v-else-if="usage?.rows.length" :option="chartOption" class="trend-chart" autoresize />
      <div v-else class="chart-empty">
        <i class="ri-line-chart-line" />
        <p>该范围内暂无调用量数据（埋点自 M1 上线起积累）</p>
      </div>
    </NCard>

    <NCard :title="`用量构成（${dimensionLabel}）`" :bordered="false" class="table-card">
      <NDataTable
        :columns="groupColumns"
        :data="usage?.rows ?? []"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
        :max-height="360"
      />
    </NCard>

    <NCard title="最近调用明细" :bordered="false" class="table-card">
      <template #header-extra>
        <NTag size="small" round :bordered="false">最近 50 条</NTag>
      </template>
      <NDataTable
        :columns="recentColumns"
        :data="recent"
        :loading="recentLoading"
        :bordered="false"
        size="small"
        :pagination="false"
        :max-height="420"
        :scroll-x="960"
      />
    </NCard>
  </div>
</template>

<style scoped>
.admin-usage {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.chart-card {
  min-height: 280px;
}

.trend-chart {
  height: 280px;
  width: 100%;
}

.chart-loading,
.chart-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 260px;
  color: var(--solo-text-muted);
  gap: 8px;
}

.chart-empty i {
  font-size: 32px;
  opacity: 0.5;
}

.chart-empty p {
  font-size: 13px;
}

.table-card :deep(.n-data-table-th) {
  background: var(--solo-bg-soft);
}
</style>
