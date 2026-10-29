<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSpin, NTag, useMessage } from 'naive-ui'
import { getChronologicalLedger } from '@/api/ledger'
import type { ChronologicalResponse } from '@/api/ledger'
import { formatDate, formatMoney, getCurrentMonth } from '@/utils/format'
import { SoloMonthPicker } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(String(route.query.month || '')) ? String(route.query.month) : getCurrentMonth())
const loading = ref(false)
const items = ref<ChronologicalResponse['items']>([])
const voucherCount = ref(0)
const totalDebit = ref(0)
const totalCredit = ref(0)
const isBalanced = ref(true)

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

watch(() => route.query.month, (value) => {
  if (value) {
    month.value = String(value)
    query()
  }
})

async function query() {
  loading.value = true
  try {
    const data = await getChronologicalLedger(month.value)
    items.value = data.items || []
    voucherCount.value = data.voucherCount
    totalDebit.value = data.totalDebit
    totalCredit.value = data.totalCredit
    isBalanced.value = data.isBalanced
  } catch {
    message.error('查询序时账失败')
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
          <h2>序时账</h2>
          <p class="report-subtitle">本月全部已过账凭证按编号顺序逐笔平铺，与凭证列表同口径</p>
        </div>
      </div>
    </div>

    <div class="filter-bar mb-4">
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="query">查询账页</NButton>
    </div>

    <NCard :bordered="false" class="report-card mb-4">
      <div class="report-summary-row">
        <div class="report-summary-item">
          <span class="report-summary-label">凭证张数</span>
          <span class="report-summary-val">{{ voucherCount }}</span>
        </div>
        <div class="report-summary-item">
          <span class="report-summary-label">借方合计</span>
          <span class="report-summary-val">{{ formatMoney(totalDebit) }}</span>
        </div>
        <div class="report-summary-item">
          <span class="report-summary-label">贷方合计</span>
          <span class="report-summary-val">{{ formatMoney(totalCredit) }}</span>
        </div>
        <div class="report-summary-item">
          <span class="report-summary-label">试算状态</span>
          <NTag size="small" :type="isBalanced ? 'success' : 'error'" :bordered="false" round>
            {{ isBalanced ? '借贷平衡' : '借贷不平' }}
          </NTag>
        </div>
      </div>
    </NCard>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th class="col-voucher">凭证号</th>
                <th class="col-date">业务日期</th>
                <th class="col-summary">摘要说明</th>
                <th class="col-account">会计科目</th>
                <th style="text-align: right; width: 130px;">借方金额</th>
                <th style="text-align: right; width: 130px;">贷方金额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in items" :key="`${item.entryId}-${idx}`">
                <td class="col-voucher">{{ item.voucherRef || '-' }}</td>
                <td>{{ formatDate(item.date) }}</td>
                <td class="col-summary">{{ item.summary }}</td>
                <td class="col-account">{{ item.accountCode }} {{ item.accountName }}</td>
                <td class="amount-cell">{{ item.direction === 'debit' ? formatMoney(item.debit) : '-' }}</td>
                <td class="amount-cell">{{ item.direction === 'credit' ? formatMoney(item.credit) : '-' }}</td>
              </tr>
              <tr v-if="items.length === 0">
                <td colspan="6" class="report-empty-cell">本月暂无已过账凭证</td>
              </tr>
            </tbody>
          </table>
        </div>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-table-wrapper { overflow-x: auto; }
.report-table th { white-space: nowrap; }
.col-voucher { width: 90px; }
.col-date { width: 110px; }
.col-summary { min-width: 200px; }
.col-account { min-width: 200px; white-space: normal; }
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
