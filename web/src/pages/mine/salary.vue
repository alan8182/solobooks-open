<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NButton, NSpace, useMessage, NInput, NModal, NForm,
  NFormItem, NSpin, NTabs, NTabPane, NTable, NSelect, NInputNumber,
  NDatePicker, NGrid, NGridItem, NTag
} from 'naive-ui'
import dayjs from 'dayjs'
import { formatMoney } from '@/utils/format'
import {
  getEmployees, addEmployee, updateEmployee, deleteEmployee,
  getSalarySheet, updateSalaryRecords, accrueSalarySheet, paySalarySheet,
  getRdSalaryAllocations, updateRdSalaryAllocations,
  type Employee, type SalaryRecord, type SalarySheet, type RdSalaryAllocation
} from '@/api/salary'
import { getRdProjects } from '@/api/rd'
import type { RdProject } from '@/api/types'
import { useEntryStore } from '@/stores/entry'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'

const router = useRouter()
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
  social_security_base: 3000
})

const departmentOptions = [
  { label: '研发部', value: '研发部' },
  { label: '销售部', value: '销售部' },
  { label: '行政部', value: '行政部' }
]

// Salary Sheet Data
const sheet = ref<SalarySheet | null>(null)
const sheetRecords = ref<SalaryRecord[]>([])
const rdProjects = ref<RdProject[]>([])
const rdAllocations = ref<RdSalaryAllocation[]>([])
const showRdAllocationModal = ref(false)
const rdAllocationEmployee = ref<SalaryRecord | null>(null)
const rdAllocationDraft = ref<RdSalaryAllocation[]>([])

const rdProjectOptions = computed(() => rdProjects.value
  .filter((project) => project.status === 'active')
  .map((project) => ({ label: `${project.code} ${project.name}`, value: project.id })))

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
    const [data, allocationData, projectData, employeeData]: any = await Promise.all([
      getSalarySheet(selectedMonth.value),
      getRdSalaryAllocations(selectedMonth.value),
      getRdProjects(),
      getEmployees(),
    ])
    sheet.value = data
    sheetRecords.value = data.records || []
    rdAllocations.value = allocationData.allocations || []
    rdProjects.value = projectData || []
    employees.value = employeeData || []
  } catch {
    message.error('加载月度工资表失败')
  } finally {
    loading.value = false
  }
}

// Watch month change to reload salary sheets
watch(selectedMonth, () => {
  fetchSalarySheet()
})

// Trigger load on tab change
watch(activeTab, (tab) => {
  if (tab === 'roster') {
    fetchEmployees()
  } else {
    fetchSalarySheet()
  }
})

onMounted(() => {
  fetchSalarySheet()
})

function employeeRdAllocations(employeeId: string) {
  return rdAllocations.value.filter((item) => item.employeeId === employeeId)
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
    social_security_base: 4000
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
    social_security_base: emp.social_security_base
  }
  showEmployeeModal.value = true
}

async function saveEmployee() {
  if (!employeeForm.value.name.trim()) {
    message.warning('请输入员工姓名')
    return
  }
  try {
    if (isEditingEmployee.value) {
      await updateEmployee(editingEmployeeId.value, employeeForm.value)
      message.success('更新员工成功')
    } else {
      await addEmployee(employeeForm.value)
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
    // Sync entries store cache
    await entryStore.fetchEntries()
    message.success('工资计提凭证生成成功，资金已计入研发/管理成本')
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
    const data: any = await paySalarySheet(selectedMonth.value)
    sheet.value = data
    sheetRecords.value = data.records || []
    // Sync entries store cache
    await entryStore.fetchEntries()
    message.success('工资发放记账成功，已生成银行存款划扣凭证')
  } catch (error: any) {
    message.error(error.response?.data?.detail || '发放失败')
  } finally {
    loading.value = false
  }
}

function viewVoucher(voucherId: string) {
  router.push(`/ledger/${voucherId}`)
}
</script>

<template>
  <div class="salary-page">
    <div class="page-header">
      <NButton text class="back-btn" @click="router.push('/mine')">
        <template #icon><i class="ri-arrow-left-line" /></template>
        返回设置
      </NButton>
      <div class="header-row">
        <div class="title-section">
          <h2>薪酬与社保管理</h2>
          <p class="subtitle">管理在职员工花名册，分摊计提月度人工及社保费用</p>
        </div>
      </div>
    </div>

    <NTabs v-model:value="activeTab" type="line" class="salary-tabs">
      
      <!-- TAB 1: Salary sheet settlement -->
      <NTabPane name="sheet" tab="工资表结算">
        <NCard :bordered="false" class="main-card">
          <div class="sheet-controls">
            <NSpace align="center" size="large">
              <span class="control-label">结算月份：</span>
              <NDatePicker
                v-model:formatted-value="selectedMonth"
                value-format="yyyy-MM"
                type="month"
                :clearable="false"
                class="month-picker"
              />
            </NSpace>
            
            <NSpace v-if="sheet">
              <NTag :type="sheet.status === 'paid' ? 'success' : sheet.status === 'accrued' ? 'warning' : 'default'" size="medium" round>
                {{ sheet.status === 'paid' ? '已发放记账' : sheet.status === 'accrued' ? '已计提记账' : '草稿 (待计提)' }}
              </NTag>
            </NSpace>
          </div>

          <!-- Total metrics grid -->
          <NGrid :cols="3" :x-gap="16" class="totals-grid" v-if="sheetRecords.length > 0">
            <NGridItem>
              <div class="total-box">
                <span class="box-label">在职应发人数</span>
                <span class="box-value">{{ sheetTotals.count }} <span class="box-unit">人</span></span>
              </div>
            </NGridItem>
            <NGridItem>
              <div class="total-box">
                <span class="box-label">实发工资总额</span>
                <span class="box-value money-color">{{ formatMoney(sheetTotals.net) }}</span>
              </div>
            </NGridItem>
            <NGridItem>
              <div class="total-box">
                <span class="box-label">企业总人工成本</span>
                <span class="box-value cost-color">{{ formatMoney(sheetTotals.cost) }}</span>
              </div>
            </NGridItem>
          </NGrid>

          <NSpin :show="loading">
            <div class="table-container" v-if="sheetRecords.length > 0">
              <NTable :bordered="false" :single-line="false" class="records-table">
                <thead>
                  <tr>
                    <th style="min-width: 70px;">姓名</th>
                    <th style="min-width: 70px;">部门</th>
                    <th style="min-width: 130px;">研发项目工时</th>
                    <th style="min-width: 80px;">基本工资</th>
                    <th style="min-width: 80px;">奖金</th>
                    <th style="min-width: 80px;">补助/津贴</th>
                    <th style="min-width: 80px;">考勤扣款</th>
                    <th style="min-width: 90px;">个人社保公积金</th>
                    <th style="min-width: 80px;">个税代扣</th>
                    <th style="min-width: 90px;">单位社保公积金</th>
                    <th style="min-width: 90px;">实发工资</th>
                    <th style="min-width: 90px;">企业成本</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in sheetRecords" :key="row.id">
                    <td class="emp-name">{{ row.employee_name }}</td>
                    <td><NTag size="small" :type="row.employee_id && employees.find(e => e.id === row.employee_id)?.department === '研发部' ? 'primary' : 'default'">{{ employees.find(e => e.id === row.employee_id)?.department || '行政部' }}</NTag></td>
                    <td>
                      <NButton size="tiny" :type="employeeRdAllocations(row.employee_id).length ? 'primary' : 'default'" :disabled="sheet?.status !== 'draft'" @click="openRdAllocation(row)">
                        {{ rdAllocationSummary(row.employee_id) }}
                      </NButton>
                    </td>
                    <td>{{ formatMoney(row.base_salary) }}</td>
                    
                    <!-- Editable Fields for draft sheet -->
                    <td>
                      <NInputNumber
                        v-model:value="row.bonus"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    <td>
                      <NInputNumber
                        v-model:value="row.allowance"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    <td>
                      <NInputNumber
                        v-model:value="row.deduction"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    <td>
                      <NInputNumber
                        v-model:value="row.personal_social_security"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    <td>
                      <NInputNumber
                        v-model:value="row.personal_tax"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    <td>
                      <NInputNumber
                        v-model:value="row.company_social_security"
                        :min="0"
                        :show-button="false"
                        size="small"
                        :disabled="sheet?.status !== 'draft'"
                        @update:value="handleRecordAmountChange(row)"
                      />
                    </td>
                    
                    <!-- Calculated Outputs -->
                    <td class="net-salary">{{ formatMoney(row.net_salary) }}</td>
                    <td class="total-cost">{{ formatMoney(row.total_cost) }}</td>
                  </tr>
                </tbody>
              </NTable>
            </div>
            <div class="empty-state" v-else>
              <i class="ri-user-unfollow-line empty-icon" />
              <p>当月无在职员工记录，请先到「员工花名册」添加员工</p>
            </div>
          </NSpin>

          <!-- Action Footer Section -->
          <div class="sheet-footer" v-if="sheet && sheetRecords.length > 0">
            <NSpace size="medium">
              <!-- DRAFT STATE -->
              <template v-if="sheet.status === 'draft'">
                <NButton secondary type="primary" @click="saveRecords">保存工资单修改</NButton>
                <NButton type="primary" @click="accrueSheet">计提记账 (列入当月研发/福利成本)</NButton>
              </template>

              <!-- ACCRUED STATE -->
              <template v-if="sheet.status === 'accrued'">
                <NButton secondary type="warning" @click="viewVoucher(sheet.accrual_voucher_id!)">
                  查看计提凭证
                </NButton>
                <NButton type="success" @click="paySheet">发放工资记账 (生成存款划扣凭证)</NButton>
              </template>

              <!-- PAID STATE -->
              <template v-if="sheet.status === 'paid'">
                <NButton secondary type="warning" @click="viewVoucher(sheet.accrual_voucher_id!)">
                  查看计提凭证
                </NButton>
                <NButton secondary type="success" @click="viewVoucher(sheet.payment_voucher_id!)">
                  查看发放凭证
                </NButton>
                <span class="paid-desc"><i class="ri-checkbox-circle-fill" /> 该月工资已结算并发放记账完毕。</span>
              </template>
            </NSpace>
          </div>

          <NModal v-model:show="showRdAllocationModal" preset="card" :title="`${rdAllocationEmployee?.employee_name || ''} · 研发项目工时分配`" style="width:min(760px,92vw)">
            <div v-for="(allocation, index) in rdAllocationDraft" :key="index" class="rd-allocation-row">
              <NSelect v-model:value="allocation.projectId" :options="rdProjectOptions" filterable placeholder="研发项目" />
              <NInputNumber v-model:value="allocation.allocationRate" :min="0.01" :max="1" :step="0.1" placeholder="比例0-1" />
              <NInput v-model:value="allocation.allocationBasis" placeholder="工时表或分配依据" />
              <NButton text type="error" @click="removeRdAllocationRow(index)">删除</NButton>
            </div>
            <NSpace justify="space-between" class="mt-4">
              <NButton @click="addRdAllocationRow">增加项目</NButton>
              <NSpace><NButton @click="showRdAllocationModal = false">取消</NButton><NButton type="primary" @click="saveRdAllocation">保存</NButton></NSpace>
            </NSpace>
          </NModal>
        </NCard>
      </NTabPane>

      <!-- TAB 2: Employee Roster -->
      <NTabPane name="roster" tab="员工花名册">
        <NCard :bordered="false" class="main-card">
          <div class="roster-header">
            <span class="roster-title">花名册成员 (在职员工)</span>
            <NButton type="primary" @click="openAddEmployeeModal">
              <template #icon><i class="ri-user-add-line" /></template>
              新增员工
            </NButton>
          </div>

          <NSpin :show="loading">
            <NTable :bordered="false" :single-line="false" class="roster-table" v-if="employees.length > 0">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>所属部门</th>
                  <th>每月基本工资</th>
                  <th>社保缴纳基数</th>
                  <th style="width: 150px; text-align: center;">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="emp in employees" :key="emp.id">
                  <td class="emp-name">{{ emp.name }}</td>
                  <td>
                    <NTag size="small" :type="emp.department === '研发部' ? 'primary' : 'default'">
                      {{ emp.department }}
                    </NTag>
                  </td>
                  <td>{{ formatMoney(emp.base_salary) }}</td>
                  <td>{{ formatMoney(emp.social_security_base) }}</td>
                  <td class="action-cell">
                    <NSpace justify="center">
                      <NButton text type="primary" @click="openEditEmployeeModal(emp)">编辑</NButton>
                      <NButton
                        text
                        type="error"
                        @click="confirmDelete({
                          title: '确认离职归档',
                          content: `确定将员工“${emp.name}”设为离职归档吗？归档后将不再参与后续薪酬计算。`,
                          positiveText: '确认归档',
                          onConfirm: () => archiveEmp(emp.id!),
                        })"
                      >离职归档</NButton>
                    </NSpace>
                  </td>
                </tr>
              </tbody>
            </NTable>
            <div class="empty-state" v-else>
              <i class="ri-user-add-line empty-icon" />
              <p>企业花名册为空，点击右上角「新增员工」录入首位成员</p>
            </div>
          </NSpin>
        </NCard>
      </NTabPane>

    </NTabs>

    <!-- Add/Edit Employee Modal -->
    <NModal
      v-model:show="showEmployeeModal"
      :title="isEditingEmployee ? '编辑员工信息' : '录入新员工'"
      preset="card"
      style="width: 400px; border-radius: var(--solo-radius-lg);"
    >
      <NForm label-placement="left" label-width="84" style="margin-top: 10px;">
        <NFormItem label="员工姓名">
          <NInput v-model:value="employeeForm.name" placeholder="请输入姓名..." />
        </NFormItem>
        <NFormItem label="所属部门">
          <NSelect v-model:value="employeeForm.department" :options="departmentOptions" />
        </NFormItem>
        <NFormItem label="基本工资">
          <NInputNumber
            v-model:value="employeeForm.base_salary"
            :min="0"
            :step="100"
            style="width: 100%"
          />
        </NFormItem>
        <NFormItem label="社保基数">
          <NInputNumber
            v-model:value="employeeForm.social_security_base"
            :min="0"
            :step="100"
            style="width: 100%"
          />
        </NFormItem>
      </NForm>
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
.salary-page {
  max-width: 980px;
  margin: 0 auto;
  padding: 16px 0 32px 0;
}

.page-header {
  margin-bottom: 20px;
}

.back-btn {
  font-weight: 500;
  color: var(--solo-text-secondary);
  transition: color 0.2s ease;
  margin-bottom: 8px;
}

.back-btn:hover {
  color: var(--solo-primary);
}

.title-section h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}

.subtitle {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.salary-tabs {
  margin-top: 16px;
}

/* Main Cards */
.main-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-lg);
  padding: 8px;
}

/* Sheet Tab Styling */
.sheet-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.control-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.month-picker {
  width: 140px;
}

/* Totals Panel */
.totals-grid {
  margin-bottom: 24px;
}

.total-box {
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  box-shadow: var(--solo-shadow-sm);
}

.box-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
  font-weight: 500;
}

.box-value {
  font-size: 20px;
  font-weight: 800;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}

.box-unit {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.money-color {
  color: var(--solo-income);
}

.cost-color {
  color: var(--solo-primary);
}

/* Tables Section */
.table-container {
  overflow-x: auto;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
}

.records-table th {
  background-color: var(--solo-border-light) !important;
  font-weight: 700;
  font-size: 13px;
  color: var(--solo-text-secondary);
  text-align: left;
}

.records-table td {
  font-size: 13px;
}

.emp-name {
  font-weight: 700;
  color: var(--solo-text-primary);
}

.net-salary {
  font-weight: 700;
  color: var(--solo-income);
}

.total-cost {
  font-weight: 700;
  color: var(--solo-primary);
}

.action-cell {
  text-align: center;
}

/* Empty State */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  color: var(--solo-text-muted);
}

.empty-icon {
  font-size: 40px;
  margin-bottom: 12px;
  color: var(--solo-text-muted);
  opacity: 0.6;
}

.empty-state p {
  font-size: 14px;
}

/* Roster Section */
.roster-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.roster-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.roster-table th {
  background-color: var(--solo-border-light) !important;
  font-weight: 700;
  font-size: 13px;
}

.sheet-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px dashed var(--solo-border-light);
  display: flex;
  justify-content: flex-end;
}

.paid-desc {
  font-size: 13px;
  color: var(--solo-income);
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

.mt-4 { margin-top: 16px; }

@media (max-width: 720px) {
  .rd-allocation-row { grid-template-columns: 1fr; }
}
</style>
