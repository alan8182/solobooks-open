<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NButton,
  NCard,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSwitch,
  NTag,
} from 'naive-ui'
import GuideTrigger from '@/components/business/GuideTrigger.vue'
import { formatMoney } from '@/utils/format'
import {
  calcLaborTaxFromGross,
  calcLaborTaxFromNet,
  DEFAULT_LABOR_TAX_OPTIONS,
  type LaborTaxMode,
} from '@/utils/laborTaxCalculator'

const route = useRoute()

const mode = ref<LaborTaxMode>('gross_to_net')
const inputAmount = ref<number>(2020)
const isSevenServices = ref<boolean>(DEFAULT_LABOR_TAX_OPTIONS.isSevenServices)
const vatRate = ref<0.01 | 0.03>(DEFAULT_LABOR_TAX_OPTIONS.vatRate)
const urbanRate = ref<0.07 | 0.05 | 0.01>(DEFAULT_LABOR_TAX_OPTIONS.urbanRate)
const halfSurcharge = ref<boolean>(DEFAULT_LABOR_TAX_OPTIONS.halfSurcharge)
const showAdvanced = ref(false)

const PRESET_AMOUNTS = [500, 1000, 2000, 5000, 10000]

const VAT_RATE_OPTIONS = [
  { label: '1%（2027年底前减按优惠）', value: 0.01 },
  { label: '3%（标准征收率）', value: 0.03 },
]

const URBAN_RATE_OPTIONS = [
  { label: '市区 7%', value: 0.07 },
  { label: '县城/镇 5%', value: 0.05 },
  { label: '其他地区 1%', value: 0.01 },
]

function applyRouteAmount() {
  const raw = Number(route.query.amount)
  if (Number.isFinite(raw) && raw > 0) {
    inputAmount.value = Math.round(raw * 100) / 100
    mode.value = 'gross_to_net'
  }
}

onMounted(applyRouteAmount)
watch(() => route.query.amount, applyRouteAmount)

const taxResult = computed(() => {
  const opts = {
    isSevenServices: isSevenServices.value,
    vatRate: vatRate.value,
    urbanRate: urbanRate.value,
    halfSurcharge: halfSurcharge.value,
  }
  const val = Number(inputAmount.value) || 0
  return mode.value === 'net_to_gross'
    ? calcLaborTaxFromNet(val, opts)
    : calcLaborTaxFromGross(val, opts)
})

const vatStatusTag = computed<{ label: string; type: 'success' | 'info' | 'warning' }>(() => {
  if (!isSevenServices.value) {
    return { label: '非七类服务 · 企业免代扣', type: 'success' }
  }
  if (!taxResult.value.vatOverThreshold) {
    return { label: '未达 1,000 元 · 零/免税申报', type: 'info' }
  }
  return { label: '已达 1,000 元 · 全额代扣', type: 'warning' }
})
</script>

<template>
  <div class="tools-page">
    <header class="tools-header">
      <div>
        <h2 class="tools-title">财务工具箱</h2>
        <p class="tools-subtitle">发钱前先算清税负，避免多打款或漏扣缴</p>
      </div>
      <div class="tools-tabs" role="tablist" aria-label="财务工具切换">
        <button type="button" class="tool-tab-pill is-active" role="tab" aria-selected="true">
          <i class="ri-calculator-line" aria-hidden="true" />
          <span>个人劳务算税</span>
        </button>
      </div>
    </header>

    <div class="calculator-grid">
      <!-- 左侧：测算输入与新政口径 -->
      <NCard :bordered="false" class="calc-card">
        <div class="calc-section">
          <div class="field-label">计算方向</div>
          <NRadioGroup v-model:value="mode" size="medium" class="mode-switch">
            <NRadioButton value="gross_to_net">已知税前价 → 算实发</NRadioButton>
            <NRadioButton value="net_to_gross">已知到手价 → 反算成本</NRadioButton>
          </NRadioGroup>
        </div>

        <div class="calc-section">
          <div class="field-label">
            {{ mode === 'gross_to_net' ? '约定含税金额（元）' : '承诺对方到手金额（元）' }}
          </div>
          <NInputNumber
            v-model:value="inputAmount"
            :min="0"
            :precision="2"
            :show-button="false"
            size="large"
            placeholder="输入金额"
            class="amount-input"
          >
            <template #prefix>¥</template>
          </NInputNumber>
          <div class="preset-row">
            <NButton
              v-for="amt in PRESET_AMOUNTS"
              :key="amt"
              size="tiny"
              :type="inputAmount === amt ? 'primary' : 'default'"
              :secondary="inputAmount === amt"
              @click="inputAmount = amt"
            >
              ¥{{ amt.toLocaleString() }}
            </NButton>
          </div>
        </div>

        <div class="calc-section policy-box">
          <div class="policy-toggle-row">
            <div class="policy-copy">
              <div class="policy-title">属于七类应税服务（2026年28号公告）</div>
              <div class="policy-hint">研发、软件、设计、咨询、广播影视、文化、教育服务；11月1日起达 1,000 元起征点由企业代扣增值税</div>
            </div>
            <NSwitch v-model:value="isSevenServices" />
          </div>

          <button
            type="button"
            class="advanced-toggle-btn"
            @click="showAdvanced = !showAdvanced"
          >
            <span>税率与附加税口径（默认 1% 减按 · 市区 7% 减半）</span>
            <i :class="showAdvanced ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          </button>

          <div v-if="showAdvanced" class="advanced-fields">
            <div class="adv-field">
              <span class="adv-label">增值税征收率</span>
              <NSelect v-model:value="vatRate" :options="VAT_RATE_OPTIONS" size="small" :disabled="!isSevenServices" />
            </div>
            <div class="adv-field">
              <span class="adv-label">城建税率</span>
              <NSelect v-model:value="urbanRate" :options="URBAN_RATE_OPTIONS" size="small" :disabled="!isSevenServices" />
            </div>
            <div class="adv-field-switch">
              <span class="adv-label">附加税费六税两费减半征收</span>
              <NSwitch v-model:value="halfSurcharge" size="small" :disabled="!isSevenServices" />
            </div>
          </div>
        </div>
      </NCard>

      <!-- 右侧：算税结果与拆解 -->
      <NCard :bordered="false" class="result-card">
        <div class="result-kpi-grid">
          <div class="kpi-box is-primary">
            <span class="kpi-label">实际应打给个人</span>
            <strong class="kpi-value">{{ formatMoney(taxResult.netAmount) }}</strong>
          </div>
          <div class="kpi-box">
            <span class="kpi-label">公司含税总支出</span>
            <strong class="kpi-value">{{ formatMoney(taxResult.grossAmount) }}</strong>
          </div>
        </div>

        <div class="breakdown-list">
          <div class="breakdown-row">
            <span class="row-label">不含税销售额</span>
            <span class="row-value">{{ formatMoney(taxResult.exclVatAmount) }}</span>
          </div>
          <div class="breakdown-row">
            <div class="row-label-group">
              <span class="row-label">代扣增值税</span>
              <NTag size="tiny" :bordered="false" :type="vatStatusTag.type">{{ vatStatusTag.label }}</NTag>
            </div>
            <span class="row-value">{{ formatMoney(taxResult.vatAmount) }}</span>
          </div>
          <div class="breakdown-row">
            <span class="row-label">代扣附加税费（城建 + 教育附加）</span>
            <span class="row-value">{{ formatMoney(taxResult.surchargeAmount) }}</span>
          </div>
          <div class="breakdown-row">
            <div class="row-label-group">
              <span class="row-label">代扣劳务报酬个税</span>
              <span class="row-sub">{{ taxResult.iitBracket }}</span>
            </div>
            <span class="row-value">{{ formatMoney(taxResult.iitAmount) }}</span>
          </div>
          <div class="breakdown-row is-total">
            <div class="row-label-group">
              <span class="row-label">合计扣缴税费</span>
              <span class="row-sub">综合税负率 {{ taxResult.effectiveTaxRate.toFixed(2) }}%</span>
            </div>
            <span class="row-value is-accent">{{ formatMoney(taxResult.totalTax) }}</span>
          </div>
        </div>

        <div class="action-advice-box">
          <div class="advice-head">
            <i class="ri-information-line" aria-hidden="true" />
            <span>实操结论</span>
          </div>
          <p class="advice-text">{{ taxResult.actionSummary }}</p>
          <div class="advice-links">
            <GuideTrigger guide="iit" label="个税怎么申报？" />
          </div>
        </div>
      </NCard>
    </div>
  </div>
</template>

<style scoped>
.tools-page {
  max-width: var(--page-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tools-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 12px;
}

.tools-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.tools-subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
}

.tools-tabs {
  display: flex;
  gap: 8px;
}

.tool-tab-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.tool-tab-pill.is-active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.calculator-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
  gap: 16px;
  align-items: start;
}

.calc-card,
.result-card {
  border-radius: var(--solo-radius-lg);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
}

.calc-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 18px;
}

.calc-section:last-child {
  margin-bottom: 0;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.mode-switch {
  display: flex;
  width: 100%;
}

.mode-switch :deep(.n-radio-button) {
  flex: 1;
  text-align: center;
}

.amount-input {
  width: 100%;
  font-variant-numeric: tabular-nums;
}

.preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.policy-box {
  padding: 12px 14px;
  border-radius: var(--solo-radius-md);
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
}

.policy-toggle-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.policy-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.policy-hint {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--solo-text-muted);
}

.advanced-toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 6px 0 0;
  border: 0;
  border-top: 1px solid var(--solo-border);
  margin-top: 4px;
  background: transparent;
  font-size: 12px;
  color: var(--solo-text-secondary);
  cursor: pointer;
}

.advanced-toggle-btn:hover {
  color: var(--solo-primary);
}

.advanced-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 6px;
}

.adv-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.adv-field-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.adv-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.result-kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.kpi-box {
  padding: 14px 16px;
  border-radius: var(--solo-radius-md);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-page);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kpi-box.is-primary {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
}

.kpi-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}

.kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}

.kpi-box.is-primary .kpi-value {
  color: var(--solo-primary);
}

.breakdown-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 0 14px;
}

.breakdown-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}

.breakdown-row.is-total {
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border);
  font-weight: 600;
}

.row-label-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.row-label {
  color: var(--solo-text-secondary);
}

.row-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.row-value {
  font-weight: 600;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.row-value.is-accent {
  color: var(--solo-expense);
}

.action-advice-box {
  padding: 12px 14px;
  border-radius: var(--solo-radius-md);
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
}

.advice-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-primary);
}

.advice-text {
  margin: 6px 0 8px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--solo-text-primary);
}

.advice-links {
  display: flex;
  align-items: center;
  gap: 12px;
}

@media (max-width: 860px) {
  .calculator-grid {
    grid-template-columns: 1fr;
  }
}
</style>
