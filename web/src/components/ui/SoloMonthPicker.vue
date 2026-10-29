<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NPopover } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    value?: string | null
    formattedValue?: string | null
    modelValue?: string | null
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
    size?: 'small' | 'medium' | 'large'
    isDateDisabled?: (ts: number) => boolean
    to?: string | HTMLElement | false
  }>(),
  {
    value: undefined,
    formattedValue: undefined,
    modelValue: undefined,
    placeholder: '选择月份',
    clearable: false,
    disabled: false,
    size: 'small',
    isDateDisabled: undefined,
    to: undefined,
  },
)

const emit = defineEmits<{
  'update:value': [val: string | null]
  'update:formattedValue': [val: string | null]
  'update:modelValue': [val: string | null]
  change: [val: string | null]
}>()

const now = new Date()
const currentCalendarYear = now.getFullYear()
const currentCalendarMonth = now.getMonth() + 1 // 1-12

const showPopover = ref(false)
const viewYear = ref<number>(currentCalendarYear)

const currentValue = computed<string | null>(() => {
  if (props.value !== undefined) return props.value
  if (props.formattedValue !== undefined) return props.formattedValue
  if (props.modelValue !== undefined) return props.modelValue
  return null
})

const selectedYear = computed<number | null>(() => {
  if (!currentValue.value) return null
  const y = parseInt(currentValue.value.slice(0, 4), 10)
  return isNaN(y) ? null : y
})

const selectedMonth = computed<number | null>(() => {
  if (!currentValue.value) return null
  const m = parseInt(currentValue.value.slice(5, 7), 10)
  return isNaN(m) ? null : m
})

const displayLabel = computed(() => {
  if (!currentValue.value) return ''
  return currentValue.value
})

watch(showPopover, (open) => {
  if (open) {
    viewYear.value = selectedYear.value || currentCalendarYear
  }
})

function prevYear() {
  viewYear.value--
}

function nextYear() {
  viewYear.value++
}

function selectMonth(m: number) {
  if (isMonthDisabled(m)) return
  const monthStr = String(m).padStart(2, '0')
  const val = `${viewYear.value}-${monthStr}`
  emitUpdate(val)
  showPopover.value = false
}

function selectThisMonth() {
  const m = String(currentCalendarMonth).padStart(2, '0')
  const val = `${currentCalendarYear}-${m}`
  emitUpdate(val)
  showPopover.value = false
}

function handleClear(e: MouseEvent) {
  e.stopPropagation()
  emitUpdate(null)
  showPopover.value = false
}

function emitUpdate(val: string | null) {
  emit('update:value', val)
  emit('update:formattedValue', val)
  emit('update:modelValue', val)
  emit('change', val)
}

function isMonthDisabled(m: number): boolean {
  if (!props.isDateDisabled) return false
  const ts = new Date(viewYear.value, m - 1, 1).getTime()
  return props.isDateDisabled(ts)
}
</script>

<template>
  <div class="solo-month-picker-wrapper">
    <NPopover
      v-model:show="showPopover"
      trigger="click"
      placement="bottom-start"
      :show-arrow="false"
      :disabled="disabled"
      :to="to"
    >
      <template #trigger>
        <div
          class="solo-month-picker"
          :class="[
            `size-${size}`,
            {
              'is-disabled': disabled,
              'is-open': showPopover,
              'has-value': Boolean(currentValue),
            },
          ]"
          role="button"
          tabindex="0"
          @keydown.enter="!disabled && (showPopover = !showPopover)"
        >
          <span class="picker-text" :class="{ 'is-placeholder': !currentValue }">
            {{ displayLabel || placeholder }}
          </span>
          <span class="picker-icons">
            <button
              v-if="clearable && currentValue && !disabled"
              type="button"
              class="clear-btn"
              tabindex="-1"
              aria-label="清空月份"
              @click="handleClear"
            >
              <i class="ri-close-circle-fill" />
            </button>
            <i class="calendar-icon ri-calendar-line" />
          </span>
        </div>
      </template>

      <div class="solo-month-popover">
        <div class="popover-header">
          <button
            type="button"
            class="year-nav-btn"
            aria-label="上一年"
            @click="prevYear"
          >
            <i class="ri-arrow-left-s-line" />
          </button>
          <span class="year-title">{{ viewYear }} 年</span>
          <button
            type="button"
            class="year-nav-btn"
            aria-label="下一年"
            @click="nextYear"
          >
            <i class="ri-arrow-right-s-line" />
          </button>
        </div>

        <div class="month-grid">
          <button
            v-for="m in 12"
            :key="m"
            type="button"
            class="month-cell"
            :class="{
              'is-selected': selectedYear === viewYear && selectedMonth === m,
              'is-current': currentCalendarYear === viewYear && currentCalendarMonth === m,
              'is-disabled': isMonthDisabled(m),
            }"
            :disabled="isMonthDisabled(m)"
            @click="selectMonth(m)"
          >
            {{ m }} 月
          </button>
        </div>

        <div class="popover-footer">
          <button type="button" class="footer-btn quick-this-month" @click="selectThisMonth">
            本月
          </button>
          <button
            v-if="clearable && currentValue"
            type="button"
            class="footer-btn text-muted"
            @click="handleClear"
          >
            清空
          </button>
        </div>
      </div>
    </NPopover>
  </div>
</template>

<style scoped>
.solo-month-picker-wrapper {
  display: inline-block;
  vertical-align: middle;
}

.solo-month-picker {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 120px;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease-in-out;
  box-sizing: border-box;
}

.solo-month-picker.size-small {
  height: 28px;
  padding: 0 8px;
  font-size: 12px;
}

.solo-month-picker.size-medium {
  height: 34px;
  padding: 0 10px;
  font-size: 13px;
}

.solo-month-picker.size-large {
  height: 40px;
  padding: 0 12px;
  font-size: 14px;
}

.solo-month-picker:hover:not(.is-disabled) {
  border-color: var(--solo-primary, #1747b0);
}

.solo-month-picker.is-open {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 0 0 2px rgba(23, 71, 176, 0.12);
}

.solo-month-picker.is-disabled {
  background: var(--solo-bg-soft, #f1f4f8);
  border-color: var(--solo-border-light, #edf0f4);
  cursor: not-allowed;
  opacity: 0.7;
}

.picker-text {
  flex: 1;
  color: var(--solo-text-primary, #182230);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.picker-text.is-placeholder {
  color: var(--solo-text-muted, #9ca3af);
}

.picker-icons {
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
  position: relative;
}

.calendar-icon {
  font-size: 14px;
  color: var(--solo-text-muted, #9ca3af);
  pointer-events: none;
}

.clear-btn {
  display: none;
  background: transparent;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  color: var(--solo-text-muted, #9ca3af);
  font-size: 14px;
  line-height: 1;
}

.clear-btn:hover {
  color: var(--solo-text-primary, #182230);
}

.solo-month-picker.has-value:hover .clear-btn {
  display: inline-flex;
}

.solo-month-picker.has-value:hover .calendar-icon {
  display: none;
}

/* Popover Content */
.solo-month-popover {
  width: 220px;
  padding: 4px;
  user-select: none;
}

.popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 8px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
  margin-bottom: 8px;
}

.year-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: var(--solo-text-secondary, #586474);
  font-size: 16px;
  transition: background-color 0.15s ease;
}

.year-nav-btn:hover {
  background: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-primary, #182230);
}

.year-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  letter-spacing: 0.5px;
}

.month-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}

.month-cell {
  height: 32px;
  padding: 0;
  margin: 0;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 13px;
  color: var(--solo-text-primary, #182230);
  cursor: pointer;
  transition: all 0.15s ease;
  font-variant-numeric: tabular-nums;
}

.month-cell:hover:not(.is-selected):not(.is-disabled) {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}

.month-cell.is-selected {
  background: var(--solo-primary, #1747b0);
  color: #ffffff;
  font-weight: 600;
}

.month-cell.is-current:not(.is-selected) {
  color: var(--solo-primary, #1747b0);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px var(--solo-primary, #1747b0);
}

.month-cell.is-disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.popover-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px solid var(--solo-border-light, #edf0f4);
  font-size: 12px;
}

.footer-btn {
  background: transparent;
  border: none;
  padding: 2px 6px;
  margin: 0;
  font-size: 12px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--solo-primary, #1747b0);
  transition: background-color 0.15s ease;
}

.footer-btn:hover {
  background: var(--solo-bg-blue-soft, #edf3ff);
}

.footer-btn.text-muted {
  color: var(--solo-text-muted, #636e7c);
}

.footer-btn.text-muted:hover {
  background: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-primary, #182230);
}
</style>
