<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { onKeyStroke } from '@vueuse/core'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import Sidebar from './Sidebar.vue'
import Navbar from './Navbar.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'

const router = useRouter()
const authStore = useAuthStore()
const entryStore = useEntryStore()
const sidebarCollapsed = ref(false)
const initializing = ref(true)
const showSearch = ref(false)

onKeyStroke('k', (e) => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    showSearch.value = !showSearch.value
  }
})

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

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
})
</script>

<template>
  <div v-if="initializing" class="loading-screen">
    <div class="loading-spinner" />
    <p>加载中...</p>
  </div>

  <div v-else class="app-layout">
    <Sidebar :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
    <div class="main-area" :class="{ collapsed: sidebarCollapsed }">
      <Navbar @toggle-sidebar="toggleSidebar" />
      <main class="content">
        <router-view />
      </main>
    </div>
    <GlobalSearch v-if="showSearch" @close="showSearch = false" />
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
.app-layout {
  display: flex;
  min-height: 100vh;
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
}
.main-area.collapsed {
  margin-left: var(--sidebar-collapsed-width);
}
.content {
  flex: 1;
  padding: 28px 40px;
  overflow-y: auto;
}
@media (max-width: 768px) {
  .content {
    padding: 20px 16px;
  }
}
@media (max-width: 1024px) {
  .main-area,
  .main-area.collapsed {
    margin-left: 0;
  }
}
</style>
