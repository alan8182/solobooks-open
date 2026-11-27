<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { routeAllowsWorkspace, type WorkspaceView } from '@/utils/workspace'

const route = useRoute()
const router = useRouter()
const viewStore = useWorkspaceViewStore()
const dismissed = ref(false)

const requiredViews = computed<WorkspaceView[] | null>(() => {
  const workspace = route.meta?.workspace
  return Array.isArray(workspace) ? workspace as WorkspaceView[] : null
})

const visible = computed(() => {
  if (dismissed.value) return false
  if (!requiredViews.value || requiredViews.value.length === 0) return false
  return !routeAllowsWorkspace(requiredViews.value, viewStore.mode)
})

// 换页后重新提示：每条专业页各提示一次，关掉不跨页记忆
watch(() => route.fullPath, () => {
  dismissed.value = false
})

const targetView = computed<WorkspaceView>(() => (
  requiredViews.value?.includes('accounting') ? 'accounting' : 'boss'
))

const noticeText = computed(() => (
  targetView.value === 'accounting'
    ? '这是会计专业视图：科目、分录与准则口径。老板视图也能进入，需要完整操作可切换视图。'
    : '这是老板视图页面，会计视图也能进入。'
))

function switchView() {
  viewStore.setMode(targetView.value)
}
</script>

<template>
  <div v-if="visible" class="workspace-view-notice" role="status">
    <div class="notice-copy">
      <i class="ri-information-line" aria-hidden="true" />
      <span>{{ noticeText }}</span>
    </div>
    <div class="notice-actions">
      <button type="button" class="notice-button primary" @click="switchView">
        切换到{{ targetView === 'accounting' ? '会计' : '老板' }}视图
      </button>
      <button type="button" class="notice-button" @click="router.push('/')">
        返回工作台
      </button>
      <button type="button" class="notice-close" aria-label="关闭提示" @click="dismissed = true">
        <i class="ri-close-line" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.workspace-view-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  max-width: var(--page-width);
  margin: 0 auto 16px;
  padding: 10px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-blue-soft);
}

.notice-copy {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
}

.notice-copy i {
  color: var(--solo-primary);
  font-size: 16px;
  flex-shrink: 0;
}

.notice-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.notice-button {
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-text-secondary);
  padding: 6px 8px;
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.notice-button:hover,
.notice-button:focus-visible {
  color: var(--solo-primary);
  background: var(--solo-bg-card);
}

.notice-button.primary {
  color: var(--solo-primary);
}

.notice-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--solo-text-muted);
  cursor: pointer;
}

.notice-close:hover,
.notice-close:focus-visible {
  color: var(--solo-primary);
  background: var(--solo-bg-card);
}

@media (max-width: 720px) {
  .workspace-view-notice {
    align-items: flex-start;
    flex-direction: column;
  }

  .notice-actions {
    align-self: flex-end;
  }
}
</style>
