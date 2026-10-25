<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'

export type BadgeTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'brand'

const props = withDefaults(
  defineProps<{
    tone?: BadgeTone
    dot?: boolean
    size?: 'small' | 'medium'
    /**
     * 无底色形态：只留小圆点 + 文字。
     * 数据表里一屏几十行时用它——每一行都套一个彩色胶囊，正常态会把
     * "一切正常"重复几十遍，真正异常的那一行反而看不出差别。
     */
    plain?: boolean
  }>(),
  {
    tone: 'neutral',
    dot: true,
    size: 'small',
    plain: false,
  },
)

const toneClass = computed(() => `badge-${props.tone}`)
const sizeClass = computed(() => `badge-${props.size}`)
</script>

<template>
  <span class="solo-status-badge" :class="[toneClass, sizeClass, { 'badge-plain': plain }]">
    <span v-if="dot" class="badge-dot" aria-hidden="true" />
    <span class="badge-content">
      <slot />
    </span>
  </span>
</template>

<style scoped>
.solo-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 999px;
  font-weight: 550;
  line-height: 1;
  white-space: nowrap;
  vertical-align: middle;
  transition: all 0.15s ease-out;
}

.badge-small {
  padding: 3px 8px;
  font-size: 11px;
}

.badge-medium {
  padding: 5px 10px;
  font-size: 12px;
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* 语义色彩体系：低饱和底色 + 墨色/语义字色 */
.badge-success {
  background-color: var(--solo-income-bg, #eaf7f1);
  color: var(--solo-income, #0f7354);
}
.badge-success .badge-dot {
  background-color: var(--solo-income, #0f7354);
}

.badge-warning {
  background-color: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
}
.badge-warning .badge-dot {
  background-color: var(--solo-warning, #a05c10);
}

.badge-danger {
  background-color: var(--solo-expense-bg, #fbeeee);
  color: var(--solo-expense, #bd3d3d);
}
.badge-danger .badge-dot {
  background-color: var(--solo-expense, #bd3d3d);
}

.badge-info {
  background-color: var(--solo-info-bg, #edf3ff);
  color: var(--solo-info, #1747b0);
}
.badge-info .badge-dot {
  background-color: var(--solo-info, #1747b0);
}

.badge-brand {
  background-color: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}
.badge-brand .badge-dot {
  background-color: var(--solo-primary, #1747b0);
}

.badge-neutral {
  background-color: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-secondary, #586474);
}
.badge-neutral .badge-dot {
  background-color: var(--solo-text-muted, #636e7c);
}

/* plain：去掉胶囊底色与内边距，只保留点 + 字。
   两级选择器已足够压过 .badge-neutral / .badge-small，不用 !important */
.solo-status-badge.badge-plain {
  padding: 0;
  background-color: transparent;
  border-radius: 0;
  font-weight: 500;
}
.solo-status-badge.badge-plain.badge-neutral {
  color: var(--solo-text-secondary, #586474);
}
</style>
