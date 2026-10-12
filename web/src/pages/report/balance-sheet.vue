<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getBalanceSheet } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type { BalanceSheetResponse } from '@/api/types'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const month = ref(getCurrentMonth())
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
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>资产负债表</h2>
          <p class="report-subtitle">反映企业在特定日期的资产、负债及所有者权益状况</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="load"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- 资产 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">资产</h3>
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
                  <td colspan="2">资产总计</td>
                  <td class="amount-cell income">{{ formatMoney(result.totalAssets) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>

        <!-- 负债 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">负债</h3>
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
                  <td class="amount-cell income">{{ formatMoney(item.currentAmount) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="total-row">
                  <td colspan="2">负债总计</td>
                  <td class="amount-cell income">{{ formatMoney(result.totalLiabilities) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>

        <!-- 所有者权益 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">所有者权益</h3>
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
                  <td colspan="2">权益总计</td>
                  <td class="amount-cell income">{{ formatMoney(result.totalEquity) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>

        <!-- 平衡校验 -->
        <NCard :bordered="false" class="report-card check-card">
          <div v-if="result.isBalanced" class="check-ok">
            ✅ 资产 = 负债 + 所有者权益，报表平衡
          </div>
          <div v-else class="check-err">
            ❌ 资产 ≠ 负债 + 所有者权益，不平衡，差额 {{ formatMoney(Math.abs(result.totalAssets - result.totalLiabilities - result.totalEquity)) }}
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.code-col { width: 90px; }
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }
.check-card {
  text-align: center;
  font-size: 14px;
  font-weight: 700;
}
.check-ok { color: var(--solo-income); }
.check-err { color: var(--solo-expense); }

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
  color: var(--solo-primary, #165dff);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.clickable-row:hover .drill-tag {
  opacity: 1;
}
</style>
