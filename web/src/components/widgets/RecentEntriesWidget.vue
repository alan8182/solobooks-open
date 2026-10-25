<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { moneyPresentation } from '@/utils/accounting'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { formatDate } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'
import { SoloErrorState } from '@/components/ui'

/** 首屏拉取失败时不要显示「暂无记账记录」——失败和无数据必须长得不一样 */
const failed = computed(() => Boolean(entryStore.error) && entryStore.activeEntries.length === 0)

async function reloadEntries() {
  try {
    await entryStore.fetchEntries()
  } catch {
    // 失败原因已由 store 记录
  }
}

const router = useRouter()
const entryStore = useEntryStore()

const recentEntries = computed(() => entryStore.activeEntries.slice(0, 5))

function directionLabel(dir: string, category = '') {
  const presentation = getEntryNaturePresentation(dir, category)
  return { ...presentation, bg: presentation.background }
}

// 金额展示：冲退类负金额按资金实际流向显示（+¥x 绿 / -¥x 红），色调跟随展示口径
function moneyDisplay(dir: string, category: string, amount: number | string) {
  const presentation = moneyPresentation(dir, category, amount)
  const toneDir = presentation.tone === 'neutral' ? dir : presentation.tone
  return { text: presentation.text, color: getEntryNaturePresentation(toneDir, category).color }
}
</script>

<template>
  <div class="entries-card">
    <header class="entries-header">
      <h2 class="entries-title">最近交易记录</h2>
      <button
        v-if="recentEntries.length > 0"
        type="button"
        class="view-all"
        @click="router.push('/ledger')"
      >
        查看全部
        <i class="ri-arrow-right-line" aria-hidden="true" />
      </button>
    </header>
      <SoloErrorState
        v-if="failed"
        title="最近交易加载失败"
        description="没能取到账目记录，稍后重试即可。"
        :detail="entryStore.error"
        @retry="reloadEntries"
      />
      <SoloEmpty
        v-else-if="recentEntries.length === 0"
        title="暂无记账记录"
        description="一句话、一张发票就能开始。"
        icon="ri-quill-pen-line"
      >
        <template #action>
          <NButton size="small" type="primary" @click="router.push('/copilot')">立即开始</NButton>
        </template>
      </SoloEmpty>

      <div v-else class="entry-list">
        <div
          v-for="entry in recentEntries"
          :key="entry.id"
          class="entry-row"
          @click="router.push(`/ledger/${entry.id}`)"
        >
          <div class="entry-left">
            <div class="category-icon" :style="{ background: directionLabel(entry.direction, entry.category).bg, color: directionLabel(entry.direction, entry.category).color }">
              {{ entry.category?.slice(0, 2) || '?' }}
            </div>
            <div class="entry-details">
              <span class="entry-title">{{ entry.title }}</span>
              <div class="entry-meta">
                <span class="meta-tag">{{ entry.category }}</span>
                <span class="meta-dot">•</span>
                <span class="meta-date">{{ formatDate(entry.occurDate || entry.created_at || '') }}</span>
              </div>
            </div>
          </div>
          <div class="entry-right">
            <span class="entry-amount" :style="{ color: moneyDisplay(entry.direction, entry.category, entry.amount).color }">
              {{ moneyDisplay(entry.direction, entry.category, entry.amount).text }}
            </span>
            <i class="ri-arrow-right-s-line row-arrow" />
          </div>
        </div>
      </div>
  </div>
</template>

<style scoped>
.entries-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: none;
  overflow: hidden;
}
.entries-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px 12px;
}
.entries-title {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 14.5px;
  font-weight: 650;
  line-height: 1.3;
}
.view-all {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-primary);
  font: inherit;
  font-size: 11.5px;
  font-weight: 650;
  cursor: pointer;
}
.view-all:hover {
  background: var(--solo-bg-blue-soft);
}
.view-all:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}
.entry-list {
  display: flex;
  flex-direction: column;
}
.entry-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  border-top: 1px solid var(--solo-border-light);
  cursor: pointer;
  transition: background-color 160ms ease-out;
}
.entry-row:hover {
  background: var(--solo-bg-soft);
}
.entry-row:hover .row-arrow {
  transform: translateX(2px);
  color: var(--solo-text-secondary);
}
.entry-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.category-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}
.entry-details {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.entry-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--solo-text-secondary);
}
.meta-tag {
  font-weight: 500;
}
.meta-dot {
  color: var(--solo-text-muted);
}
.meta-date {
  color: var(--solo-text-muted);
}
.entry-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.entry-amount {
  font-size: 13px;
  font-weight: 700;
  font-family: 'Outfit', 'Inter', sans-serif;
  font-variant-numeric: tabular-nums;
}
.row-arrow {
  font-size: 14px;
  color: var(--solo-text-muted);
  transition: transform 160ms ease-out, color 160ms ease-out;
}
.empty-state {
  text-align: center;
  padding: 40px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
.empty-icon-wrapper {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--solo-border-light);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: var(--solo-text-muted);
}
.empty-text {
  font-size: 14px;
  color: var(--solo-text-secondary);
  font-weight: 500;
  margin: 0;
}
.empty-btn {
  border-radius: 12px;
  font-weight: 600;
}
</style>
