<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NDatePicker, NFormItem, NSpin, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import * as XLSX from 'xlsx'
import { formatMoney, getCurrentMonth } from '@/utils/format'

interface AdvanceRow {
  payer: string
  kind: 'boss' | 'employee'
  name: string
  opening: number
  advance: number
  reimbursed: number
  closing: number
  count: number
  lastDate: string
}

interface AdvanceTotals {
  opening: number
  advance: number
  reimbursed: number
  closing: number
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const loading = ref(false)
const items = ref<AdvanceRow[]>([])
const totals = ref<AdvanceTotals>({ opening: 0, advance: 0, reimbursed: 0, closing: 0 })
const other = ref({ count: 0, advance: 0, reimbursed: 0 })

const hasData = computed(() => items.value.length > 0)

function num(value: unknown) {
  return Number(value) || 0
}

function moneyOrDash(value: number) {
  return value === 0 ? '—' : formatMoney(Math.abs(value))
}

function signed(value: number) {
  return value < 0 ? '−' : ''
}

async function load() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/advance-balances', {
      params: { month: month.value },
    })
    items.value = (data.items || []).map((row: any) => ({
      payer: String(row.payer || ''),
      kind: row.kind === 'boss' ? 'boss' : 'employee',
      name: String(row.name || ''),
      opening: num(row.opening),
      advance: num(row.advance),
      reimbursed: num(row.reimbursed),
      closing: num(row.closing),
      count: num(row.count),
      lastDate: String(row.lastDate || ''),
    }))
    totals.value = {
      opening: num(data.totals?.opening),
      advance: num(data.totals?.advance),
      reimbursed: num(data.totals?.reimbursed),
      closing: num(data.totals?.closing),
    }
    other.value = {
      count: num(data.other?.count),
      advance: num(data.other?.advance),
      reimbursed: num(data.other?.reimbursed),
    }
  } catch {
    message.error('查询垫付台账失败')
  } finally {
    loading.value = false
  }
}

function exportExcel() {
  const rows = items.value.map((row) => ({
    '垫付人': row.name,
    '类型': row.kind === 'boss' ? '老板' : '员工',
    '期初未报': row.opening,
    '本期垫付': row.advance,
    '本期已报销': row.reimbursed,
    '期末未报': row.closing,
    '笔数': row.count,
    '最近一笔': row.lastDate,
  }))
  if (rows.length === 0) {
    message.warning('没有可导出的数据')
    return
  }
  const worksheet = XLSX.utils.json_to_sheet(rows)
  worksheet['!cols'] = [
    { wch: 16 },
    { wch: 8 },
    { wch: 14 },
    { wch: 14 },
    { wch: 14 },
    { wch: 14 },
    { wch: 8 },
    { wch: 14 },
  ]
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '垫付台账')
  XLSX.writeFile(workbook, `垫付台账_${month.value}.xlsx`)
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
          <h2>垫付台账</h2>
          <p class="report-subtitle">
            谁替公司垫了钱、报了没有、还差多少。按凭证上的付款方归集，老板与员工分开列示
          </p>
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
      <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
        <NDatePicker
          v-model:formatted-value="month"
          type="month"
          value-format="yyyy-MM"
          format="yyyy-MM"
          style="width: 150px;"
        />
      </NFormItem>
      <NButton type="primary" @click="load">查询</NButton>
    </div>

    <!-- 打印抬头 -->
    <div class="print-only print-header">
      <div class="print-title">垫付台账</div>
      <div class="print-meta">会计期间：{{ month }}</div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="report-card">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th>垫付人</th>
                <th style="width: 80px;">类型</th>
                <th style="width: 140px; text-align: right;">期初未报</th>
                <th style="width: 140px; text-align: right;">本期垫付</th>
                <th style="width: 140px; text-align: right;">本期已报销</th>
                <th style="width: 150px; text-align: right;">期末未报</th>
                <th style="width: 80px; text-align: right;">笔数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in items" :key="row.payer">
                <td>
                  {{ row.name }}
                  <span v-if="row.lastDate" class="dir-hint">最近一笔 {{ row.lastDate }}</span>
                </td>
                <td>
                  <span class="kind-tag" :class="row.kind">{{ row.kind === 'boss' ? '老板' : '员工' }}</span>
                </td>
                <td class="amount-cell">{{ moneyOrDash(row.opening) }}</td>
                <td class="amount-cell">{{ moneyOrDash(row.advance) }}</td>
                <td class="amount-cell">{{ moneyOrDash(row.reimbursed) }}</td>
                <td class="amount-cell balance" :class="{ negative: row.closing < 0 }">
                  {{ signed(row.closing) }}{{ moneyOrDash(row.closing) }}
                </td>
                <td class="amount-cell">{{ row.count }}</td>
              </tr>
              <tr v-if="!hasData">
                <td colspan="7" class="report-empty-cell">
                  本月没有垫付记录。用 AI 记账时说一句「这笔是我先垫的」，或在确认卡片里把付款方改成老板/员工垫付
                </td>
              </tr>
            </tbody>
            <tfoot v-if="hasData">
              <tr>
                <td class="total-cell">合　计</td>
                <td></td>
                <td class="amount-cell total-cell">{{ moneyOrDash(totals.opening) }}</td>
                <td class="amount-cell total-cell">{{ moneyOrDash(totals.advance) }}</td>
                <td class="amount-cell total-cell">{{ moneyOrDash(totals.reimbursed) }}</td>
                <td class="amount-cell total-cell">{{ moneyOrDash(totals.closing) }}</td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p class="hint">
          <i class="ri-information-line" />
          本表只统计垫付业务，是「其他应付款」的一部分。
          <template v-if="other.count > 0">
            其他应付款另有 {{ other.count }} 笔非垫付业务（贷方 {{ formatMoney(other.advance) }} 元、借方
            {{ formatMoney(other.reimbursed) }} 元，如发票待付款、往来款），未计入本表，
            与科目余额表的差额即为这部分。
          </template>
          <template v-else>非垫付业务（发票待付款、往来款等）不计入本表。</template>
          期末未报为负数表示已报销金额超过垫付额，需要核对。
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
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 8px;
}
.filter-bar :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
.report-table-wrapper { overflow-x: auto; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.balance { font-weight: 700; color: var(--solo-primary); }
.balance.negative { color: var(--solo-warning, #a05c10); }
.dir-hint { margin-left: 6px; font-size: 11px; color: var(--solo-text-muted); }
.kind-tag {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid var(--solo-border, #dde3ea);
  color: var(--solo-text-secondary, #4b5563);
}
.kind-tag.boss {
  border-color: var(--solo-primary, #1747b0);
  color: var(--solo-primary, #1747b0);
}
</style>
