<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import {
  TRIAL_BALANCE_STATUS,
  trialBalanceTotals,
  type TrialBalanceColumns,
  type TrialBalanceRow,
} from '@/utils/trialBalance'

/**
 * 只读科目余额表原表核对表。
 *
 * 导入预览与期初工作台共用：展示源文件逐行金额、系统落点与处理状态。
 * 金额只做展示，不参与任何凭证/报表取数。
 */
const props = withDefaults(defineProps<{
  rows: TrialBalanceRow[]
  columns?: TrialBalanceColumns | null
  showSummary?: boolean
  maxHeight?: string
}>(), {
  columns: null,
  showSummary: true,
  maxHeight: '52vh',
})

const counts = computed(() => {
  let matched = 0
  let unmatched = 0
  for (const row of props.rows) {
    if (row.status === 'matched') matched++
    else if (row.status === 'unmatched') unmatched++
  }
  return { matched, unmatched, skipped: props.rows.length - matched - unmatched }
})

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

function amountText(val: number | null): string {
  if (val === null) return '—'
  if (Math.abs(val) < 0.005) return '0.00'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function statusMeta(row: TrialBalanceRow) {
  return TRIAL_BALANCE_STATUS[row.status] || TRIAL_BALANCE_STATUS.unmatched
}
</script>

<template>
  <div class="trial-balance">
    <div v-if="showSummary" class="tb-summary">
      <span class="tb-count">原表 {{ rows.length }} 行</span>
      <span class="tb-dot">·</span>
      <span class="tb-count tone-success">已入账 {{ counts.matched }}</span>
      <span v-if="counts.unmatched > 0" class="tb-count tone-warning">待映射 {{ counts.unmatched }}</span>
      <span v-if="counts.skipped > 0" class="tb-count tone-muted">跳过 {{ counts.skipped }}</span>
      <span v-if="columnNotes" class="tb-columns">{{ columnNotes }}</span>
    </div>

    <div class="tb-scroll" :style="{ maxHeight }">
      <table class="tb-table">
        <thead>
          <tr>
            <th class="col-no">序号</th>
            <th class="col-account">原表科目</th>
            <th class="col-partner">客商</th>
            <th class="col-amount" title="年初余额借方">年初借</th>
            <th class="col-amount" title="年初余额贷方">年初贷</th>
            <th class="col-amount" title="本年累计发生额借方">累计借</th>
            <th class="col-amount" title="本年累计发生额贷方">累计贷</th>
            <th class="col-amount" title="期末余额借方">期末借</th>
            <th class="col-amount" title="期末余额贷方">期末贷</th>
            <th class="col-target">系统科目</th>
            <th class="col-status">处理结果</th>
          </tr>
        </thead>
        <tbody v-if="rows.length > 0">
          <tr
            v-for="row in rows"
            :key="row.row_no"
            :class="`row-${row.status}`"
          >
            <td class="col-no">{{ row.row_no }}</td>
            <td class="col-account">
              <span class="account-code">{{ row.original_code || '—' }}</span>
              <span class="account-name">{{ row.original_name || '—' }}</span>
            </td>
            <td class="col-partner" :title="row.partner_name || ''">{{ row.partner_name || '—' }}</td>
            <td class="col-amount num">{{ amountText(row.year_start_debit) }}</td>
            <td class="col-amount num">{{ amountText(row.year_start_credit) }}</td>
            <td class="col-amount num">{{ amountText(row.ytd_debit) }}</td>
            <td class="col-amount num">{{ amountText(row.ytd_credit) }}</td>
            <td class="col-amount num">{{ amountText(row.ending_debit) }}</td>
            <td class="col-amount num">{{ amountText(row.ending_credit) }}</td>
            <td class="col-target">
              <template v-if="row.target_code">
                <span class="account-code">{{ row.target_code }}</span>
                <span class="account-name">{{ row.target_name || '' }}</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td class="col-status">
              <span class="status-pill" :class="`tone-${statusMeta(row).tone}`">
                {{ statusMeta(row).label }}
              </span>
              <span v-if="row.note" class="status-note" :title="row.note">{{ row.note }}</span>
            </td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr>
            <td colspan="11" class="empty-cell">
              <i class="ri-inbox-line" />
              <span>暂无原表快照</span>
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
            <td class="col-amount num">{{ amountText(totals.debit.ending) }}</td>
            <td class="col-amount num">{{ amountText(totals.credit.ending) }}</td>
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

.tb-summary {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.tb-count {
  white-space: nowrap;
}

.tb-dot {
  color: var(--solo-text-muted);
}

.tb-columns {
  flex-basis: 100%;
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.6;
}

.tb-scroll {
  overflow: auto;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-md);
  background: var(--solo-bg-card);
}

.tb-table {
  width: 100%;
  min-width: 1040px;
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

.tb-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
  font-weight: 600;
  white-space: nowrap;
}

.tb-table tbody tr:hover {
  background: var(--solo-bg-page);
}

.col-no {
  width: 44px;
  color: var(--solo-text-muted);
}

.col-account {
  width: 190px;
  min-width: 160px;
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
  width: 150px;
}

.num {
  text-align: right !important;
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

.status-pill {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.6;
  white-space: nowrap;
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

.row-unmatched {
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
