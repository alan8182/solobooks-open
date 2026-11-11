<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getActivePinia } from 'pinia'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

const route = useRoute()
const router = useRouter()

const isBoss = computed(() => {
  if (!getActivePinia()) return true
  try {
    return useWorkspaceViewStore().isBoss
  } catch {
    return true
  }
})

const tabs = [
  {
    path: '/bank',
    label: '公司公户 · 查流水',
    desc: '银行收支明细与对账',
    icon: 'ri-bank-line',
    group: 'funds',
  },
  {
    path: '/private-expenses',
    label: '老板私账 · 记垫付',
    desc: '个人垫付与私户代收',
    icon: 'ri-wallet-3-line',
    group: 'funds',
  },
  {
    path: '/reports/advance-balances',
    label: '垫付未报销汇总',
    desc: '老板与员工账内待报余额',
    icon: 'ri-hand-coin-line',
    group: 'advance',
  },
  {
    path: '/reports/ar-aging',
    label: '客户欠款 (应收)',
    desc: '待收回的客户款项与账龄',
    icon: 'ri-arrow-left-down-line',
    group: 'aging',
  },
  {
    path: '/reports/ap-aging',
    label: '待付账款 (应付)',
    desc: '待支付给供应商的款项',
    icon: 'ri-arrow-right-up-line',
    group: 'aging',
  },
] as const

const currentPath = computed(() => route?.path || '')

function isActive(path: string) {
  return currentPath.value.startsWith(path)
}

function go(path: string) {
  if (isActive(path)) return
  const month = route?.query?.month
  router?.push({
    path,
    query: month ? { month: String(month) } : {},
  })
}
</script>

<template>
  <nav
    v-if="isBoss"
    class="boss-track-nav no-print"
    aria-label="垫付与应收应付切换"
  >
    <div class="track-header">
      <span class="track-kicker">看账管钱 · 资金与往来</span>
      <span class="track-hint">随时核对公户流水、老板垫资、未报销余额与客户欠款</span>
    </div>
    <div class="track-tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.path"
        type="button"
        role="tab"
        class="track-tab"
        :class="{ active: isActive(tab.path) }"
        :aria-selected="isActive(tab.path)"
        @click="go(tab.path)"
      >
        <span class="track-tab-icon">
          <i :class="tab.icon" aria-hidden="true" />
        </span>
        <span class="track-tab-copy">
          <strong>{{ tab.label }}</strong>
          <span>{{ tab.desc }}</span>
        </span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
.boss-track-nav {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.track-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.track-kicker {
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 700;
}

.track-hint {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.track-tabs {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.track-tab {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease-out, background-color 160ms ease-out;
}

.track-tab:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.track-tab.active {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
  box-shadow: var(--solo-shadow-sm);
}

.track-tab-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.track-tab.active .track-tab-icon {
  background: var(--solo-primary);
  color: var(--solo-bg-card);
}

.track-tab-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.track-tab-copy strong {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.track-tab.active .track-tab-copy strong {
  color: var(--solo-primary);
}

.track-tab-copy span {
  font-size: 11.5px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 1160px) {
  .track-tabs {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .track-tabs {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .track-tabs {
    grid-template-columns: 1fr;
  }
}
</style>
