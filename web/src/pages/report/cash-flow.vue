<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NTag, NRadioGroup, NRadioButton } from 'naive-ui'
import { getCashFlow } from '@/api/report'
import { formatMoney, formatAmountPlain, getCurrentMonth } from '@/utils/format'
import type { CashFlowResponse } from '@/api/types'
import StatementViewToggle from '@/components/business/StatementViewToggle.vue'
import ProStatementHeader from '@/components/business/ProStatementHeader.vue'
import { useStatementViewMode } from '@/composables/useStatementViewMode'
import { SoloEmpty } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { mode } = useStatementViewMode()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const periodType = ref<'month' | 'quarter'>('month')
const loading = ref(false)
const result = ref<CashFlowResponse | null>(null)

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

function handlePeriodTypeChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

// 会计视图表头的期间文字（与月报/季报口径一致）
const proPeriodText = computed(() => {
  const [year, monthValue] = month.value.split('-').map(Number)
  if (periodType.value === 'quarter') {
    return `${year}年第${Math.floor((monthValue - 1) / 3) + 1}季度`
  }
  return `${year}年${monthValue}月`
})

interface CashFlowProRow {
  name: string
  amount?: number | string
  kind: 'item' | 'section' | 'subtotal' | 'total'
}

/** 会小企03表节次结构：一二三类活动 + 现金净增加额 + 期初/期末 */
const proRows = computed<CashFlowProRow[]>(() => {
  const data = result.value
  if (!data) return []
  const rows: CashFlowProRow[] = []
  const pushItems = (title: string, items: CashFlowResponse['operating'], net: number | string, netLabel: string) => {
    rows.push({ name: title, kind: 'section' })
    for (const item of items) {
      rows.push({ name: item.description, amount: item.netAmount, kind: 'item' })
    }
    rows.push({ name: netLabel, amount: net, kind: 'subtotal' })
  }
  pushItems('一、经营活动产生的现金流量：', data.operating, data.operatingNet, '经营活动产生的现金流量净额')
  pushItems('二、投资活动产生的现金流量：', data.investing, data.investingNet, '投资活动产生的现金流量净额')
  pushItems('三、筹资活动产生的现金流量：', data.financing, data.financingNet, '筹资活动产生的现金流量净额')
  rows.push({ name: '四、现金净增加额', amount: data.netIncrease, kind: 'subtotal' })
  rows.push({ name: '加：期初现金余额', amount: data.openingCash, kind: 'item' })
  rows.push({ name: '五、期末现金余额', amount: data.endingCash, kind: 'total' })
  return rows
})

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

async function load() {
  loading.value = true
  try {
    result.value = await getCashFlow(month.value, periodType.value)
  } catch {
    message.error('加载现金流量表失败')
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
          <h2>现金流量表</h2>
          <p class="report-subtitle">按经营、投资及筹资三分类活动，分析当期现金流量变动及净增加额</p>
        </div>
      </div>
      <div class="report-header-right">
        <StatementViewToggle style="margin-right: 8px;" />
        <NRadioGroup
          v-model:value="periodType"
          size="small"
          style="margin-right: 8px;"
          @update:value="handlePeriodTypeChange"
        >
          <NRadioButton value="month">月报</NRadioButton>
          <NRadioButton value="quarter">季报</NRadioButton>
        </NRadioGroup>
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="handleMonthChange"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <template v-if="mode === 'boss'">

        <!-- 经营活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">经营活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.operating" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : 'expense'">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">经营活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.operatingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.operatingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 投资活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">投资活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.investing" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : item.outflow > 0 ? 'expense' : ''">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">投资活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.investingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.investingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 筹资活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">筹资活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.financing" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : item.outflow > 0 ? 'expense' : ''">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">筹资活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.financingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.financingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 汇总 -->
        <NCard :bordered="false" class="report-card check-card">
          <div class="cf-row">
            <span class="cf-label-summary">加：期初现金余额</span>
            <span class="cf-amt">{{ formatMoney(result.openingCash) }}</span>
          </div>
          <div class="cf-row bold large">
            <span class="cf-label-summary">本期现金及现金等价物净增加额</span>
            <span class="cf-amt-summary" :class="result.netIncrease >= 0 ? 'income' : 'expense'">
              {{ formatMoney(result.netIncrease) }}
            </span>
          </div>
          <div class="cf-divider" />
          <div class="cf-row bold large">
            <span class="cf-label-summary">期末现金余额</span>
            <span class="cf-amt-summary">{{ formatMoney(result.endingCash) }}</span>
          </div>
          <div class="reconciliation-list">
            <div class="reconciliation-row">
              <span>三类活动净额＝现金净增加额</span>
              <NTag size="small" :type="result.classificationBalanced ? 'success' : 'error'">
                {{ result.classificationBalanced ? '已勾稽' : `差额 ${formatMoney(result.classificationDifference)}` }}
              </NTag>
            </div>
            <div class="reconciliation-row">
              <span>期初现金＋净增加额＝期末现金</span>
              <NTag size="small" :type="result.cashRollForwardBalanced ? 'success' : 'error'">
                {{ result.cashRollForwardBalanced ? '已勾稽' : `差额 ${formatMoney(result.cashRollForwardDifference)}` }}
              </NTag>
            </div>
            <div class="reconciliation-row">
              <span>期末现金＝资产负债表货币资金</span>
              <NTag size="small" :type="result.balanceSheetCashBalanced ? 'success' : 'error'">
                {{ result.balanceSheetCashBalanced ? '已勾稽' : `差额 ${formatMoney(result.balanceSheetCashDifference)}` }}
              </NTag>
            </div>
          </div>
        </NCard>
        </template>

        <!-- 会计视图：会小企03表节次结构 -->
        <template v-else>
          <NCard :bordered="false" class="report-card">
            <ProStatementHeader title="现金流量表" :period-text="proPeriodText" />
            <div class="report-table-wrapper">
              <table class="report-table pro-cf-table">
                <thead>
                  <tr>
                    <th class="cf-item-col">项目</th>
                    <th class="cf-amt-col">金额</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in proRows" :key="row.name" :class="`pro-${row.kind}`">
                    <td class="cf-item-cell">{{ row.name }}</td>
                    <td class="amount-cell">{{ row.amount === undefined ? '' : formatAmountPlain(row.amount) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="pro-cf-reconciliation">
              <span>勾稽：三类活动净额＝现金净增加额 {{ result.classificationBalanced ? '✓' : `差额 ${formatAmountPlain(result.classificationDifference)}` }}</span>
              <span>期初现金＋净增加额＝期末现金 {{ result.cashRollForwardBalanced ? '✓' : `差额 ${formatAmountPlain(result.cashRollForwardDifference)}` }}</span>
              <span>期末现金＝资产负债表货币资金 {{ result.balanceSheetCashBalanced ? '✓' : `差额 ${formatAmountPlain(result.balanceSheetCashDifference)}` }}</span>
            </div>
            <p class="pro-compiler-note">编制基础：小企业会计准则；金额单位：元，支出以负数列示。</p>
          </NCard>
        </template>
      </template>
      <SoloEmpty
        v-else-if="!loading"
        title="本期没有资金流水"
        description="去记账或导入银行流水后，这里会展示钱从哪来到哪去。"
        icon="ri-exchange-funds-line"
      />

    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.cf-row-group {
  padding: 4px 12px;
}
.cf-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  font-size: 13.5px;
}
.cf-detail {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 6px 16px;
  font-size: 13px;
  border-left: 2px solid var(--solo-border-light);
  margin-left: 4px;
  background: rgba(0,0,0,0.005);
}
.cf-label {
  color: var(--solo-text-secondary);
}
.cf-amt {
  font-family: 'Outfit', 'Inter', monospace;
  font-weight: 600;
  white-space: nowrap;
}
.cf-empty {
  padding: 10px 16px;
  color: var(--solo-text-muted);
  font-size: 13px;
  font-style: italic;
}
.cf-divider {
  height: 1px;
  background: var(--solo-border-light);
  margin: 6px 0;
}
.bold .cf-label,
.bold .cf-amt {
  font-weight: 750;
  color: var(--solo-text-primary);
}
.check-card {
  padding: 16px 20px;
}
.cf-label-summary {
  font-size: 14.5px;
  font-weight: 750;
}
.cf-amt-summary {
  font-size: 20px;
  font-weight: 800;
  font-family: 'Outfit', 'Inter', sans-serif;
}
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }
.reconciliation-list {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
.reconciliation-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0;
  color: var(--solo-text-secondary);
  font-size: 13px;
}

/* ── 会计视图 ── */
.pro-cf-table {
  min-width: 560px;
}

.cf-item-col {
  min-width: 360px;
}

.cf-amt-col {
  text-align: right;
  width: 140px;
}

.cf-item-cell {
  white-space: nowrap;
}

.pro-cf-table .pro-section td {
  font-weight: 600;
}

.pro-cf-table .pro-subtotal td {
  font-weight: 600;
  background: rgba(15, 23, 42, 0.025);
}

.pro-cf-table .pro-total td {
  font-weight: 750;
  border-top: 1px solid var(--solo-border, #e5e9ef);
  background: rgba(15, 23, 42, 0.04);
}

.pro-cf-reconciliation {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border-light, #edf0f4);
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.pro-compiler-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.6;
}
</style>
