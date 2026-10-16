<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { getTodos } from '@/api/todo'
import type { TodoItem } from '@/api/types'
import { formatMoney } from '@/utils/format'

const router = useRouter()
const todoList = ref<TodoItem[]>([])

const priorityConfig: Record<string, { label: string; class: string }> = {
  high: { label: '紧急', class: 'priority-high' },
  medium: { label: '重要', class: 'priority-medium' },
  low: { label: '普通', class: 'priority-low' },
}

onMounted(async () => {
  try {
    const resp = await getTodos()
    todoList.value = (resp.todos || []).slice(0, 3)
  } catch {
    // silent
  }
})
</script>

<template>
  <div class="todo-preview-section">
    <div class="section-header">
      <h3 class="section-title">待办事项提醒</h3>
      <NButton text type="primary" class="view-all-btn" @click="router.push('/todos')" v-if="todoList.length > 0">
        <span>查看全部</span>
        <i class="ri-arrow-right-s-line" />
      </NButton>
    </div>

    <div class="todos-card">
      <div v-if="todoList.length === 0" class="empty-state">
        <div class="empty-icon-wrapper">
          <i class="ri-checkbox-circle-line" />
        </div>
        <p class="empty-text">太棒了，目前没有待办事项</p>
      </div>

      <div v-else class="todo-list">
        <div
          v-for="todo in todoList"
          :key="todo.key"
          class="todo-row"
          @click="router.push('/todos')"
        >
          <div class="todo-left">
            <span :class="['priority-badge', priorityConfig[todo.priority]?.class || 'priority-low']">
              {{ priorityConfig[todo.priority]?.label || '普通' }}
            </span>
            <span class="todo-title">{{ todo.title }}</span>
          </div>
          <div class="todo-right">
            <span v-if="todo.amount" class="todo-amount">{{ formatMoney(todo.amount) }}</span>
            <i class="ri-arrow-right-s-line row-arrow" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.todo-preview-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.section-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
  margin: 0;
  letter-spacing: 0.5px;
}
.view-all-btn {
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 2px;
}
.todos-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: none;
  overflow: hidden;
}
.todo-list {
  display: flex;
  flex-direction: column;
}
.todo-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 1px solid var(--solo-border-light);
}
.todo-row:last-child {
  border-bottom: none;
}
.todo-row:hover {
  background: var(--solo-bg-soft);
}
.todo-row:hover .row-arrow {
  transform: translateX(2px);
  color: var(--solo-text-secondary);
}
.todo-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.priority-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 6px;
  white-space: nowrap;
  flex-shrink: 0;
}
.priority-high {
  color: var(--solo-expense);
  background: var(--solo-expense-bg);
}
.priority-medium {
  color: var(--solo-warning);
  background: var(--solo-warning-bg);
}
.priority-low {
  color: var(--solo-text-secondary);
  background: var(--solo-border-light);
}
.todo-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.todo-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.todo-amount {
  font-size: 13px;
  font-weight: 700;
  color: var(--solo-text-secondary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
.row-arrow {
  font-size: 16px;
  color: var(--solo-text-muted);
  transition: transform 0.2s, color 0.2s;
}
.empty-state {
  text-align: center;
  padding: 40px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
.empty-icon-wrapper {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--solo-income-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: var(--solo-income);
}
.empty-text {
  font-size: 14px;
  color: var(--solo-text-secondary);
  font-weight: 500;
  margin: 0;
}
</style>
