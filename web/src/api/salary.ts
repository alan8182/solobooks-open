import apiClient from './client'

export interface Employee {
  id?: string
  name: string
  department: string
  base_salary: number
  social_security_base: number
  is_active?: number
  created_at?: string
  updated_at?: string
}

export interface SalaryRecord {
  id: string
  sheet_id: string
  employee_id: string
  employee_name: string
  base_salary: number
  bonus: number
  allowance: number
  deduction: number
  personal_social_security: number
  personal_tax: number
  company_social_security: number
  net_salary: number
  total_cost: number
}

export interface SalarySheet {
  id: string
  company_id: string
  month: string
  status: 'draft' | 'accrued' | 'paid'
  accrual_voucher_id: string | null
  ss_paid_voucher_id?: string | null
  tax_paid_voucher_id?: string | null
  payment_voucher_id: string | null
  records: SalaryRecord[]
}

export interface RdSalaryAllocation {
  id?: string
  month?: string
  employeeId: string
  projectId: string
  allocationRate: number
  allocationBasis: string
}

export function getEmployees() {
  return apiClient.get<Employee[]>('/api/salary/employees')
}

export function addEmployee(data: Employee) {
  return apiClient.post<Employee>('/api/salary/employees', data)
}

export function updateEmployee(id: string, data: Partial<Employee>) {
  return apiClient.put<Employee>(`/api/salary/employees/${id}`, data)
}

export function deleteEmployee(id: string) {
  return apiClient.delete<{ success: boolean }>(`/api/salary/employees/${id}`)
}

export function getSalarySheet(month: string) {
  return apiClient.get<SalarySheet>(`/api/salary/sheets/${month}`)
}

export function updateSalaryRecords(month: string, records: Partial<SalaryRecord>[]) {
  return apiClient.put<SalarySheet>(`/api/salary/sheets/${month}/records`, { records })
}

export function accrueSalarySheet(month: string) {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/accrue`)
}

export function paySalarySheet(month: string) {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay`)
}

export function paySalarySocial(month: string) {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay-social`)
}

export function paySalaryTax(month: string) {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay-tax`)
}

export function getRdSalaryAllocations(month: string): Promise<{ allocations: RdSalaryAllocation[] }> {
  return apiClient.get(`/api/salary/sheets/${month}/rd-allocations`)
}

export function updateRdSalaryAllocations(month: string, allocations: RdSalaryAllocation[]): Promise<{ allocations: RdSalaryAllocation[] }> {
  return apiClient.put(`/api/salary/sheets/${month}/rd-allocations`, { allocations })
}
