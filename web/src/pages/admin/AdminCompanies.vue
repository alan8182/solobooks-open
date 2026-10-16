<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NInput, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { fetchAdminCompanies, type AdminCompanyItem } from '@/api/admin'

const message = useMessage()
const loading = ref(false)
const query = ref('')
const items = ref<AdminCompanyItem[]>([])

const STANDARD_LABELS: Record<string, string> = {
  small_enterprise_cn: '小企业会计准则',
}

function standardLabel(value: string): string {
  return STANDARD_LABELS[value] ?? value
}

function fmtDateTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const columns: DataTableColumns<AdminCompanyItem> = [
  {
    title: '账本 / 公司',
    key: 'name',
    minWidth: 220,
    render: (row) =>
      h('div', { style: 'display:flex;flex-direction:column;line-height:1.5' }, [
        h('span', { style: 'font-weight:600;color:var(--solo-text-primary)' }, row.name || '未命名账本'),
        h('span', { style: 'font-size:12px;color:var(--solo-text-muted);font-family:monospace' }, row.companyId),
      ]),
  },
  {
    title: '所有者',
    key: 'ownerUserId',
    minWidth: 170,
    render: (row) =>
      h('div', { style: 'display:flex;flex-direction:column;line-height:1.5' }, [
        h('span', null, row.ownerName || row.ownerUserId),
        h('span', { style: 'font-size:12px;color:var(--solo-text-muted)' }, row.ownerPhone || '—'),
      ]),
  },
  { title: '成员数', key: 'memberCount', width: 90, align: 'right' },
  { title: '凭证数', key: 'entryCount', width: 90, align: 'right' },
  { title: '最近活跃', key: 'lastActivityAt', width: 150, render: (row) => fmtDateTime(row.lastActivityAt) },
  {
    title: '会计准则',
    key: 'accountingStandard',
    width: 140,
    render: (row) =>
      h(NTag, { size: 'tiny', bordered: false }, { default: () => standardLabel(row.accountingStandard) }),
  },
  { title: '创建时间', key: 'createdAt', width: 150, render: (row) => fmtDateTime(row.createdAt) },
]

async function load() {
  loading.value = true
  try {
    const body = await fetchAdminCompanies({ query: query.value })
    items.value = body.items
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '公司列表加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-companies">
    <NCard :bordered="false">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索账本 ID / 公司名"
          size="small"
          clearable
          style="width: 260px"
          @keyup.enter="load"
          @clear="load"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <NButton size="small" type="primary" @click="load">搜索</NButton>
      </div>
    </NCard>

    <NCard :bordered="false">
      <NDataTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
        :scroll-x="1040"
      />
    </NCard>
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
</style>
