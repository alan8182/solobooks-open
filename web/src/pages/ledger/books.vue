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
 *
 * 为什么用 ?book= 而不是 /ledger/books/:kind 子路由：
 * 子路由会让 route.name 变成每本账一个，而 <KeepAlive :include> 是按路由 name 缓存的，
 * 结果五本账各占一个缓存壳、页内标签也建不出来。一个 path 一个 name 才能让「会计账簿」
 * 始终是一个标签，切账册只是换内容。
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
        label: '核算项目余额',
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
  // 切账册必须把当前期间带过去，否则用户挑好的月份会被重置成默认值
  router.push({ path: '/ledger/books', query: { ...route.query, book: book.key } })
}

function openWholeBook() {
  router.push({ path: '/ledger/book', query: route.query })
}
</script>

<template>
  <div class="books-page">
    <header class="books-bar">
      <div class="books-bar-copy">
        <h1 class="books-bar-title">会计账簿</h1>
        <span class="books-bar-sub">五本账共用同一套凭证，切换账册会保留当前期间</span>
      </div>
      <NButton secondary @click="openWholeBook">
        <template #icon><i class="ri-book-2-line" /></template>
        整册打印 / 签发
      </NButton>
    </header>

    <div class="books-body">
      <nav class="books-nav" aria-label="切换账册">
        <div v-for="group in BOOK_GROUPS" :key="group.title" class="books-group">
          <div class="books-group-title">{{ group.title }}</div>
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            class="books-item"
            :class="{ active: item.key === currentBook.key }"
            :aria-current="item.key === currentBook.key ? 'page' : undefined"
            @click="openBook(item)"
          >
            <i :class="item.icon" aria-hidden="true" />
            <span class="books-item-text">
              <span class="books-item-label">{{ item.label }}</span>
              <span class="books-item-hint">{{ item.hint }}</span>
            </span>
          </button>
        </div>
      </nav>

      <section class="books-main">
        <component :is="currentBook.view" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.books-page {
  width: 100%;
  max-width: 1240px;
  margin: 0 auto;
}

.books-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--solo-border-light);
}

.books-bar-copy {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

/* 刻意压在 15px：下面每本账自己的 h2 是 22px，两个大标题会打架 */
.books-bar-title {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.books-bar-sub {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.books-body {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.books-nav {
  position: sticky;
  top: 12px;
  display: flex;
  width: 196px;
  flex-direction: column;
  flex-shrink: 0;
  gap: 18px;
}

.books-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.books-group-title {
  padding: 0 10px 6px;
  color: var(--solo-text-muted);
  font-size: 11px;
  letter-spacing: 0.04em;
}

.books-item {
  display: flex;
  width: 100%;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--solo-radius-md);
  background: transparent;
  color: var(--solo-text-secondary);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out;
}

.books-item:hover {
  background: var(--solo-bg-page);
  color: var(--solo-text-primary);
}

.books-item:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.books-item.active {
  border-color: var(--solo-primary-soft);
  background: var(--solo-primary-soft);
  color: var(--solo-primary);
}

.books-item i {
  margin-top: 2px;
  flex-shrink: 0;
  font-size: 16px;
}

.books-item-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1px;
}

.books-item-label {
  font-size: 13px;
  font-weight: 500;
}

.books-item-hint {
  color: var(--solo-text-muted);
  font-size: 11px;
  line-height: 1.4;
}

.books-item.active .books-item-hint {
  color: var(--solo-primary);
  opacity: 0.78;
}

.books-main {
  min-width: 0;
  flex: 1;
}

/* 壳里左边已经有账册导航，子页那句「← 返回报表中心」留着只会把用户弹出工作台。
   general-ledger 的「返回科目余额表」在 filter-bar 里，同 class 但不同容器，不受影响。
   必须 !important：layout.css 给 .report-back-btn 打了 display: inline-flex !important。 */
.books-main :deep(.report-header .report-back-btn) {
  display: none !important;
}

@media (max-width: 1024px) {
  .books-body {
    flex-direction: column;
    gap: 12px;
  }

  .books-nav {
    position: static;
    width: 100%;
    flex-direction: row;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 2px;
  }

  .books-group {
    flex-direction: row;
    align-items: center;
    gap: 6px;
  }

  .books-group-title {
    display: none;
  }

  .books-item {
    width: auto;
    flex-shrink: 0;
    align-items: center;
  }

  .books-item i {
    margin-top: 0;
  }

  .books-item-hint {
    display: none;
  }
}
</style>
