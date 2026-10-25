<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { useRouter } from 'vue-router'
import { formatMoney } from '@/utils/format'
import type { TaskInboxItem, TaskSeverity } from '@/composables/useTaskInbox'

defineProps<{
  tasks: TaskInboxItem[]
  loading?: boolean
  partialFailure?: boolean
}>()

const router = useRouter()

const SEVERITY_ICONS: Record<TaskSeverity, string> = {
  critical: 'ri-error-warning-line',
  warning: 'ri-time-line',
  info: 'ri-information-line',
}

function openTask(task: TaskInboxItem) {
  router.push(task.actionPath)
}
</script>

<template>
  <section class="task-inbox" aria-labelledby="task-inbox-title">
    <header class="task-inbox-header">
      <div class="task-heading">
        <h2 id="task-inbox-title">
          {{ loading ? '正在整理待办' : tasks.length ? '今天要处理' : '今天没有待处理事项' }}
        </h2>
        <span v-if="!loading && tasks.length" class="task-count">{{ tasks.length }}</span>
      </div>
      <button v-if="tasks.length" type="button" class="view-all" @click="router.push('/todos')">
        查看全部
        <i class="ri-arrow-right-line" aria-hidden="true" />
      </button>
    </header>

    <div v-if="loading" class="task-skeleton" aria-hidden="true">
      <span v-for="index in 3" :key="index" class="skeleton-row" />
    </div>

    <div v-else-if="tasks.length === 0" class="task-empty">
      <i class="ri-checkbox-circle-line" aria-hidden="true" />
      <div>
        <strong>当前没有需要你处理的事项</strong>
        <p>新的草稿、征期和结账任务会出现在这里。</p>
      </div>
    </div>

    <div v-else class="task-list">
      <button
        v-for="task in tasks"
        :key="task.id"
        type="button"
        class="task-row"
        @click="openTask(task)"
      >
        <span class="task-state" :class="`is-${task.severity}`">
          <i :class="SEVERITY_ICONS[task.severity]" aria-hidden="true" />
        </span>

        <span class="task-copy">
          <span class="task-meta">
            <span class="task-source">{{ task.sourceLabel }}</span>
            <span v-if="task.dueDate" class="task-due">截止 {{ task.dueDate }}</span>
          </span>
          <span class="task-title">{{ task.title }}</span>
          <span v-if="task.detail" class="task-detail">{{ task.detail }}</span>
        </span>

        <span class="task-tail">
          <span v-if="task.amount" class="task-amount">{{ formatMoney(task.amount) }}</span>
          <span class="task-action">
            {{ task.actionLabel }}
            <i class="ri-arrow-right-line" aria-hidden="true" />
          </span>
        </span>
      </button>
    </div>

    <p v-if="partialFailure" class="task-hint">
      部分事项暂时无法加载，已显示其余可用内容。
    </p>
  </section>
</template>

<style scoped>
.task-inbox {
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  overflow: hidden;
}

.task-inbox-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px 12px;
}

.task-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-inbox-header h2 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 14.5px;
  font-weight: 650;
  line-height: 1.3;
}

.task-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.view-all {
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

.view-all:hover {
  background: var(--solo-bg-blue-soft);
}

.view-all:focus-visible,
.task-row:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}

.task-list {
  display: flex;
  flex-direction: column;
}

.task-row {
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

.task-row:hover {
  background: var(--solo-bg-soft);
}

.task-state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 15px;
}

.task-state.is-critical {
  color: var(--solo-expense);
  background: var(--solo-expense-bg);
}

.task-state.is-warning {
  color: var(--solo-warning);
  background: var(--solo-warning-bg);
}

.task-state.is-info {
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.task-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.task-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--solo-text-muted);
  font-size: 11px;
}

.task-source {
  color: var(--solo-text-secondary);
  font-weight: 650;
}

.task-title {
  overflow: hidden;
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-detail {
  overflow: hidden;
  color: var(--solo-text-secondary);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-tail {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.task-amount {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.task-action {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--solo-primary);
  font-size: 11.5px;
  font-weight: 650;
}

.task-empty {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 22px 20px 24px;
  border-top: 1px solid var(--solo-border-light);
}

.task-empty > i {
  color: var(--solo-income);
  font-size: 24px;
}

.task-empty strong {
  color: var(--solo-text-primary);
  font-size: 13.5px;
}

.task-empty p {
  margin: 3px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.task-skeleton {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.skeleton-row {
  height: 58px;
  border-top: 1px solid var(--solo-border-light);
  background: linear-gradient(90deg, var(--solo-bg-soft), var(--solo-bg-card), var(--solo-bg-soft));
  background-size: 200% 100%;
  animation: task-shimmer 1.4s ease-in-out infinite;
}

.task-hint {
  margin: 0;
  padding: 10px 20px;
  border-top: 1px solid var(--solo-border-light);
  color: var(--solo-text-muted);
  font-size: 11px;
}

@keyframes task-shimmer {
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
  .task-inbox-header {
    align-items: flex-start;
  }

  .task-row {
    align-items: flex-start;
    padding: 14px 16px;
  }

  .task-tail {
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
  }

  .task-detail {
    white-space: normal;
  }
}
</style>
