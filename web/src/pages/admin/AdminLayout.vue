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
  NTag,
  NTooltip,
  type MenuOption,
} from 'naive-ui'
import { useAdminStore } from '@/stores/admin'

const route = useRoute()
const router = useRouter()
const adminStore = useAdminStore()

const collapsed = ref(false)
const refreshing = ref(false)

// 窄屏响应式自适应
const narrowMedia = window.matchMedia('(max-width: 992px)')
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
  { label: '概览大盘', icon: 'ri-dashboard-3-line', path: '/console' },
  { label: 'Token 用量', icon: 'ri-pie-chart-2-line', path: '/console/usage' },
  { label: '用户管理', icon: 'ri-user-settings-line', path: '/console/users' },
  { label: '公司管理', icon: 'ri-building-2-line', path: '/console/companies' },
  { label: '操作日志', icon: 'ri-history-line', path: '/console/audit-logs' },
  { label: 'Agent 审计', icon: 'ri-file-shield-2-line', path: '/console/agent-audit' },
  { label: '数据运维', icon: 'ri-hard-drive-3-line', path: '/console/data-ops' },
]

const menuOptions = computed<MenuOption[]>(() =>
  navItems.map((item) => ({
    key: item.path,
    disabled: Boolean(item.milestone),
    icon: () => h('i', { class: [item.icon, 'admin-nav-icon'] }),
    label: () =>
      collapsed.value
        ? item.label
        : h('div', { class: 'admin-menu-label' }, [
            h('span', null, item.label),
            item.milestone ? h('em', { class: 'admin-menu-milestone' }, item.milestone) : null,
          ]),
  })),
)

const pageTitles: Record<string, string> = {
  '/console': '平台概览',
  '/console/usage': 'Token 用量分析',
  '/console/users': '平台用户管理',
  '/console/companies': '公司与账套管理',
  '/console/audit-logs': '管理操作留痕',
  '/console/agent-audit': 'Agent 审计流',
  '/console/data-ops': '数据运维与清理',
}

const pageTitle = computed(() => pageTitles[route.path] ?? '平台控制台')

function onMenuSelect(key: string | number) {
  void router.push(String(key))
}

async function handleRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await adminStore.triggerRefresh()
  } finally {
    refreshing.value = false
  }
}

function backToApp() {
  void router.push('/')
}

onMounted(() => {
  collapsed.value = narrowMedia.matches
  narrowMedia.addEventListener('change', onNarrowChange)
  void adminStore.probe()
})

onBeforeUnmount(() => {
  narrowMedia.removeEventListener('change', onNarrowChange)
})
</script>

<template>
  <NLayout has-sider class="admin-shell">
    <NLayoutSider
      class="admin-sider"
      collapse-mode="width"
      :width="232"
      :collapsed-width="64"
      :collapsed="collapsed"
      show-trigger="bar"
      :content-style="{ display: 'flex', flexDirection: 'column', height: '100%' }"
      @collapse="collapsed = true"
      @expand="collapsed = false"
    >
      <div class="admin-brand" :class="{ collapsed }" @click="backToApp">
        <div class="admin-brand-icon">
          <i class="ri-shield-star-line" />
        </div>
        <div class="admin-brand-text">
          <strong>梭罗记账管理台</strong>
          <span>SoloBooks Console</span>
        </div>
      </div>

      <NMenu
        class="admin-menu"
        :value="route.path"
        :options="menuOptions"
        :collapsed="collapsed"
        :collapsed-width="64"
        :icon-size="18"
        :collapsed-icon-size="20"
        @update:value="onMenuSelect"
      />

      <div class="admin-sidebar-footer" :class="{ collapsed }">
        <button class="admin-back-btn" title="返回业务端工作台" @click="backToApp">
          <i class="ri-arrow-go-back-line" />
          <span>返回业务端</span>
        </button>
      </div>
    </NLayoutSider>

    <NLayout class="admin-main">
      <header class="admin-topbar">
        <div class="admin-topbar-left">
          <NButton
            quaternary
            size="small"
            class="admin-collapse-btn"
            :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="collapsed = !collapsed"
          >
            <template #icon>
              <i :class="collapsed ? 'ri-menu-unfold-line' : 'ri-menu-fold-line'" />
            </template>
          </NButton>

          <div class="admin-breadcrumbs">
            <span class="crumb-parent">管理后台</span>
            <span class="crumb-separator">/</span>
            <span class="crumb-current">{{ pageTitle }}</span>
          </div>
        </div>

        <div class="admin-topbar-right">
          <!-- 系统微状态指标 -->
          <div v-if="adminStore.overview" class="admin-system-pills">
            <NTooltip trigger="hover">
              <template #trigger>
                <NTag size="small" round :type="adminStore.dbOk ? 'success' : 'error'" class="pill-tag">
                  <i class="ri-database-2-line" />
                  <span>DB {{ adminStore.dbOk ? '正常' : '异常' }}</span>
                </NTag>
              </template>
              {{ adminStore.overview.database.engine === 'sqlite' ? 'SQLite（开发测试）' : 'PostgreSQL' }}
            </NTooltip>

            <NTooltip trigger="hover">
              <template #trigger>
                <NTag
                  size="small"
                  round
                  :type="adminStore.circuitOpen ? 'warning' : 'success'"
                  class="pill-tag"
                >
                  <i class="ri-cpu-line" />
                  <span>LLM {{ adminStore.circuitOpen ? '熔断' : '正常' }}</span>
                </NTag>
              </template>
              {{ adminStore.circuitOpen ? '配额或鉴权异常已熔断' : 'LLM 调用正常通畅' }}
            </NTooltip>
          </div>

          <!-- 全局刷新按钮 -->
          <NButton
            quaternary
            circle
            size="small"
            class="admin-action-btn"
            :title="'刷新数据'"
            :loading="refreshing"
            @click="handleRefresh"
          >
            <template #icon>
              <i class="ri-refresh-line" :class="{ 'spin-icon': refreshing }" />
            </template>
          </NButton>

          <!-- 操作者身份 -->
          <div v-if="adminStore.operator" class="admin-operator-badge">
            <i class="ri-admin-line" />
            <span>{{ adminStore.operator.userId }}</span>
          </div>

          <!-- 快速返回工作台 -->
          <NButton size="small" tertiary class="admin-exit-btn" @click="backToApp">
            <template #icon>
              <i class="ri-dashboard-line" />
            </template>
            返回工作台
          </NButton>
        </div>
      </header>

      <main class="admin-content-viewport">
        <!-- 加载中 -->
        <div v-if="adminStore.state === 'loading' && !adminStore.overview" class="admin-state-container">
          <NSpin size="large" />
          <p class="admin-state-tip">正在验证管理员权限并加载控制台...</p>
        </div>

        <!-- 403 无权限 -->
        <div v-else-if="adminStore.state === 'forbidden'" class="admin-state-container">
          <NResult
            status="403"
            title="无管理控制台访问权限"
            description="当前账号未开启平台管理员权限（is_platform_admin = false）。如需访问，请联系运维团队授权。"
          >
            <template #footer>
              <NButton type="primary" @click="backToApp">返回业务工作台</NButton>
            </template>
          </NResult>
        </div>

        <!-- 错误状态 -->
        <div v-else-if="adminStore.state === 'error' && !adminStore.overview" class="admin-state-container">
          <NResult status="error" title="控制台加载失败" :description="adminStore.errorMessage || '无法连接管理后台服务'">
            <template #footer>
              <NButton type="primary" @click="() => adminStore.probe(true)">重试</NButton>
              <NButton quaternary @click="backToApp">返回业务端</NButton>
            </template>
          </NResult>
        </div>

        <!-- 正常渲染子页面（向后兼容传入 overview） -->
        <div v-else class="admin-view-body">
          <router-view v-slot="{ Component }">
            <component :is="Component" :overview="adminStore.overview" />
          </router-view>
        </div>
      </main>
    </NLayout>
  </NLayout>
</template>

<style scoped>
.admin-shell {
  height: 100vh;
  background-color: var(--solo-bg-page);
}

.admin-sider {
  background: var(--solo-bg-card);
  border-right: 1px solid var(--solo-border);
  box-shadow: 1px 0 3px rgba(0, 0, 0, 0.02);
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--solo-border);
  cursor: pointer;
  overflow: hidden;
  white-space: nowrap;
  user-select: none;
}

.admin-brand-icon {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-primary);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
}

.admin-brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.admin-brand-text strong {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.admin-brand-text span {
  font-size: 11px;
  color: var(--solo-text-muted);
}

.admin-brand.collapsed {
  justify-content: center;
  padding: 14px 0;
}

.admin-brand.collapsed .admin-brand-text {
  display: none;
}

.admin-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 8px;
}

.admin-nav-icon {
  font-size: 17px;
}

.admin-menu-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
}

.admin-menu-milestone {
  font-style: normal;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
}

.admin-sidebar-footer {
  border-top: 1px solid var(--solo-border);
  padding: 12px 14px;
  display: flex;
  align-items: center;
}

.admin-back-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: transparent;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 7px 12px;
  color: var(--solo-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease-in-out;
}

.admin-back-btn:hover {
  background: var(--solo-bg-soft);
  color: var(--solo-text-primary);
  border-color: var(--solo-border);
}

.admin-sidebar-footer.collapsed {
  padding: 12px 8px;
}

.admin-sidebar-footer.collapsed .admin-back-btn span {
  display: none;
}

.admin-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: var(--solo-bg-page);
}

.admin-topbar {
  height: 56px;
  background: var(--solo-bg-card);
  border-bottom: 1px solid var(--solo-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px 0 12px;
  position: sticky;
  top: 0;
  z-index: 20;
}

.admin-topbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.admin-collapse-btn {
  font-size: 17px;
  color: var(--solo-text-secondary);
}

.admin-breadcrumbs {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.crumb-parent {
  color: var(--solo-text-muted);
}

.crumb-separator {
  color: var(--solo-border);
}

.crumb-current {
  font-weight: 600;
  color: var(--solo-text-primary);
}

.admin-topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-system-pills {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pill-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
}

.admin-action-btn {
  font-size: 15px;
  color: var(--solo-text-secondary);
}

.spin-icon {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.admin-operator-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  font-size: 12.5px;
  font-weight: 550;
}

.admin-exit-btn {
  font-size: 12.5px;
}

.admin-content-viewport {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.admin-view-body {
  max-width: 1440px;
  margin: 0 auto;
}

.admin-state-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 380px;
  padding: 48px 0;
}

.admin-state-tip {
  margin-top: 14px;
  font-size: 13.5px;
  color: var(--solo-text-muted);
}
</style>
