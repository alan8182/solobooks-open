<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(
  defineProps<{
    compact?: boolean
    title?: string
    actionLabel?: string
  }>(),
  {
    compact: false,
    title: '请先完成期初建账',
    actionLabel: '去期初建账',
  },
)

const router = useRouter()

function resolveAuthStore() {
  try {
    return useAuthStore()
  } catch {
    return null
  }
}

const authStore = resolveAuthStore()
const reasonText = computed(
  () =>
    authStore?.openingBalanceReason ||
    '该账套从其他代账转入，尚未完成期初建账，请先完成期初建账后再导入业务数据或记账。',
)

function goToOpening() {
  if (router?.push) {
    void router.push('/mine/opening')
  } else if (typeof window !== 'undefined') {
    window.location.assign('/mine/opening')
  }
}
</script>

<template>
  <div class="opening-gate-alert" :class="{ 'is-compact': props.compact }" role="alert">
    <div class="opening-gate-icon">
      <i class="ri-error-warning-line" aria-hidden="true" />
    </div>
    <div class="opening-gate-body">
      <div class="opening-gate-title">{{ props.title }}</div>
      <div class="opening-gate-desc">{{ reasonText }}</div>
    </div>
    <div class="opening-gate-action">
      <NButton type="primary" size="small" @click="goToOpening">
        {{ props.actionLabel }}
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.opening-gate-alert {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--solo-radius);
  border: 1px solid var(--solo-expense-bg);
  border-left: 3px solid var(--solo-expense);
  background: var(--solo-warning-bg);
  color: var(--solo-text-primary);
  margin-bottom: 12px;
}

.opening-gate-alert.is-compact {
  padding: 10px 12px;
  margin-bottom: 0;
}

.opening-gate-icon {
  font-size: 20px;
  color: var(--solo-warning);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.opening-gate-body {
  flex: 1;
  min-width: 0;
}

.opening-gate-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
  line-height: 1.4;
}

.opening-gate-desc {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.opening-gate-action {
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .opening-gate-alert {
    flex-wrap: wrap;
  }
}
</style>
