<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NModal, NForm, NFormItem, NInput, NSelect, NDatePicker,
  NSwitch, NInputNumber, NSpace, NButton, useMessage,
} from 'naive-ui'
import { addEmployee, updateEmployee } from '@/api/salary'
import type { Employee, PayrollRates } from '@/api/salary'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  show: boolean
  employee: Employee | null
  departmentOptions: Array<{ label: string; value: string }>
  selectedMonth?: string
  isDraftSheet?: boolean
  payrollRates?: PayrollRates | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved'): void
}>()

const message = useMessage()
const saving = ref(false)

const isEditing = computed(() => Boolean(props.employee?.id))
const showAdvanced = ref(false)
const ssBaseTouched = ref(false)
const syncDraftRecalc = ref(true)

const round2 = (n: number) => Math.round(n * 100) / 100

const form = ref({
  name: '',
  department: '行政部',
  base_salary: 6000,
  social_security_base: 6000,
  housing_fund_base: null as number | null,
  is_owner: false,
  hire_date: null as string | null,
  special_additional_deduction: 0,
})

const costPreview = computed(() => {
  const r = props.payrollRates?.rates
  if (!r) return null
  const base = Math.max(Number(form.value.base_salary || 0), 0)
  const ssBase = Math.max(Number(form.value.social_security_base || 0), 0)
  const hfBase = form.value.housing_fund_base != null
    ? Math.max(Number(form.value.housing_fund_base || 0), 0)
    : ssBase
  const personalTotal = round2(
    round2(ssBase * Number(r.socialPersonal || 0))
    + round2(hfBase * Number(r.housingPersonal || 0)),
  )
  const companyTotal = round2(
    round2(ssBase * Number(r.socialCompany || 0))
    + round2(hfBase * Number(r.housingCompany || 0)),
  )
  return {
    personalTotal,
    preTaxNet: round2(base - personalTotal),
    totalCost: round2(base + companyTotal),
  }
})

watch(() => props.show, (visible) => {
  if (visible) {
    syncDraftRecalc.value = true
    if (props.employee) {
      const hasAdv = Boolean(
        Number(props.employee.special_additional_deduction || 0) > 0 ||
        props.employee.is_owner,
      )
      showAdvanced.value = hasAdv
      ssBaseTouched.value = true
      form.value = {
        name: props.employee.name,
        department: props.employee.department,
        base_salary: Number(props.employee.base_salary || 0),
        social_security_base: Number(props.employee.social_security_base || 0),
        housing_fund_base: props.employee.housing_fund_base != null
          ? Number(props.employee.housing_fund_base)
          : null,
        is_owner: Boolean(props.employee.is_owner),
        hire_date: props.employee.hire_date || null,
        special_additional_deduction: Number(props.employee.special_additional_deduction || 0),
      }
    } else {
      showAdvanced.value = false
      ssBaseTouched.value = false
      form.value = {
        name: '',
        department: props.departmentOptions[0]?.value || '行政部',
        base_salary: 6000,
        social_security_base: 6000,
        housing_fund_base: null,
        is_owner: false,
        hire_date: null,
        special_additional_deduction: 0,
      }
    }
  }
})

function onBaseSalaryChange(val: number | null) {
  const next = Number(val || 0)
  form.value.base_salary = next
  if (!ssBaseTouched.value) {
    form.value.social_security_base = next
  }
}

function setSsPreset(val: number) {
  ssBaseTouched.value = true
  form.value.social_security_base = val
}

function setHfPreset(val: number | null) {
  form.value.housing_fund_base = val
}

function closeModal() {
  emit('update:show', false)
}

async function handleSave() {
  if (!form.value.name.trim()) {
    message.warning('请输入员工姓名')
    return
  }
  saving.value = true
  try {
    const payload = {
      ...form.value,
      hire_date: form.value.hire_date || null,
    }
    if (isEditing.value && props.employee?.id) {
      await updateEmployee(
        props.employee.id,
        payload,
        props.isDraftSheet && props.selectedMonth
          ? { month: props.selectedMonth, recalculate: syncDraftRecalc.value }
          : undefined,
      )
      message.success(
        props.isDraftSheet && syncDraftRecalc.value && props.selectedMonth
          ? `员工信息已更新，已同步重算 ${props.selectedMonth} 草稿社保公积金`
          : '员工信息已更新',
      )
    } else {
      await addEmployee(payload)
      message.success('新增员工成功')
    }
    emit('saved')
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存员工失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="isEditing ? '编辑员工信息' : '录入新员工'"
    style="width: min(440px, 94vw); border-radius: var(--solo-radius-lg)"
    @update:show="closeModal"
  >
    <NForm label-placement="left" label-width="84" style="margin-top: 10px">
      <NFormItem label="员工姓名">
        <NInput v-model:value="form.name" placeholder="请输入员工姓名" />
      </NFormItem>
      <NFormItem
        label="所属部门"
        feedback="可直接输入新部门名（如 总经办）后回车，保存员工时自动加入部门列表"
        feedback-style="font-size: 12px; opacity: 0.65; line-height: 1.5;"
      >
        <NSelect
          v-model:value="form.department"
          :options="departmentOptions"
          filterable
          tag
          placeholder="选择或直接输入部门名"
          :consistent-menu-width="false"
        />
      </NFormItem>
      <NFormItem
        label="入职日期"
        feedback="影响个税累计减除费用的起算月份；不填按首个工资月计算"
        feedback-style="font-size: 12px; opacity: 0.65; line-height: 1.5;"
      >
        <NDatePicker
          v-model:formatted-value="form.hire_date"
          value-format="yyyy-MM-dd"
          type="date"
          clearable
          size="medium"
          style="width: 100%"
          placeholder="不填则默认当月在职"
        />
      </NFormItem>
      <NFormItem label="基本工资">
        <NInputNumber
          :value="form.base_salary"
          :min="0"
          :precision="2"
          :step="500"
          style="width: 100%"
          @update:value="onBaseSalaryChange"
        >
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
      <NFormItem label="社保基数">
        <div class="ss-field-wrap">
          <NInputNumber
            v-model:value="form.social_security_base"
            :min="0"
            :precision="2"
            :step="500"
            style="width: 100%"
            @update:value="ssBaseTouched = true"
          >
            <template #prefix>¥</template>
          </NInputNumber>
          <div class="ss-presets">
            <button type="button" class="preset-chip" :class="{ active: form.social_security_base === form.base_salary }" @click="setSsPreset(form.base_salary)">按全额工资</button>
            <button type="button" class="preset-chip" :class="{ active: form.social_security_base === 0 }" @click="setSsPreset(0)">不交社保 (0)</button>
          </div>
          <p class="ss-hint">最低缴费基数各地不同，按当地社保局公布的标准填写。</p>
        </div>
      </NFormItem>
      <NFormItem label="公积金基数">
        <div class="ss-field-wrap">
          <NInputNumber
            v-model:value="form.housing_fund_base"
            :min="0"
            :precision="2"
            :step="500"
            clearable
            style="width: 100%"
            :placeholder="`留空则同社保基数（¥${formatMoney(form.social_security_base)}）`"
          >
            <template #prefix>¥</template>
          </NInputNumber>
          <div class="ss-presets">
            <button type="button" class="preset-chip" :class="{ active: form.housing_fund_base === null }" @click="setHfPreset(null)">同社保基数</button>
            <button type="button" class="preset-chip" :class="{ active: form.housing_fund_base === form.base_salary }" @click="setHfPreset(form.base_salary)">按全额工资</button>
            <button type="button" class="preset-chip" :class="{ active: form.housing_fund_base === 0 }" @click="setHfPreset(0)">不交公积金 (0)</button>
          </div>
          <p class="ss-hint">公积金下限通常按当地最低工资标准，可与社保基数不同；留空默认跟随社保基数。</p>
        </div>
      </NFormItem>

      <div v-if="isEditing && isDraftSheet && selectedMonth" class="draft-sync-row">
        <NSwitch v-model:value="syncDraftRecalc" size="small" />
        <span class="draft-sync-hint">
          保存后按新基数重算 {{ selectedMonth }} 草稿工资表的社保与公积金（仅更新该员工）
        </span>
      </div>

      <div v-if="costPreview" class="cost-preview-bar">
        按当前比例预估：个人五险一金 {{ formatMoney(costPreview.personalTotal) }} · 税前实发 {{ formatMoney(costPreview.preTaxNet) }} · 企业总成本 {{ formatMoney(costPreview.totalCost) }}
      </div>

      <div class="advanced-toggle-row">
        <button type="button" class="advanced-toggle-btn" @click="showAdvanced = !showAdvanced">
          <i :class="showAdvanced ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          <span>{{ showAdvanced ? '收起选填项' : '更多选填项（个税专项附加 / 业主标记）' }}</span>
        </button>
      </div>

      <template v-if="showAdvanced">
        <NFormItem
          label="专项附加"
          feedback="就是「专项附加扣除」，员工在「个人所得税 App」自己填报（子女教育、房贷或房租、赡养老人等）。这里填每月合计，只用来估算要代扣多少工资个税；没有就留 0，实际申报以税务系统自动带出的为准。"
          feedback-style="font-size: 12px; opacity: 0.65; line-height: 1.5;"
        >
          <NInputNumber
            v-model:value="form.special_additional_deduction"
            :min="0"
            :precision="2"
            :step="500"
            style="width: 100%"
          >
            <template #prefix>¥</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="业主标记">
          <div class="owner-flag-row">
            <NSwitch v-model:value="form.is_owner" size="small" />
            <span class="owner-flag-hint">
              个体户/个独业主本人的工资不得税前扣除，汇算清缴时自动调增
            </span>
          </div>
        </NFormItem>
      </template>
    </NForm>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.ss-field-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ss-presets {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.preset-chip {
  border: 1px solid var(--solo-border-light);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11.5px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.preset-chip:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}
.preset-chip.active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}
.ss-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--solo-text-muted);
}
.advanced-toggle-row {
  margin: 4px 0 12px;
}
.advanced-toggle-btn {
  border: none;
  background: transparent;
  color: var(--solo-text-secondary);
  font-size: 12px;
  padding: 4px 0;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.advanced-toggle-btn:hover {
  color: var(--solo-primary);
}
.owner-flag-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.owner-flag-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.35;
}
.cost-preview-bar {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-text-primary);
  font-size: 12px;
  line-height: 1.45;
  font-variant-numeric: tabular-nums;
}
.draft-sync-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  padding: 8px 10px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius-sm);
}
.draft-sync-hint {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}
</style>
