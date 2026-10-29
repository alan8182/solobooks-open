<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NRadioGroup, NRadioButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getBalanceSheet } from '@/api/report'
import { formatMoney, formatAmountPlain, getCurrentMonth } from '@/utils/format'
import type { BalanceSheetResponse, StandardStatementRow } from '@/api/types'
import ReportScopeNote from '@/components/business/ReportScopeNote.vue'
import StatementViewToggle from '@/components/business/StatementViewToggle.vue'
import ProStatementHeader from '@/components/business/ProStatementHeader.vue'
import { useStatementViewMode } from '@/composables/useStatementViewMode'

// ponytail: Boss-centric balance sheet: plain-language asset/liability/equity formula, neutral liability coloring
// 会计视图：会小企01表全量行次（年初/期末双列，左右对照），数据同源 standard 块
const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const { mode } = useStatementViewMode()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const periodType = ref<'month' | 'quarter'>('month')
const loading = ref(false)
const result = ref<BalanceSheetResponse | null>(null)

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

const displayPeriod = computed(() => {
  if (periodType.value === 'quarter') {
    const [year, monthValue] = month.value.split('-')
    return `${year}年第${Math.floor((Number(monthValue) - 1) / 3) + 1}季度`
  }
  const [year, monthValue] = month.value.split('-')
  return `${year}年${Number(monthValue)}月`
})

function handlePeriodTypeChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

function handleMonthChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

async function load() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    result.value = await getBalanceSheet(month.value, periodType.value)
  } catch {
    message.error('加载资产负债表失败')
  } finally {
    loading.value = false
  }
}

function drillDown(accountCode: string, accountName?: string) {
  router.push({
    path: '/reports/sub-ledger',
    query: {
      account_code: accountCode,
      account_name: accountName,
      month: month.value,
      from: 'balance-sheet',
    },
  })
}

// ── 会计视图（会小企01表）──

const statementDateText = computed(() => {
  const [year, monthValue] = month.value.split('-').map(Number)
  if (periodType.value === 'quarter') {
    const quarterEndMonth = Math.ceil(monthValue / 3) * 3
    const day = new Date(year, quarterEndMonth, 0).getDate()
    return `${year}年${quarterEndMonth}月${day}日`
  }
  const day = new Date(year, monthValue, 0).getDate()
  return `${year}年${monthValue}月${day}日`
})

/** 左右对照：资产侧与负债权益侧按行号对齐，短侧补空行 */
const stdPairs = computed(() => {
  const assets = result.value?.standard?.assets || []
  const rights = result.value?.standard?.rights || []
  const length = Math.max(assets.length, rights.length)
  return Array.from({ length }, (_, i) => ({ left: assets[i] || null, right: rights[i] || null }))
})

function stdCellClass(row: StandardStatementRow) {
  return {
    'std-section': row.kind === 'section',
    'std-subtotal': row.kind === 'subtotal',
    'std-total': row.kind === 'total',
    'std-leaf': row.kind === 'item',
  }
}

function stdClick(row: StandardStatementRow) {
  if (row.kind === 'item' && row.accountCode) {
    drillDown(row.accountCode, row.name)
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <ReportScopeNote scope="本表由系统按已过账复式分录自动编制，用于内部经营分析与报税准备" />
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>资产负债表</h2>
          <p class="report-subtitle">掌握企业特定节点的总家底、对外债务与净资产状况</p>
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
        <!-- 老板家底透视卡 -->
        <NCard :bordered="false" class="report-card boss-balance-card mb-4">
          <div class="formula-head">
            <div class="formula-title">
              <i class="ri-scales-3-line" />
              <span>企业真实家底透视（截至 {{ displayPeriod }}末）</span>
            </div>
            <div
              class="balance-status-tag"
              :class="result.isBalanced ? 'balanced' : 'unbalanced'"
            >
              <i :class="result.isBalanced ? 'ri-checkbox-circle-line' : 'ri-error-warning-line'" />
              <span>
                {{ result.isBalanced ? '报表借贷平衡（资产 = 负债 + 权益）' : '账目不平，存在差额 ' + formatMoney(Math.abs(result.totalAssets - result.totalLiabilities - result.totalEquity)) }}
              </span>
            </div>
          </div>

          <div class="formula-steps">
            <div class="step-card asset-step">
              <div class="step-header">
                <span class="step-badge asset">
                  <i class="ri-bank-card-line" />
                  <span>公司总家底</span>
                </span>
                <span class="step-alias">总资产</span>
              </div>
              <div class="step-num">{{ formatMoney(result.totalAssets) }}</div>
              <div class="step-note">存款 + 客户欠款 + 存货与设备</div>
            </div>

            <div class="step-operator">
              <span class="operator-badge">
                <i class="ri-subtract-line" />
              </span>
            </div>

            <div class="step-card liability-step">
              <div class="step-header">
                <span class="step-badge liability">
                  <i class="ri-bill-line" />
                  <span>欠外部的钱</span>
                </span>
                <span class="step-alias">总负债</span>
              </div>
              <div class="step-num">{{ formatMoney(result.totalLiabilities) }}</div>
              <div class="step-note">供应商货款 + 员工工资 + 税金</div>
            </div>

            <div class="step-operator">
              <span class="operator-badge">
                <i class="ri-equal-line" />
              </span>
            </div>

            <div class="step-card equity-step">
              <div class="step-header">
                <span class="step-badge equity">
                  <i class="ri-scales-3-line" />
                  <span>真正归属净家底</span>
                </span>
                <span class="step-alias">股东权益</span>
              </div>
              <div
                class="step-num"
                :class="Number(result.totalEquity) > 0 ? 'income' : (Number(result.totalEquity) < 0 ? 'expense' : '')"
              >
                {{ formatMoney(result.totalEquity) }}
              </div>
              <div class="step-note">投入资本 + 历年累计未分配利润</div>
            </div>
          </div>
        </NCard>

        <!-- 资产表 -->
        <NCard :bordered="false" class="report-card mb-4">
          <div class="section-title-box">
            <h3 class="report-section-title mb-0">1. 资产明细（企业拥有的资源与债权）</h3>
            <span class="unit-text">单位：元</span>
          </div>
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th class="code-col">科目代码</th>
                  <th>科目名称</th>
                  <th style="text-align: right; width: 140px;">期末余额</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in result.assets"
                  :key="item.accountCode"
                  class="clickable-row"
                  @click="drillDown(item.accountCode, item.accountName)"
                >
                  <td class="code-cell">{{ item.accountCode }}</td>
                  <td>
                    <span class="account-link">{{ item.accountName }}</span>
                    <span class="drill-tag">明细账 ↗</span>
                  </td>
                  <td class="amount-cell" :class="item.currentAmount >= 0 ? 'income' : 'expense'">
                    {{ formatMoney(item.currentAmount) }}
                  </td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="total-row">
                  <td colspan="2">资产总计（总家底）</td>
                  <td class="amount-cell income font-bold">{{ formatMoney(result.totalAssets) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>

        <!-- 负债表（修正：负债金额不再使用代表收益的绿色） -->
        <NCard :bordered="false" class="report-card mb-4">
          <div class="section-title-box">
            <h3 class="report-section-title mb-0">2. 负债明细（对供应商、员工及税局的待付款项）</h3>
            <span class="unit-text">单位：元</span>
          </div>
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th class="code-col">科目代码</th>
                  <th>科目名称</th>
                  <th style="text-align: right; width: 140px;">期末余额</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in result.liabilities"
                  :key="item.accountCode"
                  class="clickable-row"
                  @click="drillDown(item.accountCode, item.accountName)"
                >
                  <td class="code-cell">{{ item.accountCode }}</td>
                  <td>
                    <span class="account-link">{{ item.accountName }}</span>
                    <span class="drill-tag">明细账 ↗</span>
                  </td>
                  <!-- 负债是债务，使用中性色显示，不再使用 .income 绿 -->
                  <td class="amount-cell text-primary">{{ formatMoney(item.currentAmount) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="total-row">
                  <td colspan="2">负债总计（对外总欠款）</td>
                  <td class="amount-cell text-primary font-bold">{{ formatMoney(result.totalLiabilities) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>

        <!-- 所有者权益表 -->
        <NCard :bordered="false" class="report-card mb-4">
          <div class="section-title-box">
            <h3 class="report-section-title mb-0">3. 所有者权益明细（扣除负债后真正属于老板的权益）</h3>
            <span class="unit-text">单位：元</span>
          </div>
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th class="code-col">科目代码</th>
                  <th>科目名称</th>
                  <th style="text-align: right; width: 140px;">期末余额</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in result.equity"
                  :key="item.accountCode"
                  class="clickable-row"
                  @click="drillDown(item.accountCode, item.accountName)"
                >
                  <td class="code-cell">{{ item.accountCode }}</td>
                  <td>
                    <span class="account-link">{{ item.accountName }}</span>
                    <span class="drill-tag">明细账 ↗</span>
                  </td>
                  <td class="amount-cell" :class="item.currentAmount >= 0 ? 'income' : 'expense'">
                    {{ formatMoney(item.currentAmount) }}
                  </td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="total-row">
                  <td colspan="2">所有者权益总计（净资产）</td>
                  <td class="amount-cell income font-bold">{{ formatMoney(result.totalEquity) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>
        </template>

        <!-- 会计视图：会小企01表全量行次，左右对照 -->
        <template v-else>
          <NCard :bordered="false" class="report-card mb-4">
            <ProStatementHeader title="资产负债表" :period-text="statementDateText" />
            <div v-if="result.standard && !result.standard.balanced" class="std-balance-note">
              <i class="ri-error-warning-line" />
              <span>
                账套存在未结账月份，未分配利润按「资产 − 负债 − 显式权益」轧差列示；
                完成月结/年结后与科目余额口径一致。
              </span>
            </div>
            <div class="report-table-wrapper std-table-wrapper">
              <table class="report-table std-table">
                <thead>
                  <tr>
                    <th class="std-item-col">资产</th>
                    <th class="std-rowno-col">行次</th>
                    <th class="std-amt-col">年初余额</th>
                    <th class="std-amt-col">期末余额</th>
                    <th class="std-item-col std-item-col-right">负债和所有者权益</th>
                    <th class="std-rowno-col">行次</th>
                    <th class="std-amt-col">年初余额</th>
                    <th class="std-amt-col">期末余额</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(pair, idx) in stdPairs" :key="idx">
                    <td class="std-item" :class="pair.left ? stdCellClass(pair.left) : ''" :style="pair.left?.level ? { paddingLeft: '18px' } : undefined" @click="pair.left && stdClick(pair.left)">
                      <span :class="{ 'account-link': Boolean(pair.left?.accountCode) }">{{ pair.left ? pair.left.name : '' }}</span>
                    </td>
                    <td class="std-rowno">{{ pair.left?.rowNo || '' }}</td>
                    <td class="amount-cell">{{ pair.left ? formatAmountPlain(pair.left.openingAmount) : '' }}</td>
                    <td class="amount-cell">{{ pair.left ? formatAmountPlain(pair.left.currentAmount) : '' }}</td>
                    <td class="std-item" :class="pair.right ? stdCellClass(pair.right) : ''" :style="pair.right?.level ? { paddingLeft: '18px' } : undefined" @click="pair.right && stdClick(pair.right)">
                      <span :class="{ 'account-link': Boolean(pair.right?.accountCode) }">{{ pair.right ? pair.right.name : '' }}</span>
                    </td>
                    <td class="std-rowno">{{ pair.right?.rowNo || '' }}</td>
                    <td class="amount-cell">{{ pair.right ? formatAmountPlain(pair.right.openingAmount) : '' }}</td>
                    <td class="amount-cell">{{ pair.right ? formatAmountPlain(pair.right.currentAmount) : '' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="std-compiler-note">编制基础：小企业会计准则；本表由系统按已过账复式分录自动编制，正式申报请由财税人员复核。</p>
          </NCard>
        </template>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-0 { margin-bottom: 0 !important; }
.mb-4 { margin-bottom: 16px; }
.code-col { width: 90px; }
.font-bold { font-weight: 750; }

.text-primary { color: var(--solo-text-primary, #182230); }
.text-secondary { color: var(--solo-text-secondary, #586474); }
.income { color: var(--solo-income, #0f7354); }
.expense { color: var(--solo-expense, #bd3d3d); }

/* 老板家底透视卡 */
.boss-balance-card {
  padding: 18px 20px;
}

.formula-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--solo-border-light, #EDF0F4);
  padding-bottom: 10px;
  flex-wrap: wrap;
}

.formula-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
}

.balance-status-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
}

.balance-status-tag.balanced {
  background: var(--solo-income-soft, #EAF7F1);
  color: var(--solo-income, #0f7354);
}

.balance-status-tag.unbalanced {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #bd3d3d);
}

.formula-steps {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 14px;
}

.step-card {
  flex: 1;
  padding: 14px 16px;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-md, 10px);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
  box-shadow: 0 1px 2px rgba(24, 34, 48, 0.02);
  transition: all 0.2s ease;
}

.step-card:hover {
  border-color: #c7d2fe;
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.05);
}

.step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.step-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
  line-height: 1.4;
}

.step-badge.asset {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}

.step-badge.liability {
  background: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-secondary, #586474);
}

.step-badge.equity {
  background: var(--solo-income-bg, #eaf7f1);
  color: var(--solo-income, #0f7354);
}

.step-alias {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}

/* 步骤 3（净家底）：轻度高亮 */
.step-card.equity-step {
  background: var(--solo-bg-blue-soft, #edf3ff);
  border: 1px solid var(--solo-border, #dde3ea);
}

.step-card.equity-step:hover {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.06);
}

.step-num {
  font-size: 24px;
  font-weight: 700;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
  letter-spacing: -0.5px;
  color: var(--solo-text-primary, #182230);
  margin: 2px 0;
}

.step-num.income {
  color: var(--solo-income, #0f7354);
}

.step-num.expense {
  color: var(--solo-expense, #bd3d3d);
}

.step-note {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 算式连接符：精致居中圆环徽章 */
.step-operator {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.operator-badge {
  width: 26px;
  height: 26px;
  border-radius: 999px;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  box-shadow: 0 1px 3px rgba(24, 34, 48, 0.04);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--solo-text-secondary, #586474);
  font-size: 13px;
}

.section-title-box {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.unit-text {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.clickable-row {
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.clickable-row:hover {
  background: rgba(22, 93, 255, 0.05);
}

.account-link {
  font-weight: 500;
}

.drill-tag {
  margin-left: 8px;
  font-size: 11px;
  color: var(--solo-primary, #1747b0);
  opacity: 0;
  transition: opacity 0.15s ease;
}

.clickable-row:hover .drill-tag {
  opacity: 1;
}

@media (max-width: 860px) {
  .formula-steps {
    flex-direction: column;
    align-items: stretch;
  }
  .step-operator {
    text-align: center;
  }
}

/* ── 会计视图（会小企01表）── */
.std-balance-note {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border-radius: 8px;
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
  font-size: 12.5px;
}

.std-table-wrapper {
  overflow-x: auto;
}

.std-table {
  min-width: 860px;
}

.std-item-col {
  min-width: 190px;
}

.std-item-col-right {
  border-left: 2px solid var(--solo-border, #e5e9ef);
}

.std-rowno-col,
.std-rowno {
  width: 44px;
  text-align: center;
  color: var(--solo-text-muted, #636e7c);
  font-size: 12px;
}

.std-amt-col {
  text-align: right;
  width: 110px;
}

.std-item {
  white-space: nowrap;
}

.std-item.std-leaf {
  cursor: pointer;
}

.std-item.std-section {
  font-weight: 600;
}

.std-table .std-subtotal td {
  background: rgba(15, 23, 42, 0.025);
  font-weight: 600;
}

.std-table .std-total td {
  font-weight: 750;
  border-top: 1px solid var(--solo-border, #e5e9ef);
  background: rgba(15, 23, 42, 0.04);
}

.std-compiler-note {
  margin-top: 12px;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.6;
}
</style>
