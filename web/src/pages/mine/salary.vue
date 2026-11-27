<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NTabs, NTab, NSpin, useMessage } from 'naive-ui'
import dayjs from 'dayjs'
import { formatMoney } from '@/utils/format'
import {
  getEmployees, getSalarySheet, getSalaryPaymentSuggestions, getPayrollLiabilitySuggestions,
  getRdSalaryAllocations, getPayrollRates, getDepartments,
  type Employee, type SalaryRecord, type SalarySheet, type RdSalaryAllocation,
  type SalaryPaymentSuggestion, type PayrollLiabilitySuggestion, type PayrollRates, type DepartmentItem,
} from '@/api/salary'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { getRdProjects } from '@/api/rd'
import type { RdProject } from '@/api/types'
import SalarySheetTab from './salary/components/SalarySheetTab.vue'
import EmployeeRosterTab from './salary/components/EmployeeRosterTab.vue'
import EmployeeModal from './salary/components/EmployeeModal.vue'
import DepartmentModal from './salary/components/DepartmentModal.vue'
import PayrollRatesModal from './salary/components/PayrollRatesModal.vue'
import TaxCalcModal from './salary/components/TaxCalcModal.vue'
import RdAllocationModal from './salary/components/RdAllocationModal.vue'

const route = useRoute()
const message = useMessage()
const viewStore = useWorkspaceViewStore()

const activeTab = ref<'sheet' | 'roster'>('sheet')
const loading = ref(false)
const selectedMonth = ref(dayjs().format('YYYY-MM'))

const employees = ref<Employee[]>([])
const departments = ref<DepartmentItem[]>([])
const payrollRates = ref<PayrollRates | null>(null)
const sheet = ref<SalarySheet | null>(null)
const records = ref<SalaryRecord[]>([])
const rdProjects = ref<RdProject[]>([])
const rdAllocations = ref<RdSalaryAllocation[]>([])
const paymentSuggestions = ref<SalaryPaymentSuggestion[]>([])
const liabilitySuggestions = ref<PayrollLiabilitySuggestion[]>([])

const sheetTabRef = ref<InstanceType<typeof SalarySheetTab> | null>(null)

// 弹窗状态
const showEmployee = ref(false)
const editingEmployee = ref<Employee | null>(null)
const showDept = ref(false)
const showRates = ref(false)
const showTax = ref(false)
const taxTarget = ref<SalaryRecord | null>(null)
const showRd = ref(false)
const rdTarget = ref<SalaryRecord | null>(null)

const departmentOptions = computed(() => departments.value.map((d) => ({
  label: d.isRd ? `${d.name} · 研发` : d.name,
  value: d.name,
})))

const housingFund = computed(() => ({
  personal: Number(sheet.value?.personal_housing_fund_total || 0),
  company: Number(sheet.value?.company_housing_fund_total || 0),
}))

const taxInitialSpecial = computed(() =>
  Number(employees.value.find((e) => e.id === taxTarget.value?.employee_id)?.special_additional_deduction || 0))

const ratesButtonSuffix = computed(() => {
  if (!payrollRates.value) return ''
  if (payrollRates.value.isCustomized) return ' · 自定义'
  return payrollRates.value.regionDesc ? ' · 默认' : ''
})

async function fetchEmployees() {
  try {
    employees.value = (await getEmployees()) as any
  } catch {
    message.error('加载员工列表失败')
  }
}

async function fetchSalarySheet() {
  if (!selectedMonth.value) return
  loading.value = true
  try {
    const [data, allocationData, projectData, employeeData, rateData, deptData]: any = await Promise.all([
      getSalarySheet(selectedMonth.value),
      getRdSalaryAllocations(selectedMonth.value),
      getRdProjects(),
      getEmployees(),
      getPayrollRates().catch(() => null),
      getDepartments().catch(() => null),
    ])
    sheet.value = data
    records.value = data.records || []
    rdAllocations.value = allocationData.allocations || []
    rdProjects.value = projectData || []
    employees.value = employeeData || []
    if (rateData) payrollRates.value = rateData
    if (deptData) departments.value = deptData.departments || []
  } catch {
    message.error('加载月度工资表失败')
  } finally {
    loading.value = false
  }
}

async function fetchPaymentSuggestions() {
  try {
    const [salaryData, liabilityData]: any = await Promise.all([
      getSalaryPaymentSuggestions(),
      getPayrollLiabilitySuggestions(),
    ])
    paymentSuggestions.value = salaryData.suggestions || []
    liabilitySuggestions.value = liabilityData.suggestions || []
  } catch {
    paymentSuggestions.value = []
    liabilitySuggestions.value = []
  }
}

function onSheetUpdated(data: SalarySheet) {
  sheet.value = data
  records.value = data.records || []
}

async function fetchRdAllocations() {
  if (!selectedMonth.value) return
  try {
    const data: any = await getRdSalaryAllocations(selectedMonth.value)
    rdAllocations.value = data.allocations || []
  } catch {
    // ignore silent refresh failure
  }
}

function openAddEmployee() {
  editingEmployee.value = null
  showEmployee.value = true
}

function openEditEmployee(emp: Employee) {
  editingEmployee.value = emp
  showEmployee.value = true
}

// 员工变动后，花名册与（未锁定的）当月草稿工资表一并刷新
async function onEmployeeChanged() {
  await Promise.all([fetchEmployees(), fetchSalarySheet()])
}

function onRatesSaved(rates: PayrollRates, recalculate: boolean) {
  payrollRates.value = rates
  if (recalculate) {
    fetchSalarySheet()
    message.success('比例已保存，本月草稿工资表已按新比例重算')
  } else {
    message.success('比例已保存，下次新建工资表时生效')
  }
}

function onTaxApply(taxDue: number) {
  const row = taxTarget.value
  if (!row) return
  // 回填到列表中对应的行；子组件监听 records 变化会重建本地副本，所以直接改源数据
  const source = records.value.find((r) => r.id === row.id)
  if (source) {
    source.personal_tax = taxDue
    const gross = Number(source.base_salary || 0) + Number(source.bonus || 0)
      + Number(source.allowance || 0) - Number(source.deduction || 0)
    source.net_salary = Math.round((gross - Number(source.personal_social_security || 0) - taxDue) * 100) / 100
    records.value = [...records.value]
  }
  sheetTabRef.value?.triggerAutoSave?.()
  message.success(`已更新本期应扣个税 ${formatMoney(taxDue)}`)
}

watch(selectedMonth, () => {
  fetchSalarySheet()
  fetchPaymentSuggestions()
})

// 支持按月深链（如结账向导「去修改」入口带 ?month=YYYY-MM）。
// 页签被 KeepAlive 缓存后再进入不会重跑 onMounted，所以 query 变化也要跟。
function applyMonthQuery(): boolean {
  const value = route.query.month
  if (typeof value === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(value) && value !== selectedMonth.value) {
    selectedMonth.value = value
    return true
  }
  return false
}
watch(() => route.query.month, applyMonthQuery)

watch(activeTab, (tab) => {
  if (tab === 'roster') fetchEmployees()
  else fetchSalarySheet()
})

onMounted(() => {
  // 深链改了月份时由 selectedMonth 的 watch 负责加载，避免重复请求
  if (!applyMonthQuery()) {
    fetchSalarySheet()
    fetchPaymentSuggestions()
  }
})

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([fetchSalarySheet(), fetchPaymentSuggestions()]))
</script>

<template>
  <div class="salary-page">
    <div class="page-header">
      <div class="header-left">
        <h2>{{ viewStore.isBoss ? '工资薪金' : '薪酬与社保管理' }}</h2>
        <p class="subtitle">
          {{ viewStore.isBoss ? '核对每月员工工资与社保个税，确认后自动入账' : '管理在职员工花名册，按月核算工资、社保公积金及个税并自动生成记账凭证' }}
        </p>
      </div>
      <div class="header-actions">
        <NButton size="small" secondary @click="showRates = true">
          <template #icon><i class="ri-percent-line" /></template>
          {{ viewStore.isBoss ? '社保比例' : '社保公积金比例' }}{{ ratesButtonSuffix }}
        </NButton>
        <NButton size="small" secondary @click="showDept = true">
          <template #icon><i class="ri-team-line" /></template>部门设置
        </NButton>
      </div>
    </div>

    <div class="tabs-toolbar">
      <NTabs v-model:value="activeTab" type="segment" class="salary-tabs">
        <NTab name="sheet">月度工资表</NTab>
        <NTab name="roster">员工花名册 · {{ employees.length }}</NTab>
      </NTabs>
    </div>

    <NSpin :show="loading">
      <SalarySheetTab
        v-show="activeTab === 'sheet'"
        ref="sheetTabRef"
        v-model:selected-month="selectedMonth"
        :sheet="sheet"
        :records="records"
        :employees="employees"
        :departments="departments"
        :rd-projects="rdProjects"
        :rd-allocations="rdAllocations"
        :payment-suggestions="paymentSuggestions"
        :liability-suggestions="liabilitySuggestions"
        :payroll-rates="payrollRates"
        :housing-fund="housingFund"
        @sheet-updated="onSheetUpdated"
        @reload-suggestions="fetchPaymentSuggestions"
        @reload-rd="fetchRdAllocations"
        @open-rd="(row) => { rdTarget = row; showRd = true }"
        @open-tax="(row) => { taxTarget = row; showTax = true }"
        @goto-roster="activeTab = 'roster'"
        @add-employee="openAddEmployee"
      />
      <EmployeeRosterTab
        v-show="activeTab === 'roster'"
        :employees="employees"
        :departments="departments"
        @add="openAddEmployee"
        @edit="openEditEmployee"
        @archived="onEmployeeChanged"
      />
    </NSpin>

    <EmployeeModal
      v-model:show="showEmployee"
      :employee="editingEmployee"
      :department-options="departmentOptions"
      :selected-month="selectedMonth"
      :is-draft-sheet="sheet?.status === 'draft'"
      :payroll-rates="payrollRates"
      @saved="onEmployeeChanged"
    />
    <DepartmentModal v-model:show="showDept" :departments="departments" @updated="(d) => (departments = d)" />
    <PayrollRatesModal v-model:show="showRates" :selected-month="selectedMonth" :payroll-rates="payrollRates" @saved="onRatesSaved" />
    <TaxCalcModal
      v-model:show="showTax"
      :target="taxTarget"
      :selected-month="selectedMonth"
      :initial-special-additional="taxInitialSpecial"
      :readonly="sheet?.status !== 'draft'"
      @apply="onTaxApply"
    />
    <RdAllocationModal v-model:show="showRd" :employee="rdTarget" :selected-month="selectedMonth" :projects="rdProjects" :allocations="rdAllocations" @saved="(a) => (rdAllocations = a)" />
  </div>
</template>

<style scoped>
.salary-page {
  max-width: var(--page-width);
  margin: 0 auto;
  padding: 16px 0 88px;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.header-left {
  flex: 1;
  min-width: 0;
}
.header-left h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  line-height: 1.3;
  color: var(--solo-text-primary);
}
.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
}

.tabs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.salary-tabs {
  max-width: 360px;
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .header-actions {
    justify-content: flex-start;
  }
}
</style>
