<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, useMessage } from 'naive-ui'
import * as XLSX from 'xlsx'
import {
  getAuxiliaryCombination,
  getLedgerAccounts,
  type AuxiliaryCombinationHeader,
  type AuxiliaryCombinationResponse,
  type AuxiliaryCombinationRow,
  type CombinationDim,
  type CombinationMetric,
  type LedgerAccountOption,
} from '@/api/ledger'
import { getCurrentMonth } from '@/utils/format'
import { ledgerMoney } from '@/utils/ledgerFormat'
import { saveWorkbook } from '@/utils/export'
import {
  COMBINATION_DIM_OPTIONS,
  COMBINATION_METRIC_OPTIONS,
  COMBINATION_PRESETS,
  type CombinationPreset,
  isAuxiliaryKind,
  isCombinationDim,
} from '@/utils/auxiliary'
import { SoloMonthPicker } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const rowDim = ref<CombinationDim>(isCombinationDim(route.query.rowDim) ? route.query.rowDim : 'department')
const colDim = ref<CombinationDim>(isCombinationDim(route.query.colDim) ? route.query.colDim : 'project')
const metric = ref<CombinationMetric>('debit')
const accountCode = ref<string | null>(null)
const loading = ref(false)
const allAccounts = ref<LedgerAccountOption[]>([])
const result = ref<AuxiliaryCombinationResponse | null>(null)

const accountOptions = computed(() => [
  { label: '全部相关科目', value: '' },
  ...allAccounts.value
    .filter((a) => a.level > 0)
    .sort((a, b) => a.code.localeCompare(b.code))
    .map((a) => ({ label: `${a.code} ${a.name}`, value: a.code })),
])

const selectedAccountValue = computed({
  get: () => accountCode.value || '',
  set: (val: string) => {
    accountCode.value = val || null
  },
})

function applyPreset(preset: CombinationPreset) {
  rowDim.value = preset.rowDim
  colDim.value = preset.colDim
  metric.value = preset.metric
  accountCode.value = preset.accountCode || null
  void query()
}

function swapDims() {
  const prevRow = rowDim.value
  rowDim.value = colDim.value
  colDim.value = prevRow
  void query()
}

function cellAmount(row: AuxiliaryCombinationRow, col: AuxiliaryCombinationHeader): string {
  const raw = row.cells[col.id]
  if (!raw || Number(raw) === 0) return '—'
  return ledgerMoney(raw)
}

function canDrillCell(row: AuxiliaryCombinationRow, col: AuxiliaryCombinationHeader): boolean {
  const raw = row.cells[col.id]
  if (!raw || Number(raw) === 0) return false
  return isAuxiliaryKind(rowDim.value) || isAuxiliaryKind(colDim.value)
}

function drillCell(row: AuxiliaryCombinationRow, col: AuxiliaryCombinationHeader) {
  if (!canDrillCell(row, col)) return
  const rowIsAux = isAuxiliaryKind(rowDim.value)
  const targetKind = rowIsAux ? rowDim.value : colDim.value
  const targetId = rowIsAux ? row.id : col.id
  const acctCode =
    rowDim.value === 'account' ? row.id : colDim.value === 'account' ? col.id : accountCode.value
  router.push({
    path: '/ledger/books',
    query: {
      book: 'auxiliary-sub-ledger',
      kind: targetKind,
      itemId: targetId,
      month: month.value,
      ...(acctCode ? { accountCode: acctCode } : {}),
    },
  })
}

async function query() {
  if (rowDim.value === colDim.value) {
    message.warning('行维度与列维度不能相同')
    return
  }
  loading.value = true
  try {
    result.value = await getAuxiliaryCombination({
      rowDim: rowDim.value,
      colDim: colDim.value,
      month: month.value,
      metric: metric.value,
      accountCode: accountCode.value,
    })
  } catch {
    message.error('查询核算项目组合表失败')
  } finally {
    loading.value = false
  }
}

function exportExcel() {
  if (!result.value || result.value.rows.length === 0) {
    message.warning('没有可导出的数据')
    return
  }
  const sheetRows = result.value.rows.map((row) => {
    const out: Record<string, string | number> = {
      [result.value!.rowDimLabel]: row.code && rowDim.value !== 'account' ? `${row.code} ${row.name}` : row.name,
    }
    for (const col of result.value!.columns) {
      out[col.name] = row.cells[col.id] ? Number(row.cells[col.id]) : 0
    }
    out['合计'] = Number(row.rowTotal)
    return out
  })
  const totalRow: Record<string, string | number> = { [result.value.rowDimLabel]: '合计' }
  for (const col of result.value.columns) {
    totalRow[col.name] = Number(result.value.colTotals[col.id] || 0)
  }
  totalRow['合计'] = Number(result.value.grandTotal)
  sheetRows.push(totalRow)

  const worksheet = XLSX.utils.json_to_sheet(sheetRows)
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '核算项目组合表')
  saveWorkbook(workbook, `核算项目组合表_${result.value.rowDimLabel}×${result.value.colDimLabel}_${month.value}.xlsx`)
  message.success('已导出')
}

function printPage() {
  window.print()
}

onMounted(async () => {
  try {
    const data = await getLedgerAccounts()
    allAccounts.value = data.accounts || []
  } catch {
    allAccounts.value = []
  }
  await query()
})
</script>

<template>
  <div class="report-container">
    <div class="report-header no-print">
      <div class="report-header-left">
        <div class="report-title-section">
          <h2>核算项目组合表</h2>
          <p class="report-subtitle">任意两维交叉透视（部门×项目、科目×部门、科目×职员、往来×科目），点击非零单元格可下钻明细账</p>
        </div>
      </div>
      <div class="report-header-right">
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

    <div class="preset-bar mb-3 no-print">
      <span class="preset-label">常用组合：</span>
      <NButton
        v-for="preset in COMBINATION_PRESETS"
        :key="preset.key"
        size="tiny"
        :type="rowDim === preset.rowDim && colDim === preset.colDim && metric === preset.metric ? 'primary' : 'default'"
        :secondary="!(rowDim === preset.rowDim && colDim === preset.colDim && metric === preset.metric)"
        @click="applyPreset(preset)"
      >
        {{ preset.label }}
      </NButton>
    </div>

    <div class="filter-bar mb-4 no-print">
      <NFormItem label="行维度" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="rowDim" :options="COMBINATION_DIM_OPTIONS" style="width: 120px;" />
      </NFormItem>
      <NButton size="small" quaternary title="行列互换" @click="swapDims">
        <template #icon><i class="ri-arrow-left-right-line" /></template>
      </NButton>
      <NFormItem label="列维度" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="colDim" :options="COMBINATION_DIM_OPTIONS" style="width: 120px;" />
      </NFormItem>
      <NFormItem label="口径" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="metric" :options="COMBINATION_METRIC_OPTIONS" style="width: 125px;" />
      </NFormItem>
      <NFormItem label="科目范围" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="selectedAccountValue" :options="accountOptions" filterable style="width: 190px;" />
      </NFormItem>
      <NFormItem label="月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 145px;" />
      </NFormItem>
      <NButton type="primary" @click="query">查询</NButton>
    </div>

    <div v-if="result" class="print-only print-header">
      <div class="print-title">核算项目组合表（{{ result.rowDimLabel }} × {{ result.colDimLabel }}）</div>
      <div class="print-meta">会计期间：{{ month }} · 单位：元</div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table matrix-table">
            <thead>
              <tr>
                <th class="sticky-col">{{ result?.rowDimLabel || '行维度' }} \ {{ result?.colDimLabel || '列维度' }}</th>
                <th v-for="col in result?.columns || []" :key="col.id" class="amount-head">
                  {{ col.code && result?.colDim === 'account' ? `${col.code} ` : '' }}{{ col.name }}
                </th>
                <th class="amount-head total-col">合计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in result?.rows || []" :key="row.id" :class="{ 'unassigned-row': row.unassigned }">
                <td class="sticky-col row-label">
                  {{ row.code && result?.rowDim === 'account' ? `${row.code} ` : '' }}{{ row.name }}
                </td>
                <td
                  v-for="col in result?.columns || []"
                  :key="col.id"
                  class="amount-cell"
                  :class="{ clickable: canDrillCell(row, col) }"
                  @click="drillCell(row, col)"
                >
                  {{ cellAmount(row, col) }}
                </td>
                <td class="amount-cell total-col">{{ ledgerMoney(row.rowTotal) }}</td>
              </tr>
              <tr v-if="!result || result.rows.length === 0">
                <td :colspan="(result?.columns.length || 0) + 2" class="report-empty-cell">本月该组合没有发生数据</td>
              </tr>
            </tbody>
            <tfoot v-if="result && result.rows.length > 0">
              <tr>
                <td class="sticky-col total-cell">合　计</td>
                <td v-for="col in result.columns" :key="col.id" class="amount-cell total-cell">
                  {{ ledgerMoney(result.colTotals[col.id] || '0.00') }}
                </td>
                <td class="amount-cell total-cell grand-total">{{ ledgerMoney(result.grandTotal) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-3 { margin-bottom: 10px; }
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }
.preset-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.preset-label { font-size: 12px; color: var(--solo-text-muted); }
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  padding: 10px 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
.report-table-wrapper { overflow-x: auto; }
.matrix-table .sticky-col {
  position: sticky;
  left: 0;
  background: var(--solo-bg-card);
  z-index: 1;
  min-width: 170px;
}
.matrix-table thead .sticky-col,
.matrix-table tfoot .sticky-col {
  background: var(--solo-bg-soft);
}
.amount-head { text-align: right; min-width: 115px; white-space: nowrap; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.clickable { cursor: pointer; color: var(--solo-primary); }
.clickable:hover { background: var(--solo-bg-blue-soft); text-decoration: underline; }
.total-col { font-weight: 700; min-width: 125px; }
.total-cell { font-weight: 700; }
.grand-total { color: var(--solo-primary); }
.unassigned-row td { color: var(--solo-text-muted); font-style: italic; }
.print-only { display: none; }
.print-header { text-align: center; margin-bottom: 12px; }
.print-title { font-size: 18px; font-weight: 700; }
.print-meta { font-size: 12px; margin-top: 4px; }
@media print {
  .print-only { display: block; }
  .report-card { box-shadow: none !important; }
}
</style>
