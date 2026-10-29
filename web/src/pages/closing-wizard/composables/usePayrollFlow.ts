/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 工资 6 连发流程（closing-wizard Step 3）：计提 → 发放 → 社保 → 公积金 → 个税
 * 及流水核销建议。从 closing-wizard/index.vue 拆出：状态与请求自包含；
 * 月切刷新走 loadSalarySheet/loadPayrollPaymentSuggestions，写操作后统一
 * reloadAll（父页 loadAllData，含凭证重拉与对账数据重建）。
 *
 * 口径（权责发生制）：所属月必须计提；发放/社保/公积金/个税可延到次月凭
 * 银行流水核销，均不阻断本月结账（阻断只看计提是否完成且不在待复核）。
 */
import { computed, ref, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { useEntryStore } from '@/stores/entry'

export function usePayrollFlow(options: {
  month: Ref<string>
  loading: Ref<boolean>
  entryStore: ReturnType<typeof useEntryStore>
  reloadAll: () => Promise<void>
}) {
  const { month, loading, entryStore, reloadAll } = options
  const message = useMessage()

  const payrollAccrued = ref(false)
  const payrollPaid = ref(false)
  const payrollPendingReview = ref(false)
  const payrollSsPaid = ref(false)
  const payrollHfPaid = ref(false)
  const payrollTaxPaid = ref(false)
  const employees = ref<any[]>([])
  const salarySheet = ref<any>(null)
  const showSalaryTable = ref(false)
  const payrollPaymentSuggestions = ref<any[]>([])
  const applyingSalaryStatementId = ref('')

  // 工资按权责发生制：所属月必须计提；发放可延到次月，凭银行流水核销，
  // 社保/个税挂账同样作为负债结转，均不阻断本月结账。
  const payrollHasSocial = computed(() =>
    employees.value.some((emp) => safeFloat(emp.social) > 0 || safeFloat(emp.personalSocial) > 0),
  )
  const payrollHasTax = computed(() =>
    employees.value.some((emp) => safeFloat(emp.personalTax) > 0),
  )
  const hasBlockingPayroll = computed(() =>
    employees.value.length > 0 && (!payrollAccrued.value || payrollPendingReview.value),
  )

  const totalNetSalary = computed(() => {
    try {
      return employees.value.reduce((sum, e) => sum + e.net, 0)
    } catch {
      return 0
    }
  })
  const totalCompanyCost = computed(() => {
    try {
      return employees.value.reduce((sum, e) => sum + e.cost, 0)
    } catch {
      return 0
    }
  })

  function safeFloat(val: any): number {
    if (val === null || val === undefined || val === '') return 0
    const n = Number(val)
    return Number.isFinite(n) ? n : 0
  }

  async function loadSalarySheet() {
    try {
      // GET /api/salary/sheets/{month} 直接返回工资表对象（扁平结构，无 sheet 包装层）
      const res: any = await apiClient.get(`/api/salary/sheets/${month.value}`)
      salarySheet.value = res
      employees.value = (res.records || []).map((r: any) => ({
        name: r.employee_name,
        dept: r.department,
        base: r.base_salary,
        bonus: r.bonus,
        social: r.company_social_security,
        personalSocial: r.personal_social_security,
        personalTax: r.personal_tax,
        net: r.net_salary,
        cost: r.total_cost,
      }))

      if (res && res.id) {
        // accrued/paid：计提凭证已入账；pending_review：计提凭证待审核（maker_checker）
        payrollAccrued.value = res.status === 'accrued' || res.status === 'paid'
        payrollPendingReview.value = res.status === 'pending_review'
        payrollPaid.value = res.status === 'paid'
        payrollSsPaid.value = ['posted', 'not_required'].includes(res.ssPaymentStatus)
        payrollHfPaid.value = ['posted', 'not_required'].includes(res.hfPaymentStatus)
        payrollTaxPaid.value = ['posted', 'not_required'].includes(res.taxPaymentStatus)
      }
    } catch (err: any) {
      console.error('[loadSalarySheet] Error occurred:', err)
      employees.value = []
      payrollAccrued.value = false
      payrollPaid.value = false
      payrollPendingReview.value = false
      payrollSsPaid.value = false
      payrollHfPaid.value = false
      payrollTaxPaid.value = false
    }
  }

  async function loadPayrollPaymentSuggestions() {
    try {
      const res: any = await apiClient.get('/api/salary/payments/suggestions')
      payrollPaymentSuggestions.value = res.suggestions || []
    } catch {
      payrollPaymentSuggestions.value = []
    }
  }

  async function handleApplySalaryPayment(item: any) {
    applyingSalaryStatementId.value = item.statement.id
    try {
      const result: any = await apiClient.post('/api/salary/payments/apply', {
        month: item.month,
        statementId: item.statement.id,
      })
      message.success(
        result.linked
          ? `${item.month} 工资已按 ${item.statement.date} 流水发放并核销`
          : `工资已发放，但流水核销失败：${result.linkError || '请到银行流水页手工关联'}`,
      )
      await entryStore.fetchEntries()
      await reloadAll()
    } catch (err: any) {
      const detail = err.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : detail?.message || '工资流水核销失败')
    } finally {
      applyingSalaryStatementId.value = ''
    }
  }

  async function handleAccruePayroll() {
    loading.value = true
    try {
      await apiClient.post(`/api/salary/sheets/${month.value}/accrue`)
      await entryStore.fetchEntries()
      message.success(`${month.value} 工资计提成功，已生成记账凭证！`)
      await reloadAll()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '工资计提失败')
    } finally {
      loading.value = false
    }
  }

  async function handlePayPayroll() {
    loading.value = true
    try {
      await apiClient.post(`/api/salary/sheets/${month.value}/pay`)
      await entryStore.fetchEntries()
      message.success(`${month.value} 工资发放确认成功，已生成付款凭证！`)
      await reloadAll()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '工资发放失败')
    } finally {
      loading.value = false
    }
  }

  async function handlePaySocial() {
    loading.value = true
    try {
      await apiClient.post(`/api/salary/sheets/${month.value}/pay-social`)
      await entryStore.fetchEntries()
      message.success(`${month.value} 社保缴纳成功，已生成付款凭证！`)
      await reloadAll()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '社保缴纳失败')
    } finally {
      loading.value = false
    }
  }

  async function handlePayHousingFund() {
    loading.value = true
    try {
      await apiClient.post(`/api/salary/sheets/${month.value}/pay-housing-fund`)
      await entryStore.fetchEntries()
      message.success(`${month.value} 公积金缴纳成功，已生成付款凭证！`)
      await reloadAll()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '公积金缴纳失败')
    } finally {
      loading.value = false
    }
  }

  async function handlePayTax() {
    loading.value = true
    try {
      await apiClient.post(`/api/salary/sheets/${month.value}/pay-tax`)
      await entryStore.fetchEntries()
      message.success(`${month.value} 个人所得税缴纳成功，已生成付款凭证！`)
      await reloadAll()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '个税缴纳失败')
    } finally {
      loading.value = false
    }
  }

  return {
    payrollAccrued,
    payrollPaid,
    payrollPendingReview,
    payrollSsPaid,
    payrollHfPaid,
    payrollTaxPaid,
    employees,
    salarySheet,
    showSalaryTable,
    payrollPaymentSuggestions,
    applyingSalaryStatementId,
    payrollHasSocial,
    payrollHasTax,
    hasBlockingPayroll,
    totalNetSalary,
    totalCompanyCost,
    loadSalarySheet,
    loadPayrollPaymentSuggestions,
    handleApplySalaryPayment,
    handleAccruePayroll,
    handlePayPayroll,
    handlePaySocial,
    handlePayHousingFund,
    handlePayTax,
  }
}
