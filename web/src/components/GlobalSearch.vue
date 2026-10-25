<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { NModal, NInput, NSpin } from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney } from '@/utils/format'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { NAV_ITEMS } from '@/utils/constants'
import { itemVisible } from '@/utils/workspace'
import SoloStatusBadge from '@/components/ui/SoloStatusBadge.vue'

const emit = defineEmits<{ close: [] }>()

const router = useRouter()
const viewStore = useWorkspaceViewStore()
const query = ref('')
const loading = ref(false)
const results = ref<{ entries: any[]; accounts: any[] }>({ entries: [], accounts: [] })
const selectedIndex = ref(0)

interface CommandAction {
  id: string
  type: 'action'
  title: string
  description: string
  icon: string
  keywords: string[]
  handler: () => void
}

interface CommandPage {
  id: string
  type: 'page'
  title: string
  path: string
  icon: string
  group: string
  keywords: string[]
}

interface CommandData {
  id: string
  type: 'entry' | 'account'
  title: string
  subtitle?: string
  amount?: number
  direction?: string
  raw: any
}

type CommandItem = CommandAction | CommandPage | CommandData

// 1. 全局快捷动作定义
const DEFAULT_ACTIONS: CommandAction[] = [
  {
    id: 'act-copilot',
    type: 'action',
    title: '记一笔 / 问 AI 会计',
    description: '通过一句话自然语言或图片记账',
    icon: 'ri-sparkling-fill',
    keywords: ['记一笔', '记账', 'ai', 'copilot', '对话', '问会计', 'jiyibi', 'jizhang'],
    handler: () => router.push('/copilot'),
  },
  {
    id: 'act-invoice',
    type: 'action',
    title: '上传发票',
    description: 'Excel 批量导入或发票文件上传',
    icon: 'ri-receipt-line',
    keywords: ['发票', '导入发票', '上传发票', 'fp', 'daoru', 'fapiao'],
    handler: () => router.push('/record/invoice-import'),
  },
  {
    id: 'act-bank',
    type: 'action',
    title: '上传银行流水',
    description: '银行对账单、微信/支付宝账单导入与对账',
    icon: 'ri-bank-line',
    keywords: ['流水', '银行', '对账', 'ls', 'yinhang', 'duizhang', 'liushui'],
    handler: () => router.push('/bank'),
  },
  {
    id: 'act-closing',
    type: 'action',
    title: '月末结账向导',
    description: '损益结转、折旧计提与关账检查',
    icon: 'ri-calendar-check-line',
    keywords: ['结账', '月末', '关账', 'jz', 'jiezhang', 'yuemo'],
    handler: () => router.push('/closing-wizard'),
  },
  {
    id: 'act-tax',
    type: 'action',
    title: '税务申报与测算',
    description: '增值税、企业所得税申报与倒计时',
    icon: 'ri-file-shield-2-line',
    keywords: ['税务', '申报', '报税', '税', 'sw', 'shuishou', 'baoshui'],
    handler: () => router.push('/tax-filing'),
  },
  {
    id: 'act-company',
    type: 'action',
    title: '管理主体与公司',
    description: '切换账套主体或编辑公司档案',
    icon: 'ri-building-line',
    keywords: ['公司', '主体', '切换', 'zhuti', 'gongsi'],
    handler: () => router.push('/mine/companies'),
  },
]

// 2. 页面直达列表（根据导航与视图过滤）
const allPages = computed<CommandPage[]>(() => {
  const pages: CommandPage[] = []

  function addPage(title: string, path: string, icon: string, group: string, keywords: string[] = []) {
    pages.push({
      id: `page-${path}`,
      type: 'page',
      title,
      path,
      icon,
      group,
      keywords,
    })
  }

  for (const item of NAV_ITEMS) {
    if (!itemVisible(item.workspace, viewStore.mode)) continue
    const label = viewStore.isBoss && item.bossLabel ? item.bossLabel : item.label

    if (item.path) {
      addPage(label, item.path, item.icon, item.group, [label, item.label, item.path])
    }
    if (item.children) {
      for (const child of item.children) {
        if (!itemVisible(child.workspace, viewStore.mode)) continue
        addPage(`${label} · ${child.label}`, child.path, child.icon, item.group, [child.label, child.path, label])
      }
    }
  }
  return pages
})

// 3. 筛选动作与页面
const matchedActions = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return DEFAULT_ACTIONS
  return DEFAULT_ACTIONS.filter(act => (
    act.title.toLowerCase().includes(q) ||
    act.description.toLowerCase().includes(q) ||
    act.keywords.some(k => k.toLowerCase().includes(q))
  ))
})

const matchedPages = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) {
    // 空查询时返回高频页面
    const highFrequencyPaths = ['/', '/ledger', '/invoices', '/reports', '/mine']
    return allPages.value.filter(p => highFrequencyPaths.includes(p.path))
  }
  return allPages.value.filter(p => (
    p.title.toLowerCase().includes(q) ||
    p.keywords.some(k => k.toLowerCase().includes(q))
  ))
})

// 4. API 搜索数据 (账目与科目)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

watch(query, (val) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  selectedIndex.value = 0
  const trimmed = val.trim()
  if (!trimmed) {
    results.value = { entries: [], accounts: [] }
    return
  }
  debounceTimer = setTimeout(() => doSearch(trimmed), 250)
})

function doSearch(q: string) {
  loading.value = true
  apiClient.get('/api/search', { params: { q } })
    .then((data: any) => {
      results.value = data
    })
    .catch(() => {
      results.value = { entries: [], accounts: [] }
    })
    .finally(() => { loading.value = false })
}

const dataResults = computed<CommandData[]>(() => {
  const list: CommandData[] = []
  if (!viewStore.isBoss && results.value.accounts?.length > 0) {
    for (const a of results.value.accounts) {
      list.push({
        id: `acc-${a.id || a.code}`,
        type: 'account',
        title: `${a.code} ${a.name}`,
        subtitle: a.category || '会计科目',
        raw: a,
      })
    }
  }
  if (results.value.entries?.length > 0) {
    for (const e of results.value.entries) {
      list.push({
        id: `entry-${e.id}`,
        type: 'entry',
        title: e.title || '记账条目',
        subtitle: e.occurDate ? String(e.occurDate).slice(0, 10) : '',
        amount: e.amount,
        direction: e.direction,
        raw: e,
      })
    }
  }
  return list
})

// 扁平列表供全键盘循环切换
const flatList = computed<CommandItem[]>(() => {
  return [
    ...matchedActions.value,
    ...matchedPages.value,
    ...dataResults.value,
  ]
})

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (flatList.value.length > 0) {
      selectedIndex.value = (selectedIndex.value + 1) % flatList.value.length
      scrollToSelected()
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (flatList.value.length > 0) {
      selectedIndex.value = (selectedIndex.value - 1 + flatList.value.length) % flatList.value.length
      scrollToSelected()
    }
  } else if (e.key === 'Enter') {
    e.preventDefault()
    if (flatList.value.length > 0 && selectedIndex.value >= 0 && selectedIndex.value < flatList.value.length) {
      executeItem(flatList.value[selectedIndex.value])
    }
  } else if (e.key === 'Escape') {
    emit('close')
  }
}

function scrollToSelected() {
  nextTick(() => {
    const el = document.querySelector('.command-row.is-selected')
    if (el) {
      el.scrollIntoView({ block: 'nearest' })
    }
  })
}

function executeItem(item: CommandItem) {
  emit('close')
  if (item.type === 'action') {
    item.handler()
  } else if (item.type === 'page') {
    router.push(item.path)
  } else if (item.type === 'account') {
    router.push('/mine/accounts')
  } else if (item.type === 'entry') {
    router.push(`/ledger/${item.raw.id}`)
  }
}

onMounted(() => {
  nextTick(() => {
    const input = document.querySelector('.palette-input input')
    if (input) (input as HTMLInputElement).focus()
  })
})
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    class="command-palette-modal"
    :bordered="false"
    :mask-closable="true"
    :close-on-esc="false"
    @update:show="(v: boolean) => { if (!v) emit('close') }"
  >
    <div class="command-palette" @keydown="handleKeydown">
      <div class="palette-header">
        <i class="ri-search-line search-icon" aria-hidden="true" />
        <NInput
          v-model:value="query"
          placeholder="输入动作、页面或关键词… (↑↓ 移动, ↵ 回车, ESC 关闭)"
          size="large"
          clearable
          class="palette-input"
        />
      </div>

      <div class="palette-body">
        <NSpin :show="loading" size="small">
          <div v-if="flatList.length > 0" class="palette-content">
            <!-- 快捷动作组 -->
            <div v-if="matchedActions.length > 0" class="command-group">
              <span class="group-title">快捷动作</span>
              <div
                v-for="act in matchedActions"
                :key="act.id"
                class="command-row"
                :class="{ 'is-selected': flatList.indexOf(act) === selectedIndex }"
                @click="executeItem(act)"
                @mouseenter="selectedIndex = flatList.indexOf(act)"
              >
                <div class="row-icon action-icon">
                  <i :class="act.icon" />
                </div>
                <div class="row-info">
                  <span class="row-title">{{ act.title }}</span>
                  <span class="row-desc">{{ act.description }}</span>
                </div>
                <SoloStatusBadge tone="brand" :dot="false" size="small">动作</SoloStatusBadge>
              </div>
            </div>

            <!-- 常用页面组 -->
            <div v-if="matchedPages.length > 0" class="command-group">
              <span class="group-title">{{ query ? '直达页面' : '常用页面' }}</span>
              <div
                v-for="pg in matchedPages"
                :key="pg.id"
                class="command-row"
                :class="{ 'is-selected': flatList.indexOf(pg) === selectedIndex }"
                @click="executeItem(pg)"
                @mouseenter="selectedIndex = flatList.indexOf(pg)"
              >
                <div class="row-icon page-icon">
                  <i :class="pg.icon" />
                </div>
                <div class="row-info">
                  <span class="row-title">{{ pg.title }}</span>
                  <span class="row-path">{{ pg.path }}</span>
                </div>
                <SoloStatusBadge tone="info" :dot="false" size="small">页面</SoloStatusBadge>
              </div>
            </div>

            <!-- 账目与科目数据组 -->
            <div v-if="dataResults.length > 0" class="command-group">
              <span class="group-title">账目与科目</span>
              <div
                v-for="dataItem in dataResults"
                :key="dataItem.id"
                class="command-row"
                :class="{ 'is-selected': flatList.indexOf(dataItem) === selectedIndex }"
                @click="executeItem(dataItem)"
                @mouseenter="selectedIndex = flatList.indexOf(dataItem)"
              >
                <div class="row-icon data-icon">
                  <i :class="dataItem.type === 'account' ? 'ri-book-2-line' : 'ri-file-text-line'" />
                </div>
                <div class="row-info">
                  <span class="row-title">{{ dataItem.title }}</span>
                  <span v-if="dataItem.subtitle" class="row-desc">{{ dataItem.subtitle }}</span>
                </div>
                <span v-if="dataItem.amount !== undefined" class="row-amount">
                  {{ formatMoney(dataItem.amount) }}
                </span>
                <SoloStatusBadge
                  v-if="dataItem.type === 'entry'"
                  :tone="dataItem.direction === 'income' ? 'success' : 'danger'"
                  size="small"
                >
                  {{ dataItem.direction === 'income' ? '收入' : '支出' }}
                </SoloStatusBadge>
                <SoloStatusBadge
                  v-else
                  tone="neutral"
                  size="small"
                >
                  科目
                </SoloStatusBadge>
              </div>
            </div>
          </div>

          <div v-else-if="query && !loading" class="palette-empty">
            <i class="ri-inbox-line empty-icon" />
            <p>未找到匹配的动作、页面或账目</p>
          </div>
        </NSpin>
      </div>

      <div class="palette-footer">
        <div class="footer-tips">
          <span class="tip-kbd"><kbd>↑</kbd><kbd>↓</kbd> 切换</span>
          <span class="tip-kbd"><kbd>↵</kbd> 执行</span>
          <span class="tip-kbd"><kbd>esc</kbd> 退出</span>
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
:deep(.command-palette-modal) {
  width: 580px;
  max-width: 92vw;
  padding: 0;
  border-radius: var(--solo-radius-lg, 12px);
  overflow: hidden;
  box-shadow: 0 20px 48px rgba(24, 34, 48, 0.16);
  border: 1px solid var(--solo-border, #dde3ea);
}

.command-palette {
  display: flex;
  flex-direction: column;
  max-height: 520px;
}

.palette-header {
  position: relative;
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
}

.search-icon {
  font-size: 18px;
  color: var(--solo-text-muted, #636e7c);
  margin-right: 10px;
}

.palette-input {
  flex: 1;
}

:deep(.palette-input .n-input) {
  background-color: transparent !important;
  border: none !important;
  box-shadow: none !important;
}

:deep(.palette-input input) {
  font-size: 15px;
  color: var(--solo-text-primary, #182230);
}

.palette-body {
  flex: 1;
  overflow-y: auto;
  max-height: 380px;
  padding: 8px 12px;
}

.command-group {
  margin-bottom: 12px;
}

.group-title {
  display: block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: var(--solo-text-muted, #636e7c);
  padding: 6px 8px;
  text-transform: uppercase;
}

.command-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border-radius: var(--solo-radius-control, 8px);
  cursor: pointer;
  transition: all 0.12s ease-out;
}

.command-row:hover,
.command-row.is-selected {
  background-color: var(--solo-bg-blue-soft, #edf3ff);
}

.row-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  font-size: 16px;
  flex-shrink: 0;
}

.action-icon {
  background-color: #e6edff;
  color: var(--solo-primary, #1747b0);
}

.page-icon {
  background-color: var(--solo-bg-soft, #f1f4f8);
  color: var(--solo-text-secondary, #586474);
}

.data-icon {
  background-color: #eaf7f1;
  color: var(--solo-income, #0f7354);
}

.row-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.row-title {
  font-size: 13px;
  font-weight: 550;
  color: var(--solo-text-primary, #182230);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-desc,
.row-path {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-amount {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
  margin-right: 6px;
}

.palette-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 36px 16px;
  color: var(--solo-text-muted, #636e7c);
  font-size: 13px;
}

.empty-icon {
  font-size: 28px;
  margin-bottom: 8px;
}

.palette-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-top: 1px solid var(--solo-border-light, #edf0f4);
  background-color: var(--solo-bg-soft, #f1f4f8);
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}

.footer-tips {
  display: flex;
  gap: 12px;
}

.tip-kbd kbd {
  display: inline-block;
  padding: 1px 4px;
  font-family: inherit;
  font-size: 10px;
  line-height: 1.2;
  background-color: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 4px;
  box-shadow: 0 1px 0 rgba(0, 0, 0, 0.08);
  margin-right: 3px;
}
</style>
