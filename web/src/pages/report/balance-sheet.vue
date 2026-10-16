<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getBalanceSheet } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type { BalanceSheetResponse } from '@/api/types'

// ponytail: Boss-centric balance sheet: plain-language asset/liability/equity formula, neutral liability coloring
const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
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
  const [year, monthValue] = month.value.split('-')
  return `${year}年${Number(monthValue)}月`
})

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
    result.value = await getBalanceSheet(month.value)
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
          <h2>资产负债表</h2>
          <p class="report-subtitle">掌握企业特定节点的总家底、对外债务与净资产状况</p>
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
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
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
              <div class="step-label">1. 公司总家底 (总资产)</div>
              <div class="step-num text-primary">{{ formatMoney(result.totalAssets) }}</div>
              <div class="step-note">银行存款 + 客户欠款 + 存货与设备</div>
            </div>

            <div class="step-operator">－</div>

            <div class="step-card liability-step">
              <div class="step-label">2. 欠外部的钱 (总负债)</div>
              <div class="step-num text-secondary">{{ formatMoney(result.totalLiabilities) }}</div>
              <div class="step-note">欠供应商货款 + 员工工资 + 应交税费</div>
            </div>

            <div class="step-operator">＝</div>

            <div class="step-card equity-step">
              <div class="step-label">3. 属于老板的净资产 (权益)</div>
              <div class="step-num income">{{ formatMoney(result.totalEquity) }}</div>
              <div class="step-note">投入资本 + 历年经营累计未分配净利润</div>
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
.income { color: var(--solo-income, #12805C); }
.expense { color: var(--solo-expense, #C74444); }

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
  color: var(--solo-income, #12805C);
}

.balance-status-tag.unbalanced {
  background: var(--solo-expense-soft, #FBEEEE);
  color: var(--solo-expense, #C74444);
}

.formula-steps {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.step-card {
  flex: 1;
  padding: 14px 16px;
  background: var(--solo-surface-soft, #F1F4F8);
  border-radius: var(--solo-radius-md, 10px);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.step-card.asset-step {
  border-left: 3px solid var(--solo-primary, #165DFF);
}

.step-card.liability-step {
  border-left: 3px solid var(--solo-text-secondary, #586474);
}

.step-card.equity-step {
  border-left: 3px solid var(--solo-income, #12805C);
  background: var(--solo-income-soft, #EAF7F1);
}

.step-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
}

.step-num {
  font-size: 22px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.step-note {
  font-size: 11px;
  color: var(--solo-text-muted, #8A95A3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-operator {
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-muted, #8A95A3);
  flex-shrink: 0;
}

.section-title-box {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.unit-text {
  font-size: 12px;
  color: var(--solo-text-muted, #8A95A3);
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
  color: var(--solo-primary, #165DFF);
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
</style>
