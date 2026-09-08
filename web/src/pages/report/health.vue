<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NTag, NSpin, NButton, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getHealthScore, getAIDiagnosis } from '@/api/report'
import type { HealthScoreResponse } from '@/api/types'

import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { GaugeChart, BarChart } from 'echarts/charts'
import VChart from 'vue-echarts'

use([CanvasRenderer, GaugeChart, BarChart])

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const loading = ref(false)
const result = ref<HealthScoreResponse | null>(null)
const diagnosisLoading = ref(false)
const diagnosis = ref<any>(null)

const dimensionLabels: Record<string, string> = {
  bookkeepingCompletion: '记账完整度',
  taxRiskControl: '税务风险控制',
  cashFlowHealth: '现金流健康',
  complianceLevel: '合规水平',
  dataIntegrity: '数据完整性',
}

function getLevelColor(level: string) {
  const map: Record<string, string> = {
    excellent: '#10b981',
    good: '#3b82f6',
    fair: '#f59e0b',
    poor: '#ef4444',
  }
  return map[level] || '#64748b'
}

function getLevelLabel(level: string) {
  const map: Record<string, string> = {
    excellent: '优秀',
    good: '良好',
    fair: '一般',
    poor: '待改善',
  }
  return map[level] || level
}

const gaugeOption = computed(() => {
  if (!result.value) return {}
  const score = Math.min(result.value.totalScore, 100)
  const color = getLevelColor(result.value.level)
  return {
    series: [{
      type: 'gauge',
      startAngle: 90,
      endAngle: -270,
      pointer: { show: false },
      progress: {
        show: true,
        width: 14,
        roundCap: true,
        itemStyle: { color },
      },
      axisLine: {
        lineStyle: { width: 14, color: [[1, '#f0f0f0']] },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        offsetCenter: [0, 0],
        valueAnimation: true,
        formatter: () => `${score}分`,
        fontSize: 28,
        fontWeight: 700,
        color,
      },
      data: [{ value: score }],
    }],
  }
})

const dimensionOption = computed(() => {
  if (!result.value?.dimensions) return {}
  const dims = Object.entries(result.value.dimensions)
  return {
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
    },
    grid: { left: 90, right: 40, top: 10, bottom: 10 },
    xAxis: {
      type: 'value' as const,
      max: 100,
      axisLabel: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'category' as const,
      data: dims.map(([k]) => dimensionLabels[k] || k),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { fontSize: 12, color: '#666' },
    },
    series: [{
      type: 'bar',
      data: dims.map(([, v]) => ({
        value: v,
        itemStyle: {
          color: v >= 80 ? '#10b981' : v >= 60 ? '#f59e0b' : '#ef4444',
          borderRadius: [0, 4, 4, 0],
        },
      })),
      barMaxWidth: 16,
    }],
  }
})

async function load() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    const now = new Date()
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    result.value = await getHealthScore(month)
  } catch {
    message.error('加载健康评分失败')
  } finally {
    loading.value = false
  }
}

async function generateDiagnosis() {
  diagnosisLoading.value = true
  try {
    const now = new Date()
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    diagnosis.value = await getAIDiagnosis(month)
  } catch {
    message.error('生成 AI 诊断失败')
  } finally {
    diagnosisLoading.value = false
  }
}

onMounted(load)
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
          <h2>健康评分</h2>
          <p class="report-subtitle">结合系统记账数据和 AI 深度审计，全方位评估财务合规与风险</p>
        </div>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- Score gauge -->
        <NCard :bordered="false" class="report-card mb-4 score-card">
          <VChart :option="gaugeOption" autoresize style="height: 200px" />
          <NTag :bordered="false" :color="{ textColor: '#fff', color: getLevelColor(result.level) }">
            {{ getLevelLabel(result.level) }}
          </NTag>
        </NCard>

        <!-- Dimension bars -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">各维度评分</h3>
          <VChart :option="dimensionOption" autoresize style="height: 220px" />
        </NCard>

        <!-- Suggestions -->
        <NCard :bordered="false" class="report-card mb-4" v-if="result.suggestions.length > 0">
          <h3 class="report-section-title">改进建议</h3>
          <ul class="suggest-list">
            <li v-for="(s, i) in result.suggestions" :key="i" class="suggest-item">
              {{ s }}
            </li>
          </ul>
        </NCard>

        <NCard :bordered="false" class="report-card mb-4" v-if="result.riskItems && result.riskItems.length > 0">
          <h3 class="report-section-title">税务风险提示</h3>
          <ul class="suggest-list risk">
            <li v-for="(item, i) in result.riskItems" :key="i" class="suggest-item">
              {{ item }}
            </li>
          </ul>
        </NCard>

        <!-- AI Diagnosis Section -->
        <NCard :bordered="false" class="report-card mb-4 diagnosis-card">
          <div class="diagnosis-header">
            <div class="diagnosis-title-row">
              <i class="ri-robot-2-line diagnosis-icon"></i>
              <h3 class="report-section-title" style="margin: 0;">AI 财务经理点评</h3>
            </div>
            <NButton
              v-if="!diagnosis"
              type="primary"
              size="small"
              :loading="diagnosisLoading"
              @click="generateDiagnosis"
            >
              <template #icon><i class="ri-sparkling-2-fill"></i></template>
              生成 AI 诊断
            </NButton>
            <NButton
              v-else
              size="small"
              quaternary
              :loading="diagnosisLoading"
              @click="generateDiagnosis"
            >
              重新生成
            </NButton>
          </div>

          <div v-if="diagnosisLoading && !diagnosis" class="diagnosis-loading">
            <div class="loading-pulse"></div>
            <span>AI 财务经理正在分析您的经营数据...</span>
          </div>

          <div v-if="diagnosis" class="diagnosis-content">
            <p class="diagnosis-text">{{ diagnosis.diagnosis }}</p>

            <div v-if="diagnosis.keyMetrics && diagnosis.keyMetrics.length > 0" class="metrics-grid">
              <div v-for="(m, i) in diagnosis.keyMetrics" :key="i" class="metric-item">
                <span class="metric-label">{{ m.label }}</span>
                <span class="metric-value">
                  {{ m.value }}
                  <i v-if="m.trend === 'up'" class="ri-arrow-up-s-fill trend-up"></i>
                  <i v-else-if="m.trend === 'down'" class="ri-arrow-down-s-fill trend-down"></i>
                </span>
                <span class="metric-comment">{{ m.comment }}</span>
              </div>
            </div>

            <div v-if="diagnosis.urgentActions && diagnosis.urgentActions.length > 0" class="urgent-actions">
              <div class="urgent-label">
                <i class="ri-alarm-warning-line"></i> 需要关注
              </div>
              <ul>
                <li v-for="(a, i) in diagnosis.urgentActions" :key="i">{{ a }}</li>
              </ul>
            </div>

            <div class="diagnosis-disclaimer">
              {{ diagnosis.disclaimer }}
            </div>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }

/* Score */
.score-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 0;
  gap: 12px;
}

/* Suggestions */
.suggest-list {
  margin: 0;
  padding-left: 18px;
}
.suggest-item {
  font-size: 13px;
  color: var(--solo-text-primary);
  line-height: 1.6;
  margin-bottom: 6px;
}
.risk .suggest-item {
  color: var(--solo-expense);
}

/* AI Diagnosis */
.diagnosis-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.diagnosis-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.diagnosis-icon {
  font-size: 20px;
  color: var(--solo-primary);
}
.diagnosis-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 24px 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}
.loading-pulse {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--solo-primary);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
.diagnosis-content {
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.diagnosis-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--solo-text-primary);
  margin: 0 0 16px;
  padding: 16px;
  background: rgba(99, 102, 241, 0.04);
  border-radius: var(--solo-radius);
  border-left: 3px solid var(--solo-primary);
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px;
  background: var(--solo-bg-page);
  border-radius: var(--solo-radius-sm);
}
.metric-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.metric-value {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.trend-up {
  color: var(--solo-income);
}
.trend-down {
  color: var(--solo-expense);
}
.metric-comment {
  font-size: 11px;
  color: var(--solo-text-secondary);
}
.urgent-actions {
  margin-bottom: 16px;
  padding: 12px;
  background: rgba(245, 158, 11, 0.06);
  border-radius: var(--solo-radius-sm);
  border-left: 3px solid var(--solo-warning);
}
.urgent-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-warning);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.urgent-actions ul {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--solo-text-primary);
  line-height: 1.6;
}
.diagnosis-disclaimer {
  font-size: 11px;
  color: var(--solo-text-muted);
  text-align: center;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
</style>
