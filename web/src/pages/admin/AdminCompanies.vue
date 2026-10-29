<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref } from 'vue'
import { NButton, NCard, NInput, type DataTableColumns } from 'naive-ui'
import { fetchAdminCompanies, type AdminCompanyItem } from '@/api/admin'
import { useAdminTable, formatAdminDateTime } from '@/composables/useAdminTable'
import { SoloTable, SoloDrawer, SoloStatusBadge, SoloButton } from '@/components/ui'

const {
  loading,
  items,
  query,
  onSearch,
} = useAdminTable<AdminCompanyItem>({
  loadData: async ({ query }) => {
    const res = await fetchAdminCompanies({ query })
    return res.items
  },
})

const activeCompany = ref<AdminCompanyItem | null>(null)
const drawerVisible = ref(false)

const STANDARD_LABELS: Record<string, string> = {
  small_enterprise_cn: '小企业会计准则',
}

function standardLabel(value: string): string {
  return STANDARD_LABELS[value] ?? value
}

function openCompanyDrawer(company: AdminCompanyItem) {
  activeCompany.value = company
  drawerVisible.value = true
}

const columns: DataTableColumns<AdminCompanyItem> = [
  {
    title: '账本 / 公司',
    key: 'name',
    minWidth: 220,
    render: (row) =>
      h('div', { class: 'company-cell', onClick: () => openCompanyDrawer(row) }, [
        h('span', { class: 'company-name' }, row.name || '未命名账本'),
        h('span', { class: 'company-id-sub' }, row.companyId),
      ]),
  },
  {
    title: '所有者',
    key: 'ownerUserId',
    minWidth: 170,
    render: (row) =>
      h('div', { class: 'owner-cell' }, [
        h('span', { class: 'owner-name' }, row.ownerName || row.ownerUserId),
        h('span', { class: 'owner-phone' }, row.ownerPhone || '—'),
      ]),
  },
  {
    title: '成员数',
    key: 'memberCount',
    width: 95,
    align: 'right',
    render: (row) => `${row.memberCount} 人`,
  },
  {
    title: '凭证总数',
    key: 'entryCount',
    width: 105,
    align: 'right',
    render: (row) => `${row.entryCount} 张`,
  },
  {
    title: '会计准则',
    key: 'accountingStandard',
    width: 150,
    render: (row) =>
      h(SoloStatusBadge, { tone: 'brand', plain: true, size: 'small' }, { default: () => standardLabel(row.accountingStandard) }),
  },
  { title: '最近活跃', key: 'lastActivityAt', width: 155, render: (row) => formatAdminDateTime(row.lastActivityAt) },
  { title: '创建时间', key: 'createdAt', width: 155, render: (row) => formatAdminDateTime(row.createdAt) },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => openCompanyDrawer(row) },
        { default: () => '详情' },
      ),
  },
]
</script>

<template>
  <div class="admin-companies">
    <NCard :bordered="false" class="filter-card">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索账本 ID / 公司名"
          size="small"
          clearable
          style="width: 280px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <SoloButton size="small" variant="primary" @click="onSearch">
          <template #icon><i class="ri-search-line" /></template>
          搜索
        </SoloButton>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          收录系统内所有注册公司的账套规模与最近活跃流水
        </span>
      </div>
    </NCard>

    <NCard :bordered="false" class="table-card">
      <SoloTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        clickable
        :pagination="false"
        :scroll-x="1080"
        empty-title="未找到匹配的公司账套"
        empty-description="请调整搜索关键词后重试"
        @row-click="openCompanyDrawer"
      />
    </NCard>

    <!-- 公司账套详情抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="activeCompany ? `账套详情 · ${activeCompany.name || activeCompany.companyId}` : '账套详情'"
      :width="540"
    >
      <div v-if="activeCompany" class="company-drawer-content">
        <div class="company-hero-card">
          <div class="company-avatar-box">
            <i class="ri-building-2-line" />
          </div>
          <div class="company-hero-meta">
            <h3>{{ activeCompany.name || '未命名账套' }}</h3>
            <span class="company-id-sub">{{ activeCompany.companyId }}</span>
          </div>
          <SoloStatusBadge tone="brand">
            {{ standardLabel(activeCompany.accountingStandard) }}
          </SoloStatusBadge>
        </div>

        <div class="detail-section">
          <h4 class="section-title">所有者信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">所有者昵称</span>
              <span class="info-val">{{ activeCompany.ownerName || '—' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">绑定手机</span>
              <span class="info-val">{{ activeCompany.ownerPhone || '—' }}</span>
            </div>
            <div class="info-item" style="grid-column: span 2">
              <span class="info-label">用户 ID</span>
              <span class="info-val monospace">{{ activeCompany.ownerUserId }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">数据规模与活跃度</h4>
          <div class="metric-row">
            <div class="metric-card">
              <span class="metric-label">成员总数</span>
              <span class="metric-value">{{ activeCompany.memberCount }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-label">记账凭证</span>
              <span class="metric-value">{{ activeCompany.entryCount }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">时间轨迹</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">账套创建时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeCompany.createdAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">最近记账活跃</span>
              <span class="info-val">{{ formatAdminDateTime(activeCompany.lastActivityAt) }}</span>
            </div>
          </div>
        </div>
      </div>
    </SoloDrawer>
  </div>
</template>

<style scoped>
.admin-companies {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.company-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  cursor: pointer;
}

.company-name {
  font-weight: 600;
  color: var(--solo-text-primary);
}

.company-id-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: monospace;
}

.owner-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.owner-name {
  color: var(--solo-text-primary);
}

.owner-phone {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* 抽屉样式 */
.company-drawer-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 8px 0;
}

.company-hero-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.company-avatar-box {
  width: 44px;
  height: 44px;
  border-radius: var(--solo-radius, 8px);
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}

.company-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.company-hero-meta h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 14px 16px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.info-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.info-val {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--solo-text-primary);
}

.monospace {
  font-family: monospace;
  font-size: 12.5px;
}

.metric-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.metric-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.metric-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
</style>
