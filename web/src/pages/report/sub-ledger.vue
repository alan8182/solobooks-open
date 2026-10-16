<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage,
  NSelect, NSpin, NDatePicker, NFormItem
} from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney, formatDate, getCurrentMonth } from '@/utils/format'

interface Counterparty {
  code: string
  name: string
  amount: number
}

interface SubLedgerItem {
  date: string
  summary: string
  debit: number
  credit: number
  balance: number
  counterparties: Counterparty[]
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const initialMonth = route.query.month ? String(route.query.month) : getCurrentMonth()
const initialCode = route.query.account_code ? String(route.query.account_code) : '1122'

const month = ref(initialMonth)
const accountCode = ref(initialCode)
const loading = ref(false)
const items = ref<SubLedgerItem[]>([])

const accountOptions = [
  { label: '1001 库存现金', value: '1001' },
  { label: '1002 银行存款', value: '1002' },
  { label: '1012 微信支付待结算', value: '1012' },
  { label: '1122 应收账款', value: '1122' },
  { label: '1221 其他应收款', value: '1221' },
  { label: '1403 原材料', value: '1403' },
  { label: '1405 库存商品', value: '1405' },
  { label: '1601 固定资产', value: '1601' },
  { label: '2202 应付账款', value: '2202' },
  { label: '2203 预收账款', value: '2203' },
  { label: '2211 应付职工薪酬', value: '2211' },
  { label: '2221 应交税费', value: '2221' },
  { label: '2241 其他应付款', value: '2241' },
  { label: '3001 实收资本', value: '3001' },
  { label: '3002 资本公积', value: '3002' },
  { label: '5001 主营业务收入', value: '5001' },
  { label: '5051 其他业务收入', value: '5051' },
  { label: '5301 营业外收入', value: '5301' },
  { label: '5401001 主营业务成本-人力', value: '5401001' },
  { label: '5401002 主营业务成本-生产', value: '5401002' },
  { label: '5403 税金及附加', value: '5403' },
  { label: '4301 研发支出-费用化支出', value: '4301' },
  { label: '430100101 研发支出-人员人工', value: '430100101' },
  { label: '430100102 研发支出-直接投入', value: '430100102' },
  { label: '430100103 研发支出-折旧与摊销', value: '430100103' },
  { label: '430100104 研发支出-设计费', value: '430100104' },
  { label: '430100105 研发支出-试验费', value: '430100105' },
  { label: '430100106 研发支出-委外研发费', value: '430100106' },
  { label: '430100199 研发支出-其他研发费用', value: '430100199' },
  { label: '5601 销售费用', value: '5601' },
  { label: '5601011 销售费用-运输费', value: '5601011' },
  { label: '5601016 销售费用-业务宣传费', value: '5601016' },
  { label: '5602 管理费用', value: '5602' },
  { label: '5602002 管理费用-业务招待费', value: '5602002' },
  { label: '5602004 管理费用-办公费', value: '5602004' },
  { label: '5602005 管理费用-水电费', value: '5602005' },
  { label: '5602006 管理费用-差旅费', value: '5602006' },
  { label: '5602007 管理费用-折旧费', value: '5602007' },
  { label: '5602013 管理费用-租赁费', value: '5602013' },
  { label: '5602014 管理费用-职工福利费', value: '5602014' },
  { label: '5602015 管理费用-信息技术服务费', value: '5602015' },
  { label: '5602999 管理费用-其他', value: '5602999' },
  { label: '5603002 财务费用-手续费', value: '5603002' },
  { label: '5603004 财务费用-利息收入', value: '5603004' },
  { label: '5711 营业外支出', value: '5711' },
  { label: '5801 所得税费用', value: '5801' },
]

if (initialCode && !accountOptions.some(o => o.value === initialCode)) {
  const customName = route.query.account_name ? String(route.query.account_name) : '明细科目'
  accountOptions.unshift({ label: `${initialCode} ${customName}`, value: initialCode })
}

const totalDebit = ref(0)
const totalCredit = ref(0)
const endBalance = ref(0)

const backButtonLabel = computed(() => {
  if (route.query.from === 'balance-sheet') return '返回资产负债表'
  if (route.query.from === 'income-statement') return '返回利润表'
  return '返回报表中心'
})

function handleBack() {
  if (route.query.from) {
    router.push({ path: `/reports/${route.query.from}`, query: { month: month.value } })
  } else {
    router.push({ path: '/reports', query: { month: month.value } })
  }
}

watch(() => route.query, (q) => {
  if (q.account_code) {
    accountCode.value = String(q.account_code)
    if (!accountOptions.some(o => o.value === accountCode.value)) {
      accountOptions.unshift({ label: `${accountCode.value} ${q.account_name || '明细科目'}`, value: accountCode.value })
    }
  }
  if (q.month) {
    month.value = String(q.month)
  }
  query()
})

async function query() {
  if (!accountCode.value || !month.value) return
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/sub-ledger', {
      params: { account_code: accountCode.value, month: month.value },
    })
    items.value = data.items || []
    totalDebit.value = items.value.reduce((s, i) => s + i.debit, 0)
    totalCredit.value = items.value.reduce((s, i) => s + i.credit, 0)
    endBalance.value = items.value.length > 0 ? items.value[items.value.length - 1].balance : 0
  } catch {
    message.error('查询明细账页失败')
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
          {{ backButtonLabel }}
        </NButton>
        <div class="report-title-section">
          <h2>明细分类账</h2>
          <p class="report-subtitle">按明细科目细化呈现借贷记录，并展示对应的对方科目关系</p>
        </div>
      </div>
    </div>

    <!-- Filter Bar -->
    <div class="filter-bar mb-4">
      <NFormItem label="明细科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="accountCode" :options="accountOptions" style="width: 220px;" filterable />
      </NFormItem>
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <NDatePicker v-model:formatted-value="month" type="month" value-format="yyyy-MM" format="yyyy-MM" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="query">查询账页</NButton>
    </div>

    <!-- Summary Stats Card -->
    <NCard :bordered="false" class="report-card mb-4">
      <div class="report-summary-row">
        <div class="report-summary-item">
          <span class="report-summary-label">本期借方发生额</span>
          <span class="report-summary-val">{{ formatMoney(totalDebit) }}</span>
        </div>
        <div class="report-summary-item">
          <span class="report-summary-label">本期贷方发生额</span>
          <span class="report-summary-val">{{ formatMoney(totalCredit) }}</span>
        </div>
        <div class="report-summary-item">
          <span class="report-summary-label">期末账面余额</span>
          <span class="report-summary-val income">{{ formatMoney(endBalance) }}</span>
        </div>
      </div>
    </NCard>

    <!-- Ledger Table Spin -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th class="col-date">发生日期</th>
                <th class="col-summary">摘要说明</th>
                <th class="col-cp">对应科目</th>
                <th style="text-align: right; width: 130px;">借方金额</th>
                <th style="text-align: right; width: 130px;">贷方金额</th>
                <th style="text-align: right; width: 140px;">当前余额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in items" :key="idx">
                <td>{{ formatDate(item.date) }}</td>
                <td>{{ item.summary }}</td>
                <td class="col-cp">
                  <NSpace size="small" wrap>
                    <NTag
                      v-for="(cp, ci) in item.counterparties"
                      :key="ci"
                      size="tiny"
                      :bordered="false"
                      type="info"
                      round
                    >{{ cp.code }} {{ cp.name }}</NTag>
                  </NSpace>
                </td>
                <td class="amount-cell">{{ item.debit ? formatMoney(item.debit) : '-' }}</td>
                <td class="amount-cell">{{ item.credit ? formatMoney(item.credit) : '-' }}</td>
                <td class="amount-cell balance">{{ formatMoney(item.balance) }}</td>
              </tr>
              <tr v-if="items.length === 0">
                <td colspan="6" class="report-empty-cell">本期该明细科目下无具体凭证记账记录</td>
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
.col-date { width: 110px; }
.report-table-wrapper { overflow-x: auto; }
.report-table th { white-space: nowrap; }
.report-table td.col-cp { white-space: normal; }
.col-summary { min-width: 200px; }
.col-cp { width: 190px; }
.balance { font-weight: 700; color: var(--solo-primary); }
/* Filter Bar：紧凑单行筛选 */
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