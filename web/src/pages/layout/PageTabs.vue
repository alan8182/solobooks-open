<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useTabsStore, type PageTab } from '@/stores/tabs'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { MAX_TABS, tabTitle } from '@/utils/tabPages'

/**
 * 页内多标签栏。
 *
 * 它不负责「去哪」——那是侧边栏的事。这里只装「已经开着什么」：开过的页面
 * 留一条标签，切回来草稿和滚动都还在。所以视觉上必须比侧边栏轻：12px 灰字、
 * 32px 矮条、只有激活的那条变白底 + 顶部 2px 品牌蓝。
 */

const route = useRoute()
const router = useRouter()
const tabsStore = useTabsStore()
const viewStore = useWorkspaceViewStore()
const authStore = useAuthStore()

const moreOpen = ref(false)
const menu = ref<{ path: string; x: number; y: number } | null>(null)
const confirmTarget = ref<{ path: string; title: string; canSave: boolean } | null>(null)
const savingDraft = ref(false)
const toast = ref('')
let toastTimer: ReturnType<typeof setTimeout> | undefined

const tabs = computed(() =>
  tabsStore.tabs.map((tab) => ({ ...tab, title: tabTitle(tab.path, viewStore.isBoss) })),
)
const activePath = computed(() => tabsStore.activePath)
const atLimit = computed(() => tabs.value.length >= MAX_TABS)

/** 每个标签记住自己的滚动位置，切回来接着看 */
const scrollMemory = new Map<string, number>()

function contentEl(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-page-scroll]')
}

function notify(text: string) {
  toast.value = text
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 2800)
}

function titleOf(path: string): string {
  return tabTitle(path, viewStore.isBoss)
}

/* ── 路由 → 标签同步 ───────────────────────────────────────────── */

watch(
  () => route.fullPath,
  () => {
    const outcome = tabsStore.open(route)
    if (outcome.status === 'replaced') {
      notify(`已达 ${MAX_TABS} 个标签上限，已关掉最久未用的「${titleOf(outcome.evictedPath)}」`)
    }
  },
  { immediate: true },
)

/* ── 切换账套 ──────────────────────────────────────────────────── */

// 换公司必须清空：标签里缓存的是上一套账的页面实例，留着就是串账
watch(
  () => authStore.currentCompanyId,
  () => {
    tabsStore.reset()
    tabsStore.open(route)
  },
)

/* ── 切换 ──────────────────────────────────────────────────────── */

async function switchTo(tab: PageTab) {
  closePopovers()
  const el = contentEl()
  if (el && activePath.value) scrollMemory.set(activePath.value, el.scrollTop)
  const target = tab.fullPath || tab.path
  if (route.fullPath !== target) await router.push(target)
  await nextTick()
  const top = scrollMemory.get(tab.path) ?? 0
  requestAnimationFrame(() => {
    const box = contentEl()
    if (box) box.scrollTop = top
  })
}

/* ── 关闭 ──────────────────────────────────────────────────────── */

function requestClose(path: string) {
  if (!tabsStore.isClosable(path)) return
  closePopovers()
  const guard = tabsStore.dirtyGuard(path)
  if (guard) {
    confirmTarget.value = { path, title: titleOf(path), canSave: Boolean(guard.save) }
    return
  }
  performClose(path)
}

function performClose(path: string) {
  scrollMemory.delete(path)
  const next = tabsStore.close(path)
  if (next && route.fullPath !== next) void router.push(next)
}

async function resolveConfirm(mode: 'save' | 'discard') {
  const target = confirmTarget.value
  if (!target) return
  if (mode === 'save') {
    const guard = tabsStore.dirtyGuard(target.path)
    savingDraft.value = true
    try {
      await guard?.save?.()
    } catch {
      // 存草稿失败就留在原地，不替用户决定「反正先关掉」
      savingDraft.value = false
      return
    }
    savingDraft.value = false
  }
  confirmTarget.value = null
  performClose(target.path)
}

/* ── 右键菜单 ──────────────────────────────────────────────────── */

function openMenu(event: MouseEvent, path: string) {
  const host = event.currentTarget as HTMLElement
  const rect = host.getBoundingClientRect()
  moreOpen.value = false
  menu.value = { path, x: Math.round(rect.left), y: Math.round(rect.bottom + 2) }
}

const menuTab = computed(() => tabs.value.find((tab) => tab.path === menu.value?.path) ?? null)
const rightCount = computed(() => {
  const index = tabsStore.tabs.findIndex((tab) => tab.path === menu.value?.path)
  if (index < 0) return 0
  return tabsStore.tabs.slice(index + 1).filter((tab) => !tab.pinned).length
})
const otherCount = computed(
  () => tabsStore.tabs.filter((tab) => tab.path !== menu.value?.path && !tab.pinned).length,
)

function menuReload() {
  const path = menu.value?.path
  closePopovers()
  if (!path) return
  tabsStore.reload(path)
}

async function menuCopyLink() {
  const target = menuTab.value
  closePopovers()
  if (!target) return
  const url = `${window.location.origin}${target.fullPath || target.path}`
  try {
    await navigator.clipboard.writeText(url)
    notify('页面链接已复制')
  } catch {
    notify('复制失败，请手动复制地址栏')
  }
}

function menuClose() {
  const path = menu.value?.path
  closePopovers()
  if (path) requestClose(path)
}

function menuCloseOthers() {
  const path = menu.value?.path
  closePopovers()
  if (!path) return
  const next = tabsStore.closeOthers(path)
  if (next) void router.push(next)
}

function menuCloseRight() {
  const path = menu.value?.path
  closePopovers()
  if (!path) return
  const next = tabsStore.closeRight(path)
  if (next) void router.push(next)
}

/* ── 弹层开关 ──────────────────────────────────────────────────── */

function closePopovers() {
  moreOpen.value = false
  menu.value = null
}

function toggleMore() {
  menu.value = null
  moreOpen.value = !moreOpen.value
}

function onDocumentClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('.page-tabs')) return
  closePopovers()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  closePopovers()
  if (confirmTarget.value) confirmTarget.value = null
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', closePopovers)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', closePopovers)
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<template>
  <div class="page-tabs no-print" role="tablist" aria-label="已打开的页面">
    <div class="tabs-strip">
      <div
        v-for="tab in tabs"
        :key="tab.path"
        class="tab"
        :class="{ active: tab.path === activePath, pinned: tab.pinned }"
        role="tab"
        :aria-selected="tab.path === activePath"
        :tabindex="tab.path === activePath ? 0 : -1"
        :title="tab.title"
        @click="switchTo(tab)"
        @keydown.enter.prevent="switchTo(tab)"
        @contextmenu.prevent="openMenu($event, tab.path)"
      >
        <span v-if="tab.pinned" class="tab-pin" aria-hidden="true">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none">
            <path d="M4.4 10.6L13.4 3.2l1.6 6.4 4.6 4.6-6.6 1.4-4 6-4.6-10.6z" fill="currentColor" />
          </svg>
        </span>
        <span class="tab-title">{{ tab.title }}</span>
        <button
          v-if="!tab.pinned"
          class="tab-close"
          type="button"
          :aria-label="`关闭 ${tab.title}`"
          @click.stop="requestClose(tab.path)"
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M7 7l10 10M17 7L7 17" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

    <div class="tabs-tools">
      <span class="tab-count" :class="{ warn: atLimit }">{{ tabs.length }} / {{ MAX_TABS }}</span>
      <button
        class="tool-btn"
        :class="{ open: moreOpen }"
        type="button"
        aria-label="全部标签"
        @click.stop="toggleMore"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M7 10l5 5 5-5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>

    <div v-if="moreOpen" class="popover more-panel" role="menu">
      <button
        v-for="tab in tabs"
        :key="tab.path"
        class="more-item"
        :class="{ current: tab.path === activePath }"
        type="button"
        @click="switchTo(tab)"
      >
        <span class="more-name">{{ tab.title }}</span>
        <span v-if="tab.pinned" class="more-pin">固定</span>
        <span
          v-else
          class="more-close"
          role="button"
          :aria-label="`关闭 ${tab.title}`"
          @click.stop="requestClose(tab.path)"
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M7 7l10 10M17 7L7 17" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
          </svg>
        </span>
      </button>
      <div class="panel-foot">最多同时打开 {{ MAX_TABS }} 个页面，首页固定不可关闭</div>
    </div>

    <div
      v-if="menu && menuTab"
      class="popover ctx-menu"
      :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
      role="menu"
    >
      <button class="ctx-item" type="button" @click="menuReload">刷新当前页面</button>
      <button class="ctx-item" type="button" @click="menuCopyLink">复制页面链接</button>
      <div class="ctx-sep" />
      <button class="ctx-item" type="button" :disabled="menuTab.pinned" @click="menuClose">关闭</button>
      <button class="ctx-item" type="button" :disabled="otherCount === 0" @click="menuCloseOthers">
        关闭其他标签
      </button>
      <button class="ctx-item" type="button" :disabled="rightCount === 0" @click="menuCloseRight">
        关闭右侧标签<span class="ctx-hint">{{ rightCount }} 个</span>
      </button>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="confirmTarget" class="confirm-mask" @click.self="confirmTarget = null">
      <div class="confirm-card" role="dialog" aria-modal="true" aria-labelledby="tab-close-title">
        <div class="confirm-head">
          <svg class="confirm-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="9" fill="#FFF5E8" />
            <path d="M12 7.4v6" stroke="#A05C10" stroke-width="2" stroke-linecap="round" />
            <circle cx="12" cy="16.6" r="1.15" fill="#A05C10" />
          </svg>
          <h2 id="tab-close-title" class="confirm-title">「{{ confirmTarget.title }}」还没保存</h2>
        </div>
        <p class="confirm-body">
          这个标签里有未保存的修改，关闭后会丢失。可以先存为草稿，之后回来继续编辑。
        </p>
        <div class="confirm-actions">
          <button class="btn ghost" type="button" @click="confirmTarget = null">取消</button>
          <button class="btn danger" type="button" :disabled="savingDraft" @click="resolveConfirm('discard')">
            直接关闭
          </button>
          <button
            v-if="confirmTarget.canSave"
            class="btn primary"
            type="button"
            :disabled="savingDraft"
            @click="resolveConfirm('save')"
          >
            {{ savingDraft ? '保存中…' : '存草稿并关闭' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="toast" class="tabs-toast" role="status">{{ toast }}</div>
  </Teleport>
</template>

<style scoped>
.page-tabs {
  position: relative;
  z-index: 6;
  flex: 0 0 var(--page-tabs-height);
  height: var(--page-tabs-height);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 0 14px;
  background: var(--solo-bg-page);
  border-bottom: 1px solid var(--solo-border-sidebar);
}

.tabs-strip {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}
.tabs-strip::-webkit-scrollbar {
  display: none;
}

.tab {
  position: relative;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  max-width: 200px;
  padding: 0 8px 0 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 12px;
  color: var(--solo-text-secondary);
  cursor: pointer;
  user-select: none;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.tab:hover {
  background: rgba(255, 255, 255, 0.72);
  color: var(--solo-text-primary);
}
.tab:focus-visible {
  outline: 2px solid var(--solo-primary-light);
  outline-offset: 1px;
}
.tab.active {
  background: #fff;
  border-color: var(--solo-border);
  color: var(--solo-primary);
  font-weight: 500;
  box-shadow: var(--solo-shadow-sm);
}
/* 顶部 2px 品牌蓝：整条栏里唯一的强调色，用来回答「现在是哪一页」 */
.tab.active::before {
  content: '';
  position: absolute;
  top: -1px;
  left: -1px;
  right: -1px;
  height: 2px;
  border-radius: 8px 8px 0 0;
  background: var(--solo-primary);
}

.tab-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 首页标签固定不可关：一个小图钉替代关闭按钮，比什么都不放更能说明「这不是临时的」 */
.tab-pin {
  display: grid;
  place-items: center;
  width: 9px;
  height: 9px;
  color: var(--solo-text-muted);
  opacity: 0.75;
}

.tab-close {
  display: grid;
  place-items: center;
  width: 14px;
  height: 14px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--solo-text-muted);
  cursor: pointer;
  opacity: 0.7;
}
.tab-close:hover {
  background: var(--solo-bg-soft);
  color: var(--solo-expense);
  opacity: 1;
}

.tabs-tools {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.tab-count {
  font-size: 11px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}
.tab-count.warn {
  color: var(--solo-warning);
}
.tool-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid var(--solo-border);
  border-radius: 6px;
  background: #fff;
  color: var(--solo-text-secondary);
  cursor: pointer;
}
.tool-btn:hover {
  border-color: var(--solo-primary-light);
  color: var(--solo-primary);
}
.tool-btn.open {
  background: var(--solo-bg-blue-soft);
  border-color: #c7d8fb;
  color: var(--solo-primary);
}

.popover {
  position: absolute;
  z-index: 60;
  padding: 6px;
  border: 1px solid var(--solo-border-glass);
  border-radius: 12px;
  background: #fff;
  box-shadow: var(--solo-shadow-lg);
}
.more-panel {
  top: 44px;
  right: 12px;
  width: 272px;
}
.more-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--solo-text-secondary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.more-item:hover {
  background: var(--solo-bg-soft);
}
.more-item.current {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-weight: 500;
}
.more-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.more-pin {
  font-size: 10px;
  color: var(--solo-text-muted);
}
.more-close {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  color: var(--solo-text-muted);
  cursor: pointer;
}
.more-close:hover {
  background: var(--solo-bg-soft);
  color: var(--solo-expense);
}
.panel-foot {
  margin-top: 4px;
  padding: 6px 10px 2px;
  border-top: 1px solid var(--solo-border-light);
  color: var(--solo-text-muted);
  font-size: 10px;
  line-height: 1.6;
}

/* 右键菜单走 fixed：脱离标签栏的横向滚动容器，贴着被点的标签落下 */
.ctx-menu {
  position: fixed;
  width: 208px;
  border-radius: 10px;
}
.ctx-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--solo-text-primary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.ctx-item:hover:not(:disabled) {
  background: var(--solo-bg-soft);
}
.ctx-item:disabled {
  color: var(--solo-text-muted);
  opacity: 0.5;
  cursor: not-allowed;
}
.ctx-hint {
  color: var(--solo-text-muted);
  font-size: 10px;
}
.ctx-sep {
  height: 1px;
  margin: 4px 2px;
  background: var(--solo-border-light);
}

.confirm-mask {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: grid;
  place-items: center;
  background: rgba(24, 34, 48, 0.45);
}
.confirm-card {
  width: 404px;
  max-width: calc(100vw - 32px);
  padding: 24px;
  border-radius: 12px;
  background: #fff;
  box-shadow: var(--solo-shadow-lg);
}
.confirm-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.confirm-icon {
  flex: 0 0 auto;
}
.confirm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.confirm-body {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--solo-text-secondary);
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  margin-top: 20px;
}
.btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  background: transparent;
}
.btn.ghost {
  border-color: var(--solo-border);
  color: var(--solo-text-secondary);
}
.btn.ghost:hover {
  border-color: var(--solo-text-muted);
}
.btn.danger {
  color: var(--solo-expense);
}
.btn.danger:hover {
  background: var(--solo-expense-bg);
}
.btn.primary {
  background: var(--solo-primary);
  color: #fff;
}
.btn.primary:hover {
  background: var(--solo-primary-light);
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tabs-toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  z-index: 4100;
  transform: translateX(-50%);
  padding: 9px 16px;
  border-radius: 8px;
  background: rgba(24, 34, 48, 0.9);
  color: #fff;
  font-size: 12px;
  box-shadow: var(--solo-shadow-lg);
}

/* 窄屏交给抽屉导航，标签栏没有存在意义 */
@media (max-width: 1024px) {
  .page-tabs {
    display: none;
  }
}
</style>
