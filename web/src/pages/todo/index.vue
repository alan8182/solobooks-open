<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, NSpin, useMessage, NEmpty, NCheckbox, NGrid, NGridItem, NRadioGroup, NRadioButton } from 'naive-ui'
import { getTodos, updateTodoState } from '@/api/todo'
import { formatMoney } from '@/utils/format'
import type { TodoItem } from '@/api/types'

const router = useRouter()
const message = useMessage()

const todos = ref<TodoItem[]>([])
const loading = ref(true)
const updating = ref<Set<string>>(new Set())
const filterTab = ref('pending')

const priorityConfig: Record<string, { label: string; type: 'error' | 'warning' | 'info'; color: string }> = {
  high: { label: '紧急', type: 'error', color: '#f43f5e' },
  medium: { label: '重要', type: 'warning', color: '#f59e0b' },
  low: { label: '普通', type: 'info', color: '#64748b' },
}

const typeIcons: Record<string, string> = {
  receipt: 'ri-file-receipt-line',
  tax: 'ri-percent-line',
  risk: 'ri-error-warning-line',
  bank: 'ri-bank-card-line',
  task: 'ri-task-line',
}

const typeLabels: Record<string, string> = {
  receipt: '票据补充',
  tax: '税务申报',
  risk: '风险复核',
  bank: '流水待配',
  task: '日常账务',
}

const pendingCount = computed(() => todos.value.filter((t) => !t.done && !t.dismissed).length)
const highCount = computed(() => todos.value.filter((t) => !t.done && !t.dismissed && t.priority === 'high').length)
const completedCount = computed(() => todos.value.filter((t) => t.done).length)

const filteredTodos = computed(() => {
  const list = todos.value.filter((t) => !t.dismissed)
  if (filterTab.value === 'pending') {
    return list.filter((t) => !t.done)
  }
  if (filterTab.value === 'high') {
    return list.filter((t) => !t.done && t.priority === 'high')
  }
  if (filterTab.value === 'completed') {
    return list.filter((t) => t.done)
  }
  return list
})

async function load() {
  loading.value = true
  try {
    const resp = await getTodos()
    todos.value = resp.todos ?? []
  } catch {
    message.error('加载待办失败')
  } finally {
    loading.value = false
  }
}

async function toggleDone(todo: TodoItem) {
  updating.value.add(todo.key)
  try {
    await updateTodoState(todo.key, { done: !todo.done })
    message.success(todo.done ? '已标为未完成' : '已标为已完成')
    await load()
  } catch {
    message.error('更新失败')
  } finally {
    updating.value.delete(todo.key)
  }
}

async function dismissTodo(todo: TodoItem) {
  updating.value.add(todo.key)
  try {
    await updateTodoState(todo.key, { dismissed: true })
    message.success('已忽略该待办')
    await load()
  } catch {
    message.error('操作失败')
  } finally {
    updating.value.delete(todo.key)
  }
}

function getActionRoute(todo: TodoItem) {
  if (todo.meta?.entryId) {
    return `/ledger/${todo.meta.entryId}`
  }
  if (todo.entryId) {
    return `/ledger/${todo.entryId}`
  }
  if (todo.type === 'bank' || todo.meta?.statementId) {
    return '/bank'
  }
  if (todo.meta?.action === 'confirm_asset' || todo.title.includes('固定资产')) {
    return '/assets'
  }
  return null
}

function getActionLabel(todo: TodoItem) {
  if (todo.meta?.entryId || todo.entryId) {
    return '查看关联交易'
  }
  if (todo.type === 'bank') {
    return '去流水对账'
  }
  if (todo.meta?.action === 'confirm_asset' || todo.title.includes('固定资产')) {
    return '设置折旧'
  }
  return '去处理'
}

onMounted(load)
</script>

<template>
  <div class="todo-page">
    <div class="page-header">
      <div class="title-section">
        <h2>待办事项</h2>
        <p class="subtitle">智能识别票据缺失、流水未配、税务合规及折旧等关键待办</p>
      </div>
    </div>

    <!-- Stats Grid -->
    <NGrid :cols="3" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box stat-pending">
          <div class="stat-icon-wrapper">
            <i class="ri-todo-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">待处理</span>
            <span class="stat-value">{{ pendingCount }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box stat-high">
          <div class="stat-icon-wrapper">
            <i class="ri-error-warning-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">高优先级</span>
            <span class="stat-value text-red">{{ highCount }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box stat-completed">
          <div class="stat-icon-wrapper">
            <i class="ri-checkbox-circle-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">已完成</span>
            <span class="stat-value text-green">{{ completedCount }}</span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- Filter Tab Bar -->
    <div class="filter-section">
      <NRadioGroup v-model:value="filterTab" size="medium" class="filter-group">
        <NRadioButton value="pending">
          待处理 ({{ pendingCount }})
        </NRadioButton>
        <NRadioButton value="high">
          高优先级 ({{ highCount }})
        </NRadioButton>
        <NRadioButton value="completed">
          已完成 ({{ completedCount }})
        </NRadioButton>
        <NRadioButton value="all">
          全部待办 ({{ todos.length }})
        </NRadioButton>
      </NRadioGroup>
    </div>

    <!-- Todo List Items -->
    <NSpin :show="loading">
      <div v-if="filteredTodos.length > 0" class="todo-list">
        <div
          v-for="todo in filteredTodos"
          :key="todo.key"
          class="todo-card-wrapper"
          :class="{ 'is-done': todo.done }"
        >
          <!-- Left priority indicator bar -->
          <div 
            class="priority-indicator" 
            :style="{ background: priorityConfig[todo.priority]?.color || '#64748b' }"
          />
          
          <div class="todo-card-content">
            <!-- Left check & icon area -->
            <div class="todo-card-left">
              <NCheckbox
                :checked="todo.done"
                :disabled="updating.has(todo.key)"
                @update:checked="toggleDone(todo)"
                class="todo-checkbox"
              />
              <div 
                class="type-icon-wrapper"
                :style="{ color: priorityConfig[todo.priority]?.color, background: (priorityConfig[todo.priority]?.color + '10') }"
              >
                <i :class="typeIcons[todo.type] || 'ri-task-line'" />
              </div>
            </div>

            <!-- Title & desc area -->
            <div class="todo-card-mid">
              <div class="todo-title-row">
                <span class="todo-title" :class="{ 'strike-through': todo.done }">
                  {{ todo.title }}
                </span>
                <NTag 
                  size="tiny" 
                  :type="priorityConfig[todo.priority]?.type" 
                  :bordered="false"
                  round
                >
                  {{ priorityConfig[todo.priority]?.label }}
                </NTag>
                <NTag 
                  v-if="todo.type"
                  size="tiny" 
                  class="type-tag"
                  :bordered="false"
                  round
                >
                  {{ typeLabels[todo.type] || '待办' }}
                </NTag>
              </div>
              <div class="todo-description">
                {{ todo.desc || todo.description }}
              </div>
              
              <!-- Action Button & Metadata -->
              <div class="todo-meta-row" v-if="!todo.done && getActionRoute(todo)">
                <NButton 
                  secondary 
                  type="primary" 
                  size="tiny" 
                  class="action-btn"
                  @click="router.push(getActionRoute(todo)!)"
                >
                  <span>{{ getActionLabel(todo) }}</span>
                  <template #icon><i class="ri-arrow-right-line" /></template>
                </NButton>
              </div>
            </div>

            <!-- Right amount & actions area -->
            <div class="todo-card-right">
              <span v-if="todo.amount" class="amount-badge">
                {{ formatMoney(todo.amount) }}
              </span>
              
              <NButton 
                v-if="!todo.done"
                text 
                type="error" 
                size="small"
                class="dismiss-btn"
                :loading="updating.has(todo.key)"
                @click="dismissTodo(todo)"
              >
                <template #icon><i class="ri-delete-bin-7-line" /></template>
                忽略
              </NButton>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty State -->
      <div v-else-if="!loading" class="empty-state">
        <NEmpty description="暂无符合条件的待办事项">
          <template #icon>
            <div class="empty-icon-circle">
              <i class="ri-checkbox-multiple-line" />
            </div>
          </template>
        </NEmpty>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.todo-page {
  max-width: 820px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.page-header {
  margin-bottom: 24px;
}
.title-section h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.subtitle {
  margin: 4px 0 0 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
}

/* Stats Dashboard */
.stats-grid {
  margin-bottom: 28px;
}
.stat-box {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.3s ease;
}
.stat-box:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow-lg);
  border-color: var(--solo-primary-light);
}
.stat-icon-wrapper {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}
.stat-pending .stat-icon-wrapper {
  background: rgba(99, 102, 241, 0.1);
  color: var(--solo-primary);
}
.stat-high .stat-icon-wrapper {
  background: rgba(244, 63, 94, 0.1);
  color: var(--solo-expense);
}
.stat-completed .stat-icon-wrapper {
  background: rgba(16, 185, 129, 0.1);
  color: var(--solo-income);
}
.stat-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.stat-value {
  font-size: 22px;
  font-weight: 800;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
  line-height: 1;
}
.text-red {
  color: var(--solo-expense);
}
.text-green {
  color: var(--solo-income);
}

/* Filter Group */
.filter-section {
  margin-bottom: 20px;
  display: flex;
  justify-content: flex-start;
}
.filter-group {
  background: var(--solo-bg-card);
  padding: 4px;
  border-radius: var(--solo-radius);
  border: 1px solid var(--solo-border-glass);
}

/* Todo List */
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.todo-card-wrapper {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  position: relative;
  overflow: hidden;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.todo-card-wrapper:hover {
  transform: translateY(-1px);
  box-shadow: var(--solo-shadow-md);
  border-color: var(--solo-border);
}
.todo-card-wrapper.is-done {
  opacity: 0.65;
  background: rgba(255, 255, 255, 0.3);
}
.priority-indicator {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
}
.todo-card-content {
  padding: 16px 20px 16px 16px;
  display: flex;
  align-items: flex-start;
  gap: 16px;
}
.todo-card-left {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 2px;
}
.todo-checkbox {
  transform: scale(1.05);
}
.type-icon-wrapper {
  width: 34px;
  height: 34px;
  border-radius: var(--solo-radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.todo-card-mid {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.todo-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.todo-title {
  font-size: 14.5px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1.4;
}
.strike-through {
  text-decoration: line-through;
  color: var(--solo-text-muted);
}
.type-tag {
  color: var(--solo-text-secondary);
  background: var(--solo-border-light);
}
.todo-description {
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}
.todo-meta-row {
  margin-top: 4px;
}
.action-btn {
  font-weight: 600;
}
.todo-card-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
  align-self: stretch;
  justify-content: space-between;
  min-width: 90px;
  flex-shrink: 0;
}
.amount-badge {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
  font-size: 13px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 999px;
  font-family: 'Outfit', 'Inter', sans-serif;
  border: 1px solid rgba(244, 63, 94, 0.1);
}
.dismiss-btn {
  font-size: 12px;
  font-weight: 500;
}

/* Empty State */
.empty-state {
  display: flex;
  justify-content: center;
  padding: 64px 0;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
}
.empty-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--solo-income-bg);
  color: var(--solo-income);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  margin: 0 auto 12px auto;
}
</style>
