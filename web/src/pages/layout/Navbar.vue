<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, useMessage } from 'naive-ui'
import type { DropdownOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useUsageStore } from '@/stores/usage'
import { SoloStatusBadge } from '@/components/ui'
import WorkspaceViewSwitch from '@/components/layout/WorkspaceViewSwitch.vue'

const emit = defineEmits<{ toggleSidebar: []; openSearch: [] }>()

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const usageStore = useUsageStore()
const shortcutLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K'

// 导航栏摘要只读缓存；个人设置页会强制刷新
onMounted(() => {
  usageStore.ensureLoaded()
})

function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 10000) return `${(n / 10000).toFixed(2)} 万`
  return n.toLocaleString()
}

const currentCompanyName = computed(() => {
  if (!authStore.user) return '加载中...'
  const co = authStore.companies.find((c) => c.company_id === authStore.currentCompanyId)
  return co?.name || '默认账本'
})

// 演示账套要一眼可辨：里面的数字是样本，不能当真账看
const isDemoCompany = computed(
  () =>
    authStore.companies.find((c) => c.company_id === authStore.currentCompanyId)?.isDemo === true,
)

const companyOptions = computed<DropdownOption[]>(() => {
  const items: DropdownOption[] = authStore.companies.map((co) => ({
    label: co.name,
    key: co.company_id,
    icon: () =>
      co.company_id === authStore.currentCompanyId ? '✓' : '',
  }))
  items.push({ type: 'divider' as const })
  items.push({ label: '管理主体', key: 'manage' })
  return items
})

// 下拉渲染在 body 传送门里，scoped 样式够不到，用量行用内联样式
const userOptions = computed<DropdownOption[]>(() => {
  const usageTokens = usageStore.summary?.currentMonth.totalTokens ?? null
  return [
    {
      key: 'usage',
      label: () =>
        h('div', { style: 'display:flex;align-items:center;gap:8px;min-width:200px;' }, [
          h('i', {
            class: 'ri-sparkling-2-line',
            style: 'font-size:15px;color:var(--solo-primary,#175cff);',
          }),
          h('span', { style: 'font-size:13px;color:var(--solo-text-secondary,#555);' }, '本月 AI 用量'),
          h(
            'span',
            {
              style:
                'margin-left:auto;font-size:12.5px;font-weight:600;color:var(--solo-text-primary,#1a1a1a);font-variant-numeric:tabular-nums;',
            },
            usageTokens === null ? '—' : `${formatTokens(usageTokens)} tokens`,
          ),
        ]),
    },
    { type: 'divider' as const },
    { label: '上手引导', key: 'guide' },
    { label: '个人设置', key: 'mine' },
    { label: '公司管理', key: 'companies' },
    { type: 'divider' as const },
    { label: '退出登录', key: 'logout' },
  ]
})

async function handleCompanyAction(key: string) {
  if (key === 'manage') {
    router.push('/mine/companies')
    return
  }
  if (key === authStore.currentCompanyId) return
  try {
    const co = authStore.companies.find((c) => c.company_id === key)
    await authStore.switchCompanyContext(key)
    message.success(`已切换到 ${co?.name || '默认账本'}`)
  } catch {
    message.error('切换失败')
  }
}

function handleUserAction(key: string) {
  if (key === 'logout') {
    authStore.logout()
    usageStore.reset()
    router.push('/login')
  } else if (key === 'usage') {
    router.push('/mine')
  } else if (key === 'guide') {
    router.push('/guide')
  } else if (key === 'mine') {
    router.push('/mine')
  } else if (key === 'companies') {
    router.push('/mine/companies')
  }
}
</script>

<template>
  <header class="navbar">
    <div class="navbar-left">
      <button
        class="menu-btn no-print"
        aria-label="打开导航"
        @click="emit('toggleSidebar')"
      >
        <i class="ri-menu-2-line" />
      </button>

      <div class="divider-y" />

      <NDropdown
        :options="companyOptions"
        trigger="click"
        @select="handleCompanyAction"
      >
        <button class="company-switcher">
          <i class="ri-building-4-line switcher-icon" />
          <span class="company-name">{{ currentCompanyName }}</span>
          <i class="ri-arrow-down-s-line arrow-icon" />
        </button>
      </NDropdown>

      <SoloStatusBadge v-if="isDemoCompany" plain tone="info">演示账套</SoloStatusBadge>

      <WorkspaceViewSwitch class="workspace-view-switch" />
    </div>

    <!-- Search shortcut bar in the middle -->
    <div class="navbar-center no-print">
      <div
        class="search-trigger"
        role="button"
        tabindex="0"
        @click="emit('openSearch')"
        @keydown.enter="emit('openSearch')"
        @keydown.space.prevent="emit('openSearch')"
      >
        <i class="ri-search-2-line search-icon" />
        <span class="search-text">搜索或输入命令...</span>
        <span class="kbd">{{ shortcutLabel }}</span>
      </div>
    </div>

    <div class="navbar-right">
      <NDropdown :options="userOptions" @select="handleUserAction">
        <div class="user-profile-btn">
          <div class="avatar-wrapper">
            <span class="avatar-text">{{ authStore.user?.nickname?.[0] || 'U' }}</span>
          </div>
          <div class="user-info">
            <span class="user-name">{{ authStore.user?.nickname || '用户' }}</span>
          </div>
          <i class="ri-arrow-down-s-line user-arrow" />
        </div>
      </NDropdown>
    </div>
  </header>
</template>

<style scoped>
.navbar {
  height: var(--navbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--solo-bg-nav);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--solo-border-glass);
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 90;
}
.navbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  min-width: 0;
}
.workspace-view-switch {
  margin-left: 2px;
}
.menu-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: var(--solo-text-secondary);
  padding: 8px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}
.menu-btn:hover {
  background: var(--solo-border-light);
  color: var(--solo-primary);
}
.divider-y {
  width: 1px;
  height: 20px;
  background: var(--solo-border);
}
.company-switcher {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  border: 1px solid var(--solo-border);
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 8px;
  transition: all 0.2s;
}
.company-switcher:hover {
  background: var(--solo-bg-blue-soft);
  border-color: rgba(22, 93, 255, 0.24);
}
.switcher-icon {
  font-size: 15px;
  color: var(--solo-primary);
}
.company-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.arrow-icon {
  font-size: 14px;
  color: var(--solo-text-muted);
}
.navbar-center {
  flex: 0 1 240px;
  min-width: 0;
  margin: 0 12px;
}
.search-trigger {
  width: 100%;
  box-sizing: border-box;
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  height: 36px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--solo-text-muted);
  cursor: pointer;
  font-size: 13px;
  white-space: nowrap;
  user-select: none;
  transition: all 0.2s ease;
}
.search-trigger:hover {
  border-color: var(--solo-primary-light);
  background: var(--solo-bg-card);
  box-shadow: var(--solo-shadow-sm);
  color: var(--solo-text-primary);
}
.search-icon {
  font-size: 15px;
  color: var(--solo-text-muted);
  flex-shrink: 0;
  transition: color 0.2s ease;
}
.search-trigger:hover .search-icon {
  color: var(--solo-primary);
}
.search-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.kbd {
  flex-shrink: 0;
  margin-left: auto;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  border-radius: var(--solo-radius-sm, 4px);
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 500;
  font-family: inherit;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}
.navbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.user-profile-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid transparent;
}
.user-profile-btn:hover {
  background: #fff;
  border-color: var(--solo-border);
  box-shadow: var(--solo-shadow-sm);
}
.avatar-wrapper {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: none;
}
.avatar-text {
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}
.user-info {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
}
.user-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  max-width: 90px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-arrow {
  font-size: 14px;
  color: var(--solo-text-muted);
}
@media (max-width: 1180px) {
  .company-name {
    max-width: 130px;
  }
  .kbd {
    display: none;
  }
}
@media (max-width: 960px) {
  .navbar {
    padding: 0 16px;
  }
  .navbar-center,
  .divider-y,
  .user-info,
  .user-arrow {
    display: none;
  }
  .company-name {
    max-width: 160px;
  }
}
@media (max-width: 560px) {
  .company-name {
    max-width: 120px;
  }
}
</style>
