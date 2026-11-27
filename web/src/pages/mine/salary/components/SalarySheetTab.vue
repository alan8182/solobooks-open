<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NSpace, NTag, NInputNumber, NDatePicker, NDropdown, NModal, NInput, NSpin, useMessage } from 'naive-ui'
import dayjs from 'dayjs'
import { formatMoney } from '@/utils/format'
import { SoloEmpty, SoloMonthPicker } from '@/components/ui'
import GuideTrigger from '@/components/business/GuideTrigger.vue'
import SalaryDetailDrawer from './SalaryDetailDrawer.vue'
import { useEntryStore } from '@/stores/entry'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import type { RdProject } from '@/api/types'
import {
  updateSalaryRecords, accrueSalarySheet, unaccrueSalarySheet, paySalarySheet,
  updateRdSalaryAllocations, applySalaryPayment, applyPayrollLiability,
  exportSalarySheet, exportSalaryPayslip,
  paySalarySocial, paySalaryHousingFund, paySalaryTax,
  previewSalaryTaxBatch, previewSalaryTax, syncSalarySheetFromPrevious,
  type Employee, type SalaryRecord, type SalarySheet, type RdSalaryAllocation,
  type SalaryPaymentSuggestion, type PayrollLiabilitySuggestion, type PayrollRates,
  type DepartmentItem,
} from '@/api/salary'

const props = defineProps<{
  selectedMonth: string
  sheet: SalarySheet | null
  records: SalaryRecord[]
  employees: Employee[]
  departments: DepartmentItem[]
  rdProjects?: RdProject[]
  rdAllocations: RdSalaryAllocation[]
  paymentSuggestions: SalaryPaymentSuggestion[]
  liabilitySuggestions: PayrollLiabilitySuggestion[]
  payrollRates: PayrollRates | null
  housingFund: { personal: number; company: number }
}>()

const emit = defineEmits<{
  (e: 'update:selected-month', month: string): void
  (e: 'sheet-updated', data: SalarySheet): void
  (e: 'reload-suggestions'): void
  (e: 'reload-rd'): void
  (e: 'open-rd', row: SalaryRecord): void
  (e: 'open-tax', row: SalaryRecord): void
  (e: 'goto-roster'): void
  (e: 'add-employee'): void
  (e: 'totals', totals: { count: number; net: number; cost: number; withheld: number }): void
}>()

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const viewStore = useWorkspaceViewStore()

const busy = ref(false)
const exporting = ref(false)
const exportingRecordId = ref('')
const applyingKey = ref('')
const showSuggestions = ref(false)
const showDrawer = ref(false)
const drawerRecordId = ref<string | null>(null)
const saveState = ref<'idle' | 'saving' | 'saved'>('idle')
const payDate = ref(dayjs().format('YYYY-MM-DD'))

// 本地可编辑副本：父级数据来自接口，编辑只动副本，保存/计提后由接口结果回写
const rows = ref<SalaryRecord[]>([])
watch(() => props.records, (list) => {
  rows.value = (list || []).map((r) => {
    const copy = { ...r }
    if (copy.personal_housing_fund === undefined || copy.personal_social_insurance === undefined) {
      const pTot = Number(copy.personal_social_security || 0)
      const pHf = Math.min(Math.max(props.housingFund.personal, 0), pTot)
      copy.personal_housing_fund = pHf
      copy.personal_social_insurance = Math.round((pTot - pHf) * 100) / 100
    }
    if (copy.company_housing_fund === undefined || copy.company_social_insurance === undefined) {
      const cTot = Number(copy.company_social_security || 0)
      const cHf = Math.min(Math.max(props.housingFund.company, 0), cTot)
      copy.company_housing_fund = cHf
      copy.company_social_insurance = Math.round((cTot - cHf) * 100) / 100
    }
    return copy
  })
}, { immediate: true })

watch(() => props.selectedMonth, () => {
  payDate.value = dayjs().format('YYYY-MM-DD')
  saveState.value = 'idle'
  showDrawer.value = false
  drawerRecordId.value = null
})

const isDraft = computed(() => props.sheet?.status === 'draft')
const drawerRow = computed(() => rows.value.find((r) => r.id === drawerRecordId.value) || null)
function openDrawer(row: SalaryRecord) {
  drawerRecordId.value = row.id
  showDrawer.value = true
}
const showRdCol = computed(() =>
  rows.value.some((r) => isRd(r)) || props.rdAllocations.length > 0,
)
const taxTimers = new Map<string, ReturnType<typeof setTimeout>>()
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null
const autoTaxedSheets = new Set<string>()

function clearAutoSaveTimer() {
  if (autoSaveTimer) {
    clearTimeout(autoSaveTimer)
    autoSaveTimer = null
  }
}

function scheduleAutoSave() {
  if (!isDraft.value || !props.sheet?.id) return
  clearAutoSaveTimer()
  saveState.value = 'saving'
  autoSaveTimer = setTimeout(async () => {
    autoSaveTimer = null
    try {
      const updated = await updateSalaryRecords(props.selectedMonth, rows.value)
      emit('sheet-updated', updated as any)
      saveState.value = 'saved'
    } catch {
      saveState.value = 'idle'
    }
  }, 800)
}

onBeforeUnmount(() => {
  clearAutoSaveTimer()
  taxTimers.forEach((t) => clearTimeout(t))
})

function debounceAutoTax(row: SalaryRecord) {
  if (taxTimers.has(row.id)) {
    clearTimeout(taxTimers.get(row.id)!)
  }
  const timer = setTimeout(async () => {
    taxTimers.delete(row.id)
    try {
      const gross = Number(row.base_salary || 0) + Number(row.bonus || 0)
        + Number(row.allowance || 0) - Number(row.deduction || 0)
      const pSi = Number(row.personal_social_insurance ?? 0)
      const pHf = Number(row.personal_housing_fund ?? 0)
      const res = await previewSalaryTax({
        month: props.selectedMonth,
        employeeId: row.employee_id,
        gross: Math.max(gross, 0),
        withheld: Math.round((pSi + pHf) * 100) / 100,
      })
      row.personal_tax = Number(res.taxDue || 0)
      recalc(row, false, true)
    } catch {
      // 忽略自动试算失败
    }
  }, 600)
  taxTimers.set(row.id, timer)
}

async function autoCalculateBatchTaxIfNeeded() {
  const sheetId = props.sheet?.id
  if (!sheetId || !isDraft.value || !rows.value.length || autoTaxedSheets.has(sheetId)) return
  const needsAutoTax = rows.value.some((r) => {
    const gross = Number(r.base_salary || 0) + Number(r.bonus || 0) + Number(r.allowance || 0) - Number(r.deduction || 0)
    const withheld = Number(r.personal_social_security || 0)
    return gross - withheld > 5000 && Number(r.personal_tax || 0) === 0
  })
  autoTaxedSheets.add(sheetId)
  if (!needsAutoTax) return
  try {
    const res = await previewSalaryTaxBatch({
      month: props.selectedMonth,
      items: rows.value.map((r) => {
        const gross = Number(r.base_salary || 0) + Number(r.bonus || 0)
          + Number(r.allowance || 0) - Number(r.deduction || 0)
        const pSi = Number(r.personal_social_insurance ?? 0)
        const pHf = Number(r.personal_housing_fund ?? 0)
        return {
          employeeId: r.employee_id,
          gross: Math.max(gross, 0),
          withheld: Math.round((pSi + pHf) * 100) / 100,
        }
      }),
    })
    const map = new Map(res.items.map((i) => [i.employeeId, Number(i.taxDue || 0)]))
    let changed = false
    for (const r of rows.value) {
      const nextTax = map.get(r.employee_id)
      if (nextTax !== undefined && nextTax !== Number(r.personal_tax || 0)) {
        r.personal_tax = nextTax
        recalc(r, false, false)
        changed = true
      }
    }
    if (changed) scheduleAutoSave()
  } catch {
    // 忽略静默预估异常
  }
}

watch(
  [() => props.sheet?.id, () => props.sheet?.status, () => rows.value.length],
  () => {
    autoCalculateBatchTaxIfNeeded()
  },
  { immediate: true },
)

function recalc(row: SalaryRecord, triggerAutoTax = false, triggerSave = true) {
  const pSi = Number(row.personal_social_insurance ?? 0)
  const pHf = Number(row.personal_housing_fund ?? 0)
  const cSi = Number(row.company_social_insurance ?? 0)
  const cHf = Number(row.company_housing_fund ?? 0)
  row.personal_social_security = Math.round((pSi + pHf) * 100) / 100
  row.company_social_security = Math.round((cSi + cHf) * 100) / 100

  const gross = Number(row.base_salary || 0) + Number(row.bonus || 0)
    + Number(row.allowance || 0) - Number(row.deduction || 0)
  row.net_salary = Math.round((gross - row.personal_social_security - Number(row.personal_tax || 0)) * 100) / 100
  row.total_cost = Math.round((gross + row.company_social_security) * 100) / 100

  if (triggerAutoTax && isDraft.value) {
    debounceAutoTax(row)
  }
  if (triggerSave && isDraft.value) {
    scheduleAutoSave()
  }
}

const totals = computed(() => {
  let gross = 0, net = 0, cost = 0, personal = 0, tax = 0, company = 0
  let pSocial = 0, pHf = 0, cSocial = 0, cHf = 0
  for (const r of rows.value) {
    gross += Number(r.base_salary || 0) + Number(r.bonus || 0) + Number(r.allowance || 0) - Number(r.deduction || 0)
    net += Number(r.net_salary || 0)
    cost += Number(r.total_cost || 0)
    personal += Number(r.personal_social_security || 0)
    tax += Number(r.personal_tax || 0)
    company += Number(r.company_social_security || 0)
    pSocial += Number(r.personal_social_insurance || 0)
    pHf += Number(r.personal_housing_fund || 0)
    cSocial += Number(r.company_social_insurance || 0)
    cHf += Number(r.company_housing_fund || 0)
  }
  const r2 = (n: number) => Math.round(n * 100) / 100
  return {
    gross: r2(gross), net: r2(net), cost: r2(cost), personal: r2(personal), tax: r2(tax), company: r2(company),
    pSocial: r2(pSocial), pHf: r2(pHf), cSocial: r2(cSocial), cHf: r2(cHf),
  }
})

watch(totals, (t) => emit('totals', {
  count: rows.value.length, net: t.net, cost: t.cost, withheld: Math.round((t.personal + t.tax) * 100) / 100,
}), { immediate: true })

const r2 = (n: number) => Math.round(n * 100) / 100
const ssTotal = computed(() => r2(totals.value.pSocial + totals.value.cSocial))
const hfTotal = computed(() => r2(totals.value.pHf + totals.value.cHf))
const benefitTotal = computed(() => r2(ssTotal.value + hfTotal.value))
const netRatio = computed(() =>
  totals.value.cost > 0 ? ((totals.value.net / totals.value.cost) * 100).toFixed(1) : '0.0',
)
const barSegments = computed(() => {
  const total = totals.value.cost
  if (total <= 0) return { net: 0, benefit: 0, tax: 0 }
  const net = Math.min(Math.max((totals.value.net / total) * 100, 0), 100)
  const benefit = Math.min(Math.max((benefitTotal.value / total) * 100, 0), 100 - net)
  const tax = Math.min(Math.max((totals.value.tax / total) * 100, 0), 100 - net - benefit)
  return {
    net: Number(net.toFixed(2)),
    benefit: Number(benefit.toFixed(2)),
    tax: Number(tax.toFixed(2)),
  }
})

const statusPill = computed(() => {
  const boss = viewStore.isBoss
  switch (props.sheet?.status) {
    case 'paid': return { cls: 'pill-paid', icon: 'ri-checkbox-circle-line', text: boss ? '已发薪完结' : '已发放记账' }
    case 'accrued': return { cls: 'pill-accrued', icon: 'ri-time-line', text: boss ? '已确认（待发薪）' : '已计提记账' }
    case 'pending_review': return { cls: 'pill-pending', icon: 'ri-shield-flash-line', text: '待复核' }
    default: return { cls: 'pill-draft', icon: 'ri-draft-line', text: boss ? '草稿（待确认）' : '草稿（待计提）' }
  }
})

function rowGross(row: SalaryRecord) {
  return r2(
    Number(row.base_salary || 0)
    + Number(row.bonus || 0)
    + Number(row.allowance || 0)
    - Number(row.deduction || 0),
  )
}

function rowAdjustSub(row: SalaryRecord) {
  const bonus = Number(row.bonus || 0)
  const allowance = Number(row.allowance || 0)
  const deduction = Number(row.deduction || 0)
  if (bonus <= 0 && allowance <= 0 && deduction <= 0) return ''
  const parts = [`基本 ${formatMoney(Number(row.base_salary || 0))}`]
  if (bonus > 0) parts.push(`+奖 ${formatMoney(bonus)}`)
  if (allowance > 0) parts.push(`+补 ${formatMoney(allowance)}`)
  if (deduction > 0) parts.push(`-扣 ${formatMoney(deduction)}`)
  return parts.join(' · ')
}

function deptOf(row: SalaryRecord) {
  return props.employees.find((e) => e.id === row.employee_id)?.department || '行政部'
}
function isRd(row: SalaryRecord) {
  return Boolean(props.departments.find((d) => d.name === deptOf(row))?.isRd)
}
function rdSummary(employeeId: string) {
  const list = props.rdAllocations.filter((a) => a.employeeId === employeeId)
  if (!list.length) return '未配置'
  const rate = list.reduce((s, a) => s + Number(a.allocationRate || 0), 0)
  return `${list.length}个项目 · ${(rate * 100).toFixed(0)}%`
}
function hasRd(employeeId: string) {
  return props.rdAllocations.some((a) => a.employeeId === employeeId)
}

// ---- 三阶段进度 ----
type StepState = 'done' | 'current' | 'review' | 'wait'
const settled = (s?: string) => s === 'posted' || s === 'not_required'
const allLiabilitiesSettled = computed(() =>
  settled(props.sheet?.ssPaymentStatus) && settled(props.sheet?.hfPaymentStatus) && settled(props.sheet?.taxPaymentStatus))
const isFullySettled = computed(() =>
  Boolean(props.sheet && allLiabilitiesSettled.value && props.sheet.paymentStatus === 'posted'))

const voucherDropdownOptions = computed(() => {
  const s = props.sheet
  if (!s) return []
  const items: Array<{ label: string; key: string }> = []
  if (s.accrual_voucher_id) items.push({ label: '查看计提凭证', key: s.accrual_voucher_id })
  if (s.payment_voucher_id) items.push({ label: '查看发放凭证', key: s.payment_voucher_id })
  if (s.ss_paid_voucher_id) items.push({ label: '查看社保凭证', key: s.ss_paid_voucher_id })
  if (s.hf_paid_voucher_id) items.push({ label: '查看公积金凭证', key: s.hf_paid_voucher_id })
  if (s.tax_paid_voucher_id) items.push({ label: '查看个税凭证', key: s.tax_paid_voucher_id })
  return items
})

const steps = computed<Array<{ title: string; desc: string; state: StepState }>>(() => {
  const s = props.sheet
  const accrued = s?.status === 'accrued' || s?.status === 'paid'
  const step1: StepState = accrued ? 'done' : s?.status === 'pending_review' ? 'review' : 'current'
  const step2: StepState = s?.paymentStatus === 'posted' ? 'done'
    : s?.paymentStatus === 'pending_review' ? 'review' : accrued ? 'current' : 'wait'
  const step3: StepState = allLiabilitiesSettled.value && s?.paymentStatus === 'posted' ? 'done'
    : accrued ? 'current' : 'wait'
  const label = (st: StepState, done: string, cur: string) =>
    st === 'done' ? done : st === 'review' ? '凭证待复核' : st === 'current' ? cur : '待上一步完成'
  const boss = viewStore.isBoss
  return [
    { title: boss ? '① 确认工资表' : '① 成本计提', desc: label(step1, boss ? '已确认入账' : '已列入当月成本', boss ? '核对本月应发与实发' : '核对后计提记账'), state: step1 },
    { title: boss ? '② 登记发薪' : '② 工资发放', desc: label(step2, boss ? '已登记发薪' : '已划扣记账', boss ? '选择发薪日并登记' : '选择发薪日并记账'), state: step2 },
    { title: boss ? '③ 社保与个税扣缴' : '③ 社保 / 公积金 / 个税', desc: label(step3, boss ? '均已登记扣缴' : '均已缴纳记账', boss ? '登记社保、公积金、个税扣款' : '逐项缴纳记账'), state: step3 },
  ]
})

// ---- 操作 ----
function errMsg(error: any, fallback: string) {
  const d = error?.response?.data?.detail
  return typeof d === 'string' ? d : d?.message || fallback
}

async function run(fn: () => Promise<void>, fallback: string) {
  busy.value = true
  try { await fn() } catch (error: any) { message.error(errMsg(error, fallback)) } finally { busy.value = false }
}

async function afterPosting(data: SalarySheet) {
  emit('sheet-updated', data)
  await entryStore.fetchEntries()
  emit('reload-suggestions')
}

const accrue = () => run(async () => {
  clearAutoSaveTimer()
  await updateSalaryRecords(props.selectedMonth, rows.value)
  await updateRdSalaryAllocations(props.selectedMonth, props.rdAllocations)
  const data: any = await accrueSalarySheet(props.selectedMonth)
  await afterPosting(data)
  message.success(data.status === 'accrued'
    ? '工资计提凭证生成成功，已列入当月研发/管理成本'
    : '计提凭证已创建，待复核通过后计入成本（可在审核中心复核）')
}, '计提失败')

const showUnaccrueModal = ref(false)
const unaccrueReason = ref('工资数据核对有误，撤销重算')
const unaccruing = ref(false)
const syncingPrevious = ref(false)

async function syncFromPrevious() {
  if (!isDraft.value) return
  clearAutoSaveTimer()
  syncingPrevious.value = true
  try {
    const data = await syncSalarySheetFromPrevious(props.selectedMonth)
    saveState.value = 'idle'
    emit('sheet-updated', data)
    emit('reload-rd')
    message.success(`已按 ${data.carryForward?.sourceMonth || '上月'} 固定项与最新花名册同步本月草稿`)
  } catch (error: any) {
    message.error(errMsg(error, '同步上月数据失败'))
  } finally {
    syncingPrevious.value = false
  }
}

const activeDownstreamLabels = computed(() => {
  const s = props.sheet
  if (!s) return []
  const active = (st?: string) => st === 'posted' || st === 'pending_review'
  const labels: string[] = []
  if (active(s.paymentStatus) || Boolean(s.payment_voucher_id)) labels.push('工资发放')
  if (active(s.ssPaymentStatus) || Boolean(s.ss_paid_voucher_id)) labels.push('社保')
  if (active(s.hfPaymentStatus) || Boolean(s.hf_paid_voucher_id)) labels.push('公积金')
  if (active(s.taxPaymentStatus) || Boolean(s.tax_paid_voucher_id)) labels.push('个税')
  return labels
})

const hasActiveDownstream = computed(() => activeDownstreamLabels.value.length > 0)

const unaccrueButtonLabel = computed(() => {
  if (props.sheet?.status === 'pending_review') {
    return viewStore.isBoss ? '撤回重改' : '撤回计提'
  }
  if (hasActiveDownstream.value) {
    return viewStore.isBoss ? '撤销重算' : '全部冲销重算'
  }
  return viewStore.isBoss ? '撤销确认' : '冲销计提'
})

function openUnaccrueModal() {
  unaccrueReason.value = '工资数据核对有误，撤销重算'
  showUnaccrueModal.value = true
}

async function confirmUnaccrue() {
  const reason = unaccrueReason.value.trim()
  if (!reason) {
    message.warning('请填写撤销原因')
    return
  }
  unaccruing.value = true
  try {
    const data = await unaccrueSalarySheet(props.selectedMonth, reason, hasActiveDownstream.value)
    showUnaccrueModal.value = false
    await afterPosting(data)
    const revCount = data.reversedVoucherIds?.length ?? (data.reversalVoucherId ? 1 : 0)
    const unmatchCount = data.unmatchedStatementCount ?? 0
    if (revCount > 0) {
      const unmatchNote = unmatchCount > 0 ? `并解除 ${unmatchCount} 笔银行流水对账，` : '，'
      message.success(
        `已生成 ${revCount} 笔红字冲销凭证${unmatchNote}${props.selectedMonth} 工资表已退回草稿，修改后请重新确认`,
      )
    } else {
      message.success(`已撤回，${props.selectedMonth} 工资表已退回草稿`)
    }
  } catch (error: any) {
    message.error(errMsg(error, '撤销确认失败'))
  } finally {
    unaccruing.value = false
  }
}

const pay = () => run(async () => {
  await afterPosting(await paySalarySheet(props.selectedMonth, payDate.value) as any)
  message.success(`工资发放记账成功（发薪日 ${payDate.value}）`)
}, '发放失败')

const liabilityApi = { social: paySalarySocial, housing: paySalaryHousingFund, tax: paySalaryTax }
const liabilityLabel = { social: '社保', housing: '公积金', tax: '个税' }
const payLiability = (kind: 'social' | 'housing' | 'tax') => run(async () => {
  await afterPosting(await liabilityApi[kind](props.selectedMonth, payDate.value) as any)
  message.success(`${liabilityLabel[kind]}缴纳记账成功`)
}, '操作失败')

// 一键登记：按所选日期依次完成未结清的发薪与社保/公积金/个税（复用同一批后端接口，凭证逻辑不变）
const payAll = () => run(async () => {
  const s = props.sheet
  if (!s) return
  let data: any = s
  const steps: Array<[boolean, () => Promise<any>]> = [
    [s.status === 'accrued' && !s.payment_voucher_id, () => paySalarySheet(props.selectedMonth, payDate.value)],
    [s.ssPaymentStatus === 'unpaid', () => paySalarySocial(props.selectedMonth, payDate.value)],
    [s.hfPaymentStatus === 'unpaid', () => paySalaryHousingFund(props.selectedMonth, payDate.value)],
    [s.taxPaymentStatus === 'unpaid', () => paySalaryTax(props.selectedMonth, payDate.value)],
  ]
  for (const [need, call] of steps) {
    if (need) data = await call()
  }
  await afterPosting(data)
  message.success('已登记本月发薪与扣缴')
}, '一键登记失败，已完成的步骤不会回滚，请刷新后继续')
const hasUnsettled = computed(() => {
  const s = props.sheet
  if (!s || s.status === 'draft' || s.status === 'pending_review') return false
  return (s.status === 'accrued' && !s.payment_voucher_id)
    || s.ssPaymentStatus === 'unpaid' || s.hfPaymentStatus === 'unpaid' || s.taxPaymentStatus === 'unpaid'
})

async function applyLiability(item: PayrollLiabilitySuggestion) {
  applyingKey.value = `${item.kind}-${item.statement.id}`
  try {
    const result: any = await applyPayrollLiability(item.month, item.kind, item.statement.id)
    await entryStore.fetchEntries()
    emit('reload-suggestions')
    if (item.month === props.selectedMonth) emit('sheet-updated', result)
    message.success(result.linked
      ? `${item.month} ${item.label}已按 ${item.statement.date} 流水缴纳并核销`
      : `${item.label}已缴纳，但流水自动核销失败：${result.linkError || '请到银行流水页手工关联'}`)
  } catch (error: any) {
    message.error(errMsg(error, '缴纳核销失败'))
  } finally { applyingKey.value = '' }
}

async function applySalary(item: SalaryPaymentSuggestion) {
  applyingKey.value = `salary-${item.statement.id}`
  try {
    const result: any = await applySalaryPayment(item.month, item.statement.id)
    await entryStore.fetchEntries()
    emit('reload-suggestions')
    if (item.month === props.selectedMonth) emit('sheet-updated', result)
    message.success(result.linked
      ? `${item.month} 工资已按 ${item.statement.date} 流水发放并核销`
      : `工资已发放，但流水自动核销失败：${result.linkError || '请到银行流水页手工关联'}`)
  } catch (error: any) {
    message.error(errMsg(error, '工资发放核销失败'))
  } finally { applyingKey.value = '' }
}

function download(blob: unknown, filename: string) {
  const url = URL.createObjectURL(blob as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function exportSheet() {
  exporting.value = true
  try {
    // 导出读的是后端已存数据，草稿先静默保存，避免导出旧数
    if (isDraft.value) emit('sheet-updated', await updateSalaryRecords(props.selectedMonth, rows.value) as any)
    download(await exportSalarySheet(props.selectedMonth), `工资表-${props.selectedMonth}.csv`)
  } catch (error: any) {
    message.error(errMsg(error, '导出工资表失败'))
  } finally { exporting.value = false }
}

async function exportPayslip(row: SalaryRecord) {
  exportingRecordId.value = row.id
  try {
    download(await exportSalaryPayslip(props.selectedMonth, row.id), `工资条-${props.selectedMonth}-${row.employee_name}.csv`)
  } catch (error: any) {
    message.error(errMsg(error, '导出工资条失败'))
  } finally { exportingRecordId.value = '' }
}

const viewVoucher = (id: string) => router.push(`/ledger/${id}`)
const suggestionCount = computed(() => props.liabilitySuggestions.length + props.paymentSuggestions.length)

defineExpose({ exportSheet, exporting, triggerAutoSave: scheduleAutoSave })
</script>

<template>
  <NSpin :show="busy">
    <div class="sheet-tab">
      <!-- 月度成本与资金去向聚合面板 -->
      <section class="overview-panel">
        <div class="overview-top">
          <div class="overview-meta">
            <span class="control-label">结算月份</span>
            <SoloMonthPicker
              :value="selectedMonth"
              :clearable="false"
              class="month-picker"
              @update:value="(v) => v && emit('update:selected-month', v)"
            />
            <span v-if="sheet" class="status-pill" :class="statusPill.cls">
              <i :class="statusPill.icon" />{{ statusPill.text }}
            </span>
            <span class="headcount-note">本月应发 {{ rows.length }} 人 / 花名册 {{ employees.length }} 人</span>
          </div>
          <NButton
            v-if="rows.length"
            size="small"
            secondary
            :loading="exporting"
            @click="exportSheet"
          >
            <template #icon><i class="ri-download-2-line" /></template>
            导出工资表
          </NButton>
        </div>

        <template v-if="rows.length">
          <div class="overview-body">
            <div class="overview-primary">
              <div class="overview-label">企业总人工成本</div>
              <div class="overview-hero">{{ formatMoney(totals.cost) }}</div>
              <div class="overview-sub">
                应发 {{ formatMoney(totals.gross) }} + 单位五险一金 {{ formatMoney(totals.company) }}
              </div>
            </div>

            <div class="overview-flows">
              <div class="flow-item">
                <div class="flow-label"><span class="flow-dot dot-net" />发给员工（实发到手）</div>
                <div class="flow-value">{{ formatMoney(totals.net) }}</div>
                <div class="flow-sub">占总成本 {{ netRatio }}%</div>
              </div>
              <div class="flow-item">
                <div class="flow-label"><span class="flow-dot dot-benefit" />缴给社保与公积金（企+个）</div>
                <div class="flow-value">{{ formatMoney(benefitTotal) }}</div>
                <div class="flow-sub">社保 {{ formatMoney(ssTotal) }} · 公积金 {{ formatMoney(hfTotal) }}</div>
              </div>
              <div class="flow-item">
                <div class="flow-label"><span class="flow-dot dot-tax" />代扣个税（缴税务局）</div>
                <div class="flow-value">{{ formatMoney(totals.tax) }}</div>
                <div class="flow-sub">个人代扣合计 {{ formatMoney(totals.personal + totals.tax) }}</div>
              </div>
            </div>
          </div>

          <div v-if="totals.cost > 0" class="overview-bar" aria-hidden="true">
            <div class="bar-seg bar-net" :style="{ width: `${barSegments.net}%` }" />
            <div class="bar-seg bar-benefit" :style="{ width: `${barSegments.benefit}%` }" />
            <div class="bar-seg bar-tax" :style="{ width: `${barSegments.tax}%` }" />
          </div>
        </template>
      </section>

      <!-- 紧凑单行：三阶段流程 + 当前动作 / 待办付款 -->
      <section v-if="sheet && rows.length" class="workflow-panel">
        <div class="workflow-head">
          <div class="steps-inline">
            <template v-for="(step, idx) in steps" :key="step.title">
              <div class="step-inline" :class="`is-${step.state}`">
                <span class="step-inline-title">
                  <i v-if="step.state === 'done'" class="ri-checkbox-circle-fill" />
                  <i v-else-if="step.state === 'review'" class="ri-shield-flash-line" />
                  <i v-else-if="step.state === 'current'" class="ri-record-circle-line" />
                  <i v-else class="ri-checkbox-blank-circle-line" />
                  {{ step.title }}
                </span>
                <span class="step-inline-desc">{{ step.desc }}</span>
              </div>
              <i v-if="idx < steps.length - 1" class="ri-arrow-right-s-line step-arrow" />
            </template>
          </div>

          <div class="workflow-cta">
            <template v-if="isDraft">
              <span class="cta-note">{{ viewStore.isBoss ? '核对无误后确认入账' : '核对后计提记账' }}</span>
              <NButton type="primary" size="small" @click="accrue">
                <template #icon><i class="ri-check-double-line" /></template>
                {{ viewStore.isBoss ? '确认本月工资表' : '计提记账（列入当月成本）' }}
              </NButton>
            </template>
            <template v-else-if="sheet.status === 'pending_review'">
              <span class="cta-warn"><i class="ri-shield-flash-line" /> 计提凭证待复核</span>
              <NButton
                size="small"
                quaternary
                type="warning"
                class="unaccrue-btn"
                @click="openUnaccrueModal"
              >
                <template #icon><i class="ri-arrow-go-back-line" /></template>
                {{ viewStore.isBoss ? '撤回重改' : '撤回计提' }}
              </NButton>
              <NButton v-if="!viewStore.isBoss && sheet.accrual_voucher_id" size="small" secondary @click="viewVoucher(sheet.accrual_voucher_id!)">
                查看计提凭证
              </NButton>
            </template>
            <template v-else-if="isFullySettled">
              <span class="settled-badge">
                <i class="ri-checkbox-circle-fill" />
                本月工资、社保、公积金及个税均已完成记账
              </span>
              <NDropdown
                v-if="!viewStore.isBoss && voucherDropdownOptions.length"
                :options="voucherDropdownOptions"
                @select="(key) => viewVoucher(String(key))"
              >
                <NButton size="small" secondary>
                  查看凭证 <i class="ri-arrow-down-s-line" />
                </NButton>
              </NDropdown>
              <NButton
                size="small"
                quaternary
                type="warning"
                class="unaccrue-btn"
                @click="openUnaccrueModal"
              >
                <template #icon><i class="ri-arrow-go-back-line" /></template>
                {{ unaccrueButtonLabel }}
              </NButton>
            </template>
          </div>
        </div>

        <div v-if="!isDraft && sheet.status !== 'pending_review' && !isFullySettled" class="workflow-todos">
          <div class="todos-toolbar">
            <div class="todos-toolbar-left">
              <span class="pay-date-wrap">
                <span class="control-label">实际发薪/缴纳日</span>
                <NDatePicker v-model:formatted-value="payDate" value-format="yyyy-MM-dd" type="date" :clearable="false" size="small" class="pay-date-picker" />
              </span>
              <span v-if="sheet.paymentStatus === 'pending_review' && viewStore.isBoss" class="cta-warn">
                <i class="ri-shield-flash-line" /> 工资发放凭证已提交审核中心，复核通过后才算发放完成
              </span>
              <span v-if="sheet.taxPaymentStatus === 'unpaid'" class="guide-inline">
                <GuideTrigger guide="iit" label="个税还没申报？看申报步骤" />
              </span>
              <span v-if="sheet.ssPaymentStatus === 'unpaid'" class="guide-inline">
                <GuideTrigger guide="social-insurance" label="社保还没申报？看申报步骤" />
              </span>
            </div>
            <NSpace size="small" align="center">
              <NButton
                v-if="sheet.status === 'accrued' || sheet.status === 'paid'"
                size="small"
                quaternary
                type="warning"
                class="unaccrue-btn"
                :title="viewStore.isBoss ? '撤销已确认记录并退回草稿重新修改' : '冲销已入账凭证并退回草稿'"
                @click="openUnaccrueModal"
              >
                <template #icon><i class="ri-arrow-go-back-line" /></template>
                {{ unaccrueButtonLabel }}
              </NButton>
              <NButton v-if="!viewStore.isBoss && sheet.accrual_voucher_id" size="small" secondary @click="viewVoucher(sheet.accrual_voucher_id!)">
                查看计提凭证
              </NButton>
              <NButton v-if="viewStore.isBoss && hasUnsettled" size="small" type="primary" @click="payAll">
                一键登记已发已缴
              </NButton>
            </NSpace>
          </div>

          <!-- 待复核期间不能发放/缴纳；老板视图保持极简，分项记账入口留给会计视图 -->
          <div v-if="!viewStore.isBoss" class="todo-grid">
            <div class="todo-item">
              <div class="todo-info">
                <span class="todo-name">实发工资</span>
                <span class="todo-amount">{{ formatMoney(totals.net) }}</span>
              </div>
              <NButton v-if="sheet.status === 'accrued' && !sheet.payment_voucher_id" size="tiny" type="primary" @click="pay">
                发放工资记账（生成划扣凭证）
              </NButton>
              <NButton v-else-if="sheet.paymentStatus === 'posted' && sheet.payment_voucher_id" size="tiny" secondary @click="viewVoucher(sheet.payment_voucher_id!)">
                查看发放凭证
              </NButton>
              <NButton v-else-if="sheet.paymentStatus === 'pending_review'" size="tiny" secondary disabled>
                发放凭证待复核
              </NButton>
            </div>

            <div class="todo-item">
              <div class="todo-info">
                <span class="todo-name">社保（企+个）</span>
                <span class="todo-amount">{{ sheet.socialSecurityDue ? formatMoney(Number(sheet.socialSecurityDue)) : formatMoney(ssTotal) }}</span>
              </div>
              <NButton v-if="sheet.ssPaymentStatus === 'unpaid'" size="tiny" secondary type="primary" @click="payLiability('social')">
                缴纳社保{{ sheet.socialSecurityDue ? `（¥${sheet.socialSecurityDue}）` : '' }}
              </NButton>
              <NButton v-else-if="sheet.ssPaymentStatus === 'pending_review'" size="tiny" secondary disabled>
                社保凭证待复核
              </NButton>
              <NButton v-else-if="sheet.ss_paid_voucher_id" size="tiny" secondary @click="viewVoucher(sheet.ss_paid_voucher_id!)">
                查看社保凭证
              </NButton>
              <span v-else class="todo-done-text">{{ sheet.ssPaymentStatus === 'not_required' ? '无需缴纳' : '已结清' }}</span>
            </div>

            <div class="todo-item">
              <div class="todo-info">
                <span class="todo-name">公积金（企+个）</span>
                <span class="todo-amount">{{ sheet.housingFundDue ? formatMoney(Number(sheet.housingFundDue)) : formatMoney(hfTotal) }}</span>
              </div>
              <NButton v-if="sheet.hfPaymentStatus === 'unpaid'" size="tiny" secondary type="primary" @click="payLiability('housing')">
                缴纳公积金{{ sheet.housingFundDue ? `（¥${sheet.housingFundDue}）` : '' }}
              </NButton>
              <NButton v-else-if="sheet.hfPaymentStatus === 'pending_review'" size="tiny" secondary disabled>
                公积金凭证待复核
              </NButton>
              <NButton v-else-if="sheet.hf_paid_voucher_id" size="tiny" secondary @click="viewVoucher(sheet.hf_paid_voucher_id!)">
                查看公积金凭证
              </NButton>
              <span v-else class="todo-done-text">{{ sheet.hfPaymentStatus === 'not_required' ? '无需缴纳' : '已结清' }}</span>
            </div>

            <div class="todo-item">
              <div class="todo-info">
                <span class="todo-name">个人所得税</span>
                <span class="todo-amount">{{ sheet.taxDue ? formatMoney(Number(sheet.taxDue)) : formatMoney(totals.tax) }}</span>
              </div>
              <NButton v-if="sheet.taxPaymentStatus === 'unpaid'" size="tiny" secondary type="primary" @click="payLiability('tax')">
                缴纳个税{{ sheet.taxDue ? `（¥${sheet.taxDue}）` : '' }}
              </NButton>
              <NButton v-else-if="sheet.taxPaymentStatus === 'pending_review'" size="tiny" secondary disabled>
                个税凭证待复核
              </NButton>
              <NButton v-else-if="sheet.tax_paid_voucher_id" size="tiny" secondary @click="viewVoucher(sheet.tax_paid_voucher_id!)">
                查看个税凭证
              </NButton>
              <span v-else class="todo-done-text">{{ sheet.taxPaymentStatus === 'not_required' ? '无需缴纳' : '已结清' }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 银行流水智能认领：默认收起，不遮挡主表 -->
      <div v-if="suggestionCount" class="suggest-bar">
        <span class="suggest-title"><i class="ri-bank-line" /> 银行流水发现 {{ suggestionCount }} 笔可核销的工资/社保/个税</span>
        <NButton size="tiny" quaternary type="primary" @click="showSuggestions = !showSuggestions">
          {{ showSuggestions ? '收起' : '展开处理' }}
        </NButton>
      </div>
      <div v-if="suggestionCount && showSuggestions" class="suggest-list">
        <div v-for="item in liabilitySuggestions" :key="`${item.kind}-${item.statement.id}`" class="suggest-row">
          <span class="suggest-main">{{ item.month }} {{ item.label }} <b>应缴 ¥{{ item.amount }}</b></span>
          <NTag size="tiny" :type="item.confidence === 'high' ? 'success' : 'warning'" :bordered="false">
            {{ item.confidence === 'high' ? '高置信' : '请确认' }}
          </NTag>
          <span class="suggest-desc" :title="item.reason">{{ item.statement.date }} · {{ item.statement.description }}</span>
          <NButton size="small" type="primary" :loading="applyingKey === `${item.kind}-${item.statement.id}`" @click="applyLiability(item)">确认缴纳并核销</NButton>
        </div>
        <div v-for="item in paymentSuggestions" :key="item.statement.id" class="suggest-row">
          <span class="suggest-main">{{ item.month }} 工资 <b>实发 ¥{{ item.netSalary }}</b></span>
          <NTag size="tiny" :type="item.confidence === 'high' ? 'success' : 'warning'" :bordered="false">
            {{ item.confidence === 'high' ? '高置信' : '请确认' }}
          </NTag>
          <span class="suggest-desc" :title="item.reason">{{ item.statement.date }} · {{ item.statement.description }}</span>
          <NButton size="small" type="primary" :loading="applyingKey === `salary-${item.statement.id}`" @click="applySalary(item)">确认发放并核销</NButton>
        </div>
      </div>

      <div v-if="isDraft && rows.length" class="draft-meta-block">
        <div v-if="sheet?.carryForward?.hasPreviousSheet" class="carry-forward-banner">
          <div class="carry-forward-info">
            <i class="ri-history-line" />
            <span>
              已自动带出 <b>{{ sheet.carryForward.sourceMonth }}</b> 固定工资与社保
              <template v-if="sheet.carryForward.isIdenticalToPrevious">（与上月一致）</template>
              <template v-else-if="sheet.carryForward.changeReasons?.length">
                · {{ sheet.carryForward.changeReasons.join(' · ') }}
              </template>
            </span>
          </div>
          <NButton
            size="tiny"
            quaternary
            type="primary"
            class="sync-previous-btn"
            :loading="syncingPrevious"
            @click="syncFromPrevious"
          >
            <template #icon><i class="ri-refresh-line" /></template>
            重新同步上月
          </NButton>
        </div>
        <p class="auto-save-hint">
          <i class="ri-magic-line" /> 个税与五险一金已自动测算，修改后自动保存
          <span class="auto-save-sub">· 点击行末「微调」可修改补助与社保公积金明细</span>
          <span v-if="saveState === 'saving'" class="save-state">保存中…</span>
          <span v-else-if="saveState === 'saved'" class="save-state saved">已自动保存</span>
        </p>
      </div>

      <div v-if="rows.length" class="table-container">
        <table class="salary-table">
          <thead>
            <tr>
              <th style="min-width: 112px">姓名</th>
              <th v-if="showRdCol" style="min-width: 120px">研发项目工时</th>
              <template v-if="isDraft">
                <th class="col-amount" style="min-width: 96px">基本工资</th>
                <th class="col-amount" style="min-width: 96px">奖金</th>
                <th class="col-amount" style="min-width: 96px">考勤扣款</th>
              </template>
              <template v-else>
                <th class="col-amount" style="min-width: 120px">应发工资</th>
              </template>
              <th class="col-amount" style="min-width: 118px">个人五险一金</th>
              <th class="col-amount" style="min-width: 90px">个税</th>
              <th class="col-amount" style="min-width: 108px">实发到手</th>
              <th class="col-amount" style="min-width: 124px">企业总成本</th>
              <th style="min-width: 92px; text-align: center">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td class="emp-cell">
                <span class="emp-name">{{ row.employee_name }}</span>
                <NTag size="tiny" :bordered="false" :type="isRd(row) ? 'info' : 'default'">{{ deptOf(row) }}</NTag>
              </td>
              <td v-if="showRdCol">
                <NButton
                  v-if="isRd(row)"
                  size="tiny"
                  :type="hasRd(row.employee_id) ? 'primary' : 'default'"
                  :secondary="!hasRd(row.employee_id)"
                  :disabled="!isDraft"
                  @click="emit('open-rd', row)"
                >
                  <template #icon><i class="ri-flask-line" /></template>
                  {{ rdSummary(row.employee_id) }}
                </NButton>
                <span v-else class="rd-na" title="非研发部门无需分配研发项目工时">—</span>
              </td>
              <template v-if="isDraft">
                <td class="col-amount">
                  <div class="num-cell">{{ formatMoney(row.base_salary) }}</div>
                  <div v-if="Number(row.allowance || 0) > 0" class="cell-sub">+补助 {{ formatMoney(row.allowance) }}</div>
                </td>
                <td class="col-amount">
                  <NInputNumber v-model:value="row.bonus" :min="0" :precision="2" :show-button="false" size="small" class="amount-input" @update:value="recalc(row, true)" />
                </td>
                <td class="col-amount">
                  <NInputNumber v-model:value="row.deduction" :min="0" :precision="2" :show-button="false" size="small" class="amount-input" @update:value="recalc(row, true)" />
                </td>
              </template>
              <template v-else>
                <td class="col-amount">
                  <div class="num-cell">{{ formatMoney(rowGross(row)) }}</div>
                  <div v-if="rowAdjustSub(row)" class="cell-sub">{{ rowAdjustSub(row) }}</div>
                </td>
              </template>
              <td class="col-amount">
                <div class="num-cell">{{ formatMoney(row.personal_social_security) }}</div>
                <div v-if="Number(row.personal_social_security || 0) > 0" class="cell-sub">
                  社 {{ formatMoney(row.personal_social_insurance ?? 0) }} · 公 {{ formatMoney(row.personal_housing_fund ?? 0) }}
                </div>
              </td>
              <td class="col-amount">
                <button
                  type="button"
                  class="tax-trigger num-cell"
                  :title="isDraft ? '点击查看或试算个税明细' : '点击查看个税计算明细'"
                  @click="emit('open-tax', row)"
                >
                  {{ formatMoney(row.personal_tax) }}
                </button>
              </td>
              <td class="col-amount net-salary num-cell">{{ formatMoney(row.net_salary) }}</td>
              <td class="col-amount">
                <div class="total-cost num-cell">{{ formatMoney(row.total_cost) }}</div>
                <div class="cell-sub">含单位 {{ formatMoney(row.company_social_security) }}</div>
              </td>
              <td class="action-cell">
                <NSpace justify="center" align="center" :size="4" :wrap="false">
                  <NButton size="tiny" quaternary type="primary" @click="openDrawer(row)">
                    {{ isDraft ? '微调' : '明细' }}
                  </NButton>
                  <NButton size="tiny" quaternary :loading="exportingRecordId === row.id" title="导出该员工工资条" @click="exportPayslip(row)">
                    <template #icon><i class="ri-file-download-line" /></template>
                  </NButton>
                </NSpace>
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr class="totals-row">
              <td :colspan="showRdCol ? 2 : 1">合计 · {{ rows.length }} 人</td>
              <td v-if="isDraft" colspan="3" class="col-amount num-cell">应发 {{ formatMoney(totals.gross) }}</td>
              <td v-else class="col-amount num-cell">{{ formatMoney(totals.gross) }}</td>
              <td class="col-amount num-cell">{{ formatMoney(totals.personal) }}</td>
              <td class="col-amount num-cell">{{ formatMoney(totals.tax) }}</td>
              <td class="col-amount net-salary num-cell">{{ formatMoney(totals.net) }}</td>
              <td class="col-amount">
                <div class="total-cost num-cell">{{ formatMoney(totals.cost) }}</div>
                <div class="cell-sub">含单位 {{ formatMoney(totals.company) }}</div>
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
        <p v-if="payrollRates && !payrollRates.isCustomized" class="withholding-hint">
          <i class="ri-information-line" />
          当前基数为默认预估，税局申报社保后可在草稿「微调」抽屉直接回填实缴数，系统将自动重算个税与成本。
        </p>
      </div>

      <div v-else class="empty-wrap">
        <SoloEmpty title="当月暂无在职员工工资记录" description="请先在「员工花名册」中录入员工姓名、部门、基本工资与社保基数，系统将自动生成月度工资结算表。" />
        <NSpace>
          <NButton type="primary" @click="emit('add-employee')"><template #icon><i class="ri-user-add-line" /></template>立即添加首位员工</NButton>
          <NButton secondary @click="emit('goto-roster')">查看员工花名册</NButton>
        </NSpace>
      </div>

      <SalaryDetailDrawer
        v-model:show="showDrawer"
        :row="drawerRow"
        :department="drawerRow ? deptOf(drawerRow) : ''"
        :is-rd-dept="drawerRow ? isRd(drawerRow) : false"
        :rd-summary-text="drawerRow ? rdSummary(drawerRow.employee_id) : ''"
        :selected-month="selectedMonth"
        :is-draft="isDraft"
        :exporting="Boolean(drawerRow && exportingRecordId === drawerRow.id)"
        @change="(triggerTax) => drawerRow && recalc(drawerRow, triggerTax, true)"
        @open-tax="(row) => emit('open-tax', row)"
        @open-rd="(row) => emit('open-rd', row)"
        @export-payslip="exportPayslip"
      />

      <NModal
        v-model:show="showUnaccrueModal"
        preset="card"
        :title="sheet?.status === 'pending_review' ? '撤回待复核工资表' : (hasActiveDownstream ? (viewStore.isBoss ? '撤销发薪并退回草稿重算' : '连带冲销本月工资及付款凭证') : (viewStore.isBoss ? '撤销本月工资表确认' : '冲销工资计提并退回草稿'))"
        style="width: min(480px, 92vw); border-radius: var(--solo-radius-lg)"
      >
        <p class="unaccrue-desc">
          <template v-if="sheet?.status === 'pending_review'">
            该月计提凭证尚未复核过账，撤回后将删除待审凭证，{{ selectedMonth }} 工资表将退回草稿，可修改后重新提交。
          </template>
          <template v-else>
            已确认的工资表不能直接修改。系统将按会计规范生成红字冲销凭证抵消原入账记录，{{ selectedMonth }} 工资表随即退回草稿，修改后可重新确认入账。
          </template>
        </p>
        <div v-if="hasActiveDownstream" class="unaccrue-cascade-warn">
          <i class="ri-alert-line" />
          <div class="cascade-warn-body">
            <div>
              本月已登记 <b>{{ activeDownstreamLabels.join('、') }}</b>，确认后将<b>一并红字冲销</b>：
              <b>工资计提、{{ activeDownstreamLabels.join('、') }}</b>。
            </div>
            <div>若已关联银行流水对账，系统将同步解除关联（重算发薪后可重新核销）；如对应月份已关账，请先在结账页反结账。</div>
          </div>
        </div>
        <div class="unaccrue-field">
          <label class="unaccrue-label">撤销原因（留存审计记录）</label>
          <NInput
            v-model:value="unaccrueReason"
            placeholder="例如：漏算奖金、社保基数调整"
            maxlength="200"
          />
        </div>
        <template #footer>
          <NSpace justify="end">
            <NButton :disabled="unaccruing" @click="showUnaccrueModal = false">取消</NButton>
            <NButton
              type="warning"
              class="confirm-unaccrue-btn"
              :loading="unaccruing"
              @click="confirmUnaccrue"
            >
              {{ sheet?.status === 'pending_review' ? '确认撤回' : (hasActiveDownstream ? '确认全部冲销并退回草稿' : '确认冲销并退回草稿') }}
            </NButton>
          </NSpace>
        </template>
      </NModal>
    </div>
  </NSpin>
</template>

<style scoped>
.sheet-tab { display: flex; flex-direction: column; gap: 12px; }

.overview-panel {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  padding: 14px 18px;
}
.overview-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.overview-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.control-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.month-picker { width: 128px; }
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: var(--solo-radius-pill);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.pill-paid { background: var(--solo-income-bg); color: var(--solo-income); }
.pill-accrued { background: var(--solo-warning-bg); color: var(--solo-warning); }
.pill-pending { background: var(--solo-bg-blue-soft); color: var(--solo-primary); }
.pill-draft { background: var(--solo-bg-soft); color: var(--solo-text-secondary); }
.headcount-note {
  font-size: 12px;
  color: var(--solo-text-muted);
  white-space: nowrap;
}

.overview-body {
  display: grid;
  grid-template-columns: minmax(210px, 1.05fr) minmax(0, 2.2fr);
  gap: 18px;
  align-items: center;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--solo-border-light);
}
.overview-primary {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.overview-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
.overview-hero {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 25px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.overview-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}

.overview-flows {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  padding-left: 18px;
  border-left: 1px solid var(--solo-border-light);
}
.flow-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.flow-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.flow-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--solo-radius-pill);
  flex-shrink: 0;
}
.dot-net { background: var(--solo-primary); }
.dot-benefit { background: color-mix(in oklch, var(--solo-primary) 40%, var(--solo-bg-soft)); }
.dot-tax { background: var(--solo-warning); }
.flow-value {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.flow-sub {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overview-bar {
  display: flex;
  height: 4px;
  border-radius: var(--solo-radius-pill);
  overflow: hidden;
  background: var(--solo-bg-soft);
  margin-top: 12px;
  gap: 1px;
}
.bar-seg { height: 100%; transition: width 0.25s ease; }
.bar-net { background: var(--solo-primary); }
.bar-benefit { background: color-mix(in oklch, var(--solo-primary) 40%, var(--solo-bg-soft)); }
.bar-tax { background: var(--solo-warning); }

.workflow-panel {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  overflow: hidden;
}
.workflow-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  min-height: 48px;
  flex-wrap: wrap;
}
.steps-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.step-inline-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.step-inline {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 8px;
  border-radius: var(--solo-radius);
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
.step-inline-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
  white-space: nowrap;
}
.step-inline-desc {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  white-space: nowrap;
}
.step-inline.is-done .step-inline-title { color: var(--solo-income); }
.step-inline.is-current { background: var(--solo-bg-blue-soft); }
.step-inline.is-current .step-inline-title { color: var(--solo-primary); }
.step-inline.is-review { background: var(--solo-warning-bg); }
.step-inline.is-review .step-inline-title { color: var(--solo-warning); }
.step-inline.is-wait { opacity: 0.55; }
.step-arrow { color: var(--solo-text-muted); font-size: 14px; opacity: 0.65; }

.workflow-cta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-left: auto;
}
.cta-note { font-size: 12px; color: var(--solo-text-muted); }
.cta-warn {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-warning);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.settled-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: var(--solo-radius-pill);
  background: var(--solo-income-bg);
  color: var(--solo-income);
  font-size: 12px;
  font-weight: 600;
}

.workflow-todos {
  border-top: 1px solid var(--solo-border-light);
  background: color-mix(in oklch, var(--solo-bg-card) 60%, var(--solo-bg-soft) 40%);
  padding: 10px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.todos-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.todos-toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.pay-date-wrap { display: inline-flex; align-items: center; gap: 6px; }
.pay-date-picker { width: 140px; }
.guide-inline { display: inline-flex; align-items: center; }

.todo-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.todo-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  min-width: 0;
}
.todo-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.todo-name { font-size: 11.5px; color: var(--solo-text-muted); white-space: nowrap; }
.todo-amount {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.todo-done-text { font-size: 11.5px; color: var(--solo-text-muted); white-space: nowrap; }

.suggest-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 14px;
  border: 1px solid var(--solo-border-light); border-radius: var(--solo-radius-lg);
  background: color-mix(in oklch, var(--solo-bg-card) 94%, var(--solo-primary) 6%);
}
.suggest-title { font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.suggest-list {
  margin-top: -6px; border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg); background: var(--solo-bg-card);
}
.suggest-row {
  display: grid; grid-template-columns: minmax(220px, 1.2fr) auto minmax(180px, 1.6fr) auto;
  align-items: center; gap: 12px; padding: 8px 14px; font-size: 13px;
  border-top: 1px solid var(--solo-border-light);
}
.suggest-row:first-child { border-top: none; }
.suggest-main b { font-variant-numeric: tabular-nums; margin-left: 6px; }
.suggest-desc {
  font-size: 12px; color: var(--solo-text-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.draft-meta-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.carry-forward-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 12.5px;
  line-height: 1.5;
}
.carry-forward-info {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.carry-forward-info i {
  font-size: 14px;
  flex-shrink: 0;
}
.auto-save-hint {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.auto-save-sub { color: var(--solo-text-muted); opacity: 0.85; }
.save-state { margin-left: auto; color: var(--solo-text-muted); }
.save-state.saved { color: var(--solo-income); }

.table-container {
  overflow-x: auto; border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg); background: var(--solo-bg-card);
}
.salary-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.salary-table th {
  text-align: left; padding: 10px 12px; font-weight: 600; white-space: nowrap;
  color: var(--solo-text-secondary); background: var(--solo-bg-soft);
  border-bottom: 1px solid var(--solo-border-light);
}
.salary-table td { padding: 9px 12px; border-bottom: 1px solid var(--solo-border-light); vertical-align: middle; }
.salary-table tbody tr:hover { background: var(--solo-bg-soft); }
.col-amount { text-align: right !important; font-variant-numeric: tabular-nums; }
.num-cell {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
}
.emp-cell { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.emp-name { font-weight: 600; color: var(--solo-text-primary); }
.cell-sub {
  font-size: 11px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
  white-space: nowrap;
}
.tax-trigger {
  border: none;
  background: transparent;
  padding: 0;
  font: inherit;
  color: var(--solo-text-primary);
  border-bottom: 1px dashed var(--solo-border);
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.tax-trigger:hover {
  color: var(--solo-primary);
  border-bottom-color: var(--solo-primary);
}
.net-salary { font-weight: 700; color: var(--solo-text-primary); }
.total-cost { font-weight: 700; color: var(--solo-primary); }
.action-cell { text-align: center; white-space: nowrap; }
.rd-na { opacity: 0.35; }
.amount-input { width: 100%; }
.amount-input :deep(.n-input__input-el) {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}
.totals-row td {
  border-top: 1px solid var(--solo-border-light);
  border-bottom: none;
  font-weight: 600;
  background: var(--solo-bg-soft);
}
.withholding-hint {
  display: flex; align-items: flex-start; flex-wrap: wrap; gap: 4px;
  margin: 0; padding: 10px 14px; font-size: 12px; line-height: 1.6;
  color: var(--solo-text-muted);
  border-top: 1px solid var(--solo-border-light);
}

.empty-wrap { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 36px 0; }

.unaccrue-desc {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
}
.unaccrue-cascade-warn {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  font-size: 12px;
  line-height: 1.55;
}
.unaccrue-cascade-warn i {
  font-size: 15px;
  margin-top: 1px;
  flex-shrink: 0;
}
.cascade-warn-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.unaccrue-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.unaccrue-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}

@media (max-width: 1024px) {
  .overview-body { grid-template-columns: 1fr; }
  .overview-flows {
    padding-left: 0;
    border-left: none;
    padding-top: 12px;
    border-top: 1px solid var(--solo-border-light);
  }
  .todo-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 640px) {
  .overview-flows { grid-template-columns: 1fr; }
  .todo-grid { grid-template-columns: 1fr; }
  .suggest-row { grid-template-columns: 1fr; }
  .step-inline-desc { display: none; }
}
</style>
