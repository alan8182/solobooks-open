<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, h, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, NDataTable, useMessage, NSpin } from 'naive-ui'
import { getTemplates, deleteTemplate, applyTemplate, type RecurringTemplate } from '@/api/recurring'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
const message = useMessage()
const { confirmDelete } = useDeleteConfirm()
const loading = ref(false)
const templates = ref<RecurringTemplate[]>([])
const applying = ref<string | null>(null)

const frequencyMap: Record<string, { label: string; type: 'info' | 'warning' | 'success' | 'default' }> = {
  weekly: { label: '每周', type: 'info' },
  biweekly: { label: '每两周', type: 'warning' },
  monthly: { label: '每月', type: 'success' },
  quarterly: { label: '每季度', type: 'warning' },
  yearly: { label: '每年', type: 'default' },
}

const columns = [
  { title: '模板名称', key: 'name' },
  {
    title: '频率', key: 'frequency', width: 100,
    render: (row: RecurringTemplate) => {
      const cfg = frequencyMap[row.frequency] || { label: row.frequency, type: 'default' as const }
      return h(NTag, { size: 'tiny', type: cfg.type, bordered: false }, () => cfg.label)
    },
  },
  { title: '下次到期', key: 'next_due_date', width: 110 },
  {
    title: '状态', key: 'is_active', width: 70,
    render: (row: RecurringTemplate) => h(NTag, {
      size: 'tiny', type: row.is_active ? 'success' : 'default', bordered: false,
    }, () => row.is_active ? '启用' : '停用'),
  },
  {
    title: '操作', key: 'actions', width: 180,
    render: (row: RecurringTemplate) => h(NSpace, { size: 'small' }, () => [
      h(NButton, {
        size: 'tiny', type: 'primary', ghost: true,
        onClick: () => handleApply(row.id),
        loading: applying.value === row.id,
      }, () => '应用'),
      h(NButton, {
        size: 'tiny', ghost: true,
        onClick: () => router.push(`/recurring/${row.id}`),
      }, () => '编辑'),
      h(NButton, {
        size: 'tiny', ghost: true, type: 'error',
        onClick: () => confirmDelete({
          content: `确定删除周期模板“${row.name}”吗？此操作不可撤销。`,
          onConfirm: () => handleDelete(row.id),
        }),
      }, () => '删除'),
    ]),
  },
]

async function loadTemplates() {
  loading.value = true
  try {
    templates.value = await getTemplates()
  } catch {
    message.error('加载模板失败')
  } finally {
    loading.value = false
  }
}

async function handleApply(id: string) {
  applying.value = id
  try {
    const result = await applyTemplate(id)
    message.success(`已生成记账条目: ${result.entry?.title || ''}`)
    await loadTemplates()
  } catch {
    message.error('应用模板失败')
  } finally {
    applying.value = null
  }
}

async function handleDelete(id: string) {
  try {
    await deleteTemplate(id)
    message.success('已删除')
    await loadTemplates()
  } catch {
    message.error('删除失败')
  }
}

onMounted(loadTemplates)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(loadTemplates)
</script>

<template>
  <div class="recurring-page">
    <div class="page-header">
      <div class="page-header-left">
        <div class="title-section">
          <h2>周期性交易模板</h2>
          <p class="subtitle">管理按月、按季或按年重复发生的固定收支规则</p>
        </div>
      </div>
      <div class="page-header-right">
        <NButton size="small" type="primary" @click="router.push('/recurring/create')">新建模板</NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <NDataTable
          :columns="columns"
          :data="templates"
          :row-key="(row: RecurringTemplate) => row.id"
          :bordered="false"
          :single-line="true"
          size="small"
        />
        <SoloEmpty
          v-if="templates.length === 0 && !loading"
          variant="hint"
          title="还没有周期性模板"
          description="房租、订阅费这类每月固定支出，设一次模板每月自动生成草稿。点上方「新建模板」创建。"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.recurring-page {
  max-width: 800px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 16px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.header-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.table-card {
  border-radius: var(--solo-radius-lg);
}
.empty-state {
  text-align: center;
  color: var(--solo-text-muted);
  padding: 48px;
}
</style>
