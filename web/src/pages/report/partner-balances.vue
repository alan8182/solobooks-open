<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NFormItem, NSelect, NSpin, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import * as XLSX from 'xlsx'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { saveWorkbook } from '@/utils/export'
import { SoloMonthPicker } from '@/components/ui'

interface PartnerBalanceRow {
  partnerId: string
  partnerName: string
  opening: number
  debit: number
  credit: number
  closing: number
  unassigned: boolean
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const accountCode = ref(String(route.query.account || '1122'))
const loading = ref(false)
const items = ref<PartnerBalanceRow[]>([])
const unassigned = ref<PartnerBalanceRow | null>(null)
const totalDebit = ref(0)
const totalCredit = ref(0)

const accountOptions = [
  { label: '1122 应收账款', value: '1122' },
  { label: '1123 预付账款', value: '1123' },
  { label: '1221 其他应收款', value: '1221' },
  { label: '2202 应付账款', value: '2202' },
  { label: '2203 预收账款', value: '2203' },
  { label: '2241 其他应付款', value: '2241' },
]

const accountLabel = computed(
  () => accountOptions.find((item) => item.value === accountCode.value)?.label || accountCode.value,
)

function directionOf(value: number) {
  return value > 0 ? '借' : value < 0 ? '贷' : '平'
}

function moneyOrDash(value: number) {
  return value === 0 ? '—' : formatMoney(Math.abs(value))
}

async function load() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/partner-balances', {
      params: { account_code: accountCode.value, month: month.value },
    })
    items.value = (data.items || []).map((row: any) => ({
      partnerId: row.partnerId,
      partnerName: row.partnerName,
      opening: Number(row.opening),
      debit: Number(row.debit),
      credit: Number(row.credit),
      closing: Number(row.closing),
      unassigned: !!row.unassigned,
    }))
    unassigned.value = data.unassigned
      ? {
          partnerId: '',
          partnerName: data.unassigned.partnerName,
          opening: Number(data.unassigned.opening),
          debit: Number(data.unassigned.debit),
          credit: Number(data.unassigned.credit),
          closing: Number(data.unassigned.closing),
          unassigned: true,
        }
      : null
    totalDebit.value = Number(data.totalDebit) || 0
    totalCredit.value = Number(data.totalCredit) || 0
  } catch {
    message.error('查询核算项目余额表失败')
  } finally {
    loading.value = false
  }
}

function exportExcel() {
  const rows = [...items.value, ...(unassigned.value ? [unassigned.value] : [])].map((row) => ({
    '往来单位': row.partnerName,
    '期初方向': directionOf(row.opening),
    '期初余额': row.opening === 0 ? '' : Math.abs(row.opening),
    '借方发生': row.debit,
    '贷方发生': row.credit,
    '期末方向': directionOf(row.closing),
    '期末余额': row.closing === 0 ? '' : Math.abs(row.closing),
  }))
  if (rows.length === 0) {
    message.warning('没有可导出的数据')
    return
  }
  const worksheet = XLSX.utils.json_to_sheet(rows)
  worksheet['!cols'] = [{ wch: 28 }, { wch: 10 }, { wch: 16 }, { wch: 16 }, { wch: 16 }, { wch: 10 }, { wch: 16 }]
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '核算项目余额表')
  saveWorkbook(workbook, `核算项目余额表_${accountCode.value}_${month.value}.xlsx`)
  message.success('已导出')
}

function printPage() {
  window.print()
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header no-print">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push({ path: '/reports', query: { month } })">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>核算项目余额表</h2>
          <p class="report-subtitle">按往来单位汇总往来科目的期初、本期借贷与期末；未挂往来单位的分录单独列示，不丢数据</p>
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
      <NFormItem label="往来科目" label-placement="left" :show-feedback="false">
        <NSelect v-model:value="accountCode" :options="accountOptions" style="width: 180px;" />
      </NFormItem>
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <SoloMonthPicker v-model:value="month" style="width: 150px;" />
      </NFormItem>
      <NButton type="primary" @click="load">查询</NButton>
      <div class="filter-actions">
        <NButton secondary @click="exportExcel">
          <template #icon><i class="ri-download-2-line" /></template>
          导出 Excel
        </NButton>
        <NButton secondary @click="printPage">
          <template #icon><i class="ri-printer-line" /></template>
          打印
        </NButton>
      </div>
    </div>

    <!-- 打印抬头 -->
    <div class="print-only print-header">
      <div class="print-title">{{ accountLabel }} · 核算项目余额表</div>
      <div class="print-meta">会计期间：{{ month }}</div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th>往来单位</th>
                <th style="width: 150px; text-align: right;">期初余额</th>
                <th style="width: 140px; text-align: right;">借方发生</th>
                <th style="width: 140px; text-align: right;">贷方发生</th>
                <th style="width: 150px; text-align: right;">期末余额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in items" :key="row.partnerId">
                <td>{{ row.partnerName }}</td>
                <td class="amount-cell">
                  {{ moneyOrDash(row.opening) }}
                  <span v-if="row.opening !== 0" class="dir-hint">{{ directionOf(row.opening) }}</span>
                </td>
                <td class="amount-cell">{{ moneyOrDash(row.debit) }}</td>
                <td class="amount-cell">{{ moneyOrDash(row.credit) }}</td>
                <td class="amount-cell balance">
                  {{ moneyOrDash(row.closing) }}
                  <span v-if="row.closing !== 0" class="dir-hint">{{ directionOf(row.closing) }}</span>
                </td>
              </tr>
              <tr v-if="unassigned" class="unassigned-row">
                <td>{{ unassigned.partnerName }}</td>
                <td class="amount-cell">{{ moneyOrDash(unassigned.opening) }}</td>
                <td class="amount-cell">{{ moneyOrDash(unassigned.debit) }}</td>
                <td class="amount-cell">{{ moneyOrDash(unassigned.credit) }}</td>
                <td class="amount-cell">{{ moneyOrDash(unassigned.closing) }}</td>
              </tr>
              <tr v-if="items.length === 0 && !unassigned">
                <td colspan="5" class="report-empty-cell">本月该科目没有发生额</td>
              </tr>
            </tbody>
            <tfoot v-if="items.length > 0 || unassigned">
              <tr>
                <td class="total-cell">合　计</td>
                <td></td>
                <td class="amount-cell total-cell">{{ formatMoney(totalDebit) }}</td>
                <td class="amount-cell total-cell">{{ formatMoney(totalCredit) }}</td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p v-if="unassigned" class="hint">
          <i class="ri-information-line" />
          「未指定往来单位」为未挂往来档案的分录；可到凭证详情给草稿凭证补挂，或到往来单位档案补齐主数据。
        </p>
      </NCard>
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
  background: #ffffff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
.filter-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.report-table-wrapper { overflow-x: auto; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.dir-hint { margin-left: 4px; font-size: 11px; color: var(--solo-text-muted); }
.total-cell { font-weight: 700; }
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
