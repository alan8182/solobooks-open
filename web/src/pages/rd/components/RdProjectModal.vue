<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  NButton, NForm, NFormItem, NInput, NModal, NSelect, NSpace, useMessage,
} from 'naive-ui'
import dayjs from 'dayjs'
import { createRdProject, updateRdProject } from '@/api/rd'
import type { RdProjectInput } from '@/api/rd'
import type { RdProject } from '@/api/types'

const props = defineProps<{
  show: boolean
  project?: RdProject | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()
const saving = ref(false)

const form = ref<RdProjectInput>({
  code: '',
  name: '',
  manager: '',
  startDate: dayjs().format('YYYY-MM-DD'),
  endDate: dayjs().endOf('year').format('YYYY-MM-DD'),
  status: 'active',
  initiationDocumentStatus: 'missing',
  notes: '',
})

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    if (props.project) {
      form.value = {
        code: props.project.code || '',
        name: props.project.name || '',
        manager: props.project.manager || '',
        startDate: props.project.startDate || dayjs().format('YYYY-MM-DD'),
        endDate: props.project.endDate || '',
        status: (props.project.status === 'archived' ? 'completed' : (props.project.status || 'active')) as 'draft' | 'active' | 'completed',
        initiationDocumentStatus: props.project.initiationDocumentStatus || 'missing',
        notes: props.project.notes || '',
      }
    } else {
      form.value = {
        code: '',
        name: '',
        manager: '',
        startDate: dayjs().format('YYYY-MM-DD'),
        endDate: dayjs().endOf('year').format('YYYY-MM-DD'),
        status: 'active',
        initiationDocumentStatus: 'missing',
        notes: '',
      }
    }
  },
  { immediate: true },
)

async function handleSubmit() {
  if (!form.value.code.trim() || !form.value.name.trim()) {
    message.warning('请填写项目编号和名称')
    return
  }
  saving.value = true
  try {
    if (props.project?.id) {
      await updateRdProject(props.project.id, {
        code: form.value.code.trim(),
        name: form.value.name.trim(),
        manager: form.value.manager?.trim() || undefined,
        startDate: form.value.startDate,
        endDate: form.value.endDate || undefined,
        status: form.value.status,
        initiationDocumentStatus: form.value.initiationDocumentStatus,
        notes: form.value.notes?.trim() || undefined,
      })
      message.success('研发项目已更新')
    } else {
      await createRdProject({
        ...form.value,
        code: form.value.code.trim(),
        name: form.value.name.trim(),
      })
      message.success('研发项目已创建')
    }
    emit('update:show', false)
    emit('success')
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="project ? '编辑研发项目' : '新建研发项目'"
    style="width: min(640px, 94vw)"
    @update:show="emit('update:show', $event)"
  >
    <NForm label-placement="top">
      <div class="form-grid">
        <NFormItem label="项目编号" required>
          <NInput v-model:value="form.code" placeholder="例如 RD-2026-01" />
        </NFormItem>
        <NFormItem label="项目名称" required>
          <NInput v-model:value="form.name" placeholder="例如 新一代智能记账引擎" />
        </NFormItem>
        <NFormItem label="项目负责人">
          <NInput v-model:value="form.manager" placeholder="负责人姓名" />
        </NFormItem>
        <NFormItem label="项目状态">
          <NSelect
            v-model:value="form.status"
            :options="[
              { label: '草稿', value: 'draft' },
              { label: '进行中', value: 'active' },
              { label: '已完成', value: 'completed' },
            ]"
          />
        </NFormItem>
        <NFormItem label="开始日期">
          <NInput v-model:value="form.startDate" placeholder="YYYY-MM-DD" />
        </NFormItem>
        <NFormItem label="结束日期">
          <NInput v-model:value="form.endDate" placeholder="YYYY-MM-DD（选填）" />
        </NFormItem>
        <NFormItem label="立项资料完整度">
          <NSelect
            v-model:value="form.initiationDocumentStatus"
            :options="[
              { label: '缺少立项资料', value: 'missing' },
              { label: '部分完整', value: 'partial' },
              { label: '资料完整', value: 'complete' },
            ]"
          />
        </NFormItem>
        <NFormItem label="备注">
          <NInput v-model:value="form.notes" placeholder="研发目标、阶段说明等" />
        </NFormItem>
      </div>
      <NSpace justify="end" style="margin-top: 12px;">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSubmit">保存</NButton>
      </NSpace>
    </NForm>
  </NModal>
</template>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
@media (max-width: 600px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
