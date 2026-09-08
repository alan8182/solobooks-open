<script setup lang="ts">
import { h, ref, onMounted } from 'vue'
import { NDataTable, NTag, NPagination, useMessage } from 'naive-ui'
import { getAuditLogs } from '@/api/audit'
import type { AuditLogItem } from '@/api/audit'

const message = useMessage()
const logs = ref<AuditLogItem[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const loading = ref(false)

const actionColors: Record<string, string> = {
  create: 'success',
  update: 'warning',
  delete: 'error',
  import: 'info',
  apply: 'primary',
}

function actionTag(action: string) {
  const map: Record<string, string> = {
    create: '创建',
    update: '更新',
    delete: '删除',
    import: '导入',
    apply: '应用',
  }
  return map[action] || action
}

function entityTag(type: string) {
  const map: Record<string, string> = {
    entry: '记账',
    recurring_template: '模板',
    bank_statement: '银行流水',
    invoice: '发票',
    user: '用户',
  }
  return map[type] || type
}

function formatTime(iso: string) {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function parseDetail(detail: string) {
  try {
    return JSON.parse(detail)
  } catch {
    return null
  }
}

function detailText(detail: string) {
  const parsed = parseDetail(detail)
  if (parsed && parsed.title) return parsed.title
  if (parsed && parsed.count !== undefined) return `导入 ${parsed.count} 条`
  return ''
}

async function fetchLogs() {
  loading.value = true
  try {
    const res = await getAuditLogs(limit.value, offset.value)
    logs.value = res.items
    total.value = res.total
  } catch {
    message.error('加载审计日志失败')
  } finally {
    loading.value = false
  }
}

function handlePageChange(page: number) {
  offset.value = (page - 1) * limit.value
  fetchLogs()
}

onMounted(fetchLogs)

const columns = [
  { title: '时间', key: 'created_at', width: 170, render: (row: AuditLogItem) => formatTime(row.created_at) },
  { title: '操作', key: 'action', width: 90, render: (row: AuditLogItem) => h(NTag, { size: 'small', type: actionColors[row.action] as any }, { default: () => actionTag(row.action) }) },
  { title: '实体', key: 'entity_type', width: 100, render: (row: AuditLogItem) => entityTag(row.entity_type) },
  { title: '详情', key: 'detail', render: (row: AuditLogItem) => detailText(row.detail) || row.detail },
]
</script>

<template>
  <div class="audit-log-page">
    <div class="page-header">
      <h2>审计日志</h2>
    </div>

    <NDataTable
      :columns="columns"
      :data="logs"
      :loading="loading"
      :bordered="false"
      striped
      size="small"
    />

    <div class="pagination-wrap" v-if="total > limit">
      <NPagination
        :page="Math.floor(offset / limit) + 1"
        :page-size="limit"
        :item-count="total"
        @update:page="handlePageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.audit-log-page {
  max-width: 960px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}
</style>
