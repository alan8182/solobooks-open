<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NLayout,
  NLayoutSider,
  NMenu,
  NResult,
  NSpin,
  useMessage,
  type MenuOption,
} from 'naive-ui'
import { fetchAdminOverview, type AdminOverviewData } from '@/api/admin'

const route = useRoute()
const router = useRouter()
const message = useMessage()

type ProbeState = 'loading' | 'ok' | 'forbidden' | 'error'
const state = ref<ProbeState>('loading')
const overview = ref<AdminOverviewData | null>(null)

const collapsed = ref(false)

// 窄屏自动收起侧边栏（naive-ui 2.44 的 LayoutSider 未内置 breakpoint 属性）
const narrowMedia = window.matchMedia('(max-width: 768px)')
function onNarrowChange(e: MediaQueryListEvent) {
  collapsed.value = e.matches
}

interface AdminNavItem {
  label: string
  icon: string
  path: string
  milestone?: string
}

const navItems: AdminNavItem[] = [
  { label: '概览', icon: 'ri-dashboard-3-line', path: '/admin' },
  { label: 'Token 用量', icon: 'ri-pie-chart-2-line', path: '/admin/usage' },
  { label: '用户管理', icon: 'ri-user-settings-line', path: '/admin/users' },
  { label: '公司管理', icon: 'ri-building-2-line', path: '/admin/companies' },
  { label: '操作日志', icon: 'ri-history-line', path: '/admin/audit-logs' },
  { label: 'Agent 审计', icon: 'ri-file-shield-2-line', path: '/admin/agent-audit' },
  { label: '数据运维', icon: 'ri-hard-drive-3-line', path: '/admin/data-ops' },
]

const menuOptions = computed<MenuOption[]>(() =>
  navItems.map((item) => ({
    key: item.path,
    disabled: Boolean(item.milestone),
    icon: () => h('i', { class: item.icon }),
    label: () =>
      collapsed.value
        ? item.label
        : h('span', { class: 'admin-menu-label', title: item.milestone ? `随 ${item.milestone} 上线` : undefined }, [
            h('span', null, item.label),
            item.milestone ? h('em', { class: 'admin-menu-milestone' }, item.milestone) : null,
          ]),
  })),
)

const pageTitles: Record<string, string> = {
  '/admin': '平台概览',
  '/admin/usage': 'Token 用量',
  '/admin/users': '用户管理',
  '/admin/companies': '公司管理',
  '/admin/audit-logs': '操作日志',
  '/admin/agent-audit': 'Agent 审计',
  '/admin/data-ops': '数据运维',
}

const pageTitle = computed(() => pageTitles[route.path] ?? '管理后台')

function onMenuSelect(key: string | number) {
  void router.push(String(key))
}

async function probe() {
  state.value = 'loading'
  try {
    overview.value = await fetchAdminOverview()
    state.value = 'ok'
  } catch (err: any) {
    if (err?.response?.status === 403) {
      state.value = 'forbidden'
    } else {
      state.value = 'error'
      message.error(err?.response?.data?.detail || '管理后台加载失败，请稍后重试')
    }
  }
}

function backToApp() {
  router.push('/')
}

onMounted(() => {
  collapsed.value = narrowMedia.matches
  narrowMedia.addEventListener('change', onNarrowChange)
  probe()
})

onBeforeUnmount(() => {
  narrowMedia.removeEventListener('change', onNarrowChange)
})
</script>

<template>
  <n-layout has-sider class="admin-shell">
    <n-layout-sider
      class="admin-sider"
      collapse-mode="width"
      :width="232"
      :collapsed-width="64"
      :collapsed="collapsed"
      inverted
      show-trigger="bar"
      :content-style="{ display: 'flex', flexDirection: 'column' }"
      @collapse="collapsed = true"
      @expand="collapsed = false"
    >
      <div class="admin-brand" :class="{ collapsed }" @click="backToApp">
        <div class="admin-brand-icon"><i class="ri-shield-star-line" /></div>
        <div class="admin-brand-text">
          <strong>梭罗记账管理台</strong>
          <span>SoloBooks Admin</span>
        </div>
      </div>

      <n-menu
        class="admin-menu"
        :value="route.path"
        :options="menuOptions"
        :collapsed="collapsed"
        :collapsed-width="64"
        :icon-size="18"
        :collapsed-icon-size="20"
        inverted
        @update:value="onMenuSelect"
      />

      <div class="admin-sidebar-footer" :class="{ collapsed }">
        <a class="admin-back" title="返回业务端" @click="backToApp">
          <i class="ri-arrow-go-back-line" />
          <span>返回业务端</span>
        </a>
      </div>
    </n-layout-sider>

    <n-layout class="admin-main">
      <header class="admin-topbar">
        <div class="admin-topbar-left">
          <n-button
            quaternary
            class="admin-collapse-btn"
            :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="collapsed = !collapsed"
          >
            <template #icon>
              <i class="collapse-icon" :class="collapsed ? 'ri-menu-unfold-line' : 'ri-menu-fold-line'" />
            </template>
          </n-button>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div v-if="overview" class="admin-operator">
          <i class="ri-admin-line" />
          <span>{{ overview.operator.userId }}</span>
        </div>
      </header>

      <div class="admin-content">
        <div v-if="state === 'loading'" class="admin-state">
          <NSpin size="large" />
        </div>
        <div v-else-if="state === 'forbidden'" class="admin-state">
          <NResult status="403" title="无管理员权限" desc="当前账号不是平台管理员。如需开通，请联系运维用 make_admin.py 置位。">
            <template #footer>
              <NButton type="primary" @click="backToApp">返回业务端</NButton>
            </template>
          </NResult>
        </div>
        <div v-else-if="state === 'error'" class="admin-state">
          <NResult status="error" title="加载失败" desc="无法获取管理后台数据，请稍后重试。">
            <template #footer>
              <NButton type="primary" @click="probe">重试</NButton>
              <NButton quaternary @click="backToApp">返回业务端</NButton>
            </template>
          </NResult>
        </div>
        <router-view v-else v-slot="{ Component }">
          <component :is="Component" :overview="overview" />
        </router-view>
      </div>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.admin-shell {
  height: 100vh;
}

.admin-sider {
  background: #10192e;
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  cursor: pointer;
  overflow: hidden;
  white-space: nowrap;
}

.admin-brand-icon {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: var(--solo-radius);
  background: linear-gradient(135deg, var(--solo-primary-light), var(--solo-primary-dark));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.admin-brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.admin-brand-text strong {
  font-size: 15px;
}

.admin-brand-text span {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}

.admin-brand.collapsed {
  flex-direction: column;
  gap: 8px;
  padding-left: 0;
  padding-right: 0;
}

.admin-brand.collapsed .admin-brand-text {
  display: none;
}

.admin-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px;
}

/* n-menu 收起态用内联 padding-left=(64-20)/2=22px 居中图标，按菜单项占满收起宽度计算；
   这里需按同口径抵消 .admin-menu 的左右 10px 内边距，否则图标整体右偏 10px */
.admin-menu.n-menu--collapsed :deep(.n-menu-item-content) {
  padding-left: calc((64px - 20px) / 2 - 10px) !important;
}

.admin-menu-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.admin-menu-milestone {
  font-style: normal;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
}

.admin-sidebar-footer {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 10px;
  display: flex;
  justify-content: center;
}

.admin-back {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.62);
  font-size: 13px;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: var(--solo-radius-sm);
  white-space: nowrap;
}

.admin-back:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.06);
}

.admin-sidebar-footer.collapsed .admin-back span {
  display: none;
}

.admin-main {
  min-width: 0;
}

.admin-topbar {
  height: 60px;
  background: var(--solo-bg-card);
  border-bottom: 1px solid var(--solo-border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px 0 12px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.admin-topbar-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.admin-topbar h1 {
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.collapse-icon {
  font-size: 18px;
}

.admin-operator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.admin-content {
  padding: 24px;
}

.admin-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 96px 0;
}
</style>
