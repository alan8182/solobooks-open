<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  NTooltip,
} from 'naive-ui'
import type { SchedulePreview } from '@/api/tax-registration'
import { SoloEmpty } from '@/components/ui'
import {
  deadlineStatusText,
  deadlineStatusType,
  monthLabel,
  periodLabel,
} from '@/utils/taxRegistration'

const props = defineProps<{
  preview: SchedulePreview | null
  loading: boolean
  year: number
  previewMode: 'dueMonth' | 'period'
}>()

const emit = defineEmits<{
  'update:year': [value: number]
  'update:previewMode': [value: 'dueMonth' | 'period']
}>()

const router = useRouter()

const yearOptions = computed(() => {
  const current = new Date().getFullYear()
  return [current - 1, current, current + 1].map((value) => ({ label: `${value} 年`, value }))
})

interface PreviewBucket {
  key: string
  label: string
  dueText: string
  isQuarterMajor: boolean
  items: Array<{
    key: string
    name: string
    period: string
    status: string
    returnGroup: string
  }>
}

const previewBuckets = computed<PreviewBucket[]>(() => {
  if (!props.preview) return []
  if (props.previewMode === 'period') {
    return props.preview.byPeriod.map((bucket) => {
      const isQuarter = bucket.period.includes('Q')
      return {
        key: bucket.period,
        label: periodLabel(bucket.period),
        dueText: bucket.dueDate,
        isQuarterMajor: isQuarter || bucket.items.length > 2,
        items: bucket.items.map((item) => ({
          key: `${item.returnGroup}-${bucket.period}`,
          name: item.name,
          period: bucket.period,
          status: item.status,
          returnGroup: item.returnGroup,
        })),
      }
    })
  }

  return props.preview.byDueMonth.map((bucket) => {
    const earliest = bucket.items.reduce(
      (min, item) => (item.dueDate < min ? item.dueDate : min),
      bucket.items[0]?.dueDate ?? '',
    )
    const hasVatOrCit = bucket.items.some(
      (it) => it.returnGroup === 'vat' || it.returnGroup === 'cit_prepay',
    )
    return {
      key: bucket.month,
      label: monthLabel(bucket.month),
      dueText: earliest,
      isQuarterMajor: hasVatOrCit || bucket.items.length > 2,
      items: bucket.items.map((item) => ({
        key: `${item.returnGroup}-${item.period}`,
        name: item.name,
        period: item.period,
        status: item.status,
        returnGroup: item.returnGroup,
      })),
    }
  })
})

function openFiling(period: string) {
  void router.push({ path: '/tax-filing', query: { period } })
}
</script>

<template>
  <NCard class="preview-card" :bordered="false">
    <template #header>
      <div class="card-head">
        <div class="card-head-left">
          <span class="card-title">申报节奏预览</span>
          <span class="card-sub">
            按认定表铺出的 {{ year }} 年应报清单，点击税种卡片可直达对应属期的「税务申报」
          </span>
        </div>
        <NSpace align="center" :size="8">
          <NSelect
            :value="year"
            :options="yearOptions"
            size="small"
            style="width: 108px"
            @update:value="emit('update:year', $event)"
          />
          <NRadioGroup
            :value="previewMode"
            size="small"
            @update:value="emit('update:previewMode', $event)"
          >
            <NRadioButton value="dueMonth">按截止月份</NRadioButton>
            <NRadioButton value="period">按属期</NRadioButton>
          </NRadioGroup>
        </NSpace>
      </div>
    </template>

    <NSpin :show="loading">
      <div v-if="previewBuckets.length" class="timeline">
        <div
          v-for="bucket in previewBuckets"
          :key="bucket.key"
          class="timeline-item"
          :class="{ 'timeline-item--major': bucket.isQuarterMajor }"
        >
          <div class="timeline-head">
            <div class="timeline-head-left">
              <span class="timeline-label">{{ bucket.label }}</span>
              <span v-if="bucket.dueText" class="timeline-due">截止 {{ bucket.dueText }}</span>
              <NTag
                v-if="bucket.isQuarterMajor"
                size="tiny"
                type="info"
                :bordered="false"
                round
                class="quarter-badge"
              >
                季度综合大税期
              </NTag>
            </div>
            <span class="timeline-count">共 {{ bucket.items.length }} 项申报</span>
          </div>

          <div class="chip-row">
            <NTooltip v-for="item in bucket.items" :key="item.key" placement="top">
              <template #trigger>
                <button
                  type="button"
                  class="chip"
                  :class="[
                    `chip--${deadlineStatusType(item.status)}`,
                    { 'chip--highlight': item.returnGroup === 'vat' || item.returnGroup === 'cit_prepay' }
                  ]"
                  @click="openFiling(item.period)"
                >
                  <i
                    v-if="item.status === 'filed'"
                    class="ri-checkbox-circle-fill chip-icon"
                  />
                  <i
                    v-else-if="item.status === 'overdue'"
                    class="ri-alert-fill chip-icon"
                  />
                  <i
                    v-else
                    class="ri-file-text-line chip-icon"
                  />
                  <span>{{ item.name }}</span>
                </button>
              </template>
              {{ periodLabel(item.period) }} · {{ deadlineStatusText(item.status) }}（点击前往申报）
            </NTooltip>
          </div>
        </div>
      </div>

      <SoloEmpty
        v-else
        title="还没有可预览的申报清单"
        description="同步或带出认定表后，这里会自动生成全年的纳税日历与征期明细。"
        icon="ri-calendar-check-line"
        class="preview-empty"
      />
    </NSpin>
  </NCard>
</template>

<style scoped>
.preview-card {
  margin-bottom: 20px;
  border-radius: 10px;
  border: 1px solid var(--solo-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-head-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.card-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.timeline-item {
  padding: 14px 12px;
  border-bottom: 1px solid var(--solo-border);
  transition: background-color 0.15s ease;
  border-radius: 6px;
}
.timeline-item:last-child {
  border-bottom: none;
}
.timeline-item:hover {
  background-color: var(--solo-bg-soft);
}
.timeline-item--major {
  background-color: rgba(23, 71, 176, 0.015);
}
.timeline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.timeline-head-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.timeline-label {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.timeline-due {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
  font-family: Inter, monospace;
}
.quarter-badge {
  font-size: 10px;
  padding: 0 6px;
}
.timeline-count {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  font-size: 12px;
  color: var(--solo-text-primary);
  cursor: pointer;
  transition: all 0.15s ease;
  line-height: 1.4;
}
.chip-icon {
  font-size: 13px;
  color: var(--solo-text-muted);
}
.chip:hover {
  border-color: var(--solo-primary-light);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(23, 71, 176, 0.08);
}
.chip--highlight {
  font-weight: 550;
  background: var(--solo-bg-soft);
}
.chip--success {
  color: var(--solo-income);
  border-color: color-mix(in srgb, var(--solo-income) 35%, transparent);
}
.chip--success .chip-icon {
  color: var(--solo-income);
}
.chip--warning {
  color: var(--solo-warning);
  border-color: color-mix(in srgb, var(--solo-warning) 35%, transparent);
}
.chip--warning .chip-icon {
  color: var(--solo-warning);
}
.chip--default {
  color: var(--solo-text-secondary);
}
.preview-empty {
  padding: 40px 0;
}
@media (max-width: 768px) {
  .card-head {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
