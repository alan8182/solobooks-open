<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NSpace, useMessage, NInput, NModal, NForm,
  NFormItem, NSpin, NSelect, NInputNumber,
  NDatePicker, NTag, NSwitch
} from 'naive-ui'
import dayjs from 'dayjs'
import { formatMoney } from '@/utils/format'
import {
  getEmployees, addEmployee, updateEmployee, deleteEmployee,
  getSalarySheet, updateSalaryRecords, accrueSalarySheet, paySalarySheet,
  getSalaryPaymentSuggestions, applySalaryPayment,
  getPayrollLiabilitySuggestions, applyPayrollLiability,
  getRdSalaryAllocations, updateRdSalaryAllocations,
  getPayrollRates, updatePayrollRates, previewSalaryTax,
  getDepartments, updateDepartments,
  exportSalarySheet, exportSalaryPayslip,
  type Employee, type SalaryRecord, type SalarySheet, type RdSalaryAllocation,
  type SalaryPaymentSuggestion, type PayrollLiabilitySuggestion,
  type PayrollRates, type TaxPreview, type DepartmentItem,
  paySalarySocial, paySalaryHousingFund, paySalaryTax } from '@/api/salary'
import { getRdProjects } from '@/api/rd'
import type { RdProject } from '@/api/types'
import { useEntryStore } from '@/stores/entry'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const entryStore = useEntryStore()
const { confirmDelete } = useDeleteConfirm()

const activeTab = ref('sheet')
const loading = ref(false)
const selectedMonth = ref(dayjs().format('YYYY-MM'))

// Employees Data
const employees = ref<Employee[]>([])
const showEmployeeModal = ref(false)
const isEditingEmployee = ref(false)
const editingEmployeeId = ref('')
const employeeForm = ref({
  name: '',
  department: '研发部',
  base_salary: 5000,
  social_security_base: 3000,
  is_owner: false,
  hire_date: null as string | null,
  special_additional_deduction: 0
})

// 部门字典（账套级）：名称 + 是否研发部门。
// 员工表单里可以直接输入新部门，保存时后端自动登记，不需要先来这里建。
const departments = ref<DepartmentItem[]>([])
const showDeptModal = ref(false)
const deptSaving = ref(false)
const deptDraft = ref<Array<{ name: string; isRd: boolean }>>([])

const departmentOptions = computed(() =>
  departments.value.map((item) => ({
    label: item.isRd ? `${item.name} · 研发` : item.name,
    value: item.name,
  })),
)

function isRdDepartment(name: string) {
  return Boolean(departments.value.find((item) => item.name === name)?.isRd)
}

function openDeptModal() {
  deptDraft.value = departments.value.map((item) => ({ name: item.name, isRd: Boolean(item.isRd) }))
  showDeptModal.value = true
}

function addDeptRow() {
  deptDraft.value.push({ name: '', isRd: false })
}

function removeDeptRow(index: number) {
  deptDraft.value.splice(index, 1)
}

function deptHeadcount(name: string) {
  const hit = departments.value.find((item) => item.name === name.trim())
  if (!hit) return '新部门'
  const count = Number(hit.headcount || 0)
  return count > 0 ? `${count} 人` : '无员工'
}

async function saveDepartments() {
  const rows = deptDraft.value
    .map((item) => ({ name: item.name.trim(), isRd: item.isRd }))
    .filter((item) => item.name)
  if (!rows.length) {
    message.warning('至少保留一个部门')
    return
  }
  if (new Set(rows.map((item) => item.name)).size !== rows.length) {
    message.warning('部门名称不能重复')
    return
  }
  deptSaving.value = true
  try {
    const data: any = await updateDepartments(rows)
    departments.value = data.departments || []
    showDeptModal.value = false
    message.success('部门已保存')
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存部门失败')
  } finally {
    deptSaving.value = false
  }
}

// Salary Sheet Data
const sheet = ref<SalarySheet | null>(null)
const sheetRecords = ref<SalaryRecord[]>([])
// 实际发薪日：默认今天；真实发薪多在工资所属期次月，用户可改。
// 决定「发放工资记账」凭证的发生日期（期间归属）。
const payDate = ref(dayjs().format('YYYY-MM-DD'))
const rdProjects = ref<RdProject[]>([])
const rdAllocations = ref<RdSalaryAllocation[]>([])
const paymentSuggestions = ref<SalaryPaymentSuggestion[]>([])
const liabilitySuggestions = ref<PayrollLiabilitySuggestion[]>([])
const applyingStatementId = ref('')
const applyingLiabilityKey = ref('')
const showRdAllocationModal = ref(false)
const rdAllocationEmployee = ref<SalaryRecord | null>(null)
const rdAllocationDraft = ref<RdSalaryAllocation[]>([])

const rdProjectOptions = computed(() => rdProjects.value
  .filter((project) => project.status === 'active')
  .map((project) => ({ label: `${project.code} ${project.name}`, value: project.id })))

function liabilitySettled(status?: string) {
  return status === 'posted' || status === 'not_required'
}

const allLiabilitiesSettled = computed(() =>
  liabilitySettled(sheet.value?.ssPaymentStatus)
  && liabilitySettled(sheet.value?.hfPaymentStatus)
  && liabilitySettled(sheet.value?.taxPaymentStatus),
)

// Load Employees List
async function fetchEmployees() {
  loading.value = true
  try {
    const data: any = await getEmployees()
    employees.value = data
  } catch {
    message.error('加载员工列表失败')
  } finally {
    loading.value = false
  }
}

// Load Salary Sheet for selectedMonth
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
    sheetRecords.value = data.records || []
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

async function applyLiabilitySuggestion(item: PayrollLiabilitySuggestion) {
  const key = `${item.kind}-${item.statement.id}`
  applyingLiabilityKey.value = key
  try {
    const result: any = await applyPayrollLiability(item.month, item.kind, item.statement.id)
    await entryStore.fetchEntries()
    await fetchPaymentSuggestions()
    if (item.month === selectedMonth.value) {
      sheet.value = result
      sheetRecords.value = result.records || []
    }
    message.success(
      result.linked
        ? `${item.month} ${item.label}已按 ${item.statement.date} 流水缴纳并核销`
        : `${item.label}已缴纳，但流水自动核销失败：${result.linkError || '请到银行流水页手工关联'}`,
    )
  } catch (error: any) {
    const detail = error.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '缴纳核销失败')
  } finally {
    applyingLiabilityKey.value = ''
  }
}

async function applySuggestion(item: SalaryPaymentSuggestion) {
  applyingStatementId.value = item.statement.id
  try {
    const result: any = await applySalaryPayment(item.month, item.statement.id)
    await entryStore.fetchEntries()
    await fetchPaymentSuggestions()
    if (item.month === selectedMonth.value) {
      sheet.value = result
      sheetRecords.value = result.records || []
    }
    message.success(
      result.linked
        ? `${item.month} 工资已按 ${item.statement.date} 流水发放并核销`
        : `工资已发放，但流水自动核销失败：${result.linkError || '请到银行流水页手工关联'}`,
    )
  } catch (error: any) {
    const detail = error.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '工资发放核销失败')
  } finally {
    applyingStatementId.value = ''
  }
}

// Watch month change to reload salary sheets
watch(selectedMonth, () => {
  payDate.value = dayjs().format('YYYY-MM-DD')
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

// Trigger load on tab change
watch(activeTab, (tab) => {
  if (tab === 'roster') {
    fetchEmployees()
  } else {
    fetchSalarySheet()
  }
})

onMounted(() => {
  // 深链改了月份时由 selectedMonth 的 watch 负责加载，避免重复请求
  if (!applyMonthQuery()) {
    fetchSalarySheet()
    fetchPaymentSuggestions()
  }
})

function employeeRdAllocations(employeeId: string) {
  return rdAllocations.value.filter((item) => item.employeeId === employeeId)
}

// 只有研发部门员工才需要分配研发项目工时；其他部门显示「—」，避免出现
// 看起来像报错的「⚠ 未配置」。研发属性来自账套部门字典，不再写死「研发部」。
function isRdEmployee(row: SalaryRecord) {
  const department = employees.value.find((item) => item.id === row.employee_id)?.department || ''
  return isRdDepartment(department)
}

function rdAllocationSummary(employeeId: string) {
  const rows = employeeRdAllocations(employeeId)
  if (!rows.length) return '未配置'
  const total = rows.reduce((sum, row) => sum + Number(row.allocationRate || 0), 0)
  return `${rows.length}个项目 · ${(total * 100).toFixed(0)}%`
}

function openRdAllocation(row: SalaryRecord) {
  rdAllocationEmployee.value = row
  rdAllocationDraft.value = employeeRdAllocations(row.employee_id).map((item) => ({ ...item }))
  if (!rdAllocationDraft.value.length) {
    rdAllocationDraft.value = [{ employeeId: row.employee_id, projectId: '', allocationRate: 1, allocationBasis: '' }]
  }
  showRdAllocationModal.value = true
}

function addRdAllocationRow() {
  if (!rdAllocationEmployee.value) return
  rdAllocationDraft.value.push({
    employeeId: rdAllocationEmployee.value.employee_id,
    projectId: '',
    allocationRate: 0,
    allocationBasis: '',
  })
}

function removeRdAllocationRow(index: number) {
  rdAllocationDraft.value.splice(index, 1)
}

async function saveRdAllocation() {
  if (!rdAllocationEmployee.value) return
  const total = rdAllocationDraft.value.reduce((sum, row) => sum + Number(row.allocationRate || 0), 0)
  if (rdAllocationDraft.value.some((row) => !row.projectId || !row.allocationBasis.trim())) {
    message.warning('每条分配都必须选择项目并填写工时依据')
    return
  }
  if (total > 1.000001) {
    message.warning('项目工时比例合计不能超过100%')
    return
  }
  const employeeId = rdAllocationEmployee.value.employee_id
  const next = [
    ...rdAllocations.value.filter((item) => item.employeeId !== employeeId),
    ...rdAllocationDraft.value,
  ]
  try {
    const response = await updateRdSalaryAllocations(selectedMonth.value, next)
    rdAllocations.value = response.allocations
    showRdAllocationModal.value = false
    message.success('研发项目工时分配已保存')
  } catch (error: any) {
    message.error(error.response?.data?.detail?.message || error.response?.data?.detail || '保存工时分配失败')
  }
}

// --- Employee Form Actions ---

function openAddEmployeeModal() {
  isEditingEmployee.value = false
  editingEmployeeId.value = ''
  employeeForm.value = {
    name: '',
    department: '研发部',
    base_salary: 6000,
    social_security_base: 4000,
    is_owner: false,
    hire_date: null,
    special_additional_deduction: 0
  }
  showEmployeeModal.value = true
}

function openEditEmployeeModal(emp: Employee) {
  isEditingEmployee.value = true
  editingEmployeeId.value = emp.id || ''
  employeeForm.value = {
    name: emp.name,
    department: emp.department,
    base_salary: emp.base_salary,
    social_security_base: emp.social_security_base,
    is_owner: Boolean(emp.is_owner),
    hire_date: emp.hire_date || null,
    special_additional_deduction: Number(emp.special_additional_deduction || 0)
  }
  showEmployeeModal.value = true
}

async function saveEmployee() {
  if (!employeeForm.value.name.trim()) {
    message.warning('请输入员工姓名')
    return
  }
  try {
    const payload = {
      ...employeeForm.value,
      hire_date: employeeForm.value.hire_date || null,
    }
    if (isEditingEmployee.value) {
      await updateEmployee(editingEmployeeId.value, payload)
      message.success('更新员工成功')
    } else {
      await addEmployee(payload)
      message.success('新增员工成功')
    }
    showEmployeeModal.value = false
    await fetchEmployees()
  } catch {
    message.error('保存员工失败')
  }
}

async function archiveEmp(id: string) {
  try {
    await deleteEmployee(id)
    message.success('员工已归档')
    await fetchEmployees()
  } catch {
    message.error('归档员工失败')
  }
}

// --- Salary Sheet Calculations & Saving ---

// Recalculates Net Salary & Total Cost locally on input change
function handleRecordAmountChange(row: SalaryRecord) {
  const base = Number(row.base_salary || 0)
  const bonus = Number(row.bonus || 0)
  const allowance = Number(row.allowance || 0)
  const deduction = Number(row.deduction || 0)
  const p_ss = Number(row.personal_social_security || 0)
  const p_tax = Number(row.personal_tax || 0)
  const c_ss = Number(row.company_social_security || 0)

  const gross = base + bonus + allowance - deduction
  row.net_salary = Math.round((gross - p_ss - p_tax) * 100) / 100
  row.total_cost = Math.round((gross + c_ss) * 100) / 100
}

const sheetTotals = computed(() => {
  let netTotal = 0
  let costTotal = 0
  let employeeCount = sheetRecords.value.length

  for (const r of sheetRecords.value) {
    netTotal += r.net_salary
    costTotal += r.total_cost
  }
  return {
    net: netTotal,
    cost: costTotal,
    count: employeeCount
  }
})

// 明细合计行：应发 / 个人代扣 / 个税 / 单位社保，供核对与导出对齐
const recordTotals = computed(() => {
  let gross = 0
  let personal = 0
  let tax = 0
  let company = 0
  for (const r of sheetRecords.value) {
    gross += Number(r.base_salary || 0) + Number(r.bonus || 0) + Number(r.allowance || 0) - Number(r.deduction || 0)
    personal += Number(r.personal_social_security || 0)
    tax += Number(r.personal_tax || 0)
    company += Number(r.company_social_security || 0)
  }
  return {
    gross: Math.round(gross * 100) / 100,
    personal: Math.round(personal * 100) / 100,
    tax: Math.round(tax * 100) / 100,
    company: Math.round(company * 100) / 100,
  }
})

// 合并列拆解：个人/单位「社保公积金」各是多少社保、多少公积金。
// 表头只有一列合计数，用户看不出 925 里哪部分是社保——这里直接摊开。
const withholdingBreakdown = computed(() => {
  const personalTotal = recordTotals.value.personal
  const companyTotal = recordTotals.value.company
  const personalHf = Math.max(Number(sheet.value?.personal_housing_fund_total || 0), 0)
  const companyHf = Math.max(Number(sheet.value?.company_housing_fund_total || 0), 0)
  return {
    personalTotal,
    personalHf,
    personalSs: Math.max(Math.round((personalTotal - personalHf) * 100) / 100, 0),
    companyTotal,
    companyHf,
    companySs: Math.max(Math.round((companyTotal - companyHf) * 100) / 100, 0),
  }
})

// --- 账套社保公积金比例 ---
// 公积金 8% / 12% 因城市与单位而异，费率错了「缴公积金」凭证就会少算、
// 「缴社保」凭证会多算，与银行实际扣款对不上。
const showRatesModal = ref(false)
const ratesSaving = ref(false)
const ratesLoading = ref(false)
const payrollRates = ref<PayrollRates | null>(null)
const ratesRecalculate = ref(true)
// 表单用百分数（8 表示 8%），提交时除以 100
const ratesForm = ref({
  socialPersonal: 10.5,
  socialCompany: 25,
  housingPersonal: 8,
  housingCompany: 8,
})

async function openRatesModal() {
  showRatesModal.value = true
  ratesLoading.value = true
  try {
    const data: any = await getPayrollRates()
    payrollRates.value = data
    ratesForm.value = {
      socialPersonal: Number(data.rates.socialPersonal) * 100,
      socialCompany: Number(data.rates.socialCompany) * 100,
      housingPersonal: Number(data.rates.housingPersonal) * 100,
      housingCompany: Number(data.rates.housingCompany) * 100,
    }
  } catch {
    message.error('加载社保公积金比例失败')
  } finally {
    ratesLoading.value = false
  }
}

async function saveRates() {
  const values = Object.values(ratesForm.value)
  if (values.some((value) => Number.isNaN(value) || value < 0 || value > 100)) {
    message.warning('比例需落在 0~100 之间（8% 请填 8）')
    return
  }
  ratesSaving.value = true
  try {
    const result: any = await updatePayrollRates(
      {
        socialPersonal: ratesForm.value.socialPersonal / 100,
        socialCompany: ratesForm.value.socialCompany / 100,
        housingPersonal: ratesForm.value.housingPersonal / 100,
        housingCompany: ratesForm.value.housingCompany / 100,
      },
      { month: selectedMonth.value, recalculate: ratesRecalculate.value },
    )
    payrollRates.value = result
    showRatesModal.value = false
    if (ratesRecalculate.value) {
      await fetchSalarySheet()
      message.success('比例已保存，本月草稿工资表已按新比例重算')
    } else {
      message.success('比例已保存，下次新建工资表时生效')
    }
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存比例失败')
  } finally {
    ratesSaving.value = false
  }
}

function ratesPercent(value: number) {
  return `${(value * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

// --- 个税试算（累计预扣法） ---
const showTaxModal = ref(false)
const taxLoading = ref(false)
const taxTarget = ref<SalaryRecord | null>(null)
const taxPreview = ref<TaxPreview | null>(null)
const taxForm = ref({ specialAdditional: 0, otherDeduction: 0 })

const currentGrossOfTarget = computed(() => {
  const row = taxTarget.value
  if (!row) return 0
  return Math.max(
    Number(row.base_salary || 0) + Number(row.bonus || 0)
      + Number(row.allowance || 0) - Number(row.deduction || 0),
    0,
  )
})

function openTaxCalc(row: SalaryRecord) {
  taxTarget.value = row
  const employee = employees.value.find((item) => item.id === row.employee_id)
  taxForm.value = {
    specialAdditional: Number(employee?.special_additional_deduction || 0),
    otherDeduction: 0,
  }
  taxPreview.value = null
  showTaxModal.value = true
  runTaxPreview()
}

async function runTaxPreview() {
  const row = taxTarget.value
  if (!row) return
  taxLoading.value = true
  try {
    const gross = Number(row.base_salary || 0) + Number(row.bonus || 0)
      + Number(row.allowance || 0) - Number(row.deduction || 0)
    const data: any = await previewSalaryTax({
      month: selectedMonth.value,
      employeeId: row.employee_id,
      gross: Math.max(gross, 0),
      withheld: Number(row.personal_social_security || 0),
      specialAdditional: taxForm.value.specialAdditional,
      otherDeduction: taxForm.value.otherDeduction,
    })
    taxPreview.value = data
  } catch (error: any) {
    taxPreview.value = null
    message.error(error.response?.data?.detail || '个税试算失败')
  } finally {
    taxLoading.value = false
  }
}

function applyTaxResult() {
  const row = taxTarget.value
  if (!row || !taxPreview.value) return
  row.personal_tax = Number(taxPreview.value.taxDue)
  handleRecordAmountChange(row)
  showTaxModal.value = false
  message.success(`已填入本期应扣个税 ${formatMoney(Number(taxPreview.value.taxDue))}，记得保存草稿`)
}

// --- 导出 ---
const exporting = ref(false)
const exportingRecordId = ref('')

function downloadBlob(blob: unknown, filename: string) {
  const url = URL.createObjectURL(blob as Blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function exportSheet() {
  exporting.value = true
  try {
    // 导出读的是后端已存数据，草稿先静默保存，避免导出旧数
    if (sheet.value?.status === 'draft') {
      const saved: any = await updateSalaryRecords(selectedMonth.value, sheetRecords.value)
      sheet.value = saved
      sheetRecords.value = saved.records || []
    }
    const blob: any = await exportSalarySheet(selectedMonth.value)
    downloadBlob(blob, `工资表-${selectedMonth.value}.csv`)
  } catch (error: any) {
    message.error(error.response?.data?.detail || '导出工资表失败')
  } finally {
    exporting.value = false
  }
}

async function exportPayslip(row: SalaryRecord) {
  exportingRecordId.value = row.id
  try {
    const blob: any = await exportSalaryPayslip(selectedMonth.value, row.id)
    downloadBlob(blob, `工资条-${selectedMonth.value}-${row.employee_name}.csv`)
  } catch (error: any) {
    message.error(error.response?.data?.detail || '导出工资条失败')
  } finally {
    exportingRecordId.value = ''
  }
}

async function saveRecords() {
  if (!selectedMonth.value) return
  loading.value = true
  try {
    const data: any = await updateSalaryRecords(selectedMonth.value, sheetRecords.value)
    sheet.value = data
    sheetRecords.value = data.records || []
    message.success('保存工资条成功')
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存工资条失败')
  } finally {
    loading.value = false
  }
}

async function accrueSheet() {
  if (!selectedMonth.value) return
  loading.value = true
  try {
    // Save records first
    await updateSalaryRecords(selectedMonth.value, sheetRecords.value)
    await updateRdSalaryAllocations(selectedMonth.value, rdAllocations.value)
    // Post accrual
    const data: any = await accrueSalarySheet(selectedMonth.value)
    sheet.value = data
    sheetRecords.value = data.records || []
    await entryStore.fetchEntries()
    await fetchPaymentSuggestions()
    const accruedStatus = data.status
    message.success(
      accruedStatus === 'accrued'
        ? '工资计提凭证生成成功，已列入当月研发/管理成本'
        : '计提凭证已创建，待复核通过后计入成本（可在审核中心复核）',
    )
  } catch (error: any) {
    message.error(error.response?.data?.detail || '计提失败')
  } finally {
    loading.value = false
  }
}

async function paySheet() {
  if (!selectedMonth.value) return
  loading.value = true
  try {
    const data: any = await paySalarySheet(selectedMonth.value, payDate.value)
    sheet.value = data
    sheetRecords.value = data.records || []
    await entryStore.fetchEntries()
    await fetchPaymentSuggestions()
    message.success(`工资发放记账成功（发薪日 ${payDate.value}），已生成银行存款划扣凭证`)
  } catch (error: any) {
    message.error(error.response?.data?.detail || '发放失败')
  } finally {
    loading.value = false
  }
}

async function payLiability(kind: 'social' | 'housing' | 'tax') {
  if (!selectedMonth.value) return
  loading.value = true
  try {
    const api = kind === 'social'
      ? paySalarySocial
      : kind === 'housing'
        ? paySalaryHousingFund
        : paySalaryTax
    const data: any = await api(selectedMonth.value, payDate.value)
    sheet.value = data
    await entryStore.fetchEntries()
    await fetchPaymentSuggestions()
    message.success(
      kind === 'social'
        ? '社保缴纳记账成功'
        : kind === 'housing'
          ? '公积金缴纳记账成功'
          : '个税缴纳记账成功',
    )
  } catch (error: any) {
    message.error(error.response?.data?.detail || '操作失败')
  } finally {
    loading.value = false
  }
}

function viewVoucher(voucherId: string) {
  router.push(`/ledger/${voucherId}`)
}

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([fetchSalarySheet(), fetchPaymentSuggestions()]))
</script>

<template>
  <div class="salary-page">
    <!-- 顶部导航与工作台抬头（遵循 layout.css .page-header-left / .page-header-right 规范） -->
    <div class="page-header">
      <div class="page-header-left">
        <button type="button" class="back-btn" @click="router.push('/mine')">
          <i class="ri-arrow-left-line" />
          <span>返回账套与设置</span>
        </button>
        <div class="title-section">
          <div class="title-with-badge">
            <h2>薪酬与社保管理</h2>
            <span
              v-if="sheet"
              class="status-pill"
              :class="{
                'pill-paid': sheet.status === 'paid',
                'pill-accrued': sheet.status === 'accrued',
                'pill-pending': sheet.status === 'pending_review',
                'pill-draft':
                  sheet.status !== 'paid' &&
                  sheet.status !== 'accrued' &&
                  sheet.status !== 'pending_review',
              }"
            >
              <i
                :class="
                  sheet.status === 'paid'
                    ? 'ri-checkbox-circle-line'
                    : sheet.status === 'accrued'
                      ? 'ri-time-line'
                      : sheet.status === 'pending_review'
                        ? 'ri-shield-flash-line'
                        : 'ri-draft-line'
                "
              />
              {{
                sheet.status === 'paid'
                  ? '已发放记账'
                  : sheet.status === 'accrued'
                    ? '已计提记账'
                    : sheet.status === 'pending_review'
                      ? '计提待复核'
                      : '草稿（待计提）'
              }}
            </span>
          </div>
          <p class="subtitle">
            管理在职员工花名册，按月核算计提工资、社保公积金及个税并自动生成记账凭证。
          </p>
        </div>
      </div>

      <div class="page-header-right">
        <div class="month-selector-wrap">
          <span class="control-label">结算月份</span>
          <NDatePicker
            v-model:formatted-value="selectedMonth"
            value-format="yyyy-MM"
            type="month"
            :clearable="false"
            size="medium"
            class="month-picker"
          />
        </div>

        <div class="header-divider" />

        <NButton type="primary" size="medium" @click="openAddEmployeeModal">
          <template #icon><i class="ri-user-add-line" /></template>
          新增员工
        </NButton>
      </div>
    </div>

    <!-- 薪酬汇总监控栏 -->
    <div class="salary-summary-bar">
      <div class="summary-metrics">
        <div class="metric-item">
          <span class="metric-label">本月应发在职人数</span>
          <span class="metric-value">
            {{ sheetTotals.count }}
            <small class="metric-unit">人（花名册共 {{ employees.length }} 人）</small>
          </span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">本月实发工资总额</span>
          <span class="metric-value text-income">{{ formatMoney(sheetTotals.net) }}</span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">企业总人工成本（含单位社保）</span>
          <span class="metric-value text-primary">{{ formatMoney(sheetTotals.cost) }}</span>
        </div>
      </div>

      <div class="summary-right">
        <span v-if="sheet?.paymentStatus === 'posted' && allLiabilitiesSettled" class="verdict-badge badge-paid">
          <i class="ri-shield-check-line" />
          本月工资、社保、公积金、个税均已记账完毕
        </span>
        <span v-else-if="sheet?.paymentStatus === 'posted'" class="verdict-badge badge-accrued">
          <i class="ri-information-line" />
          工资已发放 · 待完成社保、公积金与个税缴纳记账
        </span>
        <span v-else-if="sheet?.paymentStatus === 'pending_review'" class="verdict-badge badge-pending">
          <i class="ri-shield-flash-line" />
          工资发放凭证待复核 · 复核通过后才算发放完成
        </span>
        <span v-else-if="sheet?.status === 'accrued'" class="verdict-badge badge-accrued">
          <i class="ri-time-line" />
          人工成本已计提 · 待发放实发工资
        </span>
        <span v-else-if="sheet?.status === 'pending_review'" class="verdict-badge badge-pending">
          <i class="ri-shield-flash-line" />
          计提凭证待复核 · 复核通过后才能发放工资与缴纳社保、个税
        </span>
        <span v-else class="verdict-badge badge-draft">
          <i class="ri-edit-circle-line" />
          {{ sheetRecords.length > 0 ? '草稿可编辑 · 核对无误后点击底部「计提记账」' : '请先录入在职员工以生成本月工资表' }}
        </span>
      </div>
    </div>

    <!-- 主工作台卡片 -->
    <NSpin :show="loading">
      <div class="surface-card salary-workspace-card">
        <!-- 顶层切换栏 -->
        <div class="sheet-tabs-bar">
          <div class="sheet-tabs">
            <button
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'sheet' }"
              @click="activeTab = 'sheet'"
            >
              <i class="ri-file-list-3-line" />
              <span>工资表结算（{{ selectedMonth }}）</span>
              <span class="tab-count">{{ sheetRecords.length }}</span>
            </button>

            <button
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'roster' }"
              @click="activeTab = 'roster'"
            >
              <i class="ri-team-line" />
              <span>员工花名册</span>
              <span class="tab-count">{{ employees.length }}</span>
            </button>
          </div>

          <div class="toolbar-right">
            <span v-if="activeTab === 'sheet'" class="rule-hint">
              <i class="ri-information-line" />
              企业成本 = 应发工资 + 单位承担社保公积金；研发部人员可分配研发项目工时
            </span>
            <span v-else class="rule-hint">
              <i class="ri-information-line" />
              花名册变更将自动同步至未锁定的月度工资表草稿
            </span>
            <NSpace size="small">
              <NButton
                v-if="activeTab === 'sheet'"
                size="small"
                secondary
                :loading="exporting"
                @click="exportSheet"
              >
                <template #icon><i class="ri-download-2-line" /></template>
                导出工资表
              </NButton>
              <NButton size="small" quaternary @click="openDeptModal">
                <template #icon><i class="ri-team-line" /></template>
                部门设置
              </NButton>
              <NButton v-if="activeTab === 'sheet'" size="small" quaternary @click="openRatesModal">
                <template #icon><i class="ri-percent-line" /></template>
                社保公积金比例
              </NButton>
            </NSpace>
          </div>
        </div>

        <!-- 视图 1：工资表结算 -->
        <div v-if="activeTab === 'sheet'" class="workspace-body">
          <div v-if="liabilitySuggestions.length" class="payment-suggestion-panel">
            <div class="payment-suggestion-head">
              <strong><i class="ri-bank-line" /> 银行流水发现待缴社保/公积金/个税</strong>
              <span>流水日期决定缴纳凭证日期；费用仍归属对应工资月份</span>
            </div>
            <div
              v-for="item in liabilitySuggestions"
              :key="`${item.kind}-${item.statement.id}`"
              class="payment-suggestion-row"
            >
              <div class="payment-suggestion-main">
                <span class="payment-suggestion-month">{{ item.month }} {{ item.label }}</span>
                <span class="payment-suggestion-amount">应缴 ¥{{ item.amount }}</span>
                <NTag size="tiny" :type="item.confidence === 'high' ? 'success' : 'warning'" :bordered="false">
                  {{ item.confidence === 'high' ? '高置信' : '请确认' }}
                </NTag>
              </div>
              <div class="payment-suggestion-desc">
                {{ item.statement.date }} · {{ item.statement.description }}
              </div>
              <div class="payment-suggestion-reason">{{ item.reason }}</div>
              <NButton
                size="small"
                type="primary"
                :loading="applyingLiabilityKey === `${item.kind}-${item.statement.id}`"
                @click="applyLiabilitySuggestion(item)"
              >
                确认缴纳并核销
              </NButton>
            </div>
          </div>

          <div v-if="paymentSuggestions.length" class="payment-suggestion-panel">
            <div class="payment-suggestion-head">
              <strong><i class="ri-bank-line" /> 银行流水发现待发工资</strong>
              <span>流水日期决定发放凭证日期；工资费用仍归属 {{ paymentSuggestions.map(item => item.month).join('、') }}</span>
            </div>
            <div
              v-for="item in paymentSuggestions"
              :key="item.statement.id"
              class="payment-suggestion-row"
            >
              <div class="payment-suggestion-main">
                <span class="payment-suggestion-month">{{ item.month }} 工资</span>
                <span class="payment-suggestion-amount">实发 ¥{{ item.netSalary }}</span>
                <NTag size="tiny" :type="item.confidence === 'high' ? 'success' : 'warning'" :bordered="false">
                  {{ item.confidence === 'high' ? '高置信' : '请确认' }}
                </NTag>
              </div>
              <div class="payment-suggestion-desc">
                {{ item.statement.date }} · {{ item.statement.description }}
              </div>
              <div class="payment-suggestion-reason">{{ item.reason }}</div>
              <NButton
                size="small"
                type="primary"
                :loading="applyingStatementId === item.statement.id"
                @click="applySuggestion(item)"
              >
                确认发放并核销
              </NButton>
            </div>
          </div>

          <div v-if="sheetRecords.length > 0" class="table-container">
            <table class="salary-table">
              <thead>
                <tr>
                  <th style="min-width: 84px">姓名</th>
                  <th style="min-width: 84px">部门</th>
                  <th style="min-width: 136px">研发项目工时</th>
                  <th class="col-amount" style="min-width: 96px">基本工资</th>
                  <th class="col-amount" style="min-width: 100px">奖金</th>
                  <th class="col-amount" style="min-width: 100px">补助/津贴</th>
                  <th class="col-amount" style="min-width: 100px">考勤扣款</th>
                  <th class="col-amount" style="min-width: 112px">个人社保公积金</th>
                  <th class="col-amount" style="min-width: 96px">个税代扣</th>
                  <th class="col-amount" style="min-width: 112px">单位社保公积金</th>
                  <th class="col-amount" style="min-width: 104px">实发工资</th>
                  <th class="col-amount" style="min-width: 104px">企业总成本</th>
                  <th style="min-width: 76px; text-align: center">工资条</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in sheetRecords" :key="row.id">
                  <td class="emp-name">{{ row.employee_name }}</td>
                  <td>
                    <NTag
                      size="small"
                      :bordered="false"
                      :type="
                        isRdEmployee(row)
                          ? 'info'
                          : 'default'
                      "
                    >
                      {{ employees.find(e => e.id === row.employee_id)?.department || '行政部' }}
                    </NTag>
                  </td>
                  <td>
                    <NButton
                      v-if="isRdEmployee(row)"
                      size="tiny"
                      :type="employeeRdAllocations(row.employee_id).length ? 'primary' : 'default'"
                      :secondary="!employeeRdAllocations(row.employee_id).length"
                      :disabled="sheet?.status !== 'draft'"
                      @click="openRdAllocation(row)"
                    >
                      <template #icon><i class="ri-flask-line" /></template>
                      {{ rdAllocationSummary(row.employee_id) }}
                    </NButton>
                    <span
                      v-else
                      class="rd-not-applicable"
                      title="非研发部门无需分配研发项目工时"
                    >—</span>
                  </td>
                  <td class="col-amount num-cell">{{ formatMoney(row.base_salary) }}</td>

                  <!-- 草稿状态下可编辑字段 -->
                  <td class="col-amount">
                    <NInputNumber
                      v-model:value="row.bonus"
                      :min="0"
                      :precision="2"
                      :show-button="false"
                      size="small"
                      class="amount-input"
                      :disabled="sheet?.status !== 'draft'"
                      @update:value="handleRecordAmountChange(row)"
                    />
                  </td>
                  <td class="col-amount">
                    <NInputNumber
                      v-model:value="row.allowance"
                      :min="0"
                      :precision="2"
                      :show-button="false"
                      size="small"
                      class="amount-input"
                      :disabled="sheet?.status !== 'draft'"
                      @update:value="handleRecordAmountChange(row)"
                    />
                  </td>
                  <td class="col-amount">
                    <NInputNumber
                      v-model:value="row.deduction"
                      :min="0"
                      :precision="2"
                      :show-button="false"
                      size="small"
                      class="amount-input"
                      :disabled="sheet?.status !== 'draft'"
                      @update:value="handleRecordAmountChange(row)"
                    />
                  </td>
                  <td class="col-amount">
                    <NInputNumber
                      v-model:value="row.personal_social_security"
                      :min="0"
                      :precision="2"
                      :show-button="false"
                      size="small"
                      class="amount-input"
                      :disabled="sheet?.status !== 'draft'"
                      @update:value="handleRecordAmountChange(row)"
                    />
                  </td>
                  <td class="col-amount">
                    <div class="tax-cell">
                      <NInputNumber
                        v-model:value="row.personal_tax"
                        :min="0"
                        :precision="2"
                        :show-button="false"
                        size="small"
                        class="amount-input"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                      <NButton
                        v-if="sheet?.status === 'draft'"
                        size="tiny"
                        quaternary
                        class="tax-calc-btn"
                        title="按累计预扣法试算本期应扣个税"
                        @click="openTaxCalc(row)"
                      >
                        <template #icon><i class="ri-calculator-line" /></template>
                      </NButton>
                    </div>
                  </td>
                  <td class="col-amount">
                    <NInputNumber
                      v-model:value="row.company_social_security"
                      :min="0"
                      :precision="2"
                      :show-button="false"
                      size="small"
                      class="amount-input"
                      :disabled="sheet?.status !== 'draft'"
                      @update:value="handleRecordAmountChange(row)"
                    />
                  </td>

                  <!-- 自动核算结果 -->
                  <td class="col-amount net-salary">{{ formatMoney(row.net_salary) }}</td>
                  <td class="col-amount total-cost">{{ formatMoney(row.total_cost) }}</td>
                  <td style="text-align: center">
                    <NButton
                      size="tiny"
                      quaternary
                      :loading="exportingRecordId === row.id"
                      title="导出该员工工资条"
                      @click="exportPayslip(row)"
                    >
                      <template #icon><i class="ri-file-download-line" /></template>
                    </NButton>
                  </td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="totals-row">
                  <td colspan="7">合计 · {{ sheetRecords.length }} 人</td>
                  <td class="col-amount">{{ formatMoney(recordTotals.personal) }}</td>
                  <td class="col-amount">{{ formatMoney(recordTotals.tax) }}</td>
                  <td class="col-amount">{{ formatMoney(recordTotals.company) }}</td>
                  <td class="col-amount net-salary">{{ formatMoney(sheetTotals.net) }}</td>
                  <td class="col-amount total-cost">{{ formatMoney(sheetTotals.cost) }}</td>
                  <td></td>
                </tr>
              </tfoot>
            </table>
            <p v-if="recordTotals.personal > 0" class="withholding-hint">
              <i class="ri-information-line" />
              个人代扣 {{ formatMoney(withholdingBreakdown.personalTotal) }} = 社保
              {{ formatMoney(withholdingBreakdown.personalSs) }} + 公积金
              {{ formatMoney(withholdingBreakdown.personalHf) }}；单位
              {{ formatMoney(withholdingBreakdown.companyTotal) }} = 社保
              {{ formatMoney(withholdingBreakdown.companySs) }} + 公积金
              {{ formatMoney(withholdingBreakdown.companyHf) }}。
              <template v-if="payrollRates && !payrollRates.isCustomized">
                当前按默认杭州口径，比例不符请点右上「社保公积金比例」。
              </template>
            </p>
          </div>

          <div v-else class="empty-state">
            <div class="empty-icon-box">
              <i class="ri-user-add-line" />
            </div>
            <h4 class="empty-title">当月暂无在职员工工资记录</h4>
            <p class="empty-desc">
              请先在「员工花名册」中录入员工姓名、部门、基本工资与社保基数，系统将自动生成月度工资结算表。
            </p>
            <div class="empty-actions">
              <NButton type="primary" size="medium" @click="openAddEmployeeModal">
                <template #icon><i class="ri-user-add-line" /></template>
                立即添加首位员工
              </NButton>
              <NButton secondary size="medium" @click="activeTab = 'roster'">
                查看员工花名册
              </NButton>
            </div>
          </div>

          <!-- 底部结算动作栏 -->
          <div v-if="sheet && sheetRecords.length > 0" class="sheet-footer">
            <div class="footer-left-hint">
              <span v-if="sheet.status === 'draft'" class="hint-text">
                <i class="ri-information-line" />
                步骤 1/2：确认工资与社保明细后点击「计提记账」，系统将自动生成应付职工薪酬凭证。
              </span>
              <span v-else-if="sheet.status === 'pending_review'" class="hint-text">
                <i class="ri-shield-flash-line" />
                计提凭证已提交审核中心，复核通过后即可发放工资并缴纳社保、个税。
              </span>
              <span v-else-if="sheet.paymentStatus === 'pending_review'" class="hint-text">
                <i class="ri-shield-flash-line" />
                工资发放凭证已提交审核中心，复核通过后才算发放完成。
              </span>
              <span v-else-if="allLiabilitiesSettled" class="paid-desc">
                <i class="ri-checkbox-circle-fill" />
                本月工资、社保、公积金及个税均已完成记账。
              </span>
              <span v-else class="hint-text">
                <i class="ri-information-line" />
                步骤 2/2：请依次完成实发工资发放、社保、公积金及个税缴纳记账，清理挂账余额。
              </span>
            </div>

            <NSpace size="small">
              <!-- DRAFT STATE -->
              <template v-if="sheet.status === 'draft'">
                <NButton secondary @click="saveRecords">
                  <template #icon><i class="ri-save-line" /></template>
                  保存草稿修改
                </NButton>
                <NButton type="primary" @click="accrueSheet">
                  <template #icon><i class="ri-check-double-line" /></template>
                  计提记账（列入当月成本）
                </NButton>
              </template>

              <!-- ACCRUED / PAID / PENDING_REVIEW STATE -->
              <template v-if="sheet.status === 'accrued' || sheet.status === 'paid' || sheet.status === 'pending_review'">
                <NButton v-if="sheet.accrual_voucher_id" secondary @click="viewVoucher(sheet.accrual_voucher_id!)">
                  查看计提凭证
                </NButton>

                <!-- 实际发薪/缴纳日：决定发放、社保、公积金、个税凭证的发生日期 -->
                <span v-if="sheet.status === 'accrued' || sheet.status === 'paid'" class="pay-date-wrap">
                  <span class="control-label">实际发薪/缴纳日</span>
                  <NDatePicker
                    v-model:formatted-value="payDate"
                    value-format="yyyy-MM-dd"
                    type="date"
                    :clearable="false"
                    size="small"
                    class="pay-date-picker"
                  />
                </span>
                <NButton
                  v-if="sheet.status === 'accrued' && !sheet.payment_voucher_id"
                  type="primary"
                  @click="paySheet"
                >
                  发放工资记账（生成划扣凭证）
                </NButton>
                <NButton
                  v-else-if="sheet.paymentStatus === 'posted' && sheet.payment_voucher_id"
                  secondary
                  @click="viewVoucher(sheet.payment_voucher_id!)"
                >
                  查看发放凭证
                </NButton>
                <NButton
                  v-else-if="sheet.paymentStatus === 'pending_review'"
                  secondary
                  disabled
                >
                  发放凭证待复核
                </NButton>

                <!-- 待复核期间不能发放/缴纳：后端会因状态不符拒绝 -->
                <template v-if="sheet.status !== 'pending_review'">
                  <NButton
                    v-if="sheet.ssPaymentStatus === 'unpaid'"
                    secondary
                    type="primary"
                    @click="payLiability('social')"
                  >
                    缴纳社保{{ sheet.socialSecurityDue ? `（¥${sheet.socialSecurityDue}）` : '' }}
                  </NButton>
                  <NButton
                    v-else-if="sheet.ssPaymentStatus === 'pending_review'"
                    secondary
                    disabled
                  >
                    社保凭证待复核
                  </NButton>
                  <NButton
                    v-else-if="sheet.ss_paid_voucher_id"
                    secondary
                    @click="viewVoucher(sheet.ss_paid_voucher_id!)"
                  >
                    查看社保凭证
                  </NButton>

                  <NButton
                    v-if="sheet.hfPaymentStatus === 'unpaid'"
                    secondary
                    type="primary"
                    @click="payLiability('housing')"
                  >
                    缴纳公积金{{ sheet.housingFundDue ? `（¥${sheet.housingFundDue}）` : '' }}
                  </NButton>
                  <NButton
                    v-else-if="sheet.hfPaymentStatus === 'pending_review'"
                    secondary
                    disabled
                  >
                    公积金凭证待复核
                  </NButton>
                  <NButton
                    v-else-if="sheet.hf_paid_voucher_id"
                    secondary
                    @click="viewVoucher(sheet.hf_paid_voucher_id!)"
                  >
                    查看公积金凭证
                  </NButton>

                  <NButton
                    v-if="sheet.taxPaymentStatus === 'unpaid'"
                    secondary
                    type="primary"
                    @click="payLiability('tax')"
                  >
                    缴纳个税{{ sheet.taxDue ? `（¥${sheet.taxDue}）` : '' }}
                  </NButton>
                  <NButton
                    v-else-if="sheet.taxPaymentStatus === 'pending_review'"
                    secondary
                    disabled
                  >
                    个税凭证待复核
                  </NButton>
                  <NButton
                    v-else-if="sheet.tax_paid_voucher_id"
                    secondary
                    @click="viewVoucher(sheet.tax_paid_voucher_id!)"
                  >
                    查看个税凭证
                  </NButton>
                </template>
              </template>
            </NSpace>
          </div>
        </div>

        <!-- 视图 2：员工花名册 -->
        <div v-else class="workspace-body">
          <div v-if="employees.length > 0" class="table-container">
            <table class="salary-table roster-table">
              <thead>
                <tr>
                  <th>员工姓名</th>
                  <th>所属部门</th>
                  <th>入职日期</th>
                  <th class="col-amount">每月基本工资（元）</th>
                  <th class="col-amount">社保缴纳基数（元）</th>
                  <th style="width: 160px; text-align: center">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="emp in employees" :key="emp.id">
                  <td class="emp-name">
                    {{ emp.name }}
                    <NTag v-if="emp.is_owner" size="tiny" type="warning" :bordered="false" style="margin-left: 4px">
                      业主
                    </NTag>
                  </td>
                  <td>
                    <NTag
                      size="small"
                      :bordered="false"
                      :type="isRdDepartment(emp.department) ? 'info' : 'default'"
                    >
                      {{ emp.department }}
                    </NTag>
                  </td>
                  <td class="num-cell">{{ emp.hire_date || '—' }}</td>
                  <td class="col-amount num-cell">{{ formatMoney(emp.base_salary) }}</td>
                  <td class="col-amount num-cell">{{ formatMoney(emp.social_security_base) }}</td>
                  <td class="action-cell">
                    <NSpace justify="center" size="medium">
                      <NButton text type="primary" @click="openEditEmployeeModal(emp)">编辑</NButton>
                      <NButton
                        text
                        type="error"
                        @click="
                          confirmDelete({
                            title: '确认离职归档',
                            content: `确定将员工“${emp.name}”设为离职归档吗？归档后将不再参与后续薪酬计算。`,
                            positiveText: '确认归档',
                            onConfirm: () => archiveEmp(emp.id!),
                          })
                        "
                      >
                        离职归档
                      </NButton>
                    </NSpace>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-else class="empty-state">
            <div class="empty-icon-box">
              <i class="ri-team-line" />
            </div>
            <h4 class="empty-title">企业花名册尚无在职员工</h4>
            <p class="empty-desc">
              录入员工的基本工资与社保基数后，系统每个月将自动为您预填工资结算表与社保分摊。
            </p>
            <div class="empty-actions">
              <NButton type="primary" size="medium" @click="openAddEmployeeModal">
                <template #icon><i class="ri-user-add-line" /></template>
                录入首位员工
              </NButton>
            </div>
          </div>
        </div>
      </div>
    </NSpin>

    <!-- 部门设置 -->
    <NModal
      v-model:show="showDeptModal"
      preset="card"
      title="部门设置（本账套）"
      style="width: min(560px, 92vw)"
    >
      <p class="modal-hint">
        部门决定工资借方的归集科目：勾选「研发」的部门，工资计入研发支出
        （430100101），且计提时必须登记研发项目工时；其余部门计入管理费用。
      </p>
      <div class="dept-rows">
        <div v-for="(row, index) in deptDraft" :key="index" class="dept-row">
          <NInput v-model:value="row.name" placeholder="部门名称（如 技术部、总经办）" size="medium" />
          <div class="dept-rd-cell" :title="row.isRd ? '工资计入研发支出' : '工资计入管理费用'">
            <NSwitch v-model:value="row.isRd" size="small" />
            <span>研发</span>
          </div>
          <span class="dept-headcount">{{ deptHeadcount(row.name) }}</span>
          <NButton
            text
            type="error"
            :disabled="deptDraft.length <= 1"
            @click="removeDeptRow(index)"
          >删除</NButton>
        </div>
      </div>
      <NButton dashed block style="margin-top: 10px" @click="addDeptRow">+ 添加部门</NButton>
      <p class="modal-hint muted" style="margin-top: 10px">
        还在用的部门删不掉；员工表单里直接输入新部门名也会自动加进这里。
      </p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeptModal = false">取消</NButton>
          <NButton type="primary" :loading="deptSaving" @click="saveDepartments">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 账套社保公积金比例 -->
    <NModal
      v-model:show="showRatesModal"
      preset="card"
      title="社保公积金比例（本账套）"
      style="width: min(560px, 92vw)"
    >
      <NSpin :show="ratesLoading">
        <p class="modal-hint">
          公积金比例因城市与单位而异（杭州常见 8% 或 12%）。比例决定了代扣合计
          拆成「社保」和「公积金」两笔缴纳凭证的金额，填错会和银行实际扣款对不上。
        </p>
        <NForm label-placement="left" label-width="96" style="margin-top: 12px">
          <NFormItem label="个人社保">
            <NInputNumber v-model:value="ratesForm.socialPersonal" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
              <template #suffix>%</template>
            </NInputNumber>
          </NFormItem>
          <NFormItem label="个人公积金">
            <NInputNumber v-model:value="ratesForm.housingPersonal" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
              <template #suffix>%</template>
            </NInputNumber>
          </NFormItem>
          <NFormItem label="单位社保">
            <NInputNumber v-model:value="ratesForm.socialCompany" :min="0" :max="100" :precision="2" :step="0.5" style="width: 100%">
              <template #suffix>%</template>
            </NInputNumber>
          </NFormItem>
          <NFormItem label="单位公积金">
            <NInputNumber v-model:value="ratesForm.housingCompany" :min="0" :max="100" :precision="2" :step="1" style="width: 100%">
              <template #suffix>%</template>
            </NInputNumber>
          </NFormItem>
        </NForm>
        <p v-if="payrollRates && !payrollRates.isCustomized" class="modal-hint muted">
          当前按默认口径：个人社保 {{ ratesPercent(Number(payrollRates.defaults.socialPersonal)) }} ·
          个人公积金 {{ ratesPercent(Number(payrollRates.defaults.housingPersonal)) }}
        </p>
        <div class="rates-recalc-row">
          <NSwitch v-model:value="ratesRecalculate" size="small" />
          <span>保存后按新比例重算 {{ selectedMonth }} 草稿工资表（已计提月份不受影响）</span>
        </div>
      </NSpin>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showRatesModal = false">取消</NButton>
          <NButton type="primary" :loading="ratesSaving" @click="saveRates">保存</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 个税试算（累计预扣法） -->
    <NModal
      v-model:show="showTaxModal"
      preset="card"
      :title="`${taxTarget?.employee_name || ''} · ${selectedMonth} 个税试算`"
      style="width: min(640px, 94vw)"
    >
      <p class="modal-hint">
        按累计预扣法试算：累计收入 − 累计减除费用（5000/月）− 累计个人社保公积金
        − 累计专项附加扣除 − 其他扣除。结果仅供参考，申报以个税扣缴端为准。
      </p>
      <NForm label-placement="left" label-width="110" style="margin-top: 12px">
        <NFormItem label="本期应发">
          <span class="calc-static">{{ formatMoney(currentGrossOfTarget) }}</span>
        </NFormItem>
        <NFormItem label="本期个人社保">
          <span class="calc-static">{{ formatMoney(Number(taxTarget?.personal_social_security || 0)) }}</span>
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

      <NSpin :show="taxLoading">
        <div v-if="taxPreview" class="tax-breakdown">
          <p v-if="taxPreview.hireDateMissing" class="tax-warn">
            <i class="ri-error-warning-line" />
            该员工未填入职日期，累计减除费用按 {{ taxPreview.startMonth }} 起算（{{ taxPreview.months }} 个月）。
            若实际入职更早、别处还有工资收入，此处个税会偏低，请按扣缴端申报数为准。
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
        <p v-else-if="!taxLoading" class="modal-hint muted">暂无法试算，请检查该员工是否已在本月工资表中。</p>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showTaxModal = false">关闭</NButton>
          <NButton :loading="taxLoading" @click="runTaxPreview">重新试算</NButton>
          <NButton type="primary" :disabled="!taxPreview" @click="applyTaxResult">填入本期个税</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 研发项目工时分配弹窗 -->
    <NModal
      v-model:show="showRdAllocationModal"
      preset="card"
      :title="`${rdAllocationEmployee?.employee_name || ''} · 研发项目工时分配`"
      style="width: min(760px, 92vw)"
    >
      <div v-for="(allocation, index) in rdAllocationDraft" :key="index" class="rd-allocation-row">
        <NSelect v-model:value="allocation.projectId" :options="rdProjectOptions" filterable placeholder="研发项目" />
        <NInputNumber v-model:value="allocation.allocationRate" :min="0.01" :max="1" :step="0.1" placeholder="比例0-1" />
        <NInput v-model:value="allocation.allocationBasis" placeholder="工时表或分配依据" />
        <NButton text type="error" @click="removeRdAllocationRow(index)">删除</NButton>
      </div>
      <NSpace justify="space-between" class="mt-4">
        <NButton @click="addRdAllocationRow">增加项目</NButton>
        <NSpace>
          <NButton @click="showRdAllocationModal = false">取消</NButton>
          <NButton type="primary" @click="saveRdAllocation">保存</NButton>
        </NSpace>
      </NSpace>
    </NModal>

    <!-- 新增/编辑员工弹窗 -->
    <NModal
      v-model:show="showEmployeeModal"
      :title="isEditingEmployee ? '编辑员工信息' : '录入新员工'"
      preset="card"
      style="width: 420px; border-radius: 14px"
    >
      <NForm label-placement="left" label-width="84" style="margin-top: 10px">
        <NFormItem label="员工姓名">
          <NInput v-model:value="employeeForm.name" placeholder="请输入员工姓名" />
        </NFormItem>
        <NFormItem
          label="所属部门"
          feedback="可直接输入新部门名（如 总经办）后回车，保存员工时自动加入部门列表"
          feedback-style="font-size: 12px; opacity: 0.65; line-height: 1.5;"
        >
          <NSelect
            v-model:value="employeeForm.department"
            :options="departmentOptions"
            filterable
            tag
            placeholder="选择或直接输入部门名"
            :consistent-menu-width="false"
          />
        </NFormItem>
        <NFormItem label="入职日期">
          <NDatePicker
            v-model:formatted-value="employeeForm.hire_date"
            value-format="yyyy-MM-dd"
            type="date"
            clearable
            size="medium"
            style="width: 100%"
            placeholder="不填则不限月份"
          />
        </NFormItem>
        <NFormItem label="业主标记">
          <div class="owner-flag-row">
            <NSwitch v-model:value="employeeForm.is_owner" size="small" />
            <span class="owner-flag-hint">
              个体户/个独业主本人的工资不得税前扣除，汇算清缴时按此标记自动调增
            </span>
          </div>
        </NFormItem>
        <NFormItem label="基本工资">
          <NInputNumber
            v-model:value="employeeForm.base_salary"
            :min="0"
            :precision="2"
            :step="100"
            style="width: 100%"
          >
            <template #prefix>¥</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="社保基数">
          <NInputNumber
            v-model:value="employeeForm.social_security_base"
            :min="0"
            :precision="2"
            :step="100"
            style="width: 100%"
          >
            <template #prefix>¥</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="专项附加">
          <NInputNumber
            v-model:value="employeeForm.special_additional_deduction"
            :min="0"
            :precision="2"
            :step="500"
            style="width: 100%"
          >
            <template #prefix>¥</template>
          </NInputNumber>
        </NFormItem>
      </NForm>
      <p class="form-hint">
        专项附加为每月扣除合计（子女教育 2000/人、房贷利息 1000、赡养老人 3000、婴幼儿照护 2000 等；
        住房租金按城市分档：直辖市/省会/计划单列市 1500、市辖区户籍人口超 100 万城市 1100、其他 800），
        仅用于个税试算，按实际申报口径填写。
      </p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEmployeeModal = false">取消</NButton>
          <NButton type="primary" @click="saveEmployee">保存</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
/* 个税单元格：输入框 + 试算按钮 */
.tax-cell {
  display: flex;
  align-items: center;
  gap: 2px;
}
.tax-calc-btn {
  flex: none;
  opacity: 0.55;
}
.tax-calc-btn:hover {
  opacity: 1;
}

/* 明细合计行 */
.totals-row td {
  border-top: 1px solid rgba(15, 23, 42, 0.12);
  padding-top: 10px;
  font-weight: 600;
}

/* 非研发部门无需分配研发工时 */
.rd-not-applicable {
  opacity: 0.35;
}

.modal-hint {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  opacity: 0.75;
}
.modal-hint.muted {
  margin-top: 8px;
  opacity: 0.55;
}
.rates-recalc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 12.5px;
  opacity: 0.8;
}
.dept-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.dept-row {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  align-items: center;
  gap: 10px;
}
.dept-rd-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  opacity: 0.85;
  white-space: nowrap;
}
.dept-headcount {
  min-width: 48px;
  font-size: 12px;
  text-align: right;
  white-space: nowrap;
  opacity: 0.55;
}
.calc-static {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.tax-breakdown {
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 10px;
  padding: 6px 14px;
}
.tax-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 7px 0;
  font-size: 13px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.06);
}
.tax-line:last-child {
  border-bottom: none;
}
.tax-line.emphasis {
  font-weight: 600;
}
.tax-line.total {
  font-size: 15px;
  font-weight: 700;
}
.tax-warn {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 8px 0 4px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(160, 92, 16, 0.08);
  font-size: 12px;
  line-height: 1.6;
}
.form-hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  opacity: 0.6;
}
.withholding-hint {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 4px;
  margin: 12px 2px 0;
  font-size: 12.5px;
  line-height: 1.7;
  opacity: 0.7;
}

.owner-flag-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.owner-flag-hint {
  font-size: 12px;
  opacity: 0.65;
  line-height: 1.5;
}

.salary-page {
  width: 100%;
  box-sizing: border-box;
  padding-bottom: 40px;
}

/* 顶部导航与主标题栏（对齐全局 layout.css .page-header 规范） */
.page-header {
  flex-wrap: wrap;
}
.back-btn {
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}
.title-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.title-with-badge {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.pill-paid {
  background: #eaf7f1;
  color: #0f7354;
}
.pill-accrued {
  background: #fff5e8;
  color: #a05c10;
}
.pill-pending {
  background: #edf3ff;
  color: #1747b0;
}
.pill-draft {
  background: #f1f4f8;
  color: #586474;
}

.page-header-right {
  flex-wrap: wrap;
}
.month-selector-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.control-label {
  font-size: 13px;
  font-weight: 500;
  color: #586474;
  white-space: nowrap;
}
.month-picker {
  width: 144px;
}
.header-divider {
  width: 1px;
  height: 22px;
  background: #dde3ea;
  margin: 0 2px;
}

/* 顶部汇总指标卡片栏 */
.salary-summary-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-radius: 12px;
  margin-bottom: 16px;
  background: #ffffff;
  border: 1px solid #dde3ea;
}
.summary-metrics {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.metric-label {
  font-size: 12px;
  color: #586474;
}
.metric-value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #182230;
}
.metric-unit {
  font-size: 12px;
  font-weight: 400;
  color: #636e7c;
  margin-left: 2px;
}
.metric-divider {
  width: 1px;
  height: 28px;
  background: #edf0f4;
}
.text-income {
  color: #0f7354 !important;
}
.text-primary {
  color: #1747b0 !important;
}

.verdict-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.badge-paid {
  background: #eaf7f1;
  color: #0f7354;
}
.badge-accrued {
  background: #fff5e8;
  color: #a05c10;
}
.badge-pending {
  background: #edf3ff;
  color: #1747b0;
}
.badge-draft {
  background: #f1f4f8;
  color: #586474;
}

/* 实际发薪日选择器（仅已计提态可见） */
.pay-date-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pay-date-picker {
  width: 140px;
}

/* 主工作台平面卡片（遵循 DESIGN.md Flat-at-Rest 规范） */
.surface-card {
  background: #ffffff;
  border: 1px solid #dde3ea;
  border-radius: 12px;
  overflow: hidden;
}
.sheet-tabs-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid #edf0f4;
  flex-wrap: wrap;
}
.sheet-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sheet-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: #f1f4f8;
  color: #586474;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.sheet-tab-btn.active {
  background: #edf3ff;
  color: #1747b0;
  border-color: rgba(22, 93, 255, 0.25);
}
.tab-count {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(24, 34, 48, 0.06);
  font-variant-numeric: tabular-nums;
}
.sheet-tab-btn.active .tab-count {
  background: rgba(22, 93, 255, 0.14);
  color: #1747b0;
}
.rule-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #636e7c;
}

/* 表格区域 */
.table-container {
  overflow-x: auto;
}
.salary-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.salary-table th {
  text-align: left;
  padding: 10px 14px;
  font-weight: 600;
  color: #586474;
  background: #f6f7f9;
  border-bottom: 1px solid #dde3ea;
  white-space: nowrap;
}
.salary-table td {
  padding: 8px 14px;
  border-bottom: 1px solid #edf0f4;
  vertical-align: middle;
}
.salary-table tbody tr:hover {
  background: #f6f7f9;
}
.col-amount {
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}
.num-cell {
  font-variant-numeric: tabular-nums;
  color: #182230;
}
.emp-name {
  font-weight: 600;
  color: #182230;
}
.net-salary {
  font-weight: 700;
  color: #0f7354;
  font-variant-numeric: tabular-nums;
}
.total-cost {
  font-weight: 700;
  color: #1747b0;
  font-variant-numeric: tabular-nums;
}
.action-cell {
  text-align: center;
}

.amount-input {
  width: 100%;
}
.amount-input :deep(.n-input__input-el) {
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}

/* 空状态引导区 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 52px 24px;
  text-align: center;
}
.empty-icon-box {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: #edf3ff;
  color: #1747b0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  margin-bottom: 14px;
}
.empty-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: #182230;
}
.empty-desc {
  margin: 0 0 20px;
  font-size: 13px;
  color: #636e7c;
  max-width: 440px;
  line-height: 1.5;
}
.empty-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 底部动作条 */
.sheet-footer {
  padding: 14px 20px;
  background: #f6f7f9;
  border-top: 1px solid #edf0f4;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.hint-text {
  font-size: 12px;
  color: #586474;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.paid-desc {
  font-size: 13px;
  color: #0f7354;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}

.rd-allocation-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 130px minmax(220px, 1.4fr) auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}
.mt-4 {
  margin-top: 16px;
}

.payment-suggestion-panel {
  margin-bottom: 16px;
  padding: 14px 16px;
  border: 1px solid var(--solo-border-light);
  border-radius: 10px;
  background: var(--solo-bg-subtle, #fafafa);
}
.payment-suggestion-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.payment-suggestion-head strong {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
}
.payment-suggestion-head span {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.payment-suggestion-row {
  display: grid;
  grid-template-columns: minmax(260px, 1.4fr) minmax(240px, 1.8fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid var(--solo-border-light);
}
.payment-suggestion-main {
  display: flex;
  align-items: center;
  gap: 8px;
}
.payment-suggestion-month {
  font-weight: 600;
}
.payment-suggestion-amount {
  font-variant-numeric: tabular-nums;
}
.payment-suggestion-desc,
.payment-suggestion-reason {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.payment-suggestion-reason {
  display: none;
}

@media (max-width: 720px) {
  .rd-allocation-row {
    grid-template-columns: 1fr;
  }
}
</style>
