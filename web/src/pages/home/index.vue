<script setup lang="ts">
import { NButton, NModal, NCheckbox, NSpace, NCard } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useWidgetConfig } from '@/composables/useWidgetConfig'
import StatsWidget from '@/components/widgets/StatsWidget.vue'
import QuickActionsWidget from '@/components/widgets/QuickActionsWidget.vue'
import TodoPreviewWidget from '@/components/widgets/TodoPreviewWidget.vue'
import RecentEntriesWidget from '@/components/widgets/RecentEntriesWidget.vue'
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getPeriodCloseChecks } from '@/api/accounting'
import { firstActionableReadinessItem, getClosingReadiness } from '@/utils/closingReadiness'

const router = useRouter()
const authStore = useAuthStore()
const { widgets, visibleWidgets, toggle } = useWidgetConfig()

const showCustomize = ref(false)
const closingChecks = ref<any | null>(null)
const closingLoading = ref(true)
const closingTargetMonth = computed(() => {
  const date = new Date()
  date.setMonth(date.getMonth() - 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
})
const closingReadiness = computed(() => getClosingReadiness(closingChecks.value))
const closingNextTask = computed(() => firstActionableReadinessItem(closingReadiness.value))
const closingReady = computed(() => Boolean(closingChecks.value?.canClose))

onMounted(async () => {
  try {
    closingChecks.value = await getPeriodCloseChecks(closingTargetMonth.value)
  } finally {
    closingLoading.value = false
  }
})

const isVisible = (id: string) => {
  return visibleWidgets.value.some((w) => w.id === id)
}

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
</script>

<template>
  <div class="home">
    <div class="welcome-banner">
      <div class="welcome-text-group">
        <span class="date-label">{{ formattedDate }}</span>
        <h2 class="welcome-title">
          {{ greetingText }}，{{ authStore.user?.nickname || '主理人' }}
        </h2>
        <p class="welcome-subtitle">
          这是你的经营工作台，先看状态，再处理今天最重要的事项。
        </p>
      </div>
      <div class="welcome-actions">
        <NButton
          size="medium"
          type="primary"
          secondary
          class="customize-btn"
          @click="showCustomize = true"
        >
          <i class="ri-settings-3-line" />
          <span>自定义面板</span>
        </NButton>
      </div>
    </div>

    <div class="closing-wizard-alert mb-4" :class="{ 'is-ready': closingReady }">
      <div class="alert-content">
        <div class="alert-icon-wrapper">
          <i :class="closingReady ? 'ri-checkbox-circle-line' : 'ri-calendar-check-line'"></i>
        </div>
        <div class="alert-text">
          <span class="alert-title">{{ closingTargetMonth }} 月结账</span>
          <span v-if="closingLoading" class="alert-desc">正在检查本期结账准备情况。</span>
          <span v-else-if="closingNextTask" class="alert-desc">{{ closingNextTask.title }}：{{ closingNextTask.detail }}</span>
          <span v-else-if="closingReady" class="alert-desc">本期检查已通过，可以确认结账。</span>
          <span v-else class="alert-desc">进入结账准备查看本期状态。</span>
        </div>
      </div>
      <NButton type="primary" size="small" class="wizard-go-btn" @click="router.push('/closing-wizard')">
        {{ closingNextTask ? closingNextTask.actionLabel : '查看结账准备' }}
        <template #icon><i class="ri-arrow-right-line" /></template>
      </NButton>
    </div>

    <!-- Stats Widget Row -->
    <StatsWidget v-if="isVisible('stats')" />

    <!-- Quick Actions Widget Row -->
    <QuickActionsWidget v-if="isVisible('quick-actions')" />

    <!-- Dual Column Grid Layout -->
    <div class="dashboard-grid">
      <!-- Main/Left Column (Core accounting info) -->
      <div class="grid-main-column">
        <RecentEntriesWidget v-if="isVisible('recent-entries')" />
      </div>

      <!-- Sidebar/Right Column (Actions & tasks) -->
      <div class="grid-side-column">
        <TodoPreviewWidget v-if="isVisible('todos')" />
      </div>
    </div>

    <!-- Customize modal -->
    <NModal
      v-model:show="showCustomize"
      title="自定义仪表盘"
      preset="card"
      style="width: 380px; border-radius: 16px;"
      :segmented="{ content: 'soft' }"
    >
      <NCard :bordered="false" size="small" class="modal-card">
        <p class="modal-desc">勾选需要显示在首页的小部件：</p>
        <NSpace vertical :size="12">
          <div v-for="w in widgets" :key="w.id" class="widget-toggle-item">
            <NCheckbox
              :checked="w.visible"
              @update:checked="toggle(w.id)"
              size="large"
            >
              <span class="checkbox-label">{{ w.title }}</span>
            </NCheckbox>
          </div>
        </NSpace>
      </NCard>
    </NModal>
  </div>
</template>

<style scoped>
.home {
  max-width: 1440px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.welcome-banner {
  padding: 8px 0 4px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
}
.date-label {
  display: block;
  margin-bottom: 7px;
  color: var(--solo-text-muted);
  font-size: 12px;
  font-weight: 550;
}
.welcome-title {
  margin: 0;
  font-size: 28px;
  font-weight: 680;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.welcome-subtitle {
  margin: 8px 0 0;
  color: var(--solo-text-secondary);
  font-size: 14px;
  font-weight: 400;
}
.customize-btn {
  border-radius: 20px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  transition: all 0.2s;
}
.customize-btn:hover {
  transform: translateY(-1px);
}
.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);
  gap: 24px;
  align-items: start;
}
.grid-main-column,
.grid-side-column {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.modal-desc {
  font-size: 13px;
  color: var(--solo-text-secondary);
  margin: 0 0 16px;
}
.widget-toggle-item {
  padding: 10px 14px;
  background: var(--solo-border-light);
  border-radius: 10px;
  border: 1px solid var(--solo-border);
  transition: all 0.2s;
}
.widget-toggle-item:hover {
  border-color: var(--solo-primary-light);
  background: #fff;
}
.closing-wizard-alert {
  min-height: 66px;
  padding: 14px 16px;
  border: 1px solid rgba(22, 93, 255, 0.15);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-blue-soft);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.closing-wizard-alert.is-ready {
  border-color: rgba(18, 128, 92, 0.22);
  background: var(--solo-income-soft);
}
.closing-wizard-alert.is-ready .alert-icon-wrapper {
  color: var(--solo-income);
}
.alert-content {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.alert-icon-wrapper {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: #fff;
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
.alert-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.alert-title {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 650;
}
.alert-desc {
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.wizard-go-btn {
  flex: none;
  border-radius: 8px;
}
.checkbox-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
@media (max-width: 992px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
    gap: 32px;
  }
  .welcome-banner {
    padding: 24px;
    flex-direction: column;
    align-items: flex-start;
  }
  .customize-btn {
    margin-top: 8px;
  }
}
</style>
