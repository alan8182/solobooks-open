<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  NButton, NCard, NInput, NRadioGroup, NRadioButton, NSpace, NSpin, NTag, useDialog, useMessage,
} from 'naive-ui'
import type { RdProject } from '@/api/types'
import { updateRdProject } from '@/api/rd'
import { SoloEmpty } from '@/components/ui'

const props = defineProps<{
  projects: RdProject[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'edit', project: RdProject): void
  (e: 'addEvidence', projectId: string): void
  (e: 'viewLedger', projectId: string): void
  (e: 'refresh'): void
}>()

const message = useMessage()
const dialog = useDialog()

const filterStatus = ref<'all' | 'active' | 'completed' | 'draft' | 'archived'>('all')
const searchQuery = ref('')

const statusOptions = [
  { label: '全部项目', value: 'all' },
  { label: '进行中', value: 'active' },
  { label: '已完成', value: 'completed' },
  { label: '草稿', value: 'draft' },
  { label: '已归档', value: 'archived' },
]

function statusLabel(status: RdProject['status']) {
  return { draft: '草稿', active: '进行中', completed: '已完成', archived: '已归档' }[status] || status
}

function statusTagType(status: RdProject['status']): 'success' | 'warning' | 'default' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'completed') return 'info'
  if (status === 'draft') return 'warning'
  return 'default'
}

function documentLabel(status: RdProject['initiationDocumentStatus']) {
  return { missing: '缺少立项资料', partial: '资料不完整', complete: '资料完整' }[status] || status
}

function documentTagType(status: RdProject['initiationDocumentStatus']): 'success' | 'warning' | 'error' {
  if (status === 'complete') return 'success'
  if (status === 'partial') return 'warning'
  return 'error'
}

const filteredProjects = computed(() => {
  return props.projects.filter((p) => {
    if (filterStatus.value !== 'all' && p.status !== filterStatus.value) return false
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.trim().toLowerCase()
      const matchCode = p.code.toLowerCase().includes(q)
      const matchName = p.name.toLowerCase().includes(q)
      const matchManager = (p.manager || '').toLowerCase().includes(q)
      if (!matchCode && !matchName && !matchManager) return false
    }
    return true
  })
})

function confirmArchive(project: RdProject) {
  dialog.warning({
    title: '归档研发项目',
    content: `确定要归档项目「${project.code} · ${project.name}」吗？归档后该项目将不再出现在日常记账候选列表中，但辅助账与凭证审计链不受影响。`,
    positiveText: '确认归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await updateRdProject(project.id, { status: 'archived' })
        message.success('研发项目已归档')
        emit('refresh')
      } catch {
        message.error('归档失败')
      }
    },
  })
}
</script>

<template>
  <div class="rd-projects-tab">
    <!-- 顶部操作栏 -->
    <NCard :bordered="false" class="toolbar-card mb-4">
      <div class="toolbar-wrapper">
        <div class="toolbar-left">
          <NRadioGroup v-model:value="filterStatus" size="medium">
            <NRadioButton v-for="opt in statusOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </NRadioButton>
          </NRadioGroup>
          <NInput
            v-model:value="searchQuery"
            placeholder="搜索项目编号 / 名称 / 负责人"
            clearable
            style="width: 240px;"
          >
            <template #prefix><i class="ri-search-line" /></template>
          </NInput>
        </div>
        <div class="toolbar-right">
          <NButton type="primary" @click="emit('create')">
            <template #icon><i class="ri-add-line" /></template>
            新建研发项目
          </NButton>
        </div>
      </div>
    </NCard>

    <!-- 项目列表 -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <div v-if="filteredProjects.length" class="table-wrap">
          <table class="solo-table">
            <thead>
              <tr>
                <th style="width: 140px;">项目编号</th>
                <th style="width: 240px;">项目名称</th>
                <th style="width: 110px;">负责人</th>
                <th style="width: 220px;">研发周期</th>
                <th style="width: 100px;">项目状态</th>
                <th style="width: 130px;">立项资料状态</th>
                <th>备注</th>
                <th style="width: 220px;" class="action-th">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="project in filteredProjects" :key="project.id">
                <td class="font-mono">
                  <span class="project-code">{{ project.code }}</span>
                </td>
                <td>
                  <span class="project-name-bold">{{ project.name }}</span>
                </td>
                <td>{{ project.manager || '未指定' }}</td>
                <td class="font-mono text-secondary">
                  {{ project.startDate }} 至 {{ project.endDate || '进行中' }}
                </td>
                <td>
                  <NTag size="small" :type="statusTagType(project.status)" :bordered="false">
                    {{ statusLabel(project.status) }}
                  </NTag>
                </td>
                <td>
                  <NTag size="small" :type="documentTagType(project.initiationDocumentStatus)" :bordered="false">
                    {{ documentLabel(project.initiationDocumentStatus) }}
                  </NTag>
                </td>
                <td class="text-secondary cell-notes" :title="project.notes">
                  {{ project.notes || '-' }}
                </td>
                <td class="action-td">
                  <NSpace :size="8">
                    <NButton text size="small" type="primary" @click="emit('viewLedger', project.id)">
                      查看账目
                    </NButton>
                    <NButton text size="small" @click="emit('addEvidence', project.id)">
                      补立项材料
                    </NButton>
                    <NButton text size="small" @click="emit('edit', project)">
                      编辑
                    </NButton>
                    <NButton
                      v-if="project.status !== 'archived'"
                      text
                      size="small"
                      type="warning"
                      @click="confirmArchive(project)"
                    >
                      归档
                    </NButton>
                  </NSpace>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <SoloEmpty
          v-else
          variant="hint"
          title="未找到符合条件的研发项目"
          description="点击「新建研发项目」为企业建立研发核算台账，赋能费用精准归集与加计扣除。"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
.toolbar-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.toolbar-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.toolbar-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
}
.table-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.table-wrap {
  overflow-x: auto;
}
.solo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.solo-table th,
.solo-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  white-space: nowrap;
}
.solo-table th {
  color: var(--solo-text-secondary);
  font-weight: 600;
  background: var(--solo-bg-soft);
}
.action-th,
.action-td {
  text-align: right;
}
.font-mono {
  font-family: 'Outfit', 'Inter', monospace;
}
.project-code {
  font-weight: 600;
  color: var(--solo-primary);
}
.project-name-bold {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.text-secondary {
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.cell-notes {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
