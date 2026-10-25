<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script lang="ts">
// 抽屉状态机的**纯逻辑部分**，独立于 SFC 的响应式代码，便于 vitest 直接覆盖。
//
// 为什么单独一个 <script> 块：<script setup> 不允许具名导出，而「打开抽屉时回填已有分配」
// 与「四态下按钮是否可用」正是本页最容易出数据事故的地方，必须能被单测钉死
// （设计文档 §11 风险 1 / 风险 4）。
//
// ⚠️ 头号风险：PUT /lines/{journalLineId}/allocations 是**整行替换**语义，不传的分配会被删除，
//    且没有 undo（只有审计日志）。因此打开抽屉时必须回填 item.allocations —— 否则用户只改
//    一条金额点保存，其余已有分配就被**静默抹掉**。

import {
  absoluteAmount,
  diffCents,
  remainingPercent,
  sumAllocation,
} from '@/utils/aggregation'
import type { AllocationDraftRow } from '@/utils/aggregation'
import type { AggregationPendingItem } from '@/api/types'

/**
 * ⚠️ 这里**不再有**浮点容差常量，也不出现 `Math.abs(diff) < 0.005`。
 *
 * 两次修复的教训（都属设计文档 §11 风险 4「用户点一下吃红弹窗」）：
 *  ① 裸浮点比较：1000.005 - 1000 = 0.00499999999999545 < 0.005 → 判「已平」并放行 PUT，
 *     但后端 Decimal(20,2) 算出0.005 差额 → 422 ALLOCATION_TOTAL_MISMATCH。
 *  ② 改用 `Math.round(value * 100)` 后**正数侧仍会误放行**：`1.005 * 100`
 *     实际是 100.49999999999999 → Math.round 得 100（1.00），而后端从精确十进制
 *     "1.005" 得 1.01 → 仍是「前端判等放行、后端 422」。
 *
 * 现在统一走 utils/aggregation 的**十进制字面量量化**（toCents / diffCents），
 * 与后端 `Decimal(str(v)).quantize(ROUND_HALF_UP)` 逐位对齐，差分测试见
 * `__tests__/moneyQuantization.test.ts`（内联后端实跑的黄金值）。
 */

/**
 * 把后端返回的已有分配回填成抽屉草稿行。
 *
 * ⚠️ 这是防「静默抹掉已有分配」的唯一防线，必须保留为独立纯函数并被单测覆盖。
 * 金额已是 number（API 层归一化），**禁止**在此写 Number(a.amount) 兜底。
 */
export function toDraftRows(item: AggregationPendingItem | null | undefined): AllocationDraftRow[] {
  if (!item?.allocations?.length) return []
  return item.allocations.map((allocation) => ({
    memberId: allocation.memberId,
    amount: allocation.amount,
    allocationMethod: allocation.allocationMethod,
    allocationBasis: allocation.allocationBasis ?? '',
  }))
}

/**
 * 打开抽屉时的回填守卫。
 *
 * 返回 true 表示**检测到异常**：item 上有已有分配，但回填后草稿为空。
 * 这种情况一旦被忽略，用户保存就会把已有分配全部抹掉，所以调用方必须
 * console.warn + message.warning 提示刷新重试（设计文档 §11 风险 1）。
 */
export function hasBackfillAnomaly(
  item: AggregationPendingItem | null | undefined,
  rows: AllocationDraftRow[],
): boolean {
  return (item?.allocations?.length ?? 0) > 0 && rows.length === 0
}

/** 抽屉四态。命名与设计文档 §6.6 状态机图一致。 */
export type AllocationDrawerPhase = 'empty' | 'partial' | 'over' | 'complete'

/** 差额提示的语义色：success=已平，error=有差额，default=空态 */
export type AllocationDiffTone = 'default' | 'error' | 'success'

export interface AllocationDrawerState {
  /** 行金额基数（绝对值，已按分量化） */
  expected: number
  /** 草稿合计（已按分量化） */
  total: number
  /** 差额 = 合计 - 行金额（元，已按分量化）。正数表示超出 */
  diff: number
  phase: AllocationDrawerPhase
  /** 「完成归集」的唯一 disabled 判据就是它，不做其它判断（§6.2 表格即契约） */
  canComplete: boolean
  /** 超额时连「保存草稿」也禁用：后端 rate_total > 1 必 422 */
  canSaveDraft: boolean
  /** 差额提示文案（常驻，不是点了才弹） */
  diffText: string
  diffTone: AllocationDiffTone
}

/**
 * 由草稿行 + 行金额推导抽屉状态。
 *
 * ⚠️ 全部金额先经 toCents 量化到分再比较/展示，抽屉内**不出现裸浮点容差**。
 *
 * @param rows 草稿行（金额单位：元，恒正）
 * @param lineAmount 行金额，**带符号**（后端 _signed()，贷行为负），内部取绝对值
 */
export function drawerState(
  rows: AllocationDraftRow[],
  lineAmount: number,
): AllocationDrawerState {
  // 量化到分：expected / total / diff 全部是两位小数的「干净」值，
  // 既让 canComplete 与后端 Decimal(20,2) 同口径，也让 toFixed(2) 不会打印出 0.30000000000000004
  const expected = toCents(absoluteAmount(lineAmount)) / 100
  const total = toCents(sumAllocation(rows)) / 100
  const diff = diffCents(rows, lineAmount) / 100
  // 按分整数判等，不再用 Math.abs(diff) < EPSILON（QA-7：1000.005 会被误判为已平）
  const canComplete = diffCents(rows, lineAmount) === 0
  const isEmpty = rows.length === 0 || toCents(sumAllocation(rows)) === 0

  let phase: AllocationDrawerPhase
  if (isEmpty) phase = 'empty'
  else if (canComplete) phase = 'complete'
  else if (diff > 0) phase = 'over'
  else phase = 'partial'

  let diffText: string
  let diffTone: AllocationDiffTone
  if (phase === 'empty') {
    diffText = `尚未分配 · 行金额 ¥${expected.toFixed(2)}`
    diffTone = 'default'
  } else if (phase === 'complete') {
    diffText = '已全额分配 ✓'
    diffTone = 'success'
  } else if (phase === 'over') {
    diffText = `超出 ¥${diff.toFixed(2)}，请调减`
    diffTone = 'error'
  } else {
    diffText = `还差 ¥${Math.abs(diff).toFixed(2)}（未分配 ${remainingPercent(rows, lineAmount)}%）`
    diffTone = 'error'
  }

  return {
    expected,
    total,
    diff,
    phase,
    canComplete,
    canSaveDraft: phase !== 'over',
    diffText,
    diffTone,
  }
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { clearLineAllocations, replaceLineAllocations } from '@/api/aggregation'
import type {
  AggregationMember,
  AllocationMethod,
  LabelOption,
} from '@/api/types'
import { errorText, quantizeAmount, toCents, validateAllocations } from '@/utils/aggregation'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  show: boolean
  item: AggregationPendingItem | null
  dimensionId: string
  members: AggregationMember[]
  methodOptions: LabelOption[]
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved'): void
}>()

const message = useMessage()

/** 草稿行。用 ref 而非 reactive，便于整体替换（回填时直接赋新数组） */
const rows = ref<AllocationDraftRow[]>([])
/** 校验失败时的行级提示，常驻在差额提示下方 */
const validationError = ref('')
const saving = ref(false)
const clearing = ref(false)
const showClearConfirm = ref(false)

/** 行金额基数（绝对值）。行金额带符号，比较时一律取绝对值 */
const expectedAmount = computed(() => Math.abs(props.item?.amount ?? 0))

const state = computed(() => drawerState(rows.value, props.item?.amount ?? 0))

/** 清空按钮仅在该行确实有归集记录时显示——否则后端 DELETE 会抛 404 NOT_FOUND */
const canClear = computed(() => (props.item?.allocations?.length ?? 0) > 0)

const memberOptions = computed<SelectOption[]>(() =>
  props.members.map((member) => ({
    label: `${member.code} · ${member.name}`,
    value: member.id,
  })),
)

const methodSelectOptions = computed<SelectOption[]>(() =>
  props.methodOptions.map((option) => ({ label: option.label, value: option.value })),
)

/** 已选过的 memberId，用于在选项上标「已分配」，避免用户重复分配同一对象 */
function memberSelectedElsewhere(memberId: string, currentIndex: number): boolean {
  return rows.value.some((row, index) => index !== currentIndex && row.memberId === memberId)
}

const showProxy = computed<boolean>({
  get: () => props.show,
  set: (value) => emit('update:show', value),
})

/** 顶部常驻告警：整行替换语义必须让用户看见 */
const overwriteNotice = computed(
  () => `保存将覆盖本行在当前维度下的全部分配（${props.item?.allocations?.length ?? 0} 条），覆盖后无法撤销。`,
)

/**
 * 回填。设计文档 §6.1：打开抽屉时必须用 item.allocations 回填，否则保存会抹掉已有归集。
 * 组件里不做 dayjs/Number 兜底，金额在 API 层已归一化。
 */
function backfill(item: AggregationPendingItem | null): void {
  const next = toDraftRows(item)
  rows.value = next
  validationError.value = ''
  if (hasBackfillAnomaly(item, next)) {
    // 走到这里说明后端返回了 allocations 但我们没能读出草稿行，若继续保存会清空已有分配
    console.warn('[AllocationDrawer] 已有分配未能回填，已阻止保存以免抹掉数据', item)
    message.warning('未能读取已有分配，请刷新后重试')
  }
}

watch(
  () => [props.show, props.item] as const,
  ([show, item]) => {
    if (show) backfill(item)
  },
  { immediate: true, deep: false },
)

function addRow(): void {
  rows.value.push({
    memberId: '',
    amount: 0,
    allocationMethod: props.methodOptions[0]?.value ?? 'direct',
    allocationBasis: '',
  })
  validationError.value = ''
}

function removeRow(index: number): void {
  rows.value.splice(index, 1)
  validationError.value = ''
}

/** 把剩余未分配金额补到最后一行，减少手工输入（用共享量化函数，避免手写 Math.round 口径不一） */
function fillRemaining(index: number): void {
  const others = rows.value.reduce(
    (sum, row, i) => (i === index ? sum : sum + (Number(row.amount) || 0)),
    0,
  )
  const rest = quantizeAmount(expectedAmount.value - toCents(others) / 100)
  if (rest <= 0) {
    message.warning('剩余金额不足，请先调减其他行')
    return
  }
  rows.value[index].amount = rest
  validationError.value = ''
}

async function submit(complete: boolean): Promise<void> {
  const item = props.item
  if (!item || !props.dimensionId) return

  const error = validateAllocations(rows.value, item.amount)
  if (error) {
    validationError.value = error
    message.warning(error)
    return
  }

  saving.value = true
  try {
    await replaceLineAllocations(item.lineId, {
      dimensionId: props.dimensionId,
      allocations: rows.value.map((row) => ({
        memberId: row.memberId,
        amount: Number(row.amount),
        allocationMethod: row.allocationMethod as AllocationMethod,
        allocationBasis: row.allocationBasis?.trim() ? row.allocationBasis.trim() : null,
      })),
      complete,
    })
    message.success(complete ? '已完成归集' : '已保存归集草稿')
    emit('saved')
    emit('update:show', false)
  } catch (err) {
    message.error(errorText(err, complete ? '完成归集失败' : '保存归集草稿失败'))
  } finally {
    saving.value = false
  }
}

/** 清空：DELETE 的 dimensionId 是必填 query，漏了会 422 */
async function confirmClear(): Promise<void> {
  const item = props.item
  if (!item || !props.dimensionId) return
  clearing.value = true
  try {
    await clearLineAllocations(item.lineId, props.dimensionId)
    message.success('已清空该行在当前维度下的全部分配')
    showClearConfirm.value = false
    emit('saved')
    emit('update:show', false)
  } catch (err) {
    message.error(errorText(err, '清空归集失败'))
  } finally {
    clearing.value = false
  }
}

function close(): void {
  emit('update:show', false)
}
</script>

<template>
  <NDrawer v-model:show="showProxy" :width="720" placement="right">
    <NDrawerContent :title="item ? `归集 · ${item.voucherNo || item.summary}` : '归集'" closable>
      <template #header>
        <div class="drawer-title">
          <span>{{ item ? `归集 · ${item.voucherNo || item.summary}` : '归集' }}</span>
          <NTag v-if="item" size="small" :type="item.status === 'posted' ? 'warning' : 'info'">
            {{ item.status }}
          </NTag>
        </div>
      </template>

      <div v-if="item" class="drawer-body">
        <NAlert type="warning" :bordered="false" class="mb-4" data-testid="overwrite-alert">
          {{ overwriteNotice }}
        </NAlert>

        <NAlert type="info" :bordered="false" class="mb-4">
          <div class="meta-grid">
            <span>科目</span><strong>{{ item.accountCode }} {{ item.accountName }}</strong>
            <span>凭证行金额</span><strong>{{ formatMoney(expectedAmount) }}</strong>
            <span>记账期间</span><strong>{{ item.periodStart }}</strong>
            <span>业务日期</span><strong>{{ item.occurredOn }}</strong>
          </div>
          <div class="ratio-note">按比例分摊会产生尾差，建议按金额分配。</div>
        </NAlert>

        <div class="rows-header">
          <NText strong>分配明细</NText>
          <NButton size="small" @click="addRow">添加分配</NButton>
        </div>

        <div v-if="!rows.length" class="empty">尚未添加任何分配。</div>

        <div v-for="(row, index) in rows" :key="index" class="alloc-row">
          <NSelect
            v-model:value="row.memberId"
            :options="memberOptions.map((option) => ({
              ...option,
              disabled: memberSelectedElsewhere(option.value as string, index),
            }))"
            placeholder="归集对象"
            filterable
          />
          <NInputNumber
            v-model:value="row.amount"
            :min="0"
            :precision="2"
            :show-button="false"
            placeholder="金额"
          />
          <NSelect
            v-model:value="row.allocationMethod"
            :options="methodSelectOptions"
            placeholder="分摊方法"
          />
          <NInput v-model:value="row.allocationBasis" placeholder="分摊依据（拆分给多个对象时必填）" />
          <NButton quaternary type="error" :disabled="rows.length === 1" @click="removeRow(index)">
            删除
          </NButton>
          <NButton quaternary size="small" @click="fillRemaining(index)">补足</NButton>
        </div>

        <NAlert
          v-if="validationError"
          type="error"
          :bordered="false"
          class="mt-4"
          data-testid="validation-error"
        >
          {{ validationError }}
        </NAlert>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <!-- 差额提示常驻在操作区正上方，不是点了才弹（设计文档 §6.2） -->
          <div class="diff-bar" data-testid="diff-bar" :data-phase="state.phase">
            <span class="diff-text" :class="state.diffTone" data-testid="diff-text">{{ state.diffText }}</span>
            <span class="diff-sum">
              合计 {{ formatMoney(state.total) }} / {{ formatMoney(state.expected) }}
            </span>
          </div>
          <NSpace justify="space-between" align="center">
            <NButton
              v-if="canClear"
              text
              type="error"
              :loading="clearing"
              data-testid="btn-clear"
              @click="showClearConfirm = true"
            >
              清空
            </NButton>
            <NText v-else depth="3" style="font-size: 12px">该行在当前维度下没有归集记录</NText>
            <NSpace :size="8">
              <NButton @click="close">取消</NButton>
              <NButton
                secondary
                :loading="saving"
                :disabled="!state.canSaveDraft"
                data-testid="btn-draft"
                @click="submit(false)"
              >
                保存草稿
              </NButton>
              <NButton
                type="primary"
                :loading="saving"
                :disabled="!state.canComplete"
                data-testid="btn-complete"
                @click="submit(true)"
              >
                完成归集
              </NButton>
            </NSpace>
          </NSpace>
        </div>
      </template>
    </NDrawerContent>
  </NDrawer>

  <NModal
    v-model:show="showClearConfirm"
    preset="card"
    title="确认清空归集"
    style="width: min(460px, 92vw)"
  >
    <NAlert type="warning" :bordered="false">
      该凭证行在此维度下的全部分配将被删除，无法恢复（后端仅保留审计日志）。
    </NAlert>
    <NSpace justify="end" class="mt-4">
      <NButton @click="showClearConfirm = false">取消</NButton>
      <NButton type="error" :loading="clearing" @click="confirmClear">确认清空</NButton>
    </NSpace>
  </NModal>
</template>

<style scoped>
.drawer-body { display: grid; gap: 4px; }
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }
.meta-grid {
  display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 4px 12px; font-size: 13px;
}
.meta-grid span { color: var(--solo-text-secondary); }
.ratio-note { margin-top: 8px; font-size: 12px; color: var(--solo-text-muted); }
.rows-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.empty { text-align: center; color: var(--solo-text-muted); padding: 20px; font-size: 13px; }
.alloc-row {
  display: grid; grid-template-columns: 1.3fr .8fr 1fr 1.4fr auto auto; gap: 8px;
  align-items: center; margin-bottom: 10px;
}
.drawer-footer { display: grid; gap: 10px; }
.diff-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 12px; border-radius: 8px; background: var(--solo-bg-page); font-size: 13px;
}
.diff-text.error { color: var(--solo-expense); font-weight: 600; }
.diff-text.success { color: var(--solo-primary); font-weight: 600; }
.diff-sum { color: var(--solo-text-secondary); font-variant-numeric: tabular-nums; }
@media (max-width: 760px) {
  .alloc-row { grid-template-columns: 1fr; padding: 12px; border: 1px solid var(--solo-border-light); border-radius: 8px; }
  .meta-grid { grid-template-columns: auto 1fr; }
}
</style>