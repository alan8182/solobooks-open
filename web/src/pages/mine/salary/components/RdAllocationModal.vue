<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NModal, NSelect, NInputNumber, NInput, NButton,
  NSpace, useMessage,
} from 'naive-ui'
import {
  updateRdSalaryAllocations,
  type SalaryRecord, type RdSalaryAllocation,
} from '@/api/salary'
import type { RdProject } from '@/api/types'

const props = defineProps<{
  show: boolean
  employee: SalaryRecord | null
  selectedMonth: string
  projects: RdProject[]
  allocations: RdSalaryAllocation[]
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved', allocations: RdSalaryAllocation[]): void
}>()

const message = useMessage()
const saving = ref(false)
const draft = ref<RdSalaryAllocation[]>([])

const projectOptions = computed(() =>
  props.projects
    .filter((p) => p.status === 'active')
    .map((p) => ({ label: `${p.code} ${p.name}`, value: p.id })),
)

watch(() => props.show, (visible) => {
  if (visible && props.employee) {
    const employeeId = props.employee.employee_id
    const existing = props.allocations.filter((a) => a.employeeId === employeeId)
    draft.value = existing.map((item) => ({ ...item }))
    if (!draft.value.length) {
      draft.value = [{
        employeeId,
        projectId: '',
        allocationRate: 1,
        allocationBasis: '日常研发工时表',
      }]
    }
  }
})

function closeModal() {
  emit('update:show', false)
}

function addRow() {
  if (!props.employee) return
  draft.value.push({
    employeeId: props.employee.employee_id,
    projectId: '',
    allocationRate: 0,
    allocationBasis: '',
  })
}

function removeRow(index: number) {
  draft.value.splice(index, 1)
}

async function handleSave() {
  if (!props.employee) return
  const totalRate = draft.value.reduce((sum, row) => sum + Number(row.allocationRate || 0), 0)

  if (draft.value.some((row) => !row.projectId || !row.allocationBasis.trim())) {
    message.warning('每条工时分配都必须选择研发项目并填写工时依据')
    return
  }

  if (totalRate > 1.000001) {
    message.warning('项目工时分配比例合计不能超过 100%')
    return
  }

  saving.value = true
  const employeeId = props.employee.employee_id
  const next = [
    ...props.allocations.filter((item) => item.employeeId !== employeeId),
    ...draft.value,
  ]

  try {
    const response: any = await updateRdSalaryAllocations(props.selectedMonth, next)
    emit('saved', response.allocations || [])
    message.success('研发项目工时分配已保存')
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail?.message || error.response?.data?.detail || '保存工时分配失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`${employee?.employee_name || ''} · 研发项目工时分配`"
    style="width: min(760px, 92vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <p class="modal-hint">
      研发人员的人工成本（工资 + 单位社保公积金）将按此处登记的工时比例分摊计入对应研发项目辅助账，作为高企研发费用加计扣除的核心凭据。
    </p>

    <div class="allocation-rows">
      <div v-for="(allocation, index) in draft" :key="index" class="allocation-row">
        <NSelect
          v-model:value="allocation.projectId"
          :options="projectOptions"
          filterable
          placeholder="选择研发项目"
          style="flex: 2"
        />
        <NInputNumber
          v-model:value="allocation.allocationRate"
          :min="0.01"
          :max="1"
          :step="0.05"
          placeholder="分配比例 0~1"
          style="flex: 1"
        >
          <template #suffix>（{{ ((allocation.allocationRate || 0) * 100).toFixed(0) }}%）</template>
        </NInputNumber>
        <NInput
          v-model:value="allocation.allocationBasis"
          placeholder="工时表或分摊依据（如 研发工时记录）"
          style="flex: 2"
        />
        <NButton text type="error" @click="removeRow(index)">删除</NButton>
      </div>
    </div>

    <NButton dashed block style="margin-top: 10px" @click="addRow">+ 增加分配项目</NButton>

    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">保存分配</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.modal-hint {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
  margin: 0 0 12px;
}
.allocation-rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 380px;
  overflow-y: auto;
}
.allocation-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
