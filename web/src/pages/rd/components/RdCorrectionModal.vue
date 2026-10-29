<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert, NButton, NForm, NFormItem, NInput, NInputNumber, NModal, NSelect, NSpace, useMessage,
} from 'naive-ui'
import dayjs from 'dayjs'
import { correctRdUnallocated } from '@/api/rd'
import type { RdCorrectionAllocation, RdUnallocatedItem } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  show: boolean
  target: RdUnallocatedItem | null
  projects: RdProject[]
  expenseTypes: RdExpenseTypeOption[]
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'success'): void
}>()

const message = useMessage()
const correcting = ref(false)
const correctionDate = ref(dayjs().format('YYYY-MM-DD'))
const correctionReason = ref('补充研发项目、费用类别及分摊依据')
const allocations = ref<RdCorrectionAllocation[]>([])

const activeProjectOptions = computed(() =>
  props.projects
    .filter((p) => ['active', 'completed'].includes(p.status))
    .map((p) => ({ label: `${p.code} · ${p.name}`, value: p.id })),
)

const expenseTypeOptions = computed(() =>
  props.expenseTypes.map((item) => ({ label: item.label, value: item.value })),
)

const targetAmount = computed(() => Math.abs(Number(props.target?.amount || 0)))

const totalAllocated = computed(() =>
  allocations.value.reduce((sum, item) => sum + Number(item.amount || 0), 0),
)

const amountDiff = computed(() =>
  Number((totalAllocated.value - targetAmount.value).toFixed(2)),
)

const isAmountBalanced = computed(() => Math.abs(amountDiff.value) < 0.001)

watch(
  () => props.show,
  (visible) => {
    if (!visible || !props.target) return
    correctionDate.value = dayjs().format('YYYY-MM-DD')
    correctionReason.value = '补充研发项目、费用类别及分摊依据'
    allocations.value = [
      {
        projectId: activeProjectOptions.value[0]?.value || '',
        expenseType: 'direct_input',
        amount: targetAmount.value,
        allocationMethod: 'direct',
        allocationBasis: '',
        notes: '',
      },
    ]
  },
  { immediate: true },
)

function addAllocation() {
  allocations.value.push({
    projectId: '',
    expenseType: 'direct_input',
    amount: 0,
    allocationMethod: 'other',
    allocationBasis: '',
    notes: '',
  })
}

function removeAllocation(index: number) {
  if (allocations.value.length > 1) {
    allocations.value.splice(index, 1)
  }
}

async function handleSubmit() {
  if (!props.target || !correctionReason.value.trim()) return

  if (allocations.value.some((item) => !item.projectId || !item.expenseType || Number(item.amount) <= 0)) {
    message.warning('请完整填写每个项目的费用类别和金额（必须大于 0）')
    return
  }

  if (allocations.value.length > 1 && allocations.value.some((item) => !item.allocationBasis?.trim())) {
    message.warning('多项目拆分必须填写每条分摊依据（如工时单、用量记录、领料单等）')
    return
  }

  if (!isAmountBalanced.value) {
    message.warning(`分配合计（${formatMoney(totalAllocated.value)}）必须等于待归集金额（${formatMoney(targetAmount.value)}）`)
    return
  }

  correcting.value = true
  try {
    await correctRdUnallocated(props.target.entryId, {
      correctionDate: correctionDate.value,
      reason: correctionReason.value.trim(),
      allocations: allocations.value,
    })
    message.success(
      props.target.status === 'posted'
        ? '已生成冲销凭证和正确的研发归集凭证，保留完整审计痕迹'
        : '研发归集明细已补充成功',
    )
    emit('update:show', false)
    emit('success')
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '研发归集更正失败')
  } finally {
    correcting.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="补充研发归集与分摊"
    style="width: min(840px, 96vw)"
    @update:show="emit('update:show', $event)"
  >
    <NAlert
      v-if="target"
      :type="target.status === 'posted' ? 'warning' : 'info'"
      :bordered="false"
      class="mb-4"
    >
      {{
        target.status === 'posted'
          ? '该凭证已过账。提交后将在更正日期自动生成红字冲销凭证与新的规范研发归集凭证，遵循权威会计审计准则，不会擅自改写原凭证。'
          : '该凭证处于草稿状态，提交后将直接规范更新草稿分录并写入审计留痕。'
      }}
    </NAlert>

    <NForm label-placement="top">
      <div class="form-grid">
        <NFormItem label="待归集金额">
          <NInput :value="formatMoney(targetAmount)" disabled />
        </NFormItem>
        <NFormItem label="更正生效日期" required>
          <NInput v-model:value="correctionDate" placeholder="YYYY-MM-DD" />
        </NFormItem>
      </div>

      <NFormItem label="更正原因说明" required>
        <NInput v-model:value="correctionReason" placeholder="例如：补充研发项目、费用类别及工时分摊依据" />
      </NFormItem>

      <div class="allocation-section-header">
        <div class="header-left">
          <strong>项目分摊明细</strong>
          <span class="sub-hint">若费用由多个研发项目共用，可拆分为多行并填写分摊依据</span>
        </div>
        <NButton size="small" secondary @click="addAllocation">
          <template #icon><i class="ri-add-line" /></template>
          增加项目
        </NButton>
      </div>

      <div class="allocation-list">
        <div v-for="(allocation, index) in allocations" :key="index" class="allocation-card">
          <div class="allocation-grid">
            <NFormItem label="研发项目" :show-feedback="false">
              <NSelect
                v-model:value="allocation.projectId"
                :options="activeProjectOptions"
                placeholder="请选择项目"
                filterable
              />
            </NFormItem>
            <NFormItem label="费用类别" :show-feedback="false">
              <NSelect
                v-model:value="allocation.expenseType"
                :options="expenseTypeOptions"
                placeholder="请选择类别"
              />
            </NFormItem>
            <NFormItem label="分摊金额" :show-feedback="false">
              <NInputNumber
                v-model:value="allocation.amount"
                :min="0.01"
                :precision="2"
                :show-button="false"
                placeholder="金额"
                style="width: 100%;"
              />
            </NFormItem>
            <NFormItem label="分摊方法" :show-feedback="false">
              <NSelect
                v-model:value="allocation.allocationMethod"
                :options="[
                  { label: '直接归集', value: 'direct' },
                  { label: '工时工时比', value: 'timesheet' },
                  { label: '使用量/台时', value: 'usage' },
                  { label: '面积分摊', value: 'area' },
                  { label: '产出数量', value: 'quantity' },
                  { label: '其他合理方法', value: 'other' },
                ]"
              />
            </NFormItem>
            <NFormItem label="分摊依据说明" :show-feedback="false">
              <NInput
                v-model:value="allocation.allocationBasis"
                placeholder="合同、工时记录、领料单等"
              />
            </NFormItem>
            <div class="action-col">
              <NButton
                quaternary
                type="error"
                :disabled="allocations.length === 1"
                @click="removeAllocation(index)"
              >
                删除
              </NButton>
            </div>
          </div>
        </div>
      </div>

      <div class="balance-summary" :class="{ 'is-balanced': isAmountBalanced, 'is-mismatched': !isAmountBalanced }">
        <div class="summary-line">
          <span>分配合计：<strong>{{ formatMoney(totalAllocated) }}</strong></span>
          <span class="separator">/</span>
          <span>目标金额：<strong>{{ formatMoney(targetAmount) }}</strong></span>
          <span v-if="!isAmountBalanced" class="diff-tag">
            （差异 {{ formatMoney(Math.abs(amountDiff)) }}，需调平）
          </span>
          <span v-else class="balanced-tag">
            <i class="ri-checkbox-circle-fill" /> 金额已配平
          </span>
        </div>
      </div>

      <NSpace justify="end" style="margin-top: 16px;">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton
          type="primary"
          :loading="correcting"
          :disabled="!isAmountBalanced"
          @click="handleSubmit"
        >
          确认更正并入账
        </NButton>
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
.allocation-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 16px 0 10px 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sub-hint {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.allocation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.allocation-card {
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  padding: 10px 14px;
}
.allocation-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.9fr 0.9fr 1.3fr auto;
  gap: 10px;
  align-items: flex-end;
}
.action-col {
  padding-bottom: 2px;
}
.balance-summary {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
  display: flex;
  justify-content: flex-end;
}
.balance-summary.is-balanced {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.05);
  color: var(--solo-income);
}
.balance-summary.is-mismatched {
  border-color: rgba(239, 68, 68, 0.3);
  background: rgba(239, 68, 68, 0.05);
  color: var(--solo-expense);
}
.summary-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.separator {
  color: var(--solo-text-muted);
}
.diff-tag {
  font-weight: 600;
}
.balanced-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}
@media (max-width: 760px) {
  .allocation-grid {
    grid-template-columns: 1fr;
  }
}
</style>
