/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 对账工作台（closing-wizard Step 2 核心）：AI 建议、已配对重建、未配对过滤、
 * 高/需关注分级与批量确认、行内分类编辑三弹窗（手动分类/配对分类/改凭证）、
 * 入账去向处置、老板速记清单。分类域与对账确认互相递归
 * （openManualClassify ↔ selectExistingReconciliation、effectiveSuggestion 被
 * confirmSuggested 用），故合并为一个 workspace 而非再拆两个。
 *
 * 注入项：month/currentStep/entryPeriod/monthEntries/entryStore/loading/
 * backfillConfirm 为父页共享状态或判定；openVoucherModal/scrollIntoBank 为
 * 交叉域入口（批量过账弹层与流水定位）。
 */
import { computed, h, nextTick, ref, watch, type Ref } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { updateEntry } from '@/api/record'
import { accountingErrorText } from '@/utils/errors'
import { formatMoney } from '@/utils/format'
import { sortEntriesByDateDescending } from '@/utils/entrySort'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import {
  buildSuggestedMatchPair,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  isReconciledEntry,
  isNonBankSettledEntry,
} from '@/utils/closingWizard'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { accountForCategory, getAmbiguityGroup } from '@/utils/accounting'
import { useBackfillConfirm, isBackfillCancelled } from '@/composables/backfillConfirm'
import type { useEntryStore } from '@/stores/entry'

export function useReconciliationWorkspace(options: {
  month: Ref<string>
  currentStep: Ref<number>
  entryPeriod: (e: any) => string
  entryStore: ReturnType<typeof useEntryStore>
  backfillConfirm: ReturnType<typeof useBackfillConfirm>
  openVoucherModal: (entryId: string) => Promise<void>
  scrollIntoBank: (statementId: string) => void
  /** 跨域共享：工资名册（员工垫付选项）与批量过账弹层当前凭证（改凭证弹窗） */
  employees: Ref<any[]>
  currentVoucherEntry: Ref<any>
  reloadAll: () => Promise<void>
  apiErrorMessage: (err: any, fallback: string) => string
}) {
  const { month: targetMonth, currentStep, entryPeriod, entryStore, backfillConfirm, openVoucherModal, scrollIntoBank, employees, currentVoucherEntry, reloadAll, apiErrorMessage } = options
  const message = useMessage()
  const dialog = useDialog()

const rawStatements = ref<any[]>([])
const bankStatements = ref<any[]>([])
const invoicesList = ref<any[]>([])

// WeChat bill state
// 导入失败明细（按原因聚合）：失败不再只靠一闪而过的 message，内嵌面板可读、可操作

// 发票表的专业列（类型/入账模式/冲销）默认收起，老板只需要日期/名称/金额/状态
const showInvoiceDetails = ref(false)

// Step 4: Matching workspace states
const selectedInvoiceIds = ref<string[]>([])
const selectedBank = ref<string | null>(null)
const matchedPairs = ref<any[]>([])
const aiSuggestions = ref<any[]>([])
const reconciliationHealth = ref<any | null>(null)
const reconciliationHealthLoading = ref(false)


function filterUnmatchedData() {
  // Unmatched bank statements
  // ponytail: 空日期的流水不过滤，否则银行流水导入后因日期解析失败消失
  bankStatements.value = rawStatements.value.filter(
    (s) => !s.matched_entry_id && (
      !s.transaction_date ||
      s.transaction_date === '' ||
      s.transaction_date.substring(0, 7) <= targetMonth.value
    )
  )

  // All invoices for current month (with linked/confirmed status split)
  const { linkedEntryIds, confirmedEntryIds } = getMatchedEntryIdSets(rawStatements.value, entryStore.activeEntries)
  invoicesList.value = entryStore.activeEntries.filter((e) => {
    const isInvoice = (e as any).source === 'invoice_import' || !!(e as any).invoiceNumber
    const isInMonth = entryPeriod(e) === targetMonth.value
    return isInvoice && isInMonth
  }).map(e => ({
    id: e.id,
    title: e.title,
    date: e.occurDate,
    amount: e.amount,
    direction: e.direction,
    category: (e as any).category || '',
    billingStatus: (e as any).billingStatus,
    paymentStatus: (e as any).paymentStatus || '',
    voucherStatus: (e as any).voucherStatus || '',
    voucherRef: (e as any).voucherRef || (e as any).voucher_no || '',
    payer: (e as any).payer || 'company_account',
    is_archive_only: (e as any).is_archive_only || (e as any).isArchiveOnly || false,
    // billingStatus='reconciled' 由后端在报销垫付核销时写入（流水指向 reim- 账目，原发票账目同步结清），视为已对上
    status: confirmedEntryIds.has(e.id) || e.billingStatus === 'reconciled' ? 'matched' : linkedEntryIds.has(e.id) ? 'pending_confirmation' : 'unmatched'
  }))
}


let suggestionsController: AbortController | null = null
// Fetch AI matching suggestions
async function loadAiSuggestions() {
  if (suggestionsController) {
    try {
      suggestionsController.abort()
    } catch {
      // ignore
    }
  }
  suggestionsController = new AbortController()

  try {
    const res: any = await apiClient.post('/api/bank/ai-suggest', { month: targetMonth.value }, {
      timeout: 60000,
      signal: suggestionsController.signal
    })
    const visibleStatementIds = new Set(bankStatements.value.map((s: any) => s.id))
    aiSuggestions.value = (res.suggestions || []).filter((s: any) => visibleStatementIds.has(s.statementId))

    const suggestionPairs = aiSuggestions.value
      .map((suggestion: any) => buildSuggestedMatchPair(suggestion, entryStore.activeEntries))
      .filter(Boolean)

    matchedPairs.value = [...buildPersistedMatchedPairs(), ...suggestionPairs]
  } catch (err: any) {
    if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
      return
    }
    console.error('[loadAiSuggestions] AI 对账建议加载失败:', err)
    message.warning('AI 对账建议加载失败，可稍后重试或手动指定科目')
    matchedPairs.value = buildPersistedMatchedPairs()
  }
}


function emptyReconciliationHealth() {
  return {
    broken_links: [],
    amount_mismatches: [],
    direction_mismatches: [],
    duplicate_entry_matches: [],
    pending_confirmations: [],
    summary: { highRiskCount: 0, warningCount: 0 },
  }
}

async function loadReconciliationHealth() {
  reconciliationHealthLoading.value = true
  try {
    reconciliationHealth.value = await apiClient.get('/api/bank/reconciliation-health', {
      params: { month: targetMonth.value },
    })
  } catch {
    reconciliationHealth.value = emptyReconciliationHealth()
  } finally {
    reconciliationHealthLoading.value = false
  }
}
// Split / delete a pair
async function deletePair(id: string) {
  const pair = matchedPairs.value.find(p => p.id === id)
  if (pair) {
    if (pair.status !== 'confirmed' && !pair.persisted) {
      matchedPairs.value = matchedPairs.value.filter(p => p.id !== id)
      message.info('已忽略该 AI 匹配建议')
      return
    }
    try {
      await apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: pair.statementId,
        type: 'match',
        matched_entry_id: '',
        matched_entry_ids: [],
      })
      message.info('对账关联已取消')
      await reloadAll()
    } catch (err) {
      message.error(accountingErrorText(err))
    }
  }
}

function isDirectionCompatible(direction: string, statementAmount: number) {
  if (statementAmount > 0) return direction === 'income' || direction === 'owner_transfer'
  if (statementAmount < 0) return direction === 'expense' || direction === 'owner_transfer'
  return true
}



function safeFloat(val: any): number {
  if (val === null || val === undefined || val === '') return 0
  if (typeof val === 'number') return val
  const cleaned = String(val).replace(/,/g, '').trim()
  const parsed = parseFloat(cleaned)
  return isNaN(parsed) ? 0 : parsed
}

function isItemAutoMatched(row: any) {
  const amount = safeFloat(row.amount)
  const desc = String(row.description || '').trim()
  const date = String(row.transaction_date || '').trim()
  
  const found = rawStatements.value.find(s => 
    Math.abs(s.amount) === Math.abs(amount) && 
    s.description === desc &&
    String(s.transaction_date || '').startsWith(date)
  )
  const matchedEntryIds = getStatementMatchedEntryIds(found)
  if (matchedEntryIds.length === 0) return false
  return matchedEntryIds.every((entryId) => isReconciledEntry(
    entryStore.activeEntries.find((entry) => entry.id === entryId) as any,
  ))
}

const unmatchedInvoices = computed(() => {
  try {
    return invoicesList.value
  } catch (err) {
    console.error('unmatchedInvoices computed error:', err)
    return []
  }
})

function getEntryMonth(entry: any) {
  return entry.date ? String(entry.date).substring(0, 7) : ''
}

function getStatementMonth(statement: any) {
  return statement.transaction_date ? String(statement.transaction_date).substring(0, 7) : ''
}

function isStatementInTargetMonth(statement: any) {
  const month = getStatementMonth(statement)
  return month === targetMonth.value
}

function buildPersistedMatchedPairs() {
  try {
    const entriesById = new Map(entryStore.activeEntries.map((entry) => [entry.id, entry]))
    const pairs: any[] = []

    for (const statement of rawStatements.value) {
      const matchedEntryIds = getStatementMatchedEntryIds(statement)
      if (matchedEntryIds.length === 0 || !isStatementInTargetMonth(statement)) continue

      const entries = matchedEntryIds
        .map((entryId) => entriesById.get(entryId))
        .filter(Boolean) as any[]
      if (entries.length === 0) continue

      const confirmed = entries.length === matchedEntryIds.length && entries.every((item) => isReconciledEntry(item))
      const pairEntries = entries.map((item) => ({
        id: item.id,
        title: item.title || '未命名条目',
        amount: item.amount,
        direction: item.direction,
        billingStatus: item.billingStatus,
        category: item.category || '',
        accountCode: item.accountCode || '',
        accountName: item.accountName || '',
        voucherLines: item.voucherLines,
        voucherStatus: item.voucherStatus,
        source: item.source,
        reconciliationType: item.reconciliationType,
      }))
      pairs.push({
        id: statement.id,
        statementId: statement.id,
        matchedEntryId: matchedEntryIds[0],
        matchedEntryIds,
        entries: pairEntries,
        settlement: entries.find((item) => item.reconciliationType === 'wechat_settlement'),
        invoice: pairEntries[0],
        bank: {
          id: statement.id,
          title: statement.description || '未命名流水',
          amount: statement.amount,
        },
        confidence: confirmed ? '已确认' : '待确认',
        reason: confirmed
          ? (pairEntries.length > 1 ? `${pairEntries.length} 笔账目合并对账` : '已确认对账')
          : '已有流水关联，等待人工确认',
        persisted: true,
        status: confirmed ? 'confirmed' : 'pending',
      })
    }

    return pairs
  } catch (err) {
    console.error('buildPersistedMatchedPairs error:', err)
    return []
  }
}

const allUnmatchedEntries = computed(() => {
  try {
    const matchedIds = new Set(rawStatements.value.flatMap((statement) => getStatementMatchedEntryIds(statement)))
    // 冲销红字凭证（entryType=reversal）不参与对账；被红字冲销的原凭证同样
    // 与红字完全抵消、无剩余资金影响，一并退出对账候选（与待办去噪同口径）
    const reversalIds = new Set(entryStore.activeEntries
      .filter((e) => (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal')
      .map((e) => String((e as any).reversalOf || '').trim())
      .filter(Boolean))
    return sortEntriesByDateDescending(entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      const isBeforeOrInMonth = month ? month <= targetMonth.value : false
      const isNotMatched = !matchedIds.has(e.id)
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      const isNotReconciled = e.billingStatus !== 'reconciled' && (e as any).billing_status !== 'reconciled'
      // ponytail: 非公户结算（垫付、未付款挂账、预存扣款、平台未提现等）无需且无法匹配公户银行流水
      const isNonBank = isNonBankSettledEntry(e as any)
      const isReversal = (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal'
      const isReversedOriginal = reversalIds.has(String(e.id))
      return isBeforeOrInMonth && isNotMatched && isNotArchiveOnly && isNotReconciled && !isNonBank && !isReversal && !isReversedOriginal
    }).map(e => ({
      id: e.id,
      title: e.title,
      date: e.occurDate,
      month: e.occurDate ? e.occurDate.substring(0, 7) : '',
      amount: e.amount,
      direction: e.direction,
      category: (e as any).category || '',
      source: (e as any).source || 'manual',
      paymentStatus: (e as any).paymentStatus || '',
      invoiceNumber: (e as any).invoiceNumber || '',
      voucherLines: (e as any).voucherLines || null,
      payer: (e as any).payer || 'company_account',
      voucherStatus: (e as any).voucherStatus || '',
      is_archive_only: (e as any).is_archive_only || (e as any).isArchiveOnly || false,
      status: 'unmatched'
    })))
  } catch (err) {
    console.error('allUnmatchedEntries computed error:', err)
    return []
  }
})


const unmatchedEntries = computed(() => {
  try {
    return allUnmatchedEntries.value.filter(e => getEntryMonth(e) === targetMonth.value)
  } catch (err) {
    console.error('unmatchedEntries computed error:', err)
    return []
  }
})
const unmatchedStatements = computed(() => {
  try {
    return bankStatements.value.filter(s => {
      const month = getStatementMonth(s)
      return !month || month === targetMonth.value
    })
  } catch (err) {
    console.error('unmatchedStatements computed error:', err)
    return []
  }
})
const historicalUnmatchedStatements = computed(() => {
  try {
    return bankStatements.value.filter(s => {
      const month = getStatementMonth(s)
      return month && month < targetMonth.value
    })
  } catch (err) {
    console.error('historicalUnmatchedStatements computed error:', err)
    return []
  }
})

// Reconciliation completeness metrics
const reconciliationStats = computed(() => {
  try {
    const entriesById = new Map(entryStore.activeEntries.map((entry) => [entry.id, entry]))
    const isConfirmedStatement = (statement: any) => {
      const matchedEntryIds = getStatementMatchedEntryIds(statement)
      if (matchedEntryIds.length === 0) return false
      return matchedEntryIds.every((entryId) => isReconciledEntry(entriesById.get(entryId) as any))
    }

    // ponytail: 仅统计本月目标月份流水，避免历史未匹配流水拉低本月统计造成虚假告警
    const targetMonthStatements = rawStatements.value.filter((s: any) => {
      const m = s.transaction_date ? s.transaction_date.substring(0, 7) : ''
      return !m || m === targetMonth.value
    })

    const total = targetMonthStatements.length
    const matched = targetMonthStatements.filter(isConfirmedStatement).length
    const unmatched = total - matched
    const rate = total > 0 ? Math.round((matched / total) * 100) : (targetMonthStatements.length === 0 ? 100 : 0)

    const unmatchedItems = targetMonthStatements.filter(s => !isConfirmedStatement(s))
    const unmatchedIncome = unmatchedItems.filter(s => s.amount > 0)
    const unmatchedExpense = unmatchedItems.filter(s => s.amount < 0)

    return { total, matched, unmatched, rate, unmatchedIncome, unmatchedExpense }
  } catch (err) {
    console.error('reconciliationStats computed error:', err)
    return { total: 0, matched: 0, unmatched: 0, rate: 0, unmatchedIncome: [], unmatchedExpense: [] }
  }
})



const reconciliationHealthSummary = computed(() => {
  try {
    return reconciliationHealth.value?.summary || { highRiskCount: 0, warningCount: 0 }
  } catch {
    return { highRiskCount: 0, warningCount: 0 }
  }
})
const hasBlockingReconciliationRisk = computed(() => {
  try {
    return reconciliationHealthSummary.value.highRiskCount > 0
  } catch {
    return false
  }
})
// Get AI suggestion for a statement
function getAiSuggestion(stmtId: string) {
  return aiSuggestions.value.find(s => s.statementId === stmtId)
}

/** 账上已有对应凭证：此时该做的是核对并确认配对，不是再记一笔。 */
function hasExistingEntrySuggestion(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  return sug?.type === 'match' || sug?.type === 'existing'
}

/** 低置信 / 兜底草稿不是 AI 的判断，不能按「建议分类」展示。 */
function isFallbackSuggestion(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || sug.type !== 'new') return false
  return Number(sug.confidence || 0) < 0.5
}

function findEntryById(id: string) {
  return entryStore.activeEntries.find((entry: any) => entry.id === id)
}

function pairCategoryPreview(pair: any) {
  if (pair.entries?.length > 1) {
    const categories = [...new Set(pair.entries.map((entry: any) => entry.category).filter(Boolean))]
    return categories.length > 0 ? `${pair.entries.length} 笔 · ${categories.join('、')}` : `${pair.entries.length} 笔账目`
  }
  const category = pair.invoice?.category || '未选择分类'
  const account = [pair.invoice?.accountCode, pair.invoice?.accountName].filter(Boolean).join(' ')
  return account ? `${category} · ${account}` : category
}


// ─── Manual Classify Dialog ──────────────────────────────────────────────────

const CATEGORY_OPTIONS_RAW = [
  { label: '── 收入 ──', value: '__header_income__', disabled: true },
  { label: '主营业务收入', value: 'income|主营业务收入|5001|主营业务收入' },
  { label: '其他业务收入', value: 'income|其他业务收入|5051|其他业务收入' },
  { label: '营业外收入', value: 'income|营业外收入|5301|营业外收入' },
  { label: '利息收入', value: 'income|利息收入|5603005|财务费用-利息收入' },
  { label: '── 员工成本 ──', value: '__header_salary__', disabled: true },
  // 工资/社保流水走工资模块核销（应付职工薪酬），不直接记费用；与后端 account_mapper 对齐
  { label: '工资发放', value: 'expense|工资发放|221101|应付职工薪酬-工资' },
  { label: '社保公积金', value: 'expense|社保公积金|221102|应付职工薪酬-单位社保' },
  { label: '── 费用支出 ──', value: '__header_expense__', disabled: true },
  { label: '差旅费', value: 'expense|差旅费|5602006|管理费用-差旅费' },
  { label: '办公费', value: 'expense|办公费|5602004|管理费用-办公费' },
  { label: '业务招待费', value: 'expense|业务招待费|5602002|管理费用-业务招待费' },
  { label: '研发支出-费用化支出', value: 'expense|研发支出-费用化支出|4301|研发支出-费用化支出' },
  { label: '信息技术服务费', value: 'expense|信息技术服务费|5602015|管理费用-信息技术服务费' },
  { label: '租金物业', value: 'expense|租金物业|5602013|管理费用-租赁费' },
  { label: '银行及金融手续费', value: 'expense|银行及金融手续费|5603002|财务费用-手续费' },
  { label: '其他支出', value: 'expense|其他支出|5602999|管理费用-其他' },
  { label: '── 采购与往来 ──', value: '__header_purchase__', disabled: true },
  { label: '原材料采购', value: 'expense|原材料采购|1403|原材料' },
  { label: '商品采购', value: 'expense|商品采购|1405|库存商品' },
  { label: '供应商付款', value: 'expense|供应商付款|2202|应付账款' },
  { label: '预付账款', value: 'expense|预付账款|1123|预付账款' },
  // 1221 已容器化：往来类落末级子科目——员工报销走 122103 员工往来，
  // 往来款暂付（平台验证款/供应商往来等非员工对手方）走 122199 待收款项
  { label: '其他应收款', value: 'expense|其他应收款|122103|其他应收款-员工往来' },
  { label: '其他应付款', value: 'expense|其他应付款|2241|其他应付款-其他' },
  { label: '往来款-支（暂付款）', value: 'expense|往来款|122199|其他应收款-待收款项' },
  { label: '往来款-收（暂收款）', value: 'income|往来款|2241|其他应付款-其他' },
  { label: '往来退款（冲暂付款）', value: 'income|往来退款|122199|其他应收款-待收款项' },
  // 2221 是容器：税费缴纳落应交增值税末级（小规模纳税人专用）
  { label: '税费缴纳', value: 'expense|税费缴纳|2221026|应交增值税（小规模纳税人专用）' },
  { label: '── 资本 ──', value: '__header_capital__', disabled: true },
  { label: '股东投入', value: 'owner_transfer|股东投入|3001|实收资本' },
  { label: '资本公积', value: 'owner_transfer|资本公积|3002|资本公积' },
  { label: '── 往来 ──', value: '__header_owner__', disabled: true },
  { label: '股东垫付', value: 'owner_transfer|股东垫付|2241|其他应付款-股东' },
  { label: '员工垫付报销', value: 'owner_transfer|员工垫付报销|2241|其他应付款-员工' },
]

// 下拉选项：accountCode/accountName 权威值从后端 /account-mapping 加载后刷新，
// 硬编码仅兜底；分组/顺序/往来款收支拆分是前端 UI 编排，保留在此。
const CATEGORY_OPTIONS = ref(CATEGORY_OPTIONS_RAW)

function refreshCategoryOptions() {
  CATEGORY_OPTIONS.value = CATEGORY_OPTIONS_RAW.map((opt: any) => {
    if (opt.disabled || !opt.value || opt.value.startsWith('__')) return opt
    const [direction, category] = opt.value.split('|')
    // 往来款收/支是前端特化，后端映射只给单一 code，跳过以免覆盖收=2241 的语义
    if (category === '往来款') return opt
    const acct = accountForCategory(category)
    if (!acct) return opt
    return { ...opt, value: `${direction}|${category}|${acct.code}|${acct.name}` }
  })
}

function categoryValueNatureLabel(value: string) {
  const [direction = '', category = ''] = value.split('|')
  return getEntryNaturePresentation(direction, category).detailLabel
}

// 账目分类可搜索：同时匹配分类名 + 科目名称 + 科目代码（value 形如
// `expense|租金物业|5602013|管理费用-租赁费`），输入更随意
function filterCategory(pattern: string, option: any): boolean {
  if (option.disabled) return false
  const p = String(pattern || '').trim().toLowerCase()
  if (!p) return true
  const parts = String(option.value || '').split('|')
  const haystack = [option.label, parts[1], parts[2], parts[3]]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return haystack.includes(p)
}

// 下拉待选项：分类名靠左为主，科目代码·名称靠右弱化，字号/行高分层不挤
// render-option 传入的是 { option, selected, node }，选项本身在 info.option
function renderCategoryOption(info: any) {
  const opt = info?.option
  if (!opt || opt.disabled) return opt?.label ?? ''
  const parts = String(opt.value || '').split('|')
  const code = parts[2]
  const name = parts[3]
  if (code && name) {
    return h('div', { style: 'display: flex; align-items: center; justify-content: space-between; gap: 14px; width: 100%; padding: 2px 8px; box-sizing: border-box;' }, [
      h('span', { style: 'font-size: 13px; line-height: 1.5; color: var(--solo-text-primary, #182230); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;' }, opt.label),
      h('span', { style: 'font-size: 11px; line-height: 1.5; color: var(--solo-text-muted, #8a94a3); flex-shrink: 0; white-space: nowrap;' }, `${code} · ${name}`),
    ])
  }
  return opt.label
}

const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])
const rdProjectOptions = computed(() => rdProjects.value
  .filter(project => ['active', 'completed'].includes(project.status))
  .map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })))
const rdExpenseTypeOptions = computed(() => rdExpenseTypes.value
  .map(item => ({ label: item.label, value: item.value })))

async function loadRdOptions() {
  try {
    const [projects, types] = await Promise.all([getRdProjects(true), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypes.value = types.items
  } catch {
    message.warning('研发项目选项加载失败，研发分类暂不能确认')
  }
}

function isRdCategoryValue(value: string) {
  return value.split('|')[1] === '研发支出-费用化支出'
}

function rdDetailFromEntry(entry: any) {
  const lines = [
    ...(entry?.voucherLines?.debit || []),
    ...(entry?.voucherLines?.credit || []),
  ]
  return lines.find((line: any) => line.rdDetail)?.rdDetail || null
}

const showManualDialog = ref(false)
const manualTarget = ref<any>(null)
const manualForm = ref({
  title: '',
  categoryValue: '',
  businessContext: '',
  remark: '',
  rdProjectId: '',
  rdExpenseType: '',
  rdAllocationBasis: '',
  suggestedSubAccount: null as null | { parentCode: string; name: string },
  useSuggestedSubAccount: true,
})
const manualSubmitting = ref(false)
const manualAiLoading = ref(false)
const manualSuggestionMeta = ref<{ confidence?: number; reason?: string }>({})
const showPairCategoryDialog = ref(false)
const pairCategoryTarget = ref<any>(null)
const pairCategoryForm = ref({
  title: '',
  categoryValue: '',
  rdProjectId: '',
  rdExpenseType: '',
  rdAllocationBasis: '',
})
const pairCategorySubmitting = ref(false)
const showEditVoucherDialog = ref(false)
const editVoucherForm = ref({
  title: '',
  categoryValue: '',
  rdProjectId: '',
  rdExpenseType: '',
  rdAllocationBasis: '',
})
const editVoucherSaving = ref(false)

function defaultCategoryValue(stmt: any) {
  return stmt.amount >= 0
    ? 'income|主营业务收入|5001|主营业务收入'
    : 'expense|其他支出|5602999|管理费用-其他'
}

function cleanStatementTitle(stmt: any) {
  const desc = String(stmt.description || '')
  return desc.replace(/^\[.*?\]\s*/, '').replace(/^\(对方:\s*/, '').replace(/\)$/, '').trim() || desc || '银行流水记账'
}

function categoryValueFromSuggestion(stmt: any, suggested: any) {
  if (!suggested) return defaultCategoryValue(stmt)
  const direction = suggested.direction || (stmt.amount >= 0 ? 'income' : 'expense')
  const exact = `${direction}|${suggested.category}|${suggested.accountCode}|${suggested.accountName}`
  if (CATEGORY_OPTIONS.value.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.value.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${suggested.category}|`))
  return byCategory?.value || defaultCategoryValue(stmt)
}

// 徽章把分类预翻译成将入账的科目。方向优先匹配下拉选项（往来款收 2241 暂收 /
// 支 122199 待收款项是前端拆分，后端映射只有单一 code）；下拉没有的分类（如
// 微信支付结算）回落到 accountForCategory——它加载的 /account-mapping 与确认
// 入账的 get_voucher_hint 同源。
function suggestionAccountPreview(stmt: any, categoryOverride?: string) {
  const suggested = effectiveSuggestion(stmt)?.suggestedEntry
  const category = categoryOverride || suggested?.category
  if (!category) return null
  const direction = suggested?.direction || (stmt.amount >= 0 ? 'income' : 'expense')
  const options = CATEGORY_OPTIONS.value.filter((opt: any) => !opt.disabled && opt.value)
  const opt =
    options.find((o: any) => String(o.value).startsWith(`${direction}|${category}|`)) ||
    options.find((o: any) => String(o.value).includes(`|${category}|`))
  if (opt) {
    const [, , code, name] = String(opt.value).split('|')
    if (code && name) return { code, name }
  }
  const acct = accountForCategory(category)
  return acct ? { code: acct.code, name: acct.name } : null
}

// ── 模糊分类候选：正确科目取决于账外事实（认缴进度、缴款性质），流水层面不可判定 ──
// 低置信且命中模糊组时展开候选 chips 交用户点选，认可入账以所选分类为准。
const rowCategoryChoice = ref<Record<string, string>>({})

// 候选须与流水方向相容：收入类候选不进付款行、费用类候选不进收款行；
// owner_transfer（权益/资本类）只伴随收款流水出现
function categoryDirectionCompatible(stmt: any, cat: string): boolean {
  const d = effectiveSuggestion(stmt)?.suggestedEntry?.direction || (stmt.amount >= 0 ? 'income' : 'expense')
  const options = CATEGORY_OPTIONS.value.filter((opt: any) => !opt.disabled && opt.value)
  const dirs = new Set(
    options
      .filter((opt: any) => String(opt.value).includes(`|${cat}|`))
      .map((opt: any) => String(opt.value).split('|')[0]),
  )
  if (!dirs.size || dirs.has(d)) return true
  return dirs.size === 1 && dirs.has('owner_transfer') && d === 'income'
}

function ambiguityGroupOf(stmt: any): string[] {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || sug.type !== 'new' || isFallbackSuggestion(stmt)) return []
  if (Number(sug.confidence || 0) >= 0.95) return []
  return getAmbiguityGroup(sug.suggestedEntry?.category).filter((cat) => categoryDirectionCompatible(stmt, cat))
}

// 仍未定论的模糊行：组内 chips 都没有点选过。点选后视为用户已决断，
// 恢复批量确认资格；点选组外分类不走这里（调整科目下拉会直接入账）。
function isAmbiguityActive(stmt: any): boolean {
  const group = ambiguityGroupOf(stmt)
  if (!group.length) return false
  const chosen = rowCategoryChoice.value[stmt.id]
  return !chosen || !group.includes(chosen)
}

// 用户点选候选后，后续展示与确认入账都以所选分类为准
function effectiveSuggestion(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  const chosen = rowCategoryChoice.value[stmt.id]
  if (!sug?.suggestedEntry || !chosen || chosen === sug.suggestedEntry.category) return sug
  return { ...sug, suggestedEntry: { ...sug.suggestedEntry, category: chosen } }
}

function altAccountText(stmt: any, category: string): string {
  const preview = suggestionAccountPreview(stmt, category)
  return preview ? `${preview.name} ${preview.code}` : ''
}

// ── 行内问 AI：面板已拆入 AskAiPanel.vue，父页只负责上下文组装与结论落地 ──
const askAiVisible = ref(false)
const askAiStmt = ref<any>(null)
const askAiPrompt = ref('')
const askAiCandidateLines = ref<{ category: string; account: string }[]>([])

function buildAskAiContext(stmt: any): string {
  const candidates = ambiguityGroupOf(stmt).length
    ? ambiguityGroupOf(stmt)
    : [getAiSuggestion(stmt.id)?.suggestedEntry?.category].filter(Boolean)
  const lines = [
    '我在月度结账向导核对一笔银行流水，需要你帮我判断这笔钱的入账分类。最终由我确认，请不要执行任何入账或对账操作。',
    '',
    '流水信息：',
    `- 日期：${stmt.transaction_date}`,
    `- 摘要：${stmt.description}`,
    `- 金额：${formatMoney(stmt.amount)}（${stmt.amount >= 0 ? '收款' : '付款'}）`,
    '',
    '候选分类（都是合法去处，正确答案取决于账外事实）：',
    ...candidates.map((cat) => `- ${cat}${altAccountText(stmt, cat) ? `（对应科目 ${altAccountText(stmt, cat)}）` : ''}`),
    '',
    '请：1) 先讲清这几个候选的会计区别和各自适用情形；2) 结合我司账目（可调用查账工具查历史入账）给出你的倾向和理由；3) 如果缺少关键事实（如认缴额度、缴款性质、合同条款），直接问我，一次只问一个问题；4) 最后用单独一行输出结论分类，格式严格为「【结论】分类名」（优先从候选里选；若你认为候选都不对，可给更合适的分类名）。',
  ]
  return lines.join('\n')
}

function openAskAi(stmt: any) {
  askAiStmt.value = stmt
  const candidates = ambiguityGroupOf(stmt).length
    ? ambiguityGroupOf(stmt)
    : [getAiSuggestion(stmt.id)?.suggestedEntry?.category].filter(Boolean)
  askAiCandidateLines.value = candidates.map((cat) => ({ category: cat, account: altAccountText(stmt, cat) || '—' }))
  askAiPrompt.value = buildAskAiContext(stmt)
  askAiVisible.value = true
}

function handleAskAiAccept(category: string) {
  const stmt = askAiStmt.value
  if (!stmt) return
  askAiVisible.value = false
  handleQuickSelectCategory(stmt, category)
}

function categoryValueFromEntry(entry: any, stmt: any) {
  if (!entry) return defaultCategoryValue(stmt)
  const exact = `${entry.direction}|${entry.category}|${entry.accountCode}|${entry.accountName}`
  if (CATEGORY_OPTIONS.value.some((opt: any) => !opt.disabled && opt.value === exact)) return exact
  const byCategory = CATEGORY_OPTIONS.value.find((opt: any) => !opt.disabled && String(opt.value).includes(`|${entry.category}|`))
  return byCategory?.value || defaultCategoryValue(stmt)
}

function openManualClassify(stmt: any, suggestedEntry: any = null) {
  const suggestion = getAiSuggestion(stmt.id)
  if (suggestion?.type === 'match' || suggestion?.type === 'existing') {
    selectExistingReconciliation(stmt, suggestion.matchedEntryIds || [suggestion.matchedEntryId].filter(Boolean))
    return
  }
  manualTarget.value = stmt
  manualSuggestionMeta.value = suggestedEntry
    ? { confidence: suggestedEntry.confidence, reason: suggestedEntry.reason }
    : {}
  const sub = suggestedEntry?.suggestedSubAccount
  manualForm.value = {
    title: suggestedEntry?.title || cleanStatementTitle(stmt),
    categoryValue: categoryValueFromSuggestion(stmt, suggestedEntry),
    businessContext: '',
    remark: '',
    rdProjectId: '',
    rdExpenseType: '',
    rdAllocationBasis: '',
    suggestedSubAccount: (sub?.parentCode && sub?.name)
      ? { parentCode: String(sub.parentCode), name: String(sub.name) }
      : null,
    useSuggestedSubAccount: !!(sub?.parentCode && sub?.name),
  }
  showManualDialog.value = true
}

function selectExistingReconciliation(stmt: any, entryIds: string[]) {
  selectedBank.value = stmt.id
  selectedInvoiceIds.value = entryIds.every((id) => allUnmatchedEntries.value.some((entry) => entry.id === id)) ? entryIds : []
  message.warning('已有账目，请核对左侧收入与手续费后确认合并对账，勿重复记账。')
}

function openPairCategoryDialog(pair: any) {
  if (pair.entries?.length > 1) {
    message.info('合并对账请分别在经营账本中修改各笔分类')
    return
  }
  const entry = findEntryById(pair.matchedEntryId) || pair.invoice
  const rdDetail = rdDetailFromEntry(entry)
  pairCategoryTarget.value = pair
  pairCategoryForm.value = {
    title: entry?.title || pair.invoice?.title || '',
    categoryValue: categoryValueFromEntry(entry, pair.bank),
    rdProjectId: rdDetail?.projectId || '',
    rdExpenseType: rdDetail?.expenseType || '',
    rdAllocationBasis: rdDetail?.allocationBasis || '',
  }
  showPairCategoryDialog.value = true
}

async function handlePairCategorySave() {
  const pair = pairCategoryTarget.value
  if (!pair || !pairCategoryForm.value.title.trim() || !pairCategoryForm.value.categoryValue || pairCategoryForm.value.categoryValue.startsWith('__')) {
    message.warning('请填写标题并选择科目分类')
    return
  }

  const [direction, category, accountCode, accountName] = pairCategoryForm.value.categoryValue.split('|')
  if (category === '研发支出-费用化支出' && !(
    pairCategoryForm.value.rdProjectId && pairCategoryForm.value.rdExpenseType
  )) {
    message.warning('研发费用必须选择研发项目和费用类别')
    return
  }
  pairCategorySubmitting.value = true
  try {
    await apiClient.patch(`/api/record/entries/${pair.matchedEntryId}`, {
      title: pairCategoryForm.value.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
      ...(category === '研发支出-费用化支出' ? {
        rdProjectId: pairCategoryForm.value.rdProjectId,
        rdExpenseType: pairCategoryForm.value.rdExpenseType,
        rdOriginalAmount: Math.abs(Number(pair.bank?.amount || pair.invoice?.amount || 0)),
        rdAllocationMethod: 'direct',
        rdAllocationBasis: pairCategoryForm.value.rdAllocationBasis || undefined,
      } : {}),
    })
    pair.invoice = {
      ...pair.invoice,
      title: pairCategoryForm.value.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
    }
    await entryStore.fetchEntries()
    showPairCategoryDialog.value = false
    message.success(`已更新分类：${category}`)
  } catch (err: any) {
    message.error(apiErrorMessage(err, '分类更新失败'))
  } finally {
    pairCategorySubmitting.value = false
  }
}

function openEditVoucher() {
  const entry = currentVoucherEntry.value
  if (!entry) return
  const rdDetail = rdDetailFromEntry(entry)
  editVoucherForm.value = {
    title: entry.title || '',
    categoryValue: categoryValueFromEntry(entry, null),
    rdProjectId: rdDetail?.projectId || '',
    rdExpenseType: rdDetail?.expenseType || '',
    rdAllocationBasis: rdDetail?.allocationBasis || '',
  }
  showEditVoucherDialog.value = true
}

async function saveEditVoucher() {
  const entry = currentVoucherEntry.value
  const f = editVoucherForm.value
  if (!entry || !f.title.trim() || !f.categoryValue || f.categoryValue.startsWith('__')) {
    message.warning('请填写标题并选择科目分类')
    return
  }
  const [direction, category, accountCode, accountName] = f.categoryValue.split('|')
  if (category === '研发支出-费用化支出' && !(f.rdProjectId && f.rdExpenseType)) {
    message.warning('研发费用必须选择研发项目和费用类别')
    return
  }
  editVoucherSaving.value = true
  try {
    await apiClient.patch(`/api/record/entries/${entry.id}`, {
      title: f.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
      ...(category === '研发支出-费用化支出' ? {
        rdProjectId: f.rdProjectId,
        rdExpenseType: f.rdExpenseType,
        rdAllocationBasis: f.rdAllocationBasis || undefined,
      } : {}),
    })
    await entryStore.fetchEntries()
    showEditVoucherDialog.value = false
    message.success(`已更新分类：${category}`)
    openVoucherModal(entry.id)
  } catch (err: any) {
    message.error(apiErrorMessage(err, '修改失败'))
  } finally {
    editVoucherSaving.value = false
  }
}

async function refreshManualAiSuggestion() {
  if (!manualTarget.value) return
  manualAiLoading.value = true
  try {
    const res: any = await apiClient.post('/api/bank/classify-statement', {
      statement_id: manualTarget.value.id,
      business_context: manualForm.value.businessContext.trim(),
    }, { timeout: 60000 })
    if (!res?.suggestedEntry) {
      message.warning('暂未生成新的 AI 分类建议')
      return
    }
    const suggested = res.suggestedEntry
    manualSuggestionMeta.value = { confidence: suggested.confidence, reason: suggested.reason }
    const sub = suggested?.suggestedSubAccount
    manualForm.value = {
      ...manualForm.value,
      title: suggested.title || manualForm.value.title,
      categoryValue: categoryValueFromSuggestion(manualTarget.value, suggested),
      suggestedSubAccount: (sub?.parentCode && sub?.name)
        ? { parentCode: String(sub.parentCode), name: String(sub.name) }
        : manualForm.value.suggestedSubAccount,
      useSuggestedSubAccount: !!(sub?.parentCode && sub?.name) || manualForm.value.useSuggestedSubAccount,
    }
    message.success('已根据业务说明更新 AI 分类草稿')
  } catch (err: any) {
    message.error(`AI 分类建议更新失败: ${err.message || err}`)
  } finally {
    manualAiLoading.value = false
  }
}

async function handleManualClassify() {
  if (!manualTarget.value || !manualForm.value.categoryValue || !manualForm.value.title.trim()) {
    message.warning('请填写记账标题并选择分类')
    return
  }

  const [direction, category, suggestedCode, suggestedName] = manualForm.value.categoryValue.split('|')
  let accountCode = suggestedCode
  let accountName = suggestedName
  if (category === '研发支出-费用化支出' && !(
    manualForm.value.rdProjectId && manualForm.value.rdExpenseType
  )) {
    message.warning('研发费用必须选择研发项目和费用类别')
    return
  }
  const stmt = manualTarget.value

  const finalDirection = direction === 'owner_transfer'
    ? 'owner_transfer'
    : (stmt.amount >= 0 ? 'income' : 'expense')
  const businessContext = manualForm.value.businessContext.trim()
  const remark = [
    businessContext ? `业务说明：${businessContext}` : '',
    manualForm.value.remark.trim(),
  ].filter(Boolean).join('\n')

  // 若 AI 建议新建二级科目且用户确认，先落地科目，再用新 code 记账；失败则回落原映射科目
  if (manualForm.value.useSuggestedSubAccount && manualForm.value.suggestedSubAccount) {
    try {
      const created: any = await apiClient.post('/api/ledger/accounts/ensure-sub', {
        parent_code: manualForm.value.suggestedSubAccount.parentCode,
        name: manualForm.value.suggestedSubAccount.name,
      })
      if (created?.code && created?.name) {
        accountCode = created.code
        accountName = created.name
      }
    } catch {
      // 建科失败不阻断记账
    }
  }

  manualSubmitting.value = true
  try {
    const confirmRes: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      allowBackfill,
      backfillReason,
      new_entry: {
        title: manualForm.value.title.trim(),
        amount: Math.abs(stmt.amount),
        direction: finalDirection,
        category,
        accountCode,
        accountName,
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : new Date().toISOString().slice(0, 10),
        remark,
        ...(category === '研发支出-费用化支出' ? {
          rdProjectId: manualForm.value.rdProjectId,
          rdExpenseType: manualForm.value.rdExpenseType,
          rdOriginalAmount: Math.abs(stmt.amount),
          rdAllocationMethod: 'direct',
          rdAllocationBasis: manualForm.value.rdAllocationBasis || undefined,
        } : {}),
      }
    }, { timeout: 60000 }),
    )
    if (confirmRes?.status === 'already_matched') {
      message.success('该流水已完成对账，已刷新列表。')
    } else {
      message.success(`已确认记账并完成对账：「${manualForm.value.title.trim()}」→ ${category}`)
    }
    
    // ponytail: 接口成功后立即关闭弹窗和置灰，用非阻塞 loadAllData 刷新后台以防止 Vue 重渲染挂起阻塞 UI 响应
    showManualDialog.value = false
    manualSubmitting.value = false
    reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) {
      manualSubmitting.value = false
      return
    }
    const detail = err?.response?.data?.detail
    if (detail?.code === 'EXISTING_WECHAT_ENTRIES') {
      showManualDialog.value = false
      const groups = detail.candidateEntryGroups || []
      selectExistingReconciliation(stmt, groups.length === 1 ? groups[0] : [])
    } else {
      message.error(apiErrorMessage(err, '记账提交失败'))
    }
    manualSubmitting.value = false
  }
}


const highConfidenceStatements = computed(() => {
  return unmatchedStatements.value.filter((stmt: any) => {
    const sug = getAiSuggestion(stmt.id)
    return sug && sug.confidence >= 0.95
  })
})

const statementTierFilter = ref<'all' | 'ready' | 'attention'>('all')
const selectedStatementIds = ref<string[]>([])
const rememberRuleOnConfirm = ref(true)

function isSelectableForBatch(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return false
  if (isFallbackSuggestion(stmt)) return false
  if (sug.type === 'new' && sug.suggestedEntry?.category === '研发支出-费用化支出') return false
  // 模糊分类行必须逐笔决断（点选候选后才恢复批量资格），不能混进批量静默入账
  if (isAmbiguityActive(stmt)) return false
  return Number(sug.confidence || 0) >= 0.5
}

function isNeedsAttentionStatement(stmt: any): boolean {
  if (findMatchingInvoiceForStatement(stmt)) return false
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return true
  return isFallbackSuggestion(stmt)
}

function suggestionBadgeLabel(stmt: any): string {
  if (isFallbackSuggestion(stmt)) return '待人工指定'
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return '待指定'
  if (sug.rule === 'company_memory_rule') return '记忆规则 98%'
  if (sug.rule) return '系统规则 100%'
  const conf = Number(sug.confidence || 0)
  if (conf >= 0.95) return '高置信度'
  return '推荐依据'
}

const selectableBatchStatements = computed(() =>
  unmatchedStatements.value.filter((stmt: any) => isSelectableForBatch(stmt))
)

const attentionStatements = computed(() =>
  unmatchedStatements.value.filter((stmt: any) => isNeedsAttentionStatement(stmt))
)

const filteredUnmatchedStatements = computed(() => {
  if (statementTierFilter.value === 'ready') {
    return unmatchedStatements.value.filter((stmt: any) => !isNeedsAttentionStatement(stmt))
  }
  if (statementTierFilter.value === 'attention') {
    return attentionStatements.value
  }
  return unmatchedStatements.value
})

watch(
  [unmatchedStatements, aiSuggestions],
  () => {
    const validSelectable = new Set(selectableBatchStatements.value.map((s: any) => s.id))
    const defaultHighConf = highConfidenceStatements.value
      .filter((s: any) => validSelectable.has(s.id))
      .map((s: any) => s.id)
    const kept = selectedStatementIds.value.filter((id) => validSelectable.has(id))
    selectedStatementIds.value = kept.length > 0 ? kept : defaultHighConf
  },
  { immediate: true }
)

function toggleStatementSelection(stmtId: string, checked: boolean) {
  if (checked) {
    if (!selectedStatementIds.value.includes(stmtId)) {
      selectedStatementIds.value = [...selectedStatementIds.value, stmtId]
    }
  } else {
    selectedStatementIds.value = selectedStatementIds.value.filter((id) => id !== stmtId)
  }
}

const allSelectableChecked = computed(
  () =>
    selectableBatchStatements.value.length > 0 &&
    selectableBatchStatements.value.every((stmt: any) => selectedStatementIds.value.includes(stmt.id))
)

const someSelectableChecked = computed(
  () =>
    !allSelectableChecked.value &&
    selectableBatchStatements.value.some((stmt: any) => selectedStatementIds.value.includes(stmt.id))
)

function toggleSelectAllStatements(checked: boolean) {
  selectedStatementIds.value = checked
    ? selectableBatchStatements.value.map((stmt: any) => stmt.id)
    : []
}

const batchTargetStatements = computed(() => {
  const selectedSet = new Set(selectedStatementIds.value)
  const picked = unmatchedStatements.value.filter(
    (stmt: any) => selectedSet.has(stmt.id) && isSelectableForBatch(stmt)
  )
  return picked.length > 0 ? picked : highConfidenceStatements.value
})

/** 按建议确认单笔流水（match 关联已有记录 / new 新建入账）。批量与单条共用同一路径。 */
async function confirmSuggested(
  stmt: any,
  sug: any,
  opts: { skipDuplicateCheck?: boolean; rememberRule?: boolean } = {}
) {
  if (sug.type === 'match') {
    const matchIds = sug.matchedEntryIds || [sug.matchedEntryId].filter(Boolean)
    return await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: stmt.id,
        type: 'match',
        matched_entry_ids: matchIds,
        allowBackfill,
        backfillReason,
      })
    )
  }
  const suggestedEntry = sug.suggestedEntry || {}
  return await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        title: suggestedEntry.title || cleanStatementTitle(stmt),
        category: suggestedEntry.category || (stmt.amount > 0 ? '其他业务收入' : '其他支出'),
        amount: Math.abs(stmt.amount),
        direction: stmt.amount > 0 ? 'income' : 'expense',
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        paymentDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        billingStatus: 'reconciled',
        source: 'bank_import',
        payer: 'company_account',
        rdProjectId: suggestedEntry.rdProjectId,
        rdExpenseType: suggestedEntry.rdExpenseType,
      },
      allowBackfill,
      backfillReason,
      ...(opts.skipDuplicateCheck ? { skipDuplicateCheck: true } : {}),
      ...(opts.rememberRule ? { rememberRule: true } : {}),
    })
  )
}

const batchConfirming = ref(false)
async function handleBatchConfirmRules() {
  const items = batchTargetStatements.value
    .map((stmt: any) => ({ stmt, sug: getAiSuggestion(stmt.id) }))
    .filter((item: any) => item.sug)
  if (items.length === 0) return
  batchConfirming.value = true
  let confirmed = 0
  let cancelled = false
  try {
    // 串行执行：跨期补记弹窗共用同一个 resolver，并发会互相覆盖
    for (const { stmt, sug } of items) {
      try {
        await confirmSuggested(stmt, sug)
        confirmed += 1
      } catch (err: any) {
        if (isBackfillCancelled(err)) {
          cancelled = true
          break
        }
        console.error('批量确认失败:', stmt.id, err)
      }
    }
    selectedStatementIds.value = []
    await reloadAll()
    const failed = items.length - confirmed
    if (cancelled) message.info(`已确认 ${confirmed} 笔，其余已取消`)
    else if (failed > 0) message.warning(`已确认 ${confirmed} 笔，${failed} 笔未能自动确认，请手动处理`)
    else message.success(`已确认 ${confirmed} 笔流水`)
  } finally {
    batchConfirming.value = false
  }
}

const quickSubmittingId = ref<string | null>(null)

/**
 * 新建入账时账上已有等额凭证（后端 EXISTING_ENTRY_MATCH）：
 * 直接给出"配对"选项，而不是再记一笔。返回 true 表示已接管错误提示。
 */
function promptExistingEntryMatch(stmt: any, err: any, retryAsNew?: () => Promise<void>): boolean {
  const detail = err?.response?.data?.detail
  if (!detail || detail.code !== 'EXISTING_ENTRY_MATCH') return false
  const entryIds: string[] = (detail.candidateEntryIds || []).map(String)
  if (!entryIds.length) return false
  const first = (detail.entries || [])[0] || {}
  dialog.warning({
    title: '发现一笔相同金额的已有凭证',
    content: `账上已有「${first.title || stmt.description}」${first.date ? `（${first.date}）` : ''}，金额相同。直接配对即可，避免同一笔钱记两次；确实属于两笔独立付款时再单独记账。`,
    positiveText: '直接配对',
    negativeText: '确认是两笔，仍要记账',
    onPositiveClick: async () => {
      try {
        await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
          apiClient.post('/api/bank/confirm-suggestion', {
            statement_id: stmt.id,
            type: 'match',
            matched_entry_ids: entryIds,
            allowBackfill,
            backfillReason,
          })
        )
        message.success(`已配对，未重复记账：「${stmt.description}」`)
        await reloadAll()
      } catch (pairErr: any) {
        if (!isBackfillCancelled(pairErr)) message.error(apiErrorMessage(pairErr, '配对失败'))
      }
    },
    onNegativeClick: async () => {
      if (!retryAsNew) {
        openVoucherModal(entryIds[0])
        return
      }
      try {
        await retryAsNew()
        message.success(`已按独立的一笔记账：「${stmt.description}」`)
        await reloadAll()
      } catch (retryErr: any) {
        if (!isBackfillCancelled(retryErr)) message.error(apiErrorMessage(retryErr, '入账失败'))
      }
    },
  })
  return true
}

async function handleAcceptSuggested(stmt: any) {
  // 用户点选过候选 chips 时以所选分类为准
  const sug = effectiveSuggestion(stmt)
  if (!sug) return
  quickSubmittingId.value = stmt.id
  const finalCategory = sug.suggestedEntry?.category || ''
  // 模糊组分类的正确科目随账外事实变化，不沉淀自动规则（后端同款护栏兜底）
  const shouldRemember = Boolean(
    rememberRuleOnConfirm.value
    && sug.type === 'new'
    && !isFallbackSuggestion(stmt)
    && !getAmbiguityGroup(finalCategory).length
  )
  try {
    const res: any = await confirmSuggested(stmt, sug, { rememberRule: shouldRemember })
    const suggestedEntry = sug.suggestedEntry || {}
    if (sug.type === 'match') {
      message.success(`已完成对账：「${stmt.description}」`)
    } else if (res?.savedRulePattern) {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}`)
    }
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptExistingEntryMatch(stmt, err, () => confirmSuggested(stmt, sug, { skipDuplicateCheck: true, rememberRule: shouldRemember }).then(() => undefined))) return
    message.error(apiErrorMessage(err, '入账失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

async function handleQuickSelectCategory(stmt: any, category: string) {
  if (!category) return
  if (category === '研发支出-费用化支出') {
    openManualClassify(stmt, { category: '研发支出-费用化支出' })
    return
  }
  quickSubmittingId.value = stmt.id
  const submitNewEntry = (allowBackfill: boolean, backfillReason?: string, skipDuplicateCheck = false) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        title: cleanStatementTitle(stmt),
        category: category,
        amount: Math.abs(stmt.amount),
        direction: stmt.amount > 0 ? 'income' : 'expense',
        occurDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        paymentDate: stmt.transaction_date ? stmt.transaction_date.slice(0, 10) : '',
        billingStatus: 'reconciled',
        source: 'bank_import',
        payer: 'company_account',
      },
      allowBackfill,
      backfillReason,
      ...(skipDuplicateCheck ? { skipDuplicateCheck: true } : {}),
      // 模糊组分类不沉淀规则：同一附言在不同事实下应落不同科目
      ...(rememberRuleOnConfirm.value && !getAmbiguityGroup(category).length ? { rememberRule: true } : {}),
    })
  try {
    const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      submitNewEntry(allowBackfill, backfillReason)
    )
    if (res?.savedRulePattern) {
      message.success(`已入账：「${stmt.description}」→ ${category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已入账：「${stmt.description}」→ ${category}`)
    }
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptExistingEntryMatch(stmt, err, () => submitNewEntry(false, undefined, true).then(() => undefined))) return
    message.error(apiErrorMessage(err, '入账失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

function findMatchingInvoiceForStatement(stmt: any) {
  const stmtAmount = Math.abs(stmt.amount)
  return unmatchedEntries.value.find((entry: any) => {
    const entryAmount = Math.abs(Number(entry.amount) || 0)
    return Math.abs(stmtAmount - entryAmount) < 0.01 && isDirectionCompatible(entry.direction, stmt.amount)
  })
}

// ── 待确认账目（发票/手工账）：老板只需要回答"付款了吗" ─────────────────

/** 给一笔账目找最可能的银行流水：方向一致、日期 ±10 天、金额最接近。 */
function findStatementCandidateForEntry(entry: any) {
  const entryAmount = Math.abs(Number(entry.amount) || 0)
  const entryDate = String(entry.date || entry.occurDate || '').slice(0, 10)
  const wantsIncome = entry.direction === 'income'
  let best: { statement: any; diff: number } | null = null
  for (const stmt of unmatchedStatements.value) {
    const stmtAmount = Number(stmt.amount) || 0
    if ((stmtAmount > 0) !== wantsIncome) continue
    const stmtDate = String(stmt.transaction_date || '').slice(0, 10)
    if (entryDate && stmtDate) {
      const dayDiff = Math.abs(new Date(entryDate).getTime() - new Date(stmtDate).getTime()) / 86400000
      if (dayDiff > 10) continue
    }
    const diff = Math.abs(Math.abs(stmtAmount) - entryAmount)
    // 差额超过账目金额 10% 不算疑似对应，避免把毫无关系的流水硬凑上来
    if (diff > entryAmount * 0.1) continue
    if (!best || diff < best.diff) best = { statement: stmt, diff }
  }
  return best
}

function statementCandidateText(candidate: { statement: any; diff: number } | null) {
  if (!candidate) return ''
  const stmt = candidate.statement
  const relation = candidate.diff < 0.01 ? '金额一致' : `差 ${formatMoney(candidate.diff)}`
  return `疑似对应 ${String(stmt.transaction_date).slice(0, 10)} ${stmt.description}（${relation}）`
}

/** 账目已付款：带用户去核对对应流水；找不到流水时提示补导。 */
function handleEntryPaid(entry: any) {
  const candidate = findStatementCandidateForEntry(entry)
  currentStep.value = 2
  if (!candidate) {
    message.warning('本月没找到对应流水。如果这笔是现金或个人账户支付，可以标记「还没付款」或直接忽略。')
    return
  }
  selectedBank.value = candidate.statement.id
  nextTick(() => scrollIntoBank(candidate.statement.id))
  message.info(statementCandidateText(candidate))
}

/** 确认单据去向（未付款/老板垫付/员工垫付/预存扣除/老板代收/平台未提现/冲前期预收等；若已过账则后台自动冲销重记） */
async function confirmEntryDisposition(entry: any, choice: { payer?: string; paymentStatus?: 'unpaid'; label: string }) {
  try {
    const { label, ...payload } = choice
    await updateEntry(entry.id, { ...payload, autoReverseRepost: true })
    message.success(`已确认：${label}`)
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '确认失败'))
  }
}

/** 撤销去向确认：单据重新回到待确认列表 */
async function resetEntryDisposition(entry: any) {
  try {
    await updateEntry(entry.id, { paymentStatus: null, payer: 'company_account', autoReverseRepost: true })
    message.success('已恢复为待确认状态')
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '恢复失败'))
  }
}

const employeePayerOptions = computed(() => {
  const options = (employees.value || []).map((emp: any) => ({
    label: `员工垫付：${emp.name}`,
    key: `employee_personal:${emp.name}`
  }))
  options.push({
    label: '其他员工垫付',
    key: 'employee_personal'
  })
  return options
})

const SYSTEM_MANUAL_CATEGORIES = new Set([
  '人力成本',
  '工资发放',
  '社保公积金',
  '个人所得税',
  '银行及金融手续费',
  '银行手续费',
  '利息收入',
  '股东投入',
  '老板投入',
  '资本公积',
  '股东取现',
  '老板取现',
  '股东垫付报销',
  '员工垫付报销',
])

const pendingEntryList = computed(() =>
  unmatchedEntries.value.filter((entry: any) => {
    const isInvoice = entry.source === 'invoice_import' || !!entry.invoiceNumber
    if (isInvoice) return true
    if (entry.direction === 'owner_transfer') return false
    const cat = String(entry.category || '').trim()
    const title = String(entry.title || '').trim()
    if (SYSTEM_MANUAL_CATEGORIES.has(cat)) return false
    if (/工资|社保|公积金|手续费|注册资本|股东投入/.test(title)) return false
    // 已挂结算在途科目（微信支付待结算/平台待提现）的账目：系统已记好去向，
    // 等打款流水核销，不需要人工再确认款项去向（如微信支付月度汇总凭证）
    const voucher = entry.voucherLines
    if (voucher) {
      const codes = [...(voucher.debit || []), ...(voucher.credit || [])].map((l: any) => String(l.accountCode || ''))
      if (codes.includes('1012001') || codes.includes('1012')) return false
    }
    return true
  })
)

/** 资金流出：支出，或负数的资本往来（股东取现等）；股东投入按流入显示 */
const entryIsOutflow = (entry: any) =>
  entry.direction === 'expense' || (entry.direction !== 'income' && Number(entry.amount) < 0)

const confirmedExceptionEntries = computed(() => {
  try {
    return entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      const isCurrentMonth = month === targetMonth.value
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      return isCurrentMonth && isNotArchiveOnly && isNonBankSettledEntry(e as any)
    }).map((e) => ({
      id: e.id,
      title: e.title,
      date: e.occurDate,
      amount: e.amount,
      direction: e.direction,
      category: (e as any).category || '',
      source: (e as any).source || 'manual',
      payer: (e as any).payer || 'company_account',
      paymentStatus: (e as any).paymentStatus || '',
      invoiceNumber: (e as any).invoiceNumber || '',
    }))
  } catch (err) {
    console.error('confirmedExceptionEntries computed error:', err)
    return []
  }
})

/** 老板往来小账本汇总：让老板一眼看懂确认后的应收、应付、可免税报销垫付款 */
const bossLedgerSummary = computed(() => {
  let customerOwes = 0
  let customerCount = 0
  let companyOwesSupplier = 0
  let supplierCount = 0
  let companyOwesBossAndEmp = 0
  let bossOnly = 0
  let empOnly = 0

  for (const item of confirmedExceptionEntries.value) {
    const amt = Math.abs(Number(item.amount) || 0)
    const payer = String(item.payer || '')
    const status = String(item.paymentStatus || '')
    if (status === 'unpaid') {
      if (item.direction === 'income') {
        customerOwes += amt
        customerCount += 1
      } else if (item.direction === 'expense') {
        companyOwesSupplier += amt
        supplierCount += 1
      }
    } else if (item.direction === 'expense') {
      if (payer === 'boss_personal') {
        companyOwesBossAndEmp += amt
        bossOnly += amt
      } else if (payer.startsWith('employee_personal')) {
        companyOwesBossAndEmp += amt
        empOnly += amt
      }
    }
  }
  return {
    customerOwes,
    customerCount,
    companyOwesSupplier,
    supplierCount,
    companyOwesBossAndEmp,
    bossOnly,
    empOnly,
    hasAny: customerOwes > 0 || companyOwesSupplier > 0 || companyOwesBossAndEmp > 0
  }
})

const pendingInvoiceList = computed(() =>
  pendingEntryList.value.filter((entry: any) => entry.source === 'invoice_import' || !!entry.invoiceNumber)
)

const unpaidInvoiceCount = computed(() =>
  invoicesList.value.filter((inv: any) => isNonBankSettledEntry(inv)).length
)

async function handleMatchInvoiceDirect(stmt: any, invoice: any) {
  quickSubmittingId.value = stmt.id
  try {
    await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: stmt.id,
        type: 'match',
        matched_entry_ids: [invoice.id],
        allowBackfill,
        backfillReason,
      })
    )
    message.success(`已关联发票并完成核对：「${invoice.title}」`)
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    message.error(apiErrorMessage(err, '发票关联失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

const bossExpenseOptions = [
  { label: '办公耗材 / 打印杂费', value: '办公费' },
  { label: '信息服务 / 云服务器 / 软件', value: '信息技术服务费' },
  { label: '请客吃饭 / 商务招待', value: '业务招待费' },
  { label: '打车交通 / 差旅住宿', value: '差旅费' },
  { label: '研发项目支出（需选项目）', value: '研发支出-费用化支出' },
  { label: '采购款 / 原材料', value: '原材料采购' },
  { label: '房租 / 物业 / 水电通讯', value: '租金物业' },
  { label: '员工报销款 / 往来备用金', value: '其他应收款' },
  { label: '还老板垫付款 / 股东往来', value: '股东垫付报销' },
  { label: '税费缴纳（增值税/个税等）', value: '税费缴纳' },
  { label: '银行手续费 / 转账费', value: '银行及金融手续费' },
  { label: '其他日常支出', value: '其他支出' },
]

const bossIncomeOptions = [
  { label: '微信支付结算（提现到账）', value: '微信支付结算' },
  { label: '支付宝结算（提现到账）', value: '支付宝结算' },
  { label: '退款冲销（收回暂付款）', value: '往来退款' },
  { label: '主营业务收入（货款/服务费）', value: '主营业务收入' },
  { label: '银行存款结息（利息收入）', value: '利息收入' },
  { label: '实名认证打款 / 营业外收入', value: '营业外收入' },
  { label: '股东投资款注入', value: '股东投入' },
  { label: '资本公积（溢价增资/超认缴）', value: '资本公积' },
  { label: '其他业务收入', value: '其他业务收入' },
]

  return {
  rawStatements,
  bankStatements,
  invoicesList,
  showInvoiceDetails,
  selectedInvoiceIds,
  selectedBank,
  matchedPairs,
  aiSuggestions,
  reconciliationHealth,
  reconciliationHealthLoading,
  filterUnmatchedData,
  suggestionsController,
  loadAiSuggestions,
  emptyReconciliationHealth,
  loadReconciliationHealth,
  deletePair,
  isDirectionCompatible,
  safeFloat,
  isItemAutoMatched,
  unmatchedInvoices,
  getEntryMonth,
  getStatementMonth,
  isStatementInTargetMonth,
  buildPersistedMatchedPairs,
  allUnmatchedEntries,
  unmatchedEntries,
  unmatchedStatements,
  historicalUnmatchedStatements,
  reconciliationStats,
  reconciliationHealthSummary,
  hasBlockingReconciliationRisk,
  getAiSuggestion,
  hasExistingEntrySuggestion,
  isFallbackSuggestion,
  findEntryById,
  pairCategoryPreview,
  CATEGORY_OPTIONS_RAW,
  CATEGORY_OPTIONS,
  refreshCategoryOptions,
  categoryValueNatureLabel,
  filterCategory,
  renderCategoryOption,
  rdProjects,
  rdExpenseTypes,
  rdProjectOptions,
  rdExpenseTypeOptions,
  loadRdOptions,
  isRdCategoryValue,
  rdDetailFromEntry,
  showManualDialog,
  manualTarget,
  manualForm,
  manualSubmitting,
  manualAiLoading,
  manualSuggestionMeta,
  showPairCategoryDialog,
  pairCategoryTarget,
  pairCategoryForm,
  pairCategorySubmitting,
  showEditVoucherDialog,
  editVoucherForm,
  editVoucherSaving,
  defaultCategoryValue,
  cleanStatementTitle,
  categoryValueFromSuggestion,
  suggestionAccountPreview,
  rowCategoryChoice,
  categoryDirectionCompatible,
  ambiguityGroupOf,
  isAmbiguityActive,
  effectiveSuggestion,
  altAccountText,
  askAiVisible,
  askAiStmt,
  askAiPrompt,
  askAiCandidateLines,
  buildAskAiContext,
  openAskAi,
  handleAskAiAccept,
  categoryValueFromEntry,
  openManualClassify,
  selectExistingReconciliation,
  openPairCategoryDialog,
  handlePairCategorySave,
  openEditVoucher,
  saveEditVoucher,
  refreshManualAiSuggestion,
  handleManualClassify,
  highConfidenceStatements,
  statementTierFilter,
  selectedStatementIds,
  rememberRuleOnConfirm,
  isSelectableForBatch,
  isNeedsAttentionStatement,
  suggestionBadgeLabel,
  selectableBatchStatements,
  attentionStatements,
  filteredUnmatchedStatements,
  toggleStatementSelection,
  allSelectableChecked,
  someSelectableChecked,
  toggleSelectAllStatements,
  batchTargetStatements,
  confirmSuggested,
  batchConfirming,
  handleBatchConfirmRules,
  quickSubmittingId,
  promptExistingEntryMatch,
  handleAcceptSuggested,
  handleQuickSelectCategory,
  findMatchingInvoiceForStatement,
  findStatementCandidateForEntry,
  statementCandidateText,
  handleEntryPaid,
  confirmEntryDisposition,
  resetEntryDisposition,
  employeePayerOptions,
  SYSTEM_MANUAL_CATEGORIES,
  pendingEntryList,
  entryIsOutflow,
  confirmedExceptionEntries,
  bossLedgerSummary,
  pendingInvoiceList,
  unpaidInvoiceCount,
  handleMatchInvoiceDirect,
  bossExpenseOptions,
  bossIncomeOptions,
  }
}
