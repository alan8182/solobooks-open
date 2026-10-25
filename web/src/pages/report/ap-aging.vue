<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NTag, useMessage, NSpin, NRadioGroup, NRadioButton } from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney, formatDate } from '@/utils/format'

interface AgingItem {
  date: string
  title: string
  amount: number
  direction: string
  days: number
  bucket: string
}

interface AgingBucket {
  label: string
  total: number
  count: number
  items: AgingItem[]
}

interface AgingGroup {
  partnerId: string
  partnerName: string
  unassigned: boolean
  total: number
  count: number
  buckets: { label: string; total: number; count: number }[]
}

interface AgingResult {
  account_code: string
  as_of: string
  group_by?: 'none' | 'partner'
  buckets: AgingBucket[]
  groups?: AgingGroup[]
  unassigned?: AgingGroup | null
  total: number
  count: number
}

const route = useRoute()
const router = useRouter()
const message = useMessage()
const month = computed(() => String(route.query.month || ''))

function handleBack() {
  router.push({ path: '/reports', query: month.value ? { month: month.value } : {} })
}

const loading = ref(false)
const data = ref<AgingResult | null>(null)
// 分组维度：none = 凭证粒度；partner = 按往来单位
const groupBy = ref<'none' | 'partner'>('partner')
const bucketLabels = ['30天内', '31-60天', '61-90天', '91-120天', '120天以上']

const groupedRows = computed(() => {
  if (!data.value?.groups) return []
  return data.value.unassigned ? [...data.value.groups, data.value.unassigned] : data.value.groups
})

function bucketAmount(group: AgingGroup, label: string) {
  return Number(group.buckets.find((bucket) => bucket.label === label)?.total || 0)
}

const bucketColors: Record<string, string> = {
  '30天内': 'success',
  '31-60天': 'info',
  '61-90天': 'warning',
  '91-120天': 'error',
  '120天以上': 'error',
}

async function load() {
  loading.value = true
  try {
    data.value = await apiClient.get('/api/report/ap-aging', { params: { group_by: groupBy.value } })
  } catch {
    message.error('加载应付账龄失败')
  } finally {
    loading.value = false
  }
}

watch(groupBy, load)

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>应付账龄</h2>
          <p class="report-subtitle">分析未结清的应付账款及其账龄分布，优化支出款项排期</p>
        </div>
      </div>
      <div class="report-header-right">
        <NRadioGroup v-model:value="groupBy" size="small">
          <NRadioButton value="partner">按往来单位</NRadioButton>
          <NRadioButton value="none">按凭证</NRadioButton>
        </NRadioGroup>
        <!-- ponytail: toggle between AR and AP aging without duplicating navigation items -->
        <NRadioGroup value="ap" size="small" @update:value="(val: string) => val === 'ar' && router.push({ path: '/reports/ar-aging', query: month ? { month } : {} })">
          <NRadioButton value="ar">应收账款 (客户欠款)</NRadioButton>
          <NRadioButton value="ap">应付账款 (待付款项)</NRadioButton>
        </NRadioGroup>
      </div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card mb-4">
        <div class="report-summary-row">
          <div class="report-summary-item">
            <span class="report-summary-label">截至日期</span>
            <span class="report-summary-val">{{ data?.as_of || '-' }}</span>
          </div>
          <div class="report-summary-item">
            <span class="report-summary-label">未结笔数</span>
            <span class="report-summary-val">{{ data?.count || 0 }}</span>
          </div>
          <div class="report-summary-item">
            <span class="report-summary-label">未结总额</span>
            <span class="report-summary-val">{{ formatMoney(data?.total || 0) }}</span>
          </div>
        </div>
      </NCard>

      <!-- 按往来单位：账龄分析表（行=单位，列=账龄区间） -->
      <NCard v-if="groupBy === 'partner'" :bordered="false" class="report-card mb-4">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th>往来单位</th>
                <th v-for="label in bucketLabels" :key="label" style="width: 110px; text-align: right;">{{ label }}</th>
                <th style="width: 130px; text-align: right;">合计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="group in groupedRows" :key="group.partnerId || 'unassigned'" :class="{ 'unassigned-row': group.unassigned }">
                <td>{{ group.partnerName }}</td>
                <td v-for="label in bucketLabels" :key="label" class="amount-cell">
                  {{ bucketAmount(group, label) ? formatMoney(bucketAmount(group, label)) : '—' }}
                </td>
                <td class="amount-cell balance">{{ formatMoney(Number(group.total)) }}</td>
              </tr>
              <tr v-if="groupedRows.length === 0">
                <td :colspan="bucketLabels.length + 2" class="report-empty-cell">暂无应付账龄数据</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="group-hint">
          <i class="ri-information-line" />
          「未指定往来单位」为未挂往来档案的应付分录；到「辅助核算档案」补齐或在凭证上补挂后即可归组。
        </p>
      </NCard>

      <div v-for="bucket in groupBy === 'partner' ? [] : (data?.buckets || [])" :key="bucket.label" class="mb-4">
        <NCard :bordered="false" class="report-card">
          <div class="bucket-header">
            <NTag :type="(bucketColors[bucket.label] || 'default') as any" :bordered="false" round size="small">
              {{ bucket.label }}
            </NTag>
            <span class="bucket-total">{{ bucket.count }} 笔，合计 {{ formatMoney(bucket.total) }}</span>
          </div>
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th class="col-date">日期</th>
                  <th class="col-title">摘要</th>
                  <th class="col-dir">方向</th>
                  <th class="col-amount">金额</th>
                  <th class="col-days">天数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, idx) in bucket.items" :key="idx">
                  <td>{{ formatDate(item.date) }}</td>
                  <td>{{ item.title }}</td>
                  <td>{{ item.direction }}</td>
                  <td class="amount-cell">{{ formatMoney(item.amount) }}</td>
                  <td class="col-days">{{ item.days }}天</td>
                </tr>
              </tbody>
            </table>
          </div>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.bucket-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.bucket-total {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.col-date { width: 110px; }
.col-title { }
.col-dir { width: 60px; }
.col-amount { width: 130px; }
.col-days { width: 80px; text-align: right; }
.report-header-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.unassigned-row td { color: var(--solo-text-muted); font-style: italic; }
.group-hint { display: flex; align-items: center; gap: 6px; margin: 12px 0 0; font-size: 12px; color: var(--solo-text-muted); }
</style>