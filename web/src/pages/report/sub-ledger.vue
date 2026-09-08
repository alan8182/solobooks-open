<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage,
  NSelect, NSpin, NInput, NFormItem
} from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney, formatDate } from '@/utils/format'

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

const router = useRouter()
const message = useMessage()

const month = ref('2026-06')
const accountCode = ref('1122')
const loading = ref(false)
const items = ref<SubLedgerItem[]>([])

const accountOptions = [
  { label: '1001 库存现金', value: '1001' },
  { label: '1002 银行存款', value: '1002' },
  { label: '1012 微信支付待结算', value: '1012' },
  { label: '1122 应收账款', value: '1122' },
  { label: '1221 其他应收款', value: '1221' },
  { label: '1601 固定资产', value: '1601' },
  { label: '2202 应付账款', value: '2202' },
  { label: '2221 应交税费', value: '2221' },
  { label: '2241 其他应付款', value: '2241' },
  { label: '3001 实收资本', value: '3001' },
  { label: '5001 主营业务成本-人力', value: '5001' },
  { label: '5301 研发支出-费用化支出（待归集）', value: '5301' },
  { label: '530101 研发支出-人员人工', value: '530101' },
  { label: '530102 研发支出-直接投入', value: '530102' },
  { label: '530103 研发支出-折旧与摊销', value: '530103' },
  { label: '530104 研发支出-设计费', value: '530104' },
  { label: '530105 研发支出-试验费', value: '530105' },
  { label: '530106 研发支出-委外研发费', value: '530106' },
  { label: '530199 研发支出-其他研发费用', value: '530199' },
  { label: '5601 管理费用-差旅费', value: '5601' },
  { label: '5602 管理费用-办公费', value: '5602' },
  { label: '5603 管理费用-业务招待费', value: '5603' },
  { label: '5604 管理费用-租赁物业', value: '5604' },
  { label: '5605 管理费用-信息技术服务费', value: '5605' },
  { label: '5606 管理费用-运费及物流', value: '5606' },
  { label: '5607 管理费用-水电通讯费', value: '5607' },
  { label: '5608 管理费用-银行手续费', value: '5608' },
  { label: '5699 管理费用-其他', value: '5699' },
  { label: '6001 主营业务收入', value: '6001' },
  { label: '6051 其他业务收入', value: '6051' },
  { label: '6301 营业外收入', value: '6301' },
  { label: '6601 销售费用-推广费', value: '6601' },
]

const totalDebit = ref(0)
const totalCredit = ref(0)
const endBalance = ref(0)

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
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>明细分类账</h2>
          <p class="report-subtitle">按明细科目细化呈现借贷记录，并展示对应的对方科目关系</p>
        </div>
      </div>
    </div>

    <!-- Filter Card -->
    <NCard :bordered="false" class="report-card mb-4">
      <NSpace align="center" wrap size="large">
        <NFormItem label="明细科目" style="margin-bottom: 0;">
          <NSelect
            v-model:value="accountCode"
            :options="accountOptions"
            style="width: 240px;"
            filterable
          />
        </NFormItem>
        <NFormItem label="会计月份" style="margin-bottom: 0;">
          <NInput v-model:value="month" placeholder="YYYY-MM" style="width: 130px;" />
        </NFormItem>
        <NButton type="primary" @click="query" style="margin-top: 24px;">查询账页</NButton>
      </NSpace>
    </NCard>

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
.col-summary { }
.col-cp { width: 190px; }
.balance { font-weight: 700; color: var(--solo-primary); }
</style>
