<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, NTag, useMessage } from 'naive-ui'
import * as XLSX from 'xlsx'
import { listAuxiliary, type AuxiliaryItem, type AuxiliaryKind } from '@/api/auxiliary'
import {
  getAuxiliarySubLedger,
  getLedgerAccounts,
  type AuxiliarySubLedgerResponse,
  type LedgerAccountOption,
} from '@/api/ledger'
import { getCurrentMonth } from '@/utils/format'
import { directionLabel, ledgerAmountCell, ledgerBalanceOrDash, ledgerMoney } from '@/utils/ledgerFormat'
import { saveWorkbook } from '@/utils/export'
import {
  AUXILIARY_KIND_META,
  AUXILIARY_KIND_OPTIONS,
  UNASSIGNED_AUX_ITEM_ID,
  accountsForAuxiliaryKind,
  formatAuxiliaryLineText,
  isAuxiliaryKind,
} from '@/utils/auxiliary'
import { SoloMonthPicker } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const kind = ref<AuxiliaryKind>(isAuxiliaryKind(route.query.kind) ? route.query.kind : 'partner')
const itemId = ref<string>(String(route.query.itemId || ''))
const accountCode = ref<string | null>(
  route.query.accountCode ? String(route.query.accountCode) : null,
)

const loading = ref(false)
const masterItems = ref<AuxiliaryItem[]>([])
const allAccounts = ref<LedgerAccountOption[]>([])
const result = ref<AuxiliarySubLedgerResponse | null>(null)

const kindLabel = computed(() => AUXILIARY_KIND_META[kind.value].label)

const itemOptions = computed(() => [
  ...masterItems.value.map((item) => ({
    label: `${item.code ? item.code + ' ' : ''}${item.name}${item.isActive ? '' : '（停用）'}`,
    value: item.id,
  })),
  { label: AUXILIARY_KIND_META[kind.value].unassignedLabel, value: UNASSIGNED_AUX_ITEM_ID },
])

const accountOptions = computed(() => {
  const matched = accountsForAuxiliaryKind(allAccounts.value, kind.value)
  return [
    { label: '全部涉账科目', value: '' },
    ...matched.map((acct) => ({ label: `${acct.code} ${acct.name}`, value: acct.code })),
  ]
})

const selectedAccountValue = computed({
  get: () => accountCode.value || '',
  set: (val: string) => {
    accountCode.value = val || null
  },
})

async function loadReferences() {
  try {
    const [masterRes, acctRes] = await Promise.all([
      listAuxiliary(kind.value, { includeInactive: true, pageSize: 500 }),
      allAccounts.value.length ? Promise.resolve({ accounts: allAccounts.value }) : getLedgerAccounts(),
    ])
    masterItems.value = masterRes.items || []
    allAccounts.value = acctRes.accounts || []
    if (!itemId.value || !itemOptions.value.some((opt) => opt.value === itemId.value)) {
      itemId.value = masterItems.value[0]?.id || UNASSIGNED_AUX_ITEM_ID
    }
  } catch {
    masterItems.value = []
  }
}

async function query() {
  if (!itemId.value) return
  loading.value = true
  try {
    result.value = await getAuxiliarySubLedger({
      kind: kind.value,
      itemId: itemId.value,
      month: month.value,
      accountCode: accountCode.value,
    })
  } catch {
    message.error('查询核算项目明细账失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => [route.query.kind, route.query.itemId, route.query.accountCode, route.query.month],
  async ([qKind, qItem, qAcct, qMonth]) => {
    if (route.query.book !== 'auxiliary-sub-ledger') return
    if (isAuxiliaryKind(qKind)) kind.value = qKind
    if (typeof qItem === 'string' && qItem) itemId.value = qItem
    if (typeof qAcct === 'string') accountCode.value = qAcct || null
    if (typeof qMonth === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(qMonth)) month.value = qMonth
    await loadReferences()
    await query()
  },
)

watch(kind, async () => {
  accountCode.value = null
  itemId.value = ''
  await loadReferences()
  await query()
})

function exportExcel() {
  if (!result.value) {
    message.warning('没有可导出的数据')
    return
  }
  const sheetRows = [
    {
      日期: '',
      凭证号: '',
      科目: '',
      摘要: '期初余额',
      对方科目: '',
      借方: '',
      贷方: '',
      方向: directionLabel(result.value.openingDirection),
      余额: Math.abs(Number(result.value.opening)),
    },
    ...result.value.lines.map((line) => ({
      日期: line.date,
      凭证号: line.voucherRef || '草稿',
      科目: `${line.accountCode} ${line.accountName}`,
      摘要: line.summary,
      对方科目: line.counterparties.map((c) => `${c.code} ${c.name}`).join('、'),
      借方: Number(line.debit) === 0 ? '' : Number(line.debit),
      贷方: Number(line.credit) === 0 ? '' : Number(line.credit),
      方向: directionLabel(line.balanceDirection),
      余额: Number(line.balance) === 0 ? '' : Math.abs(Number(line.balance)),
    })),
  ]
  const worksheet = XLSX.utils.json_to_sheet(sheetRows)
  worksheet['!cols'] = [
    { wch: 12 }, { wch: 14 }, { wch: 22 }, { wch: 28 },
    { wch: 24 }, { wch: 14 }, { wch: 14 }, { wch: 8 }, { wch: 14 },
  ]
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '核算项目明细账')
  saveWorkbook(workbook, `核算项目明细账_${result.value.itemName}_${month.value}.xlsx`)
  message.success('已导出')
}

function printPage() {
  window.print()
}

onMounted(async () => {
  await loadReferences()
  await query()
})
</script>

<template>
  <div class="report-container">
    <div class="report-header no-print">
      <div class="report-header-left">
        <div class="report-title-section">
          <h2>核算项目明细账</h2>
          <p class="report-subtitle">按单个往来单位 / 部门 / 项目 / 职员逐笔展开分录、对方科目与滚动余额，可点击凭证号直接查看原凭证</p>
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

    <div class="filter-bar mb-4 no-print">
      <NFormItem label="核算类别" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="kind" :options="AUXILIARY_KIND_OPTIONS" style="width: 120px;" />
      </NFormItem>
      <NFormItem :label="kindLabel" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="itemId" :options="itemOptions" filterable style="width: 220px;" />
      </NFormItem>
      <NFormItem label="科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="selectedAccountValue" :options="accountOptions" filterable style="width: 200px;" />
      </NFormItem>
      <NFormItem label="月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 145px;" />
      </NFormItem>
      <NButton type="primary" @click="query">查询</NButton>
    </div>

    <div v-if="result" class="print-only print-header">
      <div class="print-title">{{ result.kindLabel }}明细账 · {{ result.itemName }}</div>
      <div class="print-meta">会计期间：{{ month }} · 单位：元</div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <NCard :bordered="false" class="report-card mb-4">
          <div class="report-summary-row">
            <div class="report-summary-item">
              <span class="report-summary-label">{{ result.kindLabel }}</span>
              <span class="report-summary-val">{{ result.itemName }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">期初余额</span>
              <span class="report-summary-val">
                {{ directionLabel(result.openingDirection) }} {{ ledgerBalanceOrDash(result.opening) }}
              </span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">本期借方</span>
              <span class="report-summary-val">{{ ledgerMoney(result.periodDebit) }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">本期贷方</span>
              <span class="report-summary-val">{{ ledgerMoney(result.periodCredit) }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">期末余额</span>
              <span class="report-summary-val balance">
                {{ directionLabel(result.closingDirection) }} {{ ledgerBalanceOrDash(result.closing) }}
              </span>
            </div>
          </div>
        </NCard>

        <NCard :bordered="false" class="report-card">
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th style="width: 100px;">日期</th>
                  <th style="width: 110px;">凭证号</th>
                  <th style="width: 170px;">科目</th>
                  <th>摘要</th>
                  <th style="width: 165px;">对方科目</th>
                  <th style="width: 120px; text-align: right;">借方</th>
                  <th style="width: 120px; text-align: right;">贷方</th>
                  <th style="width: 145px; text-align: right;">余额</th>
                </tr>
              </thead>
              <tbody>
                <tr class="opening-row">
                  <td>—</td>
                  <td>—</td>
                  <td>—</td>
                  <td>期初余额</td>
                  <td>—</td>
                  <td class="amount-cell"></td>
                  <td class="amount-cell"></td>
                  <td class="amount-cell">
                    <span v-if="Number(result.opening) !== 0" class="dir-hint">{{ directionLabel(result.openingDirection) }}</span>
                    {{ ledgerBalanceOrDash(result.opening) }}
                  </td>
                </tr>
                <tr v-for="(line, idx) in result.lines" :key="`${line.entryId}-${idx}`">
                  <td class="mono">{{ line.date }}</td>
                  <td>
                    <NButton text type="primary" size="tiny" @click="router.push(`/vouchers/${line.entryId}`)">
                      {{ line.voucherRef || '查看' }}
                    </NButton>
                  </td>
                  <td>{{ line.accountCode }} {{ line.accountName }}</td>
                  <td>
                    <span>{{ line.summary }}</span>
                    <NTag v-if="line.backfill" size="tiny" type="info" :bordered="false" class="line-tag">补记</NTag>
                    <NTag v-if="line.isReversal" size="tiny" type="warning" :bordered="false" class="line-tag">冲销</NTag>
                    <div v-if="formatAuxiliaryLineText(line.auxiliary)" class="aux-sub">
                      {{ formatAuxiliaryLineText(line.auxiliary) }}
                    </div>
                  </td>
                  <td class="counterparty-cell">
                    {{ line.counterparties.map((c) => `${c.code} ${c.name}`).join('、') || '—' }}
                  </td>
                  <td class="amount-cell">{{ ledgerAmountCell(line.debit) }}</td>
                  <td class="amount-cell">{{ ledgerAmountCell(line.credit) }}</td>
                  <td class="amount-cell balance">
                    <span v-if="Number(line.balance) !== 0" class="dir-hint">{{ directionLabel(line.balanceDirection) }}</span>
                    {{ ledgerBalanceOrDash(line.balance) }}
                  </td>
                </tr>
                <tr v-if="result.lines.length === 0">
                  <td colspan="8" class="report-empty-cell">本月该核算项目没有凭证分录</td>
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <td class="total-cell" colspan="5">本期合计（同期间冲销对已剔除）</td>
                  <td class="amount-cell total-cell">{{ ledgerMoney(result.periodDebit) }}</td>
                  <td class="amount-cell total-cell">{{ ledgerMoney(result.periodCredit) }}</td>
                  <td class="amount-cell total-cell">
                    <span v-if="Number(result.closing) !== 0" class="dir-hint">{{ directionLabel(result.closingDirection) }}</span>
                    {{ ledgerBalanceOrDash(result.closing) }}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }
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
.report-summary-row { display: flex; flex-wrap: wrap; gap: 16px 32px; }
.report-summary-item { display: flex; flex-direction: column; gap: 4px; }
.report-summary-label { font-size: 12px; color: var(--solo-text-muted); }
.report-summary-val { font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }
.report-table-wrapper { overflow-x: auto; }
.mono { font-variant-numeric: tabular-nums; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.dir-hint { margin-right: 4px; font-size: 11px; color: var(--solo-text-muted); }
.opening-row td { color: var(--solo-text-muted); background: var(--solo-bg-soft); }
.counterparty-cell { font-size: 12px; color: var(--solo-text-muted); }
.aux-sub { font-size: 11px; color: var(--solo-text-muted); margin-top: 2px; }
.line-tag { margin-left: 6px; }
.total-cell { font-weight: 700; }
.print-only { display: none; }
.print-header { text-align: center; margin-bottom: 12px; }
.print-title { font-size: 18px; font-weight: 700; }
.print-meta { font-size: 12px; margin-top: 4px; }
@media print {
  .print-only { display: block; }
  .report-card { box-shadow: none !important; }
}
</style>
