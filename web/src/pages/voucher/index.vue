<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpin, useMessage, useDialog,
  NInput, NSelect, NDatePicker, NDataTable, type DataTableColumns,
} from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import {
  getPeriodCloseChecks, batchPostEntries,
  getPendingAccruals, createAccrual, reverseAccrual, settleAccrual,
  type AccrualRecord,
} from '@/api/accounting'
import type { Entry } from '@/api/types'
import { getCategories } from '@/api/category'
import { formatMoney } from '@/utils/format'
import { sortByDayDesc } from '@/utils/entryGrouping'
import { SoloDrawer, SoloEmpty, SoloFieldHelp } from '@/components/ui'
import { RESCUE_RULES } from '@/utils/rescueRules'
import { usePageRefresh } from '@/composables/usePageRefresh'
import { useTabGuard } from '@/composables/useTabGuard'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const dialog = useDialog()
const entryStore = useEntryStore()

const loading = ref(true)
const batchPosting = ref(false)
const targetMonth = computed(() => typeof route.query.month === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(route.query.month) ? route.query.month : '')
// 月份筛选存 URL 查询参数（可分享、可回退）。原生 type=month 在 Firefox 没有选择面板、
// Chrome 上面板也难点中，统一用 naive-ui 月份选择器；清空即查看全部月份。
const selectedMonth = computed<string | null>({
  get: () => targetMonth.value || null,
  set: (val) => {
    const query: Record<string, string> = {}
    if (val) query.month = val
    if (typeof route.query.from === 'string' && route.query.from) query.from = route.query.from
    void router.replace({ path: '/vouchers', query })
  },
})
const issues = ref<Array<{ entryId: string; title: string; date: string; reasons: string[] }>>([])
// 凭证是法定口径：按**记账期间**筛选，不能按业务日期。
// 否则补记凭证（业务日期在上个月、记账期间在本月）会在本月凭证页里消失。
const scopedEntries = computed(() => entryStore.activeEntries.filter(e => {
  if (!targetMonth.value) return true
  const period = String(e.period || '').slice(0, 7)
  if (period) return period === targetMonth.value
  return String(e.occurDate || '').startsWith(targetMonth.value)
}))
const reasonLabels: Record<string, string> = { pending_post: '待过账', pending_review: '待复核', missing_source: '缺少有效来源', missing_rule: '缺少记账规则' }
function statusLabel(status: string | undefined) {
  return ({ draft: '待过账', pending_review: '待复核', posted: '已过账', reversed: '已冲销' } as Record<string, string>)[status || ''] || '状态待核实'
}
function statusTagType(status: string | undefined) {
  return ({ draft: 'warning', pending_review: 'info', posted: 'success', reversed: 'default' } as Record<string, 'warning' | 'info' | 'success' | 'default'>)[status || ''] || 'warning'
}

/** 已过账 / 已冲销（且有凭证号）的行不可再勾选过账 */
function isPosted(entry: Pick<Entry, 'voucherStatus' | 'voucherRef'>) {
  return ['posted', 'reversed'].includes(entry.voucherStatus || '') && !!entry.voucherRef
}

// 后端返回顺序不保证按日期，倒序排一次作为表格初始顺序
const sortedScoped = computed(() => sortByDayDesc(scopedEntries.value))

// ── 列表筛选：状态 + 关键词（摘要 / 分类 / 凭证号） ──────────────────
const statusFilter = ref('')
const keyword = ref('')
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '待过账', value: 'draft' },
  { label: '待复核', value: 'pending_review' },
  { label: '已过账', value: 'posted' },
  { label: '已冲销', value: 'reversed' },
]

const filteredRows = computed(() => {
  let rows = sortedScoped.value
  if (statusFilter.value) {
    rows = rows.filter(e => statusFilter.value === 'posted' || statusFilter.value === 'reversed'
      ? isPosted(e) && e.voucherStatus === statusFilter.value
      : e.voucherStatus === statusFilter.value)
  }
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    rows = rows.filter(e => [e.title, e.category, e.voucherRef]
      .some(v => String(v || '').toLowerCase().includes(kw)))
  }
  return rows
})

const pendingRows = computed(() => filteredRows.value.filter(e => !isPosted(e)))

// ── 勾选与批量过账：只有未过账行可勾选 ──────────────────────────────
const checkedRowKeys = ref<Array<string | number>>([])

watch(scopedEntries, () => {
  const validIds = new Set(scopedEntries.value.map(e => e.id))
  checkedRowKeys.value = checkedRowKeys.value.filter(id => validIds.has(String(id)))
})

function handleRowClick(row: Entry) {
  if (isPosted(row)) {
    void router.push(`/vouchers/${row.id}`)
  } else {
    openEntry(row.id)
  }
}

const columns = computed<DataTableColumns<Entry>>(() => [
  { type: 'selection', disabled: (row) => isPosted(row) },
  {
    title: '日期',
    key: 'occurDate',
    width: 104,
    defaultSortOrder: 'descend',
    sorter: (a, b) => String(a.occurDate || '').localeCompare(String(b.occurDate || '')),
    render: (row) => h('span', { class: 'cell-date' }, row.occurDate || '—'),
  },
  {
    title: '摘要',
    key: 'title',
    minWidth: 170,
    ellipsis: { tooltip: true },
    render: (row) => {
      const nodes = [h('div', { class: 'cell-title' }, row.title)]
      if (row.isBackfill) {
        nodes.push(h('span', { class: 'backfill-tag' }, `补记 · 原 ${row.backfillFromPeriod || ''}`))
      }
      return h('div', { class: 'cell-title-wrap' }, nodes)
    },
  },
  {
    title: '分类',
    key: 'category',
    width: 110,
    ellipsis: { tooltip: true },
    render: (row) => h('span', { class: 'cell-category' }, row.category || '—'),
  },
  {
    title: '金额',
    key: 'amount',
    width: 112,
    align: 'right',
    sorter: (a, b) => Number(a.amount) - Number(b.amount),
    render: (row) => h('span', { class: 'cell-amount' }, formatMoney(row.amount)),
  },
  {
    title: '状态',
    key: 'voucherStatus',
    width: 84,
    render: (row) => h(NTag, {
      size: 'small', bordered: false, type: statusTagType(row.voucherStatus),
    }, { default: () => statusLabel(row.voucherStatus) }),
  },
  {
    title: '凭证号',
    key: 'voucherRef',
    width: 148,
    render: (row) => h('span', { class: 'cell-ref' }, row.voucherRef || '—'),
  },
])

const rowProps = (row: Entry) => ({
  style: 'cursor: pointer;',
  onClick: () => handleRowClick(row),
})

const rowClassName = (row: Entry) => (row.voucherStatus === 'reversed' ? 'is-reversed' : '')

async function loadEntries() {
  loading.value = true
  issues.value = []
  try {
    await entryStore.fetchEntries()
    if (targetMonth.value) {
      const result = await getPeriodCloseChecks(targetMonth.value)
      const checks = result.checks as Array<{ code: string; records?: typeof issues.value }> | undefined
      const records = new Map<string, typeof issues.value[number]>()
      for (const check of checks || []) {
        for (const record of check.records || []) {
          const previous = records.get(record.entryId)
          records.set(record.entryId, { ...record, reasons: [...new Set([...(previous?.reasons || []), ...record.reasons])] })
        }
      }
      issues.value = [...records.values()]
    }
  } catch {
    message.error('读取凭证或结账检查失败，请刷新重试')
  } finally {
    loading.value = false
  }
}

function openEntry(entryId: string) {
  if (route.query.from) {
    router.push({ path: `/ledger/${entryId}`, query: { from: String(route.query.from) } })
  } else {
    router.push(`/ledger/${entryId}`)
  }
}

function promptBatchPost(mode: 'selected' | 'all') {
  const ids = mode === 'selected' ? checkedRowKeys.value.map(String) : pendingRows.value.map(e => e.id)
  if (!ids.length) return

  const targetCount = ids.length
  dialog.info({
    title: `确认批量过账 (${targetCount} 笔)`,
    content: `将批量过账选中的 ${targetCount} 笔凭证并生成正式凭证号。过账后凭证将纳入正式财务报表核算；如遇需他人复核或借贷不平衡单据，系统将自动跳过并保留为草稿。是否继续？`,
    positiveText: '确认批量过账',
    negativeText: '取消',
    onPositiveClick: async () => {
      await executeBatchPost(ids)
    },
  })
}

async function executeBatchPost(ids: string[]) {
  if (batchPosting.value) return
  batchPosting.value = true
  try {
    const res = await batchPostEntries({ entryIds: ids })
    if (res.postedCount > 0 && res.failedCount === 0) {
      message.success(`🎉 已成功批量过账 ${res.postedCount} 笔凭证`)
    } else if (res.postedCount > 0 && res.failedCount > 0) {
      message.warning(`已过账 ${res.postedCount} 笔凭证；${res.failedCount} 笔因需复核或异常未过账，已保留为草稿`)
      // 第二通道：批量失败不进 catch，但每条都带 code —— 同样能给出「去处理」的出路
      const box = dialog.warning({
        title: '部分凭证未过账说明',
        content: () => {
          return h('div', { style: 'max-height: 240px; overflow-y: auto;' }, [
            h('p', { style: 'margin-bottom: 8px;' }, `以下 ${res.failedCount} 笔凭证未过账，请核对原因：`),
            h('ul', { style: 'padding-left: 20px; font-size: 13px;' }, res.failedEntries.map(f => {
              const rule = RESCUE_RULES[f.code]
              const target = rule ? rule.actions({})[0] : null
              return h('li', { style: 'margin-bottom: 4px;' }, [
                h('span', `${f.title}：${f.reason}`),
                target?.path
                  ? h('button', {
                      style: {
                        marginLeft: '8px', padding: '2px 8px',
                        border: '1px solid var(--solo-border)',
                        borderRadius: '6px', background: 'var(--solo-bg-card)',
                        color: 'var(--solo-primary)', fontSize: '12px', cursor: 'pointer',
                      },
                      onClick: () => {
                        box.destroy()
                        router.push(target.path as string)
                      },
                    }, target.label)
                  : null,
              ])
            })),
          ])
        },
        positiveText: '知道了',
      })
    } else if (res.failedCount > 0) {
      message.error(`批量过账未完成，${res.failedCount} 笔凭证未通过核验`)
    }
    checkedRowKeys.value = []
    await loadEntries()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '批量过账请求失败，请稍后重试')
  } finally {
    batchPosting.value = false
  }
}

onMounted(loadEntries)
watch(targetMonth, loadEntries)
// 从别的标签切回来时静默重拉：另一张标签里可能刚过账/冲销过凭证
usePageRefresh(loadEntries)

// ── 暂估入账（月末票未到） ──────────────────────────────────
const pendingAccruals = ref<AccrualRecord[]>([])
const pendingAccrualTotal = ref('0.00')
const categoryOptions = ref<{ label: string; value: string }[]>([])
const accrualSaving = ref(false)
const accrualForm = reactive({
  show: false,
  vendor: '',
  amount: '',
  category: '',
  businessDate: '',
  expectedInvoiceDate: '',
  basis: 'contract' as 'contract' | 'receipt' | 'estimate',
  notes: '',
})
const settleForm = reactive({ show: false, target: null as AccrualRecord | null, amount: '', invoiceNumber: '' })

const basisOptions = [
  { label: '按合同', value: 'contract' },
  { label: '按入库单/验收单', value: 'receipt' },
  { label: '按估算', value: 'estimate' },
]

/**
 * 暂估入账 / 票到冲抵都是抽屉表单，填一半被关掉就是白敲。
 * 注意 basis 有默认值，不能算进 dirty，否则一打开就提示。
 */
function hasUnsavedVoucherDraft(): boolean {
  if (accrualForm.show) {
    return Boolean(
      accrualForm.vendor.trim() ||
        accrualForm.amount.trim() ||
        accrualForm.category ||
        accrualForm.businessDate ||
        accrualForm.expectedInvoiceDate ||
        accrualForm.notes.trim(),
    )
  }
  if (settleForm.show) {
    return Boolean(settleForm.amount.trim() || settleForm.invoiceNumber.trim())
  }
  return false
}

useTabGuard(hasUnsavedVoucherDraft)

async function loadPendingAccruals() {
  try {
    const payload = await getPendingAccruals()
    pendingAccruals.value = payload.records || []
    pendingAccrualTotal.value = payload.amount || '0.00'
  } catch {
    pendingAccruals.value = []
  }
}

async function loadAccrualCategories() {
  if (categoryOptions.value.length) return
  try {
    const items = await getCategories()
    categoryOptions.value = (items || [])
      .filter((item) => item.direction === 'expense' && item.is_active)
      .map((item) => ({ label: item.name, value: item.name }))
  } catch {
    categoryOptions.value = []
  }
}

function openAccrualForm() {
  void loadAccrualCategories()
  const today = new Date()
  const monthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0)
  const nextMonthEnd = new Date(today.getFullYear(), today.getMonth() + 2, 0)
  accrualForm.vendor = ''
  accrualForm.amount = ''
  accrualForm.category = ''
  accrualForm.businessDate = monthEnd.toISOString().slice(0, 10)
  accrualForm.expectedInvoiceDate = nextMonthEnd.toISOString().slice(0, 10)
  accrualForm.basis = 'contract'
  accrualForm.notes = ''
  accrualForm.show = true
  void loadAccrualCategories()
}

async function submitAccrual() {
  if (!accrualForm.vendor.trim()) return message.warning('请填写供应商或对方名称')
  const amount = Number(accrualForm.amount)
  if (!(amount > 0)) return message.warning('请填写大于零的暂估金额')
  if (!accrualForm.category) return message.warning('请选择费用/存货分类')
  if (!accrualForm.expectedInvoiceDate) return message.warning('请填写预计取票日期')
  accrualSaving.value = true
  try {
    await createAccrual({
      amount,
      vendor: accrualForm.vendor.trim(),
      category: accrualForm.category,
      businessDate: accrualForm.businessDate || undefined,
      expectedInvoiceDate: accrualForm.expectedInvoiceDate,
      basis: accrualForm.basis,
      notes: accrualForm.notes,
    })
    message.success('暂估已入账，次月初记得红冲')
    accrualForm.show = false
    await Promise.all([loadEntries(), loadPendingAccruals()])
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || '暂估入账失败')
  } finally {
    accrualSaving.value = false
  }
}

function handleReverseAccrual(row: AccrualRecord) {
  dialog.warning({
    title: '红冲这笔暂估？',
    content: `将按次月 1 日生成红字冲销凭证：${row.summary}，金额 ${formatMoney(Number(row.amount))}。`,
    positiveText: '红冲',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await reverseAccrual(row.entryId, '暂估次月红冲')
        message.success('已红冲')
        await Promise.all([loadEntries(), loadPendingAccruals()])
      } catch {
        message.error('红冲失败')
      }
    },
  })
}

function openSettleForm(row: AccrualRecord) {
  settleForm.target = row
  settleForm.amount = row.amount
  settleForm.invoiceNumber = ''
  settleForm.show = true
}

async function submitSettle() {
  if (!settleForm.target) return
  const amount = Number(settleForm.amount)
  if (!(amount > 0)) return message.warning('请填写发票金额')
  try {
    await settleAccrual(settleForm.target.entryId, {
      amount,
      occurDate: new Date().toISOString().slice(0, 10),
      invoiceNumber: settleForm.invoiceNumber || undefined,
    })
    message.success('已冲回暂估并按发票入账')
    settleForm.show = false
    await Promise.all([loadEntries(), loadPendingAccruals()])
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '票到冲抵失败')
  }
}

onMounted(() => {
  void loadPendingAccruals()
})
</script>

<template>
  <div class="voucher-page">
    <div class="page-header">
      <div style="display: flex; align-items: center; gap: 8px;">
        <NButton v-if="route.query.from" text size="small" @click="router.push(String(route.query.from))">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回结账向导
        </NButton>
        <h2>记账凭证</h2>
      </div>
      <div class="header-actions">
        <NButton size="small" secondary @click="router.push({ path: '/vouchers/print', query: { month: targetMonth } })">
          <template #icon><i class="ri-printer-line" /></template>
          打印本月凭证
        </NButton>
        <NButton size="small" secondary @click="openAccrualForm">
          <template #icon><i class="ri-file-add-line" /></template>
          暂估入账
        </NButton>
        <label class="month-field">
          月份
          <NDatePicker
            v-model:formatted-value="selectedMonth"
            type="month"
            value-format="yyyy-MM"
            format="yyyy-MM"
            size="small"
            clearable
            placeholder="全部月份"
            style="width: 132px;"
          />
        </label>
      </div>
    </div>

    <NSpin :show="loading">
      <p class="page-hint">按记账期间筛选，支持排序、搜索与批量过账。点击行查看来源和分录；已对账不代表已过账。</p>

      <div v-if="pendingAccruals.length" class="section mb-4 accrual-section">
        <div class="section-label">
          未冲回暂估（{{ pendingAccruals.length }} 笔 · {{ formatMoney(Number(pendingAccrualTotal)) }}）
        </div>
        <NCard
          v-for="row in pendingAccruals"
          :key="row.entryId"
          :bordered="false"
          class="entry-card accrual-card"
        >
          <div class="accrual-row">
            <div class="accrual-main">
              <span class="cell-title">{{ row.summary }}</span>
              <span class="entry-sub">
                {{ row.businessDate }} · 预计取票 {{ row.expectedInvoiceDate || '—' }}
                <NTag v-if="row.overdueDays > 0" size="tiny" type="error" :bordered="false">
                  逾期 {{ row.overdueDays }} 天
                </NTag>
              </span>
            </div>
            <div class="accrual-actions">
              <span class="cell-amount">{{ formatMoney(Number(row.amount)) }}</span>
              <NButton size="tiny" secondary @click="openSettleForm(row)">票到冲抵</NButton>
              <NButton size="tiny" quaternary @click="handleReverseAccrual(row)">红冲</NButton>
            </div>
          </div>
        </NCard>
      </div>
      <div v-if="issues.length" class="section mb-4">
        <div class="section-label">{{ targetMonth }} 结账待处理记录 ({{ issues.length }})</div>
        <NCard v-for="issue in issues" :key="issue.entryId" :bordered="false" class="entry-card" @click="openEntry(issue.entryId)">
          <div class="entry-info"><span class="cell-title">{{ issue.title }}</span><NButton size="tiny" @click.stop="openEntry(issue.entryId)">查看与处理</NButton></div>
          <div class="entry-meta"><span>{{ issue.date }}</span><NTag v-for="reason in issue.reasons" :key="reason" size="tiny" type="warning">{{ reasonLabels[reason] || reason }}</NTag></div>
          <p v-if="issue.reasons.includes('missing_source')">请核对原始业务记录及关联；补齐来源后重新检查。</p>
        </NCard>
      </div>

      <!-- 凭证列表：状态筛选 + 关键词搜索 + 排序 + 批量过账 -->
      <div class="section">
        <div class="table-toolbar">
          <NSelect v-model:value="statusFilter" :options="statusOptions" size="small" style="width: 128px;" />
          <NInput v-model:value="keyword" size="small" clearable placeholder="搜摘要 / 分类 / 凭证号" style="width: 220px;">
            <template #prefix><i class="ri-search-line toolbar-search-icon" /></template>
          </NInput>
          <div class="batch-actions">
            <span v-if="checkedRowKeys.length > 0" class="selected-badge">已勾选 {{ checkedRowKeys.length }} 笔</span>
            <NButton
              v-if="checkedRowKeys.length > 0"
              size="small"
              type="primary"
              :loading="batchPosting"
              @click="promptBatchPost('selected')"
            >
              批量过账所选 ({{ checkedRowKeys.length }})
            </NButton>
            <NButton
              v-else
              size="small"
              type="primary"
              ghost
              :loading="batchPosting"
              :disabled="pendingRows.length === 0"
              @click="promptBatchPost('all')"
            >
              一键过账全部 ({{ pendingRows.length }})
            </NButton>
          </div>
        </div>
        <NDataTable
          v-model:checked-row-keys="checkedRowKeys"
          size="small"
          :bordered="false"
          :columns="columns"
          :data="filteredRows"
          :row-key="(row: Entry) => row.id"
          :row-props="rowProps"
          :row-class-name="rowClassName"
          :pagination="{ pageSize: 20 }"
          class="voucher-table"
        >
          <template #empty>
            <span class="table-empty">没有符合条件的凭证</span>
          </template>
        </NDataTable>
      </div>

      <div v-if="scopedEntries.length === 0 && !loading" class="empty-section">
        <SoloEmpty
          title="还没有记账凭证"
          description="一句话、一张发票或一条流水就能生成凭证，不用手填借贷。"
          icon="ri-file-list-3-line"
        >
          <template #action>
            <div class="empty-actions">
              <NButton type="primary" size="small" @click="router.push('/copilot')">
                去 AI 记账
              </NButton>
              <NButton size="small" @click="router.push('/record/invoice-import')">
                导入发票
              </NButton>
            </div>
          </template>
        </SoloEmpty>
      </div>
    </NSpin>

    <!-- 两张单据表单都不带遮罩：填到一半要去别的标签查数，遮罩会把标签栏一起盖住。
         切走不丢草稿（标签是缓存的），关标签才丢 —— 那一步由 hasUnsavedVoucherDraft 兜。 -->
    <SoloDrawer
      v-model:show="accrualForm.show"
      title="暂估入账（票未到）"
      :width="520"
      :mask="false"
    >
      <div class="accrual-form">
        <p class="accrual-tip">
          月末货已到、票未到时按合同或入库单暂估。次月初会自动提示红冲，
          票到后在「票到冲抵」里按发票金额重新入账。暂估不含进项税额。
        </p>
        <div class="form-row">
          <label>供应商 / 对方</label>
          <NInput v-model:value="accrualForm.vendor" placeholder="例如：某某供应商" />
        </div>
        <div class="form-row">
          <label>金额（不含税）</label>
          <NInput v-model:value="accrualForm.amount" placeholder="0.00" />
        </div>
        <div class="form-row">
          <label>费用 / 存货分类</label>
          <NSelect
            v-model:value="accrualForm.category"
            :options="categoryOptions"
            filterable
            placeholder="选择分类"
          />
        </div>
        <div class="form-row">
          <label>业务日期</label>
          <NDatePicker
            v-model:formatted-value="accrualForm.businessDate"
            type="date"
            value-format="yyyy-MM-dd"
            style="width: 100%;"
          />
        </div>
        <div class="form-row">
          <label>预计取票日期</label>
          <NDatePicker
            v-model:formatted-value="accrualForm.expectedInvoiceDate"
            type="date"
            value-format="yyyy-MM-dd"
            style="width: 100%;"
          />
        </div>
        <div class="form-row">
          <label>
            暂估依据
            <SoloFieldHelp text="货到了发票没到时，填合同或订单作为估价来源" />
          </label>
          <NSelect v-model:value="accrualForm.basis" :options="basisOptions" />
        </div>
        <div class="form-row">
          <label>备注</label>
          <NInput v-model:value="accrualForm.notes" type="textarea" :autosize="{ minRows: 2 }" />
        </div>
      </div>
      <template #footer>
        <div class="modal-actions">
          <NButton size="small" quaternary @click="accrualForm.show = false">取消</NButton>
          <NButton size="small" type="primary" :loading="accrualSaving" @click="submitAccrual">
            确认暂估
          </NButton>
        </div>
      </template>
    </SoloDrawer>

    <SoloDrawer
      v-model:show="settleForm.show"
      title="票到冲抵"
      :width="460"
      :mask="false"
    >
      <div class="accrual-form">
        <p class="accrual-tip">
          将红冲这笔暂估（{{ settleForm.target?.summary }}），再按发票金额正式入账。
          两步在同一事务内完成，金额有差异时差额进当期成本。
        </p>
        <div class="form-row">
          <label>发票金额</label>
          <NInput v-model:value="settleForm.amount" placeholder="0.00" />
        </div>
        <div class="form-row">
          <label>发票号</label>
          <NInput v-model:value="settleForm.invoiceNumber" placeholder="选填，用于去重" />
        </div>
      </div>
      <template #footer>
        <div class="modal-actions">
          <NButton size="small" quaternary @click="settleForm.show = false">取消</NButton>
          <NButton size="small" type="primary" @click="submitSettle">确认冲抵</NButton>
        </div>
      </template>
    </SoloDrawer>
  </div>
</template>

<style scoped>
/* 表格版面比卡片流宽：容纳 7 列仍保持呼吸感 */
.voucher-page {
  max-width: 960px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }
.page-hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--solo-text-muted);
}
.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.entry-card {
  border-radius: var(--solo-radius-lg);
  cursor: pointer;
}
.entry-card:hover {
  box-shadow: var(--solo-shadow);
}
.entry-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.entry-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.empty-section {
  margin-top: 48px;
}
.empty-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── 表格工具行：状态筛选 + 搜索 + 批量动作 ── */
.table-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.toolbar-search-icon {
  color: var(--solo-text-muted);
  font-size: 14px;
}
.batch-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.selected-badge {
  font-size: 12px;
  color: var(--solo-primary);
  background: var(--solo-primary-50, #f0f7ff);
  padding: 2px 8px;
  border-radius: 8px;
  font-weight: 500;
}

/* ── 表格单元格 ── */
.voucher-table :deep(.cell-date) {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  font-variant-numeric: tabular-nums;
}
.voucher-table :deep(.cell-title-wrap) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.voucher-table :deep(.cell-title) {
  font-size: 14px;
  font-weight: 500;
  color: var(--solo-text-primary);
}
.voucher-table :deep(.backfill-tag) {
  align-self: flex-start;
  font-size: 11px;
  color: var(--solo-primary);
  background: var(--solo-primary-50, #f0f7ff);
  padding: 1px 6px;
  border-radius: 6px;
  width: fit-content;
}
.voucher-table :deep(.cell-category) {
  font-size: 12.5px;
  color: var(--solo-text-muted);
}
.voucher-table :deep(.cell-amount) {
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}
.voucher-table :deep(.cell-ref) {
  font-size: 11.5px;
  color: var(--solo-primary);
  font-family: 'Inter', monospace;
}
.voucher-table :deep(.is-reversed) {
  opacity: 0.55;
}
.voucher-table :deep(.is-reversed) .cell-amount {
  text-decoration: line-through;
}
.voucher-table :deep(.table-empty) {
  font-size: 13px;
  color: var(--solo-text-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.month-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
}

.accrual-card {
  border-left: 3px solid var(--solo-warning, #a05c10);
}

.accrual-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.accrual-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.accrual-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.accrual-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.accrual-tip {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--solo-text-muted);
}

.form-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
