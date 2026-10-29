/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface Employee {
  id?: string
  name: string
  department: string
  base_salary: number
  social_security_base: number
  /** 业主/投资者标记：经营所得 B 表业主工资调增的自动识别来源 */
  is_owner?: number | boolean
  /** 入职日期（YYYY-MM-DD）：工资表只从入职当月起计入该员工 */
  hire_date?: string | null
  /** 月度专项附加扣除合计（子女教育/房贷/赡养老人等），个税试算用 */
  special_additional_deduction?: number
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
  /** pending_review：计提凭证已提交审核中心，复核通过后才流转为 accrued */
  status: 'draft' | 'pending_review' | 'accrued' | 'paid'
  accrual_voucher_id: string | null
  ss_paid_voucher_id?: string | null
  hf_paid_voucher_id?: string | null
  tax_paid_voucher_id?: string | null
  payment_voucher_id: string | null
  accrualVoucherStatus?: string | null
  paymentVoucherStatus?: string | null
  paymentStatus?: 'posted' | 'pending_review' | 'unpaid'
  ssPaymentStatus?: 'posted' | 'pending_review' | 'unpaid' | 'not_required'
  hfPaymentStatus?: 'posted' | 'pending_review' | 'unpaid' | 'not_required'
  taxPaymentStatus?: 'posted' | 'pending_review' | 'unpaid' | 'not_required'
  socialSecurityDue?: string
  housingFundDue?: string
  taxDue?: string
  /** 个人/单位公积金部分（从合并列里拆出来，用于展示「社保 X + 公积金 Y」） */
  personal_housing_fund_total?: number
  company_housing_fund_total?: number
  records: SalaryRecord[]
}

export interface PayrollLiabilitySuggestion {
  sheetId: string
  month: string
  kind: 'social_security' | 'housing_fund' | 'tax'
  label: string
  amount: string
  payDate: string
  confidence: 'high' | 'medium'
  reason: string
  statement: {
    id: string
    date: string
    description: string
    amount: string
  }
}

export interface SalaryPaymentSuggestion {
  sheetId: string
  month: string
  netSalary: string
  payDate: string
  confidence: 'high' | 'medium'
  reason: string
  statement: {
    id: string
    date: string
    description: string
    amount: string
  }
}

export interface SalaryPaymentApplyResult extends SalarySheet {
  statementId: string
  linked: boolean
  linkError?: string
}

export interface RdSalaryAllocation {
  id?: string
  month?: string
  employeeId: string
  projectId: string
  allocationRate: number
  allocationBasis: string
}

export function getEmployees(): Promise<Employee[]> {
  return apiClient.get<Employee[]>('/api/salary/employees')
}

export function addEmployee(data: Employee): Promise<Employee> {
  return apiClient.post<Employee>('/api/salary/employees', data)
}

export function updateEmployee(id: string, data: Partial<Employee>): Promise<Employee> {
  return apiClient.put<Employee>(`/api/salary/employees/${id}`, data)
}

export function deleteEmployee(id: string): Promise<{ success: boolean }> {
  return apiClient.delete<{ success: boolean }>(`/api/salary/employees/${id}`)
}

export function getSalarySheet(month: string): Promise<SalarySheet> {
  return apiClient.get<SalarySheet>(`/api/salary/sheets/${month}`)
}

export function updateSalaryRecords(month: string, records: Partial<SalaryRecord>[]): Promise<SalarySheet> {
  return apiClient.put<SalarySheet>(`/api/salary/sheets/${month}/records`, { records })
}

export function accrueSalarySheet(month: string): Promise<SalarySheet> {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/accrue`)
}

export function getSalaryPaymentSuggestions(): Promise<{ total: number; suggestions: SalaryPaymentSuggestion[] }> {
  return apiClient.get<{ total: number; suggestions: SalaryPaymentSuggestion[] }>('/api/salary/payments/suggestions')
}

export function applySalaryPayment(month: string, statementId: string): Promise<SalaryPaymentApplyResult> {
  return apiClient.post<SalaryPaymentApplyResult>('/api/salary/payments/apply', { month, statementId })
}

export function paySalarySheet(month: string, payDate?: string): Promise<SalarySheet> {
  // payDate：实际发薪日（YYYY-MM-DD）。真实发薪多在工资所属期次月，
  // 缺省由后端取操作当天；传入后作为发放凭证的发生日期。
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay`, { payDate: payDate || null })
}

export function paySalarySocial(month: string, payDate?: string): Promise<SalarySheet> {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay-social`, { payDate: payDate || null })
}

export function paySalaryHousingFund(month: string, payDate?: string): Promise<SalarySheet> {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay-housing-fund`, { payDate: payDate || null })
}

export function paySalaryTax(month: string, payDate?: string): Promise<SalarySheet> {
  return apiClient.post<SalarySheet>(`/api/salary/sheets/${month}/pay-tax`, { payDate: payDate || null })
}

export function getPayrollLiabilitySuggestions(): Promise<{ total: number; suggestions: PayrollLiabilitySuggestion[] }> {
  return apiClient.get<{ total: number; suggestions: PayrollLiabilitySuggestion[] }>('/api/salary/liabilities/suggestions')
}

export function applyPayrollLiability(month: string, kind: string, statementId: string): Promise<SalaryPaymentApplyResult> {
  return apiClient.post('/api/salary/liabilities/apply', { month, kind, statementId })
}

export function getRdSalaryAllocations(month: string): Promise<{ allocations: RdSalaryAllocation[] }> {
  return apiClient.get(`/api/salary/sheets/${month}/rd-allocations`)
}

export function updateRdSalaryAllocations(month: string, allocations: RdSalaryAllocation[]): Promise<{ allocations: RdSalaryAllocation[] }> {
  return apiClient.put(`/api/salary/sheets/${month}/rd-allocations`, { allocations })
}

/** 账套级社保公积金比例（0~1 的小数）。 */
export interface PayrollRates {
  rates: Record<'socialPersonal' | 'socialCompany' | 'housingPersonal' | 'housingCompany', string>
  defaults: Record<'socialPersonal' | 'socialCompany' | 'housingPersonal' | 'housingCompany', string>
  isCustomized: boolean
}

export function getPayrollRates(): Promise<PayrollRates> {
  return apiClient.get<PayrollRates>('/api/salary/rates')
}

export function updatePayrollRates(
  rates: Partial<Record<keyof PayrollRates['rates'], number>>,
  options?: { month?: string; recalculate?: boolean },
): Promise<PayrollRates> {
  const params = new URLSearchParams()
  if (options?.month) params.set('month', options.month)
  if (options?.recalculate) params.set('recalculate', 'true')
  const query = params.toString()
  return apiClient.put<PayrollRates>(`/api/salary/rates${query ? `?${query}` : ''}`, rates)
}

/** 个税累计预扣试算结果（金额均为两位小数字符串）。 */
export interface TaxPreview {
  employeeId: string
  employeeName: string
  month: string
  months: number
  startMonth: string
  /** 员工没填入职日期时为 true：累计起算月靠工资数据兜底，口径可能偏低 */
  hireDateMissing: boolean
  currentGross: string
  currentWithheld: string
  priorGross: string
  priorWithheld: string
  priorTax: string
  cumulativeIncome: string
  cumulativeBasicDeduction: string
  cumulativeSpecialDeduction: string
  cumulativeAdditionalDeduction: string
  cumulativeOtherDeduction: string
  taxableIncome: string
  taxRate: string
  quickDeduction: string
  cumulativeTax: string
  prepaidTax: string
  taxDue: string
  monthlySpecialAdditional: string
}

export function previewSalaryTax(payload: {
  month: string
  employeeId: string
  gross: number
  withheld: number
  specialAdditional?: number | null
  otherDeduction?: number
}): Promise<TaxPreview> {
  return apiClient.post<TaxPreview>('/api/salary/tax/preview', payload)
}

/** 账套部门字典项：isRd 决定该部门工资走研发支出还是管理费用。 */
export interface DepartmentItem {
  name: string
  isRd: boolean
  /** 名册里在用但没登记的历史部门，后端会自动并入选项 */
  implicit?: boolean
  headcount?: number
}

export interface DepartmentsPayload {
  departments: DepartmentItem[]
  unregistered: Array<{ name: string; headcount: number }>
  customized: boolean
}

export function getDepartments(): Promise<DepartmentsPayload> {
  return apiClient.get<DepartmentsPayload>('/api/salary/departments')
}

export function updateDepartments(departments: Array<{ name: string; isRd: boolean }>): Promise<DepartmentsPayload> {
  return apiClient.put<DepartmentsPayload>('/api/salary/departments', { departments })
}

export function exportSalarySheet(month: string): Promise<Blob> {
  return apiClient.get(`/api/salary/sheets/${month}/export`, { responseType: 'blob' })
}

export function exportSalaryPayslip(month: string, recordId: string): Promise<Blob> {
  return apiClient.get(`/api/salary/sheets/${month}/payslip/${recordId}`, { responseType: 'blob' })
}
