<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NButton, useMessage,
  NSelect, NSpin, NDatePicker, NFormItem
} from 'naive-ui'
import { getLedgerAccounts, getLedgerBook } from '@/api/ledger'
import type { LedgerDetailPage, LedgerGeneralPage } from '@/api/ledger'
import * as XLSX from 'xlsx'
import { formatMoney, formatDate, getCurrentMonth } from '@/utils/format'
import { deriveOpeningBalance } from '@/utils/accounting'
import { saveWorkbook } from '@/utils/export'
import { SoloMonthPicker } from '@/components/ui'

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

const totalDebit = ref(0)
const totalCredit = ref(0)
const endBalance = ref(0)
// 期初余额：优先用账簿明细页自带的开账数（零发生额月份也有真实期初），
// 兜底由首行倒推（三栏式账页勾稽链：期初 + 借 - 贷 = 期末）
const detailOpening = ref<number | null>(null)
const openingBalance = computed(() => {
  if (detailOpening.value !== null) return detailOpening.value
  const first = items.value[0]
  if (!first) return 0
  // Number() 兜底：金额数值化依赖 client.ts 拦截器，运算处不信任运行时类型
  return deriveOpeningBalance({
    debit: Number(first.debit),
    credit: Number(first.credit),
    balance: Number(first.balance),
  })
})

// ===== 视图组织 =====
// 「总分类账」的会计定义：按科目 × 月份汇总借贷发生与余额（一年 12 行），
// 不是逐笔流水。曾经默认视图是科目汇总、下钻是单科目逐笔——前者其实是科目
// 余额表，后者其实是明细账，标题与内容对不上（P7-S2）。现在：
//   annual  总账（年度）：每科目 12 个月汇总行 + 本年合计，数据复用电子账簿 general 段
//   summary 科目余额表：全科目 期初/本期借贷/期末
//   detail  明细流水：单科目本期逐笔（从余额表行点击钻取）
const view = ref<'annual' | 'summary' | 'detail'>('annual')

// ===== 总账视图（年度汇总）=====
const annualYear = ref(String(Number(getCurrentMonth().slice(0, 4))))
const annualAccount = ref('all')
const generalPages = ref<LedgerGeneralPage[]>([])
const generalYear = ref<number | null>(null)

/** 借或贷：接口给的是方向枚举，与余额符号无关 */
function directionLabel(direction: 'debit' | 'credit' | 'flat' | undefined) {
  return direction === 'debit' ? '借' : direction === 'credit' ? '贷' : '平'
}

const visibleGeneralPages = computed(() => {
  if (annualAccount.value === 'all') return generalPages.value
  return generalPages.value.filter((page) => page.accountCode === annualAccount.value)
})

/** 总账数据复用电子账簿接口（scope=year 的 general 段）：一次请求、与账簿完全同源 */
async function loadAnnual() {
  loading.value = true
  try {
    const data = await getLedgerBook({ year: Number(annualYear.value), scope: 'year' })
    generalPages.value = data.general?.items || []
    generalYear.value = data.general?.year ?? Number(annualYear.value)
  } catch {
    message.error('查询总账失败')
  } finally {
    loading.value = false
  }
}

function switchView(target: 'annual' | 'summary') {
  if (view.value === target) return
  view.value = target
  if (target === 'annual') {
    // 从月份视图切过来时保持年度上下文一致
    annualYear.value = String(Number(month.value.slice(0, 4)))
    if (generalYear.value !== Number(annualYear.value)) loadAnnual()
  } else if (target === 'summary' && summaryItems.value.length === 0) {
    querySummary()
  }
}
interface SummaryRow {
  accountCode: string
  accountName: string
  openingBalance: number
  debitTotal: number
  creditTotal: number
  closingBalance: number
  // 容器科目（1221/2211/2221）子级合计行：仅展示，不可下钻、不计入合计
  isParent: boolean
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
    // 与电子账簿同一条取数路径（P7-S1）：balance 段字段兼容，另含方向与零行标记。
    // showZeroRows=true 保持本页"完整科目表"的既有口径，零行由用户在账簿页过滤。
    const data = await getLedgerBook({ month: month.value, showZeroRows: true })
    summaryItems.value = (data.balance.items || []).map((item) => ({
      accountCode: String(item.accountCode),
      accountName: String(item.accountName || ''),
      openingBalance: Number(item.openingBalance),
      debitTotal: Number(item.debitTotal),
      creditTotal: Number(item.creditTotal),
      closingBalance: Number(item.closingBalance),
      isParent: !!item.isParent,
    }))
    totalPeriodDebit.value = Number(data.balance.totalDebit) || 0
    totalPeriodCredit.value = Number(data.balance.totalCredit) || 0
    isBalanced.value = !!data.balance.isBalanced
  } catch {
    message.error('查询科目汇总失败')
  } finally {
    loading.value = false
  }
}

function openDetail(row: SummaryRow) {
  if (row.isParent) return
  accountCode.value = row.accountCode
  view.value = 'detail'
  query()
}

// 导出科目余额表：期初/期末按「金额 + 方向」双列呈现（标准余额表形态），金额为正数
function exportSummary() {
  if (summaryItems.value.length === 0) {
    message.warning('当前无可导出的科目余额数据')
    return
  }
  const rows = summaryItems.value.map(row => ({
    '科目编码': row.accountCode,
    '科目名称': row.isParent ? `${row.accountName}（子级合计）` : row.accountName,
    '期初方向': directionOf(row.openingBalance),
    '期初余额': row.openingBalance === 0 ? '' : Math.abs(row.openingBalance),
    '借方发生': row.debitTotal,
    '贷方发生': row.creditTotal,
    '期末方向': directionOf(row.closingBalance),
    '期末余额': row.closingBalance === 0 ? '' : Math.abs(row.closingBalance),
  }))
  rows.push({
    '科目编码': '合计',
    '科目名称': '',
    '期初方向': '',
    '期初余额': '',
    '借方发生': totalPeriodDebit.value,
    '贷方发生': totalPeriodCredit.value,
    '期末方向': '',
    '期末余额': '',
  })
  const ws = XLSX.utils.json_to_sheet(rows)
  ws['!cols'] = [
    { wch: 12 }, { wch: 30 }, { wch: 10 }, { wch: 16 },
    { wch: 16 }, { wch: 16 }, { wch: 10 }, { wch: 16 },
  ]
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '科目余额表')
  saveWorkbook(wb, `科目余额表_${month.value}.xlsx`)
  message.success('科目余额表已导出')
}

function backToSummary() {
  view.value = 'summary'
  if (summaryItems.value.length === 0) querySummary()
}

function runQuery() {
  if (view.value === 'annual') loadAnnual()
  else if (view.value === 'summary') querySummary()
  else query()
}

function printPage() {
  window.print()
}

async function query() {
  if (!accountCode.value || !month.value) return
  loading.value = true
  try {
    // 与电子账簿同一条取数路径（P7-S1）：单科目查询模式，父科目由服务端前缀聚合。
    const data = await getLedgerBook({ month: month.value, accountCode: accountCode.value })
    const page: LedgerDetailPage | undefined = data.details[0]
    detailOpening.value = page ? Number(page.opening) : null
    items.value = (page?.lines || []).map((line) => ({
      date: line.date,
      summary: line.backfill ? `${line.summary}（补记）` : line.summary,
      debit: Number(line.debit),
      credit: Number(line.credit),
      balance: Number(line.balance),
    }))
    // 金额字段以字符串传输、由 client.ts 拦截器转数值；运算处统一 Number() 兜底，防新字段名漏网
    totalDebit.value = Number(page?.periodTotals.debit) || 0
    totalCredit.value = Number(page?.periodTotals.credit) || 0
    endBalance.value = page ? Number(page.closing) : 0
  } catch {
    message.error('查询总账账页失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadAccounts()
  await loadAnnual()
})
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
          <p class="report-subtitle">按科目 × 月份汇总借贷发生与余额（一年 12 行 + 本年合计）；可切换科目余额表，余额表行可钻取单科目逐笔流水</p>
        </div>
      </div>
    </div>

    <!-- Filter Bar -->
    <div class="filter-bar mb-4">
      <div class="view-tabs">
        <NButton size="small" :type="view === 'annual' ? 'primary' : 'default'" @click="switchView('annual')">总账</NButton>
        <NButton size="small" :type="view === 'summary' ? 'primary' : 'default'" @click="switchView('summary')">科目余额表</NButton>
      </div>
      <template v-if="view === 'annual'">
        <NFormItem label="会计年度" label-placement="left" :show-feedback="false">
          <NDatePicker v-model:formatted-value="annualYear" type="year" value-format="yyyy" format="yyyy" style="width: 110px;" :clearable="false" />
        </NFormItem>
        <NFormItem label="科目" label-placement="left" :show-feedback="false">
          <NSelect
            v-model:value="annualAccount"
            :options="[{ label: '全部科目', value: 'all' }, ...accountOptions]"
            style="width: 240px;"
            filterable
          />
        </NFormItem>
      </template>
      <NButton v-if="view === 'detail'" text class="report-back-btn" @click="backToSummary">
        <template #icon><i class="ri-arrow-left-line" /></template>
        返回科目余额表
      </NButton>
      <NFormItem v-if="view === 'detail'" label="分类科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="accountCode" :options="accountOptions" style="width: 220px;" filterable />
      </NFormItem>
      <NFormItem v-if="view !== 'annual'" label="会计月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="runQuery">查询</NButton>
      <div class="filter-actions">
        <NButton v-if="view === 'summary'" secondary @click="exportSummary">
          <template #icon><i class="ri-download-2-line" /></template>
          导出 Excel
        </NButton>
        <NButton secondary @click="printPage">
          <template #icon><i class="ri-printer-line" /></template>
          打印
        </NButton>
      </div>
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
          <span class="report-summary-val">{{ moneyOrDash(endBalance) }}<span v-if="endBalance" class="dir-hint">{{ directionOf(endBalance) }}</span></span>
        </div>
      </div>
    </NCard>

    <NSpin :show="loading">
      <!-- 总账视图（默认，年度）：每科目 12 个月汇总 + 本年合计，与电子账簿 general 段同源 -->
      <NCard v-if="view === 'annual'" :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 110px;">月份</th>
                <th style="text-align: right; width: 150px;">借方发生</th>
                <th style="text-align: right; width: 150px;">贷方发生</th>
                <th style="width: 64px;">借或贷</th>
                <th style="text-align: right; width: 150px;">余额</th>
              </tr>
            </thead>
            <tbody v-for="page in visibleGeneralPages" :key="page.accountCode" class="gl-group">
              <tr class="gl-account-row">
                <td colspan="2">
                  {{ page.accountCode }} {{ page.accountName }}
                  <span v-if="page.isParent" class="parent-hint">子级小计</span>
                </td>
                <td class="amount-cell">
                  年初 {{ formatMoney(Number(page.openingAbs)) }}
                  <span v-if="page.openingDirection !== 'flat'" class="dir-hint">{{ directionLabel(page.openingDirection) }}</span>
                </td>
                <td></td>
                <td></td>
              </tr>
              <tr v-for="mrow in page.months" :key="mrow.month" :class="{ 'gl-idle-month': !mrow.hasActivity }">
                <td>{{ mrow.month }}</td>
                <td class="amount-cell">{{ mrow.hasActivity ? formatMoney(Number(mrow.debit)) : '—' }}</td>
                <td class="amount-cell">{{ mrow.hasActivity ? formatMoney(Number(mrow.credit)) : '—' }}</td>
                <td>{{ directionLabel(mrow.balanceDirection) }}</td>
                <td class="amount-cell balance">{{ formatMoney(Number(mrow.balanceAbs)) }}</td>
              </tr>
              <tr class="gl-total-row">
                <td>本年合计</td>
                <td class="amount-cell">{{ formatMoney(Number(page.totalDebit)) }}</td>
                <td class="amount-cell">{{ formatMoney(Number(page.totalCredit)) }}</td>
                <td>{{ directionLabel(page.closingDirection) }}</td>
                <td class="amount-cell balance">{{ formatMoney(Number(page.closingAbs)) }}</td>
              </tr>
            </tbody>
            <tbody v-if="visibleGeneralPages.length === 0">
              <tr>
                <td colspan="5" class="report-empty-cell">本年度无科目发生额（总账仅列示当年有发生额的科目）</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="visibleGeneralPages.length > 0" class="balance-hint">
          <i class="ri-information-line" />
          总账按记账期间归集，与电子账簿同源；年初余额含本年以前全部已过账分录
        </p>
      </NCard>

      <!-- 科目余额表视图：全科目 期初/本期借贷/方向/期末 -->
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
              <tr
                v-for="row in summaryItems"
                :key="row.accountCode"
                class="summary-row"
                :class="{ 'is-parent-row': row.isParent }"
                @click="openDetail(row)"
              >
                <td>{{ row.accountCode }}</td>
                <td>
                  {{ row.accountName }}
                  <span v-if="row.isParent" class="parent-hint">小计</span>
                </td>
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

      <!-- 明细流水视图（从余额表行钻取）：单科目本期逐笔，本质是明细账内容 -->
      <NCard v-else-if="view === 'detail'" title="明细流水（单科目本期逐笔）" :bordered="false" class="report-card">
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
                <td class="amount-cell balance">{{ moneyOrDash(openingBalance) }}<span v-if="openingBalance" class="dir-hint">{{ directionOf(openingBalance) }}</span></td>
              </tr>
              <tr v-for="(item, idx) in items" :key="idx">
                <td>{{ formatDate(item.date) }}</td>
                <td>{{ item.summary }}</td>
                <td class="amount-cell">{{ item.debit ? formatMoney(item.debit) : '-' }}</td>
                <td class="amount-cell">{{ item.credit ? formatMoney(item.credit) : '-' }}</td>
                <td class="amount-cell balance">{{ moneyOrDash(item.balance) }}<span v-if="item.balance" class="dir-hint">{{ directionOf(item.balance) }}</span></td>
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
.summary-row.is-parent-row {
  cursor: default;
}
.summary-row.is-parent-row td {
  font-weight: 700;
  border-top: 2px solid var(--solo-border-color, #e5e9ef);
  background: var(--solo-bg-muted, #f7f9fc);
}
.summary-row.is-parent-row:hover td {
  background: var(--solo-bg-muted, #f7f9fc);
}
.parent-hint {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  color: var(--solo-text-muted);
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
.filter-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
/* 视图切换（总账 / 科目余额表） */
.view-tabs {
  display: flex;
  gap: 8px;
}
/* 总账视图：科目分组行、无发生额月份弱化、本年合计强调 */
.gl-account-row td {
  font-weight: 700;
  background: var(--solo-bg-muted, #f7f9fc);
  border-top: 2px solid var(--solo-border-color, #e5e9ef);
}
.gl-idle-month td {
  color: var(--solo-text-muted);
}
.gl-total-row td {
  font-weight: 700;
  border-top: 2px solid var(--solo-border-color, #e5e9ef);
}
</style>