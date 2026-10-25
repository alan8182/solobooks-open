<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
export type SkeletonVariant = 'card' | 'table' | 'stat' | 'text' | 'rect'

withDefaults(
  defineProps<{
    variant?: SkeletonVariant
    rows?: number
    height?: string
    width?: string
  }>(),
  {
    variant: 'text',
    rows: 3,
    height: '16px',
    width: '100%',
  },
)
</script>

<template>
  <div class="solo-skeleton-wrap">
    <!-- Stat 骨架：用于经营概览/看板指标 -->
    <div v-if="variant === 'stat'" class="skeleton-stat">
      <div class="skeleton-line shimmer title-line" />
      <div class="skeleton-line shimmer number-line" />
      <div class="skeleton-line shimmer desc-line" />
    </div>

    <!-- Card 骨架：用于工作台卡片/任务卡片 -->
    <div v-else-if="variant === 'card'" class="skeleton-card">
      <div class="skeleton-card-header">
        <div class="skeleton-line shimmer title-line" />
        <div class="skeleton-line shimmer badge-line" />
      </div>
      <div class="skeleton-card-body">
        <div v-for="i in rows" :key="i" class="skeleton-line shimmer content-line" />
      </div>
    </div>

    <!-- Table 骨架：用于表格加载 -->
    <div v-else-if="variant === 'table'" class="skeleton-table">
      <div class="skeleton-table-header shimmer" />
      <div v-for="r in rows" :key="r" class="skeleton-table-row">
        <div class="skeleton-line shimmer col-sm" />
        <div class="skeleton-line shimmer col-md" />
        <div class="skeleton-line shimmer col-lg" />
        <div class="skeleton-line shimmer col-sm" />
      </div>
    </div>

    <!-- Rect 矩形骨架 -->
    <div
      v-else-if="variant === 'rect'"
      class="skeleton-rect shimmer"
      :style="{ width, height }"
    />

    <!-- Text 纯文本多行骨架 -->
    <div v-else class="skeleton-text-group">
      <div
        v-for="i in rows"
        :key="i"
        class="skeleton-line shimmer"
        :style="{
          width: i === rows ? '60%' : '100%',
          height,
        }"
      />
    </div>
  </div>
</template>

<style scoped>
.solo-skeleton-wrap {
  width: 100%;
}

.shimmer {
  position: relative;
  overflow: hidden;
  background-color: var(--solo-bg-soft, #f1f4f8);
  border-radius: var(--solo-radius-sm, 4px);
}

.shimmer::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  transform: translateX(-100%);
  background-image: linear-gradient(
    90deg,
    rgba(255, 255, 255, 0) 0,
    rgba(255, 255, 255, 0.45) 50%,
    rgba(255, 255, 255, 0) 100%
  );
  animation: shimmer-wave 1.6s infinite ease-in-out;
}

@keyframes shimmer-wave {
  100% {
    transform: translateX(100%);
  }
}

.skeleton-line {
  margin-bottom: 8px;
}

.skeleton-stat {
  padding: 16px;
  border-radius: var(--solo-radius-lg, 12px);
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
}
.skeleton-stat .title-line { width: 35%; height: 14px; margin-bottom: 12px; }
.skeleton-stat .number-line { width: 60%; height: 28px; margin-bottom: 8px; }
.skeleton-stat .desc-line { width: 45%; height: 12px; }

.skeleton-card {
  padding: 16px;
  border-radius: var(--solo-radius-lg, 12px);
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
}
.skeleton-card-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 14px;
}
.skeleton-card-header .title-line { width: 120px; height: 16px; }
.skeleton-card-header .badge-line { width: 50px; height: 16px; border-radius: 999px; }
.skeleton-card-body .content-line { height: 14px; margin-bottom: 10px; }

.skeleton-table {
  width: 100%;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 8px);
  background: #ffffff;
  overflow: hidden;
}
.skeleton-table-header {
  height: 40px;
  background-color: var(--solo-bg-soft, #f1f4f8);
  border-bottom: 1px solid var(--solo-border, #dde3ea);
}
.skeleton-table-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
}
.skeleton-table-row:last-child {
  border-bottom: none;
}
.col-sm { width: 60px; height: 14px; }
.col-md { width: 140px; height: 14px; }
.col-lg { flex: 1; height: 14px; }

.skeleton-rect {
  border-radius: var(--solo-radius-sm, 4px);
}
</style>
