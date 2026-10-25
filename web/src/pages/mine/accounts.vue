<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, h, onMounted, watch } from 'vue'
import {
  NCard, NButton, NSpace, useMessage,
  NInput, NModal, NForm, NFormItem, NSpin, NTree, NSwitch,
  NTabs, NTab, NGrid, NGridItem, NStatistic, NSelect
} from 'naive-ui'
import type { TreeOption } from 'naive-ui'
import apiClient from '@/api/client'

interface AccountNode {
  id: string
  company_id: string
  code: string
  name: string
  parent_code: string | null
  level: number
  category: string | null
  type: string | null
  is_leaf: boolean
  is_system: boolean
  is_active: boolean
  quantity_enabled?: boolean
  unit?: string | null
  created_at: string
  updated_at: string
  children: AccountNode[]
}

const message = useMessage()

// Reference components to satisfy strict TS unused imports checker
NStatistic

const loading = ref(false)
const treeData = ref<AccountNode[]>([])
const activeTab = ref('all')
const pattern = ref('')
const expandedKeys = ref<string[]>([])

const showAddModal = ref(false)
const isSubaccountCreation = ref(false)
const parentNodeRef = ref<AccountNode | null>(null)

const addForm = ref({
  code: '',
  name: '',
  parent_code: null as string | null,
  level: 1,
  category: '资产类',
  type: '流动资产',
})

const showDeleteConfirm = ref(false)
const deleteTargetCode = ref('')
const deleteTargetName = ref('')

// Category Options for new Level 1 accounts
const categoryOptions = [
  { label: '资产类', value: '资产类' },
  { label: '负债类', value: '负债类' },
  { label: '权益类', value: '权益类' },
  { label: '成本类', value: '成本类' },
  { label: '损益-收入类', value: '损益-收入类' },
  { label: '损益-费用类', value: '损益-费用类' },
]

// Dynamic Type Options based on chosen Category
const typeOptions = computed(() => {
  switch (addForm.value.category) {
    case '资产类':
      return [
        { label: '流动资产', value: '流动资产' },
        { label: '非流动资产', value: '非流动资产' },
      ]
    case '负债类':
      return [{ label: '流动负债', value: '流动负债' }]
    case '权益类':
      return [{ label: '所有者权益', value: '所有者权益' }]
    case '成本类':
      return [{ label: '成本', value: '成本' }]
    case '损益-收入类':
      return [{ label: '收入', value: '收入' }]
    case '损益-费用类':
      return [{ label: '费用', value: '费用' }]
    default:
      return []
  }
})

// Statistics computed property
const stats = computed(() => {
  let total = 0
  let system = 0
  let custom = 0

  function traverse(nodes: AccountNode[]) {
    for (const n of nodes) {
      if (n.level > 0) {
        total++
        if (n.is_system) {
          system++
        } else {
          custom++
        }
      }
      if (n.children && n.children.length > 0) {
        traverse(n.children)
      }
    }
  }

  traverse(treeData.value)
  return { total, system, custom }
})

function findNode(nodes: AccountNode[], code: string): AccountNode | undefined {
  for (const n of nodes) {
    if (n.code === code) return n
    if (n.children) {
      const found = findNode(n.children, code)
      if (found) return found
    }
  }
  return undefined
}

function getTypeClass(type: string | null): string {
  switch (type) {
    case '流动资产': return 'flow-asset'
    case '非流动资产': return 'nonflow-asset'
    case '流动负债': return 'flow-liability'
    case '所有者权益': return 'equity'
    case '成本': return 'cost'
    case '收入': return 'income'
    case '费用': return 'expense'
    default: return 'default'
  }
}

// Convert nodes to Naive UI tree options
function convertToTreeOptions(nodes: AccountNode[]): TreeOption[] {
  return nodes.map((n) => ({
    key: n.code,
    label: n.level === 0 ? n.name : `${n.code} ${n.name}`,
    code: n.code,
    level: n.level,
    is_system: n.is_system,
    type: n.type,
    isLeaf: n.children.length === 0,
    accountNode: n,
    children: n.children.length > 0 ? convertToTreeOptions(n.children) : undefined,
  }))
}

// Filter the tree by active category tab
const filteredTreeData = computed(() => {
  if (activeTab.value === 'all') {
    return treeData.value
  }
  if (activeTab.value === 'asset') {
    return treeData.value.filter(n => n.code === '资产类')
  }
  if (activeTab.value === 'liability') {
    return treeData.value.filter(n => n.code === '负债类')
  }
  if (activeTab.value === 'equity') {
    return treeData.value.filter(n => n.code === '权益类')
  }
  if (activeTab.value === 'cost') {
    return treeData.value.filter(n => n.code === '成本类')
  }
  if (activeTab.value === 'profit_loss') {
    return treeData.value.filter(n => n.code === '损益-收入类' || n.code === '损益-费用类')
  }
  return treeData.value
})

const treeOptions = computed(() => convertToTreeOptions(filteredTreeData.value))

// Expand state managers
function getInitialExpandedKeys(nodes: AccountNode[]): string[] {
  const keys: string[] = []
  for (const n of nodes) {
    keys.push(n.code)
    if (n.children) {
      for (const child of n.children) {
        keys.push(child.code)
      }
    }
  }
  return keys
}

function getAllKeys(nodes: AccountNode[]): string[] {
  const keys: string[] = []
  function traverse(list: AccountNode[]) {
    for (const n of list) {
      keys.push(n.code)
      if (n.children && n.children.length > 0) {
        traverse(n.children)
      }
    }
  }
  traverse(nodes)
  return keys
}

function handleExpandedKeysChange(keys: string[]) {
  expandedKeys.value = keys
}

function expandAll() {
  expandedKeys.value = getAllKeys(treeData.value)
}

function collapseAll() {
  expandedKeys.value = []
}

// Watch search query to automatically expand matches
watch(pattern, (newVal) => {
  if (newVal) {
    expandedKeys.value = getAllKeys(treeData.value)
  } else {
    expandedKeys.value = getInitialExpandedKeys(treeData.value)
  }
})

// Custom prefix renderer
function renderPrefix(info: { option: TreeOption }) {
  const opt = info.option as any
  if (opt.level === 0) {
    return h('i', {
      class: 'ri-folder-open-fill',
      style: 'color: var(--solo-primary); font-size: 16px; margin-right: 2px;'
    })
  }
  if (opt.level === 1) {
    return h('span', {
      class: `custom-type-badge type-${getTypeClass(opt.type)}`,
    }, () => opt.type || '')
  }
  // Level 2 Sub-account representation
  return h('i', {
    class: 'ri-corner-down-right-line subaccount-icon',
    style: 'color: var(--solo-text-muted); opacity: 0.6; font-size: 14px; margin-left: 4px; margin-right: 2px;'
  })
}

// Custom label renderer
function renderLabel(info: { option: TreeOption }) {
  const opt = info.option as any
  if (opt.level === 0) {
    return h('span', { class: 'node-label-group' }, opt.accountNode.name)
  }
  return h('span', { class: 'node-label-item' }, [
    h('span', { class: 'node-code-text' }, opt.code),
    h('span', { class: 'node-name-text' }, opt.accountNode.name),
  ])
}

// Custom suffix actions renderer
function renderSuffix(info: { option: TreeOption }) {
  const opt = info.option as any
  const node = findNode(treeData.value, opt.code)
  if (!node) return

  const children: any[] = []
  
  if (opt.is_system && opt.level > 0) {
    children.push(h('span', { class: 'system-badge' }, '内置'))
  }

  const actions: any[] = []
  
  // Can add subaccounts under level 0 (groups) or level 1 (primary accounts)
  if (opt.level < 2) {
    actions.push(h(NButton, {
      text: true,
      size: 'tiny',
      type: 'primary',
      class: 'action-btn add-btn',
      onClick: (e: Event) => {
        e.stopPropagation()
        openAddModal(node)
      },
    }, {
      default: () => h(NSpace, { size: 4, align: 'center' }, () => [
        h('i', { class: 'ri-add-line' }),
        h('span', '子科目')
      ])
    }))
  }

  // Deletable if not system account and level > 0
  if (!opt.is_system && opt.level > 0) {
    actions.push(h(NButton, {
      text: true,
      size: 'tiny',
      type: 'error',
      class: 'action-btn delete-btn',
      onClick: (e: Event) => {
        e.stopPropagation()
        deleteTargetCode.value = opt.code
        deleteTargetName.value = node.name
        showDeleteConfirm.value = true
      },
    }, {
      default: () => h(NSpace, { size: 4, align: 'center' }, () => [
        h('i', { class: 'ri-delete-bin-line' }),
        h('span', '删除')
      ])
    }))
  }

  // 数量核算（含内置存货科目）：仅末级科目可配
  if (opt.is_leaf && opt.level > 0) {
    actions.push(h(NButton, {
      text: true,
      size: 'tiny',
      type: opt.quantity_enabled ? 'success' : 'default',
      class: 'action-btn',
      onClick: (e: Event) => {
        e.stopPropagation()
        openQuantityModal(node)
      },
    }, {
      default: () => h(NSpace, { size: 4, align: 'center' }, () => [
        h('i', { class: 'ri-hashtag' }),
        h('span', opt.quantity_enabled ? `数量${opt.unit ? '·' + opt.unit : ''}` : '数量')
      ])
    }))
  }

  if (actions.length > 0) {
    children.push(h(NSpace, { class: 'node-actions', size: 'medium' }, () => actions))
  }

  return h(NSpace, { size: 'small', align: 'center' }, () => children)
}

function handleCategoryChange(val: string) {
  addForm.value.category = val
  addForm.value.type = getDefaultTypeForCategory(val)
}

function getDefaultTypeForCategory(cat: string): string {
  switch (cat) {
    case '资产类': return '流动资产'
    case '负债类': return '流动负债'
    case '权益类': return '所有者权益'
    case '成本类': return '成本'
    case '损益-收入类': return '收入'
    case '损益-费用类': return '费用'
    default: return ''
  }
}

function openAddModal(parent?: AccountNode) {
  if (parent) {
    isSubaccountCreation.value = true
    parentNodeRef.value = parent
    
    if (parent.level === 0) {
      addForm.value = {
        code: '',
        name: '',
        parent_code: parent.code,
        level: 1,
        category: parent.code,
        type: getDefaultTypeForCategory(parent.code),
      }
    } else {
      addForm.value = {
        code: '',
        name: '',
        parent_code: parent.code,
        level: 2,
        category: parent.category || '',
        type: parent.type || '',
      }
    }
  } else {
    isSubaccountCreation.value = false
    parentNodeRef.value = null
    addForm.value = {
      code: '',
      name: '',
      parent_code: '资产类',
      level: 1,
      category: '资产类',
      type: '流动资产',
    }
  }
  showAddModal.value = true
}

async function loadTree() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/ledger/accounts/tree')
    treeData.value = data
    expandedKeys.value = getInitialExpandedKeys(data)
  } catch {
    message.error('加载科目表失败')
  } finally {
    loading.value = false
  }
}

async function addAccount() {
  const code = addForm.value.code.trim()
  const name = addForm.value.name.trim()

  if (!code || !name) {
    message.warning('请填写科目代码和名称')
    return
  }

  if (!/^\d+$/.test(code)) {
    message.warning('科目代码必须为纯数字')
    return
  }

  if (addForm.value.level === 1) {
    if (code.length !== 4) {
      message.warning('一级科目代码必须为 4 位数字（如 1003）')
      return
    }
    addForm.value.parent_code = addForm.value.category
  }

  if (addForm.value.level === 2 && parentNodeRef.value) {
    const parentCode = parentNodeRef.value.code
    if (code.length !== 6) {
      message.warning(`二级科目代码必须为 6 位数字（如 ${parentCode}01）`)
      return
    }
    if (!code.startsWith(parentCode)) {
      message.warning(`二级科目代码必须以级科目代码 ${parentCode} 开头`)
      return
    }
    addForm.value.parent_code = parentCode
    addForm.value.category = parentNodeRef.value.category || ''
    addForm.value.type = parentNodeRef.value.type || ''
  }

  try {
    const payload = {
      code,
      name,
      parent_code: addForm.value.parent_code,
      level: addForm.value.level,
      category: addForm.value.category,
      type: addForm.value.type,
    }
    await apiClient.post('/api/ledger/accounts', payload)
    message.success('科目添加成功')
    showAddModal.value = false
    await loadTree()
  } catch {
    message.error('添加失败，科目代码可能已存在')
  }
}

// ── 数量金额式核算（P1 WP-D）：叶子科目可开关数量核算并填展示单位 ──
const showQuantityModal = ref(false)
const qtyForm = ref({ code: '', name: '', enabled: false, unit: '' })
const qtySaving = ref(false)

function openQuantityModal(node: AccountNode) {
  qtyForm.value = {
    code: node.code,
    name: node.name,
    enabled: !!node.quantity_enabled,
    unit: String(node.unit || ''),
  }
  showQuantityModal.value = true
}

async function saveQuantitySetting() {
  qtySaving.value = true
  try {
    await apiClient.put(`/api/ledger/accounts/${qtyForm.value.code}`, {
      quantity_enabled: qtyForm.value.enabled,
      unit: qtyForm.value.unit.trim() || null,
    })
    message.success('数量核算设置已保存')
    showQuantityModal.value = false
    await loadTree()
  } catch {
    message.error('保存失败，请重试')
  } finally {
    qtySaving.value = false
  }
}

async function doDelete() {
  try {
    await apiClient.delete(`/api/ledger/accounts/${deleteTargetCode.value}`)
    message.success('科目已删除')
    showDeleteConfirm.value = false
    await loadTree()
  } catch {
    message.error('删除失败，可能该科目含有子节点或为系统科目')
  }
}

onMounted(loadTree)
</script>

<template>
  <div class="accounts-page">
    <div class="page-header">
      <div class="header-left">
        <div class="title-section">
          <h2>科目表</h2>
          <p class="subtitle">管理您的企业级科目体系，支持两级科目架构与自定义扩展</p>
        </div>
      </div>
      <div class="header-right">
        <NButton type="primary" class="add-main-btn" @click="openAddModal()">
          <template #icon><i class="ri-add-line" /></template>
          新增科目
        </NButton>
      </div>
    </div>

    <!-- Statistics -->
    <NGrid :cols="3" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-card">
          <div class="stat-icon-bg bg-primary">
            <i class="ri-node-tree" />
          </div>
          <div class="stat-info">
            <span class="stat-label">科目总数</span>
            <div class="stat-value-row">
              <span class="stat-val">{{ stats.total }}</span>
              <span class="stat-unit">个</span>
            </div>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-card">
          <div class="stat-icon-bg bg-info">
            <i class="ri-shield-star-line" />
          </div>
          <div class="stat-info">
            <span class="stat-label">内置系统科目</span>
            <div class="stat-value-row">
              <span class="stat-val">{{ stats.system }}</span>
              <span class="stat-unit">个</span>
            </div>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-card">
          <div class="stat-icon-bg bg-warning">
            <i class="ri-user-settings-line" />
          </div>
          <div class="stat-info">
            <span class="stat-label">自定义科目</span>
            <div class="stat-value-row">
              <span class="stat-val">{{ stats.custom }}</span>
              <span class="stat-unit">个</span>
            </div>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <NSpin :show="loading">
      <NCard :bordered="false" class="main-card">
        <!-- Controls row -->
        <div class="controls-panel">
          <NInput
            v-model:value="pattern"
            placeholder="搜索科目代码或名称..."
            clearable
            class="search-input"
          >
            <template #prefix>
              <i class="ri-search-line search-icon" />
            </template>
          </NInput>
          <NSpace size="small">
            <NButton secondary size="medium" @click="expandAll">
              <template #icon><i class="ri-expand-up-down-line" /></template>
              全部展开
            </NButton>
            <NButton secondary size="medium" @click="collapseAll">
              <template #icon><i class="ri-contract-up-down-line" /></template>
              全部折叠
            </NButton>
          </NSpace>
        </div>

        <!-- Segmented Tabs -->
        <div class="tabs-container">
          <NTabs v-model:value="activeTab" type="segment" class="category-tabs">
            <NTab name="all">全部</NTab>
            <NTab name="asset">资产</NTab>
            <NTab name="liability">负债</NTab>
            <NTab name="equity">权益</NTab>
            <NTab name="cost">成本</NTab>
            <NTab name="profit_loss">损益</NTab>
          </NTabs>
        </div>

        <!-- Tree View -->
        <div class="tree-wrapper">
          <NTree
            :data="treeOptions"
            :expanded-keys="expandedKeys"
            :pattern="pattern"
            :selectable="false"
            :render-prefix="renderPrefix"
            :render-label="renderLabel"
            :render-suffix="renderSuffix"
            @update:expanded-keys="handleExpandedKeysChange"
            block-line
            class="custom-tree"
          />
        </div>
      </NCard>
    </NSpin>

    <!-- Add Account Modal -->
    <NModal v-model:show="showAddModal" :title="isSubaccountCreation ? '新增子科目' : '新增科目'" preset="card" style="width: 440px; border-radius: var(--solo-radius-lg)">
      <NForm label-placement="left" label-width="84" style="margin-top: 10px;">
        
        <!-- Parent context alert for Level 2 subaccounts -->
        <div v-if="addForm.level === 2" class="parent-info-alert">
          <div class="info-row">
            <span class="info-label">上级科目</span>
            <span class="info-value">{{ parentNodeRef?.code }} {{ parentNodeRef?.name }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">科目类型</span>
            <span class="info-value">{{ parentNodeRef?.type }}</span>
          </div>
        </div>

        <!-- Category options for new Level 1 accounts -->
        <template v-if="addForm.level === 1">
          <NFormItem label="科目类别">
            <NSelect
              v-model:value="addForm.category"
              :options="categoryOptions"
              @update:value="handleCategoryChange"
            />
          </NFormItem>
          <NFormItem label="科目类型">
            <NSelect
              v-model:value="addForm.type"
              :options="typeOptions"
            />
          </NFormItem>
        </template>

        <NFormItem label="科目代码">
          <NSpace vertical :size="4" style="width: 100%">
            <NInput
              v-model:value="addForm.code"
              :placeholder="addForm.level === 1 ? '如 1003' : `以 ${parentNodeRef?.code} 开头，如 ${parentNodeRef?.code}01`"
              clearable
            />
            <div class="input-tip">
              {{ addForm.level === 1 
                  ? '提示：一级科目代码必须为 4 位数字（如 1003）' 
                  : `提示：二级代码应为 6 位数字，且以父科目代码 ${parentNodeRef?.code} 开头（如 ${parentNodeRef?.code}01）` 
              }}
            </div>
          </NSpace>
        </NFormItem>

        <NFormItem label="科目名称">
          <NInput v-model:value="addForm.name" placeholder="如 微信电子账户" clearable />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showAddModal = false">取消</NButton>
          <NButton type="primary" @click="addAccount">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Quantity Accounting Modal (P1 WP-D) -->
    <NModal v-model:show="showQuantityModal" title="数量金额式核算" preset="card" style="width: 420px; border-radius: var(--solo-radius-lg)">
      <p style="margin: 0 0 14px; font-size: 13px; color: var(--solo-text-secondary); line-height: 1.6;">
        为「<strong>{{ qtyForm.code }} {{ qtyForm.name }}</strong>」开启数量核算后，记账时可填数量与单价，
        科目余额表将展示数量收发存。
      </p>
      <NForm label-placement="left" :label-width="90">
        <NFormItem label="数量核算">
          <NSwitch v-model:value="qtyForm.enabled" />
        </NFormItem>
        <NFormItem v-if="qtyForm.enabled" label="计量单位">
          <NInput v-model:value="qtyForm.unit" placeholder="如 件 / 箱 / 吨（选填）" clearable maxlength="16" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showQuantityModal = false">取消</NButton>
          <NButton type="primary" :loading="qtySaving" @click="saveQuantitySetting">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Delete Confirmation Modal -->
    <NModal v-model:show="showDeleteConfirm" title="确认删除" preset="card" style="width: 380px; border-radius: var(--solo-radius-lg)">
      <p style="margin: 0; font-size: 14px; color: var(--solo-text-secondary); line-height: 1.5;">
        确定要删除科目「<strong style="color: var(--solo-text-primary)">{{ deleteTargetCode }} {{ deleteTargetName }}</strong>」吗？此操作不可撤销。
      </p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeleteConfirm = false">取消</NButton>
          <NButton type="error" @click="doDelete">删除</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.accounts-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 16px 0 32px 0;
  width: 100%;
  box-sizing: border-box;
}

.page-header {
  margin-bottom: 24px;
}

.back-btn {
  font-weight: 500;
  color: var(--solo-text-secondary);
  transition: color 0.2s ease;
}

.back-btn:hover {
  color: var(--solo-primary);
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
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.add-main-btn {
  border-radius: var(--solo-radius);
  font-weight: 600;
}

/* Statistics Widgets */
.stats-grid {
  margin-bottom: 24px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  background: var(--solo-bg-card);
  backdrop-filter: blur(8px);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  padding: 18px 20px;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow);
  border-color: var(--solo-primary-light);
}

.stat-icon-bg {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  font-size: 20px;
}

.bg-primary {
  background: var(--solo-primary-glow);
  color: var(--solo-primary);
}

.bg-info {
  background: var(--solo-info-bg);
  color: var(--solo-info);
}

.bg-warning {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}

.stat-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
  font-weight: 500;
}

.stat-value-row {
  display: flex;
  align-items: baseline;
  gap: 2px;
}

.stat-val {
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1;
}

.stat-unit {
  font-size: 11px;
  color: var(--solo-text-muted);
}

/* Main Card */
.main-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-lg);
}

.controls-panel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.search-input {
  max-width: 320px;
}

.search-icon {
  color: var(--solo-text-muted);
  font-size: 16px;
}

.tabs-container {
  margin-bottom: 16px;
}

.category-tabs {
  background: var(--solo-border-light);
  border-radius: var(--solo-radius);
  padding: 3px;
}

.tree-wrapper {
  background: #ffffff;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  padding: 16px;
  min-height: 360px;
}

/* Custom Tree Hover and Line Styling */
.custom-tree :deep(.n-tree-node) {
  padding: 4px 0;
  border-radius: var(--solo-radius-sm);
  transition: all 0.2s ease;
}

.custom-tree :deep(.n-tree-node-content) {
  padding: 6px 12px;
  border-radius: var(--solo-radius-sm);
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.custom-tree :deep(.n-tree-node-content:hover) {
  background-color: var(--solo-border-light) !important;
}

/* Hide actions by default, fade in on hover */
.custom-tree :deep(.node-actions) {
  opacity: 0;
  transition: opacity 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.custom-tree :deep(.n-tree-node-content:hover .node-actions) {
  opacity: 1;
}

/* Custom Badges & Labels */
.custom-type-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
  line-height: 1;
  margin-right: 6px;
}

.type-flow-asset {
  background-color: rgba(16, 185, 129, 0.08);
  color: #0f7354;
}

.type-nonflow-asset {
  background-color: rgba(6, 182, 212, 0.08);
  color: #06b6d4;
}

.type-flow-liability {
  background-color: rgba(245, 158, 11, 0.08);
  color: #a05c10;
}

.type-equity {
  background-color: rgba(59, 130, 246, 0.08);
  color: #1747b0;
}

.type-cost {
  background-color: rgba(139, 92, 246, 0.08);
  color: #8b5cf6;
}

.type-income {
  background-color: rgba(16, 185, 129, 0.08);
  color: #0f7354;
}

.type-expense {
  background-color: rgba(244, 63, 94, 0.08);
  color: #f43f5e;
}

.type-default {
  background-color: var(--solo-border-light);
  color: var(--solo-text-secondary);
}

.node-label-group {
  font-weight: 700;
  font-size: 15px;
  color: var(--solo-text-primary);
}

.node-label-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.node-code-text {
  font-family: Menlo, Monaco, Consolas, "Courier New", monospace;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  font-weight: 500;
}

.node-name-text {
  font-size: 13.5px;
  color: var(--solo-text-primary);
  font-weight: 600;
}

.system-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 5px;
  font-size: 10px;
  font-weight: 500;
  color: var(--solo-text-muted);
  background: var(--solo-border-light);
  border: 1px solid var(--solo-border);
  border-radius: 4px;
}

.subaccount-icon {
  margin-right: 4px;
}

/* Actions inside nodes */
.action-btn {
  font-weight: 600;
  font-size: 12px;
  transition: all 0.2s ease;
}

.add-btn {
  color: var(--solo-primary);
}

.add-btn:hover {
  color: var(--solo-primary-light);
}

.delete-btn {
  color: var(--solo-expense);
}

.delete-btn:hover {
  color: #ff6b8b;
}

/* Modal and Alert styling */
.parent-info-alert {
  background: rgba(99, 102, 241, 0.04);
  border: 1px dashed rgba(99, 102, 241, 0.2);
  border-radius: var(--solo-radius);
  padding: 12px 14px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}

.info-label {
  color: var(--solo-text-secondary);
}

.info-value {
  font-weight: 600;
  color: var(--solo-text-primary);
}

.input-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.4;
  margin-top: 2px;
}
</style>