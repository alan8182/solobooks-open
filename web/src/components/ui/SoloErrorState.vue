<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 失败态。加载失败必须和「没有数据」长得不一样：
 * 没有数据是事实，失败是系统状态——两者混在一起，用户会以为自己的账没了。
 *
 * 契约：
 * - 永远给出「重试」动作（除非调用方显式关闭），失败页不该是死路；
 * - 主文案说清「什么失败了」，副文案给下一步；
 * - 颜色走语义红，只用于图标底色，不整片染色。
 */
withDefaults(
  defineProps<{
    title?: string
    description?: string
    /** 后端返回的具体原因，放一行小字 */
    detail?: string
    retryLabel?: string
    showRetry?: boolean
    icon?: string
  }>(),
  {
    title: '数据加载失败',
    description: '网络或服务暂时不可用，稍后重试即可。',
    detail: '',
    retryLabel: '重新加载',
    showRetry: true,
    icon: 'ri-error-warning-line',
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="solo-error" role="alert">
    <div class="error-icon-wrap" aria-hidden="true">
      <i :class="icon" />
    </div>
    <h4 class="error-title">{{ title }}</h4>
    <p v-if="description" class="error-description">{{ description }}</p>
    <p v-if="detail" class="error-detail">{{ detail }}</p>
    <div v-if="showRetry" class="error-action">
      <button type="button" class="error-retry" @click="emit('retry')">
        <i class="ri-refresh-line" aria-hidden="true" />
        {{ retryLabel }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.solo-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 36px 20px;
}

.error-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background-color: var(--solo-expense-bg, #fbeeee);
  color: var(--solo-expense, #bd3d3d);
  font-size: 22px;
  margin-bottom: 12px;
}

.error-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}

.error-description {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--solo-text-secondary, #586474);
  max-width: 340px;
  line-height: 1.6;
}

.error-detail {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  max-width: 340px;
  line-height: 1.6;
}

.error-action {
  margin-top: 16px;
}

.error-retry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 8px;
  border: 1px solid var(--solo-border, #dde3ea);
  background: var(--solo-bg-card, #fff);
  color: var(--solo-text-primary, #182230);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.16s ease, color 0.16s ease;
}

.error-retry:hover {
  border-color: var(--solo-primary, #1747b0);
  color: var(--solo-primary, #1747b0);
}
</style>
