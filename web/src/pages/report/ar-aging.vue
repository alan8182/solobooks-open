<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
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

interface AgingResult {
  account_code: string
  as_of: string
  buckets: AgingBucket[]
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
    data.value = await apiClient.get('/api/report/ar-aging')
  } catch {
    message.error('加载应收账龄失败')
  } finally {
    loading.value = false
  }
}

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
          <h2>应收账龄</h2>
          <p class="report-subtitle">分析未收回的款项及其到期账龄，优化催收和现金回笼排期</p>
        </div>
      </div>
      <div class="report-header-right">
        <!-- ponytail: toggle between AR and AP aging without duplicating navigation items -->
        <NRadioGroup value="ar" size="small" @update:value="(val: string) => val === 'ap' && router.push({ path: '/reports/ap-aging', query: month ? { month } : {} })">
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
            <span class="report-summary-label">未收笔数</span>
            <span class="report-summary-val">{{ data?.count || 0 }}</span>
          </div>
          <div class="report-summary-item">
            <span class="report-summary-label">未收总额</span>
            <span class="report-summary-val income">{{ formatMoney(data?.total || 0) }}</span>
          </div>
        </div>
      </NCard>

      <div v-for="bucket in data?.buckets || []" :key="bucket.label" class="mb-4">
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
</style>