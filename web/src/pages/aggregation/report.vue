<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
// 归集报表。
//
// 本页三条不可让步的规则：
// 1. start/end **必填**（后端 routers/aggregation.py:233-234 的 Query 无 default，漏传 422）。
//    所以未选月份时查询按钮直接 disabled，**不发请求**，而不是发一个注定 422 的请求。
// 2. 口径只取 posted 凭证（aggregation_service.py 的 JournalEntry.status == 'posted'），
//    与待归集清单（含草稿）天然不同。顶部必须常驻说明，否则用户会当成 bug 报上来（设计 §9 #8）。
// 3. 金额一律直用——已在 api/aggregation.ts 归一化为 number。组件层禁止 Number(x.amount) 兜底，
//    这条纪律本身就是「¥0120.00」类错数的防线。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDatePicker,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NSpin,
  NStatistic,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import dayjs from 'dayjs'
import * as XLSX from 'xlsx'
import DimensionPicker from './_components/DimensionPicker.vue'
import { getAggregationReport } from '@/api/aggregation'
import type { AggregationReportResponse } from '@/api/aggregation'
import type { AggregationBasis, AggregationReportItem } from '@/api/types'
import { useAggregationStore } from '@/stores/aggregation'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { OCCUR_DATE_WARNING, REPORT_SCOPE_NOTE, errorText } from '@/utils/aggregation'
import { formatMoney } from '@/utils/format'
import { saveWorkbook } from '@/utils/export'
import { SoloEmpty, SoloButton } from '@/components/ui'

const store = useAggregationStore()
const message = useMessage()
const router = useRouter()
const route = useRoute()
const viewStore = useWorkspaceViewStore()

/**
 * 老板看到的是经营问题（「这个项目到底赚了多少」），
 * 会计看到的是核算问题（「归集了三态行没有」）。同一份数据，两套说法。
 */
const isBoss = computed(() => viewStore.isBoss)
const pageTitle = computed(() => (isBoss.value ? '项目利润' : '归集报表'))
const pageSubtitle = computed(() => (isBoss.value
  ? '每个项目分到的收入减去成本，就是它到目前为止的净贡献。'
  : '按维度汇总本期已过账凭证的归集结果；三态行数必须全部可见。'))

/** 后端 DEFAULT_SCOPE_PREFIXES。筛选留空即用它，UI 必须显示默认值 */
const DEFAULT_SCOPE_PREFIXES = '5,6'

const loading = ref(false)
/** 是否已经跑过一次查询。用于区分「还没查」与「查了但空」两种空态文案 */
const queried = ref(false)

/** 月份区间。value-format='yyyy-MM' 让 NDatePicker 直接给 YYYY-MM 字符串，无需 dayjs 转换 */
const monthRange = ref<[string, string] | null>(null)

/**
 * NDatePicker 的 monthrange 在 value-format='yyyy-MM' 时**运行期**交出 YYYY-MM 字符串数组，
 * 但它的 Value 类型声明里只认 [number, number]。用 computed 做一次边界收敛，
 * 避免在模板里写类型断言（与 pending.vue 的 applyMonthRange 同一手法）。
 */
const applyMonthRange = computed<any>(() => monthRange.value)

function onMonthRangeUpdate(value: any): void {
  const pair = value as [string | null, string | null] | null
  monthRange.value = pair?.[0] && pair?.[1] ? [pair[0], pair[1]] : null
}
const basis = ref<AggregationBasis>('period')
const scopePrefixes = ref('')

const report = ref<AggregationReportResponse | null>(null)

const dimensionId = computed(() => store.currentDimensionId)
const hasDimension = computed(() => Boolean(dimensionId.value))

/** 月份是必填项：没选齐就不让查，也不发请求 */
const canQuery = computed(() => hasDimension.value && Boolean(monthRange.value?.[0]) && Boolean(monthRange.value?.[1]))

const showOccurDateWarning = computed(() => basis.value === 'occurDate')

const effectiveScopePrefixes = computed(() => scopePrefixes.value.trim() || DEFAULT_SCOPE_PREFIXES)

const items = computed<AggregationReportItem[]>(() => report.value?.items ?? [])

const totals = computed(() => report.value?.totals ?? null)

/** 完全没有 posted 凭证行——与「查了但该维度没有任何归集」区分开 */
const isEmptyResult = computed(() => queried.value && (totals.value?.lineCount ?? 0) === 0)

// ===== 排序控制 =====
type SortField = 'allocatedAmount' | 'allocatedLineCount' | null
type SortOrder = 'asc' | 'desc'
const sortField = ref<SortField>(null)
const sortOrder = ref<SortOrder>('desc')

function toggleSort(field: 'allocatedAmount' | 'allocatedLineCount'): void {
  if (sortField.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = field
    sortOrder.value = 'desc'
  }
}

const displayItems = computed<AggregationReportItem[]>(() => {
  if (!sortField.value) return items.value
  const field = sortField.value
  const order = sortOrder.value
  return [...items.value].sort((a, b) => {
    const valA = a[field] ?? 0
    const valB = b[field] ?? 0
    return order === 'asc' ? valA - valB : valB - valA
  })
})

async function load(): Promise<void> {
  const range = monthRange.value
  if (!dimensionId.value || !range?.[0] || !range?.[1]) return
  loading.value = true
  try {
    const data = await getAggregationReport({
      dimensionId: dimensionId.value,
      start: range[0],
      end: range[1],
      basis: basis.value,
      scopePrefixes: scopePrefixes.value.trim() || undefined,
    })
    report.value = data
    queried.value = true
  } catch (err) {
    report.value = null
    message.error(errorText(err))
  } finally {
    loading.value = false
  }
}

function query(): void {
  void load()
}

/** 快捷设置月份区间 */
function setShortcut(type: 'current' | 'last' | 'quarter' | 'year'): void {
  const now = dayjs()
  let range: [string, string]
  if (type === 'current') {
    const m = now.format('YYYY-MM')
    range = [m, m]
  } else if (type === 'last') {
    const m = now.subtract(1, 'month').format('YYYY-MM')
    range = [m, m]
  } else if (type === 'quarter') {
    range = [now.subtract(2, 'month').format('YYYY-MM'), now.format('YYYY-MM')]
  } else {
    range = [`${now.format('YYYY')}-01`, now.format('YYYY-MM')]
  }
  monthRange.value = range
  if (canQuery.value) {
    void load()
  }
}

/** 下钻到待归集清单 */
function goToPending(): void {
  if (!dimensionId.value) return
  const range = monthRange.value
  router.push({
    path: '/aggregation/pending',
    query: {
      dimensionId: dimensionId.value,
      start: range?.[0],
      end: range?.[1],
      basis: basis.value,
    },
  })
}

/** 导出 Excel */
function exportExcel(): void {
  if (!items.value.length || !report.value) return
  const dimName = report.value?.dimensionName || store.currentDimension?.name || '归集'
  const [s, e] = monthRange.value || ['', '']

  const header = ['编码', '名称', '状态', '已归集金额', '归集行数', '占行金额']
  const rows = displayItems.value.map((item) => [
    item.code || '-',
    item.name || '-',
    memberStatusLabel(item.status),
    item.allocatedAmount,
    item.allocatedLineCount,
    shareText(item),
  ])

  rows.push([
    '合　计',
    '-',
    '-',
    totals.value?.allocatedAmount ?? 0,
    items.value.reduce((sum, item) => sum + item.allocatedLineCount, 0),
    '—',
  ])

  const ws = XLSX.utils.aoa_to_sheet([header, ...rows])
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '归集报表')
  saveWorkbook(wb, `归集报表_${dimName}_${s}_${e}.xlsx`)
  message.success('已导出 Excel')
}

/** 没建维度时给一条明确出路，而不是让用户对着一张空表猜下一步 */
async function goCreateDimension(): Promise<void> {
  // 维度管理是会计视图的页面：老板点进来时先切视图，再跳过去
  if (isBoss.value) viewStore.setMode('accounting')
  await router.push('/aggregation/dimensions')
}

// ===== 展示辅助 =====

/** 成员状态标签。取自 GET /meta（store.memberStatusOptions），未收录时回落原始值 */
function memberStatusLabel(status: string): string {
  if (!status) return '—'
  const option = store.memberStatusOptions.find((item) => item.value === status)
  return option?.label ?? status
}

function memberStatusTagType(status: string): 'success' | 'warning' | 'info' | 'default' {
  if (status === 'active') return 'success'
  if (status === 'completed') return 'info'
  if (status === 'draft') return 'warning'
  return 'default'
}

/**
 * 该成员的已归集金额占本期行金额合计的百分比。
 *
 * ⚠️ 除零保护是硬要求：totals.lineAmount 是**带符号**的合计（贷行为负），
 * 完全可能为 0 甚至为负，此时不能算出 Infinity / -100% 这种误导性数字，一律显示 '—'。
 * 另外 AggregationReportItem **没有** lineAmount 字段（后端 buckets 只给
 * allocatedAmount / allocatedLineCount），所以分母只能用本期 totals.lineAmount。
 */
function sharePercent(item: AggregationReportItem): number | null {
  const base = totals.value?.lineAmount ?? 0
  if (!base) return null
  return Math.round((item.allocatedAmount / base) * 1000) / 10
}

function shareText(item: AggregationReportItem): string {
  const percent = sharePercent(item)
  return percent === null ? '—' : `${percent}%`
}

onMounted(async () => {
  await store.bootstrap()
  // 支持从其他页面或路由带参直达
  const q = route?.query ?? {}
  if (q.dimensionId) {
    store.setCurrentDimension(String(q.dimensionId))
  }
  if (q.start && q.end) {
    monthRange.value = [String(q.start), String(q.end)]
    await load()
  }
  // 首屏未选月份时不自动查询：start/end 必填，未选月份时发请求必定 422
})

watch(dimensionId, () => {
  // 换维度后旧的报表数据不再对应，清空避免误读
  report.value = null
  queried.value = false
})
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-title-section">
        <h2>{{ pageTitle }}</h2>
        <p class="report-subtitle">{{ pageSubtitle }}</p>
      </div>
      <div class="report-header-right">
        <DimensionPicker />
        <SoloButton
          variant="secondary"
          size="small"
          :disabled="!items.length"
          @click="exportExcel"
        >
          <template #icon>
            <i class="ri-download-2-line" />
          </template>
          导出 Excel
        </SoloButton>
      </div>
    </div>

    <!-- 口径说明：老板视图收进折叠区（不必先读术语），会计视图常驻 -->
    <NAlert v-if="!isBoss" type="info" :bordered="false" class="mb-4">{{ REPORT_SCOPE_NOTE }}</NAlert>
    <NCollapse v-else class="mb-4">
      <NCollapseItem name="scope" title="这些数字是怎么算的？">
        <p class="scope-note">{{ REPORT_SCOPE_NOTE }}</p>
      </NCollapseItem>
    </NCollapse>

    <NCard v-if="!hasDimension" :bordered="false" class="mb-4">
      <SoloEmpty
        title="还没有可看的项目"
        description="先建立归集维度（例如「研发项目」），把账目按项目分好，这里就会显示每个项目的净贡献。"
        icon="ri-node-tree"
      >
        <template #action>
          <NButton type="primary" @click="goCreateDimension">去建立项目维度</NButton>
        </template>
      </SoloEmpty>
    </NCard>

    <NCard :bordered="false" title="筛选" class="mb-4">
      <NSpace align="center" wrap :size="12">
        <NDatePicker
          :value="applyMonthRange"
          type="monthrange"
          value-format="yyyy-MM"
          :clearable="true"
          style="width: 240px"
          @update:value="onMonthRangeUpdate"
        />
        <!-- 快捷时间胶囊 -->
        <NSpace :size="6" align="center">
          <NButton size="tiny" quaternary @click="setShortcut('current')">本月</NButton>
          <NButton size="tiny" quaternary @click="setShortcut('last')">上月</NButton>
          <NButton size="tiny" quaternary @click="setShortcut('quarter')">近3月</NButton>
          <NButton size="tiny" quaternary @click="setShortcut('year')">本年</NButton>
        </NSpace>
        <NText depth="3" style="font-size: 12px">月份为必填项</NText>
        <NRadioGroup v-model:value="basis" size="small">
          <NRadioButton value="period">按记账期间</NRadioButton>
          <NRadioButton value="occurDate">按业务日期</NRadioButton>
        </NRadioGroup>
        <NInput
          v-model:value="scopePrefixes"
          :placeholder="`科目前缀（逗号分隔，留空＝${DEFAULT_SCOPE_PREFIXES}）`"
          style="width: 260px"
        />
        <NButton type="primary" :disabled="!canQuery" :loading="loading" @click="query">查询</NButton>
      </NSpace>

      <div class="preset-row mt-2">
        <span class="preset-label">科目前缀预设：</span>
        <NButton size="tiny" quaternary :type="scopePrefixes === '5,6' ? 'primary' : 'default'" @click="scopePrefixes = '5,6'">
          损益支出 (5,6)
        </NButton>
        <NButton size="tiny" quaternary :type="scopePrefixes === '4' ? 'primary' : 'default'" @click="scopePrefixes = '4'">
          成本类 (4)
        </NButton>
        <NButton size="tiny" quaternary :type="scopePrefixes === '4,5,6' ? 'primary' : 'default'" @click="scopePrefixes = '4,5,6'">
          全部成本费用 (4,5,6)
        </NButton>
        <NButton size="tiny" quaternary :type="scopePrefixes === '' ? 'primary' : 'default'" @click="scopePrefixes = ''">
          默认
        </NButton>
      </div>

      <NAlert v-if="showOccurDateWarning" type="warning" :bordered="false" class="mt-4">
        {{ OCCUR_DATE_WARNING }}
      </NAlert>
      <div class="mt-2">
        <NText depth="3" style="font-size: 12px">
          当前生效的科目前缀：{{ effectiveScopePrefixes }}；月份按「记账期间」匹配，业务日期口径仅在管理报表开放。
        </NText>
      </div>
    </NCard>

    <template v-if="totals">
      <div class="stats-bar mb-4">
        <template v-if="isBoss">
          <NCard :bordered="false">
            <NStatistic label="项目净额" :value="totals.allocatedAmount" />
          </NCard>
          <NCard :bordered="false">
            <NStatistic label="已归集笔数" :value="totals.allocatedLineCount" />
          </NCard>
          <NCard :bordered="false">
            <NStatistic label="未归集金额" :value="totals.unassignedAmount" />
          </NCard>
          <NCard :bordered="false">
            <NStatistic label="项目/成员数" :value="items.length" />
          </NCard>
        </template>
        <template v-else>
          <NCard :bordered="false"><NStatistic label="凭证行数" :value="totals.lineCount" /></NCard>
          <NCard :bordered="false"><NStatistic label="已归集行" :value="totals.allocatedLineCount" /></NCard>
          <NCard :bordered="false"><NStatistic label="部分归集行" :value="totals.partialLineCount" /></NCard>
          <NCard :bordered="false"><NStatistic label="未归集行" :value="totals.unallocatedLineCount" /></NCard>
        </template>
      </div>

      <NAlert
        v-if="!totals.balanced"
        type="error"
        :bordered="false"
        class="mb-4"
        title="行金额 ≠ 已归集 + 未归集，请检查数据"
      />

      <NCard :bordered="false" title="金额合计" class="mb-4">
        <NSpace :size="24" wrap>
          <NText>行金额合计：{{ formatMoney(totals.lineAmount) }}</NText>
          <NText>已归集合计：{{ formatMoney(totals.allocatedAmount) }}</NText>
          <NText>未归集合计：{{ formatMoney(totals.unassignedAmount) }}</NText>
        </NSpace>
      </NCard>
    </template>

    <NCard :bordered="false" title="成员归集明细">
      <NSpin :show="loading">
        <div class="report-table-wrapper">
          <table class="report-table">
            <thead>
              <tr>
                <th class="code-cell">编码</th>
                <th>名称</th>
                <th style="width: 100px">状态</th>
                <th class="amount sortable" @click="toggleSort('allocatedAmount')">
                  已归集金额
                  <span class="sort-icon">{{ sortField === 'allocatedAmount' ? (sortOrder === 'asc' ? '↑' : '↓') : '↕' }}</span>
                </th>
                <th class="amount sortable" @click="toggleSort('allocatedLineCount')">
                  归集行数
                  <span class="sort-icon">{{ sortField === 'allocatedLineCount' ? (sortOrder === 'asc' ? '↑' : '↓') : '↕' }}</span>
                </th>
                <th class="amount">占行金额</th>
                <th style="width: 90px; text-align: center">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in displayItems" :key="item.memberId">
                <td class="code-cell">{{ item.code || '-' }}</td>
                <td>{{ item.name || '-' }}</td>
                <td>
                  <NTag size="small" :type="memberStatusTagType(item.status)">
                    {{ memberStatusLabel(item.status) }}
                  </NTag>
                </td>
                <td class="amount amount-cell">{{ formatMoney(item.allocatedAmount) }}</td>
                <td class="amount amount-cell">{{ item.allocatedLineCount }}</td>
                <td class="amount amount-cell">{{ shareText(item) }}</td>
                <td style="text-align: center">
                  <NButton size="tiny" quaternary type="primary" @click="goToPending">
                    清单
                  </NButton>
                </td>
              </tr>
              <tr v-if="!items.length">
                <td colspan="7" class="empty report-empty-cell">
                  {{ isEmptyResult ? '该期间无已过账的损益类凭证行。' : '暂无数据：选择月份后点击查询。' }}
                </td>
              </tr>
            </tbody>
            <tfoot v-if="items.length">
              <tr class="total-row">
                <td colspan="3" class="total-cell">合　计</td>
                <td class="amount amount-cell total-cell">{{ formatMoney(totals?.allocatedAmount ?? 0) }}</td>
                <td class="amount amount-cell total-cell">
                  {{ items.reduce((sum, item) => sum + item.allocatedLineCount, 0) }}
                </td>
                <td class="amount amount-cell total-cell">—</td>
                <td class="total-cell">—</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </NSpin>
    </NCard>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.mt-2 { margin-top: 8px; }
.mt-4 { margin-top: 16px; }
.stats-bar { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.preset-row { display: flex; align-items: center; gap: 4px; font-size: 12px; flex-wrap: wrap; }
.preset-label { color: var(--solo-text-muted); font-size: 12px; margin-right: 4px; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.total-cell { font-weight: 700; }
.empty { text-align: center; color: var(--solo-text-muted); padding: 28px; }
.scope-note { margin: 0; font-size: 12.5px; color: var(--solo-text-secondary); line-height: 1.7; }
.sortable { cursor: pointer; user-select: none; }
.sortable:hover { color: var(--solo-primary); }
.sort-icon { font-size: 11px; margin-left: 2px; opacity: 0.6; }
@media (max-width: 900px) {
  .stats-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
