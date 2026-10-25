<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, useMessage } from 'naive-ui'
import { getEquityChanges } from '@/api/report'
import type { EquityChangesResponse } from '@/api/report'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const currentYear = new Date().getFullYear()
const initialYear = Number(route.query.year) || currentYear

const yearOptions = computed(() => {
  const options: { label: string; value: number }[] = []
  for (let year = currentYear; year >= currentYear - 5; year -= 1) {
    options.push({ label: `${year}年度`, value: year })
  }
  return options
})

const year = ref<number>(yearOptions.value.some(o => o.value === initialYear) ? initialYear : currentYear)
const loading = ref(false)
const result = ref<EquityChangesResponse | null>(null)

function handleBack() {
  router.push({ path: '/reports', query: {} })
}

async function query() {
  loading.value = true
  try {
    result.value = await getEquityChanges(year.value)
  } catch {
    message.error('查询所有者权益变动表失败')
  } finally {
    loading.value = false
  }
}

onMounted(query)
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
          <h2>所有者权益变动表</h2>
          <p class="report-subtitle">小企业会计准则简化年度口径：实收资本 / 资本公积 / 盈余公积 / 未分配利润（含本年利润）</p>
        </div>
      </div>
    </div>

    <div class="filter-bar mb-4">
      <NFormItem label="会计年度" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="year" :options="yearOptions" style="width: 150px;" @update:value="query" />
      </NFormItem>
      <NButton type="primary" @click="query">查询报表</NButton>
    </div>

    <NSpin :show="loading">
      <template v-if="result">

        <NCard :bordered="false" class="report-card mb-4">
          <div class="report-summary-row">
            <div class="report-summary-item">
              <span class="report-summary-label">本年净利润</span>
              <span class="report-summary-val" :class="result.netProfit >= 0 ? 'income' : 'expense'">{{ formatMoney(result.netProfit) }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">年初权益合计</span>
              <span class="report-summary-val">{{ formatMoney(result.totals.priorYearEnd) }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">年末权益合计</span>
              <span class="report-summary-val">{{ formatMoney(result.totals.yearEnd) }}</span>
            </div>
          </div>
        </NCard>

        <NCard :bordered="false" class="report-card">
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th class="col-item">项目</th>
                  <th style="text-align: right; width: 150px;">上年年末余额</th>
                  <th style="text-align: right; width: 150px;">本年增加</th>
                  <th style="text-align: right; width: 150px;">本年减少</th>
                  <th style="text-align: right; width: 150px;">本年年末余额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in result.rows" :key="row.key" :class="{ retained: row.isRetained }">
                  <td class="col-item">{{ row.label }}</td>
                  <td class="amount-cell">{{ formatMoney(row.priorYearEnd) }}</td>
                  <td class="amount-cell">{{ formatMoney(row.increase) }}</td>
                  <td class="amount-cell">{{ formatMoney(row.decrease) }}</td>
                  <td class="amount-cell balance">{{ formatMoney(row.yearEnd) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="total-row">
                  <td class="col-item">合计</td>
                  <td class="amount-cell">{{ formatMoney(result.totals.priorYearEnd) }}</td>
                  <td class="amount-cell">{{ formatMoney(result.totals.increase) }}</td>
                  <td class="amount-cell">{{ formatMoney(result.totals.decrease) }}</td>
                  <td class="amount-cell">{{ formatMoney(result.totals.yearEnd) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p class="simplify-note">
            简化口径说明：未分配利润行含本年利润（净利润计入本年增加，亏损计入本年减少），
            与资产负债表「权益合计 − 实收资本 − 资本公积 − 盈余公积 − 本年利润」的列示口径一致。
          </p>
        </NCard>
            </template>
      <SoloEmpty
        v-else-if="!loading"
        title="本期没有所有者权益变动"
        description="选有实收资本或利润分配的期间，这里才有内容。"
        icon="ri-exchange-line"
      />

    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-table-wrapper { overflow-x: auto; }
.col-item { min-width: 220px; }
tr.retained td { background: rgba(23, 71, 176, 0.04); }
.balance { font-weight: 700; color: var(--solo-primary); }
.simplify-note {
  margin-top: 12px;
  font-size: 12.5px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.6;
}
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 10px 16px;
  background: #ffffff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) {
  margin-bottom: 0;
  width: auto;
  flex: 0 0 auto;
}
</style>
