<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  TRIAL_BALANCE_STATUS,
  filterTrialBalanceRows,
  trialBalanceAccountDepth,
  trialBalanceTotals,
  type TrialBalanceColumns,
  type TrialBalanceRow,
  type TrialBalanceStatusFilter,
} from '@/utils/trialBalance'

/**
 * 只读科目余额表原表核对表。
 *
 * 导入预览与期初工作台的「科目余额表」页签共用：展示源文件逐行金额、系统落点与处理状态，
 * 支持按状态筛选、隐藏零余额与即时检索，协助会计快速做 Tie-out 核对。
 * 金额只做展示，不参与任何凭证/报表取数。
 */
const props = withDefaults(defineProps<{
  rows: TrialBalanceRow[]
  columns?: TrialBalanceColumns | null
  showSummary?: boolean
  maxHeight?: string
  defaultHideZero?: boolean
  actionableUnmatched?: boolean
}>(), {
  columns: null,
  showSummary: true,
  maxHeight: '52vh',
  defaultHideZero: true,
  actionableUnmatched: false,
})

const emit = defineEmits<{
  'select-unmatched': [row: TrialBalanceRow]
}>()

const statusFilter = ref<TrialBalanceStatusFilter>('all')
const hideZero = ref(props.defaultHideZero)
const searchQuery = ref('')

const counts = computed(() => {
  let matched = 0
  let unmatched = 0
  let rollup = 0
  let zero = 0
  for (const row of props.rows) {
    if (row.status === 'matched') matched++
    else if (row.status === 'unmatched') unmatched++
    else if (row.status === 'skipped_rollup' || row.status === 'skipped_partner_rollup') rollup++
    else if (row.status === 'skipped_zero') zero++
  }
  return { matched, unmatched, rollup, zero }
})

const filteredRows = computed(() =>
  filterTrialBalanceRows(props.rows, {
    statusFilter: statusFilter.value,
    hideZero: hideZero.value,
    query: searchQuery.value,
  }),
)

const totals = computed(() => trialBalanceTotals(props.rows))

const columnNotes = computed(() => {
  const c = props.columns
  if (!c) return ''
  const notes: string[] = []
  const pair = (a?: string, b?: string) => [a, b].filter(Boolean).join(' / ')
  if (c.code || c.name) notes.push(`科目列：${pair(c.code, c.name)}`)
  if (c.yearStartDebit || c.yearStartCredit) {
    notes.push(`年初列：${pair(c.yearStartDebit, c.yearStartCredit)}`)
  } else if (c.ytdDebit || c.ytdCredit) {
    notes.push('年初列：由「期末 − 本年累计」推算')
  } else {
    notes.push('年初列：原表未提供')
  }
  if (c.ytdDebit || c.ytdCredit) notes.push(`累计列：${pair(c.ytdDebit, c.ytdCredit)}`)
  if (c.endingDebit || c.endingCredit) {
    notes.push(`期末列：${pair(c.endingDebit, c.endingCredit)}`)
  } else if (c.balance) {
    notes.push(`期末列：${c.balance}${c.direction ? `（按「${c.direction}」分借贷）` : ''}`)
  }
  return notes.join('；')
})

function isZeroVal(val: number | null): boolean {
  return val !== null && Math.abs(val) < 0.005
}

function isNonZeroVal(val: number | null): boolean {
  return val !== null && Math.abs(val) >= 0.005
}

function amountText(val: number | null): string {
  if (val === null) return '—'
  if (Math.abs(val) < 0.005) return '0.00'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function statusMeta(row: TrialBalanceRow) {
  return TRIAL_BALANCE_STATUS[row.status] || TRIAL_BALANCE_STATUS.unmatched
}

function setStatusFilter(next: TrialBalanceStatusFilter) {
  statusFilter.value = next
}
</script>

<template>
  <div class="trial-balance">
    <div v-if="showSummary" class="tb-toolbar">
      <div class="tb-toolbar-top">
        <div class="tb-filters" role="group" aria-label="按处理结果筛选">
          <button
            type="button"
            class="tb-chip"
            :class="{ active: statusFilter === 'all' }"
            @click="setStatusFilter('all')"
          >
            全部 {{ rows.length }}
          </button>
          <button
            v-if="counts.unmatched > 0"
            type="button"
            class="tb-chip tone-warning"
            :class="{ active: statusFilter === 'unmatched' }"
            @click="setStatusFilter('unmatched')"
          >
            待映射 {{ counts.unmatched }}
          </button>
          <button
            type="button"
            class="tb-chip tone-success"
            :class="{ active: statusFilter === 'matched' }"
            @click="setStatusFilter('matched')"
          >
            已入账 {{ counts.matched }}
          </button>
          <button
            v-if="counts.rollup > 0"
            type="button"
            class="tb-chip"
            :class="{ active: statusFilter === 'rollup' }"
            @click="setStatusFilter('rollup')"
          >
            层级去重 {{ counts.rollup }}
          </button>
          <button
            v-if="counts.zero > 0"
            type="button"
            class="tb-chip"
            :class="{ active: statusFilter === 'zero' }"
            @click="setStatusFilter('zero')"
          >
            零余额 {{ counts.zero }}
          </button>
        </div>

        <div class="tb-controls">
          <label
            v-if="counts.zero > 0 && statusFilter !== 'zero'"
            class="tb-toggle"
            title="默认隐藏期末与年初均为 0 的空行，便于会计聚焦有效数据；搜索时仍会检索全表"
          >
            <input v-model="hideZero" type="checkbox" />
            <span>隐藏零余额（{{ counts.zero }}）</span>
          </label>
          <input
            v-model="searchQuery"
            type="search"
            class="tb-search"
            placeholder="搜科目码 / 名称 / 客商..."
            aria-label="搜索原表科目或客商"
          />
        </div>
      </div>

      <div v-if="columnNotes || filteredRows.length !== rows.length" class="tb-columns">
        <span v-if="filteredRows.length !== rows.length" class="tb-visible-count">
          当前显示 {{ filteredRows.length }} / {{ rows.length }} 行
        </span>
        <span v-if="columnNotes && filteredRows.length !== rows.length" class="tb-sep">｜</span>
        <span v-if="columnNotes">{{ columnNotes }}</span>
      </div>
    </div>

    <div class="tb-scroll" :style="{ maxHeight }">
      <table class="tb-table">
        <thead>
          <tr class="tb-head-group">
            <th rowspan="2" class="col-no">序号</th>
            <th rowspan="2" class="col-account">原表科目</th>
            <th rowspan="2" class="col-partner">客商</th>
            <th colspan="2" class="col-group">年初余额</th>
            <th colspan="2" class="col-group">本年累计发生额</th>
            <th colspan="2" class="col-group col-group-ending">期末余额（建账口径）</th>
            <th rowspan="2" class="col-target">系统落点</th>
            <th rowspan="2" class="col-status">处理结果</th>
          </tr>
          <tr class="tb-head-sub">
            <th class="col-amount num" title="年初余额借方">借方</th>
            <th class="col-amount num" title="年初余额贷方">贷方</th>
            <th class="col-amount num" title="本年累计发生额借方">借方</th>
            <th class="col-amount num" title="本年累计发生额贷方">贷方</th>
            <th class="col-amount num col-ending" title="期末余额借方">借方</th>
            <th class="col-amount num col-ending" title="期末余额贷方">贷方</th>
          </tr>
        </thead>
        <tbody v-if="filteredRows.length > 0">
          <tr
            v-for="row in filteredRows"
            :key="row.row_no"
            :class="`row-${row.status}`"
          >
            <td class="col-no">{{ row.row_no }}</td>
            <td
              class="col-account"
              :class="`depth-${trialBalanceAccountDepth(row.original_code)}`"
            >
              <span class="account-code">{{ row.original_code || '—' }}</span>
              <span class="account-name">{{ row.original_name || '—' }}</span>
            </td>
            <td class="col-partner" :title="row.partner_name || ''">{{ row.partner_name || '—' }}</td>
            <td class="col-amount num" :class="{ 'num-zero': isZeroVal(row.year_start_debit) }">
              {{ amountText(row.year_start_debit) }}
            </td>
            <td class="col-amount num" :class="{ 'num-zero': isZeroVal(row.year_start_credit) }">
              {{ amountText(row.year_start_credit) }}
            </td>
            <td class="col-amount num" :class="{ 'num-zero': isZeroVal(row.ytd_debit) }">
              {{ amountText(row.ytd_debit) }}
            </td>
            <td class="col-amount num" :class="{ 'num-zero': isZeroVal(row.ytd_credit) }">
              {{ amountText(row.ytd_credit) }}
            </td>
            <td
              class="col-amount num col-ending"
              :class="{
                'num-zero': isZeroVal(row.ending_debit),
                'num-ending-active': isNonZeroVal(row.ending_debit),
              }"
            >
              {{ amountText(row.ending_debit) }}
            </td>
            <td
              class="col-amount num col-ending"
              :class="{
                'num-zero': isZeroVal(row.ending_credit),
                'num-ending-active': isNonZeroVal(row.ending_credit),
              }"
            >
              {{ amountText(row.ending_credit) }}
            </td>
            <td class="col-target">
              <template v-if="row.target_code">
                <span class="account-code">{{ row.target_code }}</span>
                <span class="account-name">{{ row.target_name || '' }}</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td class="col-status">
              <div class="status-head">
                <span class="status-pill" :class="`tone-${statusMeta(row).tone}`">
                  {{ statusMeta(row).label }}
                </span>
                <button
                  v-if="actionableUnmatched && row.status === 'unmatched'"
                  type="button"
                  class="tb-map-link"
                  @click="emit('select-unmatched', row)"
                >
                  去映射 →
                </button>
              </div>
              <span v-if="row.note" class="status-note" :title="row.note">{{ row.note }}</span>
            </td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr>
            <td colspan="11" class="empty-cell">
              <i class="ri-inbox-line" />
              <span>{{ rows.length > 0 ? '当前筛选条件下没有匹配行' : '暂无原表快照' }}</span>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="rows.length > 0">
          <tr v-if="totals.hasTotalRow">
            <td colspan="3" class="footer-label">原表合计行</td>
            <td class="col-amount num">{{ amountText(totals.debit.yearStart) }}</td>
            <td class="col-amount num">{{ amountText(totals.credit.yearStart) }}</td>
            <td class="col-amount num">{{ amountText(totals.debit.ytd) }}</td>
            <td class="col-amount num">{{ amountText(totals.credit.ytd) }}</td>
            <td class="col-amount num col-ending">{{ amountText(totals.debit.ending) }}</td>
            <td class="col-amount num col-ending">{{ amountText(totals.credit.ending) }}</td>
            <td colspan="2" class="footer-hint">
              直接取自原表合计行
            </td>
          </tr>
          <tr v-else>
            <td colspan="11" class="footer-hint footer-no-total">
              原表没有合计行；本表只做逐行核对，系统入账合计见上方导入统计。
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.trial-balance {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.tb-toolbar {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.tb-toolbar-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.tb-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.tb-chip {
  border: 1px solid var(--solo-border-light);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  border-radius: var(--solo-radius-pill);
  padding: 3px 10px;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.tb-chip:hover {
  color: var(--solo-text-primary);
  border-color: var(--solo-border);
}

.tb-chip.active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}

.tb-chip.tone-warning:not(.active) {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  border-color: var(--solo-warning-bg);
}

.tb-chip.tone-success:not(.active) {
  background: var(--solo-income-bg);
  color: var(--solo-income);
  border-color: var(--solo-income-bg);
}

.tb-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tb-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--solo-text-secondary);
  cursor: pointer;
  user-select: none;
}

.tb-toggle input {
  accent-color: var(--solo-primary);
  cursor: pointer;
}

.tb-search {
  width: 196px;
  padding: 4px 10px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font-size: 12px;
  outline: none;
}

.tb-search:focus {
  border-color: var(--solo-primary);
}

.tb-search::placeholder {
  color: var(--solo-text-muted);
}

.tb-columns {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.5;
}

.tb-visible-count {
  color: var(--solo-text-secondary);
  font-weight: 500;
}

.tb-sep {
  margin: 0 4px;
}

.tb-scroll {
  overflow: auto;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-md);
  background: var(--solo-bg-card);
}

.tb-table {
  width: 100%;
  min-width: 1060px;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.tb-table th,
.tb-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  vertical-align: top;
}

.tb-table thead tr.tb-head-group th {
  position: sticky;
  top: 0;
  z-index: 2;
  height: 30px;
  padding: 5px 8px;
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
  font-weight: 600;
  white-space: nowrap;
  vertical-align: middle;
}

.tb-table thead tr.tb-head-sub th {
  position: sticky;
  top: 30px;
  z-index: 2;
  height: 28px;
  padding: 4px 8px;
  background: var(--solo-bg-page);
  color: var(--solo-text-muted);
  font-weight: 500;
  font-size: 11px;
  white-space: nowrap;
  vertical-align: middle;
}

.col-group {
  text-align: center !important;
  border-left: 1px solid var(--solo-border-light);
}

.tb-table thead th.col-group-ending,
.tb-table thead th.col-ending {
  background: var(--solo-bg-blue-soft) !important;
  color: var(--solo-primary) !important;
}

.tb-table tbody td.col-ending,
.tb-table tfoot td.col-ending {
  background: var(--solo-bg-soft);
}

.tb-table tbody tr:hover td {
  background: var(--solo-bg-page);
}

.col-no {
  width: 44px;
  color: var(--solo-text-muted);
}

.col-account {
  width: 210px;
  min-width: 170px;
}

.col-account.depth-0 .account-name {
  font-weight: 600;
}

.col-account.depth-1 {
  padding-left: 16px !important;
}

.col-account.depth-2 {
  padding-left: 28px !important;
}

.col-partner {
  width: 110px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-amount {
  width: 88px;
  white-space: nowrap;
}

.col-target {
  width: 160px;
}

.col-status {
  width: 164px;
}

.num {
  text-align: right !important;
}

.num-zero {
  color: var(--solo-text-muted);
  opacity: 0.45;
}

.num-ending-active {
  color: var(--solo-text-primary);
  font-weight: 500;
}

.account-code {
  display: block;
  font-family: var(--solo-font-mono);
  font-size: 11px;
  color: var(--solo-text-muted);
}

.account-name {
  display: block;
  color: var(--solo-text-primary);
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.status-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.status-pill {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.6;
  white-space: nowrap;
}

.tb-map-link {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--solo-primary);
  cursor: pointer;
  white-space: nowrap;
}

.tb-map-link:hover {
  text-decoration: underline;
}

.tone-success {
  color: var(--solo-income);
  background: var(--solo-income-bg);
}

.tone-warning {
  color: var(--solo-warning);
  background: var(--solo-warning-bg);
}

.tone-muted {
  color: var(--solo-text-muted);
  background: var(--solo-bg-page);
}

.tone-info {
  color: var(--solo-info);
  background: var(--solo-info-bg);
}

.status-note {
  display: -webkit-box;
  margin-top: 2px;
  font-size: 10px;
  color: var(--solo-text-muted);
  line-height: 1.4;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.row-skipped_total,
.row-skipped_rollup,
.row-skipped_zero {
  color: var(--solo-text-muted);
}

.row-skipped_total .account-name {
  font-weight: 600;
}

.row-unmatched td {
  background: var(--solo-warning-bg);
}

.footer-label {
  color: var(--solo-text-secondary);
  font-weight: 600;
}

.footer-hint {
  color: var(--solo-text-muted);
  font-size: 11px;
}

.footer-no-total {
  padding: 10px 12px !important;
}

.tb-table tfoot td {
  position: sticky;
  bottom: 0;
  z-index: 2;
  background: var(--solo-bg-page);
  border-bottom: none;
  border-top: 1px solid var(--solo-border-light);
}

.muted {
  color: var(--solo-text-muted);
}

.empty-cell {
  padding: 24px 12px !important;
  text-align: center !important;
  color: var(--solo-text-muted);
}

.empty-cell i {
  margin-right: 6px;
}
</style>
