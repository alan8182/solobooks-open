<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NTag, NSpin, useMessage, NSelect, NButton, NGrid, NGridItem } from 'naive-ui'
import { getTaxSummary } from '@/api/report'
import { formatMoney, formatDate } from '@/utils/format'
import type { TaxSummaryResponse } from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)

function previousCompletedQuarter(): string {
  if (hasRequestedMonth) {
    const year = requestedMonth.slice(0, 4)
    const m = parseInt(requestedMonth.slice(5, 7), 10)
    const q = Math.ceil(m / 3)
    return `${year}-Q${q}`
  }
  const now = new Date()
  const currentQuarter = Math.floor(now.getMonth() / 3) + 1
  return currentQuarter === 1 ? `${now.getFullYear() - 1}-Q4` : `${now.getFullYear()}-Q${currentQuarter - 1}`
}

function handleBack() {
  router.push({ path: '/reports', query: hasRequestedMonth ? { month: requestedMonth } : {} })
}

const period = ref(previousCompletedQuarter())
const loading = ref(false)
const result = ref<TaxSummaryResponse | null>(null)

const periodOptions = computed(() => {
  const opts: { label: string; value: string }[] = []
  const now = new Date()
  let year = now.getFullYear()
  let quarter = Math.floor(now.getMonth() / 3)
  if (quarter === 0) { year -= 1; quarter = 4 }
  for (let i = 0; i < 8; i++) {
    opts.push({ label: `${year}年第${quarter}季度`, value: `${year}-Q${quarter}` })
    quarter -= 1
    if (quarter === 0) { year -= 1; quarter = 4 }
  }
  return opts
})

const deadlineStatus = {
  upcoming: '即将开始', due_soon: '临近截止', due_today: '今日截止', overdue: '已逾期',
} as const

async function load() {
  loading.value = true
  try {
    result.value = await getTaxSummary(period.value, 'quarter')
  } catch {
    message.error('加载税务汇总失败')
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
          <h2>税务汇总及申报提醒</h2>
          <p class="report-subtitle">按季度已过账凭证汇总增值税、企业所得税预估与申报截止提醒</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSelect
          v-model:value="period"
          :options="periodOptions"
          size="small"
          style="width: 140px;"
          @update:value="load"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- Overview Grid Stats -->
        <NGrid cols="1 640:2 1024:4" :x-gap="16" :y-gap="16" class="stats-grid mb-4">
          <NGridItem>
            <div class="stat-box">
              <span class="stat-label">申报销售额</span>
              <span class="stat-value">{{ formatMoney(result.totalSales) }}</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="stat-box">
              <span class="stat-label">本期应补增值税</span>
              <span class="stat-value expense">{{ formatMoney(result.estimatedVat) }}</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="stat-box">
              <span class="stat-label">增值税免税额</span>
              <span class="stat-value income">{{ formatMoney(result.vatExemption) }}</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="stat-box">
              <span class="stat-label">预估企业所得税</span>
              <span class="stat-value expense">{{ formatMoney(result.estimatedCit) }}</span>
            </div>
          </NGridItem>
        </NGrid>

        <!-- Risk Warning Box -->
        <NCard :bordered="false" class="report-card mb-4 risk-card" v-if="!result.readyToFile">
          <div class="risk-banner">
            <i class="ri-error-warning-fill risk-icon" />
            <span>当前存在 <strong>{{ result.blockingIssues.length }}</strong> 项申报前必须确认的问题。</span>
          </div>
          <div v-for="issue in result.blockingIssues" :key="issue" class="issue-line">{{ issue }}</div>
        </NCard>

        <!-- Filing Deadlines -->
        <NCard :bordered="false" class="report-card mb-4" v-if="result.filingDeadlines.length > 0">
          <h3 class="report-section-title">税种申报截止日期</h3>
          <div class="deadline-list">
            <div v-for="d in result.filingDeadlines" :key="d.taxType" class="deadline-item">
              <div>
                <div class="deadline-name">{{ d.taxType }}</div>
                <div class="deadline-date">{{ d.filingPeriod }} · {{ formatDate(d.dueDate, 'YYYY年M月D日') }}</div>
              </div>
              <NTag size="small" :bordered="false" :type="d.status === 'overdue' ? 'error' : d.status === 'upcoming' ? 'info' : 'warning'" round>
                {{ deadlineStatus[d.status] }}{{ d.daysRemaining > 0 ? ` · 剩${d.daysRemaining}天` : '' }}
              </NTag>
            </div>
          </div>
        </NCard>

        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">企业所得税税前扣除资料状态</h3>
          <div class="deduction-grid">
            <div><span>账面支出</span><strong>{{ formatMoney(result.bookExpenses) }}</strong></div>
            <div><span>已确认可扣除</span><strong>{{ formatMoney(result.totalDeductible) }}</strong></div>
            <div><span>待确认扣除资料</span><strong class="warning-text">{{ formatMoney(result.pendingDeductibility) }}</strong></div>
            <div><span>已确认不可扣除</span><strong>{{ formatMoney(result.totalNonDeductible) }}</strong></div>
          </div>
          <p class="tax-note">小规模纳税人不抵扣进项增值税；这里展示的是企业所得税税前扣除资料状态。</p>
        </NCard>

        <!-- Reminders List -->
        <NCard :bordered="false" class="report-card" v-if="result.reminders.length > 0">
          <h3 class="report-section-title">税务提醒及合规建议</h3>
          <div class="reminder-container">
            <div v-for="warning in result.warnings" :key="warning" class="reminder-item warning-text">
              <i class="ri-error-warning-line reminder-icon" />
              <span class="reminder-text">{{ warning }}</span>
            </div>
            <div v-for="(r, i) in result.reminders" :key="i" class="reminder-item">
              <i class="ri-question-answer-line reminder-icon" />
              <span class="reminder-text">{{ r }}</span>
            </div>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }

/* Overview grid custom styling to match dashboard */
.stats-grid {
  display: grid;
}
.stat-box {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  box-shadow: var(--solo-shadow-sm);
}
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.stat-value {
  font-size: 17px;
  font-weight: 850;
  font-family: 'Outfit', 'Inter', sans-serif;
}
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }
.muted-val { color: var(--solo-text-secondary); }

/* Risk Warning Card */
.risk-card {
  border: 1px dashed rgba(239, 68, 68, 0.3) !important;
  background: rgba(239, 68, 68, 0.03) !important;
}
.risk-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13.5px;
  color: var(--solo-expense);
  font-weight: 600;
  padding: 4px 8px;
}
.risk-icon {
  font-size: 18px;
}

/* Deadlines */
.deadline-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 12px;
}
.deadline-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--solo-border-light);
}
.deadline-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.deadline-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.deadline-date { margin-top: 3px; color: var(--solo-text-muted); font-size: 12px; }
.issue-line { margin: 8px 8px 0 36px; color: var(--solo-expense); font-size: 13px; }
.deduction-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.deduction-grid > div { display: flex; flex-direction: column; gap: 6px; padding: 12px; border-radius: 10px; background: var(--solo-bg-subtle); }
.deduction-grid span { color: var(--solo-text-muted); font-size: 12px; }
.deduction-grid strong { font-size: 15px; }
.tax-note { margin: 12px 0 0; color: var(--solo-text-muted); font-size: 12px; }
.warning-text { color: var(--solo-warning, #d97706); }
@media (max-width: 760px) { .deduction-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* Reminders */
.reminder-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 12px;
}
.reminder-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.reminder-icon {
  font-size: 16px;
  color: var(--solo-primary);
  margin-top: 2px;
}
.reminder-text {
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}
</style>
