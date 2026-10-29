<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NButton, NSpin, useMessage, NCheckbox, NPopconfirm, NDropdown, NInput } from 'naive-ui'
import { getTodos, updateTodoState, requestTodoAiAdvice, linkInvoiceToExpense, type SilentResolvedItem } from '@/api/todo'
import { updateEntry } from '@/api/record'
import { markDeadlineFiled } from '@/api/tax-filing'
import { formatMoney } from '@/utils/format'
import {
  fixedAssetTodoRoute,
  intangibleAssetTodoRoute,
  isFixedAssetTodo,
  isIntangibleAssetTodo,
} from '@/utils/todoAction'
import type { TodoItem } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
const route = useRoute()
const message = useMessage()

const todos = ref<TodoItem[]>([])
const autoResolved = ref<SilentResolvedItem[]>([])
const showSilentModal = ref(false)
const requestingAi = ref(false)
const loading = ref(true)
const updating = ref<Set<string>>(new Set())
// statusFilter = 'pending' (待处理) | 'done' (已办结)
const statusFilter = ref<'pending' | 'done'>('pending')
// 关键字搜索
const searchKeyword = ref('')
// tab = 事项分类（'all' + CATEGORY_ORDER 里的 key），选中态同步 URL ?tab=
const filterTab = ref('all')
// 是否进入批量管理模式
const isBatchMode = ref(false)
const dismissingBatch = ref(false)
const updatingBatch = ref(false)

// 事项分类：分组展示顺序 + 元数据（Remix Icon）
const CATEGORY_ORDER = ['filing', 'match', 'receipt', 'reimburse', 'billing', 'risk', 'intangible', 'other']

const categoryMeta: Record<string, { label: string; icon: string }> = {
  filing: { label: '待申报', icon: 'ri-calendar-check-line' },
  match: { label: '待配流水', icon: 'ri-arrow-left-right-line' },
  receipt: { label: '待补票据', icon: 'ri-file-receipt-line' },
  reimburse: { label: '待报销/付款', icon: 'ri-hand-coin-line' },
  billing: { label: '待开票', icon: 'ri-bill-line' },
  risk: { label: '风险复核', icon: 'ri-error-warning-line' },
  intangible: { label: '无形资产', icon: 'ri-copyright-line' },
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
  intangible_asset_amortization: 'intangible',
}

function todoCategory(todo: TodoItem): string {
  return todo.category || ruleIdCategory[String(todo.meta?.ruleId || '')] || 'other'
}

// 跨 tab 紧急可见性与全局看板统计
const highCount = computed(() => todos.value.filter((t) => !t.done && !t.dismissed && t.priority === 'high').length)
const pendingCount = computed(() => todos.value.filter((t) => !t.done && !t.dismissed).length)
const doneCount = computed(() => todos.value.filter((t) => t.done && !t.dismissed).length)
const totalPendingAmount = computed(() =>
  todos.value.filter((t) => !t.done && !t.dismissed).reduce((sum, t) => sum + (Number(t.amount) || 0), 0),
)

// 事项 tab：全部打头 + 有内容的分类（根据当前 statusFilter 统计，零计数隐藏）
const categoryTabs = computed(() => {
  const counts = new Map<string, number>()
  for (const todo of todos.value) {
    if (todo.dismissed) continue
    const matchesStatus = statusFilter.value === 'pending' ? !todo.done : todo.done
    if (!matchesStatus) continue
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
  let list = todos.value.filter((t) => !t.dismissed)
  // 1. 状态筛选
  if (statusFilter.value === 'pending') {
    list = list.filter((t) => !t.done)
  } else {
    list = list.filter((t) => t.done)
  }
  // 2. 分类筛选
  if (filterTab.value !== 'all') {
    list = list.filter((t) => todoCategory(t) === filterTab.value)
  }
  // 3. 关键字搜索
  const kw = searchKeyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(
      (t) =>
        t.title.toLowerCase().includes(kw)
        || (t.desc || t.description || '').toLowerCase().includes(kw),
    )
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

async function load(autoAi = true) {
  loading.value = true
  try {
    const resp = await getTodos()
    todos.value = resp.todos ?? []
    autoResolved.value = resp.autoResolved ?? []

    // 针对尚未得到 LLM 深度建议的条目，后台静默拉取一次
    if (autoAi && todos.value.some((t) => !t.done && !t.dismissed && t.meta?.aiSuggestion?.source !== 'llm')) {
      triggerAiAdvice()
    }
  } catch {
    message.error('加载待办失败')
  } finally {
    loading.value = false
  }
}

async function triggerAiAdvice() {
  if (requestingAi.value) return
  requestingAi.value = true
  try {
    const resp = await requestTodoAiAdvice()
    if (resp.todos && resp.todos.length > 0) {
      todos.value = resp.todos
    }
  } catch {
    // 静默降级，继续展示确定性规则建议
  } finally {
    requestingAi.value = false
  }
}

async function handleLinkInvoice(todo: TodoItem) {
  const invEntryId = String(todo.meta?.entryId || todo.entryId || '')
  const expEntryId = String(todo.meta?.candidateExpenseId || '')
  if (!invEntryId || !expEntryId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await linkInvoiceToExpense(invEntryId, expEntryId)
    message.success('已成功关联发票并完成核销，重复发票挂账已自动消除')
    await load(false)
  } catch {
    message.error('关联核销失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
  }
}

async function handleSetPayer(todo: TodoItem, payer: 'boss_personal' | 'employee_personal', label: string) {
  const entryId = String(todo.meta?.entryId || todo.entryId || '')
  if (!entryId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await updateEntry(entryId, {
      payer,
      paymentStatus: 'paid',
      billingStatus: 'reconciled',
      autoReverseRepost: true,
    })
    message.success(`已记为${label}并核销，待办已自动完成`)
    await load(false)
  } catch {
    message.error('操作失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
  }
}

async function handleSetUnpaid(todo: TodoItem) {
  const entryId = String(todo.meta?.entryId || todo.entryId || '')
  if (!entryId || actingKeys.value.has(todo.key)) return
  actingKeys.value.add(todo.key)
  try {
    await updateEntry(entryId, {
      paymentStatus: 'unpaid',
      billingStatus: 'reconciled',
      autoReverseRepost: true,
    })
    message.success('已记为暂未付款挂账，待办已自动完成')
    await load(false)
  } catch {
    message.error('操作失败，请重试')
  } finally {
    actingKeys.value.delete(todo.key)
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
    if (billingStatus === 'no_invoice') {
      message.success('已标记未开票收入（小规模季度 ≤ 30 万全额免税），待办已完成')
    } else {
      message.success(`已标记${label}，待办已自动完成`)
    }
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

function toggleBatchMode() {
  isBatchMode.value = !isBatchMode.value
  if (!isBatchMode.value) {
    selectedKeys.value.clear()
  }
}

function handleCheckboxClick(todo: TodoItem, checked: boolean) {
  if (isBatchMode.value) {
    toggleSelect(todo, checked)
  } else {
    toggleDone(todo)
  }
}

// 批量标记办结
async function batchMarkDone() {
  const keys = [...selectedKeys.value]
  if (keys.length === 0 || updatingBatch.value) return
  updatingBatch.value = true
  let failed = 0
  for (const key of keys) {
    try {
      await updateTodoState(key, { done: true })
    } catch {
      failed += 1
    }
  }
  updatingBatch.value = false
  selectedKeys.value.clear()
  isBatchMode.value = false
  const doneNum = keys.length - failed
  message.success(
    failed
      ? `已办结 ${doneNum} 项，${failed} 项失败`
      : `已批量办结 ${doneNum} 项待办`,
  )
  await load()
}

// 征期税种聚合相关辅助
function extractFilingPeriod(title: string): string {
  const match = title.match(/（([^）]+)）/)
  return match ? match[1] : '本期'
}

function extractTaxName(title: string): string {
  return title
    .replace(/^.+?-\s*/, '')
    .replace(/（\d{4}[^）]*）\s*$/, '')
    .trim()
}

interface TaxPeriodBundle {
  period: string
  items: TodoItem[]
  urgentCount: number
}

function getPeriodBundles(items: TodoItem[]): { bundles: TaxPeriodBundle[]; singles: TodoItem[] } {
  const periodMap = new Map<string, TodoItem[]>()
  const singles: TodoItem[] = []

  for (const item of items) {
    if (item.meta?.ruleId === 'filing_deadline') {
      const period = extractFilingPeriod(item.title)
      if (!periodMap.has(period)) periodMap.set(period, [])
      periodMap.get(period)!.push(item)
    } else {
      singles.push(item)
    }
  }

  const bundles: TaxPeriodBundle[] = []
  for (const [period, pItems] of periodMap.entries()) {
    if (pItems.length >= 2) {
      bundles.push({
        period,
        items: pItems,
        urgentCount: pItems.filter((i) => i.priority === 'high').length,
      })
    } else {
      singles.push(...pItems)
    }
  }

  return { bundles, singles }
}

const expandedTaxBundles = ref<Set<string>>(new Set())

function toggleTaxBundle(period: string) {
  const next = new Set(expandedTaxBundles.value)
  if (next.has(period)) next.delete(period)
  else next.add(period)
  expandedTaxBundles.value = next
}

async function markAllTaxFiled(bundle: TaxPeriodBundle, zeroFiling = false) {
  let failed = 0
  for (const item of bundle.items) {
    const deadlineId = String(item.meta?.deadlineId || '')
    if (!deadlineId) continue
    try {
      await markDeadlineFiled(deadlineId, zeroFiling ? { zeroFiling: true } : undefined)
    } catch {
      failed += 1
    }
  }
  const doneNum = bundle.items.length - failed
  message.success(
    failed
      ? `已处理 ${doneNum} 项，${failed} 项失败`
      : zeroFiling
        ? `已将 ${bundle.period} 全部税种按零申报归档`
        : `已标记 ${bundle.period} 全部税种在税局申报完成`,
  )
  await load()
}

function handleOtherInvoiceAction(todo: TodoItem, key: string) {
  if (key === 'boss_personal') {
    handleSetPayer(todo, 'boss_personal', '老板垫付')
  } else if (key === 'employee_personal') {
    handleSetPayer(todo, 'employee_personal', '员工垫付')
  } else if (key === 'unpaid') {
    handleSetUnpaid(todo)
  } else if (key === 'match') {
    goToMatchWizard(todo)
  }
}

function filterUrgent() {
  statusFilter.value = 'pending'
  filterTab.value = 'all'
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

function shouldShowTodoItem(groupCategory: string, todo: TodoItem): boolean {
  if (groupCategory !== 'filing' || statusFilter.value !== 'pending') return true
  if (todo.meta?.ruleId !== 'filing_deadline') return true
  const period = extractFilingPeriod(todo.title)
  const itemsInPeriod = filteredTodos.value.filter(
    (t) => t.meta?.ruleId === 'filing_deadline' && extractFilingPeriod(t.title) === period,
  )
  if (itemsInPeriod.length >= 2 && !expandedTaxBundles.value.has(period)) {
    return false
  }
  return true
}

function getActionRoute(todo: TodoItem) {
  if (todo.meta?.ruleId === 'tax_registration_unsynced') {
    return '/tax-registration'
  }
  if (isFixedAssetTodo(todo)) {
    return fixedAssetTodoRoute(todo)
  }
  if (isIntangibleAssetTodo(todo)) {
    return intangibleAssetTodoRoute(todo)
  }
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
  if (todo.meta?.action === 'review_intangible' || todo.meta?.action === 'view_intangible') {
    return `/intangibles/${todo.meta?.assetId || ''}`
  }
  if (todo.title.includes('无形资产')) {
    return '/intangibles'
  }
  return null
}

function getActionLabel(todo: TodoItem) {
  if (todo.meta?.ruleId === 'tax_registration_unsynced') {
    return '去同步认定'
  }
  if (isFixedAssetTodo(todo)) {
    return '确认折旧方案'
  }
  if (isIntangibleAssetTodo(todo)) {
    return '确认摊销方案'
  }
  if (todo.meta?.entryId || todo.entryId) {
    return '查看关联交易'
  }
  if (todo.type === 'bank') {
    return '去流水对账'
  }
  if (todo.meta?.action === 'confirm_asset' || todo.title.includes('固定资产')) {
    return '设置折旧'
  }
  if (todo.meta?.action === 'review_intangible') {
    return '去复核'
  }
  if (todo.meta?.action === 'view_intangible') {
    return '查看资产'
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

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="todo-page">
    <div class="page-header">
      <div class="header-left">
        <div class="title-with-status">
          <h2>待办事项</h2>
          <div class="status-tabs-pill">
            <button
              type="button"
              class="status-pill-btn"
              :class="{ 'is-active': statusFilter === 'pending' }"
              @click="statusFilter = 'pending'"
            >
              待处理 <span class="pill-badge">{{ pendingCount }}</span>
            </button>
            <button
              type="button"
              class="status-pill-btn"
              :class="{ 'is-active': statusFilter === 'done' }"
              @click="statusFilter = 'done'"
            >
              已办结 <span class="pill-badge">{{ doneCount }}</span>
            </button>
          </div>
        </div>
        <p class="subtitle">智能识别票据缺失、流水未配、税务合规及折旧等关键待办</p>
      </div>
      <div class="header-right">
        <NInput
          v-model:value="searchKeyword"
          placeholder="搜索待办事项..."
          size="small"
          clearable
          class="todo-search-box"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <NButton
          secondary
          size="small"
          :type="isBatchMode ? 'primary' : 'default'"
          @click="toggleBatchMode"
        >
          <template #icon><i :class="isBatchMode ? 'ri-close-line' : 'ri-list-check-2'" /></template>
          {{ isBatchMode ? '退出批量' : '批量管理' }}
        </NButton>
        <NButton
          v-if="todos.length > 0"
          secondary
          size="small"
          :loading="requestingAi"
          @click="triggerAiAdvice"
        >
          <template #icon><i class="ri-sparkling-line" /></template>
          AI 分析
        </NButton>
        <NButton secondary size="small" :loading="loading" @click="() => load(false)">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新
        </NButton>
      </div>
    </div>

    <!-- 顶部 4 格待办态势看板 -->
    <div class="stats-grid">
      <div class="stat-card clickable" :class="{ 'has-urgent': highCount > 0 }" @click="filterUrgent">
        <div class="stat-card-head">
          <span class="stat-label">紧急与临期</span>
          <span class="stat-badge urgent" v-if="highCount > 0">{{ highCount }} 项需优先处理</span>
          <span class="stat-badge safe" v-else>无紧急项</span>
        </div>
        <div class="stat-value" :class="{ 'urgent-text': highCount > 0 }">{{ highCount }}</div>
        <div class="stat-desc">申报截止日或需优先核验的事项</div>
      </div>
      <div class="stat-card clickable" @click="statusFilter = 'pending'">
        <div class="stat-card-head">
          <span class="stat-label">待处理事项</span>
          <span class="stat-badge default">{{ pendingCount }} 项</span>
        </div>
        <div class="stat-value">{{ pendingCount }}</div>
        <div class="stat-desc">日常记账、对账与开票待跟进事项</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-head">
          <span class="stat-label">涉及金额</span>
          <span class="stat-badge info">待办总额</span>
        </div>
        <div class="stat-value">{{ formatMoney(totalPendingAmount) }}</div>
        <div class="stat-desc">{{ totalPendingAmount === 0 ? '当前待办无资金挂账' : '待处理流水与待确认单据总金额' }}</div>
      </div>
      <div class="stat-card clickable" @click="statusFilter = 'done'">
        <div class="stat-card-head">
          <span class="stat-label">已办结事项</span>
          <span class="stat-badge success">已完成</span>
        </div>
        <div class="stat-value text-success">{{ doneCount }}</div>
        <div class="stat-desc">本期已处理并归档的待办条目</div>
      </div>
    </div>

    <!-- 🤖 系统静默处理完成留痕横幅（给老板定心丸） -->
    <div v-if="autoResolved.length > 0" class="auto-resolved-banner">
      <div class="banner-left">
        <i class="ri-shield-check-fill banner-icon" />
        <span>系统已自动为您静默核验 <strong>{{ autoResolved.length }}</strong> 笔内部资金划转/结息事项（无需手工开票），账目合规清楚。</span>
      </div>
      <button type="button" class="banner-btn" @click="showSilentModal = !showSilentModal">
        {{ showSilentModal ? '收起明细' : '查看明细' }}
        <i :class="showSilentModal ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
      </button>
    </div>

    <!-- 静默处理事项抽屉/列表 -->
    <div v-if="showSilentModal && autoResolved.length > 0" class="silent-items-wrapper">
      <div v-for="item in autoResolved" :key="item.id" class="silent-item-row">
        <div class="silent-item-info">
          <span class="silent-item-tag">{{ item.categoryLabel || '自动免开票' }}</span>
          <strong class="silent-item-title">{{ item.title }}</strong>
          <span class="silent-item-date">{{ item.date }}</span>
          <p class="silent-item-reason">{{ item.reason }}</p>
        </div>
        <div class="silent-item-amt">
          {{ formatMoney(item.amount) }}
        </div>
      </div>
    </div>

    <!-- 分类与快捷筛选栏 -->
    <div class="filter-bar">
      <div class="tabs-scroller">
        <button
          v-for="tab in categoryTabs"
          :key="tab.key"
          type="button"
          class="category-tab-btn"
          :class="{ 'is-active': filterTab === tab.key }"
          @click="filterTab = tab.key"
        >
          <span class="tab-label">{{ tab.label }}</span>
          <span class="tab-counter">{{ tab.count }}</span>
        </button>
      </div>
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
            <!-- 征期税种多项智能聚合卡片：同周期 >= 2 项时集中呈现 -->
            <template v-if="group.category === 'filing' && statusFilter === 'pending'">
              <div
                v-for="bundle in getPeriodBundles(group.items).bundles"
                :key="bundle.period"
                class="tax-bundle-box"
              >
                <div class="bundle-main-row">
                  <div class="bundle-content">
                    <div class="bundle-header-row">
                      <span v-if="bundle.urgentCount > 0" class="priority-pill urgent">紧急临期</span>
                      <strong class="bundle-title">{{ bundle.period }} 税费综合申报（共 {{ bundle.items.length }} 项）</strong>
                    </div>
                    <div class="bundle-tax-tags">
                      <span v-for="t in bundle.items" :key="t.key" class="bundle-tax-tag">
                        <i class="ri-checkbox-circle-line" /> {{ extractTaxName(t.title) }}
                      </span>
                    </div>
                  </div>
                  <div class="bundle-actions">
                    <NButton size="small" type="primary" @click="router.push('/tax-filing')">
                      <template #icon><i class="ri-external-link-line" /></template>
                      申报向导
                    </NButton>
                    <NDropdown
                      trigger="click"
                      :options="[
                        { label: '一键全部标记在税局申报', key: 'filed' },
                        { label: '一键全部按零申报归档', key: 'zero' },
                      ]"
                      @select="(key) => markAllTaxFiled(bundle, key === 'zero')"
                    >
                      <NButton size="small" secondary>
                        一键办结 <i class="ri-arrow-down-s-line" />
                      </NButton>
                    </NDropdown>
                    <NButton size="small" quaternary @click="toggleTaxBundle(bundle.period)">
                      {{ expandedTaxBundles.has(bundle.period) ? '收起明细' : '查看各税种' }}
                      <i :class="expandedTaxBundles.has(bundle.period) ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
                    </NButton>
                  </div>
                </div>
              </div>
            </template>

            <!-- 单个待办卡片列表 -->
            <template v-for="todo in group.items" :key="todo.key">
              <div
                v-if="shouldShowTodoItem(group.category, todo)"
                class="todo-card-wrapper"
                :class="{ 'is-done': todo.done, 'is-selected': isBatchMode && selectedKeys.has(todo.key) }"
              >
                <div class="card-select-area">
                  <NCheckbox
                    :checked="isBatchMode ? selectedKeys.has(todo.key) : todo.done"
                    :disabled="updating.has(todo.key)"
                    @update:checked="(checked: boolean) => handleCheckboxClick(todo, checked)"
                    class="todo-checkbox"
                    :title="isBatchMode ? (selectedKeys.has(todo.key) ? '取消选择' : '选中此项') : (todo.done ? '点击恢复为未完成' : '点击标为完成')"
                  />
                </div>

                <div class="card-main-area">
                  <div class="card-headline-row">
                    <div class="title-with-priority">
                      <span v-if="todo.priority === 'high'" class="priority-pill urgent">紧急</span>
                      <span v-else-if="todo.priority === 'medium'" class="priority-pill medium">重要</span>
                      <span class="todo-title" :class="{ 'strike-through': todo.done }">
                        {{ todo.title }}
                      </span>
                    </div>
                    <div class="card-meta-right" v-if="todo.amount">
                      <span class="amount-val">{{ formatMoney(todo.amount) }}</span>
                    </div>
                  </div>

                  <div v-if="todo.desc || todo.description" class="todo-desc-text">
                    {{ todo.desc || todo.description }}
                  </div>

                  <!-- 💡 AI 顾问建议与大白话理由（为不懂财务的小白解惑） -->
                  <div v-if="todo.meta?.aiSuggestion" class="todo-ai-box">
                    <div class="ai-box-badge">
                      <i class="ri-sparkling-fill" />
                      <span>{{ todo.meta.aiSuggestion.source === 'llm' ? 'AI 顾问建议' : '智能建议' }}</span>
                    </div>
                    <div class="ai-box-content">
                      <div class="ai-reason-text">
                        {{ todo.meta.aiSuggestion.reason }}
                      </div>
                    </div>
                  </div>

                  <!-- 动作操作条：未完成态 -->
                  <div class="card-actions-row" v-if="!todo.done">
                    <div class="primary-actions">
                      <!-- 1. 发票待付款/报销 -->
                      <template v-if="todo.meta?.ruleId === 'invoice_unpaid'">
                        <template v-if="todo.meta?.candidateExpenseId">
                          <NButton
                            size="small"
                            type="primary"
                            :loading="actingKeys.has(todo.key)"
                            @click="handleLinkInvoice(todo)"
                          >
                            <template #icon><i class="ri-link-m" /></template>
                            关联「{{ todo.meta.candidateExpenseTitle }}」核销
                          </NButton>
                          <NButton size="small" secondary :loading="actingKeys.has(todo.key)" @click="handleSetPayer(todo, 'boss_personal', '老板垫付')">
                            记为老板垫付
                          </NButton>
                          <NDropdown
                            trigger="click"
                            :options="[
                              { label: '记为员工垫付', key: 'employee_personal' },
                              { label: '暂未付款挂账', key: 'unpaid' },
                              { label: '前往配对流水', key: 'match' },
                            ]"
                            @select="(key) => handleOtherInvoiceAction(todo, key)"
                          >
                            <NButton size="small" quaternary>
                              更多 <i class="ri-arrow-down-s-line" />
                            </NButton>
                          </NDropdown>
                        </template>
                        <template v-else>
                          <NButton size="small" type="primary" :loading="actingKeys.has(todo.key)" @click="handleSetPayer(todo, 'boss_personal', '老板垫付')">
                            <template #icon><i class="ri-user-star-line" /></template>
                            记为老板垫付
                          </NButton>
                          <NButton size="small" secondary :loading="actingKeys.has(todo.key)" @click="handleSetPayer(todo, 'employee_personal', '员工垫付')">
                            员工垫付
                          </NButton>
                          <NDropdown
                            trigger="click"
                            :options="[
                              { label: '暂未付款挂账', key: 'unpaid' },
                              { label: '前往配对流水', key: 'match' },
                            ]"
                            @select="(key) => handleOtherInvoiceAction(todo, key)"
                          >
                            <NButton size="small" quaternary>
                              更多 <i class="ri-arrow-down-s-line" />
                            </NButton>
                          </NDropdown>
                        </template>
                      </template>

                      <!-- 2. 待补票据（独立无票支出） -->
                      <template v-else-if="todo.meta?.ruleId === 'missing_document'">
                        <NButton size="small" type="primary" :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '无票支出', 'no_invoice')">
                          <template #icon><i class="ri-file-text-line" /></template>
                          按无票支出入账
                        </NButton>
                        <NButton size="small" secondary :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '已有收据/小票', 'has_receipt')">
                          已有收据/小票
                        </NButton>
                        <NButton
                          v-if="todo.meta?.entryId || todo.entryId"
                          size="small"
                          quaternary
                          @click="router.push(`/ledger/${todo.meta?.entryId || todo.entryId}`)"
                        >
                          查看凭证 <i class="ri-arrow-right-line" />
                        </NButton>
                      </template>

                      <!-- 3. 开票确认 -->
                      <template v-else-if="todo.meta?.ruleId === 'income_invoice'">
                        <NTooltip trigger="hover" placement="top">
                          <template #trigger>
                            <NButton size="small" type="primary" :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '未开票收入', 'no_invoice')">
                              <template #icon><i class="ri-receipt-line" /></template>
                              按未开票收入申报
                            </NButton>
                          </template>
                          若客户未索要发票，可直接按未开票收入申报。小规模纳税人季度 30 万元以内全额免征增值税，期末结账时自动冲减税额。
                        </NTooltip>
                        <NButton size="small" secondary :loading="actingKeys.has(todo.key)" @click="setBillingStatus(todo, '已开票', 'issued')">
                          已开发票
                        </NButton>
                      </template>

                      <!-- 4. 征期申报 -->
                      <template v-else-if="todo.meta?.ruleId === 'filing_deadline'">
                        <NButton size="small" type="primary" :loading="actingKeys.has(todo.key)" @click="markFiled(todo)">
                          <template #icon><i class="ri-checkbox-circle-line" /></template>
                          已在税局申报
                        </NButton>
                        <NButton size="small" secondary :loading="actingKeys.has(todo.key)" @click="markFiled(todo, true)">
                          本月零申报
                        </NButton>
                      </template>

                      <!-- 5. 股东垫付往来 -->
                      <template v-else-if="todo.meta?.ruleId === 'owner_transfer_nature' && isEntryEditable(todo)">
                        <NButton size="small" type="primary" :loading="actingKeys.has(todo.key)" @click="setNatureOwnerLoan(todo)">
                          <template #icon><i class="ri-exchange-dollar-line" /></template>
                          记为股东往来
                        </NButton>
                        <NButton size="small" secondary @click="router.push(`/ledger/${todo.meta?.entryId}?edit=1&focus=payer`)">
                          报销入账
                        </NButton>
                      </template>

                      <!-- 其他动作路由 -->
                      <template v-else-if="getActionRoute(todo)">
                        <NButton
                          secondary
                          type="primary"
                          size="small"
                          class="action-btn"
                          @click="router.push(getActionRoute(todo)!)"
                        >
                          <span>{{ getActionLabel(todo) }}</span>
                          <i class="ri-arrow-right-line action-icon" />
                        </NButton>
                        <NButton
                          v-if="(isFixedAssetTodo(todo) || isIntangibleAssetTodo(todo)) && todo.meta?.entryId"
                          text
                          size="small"
                          @click="router.push(`/ledger/${todo.meta.entryId}`)"
                        >
                          查看交易
                        </NButton>
                      </template>
                    </div>

                    <div class="secondary-actions">
                      <NPopconfirm @positive-click="dismissTodo(todo)">
                        <template #trigger>
                          <NButton text size="small" class="dismiss-btn" :loading="updating.has(todo.key)">
                            <i class="ri-close-circle-line" />&nbsp;忽略
                          </NButton>
                        </template>
                        确认忽略该待办事项？忽略后可在账目中重新查阅。
                      </NPopconfirm>
                    </div>
                  </div>

                  <!-- 动作操作条：已完成态 -->
                  <div class="card-actions-row" v-else>
                    <div class="primary-actions">
                      <span class="done-tag"><i class="ri-checkbox-circle-fill" /> 已办结归档</span>
                    </div>
                    <div class="secondary-actions">
                      <NButton text size="small" class="reopen-btn" :loading="updating.has(todo.key)" @click="toggleDone(todo)">
                        恢复为待处理
                      </NButton>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- Empty State -->
      <div v-else-if="!loading" class="empty-state">
        <SoloEmpty
          :title="statusFilter === 'done' ? '暂无已办结事项' : '太棒了，目前没有待处理事项'"
          :description="statusFilter === 'done' ? '办结后的待办事项会在此处归档备查。' : '新的草稿、征期和对账任务会出现在这里。'"
          icon="ri-checkbox-circle-line"
        />
      </div>
    </NSpin>

    <!-- 底部悬浮批量操作栏 -->
    <Transition name="slide-up">
      <div v-if="isBatchMode && selectedKeys.size > 0" class="floating-batch-bar">
        <div class="batch-bar-content">
          <span class="batch-count-info">已选择 <strong>{{ selectedKeys.size }}</strong> 项待办</span>
          <div class="batch-actions">
            <NButton
              size="small"
              type="primary"
              :loading="updatingBatch"
              @click="batchMarkDone"
            >
              <template #icon><i class="ri-check-line" /></template>
              批量标记办结
            </NButton>
            <template v-if="allSelectedAreBilling">
              <NButton
                size="small"
                secondary
                :loading="billingBatch"
                @click="batchSetBillingStatus('issued', '已开票')"
              >
                批量已开票
              </NButton>
              <NButton
                size="small"
                secondary
                :loading="billingBatch"
                @click="batchSetBillingStatus('no_invoice', '不开票')"
              >
                批量不开票
              </NButton>
            </template>
            <NPopconfirm @positive-click="dismissSelected">
              <template #trigger>
                <NButton size="small" secondary type="warning" :loading="dismissingBatch">
                  忽略所选
                </NButton>
              </template>
              确认批量忽略选中的 {{ selectedKeys.size }} 项待办？
            </NPopconfirm>
            <NButton size="small" quaternary @click="selectedKeys.clear()">
              清空所选
            </NButton>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.todo-page {
  max-width: 1040px;
  margin: 0 auto;
  padding: 20px 16px 72px 16px;
  box-sizing: border-box;
}

/* 页头 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  gap: 16px;
  flex-wrap: wrap;
}
.header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.title-with-status {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.title-with-status h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  letter-spacing: -0.3px;
}
.status-tabs-pill {
  display: inline-flex;
  align-items: center;
  background: var(--solo-bg-soft, #f1f4f8);
  padding: 3px;
  border-radius: 8px;
  gap: 2px;
}
.status-pill-btn {
  border: none;
  background: transparent;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #586474);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s ease;
}
.status-pill-btn:hover {
  color: var(--solo-text-primary, #182230);
}
.status-pill-btn.is-active {
  background: #ffffff;
  color: var(--solo-primary, #1747b0);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.pill-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.05);
  font-variant-numeric: tabular-nums;
}
.status-pill-btn.is-active .pill-badge {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}
.subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-muted, #636e7c);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.todo-search-box {
  width: 190px;
}

/* 🤖 静默核验留痕横幅 */
.auto-resolved-banner {
  background: var(--solo-income-bg, #f0faf5);
  border: 1px solid rgba(15, 115, 84, 0.2);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  font-size: 13px;
  color: var(--solo-income, #0f7354);
}
.banner-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.banner-icon {
  font-size: 18px;
  color: var(--solo-income, #0f7354);
}
.banner-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--solo-primary, #1747b0);
  font-weight: 600;
  font-size: 12.5px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.silent-items-wrapper {
  background: #ffffff;
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: 8px;
  padding: 12px;
  margin-top: -12px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.silent-item-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  background: var(--solo-bg-soft, #f8fafc);
  border-radius: 6px;
  font-size: 12.5px;
}
.silent-item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.silent-item-tag {
  align-self: flex-start;
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #eaf7f1;
  color: #0f7354;
  font-weight: 600;
}
.silent-item-title {
  color: var(--solo-text-primary, #182230);
}
.silent-item-date {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}
.silent-item-reason {
  margin: 2px 0 0;
  font-size: 11.5px;
  color: var(--solo-text-secondary, #586474);
}
.silent-item-amt {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  color: var(--solo-income, #0f7354);
}

/* 💡 AI 建议提示条 */
.todo-ai-box {
  background: var(--solo-bg-blue-soft, #edf3ff);
  border: 1px solid rgba(23, 71, 176, 0.15);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 4px;
  margin-bottom: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.ai-box-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-primary, #1747b0);
}
.ai-reason-text {
  font-size: 12.5px;
  color: var(--solo-text-primary, #182230);
  line-height: 1.45;
}

/* 顶部 4 格待办态势看板 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 22px;
}
.stat-card {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  transition: all 0.2s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.stat-card.clickable {
  cursor: pointer;
}
.stat-card:hover {
  border-color: rgba(23, 71, 176, 0.3);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.04);
}
.stat-card.has-urgent {
  border-color: rgba(189, 61, 61, 0.3);
  background: #ffffff;
}
.stat-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.stat-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary, #586474);
}
.stat-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
}
/* 状态徽标统一走语义 token：此前这里是 Arco 的配色（绿 00b42a / 红 f53f3f），
   与品牌语义色并存，且浅底上仅 2.6–3.3:1，白底小字不达标。 */
.stat-badge.urgent {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
}
.stat-badge.safe {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.stat-badge.default {
  background: rgba(0, 0, 0, 0.05);
  color: var(--solo-text-secondary, #586474);
}
.stat-badge.info {
  background: var(--solo-info-bg);
  color: var(--solo-info);
}
.stat-badge.success {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  margin: 8px 0 4px;
  font-variant-numeric: tabular-nums;
}
.stat-value.urgent-text {
  color: var(--solo-expense);
}
.stat-value.text-success {
  color: var(--solo-income);
}
.stat-desc {
  font-size: 11.5px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 分类 Tab 栏 */
.filter-bar {
  margin-bottom: 20px;
  border-bottom: 1px solid var(--solo-border, #dde3ea);
  padding-bottom: 8px;
}
.tabs-scroller {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.category-tab-btn {
  appearance: none;
  border: none;
  background: transparent;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #586474);
  transition: all 0.18s ease;
  white-space: nowrap;
}
.category-tab-btn:hover {
  background: rgba(0, 0, 0, 0.04);
  color: var(--solo-text-primary, #182230);
}
.category-tab-btn.is-active {
  background: var(--solo-primary, #1747b0);
  color: #ffffff;
  font-weight: 600;
}
.tab-counter {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.06);
  color: inherit;
  font-variant-numeric: tabular-nums;
}
.category-tab-btn.is-active .tab-counter {
  background: rgba(255, 255, 255, 0.24);
  color: #ffffff;
}

/* 待办分组 */
.todo-groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  background: transparent;
  border: none;
  cursor: pointer;
  margin-bottom: 8px;
  user-select: none;
}
.group-icon {
  font-size: 16px;
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
}
.group-label {
  font-size: 14.5px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}
.group-count {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-text-muted, #636e7c);
  /* 原本是半透明黑叠在分组头底色上，实际落到一个比 --solo-bg-soft 更暗的灰，
     muted 在它上面只有 4.43:1；改用 token 底色后回到 4.53 */
  background: var(--solo-bg-soft);
  padding: 1px 7px;
  border-radius: 999px;
}
.group-chevron {
  margin-left: auto;
  font-size: 18px;
  color: var(--solo-text-muted, #636e7c);
  transform: rotate(90deg);
  transition: transform 0.2s ease;
}
.group-chevron.is-collapsed {
  transform: rotate(0deg);
}

/* 待办卡片列表 */
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 征期税种多项综合卡片 */
.tax-bundle-box {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid rgba(189, 61, 61, 0.25);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 14px 18px;
  margin-bottom: 2px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  transition: all 0.2s ease;
}
.tax-bundle-box:hover {
  border-color: rgba(189, 61, 61, 0.45);
  box-shadow: 0 4px 12px rgba(189, 61, 61, 0.04);
}
.bundle-main-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.bundle-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bundle-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.bundle-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
}
.bundle-tax-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.bundle-tax-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-secondary, #586474);
  font-weight: 500;
}
.bundle-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-wrap: wrap;
}
.todo-card-wrapper {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 14px 18px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  transition: all 0.2s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.todo-card-wrapper:hover {
  border-color: rgba(23, 71, 176, 0.35);
  box-shadow: 0 4px 12px rgba(23, 71, 176, 0.05);
  transform: translateY(-1px);
}
.todo-card-wrapper.is-selected {
  border-color: var(--solo-primary, #1747b0);
  background: linear-gradient(180deg, rgba(23, 71, 176, 0.02) 0%, var(--solo-bg-card, #ffffff) 100%);
}
.todo-card-wrapper.is-done {
  opacity: 0.55;
  background: rgba(0, 0, 0, 0.01);
}
.card-select-area {
  padding-top: 2px;
  flex-shrink: 0;
}
.card-main-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card-headline-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.title-with-priority {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}
.priority-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}
.priority-pill.urgent {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
  border: 1px solid rgba(189, 61, 61, 0.2);
}
.priority-pill.medium {
  /* 原来是 Arco 橙 #ff7d00 配 #fff7e8，11px 下只有 2.41:1 —— 等于看不见 */
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  border: 1px solid rgba(160, 92, 16, 0.2);
}
.todo-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  line-height: 1.4;
}
.todo-title.strike-through {
  text-decoration: line-through;
  color: var(--solo-text-muted, #636e7c);
}
.card-meta-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  margin-left: auto;
}
.amount-val {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.todo-desc-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--solo-text-secondary, #586474);
}
.card-actions-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border, #edf0f4);
}
.primary-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.secondary-actions {
  margin-left: auto;
}
.done-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-income, #0f7354);
}
.reopen-btn {
  color: var(--solo-text-muted, #636e7c);
  font-size: 12px;
}
.reopen-btn:hover {
  color: var(--solo-primary, #1747b0);
}
.dismiss-btn {
  color: var(--solo-text-muted, #636e7c);
  font-size: 12px;
  transition: color 0.15s ease;
}
.dismiss-btn:hover {
  color: #bd3d3d;
}
.action-icon {
  margin-left: 4px;
}

/* 底部悬浮批量操作栏 */
.floating-batch-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 99;
  background: #182230;
  color: #ffffff;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22);
  padding: 10px 20px;
}
.batch-bar-content {
  display: flex;
  align-items: center;
  gap: 20px;
}
.batch-count-info {
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.85);
}
.batch-count-info strong {
  color: #ffffff;
  font-size: 15px;
}
.batch-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translate(-50%, 20px);
}

/* Empty State */
.empty-state {
  display: flex;
  justify-content: center;
  padding: 64px 0;
  background: var(--solo-bg-card, #ffffff);
  border: 1px dashed var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-lg, 12px);
}
.empty-icon-circle {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: rgba(24, 160, 88, 0.1);
  color: #0f7354;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  margin: 0 auto 12px auto;
}

@media (max-width: 900px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 600px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .floating-batch-bar {
    width: calc(100% - 32px);
    left: 16px;
    transform: none;
    bottom: 16px;
  }
  .batch-bar-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
}
</style>
