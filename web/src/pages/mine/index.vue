<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NButton,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NTag,
  NRadioGroup,
  NRadioButton,
  NProgress,
  NSpin,
  useMessage,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { useUsageStore } from '@/stores/usage'
import { aiSceneLabel } from '@/utils/constants'
import { setPassword, updateProfile } from '@/api/auth'

// ponytail: simplified settings page - removed redundant MCP & audit-share stub cards (already in nav),
// fortified password reset with confirmation check, added workspace view & table density preferences.

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const workspaceViewStore = useWorkspaceViewStore()
const usageStore = useUsageStore()

const user = ref(authStore.user)

// ── AI 用量（本人口径，跨账套聚合；数据来自 llm_usage 埋点） ──
const usageLoading = computed(() => usageStore.loading)
const currentMonth = computed(
  () =>
    usageStore.summary?.currentMonth ?? {
      calls: 0,
      promptTokens: 0,
      completionTokens: 0,
      totalTokens: 0,
    },
)
const previousMonth = computed(
  () =>
    usageStore.summary?.previousMonth ?? {
      calls: 0,
      promptTokens: 0,
      completionTokens: 0,
      totalTokens: 0,
    },
)
const usageScenes = computed(() => usageStore.summary?.byScene ?? [])
const usageDaily = computed(() => usageStore.summary?.daily ?? [])
const maxSceneTokens = computed(() =>
  Math.max(1, ...usageScenes.value.map((row) => row.totalTokens)),
)
// 配额块仅在套餐已设限时渲染（额度数值待定期间 limitTokens 恒为 null，整块隐藏）
const quotaInfo = computed(() => {
  const quota = usageStore.summary?.quota
  return quota && quota.limitTokens !== null ? quota : null
})
const quotaPercent = computed(() => {
  const quota = quotaInfo.value
  if (!quota || !quota.limitTokens) return 0
  const effective = quota.limitTokens + (quota.bonusTokens || 0)
  return Math.min(100, Math.round((quota.usedTokens / effective) * 100))
})

function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 10000) return `${(n / 10000).toFixed(2)} 万`
  return n.toLocaleString()
}

const usageChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 48, right: 12, top: 16, bottom: 24 },
  xAxis: {
    type: 'category',
    data: usageDaily.value.map((d) => d.date.slice(5)),
    axisTick: { show: false },
    axisLabel: { interval: 9, fontSize: 10 },
  },
  yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
  series: [
    {
      name: '消耗 tokens',
      type: 'line',
      smooth: true,
      data: usageDaily.value.map((d) => d.totalTokens),
      itemStyle: { color: '#1747b0' },
      areaStyle: { color: 'rgba(23, 71, 176, 0.08)' },
    },
  ],
}))

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
const pwdConfirm = ref('')
const pwdSaving = ref(false)
const hasPassword = computed(() => !!authStore.user?.has_password)

function openPwdModal() {
  pwdOld.value = ''
  pwdNew.value = ''
  pwdConfirm.value = ''
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
  if (pwdNew.value !== pwdConfirm.value) {
    message.warning('两次输入的新密码不一致')
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

// ── 工作台偏好 ──
const currentWorkspaceMode = computed({
  get: () => workspaceViewStore.mode,
  set: (val) => {
    workspaceViewStore.setMode(val)
    message.success(val === 'boss' ? '已切换至老板模式' : '已切换至会计模式')
  },
})

// ── 表格密度偏好 ──
const DENSITY_STORAGE_KEY = 'solo:table-density'
const tableDensity = ref(localStorage.getItem(DENSITY_STORAGE_KEY) || 'default')

function handleDensityChange(val: string) {
  tableDensity.value = val
  try {
    localStorage.setItem(DENSITY_STORAGE_KEY, val)
    message.success(val === 'compact' ? '已设为紧凑密度' : '已设为标准密度')
  } catch {
    // ignore
  }
}

function maskPhone(phone?: string) {
  if (!phone) return ''
  return phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')
}

onMounted(async () => {
  if (!authStore.user && authStore.token) {
    try {
      await authStore.fetchProfile()
      user.value = authStore.user
    } catch {
      // ignore
    }
  } else {
    user.value = authStore.user
  }
  // 个人页强制重取，导航栏摘要的缓存可能已经旧了
  usageStore.ensureLoaded(true)
})
</script>

<template>
  <div class="mine-page">
    <div class="page-header">
      <div class="title-section">
        <h2>个人设置</h2>
        <p class="subtitle">管理您的个人资料、账号安全与工作台交互偏好</p>
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
            <NTag size="tiny" type="info" :bordered="false">
              {{ authStore.companies.length ? `${authStore.companies.length} 个主体账套` : '未绑定主体' }}
            </NTag>
          </div>
          <div class="user-meta-line">
            <span class="user-phone">登录手机：{{ user?.phone ? maskPhone(user.phone) : '未绑定' }}</span>
            <span v-if="user?.user_id" class="user-id">UID: {{ user.user_id.slice(0, 10) }}...</span>
          </div>
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

    <!-- AI 用量 -->
    <NCard id="ai-usage" :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">AI 用量</div>
      <NSpin :show="usageLoading" size="small">
        <div class="usage-summary">
          <div class="usage-stat">
            <div class="usage-stat-label">本月消耗</div>
            <div class="usage-stat-value">
              {{ formatTokens(currentMonth.totalTokens) }}
              <span class="usage-stat-unit">tokens</span>
            </div>
            <div class="usage-stat-sub">
              {{ currentMonth.calls }} 次调用 · 上月 {{ formatTokens(previousMonth.totalTokens) }}
            </div>
          </div>
          <div class="usage-chart">
            <VChart
              v-if="usageDaily.length"
              :option="usageChartOption"
              autoresize
              class="usage-chart-canvas"
            />
          </div>
        </div>

        <div v-if="quotaInfo" class="usage-quota">
          <div class="quota-line">
            <span class="quota-plan">{{ quotaInfo.planName }}</span>
            <span class="quota-period">{{ quotaInfo.periodStart }} ~ {{ quotaInfo.periodEnd }}</span>
            <span class="quota-usage">
              已用 {{ formatTokens(quotaInfo.usedTokens) }} / {{ formatTokens(quotaInfo.limitTokens ?? 0) }}
            </span>
          </div>
          <NProgress
            type="line"
            :percentage="quotaPercent"
            :show-indicator="false"
            :height="8"
            border-radius="4px"
            :status="quotaPercent >= 100 ? 'error' : 'default'"
          />
          <div class="quota-remaining">
            {{ quotaPercent >= 100 ? '本期额度已用完，升级套餐或等待下期恢复' : `本期剩余 ${formatTokens(quotaInfo.remainingTokens ?? 0)} tokens` }}
          </div>
        </div>

        <div v-if="usageScenes.length" class="usage-scenes">
          <div v-for="row in usageScenes" :key="row.scene" class="usage-scene-row">
            <span class="scene-name">{{ aiSceneLabel(row.scene) }}</span>
            <NProgress
              class="scene-bar"
              type="line"
              :percentage="Math.round((row.totalTokens / maxSceneTokens) * 100)"
              :show-indicator="false"
              :height="6"
              border-radius="3px"
            />
            <span class="scene-tokens">{{ formatTokens(row.totalTokens) }}</span>
          </div>
        </div>
        <div v-else class="usage-empty">本月还没有 AI 使用记录，去「AI 会计」试试连续记账吧。</div>

        <div class="profile-tip">
          <i class="ri-information-line" />
          统计口径：自然月内本人全部 AI 功能（AI 会计对话、智能记账、单据识别、报表洞察等）的
          token 消耗，跨账套合并计算；语音与图片识别按调用次数计。
        </div>
      </NSpin>
    </NCard>

    <!-- 账号与安全 -->
    <NCard :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">账号与安全</div>

      <!-- 登录密码 -->
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
          <div class="item-desc">设置后支持使用手机号与密码直接登录系统</div>
        </div>
        <NButton secondary size="small" class="action-btn" @click="openPwdModal">
          {{ hasPassword ? '修改密码' : '设置密码' }}
        </NButton>
      </div>

      <div class="item-divider" />

      <!-- 登录手机号 -->
      <div class="setting-item-row">
        <div class="item-icon-box">
          <i class="ri-smartphone-line" />
        </div>
        <div class="item-info">
          <div class="item-title">
            <span>登录手机号</span>
            <NTag size="tiny" :type="user?.phone_bound ? 'success' : 'default'" :bordered="false">
              {{ user?.phone_bound ? '已绑定' : '未绑定' }}
            </NTag>
          </div>
          <div class="item-desc">
            {{ user?.phone ? `${maskPhone(user.phone)}（用于接收安全验证码及密码找回）` : '未绑定手机号' }}
          </div>
        </div>
      </div>

      <div class="item-divider" />

      <!-- 微信账号 -->
      <div class="setting-item-row">
        <div class="item-icon-box wechat-icon">
          <i class="ri-wechat-fill" />
        </div>
        <div class="item-info">
          <div class="item-title">
            <span>微信互联</span>
            <NTag size="tiny" :type="user?.wechat_bound ? 'success' : 'default'" :bordered="false">
              {{ user?.wechat_bound ? '已绑定' : '未绑定' }}
            </NTag>
          </div>
          <div class="item-desc">
            {{
              user?.wechat_bound
                ? '已关联微信账号，支持与移动端/小程序免密快捷同步'
                : '绑定微信后可在微信小程序同步登录并接收重要待办通知'
            }}
          </div>
        </div>
      </div>
    </NCard>

    <!-- 工作台与偏好设置 -->
    <NCard :bordered="false" class="settings-card mb-4">
      <div class="card-section-title">工作台偏好</div>

      <!-- 默认工作台模式 -->
      <div class="setting-item-row">
        <div class="item-icon-box">
          <i class="ri-dashboard-line" />
        </div>
        <div class="item-info">
          <div class="item-title">默认工作台模式</div>
          <div class="item-desc">
            设定当前主体账套的导航与工作台模式，老板模式聚焦经营待办，会计模式完整展现科目分录与建账
          </div>
        </div>
        <NRadioGroup v-model:value="currentWorkspaceMode" size="small">
          <NRadioButton value="boss">老板模式</NRadioButton>
          <NRadioButton value="accounting">会计模式</NRadioButton>
        </NRadioGroup>
      </div>

      <div class="item-divider" />

      <!-- 表格行高密度 -->
      <div class="setting-item-row">
        <div class="item-icon-box">
          <i class="ri-table-line" />
        </div>
        <div class="item-info">
          <div class="item-title">数据表格显示密度</div>
          <div class="item-desc">
            调整凭证列表、明细账簿与报表等表格的紧凑程度，紧凑模式一屏展现更多行
          </div>
        </div>
        <NRadioGroup :value="tableDensity" size="small" @update:value="handleDensityChange">
          <NRadioButton value="default">标准</NRadioButton>
          <NRadioButton value="compact">紧凑</NRadioButton>
        </NRadioGroup>
      </div>
    </NCard>

    <!-- 底部退出登录操作 -->
    <div class="logout-section">
      <NButton quaternary type="error" size="medium" class="logout-btn" @click="handleLogout">
        <template #icon><i class="ri-logout-box-r-line" /></template>
        退出当前账号
      </NButton>
    </div>

    <!-- 弹窗：设置/修改密码 -->
    <NModal
      v-model:show="pwdVisible"
      preset="card"
      :title="hasPassword ? '修改密码' : '设置密码'"
      style="max-width: 420px; border-radius: var(--solo-radius-lg);"
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
            placeholder="至少 8 位密码"
          >
            <template #prefix>
              <i class="ri-lock-password-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
        <NFormItem label="确认新密码">
          <NInput
            v-model:value="pwdConfirm"
            type="password"
            show-password-on="click"
            placeholder="请再次输入新密码"
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
  max-width: 760px;
  margin: 0 auto;
  padding: 8px 16px 48px;
}
.page-header {
  margin-bottom: 24px;
}
.title-section h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.title-section .subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
}
.mb-4 {
  margin-bottom: 16px;
}

.settings-card {
  border-radius: var(--solo-radius-lg);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  padding: 6px;
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
  width: 52px;
  height: 52px;
  border-radius: var(--solo-radius-pill);
  background: var(--solo-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
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
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.user-meta-line {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 4px;
}
.user-phone {
  font-size: 12.5px;
  color: var(--solo-text-muted);
}
.user-id {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: var(--solo-font-mono, monospace);
}
.profile-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--solo-text-muted);
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius);
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
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.item-icon-box.wechat-icon {
  background: var(--solo-income-bg);
  color: var(--solo-income);
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
  line-height: 1.4;
}
.item-divider {
  height: 1px;
  background: var(--solo-border-light);
  margin: 14px 0;
}
.action-btn {
  flex-shrink: 0;
}

/* AI 用量 */
.usage-summary {
  display: flex;
  align-items: stretch;
  gap: 20px;
}
.usage-stat {
  flex-shrink: 0;
  min-width: 150px;
}
.usage-stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.usage-stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1.3;
}
.usage-stat-unit {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-muted);
  margin-left: 4px;
}
.usage-stat-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}
.usage-chart {
  flex: 1;
  min-width: 0;
}
.usage-chart-canvas {
  width: 100%;
  height: 110px;
}
.usage-scenes {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.usage-scene-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.scene-name {
  width: 96px;
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
.scene-bar {
  flex: 1;
  min-width: 0;
}
.scene-tokens {
  width: 88px;
  flex-shrink: 0;
  text-align: right;
  font-size: 12px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}
.usage-empty {
  margin-top: 12px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius);
  padding: 10px 12px;
}

/* 配额块：套餐设限后才出现（额度数值待定期间整块隐藏） */
.usage-quota {
  margin-top: 14px;
  padding: 12px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius);
}
.quota-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.quota-plan {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.quota-period {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}
.quota-usage {
  margin-left: auto;
  font-size: 12px;
  color: var(--solo-text-secondary);
  font-variant-numeric: tabular-nums;
}
.quota-remaining {
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
@media (max-width: 640px) {
  .usage-summary {
    flex-direction: column;
    gap: 8px;
  }
  .usage-chart {
    width: 100%;
  }
}

/* Logout Section */
.logout-section {
  display: flex;
  justify-content: center;
  margin-top: 24px;
}
.logout-btn {
  color: var(--solo-expense);
}

.profile-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius);
  padding: 8px 10px;
  line-height: 1.5;
}
.input-icon {
  font-size: 16px;
  color: var(--solo-text-muted);
}
</style>
