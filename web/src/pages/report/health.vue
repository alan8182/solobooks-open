<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NTag, NSpin, NButton, NSelect, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getHealthScore, getAIDiagnosis } from '@/api/report'
import { getCurrentMonth } from '@/utils/format'
import type { HealthScoreResponse, HealthPenaltyItem } from '@/api/types'

import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { GaugeChart } from 'echarts/charts'
import VChart from 'vue-echarts'
import { SoloEmpty } from '@/components/ui'

use([CanvasRenderer, GaugeChart])

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())

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

function handleMonthChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

const loading = ref(false)
const result = ref<HealthScoreResponse | null>(null)
const diagnosisLoading = ref(false)
const diagnosis = ref<any>(null)

function getLevelColor(levelCode?: string, levelStr?: string) {
  if (levelCode === 'excellent' || levelStr === '优秀') return '#0f7354'
  if (levelCode === 'good' || levelStr === '良好') return '#1747b0'
  if (levelCode === 'fair' || levelStr === '一般') return '#a05c10'
  if (levelCode === 'poor' || levelStr === '需改善') return '#bd3d3d'
  return '#1747b0'
}

function getLevelLabel(res?: HealthScoreResponse | null) {
  if (!res) return '待评估'
  return res.levelLabel || res.level || '正常'
}

function getSeverityTagType(severity: string): 'error' | 'warning' | 'info' {
  if (severity === 'high') return 'error'
  if (severity === 'medium') return 'warning'
  return 'info'
}

function getSeverityText(severity: string): string {
  if (severity === 'high') return '高危'
  if (severity === 'medium') return '关注'
  return '提示'
}

const gaugeOption = computed(() => {
  if (!result.value) return {}
  const score = Math.max(0, Math.min(100, Number(result.value.totalScore) || 0))
  const color = getLevelColor(result.value.levelCode, result.value.level)
  return {
    series: [{
      type: 'gauge',
      startAngle: 90,
      endAngle: -270,
      min: 0,
      max: 100,
      pointer: { show: false },
      progress: {
        show: true,
        width: 12,
        roundCap: true,
        itemStyle: { color },
      },
      axisLine: {
        lineStyle: { width: 12, color: [[1, '#f1f5f9']] },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        offsetCenter: [0, 0],
        valueAnimation: true,
        formatter: () => `${score}`,
        fontSize: 34,
        fontWeight: 700,
        color,
      },
      data: [{ value: score }],
    }],
  }
})

async function load() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    result.value = await getHealthScore(month.value)
  } catch {
    message.error('加载健康评分失败')
  } finally {
    loading.value = false
  }
}

async function generateDiagnosis() {
  diagnosisLoading.value = true
  try {
    diagnosis.value = await getAIDiagnosis(month.value)
  } catch {
    message.error('生成 AI 诊断失败')
  } finally {
    diagnosisLoading.value = false
  }
}

function handlePenaltyAction(item: HealthPenaltyItem) {
  if (item.targetRoute) {
    router.push(item.targetRoute)
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <!-- Header -->
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>健康评分与财税体检</h2>
          <p class="report-subtitle">基于真实账套、五项结账检查与发票数据，百分制量化评估经营合规与财务健康</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="handleMonthChange"
        />
        <NButton size="small" secondary :loading="loading" @click="load">
          <template #icon><i class="ri-refresh-line" /></template>
          重新体检
        </NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- 1. 顶部总览卡片 -->
        <NCard :bordered="false" class="report-card mb-4 hero-card">
          <div class="hero-layout">
            <div class="hero-gauge-col">
              <VChart :option="gaugeOption" autoresize style="height: 160px; width: 160px;" />
              <span class="gauge-sub">满分 100 分</span>
            </div>

            <div class="hero-info-col">
              <div class="hero-title-row">
                <span class="hero-score-val">{{ result.totalScore }}分</span>
                <NTag
                  :bordered="false"
                  size="small"
                  :color="{ textColor: '#fff', color: getLevelColor(result.levelCode, result.level) }"
                  class="hero-level-tag"
                >
                  {{ getLevelLabel(result) }}
                </NTag>
              </div>
              <p class="hero-summary">{{ result.summary || '各项财税指标评估完成，可对照扣分项排查整改。' }}</p>

              <!-- 核心指标摘要 -->
              <div v-if="result.keyMetrics && result.keyMetrics.length" class="hero-metrics-grid">
                <div v-for="(km, idx) in result.keyMetrics" :key="idx" class="hero-metric-item">
                  <span class="km-label">{{ km.label }}</span>
                  <span class="km-value" :class="'km-' + (km.status || 'pass')">{{ km.value }}</span>
                </div>
              </div>
            </div>
          </div>
        </NCard>

        <!-- 2. 五维健康度矩阵 -->
        <div class="section-title-bar">
          <h3 class="report-section-title">五维健康指标</h3>
          <span class="section-tip">涵盖账务、票据、资金、税务与盈利五大核心支柱</span>
        </div>
        <div class="dims-grid mb-4">
          <NCard
            v-for="dim in (result.dimensionDetails || [])"
            :key="dim.code"
            :bordered="false"
            class="report-card dim-card"
          >
            <div class="dim-header">
              <span class="dim-name">{{ dim.name }}</span>
              <NTag
                size="tiny"
                :bordered="false"
                :type="dim.status === 'pass' ? 'success' : (dim.status === 'warning' ? 'warning' : 'error')"
              >
                {{ dim.score }}分
              </NTag>
            </div>
            <div class="dim-bar">
              <div
                class="dim-fill"
                :style="{
                  width: Math.max(5, Math.min(100, dim.score)) + '%',
                  backgroundColor: dim.status === 'pass' ? '#0f7354' : (dim.status === 'warning' ? '#d97706' : '#dc2626')
                }"
              />
            </div>
            <div class="dim-fact">
              <i class="ri-checkbox-circle-fill dim-fact-icon" v-if="dim.status === 'pass'" />
              <i class="ri-alert-fill dim-fact-icon warn" v-else />
              <span>{{ dim.highlight }}</span>
            </div>
            <span class="dim-tip">{{ dim.tips }}</span>
          </NCard>
        </div>

        <!-- 3. 体检排查与扣分台账 (核心下钻修复闭环) -->
        <NCard :bordered="false" class="report-card mb-4">
          <div class="ledger-header">
            <div>
              <h3 class="report-section-title" style="margin-bottom: 2px;">体检扣分与风险排查台账</h3>
              <span class="section-tip">逐笔列出扣分原因、影响金额及一键修复路径</span>
            </div>
            <NTag size="small" :bordered="false" :type="(result.penalties && result.penalties.length > 0) ? 'warning' : 'success'">
              {{ (result.penalties && result.penalties.length > 0) ? `共扣减 ${result.penalties.reduce((sum, p) => sum + p.deduction, 0)} 分` : '全项满分通过' }}
            </NTag>
          </div>

          <div v-if="result.penalties && result.penalties.length > 0" class="penalty-list">
            <div v-for="item in result.penalties" :key="item.id" class="penalty-row">
              <div class="penalty-badges">
                <NTag size="small" :bordered="false" :type="getSeverityTagType(item.severity)">
                  {{ getSeverityText(item.severity) }}
                </NTag>
                <span class="deduct-pill">-{{ item.deduction }}分</span>
              </div>
              <div class="penalty-main">
                <span class="penalty-title">{{ item.title }}</span>
                <span class="penalty-desc">{{ item.description }}</span>
              </div>
              <div class="penalty-action">
                <NButton
                  v-if="item.targetRoute"
                  size="small"
                  secondary
                  type="primary"
                  @click="handlePenaltyAction(item)"
                >
                  {{ item.actionLabel || '去处理' }}
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
              </div>
            </div>
          </div>
          <div v-else class="clean-pass-box">
            <i class="ri-shield-check-fill clean-pass-icon" />
            <div>
              <span class="clean-pass-title">太棒了！当月未发现任何合规与财税扣分项</span>
              <span class="clean-pass-desc">记账分录完备、发票附着规范，请继续保持。</span>
            </div>
          </div>
        </NCard>

        <!-- 4. AI 财务经理深度点评 -->
        <NCard :bordered="false" class="report-card mb-4 diagnosis-card">
          <div class="diagnosis-header">
            <div class="diagnosis-title-row">
              <i class="ri-sparkling-2-fill diagnosis-icon"></i>
              <h3 class="report-section-title" style="margin: 0;">AI 财务经理经营点评与优化指引</h3>
            </div>
            <NButton
              v-if="!diagnosis"
              type="primary"
              size="small"
              :loading="diagnosisLoading"
              @click="generateDiagnosis"
            >
              <template #icon><i class="ri-magic-line"></i></template>
              生成深度经营洞察
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
            <span>AI 财务经理正在结合全量分录与财税政策深度审计中...</span>
          </div>

          <div v-if="diagnosis" class="diagnosis-content">
            <p class="diagnosis-text">{{ diagnosis.diagnosis }}</p>

            <div v-if="diagnosis.urgentActions && diagnosis.urgentActions.length > 0" class="urgent-actions">
              <div class="urgent-label">
                <i class="ri-alarm-warning-line"></i> 优先改进项
              </div>
              <ul>
                <li v-for="(a, i) in diagnosis.urgentActions" :key="i">{{ a }}</li>
              </ul>
            </div>

            <div class="diagnosis-disclaimer">
              {{ diagnosis.disclaimer || '本评估仅供经营决策参考，不代替正式审计报告。' }}
            </div>
          </div>
        </NCard>
      </template>

      <SoloEmpty
        v-else-if="!loading"
        title="暂无健康体检数据"
        description="请确认当月是否有录入凭证或流水记录。"
        icon="ri-pulse-line"
      />
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }

/* Hero */
.hero-card {
  padding: 12px 16px;
}
.hero-layout {
  display: flex;
  align-items: center;
  gap: 32px;
}
.hero-gauge-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.gauge-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: -12px;
}
.hero-info-col {
  flex: 1;
}
.hero-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hero-score-val {
  font-size: 30px;
  font-weight: 800;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.hero-level-tag {
  font-weight: 600;
  border-radius: 4px;
}
.hero-summary {
  font-size: 14px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
  margin: 8px 0 16px;
}
.hero-metrics-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.hero-metric-item {
  padding: 8px 14px;
  background: var(--solo-bg-page);
  border-radius: var(--solo-radius-sm);
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 120px;
}
.km-label {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.km-value {
  font-size: 15px;
  font-weight: 700;
}
.km-pass { color: var(--solo-income, #0f7354); }
.km-warning { color: var(--solo-warning, #d97706); }
.km-danger { color: var(--solo-expense, #dc2626); }

/* Section Header */
.section-title-bar {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 12px;
}
.section-tip {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* Dimensions Grid */
.dims-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 12px;
}
.dim-card {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dim-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.dim-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.dim-bar {
  height: 6px;
  background: #f1f5f9;
  border-radius: 999px;
  overflow: hidden;
}
.dim-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s ease;
}
.dim-fact {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-primary);
}
.dim-fact-icon {
  font-size: 14px;
  color: #0f7354;
}
.dim-fact-icon.warn {
  color: #d97706;
}
.dim-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.4;
}

/* Penalties Ledger */
.ledger-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.penalty-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.penalty-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 14px;
  background: var(--solo-bg-page);
  border-radius: var(--solo-radius-sm);
  border-left: 3px solid #d97706;
}
.penalty-badges {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.deduct-pill {
  font-size: 12px;
  font-weight: 700;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
  padding: 2px 6px;
  border-radius: 4px;
}
.penalty-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.penalty-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.penalty-desc {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.penalty-action {
  flex-shrink: 0;
}
.clean-pass-box {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: rgba(15, 115, 84, 0.05);
  border-radius: var(--solo-radius-sm);
  border: 1px dashed rgba(15, 115, 84, 0.25);
}
.clean-pass-icon {
  font-size: 32px;
  color: #0f7354;
}
.clean-pass-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: #0f7354;
}
.clean-pass-desc {
  display: block;
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}

/* AI Diagnosis */
.diagnosis-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.diagnosis-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.diagnosis-icon {
  font-size: 18px;
  color: var(--solo-primary);
}
.diagnosis-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}
.loading-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--solo-primary);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
.diagnosis-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--solo-text-primary);
  margin: 0 0 16px;
  padding: 14px 16px;
  background: rgba(99, 102, 241, 0.04);
  border-radius: var(--solo-radius-sm);
  border-left: 3px solid var(--solo-primary);
}
.urgent-actions {
  margin-bottom: 14px;
  padding: 12px 14px;
  background: rgba(245, 158, 11, 0.06);
  border-radius: var(--solo-radius-sm);
  border-left: 3px solid var(--solo-warning);
}
.urgent-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-warning);
  margin-bottom: 6px;
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
  padding-top: 10px;
  border-top: 1px solid var(--solo-border-light);
}

@media (max-width: 768px) {
  .hero-layout {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }
}
</style>
