<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NCard,
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
import { useAdminStore } from '@/stores/admin'
import { formatAdminDateTime, formatAdminNumber } from '@/composables/useAdminTable'
import { aiSceneLabel as sceneLabel } from '@/utils/constants'
import { SoloTable, SoloDrawer, SoloStatusBadge, SoloButton } from '@/components/ui'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const message = useMessage()
const adminStore = useAdminStore()

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

const activeRecentCall = ref<UsageRecentItem | null>(null)
const drawerVisible = ref(false)

function fmtDate(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function fmtDuration(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}min`
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

function openRecentDrawer(item: UsageRecentItem) {
  activeRecentCall.value = item
  drawerVisible.value = true
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
    { title: 'Prompt Tokens', key: 'promptTokens', width: 120, align: 'right', render: (row: any) => formatAdminNumber(row.promptTokens) },
    { title: 'Completion Tokens', key: 'completionTokens', width: 140, align: 'right', render: (row: any) => formatAdminNumber(row.completionTokens) },
    { title: '总 Tokens', key: 'totalTokens', width: 120, align: 'right', render: (row: any) => formatAdminNumber(row.totalTokens) },
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

const recentColumns: DataTableColumns<UsageRecentItem> = [
  { title: '时间', key: 'createdAt', width: 155, render: (row) => formatAdminDateTime(row.createdAt) },
  { title: '业务场景', key: 'scene', width: 110, render: (row) => sceneLabel(row.scene) },
  {
    title: '模型',
    key: 'model',
    width: 150,
    render: (row) => h('span', { class: 'monospace-font' }, row.model),
  },
  {
    title: '归属公司 / 用户',
    key: 'target',
    minWidth: 160,
    render: (row) =>
      h('div', { class: 'caller-cell' }, [
        h('span', { class: 'caller-company' }, row.companyId || '—'),
        h('span', { class: 'caller-user' }, row.userId || '—'),
      ]),
  },
  { title: '消耗 Tokens', key: 'totalTokens', width: 110, align: 'right', render: (row) => formatAdminNumber(row.totalTokens) },
  { title: '耗时', key: 'durationMs', width: 85, align: 'right', render: (row) => fmtDuration(row.durationMs) },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => {
      if (row.status === 'ok') {
        return h(SoloStatusBadge, { tone: 'success', size: 'small' }, { default: () => '正常' })
      }
      if (row.status === 'circuit_open') {
        return h(SoloStatusBadge, { tone: 'warning', size: 'small' }, { default: () => '已熔断' })
      }
      return h(SoloStatusBadge, { tone: 'danger', size: 'small' }, { default: () => row.errorType || '异常' })
    },
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
        areaStyle: { color: 'rgba(23, 71, 176, 0.08)' },
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

// 联动全局刷新信号
watch(
  () => adminStore.refreshSignal,
  () => {
    refreshAll()
  },
)

onMounted(refreshAll)
</script>

<template>
  <div class="admin-usage">
    <NCard :bordered="false" class="filter-card">
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
        <SoloButton size="small" variant="secondary" @click="refreshAll">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新
        </SoloButton>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          按 UTC 日界统计；成本估算按千 tokens 定价表测算
          <template v-if="!hasPricing">（当前未配置单价表）</template>
        </span>
      </div>
    </NCard>

    <NCard title="用量变化走势（所选范围）" :bordered="false" class="chart-card">
      <div v-if="loading" class="chart-loading"><NSpin size="medium" /></div>
      <VChart v-else-if="usage?.rows.length" :option="chartOption" class="trend-chart" autoresize />
      <div v-else class="chart-empty">
        <i class="ri-line-chart-line" />
        <p>该日期范围内暂无调用量数据</p>
      </div>
    </NCard>

    <NCard :title="`用量多维聚合（按${dimensionLabel}）`" :bordered="false" class="table-card">
      <SoloTable
        :columns="groupColumns"
        :data="usage?.rows ?? []"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
        :max-height="360"
        empty-title="暂无维度用量数据"
      />
    </NCard>

    <NCard title="最近调用明细" :bordered="false" class="table-card">
      <template #header-extra>
        <NTag size="small" round :bordered="false">最近 50 条调用流水</NTag>
      </template>
      <SoloTable
        :columns="recentColumns"
        :data="recent"
        :loading="recentLoading"
        :bordered="false"
        size="small"
        clickable
        :pagination="false"
        :max-height="420"
        :scroll-x="960"
        empty-title="暂无最近 LLM 调用明细"
        @row-click="openRecentDrawer"
      />
    </NCard>

    <!-- 最近调用详情抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="activeRecentCall ? `LLM 调用详情 · ${activeRecentCall.model}` : '调用详情'"
      :width="540"
    >
      <div v-if="activeRecentCall" class="call-drawer-body">
        <div class="call-hero-card">
          <div class="call-icon-box">
            <i class="ri-cpu-line" />
          </div>
          <div class="call-hero-meta">
            <h3>{{ activeRecentCall.model }}</h3>
            <span class="call-scene-badge">{{ sceneLabel(activeRecentCall.scene) }}</span>
          </div>
          <SoloStatusBadge
            :tone="activeRecentCall.status === 'ok' ? 'success' : activeRecentCall.status === 'circuit_open' ? 'warning' : 'danger'"
          >
            {{ activeRecentCall.status === 'ok' ? '正常调用' : activeRecentCall.status === 'circuit_open' ? '已熔断' : activeRecentCall.errorType || '异常' }}
          </SoloStatusBadge>
        </div>

        <div class="detail-section">
          <h4 class="section-title">Token 与耗时指标</h4>
          <div class="metric-grid">
            <div class="metric-card">
              <span class="metric-lbl">总 Tokens</span>
              <span class="metric-val primary">{{ formatAdminNumber(activeRecentCall.totalTokens) }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-lbl">响应耗时</span>
              <span class="metric-val">{{ fmtDuration(activeRecentCall.durationMs) }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-lbl">Prompt Tokens</span>
              <span class="metric-val">{{ formatAdminNumber(activeRecentCall.promptTokens) }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-lbl">Completion Tokens</span>
              <span class="metric-val">{{ formatAdminNumber(activeRecentCall.completionTokens) }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">上下文与归属</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">调用时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeRecentCall.createdAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">调用场景</span>
              <span class="info-val">{{ sceneLabel(activeRecentCall.scene) }} ({{ activeRecentCall.scene }})</span>
            </div>
            <div class="info-item">
              <span class="info-label">用户 ID</span>
              <span class="info-val monospace">{{ activeRecentCall.userId || '—' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">公司账套 ID</span>
              <span class="info-val monospace">{{ activeRecentCall.companyId || '—' }}</span>
            </div>
          </div>
        </div>

        <div v-if="activeRecentCall.errorType" class="detail-section error-section">
          <h4 class="section-title">异常信息</h4>
          <div class="error-detail-box">
            <span class="error-type-tag">{{ activeRecentCall.errorType }}</span>
          </div>
        </div>
      </div>
    </SoloDrawer>
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

.monospace-font {
  font-family: monospace;
  font-size: 12.5px;
}

.caller-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
  font-family: monospace;
  font-size: 12px;
}

.caller-company {
  color: var(--solo-text-primary);
}

.caller-user {
  color: var(--solo-text-muted);
}

/* 抽屉样式 */
.call-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0;
}

.call-hero-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.call-icon-box {
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

.call-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.call-hero-meta h3 {
  margin: 0;
  font-size: 15.5px;
  font-weight: 650;
  color: var(--solo-text-primary);
  font-family: monospace;
}

.call-scene-badge {
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

.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 12px 14px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.metric-lbl {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.metric-val {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.metric-val.primary {
  color: var(--solo-primary);
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

.error-detail-box {
  padding: 12px 14px;
  background: var(--solo-expense-bg, #fbeeee);
  border: 1px solid var(--solo-expense, #bd3d3d);
  border-radius: var(--solo-radius, 8px);
}

.error-type-tag {
  color: var(--solo-expense, #bd3d3d);
  font-weight: 600;
  font-size: 13px;
}
</style>
