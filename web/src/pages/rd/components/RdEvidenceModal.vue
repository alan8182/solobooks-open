<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert, NButton, NForm, NFormItem, NInput, NModal, NSelect, NSpace, useMessage,
} from 'naive-ui'
import { createRdEvidence } from '@/api/rd'
import type { RdEvidenceInput } from '@/api/rd'
import type { RdProject } from '@/api/types'

const props = defineProps<{
  show: boolean
  defaultProjectId?: string
  defaultEntryId?: string
  projects: RdProject[]
  evidenceTypes: Array<{ value: string; label: string }>
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()
const saving = ref(false)

const form = ref<RdEvidenceInput>({
  projectId: '',
  entryId: '',
  documentType: 'other',
  referenceNo: '',
  fileUrl: '',
  status: 'available',
  notes: '',
})

const activeProjectOptions = computed(() =>
  props.projects
    .filter((p) => ['active', 'completed'].includes(p.status))
    .map((p) => ({ label: `${p.code} · ${p.name}`, value: p.id })),
)

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    form.value = {
      projectId: props.defaultProjectId || activeProjectOptions.value[0]?.value || '',
      entryId: props.defaultEntryId || '',
      documentType: 'other',
      referenceNo: '',
      fileUrl: '',
      status: 'available',
      notes: '',
    }
  },
  { immediate: true },
)

async function handleSubmit() {
  if (!form.value.projectId) {
    message.warning('请选择关联的研发项目')
    return
  }
  if (!form.value.referenceNo?.trim() && !form.value.fileUrl?.trim()) {
    message.warning('资料编号或文件链接至少填写一项（例如合同号、发票号或企业网盘链接）')
    return
  }

  saving.value = true
  try {
    await createRdEvidence({
      ...form.value,
      referenceNo: form.value.referenceNo?.trim() || undefined,
      fileUrl: form.value.fileUrl?.trim() || undefined,
      entryId: form.value.entryId?.trim() || undefined,
      notes: form.value.notes?.trim() || undefined,
    })
    message.success('研发证据资料已成功登记并归档')
    emit('update:show', false)
    emit('success')
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '证据登记失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="登记研发证据资料"
    style="width: min(640px, 94vw)"
    @update:show="emit('update:show', $event)"
  >
    <NForm label-placement="top">
      <div class="form-grid">
        <NFormItem label="研发项目" required>
          <NSelect
            v-model:value="form.projectId"
            :options="activeProjectOptions"
            placeholder="请选择项目"
            filterable
          />
        </NFormItem>
        <NFormItem label="资料类型" required>
          <NSelect
            v-model:value="form.documentType"
            :options="evidenceTypes"
            placeholder="请选择资料类型"
          />
        </NFormItem>
        <NFormItem label="关联账目 / 凭证 ID（选填）">
          <NInput v-model:value="form.entryId" placeholder="若留空则归为项目级整体资料" />
        </NFormItem>
        <NFormItem label="证据状态">
          <NSelect
            v-model:value="form.status"
            :options="[
              { label: '可用 (已归档生效)', value: 'available' },
              { label: '待复核 (材料待补齐)', value: 'pending_review' },
              { label: '缺失 (仅登记缺口)', value: 'missing' },
            ]"
          />
        </NFormItem>
        <NFormItem label="资料编号">
          <NInput v-model:value="form.referenceNo" placeholder="如合同号、发票代码号码、工时表批号等" />
        </NFormItem>
        <NFormItem label="文件存证链接">
          <NInput v-model:value="form.fileUrl" placeholder="https://... 或企业网盘/共享文档地址" />
        </NFormItem>
      </div>

      <NFormItem label="备注说明">
        <NInput v-model:value="form.notes" type="textarea" :rows="2" placeholder="材料归档位置、要点或审计备忘" />
      </NFormItem>

      <NAlert type="info" :bordered="false" class="mb-4">
        项目级立项资料（如立项报告、申报书）可不关联具体账目 ID；具体支出的发票、工时单或采购合同建议关联对应账目。
      </NAlert>

      <NSpace justify="end">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSubmit">保存资料</NButton>
      </NSpace>
    </NForm>
  </NModal>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
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
