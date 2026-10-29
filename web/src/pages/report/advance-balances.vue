<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NCard, NFormItem, NInput, NSelect, NSwitch, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns, DataTableCreateSummary } from 'naive-ui'
import apiClient from '@/api/client'
import * as XLSX from 'xlsx'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import { saveWorkbook } from '@/utils/export'
import {
  SoloDrawer, SoloEmpty, SoloFieldHelp, SoloMonthPicker, SoloTable,
} from '@/components/ui'
import { usePageRefresh } from '@/composables/usePageRefresh'

export interface AdvanceEntryItem {
  id: string
  sourceId?: string | null
  voucherNo?: string
  date: string
  period: string
  summary: string
  category: string
  amount: string
  direction: 'advance' | 'reimbursement'
  isCurrentMonth: boolean
}

export interface AdvanceRow {
  payer: string
  kind: 'boss' | 'employee'
  name: string
  opening: number
  advance: number
  reimbursed: number
  closing: number
  count: number
  lastDate: string
  entries?: AdvanceEntryItem[]
}

export interface AdvanceTotals {
  opening: number
  advance: number
  reimbursed: number
  closing: number
}

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route?.query?.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const loading = ref(false)
const items = ref<AdvanceRow[]>([])
const totals = ref<AdvanceTotals>({ opening: 0, advance: 0, reimbursed: 0, closing: 0 })
const other = ref({ count: 0, advance: 0, reimbursed: 0 })

// 过滤控制
const kindFilter = ref<'all' | 'boss' | 'employee'>('all')
const onlyUnsettled = ref<boolean>(false)
const keyword = ref<string>('')

// 抽屉详情
const drawerVisible = ref(false)
const selectedPerson = ref<AdvanceRow | null>(null)

const kindFilterOptions = [
  { label: '全部人员', value: 'all' },
  { label: '仅老板', value: 'boss' },
  { label: '仅员工', value: 'employee' },
]

function num(value: unknown) {
  return Number(value) || 0
}

function moneyOrDash(value: number) {
  return value === 0 ? '—' : formatMoney(Math.abs(value))
}

function signed(value: number) {
  return value < -0.005 ? '−' : ''
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
      entries: (row.entries || []).map((e: any) => ({
        id: String(e.id || ''),
        sourceId: e.sourceId ? String(e.sourceId) : null,
        voucherNo: String(e.voucherNo || ''),
        date: String(e.date || ''),
        period: String(e.period || ''),
        summary: String(e.summary || ''),
        category: String(e.category || ''),
        amount: String(e.amount || '0.00'),
        direction: e.direction === 'reimbursement' ? 'reimbursement' : 'advance',
        isCurrentMonth: Boolean(e.isCurrentMonth),
      })),
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

    // 若抽屉开着，刷新抽屉选中对象
    if (selectedPerson.value) {
      const refreshed = items.value.find((p) => p.payer === selectedPerson.value?.payer)
      if (refreshed) selectedPerson.value = refreshed
    }
  } catch {
    message.error('查询垫付台账失败')
  } finally {
    loading.value = false
  }
}

usePageRefresh(load)

// 月份快速切换
function shiftMonth(offset: number) {
  const [yStr, mStr] = month.value.split('-')
  let year = parseInt(yStr, 10)
  let m = parseInt(mStr, 10) + offset
  if (m < 1) {
    m = 12
    year -= 1
  } else if (m > 12) {
    m = 1
    year += 1
  }
  month.value = `${year}-${String(m).padStart(2, '0')}`
  load()
}

function setThisMonth() {
  month.value = getCurrentMonth()
  load()
}

// 统计衍生指标
const bossRow = computed(() => items.value.find((r) => r.kind === 'boss'))
const bossClosing = computed(() => (bossRow.value ? bossRow.value.closing : 0))
const employeeRows = computed(() => items.value.filter((r) => r.kind === 'employee'))
const employeeClosing = computed(() =>
  employeeRows.value.reduce((sum, r) => sum + r.closing, 0),
)
const employeeCount = computed(() => employeeRows.value.length)
const totalEntryCount = computed(() => items.value.reduce((sum, r) => sum + r.count, 0))

// 过滤后的数据
const filteredItems = computed(() => {
  return items.value.filter((row) => {
    if (kindFilter.value !== 'all' && row.kind !== kindFilter.value) {
      return false
    }
    if (onlyUnsettled.value && Math.abs(row.closing) < 0.005) {
      return false
    }
    if (keyword.value.trim()) {
      const q = keyword.value.trim().toLowerCase()
      if (!row.name.toLowerCase().includes(q)) {
        return false
      }
    }
    return true
  })
})

function openDetail(row: AdvanceRow) {
  selectedPerson.value = row
  drawerVisible.value = true
}

function viewVoucher(entryId: string) {
  if (!entryId) return
  router.push({ path: `/vouchers/${entryId}` })
}

function viewSourceEntry(sourceId: string | null | undefined, entryId: string) {
  if (sourceId) {
    router.push({ path: `/ledger/${sourceId}` })
  } else if (entryId) {
    router.push({ path: `/vouchers/${entryId}` })
  }
}

function copyPersonSummary(person: AdvanceRow) {
  const lines = [
    `【${person.name} 垫付对账单（${month.value}）】`,
    `身份：${person.kind === 'boss' ? '老板（股东）' : '员工'}`,
    `期初未报：¥${formatMoney(person.opening)}`,
    `本期垫付：¥${formatMoney(person.advance)}`,
    `本期已报销：¥${formatMoney(person.reimbursed)}`,
    `期末未报：¥${formatMoney(person.closing)}`,
    `单据笔数：${person.count} 笔`,
    '',
    '包含单据明细：',
    ...(person.entries || []).map((e, idx) =>
      `${idx + 1}. [${e.date || '无日期'}] ${e.direction === 'advance' ? '垫付' : '报销'} ¥${formatMoney(Number(e.amount))} - ${e.category ? e.category + ' · ' : ''}${e.summary}`,
    ),
  ]
  const text = lines.join('\n')
  if (navigator?.clipboard?.writeText) {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        message.success('已复制对账文本，可直接发送微信核对')
      })
      .catch(() => {
        message.info('复制失败，请手动选择复制')
      })
  } else {
    message.info('环境不支持自动复制，请手动选中复制')
  }
}

const columns: DataTableColumns<AdvanceRow> = [
  {
    title: '垫付人',
    key: 'name',
    minWidth: 160,
    render(row) {
      return h('div', { class: 'payer-name-cell' }, [
        h('span', { class: 'payer-name' }, row.name),
        row.lastDate
          ? h('span', { class: 'dir-hint' }, `最近一笔 ${row.lastDate}`)
          : null,
      ])
    },
  },
  {
    title: '类型',
    key: 'kind',
    width: 85,
    render(row) {
      const isBoss = row.kind === 'boss'
      return h(
        NTag,
        {
          size: 'small',
          type: isBoss ? 'info' : 'default',
          bordered: false,
          class: isBoss ? 'tag-boss' : 'tag-emp',
        },
        { default: () => (isBoss ? '老板' : '员工') },
      )
    },
  },
  {
    title: '期初未报',
    key: 'opening',
    width: 130,
    align: 'right',
    render(row) {
      return h('span', { class: 'amount-cell' }, moneyOrDash(row.opening))
    },
  },
  {
    title: '本期垫付',
    key: 'advance',
    width: 130,
    align: 'right',
    render(row) {
      return h('span', { class: 'amount-cell' }, moneyOrDash(row.advance))
    },
  },
  {
    title: '本期已报销',
    key: 'reimbursed',
    width: 130,
    align: 'right',
    render(row) {
      return h('span', { class: 'amount-cell' }, moneyOrDash(row.reimbursed))
    },
  },
  {
    title: '期末未报',
    key: 'closing',
    width: 140,
    align: 'right',
    render(row) {
      const isNegative = row.closing < -0.005
      return h(
        'span',
        {
          class: ['amount-cell balance', isNegative ? 'negative' : ''],
        },
        `${signed(row.closing)}${moneyOrDash(row.closing)}`,
      )
    },
  },
  {
    title: '笔数',
    key: 'count',
    width: 80,
    align: 'right',
    render(row) {
      return h('span', { class: 'amount-cell' }, String(row.count))
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    align: 'center',
    render(row) {
      return h(
        NButton,
        {
          size: 'tiny',
          quaternary: true,
          type: 'primary',
          onClick: (e: MouseEvent) => {
            e.stopPropagation()
            openDetail(row)
          },
        },
        { default: () => '查看明细' },
      )
    },
  },
]

const summaryRow: DataTableCreateSummary = () => {
  return {
    name: {
      value: h('span', { style: 'font-weight: 700;' }, '合　计'),
    },
    kind: { value: '' },
    opening: {
      value: h('span', { class: 'amount-cell total-cell' }, moneyOrDash(totals.value.opening)),
    },
    advance: {
      value: h('span', { class: 'amount-cell total-cell' }, moneyOrDash(totals.value.advance)),
    },
    reimbursed: {
      value: h('span', { class: 'amount-cell total-cell' }, moneyOrDash(totals.value.reimbursed)),
    },
    closing: {
      value: h(
        'span',
        {
          class: [
            'amount-cell total-cell balance',
            totals.value.closing < -0.005 ? 'negative' : '',
          ],
        },
        `${signed(totals.value.closing)}${moneyOrDash(totals.value.closing)}`,
      ),
    },
    count: {
      value: h('span', { class: 'amount-cell total-cell' }, String(totalEntryCount.value)),
    },
    actions: { value: '' },
  }
}

function exportExcel() {
  const exportRows = filteredItems.value.map((row) => ({
    '垫付人': row.name,
    '类型': row.kind === 'boss' ? '老板' : '员工',
    '期初未报': row.opening,
    '本期垫付': row.advance,
    '本期已报销': row.reimbursed,
    '期末未报': row.closing,
    '笔数': row.count,
    '最近一笔': row.lastDate,
  }))
  if (exportRows.length === 0) {
    message.warning('没有可导出的数据')
    return
  }
  const worksheet = XLSX.utils.json_to_sheet(exportRows)
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
  saveWorkbook(workbook, `垫付台账_${month.value}.xlsx`)
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
            核算老板与员工替公司先行垫付的经营开支，追踪报销冲销进度与期末待还余额
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

    <!-- 顶部核心 KPI 统计卡片 -->
    <div class="summary-cards no-print">
      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">
          期末待报总额
          <SoloFieldHelp term="垫付台账" />
        </span>
        <span class="summary-value balance" :class="{ negative: totals.closing < -0.005 }">
          {{ signed(totals.closing) }}{{ formatMoney(Math.abs(totals.closing)) }}
        </span>
        <span class="summary-sub">
          共 {{ items.length }} 位垫付人 · {{ totalEntryCount }} 笔单据
          <template v-if="totals.closing < -0.005">（存在超额报销，需核对）</template>
        </span>
      </NCard>

      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">老板个人垫付待还</span>
        <span class="summary-value income">{{ formatMoney(Math.abs(bossClosing)) }}</span>
        <span class="summary-sub">公户资金充裕时，可随时公转私免税归还</span>
      </NCard>

      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">员工待报销款</span>
        <span class="summary-value">{{ formatMoney(Math.abs(employeeClosing)) }}</span>
        <span class="summary-sub">{{ employeeCount }} 位员工垫付挂账尚未核销</span>
      </NCard>

      <NCard :bordered="false" class="summary-card">
        <span class="summary-label">本期动态（垫付 vs 已报销）</span>
        <span class="summary-value" style="font-size: 19px;">
          +{{ formatMoney(totals.advance) }}
          <span style="font-size: 14px; font-weight: normal; color: var(--solo-text-muted); margin: 0 4px;">/</span>
          -{{ formatMoney(totals.reimbursed) }}
        </span>
        <span class="summary-sub">
          期初未报余额 {{ formatMoney(totals.opening) }}
        </span>
      </NCard>
    </div>

    <!-- 过滤与搜索栏 -->
    <div class="filter-bar mb-4 no-print">
      <div class="filter-group">
        <NFormItem label="会计月份" label-placement="left" :show-feedback="false">
          <SoloMonthPicker v-model:value="month" style="width: 140px;" @update:value="load" />
        </NFormItem>
        <div class="month-stepper">
          <NButton size="small" quaternary @click="shiftMonth(-1)">上月</NButton>
          <NButton size="small" quaternary @click="setThisMonth">本月</NButton>
          <NButton size="small" quaternary @click="shiftMonth(1)">下月</NButton>
        </div>
      </div>

      <div class="filter-group">
        <NFormItem label="人员" label-placement="left" :show-feedback="false">
          <NSelect
            v-model:value="kindFilter"
            :options="kindFilterOptions"
            style="width: 110px;"
          />
        </NFormItem>

        <NFormItem label="姓名" label-placement="left" :show-feedback="false">
          <NInput
            v-model:value="keyword"
            placeholder="搜索垫付人姓名"
            clearable
            style="width: 150px;"
          />
        </NFormItem>

        <div class="switch-item">
          <NSwitch v-model:value="onlyUnsettled" size="small" />
          <span class="switch-label">仅看未结清</span>
        </div>
      </div>

      <NButton type="primary" secondary @click="load">刷新</NButton>
    </div>

    <!-- 打印抬头 -->
    <div class="print-only print-header">
      <div class="print-title">垫付台账</div>
      <div class="print-meta">会计期间：{{ month }}</div>
    </div>

    <!-- 主表格：收敛至 SoloTable 标准 -->
    <NCard :bordered="false" class="report-card">
      <SoloTable
        :columns="columns"
        :data="filteredItems"
        :loading="loading"
        :summary="summaryRow"
        :row-key="(row: AdvanceRow) => row.payer"
        clickable
        empty-title="本期没有垫付记录"
        empty-description="用 AI 记账时说明「这笔是我先垫的」，或在单据详情中将资金渠道标记为老板/员工垫付。"
        @row-click="openDetail"
      />

      <p class="hint">
        <i class="ri-information-line" />
        本表归集凭证中计入「其他应付款（2241）」并按付款人归属的垫付往来款项。
        <template v-if="other.count > 0">
          其他应付款另有 {{ other.count }} 笔非垫付业务（贷方 {{ formatMoney(other.advance) }} 元、借方
          {{ formatMoney(other.reimbursed) }} 元，如发票待付款、往来款），未计入本表，与总账科目余额表的差额即为这部分。
        </template>
        <template v-else>非垫付业务（发票待付款、往来款等）不计入本表。</template>
        期末未报为负数表示已报销冲销金额超过垫付总额，需核对是否有重复报销。
      </p>
    </NCard>

    <!-- 垫付与报销穿透抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="selectedPerson ? `${selectedPerson.name} · 垫付与报销明细` : '垫付明细'"
      :width="600"
    >
      <div v-if="selectedPerson" class="drawer-content">
        <!-- 人员概览 Banner -->
        <div class="drawer-banner">
          <div class="drawer-amount-row">
            <div>
              <div class="drawer-amount-label">期末未报余额</div>
              <div
                class="drawer-amount-value balance"
                :class="{ negative: selectedPerson.closing < -0.005 }"
              >
                {{ signed(selectedPerson.closing) }}{{ formatMoney(Math.abs(selectedPerson.closing)) }}
              </div>
            </div>
            <NTag
              size="medium"
              :bordered="false"
              :type="selectedPerson.kind === 'boss' ? 'info' : 'default'"
            >
              {{ selectedPerson.kind === 'boss' ? '老板（股东）' : '员工' }} · 共 {{ selectedPerson.count }} 笔单据
            </NTag>
          </div>

          <div class="drawer-breakdown-row">
            <span>期初未报：<strong>{{ formatMoney(selectedPerson.opening) }}</strong></span>
            <span class="breakdown-divider">·</span>
            <span>本期新垫：<strong>{{ formatMoney(selectedPerson.advance) }}</strong></span>
            <span class="breakdown-divider">·</span>
            <span>本期报销：<strong>{{ formatMoney(selectedPerson.reimbursed) }}</strong></span>
          </div>

          <div class="drawer-tip-box">
            <i class="ri-information-line"></i>
            <span v-if="selectedPerson.kind === 'boss'">
              属于公司真实经营支出，由老板个人先行垫付。公户资金充裕时，可随时通过公对私转账归还个人，属于往来款返还不涉及个税。
            </span>
            <span v-else>
              由员工个人因公先行垫付，员工凭发票报销后通过银行打款或现金冲销，结清欠款。
            </span>
          </div>
        </div>

        <!-- 单据列表 -->
        <div class="drawer-list-header">
          <span>包含单据明细</span>
          <span style="font-size: 12px; color: var(--solo-text-muted);">
            可点击查看记账凭证借贷分录
          </span>
        </div>

        <div v-if="!selectedPerson.entries || selectedPerson.entries.length === 0" style="padding: 30px 0;">
          <SoloEmpty description="暂无该人员相关单据明细" />
        </div>

        <div v-else class="drawer-entries-list">
          <div
            v-for="entry in selectedPerson.entries"
            :key="entry.id"
            class="drawer-entry-card"
          >
            <div class="drawer-entry-main">
              <div class="drawer-entry-header-line">
                <NTag
                  size="small"
                  :bordered="false"
                  :type="entry.direction === 'advance' ? 'warning' : 'success'"
                >
                  {{ entry.direction === 'advance' ? '个人垫付' : '报销冲销' }}
                </NTag>
                <span class="drawer-entry-date">{{ entry.date || entry.period }}</span>
                <strong class="drawer-entry-title" :title="entry.summary">
                  {{ entry.summary || '未命名账目' }}
                </strong>
                <NTag v-if="entry.voucherNo" size="tiny" quaternary type="default" style="margin-left: 4px;">
                  记字第 {{ entry.voucherNo }} 号
                </NTag>
              </div>

              <div class="drawer-entry-meta-line">
                <span class="drawer-entry-category">{{ entry.category || '经营支出' }}</span>
                <span v-if="!entry.isCurrentMonth" class="drawer-entry-period-tag">
                  （前期业务结转）
                </span>
              </div>
            </div>

            <div class="drawer-entry-side">
              <div
                class="drawer-entry-amount"
                :class="entry.direction === 'advance' ? 'expense' : 'income'"
              >
                {{ entry.direction === 'advance' ? '+' : '-' }}{{ formatMoney(Math.abs(Number(entry.amount) || 0)) }}
              </div>
              <div class="drawer-entry-actions">
                <NButton
                  size="tiny"
                  secondary
                  type="primary"
                  @click="viewVoucher(entry.id)"
                >
                  查看凭证
                </NButton>
                <NButton
                  v-if="entry.sourceId"
                  size="tiny"
                  quaternary
                  @click="viewSourceEntry(entry.sourceId, entry.id)"
                >
                  账本流水
                </NButton>
              </div>
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <div class="drawer-footer-row">
          <NButton
            v-if="selectedPerson"
            secondary
            type="primary"
            @click="copyPersonSummary(selectedPerson)"
          >
            <template #icon><i class="ri-file-copy-line" /></template>
            复制对账明细
          </NButton>
          <div v-else></div>
          <NButton @click="drawerVisible = false">关闭</NButton>
        </div>
      </template>
    </SoloDrawer>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }

/* 顶部 KPI 卡片 */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.summary-card :deep(.n-card-content) {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.summary-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
}
.summary-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary);
}
.summary-value.income { color: var(--solo-primary, #1747b0); }
.summary-value.expense { color: var(--solo-expense, #cf4b38); }
.summary-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* 过滤栏 */
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px 20px;
  padding: 10px 16px;
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 8px;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 10px;
}
.filter-bar :deep(.n-form-item) {
  margin-bottom: 0;
  width: auto;
  flex: 0 0 auto;
}
.month-stepper {
  display: flex;
  gap: 4px;
}
.switch-item {
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}
.switch-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
}

/* 表格单元格样式 */
.payer-name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.payer-name {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.dir-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.amount-cell {
  font-variant-numeric: tabular-nums;
  text-align: right;
  display: block;
}
.total-cell {
  font-weight: 700;
}
.balance {
  font-weight: 700;
  color: var(--solo-primary, #1747b0);
}
.balance.negative {
  color: var(--solo-warning, #a05c10);
}
.tag-boss {
  background: rgba(23, 71, 176, 0.08);
  color: var(--solo-primary, #1747b0);
}

/* 抽屉样式 */
.drawer-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.drawer-banner {
  background: var(--solo-bg-secondary, #f8fafc);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.drawer-amount-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.drawer-amount-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.drawer-amount-value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}
.drawer-breakdown-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--solo-text-secondary);
  padding: 6px 0;
  border-top: 1px dashed var(--solo-border, #dde3ea);
}
.breakdown-divider {
  color: var(--solo-text-muted);
}
.drawer-tip-box {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--solo-text-muted);
  background: rgba(0, 0, 0, 0.02);
  padding: 8px 10px;
  border-radius: 6px;
}
.drawer-tip-box i {
  margin-top: 2px;
  font-size: 14px;
  color: var(--solo-primary);
}
.drawer-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 14px;
  color: var(--solo-text-primary);
}
.drawer-entries-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.drawer-entry-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 8px;
  background: var(--solo-bg-card, #ffffff);
  transition: all 0.2s ease;
}
.drawer-entry-card:hover {
  border-color: var(--solo-primary-light, #93b4ec);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
}
.drawer-entry-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}
.drawer-entry-header-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.drawer-entry-date {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.drawer-entry-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}
.drawer-entry-meta-line {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.drawer-entry-period-tag {
  color: var(--solo-text-muted);
}
.drawer-entry-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  margin-left: 12px;
}
.drawer-entry-amount {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.drawer-entry-amount.income {
  color: var(--solo-income, #12805c);
}
.drawer-entry-amount.expense {
  color: var(--solo-expense, #cf4b38);
}
.drawer-entry-actions {
  display: flex;
  gap: 6px;
}
.drawer-footer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.hint {
  margin-top: 16px;
  font-size: 12px;
  color: var(--solo-text-muted);
  line-height: 1.6;
}
.hint i {
  margin-right: 4px;
}
</style>
