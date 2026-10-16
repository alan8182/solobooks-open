<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, reactive } from 'vue'
import { NButton, NCheckbox, NInput, NTag, useMessage } from 'naive-ui'
import { approveAgentAction, rejectAgentAction, type AgentActionItem } from '@/api/agent'

interface VoucherRow {
  direction?: string
  amount?: number
  title?: string
  category?: string
  occurDate?: string
  payer?: string
  merchant?: string
  note?: string
}

const props = defineProps<{
  action: AgentActionItem
}>()

const emit = defineEmits<{
  (e: 'resolved'): void
}>()

const message = useMessage()
const busy = reactive({ approving: false, rejecting: false })
const local = reactive({
  status: props.action.status,
  result: props.action.result,
})

const payload = computed(() => props.action.payload || {})
const isBatch = computed(() => Array.isArray(payload.value.items) && payload.value.items.length > 0)
const isMatch = computed(() => props.action.actionType === 'propose_match')
const isAccrual = computed(() => props.action.actionType === 'propose_accrual')

const matchData = computed(() => ({
  statement: (payload.value.statement || {}) as Record<string, unknown>,
  entries: (payload.value.entries || []) as Record<string, unknown>[],
}))
const accrualData = computed(() => ({
  kind: String(payload.value.kind || ''),
  month: String(payload.value.month || ''),
  preview: (payload.value.preview || {}) as Record<string, unknown>,
}))
const accrualKindText = computed(() => (accrualData.value.kind === 'depreciation' ? '折旧计提' : '工资计提'))

// 批量清单：可勾选排除、可改分类
const rows = reactive<{ row: VoucherRow; include: boolean; editedCategory: string }[]>([])
if (isBatch.value) {
  for (const raw of payload.value.items as VoucherRow[]) {
    rows.push({ row: raw, include: true, editedCategory: String(raw.category ?? '') })
  }
}
// 单笔模式：分类同样可改
const singleEditedCategory = reactive({ value: '' })
if (!isBatch.value) {
  singleEditedCategory.value = String(payload.value.category ?? '')
}

const DIRECTION_TEXT: Record<string, string> = {
  income: '收入',
  expense: '支出',
  owner_transfer: '老板投入',
}
const PAYER_TEXT: Record<string, string> = {
  company_account: '对公',
  personal: '私账',
}
const STATUS_TAG: Record<string, { text: string; type: 'info' | 'success' | 'default' | 'warning' }> = {
  pending: { text: '待确认', type: 'warning' },
  approved: { text: '已入账', type: 'success' },
  rejected: { text: '已放弃', type: 'default' },
  expired: { text: '已过期', type: 'default' },
}

function amountText(v: unknown) {
  const amount = Number(v ?? 0)
  return amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function rowAmount(row: VoucherRow) {
  const amount = Number(row.amount ?? 0)
  return `${row.direction === 'income' ? '+' : '−'}¥ ${amountText(Math.abs(amount))}`
}

const includedIndexes = computed(() =>
  rows.map((r, i) => (r.include ? i : -1)).filter((i) => i >= 0),
)

async function doApprove(opts: Parameters<typeof approveAgentAction>[1] = {}) {
  busy.approving = true
  try {
    const resp = await approveAgentAction(props.action.id, opts)
    local.status = 'approved'
    local.result = resp.result || null
    emit('resolved')
  } catch (err) {
    message.error(err instanceof Error ? err.message : '确认失败')
  } finally {
    busy.approving = false
  }
}

async function approveSingle() {
  if (isMatch.value || isAccrual.value) return doApprove()
  const edits: Record<string, { category?: string }> = {}
  if (singleEditedCategory.value && singleEditedCategory.value !== payload.value.category) {
    edits['0'] = { category: singleEditedCategory.value }
  }
  await doApprove(Object.keys(edits).length ? { edits } : {})
}

async function approveBatch() {
  const excludeIndexes = rows.map((r, i) => (r.include ? -1 : i)).filter((i) => i >= 0)
  if (excludeIndexes.length === rows.length) {
    message.warning('请至少保留一笔，或直接放弃整单')
    return
  }
  const edits: Record<string, { category?: string }> = {}
  rows.forEach((r, i) => {
    if (r.include && r.editedCategory && r.editedCategory !== r.row.category) {
      edits[String(i)] = { category: r.editedCategory }
    }
  })
  await doApprove({
    excludeIndexes: excludeIndexes.length ? excludeIndexes : undefined,
    edits: Object.keys(edits).length ? edits : undefined,
  })
}

async function reject() {
  busy.rejecting = true
  try {
    await rejectAgentAction(props.action.id)
    local.status = 'rejected'
    emit('resolved')
  } catch (err) {
    message.error(err instanceof Error ? err.message : '操作失败')
  } finally {
    busy.rejecting = false
  }
}

// 批量结果按行展示
const batchResults = computed(() => {
  const items = (local.result as Record<string, unknown> | null)?.items
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : null
})
function resultLine(r: Record<string, unknown>): string {
  if (r.status === 'booked') return `已入账 ${(r.voucherRef as string) || ''}`.trim()
  if (r.status === 'excluded') return '已排除'
  if (r.status === 'failed') return `失败：${(r.error as string) || ''}`
  return ''
}

function summarizeAccrualResult(result: unknown): string {
  const accrual = (result as Record<string, unknown>)?.accrual as Record<string, unknown> | undefined
  const summary = (accrual?.summary || {}) as Record<string, unknown>
  const parts: string[] = []
  for (const key of ['processed_count', 'processedCount', 'booked_count', 'total_depreciation', 'total']) {
    if (summary[key] != null) parts.push(`${key}: ${summary[key]}`)
  }
  const text = parts.length ? `计提完成（${parts.join('，')}）` : '计提已执行'
  return text
}

const approveText = computed(() => {
  if (isMatch.value) return '确认对账'
  if (isAccrual.value) return '确认计提'
  if (isBatch.value) return `确认入账（${includedIndexes.value.length} 笔）`
  return '确认入账'
})
</script>

<template>
  <div class="agent-action-card" :class="local.status">
    <div class="card-head">
      <span class="card-title">
        {{
          isMatch
            ? '银行流水对账建议'
            : isAccrual
              ? `${accrualKindText}（${accrualData.month}）`
              : isBatch
                ? `批量记账（${(payload.items as VoucherRow[]).length} 笔）`
                : action.title || '记账草稿'
        }}
      </span>
      <NTag size="small" :type="STATUS_TAG[local.status]?.type || 'default'" :bordered="false">
        {{ STATUS_TAG[local.status]?.text || local.status }}
      </NTag>
    </div>

    <!-- 对账建议 -->
    <template v-if="isMatch">
      <div class="match-block">
        <div class="match-row">
          <span class="match-side">流水</span>
          <span class="match-main">
            {{ matchData.statement.description || '—' }}
            <span class="muted">{{ matchData.statement.date }}</span>
          </span>
          <span class="row-amount">¥ {{ amountText(matchData.statement.amount) }}</span>
        </div>
        <div class="match-row" v-for="(e, i) in matchData.entries" :key="i">
          <span class="match-side">账目</span>
          <span class="match-main">{{ e.title || e.id }}</span>
          <span class="row-amount is-income">¥ {{ amountText(e.amount) }}</span>
        </div>
      </div>
      <div class="card-result" v-if="local.status === 'approved'">
        {{ (local.result as any)?.match?.status === 'matched' ? '已对账 ✓' : '已处理' }}
      </div>
    </template>

    <!-- 计提确认 -->
    <template v-else-if="isAccrual">
      <div class="card-fields">
        <div class="field">
          <span class="field-label">计提类型</span>
          <span class="field-value">{{ accrualKindText }}</span>
        </div>
        <div class="field">
          <span class="field-label">月份</span>
          <span class="field-value">{{ accrualData.month }}</span>
        </div>
        <div class="field" v-if="accrualData.kind === 'depreciation'">
          <span class="field-label">待计提</span>
          <span class="field-value amount">
            {{ accrualData.preview.pendingCount ?? 0 }} 项 / ¥ {{ amountText(accrualData.preview.pendingTotal ?? 0) }}
          </span>
        </div>
        <div class="field" v-else>
          <span class="field-label">工资表</span>
          <span class="field-value">{{ accrualData.preview.message || (accrualData.preview.sheetExists ? '已创建' : '未创建') }}</span>
        </div>
      </div>
      <div class="card-result" v-if="local.status === 'approved' && local.result">
        {{ summarizeAccrualResult(local.result) }}
      </div>
    </template>

    <!-- 批量清单 -->
    <div v-else-if="isBatch" class="batch-list">
      <div v-for="(r, i) in rows" :key="i" class="batch-row">
        <div class="row-head">
          <NCheckbox
            v-if="local.status === 'pending'"
            v-model:checked="r.include"
            size="small"
          />
          <span v-else class="row-dot" :class="String(batchResults?.[i]?.status || 'booked')" />
          <span class="row-title">{{ r.row.title }}</span>
          <span class="row-amount" :class="r.row.direction === 'income' ? 'is-income' : ''">
            {{ rowAmount(r.row) }}
          </span>
        </div>
        <div class="row-fields">
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="r.editedCategory"
            size="tiny"
            placeholder="分类"
            class="row-category-input"
          />
          <span v-else class="row-field">
            {{ (batchResults?.[i]?.category as string) || r.row.category }}
          </span>
          <span class="row-field muted" v-if="r.row.occurDate">{{ r.row.occurDate }}</span>
          <span class="row-field muted" v-if="r.row.payer">{{ PAYER_TEXT[r.row.payer] || r.row.payer }}</span>
          <span class="row-result" v-if="batchResults && batchResults[i]">
            {{ resultLine(batchResults[i]) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 单笔草稿 -->
    <template v-else>
      <div class="card-fields">
        <div class="field">
          <span class="field-label">方向</span>
          <span class="field-value" :class="String(payload.direction) === 'income' ? 'is-income' : ''">
            {{ DIRECTION_TEXT[String(payload.direction)] || payload.direction || '—' }}
          </span>
        </div>
        <div class="field">
          <span class="field-label">金额</span>
          <span class="field-value amount">¥ {{ amountText(payload.amount) }}</span>
        </div>
        <div class="field">
          <span class="field-label">分类（可修改）</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="singleEditedCategory.value"
            size="tiny"
            placeholder="分类"
          />
          <span v-else class="field-value">{{ payload.category || '—' }}</span>
        </div>
        <div class="field">
          <span class="field-label">日期</span>
          <span class="field-value">{{ payload.occurDate || '—' }}</span>
        </div>
        <div class="field">
          <span class="field-label">账户</span>
          <span class="field-value">{{ PAYER_TEXT[String(payload.payer)] || payload.payer || '对公' }}</span>
        </div>
        <div class="field" v-if="payload.merchant">
          <span class="field-label">对方</span>
          <span class="field-value">{{ payload.merchant }}</span>
        </div>
      </div>
    </template>

    <div class="card-evidence" v-if="action.evidence?.note">
      {{ action.evidence.note }}
    </div>

    <div class="card-result" v-if="local.status === 'approved' && !isBatch && !isMatch && !isAccrual && local.result">
      已入账，凭证号 {{ (local.result as Record<string, unknown>).voucherRef || '—' }}
    </div>
    <div class="card-result" v-else-if="local.status === 'approved' && isBatch && !batchResults?.some((r) => r.status === 'booked')">
      未入账任何一笔
    </div>

    <div class="card-actions" v-if="local.status === 'pending'">
      <NButton size="small" quaternary :disabled="busy.approving" @click="reject">
        放弃
      </NButton>
      <NButton
        size="small"
        type="primary"
        :loading="busy.approving"
        @click="isBatch ? approveBatch() : approveSingle()"
      >
        {{ approveText }}
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.agent-action-card {
  border: 1px solid var(--solo-border, #dde3ea);
  border-left: 3px solid var(--solo-primary, #165dff);
  border-radius: var(--solo-radius, 8px);
  padding: 11px 13px;
  background: var(--solo-bg-card, #ffffff);
  font-size: 13px;
  transition: all 0.2s ease;
}
.agent-action-card.approved {
  border-left-color: var(--solo-income, #12805c);
  background: #fafcfb;
}
.agent-action-card.rejected {
  border-left-color: var(--solo-text-muted, #8a95a3);
  opacity: 0.8;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.card-title {
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.card-fields {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.field-label {
  font-size: 11px;
  color: var(--solo-text-muted, #8a95a3);
}
.field-value {
  color: var(--solo-text-primary, #182230);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.field-value.amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.field-value.is-income,
.row-amount.is-income {
  color: var(--solo-income, #12805c);
}

/* 批量清单 */
.batch-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}
.batch-row {
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 6px);
  padding: 6px 8px;
  background: var(--solo-bg-page, #f6f7f9);
}
.row-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--solo-income, #12805c);
  flex-shrink: 0;
}
.row-dot.excluded {
  background: var(--solo-text-muted, #8a95a3);
}
.row-dot.failed {
  background: var(--solo-expense, #c74444);
}
.row-title {
  flex: 1;
  color: var(--solo-text-primary, #182230);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.row-fields {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  padding-left: 24px;
  flex-wrap: wrap;
}
.row-category-input {
  width: 130px;
}
.row-field {
  font-size: 12px;
  color: var(--solo-text-primary, #182230);
}
.row-field.muted {
  color: var(--solo-text-muted, #8a95a3);
}
.row-result {
  font-size: 12px;
  color: var(--solo-income, #12805c);
}

.card-evidence {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-text-muted, #8a95a3);
}
.card-result {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-income, #12805c);
}
.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

/* 对账建议 */
.match-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.match-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.match-side {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--solo-text-muted);
  border: 1px solid var(--solo-border, #e5e6eb);
  border-radius: 4px;
  padding: 1px 6px;
}
.match-main {
  flex: 1;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted {
  color: var(--solo-text-muted);
  font-size: 12px;
}
</style>
