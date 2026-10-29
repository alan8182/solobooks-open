<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NModal, NForm, NFormItem, NInputNumber, NSpin,
  NSpace, NButton, useMessage,
} from 'naive-ui'
import { previewSalaryTax, type SalaryRecord, type TaxPreview } from '@/api/salary'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  show: boolean
  target: SalaryRecord | null
  selectedMonth: string
  initialSpecialAdditional: number
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'apply', taxDue: number): void
}>()

const message = useMessage()
const loading = ref(false)
const taxPreview = ref<TaxPreview | null>(null)
const taxForm = ref({ specialAdditional: 0, otherDeduction: 0 })

const currentGross = computed(() => {
  const row = props.target
  if (!row) return 0
  return Math.max(
    Number(row.base_salary || 0) + Number(row.bonus || 0)
      + Number(row.allowance || 0) - Number(row.deduction || 0),
    0,
  )
})

function ratesPercent(value: number) {
  return `${(value * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

async function runTaxPreview() {
  const row = props.target
  if (!row) return
  loading.value = true
  try {
    const data: any = await previewSalaryTax({
      month: props.selectedMonth,
      employeeId: row.employee_id,
      gross: currentGross.value,
      withheld: Number(row.personal_social_security || 0),
      specialAdditional: taxForm.value.specialAdditional,
      otherDeduction: taxForm.value.otherDeduction,
    })
    taxPreview.value = data
  } catch (error: any) {
    taxPreview.value = null
    message.error(error.response?.data?.detail || '个税试算失败')
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (visible) => {
  if (visible && props.target) {
    taxForm.value = {
      specialAdditional: Number(props.initialSpecialAdditional || 0),
      otherDeduction: 0,
    }
    taxPreview.value = null
    runTaxPreview()
  }
})

function closeModal() {
  emit('update:show', false)
}

function handleApply() {
  if (!taxPreview.value) return
  const taxDue = Number(taxPreview.value.taxDue || 0)
  emit('apply', taxDue)
  closeModal()
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`${target?.employee_name || ''} · ${selectedMonth} 个税累计预扣试算`"
    style="width: min(640px, 94vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <p class="modal-hint">
      按国家累计预扣法试算：累计收入 − 累计减除费用（5000元/月）− 累计个人社保公积金 − 累计专项附加扣除。试算结果仅供参考，申报纳税以自然人税收管理系统（个税扣缴端）申报数据为准。
    </p>

    <NForm label-placement="left" label-width="110" style="margin-top: 12px">
      <NFormItem label="本期应发工资">
        <span class="calc-static">{{ formatMoney(currentGross) }}</span>
      </NFormItem>
      <NFormItem label="本期个人社保">
        <span class="calc-static">{{ formatMoney(Number(target?.personal_social_security || 0)) }}</span>
      </NFormItem>
      <NFormItem label="专项附加/月">
        <NInputNumber v-model:value="taxForm.specialAdditional" :min="0" :precision="2" :step="500" style="width: 100%" @update:value="runTaxPreview">
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
      <NFormItem label="其他扣除/月">
        <NInputNumber v-model:value="taxForm.otherDeduction" :min="0" :precision="2" :step="200" style="width: 100%" @update:value="runTaxPreview">
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
    </NForm>

    <NSpin :show="loading">
      <div v-if="taxPreview" class="tax-breakdown">
        <p v-if="taxPreview.hireDateMissing" class="tax-warn">
          <i class="ri-error-warning-line" />
          该员工未设置入职日期，累计减除费用按 {{ taxPreview.startMonth }} 起算（{{ taxPreview.months }} 个月）。若实际入职更早，此处测算可能偏低。
        </p>
        <div class="tax-line">
          <span>累计收入（{{ taxPreview.months }} 个月，含本期）</span>
          <strong>{{ formatMoney(Number(taxPreview.cumulativeIncome)) }}</strong>
        </div>
        <div class="tax-line">
          <span>累计减除费用（5000 × {{ taxPreview.months }}）</span>
          <strong>-{{ formatMoney(Number(taxPreview.cumulativeBasicDeduction)) }}</strong>
        </div>
        <div class="tax-line">
          <span>累计个人社保公积金</span>
          <strong>-{{ formatMoney(Number(taxPreview.cumulativeSpecialDeduction)) }}</strong>
        </div>
        <div class="tax-line">
          <span>累计专项附加扣除</span>
          <strong>-{{ formatMoney(Number(taxPreview.cumulativeAdditionalDeduction)) }}</strong>
        </div>
        <div v-if="Number(taxPreview.cumulativeOtherDeduction) > 0" class="tax-line">
          <span>累计其他扣除</span>
          <strong>-{{ formatMoney(Number(taxPreview.cumulativeOtherDeduction)) }}</strong>
        </div>
        <div class="tax-line emphasis">
          <span>累计应纳税所得额</span>
          <strong>{{ formatMoney(Number(taxPreview.taxableIncome)) }}</strong>
        </div>
        <div class="tax-line">
          <span>适用税率 / 速算扣除数</span>
          <strong>
            {{ ratesPercent(Number(taxPreview.taxRate)) }} /
            {{ formatMoney(Number(taxPreview.quickDeduction)) }}
          </strong>
        </div>
        <div class="tax-line">
          <span>累计应纳税额</span>
          <strong>{{ formatMoney(Number(taxPreview.cumulativeTax)) }}</strong>
        </div>
        <div class="tax-line">
          <span>本年已预缴（前几个月工资表已扣）</span>
          <strong>{{ formatMoney(Number(taxPreview.prepaidTax)) }}</strong>
        </div>
        <div class="tax-line total">
          <span>本期应扣个税</span>
          <strong>{{ formatMoney(Number(taxPreview.taxDue)) }}</strong>
        </div>
      </div>
      <p v-else-if="!loading" class="modal-hint muted">暂无法试算，请检查该员工是否已在本月工资表中。</p>
    </NSpin>

    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">关闭</NButton>
        <NButton :loading="loading" @click="runTaxPreview">重新试算</NButton>
        <NButton type="primary" :disabled="!taxPreview" @click="handleApply">填入本期个税</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.modal-hint {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
  margin: 0;
}
.modal-hint.muted {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  text-align: center;
  padding: 16px 0;
}
.calc-static {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.tax-breakdown {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--solo-bg-hover, rgba(0, 0, 0, 0.02));
  border-radius: var(--solo-radius-md, 8px);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tax-warn {
  font-size: 11.5px;
  color: var(--solo-warning);
  line-height: 1.4;
  margin: 0 0 6px;
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.tax-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
.tax-line.emphasis {
  padding: 6px 0;
  border-top: 1px dashed var(--solo-border-light);
  border-bottom: 1px dashed var(--solo-border-light);
  font-weight: 600;
  color: var(--solo-text-primary);
}
.tax-line.total {
  padding-top: 6px;
  border-top: 1px solid var(--solo-border-light);
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-primary);
}
</style>
