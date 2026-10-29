<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { useTaskInbox } from '@/composables/useTaskInbox'
import { useSetupReadiness } from '@/composables/useSetupReadiness'
import TaskInbox from '@/components/business/TaskInbox.vue'
import SetupChecklist from '@/components/business/SetupChecklist.vue'
import StatsWidget from '@/components/widgets/StatsWidget.vue'
import RecentEntriesWidget from '@/components/widgets/RecentEntriesWidget.vue'

const router = useRouter()
const authStore = useAuthStore()
const viewStore = useWorkspaceViewStore()
const entryStore = useEntryStore()
// 两个看板各自持有数据，切回首页时要一起重拉，否则问候语是新的、待办还是两小时前的
const { tasks, loading, partialFailure, reload: reloadTaskInbox } = useTaskInbox()
const { readiness, loading: readinessLoading, reload: reloadReadiness } = useSetupReadiness()
const quickPrompt = ref('')

function handleQuickSubmit() {
  const text = quickPrompt.value.trim()
  if (!text) return
  router.push({ path: '/copilot', query: { q: text, autoSend: '1' } })
}

const proLinks = [
  { path: '/vouchers', label: '记账凭证', icon: 'ri-file-list-3-line' },
  { path: '/reports/general-ledger', label: '总账', icon: 'ri-book-3-line' },
  { path: '/closing-wizard', label: '月末结账', icon: 'ri-calendar-check-line' },
  { path: '/tax-filing', label: '税务申报', icon: 'ri-file-shield-2-line' },
]

const bossActions = [
  {
    path: '/invoices?import=true',
    label: '上传发票',
    description: 'Excel 批量导入税局发票',
    icon: 'ri-receipt-line',
  },
  {
    path: '/bank',
    label: '上传流水',
    description: '银行流水、微信支付账单',
    icon: 'ri-bank-line',
  },
  {
    path: '/private-expenses',
    label: '小私账',
    description: '老板个人垫付与代收对账',
    icon: 'ri-wallet-3-line',
  },
  {
    path: '/ledger',
    label: '经营账本',
    description: '核对日常收支与历史账目',
    icon: 'ri-book-open-line',
  },
]

const isDemoCurrent = computed(() => {
  const current = authStore.companies.find((c) => c.company_id === authStore.currentCompanyId)
  return current?.isDemo === true
})

const formattedDate = computed(() => {
  return new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })
})

const greetingText = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了，注意休息'
  if (hour < 11) return '早上好'
  if (hour < 13) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

function openCopilot() {
  router.push('/copilot')
}

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([reloadTaskInbox(), reloadReadiness(), entryStore.fetchEntries()]))
</script>

<template>
  <div class="home-workbench">
    <header class="workbench-header">
      <div class="header-main">
        <span class="workbench-kicker">{{ viewStore.isBoss ? '老板工作台' : '会计工作台' }}</span>
        <span class="date-label">{{ formattedDate }}</span>
        <h1 class="greeting-title">
          {{ greetingText }}，{{ authStore.user?.nickname || '主理人' }}
        </h1>
        <p class="greeting-subtitle">
          {{ viewStore.isBoss
            ? '上传发票或流水，或者直接说一句，就能开始记账。'
            : '专业工作台。凭证、账簿、结账与申报入口都在这里。' }}
        </p>
      </div>

      <NButton
        v-if="!viewStore.isBoss"
        type="primary"
        size="large"
        class="primary-action"
        @click="openCopilot"
      >
        <template #icon><i class="ri-sparkling-fill" /></template>
        录入凭证 / 问 AI 会计
      </NButton>
    </header>

    <div v-if="viewStore.isBoss" class="boss-quick-bar">
      <div class="quick-input-container">
        <i class="ri-sparkling-fill quick-icon" />
        <input
          v-model="quickPrompt"
          type="text"
          class="quick-input-field"
          placeholder="交代一句话记账，例如：“昨天请客户吃饭 380 微信付” 或 “收到技术咨询费 12000”..."
          @keydown.enter="handleQuickSubmit"
        />
        <button
          type="button"
          class="quick-send-btn"
          :disabled="!quickPrompt.trim()"
          @click="handleQuickSubmit"
        >
          <span>记一笔</span>
          <i class="ri-arrow-right-line" />
        </button>
      </div>
    </div>

    <div v-if="viewStore.isBoss" class="boss-action-grid" aria-label="老板记账入口">
      <button
        v-for="action in bossActions"
        :key="action.path"
        type="button"
        class="boss-action-card"
        @click="router.push(action.path)"
      >
        <span class="boss-action-icon">
          <i :class="action.icon" aria-hidden="true" />
        </span>
        <span class="boss-action-copy">
          <strong>{{ action.label }}</strong>
          <span>{{ action.description }}</span>
        </span>
        <i class="ri-arrow-right-line boss-action-arrow" aria-hidden="true" />
      </button>
    </div>

    <div v-if="viewStore.isAccounting" class="pro-entry-row" aria-label="专业入口">
      <button
        v-for="link in proLinks"
        :key="link.path"
        type="button"
        class="pro-entry"
        @click="router.push(link.path)"
      >
        <i :class="link.icon" aria-hidden="true" />
        <span>{{ link.label }}</span>
        <i class="ri-arrow-right-line pro-entry-arrow" aria-hidden="true" />
      </button>
    </div>

    <StatsWidget />

    <!-- 演示账套的样本数据会让清单全绿，但那不是用户自己的进度 -->
    <div v-if="isDemoCurrent" class="demo-onboarding-tip">
      <i class="ri-information-line" aria-hidden="true" />
      <span class="demo-onboarding-copy">
        当前是演示账套，样本数据已满足清单，但不计入你的上手进度。创建自己的账套后，引导会从第一步开始。
      </span>
      <NButton size="small" secondary @click="router.push('/mine/companies')">
        创建我的账套
      </NButton>
    </div>

    <!-- 上手清单：只在「没配完 + 新手期」出现，就绪后自己折叠成一行 -->
    <SetupChecklist :readiness="readiness" :loading="readinessLoading" />

    <div class="dashboard-grid">
      <TaskInbox
        :tasks="tasks"
        :loading="loading"
        :partial-failure="partialFailure"
      />
      <RecentEntriesWidget />
    </div>
  </div>
</template>

<style scoped>
.home-workbench {
  max-width: 1440px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.demo-onboarding-tip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}

.demo-onboarding-tip > i {
  flex-shrink: 0;
  color: var(--solo-primary);
  font-size: 16px;
}

.demo-onboarding-copy {
  flex: 1;
  min-width: 0;
}

@media (max-width: 640px) {
  .demo-onboarding-tip {
    align-items: flex-start;
    flex-wrap: wrap;
  }
}

.workbench-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  padding: 4px 0 2px;
}

.workbench-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--solo-primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.2px;
}

.date-label {
  display: block;
  margin-bottom: 6px;
  color: var(--solo-text-muted);
  font-size: 12px;
  font-weight: 550;
}

.greeting-title {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.4px;
}

.greeting-subtitle {
  margin: 6px 0 0;
  color: var(--solo-text-secondary);
  font-size: 13px;
}

.primary-action {
  flex-shrink: 0;
  border-radius: 8px;
  font-weight: 650;
}

.boss-quick-bar {
  width: 100%;
}

.quick-input-container {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--solo-bg-card, #ffffff);
  border: 1.5px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-xl, 12px);
  padding: 8px 14px 8px 18px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.03);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.quick-input-container:focus-within {
  border-color: var(--solo-primary, #1747b0);
  box-shadow: 0 0 0 3px rgba(23, 71, 176, 0.12);
}

.quick-icon {
  font-size: 20px;
  color: var(--solo-primary, #1747b0);
  flex-shrink: 0;
}

.quick-input-field {
  flex: 1;
  border: 0;
  outline: none;
  font-size: 14px;
  color: var(--solo-text-primary, #182230);
  background: transparent;
  padding: 6px 0;
}

.quick-input-field::placeholder {
  color: var(--solo-text-muted, #636e7c);
}

.quick-send-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--solo-primary, #1747b0);
  color: #ffffff;
  border: 0;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.16s ease;
}

.quick-send-btn:hover:not(:disabled) {
  background: var(--solo-primary-hover, #2b66e2);
}

.quick-send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.boss-action-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 960px) {
  .boss-action-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .boss-action-grid {
    grid-template-columns: 1fr;
  }
}

.boss-action-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease-out, background-color 160ms ease-out;
}

.boss-action-card:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.boss-action-card:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.boss-action-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 19px;
}

.boss-action-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.boss-action-copy strong {
  font-size: 14px;
  font-weight: 650;
}

.boss-action-copy span {
  overflow: hidden;
  color: var(--solo-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.boss-action-arrow {
  color: var(--solo-text-muted);
  font-size: 15px;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(380px, 0.9fr);
  gap: 18px;
  align-items: start;
}

.pro-entry-row {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 2px 0;
}

.pro-entry {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  padding: 8px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 160ms ease-out, color 160ms ease-out;
}

.pro-entry:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.pro-entry:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.pro-entry-arrow {
  color: var(--solo-text-muted);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .workbench-header {
    flex-direction: column;
    align-items: stretch;
  }

  .primary-action {
    align-self: flex-start;
  }

  .boss-action-grid {
    grid-template-columns: 1fr;
  }
}
</style>
