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

interface DraftAllocationRow {
  id?: string
  month?: string
  employeeId: string
  projectId: string
  allocationPercent: number
  allocationBasis: string
}

const message = useMessage()
const saving = ref(false)
const draft = ref<DraftAllocationRow[]>([])

const round2 = (n: number) => Math.round(n * 100) / 100

const totalPercent = computed(() =>
  round2(draft.value.reduce((sum, row) => sum + Number(row.allocationPercent || 0), 0)),
)
const remainingPercent = computed(() => round2(100 - totalPercent.value))

const projectOptions = computed(() =>
  props.projects
    .filter((p) => p.status === 'active')
    .map((p) => ({ label: `${p.code} ${p.name}`, value: p.id })),
)

watch(() => props.show, (visible) => {
  if (visible && props.employee) {
    const employeeId = props.employee.employee_id
    const existing = props.allocations.filter((a) => a.employeeId === employeeId)
    draft.value = existing.map((item) => ({
      id: item.id,
      month: item.month,
      employeeId: item.employeeId,
      projectId: item.projectId,
      allocationPercent: Math.round(Number(item.allocationRate || 0) * 10000) / 100,
      allocationBasis: item.allocationBasis,
    }))
    if (!draft.value.length) {
      draft.value = [{
        employeeId,
        projectId: '',
        allocationPercent: 100,
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
    allocationPercent: remainingPercent.value > 0 ? remainingPercent.value : 0,
    allocationBasis: '',
  })
}

function removeRow(index: number) {
  draft.value.splice(index, 1)
}

async function handleSave() {
  if (!props.employee) return

  if (draft.value.some((row) => !row.projectId || !row.allocationBasis.trim())) {
    message.warning('每条工时分配都必须选择研发项目并填写工时依据')
    return
  }

  if (draft.value.some((row) => Number(row.allocationPercent || 0) <= 0)) {
    message.warning('每条工时分配比例需大于 0%')
    return
  }

  if (totalPercent.value > 100.0001) {
    message.warning('项目工时分配比例合计不能超过 100%')
    return
  }

  saving.value = true
  const employeeId = props.employee.employee_id
  const mapped: RdSalaryAllocation[] = draft.value.map((row) => ({
    ...(row.id ? { id: row.id } : {}),
    ...(row.month ? { month: row.month } : {}),
    employeeId: row.employeeId,
    projectId: row.projectId,
    allocationRate: Math.round(Number(row.allocationPercent || 0) * 100) / 10000,
    allocationBasis: row.allocationBasis,
  }))
  const next = [
    ...props.allocations.filter((item) => item.employeeId !== employeeId),
    ...mapped,
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
    style="width: min(760px, 92vw); border-radius: var(--solo-radius-lg)"
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
          v-model:value="allocation.allocationPercent"
          :min="1"
          :max="100"
          :step="5"
          :precision="2"
          placeholder="分配比例 1~100"
          style="flex: 1"
        >
          <template #suffix>%</template>
        </NInputNumber>
        <NInput
          v-model:value="allocation.allocationBasis"
          placeholder="工时表或分摊依据（如 研发工时记录）"
          style="flex: 2"
        />
        <NButton text type="error" @click="removeRow(index)">删除</NButton>
      </div>
    </div>

    <div class="allocation-summary" :class="{ 'is-over': totalPercent > 100 }">
      <span>已分配合计：<strong>{{ totalPercent }}%</strong></span>
      <span v-if="totalPercent <= 100">剩余未分配：<strong>{{ Math.max(remainingPercent, 0) }}%</strong></span>
      <span v-else class="over-warn">已超出 {{ round2(totalPercent - 100) }}%，请调整至 100% 以内</span>
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
.allocation-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
  font-size: 12px;
  color: var(--solo-text-secondary);
  font-variant-numeric: tabular-nums;
}
.allocation-summary.is-over {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
}
.over-warn {
  font-weight: 600;
}
</style>
