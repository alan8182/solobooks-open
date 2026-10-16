<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
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

const message = useMessage()
const loading = ref(false)
const query = ref('')
const items = ref<AdminUserItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const ROLE_LABELS: Record<string, string> = {
  owner: '所有者',
  accountant: '会计',
  preparer: '制单',
  viewer: '查看',
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    const body = await fetchAdminUsers({ query: query.value, page: page.value, pageSize: pageSize.value })
    items.value = body.items
    total.value = body.total
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '用户列表加载失败')
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  void load()
}

async function applyPatch(user: AdminUserItem, payload: { isDisabled?: boolean; isPlatformAdmin?: boolean }) {
  try {
    await patchAdminUser(user.userId, payload)
    message.success('操作成功')
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
      h('div', { style: 'display:flex;flex-direction:column;line-height:1.5' }, [
        h('span', { style: 'font-weight:600;color:var(--solo-text-primary)' }, row.nickname || '未设置昵称'),
        h('span', { style: 'font-size:12px;color:var(--solo-text-muted)' }, row.phone || row.userId),
      ]),
  },
  {
    title: '平台管理员',
    key: 'isPlatformAdmin',
    width: 110,
    render: (row) =>
      row.isPlatformAdmin
        ? h(NTag, { type: 'info', size: 'small', round: true, bordered: false }, { default: () => '管理员' })
        : h('span', { style: 'color:var(--solo-text-muted)' }, '—'),
  },
  {
    title: '状态',
    key: 'isDisabled',
    width: 90,
    render: (row) =>
      row.isDisabled
        ? h(NTag, { type: 'error', size: 'small', round: true, bordered: false }, { default: () => '已禁用' })
        : h(NTag, { type: 'success', size: 'small', round: true, bordered: false }, { default: () => '正常' }),
  },
  {
    title: '公司 / 角色',
    key: 'companyCount',
    minWidth: 180,
    render: (row) =>
      h('div', { style: 'display:flex;align-items:center;gap:6px;flex-wrap:wrap' }, [
        h('span', { style: 'font-size:13px' }, `${row.companyCount} 个`),
        ...row.roles.map((role) =>
          h(NTag, { key: role, size: 'tiny', bordered: false }, { default: () => roleLabel(role) }),
        ),
      ]),
  },
  { title: '注册时间', key: 'createdAt', width: 150, render: (row) => fmtDateTime(row.createdAt) },
  { title: '最近登录', key: 'lastLoginAt', width: 150, render: (row) => fmtDateTime(row.lastLoginAt) },
  {
    title: '操作',
    key: 'actions',
    width: 230,
    render: (row) => {
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
              ? `确认启用 ${row.nickname || row.userId}？`
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
              { default: () => (row.isPlatformAdmin ? '撤销管理员' : '设为管理员') },
            ),
          default: () =>
            row.isPlatformAdmin
              ? `撤销 ${row.nickname || row.userId} 的平台管理员权限？`
              : `授予 ${row.nickname || row.userId} 平台管理员权限？`,
        },
      )
      return h('div', { style: 'display:flex;gap:4px' }, [disableBtn, adminBtn])
    },
  },
]

onMounted(load)
</script>

<template>
  <div class="admin-users">
    <NCard :bordered="false">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索用户 ID / 手机号 / 昵称"
          size="small"
          clearable
          style="width: 260px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <NButton size="small" type="primary" @click="onSearch">搜索</NButton>
        <span class="toolbar-note"><i class="ri-information-line" /> 禁用即时生效：被禁用户所有接口返回 401</span>
      </div>
    </NCard>

    <NCard :bordered="false">
      <NDataTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        remote
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: (p: number) => { page = p; void load() },
          onUpdatePageSize: (s: number) => { pageSize = s; page = 1; void load() },
        }"
      />
    </NCard>
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
</style>
