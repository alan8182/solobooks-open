<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NDatePicker, NInput, NSelect, NSpace, NSpin, NTag,
} from 'naive-ui'
import dayjs from 'dayjs'
import type { RdLedgerItem } from '@/api/rd'
import type { RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

const props = defineProps<{
  items: RdLedgerItem[]
  total: number
  loading: boolean
  projects: RdProject[]
  selectedProject: string | null
  period: [number, number]
  applicationRegion: string
  applicationBatch: string
}>()

const emit = defineEmits<{
  (e: 'update:selectedProject', val: string | null): void
  (e: 'update:period', val: [number, number]): void
  (e: 'update:applicationRegion', val: string): void
  (e: 'update:applicationBatch', val: string): void
  (e: 'search'): void
  (e: 'export'): void
}>()

const router = useRouter()

const projectOptions = computed(() => [
  { label: '全部研发项目', value: '' },
  ...props.projects.map((p) => ({ label: `${p.code} ${p.name}`, value: p.id })),
])

function setYearPreset(offset = 0) {
  const targetYear = dayjs().year() + offset
  const start = dayjs().year(targetYear).startOf('year').valueOf()
  const end = dayjs().year(targetYear).endOf('year').valueOf()
  emit('update:period', [start, end])
  emit('search')
}

function methodLabel(method: string) {
  const map: Record<string, string> = {
    direct: '直接归集',
    timesheet: '工时比',
    usage: '用量/台时',
    area: '面积分摊',
    quantity: '产出数量',
    other: '其他方法',
  }
  return map[method] || method
}

function navigateToVoucher(entryId: string) {
  if (entryId) {
    router.push(`/vouchers/${entryId}`)
  }
}
</script>

<template>
  <div class="rd-ledger-tab">
    <!-- 筛选工具栏 -->
    <NCard :bordered="false" class="filter-card mb-4">
      <div class="filter-wrapper">
        <div class="filter-left">
          <div class="filter-item">
            <span class="filter-label">期间范围</span>
            <NDatePicker
              :value="period"
              type="daterange"
              :clearable="false"
              style="width: 260px;"
              @update:value="emit('update:period', $event)"
            />
          </div>
          <div class="quick-presets">
            <NButton size="tiny" quaternary @click="setYearPreset(0)">本年</NButton>
            <NButton size="tiny" quaternary @click="setYearPreset(-1)">上年</NButton>
          </div>
          <div class="filter-item">
            <span class="filter-label">研发项目</span>
            <NSelect
              :value="selectedProject"
              :options="projectOptions"
              placeholder="按项目筛选"
              clearable
              style="width: 220px;"
              @update:value="emit('update:selectedProject', $event)"
            />
          </div>
          <div class="filter-item">
            <span class="filter-label">申报地区</span>
            <NInput
              :value="applicationRegion"
              placeholder="如：浙江/杭州（选填）"
              clearable
              style="width: 170px;"
              @update:value="emit('update:applicationRegion', $event)"
            />
          </div>
          <div class="filter-item">
            <span class="filter-label">专项批次</span>
            <NInput
              :value="applicationBatch"
              placeholder="申报批次/专项（选填）"
              clearable
              style="width: 180px;"
              @update:value="emit('update:applicationBatch', $event)"
            />
          </div>
        </div>
        <div class="filter-right">
          <NSpace>
            <NButton type="primary" secondary @click="emit('search')">
              <template #icon><i class="ri-search-line" /></template>
              查询
            </NButton>
            <NButton secondary @click="emit('export')">
              <template #icon><i class="ri-download-2-line" /></template>
              导出 Excel
            </NButton>
          </NSpace>
        </div>
      </div>
    </NCard>

    <!-- 汇总提示栏 -->
    <div class="summary-bar mb-4">
      <div class="summary-left">
        <span>已过账研发归集明细：共 <strong>{{ items.length }}</strong> 笔分录</span>
      </div>
      <div class="summary-right">
        <span>期间研发金额合计：<strong class="highlight-amount">{{ formatMoney(total) }}</strong></span>
      </div>
    </div>

    <!-- 研发辅助账表格 -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <div v-if="items.length" class="table-wrap">
          <table class="solo-table">
            <thead>
              <tr>
                <th style="width: 110px;">日期</th>
                <th style="width: 140px;">凭证号</th>
                <th style="width: 220px;">研发项目</th>
                <th>摘要</th>
                <th style="width: 110px;">费用类别</th>
                <th style="width: 110px;">科目编码</th>
                <th style="width: 180px;">分摊方法与依据</th>
                <th style="width: 130px;" class="amount">研发金额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="`${item.entryId}-${item.accountCode}`">
                <td>{{ item.date }}</td>
                <td>
                  <NButton
                    v-if="item.voucherNo"
                    text
                    type="primary"
                    size="small"
                    class="voucher-link"
                    @click="navigateToVoucher(item.entryId)"
                  >
                    <i class="ri-file-text-line" style="margin-right: 4px;" />
                    {{ item.voucherNo }}
                  </NButton>
                  <span v-else class="text-muted">-</span>
                </td>
                <td>
                  <span class="project-tag">{{ item.projectCode }}</span>
                  <span class="project-name">{{ item.projectName }}</span>
                </td>
                <td class="cell-summary" :title="item.summary">{{ item.summary }}</td>
                <td>
                  <NTag size="small" :bordered="false" type="info">
                    {{ item.expenseTypeLabel }}
                  </NTag>
                </td>
                <td class="font-mono">{{ item.accountCode }}</td>
                <td>
                  <div class="method-cell">
                    <span class="method-tag">{{ methodLabel(item.allocationMethod) }}</span>
                    <span v-if="item.allocationBasis" class="basis-note" :title="item.allocationBasis">
                      {{ item.allocationBasis }}
                    </span>
                  </div>
                </td>
                <td class="amount font-mono amount-cell">
                  {{ formatMoney(item.amount) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <SoloEmpty
          v-else
          variant="hint"
          title="所选期间暂无已过账研发费用"
          description="日常记账录入研发支出、或在工资/折旧中指定研发项目后，将自动按权责发生制归集至此。"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
.filter-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.filter-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.filter-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.quick-presets {
  display: flex;
  align-items: center;
  gap: 2px;
}
.summary-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}
.highlight-amount {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', monospace;
  margin-left: 4px;
}
.table-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.table-wrap {
  overflow-x: auto;
}
.solo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.solo-table th,
.solo-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  white-space: nowrap;
}
.solo-table th {
  color: var(--solo-text-secondary);
  font-weight: 600;
  background: var(--solo-bg-soft);
}
.amount {
  text-align: right;
}
.amount-cell {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.font-mono {
  font-family: 'Outfit', 'Inter', monospace;
  font-variant-numeric: tabular-nums;
}
.voucher-link {
  font-family: 'Outfit', 'Inter', monospace;
  font-size: 13px;
  font-weight: 500;
}
.project-tag {
  font-family: 'Outfit', monospace;
  font-size: 11px;
  background: var(--solo-bg-soft);
  color: var(--solo-primary);
  padding: 2px 6px;
  border-radius: 4px;
  margin-right: 6px;
  font-weight: 600;
}
.project-name {
  color: var(--solo-text-primary);
}
.cell-summary {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--solo-text-primary);
}
.method-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.method-tag {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.basis-note {
  font-size: 11px;
  color: var(--solo-text-muted);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.text-muted {
  color: var(--solo-text-muted);
}
</style>
