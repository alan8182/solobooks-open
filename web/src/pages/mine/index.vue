<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NModal, NForm, NFormItem, NInput, NTag, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { setPassword, updateProfile } from '@/api/auth'

// ponytail: streamlined mine page from messy duplicate feature hub into clean user profile & account security
const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

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
      <div class="page-header-left">
        <div class="title-section">
          <h2>个人设置</h2>
          <p class="subtitle">管理您的个人资料、账号安全与登录凭证</p>
        </div>
      </div>
    </div>

    <!-- 个人基本资料 -->
    <NCard :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">基本资料</div>
      <div class="user-info-row">
        <div class="user-avatar">{{ user?.nickname?.[0] || '?' }}</div>
        <div class="user-detail">
          <div class="user-name-line">
            <span class="user-name">{{ user?.nickname || '未设置昵称' }}</span>
            <NTag size="tiny" type="info" :bordered="false">管理员</NTag>
          </div>
          <div class="user-phone">绑定手机：{{ user?.phone || '未绑定' }}</div>
        </div>
        <NButton secondary size="small" class="action-btn" @click="openProfileModal">
          <template #icon><i class="ri-edit-line" /></template>
          修改昵称
        </NButton>
      </div>
      <div class="profile-tip">
        <i class="ri-information-line" />
        凭证署名（制单人/审核人/记账人）优先显示此昵称；未设置时显示掩码手机号。
      </div>
    </NCard>

    <!-- 安全与登录 -->
    <NCard :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">账号与安全</div>
      <div class="setting-item-row">
        <div class="item-icon-box">
          <i class="ri-lock-password-line" />
        </div>
        <div class="item-info">
          <div class="item-title">
            <span>登录密码</span>
            <NTag size="tiny" :type="hasPassword ? 'success' : 'warning'" :bordered="false">
              {{ hasPassword ? '已设置' : '未设置' }}
            </NTag>
          </div>
          <div class="item-desc">设置后支持使用手机号与密码直接登录</div>
        </div>
        <NButton secondary size="small" class="action-btn" @click="openPwdModal">
          {{ hasPassword ? '修改密码' : '设置密码' }}
        </NButton>
      </div>

      <div class="item-divider" />

      <div class="setting-item-row">
        <div class="item-icon-box">
          <i class="ri-smartphone-line" />
        </div>
        <div class="item-info">
          <div class="item-title">
            <span>登录手机号</span>
            <NTag size="tiny" type="success" :bordered="false">已绑定</NTag>
          </div>
          <div class="item-desc">{{ user?.phone || '暂无手机号' }}</div>
        </div>
      </div>
    </NCard>

    <!-- 开放接入与令牌 -->
    <NCard :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">开发者与 AI 接入</div>
      <div class="setting-item-row">
        <div class="item-icon-box mcp-icon">
          <i class="ri-plug-line" />
        </div>
        <div class="item-info">
          <div class="item-title">AI 连接（MCP 访问令牌）</div>
          <div class="item-desc">签发用于 Claude Desktop 或外部 Agent 的个人访问令牌</div>
        </div>
        <NButton secondary size="small" class="action-btn" @click="router.push('/mine/mcp')">
          管理令牌
        </NButton>
      </div>
    </NCard>

    <!-- 退出登录 -->
    <NCard :bordered="false" class="settings-card logout-card">
      <div class="logout-row">
        <div>
          <div class="logout-title">退出当前账号</div>
          <div class="logout-desc">退出后需要重新通过手机短信或密码登录系统</div>
        </div>
        <NButton quaternary type="error" size="small" @click="handleLogout">
          <template #icon><i class="ri-logout-box-r-line" /></template>
          退出登录
        </NButton>
      </div>
    </NCard>

    <!-- 弹窗：设置/修改密码 -->
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

    <!-- 弹窗：修改昵称 -->
    <NModal
      v-model:show="profileVisible"
      preset="card"
      title="修改昵称"
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
  max-width: 680px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 24px;
}
.page-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.page-header .subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
}
.mb-4 { margin-bottom: 16px; }

.settings-card {
  border-radius: var(--solo-radius-lg);
  padding: 4px;
}
.card-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  margin-bottom: 16px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* User Info Row */
.user-info-row {
  display: flex;
  align-items: center;
  gap: 16px;
}
.user-avatar {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  background: var(--solo-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 700;
  flex-shrink: 0;
}
.user-detail {
  flex: 1;
  min-width: 0;
}
.user-name-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.user-name {
  font-size: 17px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.user-phone {
  font-size: 13px;
  color: var(--solo-text-muted);
  margin-top: 4px;
}
.profile-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--solo-text-muted);
  background: var(--solo-border-light, #f8fafc);
  border-radius: var(--solo-radius, 8px);
  padding: 8px 12px;
  line-height: 1.4;
}

/* Setting Item Row */
.setting-item-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
}
.item-icon-box {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--solo-bg-blue-soft, #eff6ff);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.item-icon-box.mcp-icon {
  background: #f5f3ff;
  color: #7c3aed;
}
.item-info {
  flex: 1;
  min-width: 0;
}
.item-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}
.item-desc {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}
.item-divider {
  height: 1px;
  background: var(--solo-border-light, #f1f5f9);
  margin: 14px 0;
}
.action-btn {
  flex-shrink: 0;
}

/* Logout Row */
.logout-card {
  border: 1px dashed var(--solo-border, #e2e8f0);
}
.logout-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.logout-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.logout-desc {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  margin-top: 2px;
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
.input-icon {
  font-size: 16px;
  color: var(--solo-text-muted);
}
</style>
