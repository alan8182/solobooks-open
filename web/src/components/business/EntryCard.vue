<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import type { Entry } from '@/api/types'
import { moneyPresentation } from '@/utils/accounting'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  entry: Entry
}>()

const emit = defineEmits<{
  click: []
}>()

const config = computed(() => {
  const presentation = getEntryNaturePresentation(props.entry.direction, props.entry.category)
  return { ...presentation, bg: presentation.background }
})

// 金额展示：冲退类负金额按资金实际流向显示（+¥x 绿 / -¥x 红），色调跟随展示口径
const money = computed(() => {
  const presentation = moneyPresentation(props.entry.direction, props.entry.category, props.entry.amount)
  const toneDir = presentation.tone === 'neutral' ? props.entry.direction : presentation.tone
  return { text: presentation.text, color: getEntryNaturePresentation(toneDir, props.entry.category).color }
})

const categoryIcon = computed(() => {
  return (props.entry.category || '?').slice(0, 2)
})
</script>

<template>
  <div class="entry-card" @click="emit('click')">
    <div class="entry-icon" :style="{ background: config.bg, color: config.color }">
      {{ categoryIcon }}
    </div>

    <div class="entry-body">
      <div class="entry-title">{{ entry.title || '未命名' }}</div>
      <div class="entry-meta">
        <NTag size="tiny" :bordered="false" :color="{ textColor: config.color, borderColor: 'transparent', color: config.bg }">
          {{ config.label }}
        </NTag>
        <span>{{ entry.category }}</span>
        <span v-if="entry.occurDate">· {{ formatDate(entry.occurDate) }}</span>
      </div>
    </div>

    <div class="entry-amount" :style="{ color: money.color }">
      {{ money.text }}
    </div>
  </div>
</template>

<style scoped>
.entry-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: #fff;
  border-radius: var(--solo-radius);
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: var(--solo-shadow-sm);
}
.entry-card:hover {
  box-shadow: var(--solo-shadow);
  transform: translateX(2px);
}
.entry-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}
.entry-body {
  flex: 1;
  min-width: 0;
}
.entry-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--solo-text-primary);
  margin-bottom: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
  flex-wrap: wrap;
}
.entry-amount {
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}
</style>
