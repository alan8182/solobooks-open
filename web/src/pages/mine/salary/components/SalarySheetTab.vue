<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NSpace, NTag, NInputNumber, NDatePicker, NSpin, useMessage } from 'naive-ui'
import dayjs from 'dayjs'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'
import { useEntryStore } from '@/stores/entry'
import {
  updateSalaryRecords, accrueSalarySheet, paySalarySheet,
  updateRdSalaryAllocations, applySalaryPayment, applyPayrollLiability,
  exportSalarySheet, exportSalaryPayslip,
  paySalarySocial, paySalaryHousingFund, paySalaryTax,
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
  rdAllocations: RdSalaryAllocation[]
  paymentSuggestions: SalaryPaymentSuggestion[]
  liabilitySuggestions: PayrollLiabilitySuggestion[]
  payrollRates: PayrollRates | null
  housingFund: { personal: number; company: number }
}>()

const emit = defineEmits<{
  (e: 'sheet-updated', data: SalarySheet): void
  (e: 'reload-suggestions'): void
  (e: 'open-rd', row: SalaryRecord): void
  (e: 'open-tax', row: SalaryRecord): void
  (e: 'goto-roster'): void
  (e: 'add-employee'): void
  (e: 'totals', totals: { count: number; net: number; cost: number; withheld: number }): void
}>()

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const busy = ref(false)
const exporting = ref(false)
const exportingRecordId = ref('')
const applyingKey = ref('')
const showSuggestions = ref(false)
const payDate = ref(dayjs().format('YYYY-MM-DD'))

// 本地可编辑副本：父级数据来自接口，编辑只动副本，保存/计提后由接口结果回写
const rows = ref<SalaryRecord[]>([])
watch(() => props.records, (list) => {
  rows.value = (list || []).map((r) => ({ ...r }))
}, { immediate: true })

watch(() => props.selectedMonth, () => {
  payDate.value = dayjs().format('YYYY-MM-DD')
})

const isDraft = computed(() => props.sheet?.status === 'draft')

function recalc(row: SalaryRecord) {
  const gross = Number(row.base_salary || 0) + Number(row.bonus || 0)
    + Number(row.allowance || 0) - Number(row.deduction || 0)
  row.net_salary = Math.round((gross - Number(row.personal_social_security || 0) - Number(row.personal_tax || 0)) * 100) / 100
  row.total_cost = Math.round((gross + Number(row.company_social_security || 0)) * 100) / 100
}

const totals = computed(() => {
  let gross = 0, net = 0, cost = 0, personal = 0, tax = 0, company = 0
  for (const r of rows.value) {
    gross += Number(r.base_salary || 0) + Number(r.bonus || 0) + Number(r.allowance || 0) - Number(r.deduction || 0)
    net += Number(r.net_salary || 0)
    cost += Number(r.total_cost || 0)
    personal += Number(r.personal_social_security || 0)
    tax += Number(r.personal_tax || 0)
    company += Number(r.company_social_security || 0)
  }
  const r2 = (n: number) => Math.round(n * 100) / 100
  return { gross: r2(gross), net: r2(net), cost: r2(cost), personal: r2(personal), tax: r2(tax), company: r2(company) }
})

watch(totals, (t) => emit('totals', {
  count: rows.value.length, net: t.net, cost: t.cost, withheld: Math.round((t.personal + t.tax) * 100) / 100,
}), { immediate: true })

const withholding = computed(() => {
  const personalHf = Math.max(props.housingFund.personal, 0)
  const companyHf = Math.max(props.housingFund.company, 0)
  return {
    personalHf,
    personalSs: Math.max(Math.round((totals.value.personal - personalHf) * 100) / 100, 0),
    companyHf,
    companySs: Math.max(Math.round((totals.value.company - companyHf) * 100) / 100, 0),
  }
})

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
  return [
    { title: '① 成本计提', desc: label(step1, '已列入当月成本', '核对后计提记账'), state: step1 },
    { title: '② 工资发放', desc: label(step2, '已划扣记账', '选择发薪日并记账'), state: step2 },
    { title: '③ 社保 / 公积金 / 个税', desc: label(step3, '均已缴纳记账', '逐项缴纳记账'), state: step3 },
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

const saveRecords = () => run(async () => {
  emit('sheet-updated', await updateSalaryRecords(props.selectedMonth, rows.value) as any)
  message.success('保存工资条成功')
}, '保存工资条失败')

const accrue = () => run(async () => {
  await updateSalaryRecords(props.selectedMonth, rows.value)
  await updateRdSalaryAllocations(props.selectedMonth, props.rdAllocations)
  const data: any = await accrueSalarySheet(props.selectedMonth)
  await afterPosting(data)
  message.success(data.status === 'accrued'
    ? '工资计提凭证生成成功，已列入当月研发/管理成本'
    : '计提凭证已创建，待复核通过后计入成本（可在审核中心复核）')
}, '计提失败')

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

defineExpose({ exportSheet, exporting })
</script>

<template>
  <NSpin :show="busy">
    <div class="sheet-tab">
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

      <!-- 三阶段进度 -->
      <div v-if="sheet && rows.length" class="steps">
        <div v-for="step in steps" :key="step.title" class="step" :class="`is-${step.state}`">
          <span class="step-title">{{ step.title }}</span>
          <span class="step-desc">{{ step.desc }}</span>
        </div>
      </div>

      <div v-if="rows.length" class="table-container">
        <table class="salary-table">
          <thead>
            <tr class="group-row">
              <th colspan="3">员工</th>
              <th colspan="4" class="grp grp-gross">应发项目</th>
              <th colspan="2" class="grp grp-withhold">代扣代缴</th>
              <th colspan="3" class="grp grp-net">实发与成本</th>
              <th />
            </tr>
            <tr>
              <th style="min-width: 84px">姓名</th>
              <th style="min-width: 84px">部门</th>
              <th style="min-width: 128px">研发项目工时</th>
              <th class="col-amount" style="min-width: 96px">基本工资</th>
              <th class="col-amount" style="min-width: 96px">奖金</th>
              <th class="col-amount" style="min-width: 96px">补助/津贴</th>
              <th class="col-amount" style="min-width: 96px">考勤扣款</th>
              <th class="col-amount" style="min-width: 108px">个人社保公积金</th>
              <th class="col-amount" style="min-width: 120px">个税代扣</th>
              <th class="col-amount" style="min-width: 100px">实发工资</th>
              <th class="col-amount" style="min-width: 108px">单位社保公积金</th>
              <th class="col-amount" style="min-width: 100px">企业总成本</th>
              <th style="min-width: 64px; text-align: center">工资条</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td class="emp-name">{{ row.employee_name }}</td>
              <td><NTag size="small" :bordered="false" :type="isRd(row) ? 'info' : 'default'">{{ deptOf(row) }}</NTag></td>
              <td>
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
              <td class="col-amount num-cell">{{ formatMoney(row.base_salary) }}</td>
              <td v-for="key in (['bonus', 'allowance', 'deduction', 'personal_social_security'] as const)" :key="key" class="col-amount">
                <NInputNumber v-model:value="row[key]" :min="0" :precision="2" :show-button="false" size="small" class="amount-input" :disabled="!isDraft" @update:value="recalc(row)" />
              </td>
              <td class="col-amount">
                <div class="tax-cell">
                  <NInputNumber v-model:value="row.personal_tax" :min="0" :precision="2" :show-button="false" size="small" class="amount-input" :disabled="!isDraft" @update:value="recalc(row)" />
                  <NButton v-if="isDraft" size="tiny" quaternary class="tax-calc-btn" title="按累计预扣法试算本期应扣个税" @click="emit('open-tax', row)">
                    <template #icon><i class="ri-calculator-line" /></template>
                  </NButton>
                </div>
              </td>
              <td class="col-amount net-salary">{{ formatMoney(row.net_salary) }}</td>
              <td class="col-amount">
                <NInputNumber v-model:value="row.company_social_security" :min="0" :precision="2" :show-button="false" size="small" class="amount-input" :disabled="!isDraft" @update:value="recalc(row)" />
              </td>
              <td class="col-amount total-cost">{{ formatMoney(row.total_cost) }}</td>
              <td style="text-align: center">
                <NButton size="tiny" quaternary :loading="exportingRecordId === row.id" title="导出该员工工资条" @click="exportPayslip(row)">
                  <template #icon><i class="ri-file-download-line" /></template>
                </NButton>
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr class="totals-row">
              <td colspan="3">合计 · {{ rows.length }} 人</td>
              <td colspan="4" class="col-amount">应发 {{ formatMoney(totals.gross) }}</td>
              <td class="col-amount">{{ formatMoney(totals.personal) }}</td>
              <td class="col-amount">{{ formatMoney(totals.tax) }}</td>
              <td class="col-amount net-salary">{{ formatMoney(totals.net) }}</td>
              <td class="col-amount">{{ formatMoney(totals.company) }}</td>
              <td class="col-amount total-cost">{{ formatMoney(totals.cost) }}</td>
              <td />
            </tr>
          </tfoot>
        </table>
        <p v-if="totals.personal > 0" class="withholding-hint">
          <i class="ri-information-line" />
          个人代扣 {{ formatMoney(totals.personal) }} = 社保 {{ formatMoney(withholding.personalSs) }} + 公积金 {{ formatMoney(withholding.personalHf) }}；
          单位 {{ formatMoney(totals.company) }} = 社保 {{ formatMoney(withholding.companySs) }} + 公积金 {{ formatMoney(withholding.companyHf) }}。
          <template v-if="payrollRates && !payrollRates.isCustomized">当前按默认杭州口径，比例不符请点右上「社保公积金比例」。</template>
        </p>
      </div>

      <div v-else class="empty-wrap">
        <SoloEmpty title="当月暂无在职员工工资记录" description="请先在「员工花名册」中录入员工姓名、部门、基本工资与社保基数，系统将自动生成月度工资结算表。" />
        <NSpace>
          <NButton type="primary" @click="emit('add-employee')"><template #icon><i class="ri-user-add-line" /></template>立即添加首位员工</NButton>
          <NButton secondary @click="emit('goto-roster')">查看员工花名册</NButton>
        </NSpace>
      </div>

      <!-- 底部结算动作栏 -->
      <div v-if="sheet && rows.length" class="sheet-footer">
        <div class="footer-hint">
          <template v-if="isDraft"><i class="ri-information-line" /> 确认工资与社保明细后点击「计提记账」，系统将自动生成应付职工薪酬凭证。</template>
          <template v-else-if="sheet.status === 'pending_review'"><i class="ri-shield-flash-line" /> 计提凭证已提交审核中心，复核通过后即可发放工资并缴纳社保、个税。</template>
          <template v-else-if="sheet.paymentStatus === 'pending_review'"><i class="ri-shield-flash-line" /> 工资发放凭证已提交审核中心，复核通过后才算发放完成。</template>
          <span v-else-if="allLiabilitiesSettled && sheet.paymentStatus === 'posted'" class="paid-desc"><i class="ri-checkbox-circle-fill" /> 本月工资、社保、公积金及个税均已完成记账。</span>
          <template v-else><i class="ri-information-line" /> 请依次完成实发工资发放、社保、公积金及个税缴纳记账，清理挂账余额。</template>
        </div>

        <NSpace size="small" align="center">
          <template v-if="isDraft">
            <NButton secondary @click="saveRecords"><template #icon><i class="ri-save-line" /></template>保存草稿修改</NButton>
            <NButton type="primary" @click="accrue"><template #icon><i class="ri-check-double-line" /></template>计提记账（列入当月成本）</NButton>
          </template>

          <template v-else>
            <NButton v-if="sheet.accrual_voucher_id" secondary @click="viewVoucher(sheet.accrual_voucher_id!)">查看计提凭证</NButton>

            <span v-if="sheet.status === 'accrued' || sheet.status === 'paid'" class="pay-date-wrap">
              <span class="control-label">实际发薪/缴纳日</span>
              <NDatePicker v-model:formatted-value="payDate" value-format="yyyy-MM-dd" type="date" :clearable="false" size="small" class="pay-date-picker" />
            </span>

            <NButton v-if="sheet.status === 'accrued' && !sheet.payment_voucher_id" type="primary" @click="pay">发放工资记账（生成划扣凭证）</NButton>
            <NButton v-else-if="sheet.paymentStatus === 'posted' && sheet.payment_voucher_id" secondary @click="viewVoucher(sheet.payment_voucher_id!)">查看发放凭证</NButton>
            <NButton v-else-if="sheet.paymentStatus === 'pending_review'" secondary disabled>发放凭证待复核</NButton>

            <!-- 待复核期间不能发放/缴纳：后端会因状态不符拒绝 -->
            <template v-if="sheet.status !== 'pending_review'">
              <NButton v-if="sheet.ssPaymentStatus === 'unpaid'" secondary type="primary" @click="payLiability('social')">缴纳社保{{ sheet.socialSecurityDue ? `（¥${sheet.socialSecurityDue}）` : '' }}</NButton>
              <NButton v-else-if="sheet.ssPaymentStatus === 'pending_review'" secondary disabled>社保凭证待复核</NButton>
              <NButton v-else-if="sheet.ss_paid_voucher_id" secondary @click="viewVoucher(sheet.ss_paid_voucher_id!)">查看社保凭证</NButton>

              <NButton v-if="sheet.hfPaymentStatus === 'unpaid'" secondary type="primary" @click="payLiability('housing')">缴纳公积金{{ sheet.housingFundDue ? `（¥${sheet.housingFundDue}）` : '' }}</NButton>
              <NButton v-else-if="sheet.hfPaymentStatus === 'pending_review'" secondary disabled>公积金凭证待复核</NButton>
              <NButton v-else-if="sheet.hf_paid_voucher_id" secondary @click="viewVoucher(sheet.hf_paid_voucher_id!)">查看公积金凭证</NButton>

              <NButton v-if="sheet.taxPaymentStatus === 'unpaid'" secondary type="primary" @click="payLiability('tax')">缴纳个税{{ sheet.taxDue ? `（¥${sheet.taxDue}）` : '' }}</NButton>
              <NButton v-else-if="sheet.taxPaymentStatus === 'pending_review'" secondary disabled>个税凭证待复核</NButton>
              <NButton v-else-if="sheet.tax_paid_voucher_id" secondary @click="viewVoucher(sheet.tax_paid_voucher_id!)">查看个税凭证</NButton>
            </template>
          </template>
        </NSpace>
      </div>
    </div>
  </NSpin>
</template>

<style scoped>
.sheet-tab { display: flex; flex-direction: column; }

.suggest-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 14px; margin-bottom: 12px;
  border: 1px solid var(--solo-border-light); border-radius: var(--solo-radius-lg);
  background: color-mix(in oklch, var(--solo-bg-card) 94%, var(--solo-primary) 6%);
}
.suggest-title { font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.suggest-list {
  margin: -4px 0 12px; border: 1px solid var(--solo-border-light);
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

.steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.step {
  display: flex; flex-direction: column; gap: 2px; padding: 8px 14px; min-width: 0;
  border: 1px solid var(--solo-border-light); border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}
.step-title { font-size: 13px; font-weight: 600; color: var(--solo-text-secondary); }
.step-desc { font-size: 12px; color: var(--solo-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step.is-done { border-color: color-mix(in oklch, var(--solo-border-light) 50%, var(--solo-income) 50%); }
.step.is-done .step-title { color: var(--solo-income); }
.step.is-current { border-color: var(--solo-primary); background: color-mix(in oklch, var(--solo-bg-card) 94%, var(--solo-primary) 6%); }
.step.is-current .step-title { color: var(--solo-primary); }
.step.is-review { border-color: var(--solo-warning); }
.step.is-review .step-title { color: var(--solo-warning); }
.step.is-wait { opacity: 0.6; }

.table-container {
  overflow-x: auto; border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg); background: var(--solo-bg-card);
}
.salary-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.salary-table th {
  text-align: left; padding: 9px 12px; font-weight: 600; white-space: nowrap;
  color: var(--solo-text-secondary); background: var(--solo-bg-hover, #f6f7f9);
  border-bottom: 1px solid var(--solo-border-light);
}
.group-row th { padding: 6px 12px; font-size: 12px; border-bottom: 1px dashed var(--solo-border-light); }
.group-row .grp { text-align: center; }
.grp-gross { color: var(--solo-primary); }
.grp-withhold { color: var(--solo-warning); }
.grp-net { color: var(--solo-income); }
.salary-table td { padding: 7px 12px; border-bottom: 1px solid var(--solo-border-light); vertical-align: middle; }
.salary-table tbody tr:hover { background: var(--solo-bg-hover, #f6f7f9); }
.col-amount { text-align: right !important; font-variant-numeric: tabular-nums; }
.num-cell { font-variant-numeric: tabular-nums; }
.emp-name { font-weight: 600; }
.net-salary { font-weight: 700; color: var(--solo-income); }
.total-cost { font-weight: 700; color: var(--solo-primary); }
.rd-na { opacity: 0.35; }
.amount-input { width: 100%; }
.amount-input :deep(.n-input__input-el) { text-align: right !important; font-variant-numeric: tabular-nums; }
.tax-cell { display: flex; align-items: center; gap: 2px; }
.tax-calc-btn { flex: none; opacity: 0.55; }
.tax-calc-btn:hover { opacity: 1; }
.totals-row td { border-top: 1px solid var(--solo-border-light); font-weight: 600; background: var(--solo-bg-hover, #f6f7f9); }
.withholding-hint {
  display: flex; align-items: flex-start; flex-wrap: wrap; gap: 4px;
  margin: 0; padding: 10px 14px; font-size: 12.5px; line-height: 1.7;
  color: var(--solo-text-muted);
}

.empty-wrap { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 36px 0; }

.sheet-footer {
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
  margin-top: 12px; padding: 12px 16px;
  border: 1px solid var(--solo-border-light); border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}
.footer-hint { font-size: 12px; color: var(--solo-text-secondary); display: inline-flex; align-items: center; gap: 4px; }
.paid-desc { font-size: 13px; color: var(--solo-income); font-weight: 600; display: inline-flex; align-items: center; gap: 4px; }
.pay-date-wrap { display: inline-flex; align-items: center; gap: 6px; }
.control-label { font-size: 13px; font-weight: 500; color: var(--solo-text-secondary); white-space: nowrap; }
.pay-date-picker { width: 140px; }

@media (max-width: 900px) {
  .steps { grid-template-columns: 1fr; }
  .suggest-row { grid-template-columns: 1fr; }
}
</style>
