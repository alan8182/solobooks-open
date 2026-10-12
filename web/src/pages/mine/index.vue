<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NModal, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { formatMoney } from '@/utils/format'
import { setPassword, updateProfile } from '@/api/auth'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const entryStore = useEntryStore()

const user = ref(authStore.user)

async function handleLogout() {
  authStore.logout()
  message.success('已退出登录')
  router.push('/login')
}

// ── 编辑资料（昵称） ──
const profileVisible = ref(false)
const nicknameInput = ref('')
const profileSaving = ref(false)

function openProfileModal() {
  nicknameInput.value = authStore.user?.nickname || ''
  profileVisible.value = true
}

async function handleSaveProfile() {
  const nickname = nicknameInput.value.trim()
  if (nickname.length > 20) {
    message.warning('昵称不能超过 20 个字')
    return
  }
  profileSaving.value = true
  try {
    await updateProfile({ nickname })
    message.success('资料已保存')
    profileVisible.value = false
    await authStore.fetchProfile()
    user.value = authStore.user
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '保存失败，请重试')
  } finally {
    profileSaving.value = false
  }
}

// ── 设置密码 ──
const pwdVisible = ref(false)
const pwdOld = ref('')
const pwdNew = ref('')
const pwdSaving = ref(false)
const hasPassword = computed(() => !!authStore.user?.has_password)

function openPwdModal() {
  pwdOld.value = ''
  pwdNew.value = ''
  pwdVisible.value = true
}

async function handleSavePassword() {
  if (hasPassword.value && !pwdOld.value) {
    message.warning('请输入当前密码')
    return
  }
  if (pwdNew.value.length < 8) {
    message.warning('密码长度至少 8 位')
    return
  }
  pwdSaving.value = true
  try {
    await setPassword({
      old_password: hasPassword.value ? pwdOld.value : undefined,
      new_password: pwdNew.value,
    })
    message.success(hasPassword.value ? '密码已修改' : '密码已设置')
    pwdVisible.value = false
    await authStore.fetchProfile()
    user.value = authStore.user
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '保存失败，请重试')
  } finally {
    pwdSaving.value = false
  }
}

onMounted(async () => {
  if (!authStore.user && authStore.token) {
    try {
      await authStore.fetchProfile()
      user.value = authStore.user
    } catch {
      // ignore
    }
  }
})
</script>

<template>
  <div class="mine-page">
    <div class="page-header">
      <h2>个人中心</h2>
    </div>

    <!-- User info -->
    <NCard :bordered="false" class="user-card mb-4">
      <div class="user-info">
        <div class="user-avatar">{{ user?.nickname?.[0] || '?' }}</div>
        <div class="user-detail">
          <div class="user-name">{{ user?.nickname || '用户' }}</div>
          <div class="user-phone">{{ user?.phone || '' }}</div>
        </div>
        <NButton text size="small" class="edit-profile-btn" @click="openProfileModal">
          <template #icon><i class="ri-edit-line" /></template>
          编辑
        </NButton>
      </div>
    </NCard>

    <!-- Stats -->
    <NCard :bordered="false" class="stats-card mb-4">
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-number">{{ entryStore.activeEntries.length }}</span>
          <span class="stat-label">记账笔数</span>
        </div>
        <div class="stat-item">
          <span class="stat-number income">{{ formatMoney(entryStore.monthlyIncome) }}</span>
          <span class="stat-label">本月收入</span>
        </div>
        <div class="stat-item">
          <span class="stat-number expense">{{ formatMoney(entryStore.monthlyExpense) }}</span>
          <span class="stat-label">本月支出</span>
        </div>
      </div>
    </NCard>

    <!-- Menu list -->
    <NCard :bordered="false" class="menu-card mb-4">
      <div class="menu-item" @click="openPwdModal">
        <span class="menu-icon"><i class="ri-lock-password-line" /></span>
        <span class="menu-label">设置密码</span>
        <span class="menu-extra">{{ hasPassword ? '已设置' : '未设置' }}</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/companies')">
        <span class="menu-icon"><i class="ri-building-2-line" /></span>
        <span class="menu-label">公司管理</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/categories')">
        <span class="menu-icon"><i class="ri-folder-2-line" /></span>
        <span class="menu-label">分类管理</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/accounts')">
        <span class="menu-icon"><i class="ri-book-2-line" /></span>
        <span class="menu-label">科目表</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/salary')">
        <span class="menu-icon"><i class="ri-money-cny-circle-line" /></span>
        <span class="menu-label">薪酬社保</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/opening')">
        <span class="menu-icon"><i class="ri-scales-3-line" /></span>
        <span class="menu-label">期初余额</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/recurring')">
        <span class="menu-icon"><i class="ri-repeat-2-line" /></span>
        <span class="menu-label">周期性模板</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/bank')">
        <span class="menu-icon"><i class="ri-bank-line" /></span>
        <span class="menu-label">银行对账</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/audit-log')">
        <span class="menu-icon"><i class="ri-file-list-3-line" /></span>
        <span class="menu-label">审计日志</span>
        <span class="menu-arrow">→</span>
      </div>
    </NCard>

    <NButton block @click="handleLogout">退出登录</NButton>

    <NModal
      v-model:show="pwdVisible"
      preset="card"
      :title="hasPassword ? '修改密码' : '设置密码'"
      style="max-width: 400px; border-radius: var(--solo-radius-lg);"
    >
      <NForm style="margin-top: 8px;">
        <NFormItem v-if="hasPassword" label="当前密码">
          <NInput
            v-model:value="pwdOld"
            type="password"
            show-password-on="click"
            placeholder="请输入当前密码"
          >
            <template #prefix>
              <i class="ri-lock-password-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
        <NFormItem label="新密码">
          <NInput
            v-model:value="pwdNew"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位"
          >
            <template #prefix>
              <i class="ri-lock-password-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
      </NForm>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton :disabled="pwdSaving" @click="pwdVisible = false">取消</NButton>
          <NButton type="primary" :loading="pwdSaving" @click="handleSavePassword">保存</NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="profileVisible"
      preset="card"
      title="编辑资料"
      style="max-width: 400px; border-radius: var(--solo-radius-lg);"
    >
      <NForm style="margin-top: 8px;">
        <NFormItem label="昵称">
          <NInput
            v-model:value="nicknameInput"
            maxlength="20"
            show-count
            placeholder="如：杨会计"
          />
        </NFormItem>
      </NForm>
      <div class="profile-hint">
        <i class="ri-information-line" />
        凭证署名（制单人/审核人/记账人）优先显示昵称；未设置时显示掩码手机号。
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton :disabled="profileSaving" @click="profileVisible = false">取消</NButton>
          <NButton type="primary" :loading="profileSaving" @click="handleSaveProfile">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mine-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }

/* User */
.user-card {
  border-radius: var(--solo-radius-lg);
}
.user-info {
  display: flex;
  align-items: center;
  gap: 16px;
}
.edit-profile-btn {
  margin-left: auto;
  color: var(--solo-primary);
}
.profile-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  background: var(--solo-border-light, #f3f4f6);
  border-radius: var(--solo-radius, 8px);
  padding: 8px 10px;
  line-height: 1.5;
}
.user-avatar {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--solo-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 700;
}
.user-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.user-phone {
  font-size: 13px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}

/* Stats */
.stats-card {
  border-radius: var(--solo-radius-lg);
}
.stats-grid {
  display: flex;
  gap: 24px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-number {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.stat-number.income { color: var(--solo-income); }
.stat-number.expense { color: var(--solo-expense); }
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* Menu */
.menu-card {
  border-radius: var(--solo-radius-lg);
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  cursor: pointer;
}
.menu-icon {
  font-size: 18px;
  color: var(--solo-text-secondary);
  display: flex;
  align-items: center;
}
.menu-extra {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-right: 8px;
}
.menu-label {
  flex: 1;
  font-size: 14px;
  color: var(--solo-text-primary);
}
.menu-arrow {
  color: var(--solo-text-muted);
  font-size: 14px;
}
.menu-divider {
  height: 1px;
  background: var(--solo-border-light);
  margin: 6px 0;
}
</style>
