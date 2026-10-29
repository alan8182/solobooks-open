<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
// 待归集清单 + 归集抽屉入口。
//
// 三条本页特有的铁律：
// 1. dimensionId **必传**：后端 aggregation_service.py:829-831 在 dimensionId 为空且
//    state='all' 时会跳过 allocated 行，语义对用户不直观。store 默认选中第一个维度，
//    DimensionPicker 不设 clearable，所以这里恒有值（设计文档 §9 #14）。
// 2. 月份筛选用 periodStart，occurredOn 只作展示列。补记凭证的业务日期在上月，
//    按业务日期过滤会让它在自己的记账期间里「消失」——全项目最容易串台的点。
// 3. 合计条是**全量合计**（后端分页前累加），不是当页合计，文案必须说清。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDatePicker,
  NInput,
  NModal,
  NPagination,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NStatistic,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import DimensionPicker from './_components/DimensionPicker.vue'
import AllocationDrawer from './_components/AllocationDrawer.vue'
import {
  listAggregationRules,
  listMembers,
  listPendingLines,
  replaceLineAllocations,
} from '@/api/aggregation'
import type {
  AggregationBasis,
  AggregationMember,
  AggregationPendingItem,
  AggregationPendingStateParam,
  AllocationMethod,
} from '@/api/types'
import { useAggregationStore } from '@/stores/aggregation'
import {
  OCCUR_DATE_WARNING,
  PENDING_SCOPE_NOTE,
  absoluteAmount,
  directionLabel,
  errorText,
  isAmountEqual,
  quantizeAmount,
  stateLabel,
  voucherStatusLabel,
} from '@/utils/aggregation'
import { formatMoney } from '@/utils/format'

const store = useAggregationStore()
const router = useRouter()
const message = useMessage()

/** 后端 limit 上限 500（Query(le=500)） */
const MAX_LIMIT = 500
const DEFAULT_LIMIT = 100
/** 后端 DEFAULT_SCOPE_PREFIXES。筛选框留空即用这个默认值，UI 必须显示出来 */
const DEFAULT_SCOPE_PREFIXES = '5,6'

const loading = ref(false)
const items = ref<AggregationPendingItem[]>([])
const total = ref(0)
const members = ref<AggregationMember[]>([])
const ruleCount = ref(0)
const totals = ref({ lineAmount: 0, allocatedAmount: 0, remainingAmount: 0 })

/** 规则数 = 0 时的引导条；用户点「仍要手工分配」后只 dismiss 引导条，不跳转也不阻断 */
const showRuleGuide = ref(true)
const guideDismissed = ref(false)

/** 月份区间。value-format='yyyy-MM' 让 NDatePicker 直接给 YYYY-MM 字符串，无需 dayjs 转换 */
const monthRange = ref<[string, string] | null>(null)

/**
 * NDatePicker 的 monthrange 在 value-format='yyyy-MM' 时**运行期**交出 YYYY-MM 字符串数组，
 * 但它的 Value 类型声明里只认 [number, number]。用 applyMonthRange 做一次边界收敛，
 * 避免在模板里写类型断言（与 rules.vue 的 applyPeriod 同一手法）。
 */
const applyMonthRange = computed<any>(() => monthRange.value)

function onMonthRangeUpdate(value: [string | null, string | null] | null): void {
  monthRange.value = value?.[0] && value?.[1] ? [value[0], value[1]] : null
}
const basis = ref<AggregationBasis>('period')
const stateFilter = ref<AggregationPendingStateParam>('all')
const scopePrefixes = ref('')
const limit = ref(DEFAULT_LIMIT)
const page = ref(1)

const checkedKeys = ref<string[]>([])

const stateOptions: SelectOption[] = [
  { label: '全部', value: 'all' },
  { label: '未归集', value: 'unallocated' },
  { label: '部分归集', value: 'partial' },
  { label: '已归集', value: 'allocated' },
]

const dimensionId = computed(() => store.currentDimensionId)
const hasDimension = computed(() => Boolean(dimensionId.value))

/** 切到 occurDate 必须显示警示：补记凭证会跑到它业务日期所在的月份 */
const showOccurDateWarning = computed(() => basis.value === 'occurDate')

/** 实际生效的科目前缀：留空走后端默认 5,6 */
const effectiveScopePrefixes = computed(() => scopePrefixes.value.trim() || DEFAULT_SCOPE_PREFIXES)

const limitOverflow = computed(() => limit.value > MAX_LIMIT)

const showRuleGuideBar = computed(() => showRuleGuide.value && !guideDismissed.value && ruleCount.value === 0)

// ===== 数据加载 =====

/** 规则数只用于引导条，失败不打扰用户 */
async function loadRuleCount(): Promise<void> {
  if (!dimensionId.value) {
    ruleCount.value = 0
    return
  }
  try {
    ruleCount.value = (await listAggregationRules(dimensionId.value)).length
  } catch {
    ruleCount.value = 0
  }
}

async function loadMembers(): Promise<void> {
  if (!dimensionId.value) {
    members.value = []
    return
  }
  try {
    members.value = await listMembers(dimensionId.value)
  } catch (err) {
    members.value = []
    message.error(errorText(err))
  }
}

async function load(): Promise<void> {
  // dimensionId 视为必传：空维度请求会被后端跳过 allocated 行，语义不可直观
  if (!dimensionId.value) return
  loading.value = true
  checkedKeys.value = []
  try {
    const range = monthRange.value
    const data = await listPendingLines({
      dimensionId: dimensionId.value,
      start: range?.[0],
      end: range?.[1],
      basis: basis.value,
      state: stateFilter.value,
      scopePrefixes: scopePrefixes.value.trim() || undefined,
      limit: limit.value,
      offset: (page.value - 1) * limit.value,
    })
    items.value = data.items
    total.value = data.total
    totals.value = data.totals
  } catch (err) {
    items.value = []
    total.value = 0
    message.error(errorText(err))
  } finally {
    loading.value = false
  }
}

function query(): void {
  page.value = 1
  void load()
}

// ===== 归集抽屉 =====

const drawerShow = ref(false)
const drawerItem = ref<AggregationPendingItem | null>(null)

function openDrawer(item: AggregationPendingItem): void {
  drawerItem.value = item
  drawerShow.value = true
}

async function onDrawerSaved(): Promise<void> {
  await load()
}

// ===== 批量分配 =====

const showBatch = ref(false)
const batchMode = ref<'member' | 'amount'>('member')
const batchMemberId = ref('')
const batchAmount = ref<number | null>(null)
const batchMethod = ref('')
const batchBasis = ref('')
const batchSubmitting = ref(false)

const selectedItems = computed(() =>
  items.value.filter((item) => checkedKeys.value.includes(item.lineId)),
)

const batchMethodOptions = computed<SelectOption[]>(() =>
  store.methodOptions.map((option) => ({ label: option.label, value: option.value })),
)

/**
 * 统一金额模式下，若该金额与行金额判等，才把这一行标为「完成归集」，
 * 否则一律按草稿（complete=false）保存，避免吃 ALLOCATION_TOTAL_MISMATCH。
 *
 * ⚠️ 判等走 isAmountEqual（按分量化），不用裸浮点 `< 0.005`——
 *    后端用 Decimal(20,2) 比较，前端必须同精度，否则会放行一个必 422 的请求（QA-7 同源问题）。
 */
function batchCompleteFlag(item: AggregationPendingItem, amount: number): boolean {
  return isAmountEqual(absoluteAmount(item.amount), amount)
}

async function submitBatch(): Promise<void> {
  const selected = selectedItems.value
  if (!selected.length) return
  if (!batchMemberId.value) {
    message.warning('请选择归集对象')
    return
  }
  if (batchMode.value === 'amount' && !(Number(batchAmount.value) > 0)) {
    message.warning('请填写大于 0 的统一金额')
    return
  }
  const method = (batchMethod.value || store.methodOptions[0]?.value || 'direct') as AllocationMethod
  const amount = batchMode.value === 'member'
    ? null
    : Number(batchAmount.value)

  batchSubmitting.value = true
  try {
    // 统一对象模式：每行各自发一次 PUT（每行一个 lineId），用 allSettled 汇总而不短路
    const results = await Promise.allSettled(
      selected.map((item) => {
        const rowAmount = amount ?? absoluteAmount(item.amount)
        return replaceLineAllocations(item.lineId, {
          dimensionId: dimensionId.value,
          allocations: [
            {
              memberId: batchMemberId.value,
              amount: rowAmount,
              allocationMethod: method,
              allocationBasis: batchBasis.value.trim() ? batchBasis.value.trim() : null,
            },
          ],
          complete: batchCompleteFlag(item, rowAmount),
        })
      }),
    )
    const succeeded = results.filter((r) => r.status === 'fulfilled').length
    const failed = results.length - succeeded
    if (failed) {
      message.warning(`批量归集完成：成功 ${succeeded} 行，失败 ${failed} 行（失败行原因请单独打开查看）`)
    } else {
      message.success(`批量归集完成：${succeeded} 行已保存`)
    }
    showBatch.value = false
    await load()
  } finally {
    batchSubmitting.value = false
  }
}

// ===== 展示辅助 =====

function stateTagType(state: string): 'success' | 'warning' | 'default' {
  if (state === 'allocated') return 'success'
  if (state === 'partial') return 'warning'
  return 'default'
}

function toggleCheck(lineId: string, checked: boolean): void {
  const next = new Set(checkedKeys.value)
  if (checked) next.add(lineId)
  else next.delete(lineId)
  checkedKeys.value = Array.from(next)
}

function toggleAll(checked: boolean): void {
  checkedKeys.value = checked ? items.value.map((item) => item.lineId) : []
}

const allChecked = computed(
  () => items.value.length > 0 && checkedKeys.value.length === items.value.length,
)

onMounted(async () => {
  await store.bootstrap()
  await Promise.all([loadRuleCount(), loadMembers()])
  await load()
})

watch(dimensionId, async () => {
  page.value = 1
  await Promise.all([loadRuleCount(), loadMembers()])
  await load()
})
</script>

<template>
  <div class="agg-pending">
    <div class="page-header">
      <div>
        <h2>待归集清单</h2>
        <p>按维度把凭证行分配到归集对象；未全额分配的行会停在「部分归集」。</p>
      </div>
      <DimensionPicker />
    </div>

    <NAlert type="info" :bordered="false" class="mb-4">{{ PENDING_SCOPE_NOTE }}</NAlert>

    <!-- 规则数 = 0 的引导条：主 CTA 去配置规则，次要按钮仅 dismiss 引导条 -->
    <NAlert
      v-if="showRuleGuideBar"
      type="error"
      :bordered="false"
      class="mb-4"
      title="当前维度尚未配置归集规则"
      data-testid="rule-guide"
    >
      <div class="guide-row">
        <span>没有规则时全部凭证行都要手工分配。可以先去配置规则，或直接手工分配。</span>
        <NSpace :size="8">
          <NButton size="small" type="primary" @click="router.push('/aggregation/rules')">配置规则</NButton>
          <NButton size="small" text @click="guideDismissed = true">仍要手工分配</NButton>
        </NSpace>
      </div>
    </NAlert>

    <NAlert v-if="!hasDimension" type="warning" :bordered="false" class="mb-4">
      请先建立归集维度。待归集清单始终按维度查询，不提供「不选维度」选项。
    </NAlert>

    <NCard :bordered="false" title="筛选" class="mb-4">
      <NSpace align="center" wrap :size="12">
        <NDatePicker
          :value="applyMonthRange"
          type="monthrange"
          value-format="yyyy-MM"
          :clearable="true"
          style="width: 240px"
          @update:value="onMonthRangeUpdate as (value: unknown) => void"
        />
        <NRadioGroup v-model:value="basis" size="small">
          <NRadioButton value="period">按记账期间</NRadioButton>
          <NRadioButton value="occurDate">按业务日期</NRadioButton>
        </NRadioGroup>
        <NSelect v-model:value="stateFilter" :options="stateOptions" style="width: 140px" />
        <NInput
          v-model:value="scopePrefixes"
          :placeholder="`科目前缀（逗号分隔，留空＝${DEFAULT_SCOPE_PREFIXES}）`"
          style="width: 260px"
        />
        <NInputNumber v-model:value="limit" :min="1" :max="MAX_LIMIT" :show-button="false" style="width: 110px" />
        <NButton type="primary" :disabled="!hasDimension" @click="query">查询</NButton>
      </NSpace>
      <NAlert v-if="showOccurDateWarning" type="warning" :bordered="false" class="mt-4">
        {{ OCCUR_DATE_WARNING }}
      </NAlert>
      <NAlert v-if="limitOverflow" type="warning" :bordered="false" class="mt-4">
        每页最多 {{ MAX_LIMIT }} 行，当前设置超出后端上限，请调小。
      </NAlert>
      <NText depth="3" style="font-size: 12px">
        当前生效的科目前缀：{{ effectiveScopePrefixes }}；月份筛选按「记账期间」列（periodStart）匹配，业务日期列仅供展示。
      </NText>
    </NCard>

    <div class="totals-bar mb-4">
      <NCard :bordered="false"><NStatistic label="行金额合计（全量）" :value="formatMoney(quantizeAmount(totals.lineAmount))" /></NCard>
      <NCard :bordered="false"><NStatistic label="已归集合计（全量）" :value="formatMoney(quantizeAmount(totals.allocatedAmount))" /></NCard>
      <NCard :bordered="false"><NStatistic label="未归集合计（全量）" :value="formatMoney(quantizeAmount(totals.remainingAmount))" /></NCard>
    </div>

    <NCard :bordered="false" title="待归集凭证行">
      <template #header-extra>
        <NSpace align="center" :size="8">
          <NText v-if="checkedKeys.length" depth="2" style="font-size: 13px">已选 {{ checkedKeys.length }} 行</NText>
          <NButton size="small" :disabled="!checkedKeys.length" @click="showBatch = true">批量归集</NButton>
        </NSpace>
      </template>

      <NSpin :show="loading">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="check-col">
                  <NCheckbox :checked="allChecked" @update:checked="toggleAll" />
                </th>
                <th>凭证号</th>
                <th>摘要</th>
                <th>科目</th>
                <th>期间</th>
                <th>业务日期</th>
                <th>方向</th>
                <th class="amount">金额</th>
                <th class="amount">已归集</th>
                <th class="amount">未归集</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.lineId">
                <td class="check-col">
                  <NCheckbox
                    :checked="checkedKeys.includes(item.lineId)"
                    @update:checked="(checked: boolean) => toggleCheck(item.lineId, checked)"
                  />
                </td>
                <td>{{ item.voucherNo || '-' }}</td>
                <td class="summary-cell" :title="item.summary">{{ item.summary }}</td>
                <td>{{ item.accountCode }} {{ item.accountName }}</td>
                <td>{{ item.periodStart }}</td>
                <td>{{ item.occurredOn }}</td>
                <td>{{ directionLabel(item.direction) }}</td>
                <td class="amount">{{ formatMoney(quantizeAmount(absoluteAmount(item.amount))) }}</td>
                <td class="amount">{{ formatMoney(quantizeAmount(absoluteAmount(item.allocatedAmount))) }}</td>
                <td class="amount">{{ formatMoney(quantizeAmount(absoluteAmount(item.remainingAmount))) }}</td>
                <td>
                  <NSpace :size="4">
                    <NTag size="small" :type="stateTagType(item.state)">{{ stateLabel(item.state) }}</NTag>
                    <NTag size="small" :type="item.status === 'posted' ? 'warning' : 'info'">
                      {{ voucherStatusLabel(item.status) }}
                    </NTag>
                  </NSpace>
                </td>
                <td>
                  <NButton size="small" secondary @click="openDrawer(item)">归集</NButton>
                </td>
              </tr>
              <tr v-if="!items.length">
                <td colspan="12" class="empty">当前筛选条件下无待归集凭证行。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </NSpin>

      <NSpace justify="space-between" align="center" class="pager">
        <NText depth="3" style="font-size: 12px">共 {{ total }} 行 · 每页 {{ limit }} 行</NText>
        <NPagination
          v-model:page="page"
          :page-size="limit"
          :item-count="total"
          :page-slot="10"
          @update:page="load"
        />
      </NSpace>
    </NCard>

    <AllocationDrawer
      v-model:show="drawerShow"
      :item="drawerItem"
      :dimension-id="dimensionId"
      :members="members"
      :method-options="store.methodOptions"
      @saved="onDrawerSaved"
    />

    <NModal v-model:show="showBatch" preset="card" title="批量归集" style="width: min(560px, 94vw)">
      <NAlert type="warning" :bordered="false" class="mb-4">
        批量归集会逐行覆盖各自在当前维度下的全部分配。统一金额不等于行金额时按草稿保存（停在「部分归集」）。
      </NAlert>
      <NSpace vertical :size="12">
        <NRadioGroup v-model:value="batchMode">
          <NRadioButton value="member">统一归集对象（全额）</NRadioButton>
          <NRadioButton value="amount">统一金额</NRadioButton>
        </NRadioGroup>
        <NSelect
          v-model:value="batchMemberId"
          :options="members.map((member) => ({ label: `${member.code} · ${member.name}`, value: member.id }))"
          placeholder="归集对象"
          filterable
        />
        <NInputNumber
          v-if="batchMode === 'amount'"
          v-model:value="batchAmount"
          :min="0"
          :precision="2"
          :show-button="false"
          placeholder="统一金额"
        />
        <NSelect v-model:value="batchMethod" :options="batchMethodOptions" placeholder="分摊方法" />
        <NInput v-model:value="batchBasis" placeholder="分摊依据（选填）" />
        <NText depth="3" style="font-size: 12px">
          本次将对 {{ selectedItems.length }} 行分别提交。按比例分摊会产生尾差，本面板不提供按比例分配。
        </NText>
      </NSpace>
      <NSpace justify="end" class="mt-4">
        <NButton @click="showBatch = false">取消</NButton>
        <NButton type="primary" :loading="batchSubmitting" @click="submitBatch">提交</NButton>
      </NSpace>
    </NModal>
  </div>
</template>

<style scoped>
.agg-pending { max-width: 1320px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 22px; }
.page-header p { margin: 5px 0 0; color: var(--solo-text-secondary); font-size: 13px; }
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }
.guide-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.totals-bar { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 10px; border-bottom: 1px solid var(--solo-border-light); text-align: left; white-space: nowrap; }
th { color: var(--solo-text-secondary); font-weight: 600; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.check-col { width: 36px; }
.summary-cell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
.empty { text-align: center; color: var(--solo-text-muted); padding: 28px; }
.pager { margin-top: 16px; }
@media (max-width: 900px) {
  .totals-bar { grid-template-columns: 1fr; }
  .page-header { flex-direction: column; }
}
</style>