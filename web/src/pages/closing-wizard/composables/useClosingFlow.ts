/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 结账域（closing-wizard Step 3→5）：执行结账（含批量过账合并路径）、已结账
 * 指标拉取、AI CFO 诊断、下载提示。从 index.vue 拆出。
 *
 * 依赖注入口径：阻断判断（工资/零申报月/对账风险/批量过账）与期间状态
 * （periodCloseChecks/currentStep）由父页传入 Ref 或回调——结账是各域的
 * 汇合点，判定权仍归各域所有，本模块只编排与请求。
 */
import { ref, type Ref } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getAIDiagnosis, getIncomeStatement } from '@/api/report'
import { getTodos } from '@/api/todo'
import { buildClosingTodoSummary, type ClosingTodoSummary } from '@/utils/closingTodos'
import { getClosingReadiness, canBatchPostDrafts } from '@/utils/closingReadiness'
import { reportErrorToast } from '@/utils/errorPresenter'
import { offerRescue } from '@/composables/useRescue'
import type { useEntryStore } from '@/stores/entry'

export function useClosingFlow(options: {
  month: Ref<string>
  currentStep: Ref<number>
  periodCloseChecks: Ref<any>
  hasBlockingPayroll: Ref<boolean>
  isZeroRecordMonth: Ref<boolean>
  confirmZeroActivity: Ref<boolean>
  loadReconciliationHealth: () => Promise<void>
  /** getter：规避 setup 声明顺序（对账域 computed 声明晚于本模块实例化） */
  hasBlockingReconciliationRisk: () => boolean
  entryStore: ReturnType<typeof useEntryStore>
}) {
  const {
    month, currentStep, periodCloseChecks, hasBlockingPayroll, isZeroRecordMonth,
    confirmZeroActivity, loadReconciliationHealth, hasBlockingReconciliationRisk, entryStore,
  } = options
  const message = useMessage()
  const dialog = useDialog()

  const closed = ref(false)
  const closingLoading = ref(false)
  const monthlyIncome = ref(0)
  const monthlyExpense = ref(0)
  const netProfit = ref(0)
  const cfoDiagnosis = ref<any>(null)
  const diagnosisLoading = ref(false)
  // 本次结账自动生成的期末损益结转凭证号（完成页持久展示，不只靠瞬时 toast）
  const closingVoucherRefs = ref<string[]>([])
  // 本次结账减免的增值税及免税凭证号
  const vatExemptAmount = ref(0)
  const vatExemptVoucherRefs = ref<string[]>([])
  // 结账成功页「结账后待办」卡的摘要数据（只读展示，不做卡内办结）
  const closingTodos = ref<ClosingTodoSummary>({ pendingCount: 0, topItems: [] })

  const canBatchPostAndClose = () => canBatchPostDrafts(periodCloseChecks.value)

  // 结账后自动拉取报表指标（免费）；AI 诊断每次都是一次 LLM 调用，
  // 改为用户在诊断卡片上手动点击生成，避免每次打开已结账月份都烧 token
  async function fetchClosedMetrics() {
    try {
      const statement = await getIncomeStatement(month.value, 'summary')
      const value = (key: string) => Number(statement.rows.find(row => row.key === key)?.values?.current || 0)
      monthlyIncome.value = value('operating_revenue') + value('nonoperating_income') + value('investment_income')
      netProfit.value = Number(statement.totals?.netProfit?.current || 0)
      monthlyExpense.value = monthlyIncome.value - netProfit.value
    } catch {
      // ignore
    }
  }

  async function getFinancialContentWarning() {
    if (isZeroRecordMonth.value) {
      return {
        title: `${month.value} 尚无业务记录`,
        content: '本月凭证、银行流水和发票均为空。若您还没有录入或导入本月业务，建议先返回检查；继续结账后，本期将按零收支锁定归档。',
      }
    }

    try {
      const statement = await getIncomeStatement(month.value, 'summary')
      const detailRows = Array.isArray(statement?.rows)
        ? statement.rows.filter(row => !row.isSubtotal)
        : []
      if (detailRows.length === 0) {
        return {
          title: `${month.value} 利润表暂无明细`,
          content: '系统没有读取到本月利润表明细。若您还没有录入或过账本月业务，建议先返回检查；继续结账后，本期账册将锁定归档。',
        }
      }
      const allZero = detailRows.every((row) =>
        Math.abs(Number(row.values?.current || 0)) < 0.005
      )
      return allZero ? {
        title: `${month.value} 收入和支出均为 0`,
        content: '系统检查到本月利润表中的收入、成本和费用均为 0。若您还没有录入或过账本月业务，建议先返回检查；继续结账后，本期将按零收支锁定归档。',
      } : null
    } catch {
      // 无法核实利润表时先提醒，避免接口异常让全零账目直接结账。
      return {
        title: `${month.value} 利润表暂时无法核对`,
        content: '系统暂时无法确认本月收入和支出是否已录入。请先检查本月业务；若确认无需补录，仍可继续结账并锁定归档。',
      }
    }
  }

  // 结账后待办摘要：给成功页待办卡取数。与 fetchClosedMetrics 同一原则——
  // 引导可以失灵但不能挡住主流程，失败时静默置空，卡片整块不渲染
  async function fetchClosingTodos() {
    try {
      const resp = await getTodos()
      closingTodos.value = buildClosingTodoSummary(resp.todos || [])
    } catch {
      closingTodos.value = { pendingCount: 0, topItems: [] }
    }
  }

  async function generateDiagnosis() {
    if (diagnosisLoading.value) return
    diagnosisLoading.value = true
    try {
      cfoDiagnosis.value = await getAIDiagnosis(month.value)
    } catch {
      message.error('AI 诊断生成失败，请稍后重试')
    } finally {
      diagnosisLoading.value = false
    }
  }

  // Perform Month-end Closing and lock period
  async function runClosing(confirmedZeroClose: boolean = false) {
    if (hasBlockingPayroll.value) {
      currentStep.value = 2
      message.warning('本月在职员工工资尚未完成计提，请先计提后再结账（发放可延至次月）。')
      return
    }
    if (isZeroRecordMonth.value && !confirmZeroActivity.value) {
      message.warning('本月尚未录入任何账目记录。若确认本月无经营业务，请先勾选确认零申报。')
      return
    }
    if (closingLoading.value) return

    closingLoading.value = true
    try {
      if (confirmedZeroClose !== true) {
        const warning = await getFinancialContentWarning()
        if (warning) {
          dialog.warning({
            ...warning,
            positiveText: '仍要继续结账',
            negativeText: '返回检查',
            onPositiveClick: () => { void runClosing(true) },
          })
          return
        }
      }

      await loadReconciliationHealth()
      if (hasBlockingReconciliationRisk()) {
        currentStep.value = 2
        message.warning('仍有高风险流水认领问题，请先处理后再结账。')
        return
      }

      const checks: any = await apiClient.get(`/api/periods/${month.value}/close-checks`)
      periodCloseChecks.value = checks
      if (!checks.canClose && !canBatchPostAndClose()) {
        const blocking = getClosingReadiness(checks).filter(item => item.status === 'blocked')
        currentStep.value = 3
        message.warning(`还不能结账：${blocking.map(item => item.title).join('、')}`)
        return
      }
      if (!checks.snapshotHash) {
        currentStep.value = 3
        message.warning('结账预览已失效，请重新加载本月检查结果后再确认')
        periodCloseChecks.value = await apiClient.get(`/api/periods/${month.value}/close-checks`)
        return
      }

      // 幂等键交给 axios 拦截器按次生成：固定 key 会让反结账后的重新结账命中
      // 上一次成功响应的缓存，前端显示成功而实际未落库
      const result: any = await apiClient.post(`/api/periods/${month.value}/close`, {
        expectedSnapshotHash: checks.snapshotHash,
      })
      closed.value = result.status === 'closed' || result.periodStatus === 'closed'
      periodCloseChecks.value = result
      currentStep.value = 3
      await entryStore.fetchEntries()
      await fetchClosedMetrics()
      // 待办卡不阻塞成功 toast，取到后自行填充
      void fetchClosingTodos()
      const postedCount = Number(result.autoPostedCount || 0)
      const closingRefs: string[] = Array.isArray(result.closingVoucherRefs) ? result.closingVoucherRefs.filter(Boolean) : []
      closingVoucherRefs.value = closingRefs
      vatExemptAmount.value = Number(result.vatExemptAmount || 0)
      vatExemptVoucherRefs.value = Array.isArray(result.vatExemptVoucherRefs) ? result.vatExemptVoucherRefs.filter(Boolean) : []
      const closingSuffix = closingRefs.length ? `（已自动生成期末损益结转凭证：${closingRefs.join('、')}）` : ''
      message.success(postedCount
        ? `🎉 已批量过账 ${postedCount} 张凭证并完成 ${month.value} 结账${closingSuffix}。`
        : `🎉 ${month.value} 结账成功！本期账册已锁定${closingSuffix}。`)
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (detail?.code === 'CLOSE_CHECK_FAILED' && detail.context?.checks) {
        periodCloseChecks.value = { ...periodCloseChecks.value, checks: detail.context.checks, canClose: false }
        message.error('本月核验仍有阻断项；本次自动过账已一并回滚，请按检查结果处理后重试。')
      } else if (detail?.code === 'CLOSING_SNAPSHOT_STALE') {
        try {
          periodCloseChecks.value = await apiClient.get(`/api/periods/${month.value}/close-checks`)
        } catch {
          periodCloseChecks.value = null
        }
        currentStep.value = 3
        message.warning('账务数据在确认前发生了变化，本次没有结账。已刷新最新检查结果，请重新核对后再确认。', {
          duration: 7000,
        })
      } else if (detail?.code === 'REVIEW_REQUIRED') {
        // 救援层能定位到具体是哪张待复核，比这里一句笼统的话有用
        if (!offerRescue(err)) {
          message.error('本月有凭证需要其他复核人审批，本次过账和结账均未完成。')
        }
      } else {
        reportErrorToast(message, err, '结账失败，请检查阻断项')
      }
    } finally {
      closingLoading.value = false
    }
  }

  function downloadReport() {
    message.success('月度财务报销报告及凭证归档包 PDF 生成成功，正在下载...')
  }

  return {
    closed,
    closingLoading,
    monthlyIncome,
    monthlyExpense,
    netProfit,
    cfoDiagnosis,
    diagnosisLoading,
    closingVoucherRefs,
    vatExemptAmount,
    vatExemptVoucherRefs,
    closingTodos,
    fetchClosedMetrics,
    fetchClosingTodos,
    generateDiagnosis,
    runClosing,
    downloadReport,
  }
}
