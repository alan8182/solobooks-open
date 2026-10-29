<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  NModal, NSpin, NForm, NFormItem, NInputNumber, NSwitch,
  NSpace, NButton, useMessage,
} from 'naive-ui'
import { getPayrollRates, updatePayrollRates, type PayrollRates } from '@/api/salary'

const props = defineProps<{
  show: boolean
  selectedMonth: string
  payrollRates: PayrollRates | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved', rates: PayrollRates, recalculate: boolean): void
}>()

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const localRates = ref<PayrollRates | null>(null)
const ratesRecalculate = ref(true)

const form = ref({
  socialPersonal: 10.5,
  socialCompany: 25,
  housingPersonal: 8,
  housingCompany: 8,
})

function ratesPercent(value: number) {
  return `${(value * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

watch(() => props.show, async (visible) => {
  if (visible) {
    loading.value = true
    try {
      const data: any = await getPayrollRates()
      localRates.value = data
      form.value = {
        socialPersonal: Number(data.rates.socialPersonal) * 100,
        socialCompany: Number(data.rates.socialCompany) * 100,
        housingPersonal: Number(data.rates.housingPersonal) * 100,
        housingCompany: Number(data.rates.housingCompany) * 100,
      }
    } catch {
      message.error('加载社保公积金比例失败')
    } finally {
      loading.value = false
    }
  }
})

function closeModal() {
  emit('update:show', false)
}

async function handleSave() {
  const values = Object.values(form.value)
  if (values.some((v) => Number.isNaN(v) || v < 0 || v > 100)) {
    message.warning('比例需落在 0~100 之间（如 8% 请填 8）')
    return
  }

  saving.value = true
  try {
    const result: any = await updatePayrollRates(
      {
        socialPersonal: form.value.socialPersonal / 100,
        socialCompany: form.value.socialCompany / 100,
        housingPersonal: form.value.housingPersonal / 100,
        housingCompany: form.value.housingCompany / 100,
      },
      { month: props.selectedMonth, recalculate: ratesRecalculate.value },
    )
    localRates.value = result
    emit('saved', result, ratesRecalculate.value)
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存比例失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="社保公积金比例（本账套）"
    style="width: min(540px, 92vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <NSpin :show="loading">
      <p class="modal-hint">
        公积金比例因城市与单位而异（常见 8% 或 12%）。比例决定代扣拆分为「社保」与「公积金」缴纳凭证的金额，请与当地社保局扣缴端保持一致。
      </p>
      <NForm label-placement="left" label-width="96" style="margin-top: 12px">
        <NFormItem label="个人社保">
          <NInputNumber v-model:value="form.socialPersonal" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="个人公积金">
          <NInputNumber v-model:value="form.housingPersonal" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="单位社保">
          <NInputNumber v-model:value="form.socialCompany" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="单位公积金">
          <NInputNumber v-model:value="form.housingCompany" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
            <template #suffix>%</template>
          </NInputNumber>
        </NFormItem>
      </NForm>
      <p v-if="localRates && !localRates.isCustomized" class="modal-hint muted">
        当前为系统默认基准：个人社保 {{ ratesPercent(Number(localRates.defaults.socialPersonal)) }} · 个人公积金 {{ ratesPercent(Number(localRates.defaults.housingPersonal)) }}
      </p>
      <div class="rates-recalc-row">
        <NSwitch v-model:value="ratesRecalculate" size="small" />
        <span class="recalc-hint">保存后按新比例重新计算 {{ selectedMonth }} 草稿工资表（已计提月份不受影响）</span>
      </div>
    </NSpin>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
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
  margin-top: 6px;
}
.rates-recalc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  padding: 10px 12px;
  background: var(--solo-bg-hover, rgba(0, 0, 0, 0.02));
  border-radius: var(--solo-radius-md, 8px);
}
.recalc-hint {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}
</style>
