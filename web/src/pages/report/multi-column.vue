<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getMultiColumnLedger } from '@/api/ledger'
import type { MultiColumnResponse } from '@/api/ledger'
import { formatDate, formatMoney, getCurrentMonth } from '@/utils/format'
import { SoloMonthPicker } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(String(route.query.month || '')) ? String(route.query.month) : getCurrentMonth())
const accountCode = ref(String(route.query.account_code || '5602'))
const loading = ref(false)
const result = ref<MultiColumnResponse | null>(null)

// 多栏账的读者是"看费用花在哪"的老板：默认列出有明细子科目的常用总账科目，
// 并从科目表补充全部非末级科目，避免硬编码清单漏项。
const defaultParents = [
  { label: '5602 管理费用', value: '5602' },
  { label: '5601 销售费用', value: '5601' },
  { label: '5603 财务费用', value: '5603' },
  { label: '5401 主营业务成本', value: '5401' },
  { label: '5301 营业外收入', value: '5301' },
]
const accountOptions = ref(defaultParents)

const directionLabel = computed(() => (result.value?.direction === 'credit' ? '贷方' : '借方'))

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

async function loadAccountOptions() {
  try {
    const data: any = await apiClient.get('/api/ledger/accounts')
    const parents = (data.accounts || [])
      .filter((a: any) => a.isLeaf === false)
      .map((a: any) => ({ label: `${a.code} ${a.name}`, value: a.code }))
    if (parents.length > 0) {
      // 常用科目排前，其余非末级科目按编码跟后
      const defaults = parents.filter((o: { value: string }) => defaultParents.some(d => d.value === o.value))
      const others = parents.filter((o: { value: string }) => !defaultParents.some(d => d.value === o.value))
      accountOptions.value = [...defaults, ...others]
    }
  } catch {
    // 拉不到科目表就用默认清单
  }
}

async function query() {
  if (!accountCode.value || !month.value) return
  loading.value = true
  try {
    result.value = await getMultiColumnLedger(accountCode.value, month.value)
  } catch {
    message.error('查询多栏账失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAccountOptions()
  query()
})
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
          <h2>多栏账</h2>
          <p class="report-subtitle">总账科目按明细子科目展开多栏，一眼看清费用/收入构成</p>
        </div>
      </div>
    </div>

    <div class="filter-bar mb-4">
      <NFormItem label="总账科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="accountCode" :options="accountOptions" style="width: 240px;" filterable />
      </NFormItem>
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="query">查询账页</NButton>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <NCard v-if="result.hint" :bordered="false" class="report-card">
          <div class="report-empty-cell">{{ result.hint }}</div>
        </NCard>
        <template v-else>
          <NCard :bordered="false" class="report-card mb-4">
            <div class="report-summary-row">
              <div class="report-summary-item">
                <span class="report-summary-label">科目</span>
                <span class="report-summary-val">{{ result.accountCode }} {{ result.accountName }}</span>
              </div>
              <div class="report-summary-item">
                <span class="report-summary-label">栏方向</span>
                <span class="report-summary-val">{{ directionLabel }}多栏</span>
              </div>
              <div class="report-summary-item">
                <span class="report-summary-label">{{ directionLabel }}净额合计</span>
                <span class="report-summary-val">{{ formatMoney(result.mainTotal) }}</span>
              </div>
              <div class="report-summary-item">
                <span class="report-summary-label">凭证张数</span>
                <span class="report-summary-val">{{ result.items.length }}</span>
              </div>
            </div>
          </NCard>

          <NCard :bordered="false" class="report-card">
            <div class="report-table-wrapper">
              <table class="report-table">
                <thead>
                  <tr>
                    <th class="col-date">业务日期</th>
                    <th class="col-summary">摘要说明</th>
                    <th v-for="col in result.columns" :key="col.code" class="col-column">
                      {{ col.code }}<br /><span class="col-sub-name">{{ col.name }}</span>
                    </th>
                    <th style="text-align: right; width: 110px;">借方合计</th>
                    <th style="text-align: right; width: 110px;">贷方合计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in result.items" :key="item.entryId">
                    <td>{{ formatDate(item.date) }}</td>
                    <td class="col-summary">{{ item.summary }}</td>
                    <td v-for="col in result.columns" :key="col.code" class="amount-cell">
                      {{ item.columns[col.code] && Number(item.columns[col.code]) !== 0 ? formatMoney(item.columns[col.code]) : '-' }}
                    </td>
                    <td class="amount-cell">{{ formatMoney(item.debit) }}</td>
                    <td class="amount-cell">{{ formatMoney(item.credit) }}</td>
                  </tr>
                  <tr v-if="result.items.length === 0">
                    <td :colspan="3 + result.columns.length" class="report-empty-cell">本月该科目下无凭证记账记录</td>
                  </tr>
                </tbody>
                <tfoot v-if="result.items.length > 0">
                  <tr class="total-row">
                    <td colspan="2" style="text-align: right; font-weight: 700;">本期合计</td>
                    <td v-for="col in result.columns" :key="col.code" class="amount-cell">
                      {{ Number(result.columnTotals[col.code]) !== 0 ? formatMoney(result.columnTotals[col.code]) : '-' }}
                    </td>
                    <td class="amount-cell">{{ formatMoney(result.debitTotal) }}</td>
                    <td class="amount-cell">{{ formatMoney(result.creditTotal) }}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </NCard>
        </template>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-table-wrapper { overflow-x: auto; }
.report-table th { white-space: nowrap; }
.col-date { width: 110px; }
.col-summary { min-width: 180px; }
.col-column { min-width: 110px; text-align: right; }
.col-sub-name { font-weight: 400; font-size: 12px; color: var(--solo-text-muted, #636e7c); }
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
