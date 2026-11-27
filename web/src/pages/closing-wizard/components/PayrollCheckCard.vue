<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag, NAlert } from 'naive-ui'
import { formatMoney } from '@/utils/format'
import type { CarryForwardMeta } from '@/api/salary'

interface PayrollEmployeeItem {
  id: string
  name: string
  dept?: string
  base: number
  bonus: number
  allowance?: number
  deduction?: number
  social: number
  personalSocial: number
  personalTax: number
  net: number
  cost: number
}

const props = defineProps<{
  month: string
  employees: PayrollEmployeeItem[]
  showSalaryTable: boolean
  payrollAccrued: boolean
  payrollPendingReview: boolean
  totalNetSalary: number
  totalCompanyCost: number
  payrollAllSettled: boolean
  carryForward?: CarryForwardMeta | null
}>()

const hasCarryForward = computed(() => Boolean(props.carryForward?.hasPreviousSheet))
const primaryAccrueLabel = computed(() => {
  if (!hasCarryForward.value) return '一键计提记账'
  return props.carryForward?.isIdenticalToPrevious
    ? '与上月无变化，直接确认'
    : '确认本月工资并计提'
})

const emit = defineEmits<{
  (e: 'update:show-salary-table', value: boolean): void
  (e: 'go-edit'): void
  (e: 'accrue'): void
}>()

function personalWithheld(emp: PayrollEmployeeItem): number {
  return Math.round((Number(emp.personalSocial || 0) + Number(emp.personalTax || 0)) * 100) / 100
}
</script>

<template>
  <div class="payroll-card" :class="{ 'is-settled': payrollAllSettled }">
    <!-- 1) 无员工空态 -->
    <div v-if="employees.length === 0" class="compact-strip">
      <div class="strip-left">
        <i class="ri-user-star-line strip-icon muted" aria-hidden="true" />
        <span class="strip-title">员工薪酬核对</span>
        <span class="strip-meta">本月无在职员工（轻资产/一人企业无需计提）</span>
      </div>
      <div class="strip-actions">
        <NButton size="small" quaternary @click="emit('go-edit')">去配置</NButton>
      </div>
    </div>

    <!-- 2) 形态 A：未计提或计提待复核（阻断本月关账，默认展开） -->
    <template v-else-if="!payrollAccrued || payrollPendingReview">
      <div class="card-head">
        <div class="head-left">
          <i class="ri-user-star-line strip-icon primary" aria-hidden="true" />
          <span class="strip-title">员工薪酬核对</span>
          <NTag size="small" type="warning" :bordered="false">
            {{ payrollPendingReview ? '计提凭证待审核' : '待计提（阻断关账）' }}
          </NTag>
        </div>
        <div class="strip-actions">
          <NButton size="small" quaternary @click="emit('update:show-salary-table', !showSalaryTable)">
            {{ showSalaryTable ? '收起明细' : '查看明细' }}
            <template #icon><i :class="showSalaryTable ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" /></template>
          </NButton>
          <NButton size="small" quaternary @click="emit('go-edit')">
            <template #icon><i class="ri-edit-line" /></template>
            去修改
          </NButton>
        </div>
      </div>

      <div class="metrics-bar">
        <div class="metric-item">
          <span class="metric-label">本月应发人数</span>
          <strong class="metric-val">{{ employees.length }} 人</strong>
        </div>
        <div class="metric-divider" aria-hidden="true" />
        <div class="metric-item">
          <span class="metric-label">本月实发薪酬</span>
          <strong class="metric-val">{{ formatMoney(totalNetSalary) }}</strong>
        </div>
        <div class="metric-divider" aria-hidden="true" />
        <div class="metric-item">
          <span class="metric-label">企业总人工成本</span>
          <strong class="metric-val">{{ formatMoney(totalCompanyCost) }}</strong>
        </div>
      </div>

      <div v-if="showSalaryTable" class="detail-table-wrap">
        <table class="detail-table">
          <thead>
            <tr>
              <th>姓名</th>
              <th>部门</th>
              <th class="num">基本工资</th>
              <th class="num">奖金</th>
              <th class="num">个人代扣（五险一金+个税）</th>
              <th class="num">实发工资</th>
              <th class="num">企业总成本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="emp in employees" :key="emp.id">
              <td class="emp-name">{{ emp.name }}</td>
              <td>{{ emp.dept || '—' }}</td>
              <td class="num">{{ formatMoney(Number(emp.base || 0)) }}</td>
              <td class="num">{{ formatMoney(Number(emp.bonus || 0)) }}</td>
              <td class="num">{{ formatMoney(personalWithheld(emp)) }}</td>
              <td class="num strong">{{ formatMoney(Number(emp.net || 0)) }}</td>
              <td class="num strong">{{ formatMoney(Number(emp.cost || 0)) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="hasCarryForward" class="carry-forward-strip" :class="{ 'is-same': carryForward?.isIdenticalToPrevious }">
        <i :class="carryForward?.isIdenticalToPrevious ? 'ri-checkbox-circle-line' : 'ri-history-line'" aria-hidden="true" />
        <div class="carry-forward-copy">
          <span v-if="carryForward?.isIdenticalToPrevious">
            已自动带出 {{ carryForward.sourceMonth }} 工资与社保明细（共 {{ employees.length }} 人，金额与上月一致）
          </span>
          <span v-else>
            已基于 {{ carryForward?.sourceMonth }} 自动带出固定工资与社保
            <template v-if="carryForward?.changeReasons?.length">
              （{{ carryForward.changeReasons.join(' · ') }}）
            </template>
          </span>
        </div>
      </div>

      <NAlert
        v-if="payrollPendingReview"
        type="warning"
        :show-icon="false"
        class="review-alert"
      >
        工资计提凭证正在等待审核（复核模式下需他人过账）。审核通过后此处会自动更新为「已计提」，届时才能继续发放与缴纳。
      </NAlert>

      <div v-else class="blocking-footer">
        <span class="footer-note">
          确认后将自动计入 {{ month }} 人工成本；实际发薪与社保扣款可次月随银行流水核销
        </span>
        <div class="footer-actions">
          <NButton v-if="hasCarryForward" size="small" secondary @click="emit('go-edit')">
            有变化，去修改
          </NButton>
          <NButton size="small" type="primary" @click="emit('accrue')">
            {{ primaryAccrueLabel }}
          </NButton>
        </div>
      </div>
    </template>

    <!-- 3) 形态 B & C：已计提（不阻断关账，收起为单行紧凑条；实际发薪/社保随银行流水核销） -->
    <template v-else>
      <div class="compact-strip">
        <div class="strip-left">
          <i class="ri-checkbox-circle-fill strip-icon income" aria-hidden="true" />
          <span class="strip-title">
            {{ payrollAllSettled ? '员工薪酬与社保已结清' : '员工薪酬已计提入账' }}
          </span>
          <NTag v-if="!payrollAllSettled" size="small" type="success" :bordered="false">
            不阻断本月关账
          </NTag>
          <span class="strip-meta num-inline">
            {{ employees.length }} 人 · 实发 {{ formatMoney(totalNetSalary) }} ·
            {{ payrollAllSettled ? '企业总成本' : '总成本' }} {{ formatMoney(totalCompanyCost) }}
            <template v-if="!payrollAllSettled"> · 发薪与社保扣款将随银行流水自动核销</template>
          </span>
        </div>
        <div class="strip-actions">
          <NButton size="small" quaternary @click="emit('update:show-salary-table', !showSalaryTable)">
            {{ showSalaryTable ? '收起明细' : '明细' }}
          </NButton>
          <NButton size="small" quaternary @click="emit('go-edit')">去修改</NButton>
        </div>
      </div>

      <!-- 展开后的员工薪酬明细 -->
      <div v-if="showSalaryTable" class="detail-table-wrap">
        <table class="detail-table">
          <thead>
            <tr>
              <th>姓名</th>
              <th>部门</th>
              <th class="num">基本工资</th>
              <th class="num">奖金</th>
              <th class="num">个人代扣（五险一金+个税）</th>
              <th class="num">实发工资</th>
              <th class="num">企业总成本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="emp in employees" :key="emp.id">
              <td class="emp-name">{{ emp.name }}</td>
              <td>{{ emp.dept || '—' }}</td>
              <td class="num">{{ formatMoney(Number(emp.base || 0)) }}</td>
              <td class="num">{{ formatMoney(Number(emp.bonus || 0)) }}</td>
              <td class="num">{{ formatMoney(personalWithheld(emp)) }}</td>
              <td class="num strong">{{ formatMoney(Number(emp.net || 0)) }}</td>
              <td class="num strong">{{ formatMoney(Number(emp.cost || 0)) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.payroll-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  padding: 14px 18px;
  margin-bottom: 16px;
}

.payroll-card.is-settled {
  background: color-mix(in oklch, var(--solo-bg-card) 96%, var(--solo-income) 4%);
  border-color: color-mix(in oklch, var(--solo-border-light) 70%, var(--solo-income) 30%);
}

.compact-strip,
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.strip-left,
.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.strip-icon {
  font-size: 17px;
  flex-shrink: 0;
}
.strip-icon.primary { color: var(--solo-primary); }
.strip-icon.income { color: var(--solo-income); }
.strip-icon.muted { color: var(--solo-text-muted); }

.strip-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  white-space: nowrap;
}

.strip-meta {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}

.num-inline {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
}

.strip-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.metrics-bar {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-top: 12px;
  padding: 10px 0 4px;
  border-top: 1px solid var(--solo-border-light);
  flex-wrap: wrap;
}

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.metric-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.metric-val {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 18px;
  font-weight: 600;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}

.metric-divider {
  width: 1px;
  height: 28px;
  background: var(--solo-border-light);
}

.review-alert {
  margin-top: 12px;
}

.carry-forward-strip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 12.5px;
  line-height: 1.5;
}
.carry-forward-strip.is-same {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.carry-forward-strip i {
  font-size: 15px;
  margin-top: 1px;
  flex-shrink: 0;
}
.carry-forward-copy {
  min-width: 0;
}

.blocking-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.footer-note {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}

.detail-table-wrap {
  margin-top: 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  overflow-x: auto;
  max-height: 260px;
  overflow-y: auto;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.detail-table th {
  text-align: left;
  padding: 8px 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-soft);
  border-bottom: 1px solid var(--solo-border-light);
  white-space: nowrap;
}

.detail-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--solo-border-light);
  color: var(--solo-text-primary);
  white-space: nowrap;
}

.detail-table tbody tr:last-child td {
  border-bottom: none;
}

.detail-table .num {
  text-align: right;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
}

.detail-table .emp-name {
  font-weight: 500;
}

.detail-table .strong {
  font-weight: 600;
}

@media (max-width: 640px) {
  .metric-divider {
    display: none;
  }
  .metrics-bar {
    gap: 14px 20px;
  }
}
</style>
