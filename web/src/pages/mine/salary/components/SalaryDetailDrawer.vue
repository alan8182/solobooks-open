<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onDeactivated } from 'vue'
import { NDrawer, NDrawerContent, NTag, NInputNumber, NButton, NSpace } from 'naive-ui'
import type { SalaryRecord } from '@/api/salary'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  show: boolean
  row: SalaryRecord | null
  department: string
  isRdDept: boolean
  rdSummaryText: string
  selectedMonth: string
  isDraft: boolean
  exporting?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'change', triggerAutoTax: boolean): void
  (e: 'open-tax', row: SalaryRecord): void
  (e: 'open-rd', row: SalaryRecord): void
  (e: 'export-payslip', row: SalaryRecord): void
}>()

onDeactivated(() => {
  if (props.show) emit('update:show', false)
})

const round2 = (n: number) => Math.round(n * 100) / 100

const rowGross = computed(() => {
  const r = props.row
  if (!r) return 0
  return round2(
    Number(r.base_salary || 0)
    + Number(r.bonus || 0)
    + Number(r.allowance || 0)
    - Number(r.deduction || 0),
  )
})

const personalTotal = computed(() => {
  const r = props.row
  if (!r) return 0
  return round2(
    Number(r.personal_social_insurance ?? 0) + Number(r.personal_housing_fund ?? 0),
  )
})

const companyTotal = computed(() => {
  const r = props.row
  if (!r) return 0
  return round2(
    Number(r.company_social_insurance ?? 0) + Number(r.company_housing_fund ?? 0),
  )
})
</script>

<template>
  <NDrawer
    :show="show"
    width="min(440px, 94vw)"
    placement="right"
    @update:show="(v) => emit('update:show', v)"
  >
    <NDrawerContent closable :native-scrollbar="false">
      <template #header>
        <div v-if="row" class="drawer-head">
          <div class="drawer-head-main">
            <span class="drawer-name">{{ row.employee_name }}</span>
            <NTag size="tiny" :bordered="false" :type="isRdDept ? 'info' : 'default'">
              {{ department }}
            </NTag>
            <NTag size="tiny" :bordered="false" :type="isDraft ? 'warning' : 'success'">
              {{ isDraft ? '草稿可调整' : '已锁定只读' }}
            </NTag>
          </div>
          <span class="drawer-sub">{{ selectedMonth }} 月度薪酬核算明细</span>
        </div>
      </template>

      <div v-if="row" class="detail-body">
        <section class="detail-section">
          <div class="section-head">
            <span class="section-title">应发工资</span>
            <span v-if="isDraft" class="section-note">基本工资在员工花名册中维护</span>
          </div>

          <div class="field-row">
            <span class="field-label">基本工资</span>
            <span class="field-value">{{ formatMoney(Number(row.base_salary || 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">奖金</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.bonus"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', true)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.bonus || 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">补助 / 津贴</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.allowance"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', true)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.allowance || 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">考勤扣款</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.deduction"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', true)"
            />
            <span v-else class="field-value">
              {{ Number(row.deduction || 0) > 0 ? `-${formatMoney(Number(row.deduction || 0))}` : formatMoney(0) }}
            </span>
          </div>

          <div class="summary-row">
            <span>应发合计</span>
            <strong>{{ formatMoney(rowGross) }}</strong>
          </div>
        </section>

        <section class="detail-section">
          <div class="section-head">
            <span class="section-title">个人代扣与实发</span>
            <span class="section-note">代扣小计 {{ formatMoney(personalTotal) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">个人社保</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.personal_social_insurance"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', true)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.personal_social_insurance ?? 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">个人公积金</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.personal_housing_fund"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', true)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.personal_housing_fund ?? 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">个人所得税</span>
            <div class="tax-action-wrap">
              <span class="field-value">{{ formatMoney(Number(row.personal_tax || 0)) }}</span>
              <NButton size="tiny" quaternary type="primary" @click="emit('open-tax', row)">
                查看/试算明细
              </NButton>
            </div>
          </div>

          <div class="summary-row highlight-income">
            <span>实发到手</span>
            <strong>{{ formatMoney(Number(row.net_salary || 0)) }}</strong>
          </div>
        </section>

        <section class="detail-section">
          <div class="section-head">
            <span class="section-title">单位承担与总成本</span>
            <span class="section-note">单位缴费 {{ formatMoney(companyTotal) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">单位社保</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.company_social_insurance"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', false)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.company_social_insurance ?? 0)) }}</span>
          </div>

          <div class="field-row">
            <span class="field-label">单位公积金</span>
            <NInputNumber
              v-if="isDraft"
              v-model:value="row.company_housing_fund"
              :min="0"
              :precision="2"
              :show-button="false"
              size="small"
              class="drawer-input"
              @update:value="emit('change', false)"
            />
            <span v-else class="field-value">{{ formatMoney(Number(row.company_housing_fund ?? 0)) }}</span>
          </div>

          <div class="summary-row highlight-primary">
            <span>企业总人工成本</span>
            <strong>{{ formatMoney(Number(row.total_cost || 0)) }}</strong>
          </div>
        </section>

        <section v-if="isRdDept" class="detail-section">
          <div class="section-head">
            <span class="section-title">研发工时分摊</span>
          </div>
          <div class="field-row">
            <span class="field-label">当前分摊项目</span>
            <div class="tax-action-wrap">
              <span class="field-value muted">{{ rdSummaryText }}</span>
              <NButton v-if="isDraft" size="tiny" secondary type="primary" @click="emit('open-rd', row)">
                配置研发工时
              </NButton>
            </div>
          </div>
        </section>
      </div>

      <template #footer>
        <NSpace justify="space-between" style="width: 100%">
          <NButton v-if="row" secondary :loading="exporting" @click="emit('export-payslip', row)">
            <template #icon><i class="ri-file-download-line" /></template>
            导出工资条
          </NButton>
          <span v-else />
          <NButton type="primary" @click="emit('update:show', false)">完成</NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.drawer-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.drawer-head-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.drawer-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.drawer-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--solo-text-muted);
}

.detail-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--solo-border-light);
}
.detail-section:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.section-note {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}

.field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 28px;
}
.field-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.field-value {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.field-value.muted {
  font-family: inherit;
  font-weight: 400;
  color: var(--solo-text-secondary);
}

.drawer-input {
  width: 152px;
}
.drawer-input :deep(.n-input__input-el) {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}

.tax-action-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.summary-row strong {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.summary-row.highlight-income {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.summary-row.highlight-primary {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
}
</style>
