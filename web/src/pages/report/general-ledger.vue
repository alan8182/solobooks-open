<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NButton, useMessage,
  NSelect, NSpin, NDatePicker, NFormItem
} from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney, formatDate, getCurrentMonth } from '@/utils/format'
import { deriveOpeningBalance } from '@/utils/accounting'

interface LedgerItem {
  date: string
  summary: string
  debit: number
  credit: number
  balance: number
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const accountCode = ref('1002')
const loading = ref(false)
const items = ref<LedgerItem[]>([])

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
  { label: '5601011 销售费用-运输费', value: '5601011' },
  { label: '5601016 销售费用-业务宣传费', value: '5601016' },
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

const totalDebit = ref(0)
const totalCredit = ref(0)
const endBalance = ref(0)
// 期初余额由首行倒推（三栏式账页勾稽链：期初 + 借 - 贷 = 期末）
const openingBalance = computed(() => {
  const first = items.value[0]
  if (!first) return 0
  // Number() 兜底：金额数值化依赖 client.ts 拦截器，运算处不信任运行时类型
  return deriveOpeningBalance(accountCode.value, {
    debit: Number(first.debit),
    credit: Number(first.credit),
    balance: Number(first.balance),
  })
})

// ===== 科目汇总视图（默认）：全科目 期初/本期借贷/期末/方向 =====
const view = ref<'summary' | 'detail'>('summary')
interface SummaryRow {
  accountCode: string
  accountName: string
  openingBalance: number
  debitTotal: number
  creditTotal: number
  closingBalance: number
}
const summaryItems = ref<SummaryRow[]>([])
const totalPeriodDebit = ref(0)
const totalPeriodCredit = ref(0)
const isBalanced = ref(true)

// 余额符号约定：借余为正、贷余为负
function directionOf(value: number) {
  return value > 0 ? '借' : value < 0 ? '贷' : '平'
}
function moneyOrDash(value: number) {
  return value === 0 ? '—' : formatMoney(Math.abs(value))
}

async function querySummary() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/balance-summary', {
      params: { month: month.value },
    })
    summaryItems.value = (data.items || []).map((item: any) => ({
      accountCode: String(item.accountCode),
      accountName: String(item.accountName || ''),
      openingBalance: Number(item.openingBalance),
      debitTotal: Number(item.debitTotal),
      creditTotal: Number(item.creditTotal),
      closingBalance: Number(item.closingBalance),
    }))
    totalPeriodDebit.value = Number(data.totalDebit) || 0
    totalPeriodCredit.value = Number(data.totalCredit) || 0
    isBalanced.value = !!data.isBalanced
  } catch {
    message.error('查询科目汇总失败')
  } finally {
    loading.value = false
  }
}

function openDetail(row: SummaryRow) {
  accountCode.value = row.accountCode
  view.value = 'detail'
  query()
}

function backToSummary() {
  view.value = 'summary'
  querySummary()
}

function runQuery() {
  if (view.value === 'summary') querySummary()
  else query()
}

async function query() {
  if (!accountCode.value || !month.value) return
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/general', {
      params: { account_code: accountCode.value, month: month.value },
    })
    items.value = data.items || []
    // 金额字段以字符串传输、由 client.ts 拦截器转数值；运算处统一 Number() 兜底，防新字段名漏网
    totalDebit.value = items.value.reduce((s, i) => s + Number(i.debit), 0)
    totalCredit.value = items.value.reduce((s, i) => s + Number(i.credit), 0)
    endBalance.value = items.value.length > 0 ? Number(items.value[items.value.length - 1].balance) : 0
  } catch {
    message.error('查询总账账页失败')
  } finally {
    loading.value = false
  }
}

onMounted(querySummary)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push({ path: '/reports', query: { month } })">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>总分类账</h2>
          <p class="report-subtitle">按一级科目汇总呈现，提供期初、本期借贷变动及期末三栏式账页</p>
        </div>
      </div>
    </div>

    <!-- Filter Bar -->
    <div class="filter-bar mb-4">
      <NButton v-if="view === 'detail'" text class="report-back-btn" @click="backToSummary">
        <template #icon><i class="ri-arrow-left-line" /></template>
        返回科目汇总
      </NButton>
      <NFormItem v-if="view === 'detail'" label="分类科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="accountCode" :options="accountOptions" style="width: 220px;" filterable />
      </NFormItem>
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <NDatePicker v-model:formatted-value="month" type="month" value-format="yyyy-MM" format="yyyy-MM" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="runQuery">查询</NButton>
    </div>

    <!-- Summary Stats Card (明细视图) -->
    <NCard v-if="view === 'detail'" :bordered="false" class="report-card mb-4">
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

    <NSpin :show="loading">
      <!-- 科目汇总视图（默认）：全科目 期初/本期借贷/方向/期末 -->
      <NCard v-if="view === 'summary'" :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 110px;">科目编码</th>
                <th>科目名称</th>
                <th style="text-align: right; width: 150px;">期初余额</th>
                <th style="text-align: right; width: 140px;">借方发生</th>
                <th style="text-align: right; width: 140px;">贷方发生</th>
                <th style="width: 56px;">方向</th>
                <th style="text-align: right; width: 150px;">期末余额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in summaryItems" :key="row.accountCode" class="summary-row" @click="openDetail(row)">
                <td>{{ row.accountCode }}</td>
                <td>{{ row.accountName }}</td>
                <td class="amount-cell">
                  {{ moneyOrDash(row.openingBalance) }}
                  <span v-if="row.openingBalance !== 0" class="dir-hint">{{ directionOf(row.openingBalance) }}</span>
                </td>
                <td class="amount-cell">{{ moneyOrDash(row.debitTotal) }}</td>
                <td class="amount-cell">{{ moneyOrDash(row.creditTotal) }}</td>
                <td>{{ directionOf(row.closingBalance) }}</td>
                <td class="amount-cell balance">{{ moneyOrDash(row.closingBalance) }}</td>
              </tr>
              <tr v-if="summaryItems.length === 0">
                <td colspan="7" class="report-empty-cell">本月无科目发生额与余额</td>
              </tr>
            </tbody>
            <tfoot v-if="summaryItems.length > 0">
              <tr>
                <td colspan="3" class="total-cell">合　计</td>
                <td class="amount-cell total-cell">{{ formatMoney(totalPeriodDebit) }}</td>
                <td class="amount-cell total-cell">{{ formatMoney(totalPeriodCredit) }}</td>
                <td></td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p v-if="summaryItems.length > 0" class="balance-hint">
          <i :class="isBalanced ? 'ri-checkbox-circle-line' : 'ri-error-warning-line'" />
          {{ isBalanced ? '本期借贷发生额合计相等，试算平衡' : '本期借贷发生额合计不相等，请检查凭证' }}
        </p>
      </NCard>

      <!-- 单科目三栏账页（下钻明细） -->
      <NCard v-else :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th class="col-date">发生日期</th>
                <th class="col-summary">摘要说明</th>
                <th style="text-align: right; width: 140px;">借方金额</th>
                <th style="text-align: right; width: 140px;">贷方金额</th>
                <th style="text-align: right; width: 150px;">当前余额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="items.length" class="opening-row">
                <td></td>
                <td>期初余额</td>
                <td class="amount-cell"></td>
                <td class="amount-cell"></td>
                <td class="amount-cell balance">{{ formatMoney(openingBalance) }}</td>
              </tr>
              <tr v-for="(item, idx) in items" :key="idx">
                <td>{{ formatDate(item.date) }}</td>
                <td>{{ item.summary }}</td>
                <td class="amount-cell">{{ item.debit ? formatMoney(item.debit) : '-' }}</td>
                <td class="amount-cell">{{ item.credit ? formatMoney(item.credit) : '-' }}</td>
                <td class="amount-cell balance">{{ formatMoney(item.balance) }}</td>
              </tr>
              <tr v-if="items.length === 0">
                <td colspan="5" class="report-empty-cell">本期该科目下无具体凭证记账记录</td>
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
.balance { font-weight: 700; color: var(--solo-primary); }
.opening-row td:nth-child(2) {
  color: var(--solo-text-muted);
}
.summary-row {
  cursor: pointer;
}
.summary-row:hover td {
  background: var(--solo-primary-50, #f0f7ff);
}
.dir-hint {
  margin-left: 4px;
  font-size: 11px;
  color: var(--solo-text-muted);
}
.total-cell {
  font-weight: 700;
}
.balance-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}
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