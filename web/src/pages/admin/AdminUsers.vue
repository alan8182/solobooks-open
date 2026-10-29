<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref } from 'vue'
import {
  NButton,
  NCard,
  NInput,
  NPopconfirm,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  fetchAdminUsers,
  patchAdminUser,
  type AdminUserItem,
} from '@/api/admin'
import { useAdminTable, formatAdminDateTime } from '@/composables/useAdminTable'
import { SoloTable, SoloDrawer, SoloStatusBadge, SoloButton } from '@/components/ui'

const message = useMessage()

const {
  loading,
  items,
  total,
  page,
  pageSize,
  query,
  load,
  onSearch,
  onPageChange,
  onPageSizeChange,
} = useAdminTable<AdminUserItem>({
  defaultPageSize: 20,
  loadData: async ({ page, pageSize, query }) => {
    return await fetchAdminUsers({ query, page, pageSize })
  },
})

const activeUser = ref<AdminUserItem | null>(null)
const drawerVisible = ref(false)

const ROLE_LABELS: Record<string, string> = {
  owner: '所有者',
  accountant: '会计',
  preparer: '制单',
  viewer: '查看',
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

function openUserDrawer(user: AdminUserItem) {
  activeUser.value = user
  drawerVisible.value = true
}

async function applyPatch(user: AdminUserItem, payload: { isDisabled?: boolean; isPlatformAdmin?: boolean }) {
  try {
    await patchAdminUser(user.userId, payload)
    message.success('操作成功')
    if (activeUser.value && activeUser.value.userId === user.userId) {
      if (payload.isDisabled !== undefined) activeUser.value.isDisabled = payload.isDisabled
      if (payload.isPlatformAdmin !== undefined) activeUser.value.isPlatformAdmin = payload.isPlatformAdmin
    }
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '操作失败')
  }
}

const columns: DataTableColumns<AdminUserItem> = [
  {
    title: '用户',
    key: 'userId',
    minWidth: 200,
    render: (row) =>
      h('div', { class: 'user-cell', onClick: () => openUserDrawer(row) }, [
        h('span', { class: 'user-name' }, row.nickname || '未设置昵称'),
        h('span', { class: 'user-sub' }, row.phone || row.userId),
      ]),
  },
  {
    title: '平台管理员',
    key: 'isPlatformAdmin',
    width: 120,
    render: (row) =>
      row.isPlatformAdmin
        ? h(SoloStatusBadge, { tone: 'brand', size: 'small' }, { default: () => '平台管理员' })
        : h(SoloStatusBadge, { tone: 'neutral', plain: true, size: 'small' }, { default: () => '普通用户' }),
  },
  {
    title: '账号状态',
    key: 'isDisabled',
    width: 100,
    render: (row) =>
      row.isDisabled
        ? h(SoloStatusBadge, { tone: 'danger', size: 'small' }, { default: () => '已禁用' })
        : h(SoloStatusBadge, { tone: 'success', size: 'small' }, { default: () => '正常' }),
  },
  {
    title: '公司与角色',
    key: 'companyCount',
    minWidth: 180,
    render: (row) =>
      h('div', { class: 'roles-cell' }, [
        h('span', { class: 'company-count' }, `${row.companyCount} 个账套`),
        ...row.roles.map((role) =>
          h(NTag, { key: role, size: 'tiny', bordered: false, round: true }, { default: () => roleLabel(role) }),
        ),
      ]),
  },
  { title: '注册时间', key: 'createdAt', width: 155, render: (row) => formatAdminDateTime(row.createdAt) },
  { title: '最近登录', key: 'lastLoginAt', width: 155, render: (row) => formatAdminDateTime(row.lastLoginAt) },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => {
      const detailBtn = h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => openUserDrawer(row) },
        { default: () => '详情' },
      )
      const disableBtn = h(
        NPopconfirm,
        {
          onPositiveClick: () => applyPatch(row, { isDisabled: !row.isDisabled }),
        },
        {
          trigger: () =>
            h(
              NButton,
              { size: 'tiny', type: row.isDisabled ? 'success' : 'error', quaternary: true },
              { default: () => (row.isDisabled ? '启用' : '禁用') },
            ),
          default: () =>
            row.isDisabled
              ? `确认启用用户 ${row.nickname || row.userId}？`
              : `禁用后该用户所有接口立即 401，确认禁用 ${row.nickname || row.userId}？`,
        },
      )
      const adminBtn = h(
        NPopconfirm,
        {
          onPositiveClick: () => applyPatch(row, { isPlatformAdmin: !row.isPlatformAdmin }),
        },
        {
          trigger: () =>
            h(
              NButton,
              { size: 'tiny', type: 'primary', quaternary: true },
              { default: () => (row.isPlatformAdmin ? '撤销管理' : '设为管理') },
            ),
          default: () =>
            row.isPlatformAdmin
              ? `撤销 ${row.nickname || row.userId} 的平台管理员权限？`
              : `授予 ${row.nickname || row.userId} 平台管理员权限？`,
        },
      )
      return h('div', { class: 'action-btns' }, [detailBtn, disableBtn, adminBtn])
    },
  },
]
</script>

<template>
  <div class="admin-users">
    <NCard :bordered="false" class="filter-card">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索用户 ID / 手机号 / 昵称"
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
          禁用即时生效：被禁用户发起的所有 API 请求均直接拦截并返回 401
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
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        empty-title="未找到匹配的用户"
        empty-description="请尝试调整搜索条件或清空关键字"
        @row-click="openUserDrawer"
      />
    </NCard>

    <!-- 用户详情抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="activeUser ? `用户详情 · ${activeUser.nickname || activeUser.userId}` : '用户详情'"
      :width="540"
    >
      <div v-if="activeUser" class="user-drawer-content">
        <div class="user-hero-card">
          <div class="user-avatar-circle">
            <i class="ri-user-line" />
          </div>
          <div class="user-hero-meta">
            <h3>{{ activeUser.nickname || '未设置昵称' }}</h3>
            <span class="user-id-sub">{{ activeUser.userId }}</span>
          </div>
          <div class="user-hero-badges">
            <SoloStatusBadge :tone="activeUser.isDisabled ? 'danger' : 'success'">
              {{ activeUser.isDisabled ? '已禁用' : '正常' }}
            </SoloStatusBadge>
            <SoloStatusBadge v-if="activeUser.isPlatformAdmin" tone="brand">
              平台管理员
            </SoloStatusBadge>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">基本信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">手机号码</span>
              <span class="info-val">{{ activeUser.phone || '未绑定' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">注册时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeUser.createdAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">最近登录</span>
              <span class="info-val">{{ formatAdminDateTime(activeUser.lastLoginAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">关联账套</span>
              <span class="info-val">{{ activeUser.companyCount }} 个公司账套</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">角色身份</h4>
          <div class="roles-tags-wrapper">
            <NTag
              v-for="role in activeUser.roles"
              :key="role"
              size="small"
              round
              :bordered="false"
              type="info"
            >
              {{ roleLabel(role) }}
            </NTag>
            <span v-if="!activeUser.roles.length" class="empty-roles-tip">暂无分配业务角色</span>
          </div>
        </div>

        <div class="detail-section danger-section">
          <h4 class="section-title">运维操作与权限变更</h4>
          <p class="section-desc">修改操作将实时写入操作审计日志，即时在全平台生效。</p>
          <div class="action-row">
            <NPopconfirm
              :on-positive-click="() => applyPatch(activeUser!, { isDisabled: !activeUser!.isDisabled })"
            >
              <template #trigger>
                <SoloButton
                  size="small"
                  :variant="activeUser.isDisabled ? 'primary' : 'danger'"
                >
                  <template #icon>
                    <i :class="activeUser.isDisabled ? 'ri-shield-check-line' : 'ri-forbid-line'" />
                  </template>
                  {{ activeUser.isDisabled ? '恢复并启用用户' : '立即禁用用户账号' }}
                </SoloButton>
              </template>
              {{ activeUser.isDisabled ? '确认解除该用户的所有禁用限制？' : '禁用后该用户所有现有 Token 立即失效，无法发起任何请求，确认？' }}
            </NPopconfirm>

            <NPopconfirm
              :on-positive-click="() => applyPatch(activeUser!, { isPlatformAdmin: !activeUser!.isPlatformAdmin })"
            >
              <template #trigger>
                <SoloButton
                  size="small"
                  variant="secondary"
                >
                  <template #icon>
                    <i :class="activeUser.isPlatformAdmin ? 'ri-user-unfollow-line' : 'ri-admin-line'" />
                  </template>
                  {{ activeUser.isPlatformAdmin ? '取消平台管理员权限' : '设为平台管理员' }}
                </SoloButton>
              </template>
              {{ activeUser.isPlatformAdmin ? `确认撤销 ${activeUser.nickname || activeUser.userId} 的管理后台访问权限？` : `确认赋予 ${activeUser.nickname || activeUser.userId} 平台管理员最高权限？` }}
            </NPopconfirm>
          </div>
        </div>
      </div>
    </SoloDrawer>
  </div>
</template>

<style scoped>
.admin-users {
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

.user-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  cursor: pointer;
}

.user-name {
  font-weight: 600;
  color: var(--solo-text-primary);
}

.user-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.roles-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.company-count {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 抽屉样式 */
.user-drawer-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 8px 0;
}

.user-hero-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.user-avatar-circle {
  width: 44px;
  height: 44px;
  border-radius: 999px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.user-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.user-hero-meta h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.user-id-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: monospace;
}

.user-hero-badges {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
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

.section-desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
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

.roles-tags-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.empty-roles-tip {
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}
</style>
