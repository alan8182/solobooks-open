<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useEntryStore } from '@/stores/entry'
import { useAuthStore } from '@/stores/auth'
import { getAllExpenses } from '@/api/private-expense'
import type { PrivateExpense } from '@/api/private-expense'
import { formatMoney } from '@/utils/format'
import { SoloSkeleton } from '@/components/ui'
import { SoloErrorState } from '@/components/ui'

/** 失败后重试：store 会把失败原因写进 error，成功时自动清空 */
async function reloadEntries() {
  try {
    await entryStore.fetchEntries()
  } catch {
    // 失败原因已由 store 记录，页面会继续显示错误态
  }
}

const entryStore = useEntryStore()
const authStore = useAuthStore()
const selectedPeriod = ref<'year' | 'lastMonth'>('year')
const includePrivate = ref(false)
const privateExpenses = ref<PrivateExpense[]>([])
const privateLoading = ref(false)
const privateLoadFailed = ref(false)

async function loadPrivateExpenses() {
  privateLoading.value = true
  privateLoadFailed.value = false
  try {
    privateExpenses.value = await getAllExpenses()
  } catch {
    privateExpenses.value = []
    privateLoadFailed.value = true
    includePrivate.value = false
  } finally {
    privateLoading.value = false
  }
}

onMounted(loadPrivateExpenses)
watch(() => authStore.currentCompanyId, loadPrivateExpenses)

const periodMeta = computed(() => {
  const now = new Date()
  if (selectedPeriod.value === 'year') {
    const year = now.getFullYear()
    return {
      key: String(year),
      title: '本年经营概览',
      incomeLabel: '本年收入',
      expenseLabel: '本年支出',
      note: `数据来自 ${year} 年已确认经营记录`,
    }
  }

  const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  const year = lastMonth.getFullYear()
  const month = lastMonth.getMonth() + 1
  return {
    key: `${year}-${String(month).padStart(2, '0')}`,
    title: '上月经营概览',
    incomeLabel: '上月收入',
    expenseLabel: '上月支出',
    note: `数据来自 ${year} 年 ${month} 月已确认经营记录`,
  }
})

const periodEntries = computed(() => entryStore.activeEntries.filter((entry) => (
  entry.occurDate?.startsWith(periodMeta.value.key)
)))

const privatePeriodEntries = computed(() => privateExpenses.value.filter((entry) => {
  const isAlreadyRecorded = entry.invoice_status === 'substitute' && Boolean(entry.substitute_entry_id)
  return entry.occur_date?.startsWith(periodMeta.value.key) && !isAlreadyRecorded
}))

const privateIncome = computed(() => privatePeriodEntries.value
  .filter(entry => entry.direction === 'income')
  .reduce((sum, entry) => sum + Number(entry.amount || 0), 0))

const privateExpense = computed(() => privatePeriodEntries.value
  .filter(entry => entry.direction !== 'income' && entry.category !== '押金保证金')
  .reduce((sum, entry) => sum + Number(entry.amount || 0), 0))

const officialIncome = computed(() => periodEntries.value
  .filter(entry => entry.direction === 'income')
  .reduce((sum, entry) => sum + entry.amount, 0))

const officialExpense = computed(() => periodEntries.value
  .filter(entry => entry.direction === 'expense')
  .reduce((sum, entry) => sum + entry.amount, 0))

const periodIncome = computed(() => officialIncome.value + (includePrivate.value ? privateIncome.value : 0))
const periodExpense = computed(() => officialExpense.value + (includePrivate.value ? privateExpense.value : 0))

const periodProfit = computed(() => periodIncome.value - periodExpense.value)
</script>

<template>
  <div v-if="entryStore.loading && entryStore.activeEntries.length === 0" class="summary-panel-loading">
    <SoloSkeleton variant="stat" />
  </div>
  <SoloErrorState
    v-else-if="entryStore.error && entryStore.activeEntries.length === 0"
    title="经营概览加载失败"
    description="没能取到账目数据，这里不会显示 0，以免让你以为这个月真的一分没赚。"
    :detail="entryStore.error"
    @retry="reloadEntries"
  />
  <div v-else class="summary-panel">
    <div class="summary-intro">
      <div class="summary-heading">
        <span class="summary-kicker">{{ periodMeta.title }}</span>
        <div class="summary-controls">
          <div class="period-switch" role="group" aria-label="经营概览时间范围">
            <button
              type="button"
              :class="{ active: selectedPeriod === 'year' }"
              :aria-pressed="selectedPeriod === 'year'"
              @click="selectedPeriod = 'year'"
            >
              本年累计
            </button>
            <button
              type="button"
              :class="{ active: selectedPeriod === 'lastMonth' }"
              :aria-pressed="selectedPeriod === 'lastMonth'"
              @click="selectedPeriod = 'lastMonth'"
            >
              上月
            </button>
          </div>
          <button
            type="button"
            class="private-toggle"
            :class="{ active: includePrivate }"
            :aria-pressed="includePrivate"
            :disabled="privateLoading || privateLoadFailed"
            :title="privateLoadFailed ? '小私账数据加载失败' : '不含可收回押金，已关联正式账目的记录不会重复统计'"
            @click="includePrivate = !includePrivate"
          >
            <i :class="includePrivate ? 'ri-checkbox-circle-fill' : 'ri-add-circle-line'" />
            {{ privateLoading ? '加载中' : '含小私账' }}
          </button>
        </div>
      </div>
      <span class="summary-label">净利润</span>
      <strong class="summary-profit" :class="{ negative: periodProfit < 0 }">
        {{ periodProfit >= 0 ? '+' : '' }}{{ formatMoney(periodProfit) }}
      </strong>
      <span class="summary-note">
        {{ periodMeta.note }}{{ includePrivate ? '，含未转入正式账本的小私账' : '' }}
      </span>
    </div>
    <div class="stats-list">
      <div class="stat-row income">
        <span class="stat-label">{{ periodMeta.incomeLabel }}</span>
        <span class="stat-value" :title="formatMoney(periodIncome)">
          {{ formatMoney(periodIncome) }}
        </span>
      </div>
      <div class="stat-row expense">
        <span class="stat-label">{{ periodMeta.expenseLabel }}</span>
        <span class="stat-value" :title="formatMoney(periodExpense)">
          {{ formatMoney(periodExpense) }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary-panel {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(340px, 1.1fr);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-xl);
  background: #fff;
  overflow: hidden;
}
.summary-intro {
  padding: 22px 24px;
  border-right: 1px solid var(--solo-border-light);
  display: flex;
  flex-direction: column;
}
.summary-heading {
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}
.summary-kicker {
  color: var(--solo-text-primary);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.3;
  white-space: nowrap;
}
.summary-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.period-switch {
  display: inline-flex;
  flex: none;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--solo-border-light);
  border-radius: 999px;
  background: var(--solo-bg-soft);
}
.period-switch button {
  min-height: 26px;
  padding: 3px 9px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--solo-text-secondary);
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}
.period-switch button:hover {
  color: var(--solo-primary);
}
.period-switch button:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}
.period-switch button.active {
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  font-weight: 650;
  box-shadow: var(--solo-shadow-sm);
}
.private-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 30px;
  padding: 3px 9px;
  border: 1px solid var(--solo-border-light);
  border-radius: 999px;
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}
.private-toggle:hover:not(:disabled),
.private-toggle.active {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}
.private-toggle:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}
.private-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.summary-label,
.summary-note {
  color: var(--solo-text-muted);
  font-size: 12px;
}
.summary-profit {
  margin: 4px 0 10px;
  color: var(--solo-primary);
  font-size: 34px;
  font-weight: 700;
  letter-spacing: -1px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.summary-profit.negative {
  color: var(--solo-expense);
}
.stats-list {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}
.stat-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 20px;
  padding: 20px 26px;
  min-width: 0;
}
.stat-row + .stat-row {
  border-top: 1px solid var(--solo-border-light);
}
.stat-label {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.stat-value {
  min-width: 0;
  font-size: 24px;
  font-weight: 680;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
  letter-spacing: -0.4px;
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.income .stat-value {
  color: var(--solo-income);
}
.expense .stat-value {
  color: var(--solo-expense);
}
@media (max-width: 660px) {
  .summary-panel {
    grid-template-columns: 1fr;
  }
  .summary-intro {
    border-right: 0;
    border-bottom: 1px solid var(--solo-border-light);
  }
  .stat-row {
    padding: 16px 22px;
  }
}
</style>
