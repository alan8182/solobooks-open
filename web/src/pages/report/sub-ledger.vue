<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage,
  NSelect, NSpin, NDatePicker, NFormItem
} from 'naive-ui'
import { getLedgerAccounts, getLedgerBook } from '@/api/ledger'
import type { LedgerDetailLine } from '@/api/ledger'
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
  // 数量金额式（P1 WP-D）：数量科目行才带
  quantityIn?: string
  quantityOut?: string
  unit?: string | null
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

const accountOptions = ref<{ label: string; value: string }[]>([])

/** 科目下拉取权威科目表：硬编码清单会让用户自建的科目在账簿里查不到。 */
async function loadAccounts() {
  try {
    const data = await getLedgerAccounts()
    accountOptions.value = data.accounts
      .map((item) => ({ label: `${item.code} ${item.name}`, value: item.code }))
      .sort((a, b) => a.value.localeCompare(b.value))
  } catch {
    accountOptions.value = []
  }
}

/** URL 带来的科目即使不在科目表里也要能显示（历史链接、已停用科目） */
function ensureInitialOption(code: string, name?: string) {
  if (!code || accountOptions.value.some((item) => item.value === code)) return
  accountOptions.value.unshift({ label: `${code} ${name || '明细科目'}`, value: code })
}

const totalDebit = ref(0)
const totalCredit = ref(0)
const endBalance = ref(0)

// 余额符号约定：借余为正、贷余为负（与科目汇总/电子账簿同口径）
function directionOf(value: number) {
  return value > 0 ? '借' : value < 0 ? '贷' : '平'
}
function moneyOrDash(value: number) {
  return value === 0 ? '—' : formatMoney(Math.abs(value))
}
const hasQuantity = computed(() => items.value.some((item) => item.quantityIn != null || item.quantityOut != null))

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
    ensureInitialOption(accountCode.value, q.account_name ? String(q.account_name) : undefined)
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
    // 与电子账簿同一条取数路径（P7-S1）：单科目查询模式，父科目/容器科目由
    // 服务端用同一批分录前缀合成聚合页，粒度为凭证行（与账簿明细页一致）。
    const data = await getLedgerBook({ month: month.value, accountCode: accountCode.value })
    const page = data.details[0]
    items.value = (page?.lines || []).map(toLedgerItem)
    totalDebit.value = Number(page?.periodTotals.debit) || 0
    totalCredit.value = Number(page?.periodTotals.credit) || 0
    endBalance.value = page ? Number(page.closing) : 0
  } catch {
    message.error('查询明细账页失败')
  } finally {
    loading.value = false
  }
}

/** 账簿明细行 → 页面行：数量科目的单笔移动按借贷拆进收入/发出数量列 */
function toLedgerItem(line: LedgerDetailLine): SubLedgerItem {
  const isDebit = Number(line.debit) !== 0
  return {
    date: line.date,
    summary: line.backfill ? `${line.summary}（补记）` : line.summary,
    debit: Number(line.debit),
    credit: Number(line.credit),
    balance: Number(line.balance),
    counterparties: line.counterparties.map((cp) => ({
      code: cp.code,
      name: cp.name,
      amount: Number(cp.amount),
    })),
    ...(line.quantity != null
      ? {
          quantityIn: isDebit ? line.quantity : undefined,
          quantityOut: !isDebit ? line.quantity : undefined,
          unit: line.unit,
        }
      : {}),
  }
}

onMounted(async () => {
  await loadAccounts()
  ensureInitialOption(initialCode, route.query.account_name ? String(route.query.account_name) : undefined)
  await query()
})
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
          <span class="report-summary-val">{{ moneyOrDash(endBalance) }}<span v-if="endBalance" class="dir-hint">{{ directionOf(endBalance) }}</span></span>
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
                <th v-if="hasQuantity" style="text-align: right; width: 90px;">收入数量</th>
                <th v-if="hasQuantity" style="text-align: right; width: 90px;">发出数量</th>
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
                <td v-if="hasQuantity" class="amount-cell">{{ item.quantityIn ? `${item.quantityIn}${item.unit || ''}` : '-' }}</td>
                <td v-if="hasQuantity" class="amount-cell">{{ item.quantityOut ? `${item.quantityOut}${item.unit || ''}` : '-' }}</td>
                <td class="amount-cell">{{ item.debit ? formatMoney(item.debit) : '-' }}</td>
                <td class="amount-cell">{{ item.credit ? formatMoney(item.credit) : '-' }}</td>
                <td class="amount-cell balance">{{ moneyOrDash(item.balance) }}<span v-if="item.balance" class="dir-hint">{{ directionOf(item.balance) }}</span></td>
              </tr>
              <tr v-if="items.length === 0">
                <td :colspan="hasQuantity ? 8 : 6" class="report-empty-cell">本期该明细科目下无具体凭证记账记录</td>
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
.dir-hint {
  margin-left: 4px;
  font-size: 11px;
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