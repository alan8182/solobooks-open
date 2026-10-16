<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NButton, NTag, NSpin, useMessage, NEmpty, NCheckbox, NRadioGroup, NRadioButton } from 'naive-ui'
import { getTodos, updateTodoState } from '@/api/todo'
import { updateEntry } from '@/api/record'
import { markDeadlineFiled } from '@/api/tax-filing'
import { formatMoney } from '@/utils/format'
import type { TodoItem } from '@/api/types'

const router = useRouter()
const route = useRoute()
const message = useMessage()

const todos = ref<TodoItem[]>([])
const loading = ref(true)
const updating = ref<Set<string>>(new Set())
// tab = 事项分类（'all' + CATEGORY_ORDER 里的 key），选中态同步 URL ?tab=
const filterTab = ref('all')
const dismissingBatch = ref(false)

const priorityConfig: Record<string, { label: string; type: 'error' | 'warning' | 'info'; color: string }> = {
  high: { label: '紧急', type: 'error', color: '#f43f5e' },
  medium: { label: '重要', type: 'warning', color: '#f59e0b' },
  low: { label: '普通', type: 'info', color: '#64748b' },
}

// 事项分类：分组展示顺序 + 元数据（Remix Icon）
const CATEGORY_ORDER = ['filing', 'match', 'receipt', 'reimburse', 'billing', 'risk', 'other']

const categoryMeta: Record<string, { label: string; icon: string }> = {
  filing: { label: '待申报', icon: 'ri-calendar-check-line' },
  match: { label: '待配流水', icon: 'ri-arrow-left-right-line' },
  receipt: { label: '待补票据', icon: 'ri-file-receipt-line' },
  reimburse: { label: '待报销/付款', icon: 'ri-hand-coin-line' },
  billing: { label: '待开票', icon: 'ri-bill-line' },
  risk: { label: '风险复核', icon: 'ri-error-warning-line' },
  other: { label: '其他待办', icon: 'ri-task-line' },
}

// 旧后端未下发 category 字段时按 ruleId 兜底，保证分组不退化
const ruleIdCategory: Record<string, string> = {
  filing_deadline: 'filing',
  unmatched_statement: 'match',
  missing_document: 'receipt',
  invoice_unpaid: 'reimburse',
  owner_transfer_nature: 'reimburse',
  income_invoice: 'billing',
  entertainment_limit: 'risk',
  ad_limit: 'risk',
  fixed_asset_depreciation: 'risk',
}

function todoCategory(todo: TodoItem): string {
  return todo.category || ruleIdCategory[String(todo.meta?.ruleId || '')] || 'other'
}

// 跨 tab 紧急可见性：分类 tab 下其它分类的紧急项不可见，tab 行常驻一个紧急徽标兜底
const highCount = computed(() => todos.value.filter((t) => !t.done && !t.dismissed && t.priority === 'high').length)

// 事项 tab：全部打头 + 有内容的分类（零计数隐藏，避免一排死 tab）
const categoryTabs = computed(() => {
  const counts = new Map<string, number>()
  for (const todo of todos.value) {
    if (todo.dismissed) continue
    const cat = todoCategory(todo)
    counts.set(cat, (counts.get(cat) || 0) + 1)
  }
  const tabs = [{ key: 'all', label: '全部', count: [...counts.values()].reduce((sum, n) => sum + n, 0) }]
  for (const cat of CATEGORY_ORDER) {
    const count = counts.get(cat) || 0
    if (count > 0) tabs.push({ key: cat, label: categoryMeta[cat]?.label || '其他待办', count })
  }
  return tabs
})

const filteredTodos = computed(() => {
  const list = todos.value.filter((t) => !t.dismissed)
  if (filterTab.value !== 'all') {
    return list.filter((t) => todoCategory(t) === filterTab.value)
  }
  return list
})

// 当前分类被处理完（tab 消失）时回落到全部，避免停在空页
watch(categoryTabs, (tabs) => {
  if (filterTab.value !== 'all' && !tabs.some((tab) => tab.key === filterTab.value)) {
    filterTab.value = 'all'
  }
})

// tab 选中态写回 URL，刷新/回跳不丢
watch(filterTab, (tab) => {
  router.replace({ query: { ...route.query, tab: tab === 'all' ? undefined : tab } })
})

const priorityRank: Record<string, number> = { high: 0, medium: 1, low: 2 }

// 按事项分组；组间按固定顺序、组内按紧急程度再金额降序
const groupedTodos = computed(() => {
  const groups = new Map<string, TodoItem[]>()
  for (const todo of filteredTodos.value) {
    const cat = todoCategory(todo)
    if (!groups.has(cat)) groups.set(cat, [])
    groups.get(cat)!.push(todo)
  }
  return [...groups.entries()]
    .sort((a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]))
    .map(([category, items]) => ({
      category,
      label: categoryMeta[category]?.label || '其他待办',
      icon: categoryMeta[category]?.icon || 'ri-task-line',
      items: [...items].sort(
        (a, b) =>
          (priorityRank[a.priority] ?? 3) - (priorityRank[b.priority] ?? 3)
          || (Number(b.amount) || 0) - (Number(a.amount) || 0),
      ),
    }))
})

const collapsedCats = ref<Set<string>>(new Set())

function toggleGroup(category: string) {
  const next = new Set(collapsedCats.value)
  if (next.has(category)) next.delete(category)
  else next.add(category)
  collapsedCats.value = next
}

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

const selectedKeys = ref(new Set<string>())
const actingKeys = ref(new Set<string>())
const billingBatch = ref(false)

const selectedTodos = computed(() => todos.value.filter((t) => selectedKeys.value.has(t.key)))

// 批量开票确认仅对纯开票选择开放：混选其它类型只给「忽略所选」，
// 避免"批量已开票"默默跳过不适用的条目造成假成功
const allSelectedAreBilling = computed(() =>
  selectedTodos.value.length > 0
  && selectedTodos.value.every((t) => t.meta?.ruleId === 'income_invoice' && (t.meta?.entryId || t.entryId)),
)

// 注意：@click 内联调用会丢弃返回值，handler 必须是直接执行的 async 函数，
// 不能写成"返回 async 函数"的工厂（否则点击无任何反应）
async function setBillingStatus(todo: TodoItem, label: string, billingStatus: string) {
  const entryId = String(todo.meta?.entryId || todo.entryId || '')
  if (!entryId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await updateEntry(entryId, { billingStatus })
    message.success(`已标记${label}，待办已自动完成`)
    await load()
  } catch {
    message.error('操作失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
  }
}

async function markFiled(todo: TodoItem, zeroFiling = false) {
  const deadlineId = String(todo.meta?.deadlineId || '')
  if (!deadlineId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await markDeadlineFiled(deadlineId, zeroFiling ? { zeroFiling: true } : undefined)
    message.success(zeroFiling ? '已按零申报归档，待办已自动完成' : '已标记申报完成')
    await load()
  } catch {
    message.error('操作失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
  }
}

function goToMatchWizard(todo: TodoItem) {
  const month = String(todo.meta?.date || '').slice(0, 7)
  const query = new URLSearchParams()
  if (month) query.set('month', month)
  query.set('step', '4')
  router.push(`/closing-wizard?${query.toString()}`)
}

async function setNatureOwnerLoan(todo: TodoItem) {
  const entryId = String(todo.meta?.entryId || todo.entryId || '')
  if (!entryId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await updateEntry(entryId, { category: '其他应收款' })
    message.success('已记为股东往来（其他应收款），待办已自动完成')
    await load()
  } catch {
    message.error('操作失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
  }
}

function toggleSelect(todo: TodoItem, checked: boolean) {
  if (checked) selectedKeys.value.add(todo.key)
  else selectedKeys.value.delete(todo.key)
}

async function dismissSelected() {
  const keys = [...selectedKeys.value]
  if (keys.length === 0) return
  dismissingBatch.value = true
  let failed = 0
  for (const key of keys) {
    try {
      await updateTodoState(key, { dismissed: true })
    } catch {
      failed += 1
    }
  }
  dismissingBatch.value = false
  selectedKeys.value.clear()
  message.success(failed ? `已忽略 ${keys.length - failed} 条，${failed} 条失败` : `已忽略 ${keys.length} 条待办`)
  await load()
}

// 批量开票确认：与单卡按钮同一 updateEntry 路径，逐条执行、失败计数，完成后清勾选刷新
async function batchSetBillingStatus(billingStatus: string, label: string) {
  const targets = selectedTodos.value.filter((t) => t.meta?.ruleId === 'income_invoice')
  if (targets.length === 0 || billingBatch.value) return
  billingBatch.value = true
  let failed = 0
  for (const todo of targets) {
    const entryId = String(todo.meta?.entryId || todo.entryId || '')
    if (!entryId) {
      failed += 1
      continue
    }
    try {
      await updateEntry(entryId, { billingStatus })
    } catch {
      failed += 1
    }
  }
  billingBatch.value = false
  selectedKeys.value.clear()
  const done = targets.length - failed
  message.success(failed ? `已标记 ${done} 项${label}，${failed} 项失败` : `已批量标记 ${done} 项${label}，待办已自动完成`)
  await load()
}

// 已过账凭证不可编辑（要改只能冲销）；entryStatus 缺失时按可编辑兜底（兼容旧后端）
function isEntryEditable(todo: TodoItem): boolean {
  const status = String(todo.meta?.entryStatus || '')
  return !status || status === 'draft' || status === 'pending_review'
}

function getActionRoute(todo: TodoItem) {
  if (todo.meta?.entryId) {
    return `/ledger/${todo.meta.entryId}`
  }
  if (todo.entryId) {
    return `/ledger/${todo.entryId}`
  }
  if (todo.type === 'bank' || todo.meta?.statementId) {
    // 直达结账向导第四步配对现场，并带上流水定位参数
    const month = String(todo.meta?.date || '').slice(0, 7)
    const query = new URLSearchParams()
    if (month) query.set('month', month)
    query.set('step', '4')
    if (todo.meta?.statementId) query.set('statement', String(todo.meta.statementId))
    return `/closing-wizard?${query.toString()}`
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

onMounted(() => {
  // 从 URL 恢复 tab（旧状态值/非法值一律回落全部）
  const tab = String(route.query.tab || '')
  if (tab === 'all' || CATEGORY_ORDER.includes(tab)) {
    filterTab.value = tab
  }
  load()
})
</script>

<template>
  <div class="todo-page">
    <div class="page-header">
      <div class="title-section">
        <h2>待办事项</h2>
        <p class="subtitle">智能识别票据缺失、流水未配、税务合规及折旧等关键待办</p>
      </div>
    </div>

    <!-- Filter Tab Bar：按事项分类切换；紧急徽标只在分类 tab 下出现兜底
         （全部视图紧急卡本来可见，徽标若在会造成"点了没反应"的假死） -->
    <div class="filter-section">
      <NRadioGroup v-model:value="filterTab" size="medium" class="filter-group">
        <NRadioButton v-for="tab in categoryTabs" :key="tab.key" :value="tab.key">
          {{ tab.label }} ({{ tab.count }})
        </NRadioButton>
      </NRadioGroup>
      <NButton
        v-if="highCount > 0 && filterTab !== 'all'"
        size="tiny"
        type="error"
        secondary
        round
        @click="filterTab = 'all'"
      >
        <template #icon><i class="ri-alarm-warning-fill" /></template>
        {{ highCount }} 项紧急，点回全部
      </NButton>
      <template v-if="selectedKeys.size > 0 && allSelectedAreBilling">
        <NButton
          size="small"
          secondary
          type="primary"
          :loading="billingBatch"
          @click="batchSetBillingStatus('issued', '已开票')"
        >
          已开票 ({{ selectedKeys.size }})
        </NButton>
        <NButton
          size="small"
          secondary
          :loading="billingBatch"
          @click="batchSetBillingStatus('no_invoice', '不开票')"
        >
          不开票 ({{ selectedKeys.size }})
        </NButton>
      </template>
      <NButton
        v-if="selectedKeys.size > 0"
        size="small"
        secondary
        type="warning"
        :loading="dismissingBatch"
        @click="dismissSelected"
      >
        忽略所选 ({{ selectedKeys.size }})
      </NButton>
    </div>

    <!-- Todo List Items -->
    <NSpin :show="loading">
      <div v-if="filteredTodos.length > 0" class="todo-groups">
        <div v-for="group in groupedTodos" :key="group.category" class="todo-group">
          <button
            type="button"
            class="group-header"
            :disabled="filterTab !== 'all'"
            @click="toggleGroup(group.category)"
          >
            <span class="group-icon"><i :class="group.icon" /></span>
            <span class="group-label">{{ group.label }}</span>
            <span class="group-count">{{ group.items.length }}</span>
            <i
              v-if="filterTab === 'all'"
              class="ri-arrow-right-s-line group-chevron"
              :class="{ 'is-collapsed': collapsedCats.has(group.category) }"
            />
          </button>
          <!-- 具体事项 tab 下平铺展开，折叠仅用于「全部」全景视图 -->
          <div
            v-show="filterTab !== 'all' || !collapsedCats.has(group.category)"
            class="todo-list"
          >
            <div
              v-for="todo in group.items"
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
                    :checked="todo.done || selectedKeys.has(todo.key)"
                    :disabled="updating.has(todo.key)"
                    @update:checked="(checked: boolean) => todo.done ? toggleDone(todo) : toggleSelect(todo, checked)"
                    class="todo-checkbox"
                  />
                  <div
                    class="type-icon-wrapper"
                    :style="{ color: priorityConfig[todo.priority]?.color, background: (priorityConfig[todo.priority]?.color + '10') }"
                  >
                    <i :class="group.icon" />
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
                      size="tiny"
                      class="type-tag"
                      :bordered="false"
                      round
                    >
                      {{ group.label }}
                    </NTag>
                  </div>
                  <div class="todo-description">
                    {{ todo.desc || todo.description }}
                  </div>

                  <!-- Action Button & Metadata -->
                  <div class="todo-meta-row" v-if="!todo.done">
                    <!-- 开票确认：就地双选 -->
                    <template v-if="todo.meta?.ruleId === 'income_invoice'">
                      <NButton size="tiny" secondary type="primary" :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '已开票', 'issued')">已开票</NButton>
                      <NButton size="tiny" secondary :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '不开票', 'no_invoice')">不开票</NButton>
                    </template>
                    <!-- 征期：就地闭环 -->
                    <template v-else-if="todo.meta?.ruleId === 'filing_deadline'">
                      <NButton size="tiny" secondary type="primary" :loading="actingKeys.has(todo.key)" @click="markFiled(todo)">已申报</NButton>
                      <NButton size="tiny" secondary :loading="actingKeys.has(todo.key)" @click="markFiled(todo, true)">本月零申报</NButton>
                    </template>
                    <!-- 发票待付款：去配对（不想处理可直接忽略，勿提供伪装成"暂缓"的永久忽略入口） -->
                    <template v-else-if="todo.meta?.ruleId === 'invoice_unpaid'">
                      <NButton size="tiny" secondary type="primary" @click="goToMatchWizard(todo)">去配对流水</NButton>
                    </template>
                    <!-- 股东垫付：报销入账 / 记为往来（仅草稿可改；已过账走下方"查看关联交易"） -->
                    <template v-else-if="todo.meta?.ruleId === 'owner_transfer_nature' && isEntryEditable(todo)">
                      <NButton size="tiny" secondary type="primary" @click="router.push(`/ledger/${todo.meta?.entryId}?edit=1&focus=payer`)">报销入账</NButton>
                      <NButton size="tiny" secondary :loading="actingKeys.has(todo.key)" @click="setNatureOwnerLoan(todo)">记为往来</NButton>
                    </template>
                    <template v-else-if="getActionRoute(todo)">
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
                    </template>
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

/* Filter Group */
.filter-section {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}
.filter-group {
  background: var(--solo-border-light);
  padding: 4px;
  border-radius: var(--solo-radius);
  border: none;
  flex-wrap: wrap;
}
/* 分段控件轨道语法：灰轨上未选中透明、选中白底微浮起，让选中态靠色差成立 */
.todo-page .filter-group :deep(.n-radio-button) {
  background: transparent;
  border: none;
  border-radius: 6px;
}
.todo-page .filter-group :deep(.n-radio-group__splitor) {
  display: none;
}
.todo-page .filter-group :deep(.n-radio-button--checked) {
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(23, 43, 77, 0.12);
}
/* 选中/悬停/聚焦态的蓝框与双重光环全部关掉——选中态已由白底浮起表达 */
.todo-page .filter-group :deep(.n-radio-button__state-border) {
  border: none !important;
  box-shadow: none !important;
}

/* Todo Groups */
.todo-groups {
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 2px 4px;
  border: none;
  background: transparent;
  cursor: pointer;
  user-select: none;
}
.group-icon {
  width: 28px;
  height: 28px;
  border-radius: var(--solo-radius);
  background: rgba(99, 102, 241, 0.1);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
}
.group-label {
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.group-count {
  min-width: 22px;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  font-family: 'Outfit', 'Inter', sans-serif;
  color: var(--solo-text-secondary);
  background: var(--solo-border-light);
  border-radius: 999px;
  padding: 1px 8px;
}
.group-chevron {
  margin-left: auto;
  font-size: 18px;
  color: var(--solo-text-muted);
  transform: rotate(90deg);
  transition: transform 0.2s ease;
}
.group-chevron.is-collapsed {
  transform: rotate(0deg);
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
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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
.empty-state :deep(.n-empty__icon) {
  height: auto;  /* 自定义 64px 圆形图标超出 naive 默认 40px 图标盒，防压到描述文字 */
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
