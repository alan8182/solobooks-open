<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpin, useMessage, useDialog, NEmpty, NCheckbox } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getPeriodCloseChecks, batchPostEntries } from '@/api/accounting'
import { formatMoney, formatDate } from '@/utils/format'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const dialog = useDialog()
const entryStore = useEntryStore()

const loading = ref(true)
const batchPosting = ref(false)
const selectedEntryIds = ref<string[]>([])
const targetMonth = computed(() => typeof route.query.month === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(route.query.month) ? route.query.month : '')
const issues = ref<Array<{ entryId: string; title: string; date: string; reasons: string[] }>>([])
const scopedEntries = computed(() => entryStore.activeEntries.filter(e => !targetMonth.value || String(e.occurDate || '').startsWith(targetMonth.value)))
const reasonLabels: Record<string, string> = { pending_post: '待过账', pending_review: '待复核', missing_source: '缺少有效来源', missing_rule: '缺少记账规则' }
function statusLabel(status: string | undefined) {
  return ({ draft: '待过账', pending_review: '待复核', posted: '已过账', reversed: '已冲销' } as Record<string, string>)[status || ''] || '状态待核实'
}

const voucheredEntries = computed(() =>
  scopedEntries.value.filter((e) => ['posted', 'reversed'].includes(e.voucherStatus || '') && e.voucherRef),
)

const unvoucheredEntries = computed(() =>
  scopedEntries.value.filter((e) => !['posted', 'reversed'].includes(e.voucherStatus || '') || !e.voucherRef),
)

const isAllSelected = computed(() => {
  if (!unvoucheredEntries.value.length) return false
  return unvoucheredEntries.value.every(e => selectedEntryIds.value.includes(e.id))
})

const isIndeterminate = computed(() => {
  const count = unvoucheredEntries.value.filter(e => selectedEntryIds.value.includes(e.id)).length
  return count > 0 && count < unvoucheredEntries.value.length
})

const selectedCount = computed(() => {
  return unvoucheredEntries.value.filter(e => selectedEntryIds.value.includes(e.id)).length
})

function handleSelectAll(checked: boolean) {
  if (checked) {
    selectedEntryIds.value = unvoucheredEntries.value.map(e => e.id)
  } else {
    selectedEntryIds.value = []
  }
}

function toggleSelect(id: string, checked: boolean) {
  if (checked) {
    if (!selectedEntryIds.value.includes(id)) {
      selectedEntryIds.value.push(id)
    }
  } else {
    selectedEntryIds.value = selectedEntryIds.value.filter(x => x !== id)
  }
}

watch(scopedEntries, () => {
  const validIds = new Set(unvoucheredEntries.value.map(e => e.id))
  selectedEntryIds.value = selectedEntryIds.value.filter(id => validIds.has(id))
})

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
  const ids = mode === 'selected' ? selectedEntryIds.value : unvoucheredEntries.value.map(e => e.id)
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
      dialog.warning({
        title: '部分凭证未过账说明',
        content: () => {
          return h('div', { style: 'max-height: 240px; overflow-y: auto;' }, [
            h('p', { style: 'margin-bottom: 8px;' }, `以下 ${res.failedCount} 笔凭证未过账，请核对原因：`),
            h('ul', { style: 'padding-left: 20px; font-size: 13px;' }, res.failedEntries.map(f =>
              h('li', { style: 'margin-bottom: 4px;' }, `${f.title}：${f.reason}`)
            )),
          ])
        },
        positiveText: '知道了',
      })
    } else if (res.failedCount > 0) {
      message.error(`批量过账未完成，${res.failedCount} 笔凭证未通过核验`)
    }
    selectedEntryIds.value = []
    await loadEntries()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '批量过账请求失败，请稍后重试')
  } finally {
    batchPosting.value = false
  }
}

onMounted(loadEntries)
watch(targetMonth, loadEntries)
</script>

<template>
  <div class="voucher-page">
    <div class="page-header">
      <div style="display: flex; align-items: center; gap: 8px;">
        <NButton v-if="route.query.from" text size="small" @click="router.push(String(route.query.from))">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回结账向导
        </NButton>
        <h2>凭证管理</h2>
      </div>
      <label>月份 <input type="month" :value="targetMonth" @change="router.replace({ path: '/vouchers', query: { month: ($event.target as HTMLInputElement).value, from: route.query.from } })" /></label>
    </div>

    <NSpin :show="loading">
      <p class="page-hint">逐笔查看来源和分录后确认过账，亦可全选或批量过账。已对账不代表已过账。</p>
      <div v-if="issues.length" class="section mb-4">
        <div class="section-label">{{ targetMonth }} 结账待处理记录 ({{ issues.length }})</div>
        <NCard v-for="issue in issues" :key="issue.entryId" :bordered="false" class="entry-card" @click="openEntry(issue.entryId)">
          <div class="entry-info"><span class="entry-title">{{ issue.title }}</span><NButton size="tiny" @click.stop="openEntry(issue.entryId)">查看与处理</NButton></div>
          <div class="entry-meta"><span>{{ issue.date }}</span><NTag v-for="reason in issue.reasons" :key="reason" size="tiny" type="warning">{{ reasonLabels[reason] || reason }}</NTag></div>
          <p v-if="issue.reasons.includes('missing_source')">请核对原始业务记录及关联；补齐来源后重新检查。</p>
        </NCard>
      </div>
      <!-- Unvouchered entries -->
      <div v-if="unvoucheredEntries.length > 0" class="section mb-4">
        <div class="section-label-row">
          <div class="batch-header-left">
            <NCheckbox
              :checked="isAllSelected"
              :indeterminate="isIndeterminate"
              @update:checked="handleSelectAll"
            >
              <span class="section-label">待过账 / 待复核凭证 ({{ unvoucheredEntries.length }})</span>
            </NCheckbox>
            <span v-if="selectedCount > 0" class="selected-badge">已勾选 {{ selectedCount }} 笔</span>
          </div>
          <div class="batch-actions">
            <NButton
              v-if="selectedCount > 0"
              size="small"
              type="primary"
              :loading="batchPosting"
              @click="promptBatchPost('selected')"
            >
              批量过账所选 ({{ selectedCount }})
            </NButton>
            <NButton
              v-else
              size="small"
              type="primary"
              ghost
              :loading="batchPosting"
              @click="promptBatchPost('all')"
            >
              一键过账全部 ({{ unvoucheredEntries.length }})
            </NButton>
          </div>
        </div>
        <div
          v-for="entry in unvoucheredEntries"
          :key="entry.id"
          class="voucher-row"
          @click="openEntry(entry.id)"
        >
          <div class="checkbox-col" @click.stop>
            <NCheckbox
              :checked="selectedEntryIds.includes(entry.id)"
              @update:checked="toggleSelect(entry.id, $event)"
            />
          </div>
          <div class="entry-main">
            <span class="entry-title">{{ entry.title }}</span>
            <span class="entry-sub">{{ entry.category }} · {{ formatDate(entry.occurDate || entry.created_at || '') }} · {{ statusLabel(entry.voucherStatus) }}</span>
          </div>
          <div class="entry-right">
            <span class="entry-amount">{{ formatMoney(entry.amount) }}</span>
          </div>
        </div>
      </div>

      <!-- Vouchered entries -->
      <div v-if="voucheredEntries.length > 0" class="section">
        <div class="section-label">已过账 / 已冲销凭证 ({{ voucheredEntries.length }})</div>
        <div
          v-for="entry in voucheredEntries"
          :key="entry.id"
          class="voucher-row"
          :class="{ 'is-reversed': entry.voucherStatus === 'reversed' }"
          @click="router.push(`/vouchers/${entry.id}`)"
        >
          <div class="entry-main">
            <span class="entry-title">{{ entry.title }}</span>
            <span class="entry-sub">
              {{ entry.category }} · {{ formatDate(entry.occurDate || entry.created_at || '') }}
              <template v-if="entry.voucherStatus === 'reversed'"> · 已冲销</template>
            </span>
          </div>
          <div class="entry-right">
            <span class="entry-amount">{{ formatMoney(entry.amount) }}</span>
            <span class="voucher-ref">{{ entry.voucherRef }}</span>
          </div>
        </div>
      </div>

      <div v-if="scopedEntries.length === 0 && !loading" class="empty-section">
        <NEmpty description="暂无数据" />
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.voucher-page {
  max-width: 720px;
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
.section-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.batch-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.selected-badge {
  font-size: 12px;
  color: var(--solo-primary);
  background: var(--solo-primary-50, #f0f7ff);
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.batch-actions {
  display: flex;
  align-items: center;
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
.checkbox-col {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  flex-shrink: 0;
}

/* 紧凑行式凭证行：左标题/摘要行 + 右金额列（两区块统一结构） */
.voucher-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #fff;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  cursor: pointer;
  transition: box-shadow 0.15s ease;
}
.voucher-row:hover {
  box-shadow: var(--solo-shadow);
}
.entry-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.entry-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entry-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.entry-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  flex-shrink: 0;
}
.entry-amount {
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}
.voucher-ref {
  font-size: 11px;
  color: var(--solo-primary);
  font-family: 'Inter', monospace;
}
.is-reversed {
  opacity: 0.55;
}
.is-reversed .entry-amount {
  text-decoration: line-through;
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
.empty-section {
  margin-top: 48px;
}
</style>
