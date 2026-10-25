<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { NCard, NTag, NInput, NSelect, NInputNumber, NButton, NDivider, NAlert, useMessage } from 'naive-ui'
import { moneyPresentation } from '@/utils/accounting'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { joinPayer, splitPayer, type PayerKind } from '@/utils/fundsChannel'
import { usePayeeEmployees } from '@/composables/usePayeeEmployees'
import type { RecordTextResponse } from '@/api/types'
import type { RdExpenseType, RdExpenseTypeOption, RdProject } from '@/api/types'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'

type ClassificationResult = Pick<RecordTextResponse, 'id' | 'direction' | 'amount' | 'title' | 'category' | 'confidence' | 'explanation' | 'accountCode' | 'accountName' | 'vatRate' | 'vatAmount' | 'taxDeductible' | 'riskTags' | 'payer' | 'askFollowUp' | 'followUpOptions'>

const props = defineProps<{
  result: ClassificationResult
  loading?: boolean
}>()

const emit = defineEmits<{
  confirm: [data: Record<string, unknown>]
  cancel: []
  retry: []
}>()

const message = useMessage()
const editing = ref(false)

// Editable fields
const editAmount = ref(props.result.amount)
const editTitle = ref(props.result.title)
const editCategory = ref(props.result.category)
const editDirection = ref(props.result.direction)
// payer 会写入 employee_personal:<姓名>，比接口响应里的两值枚举更宽，显式放宽为 string
const editPayer = ref<string>(props.result.payer || 'company_account')
const rdProjectId = ref<string | null>(null)
const rdExpenseType = ref<RdExpenseType | null>(null)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])
const rdLoading = ref(false)

watch(() => props.result, (r) => {
  editAmount.value = r.amount
  editTitle.value = r.title
  editCategory.value = r.category
  editDirection.value = r.direction
  editPayer.value = r.payer || 'company_account'
})

const directionOptions = [
  { label: '收入', value: 'income' },
  { label: '支出', value: 'expense' },
  { label: '资本与往来', value: 'owner_transfer' },
]

const payerOptions = [
  { label: '公司账户', value: 'company_account' },
  { label: '股东个人', value: 'boss_personal' },
  { label: '员工垫付', value: 'employee_personal' },
]

// 员工垫付必须选到人：editPayer 仍是标准串 employee_personal:<姓名>，
// 下拉拆成「类型 + 姓名」两段，确认时原样提交。
const payerEmployees = usePayeeEmployees()
const payerEmployeesLoading = payerEmployees.loading
const editPayerKind = computed<PayerKind>({
  get: () => splitPayer(editPayer.value).kind,
  set: (kind) => {
    const pendingName = kind === 'employee_personal' ? splitPayer(editPayer.value).employeeName : ''
    editPayer.value = joinPayer(kind, pendingName)
    if (kind === 'employee_personal') void payerEmployees.ensure()
  },
})
const editPayerEmployee = computed<string>({
  get: () => splitPayer(editPayer.value).employeeName,
  set: (name) => {
    editPayer.value = joinPayer('employee_personal', name || '')
  },
})
const editPayerEmployeeOptions = computed(() => payerEmployees.options(editPayerEmployee.value))

function payerText() {
  const { kind, employeeName } = splitPayer(editPayer.value)
  if (kind === 'employee_personal') return employeeName ? `员工垫付 · ${employeeName}` : '员工垫付'
  return kind === 'boss_personal' ? '股东个人' : '公司账户'
}

const directionLabel = computed(() => {
  const presentation = getEntryNaturePresentation(editDirection.value, editCategory.value)
  return { ...presentation, label: presentation.detailLabel }
})

// 金额展示：冲退类负金额按资金实际流向显示（+¥x 绿 / -¥x 红），色调跟随展示口径
const moneyDisplay = computed(() => {
  const presentation = moneyPresentation(editDirection.value, editCategory.value, editAmount.value)
  const toneDir = presentation.tone === 'neutral' ? editDirection.value : presentation.tone
  return { text: presentation.text, color: getEntryNaturePresentation(toneDir, editCategory.value).color }
})

const confidencePercent = computed(() => Math.round(props.result.confidence * 100))
const isRdExpense = computed(() => editCategory.value === '研发支出-费用化支出')
const rdProjectOptions = computed(() => rdProjects.value
  .filter((project) => project.status === 'active')
  .map((project) => ({ label: `${project.code} ${project.name}`, value: project.id })))
const rdExpenseTypeOptions = computed(() => rdExpenseTypes.value.map((item) => ({ label: item.label, value: item.value })))
const rdReady = computed(() => !isRdExpense.value || Boolean(rdProjectId.value && rdExpenseType.value))

onMounted(async () => {
  rdLoading.value = true
  try {
    const [projects, types] = await Promise.all([getRdProjects(), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypes.value = types.items
  } finally {
    rdLoading.value = false
  }
})

function handleConfirm() {
  // 员工垫付没选人，凭证只会挂一个不具名的「其他应付款-员工往来」
  if (editPayerKind.value === 'employee_personal' && !editPayerEmployee.value.trim()) {
    message.warning('请选择垫付的员工，或改为股东个人/公司账户')
    return
  }
  emit('confirm', {
    id: props.result.id,
    direction: editDirection.value,
    amount: editAmount.value,
    title: editTitle.value,
    category: editCategory.value,
    payer: editPayer.value,
    accountCode: props.result.accountCode,
    accountName: props.result.accountName,
    occurDate: new Date().toISOString().split('T')[0],
    sourceType: 'web',
    ...(isRdExpense.value ? {
      rdProjectId: rdProjectId.value,
      rdExpenseType: rdExpenseType.value,
      rdOriginalAmount: editAmount.value,
      rdAllocationMethod: 'direct',
    } : {}),
  })
}
</script>

<template>
  <NCard :bordered="true" :loading="loading" class="ai-card">
    <div class="card-header">
      <div class="header-left">
        <span class="badge" :style="{ background: directionLabel.background, color: directionLabel.color }">
          {{ directionLabel.label }}
        </span>
        <NTag :type="confidencePercent >= 80 ? 'success' : confidencePercent >= 50 ? 'warning' : 'error'" size="small" :bordered="false">
          {{ confidencePercent }}% 置信度
        </NTag>
      </div>
      <NButton text size="small" @click="editing = !editing">
        {{ editing ? '完成编辑' : '编辑' }}
      </NButton>
    </div>

    <div class="card-body">
      <template v-if="!editing">
        <div class="result-row main-amount">
          <span class="label">金额</span>
          <span class="amount" :style="{ color: moneyDisplay.color }">
            {{ moneyDisplay.text }}
          </span>
        </div>
        <div class="result-row">
          <span class="label">摘要</span>
          <span class="value">{{ editTitle }}</span>
        </div>
        <div class="result-row">
          <span class="label">分类</span>
          <span class="value">{{ editCategory }}</span>
        </div>
        <div class="result-row">
          <span class="label">付款方式</span>
          <span class="value">{{ payerText() }}</span>
        </div>
        <NDivider v-if="result.explanation" style="margin: 8px 0;" />
        <div v-if="result.explanation" class="result-row explanation">
          <span class="label">AI 说明</span>
          <span class="value text-muted">{{ result.explanation }}</span>
        </div>
        <div v-if="result.riskTags?.length" class="result-row">
          <span class="label">风险标签</span>
          <div class="tags-group">
            <NTag v-for="tag in result.riskTags" :key="tag" size="tiny" type="warning" :bordered="false">{{ tag }}</NTag>
          </div>
        </div>
        <div v-if="isRdExpense" class="rd-summary">
          <div class="result-row">
            <span class="label">研发项目</span>
            <span class="value">{{ rdProjectOptions.find((item) => item.value === rdProjectId)?.label || '待选择' }}</span>
          </div>
          <div class="result-row">
            <span class="label">费用类别</span>
            <span class="value">{{ rdExpenseTypeOptions.find((item) => item.value === rdExpenseType)?.label || '待选择' }}</span>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="edit-form">
          <div class="form-field">
            <label>金额</label>
            <NInputNumber v-model:value="editAmount" :min="0" :step="0.01" placeholder="0.00" style="width: 100%;" />
          </div>
          <div class="form-field">
            <label>摘要</label>
            <NInput v-model:value="editTitle" placeholder="交易摘要" />
          </div>
          <div class="form-row">
            <div class="form-field">
              <label>方向</label>
              <NSelect v-model:value="editDirection" :options="directionOptions" />
            </div>
            <div class="form-field">
              <label>付款方</label>
              <NSelect v-model:value="editPayerKind" :options="payerOptions" />
            </div>
            <div v-if="editPayerKind === 'employee_personal'" class="form-field">
              <label>垫付员工</label>
              <NSelect
                v-model:value="editPayerEmployee"
                :options="editPayerEmployeeOptions"
                :loading="payerEmployeesLoading"
                filterable
                clearable
                placeholder="选择垫付的员工"
              />
            </div>
          </div>
          <div class="form-field">
            <label>分类</label>
            <NInput v-model:value="editCategory" placeholder="经营分类" />
          </div>
        </div>
      </template>
      <div v-if="isRdExpense" class="rd-fields">
        <NAlert v-if="!rdProjectOptions.length && !rdLoading" type="warning" :bordered="false">
          暂无启用中的研发项目，请先到“研发费用”建立并启用项目。
        </NAlert>
        <div class="form-row">
          <div class="form-field">
            <label>研发项目</label>
            <NSelect v-model:value="rdProjectId" :options="rdProjectOptions" :loading="rdLoading" filterable placeholder="请选择研发项目" />
          </div>
          <div class="form-field">
            <label>费用类别</label>
            <NSelect v-model:value="rdExpenseType" :options="rdExpenseTypeOptions" :loading="rdLoading" placeholder="请选择费用类别" />
          </div>
        </div>
      </div>
    </div>

    <template #action>
      <div class="card-actions">
        <NButton size="small" @click="emit('cancel')">取消</NButton>
        <NButton size="small" @click="emit('retry')" v-if="confidencePercent < 70">重新识别</NButton>
        <NButton type="primary" size="small" :disabled="!rdReady" @click="handleConfirm">确认入账</NButton>
      </div>
    </template>
  </NCard>
</template>

<style scoped>
.ai-card {
  border-radius: var(--solo-radius-lg);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.badge {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 999px;
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.result-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.result-row .label {
  font-size: 12px;
  color: var(--solo-text-muted);
  width: 64px;
  flex-shrink: 0;
}
.result-row .value {
  font-size: 14px;
  color: var(--solo-text-primary);
}
.result-row .amount {
  font-size: 24px;
  font-weight: 700;
}
.main-amount {
  padding-bottom: 8px;
  border-bottom: 1px solid var(--solo-border-light);
}
.tags-group {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
}
.edit-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form-field label {
  display: block;
  font-size: 12px;
  color: var(--solo-text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}
.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 0;
}
.rd-fields,
.rd-summary {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
</style>
