<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { onKeyStroke } from '@vueuse/core'
import { NModal, NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { updateProfile, setPassword } from '@/api/auth'
import Sidebar from './Sidebar.vue'
import Navbar from './Navbar.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'

const router = useRouter()
const authStore = useAuthStore()
const entryStore = useEntryStore()
const message = useMessage()
const sidebarCollapsed = ref(false)
const initializing = ref(true)
const showSearch = ref(false)

const ONBOARDING_HINT_KEY = 'solo:setup-password-hint'

// ── 首次登录"完善账号信息"引导：昵称 + （未设密码时的）登录密码 ──
const showOnboarding = ref(false)
const obNickname = ref('')
const obPwd = ref('')
const obSaving = ref(false)

// 登录页在 昵称为空或未设密码 时埋下标志，这里弹一次引导。
function checkOnboardingHint() {
  if (sessionStorage.getItem(ONBOARDING_HINT_KEY) !== '1') return
  sessionStorage.removeItem(ONBOARDING_HINT_KEY)
  // 双保险：账号信息已完善则不弹
  const needNickname = !String(authStore.user?.nickname || '').trim()
  const needPassword = !authStore.user?.has_password
  if (!needNickname && !needPassword) return
  obNickname.value = authStore.user?.nickname || ''
  obPwd.value = ''
  showOnboarding.value = true
}

async function handleOnboardingSave() {
  const user = authStore.user
  const nickname = obNickname.value.trim()
  const nicknameChanged = nickname !== String(user?.nickname || '').trim()
  const wantPassword = !user?.has_password && !!obPwd.value
  // 什么都没填 = 稍后再说
  if (!nicknameChanged && !wantPassword) {
    showOnboarding.value = false
    return
  }
  if (nickname.length > 20) {
    message.warning('昵称不能超过 20 个字')
    return
  }
  if (obPwd.value && obPwd.value.length < 8) {
    message.warning('密码长度至少 8 位')
    return
  }
  obSaving.value = true
  try {
    if (nicknameChanged) {
      await updateProfile({ nickname })
    }
    if (wantPassword) {
      await setPassword({ new_password: obPwd.value })
    }
    await authStore.fetchProfile()
    message.success('账号信息已保存')
    showOnboarding.value = false
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '保存失败，请重试')
  } finally {
    obSaving.value = false
  }
}

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

  checkOnboardingHint()
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

    <!-- 首次登录：完善账号信息（昵称 + 可选的登录密码） -->
    <NModal
      v-model:show="showOnboarding"
      preset="card"
      title="完善账号信息"
      style="max-width: 420px; border-radius: var(--solo-radius-lg);"
    >
      <NForm style="margin-top: 8px;">
        <NFormItem label="昵称">
          <NInput
            v-model:value="obNickname"
            maxlength="20"
            show-count
            placeholder="如：杨会计"
          />
        </NFormItem>
        <div class="onboarding-hint">
          <i class="ri-information-line" />
          凭证署名（制单人/审核人/记账人）优先显示昵称；未设置时显示掩码手机号。
        </div>
        <NFormItem v-if="!authStore.user?.has_password" label="登录密码（可选）">
          <NInput
            v-model:value="obPwd"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位，设置后可用手机号 + 密码登录"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton :disabled="obSaving" @click="showOnboarding = false">稍后再说</NButton>
          <NButton type="primary" :loading="obSaving" @click="handleOnboardingSave">保存</NButton>
        </div>
      </template>
    </NModal>
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
.onboarding-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: -6px 0 16px 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  background: var(--solo-border-light, #f3f4f6);
  border-radius: var(--solo-radius, 8px);
  padding: 8px 10px;
  line-height: 1.5;
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
