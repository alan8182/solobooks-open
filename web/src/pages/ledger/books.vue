<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'

interface BookEntry {
  /** URL 上的 ?book= 值，同时是导航项的身份 */
  key: string
  label: string
  hint: string
  icon: string
  view: ReturnType<typeof defineAsyncComponent>
}

/**
 * 账册清单是这一层壳的唯一真值：导航、高亮、内容渲染共用同一份定义。
 */
const BOOK_GROUPS: { title: string; items: BookEntry[] }[] = [
  {
    title: '主账',
    items: [
      {
        key: 'general-ledger',
        label: '总分类账',
        hint: '按科目 × 月份汇总',
        icon: 'ri-book-3-line',
        view: defineAsyncComponent(() => import('@/pages/report/general-ledger.vue')),
      },
      {
        key: 'sub-ledger',
        label: '明细账',
        hint: '逐笔分录与科目核对',
        icon: 'ri-book-read-line',
        view: defineAsyncComponent(() => import('@/pages/report/sub-ledger.vue')),
      },
      {
        key: 'chronological',
        label: '序时账',
        hint: '凭证按编号顺序平铺',
        icon: 'ri-order-play-line',
        view: defineAsyncComponent(() => import('@/pages/report/chronological.vue')),
      },
    ],
  },
  {
    title: '辅助账',
    items: [
      {
        key: 'multi-column',
        label: '多栏账',
        hint: '明细子科目展开分析',
        icon: 'ri-layout-column-line',
        view: defineAsyncComponent(() => import('@/pages/report/multi-column.vue')),
      },
      {
        key: 'partner-balances',
        label: '往来项目余额',
        hint: '按往来单位汇总余额',
        icon: 'ri-user-search-line',
        view: defineAsyncComponent(() => import('@/pages/report/partner-balances.vue')),
      },
    ],
  },
]

const DEFAULT_BOOK = 'general-ledger'

const route = useRoute()
const router = useRouter()

const allBooks = computed(() => BOOK_GROUPS.flatMap((group) => group.items))
const currentKey = computed(() => String(route.query.book || DEFAULT_BOOK))
const currentBook = computed(
  () => allBooks.value.find((book) => book.key === currentKey.value) ?? allBooks.value[0],
)

function openBook(book: BookEntry) {
  if (book.key === currentBook.value.key) return
  router.push({ path: '/ledger/books', query: { ...route.query, book: book.key } })
}

function openWholeBook() {
  router.push({ path: '/ledger/book', query: route.query })
}
</script>

<template>
  <div class="books-page">
    <!-- 第一行：页面标题栏 + 右侧整册打印/签发动作 -->
    <header class="books-header">
      <div class="books-title-group">
        <h1 class="books-title">会计账簿</h1>
        <p class="books-subtitle">五本账共用凭证底数，切换账册保留当前期间</p>
      </div>

      <div class="books-actions">
        <NButton secondary @click="openWholeBook">
          <template #icon><i class="ri-book-2-line" /></template>
          整册打印 / 签发
        </NButton>
      </div>
    </header>

    <!-- 第二行：账册切换分段标签栏，独立一行平铺 -->
    <nav class="books-nav-bar" aria-label="切换账册">
      <div class="books-tabs" role="tablist">
        <template v-for="(group, gIdx) in BOOK_GROUPS" :key="group.title">
          <span v-if="gIdx > 0" class="books-tabs-divider" aria-hidden="true" />
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            role="tab"
            class="books-tab-btn"
            :class="{ active: item.key === currentBook.key }"
            :aria-selected="item.key === currentBook.key"
            @click="openBook(item)"
          >
            <i :class="item.icon" aria-hidden="true" />
            <span>{{ item.label }}</span>
          </button>
        </template>
      </div>
    </nav>

    <!-- 第三行：所选账册主内容 -->
    <main class="books-main">
      <component :is="currentBook.view" />
    </main>
  </div>
</template>

<style scoped>
.books-page {
  width: 100%;
  max-width: 1360px;
  margin: 0 auto;
}

/* 第一行：标题 + 右侧操作按钮 */
.books-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.books-title-group {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.books-title {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.books-subtitle {
  margin: 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}

.books-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* 第二行：账册分段切换栏，整行居左排列 */
.books-nav-bar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--solo-border-light);
}

.books-tabs {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  background: var(--solo-bg-page, #f4f6fa);
  border: 1px solid var(--solo-border-light, #e5e9ef);
  border-radius: var(--solo-radius-md, 8px);
  gap: 2px;
}

.books-tabs-divider {
  width: 1px;
  height: 14px;
  background: var(--solo-border-light, #dcdfe6);
  margin: 0 6px;
}

.books-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  background: transparent;
  color: var(--solo-text-secondary, #606266);
  font-size: 13px;
  font-weight: 500;
  border-radius: calc(var(--solo-radius-md, 8px) - 2px);
  cursor: pointer;
  transition: all 160ms ease;
  white-space: nowrap;
}

.books-tab-btn:hover {
  color: var(--solo-text-primary, #303133);
  background: rgba(0, 0, 0, 0.04);
}

.books-tab-btn.active {
  background: #ffffff;
  color: var(--solo-primary, #1747b0);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.books-tab-btn i {
  font-size: 15px;
}

.books-main {
  width: 100%;
  min-width: 0;
}

/* 消除子页面内嵌入时产生的多余顶部包裹、重复标题和误弹出的“返回报表中心”按钮 */
.books-main :deep(.report-header) {
  display: none !important;
}

.books-main :deep(.report-container) {
  padding: 0 !important;
  max-width: 100% !important;
}

.books-main :deep(.filter-bar) {
  display: flex !important;
  align-items: center !important;
  width: 100% !important;
  box-sizing: border-box !important;
}
</style>
