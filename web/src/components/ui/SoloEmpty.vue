<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'

/**
 * 空态只留两种形态，别再各页手写：
 * - guide（引导型）：这块区域整片为空，用户不知道下一步干什么 —— 大图标 + 标题 + 说明 + 主动作。
 * - hint（提示型）：容器里已有别的结构（表格 / 列表 / 面板），只是这一段没内容 ——
 *   一行紧凑灰字，不放大图标，不跟主结构抢注意力。
 *
 * 判定标准是「空区域是不是这一屏的主角」，不是「有没有 CTA」。
 */
export type EmptyVariant = 'guide' | 'hint'

const props = withDefaults(
  defineProps<{
    variant?: EmptyVariant
    title?: string
    description?: string
    icon?: string
    /** 更紧凑的 hint：表格 td 里带动作时用，空行只涨 ~12px 而不是 ~26px */
    dense?: boolean
  }>(),
  {
    variant: 'guide',
    title: '暂无数据',
    description: '',
    icon: 'ri-inbox-line',
    dense: false,
  },
)

const isHint = computed(() => props.variant === 'hint')
// hint 默认不显示标题：表格里「暂无数据」+ 一句解释是两层重复，留解释就够了
const showTitle = computed(() => !isHint.value || props.title !== '暂无数据')
</script>

<template>
  <div
    class="solo-empty"
    :class="[isHint ? 'is-hint' : 'is-guide', { 'is-dense': dense }]"
  >
    <div v-if="!isHint" class="empty-icon-wrap" aria-hidden="true">
      <i :class="icon" />
    </div>
    <h4 v-if="showTitle" class="empty-title">{{ title }}</h4>
    <p v-if="description" class="empty-description">{{ description }}</p>
    <div v-if="$slots.action" class="empty-action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.solo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

/* 引导型：整块空区域的主角，留得开 */
.is-guide {
  padding: 40px 20px;
}

.is-guide .empty-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background-color: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-muted, #636e7c);
  font-size: 24px;
  margin-bottom: 12px;
}

.is-guide .empty-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}

.is-guide .empty-description {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  max-width: 320px;
  line-height: 1.5;
}

.is-guide .empty-action {
  margin-top: 16px;
}

/* 提示型：嵌在表格 / 列表 / 面板里，只占一行的高度 */
.is-hint {
  padding: 22px 16px;
  gap: 2px;
}

/* td 里带动作的紧凑档：接近一行文字的高度，动作按钮不顶破行 */
.is-hint.is-dense {
  padding: 12px 10px;
}

.is-hint.is-dense .empty-action {
  margin-top: 8px;
}

.is-hint .empty-title {
  margin: 0;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
}

.is-hint .empty-description {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted, #636e7c);
  max-width: 460px;
  line-height: 1.6;
}

.is-hint .empty-action {
  margin-top: 10px;
}
</style>
