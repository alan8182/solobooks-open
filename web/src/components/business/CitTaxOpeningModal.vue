<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NInputNumber, NModal, NSpace, NSpin, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { reportErrorToast } from '@/utils/errorPresenter'
import { formatMoney } from '@/utils/format'
import {
  detectPrepaidCitHint,
  loadTaxOpeningMemory,
  saveTaxOpeningMemory,
  type PrepaidCitHint,
} from '@/utils/taxOpeningMemory'
import { normalizeTrialBalanceRows } from '@/utils/trialBalance'

const props = defineProps<{
  show: boolean
  fiscalYear: number
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: []
}>()

const message = useMessage()
const authStore = useAuthStore()

const loading = ref(false)
const saving = ref(false)
const priorYearLoss = ref<number>(0)
const prepaidCitTax = ref<number>(0)
const carryforwardAdvertising = ref<number>(0)
const carryforwardEducation = ref<number>(0)
const carryforwardDonationMap = ref<Record<number, number>>({})
const migrationNote = ref('')
const startPeriod = ref('')
const prepaidCitHint = ref<PrepaidCitHint | null>(null)

const donationCarryforwardYears = computed(() => [
  props.fiscalYear - 3,
  props.fiscalYear - 2,
  props.fiscalYear - 1,
])

function buildDonationMap(raw: unknown): Record<number, number> {
  const map: Record<number, number> = {}
  for (const yr of donationCarryforwardYears.value) {
    map[yr] = 0
  }
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (!item || typeof item !== 'object') continue
      const yr = Number((item as { year?: unknown }).year)
      const amt = Number((item as { amount?: unknown }).amount) || 0
      if (Number.isInteger(yr) && yr in map && amt > 0) {
        map[yr] = amt
      }
    }
  }
  return map
}

const visible = computed({
  get: () => props.show,
  set: (val: boolean) => emit('update:show', val),
})

const showHintBar = computed(
  () =>
    prepaidCitHint.value !== null
    && Math.abs((priorOrZero(prepaidCitTax.value)) - prepaidCitHint.value.amount) >= 0.01,
)

function priorOrZero(val: number | null | undefined): number {
  return Number(val) || 0
}

async function loadCurrentTaxOpening() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/opening/balances', {
      params: { fiscal_year: props.fiscalYear },
    })
    const tm = (data?.tax_memory && typeof data.tax_memory === 'object') ? data.tax_memory : {}
    const serverLoss = Number(tm.priorYearLoss ?? tm.prior_year_loss ?? 0) || 0
    const serverPrepaid = Number(tm.prepaidCitTax ?? tm.prepaid_cit_tax ?? 0) || 0
    const serverAdv = Number(tm.carryforwardAdvertising ?? tm.carryforward_advertising ?? 0) || 0
    const serverEdu = Number(tm.carryforwardEducation ?? tm.carryforward_education ?? 0) || 0
    const serverDonations = tm.carryforwardDonations ?? tm.carryforward_donations ?? []
    const serverNote = String(tm.migrationNote ?? tm.migration_note ?? '')
    startPeriod.value = data?.start_period_saved ? String(data.start_period || '') : ''

    const hasServerCarryforward = serverAdv > 0 || serverEdu > 0 || (Array.isArray(serverDonations) && serverDonations.length > 0)
    if (serverLoss > 0 || serverPrepaid > 0 || hasServerCarryforward) {
      priorYearLoss.value = serverLoss
      prepaidCitTax.value = serverPrepaid
      carryforwardAdvertising.value = serverAdv
      carryforwardEducation.value = serverEdu
      carryforwardDonationMap.value = buildDonationMap(serverDonations)
      migrationNote.value = serverNote
    } else {
      const local = loadTaxOpeningMemory(authStore.currentCompanyId, props.fiscalYear)
      priorYearLoss.value = local?.priorYearLoss || 0
      prepaidCitTax.value = local?.prepaidCitTax || 0
      carryforwardAdvertising.value = local?.carryforwardAdvertising || 0
      carryforwardEducation.value = local?.carryforwardEducation || 0
      carryforwardDonationMap.value = buildDonationMap(local?.carryforwardDonations)
      migrationNote.value = serverNote || local?.migrationNote || ''
    }

    const trialRows = normalizeTrialBalanceRows(data?.trial_balance_items)
    prepaidCitHint.value = detectPrepaidCitHint(trialRows)
  } catch (err: unknown) {
    reportErrorToast(message, err, '加载企业所得税校准参数失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.show,
  (isOpen) => {
    if (isOpen) {
      void loadCurrentTaxOpening()
    }
  },
)

function applyHint() {
  if (!prepaidCitHint.value) return
  prepaidCitTax.value = prepaidCitHint.value.amount
}

async function handleSave() {
  if (saving.value) return
  saving.value = true
  try {
    const lossVal = priorOrZero(priorYearLoss.value)
    const prepaidVal = priorOrZero(prepaidCitTax.value)
    const advVal = priorOrZero(carryforwardAdvertising.value)
    const eduVal = priorOrZero(carryforwardEducation.value)
    const donationBuckets = donationCarryforwardYears.value
      .map((yr) => ({ year: yr, amount: priorOrZero(carryforwardDonationMap.value[yr]) }))
      .filter((item) => item.amount > 0)

    await apiClient.put('/api/opening/tax-memory', {
      fiscal_year: props.fiscalYear,
      start_period: startPeriod.value || undefined,
      prior_year_loss: lossVal,
      prepaid_cit_tax: prepaidVal,
      carryforward_advertising: advVal,
      carryforward_education: eduVal,
      carryforward_donations: donationBuckets,
    })
    saveTaxOpeningMemory(authStore.currentCompanyId, props.fiscalYear, {
      priorYearLoss: lossVal,
      prepaidCitTax: prepaidVal,
      migrationNote: migrationNote.value,
      carryforwardAdvertising: advVal,
      carryforwardEducation: eduVal,
      carryforwardDonations: donationBuckets,
    })
    message.success('已保存并重新估算企业所得税')
    emit('update:show', false)
    emit('saved')
  } catch (err: unknown) {
    reportErrorToast(message, err, '保存企业所得税校准参数失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    v-model:show="visible"
    preset="card"
    :auto-focus="false"
    title="校准企业所得税抵扣基数（选填）"
    style="max-width: 580px"
  >
    <NSpin :show="loading">
      <div class="cit-modal-body">
        <NAlert type="info" :bordered="false">
          去电子税务局正式报税时，第 24 行「以前年度亏损」和第 30 行「本年已预缴」税局都会自动带出，不会多交税。这里仅用于让本页预估应补税额与税局口径保持一致；不确定可直接保持 0。
        </NAlert>

        <div class="cit-field">
          <label class="cit-field-label">
            以前年度可弥补亏损额（元）
            <span class="cit-line-tag">对应 A200000 第 24 行</span>
          </label>
          <NInputNumber
            v-model:value="priorYearLoss"
            :min="0"
            :precision="2"
            :show-button="false"
            placeholder="0.00"
            size="medium"
          >
            <template #prefix>¥</template>
          </NInputNumber>
          <details class="cit-guide">
            <summary>不知道去哪查？看电子税务局 3 步指引</summary>
            <ol>
              <li>登录电子税务局，点击 <strong>「我要查询」→「申报信息查询」</strong>。</li>
              <li>筛选<strong>上一年度</strong>的《企业所得税年度纳税申报表（汇算清缴）》，打开 <strong>《企业所得税弥补亏损明细表（A106000）》</strong>。</li>
              <li>取最底部 <strong>「可结转以后年度弥补的亏损额合计」</strong> 填入（去年盈利或今年新设公司填 0）。</li>
            </ol>
          </details>
        </div>

        <div class="cit-field">
          <label class="cit-field-label">
            本年已预缴企业所得税额（元）
            <span class="cit-line-tag">对应 A200000 第 30 行</span>
          </label>
          <NInputNumber
            v-model:value="prepaidCitTax"
            :min="0"
            :precision="2"
            :show-button="false"
            placeholder="0.00"
            size="medium"
          >
            <template #prefix>¥</template>
          </NInputNumber>

          <div v-if="showHintBar && prepaidCitHint" class="cit-hint-bar">
            <span>
              期初原表 2221006 本年累计借方
              <strong>¥{{ formatMoney(prepaidCitHint.amount) }}</strong>
              （如含年初补缴上年税款请扣除）
            </span>
            <NButton size="tiny" type="primary" secondary @click="applyHint">
              填入参考值
            </NButton>
          </div>

          <details class="cit-guide">
            <summary>不知道去哪查？看电子税务局 3 步指引</summary>
            <ol>
              <li>登录电子税务局，点击 <strong>「我要查询」→「申报信息查询」</strong>。</li>
              <li>打开<strong>今年最近一季</strong>已申报的《企业所得税月（季）度预缴纳税申报表（A200000）》。</li>
              <li>将 <strong>第 30 行（实际已缴纳所得税额）+ 第 32 行（本期应补税额）</strong> 相加填入（前几季均零申报或亏损填 0）。</li>
            </ol>
          </details>
        </div>

        <details class="cit-guide cit-carryforward-box">
          <summary>以前年度超限额费用结转（选填：广宣费 / 职工教育经费 / 近 3 年公益捐赠）</summary>
          <div class="cit-carryforward-grid">
            <div class="cit-cf-item">
              <label class="cit-cf-label">广宣费结转余额（元，无限期）</label>
              <NInputNumber
                v-model:value="carryforwardAdvertising"
                :min="0"
                :precision="2"
                :show-button="false"
                placeholder="0.00"
                size="small"
              >
                <template #prefix>¥</template>
              </NInputNumber>
            </div>
            <div class="cit-cf-item">
              <label class="cit-cf-label">职工教育经费结转（元，无限期）</label>
              <NInputNumber
                v-model:value="carryforwardEducation"
                :min="0"
                :precision="2"
                :show-button="false"
                placeholder="0.00"
                size="small"
              >
                <template #prefix>¥</template>
              </NInputNumber>
            </div>
            <div v-for="yr in donationCarryforwardYears" :key="yr" class="cit-cf-item">
              <label class="cit-cf-label">{{ yr }} 年公益捐赠结转（3 年内有效）</label>
              <NInputNumber
                v-model:value="carryforwardDonationMap[yr]"
                :min="0"
                :precision="2"
                :show-button="false"
                placeholder="0.00"
                size="small"
              >
                <template #prefix>¥</template>
              </NInputNumber>
            </div>
          </div>
        </details>
      </div>
    </NSpin>

    <template #footer>
      <NSpace justify="end">
        <NButton @click="visible = false">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">
          保存并重新估算
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.cit-modal-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cit-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cit-field-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.cit-line-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
}
.cit-hint-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding: 7px 10px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 12px;
  line-height: 1.45;
}
.cit-guide {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.cit-guide summary {
  color: var(--solo-primary);
  cursor: pointer;
  user-select: none;
  font-weight: 500;
}
.cit-guide summary:hover {
  text-decoration: underline;
}
.cit-guide ol {
  margin: 6px 0 0;
  padding: 8px 12px 8px 26px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-sm);
  display: flex;
  flex-direction: column;
  gap: 4px;
  line-height: 1.55;
}
.cit-carryforward-box {
  padding-top: 6px;
  border-top: 1px dashed var(--solo-border-light);
}
.cit-carryforward-grid {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
}
.cit-cf-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cit-cf-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
</style>
