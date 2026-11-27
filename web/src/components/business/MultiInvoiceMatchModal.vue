<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NCheckbox, NEmpty, NInput, NModal, NSpace, NTag } from 'naive-ui'
import { formatMoney } from '@/utils/format'
import {
  classifyMultiMatchCandidate,
  evaluateMultiMatchSelection,
  parseBankStatementDisplay,
} from '@/utils/closingWizard'

const props = defineProps<{
  show: boolean
  statement: any | null
  candidates: any[]
  initialSelectedIds?: string[]
  submitting?: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  confirm: [entryIds: string[]]
  openVoucher: [entryId: string]
}>()

type PoolFilter = 'all' | 'pending_invoice' | 'advancement'

const visible = computed({
  get: () => props.show,
  set: (val: boolean) => emit('update:show', val),
})

const poolFilter = ref<PoolFilter>('all')
const searchText = ref('')
const selectedIds = ref<string[]>([])

watch(
  () => [props.show, props.statement?.id, props.initialSelectedIds] as const,
  ([isOpen]) => {
    if (!isOpen) return
    const candidateIdSet = new Set(props.candidates.map((item) => String(item.id)))
    selectedIds.value = (props.initialSelectedIds || [])
      .map(String)
      .filter((id) => candidateIdSet.has(id))
    searchText.value = ''
    poolFilter.value = 'all'
  },
  { immediate: true },
)

const statementDisplay = computed(() =>
  parseBankStatementDisplay(props.statement?.description || ''),
)

const enrichedCandidates = computed(() =>
  props.candidates.map((item) => ({
    ...item,
    _pool: item.poolInfo || classifyMultiMatchCandidate(item),
  })),
)

const filterCounts = computed(() => {
  let pendingInvoice = 0
  let advancement = 0
  for (const item of enrichedCandidates.value) {
    if (item._pool?.poolKind === 'pending_invoice') pendingInvoice += 1
    else if (item._pool?.poolKind === 'advancement') advancement += 1
  }
  return {
    all: enrichedCandidates.value.length,
    pending_invoice: pendingInvoice,
    advancement,
  }
})

const filteredCandidates = computed(() => {
  const kw = searchText.value.trim().toLowerCase()
  return enrichedCandidates.value.filter((item) => {
    if (poolFilter.value === 'pending_invoice' && item._pool?.poolKind !== 'pending_invoice') return false
    if (poolFilter.value === 'advancement' && item._pool?.poolKind !== 'advancement') return false
    if (!kw) return true
    const haystack = [
      item.title,
      item.category,
      item.date || item.occurDate,
      item._pool?.poolLabel,
      formatMoney(item.amount),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(kw)
  })
})

const selectedSet = computed(() => new Set(selectedIds.value))

const selectedEntries = computed(() =>
  props.candidates.filter((item) => selectedSet.value.has(String(item.id))),
)

const evaluation = computed(() =>
  evaluateMultiMatchSelection(props.statement?.amount, selectedEntries.value),
)

function toggleEntry(id: string) {
  const key = String(id)
  if (selectedSet.value.has(key)) {
    selectedIds.value = selectedIds.value.filter((item) => item !== key)
  } else {
    selectedIds.value = [...selectedIds.value, key]
  }
}

function clearSelection() {
  selectedIds.value = []
}

function handleConfirm() {
  if (!evaluation.value.canConfirm || props.submitting) return
  emit('confirm', [...selectedIds.value])
}

function poolTagType(poolKind?: string): 'info' | 'warning' | 'default' {
  if (poolKind === 'advancement') return 'info'
  if (poolKind === 'pending_invoice') return 'warning'
  return 'default'
}
</script>

<template>
  <NModal
    v-model:show="visible"
    preset="card"
    title="多笔单据合并核销"
    style="width: min(680px, 95vw);"
    :mask-closable="!submitting"
  >
    <div v-if="statement" class="mm-container">
      <!-- 银行流水摘要头部 -->
      <div class="mm-stmt-card">
        <div class="mm-stmt-meta">
          <span class="mm-stmt-date">{{ String(statement.transaction_date || '').slice(0, 10) }}</span>
          <NTag
            size="tiny"
            :type="Number(statement.amount) >= 0 ? 'success' : 'error'"
            :bordered="false"
          >
            {{ Number(statement.amount) >= 0 ? '收款入账' : '付款支出' }}
          </NTag>
          <span class="mm-stmt-title" :title="statement.description">
            {{ statementDisplay.counterparty || statementDisplay.summary || statement.description }}
          </span>
        </div>
        <div class="mm-stmt-amount tabular-nums">
          {{ Number(statement.amount) >= 0 ? '+' : '−' }}¥{{ formatMoney(Math.abs(Number(statement.amount) || 0)) }}
        </div>
      </div>

      <!-- 筛选条 -->
      <div class="mm-toolbar">
        <div class="mm-tabs" role="tablist" aria-label="单据池筛选">
          <button
            type="button"
            role="tab"
            :aria-selected="poolFilter === 'all'"
            class="mm-tab"
            :class="{ 'mm-tab--active': poolFilter === 'all' }"
            @click="poolFilter = 'all'"
          >
            全部 ({{ filterCounts.all }})
          </button>
          <button
            v-if="filterCounts.pending_invoice > 0"
            type="button"
            role="tab"
            :aria-selected="poolFilter === 'pending_invoice'"
            class="mm-tab"
            :class="{ 'mm-tab--active': poolFilter === 'pending_invoice' }"
            @click="poolFilter = 'pending_invoice'"
          >
            待付款发票 ({{ filterCounts.pending_invoice }})
          </button>
          <button
            v-if="filterCounts.advancement > 0"
            type="button"
            role="tab"
            :aria-selected="poolFilter === 'advancement'"
            class="mm-tab"
            :class="{ 'mm-tab--active': poolFilter === 'advancement' }"
            @click="poolFilter = 'advancement'"
          >
            个人垫付 ({{ filterCounts.advancement }})
          </button>
        </div>
        <div class="mm-search">
          <label for="multi-match-search" class="sr-only">搜索单据标题、分类或金额</label>
          <NInput
            id="multi-match-search"
            v-model:value="searchText"
            size="small"
            clearable
            placeholder="搜索单据名称 / 金额 / 日期"
          />
        </div>
      </div>

      <!-- 候选单据多选列表（禁用 ul/li 包装复选框以防读屏计数重复） -->
      <div class="mm-list" role="group" aria-label="可合并核销的单据">
        <template v-if="filteredCandidates.length">
          <div
            v-for="item in filteredCandidates"
            :key="item.id"
            class="mm-row"
            :class="{ 'mm-row--selected': selectedSet.has(String(item.id)) }"
          >
            <label class="mm-row-check">
              <NCheckbox
                :checked="selectedSet.has(String(item.id))"
                @update:checked="() => toggleEntry(item.id)"
              />
              <span class="mm-row-main">
                <span class="mm-row-top">
                  <span class="mm-row-title">{{ item.title || item.category || '未命名单据' }}</span>
                  <NTag
                    v-if="item._pool?.poolLabel"
                    size="tiny"
                    :type="poolTagType(item._pool.poolKind)"
                    :bordered="false"
                  >
                    {{ item._pool.poolLabel }}
                  </NTag>
                </span>
                <span class="mm-row-sub">
                  <span>{{ String(item.date || item.occurDate || '').slice(0, 10) }}</span>
                  <span v-if="item.category">· {{ item.category }}</span>
                </span>
              </span>
              <span class="mm-row-amount tabular-nums">
                ¥{{ formatMoney(item.amount) }}
              </span>
            </label>
            <NButton
              text
              size="tiny"
              type="primary"
              class="mm-row-view"
              @click="emit('openVoucher', String(item.id))"
            >
              看凭证
            </NButton>
          </div>
        </template>
        <div v-else class="mm-empty">
          <NEmpty size="small" description="暂无符合条件的待核销单据" />
        </div>
      </div>

      <!-- 实时轧差汇总栏 -->
      <div class="mm-tieout" aria-live="polite">
        <div class="mm-tieout-metrics">
          <div class="mm-metric">
            <span class="mm-metric-label">流水金额</span>
            <strong class="mm-metric-val tabular-nums">¥{{ formatMoney(evaluation.statementAbs) }}</strong>
          </div>
          <div class="mm-metric">
            <span class="mm-metric-label">已选 {{ evaluation.selectedCount }} 笔合计</span>
            <strong class="mm-metric-val tabular-nums">¥{{ formatMoney(evaluation.selectedTotal) }}</strong>
          </div>
          <div class="mm-metric">
            <span class="mm-metric-label">差额</span>
            <strong
              class="mm-metric-val tabular-nums"
              :class="evaluation.amountMatches ? 'mm-val-ok' : 'mm-val-diff'"
            >
              <template v-if="evaluation.selectedCount === 0">—</template>
              <template v-else-if="Math.abs(evaluation.diff) < 0.005">¥0.00（完全吻合）</template>
              <template v-else>
                {{ evaluation.diff > 0 ? '多出 +' : '还差 −' }}¥{{ formatMoney(Math.abs(evaluation.diff)) }}
              </template>
            </strong>
          </div>
        </div>
        <NButton v-if="selectedIds.length > 0" text size="tiny" @click="clearSelection">
          清空重选
        </NButton>
      </div>

      <!-- 同质资金池与差额引导提示 -->
      <NAlert v-if="!evaluation.isHomogeneous" type="error" :bordered="false">
        {{ evaluation.poolError }}
      </NAlert>
      <NAlert
        v-else-if="evaluation.selectedCount > 0 && !evaluation.amountMatches"
        type="warning"
        :bordered="false"
      >
        当前已选合计与流水差额 ¥{{ formatMoney(Math.abs(evaluation.diff)) }}（超过 ±¥{{ evaluation.tolerance.toFixed(2) }} 容差）。请增减勾选单据，或先回列表补录/拆分手头差异后再核销。
      </NAlert>
      <div v-else-if="evaluation.selectedCount === 0" class="mm-hint">
        请勾选同一次打款汇总报销的多张发票或同一人垫付单据（仅支持同池合并）。
      </div>
    </div>

    <template #footer>
      <NSpace justify="end">
        <NButton :disabled="submitting" @click="visible = false">取消</NButton>
        <NButton
          type="primary"
          :disabled="!evaluation.canConfirm || submitting"
          :loading="submitting"
          @click="handleConfirm"
        >
          确认合并核销（{{ evaluation.selectedCount }} 笔）
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.mm-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mm-stmt-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
}

.mm-stmt-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.mm-stmt-date {
  font-size: 12px;
  color: var(--solo-text-muted);
  white-space: nowrap;
}

.mm-stmt-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mm-stmt-amount {
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
  white-space: nowrap;
}

.mm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.mm-tabs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.mm-tab {
  min-block-size: 28px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s, color 0.15s;
}

.mm-tab:focus-visible,
.mm-row-check:focus-within {
  outline: 2px solid var(--solo-primary);
  outline-offset: 2px;
}

.mm-tab--active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}

.mm-search {
  width: min(220px, 100%);
}

.mm-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 280px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 4px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
}

.mm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  min-block-size: 44px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  transition: border-color 0.15s, background-color 0.15s;
}

.mm-row--selected {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
}

.mm-row-check {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.mm-row-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.mm-row-top {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.mm-row-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mm-row-sub {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.mm-row-amount {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  white-space: nowrap;
}

.mm-row-view {
  flex-shrink: 0;
}

.mm-empty {
  padding: 24px 0;
}

.mm-tieout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 12px;
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
}

.mm-tieout-metrics {
  display: flex;
  align-items: baseline;
  gap: 20px;
  flex-wrap: wrap;
}

.mm-metric {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.mm-metric-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.mm-metric-val {
  font-size: 14px;
  color: var(--solo-text-primary);
}

.mm-val-ok {
  color: var(--solo-income);
}

.mm-val-diff {
  color: var(--solo-warning);
}

.mm-hint {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}
</style>
