<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { moneyPresentation } from '@/utils/accounting'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { formatDate } from '@/utils/format'

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
  <div class="recent-entries-section">
    <div class="section-header">
      <h3 class="section-title">最近交易记录</h3>
      <NButton text type="primary" class="view-all-btn" @click="router.push('/ledger')" v-if="recentEntries.length > 0">
        <span>查看全部</span>
        <i class="ri-arrow-right-s-line" />
      </NButton>
    </div>

    <div class="entries-card">
      <div v-if="recentEntries.length === 0" class="empty-state">
        <div class="empty-icon-wrapper">
          <i class="ri-folder-open-line" />
        </div>
        <p class="empty-text">暂无记账记录</p>
        <NButton size="small" type="primary" class="empty-btn" @click="router.push('/copilot')">立即开始</NButton>
      </div>

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
  </div>
</template>

<style scoped>
.recent-entries-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.section-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
  margin: 0;
  letter-spacing: 0.5px;
}
.view-all-btn {
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 2px;
}
.entries-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: none;
  overflow: hidden;
}
.entry-list {
  display: flex;
  flex-direction: column;
}
.entry-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 1px solid var(--solo-border-light);
}
.entry-row:last-child {
  border-bottom: none;
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
  gap: 14px;
  min-width: 0;
}
.category-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}
.entry-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.entry-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
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
  font-size: 15px;
  font-weight: 700;
  font-family: 'Outfit', 'Inter', sans-serif;
  font-variant-numeric: tabular-nums;
}
.row-arrow {
  font-size: 16px;
  color: var(--solo-text-muted);
  transition: transform 0.2s, color 0.2s;
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
  border-radius: 20px;
  font-weight: 600;
}
</style>
