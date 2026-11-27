<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, NSwitch, NTag, useMessage } from 'naive-ui'
import * as XLSX from 'xlsx'
import type { AuxiliaryKind } from '@/api/auxiliary'
import {
  getAuxiliaryBalances,
  getLedgerAccounts,
  type AuxiliaryBalanceRow,
  type AuxiliaryBalancesResponse,
  type LedgerAccountOption,
} from '@/api/ledger'
import { getCurrentMonth } from '@/utils/format'
import { directionLabel, ledgerMoney } from '@/utils/ledgerFormat'
import { saveWorkbook } from '@/utils/export'
import {
  AUXILIARY_KIND_META,
  AUXILIARY_KIND_OPTIONS,
  UNASSIGNED_AUX_ITEM_ID,
  accountsForAuxiliaryKind,
  isAuxiliaryKind,
} from '@/utils/auxiliary'
import { SoloMonthPicker } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const kind = ref<AuxiliaryKind>(isAuxiliaryKind(route.query.kind) ? route.query.kind : 'partner')
const accountCode = ref<string | null>(
  route.query.account ? String(route.query.account) : route.query.accountCode ? String(route.query.accountCode) : null,
)
const includeZero = ref(false)
const loading = ref(false)
const allAccounts = ref<LedgerAccountOption[]>([])
const result = ref<AuxiliaryBalancesResponse | null>(null)

const kindLabel = computed(() => AUXILIARY_KIND_META[kind.value].label)

const accountOptions = computed(() => {
  const matched = accountsForAuxiliaryKind(allAccounts.value, kind.value)
  return [
    { label: `全部${kindLabel.value}科目`, value: '' },
    ...matched.map((acct) => ({ label: `${acct.code} ${acct.name}`, value: acct.code })),
  ]
})

const selectedAccountValue = computed({
  get: () => accountCode.value || '',
  set: (val: string) => {
    accountCode.value = val || null
  },
})

const accountTitle = computed(() => {
  if (!accountCode.value) return `全部${kindLabel.value}科目`
  const hit = allAccounts.value.find((a) => a.code === accountCode.value)
  return hit ? `${hit.code} ${hit.name}` : accountCode.value
})

const rows = computed(() => result.value?.items ?? [])
const unassignedRow = computed(() => result.value?.unassigned ?? null)

function amountOrDash(value: string) {
  return Number(value) === 0 ? '—' : ledgerMoney(Math.abs(Number(value)))
}

function drillToSubLedger(row: AuxiliaryBalanceRow) {
  router.push({
    path: '/ledger/books',
    query: {
      book: 'auxiliary-sub-ledger',
      kind: kind.value,
      itemId: row.unassigned ? UNASSIGNED_AUX_ITEM_ID : row.itemId,
      month: month.value,
      ...(accountCode.value ? { accountCode: accountCode.value } : {}),
    },
  })
}

async function loadAccounts() {
  try {
    const data = await getLedgerAccounts()
    allAccounts.value = data.accounts || []
  } catch {
    allAccounts.value = []
  }
}

async function load() {
  loading.value = true
  try {
    result.value = await getAuxiliaryBalances({
      kind: kind.value,
      month: month.value,
      accountCode: accountCode.value,
      includeZero: includeZero.value,
    })
  } catch {
    message.error('查询核算项目余额表失败')
  } finally {
    loading.value = false
  }
}

watch(kind, () => {
  const validCodes = new Set(accountsForAuxiliaryKind(allAccounts.value, kind.value).map((a) => a.code))
  if (accountCode.value && !validCodes.has(accountCode.value)) {
    accountCode.value = null
  }
  void load()
})

watch(includeZero, () => {
  void load()
})

function exportExcel() {
  if (!result.value) {
    message.warning('没有可导出的数据')
    return
  }
  const allRows = [...rows.value, ...(unassignedRow.value ? [unassignedRow.value] : [])]
  if (allRows.length === 0) {
    message.warning('没有可导出的数据')
    return
  }
  const sheetRows = allRows.map((row) => ({
    编码: row.itemCode || '',
    [kindLabel.value]: row.itemName,
    期初方向: directionLabel(row.openingDirection),
    期初余额: Number(row.opening) === 0 ? '' : Math.abs(Number(row.opening)),
    本期借方: Number(row.debit),
    本期贷方: Number(row.credit),
    本年累计借方: Number(row.ytdDebit),
    本年累计贷方: Number(row.ytdCredit),
    期末方向: directionLabel(row.closingDirection),
    期末余额: Number(row.closing) === 0 ? '' : Math.abs(Number(row.closing)),
  }))
  const worksheet = XLSX.utils.json_to_sheet(sheetRows)
  worksheet['!cols'] = [
    { wch: 12 }, { wch: 26 }, { wch: 10 }, { wch: 15 },
    { wch: 15 }, { wch: 15 }, { wch: 15 }, { wch: 15 }, { wch: 10 }, { wch: 15 },
  ]
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '核算项目余额表')
  saveWorkbook(workbook, `核算项目余额表_${kindLabel.value}_${accountCode.value || '全部'}_${month.value}.xlsx`)
  message.success('已导出')
}

function printPage() {
  window.print()
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<template>
  <div class="report-container">
    <div class="report-header no-print">
      <div class="report-header-left">
        <div class="report-title-section">
          <h2>核算项目余额表</h2>
          <p class="report-subtitle">按往来单位 / 部门 / 项目 / 职员汇总期初、本期借贷、本年累计与期末；点击任意行可下钻核算项目明细账</p>
        </div>
      </div>
      <div class="report-header-right">
        <NButton secondary @click="router.push('/mine/contacts')">
          <template #icon><i class="ri-contacts-book-line" /></template>
          管理辅助档案
        </NButton>
        <NButton secondary @click="exportExcel">
          <template #icon><i class="ri-download-2-line" /></template>
          导出 Excel
        </NButton>
        <NButton @click="printPage">
          <template #icon><i class="ri-printer-line" /></template>
          打印
        </NButton>
      </div>
    </div>

    <div class="filter-bar mb-4 no-print">
      <NFormItem label="核算类别" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="kind" :options="AUXILIARY_KIND_OPTIONS" style="width: 130px;" />
      </NFormItem>
      <NFormItem label="科目范围" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="selectedAccountValue" :options="accountOptions" filterable style="width: 220px;" />
      </NFormItem>
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 150px;" />
      </NFormItem>
      <div class="zero-switch">
        <span>含零行</span>
        <NSwitch v-model:value="includeZero" size="small" />
      </div>
      <NButton type="primary" @click="load">查询</NButton>
    </div>

    <!-- 打印抬头 -->
    <div class="print-only print-header">
      <div class="print-title">{{ kindLabel }} · 核算项目余额表（{{ accountTitle }}）</div>
      <div class="print-meta">会计期间：{{ month }} · 单位：元</div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 100px;">编码</th>
                <th>{{ kindLabel }}</th>
                <th style="width: 145px; text-align: right;">期初余额</th>
                <th style="width: 125px; text-align: right;">本期借方</th>
                <th style="width: 125px; text-align: right;">本期贷方</th>
                <th style="width: 125px; text-align: right;">本年借方</th>
                <th style="width: 125px; text-align: right;">本年贷方</th>
                <th style="width: 145px; text-align: right;">期末余额</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.itemId"
                class="drill-row"
                :class="{ inactive: !row.isActive }"
                title="点击查看核算项目明细账"
                @click="drillToSubLedger(row)"
              >
                <td class="mono">{{ row.itemCode || '—' }}</td>
                <td>
                  <span>{{ row.itemName }}</span>
                  <NTag v-if="!row.isActive" size="tiny" :bordered="false" class="inactive-tag">停用</NTag>
                </td>
                <td class="amount-cell">
                  <span v-if="Number(row.opening) !== 0" class="dir-hint">{{ directionLabel(row.openingDirection) }}</span>
                  {{ amountOrDash(row.opening) }}
                </td>
                <td class="amount-cell">{{ amountOrDash(row.debit) }}</td>
                <td class="amount-cell">{{ amountOrDash(row.credit) }}</td>
                <td class="amount-cell">{{ amountOrDash(row.ytdDebit) }}</td>
                <td class="amount-cell">{{ amountOrDash(row.ytdCredit) }}</td>
                <td class="amount-cell balance">
                  <span v-if="Number(row.closing) !== 0" class="dir-hint">{{ directionLabel(row.closingDirection) }}</span>
                  {{ amountOrDash(row.closing) }}
                </td>
              </tr>
              <tr
                v-if="unassignedRow"
                class="drill-row unassigned-row"
                title="点击查看未指定项目明细"
                @click="drillToSubLedger(unassignedRow)"
              >
                <td class="mono">—</td>
                <td>{{ unassignedRow.itemName }}</td>
                <td class="amount-cell">
                  <span v-if="Number(unassignedRow.opening) !== 0" class="dir-hint">{{ directionLabel(unassignedRow.openingDirection) }}</span>
                  {{ amountOrDash(unassignedRow.opening) }}
                </td>
                <td class="amount-cell">{{ amountOrDash(unassignedRow.debit) }}</td>
                <td class="amount-cell">{{ amountOrDash(unassignedRow.credit) }}</td>
                <td class="amount-cell">{{ amountOrDash(unassignedRow.ytdDebit) }}</td>
                <td class="amount-cell">{{ amountOrDash(unassignedRow.ytdCredit) }}</td>
                <td class="amount-cell">
                  <span v-if="Number(unassignedRow.closing) !== 0" class="dir-hint">{{ directionLabel(unassignedRow.closingDirection) }}</span>
                  {{ amountOrDash(unassignedRow.closing) }}
                </td>
              </tr>
              <tr v-if="rows.length === 0 && !unassignedRow">
                <td colspan="8" class="report-empty-cell">本月该维度没有发生额或余额</td>
              </tr>
            </tbody>
            <tfoot v-if="(rows.length > 0 || unassignedRow) && result">
              <tr>
                <td class="total-cell" colspan="2">合　计</td>
                <td class="amount-cell total-cell">
                  <span v-if="Number(result.totalOpening) !== 0" class="dir-hint">{{ directionLabel(result.totalOpeningDirection) }}</span>
                  {{ amountOrDash(result.totalOpening) }}
                </td>
                <td class="amount-cell total-cell">{{ ledgerMoney(result.totalDebit) }}</td>
                <td class="amount-cell total-cell">{{ ledgerMoney(result.totalCredit) }}</td>
                <td class="amount-cell total-cell">{{ ledgerMoney(result.ytdTotalDebit) }}</td>
                <td class="amount-cell total-cell">{{ ledgerMoney(result.ytdTotalCredit) }}</td>
                <td class="amount-cell total-cell">
                  <span v-if="Number(result.totalClosing) !== 0" class="dir-hint">{{ directionLabel(result.totalClosingDirection) }}</span>
                  {{ amountOrDash(result.totalClosing) }}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p v-if="unassignedRow" class="hint">
          <i class="ri-information-line" />
          「{{ unassignedRow.itemName }}」为尚未挂接{{ kindLabel }}档案的分录（已计入合计，与科目余额表一致）；点击该行可排查具体凭证。
        </p>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; flex-wrap: wrap; }
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 10px 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
.zero-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--solo-text-muted); }
.report-table-wrapper { overflow-x: auto; }
.mono { font-variant-numeric: tabular-nums; color: var(--solo-text-muted); }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.dir-hint { margin-right: 4px; font-size: 11px; color: var(--solo-text-muted); }
.total-cell { font-weight: 700; }
.drill-row { cursor: pointer; }
.drill-row:hover td { background: var(--solo-bg-soft); }
.inactive td { color: var(--solo-text-muted); }
.inactive-tag { margin-left: 6px; }
.unassigned-row td { color: var(--solo-text-muted); font-style: italic; }
.hint { display: flex; align-items: center; gap: 6px; margin: 12px 0 0; font-size: 12px; color: var(--solo-text-muted); }
.print-only { display: none; }
.print-header { text-align: center; margin-bottom: 12px; }
.print-title { font-size: 18px; font-weight: 700; }
.print-meta { font-size: 12px; margin-top: 4px; }
@media print {
  .print-only { display: block; }
  .report-card { box-shadow: none !important; }
}
</style>
