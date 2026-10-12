<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, useMessage } from 'naive-ui'
import type { DropdownOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ toggleSidebar: [] }>()

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

const currentCompanyName = computed(() => {
  if (!authStore.user) return '加载中...'
  const co = authStore.companies.find((c) => c.company_id === authStore.currentCompanyId)
  return co?.name || '默认账本'
})

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

const userOptions: DropdownOption[] = [
  { label: '个人中心', key: 'mine' },
  { label: '公司管理', key: 'companies' },
  { type: 'divider' as const },
  { label: '退出登录', key: 'logout' },
]

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
    router.push('/login')
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
      <button class="menu-btn no-print" @click="emit('toggleSidebar')">
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
    </div>

    <!-- Search shortcut bar in the middle -->
    <div class="navbar-center no-print">
      <div class="search-trigger" @click="message.info('按 Ctrl + K 或 ⌘ + K 唤起全局搜索')">
        <i class="ri-search-2-line" />
        <span>搜索...</span>
        <span class="kbd">⌘ K</span>
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
            <span class="user-role">管理员</span>
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
  gap: 16px;
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
}
.arrow-icon {
  font-size: 14px;
  color: var(--solo-text-muted);
}
.navbar-center {
  flex: 1;
  max-width: 420px;
  margin: 0 24px;
}
.search-trigger {
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  height: 36px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--solo-text-muted);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}
.search-trigger:hover {
  border-color: var(--solo-primary-light);
  background: #fff;
}
.search-trigger span {
  flex: 1;
}
.kbd {
  background: #fff;
  border: 1px solid var(--solo-border);
  box-shadow: 0 1px 1px rgba(0,0,0,0.05);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
.navbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.user-profile-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-radius: 10px;
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
}
.user-role {
  font-size: 10px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.user-arrow {
  font-size: 14px;
  color: var(--solo-text-muted);
}
@media (max-width: 900px) {
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
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
@media (max-width: 560px) {
  .company-name {
    max-width: 120px;
  }
}
</style>
