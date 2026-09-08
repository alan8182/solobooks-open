<script setup lang="ts">
import { h, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NSpace, NSpin, NDataTable, NSelect, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getComparison } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const loading = ref(false)
const result = ref<any>(null)
const baseMonth = ref(getCurrentMonth())
const compareMode = ref<'mom' | 'yoy'>('mom')

const monthOptions = computed(() => {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    const label = `${d.getFullYear()}年${d.getMonth() + 1}月`
    options.push({ label, value: val })
  }
  return options
})

const summaryColumns = [
  { title: '财务指标', key: 'metric', width: 120 },
  { title: '基期金额', key: 'base', width: 140 },
  { title: '对比期金额', key: 'compare', width: 140 },
  {
    title: '变动比例', key: 'change', width: 120,
    render: (row: any) => {
      if (row.change === null || row.change === undefined) return '-'
      const color = row.change >= 0 ? 'var(--solo-income)' : 'var(--solo-expense)'
      const prefix = row.change >= 0 ? '+' : ''
      return h('span', { style: { color, fontWeight: '700', fontFamily: 'Inter' } }, `${prefix}${row.change}%`)
    },
  },
]

const categoryColumns = [
  { title: '支出分类', key: 'category' },
  { title: '基期金额', key: 'base_amount', width: 140, render: (row: any) => formatMoney(row.base_amount) },
  { title: '对比期金额', key: 'compare_amount', width: 140, render: (row: any) => formatMoney(row.compare_amount) },
  {
    title: '变动比例', key: 'change_percent', width: 120,
    render: (row: any) => {
      if (row.change_percent === null || row.change_percent === undefined) return '-'
      const color = row.change_percent >= 0 ? 'var(--solo-income)' : 'var(--solo-expense)'
      const prefix = row.change_percent >= 0 ? '+' : ''
      return h('span', { style: { color, fontWeight: '700', fontFamily: 'Inter' } }, `${prefix}${row.change_percent}%`)
    },
  },
]

const summaryData = computed(() => {
  if (!result.value) return []
  return [
    { metric: '营业收入', base: formatMoney(result.value.base_income), compare: formatMoney(result.value.compare_income), change: result.value.income_change_percent },
    { metric: '成本费用', base: formatMoney(result.value.base_expense), compare: formatMoney(result.value.compare_expense), change: result.value.expense_change_percent },
    { metric: '净利润额', base: formatMoney(result.value.base_profit), compare: formatMoney(result.value.compare_profit), change: result.value.profit_change_percent },
  ]
})

async function doQuery() {
  if (!baseMonth.value) {
    message.warning('请选择基准月份')
    return
  }
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    const data = await getComparison(
      baseMonth.value,
      compareMode.value,
    )
    result.value = data
  } catch {
    message.error('查询同比/环比分析失败')
  } finally {
    loading.value = false
  }
}

onMounted(doQuery)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>同比/环比分析</h2>
          <p class="report-subtitle">对比不同时期的收入、支出及利润，追踪企业经营效益的发展趋势</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSpace align="center" size="small">
          <NSelect
            v-model:value="baseMonth"
            placeholder="选择月份"
            style="width: 140px;"
            :options="monthOptions"
            @update:value="doQuery"
          />
          <NButton
            :type="compareMode === 'mom' ? 'primary' : 'default'"
            size="small"
            secondary
            @click="compareMode = 'mom'; doQuery()"
          >环比 (MOM)</NButton>
          <NButton
            :type="compareMode === 'yoy' ? 'primary' : 'default'"
            size="small"
            secondary
            @click="compareMode = 'yoy'; doQuery()"
          >同比 (YOY)</NButton>
        </NSpace>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">核心财务数据对比</h3>
          <p class="text-muted">基期月份：<strong>{{ result.base_month }}</strong> 与 对比月份：<strong>{{ result.compare_month }}</strong>（{{ compareMode === 'mom' ? '环比上月' : '同比去年同期' }}）</p>
          <NDataTable
            :columns="summaryColumns"
            :data="summaryData"
            :bordered="false"
            :single-line="true"
            size="small"
            class="report-data-table"
          />
        </NCard>

        <NCard :bordered="false" class="report-card">
          <h3 class="report-section-title">支出分类明细对比</h3>
          <NDataTable
            :columns="categoryColumns"
            :data="result.category_breakdown || []"
            :bordered="false"
            :single-line="true"
            size="small"
            class="report-data-table"
          />
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
  margin-bottom: 12px;
}
.text-muted strong {
  color: var(--solo-primary);
}
.report-data-table :deep(.n-data-table-thead th) {
  background-color: var(--solo-border-light) !important;
  font-weight: 700;
  font-size: 12px;
}
</style>
