<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { accountingErrorText } from '@/utils/errors'
import { NButton, NCheckbox, NInput, NModal, NSelect, NTag, useMessage } from 'naive-ui'
import { approveAgentAction, rejectAgentAction, type AgentActionItem } from '@/api/agent'
import { getBackfillTarget } from '@/api/accounting'
import { requestStepUp, sendPhoneCode } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { usePayeeEmployees } from '@/composables/usePayeeEmployees'
import { guideStartDate } from '@/composables/startDateGuide'
import {
  INCOME_PAYER_OPTIONS,
  PAYER_OPTIONS,
  joinPayer,
  payerCardLabel,
  splitPayer,
  type PayerKind,
} from '@/utils/fundsChannel'

interface VoucherRow {
  direction?: string
  amount?: number
  title?: string
  category?: string
  occurDate?: string
  payer?: string
  merchant?: string
  note?: string
}

const props = defineProps<{
  action: AgentActionItem
}>()

const emit = defineEmits<{
  (e: 'resolved'): void
}>()

const message = useMessage()
const router = useRouter()
const authStore = useAuthStore()
const busy = reactive({ approving: false, rejecting: false })
const local = reactive({
  status: props.action.status,
  result: props.action.result,
})

const payload = computed(() => props.action.payload || {})
const isCompanySetup = computed(() => props.action.actionType === 'propose_company_setup')
const isOpeningBalance = computed(() => props.action.actionType === 'propose_opening_balance')
const isBatch = computed(
  () =>
    !isCompanySetup.value &&
    !isOpeningBalance.value &&
    Array.isArray(payload.value.items) &&
    payload.value.items.length > 0,
)
const isMatch = computed(() => props.action.actionType === 'propose_match')
const isAccrual = computed(() => props.action.actionType === 'propose_accrual')

// ── 主体建账信息确认卡 ──────────────────────────────────────────
const companyForm = reactive({
  onboardingType: String(payload.value.onboardingType || 'new'),
  name: String(payload.value.name || ''),
  taxId: String(payload.value.taxId || ''),
  type: String(payload.value.type || '有限责任公司'),
  taxpayerType: String(payload.value.taxpayerType || '小规模纳税人'),
  startDate: String(payload.value.startDate || ''),
  industry: String(payload.value.industry || ''),
  vatFilingCycle: String(payload.value.vatFilingCycle || 'quarter'),
})

const ONBOARDING_OPTIONS = [
  { label: '新成立公司 · 从零开始记账', value: 'new' },
  { label: '已记账公司 · 从其他代账/软件转入', value: 'migration' },
]
const ENTITY_TYPE_OPTIONS = [
  { label: '有限责任公司', value: '有限责任公司' },
  { label: '个人独资企业', value: '个人独资企业' },
  { label: '个体工商户', value: '个体工商户' },
]
const TAXPAYER_OPTIONS = [
  { label: '小规模纳税人', value: '小规模纳税人' },
  { label: '一般纳税人', value: '一般纳税人' },
]

// ── 期初余额导入确认卡 ──────────────────────────────────────────
interface OpeningCardItem {
  account_code: string
  account_name: string
  debit_balance: string | number
  credit_balance: string | number
  partner_name?: string
}
const openingState = reactive({
  fiscalYear: Number(payload.value.fiscalYear || new Date().getFullYear()),
  startPeriod: String(payload.value.startPeriod || ''),
  items: (Array.isArray(payload.value.items) ? payload.value.items : []).map((it: any) => ({
    account_code: String(it.account_code || ''),
    account_name: String(it.account_name || ''),
    debit_balance: String(it.debit_balance ?? '0.00'),
    credit_balance: String(it.credit_balance ?? '0.00'),
    partner_name: String(it.partner_name || ''),
  })) as OpeningCardItem[],
  ytdItems: (Array.isArray(payload.value.ytdItems) ? payload.value.ytdItems : []).map((it: any) => ({
    account_code: String(it.account_code || ''),
    account_name: String(it.account_name || ''),
    debit_balance: String(it.debit_balance ?? '0.00'),
    credit_balance: String(it.credit_balance ?? '0.00'),
  })) as OpeningCardItem[],
  taxMemory: (payload.value.taxMemory || {}) as Record<string, unknown>,
})

const openingTotals = computed(() => {
  const debit =
    Math.round(
      openingState.items.reduce((s, it) => s + (parseFloat(String(it.debit_balance)) || 0), 0) * 100,
    ) / 100
  const credit =
    Math.round(
      openingState.items.reduce((s, it) => s + (parseFloat(String(it.credit_balance)) || 0), 0) * 100,
    ) / 100
  const diff = Math.round((debit - credit) * 100) / 100
  return {
    totalDebit: debit,
    totalCredit: credit,
    diff,
    absDiff: Math.abs(diff),
    balanced: debit > 0 && Math.abs(diff) <= 0.01,
  }
})

function plugOpeningDifference() {
  const diff = openingTotals.value.diff
  if (Math.abs(diff) <= 0.01) return
  const existing = openingState.items.find((i) => i.account_code === '3104006')
  if (existing) {
    const net =
      (parseFloat(String(existing.credit_balance)) || 0) -
      (parseFloat(String(existing.debit_balance)) || 0) +
      diff
    if (net >= 0) {
      existing.credit_balance = net.toFixed(2)
      existing.debit_balance = '0.00'
    } else {
      existing.debit_balance = Math.abs(net).toFixed(2)
      existing.credit_balance = '0.00'
    }
  } else {
    openingState.items.push({
      account_code: '3104006',
      account_name: '未分配利润',
      debit_balance: diff < 0 ? Math.abs(diff).toFixed(2) : '0.00',
      credit_balance: diff > 0 ? diff.toFixed(2) : '0.00',
      partner_name: '',
    })
  }
  message.success('已将借贷差额计入「3104006 未分配利润」自动配平')
}

function openInOpeningWorkbench() {
  try {
    sessionStorage.setItem(
      'solobooks:ai_opening_draft',
      JSON.stringify({
        fiscalYear: openingState.fiscalYear,
        startPeriod: openingState.startPeriod,
        items: openingState.items,
        ytdItems: openingState.ytdItems,
        taxMemory: openingState.taxMemory,
      }),
    )
  } catch {
    /* ignore */
  }
  router.push('/mine/opening')
}

const stepUpModal = reactive({
  show: false,
  phone: '',
  code: '',
  bizId: '',
  sending: false,
})

function openOpeningStepUp() {
  if (!openingTotals.value.balanced) {
    message.warning('当前借贷尚未平衡，请先点击「差额计入未分配利润配平」或「去期初工作台细调」')
    return
  }
  stepUpModal.phone = authStore.user?.phone || ''
  stepUpModal.code = ''
  stepUpModal.show = true
}

async function sendStepUpSms() {
  if (!stepUpModal.phone.trim()) {
    message.warning('请输入当前绑定手机号')
    return
  }
  stepUpModal.sending = true
  try {
    const res = await sendPhoneCode({ phone: stepUpModal.phone.trim() })
    stepUpModal.bizId = res.biz_id || ''
    message.success('验证码已发送')
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    stepUpModal.sending = false
  }
}

async function confirmOpeningStepUp() {
  if (!stepUpModal.phone.trim() || !stepUpModal.code.trim()) {
    message.warning('请填写手机号与短信验证码')
    return
  }
  busy.approving = true
  try {
    const step = await requestStepUp({
      action: 'adjust_opening_balance',
      phone: stepUpModal.phone.trim(),
      code: stepUpModal.code.trim(),
      bizId: stepUpModal.bizId || undefined,
    })
    stepUpModal.show = false
    await doApprove({
      edits: {
        '0': {
          fiscalYear: openingState.fiscalYear,
          startPeriod: openingState.startPeriod,
          items: openingState.items,
          ytdItems: openingState.ytdItems,
          taxMemory: openingState.taxMemory,
        },
      },
      stepUpToken: step.stepUpToken,
    })
    message.success('期初余额已正式入账！')
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    busy.approving = false
  }
}

const matchData = computed(() => ({
  statement: (payload.value.statement || {}) as Record<string, unknown>,
  entries: (payload.value.entries || []) as Record<string, unknown>[],
}))
const accrualData = computed(() => ({
  kind: String(payload.value.kind || ''),
  month: String(payload.value.month || ''),
  preview: (payload.value.preview || {}) as Record<string, unknown>,
}))
const isAssetClosure = computed(() => isAccrual.value && accrualData.value.kind === 'asset_closure')
const accrualKindText = computed(() => {
  if (accrualData.value.kind === 'depreciation') return '折旧计提'
  if (accrualData.value.kind === 'asset_closure') return '资产处理'
  return '工资计提'
})
interface AssetClosureCard {
  name: string
  originalValue: number
  categoryName?: string
  usefulLifeMonths?: number
}
interface AssetClosureDue {
  period: string
  amount: number
  count: number
}
interface AssetClosureBlocked {
  name?: string
  period?: string
  reason: string
}

const assetClosureData = computed(() => {
  const preview = (payload.value.preview || {}) as Record<string, unknown>
  const fixed = (preview.fixedAssets || {}) as Record<string, unknown>
  const intangible = (preview.intangibles || {}) as Record<string, unknown>
  return {
    fixedCards: (fixed.missingCards || []) as AssetClosureCard[],
    fixedDue: (fixed.dueDepreciation || []) as AssetClosureDue[],
    fixedBlocked: (fixed.blockedItems || []) as AssetClosureBlocked[],
    intangibleCards: (intangible.missingCards || []) as AssetClosureCard[],
    intangibleDue: (intangible.dueAmortization || []) as AssetClosureDue[],
    intangibleBlocked: (intangible.blockedItems || []) as AssetClosureBlocked[],
  }
})
const runFixedDepreciation = computed(() => Boolean(payload.value.runFixedDepreciation))
const runIntangibleAmortization = computed(() => Boolean(payload.value.runIntangibleAmortization))

// 批量清单：可勾选排除、可改分类
const rows = reactive<{ row: VoucherRow; include: boolean; editedCategory: string }[]>([])
if (isBatch.value) {
  for (const raw of payload.value.items as VoucherRow[]) {
    rows.push({ row: raw, include: true, editedCategory: String(raw.category ?? '') })
  }
}
// 单笔模式：分类同样可改
const singleEditedCategory = reactive({ value: '' })
if (!isBatch.value) {
  singleEditedCategory.value = String(payload.value.category ?? '')
}

// ── 付款方（公司对公 / 老板垫付 / 员工垫付）──────────────────────
// 老板固定一项、不绑员工档案；员工从在职员工里选人。is_owner=1 的人不进
// 员工列表——否则同一笔垫付会落到「员工往来」而不是「其他应付款-股东」。
const payer = reactive<{ kind: PayerKind; employeeName: string }>({
  kind: 'company_account',
  employeeName: '',
})
if (!isBatch.value) {
  const initialPayer = splitPayer(payload.value.payer)
  const note = String(props.action.evidence?.note || '')
  const isCrossPeriodSettlement =
    String(payload.value.direction) === 'income' &&
    (note.includes('待到账') || note.includes('挂应收') || /202\d-\d{2}-\d{2}\s*到账/.test(note))
  if (initialPayer.kind === 'company_account' && isCrossPeriodSettlement) {
    payer.kind = 'receivable'
  } else {
    payer.kind = initialPayer.kind
  }
  payer.employeeName = initialPayer.employeeName
}

const isIncome = computed(() => String(payload.value.direction) === 'income')
const cardBadges = computed(() => {
  const badges: Array<{ text: string; type: 'info' | 'warning' | 'default' | 'success' }> = []
  const note = String(props.action.evidence?.note || '')
  if (
    note.includes('未开票') ||
    note.includes('no_invoice') ||
    payload.value.billingStatus === 'unbilled'
  ) {
    badges.push({ text: '未开票收入', type: 'info' })
  }
  if (payer.kind === 'receivable') {
    badges.push({ text: '待流水核销', type: 'warning' })
  }
  return badges
})

const employeeState = usePayeeEmployees()
const employeeLoading = employeeState.loading

const employeeOptions = computed(() => employeeState.options(payer.employeeName))

function onPayerKindChange(kind: PayerKind) {
  if (kind === 'employee_personal') {
    void employeeState.ensure()
    return
  }
  payer.employeeName = ''
}

watch(() => payer.kind, onPayerKindChange, { immediate: true })

const DIRECTION_TEXT: Record<string, string> = {
  income: '收入',
  expense: '支出',
  owner_transfer: '老板投入',
}
const STATUS_TAG: Record<string, { text: string; type: 'info' | 'success' | 'default' | 'warning' }> = {
  pending: { text: '待确认', type: 'warning' },
  approved: { text: '已记录', type: 'success' },
  rejected: { text: '已放弃', type: 'default' },
  expired: { text: '已过期', type: 'default' },
}

function amountText(v: unknown) {
  const amount = Number(v ?? 0)
  return amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function rowAmount(row: VoucherRow) {
  const amount = Number(row.amount ?? 0)
  return `${row.direction === 'income' ? '+' : '−'}¥ ${amountText(Math.abs(amount))}`
}

const includedIndexes = computed(() =>
  rows.map((r, i) => (r.include ? i : -1)).filter((i) => i >= 0),
)

async function doApprove(opts: Parameters<typeof approveAgentAction>[1] = {}) {
  busy.approving = true
  try {
    const resp = await approveAgentAction(props.action.id, opts)
    local.status = 'approved'
    local.result = resp.result || null
    if (isCompanySetup.value && resp.result) {
      const r = resp.result as Record<string, unknown>
      if (r.accessToken) {
        authStore.token = String(r.accessToken)
        localStorage.setItem('auth_token', String(r.accessToken))
      }
      await authStore.fetchProfile().catch(() => {})
      message.success(r.switchedFromDemo ? '已创建正式账套并完成切换！' : '主体建账信息已保存！')
    }
    emit('resolved')
  } catch (err) {
    if (guideStartDate(err)) return
    message.error(accountingErrorText(err))
  } finally {
    busy.approving = false
  }
}

async function approveSingle() {
  if (isCompanySetup.value) {
    if (!companyForm.name.trim()) {
      message.warning('请填写公司全称')
      return
    }
    return doApprove({
      edits: {
        '0': {
          onboardingType: companyForm.onboardingType,
          name: companyForm.name.trim(),
          taxId: companyForm.taxId.trim().toUpperCase(),
          type: companyForm.type,
          taxpayerType: companyForm.taxpayerType,
          startDate: companyForm.startDate.trim(),
          industry: companyForm.industry.trim(),
          vatFilingCycle: companyForm.vatFilingCycle,
        },
      },
    })
  }
  if (isOpeningBalance.value) {
    openOpeningStepUp()
    return
  }
  if (isMatch.value || isAccrual.value) return doApprove()
  if (payer.kind === 'employee_personal' && !payer.employeeName.trim()) {
    message.warning('请选择垫付的员工，或改为老板垫付')
    return
  }
  const edits: Record<string, { category?: string; payer?: string }> = {}
  const edit: { category?: string; payer?: string } = {}
  if (singleEditedCategory.value && singleEditedCategory.value !== payload.value.category) {
    edit.category = singleEditedCategory.value
  }
  const nextPayer = joinPayer(payer.kind, payer.employeeName)
  if (nextPayer !== String(payload.value.payer || 'company_account')) {
    edit.payer = nextPayer
  }
  if (Object.keys(edit).length) edits['0'] = edit
  const opts = Object.keys(edits).length ? { edits } : {}
  if (!(await guardBackfill(opts, [String(payload.value.occurDate || '')]))) return
  await doApprove(opts)
}

async function approveBatch() {
  const excludeIndexes = rows.map((r, i) => (r.include ? -1 : i)).filter((i) => i >= 0)
  if (excludeIndexes.length === rows.length) {
    message.warning('请至少保留一笔，或直接放弃整单')
    return
  }
  const edits: Record<string, { category?: string }> = {}
  rows.forEach((r, i) => {
    if (r.include && r.editedCategory && r.editedCategory !== r.row.category) {
      edits[String(i)] = { category: r.editedCategory }
    }
  })
  const opts = {
    excludeIndexes: excludeIndexes.length ? excludeIndexes : undefined,
    edits: Object.keys(edits).length ? edits : undefined,
  }
  const dates = rows.filter((r) => r.include).map((r) => String(r.row.occurDate || ''))
  if (!(await guardBackfill(opts, dates))) return
  await doApprove(opts)
}

// ── 跨期补记确认 ──────────────────────────────────────────────
// 单据所属期间已关账时，后端默认抛 PERIOD_CLOSED。这里先预判、再让用户
// 明确选「补记到 X 月」并填原因，最后带 allowBackfill 重新提交。
const backfill = reactive({
  show: false,
  businessPeriod: '',
  targetPeriod: '',
  crossQuarter: false,
  reason: '',
  pending: null as Parameters<typeof approveAgentAction>[1] | null,
})

async function guardBackfill(
  opts: Parameters<typeof approveAgentAction>[1],
  dates: string[],
): Promise<boolean> {
  const unique = [...new Set(dates.map((d) => String(d).slice(0, 10)).filter(Boolean))]
  for (const day of unique) {
    let target
    try {
      target = await getBackfillTarget(day)
    } catch {
      continue // 预判失败不阻塞正常入账，后端仍会做最终校验
    }
    if (!target?.needsBackfill) continue
    if (!target.canBackfill) {
      message.error(
        `${day} 属于 ${target.businessPeriod || day.slice(0, 7)}，该年度已结账，不能补记。` +
          '请由财税人员按以前年度应扣未扣支出做专项申报，或在「结账 → 导出清单」中下载底稿。',
        { duration: 8000 },
      )
      return false
    }
    backfill.pending = opts
    backfill.businessPeriod = target.businessPeriod || day.slice(0, 7)
    backfill.targetPeriod = target.targetPeriod || ''
    backfill.crossQuarter = !!target.crossQuarter
    backfill.reason = ''
    backfill.show = true
    return false
  }
  return true
}

async function confirmBackfill() {
  const reason = backfill.reason.trim()
  if (!reason) {
    message.warning('请填写补记原因，它会写进跨期补记台账')
    return
  }
  const opts = { ...(backfill.pending || {}), allowBackfill: true, backfillReason: reason }
  backfill.show = false
  await doApprove(opts)
}

async function reject() {
  busy.rejecting = true
  try {
    await rejectAgentAction(props.action.id)
    local.status = 'rejected'
    emit('resolved')
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    busy.rejecting = false
  }
}

// 批量结果按行展示
const batchResults = computed(() => {
  const items = (local.result as Record<string, unknown> | null)?.items
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : null
})
function resultLine(r: Record<string, unknown>): string {
  if (r.status === 'booked') return `已入账 ${(r.voucherRef as string) || ''}`.trim()
  if (r.status === 'excluded') return '已排除'
  if (r.status === 'failed') return `失败：${(r.error as string) || ''}`
  return ''
}

function summarizeAccrualResult(result: unknown): string {
  const accrual = (result as Record<string, unknown>)?.accrual as Record<string, unknown> | undefined
  const summary = (accrual?.summary || {}) as Record<string, unknown>
  const parts: string[] = []
  for (const key of ['processed_count', 'processedCount', 'booked_count', 'total_depreciation', 'total']) {
    if (summary[key] != null) parts.push(`${key}: ${summary[key]}`)
  }
  const text = parts.length ? `计提完成（${parts.join('，')}）` : '计提已执行'
  return text
}

function summarizeAssetClosureResult(result: unknown): string {
  const closure = (result as Record<string, unknown>)?.assetClosure as Record<string, unknown> | undefined
  if (!closure) return '资产处理已完成'
  const fixedCards = Array.isArray(closure.fixedCards) ? closure.fixedCards.length : 0
  const intangibleCards = Array.isArray(closure.intangibleCards) ? closure.intangibleCards.length : 0
  const fixedDep = closure.fixedDepreciation as Record<string, unknown> | null | undefined
  const intangibleAmt = closure.intangibleAmortization as Record<string, unknown> | null | undefined
  const parts = [
    fixedCards ? `固定资产卡 ${fixedCards} 张` : '',
    intangibleCards ? `无形资产卡 ${intangibleCards} 张` : '',
    fixedDep?.processedCount ? `固定资产折旧 ${fixedDep.processedCount} 期` : '',
    intangibleAmt?.processedCount ? `无形资产摊销 ${intangibleAmt.processedCount} 期` : '',
  ].filter(Boolean)
  return parts.length ? `资产处理完成：${parts.join('，')}` : '资产处理已完成'
}

const approveText = computed(() => {
  if (isCompanySetup.value) return payload.value.isFromDemo ? '确认创建正式账套' : '确认保存建账信息'
  if (isOpeningBalance.value) return '验证并生成期初凭证'
  if (isMatch.value) return '确认对账'
  if (isAssetClosure.value) return '确认处理'
  if (isAccrual.value) return '确认计提'
  if (isBatch.value) return `确认记录（${includedIndexes.value.length} 笔）`
  return '确认记录'
})
</script>

<template>
  <div class="agent-action-card" :class="local.status">
    <div class="card-head">
      <span class="card-title">
        {{
          isCompanySetup
            ? companyForm.onboardingType === 'migration'
              ? '代账/旧账迁入 · 主体建账确认'
              : '新公司从零记账 · 主体建账确认'
            : isOpeningBalance
              ? `期初余额导入确认（${openingState.startPeriod || openingState.fiscalYear}）`
              : isMatch
                ? '银行流水对账建议'
                : isAccrual
                  ? `${accrualKindText}（${accrualData.month}）`
                  : isBatch
                    ? `批量记账（${(payload.items as VoucherRow[]).length} 笔）`
                    : action.title || '记账草稿'
        }}
      </span>
      <div class="card-head-tags">
        <NTag
          v-for="b in cardBadges"
          :key="b.text"
          size="small"
          :type="b.type"
          :bordered="false"
        >
          {{ b.text }}
        </NTag>
        <NTag size="small" :type="STATUS_TAG[local.status]?.type || 'default'" :bordered="false">
          {{
            local.status === 'approved' && isCompanySetup
              ? '已建账'
              : STATUS_TAG[local.status]?.text || local.status
          }}
        </NTag>
      </div>
    </div>

    <!-- 主体建账信息确认卡 -->
    <template v-if="isCompanySetup">
      <div class="card-fields setup-fields">
        <div class="field field-span-2">
          <span class="field-label">建账场景</span>
          <NSelect
            v-if="local.status === 'pending'"
            v-model:value="companyForm.onboardingType"
            :options="ONBOARDING_OPTIONS"
            size="tiny"
          />
          <span v-else class="field-value">
            {{ companyForm.onboardingType === 'migration' ? '从其他代账/软件转入' : '新成立公司从零记账' }}
          </span>
        </div>
        <div class="field">
          <span class="field-label">启用日期</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="companyForm.startDate"
            size="tiny"
            placeholder="YYYY-MM-DD"
          />
          <span v-else class="field-value">{{ companyForm.startDate || '—' }}</span>
        </div>
        <div class="field field-span-2">
          <span class="field-label">公司全称（必填）</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="companyForm.name"
            size="tiny"
            placeholder="营业执照上的完整名称"
          />
          <span v-else class="field-value">{{ companyForm.name }}</span>
        </div>
        <div class="field">
          <span class="field-label">统一社会信用代码（18位）</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="companyForm.taxId"
            size="tiny"
            maxlength="18"
            placeholder="可稍后补填"
          />
          <span v-else class="field-value">{{ companyForm.taxId || '暂未填' }}</span>
        </div>
        <div class="field">
          <span class="field-label">主体类型</span>
          <NSelect
            v-if="local.status === 'pending'"
            v-model:value="companyForm.type"
            :options="ENTITY_TYPE_OPTIONS"
            size="tiny"
          />
          <span v-else class="field-value">{{ companyForm.type }}</span>
        </div>
        <div class="field">
          <span class="field-label">纳税人身份</span>
          <NSelect
            v-if="local.status === 'pending'"
            v-model:value="companyForm.taxpayerType"
            :options="TAXPAYER_OPTIONS"
            size="tiny"
          />
          <span v-else class="field-value">{{ companyForm.taxpayerType }}</span>
        </div>
        <div class="field">
          <span class="field-label">所属行业</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="companyForm.industry"
            size="tiny"
            placeholder="如：软件和信息技术服务"
          />
          <span v-else class="field-value">{{ companyForm.industry || '—' }}</span>
        </div>
      </div>
      <div class="card-result" v-if="local.status === 'approved'">
        {{
          (local.result as any)?.switchedFromDemo
            ? '正式账套已创建并切换生效 ✓'
            : '主体建账信息已更新 ✓'
        }}
      </div>
    </template>

    <!-- 期初余额导入确认卡 -->
    <template v-else-if="isOpeningBalance">
      <div class="opening-summary-bar">
        <div class="opening-stat">
          <span class="field-label">借方合计（资产）</span>
          <span class="field-value amount">¥ {{ amountText(openingTotals.totalDebit) }}</span>
        </div>
        <div class="opening-stat">
          <span class="field-label">贷方合计（负债+权益）</span>
          <span class="field-value amount">¥ {{ amountText(openingTotals.totalCredit) }}</span>
        </div>
        <div class="opening-stat">
          <span class="field-label">试算状态</span>
          <NTag
            size="small"
            :type="openingTotals.balanced ? 'success' : 'warning'"
            :bordered="false"
          >
            {{
              openingTotals.balanced
                ? '借贷已平衡 ✓'
                : `差额 ¥${amountText(openingTotals.absDiff)}`
            }}
          </NTag>
        </div>
      </div>

      <div class="batch-list opening-item-list">
        <div v-for="(it, idx) in openingState.items" :key="`ob-${idx}`" class="batch-row">
          <div class="row-head">
            <span class="match-side">{{ it.account_code }}</span>
            <span class="row-title">
              {{ it.account_name }}
              <span v-if="it.partner_name" class="muted">（{{ it.partner_name }}）</span>
            </span>
            <span class="row-amount" v-if="parseFloat(String(it.debit_balance)) > 0">
              借 ¥ {{ amountText(it.debit_balance) }}
            </span>
            <span class="row-amount is-income" v-else>
              贷 ¥ {{ amountText(it.credit_balance) }}
            </span>
          </div>
        </div>
        <div v-if="openingState.ytdItems.length" class="opening-ytd-note">
          含年中本年损益累计发生额 {{ openingState.ytdItems.length }} 项
        </div>
      </div>

      <div
        v-if="local.status === 'pending' && !openingTotals.balanced && openingTotals.absDiff > 0"
        class="opening-plug-bar"
      >
        <span>借贷存在差额 ¥{{ amountText(openingTotals.absDiff) }}，可一键归入未分配利润配平，或前往期初工作台细调：</span>
        <NButton size="tiny" secondary type="warning" @click="plugOpeningDifference">
          差额计入未分配利润(3104006)配平
        </NButton>
      </div>

      <div class="card-result" v-if="local.status === 'approved'">
        期初余额凭证已生成（凭证号 {{ (local.result as any)?.opening?.voucherNo || '已过账' }}）✓
      </div>
    </template>

    <!-- 对账建议 -->
    <template v-if="isMatch">
      <div class="match-block">
        <div class="match-row">
          <span class="match-side">流水</span>
          <span class="match-main">
            {{ matchData.statement.description || '—' }}
            <span class="muted">{{ matchData.statement.date }}</span>
          </span>
          <span class="row-amount">¥ {{ amountText(matchData.statement.amount) }}</span>
        </div>
        <div class="match-row" v-for="(e, i) in matchData.entries" :key="i">
          <span class="match-side">账目</span>
          <span class="match-main">{{ e.title || e.id }}</span>
          <span class="row-amount is-income">¥ {{ amountText(e.amount) }}</span>
        </div>
      </div>
      <div class="card-result" v-if="local.status === 'approved'">
        {{ (local.result as any)?.match?.status === 'matched' ? '已对账 ✓' : '已处理' }}
      </div>
    </template>

    <!-- 资产处理确认 -->
    <template v-else-if="isAssetClosure">
      <div class="card-fields">
        <div class="field">
          <span class="field-label">处理月份</span>
          <span class="field-value">{{ accrualData.month }}</span>
        </div>
      </div>

      <div v-if="assetClosureData.fixedCards.length" class="match-block">
        <div class="match-row">
          <span class="match-side">固定资产</span>
          <span class="match-main">将建立 {{ assetClosureData.fixedCards.length }} 张资产卡</span>
        </div>
        <div v-for="(card, i) in assetClosureData.fixedCards" :key="`fa-${i}`" class="match-row">
          <span class="match-side" />
          <span class="match-main">
            {{ card.name }}
            <span class="muted">{{ card.categoryName }} · {{ card.usefulLifeMonths }} 个月</span>
          </span>
          <span class="row-amount">¥ {{ amountText(card.originalValue) }}</span>
        </div>
      </div>

      <div v-if="assetClosureData.intangibleCards.length" class="match-block">
        <div class="match-row">
          <span class="match-side">无形资产</span>
          <span class="match-main">将建立 {{ assetClosureData.intangibleCards.length }} 张资产卡</span>
        </div>
        <div v-for="(card, i) in assetClosureData.intangibleCards" :key="`ia-${i}`" class="match-row">
          <span class="match-side" />
          <span class="match-main">
            {{ card.name }}
            <span class="muted">{{ card.categoryName }} · {{ card.usefulLifeMonths }} 个月</span>
          </span>
          <span class="row-amount">¥ {{ amountText(card.originalValue) }}</span>
        </div>
      </div>

      <div v-if="runFixedDepreciation && assetClosureData.fixedDue.length" class="match-block">
        <div class="match-row">
          <span class="match-side">折旧</span>
          <span class="match-main">将补提 {{ assetClosureData.fixedDue.length }} 个期间</span>
        </div>
        <div v-for="item in assetClosureData.fixedDue" :key="`fd-${item.period}`" class="match-row">
          <span class="match-side" />
          <span class="match-main">{{ item.period }} · {{ item.count }} 项</span>
          <span class="row-amount">¥ {{ amountText(item.amount) }}</span>
        </div>
      </div>

      <div v-if="runIntangibleAmortization && assetClosureData.intangibleDue.length" class="match-block">
        <div class="match-row">
          <span class="match-side">摊销</span>
          <span class="match-main">将补提 {{ assetClosureData.intangibleDue.length }} 个期间</span>
        </div>
        <div v-for="item in assetClosureData.intangibleDue" :key="`id-${item.period}`" class="match-row">
          <span class="match-side" />
          <span class="match-main">{{ item.period }} · {{ item.count }} 项</span>
          <span class="row-amount">¥ {{ amountText(item.amount) }}</span>
        </div>
      </div>

      <div
        v-if="assetClosureData.fixedBlocked.length || assetClosureData.intangibleBlocked.length"
        class="card-evidence"
      >
        <div>以下项目暂不能处理：</div>
        <div
          v-for="(item, i) in [...assetClosureData.fixedBlocked, ...assetClosureData.intangibleBlocked]"
          :key="`blocked-${i}`"
        >
          · {{ item.name || item.period }}：{{ item.reason }}
        </div>
      </div>

      <div class="card-result" v-if="local.status === 'approved' && local.result">
        {{ summarizeAssetClosureResult(local.result) }}
      </div>
    </template>

    <!-- 计提确认 -->
    <template v-else-if="isAccrual">
      <div class="card-fields">
        <div class="field">
          <span class="field-label">计提类型</span>
          <span class="field-value">{{ accrualKindText }}</span>
        </div>
        <div class="field">
          <span class="field-label">月份</span>
          <span class="field-value">{{ accrualData.month }}</span>
        </div>
        <div class="field" v-if="accrualData.kind === 'depreciation'">
          <span class="field-label">待计提</span>
          <span class="field-value amount">
            {{ accrualData.preview.pendingCount ?? 0 }} 项 / ¥ {{ amountText(accrualData.preview.pendingTotal ?? 0) }}
          </span>
        </div>
        <div class="field" v-else>
          <span class="field-label">工资表</span>
          <span class="field-value">{{ accrualData.preview.message || (accrualData.preview.sheetExists ? '已创建' : '未创建') }}</span>
        </div>
      </div>
      <div class="card-result" v-if="local.status === 'approved' && local.result">
        {{ summarizeAccrualResult(local.result) }}
      </div>
    </template>

    <!-- 批量清单 -->
    <div v-else-if="isBatch" class="batch-list">
      <div v-for="(r, i) in rows" :key="i" class="batch-row">
        <div class="row-head">
          <NCheckbox
            v-if="local.status === 'pending'"
            v-model:checked="r.include"
            size="small"
          />
          <span v-else class="row-dot" :class="String(batchResults?.[i]?.status || 'booked')" />
          <span class="row-title">{{ r.row.title }}</span>
          <span class="row-amount" :class="r.row.direction === 'income' ? 'is-income' : ''">
            {{ rowAmount(r.row) }}
          </span>
        </div>
        <div class="row-fields">
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="r.editedCategory"
            size="tiny"
            placeholder="分类"
            class="row-category-input"
          />
          <span v-else class="row-field">
            {{ (batchResults?.[i]?.category as string) || r.row.category }}
          </span>
          <span class="row-field muted" v-if="r.row.occurDate">{{ r.row.occurDate }}</span>
          <span class="row-field muted" v-if="r.row.payer">{{ payerCardLabel(r.row.payer) }}</span>
          <span class="row-result" v-if="batchResults && batchResults[i]">
            {{ resultLine(batchResults[i]) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 单笔草稿 -->
    <template v-else>
      <div class="card-fields">
        <div class="field">
          <span class="field-label">方向</span>
          <span class="field-value" :class="String(payload.direction) === 'income' ? 'is-income' : ''">
            {{ DIRECTION_TEXT[String(payload.direction)] || payload.direction || '—' }}
          </span>
        </div>
        <div class="field">
          <span class="field-label">金额</span>
          <span class="field-value amount">¥ {{ amountText(payload.amount) }}</span>
        </div>
        <div class="field">
          <span class="field-label">分类（可修改）</span>
          <NInput
            v-if="local.status === 'pending'"
            v-model:value="singleEditedCategory.value"
            size="tiny"
            placeholder="分类"
          />
          <span v-else class="field-value">{{ payload.category || '—' }}</span>
        </div>
        <div class="field">
          <span class="field-label">日期</span>
          <span class="field-value">{{ payload.occurDate || '—' }}</span>
        </div>
        <div class="field field-payer">
          <span class="field-label">{{ isIncome ? '结算方式' : '付款方' }}</span>
          <template v-if="local.status === 'pending'">
            <NSelect
              v-model:value="payer.kind"
              :options="isIncome ? INCOME_PAYER_OPTIONS : PAYER_OPTIONS"
              size="tiny"
            />
            <NSelect
              v-if="payer.kind === 'employee_personal'"
              v-model:value="payer.employeeName"
              :options="employeeOptions"
              size="tiny"
              filterable
              clearable
              :loading="employeeLoading"
              placeholder="选择垫付员工"
              class="employee-select"
            />
          </template>
          <span v-else class="field-value">{{ payerCardLabel(payload.payer) }}</span>
        </div>
        <div class="field" v-if="payload.merchant">
          <span class="field-label">对方</span>
          <span class="field-value">{{ payload.merchant }}</span>
        </div>
      </div>
    </template>

    <div class="card-evidence" v-if="action.evidence?.note">
      {{ action.evidence.note }}
    </div>

    <div class="card-result" v-if="local.status === 'approved' && !isBatch && !isMatch && !isAccrual && !isCompanySetup && !isOpeningBalance && local.result">
      已记入账簿，凭证号 {{ (local.result as Record<string, unknown>).voucherRef || '—' }}
      <span v-if="payer.kind === 'receivable'" class="muted" style="margin-left: 6px;">
        （已挂入应收账款，后续导入银行流水时直接对账核销）
      </span>
    </div>
    <div class="card-result" v-else-if="local.status === 'approved' && isBatch && !batchResults?.some((r) => r.status === 'booked')">
      未入账任何一笔
    </div>

    <div class="card-actions" v-if="local.status === 'pending'">
      <NButton size="small" quaternary :disabled="busy.approving" @click="reject">
        放弃
      </NButton>
      <NButton
        v-if="isOpeningBalance"
        size="small"
        secondary
        :disabled="busy.approving"
        @click="openInOpeningWorkbench"
      >
        去期初工作台细调
      </NButton>
      <NButton
        size="small"
        type="primary"
        :loading="busy.approving"
        @click="isBatch ? approveBatch() : approveSingle()"
      >
        {{ approveText }}
      </NButton>
    </div>

    <NModal
      v-model:show="stepUpModal.show"
      preset="card"
      title="安全验证 · 生成正式期初凭证"
      style="max-width: 420px;"
      :mask-closable="false"
    >
      <div class="backfill-copy">
        <p class="muted">
          期初余额直接决定资产负债表基数，需验证账号绑定手机号后生成正式期初凭证。
        </p>
        <div class="stepup-form">
          <NInput v-model:value="stepUpModal.phone" size="small" placeholder="绑定手机号" />
          <div class="stepup-code-row">
            <NInput v-model:value="stepUpModal.code" size="small" placeholder="短信验证码" />
            <NButton size="small" secondary :loading="stepUpModal.sending" @click="sendStepUpSms">
              获取验证码
            </NButton>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="backfill-actions">
          <NButton size="small" quaternary @click="stepUpModal.show = false">取消</NButton>
          <NButton size="small" type="primary" :loading="busy.approving" @click="confirmOpeningStepUp">
            确认并生成期初凭证
          </NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="backfill.show"
      preset="card"
      title="这笔业务要补记到下个月"
      style="max-width: 460px;"
      :mask-closable="false"
    >
      <div class="backfill-copy">
        <p>
          <strong>{{ backfill.businessPeriod }}</strong> 已经关账，这笔业务将按
          <strong>{{ backfill.targetPeriod }}</strong> 记账（原始单据日期不变）。
        </p>
        <p class="muted">
          依据《小企业会计准则》第 88 条，差错在发现当期更正，不追溯调整已关账月份。
        </p>
        <p v-if="backfill.crossQuarter" class="backfill-warn">
          注意：这次补记跨越季度，会同时影响两个季度的所得税预缴；小规模纳税人的
          季度 30 万免税判断也可能因此变化。
        </p>
      </div>
      <NInput
        v-model:value="backfill.reason"
        type="textarea"
        placeholder="补记原因（必填，例如：8 月发票 9 月才收到）"
        :autosize="{ minRows: 2, maxRows: 4 }"
      />
      <template #footer>
        <div class="backfill-actions">
          <NButton size="small" quaternary @click="backfill.show = false">取消</NButton>
          <NButton size="small" type="primary" @click="confirmBackfill">确认补记到 {{ backfill.targetPeriod }}</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.agent-action-card {
  border: 1px solid var(--solo-border, #dde3ea);
  border-left: 3px solid var(--solo-primary, #1747b0);
  border-radius: var(--solo-radius, 8px);
  padding: 11px 13px;
  background: var(--solo-bg-card, #ffffff);
  font-size: 13px;
  transition: all 0.2s ease;
}
.agent-action-card.approved {
  border-left-color: var(--solo-income, #0f7354);
  background: var(--solo-income-bg);
}
.agent-action-card.rejected {
  border-left-color: var(--solo-text-muted, #636e7c);
  opacity: 0.8;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.card-head-tags {
  display: flex;
  align-items: center;
  gap: 6px;
}
.card-title {
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
}
.card-fields {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.field-label {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}
.field-value {
  color: var(--solo-text-primary, #182230);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.field-value.amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.field-value.is-income,
.row-amount.is-income {
  color: var(--solo-income, #0f7354);
}

/* 付款方：选「员工垫付」时级联出员工名单，两个选择器竖排 */
.field-payer {
  gap: 4px;
}
.employee-select {
  margin-top: 2px;
}

/* 批量清单 */
.batch-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}
.batch-row {
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 4px);
  padding: 6px 8px;
  background: var(--solo-bg-page, #f6f7f9);
}
.row-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--solo-income, #0f7354);
  flex-shrink: 0;
}
.row-dot.excluded {
  background: var(--solo-text-muted, #636e7c);
}
.row-dot.failed {
  background: var(--solo-expense, #bd3d3d);
}
.row-title {
  flex: 1;
  color: var(--solo-text-primary, #182230);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.row-fields {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  padding-left: 24px;
  flex-wrap: wrap;
}
.row-category-input {
  width: 130px;
}
.row-field {
  font-size: 12px;
  color: var(--solo-text-primary, #182230);
}
.row-field.muted {
  color: var(--solo-text-muted, #636e7c);
}
.row-result {
  font-size: 12px;
  color: var(--solo-income, #0f7354);
}

.card-evidence {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}
.card-result {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-income, #0f7354);
}
.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

/* 对账建议 */
.match-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.match-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.match-side {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--solo-text-muted);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 4px;
  padding: 1px 6px;
}
.match-main {
  flex: 1;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted {
  color: var(--solo-text-muted);
  font-size: 12px;
}

.backfill-copy {
  margin-bottom: 10px;
  font-size: 13px;
  line-height: 1.7;
}

.backfill-copy p {
  margin: 0 0 6px;
}

.backfill-warn {
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
  font-size: 12px;
}

.backfill-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.field-span-2 {
  grid-column: span 2;
}
.opening-summary-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  margin-bottom: 8px;
  border-radius: 6px;
  background: var(--solo-bg-page, #f6f7f9);
  border: 1px solid var(--solo-border, #dde3ea);
}
.opening-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.opening-item-list {
  max-height: 220px;
}
.opening-ytd-note {
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  padding: 2px 4px;
}
.opening-plug-bar {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--solo-warning-bg, #fff5e8);
  color: var(--solo-warning, #a05c10);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.stepup-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.stepup-code-row {
  display: flex;
  gap: 8px;
}
</style>
