<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import {
  defineComponent,
  h,
  onMounted,
  ref,
  shallowRef,
  watch,
  type Component,
  type ShallowRef,
} from 'vue'
import { useRouter } from 'vue-router'
import { onKeyStroke, useMediaQuery, useStorage } from '@vueuse/core'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { daysSince, EXPAND_WINDOW_DAYS, useGuideStateStore } from '@/stores/guideState'
import { useTabsStore } from '@/stores/tabs'
import { resolveActiveTab } from '@/utils/tabPages'
import SetupWelcomeModal from '@/components/business/SetupWelcomeModal.vue'
import Sidebar from './Sidebar.vue'
import Navbar from './Navbar.vue'
import PageTabs from './PageTabs.vue'
import WorkspaceViewNotice from './WorkspaceViewNotice.vue'
import WorkspaceViewSwitch from '@/components/layout/WorkspaceViewSwitch.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'

const router = useRouter()
const authStore = useAuthStore()
const entryStore = useEntryStore()
const sidebarCollapsed = useStorage('solo:sidebar-collapsed', false)
const mobileNavOpen = ref(false)
const isMobile = useMediaQuery('(max-width: 1024px)')
const initializing = ref(true)
const showSearch = ref(false)

const ONBOARDING_HINT_KEY = 'solo:setup-password-hint'
const L0_REPLAY_KEY = 'solo:l0-replay'
const L0_SHOWN_KEY = 'solo:l0-shown'
const LEGACY_DISMISS_PREFIX = 'solo:setup-hint-dismissed:'

const guideStore = useGuideStateStore()
const showWelcome = ref(false)
const tabsStore = useTabsStore()

/**
 * 让 <KeepAlive :include> 能按「路由 name」精确命中。
 *
 * <script setup> 写出来的页面没有 name 选项，include 根本匹配不到它们；
 * 与其给十几个页面逐个加 defineOptions，不如在这里套一层同名壳：
 * include 比较的是壳的名字，壳再把真正的页面渲染出来。页面文件零改动。
 *
 * holder 是可写的，HMR 换了组件也会跟着换，不会把旧版本焊死在缓存里。
 */
const viewShells = new Map<string, { shell: Component; holder: ShallowRef<Component | null> }>()

function viewShell(target: Component | undefined, name: unknown): Component | null {
  if (!target) return null
  const key = String(name ?? 'anonymous')
  const cached = viewShells.get(key)
  if (cached) {
    cached.holder.value = target
    return cached.shell
  }
  const holder = shallowRef<Component | null>(target)
  const shell = defineComponent({
    name: key,
    setup: () => () => (holder.value ? h(holder.value) : null),
  })
  viewShells.set(key, { shell, holder })
  return shell
}

/** 手动刷新某页时 token 变化 → key 变化 → 页面重新挂载、重新拉数据 */
function viewKey(name: unknown, path: string): string {
  return `${String(name ?? path)}:${tabsStore.reloadToken(resolveActiveTab(path))}`
}

/**
 * L0 起跑线：首次登录唯一的一次引导（昵称/密码已并入其中一步）。
 *
 * 三种触发：登录页埋的「账号待完善」信号、/guide 的「重看新手引导」、
 * 以及账号没看过且公司建了不到 14 天。老账套升级上来的用户不会被打扰。
 * 打开即记 l0SeenAt——不是关闭才记，否则按 Esc 下次登录又要弹一遍。
 */
function maybeOpenL0() {
  if (sessionStorage.getItem(L0_REPLAY_KEY) === '1') {
    sessionStorage.removeItem(L0_REPLAY_KEY)
    showWelcome.value = true
    return
  }
  if (sessionStorage.getItem(ONBOARDING_HINT_KEY) === '1') {
    sessionStorage.removeItem(ONBOARDING_HINT_KEY)
    guideStore.markL0Seen()
    showWelcome.value = true
    return
  }
  guideStore.load()
  if (guideStore.state.l0SeenAt) return
  try {
    // 兼容旧键：以前关过「完善账号信息」的账号不再被新引导打扰
    if (localStorage.getItem(`${LEGACY_DISMISS_PREFIX}${authStore.user?.user_id || ''}`) === '1') return
  } catch { /* localStorage 不可用时按未关过处理 */ }
  if (sessionStorage.getItem(L0_SHOWN_KEY) === '1') return
  const company = authStore.companies.find((item) => item.company_id === authStore.currentCompanyId)
  const age = daysSince(company?.created_at)
  if (age === null || age >= EXPAND_WINDOW_DAYS) return
  guideStore.markL0Seen()
  sessionStorage.setItem(L0_SHOWN_KEY, '1')
  showWelcome.value = true
}

onKeyStroke('k', (e) => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    showSearch.value = !showSearch.value
  }
})

function toggleSidebar() {
  if (isMobile.value) {
    mobileNavOpen.value = true
    return
  }
  sidebarCollapsed.value = !sidebarCollapsed.value
}

watch(isMobile, (mobile) => {
  if (!mobile) mobileNavOpen.value = false
})

onMounted(async () => {
  if (!authStore.isAuthenticated) {
    router.push('/login')
    return
  }
  try {
    await authStore.fetchProfile()
  } catch {
    router.push('/login')
    return
  }

  try {
    await entryStore.fetchEntries()
  } catch (error) {
    console.error('Failed to load ledger entries during app initialization', error)
  } finally {
    initializing.value = false
  }

  maybeOpenL0()
})
</script>

<template>
  <div v-if="initializing" class="loading-screen">
    <div class="loading-spinner" />
    <p>加载中...</p>
  </div>

  <div v-else class="app-layout">
    <Sidebar class="no-print" :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
    <div class="main-area" :class="{ collapsed: sidebarCollapsed }">
      <Navbar class="no-print" @toggle-sidebar="toggleSidebar" @open-search="showSearch = true" />
      <PageTabs />
      <main class="content" data-page-scroll>
        <WorkspaceViewNotice />
        <!-- 标签保活：只有开着的标签在 include 里，关掉即释放内存 -->
        <router-view v-slot="{ Component, route: current }">
          <keep-alive :include="tabsStore.cachedViews" :max="14">
            <component
              :is="viewShell(Component, current.name)"
              :key="viewKey(current.name, current.path)"
            />
          </keep-alive>
        </router-view>
      </main>
    </div>
    <GlobalSearch v-if="showSearch" @close="showSearch = false" />

    <NDrawer v-model:show="mobileNavOpen" placement="left" :width="280" :trap-focus="true">
      <NDrawerContent
        title="导航"
        closable
        :native-scrollbar="false"
        body-content-style="padding: 0; height: 100%; display: flex; flex-direction: column;"
      >
        <div class="mobile-view-switch">
          <WorkspaceViewSwitch />
        </div>
        <Sidebar
          mobile
          :collapsed="false"
          @navigate="mobileNavOpen = false"
        />
      </NDrawerContent>
    </NDrawer>

    <!-- L0 起跑线：首次登录唯一的一次引导（昵称/密码已并入其中一步） -->
    <SetupWelcomeModal v-model:show="showWelcome" />
  </div>
</template>

<style scoped>
.loading-screen {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-page);
}
.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid rgba(99, 102, 241, 0.1);
  border-top-color: var(--solo-primary);
  border-radius: 50%;
  animation: spin 0.8s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.mobile-view-switch {
  flex-shrink: 0;
  padding: 12px 16px 4px;
  border-bottom: 1px solid var(--solo-border-light);
}
.app-layout {
  display: flex;
  /* 高度锁死在视口，让 .content 成为真正的滚动区。
     原来是 min-height: 100vh —— 内容一高容器跟着长，.content 的
     overflow-y:auto 永远不触发，于是它变成了一个"不滚动的滚动容器"，
     里面的 position: sticky 全部失效（凭证列表的日期组头就栽在这）。
     侧栏是 fixed、导航栏在流内，锁定高度对它们没有影响。 */
  height: 100vh;
  background-color: var(--solo-bg-page);
  position: relative;
  overflow: hidden;
}
.main-area {
  flex: 1;
  margin-left: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  transition: margin-left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  z-index: 2;
  min-width: 0;
  /* flex 子项默认 min-height:auto，会撑破父容器导致内部不滚动 */
  min-height: 0;
}
.main-area.collapsed {
  margin-left: var(--sidebar-collapsed-width);
}
.content {
  flex: 1;
  /* 纵向 padding 必须为 0：这里是滚动容器，一旦它有 padding-top，
     position: sticky 的元素会吸附在 padding 内侧，padding 那一条永远
     空着，滚过去的内容会从缝里露出来。页面顶部留白改由
     layout.css 的 `.content > *` 提供（跟着内容一起滚走）。 */
  padding: 0 40px;
  overflow-y: auto;
}
@media (max-width: 768px) {
  .content {
    padding: 0 16px;
  }
}
@media (max-width: 1024px) {
  .main-area,
  .main-area.collapsed {
    margin-left: 0;
  }
}
</style>
