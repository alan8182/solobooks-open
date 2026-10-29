<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, watch, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NButton, NGrid, NGridItem, NTabs, NTab, NDropdown, type DropdownOption } from 'naive-ui'
import { formatMoney } from '@/utils/format'
import { usePageRefresh } from '@/composables/usePageRefresh'
import FixedAssetTab from './FixedAssetTab.vue'
import IntangibleAssetTab from './IntangibleAssetTab.vue'

const router = useRouter()
const route = useRoute()

type TabKey = 'fixed' | 'intangible'
const activeTab = ref<TabKey>(route.query.tab === 'intangible' ? 'intangible' : 'fixed')

watch(
  () => route.query.tab,
  (tab) => {
    const target: TabKey = tab === 'intangible' ? 'intangible' : 'fixed'
    if (activeTab.value !== target) {
      activeTab.value = target
    }
  },
)

function onTabChange(val: string) {
  const target = val as TabKey
  activeTab.value = target
  router.replace({ query: { ...route.query, tab: target } })
}

const fixedTabRef = ref<InstanceType<typeof FixedAssetTab> | null>(null)
const intangibleTabRef = ref<InstanceType<typeof IntangibleAssetTab> | null>(null)

const fixedStats = ref({
  originalValue: 0,
  accumulatedDepreciation: 0,
  netValue: 0,
  count: 0,
})

const intangibleStats = ref({
  originalValue: 0,
  accumulatedAmortization: 0,
  netValue: 0,
  count: 0,
  expiringCount: 0,
})

function onFixedStats(stats: typeof fixedStats.value) {
  fixedStats.value = stats
}

function onIntangibleStats(stats: typeof intangibleStats.value) {
  intangibleStats.value = stats
}

// 合并统计
const totalOriginalValue = computed(() => fixedStats.value.originalValue + intangibleStats.value.originalValue)
const totalAccumulated = computed(() => fixedStats.value.accumulatedDepreciation + intangibleStats.value.accumulatedAmortization)
const totalNetValue = computed(() => totalOriginalValue.value - totalAccumulated.value)
const totalCount = computed(() => fixedStats.value.count + intangibleStats.value.count)
const expiringCount = computed(() => intangibleStats.value.expiringCount)

const createOptions: DropdownOption[] = [
  {
    label: '录入固定资产',
    key: 'fixed',
    icon: () => h('i', { class: 'ri-computer-line' }),
  },
  {
    label: '录入无形资产',
    key: 'intangible',
    icon: () => h('i', { class: 'ri-copyright-line' }),
  },
]

function handleCreateSelect(key: string) {
  if (key === 'fixed') {
    router.push('/assets/create')
  } else if (key === 'intangible') {
    router.push('/intangibles/create')
  }
}

usePageRefresh(() => {
  fixedTabRef.value?.load()
  intangibleTabRef.value?.load()
})
</script>

<template>
  <div class="asset-page">
    <div class="page-header">
      <div class="title-section">
        <h2>长期资产</h2>
        <p class="subtitle">管理固定资产与无形资产，按月计提折旧/摊销并生成记账凭证</p>
      </div>
      <div class="header-actions">
        <!-- 固定资产专属操作 -->
        <template v-if="activeTab === 'fixed'">
          <NButton secondary @click="fixedTabRef?.handleReconciliation()">
            <template #icon><i class="ri-scales-3-line" /></template>
            资产核对
          </NButton>
          <NButton secondary @click="fixedTabRef?.handleExport()">
            <template #icon><i class="ri-download-2-line" /></template>
            导出台账
          </NButton>
          <NButton secondary type="primary" @click="fixedTabRef?.handlePreviewDepreciation()">
            <template #icon><i class="ri-calculator-line" /></template>
            一键计提折旧
          </NButton>
        </template>

        <!-- 无形资产专属操作 -->
        <template v-else-if="activeTab === 'intangible'">
          <NButton secondary @click="intangibleTabRef?.handleExport()">
            <template #icon><i class="ri-download-2-line" /></template>
            导出台账
          </NButton>
          <NButton secondary type="primary" @click="intangibleTabRef?.handlePreviewAmortization()">
            <template #icon><i class="ri-calculator-line" /></template>
            一键计提摊销
          </NButton>
        </template>

        <!-- 录入资产下拉菜单 -->
        <NDropdown :options="createOptions" trigger="click" @select="handleCreateSelect">
          <NButton type="primary">
            <template #icon><i class="ri-add-line" /></template>
            录入资产
            <i class="ri-arrow-down-s-line" style="margin-left: 4px;" />
          </NButton>
        </NDropdown>
      </div>
    </div>

    <!-- 全局统计卡 -->
    <NGrid :cols="4" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper val-icon"><i class="ri-money-cny-box-line" /></div>
          <div class="stat-content">
            <span class="stat-label">长期资产原值</span>
            <span class="stat-value">{{ formatMoney(totalOriginalValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper dep-icon"><i class="ri-line-chart-line" /></div>
          <div class="stat-content">
            <span class="stat-label">累计折旧 / 摊销</span>
            <span class="stat-value text-muted-value">{{ formatMoney(totalAccumulated) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper net-icon"><i class="ri-shield-check-line" /></div>
          <div class="stat-content">
            <span class="stat-label">账面净值</span>
            <span class="stat-value text-green">{{ formatMoney(totalNetValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper qty-icon"><i class="ri-database-line" /></div>
          <div class="stat-content">
            <span class="stat-label">
              在册总数
              <span v-if="expiringCount" class="expiring-badge">{{ expiringCount }} 项 90 天内到期</span>
            </span>
            <span class="stat-value">{{ totalCount }} <span class="unit">件</span></span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- 分段标签 -->
    <div class="tabs-bar">
      <NTabs :value="activeTab" type="segment" class="asset-tabs" @update:value="onTabChange">
        <NTab name="fixed">固定资产 ({{ fixedStats.count }})</NTab>
        <NTab name="intangible">无形资产 ({{ intangibleStats.count }})</NTab>
      </NTabs>
    </div>

    <!-- 子页面内容 -->
    <FixedAssetTab v-show="activeTab === 'fixed'" ref="fixedTabRef" @update:stats="onFixedStats" />
    <IntangibleAssetTab v-show="activeTab === 'intangible'" ref="intangibleTabRef" @update:stats="onIntangibleStats" />
  </div>
</template>

<style scoped>
.asset-page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}
.title-section {
  min-width: 0;
  flex: 1;
}
.title-section h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.subtitle {
  margin: 4px 0 0 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
}
.stats-grid {
  margin-bottom: 24px;
}
.stat-box {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.3s ease;
}
.stat-box:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow-lg);
  border-color: var(--solo-primary-light);
}
.stat-icon-wrapper {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.val-icon { background: rgba(99, 102, 241, 0.1); color: var(--solo-primary); }
.dep-icon { background: rgba(100, 116, 139, 0.1); color: var(--solo-text-secondary); }
.net-icon { background: rgba(16, 185, 129, 0.1); color: var(--solo-income); }
.qty-icon { background: rgba(245, 158, 11, 0.1); color: var(--solo-warning); }
.stat-content { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 6px;
}
.expiring-badge {
  font-size: 10px;
  color: var(--solo-warning);
  font-weight: 600;
}
.stat-value {
  font-size: 19px;
  font-weight: 850;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
  line-height: 1.1;
  white-space: nowrap;
}
.stat-value .unit { font-size: 12px; color: var(--solo-text-muted); font-weight: 500; }
.text-muted-value { color: var(--solo-text-secondary); }
.text-green { color: var(--solo-income); }
.tabs-bar {
  margin-bottom: 20px;
  display: flex;
  justify-content: flex-start;
}
.asset-tabs {
  max-width: 280px;
}
</style>
