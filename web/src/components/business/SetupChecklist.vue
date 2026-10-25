<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { SoloStatusBadge } from '@/components/ui'
import { useAuthStore } from '@/stores/auth'
import { useGuideStateStore, resolveChecklistDisplay, isArchived } from '@/stores/guideState'
import type { SetupReadiness, SetupStep, SetupStepStatus } from '@/api/types'

/**
 * 上手清单：把「账套能不能开始用」摊开成 5 行。
 *
 * 完成状态全部来自后端 readiness，组件只负责表达与降级。降级规则在
 * `stores/guideState.ts` 里（就绪折叠 / 14 天降级 / 当天可关），这里是纯渲染。
 */
const props = withDefaults(
  defineProps<{
    readiness: SetupReadiness | null
    loading?: boolean
    /** page：/guide 里强制展开，忽略折叠与关闭 */
    mode?: 'home' | 'page'
  }>(),
  { loading: false, mode: 'home' },
)

const router = useRouter()
const authStore = useAuthStore()
const guideStore = useGuideStateStore()

const currentCompany = computed(
  () =>
    authStore.companies.find((item) => item.company_id === authStore.currentCompanyId) ?? null,
)
const createdAt = computed(() => currentCompany.value?.created_at ?? null)
const isDemo = computed(() => currentCompany.value?.isDemo === true)

const display = computed(() => {
  if (props.mode === 'page') return props.readiness ? 'expanded' : 'hidden'
  return resolveChecklistDisplay(
    guideStore.state,
    props.readiness,
    createdAt.value,
    isDemo.value,
  )
})

const items = computed<SetupStep[]>(() => props.readiness?.items ?? [])
const completed = computed(() => props.readiness?.completed ?? 0)
const total = computed(() => props.readiness?.total ?? 0)
const archived = computed(() => isArchived(guideStore.state))

const heading = computed(() => {
  if (props.loading) return '正在检查账套'
  if (display.value === 'expanded') return '上手清单'
  if (props.readiness?.ready) {
    return archived.value ? '上手清单已归档' : `账套已就绪 · ${total.value}/${total.value}`
  }
  return `上手清单：已完成 ${completed.value}/${total.value}`
})

const STATE_ICONS: Record<SetupStepStatus, string> = {
  todo: '',
  doing: 'ri-loader-4-line',
  done: 'ri-check-line',
  waived: 'ri-subtract-line',
  locked: 'ri-lock-line',
}

const STATE_LABELS: Record<SetupStepStatus, string> = {
  todo: '待开始',
  doing: '进行中',
  done: '已完成',
  waived: '无需',
  locked: '待解锁',
}

function isClickable(item: SetupStep) {
  return item.status !== 'locked'
}

function openItem(item: SetupStep) {
  if (!isClickable(item)) return
  router.push(item.deepLink)
}

function dismissToday() {
  guideStore.patch({ dismissedAt: new Date().toISOString() })
}

function goGuide() {
  router.push('/guide')
}
</script>

<template>
  <section
    v-if="display !== 'hidden' || loading"
    class="setup-checklist"
    :class="{ 'is-summary': display === 'summary' }"
    aria-labelledby="setup-checklist-title"
  >
    <header class="checklist-header">
      <div class="checklist-heading">
        <h2 id="setup-checklist-title">{{ heading }}</h2>
      </div>
      <div class="checklist-actions">
        <button
          v-if="mode === 'home' && display === 'expanded'"
          type="button"
          class="checklist-link is-quiet"
          @click="dismissToday"
        >
          今天先不看
        </button>
        <button
          v-if="display === 'expanded' && mode === 'home'"
          type="button"
          class="checklist-link"
          @click="goGuide"
        >
          查看全部
          <i class="ri-arrow-right-line" aria-hidden="true" />
        </button>
        <button
          v-else-if="display === 'summary'"
          type="button"
          class="checklist-link"
          @click="goGuide"
        >
          查看
          <i class="ri-arrow-right-line" aria-hidden="true" />
        </button>
      </div>
    </header>

    <div v-if="loading" class="checklist-skeleton" aria-hidden="true">
      <span v-for="index in 2" :key="index" class="skeleton-row" />
    </div>

    <div v-else-if="display === 'expanded'" class="checklist-list">
      <component
        :is="isClickable(item) ? 'button' : 'div'"
        v-for="(item, index) in items"
        :key="item.key"
        class="checklist-row"
        :class="{ 'is-locked': !isClickable(item), 'is-informational': item.informational }"
        :type="isClickable(item) ? 'button' : undefined"
        @click="openItem(item)"
      >
        <span class="checklist-state" :class="`is-${item.status}`">
          <i v-if="STATE_ICONS[item.status]" :class="STATE_ICONS[item.status]" aria-hidden="true" />
          <template v-else>{{ index + 1 }}</template>
        </span>

        <span class="checklist-copy">
          <span class="checklist-title">{{ item.title }}</span>
          <span class="checklist-hint">{{ item.detail || item.hint }}</span>
        </span>

        <span class="checklist-tail">
          <SoloStatusBadge plain :tone="item.status === 'done' ? 'success' : 'neutral'">
            {{ STATE_LABELS[item.status] }}
          </SoloStatusBadge>
          <i
            v-if="isClickable(item)"
            class="ri-arrow-right-line checklist-arrow"
            aria-hidden="true"
          />
        </span>
      </component>
    </div>
  </section>
</template>

<style scoped>
.setup-checklist {
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  overflow: hidden;
}

.checklist-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px 12px;
}

.setup-checklist.is-summary .checklist-header {
  padding: 14px 18px;
}

.checklist-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.checklist-heading h2 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 14.5px;
  font-weight: 650;
  line-height: 1.3;
}

.checklist-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.checklist-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-primary);
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}

.checklist-link.is-quiet {
  color: var(--solo-text-muted);
  font-weight: 550;
}

.checklist-link:hover {
  background: var(--solo-bg-blue-soft);
}

.checklist-link:focus-visible,
.checklist-row:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}

.checklist-list {
  display: flex;
  flex-direction: column;
}

.checklist-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 18px;
  border: 0;
  border-top: 1px solid var(--solo-border-light);
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 160ms ease-out;
}

button.checklist-row:hover {
  background: var(--solo-bg-soft);
}

.checklist-row.is-locked {
  cursor: default;
  opacity: 0.55;
}

.checklist-state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.checklist-state i {
  font-size: 15px;
}

.checklist-state.is-done {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}

.checklist-state.is-doing {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
}

.checklist-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.checklist-title {
  overflow: hidden;
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.checklist-hint {
  overflow: hidden;
  color: var(--solo-text-secondary);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.checklist-row.is-informational .checklist-hint {
  color: var(--solo-text-muted);
}

.checklist-tail {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  color: var(--solo-text-muted);
}

.checklist-arrow {
  font-size: 14px;
}

.checklist-skeleton {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-bottom: 1px;
}

.skeleton-row {
  height: 56px;
  border-top: 1px solid var(--solo-border-light);
  background: linear-gradient(90deg, var(--solo-bg-soft), var(--solo-bg-card), var(--solo-bg-soft));
  background-size: 200% 100%;
  animation: checklist-shimmer 1.4s ease-in-out infinite;
}

@keyframes checklist-shimmer {
  to {
    background-position: -200% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-row {
    animation: none;
  }
}

@media (max-width: 640px) {
  .checklist-row {
    padding: 12px 16px;
  }

  .checklist-hint {
    white-space: normal;
  }
}
</style>
