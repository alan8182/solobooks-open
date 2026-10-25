<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'small' | 'medium' | 'large'

const props = withDefaults(
  defineProps<{
    variant?: ButtonVariant
    size?: ButtonSize
    type?: 'button' | 'submit' | 'reset'
    loading?: boolean
    disabled?: boolean
    icon?: string
  }>(),
  {
    variant: 'secondary',
    size: 'medium',
    type: 'button',
    loading: false,
    disabled: false,
  },
)

const emit = defineEmits<{ click: [event: MouseEvent] }>()

const variantClass = computed(() => `btn-${props.variant}`)
const sizeClass = computed(() => `btn-${props.size}`)

function handleClick(e: MouseEvent) {
  if (props.loading || props.disabled) return
  emit('click', e)
}
</script>

<template>
  <button
    :type="type"
    class="solo-btn"
    :class="[variantClass, sizeClass, { 'is-loading': loading }]"
    :disabled="disabled || loading"
    @click="handleClick"
  >
    <span v-if="loading" class="btn-spinner" aria-hidden="true" />
    <i v-else-if="icon" :class="icon" class="btn-icon" aria-hidden="true" />
    <span class="btn-label">
      <slot />
    </span>
  </button>
</template>

<style scoped>
.solo-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-family: inherit;
  font-weight: 600;
  white-space: nowrap;
  border-radius: var(--solo-radius-control, 8px);
  cursor: pointer;
  transition: all 0.15s cubic-bezier(0.16, 1, 0.3, 1);
  text-decoration: none;
  outline: none;
}

.solo-btn:focus-visible {
  outline: 2px solid var(--solo-primary-light, #2b66e2);
  outline-offset: 2px;
}

.solo-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  box-shadow: none !important;
}

/* 尺寸体系 */
.btn-small {
  height: 32px;
  padding: 0 10px;
  font-size: 12px;
}

.btn-medium {
  height: 38px;
  padding: 0 14px;
  font-size: 13px;
}

.btn-large {
  height: 44px;
  padding: 0 18px;
  font-size: 14px;
  border-radius: var(--solo-radius, 8px);
}

/* 变体体系 */
.btn-primary {
  background-color: var(--solo-primary, #1747b0);
  color: #ffffff;
  border: 1px solid var(--solo-primary, #1747b0);
  box-shadow: 0 1px 3px rgba(23, 71, 176, 0.2);
}
.btn-primary:hover:not(:disabled) {
  background-color: var(--solo-primary-light, #2b66e2);
  border-color: var(--solo-primary-light, #2b66e2);
  transform: translateY(-0.5px);
}
.btn-primary:active:not(:disabled) {
  background-color: var(--solo-primary-dark, #0d3282);
  border-color: var(--solo-primary-dark, #0d3282);
  transform: translateY(0);
}

.btn-secondary {
  background-color: #ffffff;
  color: var(--solo-text-primary, #182230);
  border: 1px solid var(--solo-border, #dde3ea);
  box-shadow: var(--solo-shadow-sm, 0 1px 2px rgba(24, 34, 48, 0.04));
}
.btn-secondary:hover:not(:disabled) {
  background-color: var(--solo-bg-soft, #f1f4f8);
  border-color: #c9d3e0;
}
.btn-secondary:active:not(:disabled) {
  background-color: #e8ecf2;
}

.btn-ghost {
  background-color: transparent;
  color: var(--solo-text-secondary, #586474);
  border: 1px solid transparent;
}
.btn-ghost:hover:not(:disabled) {
  background-color: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-primary, #182230);
}

.btn-danger {
  background-color: #ffffff;
  color: var(--solo-expense, #bd3d3d);
  border: 1px solid #fed7d7;
}
.btn-danger:hover:not(:disabled) {
  background-color: var(--solo-expense-bg, #fbeeee);
  border-color: var(--solo-expense, #bd3d3d);
}

.btn-icon {
  font-size: 1.15em;
  line-height: 1;
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: btn-spin 0.6s linear infinite;
}

@keyframes btn-spin {
  to { transform: rotate(360deg); }
}
</style>
